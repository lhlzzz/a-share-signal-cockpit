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
    failed = social.normalize_social_signal('eastmoney_guba', {'error': 'no_cdp_tab'})

    result = social._store_social_payload(date(2026, 7, 10), '600001', [failed])

    assert result['status'] == 'WARN'
    assert [item[2] for item in written] == ['social_collection_status']
    assert written[0][4]['preserved_existing_social_values'] is True
    assert written[0][4]['collection_errors'][0]['source'] == 'eastmoney_guba'
    assert written[0][4]['collection_errors'][0]['error'] == 'no_cdp_tab'


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
                    'collection_errors': [{'source': 'eastmoney_guba', 'error': 'no_cdp_tab'}],
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


def test_social_collectors_use_direct_public_pages_before_cdp(monkeypatch):
    import xiaogu_social_sentiment as social

    # Primary path is gbapi JSON (not captcha HTML). CDP must not be required.
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
    monkeypatch.setattr(
        social,
        'cdp_get_tabs',
        lambda: pytest.fail('direct collector should not request a CDP tab'),
    )

    guba = social.scrape_eastmoney_guba('600001')

    assert guba['post_count'] == 2
    assert guba['positive_count'] == 2
    assert guba.get('transport') == 'gbapi_articlelist'


def test_social_collectors_fall_back_to_html_when_api_empty(monkeypatch):
    import xiaogu_social_sentiment as social

    guba_html = """
        <div class="title">芯片利好，明天涨停</div>
        <div class="title">资金回流，继续看多</div>
    """

    def fake_fetch(url, *, timeout=15, direct=False, accept=''):
        if 'gbapi.eastmoney.com' in url:
            return json.dumps({'count': 0, 're': []})
        return guba_html

    monkeypatch.setattr(social, '_fetch_public_text', fake_fetch)
    monkeypatch.setattr(
        social,
        'cdp_get_tabs',
        lambda: pytest.fail('html fallback should not request a CDP tab'),
    )

    guba = social.scrape_eastmoney_guba('600001')
    assert guba['post_count'] == 2
    assert guba.get('transport') == 'public_html'


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


def test_daily_pipeline_defaults_social_provider_to_eastmoney_only():
    script = Path('daily_pipeline.sh').read_text(encoding='utf-8')

    obsolete_env_keys = [
        'SOCIAL_' + 'X_PROVIDER',
        'XIAOGU_SOCIAL_' + 'X_HANDLES',
        'XIAOGU_' + 'GR' + 'OK_ENABLED',
    ]
    assert 'SOCIAL_SOURCES="${XIAOGU_SOCIAL_SOURCES:-eastmoney_guba}"' in script
    assert 'SOCIAL_SOURCES="${XIAOGU_SOCIAL_SOURCES:-eastmoney_guba,x}"' not in script
    assert all(key not in script for key in obsolete_env_keys)
    # Gate detection must use compact status-file, not only rg on multi-MB JSON.
    assert '--status-file' in script
    assert 'social_gate=' in script
    assert '--ensure-formal-top10' in script
    assert '补采 social' in script or 'ensure-formal-top10' in script


def test_daily_pipeline_hooks_sszcw_pre_pick_and_safe_self_evolve():
    script = Path('daily_pipeline.sh').read_text(encoding='utf-8')
    assert 'scripts/xiaogu_sszcw_market_context.py' in script
    assert 'scripts/xiaogu_safe_self_evolve.py' in script
    assert 'backfill_return_pick_ids' in script
    # Chain auto-applies when gate READY (not a manual ops checklist)
    assert '--apply-if-ready' in script
    assert 'XIAOGU_SAFE_SELF_EVOLVE_DRY_RUN' in script
    # Soft context must run before runner
    assert script.index('xiaogu_sszcw_market_context.py') < script.index('xiaogu_forward_d1_1450_runner_v0_1.py')
    # T1 validation path also owns self-evolve
    assert script.index('--manual-return-backfill') < script.index('--apply-if-ready')
    # Shadow profit candidates: main LIVE path after runner; T1 path refreshes with --with-returns
    assert 'scripts/xiaogu_profit_candidates_shadow.py' in script
    assert '--compare-official' in script
    assert script.count('xiaogu_profit_candidates_shadow.py') >= 2
    assert '--with-returns' in script
    # Main LIVE path block: runner appears before the post-decision profit shadow step marker
    live_marker = '[5.4/6] 影子获利候选'
    assert live_marker in script
    assert script.index('xiaogu_forward_d1_1450_runner_v0_1.py') < script.index(live_marker)
    assert script.index(live_marker) < script.index('[5.5/6] 有界因子自进化')

def test_forced_candidate_snapshot_archives_then_prunes_stale_rows(monkeypatch, tmp_path):
    import xiaogu_forward_d1_1450_runner_v0_1 as runner

    trade_date = date(2026, 7, 13)
    written = []
    pruned = []
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
    monkeypatch.setattr(
        'xiaogu_db.prune_daily_candidates_to_symbols',
        lambda _date, symbols: pruned.append((_date, symbols)) or 1,
    )

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
    assert result['pruned_stale_count'] == 1
    assert pruned == [(trade_date, ['600001'])]
    archive = json.loads(Path(result['correction_archive']['archive_path']).read_text(encoding='utf-8'))
    assert archive['correction_of'] == 'old-ledger-reference'
    assert archive['rows'] == [{'symbol': '600999'}]
    assert written[0]['symbol'] == '600001'


def test_run_recorder_passes_correction_reference(monkeypatch, tmp_path):
    import xiaogu_forward_d1_1450_runner_v0_1 as runner

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
    import xiaogu_forward_d1_1450_runner_v0_1 as runner

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
    import xiaogu_forward_d1_1450_runner_v0_1 as runner

    summary_path = tmp_path / '2026-07-17' / 'eastmoney_scan_afternoon' / 'eastmoney_web_tabs_summary_runner.json'
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
    bundle_file = bundle_dir / "2026-06-10_eastmoney_web_tabs_v0_1_research_basket_candidate.json"
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
    scan_dir = tmp_path / "scan" / "2026-06-10" / "eastmoney_web_tabs_scan_v0_1"
    scan_dir.mkdir(parents=True)
    scored_file = scan_dir / "eastmoney_web_tabs_scored.jsonl"
    records = [
        {"code": "601816", "name": "京沪高铁", "rank": 1, "score": 67.96,
         "final_score": 67.96, "signal_pct": 1.77, "setup_type": "INTRADAY_ALERT_REVERSAL"},
        {"code": "600210", "name": "紫江企业", "rank": 2, "score": 82.0,
         "final_score": 82.0, "signal_pct": 2.1},
    ]
    with open(scored_file, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')

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

    (morning_dir / "eastmoney_web_tabs_summary_runner.json").write_text(
        json.dumps({
            "source_time": "2026-06-10 09:25:00",
            "paper_scoring_candidates": [{"symbol": "600001", "rank": 1}],
        }),
        encoding="utf-8",
    )
    (afternoon_dir / "eastmoney_web_tabs_summary_runner.json").write_text(
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


def test_return_backfill_next_day_execution_metrics(monkeypatch):
    """Verify T+1 execution metrics are emitted for backtest rows."""
    import scripts.xiaogu_return_backfill as backfill

    class FakeBaoStock:
        def login(self):
            return type('LoginResult', (), {'error_code': '0', 'error_msg': ''})()

        def logout(self):
            return None

    class FakeConn:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            FakeConn.calls += 1
            rows = (
                [(date(2026, 6, 22), '300001', 80.0)]
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

    monkeypatch.setattr(backfill, 'bs', FakeBaoStock())
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'datetime', type('FakeDatetime', (), {'now': staticmethod(lambda: type('Now', (), {'date': lambda self: date(2026, 7, 1)})())}))
    monkeypatch.setattr(backfill, 'fetch_kline_range_baostock', lambda symbol, start, end: klines)
    monkeypatch.setattr(backfill.time, 'sleep', lambda seconds: None)

    stats = backfill.backfill_returns(dry_run=True)
    row = stats['results'][0]

    assert row['next_day_open_return'] == -0.02
    assert row['next_day_close_return'] == 0.04
    assert row['next_day_high_return'] == 0.08
    assert row['next_day_low_return'] == -0.04
    assert row['next_day_gap_return'] == -0.02
    assert row['next_day_drawdown'] == -0.1111
    assert row['high_to_close_retrace'] == -0.037
    assert row['sellable_profit'] == pytest.approx(0.056)
    assert row['sellable_profit_v1_conservative'] == 0.044
    assert row['sellable_profit_v2_normal'] == pytest.approx(0.056)
    assert row['sellable_profit_v3_aggressive'] == pytest.approx(0.056)
    assert row['sell_strategy_used'] == '冲高回落卖'
    assert row['sell_signal_time'] == '10:30'
    assert row['sell_signal_price'] == 10.56
    assert row['max_profit_before_sell'] == 0.08
    assert row['profit_capture_ratio'] == pytest.approx(0.70)
    assert row['missed_profit'] == pytest.approx(0.024)
    assert row['panic_sell_avoided'] is True
    assert row['should_wait_rebound'] is True
    assert row['failure_exit_triggered'] is False


def test_return_backfill_accepts_validation_day_t1_metrics(monkeypatch):
    """Manual --validate-on should fill T+1 metrics on the validation date itself."""
    import scripts.xiaogu_return_backfill as backfill

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 13)

    class FakeBaoStock:
        def login(self):
            return type('LoginResult', (), {'error_code': '0', 'error_msg': ''})()

        def logout(self):
            return None

    class FakeConn:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            FakeConn.calls += 1
            rows = (
                [(date(2026, 7, 10), '002558', 67.17, True, 10)]
                if FakeConn.calls == 1
                else []
            )
            return type('Result', (), {'fetchall': lambda self: rows})()

    klines = [
        {'date': '2026-07-10', 'open': 27.9, 'high': 29.88, 'low': 27.4, 'close': 29.56},
        {'date': '2026-07-13', 'open': 29.01, 'high': 29.35, 'low': 27.5, 'close': 28.48},
    ]

    monkeypatch.setattr(backfill, 'date', FakeDate)
    monkeypatch.setattr(backfill, 'bs', FakeBaoStock())
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_kline_range_baostock', lambda symbol, start, end: klines)
    monkeypatch.setattr(backfill.time, 'sleep', lambda seconds: None)

    stats = backfill.backfill_returns(
        dry_run=True, input_trade_date='2026-07-10', validation_trade_date='2026-07-13',
    )

    assert stats['t1_filled'] == 1
    assert stats['new_success_count'] == 1
    row = stats['results'][0]
    assert row['returns']['t1'] == -0.0365
    assert row['next_day_open_return'] == -0.0186
    assert row['next_day_high_return'] == -0.0071
    assert row['next_day_low_return'] == -0.0697


def test_return_backfill_rejects_mismatched_validation_date():
    import scripts.xiaogu_return_backfill as backfill

    stats = backfill.backfill_returns(
        dry_run=True, input_trade_date='2026-07-09', validation_trade_date='2026-07-13',
    )

    assert stats['fatal_error'] == 'VALIDATION_TRADE_DATE_MISMATCH'
    assert stats['expected_validation_trade_date'] == '2026-07-10'


def test_eastmoney_realtime_ohlc_only_stamps_today(monkeypatch):
    """Realtime quote must not be backdated onto non-today target dates (T+1 pollution)."""
    import scripts.xiaogu_return_backfill as backfill
    from datetime import date

    today = date.today().isoformat()

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data":{"f43":10.0,"f44":11.0,"f45":9.0,"f46":9.5}}'

    calls = {'n': 0}

    def fake_urlopen(*a, **k):
        calls['n'] += 1
        return FakeResp()

    monkeypatch.setattr(backfill.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(backfill, 'secid_for', lambda symbol: '1.600000')

    # Non-today target: refuse without network.
    assert backfill.fetch_eastmoney_realtime_ohlc('600000', '2026-07-21') is None
    assert calls['n'] == 0

    # Today target: allowed to fetch live OHLC.
    row = backfill.fetch_eastmoney_realtime_ohlc('600000', today)
    assert calls['n'] == 1
    assert row == {'date': today, 'open': 9.5, 'high': 11.0, 'low': 9.0, 'close': 10.0}


def test_estimate_sellable_profit_classifies_exit_strategies():
    """Verify sellable-profit rules distinguish key T+1 exit tactics."""
    import scripts.xiaogu_return_backfill as backfill

    cases = [
        (
            '低开等待反弹',
            dict(entry_price=10, open_return=-0.02, high_return=0.03, low_return=-0.035,
                 close_return=0.01, high_to_close_retrace=-0.019, limit_touched=False),
        ),
        (
            '高开冲高卖',
            dict(entry_price=10, open_return=0.025, high_return=0.07, low_return=0.01,
                 close_return=0.03, high_to_close_retrace=-0.037, limit_touched=False),
        ),
        (
            '涨停炸板卖',
            dict(entry_price=10, open_return=0.03, high_return=0.10, low_return=0.01,
                 close_return=0.055, high_to_close_retrace=-0.041, limit_touched=True),
        ),
        (
            '失败止损卖',
            dict(entry_price=10, open_return=-0.015, high_return=0.01, low_return=-0.06,
                 close_return=-0.04, high_to_close_retrace=-0.049, limit_touched=False),
        ),
    ]

    for expected, kwargs in cases:
        profile = backfill.estimate_sellable_profit(**kwargs)
        assert profile['sell_strategy_used'] == expected
        assert profile['sellable_profit'] <= kwargs['high_return']
        assert profile['sellable_profit'] >= kwargs['low_return']


def test_estimate_sellable_profit_never_treats_intraday_high_as_a_certain_fill():
    import scripts.xiaogu_return_backfill as backfill

    profile = backfill.estimate_sellable_profit(
        entry_price=10, open_return=0.01, high_return=0.10, low_return=-0.01,
        close_return=0.03, high_to_close_retrace=-0.07, limit_touched=False,
    )

    assert profile['sellable_profit'] < 0.10
    assert profile['profit_capture_ratio'] <= 0.70


def test_return_backfill_strategy_report_explains_sell_rules(capsys):
    """Verify strategy report names phase-1 sell tactics and risk handling."""
    import scripts.xiaogu_return_backfill as backfill

    stats = {
        'total_picks': 1,
        'already_filled': 0,
        'missing_returns': 1,
        'fetched': 1,
        'fetch_failed': 0,
        't1_filled': 1,
        't2_filled': 0,
        't3_filled': 0,
        't5_filled': 0,
        'results': [
            {
                'returns': {'t1': -0.01},
                'next_day_high_return': 0.06,
                'next_day_limit_touch': False,
                'next_day_open_return': -0.02,
                'next_day_low_return': -0.04,
                'next_day_gap_return': -0.02,
                'next_day_drawdown': -0.09,
                'high_to_close_retrace': -0.04,
                'sellable_profit': 0.04,
                'sellable_profit_v1_conservative': 0.03,
                'sellable_profit_v2_normal': 0.04,
                'sellable_profit_v3_aggressive': 0.05,
                'sell_strategy_used': '低开等待反弹',
                'profit_capture_ratio': 0.6667,
                'panic_sell_avoided': True,
            }
        ],
    }

    backfill.print_analysis(stats)
    output = capsys.readouterr().out

    for text in (
        '高开冲高卖',
        '冲高回落卖',
        '10:00前不强卖',
        '低开不能恐慌卖',
        '等待反弹条件',
        '必须止损条件',
        'RULE-BASED SELLABLE PROFIT',
        'Avg sellable profit',
        'Avg profit capture ratio',
        'Low-open rebound probability',
        'Panic-sell avoided benefit',
        'Sell strategy performance',
    ):
        assert text in output


def test_upsert_return_persists_next_day_execution_metrics(monkeypatch):
    """Verify DB upsert forwards all T+1 execution metric fields."""
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
    )

    for field in (
        't1_return_close',
        'next_day_open_return',
        'next_day_high_return',
        'next_day_low_return',
        'next_day_gap_return',
        'next_day_drawdown',
        'high_to_close_retrace',
    ):
        assert field in captured['sql']
        assert field in captured['params']
    # pick_id is auto-resolved when caller passes None
    assert captured['params']['pick_id'] == 42
    assert 'COALESCE(EXCLUDED.pick_id, returns.pick_id)' in captured['sql']


def test_return_backfill_cli_exposes_configurable_timeouts():
    result = subprocess.run(
        [sys.executable, 'scripts/xiaogu_return_backfill.py', '--help'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '--per-symbol-timeout' in result.stdout
    assert '--batch-soft-timeout' in result.stdout


def test_return_backfill_timeout_continues_with_next_symbol(monkeypatch):
    import scripts.xiaogu_return_backfill as backfill

    trade_date = date(2026, 7, 1)
    rows = [
        (trade_date, '000001', 80.0, False, 1),
        (trade_date, '000002', 79.0, False, 2),
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
            return FakeResult(rows if 'FROM daily_candidates' in sql else [])

    class FakeBaoStock:
        def login(self):
            return type('LoginResult', (), {'error_code': '0', 'error_msg': ''})()

        def logout(self):
            return None

    fetch_calls = []

    def fetch(symbol, _start, _end):
        fetch_calls.append(symbol)
        if symbol == '000001':
            raise backfill.ReturnFetchTimeout('timeout')
        return [
            {'date': '2026-07-01', 'open': 10.0, 'high': 10.0, 'low': 10.0, 'close': 10.0},
            {'date': '2026-07-02', 'open': 10.0, 'high': 10.5, 'low': 10.0, 'close': 10.2},
        ]

    monkeypatch.setattr(backfill, 'bs', FakeBaoStock())
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_kline_range_baostock', fetch)
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

    monkeypatch.setattr(xiaogu_db, 'get_db', lambda: FakeDb())
    trade_date = date(2026, 7, 1)

    xiaogu_db.upsert_return(
        trade_date=trade_date,
        symbol='000001',
        pick_id=None,
        t1_return=0.025,
        next_day_open_return=0.01,
        next_day_high_return=0.04,
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
                return FakeResult([(trade_date, '000001', 80.0, False, 1)])
            return FakeResult([(trade_date, '000001', 0.01, 0.02, 0.03, 0.05, 0.02)])

    class FakeBaoStock:
        def login(self):
            return type('LoginResult', (), {'error_code': '0', 'error_msg': ''})()

        def logout(self):
            return None

    fetch_calls = []
    monkeypatch.setattr(backfill, 'bs', FakeBaoStock())
    monkeypatch.setattr(backfill, 'engine', type('FakeEngine', (), {'connect': lambda self: FakeConn()})())
    monkeypatch.setattr(backfill, 'fetch_kline_range_baostock', lambda *args: fetch_calls.append(args))
    monkeypatch.setattr(backfill, 'date', type('FakeDate', (), {'today': staticmethod(lambda: date(2026, 7, 3))}))

    stats = backfill.backfill_returns(dry_run=True)

    assert stats['skipped_existing_success_count'] == 1
    assert fetch_calls == []


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


def test_sszcw_market_context_soft_only_and_favors_defensive_rotation(tmp_path, monkeypatch):
    import scripts.xiaogu_sszcw_market_context as sszcw

    monkeypatch.setattr(sszcw, 'DATA_DIR', tmp_path / 'data_sszcw')
    monkeypatch.setattr(sszcw, 'SUMMARY_DIR', tmp_path / 'summary')
    monkeypatch.setattr(sszcw, 'LIVE_INBOX_PATH', tmp_path / 'data_sszcw' / 'live_inbox.jsonl')
    # Seed-only soft path: do not depend on network scrapy live posts.
    monkeypatch.setattr(sszcw, 'fetch_live_posts', lambda asof, limit=30: ([], []))
    payload = sszcw.build_context(date(2026, 7, 22), days=5, seed=True, prefer_live=True)
    paths = sszcw.write_outputs(payload, date(2026, 7, 22))

    assert payload['selected_for_production'] is False
    assert payload['production_mutation_allowed'] is False
    assert payload['usage']['hard_gate'] is False
    assert payload['usage']['force_pick'] is False
    assert payload['usage']['soft_sector_bias'] is True
    assert payload['post_count'] >= 1
    assert any(theme in payload['favored_sectors'] for theme in ('有色', '油气', '贵金属', '电力', '煤炭', '医药'))
    assert Path(paths['dated']).exists()
    assert Path(paths['summary_latest']).exists()
    # Seed must not pollute durable dated cache when only seed is available.
    assert payload.get('used_seed_fallback') is True
    assert not (tmp_path / 'data_sszcw' / 'posts_20260722.jsonl').exists()
    assert (tmp_path / 'data_sszcw' / 'posts_seed_snapshot.jsonl').exists()


def test_sszcw_live_inbox_counts_as_live_not_seed(tmp_path, monkeypatch):
    import scripts.xiaogu_sszcw_market_context as sszcw

    data_dir = tmp_path / 'data_sszcw'
    data_dir.mkdir(parents=True)
    inbox = data_dir / 'live_inbox.jsonl'
    inbox.write_text(
        json.dumps(
            {
                'id': 'inbox-1',
                'created_at': '2026-07-22T12:00:00+08:00',
                'text': '铜还会涨，像石油，黄金，有色这些与期货强关联。电力也在防守。',
            },
            ensure_ascii=False,
        )
        + '\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(sszcw, 'DATA_DIR', data_dir)
    monkeypatch.setattr(sszcw, 'SUMMARY_DIR', tmp_path / 'summary')
    monkeypatch.setattr(sszcw, 'LIVE_INBOX_PATH', inbox)
    monkeypatch.setattr(sszcw, 'fetch_live_posts', lambda asof, limit=30: ([], []))
    payload = sszcw.build_context(date(2026, 7, 22), days=5, seed=True, prefer_live=True)
    # inbox source is counted as live (not seed); no X API needed.
    assert payload['live_inbox_count'] == 1
    assert payload['live_post_count'] >= 1
    assert payload.get('used_seed_fallback') is False
    assert payload.get('soft_context_source') == 'live'
    assert payload['seed_post_count'] == 0
    assert (data_dir / 'posts_20260722.jsonl').exists()


def test_social_context_supports_multiple_verified_handles(tmp_path, monkeypatch):
    import scripts.xiaogu_sszcw_market_context as sszcw

    monkeypatch.setattr(sszcw, 'DATA_DIR', tmp_path / 'data_sszcw')
    monkeypatch.setattr(sszcw, 'SUMMARY_DIR', tmp_path / 'summary')
    monkeypatch.setattr(sszcw, 'LIVE_INBOX_PATH', tmp_path / 'data_sszcw' / 'live_inbox.jsonl')

    requested = []

    def fake_fetch_live_posts(asof, limit=40, handles=None):
        requested.append(tuple(handles or []))
        return (
            [
                {
                    'id': '1',
                    'created_at': '2026-07-28T10:00:00+08:00',
                    'text': '电力防守，半导体风险。',
                    'kind': 'post',
                    'source': 'x_api:sszcw',
                    'author_handle': 'sszcw',
                },
                {
                    'id': '2',
                    'created_at': '2026-07-28T10:05:00+08:00',
                    'text': '有色还会涨。',
                    'kind': 'reply',
                    'source': 'x_api:naiyin04',
                    'author_handle': 'naiyin04',
                    'parent_text': '有色怎么看',
                },
                {
                    'id': '3',
                    'created_at': '2026-07-28T10:10:00+08:00',
                    'text': '银行可以看稳一点。',
                    'kind': 'reply',
                    'source': 'x_api:andredavid90',
                    'author_handle': 'andredavid90',
                    'parent_text': '银行怎么走',
                },
            ],
            [],
        )

    monkeypatch.setattr(sszcw, 'fetch_live_posts', fake_fetch_live_posts)
    payload = sszcw.build_context(
        date(2026, 7, 28),
        days=5,
        seed=False,
        prefer_live=True,
        handles=('sszcw', 'naiyin04', 'andredavid90'),
    )
    assert requested == [('sszcw', 'naiyin04', 'andredavid90')]
    assert payload['handle_count'] == 3
    assert payload['handles'] == ['andredavid90', 'naiyin04', 'sszcw']
    assert len(payload['accounts']) == 3
    assert any(account['handle'] == 'naiyin04' for account in payload['accounts'])
    assert '电力' in payload['favored_sectors'] or '有色' in payload['favored_sectors'] or '银行' in payload['favored_sectors']


def test_social_context_scopes_requested_handle_before_analysis(tmp_path, monkeypatch):
    import scripts.xiaogu_sszcw_market_context as sszcw

    data_dir = tmp_path / 'data_sszcw'
    data_dir.mkdir(parents=True)
    (data_dir / 'posts_20260730.jsonl').write_text(
        '\n'.join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                {
                    'id': 'sszcw-1',
                    'created_at': '2026-07-30T10:00:00+08:00',
                    'text': '白酒还能涨一阵子。',
                    'source': 'cdp_live',
                    'author_handle': 'sszcw',
                },
                {
                    'id': 'other-1',
                    'created_at': '2026-07-30T10:01:00+08:00',
                    'text': '半导体继续看多。',
                    'source': 'cdp_live',
                    'author_handle': 'naiyin04',
                },
            )
        )
        + '\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(sszcw, 'DATA_DIR', data_dir)
    monkeypatch.setattr(sszcw, 'SUMMARY_DIR', tmp_path / 'summary')
    monkeypatch.setattr(sszcw, 'LIVE_INBOX_PATH', data_dir / 'live_inbox.jsonl')
    monkeypatch.setattr(sszcw, 'fetch_live_posts', lambda asof, limit=40, handles=None: ([], []))

    payload = sszcw.build_context(
        date(2026, 7, 30),
        days=3,
        seed=False,
        prefer_live=False,
        handles=('sszcw',),
    )

    assert payload['handles'] == ['sszcw']
    assert payload['post_count'] == 1
    assert all(row['author_handle'] == 'sszcw' for row in payload['excerpts'])


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
    assert evolve.should_apply_if_ready(evolve._gate(closure)) is True

    (tmp_path / 'daily_closure_latest.json').write_text(
        json.dumps({'cohort_gates': {'production_ranking_change_gate': {'status': 'LOCKED'}}}),
        encoding='utf-8',
    )
    locked = evolve._load_closure()
    assert evolve.propose_nudges(locked) == []
    assert evolve.should_apply_if_ready(evolve._gate(locked)) is False
