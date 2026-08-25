"""Tests for DB backfill script."""
import importlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest


def test_backfill_script_imports():
    """Verify backfill script can be imported."""
    mod = importlib.import_module('scripts.xiaogu_db_backfill')
    assert hasattr(mod, 'backfill_from_ledger')
    assert hasattr(mod, 'backfill_from_bundles')
    assert hasattr(mod, 'backfill_from_live_scan')
    assert hasattr(mod, 'backfill_from_factors')
    assert hasattr(mod, 'main')


def test_social_collector_normalizes_and_stores_shadow_signals(monkeypatch):
    import xiaogu_social_sentiment as social

    written = []
    monkeypatch.setattr(
        'xiaogu_db.upsert_signal',
        lambda trade_date, symbol, signal_key, signal_value, raw_json: written.append(
            (trade_date, symbol, signal_key, signal_value, raw_json)
        ),
    )
    payload = social.normalize_social_signal(
        'eastmoney_guba',
        {
            'post_count': 12,
            'positive_count': 9,
            'negative_count': 1,
            'sample_titles': ['东财股吧资金回流，继续看多'],
        },
    )
    result = social._store_social_payload(date(2026, 7, 10), '600001', [payload])

    written_keys = {item[2] for item in written}
    obsolete_signal_keys = {
        'social_mentions_' + suffix
        for suffix in ('x', 'red' + 'dit')
    } | {'theme_strength_last30d'}
    assert result['status'] == 'PASS'
    assert result['social_catalyst_score'] > 0
    assert result['theme_strength_last30d'] == 0.0
    assert written_keys >= {
        'social_sentiment_eastmoney_guba',
        'social_catalyst_score',
        'social_noise_risk',
        'social_sentiment_score',
        'social_collection_status',
    }
    assert written_keys.isdisjoint(obsolete_signal_keys)
    assert all(item[4]['used_for_official_ranking'] is False for item in written)
    status_row = next(item for item in written if item[2] == 'social_collection_status')
    assert status_row[4]['source_layers'] == ['eastmoney_guba']
    assert status_row[4]['social_signal_quality'] == 'MEDIUM'


def test_social_collection_failure_preserves_operational_signal_values(monkeypatch):
    import xiaogu_social_sentiment as social

    written = []
    monkeypatch.setattr(
        'xiaogu_db.upsert_signal',
        lambda trade_date, symbol, signal_key, signal_value, raw_json: written.append(
            (trade_date, symbol, signal_key, signal_value, raw_json)
        ),
    )
    failed = social.normalize_social_signal('eastmoney_guba', {'error': 'no_direct_source'})

    result = social._store_social_payload(date(2026, 7, 10), '600001', [failed])

    assert result['status'] == 'WARN'
    assert [item[2] for item in written] == ['social_collection_status']
    assert written[0][4]['preserved_existing_social_values'] is True
    assert written[0][4]['collection_errors'][0]['source'] == 'eastmoney_guba'
    assert written[0][4]['collection_errors'][0]['error'] == 'no_direct_source'


def test_social_attachment_retains_valid_values_and_surfaces_collection_warning(monkeypatch):
    import xiaogu_social_sentiment as social

    metadata = {
        'collection_status': 'PASS',
        'source_layers': ['eastmoney_guba'],
        'social_signal_quality': 'MEDIUM',
    }
    monkeypatch.setattr(
        'xiaogu_db.fetch_signals',
        lambda _date: [
            {'symbol': '600001', 'signal_key': 'social_catalyst_score', 'signal_value': 0.8, 'raw_json': metadata},
            {'symbol': '600001', 'signal_key': 'social_sentiment_score', 'signal_value': 0.3, 'raw_json': metadata},
            {'symbol': '600001', 'signal_key': 'social_noise_risk', 'signal_value': 0.1, 'raw_json': metadata},
            {
                'symbol': '600001',
                'signal_key': 'social_collection_status',
                'signal_value': None,
                'raw_json': {
                    'collection_status': 'WARN',
                    'collection_errors': [{'source': 'eastmoney_guba', 'error': 'no_direct_source'}],
                    'source_layers': ['eastmoney_guba'],
                    'social_signal_quality': 'MEDIUM',
                },
            },
        ],
    )

    attached = social.attach_social_features([{'symbol': '600001'}], '2026-07-10')

    assert attached[0]['social_catalyst_score'] == 0.8
    assert attached[0]['theme_strength_last30d'] == 0.0
    assert attached[0]['social_source_layers'] == ['eastmoney_guba']
    assert attached[0]['social_signal_quality'] == 'MEDIUM'
    assert attached[0]['social_signal_collection_status'] == 'WARN'
    assert attached[0]['social_signal_error'][0]['source'] == 'eastmoney_guba'


def test_social_collectors_use_direct_public_pages(monkeypatch):
    import xiaogu_social_sentiment as social

    # Primary path is gbapi JSON.
    api_payload = json.dumps(
        {
            'count': 2,
            're': [
                {'post_title': '芯片利好，明天涨停'},
                {'post_title': '资金回流，继续看多'},
            ],
        },
        ensure_ascii=False,
    )

    def fake_fetch(url, *, timeout=15, direct=False, accept=''):
        if 'gbapi.eastmoney.com' in url and 'Articlelist' in url:
            return api_payload
        pytest.fail(f'unexpected fetch url for primary path: {url}')

    monkeypatch.setattr(social, '_fetch_public_text', fake_fetch)

    guba = social.scrape_eastmoney_guba('600001')

    assert guba['post_count'] == 2
    assert guba['positive_count'] == 2
    assert guba.get('transport') == 'gbapi_articlelist'


def test_social_collectors_fail_closed_when_api_empty(monkeypatch):
    import xiaogu_social_sentiment as social

    def fake_fetch(url, *, timeout=15, direct=False, accept=''):
        if 'gbapi.eastmoney.com' in url:
            return json.dumps({'count': 0, 're': []})
        pytest.fail(f'unexpected fallback fetch: {url}')

    monkeypatch.setattr(social, '_fetch_public_text', fake_fetch)

    guba = social.scrape_eastmoney_guba('600001')
    assert 'error' in guba
    assert guba['symbol'] == '600001'


def test_collect_and_store_ignores_obsolete_external_sources(monkeypatch):
    import xiaogu_social_sentiment as social

    collected = []
    written = []
    monkeypatch.setattr(
        social,
        'scrape_eastmoney_guba',
        lambda symbol: collected.append(symbol) or {'post_count': 1, 'positive_count': 1, 'negative_count': 0},
    )
    monkeypatch.setattr(
        'xiaogu_db.upsert_signal',
        lambda trade_date, symbol, signal_key, signal_value, raw_json: written.append(signal_key),
    )

    obsolete_sources = ['x', 'red' + 'dit', 'last' + '30days']
    obsolete_signal_keys = {
        'social_mentions_' + suffix
        for suffix in ('x', 'red' + 'dit')
    } | {'theme_strength_last30d'}
    result = social.collect_and_store(
        ['600001'],
        trade_date='2026-07-10',
        sources=['eastmoney_guba', *obsolete_sources],
    )

    assert result['status'] == 'PASS'
    assert collected == ['600001']
    assert 'social_sentiment_eastmoney_guba' in written
    assert set(written).isdisjoint(obsolete_signal_keys)


def test_daily_pipeline_has_one_authoritative_chain():
    script = Path('daily_pipeline.sh').read_text(encoding='utf-8')

    assert 'scrapy_scanner/runner_v2.py' in script
    assert 'xiaogu_forward_runner.py' in script
    assert 'scripts/xiaogu_return_backfill.py' in script
    assert '--fill-all-pending' not in script
    assert 'scripts/xiaogu_knowledge_asset_export.py' in script
    assert 'xiaogu_social_sentiment.py' not in script
    assert 'legacy_browser_scan' not in script


def test_daily_pipeline_keeps_safe_self_evolve_observation_only():
    script = Path('daily_pipeline.sh').read_text(encoding='utf-8')
    assert 'scripts/xiaogu_safe_self_evolve.py' in script
    assert 'backfill_return_pick_ids' not in script
    # Research self-evolution is observation-only and cannot mutate production scoring.
    assert '--dry-run' in script
    assert '--apply-if-ready' not in script
    # T1 validation path owns observation-only self-evolution.
    assert script.index('--manual-return-backfill') < script.index('--dry-run')
    # Shadow profit candidates: main LIVE path after runner; T1 path refreshes with --with-returns
    assert 'scripts/xiaogu_profit_candidates_shadow.py' in script
    assert '--compare-official' in script
    assert script.count('xiaogu_profit_candidates_shadow.py') >= 2
    assert '--with-returns' in script
    # Main LIVE path block: runner appears before the post-decision profit shadow step marker
    live_marker = '[4.2/5] 影子获利候选'
    assert live_marker in script
    assert script.index('xiaogu_forward_runner.py') < script.index(live_marker)
    assert script.index(live_marker) < script.index('[4.3/5] 有界因子自进化')

def test_forced_candidate_snapshot_archives_and_preserves_stale_rows(monkeypatch, tmp_path):
    import xiaogu_forward_runner as runner

    trade_date = date(2026, 7, 13)
    written = []
    payloads = {
        'status': 'OK',
        'scan_session': None,
        'daily_candidates': [{'trade_date': trade_date, 'symbol': '600001'}],
        'limitup_gene_signals': [],
        'candidate_pool_exclusion_summary': {'target_count': 200},
    }
    monkeypatch.setattr(runner, 'RAW_ROOT', tmp_path)
    monkeypatch.setattr(runner, 'build_daily_candidate_persistence_payloads', lambda *args: payloads)
    monkeypatch.setattr('xiaogu_db.fetch_daily_candidates', lambda _date: [{'symbol': '600999'}])
    monkeypatch.setattr('xiaogu_db.has_returns_for_trade_date', lambda _date: False)
    monkeypatch.setattr('xiaogu_db.insert_scan_session', lambda **kwargs: 1)
    monkeypatch.setattr('xiaogu_db.upsert_daily_candidate', lambda **kwargs: written.append(kwargs))
    monkeypatch.setattr('xiaogu_db.upsert_limitup_gene_signals', lambda **kwargs: {})
    result = runner.persist_daily_candidate_snapshot(
        '2026-07-13',
        {'_runner_asof_time': '17:22:57'},
        {},
        'PAPER_PICK',
        'selected',
        replace_existing=True,
        correction_of='old-ledger-reference',
    )

    assert result['status'] == 'OK'
    assert result['written'] == 1
    assert result['pruned_stale_count'] == 0
    assert result['preserved_stale_count'] == 1
    archive = json.loads(Path(result['correction_archive']['archive_path']).read_text(encoding='utf-8'))
    assert archive['correction_of'] == 'old-ledger-reference'
    assert archive['rows'] == [{'symbol': '600999'}]
    assert written[0]['symbol'] == '600001'


def test_runtime_database_writes_have_no_destructive_sql():
    sources = [
        Path('xiaogu_db.py'),
        Path('xiaogu_forward_bundle_io.py'),
        Path('xiaogu_signal_effectiveness_v0_1.py'),
        Path('xiaogu_case_vector_store.py'),
    ]
    source = '\n'.join(path.read_text(encoding='utf-8') for path in sources)
    assert 'DELETE FROM' not in source
    assert 'TRUNCATE' not in source
    assert 'DROP TABLE' not in source
    assert 'DROP COLUMN' not in source


def test_run_recorder_passes_correction_reference(monkeypatch, tmp_path):
    import xiaogu_forward_runner as runner

    captured = []
    monkeypatch.setattr(runner, 'RAW_ROOT', tmp_path)

    def fake_run(command, **kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, '{}', '')

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)
    result = runner.run_recorder(
        '2026-07-13',
        '17:22:57',
        'PAPER_PICK',
        '600186',
        {},
        'selected',
        False,
        correction_of='old-ledger-reference',
    )

    assert result['returncode'] == 0
    assert captured[0][captured[0].index('--correction-of') + 1] == 'old-ledger-reference'


def test_replay_daily_candidate_snapshots_uses_live_persist_owner(monkeypatch, tmp_path):
    import scripts.xiaogu_db_backfill as backfill
    import xiaogu_forward_runner as runner

    payload_path = tmp_path / 'db_persistence_retry_payload.json'
    payload = {
        'payload_version': 'daily_candidate_snapshot_v1',
        'date': '2026-07-13',
        'decision': 'PAPER_PICK',
        'reason': 'selected',
        'bundle': {
            'full_candidate_pool': [
                {'code': '600100', 'name': '主板样本', 'rank': 1},
                {'code': '600101', 'name': '主板样本二', 'rank': 11},
            ],
            'paper_scoring_candidates': [{'code': '600100', 'name': '主板样本', 'rank': 1}],
            'candidate_pool_exclusion_summary': {'target_count': 200, 'source_row_count': 2},
        },
        'features': {'candidate_consumption_summary': {'official_result': {'symbol': '600100'}}},
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    calls = []

    def fake_persist(date_arg, bundle_arg, features_arg, decision_arg, reason_arg):
        calls.append((date_arg, bundle_arg, features_arg, decision_arg, reason_arg))
        return {'status': 'OK', 'written': 1}

    monkeypatch.setattr(backfill, '_retry_payload_paths', lambda target_date='': [payload_path])
    monkeypatch.setattr(runner, 'persist_daily_candidate_snapshot', fake_persist)

    dry_run_stats = backfill.replay_daily_candidate_snapshots(target_date='2026-07-13', dry_run=True)

    assert dry_run_stats['payloads_found'] == 1
    assert dry_run_stats['payloads_replayed'] == 0
    assert dry_run_stats['details'][0]['candidate_count'] == 2
    assert calls == []

    stats = backfill.replay_daily_candidate_snapshots(target_date='2026-07-13')

    assert stats['payloads_found'] == 1
    assert stats['payloads_replayed'] == 1
    assert stats['written'] == 1
    assert calls == [(
        '2026-07-13',
        payload['bundle'],
        payload['features'],
        'PAPER_PICK',
        'selected',
    )]


def test_replay_daily_candidate_snapshots_falls_back_to_scan_summary(monkeypatch, tmp_path):
    import scripts.xiaogu_db_backfill as backfill
    import xiaogu_forward_runner as runner

    summary_path = tmp_path / '2026-07-17' / 'eastmoney_scan_afternoon' / 'xiaogu_scan_summary_runner.json'
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(json.dumps({'source_time': '2026-07-17 13:58:16'}, ensure_ascii=False), encoding='utf-8')
    bundle = {
        'available': True,
        'full_candidate_pool': [{'code': '600100', 'rank': 1}, {'code': '600101', 'rank': 11}],
        'candidate_pool_exclusion_summary': {'target_count': 200, 'source_row_count': 2},
    }
    calls = []

    def fake_bundle(path_arg, summary_arg):
        assert path_arg == summary_path
        assert summary_arg['source_time'] == '2026-07-17 13:58:16'
        return bundle

    def fake_persist(date_arg, bundle_arg, features_arg, decision_arg, reason_arg):
        calls.append((date_arg, bundle_arg, features_arg, decision_arg, reason_arg))
        return {'status': 'OK', 'written': 2}

    monkeypatch.setattr(backfill, '_retry_payload_paths', lambda target_date='': [])
    monkeypatch.setattr(backfill, '_summary_replay_paths', lambda target_date='': [summary_path])
    monkeypatch.setattr(backfill, 'fetch_picks', lambda trade_date: [{'decision': 'PAPER_PICK', 'symbol': '600101'}])
    monkeypatch.setattr(runner, '_bundle_from_scan_summary', fake_bundle)
    monkeypatch.setattr(runner, 'persist_daily_candidate_snapshot', fake_persist)

    dry_run = backfill.replay_daily_candidate_snapshots(target_date='2026-07-17', dry_run=True)
    assert dry_run['details'][0]['source_kind'] == 'summary'
    assert dry_run['details'][0]['candidate_count'] == 2
    assert calls == []

    stats = backfill.replay_daily_candidate_snapshots(target_date='2026-07-17')
    assert stats['payloads_found'] == 1
    assert stats['payloads_replayed'] == 1
    assert stats['written'] == 2
    assert calls == [(
        '2026-07-17',
        bundle,
        {'candidate_consumption_summary': {'official_result': {'symbol': '600101', 'decision': 'PAPER_PICK', 'source': 'picks'}}},
        'PAPER_PICK',
        'replayed_from_scan_summary_with_db_pick',
    )]


def test_ledger_backfill_dry_run(tmp_path):
    """Verify ledger parser extracts records correctly."""
    ledger = tmp_path / "test_ledger.jsonl"
    records = [
        {
            "date": "2026-05-20", "symbol": "300603", "decision": "PAPER_PICK",
            "rule_version": "rule_v0_2", "features_used": {
                "candidate_bundle_status": {
                    "available": True,
                    "paper_scoring_candidates": [
                        {"code": "300603", "name": "立昂微", "rank": 1, "final_score": 75.0,
                         "score": 75.0, "structured_score": 50.0, "signal_pct": 3.5,
                         "close_position_score": 0.8, "fund_flow_momentum": 0.5,
                         "sector_catalyst_score": 0.3, "early_opportunity_score": 0.7,
                         "topic_propagation_score": 0.2, "market_regime": "neutral",
                         "setup_type": "FIRST_BOARD_PRE_SIGNAL"}
                    ]
                },
                "candidate_features": {"code": "300603", "name": "立昂微"},
                "risk_flags": [],
            },
            "t1_return": 0.05, "t2_return": None, "t3_return": None,
        },
        {
            "date": "2026-05-20", "symbol": "300603", "decision": "",
            "rule_version": "rule_v0_2", "features_used": {
                "candidate_bundle_status": {"available": None},
                "candidate_features": {},
                "risk_flags": [],
            },
        },
    ]
    with open(ledger, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')

    from scripts.xiaogu_db_backfill import _parse_ledger
    parsed = _parse_ledger(ledger)
    assert len(parsed) >= 1
    assert parsed[0]['trade_date'] == '2026-05-20'
    assert parsed[0]['symbol'] == '300603'
    assert parsed[0]['decision'] == 'PAPER_PICK'


def test_bundles_backfill_dry_run(tmp_path):
    """Verify bundle parser extracts candidates correctly."""
    bundle_dir = tmp_path / "bundles" / "2026-06-10"
    bundle_dir.mkdir(parents=True)
    bundle_file = bundle_dir / "2026-06-10_eastmoney_api_scan_v2_candidate.json"
    bundle_data = {
        "date": "2026-06-10",
        "paper_scoring_candidates": [
            {"code": "601816", "name": "京沪高铁", "rank": 1, "final_score": 83.8,
             "score": 67.96, "structured_score": 46.8, "signal_pct": 1.77,
             "close_position_score": 0.9, "fund_flow_momentum": 0.415,
             "sector_catalyst_score": 0.0, "early_opportunity_score": 0.96,
             "topic_propagation_score": 0.0, "market_regime": "neutral",
             "setup_type": "INTRADAY_ALERT_REVERSAL"},
            {"code": "600210", "name": "紫江企业", "rank": 2, "final_score": 82.0,
             "score": 82.0, "signal_pct": 2.1, "market_regime": "neutral"},
        ],
    }
    with open(bundle_file, 'w') as f:
        json.dump(bundle_data, f)

    from scripts.xiaogu_db_backfill import _parse_bundles
    parsed = _parse_bundles(tmp_path / "bundles")
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2
    assert parsed[0]['date'] == '2026-06-10'


def test_live_scan_backfill_dry_run(tmp_path):
    """Verify live scan parser extracts scored candidates."""
    scan_dir = tmp_path / "scan" / "2026-06-10" / "eastmoney_scan_afternoon"
    scan_dir.mkdir(parents=True)
    records = [
        {"code": "601816", "name": "京沪高铁", "rank": 1, "score": 67.96,
         "final_score": 67.96, "signal_pct": 1.77, "setup_type": "INTRADAY_ALERT_REVERSAL"},
        {"code": "600210", "name": "紫江企业", "rank": 2, "score": 82.0,
         "final_score": 82.0, "signal_pct": 2.1},
    ]
    (scan_dir / "xiaogu_scan_summary_runner.json").write_text(
        json.dumps({
            "source": "eastmoney_api_scan_v2",
            "pipeline_version": "v2_scanner_api",
            "source_time": "2026-06-10 14:30:00",
            "paper_scoring_candidates": records,
        }),
        encoding="utf-8",
    )

    from scripts.xiaogu_db_backfill import _parse_live_scan
    parsed = _parse_live_scan(tmp_path / "scan")
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2


def test_live_scan_parser_prefers_latest_summary_runner(tmp_path):
    scan_dir = tmp_path / "scan" / "2026-06-10"
    morning_dir = scan_dir / "eastmoney_scan_morning"
    afternoon_dir = scan_dir / "eastmoney_scan_afternoon"
    morning_dir.mkdir(parents=True)
    afternoon_dir.mkdir(parents=True)

    (morning_dir / "xiaogu_scan_summary_runner.json").write_text(
        json.dumps({
            "source_time": "2026-06-10 09:25:00",
            "paper_scoring_candidates": [{"symbol": "600001", "rank": 1}],
        }),
        encoding="utf-8",
    )
    (afternoon_dir / "xiaogu_scan_summary_runner.json").write_text(
        json.dumps({
            "source_time": "2026-06-10 14:30:00",
            "paper_scoring_candidates": [{"symbol": "600002", "rank": 1}],
        }),
        encoding="utf-8",
    )

    from scripts.xiaogu_db_backfill import _parse_live_scan
    parsed = _parse_live_scan(tmp_path / "scan")

    assert parsed == [{
        "date": "2026-06-10",
        "candidates": [{"symbol": "600002", "rank": 1}],
    }]


def test_factors_backfill_dry_run(tmp_path):
    """Verify factors parquet parser extracts candidates."""
    import pandas as pd
    factors_dir = tmp_path / "factors"
    factors_dir.mkdir()
    df = pd.DataFrame({
        'trade_date': ['2026-06-10', '2026-06-10'],
        'code': ['601816', '600210'],
        'symbol': ['601816', '600210'],
        'name': ['京沪高铁', '紫江企业'],
        'score': [67.96, 82.0],
        'final_score': [67.96, 82.0],
        'pct_chg': [1.77, 2.1],
        'close': [4.61, 8.5],
        'amount': [1e9, 5e8],
        'volume_ratio': [1.26, 1.5],
        'net_inflow_main': [2.3e7, 1e7],
        'close_position_score': [0.9, 0.7],
        'sector_opportunity_score': [0.0, 0.5],
        'decision': ['CANDIDATE', 'CANDIDATE'],
    })
    df.to_parquet(factors_dir / "20260610.parquet")

    from scripts.xiaogu_db_backfill import _parse_factors
    parsed = _parse_factors(factors_dir)
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2


def test_return_backfill_emits_canonical_t1_labels(monkeypatch):
    """Verify backfill emits the complete canonical T+1 target."""
    import scripts.xiaogu_return_backfill as backfill

    class FakeConn:
        calls = 0
        queries = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            FakeConn.calls += 1
            FakeConn.queries.append(str(query))
            rows = (
                [(date(2026, 6, 22), '300001', 80.0, False, 1, 'run-622', 'snapshot-622', 101)]
                if FakeConn.calls == 1
                else []
            )
            return type('Result', (), {'fetchall': lambda self: rows})()

    klines = [
        {'date': '2026-06-22', 'open': 10.0, 'high': 10.2, 'low': 9.8, 'close': 10.0},
        {'date': '2026-06-23', 'open': 9.8, 'high': 10.8, 'low': 9.6, 'close': 10.4},
        {'date': '2026-06-24', 'open': 10.4, 'high': 10.5, 'low': 10.1, 'close': 10.3},
        {'date': '2026-06-25', 'open': 10.3, 'high': 10.6, 'low': 10.2, 'close': 10.5},
        {'date': '2026-06-29', 'open': 10.5, 'high': 10.7, 'low': 10.3, 'close': 10.6},
    ]

    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'datetime', type('FakeDatetime', (), {'now': staticmethod(lambda: type('Now', (), {'date': lambda self: date(2026, 7, 1)})())}))
    monkeypatch.setattr(backfill, 'fetch_canonical_daily_ohlc', lambda symbol, start, end: klines)
    monkeypatch.setattr(backfill.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(backfill, 'is_trading_day', lambda _day: True)

    stats = backfill.backfill_returns(dry_run=True)
    row = stats['results'][0]

    assert row['returns']['t1'] < 0.04
    assert row['returns']['t1'] == round(row['t1_metrics']['t1_net_return'], 4)
    assert set(row) == {
        'date', 'symbol', 'score', 'entry', 'returns', 't1_metrics',
        'execution_model', 'production_trade_mode',
    }
    assert row['t1_metrics']['t1_open_return'] == -0.02
    assert row['t1_metrics']['t1_high_return'] == 0.08
    assert row['t1_metrics']['t1_low_return'] == -0.04
    assert row['t1_metrics']['t1_close_return'] == 0.04
    assert row['t1_metrics']['t1_mfe'] == 0.08
    assert row['t1_metrics']['t1_mae'] == -0.04
    assert round(row['t1_metrics']['t1_net_return'], 4) == row['returns']['t1']
    assert row['t1_metrics']['label_status'] == 'SETTLED'
    assert row['execution_model']['execution_status'] == 'FILLED'
    assert 'dc.rank <= 10' not in FakeConn.queries[0]


def test_shadow_execution_profile_marks_limit_states_and_costs():
    from xiaogu_forward_result_filler_v0_1 import shadow_execution_profile

    base = {
        'date': '2026-08-20',
        'symbol': '600001',
        'execution_contract': {
            'execution_price': 10.0,
            'entry_price_source': 'test.snapshot.close',
            'price_basis': 'UNADJUSTED_DAILY_OHLC',
            'execution_mode': 'EXPLICIT',
        },
        'features_used': {
            'candidate_features': {
                'entry_price': 10.0,
            },
        },
    }
    filled = shadow_execution_profile(
        base,
        {
            'date': '2026-08-21',
            'open': 10.0,
            'high': 10.8,
            'low': 9.9,
            'close': 10.5,
            'pct_chg': 5.0,
        },
        {'date': '2026-08-20', 'close': 10.0},
        gross_return=0.05,
    )

    assert filled['execution_status'] == 'FILLED'
    assert filled['entry_reference_price'] == 10.0
    assert filled['next_day_exit_reference_price'] == 10.5
    assert filled['net_return'] < filled['gross_return']
    assert filled['worst_case_return'] is not None
    assert filled['entry_trade_state'] == 'T_DAY_BUYABLE'
    assert filled['exit_trade_state'] == 'T1_SELLABLE'
    assert filled['t_plus_one_sell_restriction'] == 'T_DAY_BUY_NOT_SELLABLE_UNTIL_T1'
    assert filled['board_limit_percent'] == 10.0
    assert filled['share_lot_constraint'] == 'MINIMUM_100_SHARES'
    assert filled['price_basis_consistent'] is True

    blocked_buy = {
        **base,
        'features_used': {
            'candidate_features': {
                'entry_price': 10.0,
                'sealed_limit_up': True,
            },
        },
    }
    blocked = shadow_execution_profile(blocked_buy, {'close': 10.5})
    assert blocked['entry_fill_status'] == 'NOT_FILLABLE'
    assert blocked['execution_status'] == 'NOT_FILLABLE'
    assert blocked['net_return'] is None
    assert blocked['not_fillable_is_not_loss'] is True
    assert blocked['entry_trade_state'] == 'T_DAY_NOT_BUYABLE'


def test_shadow_execution_profile_marks_locked_limit_down_as_not_fillable():
    from xiaogu_forward_result_filler_v0_1 import shadow_execution_profile

    result = shadow_execution_profile(
        {
            'date': '2026-08-20',
            'symbol': '600001',
            'execution_contract': {
                'execution_price': 10.0,
                'entry_price_source': 'test.snapshot.close',
                'price_basis': 'UNADJUSTED_DAILY_OHLC',
                'execution_mode': 'EXPLICIT',
            },
            'features_used': {'candidate_features': {'entry_price': 10.0}},
        },
        {
            'date': '2026-08-21',
            'open': 9.0,
            'high': 9.0,
            'low': 9.0,
            'close': 9.0,
            'pct_chg': -10.0,
        },
        {'date': '2026-08-20', 'close': 10.0},
        gross_return=-0.1,
    )

    assert result['exit_fill_status'] == 'NOT_FILLABLE'
    assert result['execution_status'] == 'NOT_FILLABLE'
    assert result['net_return'] is None


def test_price_source_consistency_exposes_conflict_without_silent_selection():
    from xiaogu_forward_result_filler_v0_1 import price_source_consistency

    result = price_source_consistency({'baostock': 10.0, 'eastmoney': 10.2})
    assert result['status'] == 'CONFLICT'
    assert result['conflict'] is True
    assert result['provider_count'] == 2


def test_return_from_rows_rejects_mixed_adjustment_basis(tmp_path):
    from xiaogu_forward_result_filler_v0_1 import return_from_rows

    ret, evidence = return_from_rows(
            {
                'date': '2026-08-20',
                'symbol': '600001',
                'execution_contract': {
                    'execution_price': 10.0,
                    'entry_price_source': 'test.snapshot.close',
                    'price_basis': 'UNADJUSTED_DAILY_OHLC',
                    'execution_mode': 'EXPLICIT',
                },
                'features_used': {'candidate_features': {'entry_price': 10.0}},
            },
        't1',
        [
            {'date': '2026-08-20', 'close': 10.0},
            {'date': '2026-08-21', 'close': 10.5, 'high': 10.6, 'low': 10.2, 'pct_chg': 5.0},
        ],
        tmp_path / 'evidence.json',
        {'_request_url': 'https://example.test/kline?fqt=1'},
        'test',
    )

    assert ret is None
    assert evidence['status'] == 'PRICE_BASIS_MISMATCH'


def test_return_from_rows_uses_canonical_market_data_source(tmp_path):
    from xiaogu_forward_result_filler_v0_1 import (
        CANONICAL_MARKET_DATA_SOURCE,
        return_from_rows,
    )

    ret, evidence = return_from_rows(
            {
                'date': '2026-08-20',
                'symbol': '600001',
                'execution_contract': {
                    'execution_price': 10.0,
                    'entry_price_source': 'test.snapshot.close',
                    'price_basis': 'UNADJUSTED_DAILY_OHLC',
                    'execution_mode': 'EXPLICIT',
                },
                'features_used': {'candidate_features': {'entry_price': 10.0}},
            },
        't1',
        [
            {'date': '2026-08-20', 'close': 10.0},
            {'date': '2026-08-21', 'open': 10.1, 'high': 10.8, 'low': 9.9, 'close': 10.5},
        ],
        tmp_path / 'evidence.json',
        {'_request_url': 'https://example.test/kline?fqt=0'},
        'test_source_detail',
    )

    assert ret == 0.05
    assert evidence['source'] == CANONICAL_MARKET_DATA_SOURCE
    assert evidence['market_data_source'] == CANONICAL_MARKET_DATA_SOURCE
    assert evidence['market_data_source_detail'] == 'test_source_detail'


def test_return_backfill_accepts_validation_day_t1_metrics(monkeypatch):
    """Manual --validate-on should fill T+1 metrics on the validation date itself."""
    import scripts.xiaogu_return_backfill as backfill

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 13)

    class FakeConn:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            FakeConn.calls += 1
            rows = (
                [(date(2026, 7, 10), '002558', 67.17, True, 10, 'run-710', 'snapshot-710', 102)]
                if FakeConn.calls == 1
                else []
            )
            return type('Result', (), {'fetchall': lambda self: rows})()

    klines = [
        {'date': '2026-07-10', 'open': 27.9, 'high': 29.88, 'low': 27.4, 'close': 29.56},
        {'date': '2026-07-13', 'open': 29.01, 'high': 29.35, 'low': 27.5, 'close': 28.48},
    ]

    monkeypatch.setattr(backfill, 'date', FakeDate)
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_canonical_daily_ohlc', lambda symbol, start, end: klines)
    monkeypatch.setattr(backfill.time, 'sleep', lambda seconds: None)

    stats = backfill.backfill_returns(
        dry_run=True, input_trade_date='2026-07-10', validation_trade_date='2026-07-13',
    )

    assert stats['t1_filled'] == 1
    assert stats['new_success_count'] == 1
    row = stats['results'][0]
    assert row['returns']['t1'] == -0.0433
    assert row['t1_metrics']['t1_net_return'] < row['t1_metrics']['t1_close_return']
    assert set(row['returns']) == {'t1'}


def test_return_backfill_rejects_mismatched_validation_date():
    import scripts.xiaogu_return_backfill as backfill

    stats = backfill.backfill_returns(
        dry_run=True, input_trade_date='2026-07-09', validation_trade_date='2026-07-13',
    )

    assert stats['fatal_error'] == 'VALIDATION_TRADE_DATE_MISMATCH'
    assert stats['expected_validation_trade_date'] == '2026-07-10'


def test_return_backfill_exposes_canonical_t1_metrics():
    import scripts.xiaogu_return_backfill as backfill

    assert backfill.HORIZON_OFFSETS == {'t1': 1}
    assert backfill.CANONICAL_LABEL_VERSION == 'canonical_t1_v1'
    assert backfill.CANONICAL_MARKET_DATA_SOURCE == 'eastmoney_push2his_daily_kline_fqt0'
    assert backfill.CANONICAL_PRICE_BASIS == 'UNADJUSTED_DAILY_OHLC'


def test_upsert_return_persists_canonical_t1_labels(monkeypatch):
    """The production writer persists the canonical six-field target."""
    import xiaogu_db

    captured = {}

    class FakeResult:
        def fetchone(self):
            return (42,)

        def scalar(self):
            return 0

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            sql = str(query)
            if 'INSERT INTO returns' in sql or 'ON CONFLICT' in sql:
                captured['sql'] = sql
                captured['params'] = params
            return FakeResult()

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())

    xiaogu_db.upsert_return(
        trade_date=date(2026, 6, 23),
        symbol='300001',
        pick_id=None,
        t1_return=0.01,
        t1_return_close=0.01,
        next_day_open_return=-0.02,
        next_day_high_return=0.08,
        next_day_low_return=-0.04,
        next_day_gap_return=-0.02,
        next_day_drawdown=-0.1111,
        high_to_close_retrace=-0.037,
        t1_labels={
            't1_open_return': -0.02,
            't1_high_return': 0.08,
            't1_low_return': -0.04,
            't1_close_return': 0.01,
            't1_mfe': 0.08,
            't1_mae': -0.04,
            'label_status': 'SETTLED',
        },
        settlement_evidence={
            'entry_price': 10.0,
            'entry_price_source': 'BACKFILL_T_DAY_CLOSE',
            'entry_price_basis': 'UNADJUSTED_DAILY_OHLC',
            'label_version': 'canonical_t1_v1',
            'source': 'baostock',
        },
        legacy_backfill=True,
    )

    assert 't1_return' in captured['sql']
    assert captured['params']['t1_return'] == 0.01
    assert 't1_open_return' in captured['sql']
    assert captured['params']['t1_open_return'] == -0.02
    assert captured['params']['label_version'] == 'canonical_t1_v1'
    # pick_id is auto-resolved when caller passes None
    assert captured['params']['pick_id'] == 42
    assert 'COALESCE(EXCLUDED.pick_id, returns.pick_id)' in captured['sql']


def test_canonical_entry_rejects_legacy_aliases_and_calculates_labels():
    from xiaogu_forward_result_filler_v0_1 import (
        build_execution_contract,
        calculate_t1_labels,
        resolve_canonical_entry_price,
        target_quality_gate,
    )

    decision = {
        'date': '2026-08-18',
        'asof_time': '14:50:00',
        'features_used': {'candidate_features': {'price': 27.79, 'signal_close': 27.79}},
    }
    assert resolve_canonical_entry_price(decision) is None
    assert build_execution_contract(decision)['status'] == 'INVALID'

    contract = build_execution_contract(
        {'date': '2026-08-18', 'asof_time': '15:00:00'},
        entry_price_override=27.79,
        entry_price_source='BACKFILL_T_DAY_CLOSE',
        entry_price_basis='UNADJUSTED_DAILY_OHLC',
    )
    labels = calculate_t1_labels(
        27.79,
        {'open': 27.03, 'high': 27.68, 'low': 26.00, 'close': 26.17},
    )
    assert labels == {
        't1_open_return': -0.027348,
        't1_high_return': -0.003958,
        't1_low_return': -0.064412,
        't1_close_return': -0.058294,
        't1_mfe': -0.003958,
        't1_mae': -0.064412,
        'label_status': 'SETTLED',
    }
    assert target_quality_gate(
        contract,
        {'open': 27.03, 'high': 27.68, 'low': 26.00, 'close': 26.17},
        labels,
    )['status'] == 'PASS'


def test_execution_contract_is_explicit_and_rejects_conflicting_or_unproven_prices():
    from xiaogu_forward_result_filler_v0_1 import (
        CANONICAL_PRICE_BASIS,
        build_execution_contract,
    )

    contract = build_execution_contract({
        'date': '2026-08-18',
        'asof_time': '14:50:00',
        'execution_contract': {
            'execution_price': 10.0,
            'entry_price_source': 'scanner_snapshot.close_price',
            'price_basis': CANONICAL_PRICE_BASIS,
            'execution_mode': 'EXPLICIT',
        },
        'features_used': {'candidate_features': {'entry_price': 10.0}},
    })
    assert contract['status'] == 'VALID'
    assert contract['execution_mode'] == 'EXPLICIT'
    assert contract['entry_price'] == 10.0
    assert contract['entry_source'] == 'scanner_snapshot.close_price'
    assert contract['price_basis'] == CANONICAL_PRICE_BASIS

    missing_contract = build_execution_contract({
        'date': '2026-08-18',
        'asof_time': '14:50:00',
        'features_used': {'candidate_features': {'entry_price': 10.0}},
    })
    assert missing_contract['status'] == 'INVALID'
    assert 'EXECUTION_CONTRACT_REQUIRED' in missing_contract['errors']

    conflict = build_execution_contract({
        'date': '2026-08-18',
        'execution_contract': {'execution_price': 11.0},
        'features_used': {'candidate_features': {'entry_price': 10.0}},
    })
    assert conflict['status'] == 'INVALID'
    assert 'ENTRY_PRICE_CONFLICT' in conflict['errors']

    override_without_provenance = build_execution_contract(
        {'date': '2026-08-18'},
        entry_price_override=10.0,
        entry_price_source='MANUAL',
    )
    assert override_without_provenance['status'] == 'INVALID'
    assert 'ENTRY_PRICE_BASIS_REQUIRED' in override_without_provenance['errors']


def test_extended_t1_labels_include_vwap_gap_and_costed_net_return():
    from xiaogu_forward_result_filler_v0_1 import calculate_t1_labels

    labels = calculate_t1_labels(
        10.0,
        {
            'open': 10.5,
            'high': 11.0,
            'low': 10.0,
            'close': 10.8,
            'volume': 1000,
            'amount': 10500,
        },
        previous_row={'close': 10.0},
        execution_profile={
            'net_return': 0.07,
            'buy_slippage': 0.001,
            'sell_slippage': 0.002,
            'commission': 5.0,
            'stamp_duty': 1.0,
            'transfer_fee': 0.1,
            'impact_cost': 0.0005,
        },
        include_extended=True,
    )
    assert labels['t1_vwap_return'] == 0.05
    assert labels['t1_gap_return'] == 0.05
    assert labels['t1_net_return'] == 0.07
    assert labels['slippage'] == 0.003
    assert labels['market_impact'] == 0.0005


def test_stored_execution_label_patch_only_fills_missing_net_return():
    import scripts.xiaogu_return_backfill as backfill

    row = {
        't1_net_return': None,
        't1_close_return': -0.02,
        'entry_price': 10.0,
        'label_status': 'SETTLED',
        'settlement_evidence': {
            'execution_model': {
                'net_return': '-0.03125',
                'buy_slippage': 0.001,
                'sell_slippage': 0.001,
                'commission': 5.0,
                'stamp_duty': 0.5,
                'transfer_fee': 0.01,
                'impact_cost': 0.0005,
            },
        },
    }

    patch = backfill._stored_execution_label_patch(row)

    assert patch['t1_net_return'] == -0.03125
    assert patch['slippage'] == 0.002
    assert patch['market_impact'] == 0.0005
    assert backfill._stored_execution_label_patch({**row, 't1_net_return': 0.01}) is None


def test_target_quality_report_requires_costed_t1_net_return():
    from xiaogu_forward_result_filler_v0_1 import target_dataset_quality_report

    rows = [{
        't1_open_return': 0.01,
        't1_high_return': 0.02,
        't1_low_return': -0.01,
        't1_close_return': 0.01,
        't1_mfe': 0.02,
        't1_mae': -0.01,
        't1_net_return': None,
        'label_status': 'SETTLED',
    }]

    report = target_dataset_quality_report(rows)

    assert report['status'] == 'TARGET_NOT_READY'
    assert report['core_coverage']['t1_net_return'] == 0.0


def test_model_registry_requires_acceptance_and_health_can_activate_kill_switch(monkeypatch):
    import xiaogu_db

    with pytest.raises(ValueError, match='MODEL_PRODUCTION_ACCEPTANCE_REQUIRED'):
        xiaogu_db.register_model(
            'model-research-1',
            feature_version='features-v1',
            label_version='canonical_t1_v1',
            status='PRODUCTION',
            db=object(),
        )

    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                'model_id': 'model-research-1',
                'health_date': date(2026, 8, 25),
                'status': 'PAPER_ONLY',
                'kill_switch': True,
                'kill_switch_reason': 'rolling_expectancy_below_ci',
            }

    class FakeDb:
        def execute(self, query, params=None):
            return FakeResult()

    state = xiaogu_db.alpha_kill_switch_active(
        model_id='model-research-1',
        db=FakeDb(),
    )
    assert state['active'] is True
    assert state['status'] == 'PAPER_ONLY'


def test_minimum_tradable_edge_gate_returns_no_pick_below_cost_or_before_promotion():
    import xiaogu_forward_runner as runner

    assert runner.minimum_tradable_edge_gate(
        {'tradable_edge': 0.01},
        minimum_edge=0.0,
        transaction_cost=0.02,
        model_status='PRODUCTION',
    )['status'] == 'NO_PICK'
    assert runner.minimum_tradable_edge_gate(
        {'tradable_edge': 0.03},
        minimum_edge=0.0,
        transaction_cost=0.02,
        model_status='PRODUCTION',
    )['eligible'] is True
    assert runner.minimum_tradable_edge_gate(
        {'tradable_edge': 0.03},
        model_status='UNVERIFIED',
    )['reason'] == 'T1_ALPHA_MODEL_NOT_PRODUCTION'
def test_target_dataset_quality_report_never_treats_missing_as_zero():
    from xiaogu_forward_result_filler_v0_1 import target_dataset_quality_report

    report = target_dataset_quality_report([
        {
            't1_open_return': 0.01,
            't1_high_return': 0.02,
            't1_low_return': -0.01,
            't1_close_return': 0.01,
            't1_mfe': 0.02,
            't1_mae': -0.01,
            't1_net_return': 0.01,
            'label_status': 'SETTLED',
        },
        {
            't1_open_return': None,
            't1_high_return': None,
            't1_low_return': None,
            't1_close_return': None,
            't1_mfe': None,
            't1_mae': None,
            't1_net_return': None,
            'label_status': 'INVALID',
        },
    ])

    assert report['status'] == 'TARGET_NOT_READY'
    assert report['coverage']['t1_open_return'] == 0.5
    assert report['invalid_rows'] == 1


def test_production_return_settlement_updates_matching_candidate_placeholder(monkeypatch):
    import xiaogu_db

    updates = []

    class FakeResult:
        def __init__(self, row=None, rowcount=1):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            sql = str(query)
            if 'SELECT id, t1_return, candidate_snapshot_id' in sql:
                return FakeResult()
            if 'INSERT INTO returns' in sql:
                return FakeResult((314,))
            if 'UPDATE daily_candidates' in sql:
                updates.append(dict(params))
                return FakeResult(rowcount=1)
            raise AssertionError(f'unexpected SQL: {sql}')

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())

    result_id = xiaogu_db.upsert_return(
        trade_date=date(2026, 8, 17),
        symbol='600001',
        pick_id=None,
        t1_return=0.025,
        production_run_id='run-817',
        candidate_snapshot_id='snapshot-817',
        settlement_evidence={'provider': 'baostock', 'price_source': 'T_PLUS_1_CLOSE'},
    )

    assert result_id == 314
    assert len(updates) == 1
    payload = json.loads(updates[0]['candidate_settlement_payload'])
    assert payload['t1_return'] == 0.025
    assert payload['return_status'] == 'SETTLED'
    assert payload['settlement_evidence']['provider'] == 'baostock'
    assert updates[0]['production_run_id'] == 'run-817'
    assert updates[0]['candidate_snapshot_id'] == 'snapshot-817'
    assert updates[0]['symbol'] == '600001'


def test_production_return_settlement_rejects_missing_snapshot_candidate(monkeypatch):
    import xiaogu_db

    class FakeResult:
        def __init__(self, row=None, rowcount=1):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            sql = str(query)
            if 'SELECT id, t1_return, candidate_snapshot_id' in sql:
                return FakeResult()
            if 'INSERT INTO returns' in sql:
                return FakeResult((315,))
            if 'UPDATE daily_candidates' in sql:
                return FakeResult(rowcount=0)
            raise AssertionError(f'unexpected SQL: {sql}')

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())

    with pytest.raises(ValueError, match='PRODUCTION_RETURN_CANDIDATE_NOT_FOUND'):
        xiaogu_db.upsert_return(
            trade_date=date(2026, 8, 17),
            symbol='600001',
            pick_id=None,
            t1_return=0.025,
            production_run_id='run-817',
            candidate_snapshot_id='wrong-snapshot',
        )


def test_return_backfill_cli_exposes_configurable_timeouts():
    result = subprocess.run(
        [sys.executable, 'scripts/xiaogu_return_backfill.py', '--help'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '--per-symbol-timeout' in result.stdout
    assert '--batch-soft-timeout' in result.stdout
    assert '--top10-only' in result.stdout


def test_return_backfill_timeout_continues_with_next_symbol(monkeypatch):
    import scripts.xiaogu_return_backfill as backfill

    trade_date = date(2026, 7, 1)
    rows = [
        (trade_date, '000001', 80.0, False, 1, 'run-timeout', 'snapshot-timeout', 201),
        (trade_date, '000002', 79.0, False, 2, 'run-timeout', 'snapshot-timeout', 202),
    ]

    class FakeResult:
        def __init__(self, values):
            self.values = values

        def fetchall(self):
            return self.values

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            sql = str(query)
            return FakeResult(rows if 'FROM daily_candidates dc' in sql else [])

    fetch_calls = []

    def fetch(symbol, _start, _end):
        fetch_calls.append(symbol)
        if symbol == '000001':
            raise backfill.ReturnFetchTimeout('timeout')
        return [
            {'date': '2026-07-01', 'open': 10.0, 'high': 10.0, 'low': 10.0, 'close': 10.0},
            {'date': '2026-07-02', 'open': 10.0, 'high': 10.5, 'low': 10.0, 'close': 10.2},
        ]

    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_canonical_daily_ohlc', fetch)
    monkeypatch.setattr(backfill, 'date', type('FakeDate', (), {'today': staticmethod(lambda: date(2026, 7, 3))}))
    monkeypatch.setattr(backfill.time, 'sleep', lambda _seconds: None)

    stats = backfill.backfill_returns(dry_run=True, per_symbol_timeout_seconds=8, batch_soft_timeout_seconds=600)

    assert fetch_calls == ['000001', '000002']
    assert stats['failure_reasons'] == {'BAOSTOCK_TIMEOUT': 1}
    assert stats['return_backfill_config']['per_symbol_timeout_seconds'] == 8
    assert stats['return_backfill_timeout_gate']['status'] == 'FAIL'
    assert stats['new_success_count'] == 1


def test_return_backfill_success_and_failure_persist_without_overwrite(monkeypatch):
    import xiaogu_db

    stored = {'returns': {}, 'failure_payloads': {}}

    class FakeResult:
        def fetchone(self):
            return (1,)

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params):
            sql = str(query)
            key = (params['trade_date'], params['symbol'])
            if 'INSERT INTO returns' in sql:
                stored['returns'][key] = dict(params)
            elif 'return_backfill_failure' in params.get('payload', ''):
                stored['failure_payloads'][key] = json.loads(params['payload'])['return_backfill_failure']
            return FakeResult()

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())
    trade_date = date(2026, 7, 1)

    xiaogu_db.upsert_return(
        trade_date=trade_date,
        symbol='000001',
        pick_id=None,
        t1_return=0.025,
        next_day_open_return=0.01,
        next_day_high_return=0.04,
        legacy_backfill=True,
    )
    success = stored['returns'][(trade_date, '000001')]
    assert success['t1_return'] == 0.025
    assert success['next_day_open_return'] == 0.01
    assert success['next_day_high_return'] == 0.04

    xiaogu_db.record_return_backfill_failure(trade_date, '000001', 'BAOSTOCK_TIMEOUT')
    failure = stored['failure_payloads'][(trade_date, '000001')]
    assert failure['symbol'] == '000001'
    assert failure['trade_date'] == '2026-07-01'
    assert failure['return_horizon'] == 'T+1'
    assert failure['status'] == 'FAILED'
    assert failure['failure_reason'] == 'BAOSTOCK_TIMEOUT'
    assert failure['last_attempt_at']
    assert failure['payload'] == {'provider': 'baostock', 'error_type': 'BAOSTOCK_TIMEOUT'}
    assert stored['returns'][(trade_date, '000001')]['t1_return'] == 0.025


def test_return_backfill_resume_skips_existing_success(monkeypatch):
    import scripts.xiaogu_return_backfill as backfill

    trade_date = date(2026, 7, 1)

    class FakeResult:
        def __init__(self, values):
            self.values = values

        def fetchall(self):
            return self.values

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            sql = str(query)
            if 'FROM daily_candidates' in sql:
                return FakeResult([
                    (trade_date, '000001', 80.0, False, 1, 'run-resume', 'snapshot-resume', 301),
                ])
            return FakeResult([('run-resume', '000001', 0.01)])

    fetch_calls = []
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_canonical_daily_ohlc', lambda *args: fetch_calls.append(args))
    monkeypatch.setattr(backfill, 'date', type('FakeDate', (), {'today': staticmethod(lambda: date(2026, 7, 3))}))

    stats = backfill.backfill_returns(dry_run=True)

    assert stats['skipped_existing_success_count'] == 1
    assert fetch_calls == []


def test_return_backfill_syncs_existing_return_to_candidate_placeholder(monkeypatch):
    import scripts.xiaogu_return_backfill as backfill

    trade_date = date(2026, 7, 1)
    synced = []

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            sql = str(query)
            if 'FROM daily_candidates dc' in sql:
                return FakeResult([
                    (trade_date, '000001', 80.0, False, 1, 'run-resume', 'snapshot-resume', 301),
                ])
            if 'FROM returns' in sql:
                return FakeResult([
                    ('run-resume', '000001', 0.01, {'provider': 'baostock'}),
                ])
            if 'FROM picks p' in sql:
                return FakeResult([])
            raise AssertionError(f'unexpected SQL: {sql}')

    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'upsert_return', lambda **kwargs: synced.append(kwargs) or 1)
    monkeypatch.setattr(backfill, 'update_production_run_step', lambda *args, **kwargs: None)
    monkeypatch.setattr(backfill.time, 'sleep', lambda _seconds: None)

    stats = backfill.backfill_returns(
        input_trade_date='2026-07-01',
        validation_trade_date='2026-07-02',
        production_run_id='run-resume',
    )

    assert stats['already_filled'] == 1
    assert stats['existing_candidate_label_sync_count'] == 1
    assert synced == [{
        'trade_date': trade_date,
        'symbol': '000001',
        'pick_id': 301,
        't1_return': 0.01,
        't1_labels': {
            't1_open_return': None,
            't1_high_return': None,
            't1_low_return': None,
            't1_close_return': None,
            't1_mfe': None,
            't1_mae': None,
            't1_vwap_return': None,
            't1_gap_return': None,
            't1_net_return': None,
        },
        'production_run_id': 'run-resume',
        'candidate_snapshot_id': 'snapshot-resume',
        'return_status': 'SETTLED',
        'settlement_evidence': {'provider': 'baostock'},
    }]


def test_resolve_pick_id_prefers_paper_pick(monkeypatch):
    import xiaogu_db

    class FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=None):
            assert params['symbol'] == '600001'
            assert params['trade_date'] == date(2026, 7, 21)
            return FakeResult((99,))

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())
    assert xiaogu_db.resolve_pick_id(date(2026, 7, 21), '600001') == 99
    assert xiaogu_db.resolve_pick_id(date(2026, 7, 21), '  ') is None


def test_safe_self_evolve_proposes_only_when_gate_ready(monkeypatch, tmp_path):
    import scripts.xiaogu_safe_self_evolve as evolve

    monkeypatch.setattr(evolve, 'SUMMARY', tmp_path)
    (tmp_path / 'daily_closure_latest.json').write_text(
        json.dumps(
            {
                'cohort_gates': {
                    'production_ranking_change_gate': {
                        'status': 'READY_FOR_PROPOSAL',
                        'selected_shadow_variant': 'limitup_gene_shadow_plus',
                        'allowed_actions': ['apply_bounded_factor_weights'],
                        'self_evolution': {'factor_weight_apply_allowed': True},
                    },
                    'limitup_capture_gate': {'primary_blocker': 'LIMITUP_GENE_UNDERWEIGHTED'},
                    'shadow_ranking_replay': {'selected_candidate_variant': 'limitup_gene_shadow_plus'},
                }
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(evolve, '_get_config_value', lambda key: 0.7)
    closure = evolve._load_closure()
    proposals = evolve.propose_nudges(closure)
    assert proposals
    assert proposals[0]['config_key'] == 'evidence_limitup_momentum_weight'
    assert proposals[0]['direction'] == 'INCREASE'

    (tmp_path / 'daily_closure_latest.json').write_text(
        json.dumps({'cohort_gates': {'production_ranking_change_gate': {'status': 'LOCKED'}}}),
        encoding='utf-8',
    )
    locked = evolve._load_closure()
    assert evolve.propose_nudges(locked) == []
