from datetime import date
from pathlib import Path

import pytest
import scripts.xiaogu_knowledge_asset_export as export


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self):
        return self.row

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _KnowledgeDb:
    def __init__(self, active_run_id):
        self.active_run_id = active_run_id
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params):
        sql = str(statement)
        self.queries.append((sql, dict(params)))
        run_id = params.get('production_run_id')
        if 'FROM production_runs' in sql:
            return _FakeResult(row=(run_id,) if run_id in {'run-a', 'run-b'} else None)
        if 'FROM production_run_active' in sql:
            return _FakeResult(row=(self.active_run_id,))
        if 'FROM picks p' in sql and 'ORDER BY created_at' in sql:
            return _FakeResult(rows=[{
                'id': 1 if run_id == 'run-a' else 2,
                'symbol': '600001' if run_id == 'run-a' else '600002',
                'stock_name': 'Run A' if run_id == 'run-a' else 'Run B',
                'decision': 'PAPER_PICK',
                'final_score': 91.0 if run_id == 'run-a' else 81.0,
                'rank': 1,
                'features': {},
                'selection_reason': {},
                'ticket_reason': {},
                'auxiliary_evidence_status': 'PASS',
                'ranking_basis': {},
                'paper_pick_eligibility': {},
                'created_at': None,
                'updated_at': None,
            }])
        if 'FROM daily_candidates dc' in sql:
            return _FakeResult(rows=[{
                'rank': 1,
                'symbol': '600001' if run_id == 'run-a' else '600002',
                'stock_name': 'Run A' if run_id == 'run-a' else 'Run B',
                'final_score': 91.0 if run_id == 'run-a' else 81.0,
                'decision': 'PAPER_PICK',
                'selection_outcome': 'OFFICIAL_PICK',
                'is_official_pick': True,
                'selection_reason': {},
                'ticket_reason': {},
                'not_selected_reason': [],
                'auxiliary_evidence_snapshot': {},
                'ranking_basis': {},
                't1_return': 0.03 if run_id == 'run-a' else None,
                'pick_id': 1 if run_id == 'run-a' else 2,
            }])
        if 'FROM picks p' in sql and "p.decision = 'PAPER_PICK'" in sql:
            return _FakeResult(rows=[{
                'symbol': '600001' if run_id == 'run-a' else '600002',
                'stock_name': 'Run A' if run_id == 'run-a' else 'Run B',
                'final_score': 91.0 if run_id == 'run-a' else 81.0,
                't1_return': 0.03 if run_id == 'run-a' else None,
                'pick_id': 1 if run_id == 'run-a' else 2,
            }])
        raise AssertionError(f'unexpected SQL: {sql}')


def test_load_day_knowledge_isolates_explicit_and_active_runs(monkeypatch):
    db = _KnowledgeDb(active_run_id='run-b')
    monkeypatch.setattr(export, 'get_db', lambda: db)

    explicit = export.load_day_knowledge(date(2026, 8, 7), 'run-a')
    active = export.load_day_knowledge(date(2026, 8, 7))

    assert explicit['production_run_id'] == 'run-a'
    assert explicit['formal_paper_pick']['symbol'] == '600001'
    assert explicit['top10'][0]['symbol'] == '600001'
    assert explicit['top10'][0]['t1_return'] == 0.03
    assert explicit['paper_pick_returns'][0]['symbol'] == '600001'
    assert active['production_run_id'] == 'run-b'
    assert active['formal_paper_pick']['symbol'] == '600002'
    assert active['top10'][0]['symbol'] == '600002'
    assert active['top10'][0]['t1_return'] is None
    assert active['paper_pick_returns'][0]['symbol'] == '600002'
    scoped_queries = [
        sql for sql, params in db.queries
        if params.get('production_run_id') in {'run-a', 'run-b'}
        and ('FROM picks p' in sql or 'FROM daily_candidates dc' in sql)
    ]
    assert scoped_queries
    assert all('production_run_id = :production_run_id' in sql for sql in scoped_queries)
    assert all(
        'r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol' in sql
        for sql in scoped_queries
        if 'FROM daily_candidates dc' in sql
    )


def test_load_day_knowledge_rejects_missing_production_run(monkeypatch):
    monkeypatch.setattr(export, 'get_db', lambda: _KnowledgeDb(active_run_id='run-b'))

    with pytest.raises(ValueError, match='PRODUCTION_RUN_NOT_FOUND_FOR_KNOWLEDGE_EXPORT'):
        export.load_day_knowledge(date(2026, 8, 7), 'missing-run')


def test_knowledge_export_source_has_no_literal_run_clause_placeholder():
    source = Path('scripts/xiaogu_knowledge_asset_export.py').read_text(encoding='utf-8')

    assert '{run_clause}' not in source
    assert '{run_clause.replace' not in source


def test_export_historical_knowledge_aggregates_cases(monkeypatch):
    dates = [date(2026, 7, 28), date(2026, 7, 29)]
    monkeypatch.setattr(export, 'historical_knowledge_dates', lambda *_args: dates)
    monkeypatch.setattr(
        export,
        'upsert_top10_cases_from_db',
        lambda td: {'upserted': 10 if td.day == 28 else 8, 'failed': 0},
    )
    monkeypatch.setattr(
        export,
        'upsert_paper_pick_cases_from_db',
        lambda td: {'upserted': 1, 'failed': 0},
    )
    monkeypatch.setattr(
        export,
        'rebuild_all_case_embeddings',
        lambda: {'status': 'OK', 'updated': 20, 'failed': 0},
    )

    report = export.export_historical_knowledge(
        date(2026, 7, 28),
        date(2026, 7, 29),
        rebuild_vectors=True,
    )

    assert report['dates'] == 2
    assert report['top10_upserted'] == 18
    assert report['paper_pick_upserted'] == 2
    assert report['failed'] == 0
    assert report['rebuild_vectors']['updated'] == 20
    assert [row['trade_date'] for row in report['date_results']] == [
        '2026-07-28',
        '2026-07-29',
    ]


def test_write_obsidian_updates_same_day_status_and_reports_vector_failures(tmp_path, monkeypatch):
    ashare = tmp_path / 'Project' / 'A股'
    shenlin = tmp_path / '神临'
    (ashare / 'inbox').mkdir(parents=True)
    shenlin.mkdir(parents=True)
    (ashare / '状态.md').write_text(
        '# 当前状态\n\n'
        '## 2026-08-04 · 正式票锁定与知识资产\n\n'
        '- 正式票: **旧票** score=79.7 pick_id=1\n'
        '- 向量: TOP10 cohort 已写入\n\n'
        '## 2026-08-03 · 正式票锁定与知识资产\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(export, 'OBSIDIAN_ASHARE', ashare)
    monkeypatch.setattr(export, 'OBSIDIAN_SHENLIN', shenlin)
    monkeypatch.setattr(export, 'embed_method_name', lambda: 'structured_hybrid_v2')

    result = export.write_obsidian(
        {
            'formal_paper_pick': {
                'symbol': '600487',
                'stock_name': '亨通光电',
                'final_score': 100.0,
                'pick_id': 2131,
                'auxiliary_evidence_status': 'PASS',
                'features_flags': {},
                'ticket_reason': {},
                'selection_reason': {},
            },
            'top10': [
                {
                    'rank': 1,
                    'symbol': '600487',
                    'stock_name': '亨通光电',
                    'final_score': 100.0,
                    'selection_outcome': 'OFFICIAL_PICK',
                },
            ],
            'top10_vector_upsert': {'upserted': 0, 'failed': 1},
            'vector_layer': {'embed_method': 'structured_hybrid_v2'},
            'generated_at': '2026-08-04T16:00:00',
        },
        date(2026, 8, 4),
    )

    assert result['status'] == 'OK'
    note = (ashare / 'inbox' / '2026-08-04-正式票与前十知识资产.md').read_text(encoding='utf-8')
    status = (ashare / '状态.md').read_text(encoding='utf-8')
    assert 'TOP10 向量写入未完成（0/1，失败 1 条' in note
    assert '600487 亨通光电' in status
    assert 'score=100.0 pick_id=2131' in status
    assert 'TOP10 upsert=0/1，failed=1' in status
    assert 'score=79.7 pick_id=1' not in status


def test_write_obsidian_promotes_picker_results_into_structured_knowledge(tmp_path, monkeypatch):
    ashare = tmp_path / 'Project' / 'A股'
    shenlin = tmp_path / '神临'
    (ashare / 'inbox').mkdir(parents=True)
    shenlin.mkdir(parents=True)
    monkeypatch.setattr(export, 'OBSIDIAN_ASHARE', ashare)
    monkeypatch.setattr(export, 'OBSIDIAN_SHENLIN', shenlin)
    monkeypatch.setattr(export, 'embed_method_name', lambda: 'structured_hybrid_v2')

    result = export.write_obsidian(
        {
            'formal_paper_pick': {
                'symbol': '600001',
                'stock_name': '测试公司',
                'final_score': 91.0,
                'pick_id': 10,
                'auxiliary_evidence_status': 'PASS',
                'features_flags': {},
                'ticket_reason': {'reason': 'sector_flow'},
                'selection_reason': {'reason': 'formal_rank'},
            },
            'top10': [
                {
                    'rank': 1,
                    'symbol': '600001',
                    'stock_name': '测试公司',
                    'final_score': 91.0,
                    'selection_outcome': 'OFFICIAL_PICK',
                    't1_return': -0.03,
                    'selection_reason': 'sector_flow',
                },
            ],
            'paper_pick_returns': [
                {
                    'symbol': '600001',
                    'stock_name': '测试公司',
                    'final_score': 91.0,
                    't1_return': -0.03,
                },
            ],
            'top10_return_coverage': {'n': 1, 'with_t1': 1, 'ratio': 1.0},
            'top10_vector_upsert': {'upserted': 1, 'failed': 0},
            'vector_layer': {'embed_method': 'structured_hybrid_v2'},
            'generated_at': '2026-08-21T16:00:00',
        },
        date(2026, 8, 21),
    )

    decision = ashare / '决策日志' / '2026-08-21-PAPER_PICK决策.md'
    tracking = ashare / '跟踪记录' / '2026-08-21-每日变化与出票结果.md'
    lesson = ashare / '失败案例' / '2026-08-21-600001-出票复盘.md'
    assert str(decision) in result['paths']
    assert str(tracking) in result['paths']
    assert str(lesson) in result['paths']
    assert 'T+1 结果：-0.03' in decision.read_text(encoding='utf-8')
    assert '亏损' in tracking.read_text(encoding='utf-8')
    assert '根因结论' in lesson.read_text(encoding='utf-8')
