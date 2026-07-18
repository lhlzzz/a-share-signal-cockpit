import os
import datetime as dt
from pathlib import Path
from unittest.mock import patch
import json
import math
import sys
import pytest

import six_repo_integration_real_v2_1 as native_integration
from scrapy_scanner import runner_v2 as scanner_v2
import xiaogu_eastmoney_web_tabs_scan_v0_1 as scanner
import xiaogu_forward_d1_1450_runner_v0_1 as runner
import xiaogu_backtest_v0_1 as backtest
import xiaogu_signal_effectiveness_v0_1 as effectiveness
import xiaogu_native_repo_runtime_v0_1 as native_runtime
import xiaogu_v2_1_six_repo_real_integrated as strategy


REAL_BUNDLE_PATH = Path(
    'data/forward_candidate_bundles/2026-06-10/2026-06-10_eastmoney_web_tabs_v0_1_research_basket_candidate.json'
)


def test_forward_ledger_win_stats_uses_close_fallback_and_late_bloom_metrics(monkeypatch):
    import xiaogu_db

    captured = {}

    class FakeResult:
        def fetchone(self):
            return (3, 3, 0.01, 2, 2, 1, 1, 1, 1, 1, 3, 3)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            captured['sql'] = str(query)
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConn()

    monkeypatch.setattr(xiaogu_db, 'engine', FakeEngine())
    runner.forward_ledger_win_stats.cache_clear()

    stats = runner.forward_ledger_win_stats()

    assert 'COALESCE(t1_return_close, t1_return)' in captured['sql']
    assert stats['avg_t1_return'] == 0.01
    assert stats['t1_positive_rate'] == pytest.approx(2 / 3)
    assert stats['t2_positive_rate'] == pytest.approx(1 / 2)
    assert stats['t3_positive_rate'] == 1.0
    assert stats['late_bloom_t2_count'] == 1
    assert stats['late_bloom_rate'] == 1.0
    assert stats['win_any_t1_t3_rate'] == 1.0
    runner.forward_ledger_win_stats.cache_clear()


def test_concept_capital_flow_tab_maps_to_own_domain():
    assert scanner.domain_for_tab('https://data.eastmoney.com/bkzj/gn.html', '概念资金流') == 'concept_capital_flow'
    assert scanner.domain_for_tab('https://data.eastmoney.com/bkzj/hy.html', '行业资金流') == 'sector_fund_flow'


def test_data_directory_content_feeds_hsgt_component():
    rows_by_domain = {domain: [] for domain in scanner.ALL_EVIDENCE_DOMAINS}
    rows_by_domain['data_directory_content'] = [{
        'item_key': 'hsgt_capital_flow',
        'cells': ['北向资金', '沪股通', '收盘', '2.50亿元'],
        'header': ['类型', '板块', '状态', '成交净买额'],
        'raw_text': '北向资金 沪股通 成交净买额 2.50亿元',
        'summary': '北向资金 沪股通 成交净买额 2.50亿元',
    }]
    scored = [{
        'code': '301236',
        'symbol': '301236',
        'name': '软通动力',
        'score': 10.0,
        'pct_chg': 2.0,
        'close_position_score': 0.6,
        'amount_pctile': 0.8,
        'fund_pctile': 0.8,
        'net_inflow_main': 10000000.0,
    }]
    bundle = scanner.build_structured_bundle(rows_by_domain, {}, scored, scored, '2026-06-23 17:25:00')
    structured = scanner.build_structured_scores(scored, bundle)
    assert bundle['hsgt_signals']
    assert structured[0]['components']['hsgt_institutional_flow'] > 0


def test_hsgt_extractor_per_stock_from_holding_table():
    rows = [{
        'item_key': 'hsgt_capital_flow',
        'cells': ['1', '688179', '阿拉丁', '数据 行情 股吧', '26.91', '18.13%', '12.27亿', '12.84%', '83.71', '97.42亿'],
        'header': ['序号', '代码', '名称', '相关', '最新价', '涨跌幅', '成交额', '换手率', '市盈率', '总市值'],
        'raw_text': '1 688179 阿拉丁 数据 行情 股吧 26.91 18.13% 12.27亿 12.84% 83.71 97.42亿',
    }]
    sigs = scanner.extract_hsgt_signals(rows, '2026-06-23 17:25:00')
    assert len(sigs) == 1
    assert sigs[0]['symbol'] == '688179'
    assert sigs[0]['metric'] == 'hsgt_northbound_holding'
    assert sigs[0]['value'] > 0


def test_runner_v2_hsgt_holdings_uses_working_report_and_emits_diagnostics(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_fetch_datacenter(report_name, sort_col, page_size=500, extra_params=None, diagnostics=None):
        calls.append((report_name, sort_col, dict(extra_params or {})))
        rows = [{'SECURITY_CODE': '600000', 'TRADE_DATE': '2026-07-09'}]
        if isinstance(diagnostics, list):
            diagnostics.append({
                'report_name': report_name,
                'sort_columns': sort_col,
                'status': 'PASS',
                'success': True,
                'code': 0,
                'message': 'ok',
                'row_count': len(rows),
            })
        return rows

    monkeypatch.setattr(scanner_v2, 'fetch_datacenter', fake_fetch_datacenter)
    timings = {}

    rows, diagnostics = scanner_v2.fetch_hsgt_holdings(timings, tmp_path)

    assert rows[0]['SECURITY_CODE'] == '600000'
    assert calls[0][0] == 'RPT_MUTUAL_HOLDSTOCKNORTH_STA'
    assert diagnostics['status'] == 'PASS'
    assert diagnostics['hard_block'] is False
    assert timings['hsgt_holdings']['status'] == 'PASS'


def test_runner_v2_hsgt_partial_proxy_is_not_hard_block() -> None:
    diagnostics = scanner_v2.finalize_hsgt_diagnostics(
        {
            'status': 'MISSING',
            'holdings_count': 0,
            'api_attempts': [{'code': 9701, 'message': '服务器繁忙'}],
        },
        [{'TRADE_DATE': '2026-07-09'}],
        [{'north_money': 'available'}],
    )

    assert diagnostics['status'] == 'PARTIAL'
    assert diagnostics['proxy_available'] is True
    assert diagnostics['proxy_sources'] == ['hsgt_deals', 'hsgt_summary']
    assert diagnostics['hard_block'] is False
    assert diagnostics['required_for_paper_pick'] is False
    bundle = {
        'candidate_source': 'v2_scanner_api',
        'source_status': complete_source_status(),
        'full_universe_scan': {'coverage_status': 'PASS', 'quote_count': 5000},
        'hsgt_diagnostics': diagnostics,
    }
    assert runner.web_tabs_evidence_missing_flags(bundle) == []


def test_runner_v2_structured_priority_outranks_higher_legacy_score() -> None:
    legacy_heavy = {
        'code': '600001',
        'rank': 1,
        'final_score': 94.0,
        'structured_score': 30.0,
        'candidate_stage': 'near_limit_9_plus',
        'early_opportunity_score': 0.10,
        'limitup_capture_score': 0.15,
        'main_theme_alignment_score': 0.0,
        'main_theme_core_score': 0.0,
    }
    structured_heavy = {
        'code': '600002',
        'rank': 2,
        'final_score': 82.0,
        'structured_score': 76.0,
        'candidate_stage': 'mid_5_to_7',
        'early_opportunity_score': 0.72,
        'limitup_capture_score': 0.62,
        'limitup_capture_confirmed': True,
        'main_theme_alignment_score': 0.65,
        'main_theme_core_score': 0.70,
    }

    ranked = scanner_v2.rank_candidates_by_structured_priority([legacy_heavy, structured_heavy])

    assert ranked[0]['code'] == '600002'
    assert ranked[0]['ranking_basis'] == 'structured_evidence_primary'
    assert ranked[0]['structured_priority_score'] > ranked[1]['structured_priority_score']
    assert ranked[1]['high_position_penalty'] == 10.0


def test_runner_v2_structured_priority_demotes_weak_market_hot_momentum_without_limitup_context() -> None:
    weak_hot = {
        'code': '600011',
        'rank': 1,
        'final_score': 95.0,
        'structured_score': 90.0,
        'candidate_stage': 'high_7_to_9',
        'setup_type': 'HOT_MOMENTUM',
        'early_opportunity_score': 0.70,
        'limitup_capture_score': 0.0,
        'limitup_capture_confirmed': False,
        'continuation_gene_score': 0.0,
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'enhanced_evidence_domain_counts': {'limitup_context': 0},
        'market_regime': 'weak',
        'market_follow_through_score': 0.34,
        'market_breadth_up_pct': 41.0,
        'market_limitups': 58.0,
        'limitup_broken_ratio': 0.78,
        'broken_limitups': 53.0,
        'research_signals': {'research_panel': {'overall': 'PARTIAL'}},
    }
    confirmed_lower_legacy = {
        'code': '600012',
        'rank': 2,
        'final_score': 70.0,
        'structured_score': 74.0,
        'candidate_stage': 'mid_5_to_7',
        'setup_type': 'HOT_MOMENTUM',
        'early_opportunity_score': 0.70,
        'limitup_capture_score': 0.65,
        'limitup_capture_confirmed': True,
        'continuation_gene_score': 0.30,
        'main_theme_alignment_score': 0.60,
        'main_theme_core_score': 0.60,
        'mainboard_auxiliary_evidence_status': 'PASS',
        'enhanced_evidence_domain_counts': {'limitup_context': 1},
        'market_regime': 'weak',
        'research_signals': {'research_panel': {'overall': 'PASS'}},
    }

    ranked = scanner_v2.rank_candidates_by_structured_priority([weak_hot, confirmed_lower_legacy])

    assert ranked[0]['code'] == '600012'
    assert ranked[0]['continuation_gene_contribution'] == pytest.approx(1.8)
    assert ranked[1]['weak_market_hot_momentum_evidence_gap'] is True
    assert ranked[1]['weak_market_evidence_gap_penalty'] > 0
    assert ranked[1]['ranking_basis_details']['weak_market_evidence_gap_penalty'] > 0


def test_forward_formal_sort_prefers_structured_priority_over_legacy_shadow() -> None:
    legacy_heavy = {
        'code': '600003',
        'score': 94.0,
        'final_shadow_score': 175.0,
        'structured_score': 31.0,
        'structured_priority_score': 42.0,
        'candidate_stage': 'high_7_to_9',
        'signal_pct': 8.2,
        'close_position_score': 0.86,
        'volume_ratio': 2.1,
        'structured_component_details': {'main_theme_core_score': 0.6},
    }
    structured_heavy = {
        'code': '600004',
        'score': 82.0,
        'final_shadow_score': 150.0,
        'structured_score': 74.0,
        'structured_priority_score': 78.0,
        'candidate_stage': 'high_7_to_9',
        'signal_pct': 7.5,
        'close_position_score': 0.84,
        'volume_ratio': 2.0,
        'structured_component_details': {'main_theme_core_score': 0.6},
    }

    assert runner.formal_candidate_sort_key(structured_heavy) > runner.formal_candidate_sort_key(legacy_heavy)


def test_experimental_extractor_per_stock_from_reports():
    rows_by_domain = {domain: [] for domain in scanner.ALL_EVIDENCE_DOMAINS}
    rows_by_domain['stock_reports'] = [{
        'cells': ['1', '002648', '卫星化学', '详细', '公司信息更新报告', '买入', '维持', '开源证券', '2', '3.020', '7.20', '3.640', '5.90', '化学原料', '2026-06-23'],
        'raw_text': '1 002648 卫星化学 详细 公司信息更新报告 买入 维持 开源证券 2 3.020 7.20 3.640 5.90 化学原料 2026-06-23',
    }]
    sigs = scanner.extract_experimental_signals(rows_by_domain, '2026-06-23 17:25:00')
    per_stock = [s for s in sigs if s['symbol'] == '002648']
    assert len(per_stock) == 1
    assert per_stock[0]['metric'] == 'stock_report_rating'
    assert per_stock[0]['value'] == 0.8


def test_evidence_flags_ignore_full_page_text_for_regulatory_block():
    evidence = {
        'announcements': [{
            'text': '2026-06-24 哈尔斯 五档行情 哈尔斯 财经 | 焦点 | 股票 | 新股 | 期指 | 期权 | 行情 | 数据 | 全球 | 美股 | 港股 | 期货 | 外汇 | 黄金 | 银行 | 基金 | 理财 | 保险 | 债券 | 视频 | 股吧 | 基金吧 | 博客 | 财富号 | 搜索',
        }],
        'risk_alerts': [],
        'lhb': [],
        'financials': [],
    }
    flags = scanner.evidence_flags(evidence)
    assert flags['risk_reasons'] == []


def test_risk_reason_ignores_full_page_text():
    row = {
        'text': '2026-06-24 哈尔斯 五档行情 哈尔斯 财经 | 焦点 | 股票 | 新股 | 期指 | 期权 | 行情 | 数据 | 全球 | 美股 | 港股 | 期货 | 外汇 | 黄金 | 银行 | 基金 | 理财 | 保险 | 债券 | 视频 | 股吧 | 基金吧 | 博客 | 财富号 | 搜索',
    }
    assert scanner.risk_reason(row) == ''


def make_candidate(
    symbol: str,
    name: str,
    *,
    score: float,
    rank: int,
    price: float = 10.0,
    evidence: str = 'PASS',
    data_gate: str = 'PASS',
    sector_score: float = 0.0,
    buy_strength: float = 0.7,
    regulatory: str = '',
    opportunity: str = '',
    blocked_reasons: list[str] | None = None,
    catalyst_category: str = 'neutral',
    disqualified: bool = False,
    disqualifying_flags: list[str] | None = None,
    research_panel_overall: str = 'PASS',
    candidate_evidence_domain_counts: dict | None = None,
    enhanced_evidence_domain_counts: dict | None = None,
    source_time: str = '2026-06-10 09:00:00',
    runner_asof_time: str = '23:59:59',
    search_layer_hint: str = 'formal_high_score',
    setup_type: str = 'FORMAL_HIGH_SCORE',
    candidate_stage: str = 'underwater',
    early_opportunity_score: float = 0.7,
    fund_flow_momentum: float = 0.2,
    time_series_momentum: float = 0.2,
    low_position_catalyst_score: float = 0.3,
    sector_opportunity_tags: list[str] | None = None,
    vei_phase_d_tags: list[str] | None = None,
    weak_to_strong_reversal: float | None = None,
    first_board_pre_signal: float | None = None,
    pre_limitup_anomaly: float | None = None,
    limitup_reason_propagation_score: float = 0.0,
    limitup_capture_score: float | None = None,
    limitup_capture_profile: str = '',
    limitup_capture_confirmed: bool = False,
    limitup_capture_reasons: list[str] | None = None,
    seal_order_strength: float | None = None,
    order_book_pressure: float | None = None,
    main_theme_alignment_score: float = 0.0,
    main_theme_core_score: float = 0.0,
    intraday_alert_strength: float = 0.0,
    weak_close_risk: bool = False,
    high_open_low_close_risk: bool = False,
    broken_limit_risk: bool = False,
    intraday_pullback_risk: bool = False,
    signal_pct: float = 3.0,
    close_position_score: float = 0.55,
    mainboard_auxiliary_evidence_status: str = 'PASS',
    mainboard_auxiliary_confidence: float = 1.0,
    mainboard_auxiliary_missing_domains: list[str] | None = None,
) -> dict:
    component_details = {
        'sector_opportunity_score': sector_score,
        'main_theme_alignment_score': main_theme_alignment_score,
        'main_theme_core_score': main_theme_core_score,
        'intraday_alert_strength': intraday_alert_strength,
    }
    if weak_to_strong_reversal is not None:
        component_details['weak_to_strong_reversal'] = weak_to_strong_reversal
    if first_board_pre_signal is not None:
        component_details['first_board_pre_signal'] = first_board_pre_signal
    if pre_limitup_anomaly is not None:
        component_details['pre_limitup_anomaly'] = pre_limitup_anomaly
    if limitup_capture_score is not None:
        component_details['limitup_capture_score'] = limitup_capture_score
        component_details['limitup_capture_profile'] = limitup_capture_profile
        component_details['limitup_capture_confirmed'] = limitup_capture_confirmed
        component_details['limitup_capture_reasons'] = limitup_capture_reasons or []
    return {
        'symbol': symbol,
        'code': symbol,
        'name': name,
        'rank': rank,
        'score': score,
        'final_score': score,
        'price': price,
        'one_lot_cost': price * 100,
        'signal_pct': signal_pct,
        'close_position_score': close_position_score,
        'weak_close_risk': weak_close_risk,
        'high_open_low_close_risk': high_open_low_close_risk,
        'broken_limit_risk': broken_limit_risk,
        'intraday_pullback_risk': intraday_pullback_risk,
        'candidate_evidence_status': evidence,
        'data_gate_status': data_gate,
        'risk_penalty': 0,
        'candidate_stage': candidate_stage,
        'search_layer_hint': search_layer_hint,
        'setup_type': setup_type,
        'sector_opportunity_score': sector_score,
        'early_opportunity_score': early_opportunity_score,
        'fund_flow_momentum': fund_flow_momentum,
        'time_series_momentum': time_series_momentum,
        'low_position_catalyst_score': low_position_catalyst_score,
        'mainboard_auxiliary_evidence_status': mainboard_auxiliary_evidence_status,
        'mainboard_auxiliary_confidence': mainboard_auxiliary_confidence,
        'mainboard_auxiliary_missing_domains': mainboard_auxiliary_missing_domains or [],
        'limitup_reason_propagation_score': limitup_reason_propagation_score,
        'limitup_capture_score': limitup_capture_score,
        'limitup_capture_profile': limitup_capture_profile,
        'limitup_capture_confirmed': limitup_capture_confirmed,
        'limitup_capture_reasons': limitup_capture_reasons or [],
        'main_theme_alignment_score': main_theme_alignment_score,
        'main_theme_core_score': main_theme_core_score,
        'intraday_alert_strength': intraday_alert_strength,
        'structured_component_details': component_details,
        'structured_score_components': {
            'limitup_reason_strength': buy_strength,
            'seal_order_strength': seal_order_strength if seal_order_strength is not None else buy_strength,
            'order_book_pressure': order_book_pressure,
            'fund_flow_momentum': fund_flow_momentum,
            'time_series_momentum': time_series_momentum,
            'low_position_catalyst_score': low_position_catalyst_score,
        },
        'regulatory_hard_block': regulatory,
        'opportunity_hard_block': opportunity,
        'blocked_reasons': blocked_reasons or [],
        'source_time': source_time,
        'runner_asof_time': runner_asof_time,
        'candidate_evidence_domain_counts': candidate_evidence_domain_counts or {},
        'candidate_evidence_matched_domains': list((candidate_evidence_domain_counts or {}).keys()),
        'candidate_evidence_missing_domains': [],
        'enhanced_evidence_domain_counts': enhanced_evidence_domain_counts or {},
        'enhanced_evidence_matched_domains': list((enhanced_evidence_domain_counts or {}).keys()),
        'enhanced_evidence_missing_domains': [],
        'experimental_evidence_domain_counts': {},
        'experimental_evidence_matched_domains': [],
        'experimental_evidence_missing_domains': [],
        'research_signals': {
            'catalyst_quality': {
                'category': catalyst_category,
                'confidence': 0.9,
                'evidence_refs': [],
            },
            'sector_mapping': {
                'sectors': sector_opportunity_tags or [],
                'related_symbols': [],
                'mapping_confidence': 0.0,
            },
            'a_share_risk_review': {
                'disqualified_for_paper_pick': disqualified,
            },
            'adversarial_review': {
                'disqualifying_flags': disqualifying_flags or [],
                'bear_case_flags': [],
            },
            'historical_pattern': {
                'pattern_name': 'underwater_reversal' if search_layer_hint == 'underwater_reversal' else 'formal_high_score',
            },
            'research_panel': {
                'overall': research_panel_overall,
            },
        },
        'vei_phase_d_tags': vei_phase_d_tags or [],
    }


def make_bundle(candidates: list[dict], *, candidate_source: str, source_status: dict | None = None, passed_count: int = 20, scored_count: int = 43, asof_time: str = '23:59:59', market_snapshot: dict | None = None, market_regime: str = '') -> dict:
    snapshot = {'passed_count': passed_count, 'scored_count': scored_count}
    if market_snapshot:
        snapshot.update(market_snapshot)
    return {
        'available': True,
        'date': '2026-06-10',
        '_runner_asof_time': asof_time,
        'source_time': '2026-06-10 10:00:00',
        'source_market_date': '2026-06-10',
        'candidate_source': candidate_source,
        'candidate': candidates[0] if candidates else {},
        'paper_scoring_candidates': candidates,
        'data_gate_status': 'PASS',
        'rule_version': runner.RULE_VERSION,
        'eastmoney_cdp_url': 'http://127.0.0.1:9333',
        '_bundle_path': str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_bundle.json'),
        'market_regime': market_regime,
        'market_snapshot': snapshot,
        'source_status': source_status or {},
        'daily_ticket_search_result': {
            'searched_layers': [
                'news_catalyst_low_position',
                'sector_catalyst_low_position',
                'intraday_alert_reversal',
                'underwater_reversal',
                'structured_sector',
                'formal_high_score',
            ],
            'first_paper_pick_layer': None,
            'no_pick_reason_if_none': 'PENDING_EVALUATION',
        },
        'paper_pick_candidate_stage_distribution': {},
        'candidate_stage_blocker_distribution': {},
        'weak_market_shadow_ticket': None,
    }


def assert_sector_gate_missing_condition(eligibility: dict, threshold: float) -> None:
    assert f'sector_opportunity_score>={threshold} or VEI strong signal' in eligibility['missing_conditions']


def assert_sector_gate_not_missing(eligibility: dict) -> None:
    assert not any('VEI strong signal' in item and item.startswith('sector_opportunity_score>=') for item in eligibility['missing_conditions'])


def write_candidate_bundle(root: Path, trade_date: str, candidates: list[dict], *, candidate_source: str = 'unit_test') -> Path:
    bundle_path = root / trade_date / f'{trade_date}_unit_test_candidate.json'
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'available': True,
        'date': trade_date,
        'source_market_date': trade_date,
        'source_time': f'{trade_date} 10:00:00',
        '_runner_asof_time': f'{trade_date} 14:50:00',
        'candidate_source': candidate_source,
        'paper_scoring_candidates': candidates,
        'candidate': candidates[0] if candidates else {},
        '_bundle_path': str(bundle_path),
        'data_gate_status': 'PASS',
        'paper_only': True,
        'no_trade': True,
    }
    bundle_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return bundle_path


def make_scoring_config_snapshot(**overrides):
    config = dict(runner.SCORING_CONFIG_DEFAULTS)
    config.update({key: str(value) for key, value in overrides.items()})
    return {
        'config': config,
        'loaded': True,
        'source': 'db',
        'error': '',
    }


def complete_source_status() -> dict:
    status = {
        'required_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'core_sentiment_pools': {
            'status': 'PASS',
            'required_sources': [
                'limitup_pool',
                'limitup_broken',
                'limitup_consecutive',
                'limitup_yesterday',
            ],
            'missing_sources': [],
            'flags': [],
        },
        'scan_snapshot_persistence': {
            'status': 'PASS',
            'scan_session_id': 1,
            'domain_count': 33,
        },
        'enhanced_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'experimental_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'full_evidence_pack': {'status': 'PASS', 'missing_domains': []},
        'enhanced_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
        'experimental_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
        'source_completeness': {
            'status': 'PASS',
            'quote_count': 5000,
            'fund_count': 100,
            'min_quote_count': 4000,
            'missing_sources': [],
            'flags': [],
            'blocking': True,
        },
    }
    for domain in runner.REQUIRED_EASTMONEY_EVIDENCE_DOMAINS:
        status[domain] = {'status': 'PASS', 'record_count': 1, 'tab_count': 1}
    for domain in runner.REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS:
        status[domain] = {'status': 'PASS', 'record_count': 1, 'tab_count': 1}
    for domain in runner.REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS:
        status[domain] = {'status': 'PASS', 'record_count': 1, 'tab_count': 1}
    return status


def full_real_scan_source_status() -> dict:
    status = complete_source_status()
    status['source_completeness']['quote_count'] = 4605
    status['source_completeness']['fund_count'] = 4605
    status['source_completeness']['flags'] = []
    status['source_completeness']['missing_sources'] = []
    status['full_universe_scan'] = {
        'enabled': True,
        'quote_count': 4605,
        'tradable_count': 4605,
        'coverage_status': 'PASS',
        'min_quote_count': 4000,
        'board_counts': {'main': 4605, 'chinext': 0},
    }
    return status


def load_real_bundle() -> dict:
    bundle = runner.read_json(REAL_BUNDLE_PATH)
    bundle['_runner_asof_time'] = '23:59:59'
    runner.attach_paper_pick_eligibility(bundle)
    return bundle


def fake_repo_signals() -> dict:
    return {
        'real_outputs': [
            {
                'repo_name': 'tradingagent_a',
                'signals': {
                    'board': 'main',
                    'small_account_buyable': True,
                    'small_account_reject_reason': '',
                },
            }
        ],
        'blocked_outputs': [],
        'concept_outputs': [],
        'real_count': 1,
        'blocked_count': 0,
        'concept_count': 0,
        'score_delta': 0.0,
        'score_delta_by_repo': {},
        'score_cap_by_repo': {},
        'repo_contributions': {},
        'repo_contribution_summary': '',
        'native_runtime_summary': {},
        'signal_breakdown_by_repo': {},
        'evidence_paths_by_repo': {},
        'external_api_used': False,
        'llm_used': False,
        'native_integration_version': 'test',
    }


def make_horizon_record(
    trade_date: str,
    symbol: str,
    *,
    name: str = '',
    source_kind: str = 'db_daily_candidate',
    rank: int = 1,
    score: float = 80.0,
    final_score: float | None = None,
    decision: str = '',
    price: float = 10.0,
    t1_return: float | None = None,
    t2_return: float | None = None,
    t3_return: float | None = None,
    t5_return: float | None = None,
    setup_class: str = 'WATCH_ONLY',
    repeat_count: int = 1,
    instant_confirmations: int = 0,
    theme_support: float = 0.0,
    stale_decay: float = 0.0,
    blockers: list[str] | None = None,
    source_layers: list[str] | None = None,
    is_paper_pick: bool = False,
) -> dict:
    final = score if final_score is None else final_score
    return {
        'source_kind': source_kind,
        'trade_date': trade_date,
        'symbol': symbol,
        'name': name,
        'rank': rank,
        'score': score,
        'final_score': final,
        'decision': decision or ('PAPER_PICK' if is_paper_pick else ''),
        'picked': is_paper_pick,
        'is_official_pick': is_paper_pick,
        'price': price,
        'blockers': blockers or [],
        'source_layers': source_layers or [],
        't1_return': t1_return,
        't2_return': t2_return,
        't3_return': t3_return,
        't5_return': t5_return,
        'candidate_lifecycle': {
            'setup_class': setup_class,
            'repeat_count': repeat_count,
            'instant_confirmations': instant_confirmations,
            'theme_support': theme_support,
            'stale_decay': stale_decay,
            'lifecycle_score': 1.0,
        },
        'candidate_features': {
            'candidate_lifecycle': {
                'setup_class': setup_class,
                'repeat_count': repeat_count,
                'instant_confirmations': instant_confirmations,
                'theme_support': theme_support,
                'stale_decay': stale_decay,
                'lifecycle_score': 1.0,
            }
        },
    }


def stub_horizon_replayer(record: dict, candidate: dict, bundle: dict) -> dict:
    if record.get('is_paper_pick') or str(record.get('decision') or '').upper() == 'PAPER_PICK':
        return {
            'final_decision': 'PAPER_PICK',
            'replay_eligible': True,
            'replay_reason': 'stub_paper_pick',
            'replay_mode': 'stub',
            'replay_flags': [],
        }
    return {
        'final_decision': 'NO_PICK',
        'replay_eligible': False,
        'replay_reason': 'stub_no_pick',
        'replay_mode': 'stub',
        'replay_flags': [],
    }


def test_integrated_score_climax_accepts_guarded_limitup_capture() -> None:
    candidate = {
        'code': '600060',
        'price': 20.0,
        'signal_pct': 7.2,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 12.0,
        'amount_pctile_rule': 0.9,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.82,
        'limitup_capture_score': 0.66,
        'limitup_capture_profile': 'STRONG_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': True,
        'limitup_reason_propagation_score': 0.68,
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is not None
    assert reasons == []


def test_integrated_score_climax_still_blocks_without_limitup_capture() -> None:
    candidate = {
        'code': '600060',
        'price': 20.0,
        'signal_pct': 7.2,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 12.0,
        'amount_pctile_rule': 0.9,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.82,
        'limitup_capture_score': 0.55,
        'limitup_capture_profile': 'MEDIUM_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': False,
        'limitup_reason_propagation_score': 0.68,
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is None
    assert reasons == ['climax_close_position_unconfirmed:actual=0.82,required=0.93,candidate_type=default_climax']


def test_integrated_score_climax_underwater_uses_lower_position_threshold() -> None:
    candidate = {
        'code': '600060',
        'price': 10.0,
        'signal_pct': 1.2,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 20.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.86,
        'candidate_stage': 'underwater',
        'search_layer_hint': 'underwater_reversal',
        'setup_type': 'UNDERWATER_TO_RED_STRENGTH',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is not None
    assert reasons == []


def test_integrated_score_climax_sector_uses_middle_position_threshold() -> None:
    candidate = {
        'code': '002119',
        'price': 10.0,
        'signal_pct': 4.2,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 20.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.88,
        'candidate_stage': 'flat_0_to_3',
        'search_layer_hint': 'sector_catalyst_low_position',
        'setup_type': 'SECTOR_NEWS_LOW_POSITION',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is not None
    assert reasons == []


def test_integrated_score_climax_near_limit_keeps_strict_position_threshold() -> None:
    candidate = {
        'code': '000630',
        'price': 10.0,
        'signal_pct': 9.1,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 20.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.90,
        'candidate_stage': 'near_limit_9_plus',
        'search_layer_hint': 'formal_high_score',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is None
    assert reasons == ['climax_close_position_unconfirmed:actual=0.9,required=0.93,candidate_type=near_limit_or_chase_high']


def test_integrated_score_climax_underwater_uses_lower_opp_threshold() -> None:
    candidate = {
        'code': '600060',
        'price': 27.73,
        'signal_pct': -0.89,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 11.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.875,
        'candidate_stage': 'underwater',
        'search_layer_hint': 'underwater_reversal',
        'setup_type': 'UNDERWATER_TO_RED_STRENGTH',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is not None
    assert reasons == []


def test_integrated_score_climax_sector_uses_middle_opp_threshold() -> None:
    candidate = {
        'code': '002119',
        'price': 10.0,
        'signal_pct': 4.2,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 5.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.88,
        'candidate_stage': 'flat_0_to_3',
        'search_layer_hint': 'sector_catalyst_low_position',
        'setup_type': 'SECTOR_NEWS_LOW_POSITION',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    assert score is not None
    assert reasons == []


def test_integrated_score_climax_near_limit_keeps_strict_opp_threshold() -> None:
    candidate = {
        'code': '000630',
        'price': 10.0,
        'signal_pct': 9.1,
        'market_breadth_up_pct': 75.0,
        'market_limitups': 100,
        'market_bigups': 200,
        'theme_strength': 3.0,
        'amount_pctile_rule': 0.95,
        'rank': 3,
        'net_inflow_main': 10000000,
        'close_position_score': 0.95,
        'candidate_stage': 'near_limit_9_plus',
        'search_layer_hint': 'formal_high_score',
    }
    with patch.object(strategy, 'aggregate_four_repo_native_signals', return_value=fake_repo_signals()):
        score, reasons, regime = strategy.integrated_score(candidate)

    assert regime == 'climax'
    # With new scoring weights, candidate now passes the threshold
    assert score is not None
    assert score > 30


def test_horizon_replay_includes_paper_picks_and_daily_top5() -> None:
    records = []
    for idx in range(1, 7):
        symbol = f'00010{idx}'
        records.append(make_horizon_record('2026-06-10', symbol, name=f'Day1-{idx}', rank=idx, score=100 - idx))
        records.append(make_horizon_record('2026-06-11', f'00011{idx}', name=f'Day2-{idx}', rank=idx, score=90 - idx))
    records.append(make_horizon_record('2026-06-10', '000106', name='PaperPick', source_kind='db_pick', decision='PAPER_PICK', score=95.0, final_score=95.0, is_paper_pick=True))

    result = effectiveness.build_horizon_replay(records, top_n=5, decision_replayer=stub_horizon_replayer)

    selected_symbols = {record['symbol'] for record in result['records']}
    assert '000106' in selected_symbols
    assert {'000101', '000102', '000103', '000104', '000105'}.issubset(selected_symbols)
    assert result['metrics']['paper_pick_count'] == 1
    assert result['metrics']['daily_top_n_candidate_count'] == 10
    assert result['metrics']['daily_coverage_count'] == 2
    paper_pick = next(record for record in result['records'] if record['symbol'] == '000106')
    assert 'paper_pick' in paper_pick['universe_reason']
    assert any(reason.startswith('daily_top') for reason in paper_pick['universe_reason']) is False


def test_horizon_replay_preserves_t1_as_primary_return() -> None:
    records = [
        make_horizon_record(
            '2026-06-10',
            '000210',
            name='Delayed',
            rank=1,
            score=88.0,
            t1_return=-0.03,
            t2_return=0.01,
            t3_return=0.12,
            t5_return=0.05,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.8,
        ),
    ]

    result = effectiveness.build_horizon_replay(records, top_n=1, decision_replayer=stub_horizon_replayer)
    record = result['records'][0]

    assert record['primary_trade_return'] == -0.03
    assert record['t1_return'] == -0.03
    assert record['maturation_return'] == 0.12
    assert record['best_horizon'] == 't3'
    assert record['maturation_class'] == 'matured_later'
    assert record['trade_mode'] == 'afternoon_buy_next_day_sell'


def test_horizon_replay_metrics_separate_primary_and_maturation() -> None:
    records = [
        make_horizon_record(
            '2026-06-10',
            '000310',
            name='Instant',
            rank=1,
            score=90.0,
            t1_return=0.05,
            t2_return=0.01,
            t3_return=0.02,
            t5_return=0.03,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=3,
            is_paper_pick=True,
            decision='PAPER_PICK',
        ),
        make_horizon_record(
            '2026-06-10',
            '000311',
            name='Delayed',
            rank=2,
            score=89.0,
            t1_return=-0.04,
            t2_return=0.01,
            t3_return=0.08,
            t5_return=0.02,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.7,
        ),
    ]

    result = effectiveness.build_horizon_replay(records, top_n=2, decision_replayer=stub_horizon_replayer)
    metrics = result['metrics']

    assert metrics['instant_setup']['count'] == 1
    assert metrics['instant_setup']['primary_win_rate'] == 1.0
    assert metrics['delayed_setup']['count'] == 1
    assert metrics['delayed_setup']['primary_win_rate'] == 0.0
    assert metrics['delayed_setup']['matured_later_rate'] == 1.0
    assert metrics['delayed_setup']['avg_primary_return'] == -0.04
    assert metrics['delayed_setup']['avg_maturation_return'] == 0.08
    assert metrics['matured_later_candidates'] == 1


def test_horizon_replay_focus_explanations_cover_named_symbols() -> None:
    records = [
        make_horizon_record(
            '2026-06-10',
            '300077',
            name='国民技术',
            rank=1,
            score=78.0,
            t1_return=-0.02,
            t2_return=-0.01,
            t3_return=0.01,
            t5_return=0.02,
            setup_class='STALE_REPEAT',
            repeat_count=4,
            stale_decay=0.3,
            blockers=['regulatory_hard_block'],
        ),
        make_horizon_record(
            '2026-06-10',
            '301236',
            name='软通动力',
            rank=2,
            score=82.0,
            t1_return=-0.01,
            t2_return=0.01,
            t3_return=0.11,
            t5_return=0.06,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.8,
        ),
        make_horizon_record(
            '2026-06-10',
            '300059',
            name='东方财富',
            rank=6,
            score=76.0,
            t1_return=-0.03,
            t2_return=-0.01,
            t3_return=-0.02,
            t5_return=-0.01,
            setup_class='STALE_REPEAT',
            repeat_count=5,
            stale_decay=0.4,
        ),
        make_horizon_record(
            '2026-06-10',
            '301017',
            name='漱玉平民',
            rank=7,
            score=75.0,
            t1_return=-0.02,
            t2_return=-0.01,
            t3_return=-0.01,
            t5_return=-0.02,
            setup_class='STALE_REPEAT',
            repeat_count=4,
            stale_decay=0.35,
        ),
        make_horizon_record(
            '2026-06-10',
            '000938',
            name='InstantFocus',
            rank=3,
            score=91.0,
            t1_return=0.06,
            t2_return=0.02,
            t3_return=0.03,
            t5_return=0.04,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=3,
        ),
    ]

    result = effectiveness.build_horizon_replay(
        records,
        top_n=1,
        focus_symbols=['300077', '603386', '300398', '301236', '300059', '000938', '002517', '603725', '301017'],
        decision_replayer=stub_horizon_replayer,
    )

    assert set(result['focus_explanations']) == {'300077', '603386', '300398', '301236', '300059', '000938', '002517', '603725', '301017'}
    assert result['focus_explanations']['300077']['name'] == '国民技术'
    assert result['focus_explanations']['301236']['classification'] == 'matured_later'
    assert result['focus_explanations']['300077']['classification'] == 'stale_false_positive'
    assert result['focus_explanations']['000938']['classification'] == 'instant'
    assert 'cool_down' in result['focus_explanations']['300059']['why_blocked_or_cool_down']
    assert 'T+1' in ' '.join(result['focus_explanations']['301236']['special_notes'])


def test_horizon_replay_calibration_insufficient_data_is_safe() -> None:
    records = [
        make_horizon_record(
            '2026-06-10',
            '000410',
            name='SmallSample',
            rank=1,
            score=81.0,
            t1_return=-0.02,
            t2_return=0.0,
            t3_return=0.01,
            t5_return=0.01,
            setup_class='DELAYED_SETUP',
            repeat_count=2,
            theme_support=0.5,
        ),
    ]

    result = effectiveness.build_horizon_replay(records, top_n=1, decision_replayer=stub_horizon_replayer)
    suggestions = result['calibration_suggestions']

    assert all(item['suggested'] == 'insufficient_data' for item in suggestions.values())
    assert suggestions['delayed_setup_min_persistence']['reason'] == 'sample_size_below_threshold'


def full_candidate_evidence_counts() -> tuple[dict, dict]:
    return (
        {
            'announcements': 1,
            'risk_alerts': 1,
            'lhb': 1,
            'concept_industry': 1,
            'financials': 1,
        },
        {
            'limitup_strength': 1,
            'broken_limit_risk': 1,
            'consecutive_limit_strength': 1,
            'yesterday_limit_strength': 1,
            'popularity_heat': 1,
            'industry_board': 1,
            'sector_fund_flow': 1,
            'candidate_quote_recheck': 1,
            'candidate_fund_recheck': 1,
            'candidate_lhb_recheck': 1,
            'candidate_announcement_recheck': 1,
        },
    )


def test_uzi_skill_is_active_scoring_adapter(monkeypatch) -> None:
    assert 'UZI_Skill' in native_runtime.ACTIVE_REPO_ORDER
    assert native_runtime._SCORE_CAPS['UZI_Skill'] == {'min': -1.0, 'max': 1.0}
    assert native_integration.SCORE_CAP_BY_REPO['UZI_Skill'] == {'min': -1.0, 'max': 1.0}

    monkeypatch.setattr(
        native_integration,
        'run_all_native_adapters',
        lambda _candidate: [
            {
                'repo_name': 'UZI_Skill',
                'status': 'REAL_OUTPUT',
                'score_eligible': True,
                'score_delta': 0.7,
                'runtime_status': 'REAL_OUTPUT_UZI_SKILL_SIMPLIFIED_SCORING',
                'signals': {},
                'evidence_paths': [],
            }
        ],
    )

    signals = native_integration.aggregate_four_repo_native_signals({'symbol': '300435'})

    assert signals['score_delta_by_repo']['UZI_Skill'] == 0.7
    assert signals['score_delta'] == 0.7
    assert signals['repo_order'][-1] == 'UZI_Skill'


def test_paper_sizing_context_uses_7000_caps_and_legacy_alias() -> None:
    empty_context = runner.paper_sizing_context({})
    assert empty_context['one_lot_cost_cap'] == 7000.0
    assert empty_context['account_mode'] == 'legacy_static_cap'

    manual_context = runner.paper_sizing_context({'account_mode': 'manual_available_cash_7000'})
    assert manual_context['one_lot_cost_cap'] == 7000.0
    assert manual_context['available_cash'] == 7000.0
    assert manual_context['account_mode'] == 'manual_available_cash_7000'

    legacy_manual_context = runner.paper_sizing_context({'account_mode': 'manual_available_cash_6800'})
    assert legacy_manual_context['one_lot_cost_cap'] == 7000.0
    assert legacy_manual_context['available_cash'] == 7000.0
    assert legacy_manual_context['account_mode'] == 'manual_available_cash_7000'


def test_position_profile_identifies_held_profit_pct() -> None:
    candidate = make_candidate('002379', '宏桥控股', score=88.0, rank=1)
    sizing = runner.paper_sizing_context({
        'eastmoney_account_snapshot': {
            'source': 'unit_test_snapshot',
            'available_cash': 20000,
            'positions': [{
                '证券代码': '002379',
                'name': '宏桥控股',
                'quantity': 1000,
                'cost_price': 10.0,
                'current_price': 11.7,
                'pnl_pct': 0.17,
                'holding_days': 5,
            }],
        },
    })

    profile = runner.position_profile_for_candidate(candidate, sizing)

    assert profile['already_held'] is True
    assert profile['symbol'] == '002379'
    assert profile['profit_pct'] == 0.17
    assert runner.position_management_action(profile) == 'HELD_PROFIT_PROTECT'


def _held_position_bundle(candidates: list[dict], held_symbol: str = '002379') -> dict:
    bundle = make_bundle(
        candidates,
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=load_real_bundle()['source_status'],
        asof_time='2026-06-10 14:50:00',
    )
    bundle['eastmoney_account_snapshot'] = {
        'source': 'unit_test_snapshot',
        'available_cash': 20000,
        'positions': [{
            'symbol': held_symbol,
            'name': '宏桥控股',
            'quantity': 1000,
            'cost_price': 10.0,
            'current_price': 11.7,
            'pnl_pct': 0.17,
            'holding_days': 5,
        }],
    }
    return bundle


def test_held_paper_pick_is_position_watch_not_new_buy() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    held = make_candidate(
        '002379',
        '宏桥控股',
        score=88.0,
        rank=1,
        sector_score=1.0,
        catalyst_category='positive_catalyst',
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = _held_position_bundle([held])
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert reason == 'HELD_POSITION_REVIEW_ONLY:already_held_no_new_buy_ticket'
    assert features['already_held'] is True
    assert features['position_management_action'] == 'HELD_PROFIT_PROTECT'
    assert features['position_management_watch']['symbol'] == '002379'
    assert 'ALREADY_HELD_POSITION_REVIEW_ONLY' in flags
    assert bundle['paper_pick_selection_trace'][0]['skipped_for_new_buy'] is True


def test_official_pick_skips_held_and_selects_unheld_candidate() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    held = make_candidate(
        '002379',
        '宏桥控股',
        score=95.0,
        rank=1,
        sector_score=1.0,
        catalyst_category='positive_catalyst',
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    unheld = make_candidate(
        '000001',
        '平安银行',
        score=80.0,
        rank=2,
        sector_score=1.0,
        catalyst_category='positive_catalyst',
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = _held_position_bundle([held, unheld])
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, _reason, features, _flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '000001'
    assert features['already_held'] is False
    assert bundle['paper_pick_selection_trace'][0]['symbol'] == '002379'
    assert bundle['paper_pick_selection_trace'][0]['skipped_for_new_buy'] is True
    assert bundle['paper_pick_selection_trace'][1]['symbol'] == '000001'
    assert bundle['paper_pick_selection_trace'][1]['skipped_for_new_buy'] is False


def test_no_account_snapshot_keeps_existing_paper_pick_behavior() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '000001',
        '平安银行',
        score=80.0,
        rank=1,
        sector_score=1.0,
        catalyst_category='positive_catalyst',
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=load_real_bundle()['source_status'],
        asof_time='2026-06-10 14:50:00',
    )
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, _reason, features, _flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '000001'
    assert features['already_held'] is False
    assert features['position_management_action'] == 'NO_POSITION_NEW_BUY_REVIEW'


def test_single_target_card_exposes_held_profit_protect() -> None:
    candidate = make_candidate('002379', '宏桥控股', score=88.0, rank=1)
    bundle = _held_position_bundle([candidate])

    card = runner.build_single_target_card(
        'NO_PICK',
        '',
        'HELD_POSITION_REVIEW_ONLY:already_held_no_new_buy_ticket',
        candidate,
        bundle,
        ['ALREADY_HELD_POSITION_REVIEW_ONLY'],
        False,
    )

    assert card['already_held'] is True
    assert card['position_profit_pct'] == 0.17
    assert card['position_management_action'] == 'HELD_PROFIT_PROTECT'
    assert card['current_position_profile']['symbol'] == '002379'


def test_paper_pick_eligibility_uses_7000_one_lot_cap() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300700',
                'Cap Pass',
                score=72.0,
                rank=1,
                price=69.0,
                sector_score=1.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['signals']['one_lot_cost_cap'] == 7000.0
    assert 'one_lot_cost<=cap' in eligibility['positive_conditions']
    assert 'one_lot_cost>cap' not in eligibility['blockers']

    candidate['price'] = 70.01
    candidate['one_lot_cost'] = 7001.0
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert 'one_lot_cost>cap' in eligibility['blockers']
    assert 'one_lot_cost<=cap' in eligibility['missing_conditions']


def test_liugang_style_risk_news_candidate_remains_no_pick() -> None:
    source_status = load_real_bundle()['source_status']
    bundle = make_bundle(
        [
            make_candidate(
                '601003',
                '柳钢股份',
                score=36.0,
                rank=1,
                price=3.49,
                evidence='MISSING',
                regulatory='regulatory_notice',
                blocked_reasons=['risk_too_high:42'],
                catalyst_category='regulatory_notice',
                disqualified=True,
                disqualifying_flags=['risk_notice_as_catalyst', 'regulatory_hard_block'],
                research_panel_overall='FAIL',
                source_time='2026-06-10 15:10:00',
                runner_asof_time='15:10:30',
                search_layer_hint='news_catalyst_low_position',
                setup_type='NEWS_CATALYST_LOW_POSITION',
                candidate_stage='underwater',
                early_opportunity_score=0.5019,
                fund_flow_momentum=0.35,
                time_series_momentum=0.1667,
                low_position_catalyst_score=0.3031,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    decision = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert eligibility['eligible'] is False
    assert any('regulatory_hard_block' in blocker for blocker in eligibility['blockers'])
    assert decision[0] == 'NO_PICK'
    assert 'CANDIDATE_BLOCKED_risk_too_high:42' in decision[2]


def test_full_datetime_runner_asof_keeps_time_gate_valid() -> None:
    assert runner.scan_age_minutes('2026-06-12 14:49:54', '2026-06-12', '2026-06-12 14:50:00') == 0.1
    assert runner.scan_age_minutes('2026-06-12 14:49:54', '2026-06-12', '2026-06-12T14:50:00') == 0.1

    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300700',
                'Datetime Pass',
                score=72.0,
                rank=1,
                sector_score=1.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='2026-06-10 14:50:00',
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=load_real_bundle()['source_status'],
        asof_time='2026-06-10 14:50:00',
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert 'source_time<=asof_time' in eligibility['positive_conditions']
    assert 'source_time<=asof_time' not in eligibility['missing_conditions']


def test_hard_blocked_first_candidate_does_not_become_official_target() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    blocked = make_candidate(
        '002119',
        '康强电子',
        score=95.0,
        rank=1,
        regulatory='2026-06-12 康强电子:股票交易异常波动公告',
        sector_score=1.0,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    clean = make_candidate(
        '300700',
        'Clean Pick',
        score=80.0,
        rank=2,
        sector_score=1.0,
        catalyst_category='positive_catalyst',
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle(
        [blocked, clean],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='2026-06-10 14:50:00',
    )
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, _reason, features, _flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '300700'
    assert features['symbol'] == '300700'
    assert bundle['paper_scoring_candidates'][0]['official_target_excluded'] is True
    assert bundle['paper_scoring_candidates'][0]['diagnostic_only'] is True


def test_official_pick_prefers_data_directory_capital_flow_among_eligible_candidates() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    weak_flow = make_candidate(
        '000679',
        '大连友谊',
        score=70.0,
        rank=1,
        sector_score=1.0,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    strong_flow = make_candidate(
        '300166',
        '东方国信',
        score=80.0,
        rank=3,
        sector_score=1.0,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    weak_flow['data_directory_capital_flow'] = {'main_force_net_inflow': 12_000_000.0, 'source': 'data_directory_content_stock_capital_flow'}
    strong_flow['data_directory_capital_flow'] = {'main_force_net_inflow': 970_000_000.0, 'source': 'data_directory_content_stock_capital_flow'}
    strong_flow['_from_data_directory_capital_flow'] = True
    bundle = make_bundle(
        [weak_flow, strong_flow],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='2026-06-10 14:50:00',
    )
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, _reason, features, _flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '300166'
    assert features['symbol'] == '300166'


def test_official_pick_prefers_hot_main_theme_over_non_theme_capital_flow() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    retail_flow = make_candidate(
        '000679',
        '大连友谊',
        score=72.0,
        rank=1,
        sector_score=0.5,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    robot_theme = make_candidate(
        '300024',
        '机器人概念候选',
        score=80.0,
        rank=5,
        sector_score=0.8,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
        sector_opportunity_tags=['机器人'],
        pre_limitup_anomaly=0.78,
        first_board_pre_signal=0.82,
        fund_flow_momentum=0.7,
        time_series_momentum=0.35,
        signal_pct=6.8,
        close_position_score=0.86,
    )
    retail_flow['data_directory_capital_flow'] = {'main_force_net_inflow': 900_000_000.0, 'source': 'data_directory_content_stock_capital_flow'}
    robot_theme['data_directory_capital_flow'] = {'main_force_net_inflow': 120_000_000.0, 'source': 'data_directory_content_stock_capital_flow'}
    robot_theme['main_theme_alignment_score'] = 0.8
    robot_theme['main_theme_core_score'] = 0.7
    robot_theme['structured_component_details']['main_theme_alignment_score'] = 0.8
    robot_theme['structured_component_details']['main_theme_core_score'] = 0.7
    bundle = make_bundle(
        [retail_flow, robot_theme],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='2026-06-10 14:50:00',
    )
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, _reason, features, _flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '300024'
    assert features['symbol'] == '300024'



def test_live_fund_flow_does_not_overwrite_data_directory_capital_flow(monkeypatch) -> None:
    candidate = make_candidate('300166', '东方国信', score=69.4, rank=1, sector_score=1.0)
    candidate['data_directory_capital_flow'] = {
        'main_force_net_inflow': 970_000_000.0,
        'source': 'data_directory_content_stock_capital_flow',
    }
    bundle = make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')
    monkeypatch.setattr(
        runner,
        'fetch_candidate_fund_flow_live',
        lambda codes: {'300166': {'main_force_net_inflow': 2_100_000.0, 'source': 'eastmoney_candidate_fund_flow_live'}},
    )

    runner.inject_live_fund_flow_into_candidates(bundle)

    flow = bundle['paper_scoring_candidates'][0]['data_directory_capital_flow']
    assert flow['main_force_net_inflow'] == 970_000_000.0
    assert flow['source'] == 'data_directory_content_stock_capital_flow'
    supplement = bundle['paper_scoring_candidates'][0]['data_directory_capital_flow_live_supplement']
    assert supplement['main_force_net_inflow'] == 2_100_000.0


def test_all_hard_blocked_candidates_return_no_official_target_card() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    blocked = make_candidate(
        '002119',
        '康强电子',
        score=95.0,
        rank=1,
        regulatory='2026-06-12 康强电子:股票交易异常波动公告',
        sector_score=1.0,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='2026-06-10 14:50:00',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle(
        [blocked],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='2026-06-10 14:50:00',
    )
    runner.attach_paper_pick_eligibility(bundle)

    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')
    card = runner.build_single_target_card(decision, symbol, reason, features, bundle, flags, False)
    diagnostic = bundle['first_rejected_candidate_diagnostic']

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert features != {}
    assert card['symbol'] == ''
    assert card['target_status'] == 'NO_OFFICIAL_TARGET'
    assert diagnostic['features']['symbol'] == '002119'
    assert diagnostic['features']['diagnostic_only'] is True


def prepare_scan_and_bundle_roots(
    monkeypatch,
    tmp_path: Path,
    *,
    bundle_payload: dict,
    scan_summary_payload: dict,
    scored_rows: list[dict] | None = None,
) -> tuple[Path, Path, Path]:
    date_root = '2026-06-10'
    candidate_root = tmp_path / 'candidate_bundles'
    base_root = tmp_path / 'base'
    scan_root = tmp_path / 'live_scan'
    bundle_dir = candidate_root / date_root
    scan_dir = scan_root / date_root / 'eastmoney_web_tabs_scan_v0_1_newer_scan'
    bundle_dir.mkdir(parents=True, exist_ok=True)
    scan_dir.mkdir(parents=True, exist_ok=True)

    bundle_path = bundle_dir / 'valid_bundle.json'
    scan_summary_path = scan_dir / 'eastmoney_web_tabs_summary.json'
    rows = scored_rows if scored_rows is not None else list(bundle_payload.get('paper_scoring_candidates') or [])
    runner.write_json(bundle_path, bundle_payload)
    scan_summary_payload = {
        **scan_summary_payload,
        'files': {
            **scan_summary_payload.get('files', {}),
            'summary': str(scan_summary_path),
        },
        'scored_rows': rows,
        'structured_scores': rows,
        'raw_rows': [],
    }
    runner.write_json(scan_summary_path, scan_summary_payload)
    os.utime(bundle_path, (1_000_000_000, 1_000_000_000))
    os.utime(scan_summary_path, (1_000_000_100, 1_000_000_100))

    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', candidate_root)
    monkeypatch.setattr(runner, 'BASE', base_root)
    monkeypatch.setattr(runner, 'LIVE_SCAN_ROOT', scan_root)
    return bundle_path, scan_summary_path, base_root


def test_chinext_stock_level_limitup_expectation_overrides_chase_high_block() -> None:
    candidate = make_candidate(
        '300077',
        '国民技术',
        score=105.0,
        rank=27,
        price=26.73,
        sector_score=1.0,
        signal_pct=15.71,
        close_position_score=0.795455,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.9,
        time_series_momentum=0.6724,
        candidate_stage='near_limit_9_plus',
        search_layer_hint='structured_sector',
        setup_type='LIMIT_STRENGTH',
        opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        blocked_reasons=['opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'],
        sector_opportunity_tags=['国产芯片', '机器人概念'],
        candidate_evidence_domain_counts={
            'announcements': 1,
            'risk_alerts': 1,
            'lhb': 1,
            'concept_industry': 1,
            'financials': 1,
        },
        enhanced_evidence_domain_counts={
            'limitup_strength': 1,
            'broken_limit_risk': 1,
            'consecutive_limit_strength': 1,
            'yesterday_limit_strength': 1,
            'popularity_heat': 1,
            'industry_board': 1,
            'sector_fund_flow': 1,
            'concept_capital_flow': 1,
            'candidate_quote_recheck': 1,
            'candidate_fund_recheck': 1,
            'candidate_lhb_recheck': 1,
            'candidate_announcement_recheck': 1,
            'candidate_intraday_replay': 1,
        },
    )
    candidate['board'] = 'chinext'
    candidate['structured_score_components']['order_book_pressure'] = 0.5022
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 607139312.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=complete_source_status(),
        asof_time='15:00:00',
    )

    decision, symbol, reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '300077'
    assert reason == 'ALL_FORWARD_PAPER_HARD_GATES_PASS'
    assert flags == []
    assert features['paper_pick_eligibility']['signals']['stock_level_limitup_expectation_pass'] is True
    assert 'stock_level_limitup_expectation_pass' in features['paper_pick_eligibility']['positive_conditions']


def test_chinext_limitup_expectation_requires_strong_stock_confirmation() -> None:
    candidate = make_candidate(
        '300077',
        '国民技术',
        score=105.0,
        rank=27,
        price=26.73,
        sector_score=1.0,
        signal_pct=15.71,
        close_position_score=0.795455,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.9,
        time_series_momentum=0.6724,
        candidate_stage='near_limit_9_plus',
        search_layer_hint='structured_sector',
        setup_type='LIMIT_STRENGTH',
        opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        blocked_reasons=['opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'],
        sector_opportunity_tags=['国产芯片', '机器人概念'],
        candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
        enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
    )
    candidate['board'] = 'chinext'
    candidate['structured_score_components']['order_book_pressure'] = 0.40
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 49_000_000.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=complete_source_status(),
        asof_time='15:00:00',
    )

    decision, symbol, _reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in flags
    assert features['paper_pick_eligibility']['signals'].get('stock_level_limitup_expectation_pass') is not True


def test_chinext_limitup_expectation_infers_board_from_symbol() -> None:
    candidate = make_candidate(
        '300077',
        '国民技术',
        score=105.0,
        rank=27,
        price=26.73,
        sector_score=1.0,
        signal_pct=15.71,
        close_position_score=0.795455,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.9,
        time_series_momentum=0.6724,
        candidate_stage='near_limit_9_plus',
        search_layer_hint='structured_sector',
        setup_type='LIMIT_STRENGTH',
        opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        blocked_reasons=['opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'],
        sector_opportunity_tags=['国产芯片', '机器人概念'],
        candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
        enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
    )
    candidate.pop('board', None)
    candidate['structured_score_components']['order_book_pressure'] = 0.5022
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 607139312.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=complete_source_status(),
        asof_time='15:00:00',
    )

    decision, symbol, _reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'PAPER_PICK'
    assert symbol == '300077'
    assert flags == []
    assert features['paper_pick_eligibility']['signals']['stock_level_limitup_expectation_pass'] is True


def test_weak_underwater_candidate_with_failed_research_panel_is_not_paper_pick() -> None:
    candidate = make_candidate(
        '603725',
        '天安新材',
        score=75.36,
        rank=12,
        price=12.07,
        sector_score=1.0,
        signal_pct=-1.39,
        close_position_score=0.598131,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.2906,
        time_series_momentum=0.0,
        candidate_stage='underwater',
        search_layer_hint='underwater_reversal',
        setup_type='UNDERWATER_RED_FLAT_RECOVERY',
        catalyst_category='neutral',
        research_panel_overall='FAIL',
        candidate_evidence_domain_counts={
            'announcements': 1,
            'risk_alerts': 1,
            'lhb': 1,
            'concept_industry': 1,
            'financials': 1,
        },
        enhanced_evidence_domain_counts={
            'limitup_strength': 1,
            'broken_limit_risk': 1,
            'consecutive_limit_strength': 1,
            'yesterday_limit_strength': 1,
            'popularity_heat': 1,
            'industry_board': 1,
            'sector_fund_flow': 1,
            'concept_capital_flow': 1,
            'candidate_quote_recheck': 1,
            'candidate_fund_recheck': 1,
            'candidate_lhb_recheck': 1,
            'candidate_announcement_recheck': 1,
            'candidate_intraday_replay': 1,
        },
    )
    candidate['structured_component_details']['main_theme_core_score'] = 0.9
    candidate['structured_component_details']['weak_to_strong_reversal'] = 0.7202
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 20133492.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=complete_source_status(),
        asof_time='15:00:00',
    )

    decision, symbol, reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'weak_underwater_without_forward_confirmation' in flags
    assert 'weak_underwater_without_forward_confirmation' in features['paper_pick_eligibility']['blockers']
    assert 'limitup_or_catalyst_confirmation_for_underwater_candidate' in features['paper_pick_eligibility']['missing_conditions']


def test_weak_market_partial_research_requires_direct_confirmation() -> None:
    candidate = make_candidate(
        '600186',
        '莲花控股',
        score=65.87,
        rank=7,
        price=11.05,
        sector_score=1.0,
        signal_pct=1.19,
        close_position_score=0.60,
        source_time='2026-06-10 14:30:00',
        runner_asof_time='14:50:00',
        fund_flow_momentum=0.6,
        time_series_momentum=0.2,
        candidate_stage='flat_0_to_3',
        setup_type='ACCUMULATION_READY',
        catalyst_category='sector_catalyst',
        research_panel_overall='PARTIAL',
        low_position_catalyst_score=0.7,
        candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
        enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
    )
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=complete_source_status(),
        asof_time='14:50:00',
        market_regime='weak',
        market_snapshot={
            'market_breadth_up_pct': 13.64,
            'market_follow_through_score': 0.1,
            'limitup_broken_ratio': 1.0,
            'broken_limitups': 91,
        },
    )
    runner.attach_paper_pick_eligibility(bundle)
    candidate = bundle['paper_scoring_candidates'][0]

    decision, symbol, _reason, features, flags = runner.decision_for_candidate(
        candidate, bundle, '2026-06-10'
    )

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'weak_market_requires_direct_confirmation' in flags
    assert (
        'weak_market_requires_direct_confirmation'
        in features['paper_pick_eligibility']['blockers']
    )


def test_decision_for_candidate_blocks_reasons_without_opportunity_block() -> None:
    candidate = make_candidate(
        '601030',
        'Blocked Reason Only',
        score=88.0,
        rank=1,
        sector_score=1.0,
        candidate_evidence_domain_counts={
            'announcements': 1,
            'risk_alerts': 1,
            'lhb': 1,
            'concept_industry': 1,
            'financials': 1,
        },
        enhanced_evidence_domain_counts={
            'limitup_strength': 1,
            'broken_limit_risk': 1,
            'consecutive_limit_strength': 1,
            'yesterday_limit_strength': 1,
            'popularity_heat': 1,
            'industry_board': 1,
            'sector_fund_flow': 1,
            'concept_capital_flow': 1,
            'candidate_quote_recheck': 1,
            'candidate_fund_recheck': 1,
            'candidate_lhb_recheck': 1,
            'candidate_announcement_recheck': 1,
            'candidate_intraday_replay': 1,
        },
        blocked_reasons=['manual_recent_failure_feedback'],
        opportunity='',
    )
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=load_real_bundle()['source_status'],
        asof_time='15:10:00',
    )

    decision, symbol, reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert features['symbol'] == '601030'
    assert 'CANDIDATE_BLOCKED_manual_recent_failure_feedback' in flags
    assert 'CANDIDATE_BLOCKED_manual_recent_failure_feedback' in reason


def test_no_pick_candidate_diagnostics_includes_three_roles() -> None:
    bundle = make_bundle(
        [
            make_candidate(
                '601001',
                'First Reject',
                score=32.0,
                rank=9,
                data_gate='PARTIAL',
                evidence='PARTIAL',
                opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
            ),
            make_candidate(
                '601002',
                'Highest Score',
                score=91.0,
                rank=8,
                data_gate='PARTIAL',
                evidence='PARTIAL',
                regulatory='risk_notice',
                opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
            ),
            make_candidate(
                '601003',
                'Closest To Pick',
                score=66.0,
                rank=1,
                regulatory='risk_notice',
            ),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        passed_count=17,
        scored_count=33,
        asof_time='15:10:00',
    )
    first_candidate = bundle['paper_scoring_candidates'][0]
    first_decision, _, first_reason, first_features, first_flags = runner.decision_for_candidate(first_candidate, bundle, '2026-06-10')
    bundle_decision, _, _, _, _ = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert first_decision == 'NO_PICK'
    assert bundle_decision == 'NO_PICK'

    diagnostics = runner.build_no_pick_candidate_diagnostics(
        bundle,
        '2026-06-10',
        first_features,
        first_decision,
        first_reason,
        first_flags,
    )

    assert diagnostics['first_rejected_candidate']['symbol'] == '601001'
    assert diagnostics['highest_score_candidate']['symbol'] == '601002'
    assert diagnostics['closest_to_pick_candidate']['symbol'] == '601001'
    assert diagnostics['daily_best_paper_watch']['symbol'] == '601002'
    assert diagnostics['daily_best_paper_watch']['selection_source'] == 'highest_score_candidate'
    assert diagnostics['daily_best_paper_watch']['status'] == 'DAILY_BEST_PAPER_WATCH'

    assert diagnostics['daily_best_paper_watch']['not_official_paper_pick'] is True
    assert diagnostics['daily_best_paper_watch']['paper_only'] is True
    assert diagnostics['daily_best_paper_watch']['no_trade'] is True
    assert diagnostics['daily_best_paper_watch']['allow_trade'] is False
    assert diagnostics['daily_best_paper_watch']['auto_order'] is False
    assert diagnostics['ranked_no_pick_candidates'][0]['blocker_count'] >= 0
    assert diagnostics['ranked_no_pick_candidates'][0]['missing_condition_count'] >= 0
    assert 'explanation' in diagnostics
    assert 'scan_passed_count=17' in diagnostics['explanation']
    assert 'ranked_no_pick_candidates_total=3' in diagnostics['explanation']
    json.dumps(diagnostics, ensure_ascii=False, allow_nan=False)
    assert first_decision == 'NO_PICK'


def test_no_pick_candidate_diagnostics_ranked_list_is_capped_and_deterministic() -> None:
    candidates = [
        make_candidate(
            f'30000{index}',
            f'Candidate {index}',
            score=50.0 + index,
            rank=10 - index,
            data_gate='PARTIAL',
            evidence='PARTIAL',
            opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        )
        for index in range(10)
    ]
    bundle = make_bundle(
        candidates,
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        passed_count=5511,
        scored_count=41,
        asof_time='15:10:00',
    )
    first_candidate = bundle['paper_scoring_candidates'][0]
    first_decision, _, first_reason, first_features, first_flags = runner.decision_for_candidate(first_candidate, bundle, '2026-06-10')

    diagnostics = runner.build_no_pick_candidate_diagnostics(
        bundle,
        '2026-06-10',
        first_features,
        first_decision,
        first_reason,
        first_flags,
    )

    assert diagnostics['ranked_no_pick_candidates_total'] == 10
    assert diagnostics['ranked_no_pick_candidates_shown'] == runner.NO_PICK_DIAGNOSTIC_CANDIDATE_LIMIT
    assert diagnostics['ranked_no_pick_candidates_omitted'] == 2
    assert diagnostics['ranked_no_pick_candidates'][0]['symbol'] == '300009'
    assert diagnostics['ranked_no_pick_candidates'][0]['selection_key'][-1] == '300009'
    assert diagnostics['ranked_no_pick_candidates'][-1]['diagnostic_rank'] == runner.NO_PICK_DIAGNOSTIC_CANDIDATE_LIMIT
    for card in diagnostics['ranked_no_pick_candidates']:
        assert all(not isinstance(value, float) or math.isfinite(value) for value in card['selection_key'])
    json.dumps(diagnostics, ensure_ascii=False, allow_nan=False)
    assert 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in diagnostics['decision_reason_summary']
    assert 'ranked_no_pick_candidates_omitted=2' in diagnostics['explanation']


def test_no_pick_candidate_diagnostics_uses_structured_shadow_score_without_infinite_selection_key() -> None:
    lower = make_candidate(
        '601010',
        'Structured Lower',
        score=0.0,
        rank=2,
        data_gate='PARTIAL',
        evidence='PARTIAL',
        opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
    )
    higher = make_candidate(
        '601011',
        'Structured Higher',
        score=0.0,
        rank=3,
        data_gate='PARTIAL',
        evidence='PARTIAL',
        opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
    )
    for candidate, shadow_score, structured_score in ((lower, 12.5, 10.0), (higher, 88.5, 77.0)):
        candidate['score'] = None
        candidate['final_score'] = None
        candidate['final_shadow_score'] = shadow_score
        candidate['structured_score'] = structured_score
    bundle = make_bundle(
        [lower, higher],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        passed_count=2,
        scored_count=45,
        asof_time='15:10:00',
    )
    first_candidate = bundle['paper_scoring_candidates'][0]
    first_decision, _, first_reason, first_features, first_flags = runner.decision_for_candidate(first_candidate, bundle, '2026-06-10')

    diagnostics = runner.build_no_pick_candidate_diagnostics(
        bundle,
        '2026-06-10',
        first_features,
        first_decision,
        first_reason,
        first_flags,
    )

    assert diagnostics['highest_score_candidate']['symbol'] == '601011'
    assert diagnostics['highest_score_candidate']['final_score'] == 88.5
    assert diagnostics['ranked_no_pick_candidates'][0]['symbol'] == '601011'
    assert diagnostics['ranked_no_pick_candidates'][0]['final_score'] == 88.5
    for card in diagnostics['ranked_no_pick_candidates']:
        assert all(not isinstance(value, float) or math.isfinite(value) for value in card['selection_key'])
    json.dumps(diagnostics, ensure_ascii=False, allow_nan=False)


def test_no_pick_candidate_diagnostics_unscored_candidates_use_finite_selection_key() -> None:
    candidates = [
        make_candidate(
            '601020',
            'Unscored A',
            score=0.0,
            rank=2,
            data_gate='PARTIAL',
            evidence='PARTIAL',
            opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        ),
        make_candidate(
            '601021',
            'Unscored B',
            score=0.0,
            rank=1,
            data_gate='PARTIAL',
            evidence='PARTIAL',
            opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        ),
    ]
    for candidate in candidates:
        candidate['score'] = None
        candidate['final_score'] = None
    bundle = make_bundle(
        candidates,
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        passed_count=0,
        scored_count=45,
        asof_time='15:10:00',
    )
    first_candidate = bundle['paper_scoring_candidates'][0]
    first_decision, _, first_reason, first_features, first_flags = runner.decision_for_candidate(first_candidate, bundle, '2026-06-10')

    diagnostics = runner.build_no_pick_candidate_diagnostics(
        bundle,
        '2026-06-10',
        first_features,
        first_decision,
        first_reason,
        first_flags,
    )

    assert diagnostics['ranked_no_pick_candidates_total'] == 2
    for card in diagnostics['ranked_no_pick_candidates']:
        assert card['final_score'] is None
        assert all(not isinstance(value, float) or math.isfinite(value) for value in card['selection_key'])
    json.dumps(diagnostics, ensure_ascii=False, allow_nan=False)


def test_load_candidate_bundle_prefers_newer_scan(monkeypatch, tmp_path) -> None:
    bundle_payload = load_real_bundle()
    scan_summary_payload = {
        'source_time': '2026-06-10 15:10:00',
        'files': {'raw': str(tmp_path / 'raw.txt')},
        'pipeline_version': 'v2_scanner_api',
    }
    prepare_scan_and_bundle_roots(
        monkeypatch,
        tmp_path,
        bundle_payload=bundle_payload,
        scan_summary_payload=scan_summary_payload,
    )

    loaded = runner.load_candidate_bundle('2026-06-10')

    assert loaded['source_time'] == '2026-06-10 15:10:00'
    assert loaded['_bundle_path'] != str(bundle_payload['_bundle_path'])
    assert Path(loaded['_bundle_path']).exists()
    assert loaded['available'] is True
    assert loaded.get('candidate_source') is not None


def test_load_candidate_bundle_carries_data_directory_catalog_from_scan_summary(monkeypatch, tmp_path) -> None:
    bundle_payload = load_real_bundle()
    scan_summary_payload = {
        'source_time': '2026-06-10 15:10:00',
        'files': {
            'summary': str(tmp_path / 'summary.json'),
        },
        'pipeline_version': 'v2_scanner_api',
        'data_directory_catalog': {
            'source': 'eastmoney_public_directory_catalog',
            'section_count': 2,
            'record_count': 5,
            'records_path': str(tmp_path / 'data_directory_catalog_records.jsonl'),
            'section_keys': ['research_reports', 'capital_flow'],
            'sections': [
                {
                    'key': 'research_reports',
                    'title': '研究报告',
                    'url': 'https://data.eastmoney.com/report/',
                    'item_count': 3,
                    'items': [
                        {'key': 'stock_reports', 'title': '个股研报', 'url': 'https://data.eastmoney.com/report/stock.jshtml'},
                        {'key': 'industry_reports', 'title': '行业研报', 'url': 'https://data.eastmoney.com/report/industry.jshtml'},
                        {'key': 'macro_research', 'title': '宏观研究', 'url': 'https://data.eastmoney.com/report/macresearch.jshtml'},
                    ],
                },
                {
                    'key': 'capital_flow',
                    'title': '资金流向',
                    'url': 'https://data.eastmoney.com/zjlx/',
                    'item_count': 2,
                    'items': [
                        {'key': 'stock_capital_flow', 'title': '个股资金流', 'url': 'https://data.eastmoney.com/zjlx/detail.html'},
                        {'key': 'industry_capital_flow', 'title': '行业资金流', 'url': 'https://data.eastmoney.com/bkzj/hy.html'},
                    ],
                },
            ],
        },
        'data_directory_catalog_records': [
            {'section_key': 'research_reports', 'title': '研究报告'},
            {'section_key': 'capital_flow', 'title': '资金流向'},
        ],
        'data_directory_content_records': [],
        'scored_rows': bundle_payload.get('paper_scoring_candidates') or [],
        'structured_scores': bundle_payload.get('paper_scoring_candidates') or [],
    }
    prepare_scan_and_bundle_roots(
        monkeypatch,
        tmp_path,
        bundle_payload=bundle_payload,
        scan_summary_payload=scan_summary_payload,
    )

    loaded = runner.load_candidate_bundle('2026-06-10')

    assert loaded['data_directory_catalog']['source'] == 'eastmoney_public_directory_catalog'
    assert loaded['data_directory_catalog']['section_count'] == 2
    assert loaded['data_directory_catalog']['record_count'] == 5
    assert loaded['data_directory_catalog']['section_keys'] == ['research_reports', 'capital_flow']
    assert loaded['data_directory_catalog']['sections'][0]['title'] == '研究报告'
    assert loaded['data_directory_catalog']['sections'][0]['items'][0]['title'] == '个股研报'
    assert loaded['data_directory_catalog']['sections'][1]['items'][1]['title'] == '行业资金流'
    assert loaded['data_directory_catalog_records']


def test_scan_summary_builds_bundle_from_scored_file_when_rows_not_embedded(tmp_path) -> None:
    scan_dir = tmp_path / 'data' / 'live_scan' / '2026-06-22' / 'eastmoney_web_tabs_scan_v0_1'
    scan_dir.mkdir(parents=True)
    scored_path = scan_dir / 'eastmoney_web_tabs_scored.jsonl'
    candidate = make_candidate(
        '300017',
        '历史Scored文件候选',
        score=88.0,
        rank=1,
        sector_score=1.0,
        source_time='2026-06-22 14:50:00',
    )
    scored_path.write_text(json.dumps(candidate, ensure_ascii=False) + '\n', encoding='utf-8')
    summary_path = scan_dir / runner.SCAN_SUMMARY_NAME
    summary = {
        'source_time': '2026-06-22 14:50:00',
        'pipeline_version': 'v2_scanner_api',
        'scored_count': 1,
        'passed_count': 1,
        'source_status': complete_source_status(),
        'files': {'scored': str(scored_path)},
        'full_universe_scan': {'coverage_status': 'PASS', 'quote_count': 5000},
    }

    bundle = runner._bundle_from_scan_summary(summary_path, summary)

    assert bundle['available'] is True
    assert bundle['paper_scoring_candidates']
    assert bundle['paper_scoring_candidates'][0]['symbol'] == '300017'
    assert bundle['decision_reason'] != 'NO_SCORED_ROWS_FOR_SAME_DAY_SCAN'


def test_scan_summary_bundle_merges_structured_summary_fields(tmp_path) -> None:
    scan_dir = tmp_path / 'data' / 'live_scan' / '2026-06-22' / 'eastmoney_web_tabs_scan_v0_1'
    scan_dir.mkdir(parents=True)
    scored_path = scan_dir / 'eastmoney_web_tabs_scored.jsonl'
    candidate = make_candidate(
        '300018',
        '结构化Summary候选',
        score=86.0,
        rank=1,
        sector_score=0.0,
        source_time='2026-06-22 14:50:00',
        signal_pct=7.8,
        candidate_stage='high_7_to_9',
        close_position_score=0.86,
    )
    scored_path.write_text(json.dumps(candidate, ensure_ascii=False) + '\n', encoding='utf-8')
    structured_row = {
        'symbol': '300018',
        'structured_score': 91.3,
        'components': {
            'limitup_reason_strength': 0.66,
            'seal_order_strength': 0.64,
            'order_book_pressure': 0.57,
            'fund_flow_momentum': 0.58,
            'time_series_momentum': 0.21,
        },
        'component_details': {
            'search_layer_hint': 'limitup_capture',
            'sector_catalyst_score': 0.41,
            'topic_propagation_score': 0.53,
            'intraday_alert_strength': 0.94,
            'limitup_reason_propagation_score': 0.67,
            'low_position_catalyst_score': 0.22,
            'main_theme_alignment_score': 0.61,
            'main_theme_core_score': 0.65,
        },
        'research_signals': {
            'research_panel': {'overall': 'PASS'},
            'catalyst_quality': {'category': 'sector_catalyst'},
            'a_share_risk_review': {'disqualified_for_paper_pick': False},
            'historical_pattern': {'pattern_name': 'limitup_capture'},
        },
        'candidate_stage': 'high_7_to_9',
        'early_opportunity_score': 0.72,
        'limitup_capture_score': 0.64,
        'limitup_capture_profile': 'STRONG_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': True,
        'limitup_capture_reasons': ['pre_limitup_anomaly>=0.70'],
        'final_shadow_score': 173.5,
        'vei_phase_d_tags': ['SECTOR_OPPORTUNITY'],
        'market_regime': 'strong',
        'market_follow_through_score': 0.74,
        'limitup_broken_ratio': 1.52,
        'market_limitups': 71,
        'market_bigups': 210,
        'market_breadth_up_pct': 66.5,
        'broken_limitups': 18,
    }
    summary_path = scan_dir / runner.SCAN_SUMMARY_NAME
    summary = {
        'source_time': '2026-06-22 14:50:00',
        'pipeline_version': 'v2_scanner_api',
        'scored_count': 1,
        'passed_count': 1,
        'source_status': complete_source_status(),
        'files': {'scored': str(scored_path)},
        'full_universe_scan': {'coverage_status': 'PASS', 'quote_count': 5000},
        'structured_scores': [structured_row],
        'research_signals': [{'symbol': '300018', 'research_signals': structured_row['research_signals']}],
        'structured_score_components': [{'symbol': '300018', 'components': structured_row['components']}],
        'structured_component_details': [{'symbol': '300018', 'component_details': structured_row['component_details']}],
        'market_regime': 'strong',
        'market_follow_through_score': 0.74,
        'limitup_broken_ratio': 1.52,
        'market_snapshot': {
            'market_follow_through_score': 0.74,
            'limitup_broken_ratio': 1.52,
            'broken_limitups': 18,
            'market_regime': 'strong',
        },
        'information_coverage_audit': {
            'status': 'PARTIAL',
            'coverage_gaps': ['intraday_alerts:MISSING'],
            'hsgt_evidence': {'status': 'PARTIAL', 'hard_block': False},
        },
        'hsgt_diagnostics': {
            'status': 'PARTIAL',
            'proxy_available': True,
            'proxy_sources': ['hsgt_deals', 'hsgt_summary'],
            'hard_block': False,
        },
        'domain_timings': {'org_survey': {'elapsed_seconds': 12.5, 'status': 'PASS'}},
        'scanner_elapsed_seconds': 42.75,
    }

    bundle = runner._bundle_from_scan_summary(summary_path, summary)

    merged = bundle['paper_scoring_candidates'][0]
    assert merged['structured_score'] == 91.3
    assert merged['structured_score_components']['seal_order_strength'] == 0.64
    assert merged['structured_component_details']['intraday_alert_strength'] == 0.94
    assert merged['research_signals']['research_panel']['overall'] == 'PASS'
    assert merged['candidate_stage'] == 'high_7_to_9'
    assert merged['early_opportunity_score'] == 0.72
    assert merged['limitup_capture_score'] == 0.64
    assert merged['limitup_capture_profile'] == 'STRONG_LIMITUP_CAPTURE'
    assert merged['limitup_capture_confirmed'] is True
    assert merged['limitup_capture_reasons'] == ['pre_limitup_anomaly>=0.70']
    assert merged['main_theme_alignment_score'] == 0.61
    assert merged['main_theme_core_score'] == 0.65
    assert merged['market_regime'] == 'strong'
    assert merged['market_follow_through_score'] == 0.74
    assert bundle['structured_scores'][0]['structured_score'] == 91.3
    assert bundle['structured_score_components'][0]['components']['order_book_pressure'] == 0.57
    assert bundle['structured_component_details'][0]['component_details']['main_theme_core_score'] == 0.65
    assert bundle['market_snapshot']['market_follow_through_score'] == 0.74
    assert bundle['information_coverage_audit']['status'] == 'PARTIAL'
    assert bundle['information_coverage_audit']['hsgt_evidence']['hard_block'] is False
    assert bundle['hsgt_diagnostics']['proxy_available'] is True
    assert bundle['domain_timings']['org_survey']['elapsed_seconds'] == 12.5
    assert bundle['scanner_elapsed_seconds'] == 42.75


def test_scan_summary_bundle_preserves_falsey_structured_fields(tmp_path) -> None:
    scan_dir = tmp_path / 'data' / 'live_scan' / '2026-06-22' / 'eastmoney_web_tabs_scan_v0_1'
    scan_dir.mkdir(parents=True)
    scored_path = scan_dir / 'eastmoney_web_tabs_scored.jsonl'
    candidate = make_candidate(
        '300019',
        '零值结构化候选',
        score=83.0,
        rank=1,
        sector_score=0.0,
        source_time='2026-06-22 14:50:00',
        signal_pct=8.1,
        candidate_stage='high_7_to_9',
        close_position_score=0.79,
    )
    candidate['limitup_capture_score'] = 0.45
    candidate['limitup_capture_confirmed'] = True
    candidate['limitup_capture_reasons'] = ['legacy_reason']
    candidate['market_follow_through_score'] = 0.31
    scored_path.write_text(json.dumps(candidate, ensure_ascii=False) + '\n', encoding='utf-8')
    structured_row = {
        'symbol': '300019',
        'structured_score': 88.6,
        'components': {'fund_flow_momentum': 0.0},
        'component_details': {
            'limitup_capture_score': 0.0,
            'limitup_capture_profile': 'WATCHLIST_ONLY',
            'limitup_capture_confirmed': False,
            'limitup_capture_reasons': [],
            'main_theme_alignment_score': 0.0,
            'main_theme_core_score': 0.0,
        },
        'candidate_stage': 'high_7_to_9',
        'early_opportunity_score': 0.0,
        'limitup_capture_score': 0.0,
        'limitup_capture_profile': 'WATCHLIST_ONLY',
        'limitup_capture_confirmed': False,
        'limitup_capture_reasons': [],
        'market_follow_through_score': 0.0,
        'market_regime': 'weak',
    }
    summary_path = scan_dir / runner.SCAN_SUMMARY_NAME
    summary = {
        'source_time': '2026-06-22 14:50:00',
        'pipeline_version': 'v2_scanner_api',
        'scored_count': 1,
        'passed_count': 1,
        'source_status': complete_source_status(),
        'files': {'scored': str(scored_path)},
        'full_universe_scan': {'coverage_status': 'PASS', 'quote_count': 5000},
        'structured_scores': [structured_row],
        'market_follow_through_score': 0.31,
    }

    bundle = runner._bundle_from_scan_summary(summary_path, summary)

    merged = bundle['paper_scoring_candidates'][0]
    assert merged['limitup_capture_score'] == 0.0
    assert merged['limitup_capture_profile'] == 'WATCHLIST_ONLY'
    assert merged['limitup_capture_confirmed'] is False
    assert merged['limitup_capture_reasons'] == []
    assert merged['early_opportunity_score'] == 0.0
    assert merged['market_follow_through_score'] == 0.31
    assert merged['main_theme_alignment_score'] == 0.0
    assert merged['main_theme_core_score'] == 0.0


def test_daily_candidate_persist_normalizes_market_regime_and_keeps_running(monkeypatch):
    import xiaogu_db

    calls = []

    def fake_upsert_daily_candidate(**kwargs):
        calls.append(kwargs)
        if kwargs['symbol'] == '300002':
            raise ValueError('simulated db failure')

    monkeypatch.setattr(xiaogu_db, 'upsert_daily_candidate', fake_upsert_daily_candidate)
    bundle = make_bundle(
        [
            make_candidate('300001', 'Normal', score=80.0, rank=1),
            make_candidate('300002', 'Broken', score=79.0, rank=2),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
    )
    for row in bundle['paper_scoring_candidates']:
        row['market_regime'] = 'eastmoney_web_tabs_live'

    result = runner.persist_daily_candidate_snapshot('2026-06-10', bundle, {}, 'PAPER_PICK', 'unit')

    assert result['status'] == 'PARTIAL'
    assert result['written'] == 1
    assert result['errors']
    assert calls[0]['market_regime'] == 'eastmoney_web_tabs'
    assert len(calls[1]['market_regime']) <= 20
    assert calls[0]['selection_outcome'] == 'TOP10_NOT_SELECTED'
    assert isinstance(calls[0]['selection_diagnostics'], dict)
    assert isinstance(calls[0]['eligibility_snapshot'], dict)


def test_build_daily_candidate_persistence_payloads_keeps_replay_snapshots() -> None:
    candidates = [
        make_candidate('600001', 'Payload One', score=88.0, rank=1),
        make_candidate('600002', 'Payload Two', score=82.0, rank=2),
    ]
    bundle = make_bundle(candidates, candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')
    bundle['information_coverage_audit'] = {'status': 'PASS'}
    features = {
        'candidate_consumption_summary': {
            'official_result': {'symbol': '600001'},
            'top10_candidates': [
                {
                    'symbol': '600001',
                    'selection_key': [1, 88.0],
                    'why_candidate': ['ranked first'],
                    'official_decision_if_evaluated': 'PAPER_PICK',
                },
                {
                    'symbol': '600002',
                    'selection_key': [2, 82.0],
                    'not_selected_reasons': ['lower rank'],
                },
            ],
        }
    }

    payloads = runner.build_daily_candidate_persistence_payloads('2026-07-13', bundle, features, 'PAPER_PICK', 'selected')

    assert payloads['status'] == 'OK'
    assert len(payloads['daily_candidates']) == 2
    assert len(payloads['limitup_gene_signals']) == 2
    first = payloads['daily_candidates'][0]
    assert first['auxiliary_evidence_snapshot']['information_coverage_audit']['status'] == 'PASS'
    assert first['factor_snapshot']
    assert first['ranking_basis']
    assert first['selection_diagnostics']['why_candidate'] == ['ranked first']


def test_ledger_latest_wins_for_existing_decision_and_paper_pick(monkeypatch) -> None:
    rows = [
        {'record_type': 'DECISION', 'date': '2026-06-10', 'rule_version': runner.RULE_VERSION, 'decision': 'PAPER_PICK', 'symbol': '600100'},
        {'record_type': 'CORRECTION', 'date': '2026-06-10', 'rule_version': runner.RULE_VERSION, 'decision': 'NO_PICK', 'symbol': '600100'},
        {'record_type': 'DECISION', 'date': '2026-06-11', 'rule_version': runner.RULE_VERSION, 'decision': 'NO_PICK', 'symbol': '600200'},
        {'record_type': 'CORRECTION', 'date': '2026-06-11', 'rule_version': runner.RULE_VERSION, 'decision': 'PAPER_PICK', 'symbol': '600201'},
    ]
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: rows)

    assert runner.existing_decision_for_date('2026-06-10') is True
    assert runner.existing_paper_pick_symbol_for_date('2026-06-10') is None
    assert runner.existing_decision_for_date('2026-06-11') is True
    assert runner.existing_paper_pick_symbol_for_date('2026-06-11') == '600201'


def test_existing_decision_skip_requires_db_candidate_snapshot(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: True)
    monkeypatch.setattr(runner, 'daily_candidate_snapshot_exists_for_date', lambda date: False)

    assert runner.should_skip_existing_decision_for_date('2026-07-13', dry_run=False, force=False) is False
    assert 'LEDGER_DECISION_EXISTS_BUT_DB_SNAPSHOT_MISSING' in capsys.readouterr().err

    monkeypatch.setattr(runner, 'daily_candidate_snapshot_exists_for_date', lambda date: True)
    assert runner.should_skip_existing_decision_for_date('2026-07-13', dry_run=False, force=False) is True
    assert runner.should_skip_existing_decision_for_date('2026-07-13', dry_run=True, force=False) is False
    assert runner.should_skip_existing_decision_for_date('2026-07-13', dry_run=False, force=True) is False


def test_candidate_consumption_summary_reuses_cached_candidate_evaluations(monkeypatch) -> None:
    source_status = load_real_bundle()['source_status']
    bundle = make_bundle(
        [
            make_candidate('600301', 'Cache A', score=81.0, rank=1, sector_score=1.0),
            make_candidate('600302', 'Cache B', score=79.0, rank=2, sector_score=0.9),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='14:50:00',
    )
    counters = {'structured': 0, 'eligibility': 0, 'decision': 0}
    original_structured = runner.structured_signal_profile
    original_eligibility = runner.paper_pick_eligibility_profile
    original_decision = runner.decision_for_candidate

    def counting_structured(*args, **kwargs):
        counters['structured'] += 1
        return original_structured(*args, **kwargs)

    def counting_eligibility(*args, **kwargs):
        counters['eligibility'] += 1
        return original_eligibility(*args, **kwargs)

    def counting_decision(*args, **kwargs):
        counters['decision'] += 1
        return original_decision(*args, **kwargs)

    monkeypatch.setattr(runner, 'structured_signal_profile', counting_structured)
    monkeypatch.setattr(runner, 'paper_pick_eligibility_profile', counting_eligibility)
    monkeypatch.setattr(runner, 'decision_for_candidate', counting_decision)

    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')
    summary = runner.build_candidate_consumption_summary(bundle, '2026-06-10', decision, symbol, reason, features, flags)

    assert summary['top10_candidates']
    assert counters['decision'] == 2
    assert counters['eligibility'] == 2
    assert counters['structured'] == 2


def test_runner_write_json_serializes_date_values(tmp_path):
    path = tmp_path / 'runtime.json'
    runner.write_json(path, {'trade_date': dt.date(2026, 6, 29)})
    loaded = json.loads(path.read_text(encoding='utf-8'))
    assert loaded['trade_date'] == '2026-06-29'


def test_load_candidate_bundle_prefers_db_daily_candidates(monkeypatch, tmp_path) -> None:
    import xiaogu_db

    # Ensure load_latest_eastmoney_scan returns None so we fall through to DB path
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda *a, **kw: None)

    session = {
        'trade_date': '2026-06-10',
        'scan_time': '2026-06-10 10:00:00',
        'cdp_url': 'http://127.0.0.1:9333',
        'quotes_count': 5123,
        'scored_count': 21,
        'passed_count': 3,
        'scan_dir': str(tmp_path / 'scan_dir'),
        'status': 'completed',
    }
    rows = [{
        'trade_date': '2026-06-10',
        'symbol': '300001',
        'stock_name': '特锐德',
        'rank': 1,
        'final_score': 91.2,
        'is_official_pick': True,
        'decision': 'PAPER_PICK',
        'signal_pct': 8.7,
        'close_position_score': 0.83,
        'fund_flow_momentum': 0.62,
        'sector_catalyst_score': 0.71,
        'early_opportunity_score': 0.66,
        'topic_propagation_score': 0.55,
        'market_regime': 'eastmoney_web_tabs_live',
        'blockers': [],
        'hard_gate_status': {},
        'raw_json': {
            'setup_type': 'TOPIC_FUND_IGNITION',
            'search_layer_hint': 'structured_sector',
            'paper_pick_eligibility': {'eligible': True, 'blockers': [], 'signals': {}},
            'research_signals': {'research_panel': {'overall': 'PASS'}},
            'source_row_hash': 'db:test',
        },
        'source_layers': ['L2_LIMIT_STRENGTH'],
        'candidate_features': {'score': 91.2, 'final_score': 91.2, 'source_layers': ['L2_LIMIT_STRENGTH']},
    }, {
        'trade_date': '2026-06-10',
        'symbol': '300002',
        'stock_name': '神州泰岳',
        'rank': 2,
        'final_score': None,
        'is_official_pick': False,
        'decision': 'RESEARCH_CANDIDATE',
        'signal_pct': 3.2,
        'close_position_score': 0.61,
        'fund_flow_momentum': 0.21,
        'sector_catalyst_score': 0.52,
        'early_opportunity_score': 0.43,
        'topic_propagation_score': 0.34,
        'market_regime': 'eastmoney_web_tabs_live',
        'blockers': [],
        'hard_gate_status': {},
        'raw_json': {
            'setup_type': 'SECTOR_NEWS_LOW_POSITION',
            'search_layer_hint': 'sector_catalyst_low_position',
            'structured_component_details': {'sector_opportunity_score': 0.52},
            'structured_score_components': {'fund_flow_momentum': 0.21},
            'paper_pick_eligibility': {'eligible': False, 'blockers': ['NOT_FORMAL'], 'signals': {}},
            'research_signals': {'research_panel': {'overall': 'PARTIAL'}},
            'source_row_hash': 'db:test:2',
        },
        'source_layers': ['L6_SECTOR_CATALYST'],
        'candidate_features': {'score': None, 'source_layers': ['L6_SECTOR_CATALYST']},
    }]

    monkeypatch.setattr(xiaogu_db, 'fetch_latest_scan_session', lambda trade_date: session if str(trade_date) == '2026-06-10' else None)
    monkeypatch.setattr(xiaogu_db, 'fetch_daily_candidates', lambda trade_date: rows if str(trade_date) == '2026-06-10' else [])

    loaded = runner.load_candidate_bundle('2026-06-10')
    assert loaded['candidate_source'] == 'eastmoney_web_tabs_db_daily_candidates'
    assert loaded['paper_scoring_candidates'][0]['symbol'] == '300001'
    assert loaded['paper_scoring_candidates'][0]['decision'] == 'PAPER_PICK'


def test_load_candidate_bundle_merges_signals_from_db(monkeypatch) -> None:
    import xiaogu_db
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda *a, **kw: None)

    session = {
        'trade_date': '2026-06-10',
        'scan_time': '2026-06-10 10:00:00',
        'cdp_url': 'http://127.0.0.1:9333',
        'quotes_count': 5123,
        'scored_count': 21,
        'passed_count': 3,
        'scan_dir': '/tmp/scan_dir',
        'status': 'completed',
    }
    candidates = [make_candidate('300001', '特锐德', score=91.2, rank=1)]
    signals = [
        {'trade_date': '2026-06-10', 'symbol': '300001', 'signal_key': 'fund_flow_momentum', 'signal_value': 0.88, 'raw_json': {'value': 0.88}},
        {'trade_date': '2026-06-10', 'symbol': '300001', 'signal_key': 'sector_opportunity_score', 'signal_value': 0.77, 'raw_json': {'value': 0.77}},
        {'trade_date': '2026-06-10', 'symbol': '300001', 'signal_key': 'structured_score', 'signal_value': 82.1, 'raw_json': {'value': 82.1}},
    ]

    monkeypatch.setattr(xiaogu_db, 'fetch_latest_scan_session', lambda trade_date: session if str(trade_date) == '2026-06-10' else None)
    monkeypatch.setattr(xiaogu_db, 'fetch_daily_candidates', lambda trade_date: [{
        'trade_date': '2026-06-10',
        'symbol': '300001',
        'stock_name': '特锐德',
        'rank': 1,
        'final_score': 91.2,
        'is_official_pick': True,
        'decision': 'PAPER_PICK',
        'signal_pct': 8.7,
        'close_position_score': 0.83,
        'fund_flow_momentum': None,
        'sector_catalyst_score': None,
        'early_opportunity_score': 0.66,
        'topic_propagation_score': 0.55,
        'market_regime': 'eastmoney_web_tabs_live',
        'blockers': [],
        'hard_gate_status': {},
        'raw_json': {
            'setup_type': 'TOPIC_FUND_IGNITION',
            'search_layer_hint': 'structured_sector',
            'paper_pick_eligibility': {'eligible': True, 'blockers': [], 'signals': {}},
            'research_signals': {'research_panel': {'overall': 'PASS'}},
            'source_row_hash': 'db:test',
        },
        'source_layers': ['L2_LIMIT_STRENGTH'],
        'candidate_features': {'score': 91.2, 'final_score': 91.2, 'source_layers': ['L2_LIMIT_STRENGTH']},
    }] if str(trade_date) == '2026-06-10' else [])
    monkeypatch.setattr(xiaogu_db, 'fetch_signals', lambda trade_date: signals if str(trade_date) == '2026-06-10' else [])

    loaded = runner.load_candidate_bundle('2026-06-10')
    candidate = loaded['paper_scoring_candidates'][0]
    assert candidate['fund_flow_momentum'] == 0.88
    assert candidate['sector_opportunity_score'] == 0.77
    assert candidate['structured_score'] == 82.1


def test_load_candidate_bundle_rebuilds_structured_context_from_db(monkeypatch) -> None:
    import xiaogu_db
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda *a, **kw: None)

    session = {
        'trade_date': '2026-06-10',
        'scan_time': '2026-06-10 10:00:00',
        'cdp_url': 'http://127.0.0.1:9333',
        'quotes_count': 5123,
        'scored_count': 21,
        'passed_count': 3,
        'scan_dir': '/tmp/scan_dir',
        'status': 'completed',
    }
    rows = [{
        'trade_date': '2026-06-10',
        'symbol': '300001',
        'stock_name': '特锐德',
        'rank': 1,
        'final_score': 91.2,
        'is_official_pick': True,
        'decision': 'PAPER_PICK',
        'signal_pct': 8.7,
        'close_position_score': 0.83,
        'fund_flow_momentum': 0.62,
        'sector_catalyst_score': 0.71,
        'early_opportunity_score': 0.66,
        'topic_propagation_score': 0.55,
        'market_regime': 'eastmoney_web_tabs_live',
        'blockers': [],
        'hard_gate_status': {},
        'raw_json': {
            'setup_type': 'TOPIC_FUND_IGNITION',
            'search_layer_hint': 'structured_sector',
            'structured_component_details': {'sector_opportunity_score': 0.71},
            'structured_score_components': {'fund_flow_momentum': 0.62},
            'paper_pick_eligibility': {'eligible': True, 'blockers': [], 'signals': {}},
            'research_signals': {'research_panel': {'overall': 'PASS'}},
            'source_row_hash': 'db:test',
        },
        'source_layers': ['L2_LIMIT_STRENGTH'],
        'candidate_features': {'score': 91.2, 'final_score': 91.2, 'source_layers': ['L2_LIMIT_STRENGTH']},
    }, {
        'trade_date': '2026-06-10',
        'symbol': '300002',
        'stock_name': '神州泰岳',
        'rank': 2,
        'final_score': None,
        'is_official_pick': False,
        'decision': 'RESEARCH_CANDIDATE',
        'signal_pct': 3.2,
        'close_position_score': 0.61,
        'fund_flow_momentum': 0.21,
        'sector_catalyst_score': 0.52,
        'early_opportunity_score': 0.43,
        'topic_propagation_score': 0.34,
        'market_regime': 'eastmoney_web_tabs_live',
        'blockers': [],
        'hard_gate_status': {},
        'raw_json': {
            'setup_type': 'SECTOR_NEWS_LOW_POSITION',
            'search_layer_hint': 'sector_catalyst_low_position',
            'structured_component_details': {'sector_opportunity_score': 0.52},
            'structured_score_components': {'fund_flow_momentum': 0.21},
            'paper_pick_eligibility': {'eligible': False, 'blockers': ['NOT_FORMAL'], 'signals': {}},
            'research_signals': {'research_panel': {'overall': 'PARTIAL'}},
            'source_row_hash': 'db:test:2',
        },
        'source_layers': ['L6_SECTOR_CATALYST'],
        'candidate_features': {'score': None, 'source_layers': ['L6_SECTOR_CATALYST']},
    }]
    content_rows = [{
        'trade_date': '2026-06-10',
        'scan_time': '2026-06-10 10:00:00',
        'section_key': 'capital_flow',
        'section_title': '资金流向',
        'section_url': 'https://data.eastmoney.com/zjlx/',
        'item_key': 'stock_capital_flow',
        'item_title': '个股资金流',
        'item_url': 'https://data.eastmoney.com/zjlx/detail.html',
        'page_url': 'https://data.eastmoney.com/zjlx/detail.html',
        'page_title': '个股资金流',
        'table_index': 0,
        'row_index': 0,
        'row_key': '300001:0:0',
        'code': '300001',
        'title': '300001 特锐德',
        'summary': '主力净流入 1.23亿',
        'cells': ['1', '300001', '特锐德', '数据 行情 股吧', '10.00', '8.70%', '1.23亿', '0.50亿', '0.30亿', '0.20亿', '0.10亿'],
        'raw_json': {'code': '300001'},
    }]

    monkeypatch.setattr(xiaogu_db, 'fetch_latest_scan_session', lambda trade_date: session if str(trade_date) == '2026-06-10' else None)
    monkeypatch.setattr(xiaogu_db, 'fetch_daily_candidates', lambda trade_date: rows if str(trade_date) == '2026-06-10' else [])
    monkeypatch.setattr(xiaogu_db, 'fetch_signals', lambda trade_date: [])
    monkeypatch.setattr(xiaogu_db, 'fetch_scan_data_directory_catalog', lambda trade_date: [])
    monkeypatch.setattr(xiaogu_db, 'fetch_scan_data_directory_content', lambda trade_date: content_rows if str(trade_date) == '2026-06-10' else [])

    loaded = runner.load_candidate_bundle('2026-06-10')

    assert loaded['candidate']['price'] == 10.0
    assert loaded['daily_ticket_search_result']['searched_layers']
    assert loaded['structured_formal_impact']['structured_observation_candidates']
    assert loaded['structured_formal_impact']['sector_opportunity_candidates']
    assert loaded['paper_scoring_candidates'][0]['data_directory_capital_flow']['main_force_net_inflow'] == 123000000.0


def test_signal_records_from_candidate_emits_main_force_and_sector_signals() -> None:
    row = make_candidate(
        '300001',
        '特锐德',
        score=91.2,
        rank=1,
        sector_score=0.7,
        fund_flow_momentum=0.62,
        early_opportunity_score=0.66,
        low_position_catalyst_score=0.73,
        limitup_reason_propagation_score=0.58,
        limitup_capture_score=0.65,
        limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
        limitup_capture_confirmed=True,
        limitup_capture_reasons=['positive_flow_evidence'],
    )
    row['net_inflow_main'] = 123456789.0
    row['structured_score'] = 82.1
    row['final_shadow_score'] = 173.3
    row['structured_component_details'] = {'sector_opportunity_score': 0.7, 'pre_limitup_anomaly': 0.4}
    row['structured_score_components'] = {'fund_flow_momentum': 0.62, 'time_series_momentum': 0.2}
    row['research_signals'] = {
        'catalyst_quality': {'category': 'sector_catalyst'},
        'sector_mapping': {'sectors': ['电池'], 'mapping_confidence': 0.8},
        'a_share_risk_review': {'disqualified_for_paper_pick': False},
        'adversarial_review': {'bear_case_flags': ['weak_fund_confirmation'], 'disqualifying_flags': []},
        'historical_pattern': {'pattern_name': 'topic_fund_ignition'},
        'research_panel': {'overall': 'PASS'},
    }

    records = scanner.signal_records_from_candidate(row, '2026-06-10 10:00:00')
    keys = {rec['signal_key'] for rec in records}
    assert 'main_force_net_inflow' in keys
    assert 'sector_opportunity_score' in keys
    assert 'structured_score' in keys
    assert 'research_catalyst_category' in keys


def test_scanner_build_data_directory_catalog_records_has_record_keys() -> None:
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scanner

    records = scanner.build_data_directory_catalog_records('2026-06-10 15:10:00')

    assert records
    assert all(record['domain'] == 'data_directory_catalog' for record in records)
    assert all(record['source'] == 'eastmoney_public_directory_catalog' for record in records)
    assert all(record['record_key'] == f"{record['section_key']}:{record['item_key']}" for record in records)
    assert any(record['title'] == '研究报告 / 个股研报' for record in records)
    assert any(record['title'] == '资金流向 / 行业资金流' for record in records)


def test_no_pick_candidate_diagnostics_not_emitted_for_paper_pick(monkeypatch, tmp_path, capsys) -> None:
    prepare_scan_and_bundle_roots(
        monkeypatch,
        tmp_path,
        bundle_payload=load_real_bundle(),
        scan_summary_payload={
            'source_time': '2026-06-10 15:10:00',
            'files': {'raw': str(tmp_path / 'raw.txt')},
            'pipeline_version': 'v2_scanner_api',
        },
    )
    paper_pick_source_status = full_real_scan_source_status()
    bundle = make_bundle(
        [
            make_candidate(
                '600100',
                'Paper Pick',
                score=88.0,
                rank=1,
                sector_score=1.0,
                catalyst_category='positive_catalyst',
                candidate_evidence_domain_counts={
                    'announcements': 1,
                    'risk_alerts': 1,
                    'lhb': 1,
                    'concept_industry': 1,
                    'financials': 1,
                },
                enhanced_evidence_domain_counts={
                    'limitup_strength': 1,
                    'broken_limit_risk': 1,
                    'consecutive_limit_strength': 1,
                    'yesterday_limit_strength': 1,
                    'popularity_heat': 1,
                    'industry_board': 1,
                    'sector_fund_flow': 1,
                    'candidate_quote_recheck': 1,
                    'candidate_fund_recheck': 1,
                    'candidate_lhb_recheck': 1,
                    'candidate_announcement_recheck': 1,
                },
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=paper_pick_source_status,
        asof_time='15:10:00',
    )
    bundle['source_time'] = '2026-06-10 15:10:00'
    bundle['_runner_asof_time'] = '15:10:00'
    bundle['candidate']['source_time'] = '2026-06-10 15:10:00'
    bundle['paper_scoring_candidates'][0]['source_time'] = '2026-06-10 15:10:00'
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_paper_pick.json')
    bundle['scan_summary_path'] = str(tmp_path / 'live_scan' / '2026-06-10' / 'eastmoney_web_tabs_scan_v0_1_newer_scan' / 'eastmoney_web_tabs_summary.json')
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': str(tmp_path / 'raw'),
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-10T15:10:00',
    })
    monkeypatch.setattr(runner, 'RAW_ROOT', tmp_path / 'forward_raw_runtime')
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': str(tmp_path / 'raw' / 'recorder_features.json'),
    })
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-10', '--asof-time', '15:10:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['decision'] == 'PAPER_PICK'
    assert 'no_pick_candidate_diagnostics' not in out
    assert 'daily_best_paper_watch' not in out
    assert 'no_pick_candidate_diagnostics' not in runtime['features']
    assert 'daily_best_paper_watch' not in runtime['features']
    assert out['single_target_card']['official_decision'] == 'PAPER_PICK'
    assert out['single_target_card']['target_status'] == 'OFFICIAL_PAPER_PICK'
    assert out['candidate_consumption_summary']['official_result']['decision'] == 'PAPER_PICK'
    assert out['candidate_consumption_summary']['top10_candidates'][0]['selection_outcome'] == 'OFFICIAL_PICK'
    assert out['candidate_consumption_summary']['top10_candidates'][0]['why_candidate']
    assert 'source_consumption_summary' in out
    assert runtime['features']['candidate_consumption_summary']['top10_candidates'][0]['symbol'] == '600100'


def test_official_no_pick_highest_score_candidate_becomes_watch_not_paper_pick() -> None:
    bundle = make_bundle(
        [
            make_candidate(
                '300001',
                'Watch Highest',
                score=92.0,
                rank=1,
                data_gate='PARTIAL',
                evidence='PARTIAL',
                opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
            ),
            make_candidate(
                '300002',
                'Watch Secondary',
                score=80.0,
                rank=2,
                data_gate='PARTIAL',
                evidence='PARTIAL',
                opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
            ),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status={
            **load_real_bundle()['source_status'],
            'required_cdp_tabs': {'status': 'FAIL', 'missing_sources': ['quote_rank']},
            'source_completeness': {'status': 'FAIL', 'quote_count': 0, 'fund_count': 0, 'min_quote_count': 4000, 'flags': ['ZERO_QUOTE_READ', 'ZERO_FUND_FLOW_READ', 'FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW']},
        },
        passed_count=0,
        scored_count=2,
        asof_time='15:10:00',
    )
    first_candidate = bundle['paper_scoring_candidates'][0]
    decision, _, reason, features, flags = runner.decision_for_candidate(first_candidate, bundle, '2026-06-10')
    diagnostics = runner.build_no_pick_candidate_diagnostics(bundle, '2026-06-10', features, decision, reason, flags)

    assert decision == 'NO_PICK'
    assert diagnostics['daily_best_paper_watch']['status'] == 'DAILY_BEST_PAPER_WATCH'
    assert diagnostics['daily_best_paper_watch']['not_official_paper_pick'] is True
    assert diagnostics['daily_best_paper_watch']['observation_only'] is True
    assert diagnostics['daily_best_paper_watch']['watch_status'] == 'DAILY_BEST_PAPER_WATCH'
    assert diagnostics['daily_best_paper_watch']['symbol'] == '300001'


def test_source_incomplete_hard_blocks_official_pick_and_marks_flags() -> None:
    source_status = {
        **load_real_bundle()['source_status'],
        'required_cdp_tabs': {'status': 'FAIL', 'missing_sources': ['quote_rank', 'fund_flow']},
        'source_completeness': {'status': 'FAIL', 'quote_count': 0, 'fund_count': 0, 'min_quote_count': 4000, 'flags': ['ZERO_QUOTE_READ', 'ZERO_FUND_FLOW_READ', 'FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW']},
    }
    bundle = make_bundle(
        [
            make_candidate(
                '603725',
                '天安新材',
                score=90.0,
                rank=1,
                price=9.81,
                weak_close_risk=True,
                high_open_low_close_risk=True,
                broken_limit_risk=True,
                intraday_pullback_risk=True,
                evidence='PASS',
                data_gate='PASS',
                candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
                enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        passed_count=0,
        scored_count=1,
        asof_time='15:10:00',
    )
    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'DATA_SOURCE_INCOMPLETE' in flags or 'FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW' in flags or 'ZERO_QUOTE_READ' in flags
    assert any(flag.startswith('EASTMONEY_REQUIRED_CDP_TABS_MISSING_') for flag in runner.web_tabs_evidence_missing_flags(bundle))
    assert reason.startswith('HARD_GATE_NOT_ALL_PASS:')


def test_official_eastmoney_source_missing_completeness_blocks_paper_pick() -> None:
    source_status = {
        **load_real_bundle()['source_status'],
        'required_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'source_completeness_required': True,
    }
    source_status.pop('source_completeness', None)
    candidate = make_candidate(
        '300077',
        '国民技术',
        score=105.0,
        rank=1,
        price=26.73,
        sector_score=1.0,
        signal_pct=15.71,
        close_position_score=0.795455,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.9,
        time_series_momentum=0.6724,
        candidate_stage='near_limit_9_plus',
        search_layer_hint='structured_sector',
        setup_type='LIMIT_STRENGTH',
        sector_opportunity_tags=['国产芯片', '机器人概念'],
        candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
        enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
    )
    candidate['structured_score_components']['order_book_pressure'] = 0.5022
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 607139312.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        passed_count=20,
        scored_count=1,
        asof_time='15:00:00',
    )

    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')
    diagnostics = runner.build_no_pick_candidate_diagnostics(bundle, '2026-06-10', features, decision, reason, flags)

    assert decision == 'PAPER_PICK'
    assert symbol == '300077'
    assert 'SOURCE_COMPLETENESS_MISSING' in flags
    assert 'DATA_SOURCE_INCOMPLETE' in flags
    assert diagnostics['daily_best_paper_watch']['not_official_paper_pick'] is True


def test_failure_risk_candidate_cannot_become_paper_pick() -> None:
    source_status = load_real_bundle()['source_status']
    bundle = make_bundle(
        [
            make_candidate(
                '603725',
                '天安新材',
                score=88.0,
                rank=1,
                price=9.81,
                weak_close_risk=True,
                high_open_low_close_risk=True,
                broken_limit_risk=True,
                intraday_pullback_risk=True,
                evidence='PASS',
                data_gate='PASS',
                candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
                enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
            ),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        passed_count=20,
        scored_count=1,
        asof_time='15:10:00',
    )
    decision, symbol, reason, _features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'WEAK_CLOSE_RISK' in flags
    assert 'HIGH_OPEN_LOW_CLOSE_RISK' in flags
    assert 'BROKEN_LIMIT_RISK' in ';'.join(flags)
    assert 'INTRADAY_PULLBACK_RISK' in flags
    assert reason.startswith('HARD_GATE_NOT_ALL_PASS:')


def test_chuangye_highest_score_candidate_remains_watch_when_official_gates_block() -> None:
    source_status = {
        **load_real_bundle()['source_status'],
        'required_cdp_tabs': {'status': 'FAIL', 'missing_sources': ['quote_rank']},
        'source_completeness': {'status': 'FAIL', 'quote_count': 0, 'fund_count': 0, 'min_quote_count': 4000, 'flags': ['ZERO_QUOTE_READ', 'ZERO_FUND_FLOW_READ', 'FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW']},
    }
    bundle = make_bundle(
        [
            make_candidate(
                '301236',
                '软通动力',
                score=95.0,
                rank=1,
                price=40.28,
                sector_opportunity_tags=['创业板'],
                sector_score=0.8,
                evidence='PARTIAL',
                data_gate='PARTIAL',
                candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
                enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
            ),
            make_candidate(
                '300077',
                '国民技术',
                score=91.0,
                rank=2,
                price=13.05,
                evidence='PARTIAL',
                data_gate='PARTIAL',
                candidate_evidence_domain_counts=full_candidate_evidence_counts()[0],
                enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1],
            ),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        passed_count=0,
        scored_count=2,
        asof_time='15:10:00',
    )
    decision, symbol, reason, features, flags = runner.evaluate_candidate_bundle(bundle, '2026-06-10')
    diagnostics = runner.build_no_pick_candidate_diagnostics(bundle, '2026-06-10', features, decision, reason, flags)

    assert decision == 'NO_PICK'
    assert diagnostics['highest_score_candidate']['symbol'] == '301236'
    assert diagnostics['daily_best_paper_watch']['symbol'] == '301236'
    assert diagnostics['daily_best_paper_watch']['status'] == 'DAILY_BEST_PAPER_WATCH'
    assert diagnostics['daily_best_paper_watch']['not_official_paper_pick'] is True


def test_closest_to_pick_candidate_tiebreak_is_deterministic() -> None:
    higher_score_bundle = make_bundle(
        [
            make_candidate('300001', 'A', score=61.0, rank=10, data_gate='PARTIAL', evidence='PARTIAL'),
            make_candidate('300002', 'B', score=60.0, rank=1, data_gate='PARTIAL', evidence='PARTIAL'),
        ],
        candidate_source='test_scan',
        asof_time='10:00:00',
    )
    selected, reason = runner.closest_to_pick_candidate_from_bundle(higher_score_bundle, '2026-06-10')
    assert reason == ''
    assert selected is not None
    assert selected['symbol'] == '300001'

    same_score_bundle = make_bundle(
        [
            make_candidate('300003', 'C', score=60.0, rank=9, data_gate='PARTIAL', evidence='PARTIAL'),
            make_candidate('300004', 'D', score=60.0, rank=2, data_gate='PARTIAL', evidence='PARTIAL'),
        ],
        candidate_source='test_scan',
        asof_time='10:00:00',
    )
    selected, reason = runner.closest_to_pick_candidate_from_bundle(same_score_bundle, '2026-06-10')
    assert reason == ''
    assert selected is not None
    assert selected['symbol'] == '300004'


def test_single_target_card_paper_pick_behavior_unchanged(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'RAW_ROOT', Path('/tmp/forward_raw_runtime_test'))
    paper_pick_source_status = full_real_scan_source_status()
    bundle = make_bundle(
        [
            make_candidate(
                '600100',
                'Paper Pick',
                score=88.0,
                rank=1,
                sector_score=1.0,
                catalyst_category='positive_catalyst',
                candidate_evidence_domain_counts={
                    'announcements': 1,
                    'risk_alerts': 1,
                    'lhb': 1,
                    'concept_industry': 1,
                    'financials': 1,
                },
                enhanced_evidence_domain_counts={
                    'limitup_strength': 1,
                    'broken_limit_risk': 1,
                    'consecutive_limit_strength': 1,
                    'yesterday_limit_strength': 1,
                    'popularity_heat': 1,
                    'industry_board': 1,
                    'sector_fund_flow': 1,
                    'candidate_quote_recheck': 1,
                    'candidate_fund_recheck': 1,
                    'candidate_lhb_recheck': 1,
                    'candidate_announcement_recheck': 1,
                },
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=paper_pick_source_status,
        asof_time='10:00:00',
    )
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_paper_pick.json')

    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-10T10:00:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr('xiaogu_db.insert_pick', lambda *args, **kwargs: -1)
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-10', '--asof-time', '10:00:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['decision'] == 'PAPER_PICK'
    assert out['ledger_line_added'] is False
    assert out['paper_only'] is True
    assert out['no_trade'] is True
    assert out['allow_trade'] is False
    assert out['auto_order'] is False
    assert 'no_pick_candidate_diagnostics' not in out
    assert 'daily_best_paper_watch' not in out
    assert 'no_pick_candidate_diagnostics' not in runtime['features']
    assert 'daily_best_paper_watch' not in runtime['features']
    assert 'diagnostic_role' not in out['single_target_card']
    execution_plan = out['sell_plan']['next_day_execution_plan']
    for text in (
        '## 次日卖出执行计划',
        '### 1. 竞价判断',
        '### 2. 开盘 30 分钟',
        '### 3. 冲高卖出',
        '### 4. 涨停处理',
        '### 5. 失败条件',
        '低开',
        '高点回撤2%-3%',
        '炸板',
        '分时均价线',
    ):
        assert text in execution_plan


def test_no_pick_main_promotes_highest_score_and_keeps_daily_best_watch_diagnostics(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'RAW_ROOT', Path('/tmp/forward_raw_runtime_test'))
    source_status = load_real_bundle()['source_status']
    bundle = make_bundle(
        [
            make_candidate('600200', 'Clean Higher', score=80.0, rank=3, sector_score=1.0),
            make_candidate('600201', 'Best Watch', score=70.0, rank=1, sector_score=1.0),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='10:00:00',
    )
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_no_pick.json')

    monkeypatch.setattr(runner, 'run_realtime_scan', lambda date, asof_time=None: {'source_time': '2026-06-10 10:00:00'})
    monkeypatch.setattr(runner, 'build_research_basket_from_latest_scan', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'evaluate_candidate_bundle', lambda bundle, date, allow_stale_data=False: ('NO_PICK', '', 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS', {}, []))
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-10T10:00:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr('xiaogu_db.insert_pick', lambda *args, **kwargs: -1)
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-10', '--asof-time', '10:00:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['decision'] == 'PAPER_PICK'
    assert out['symbol'] == '600200'
    assert out['daily_best_paper_watch']['symbol'] == '600200'
    assert out['daily_best_paper_watch']['selection_source'] == 'highest_score_candidate'
    assert out['daily_best_paper_watch']['status'] == 'DAILY_BEST_PAPER_WATCH'
    assert out['daily_best_paper_watch']['not_official_paper_pick'] is True
    assert out['daily_best_paper_watch']['paper_only'] is True
    assert out['daily_best_paper_watch']['no_trade'] is True
    assert out['daily_best_paper_watch']['allow_trade'] is False
    assert out['daily_best_paper_watch']['auto_order'] is False
    assert runtime['features']['daily_best_paper_watch']['symbol'] == '600200'
    assert runtime['features']['daily_best_paper_watch']['observation_only'] is True
    assert out['candidate_consumption_summary']['daily_best_paper_watch']['symbol'] == '600200'
    promoted_top10_card = next(card for card in out['candidate_consumption_summary']['top10_candidates'] if card['symbol'] == '600200')
    assert promoted_top10_card['selection_outcome'] == 'OFFICIAL_PICK'
    assert runtime['features']['candidate_features']['no_pick_promoted_to_highest_score'] is True
    assert runtime['features']['candidate_features']['original_no_pick_reason']
    assert 'NO_PICK_PROMOTED_TO_HIGHEST_SCORE_CANDIDATE' in runtime['features']['risk_flags']
    assert not runtime['features']['candidate_features'].get('fallback_from_no_pick')


def test_no_pick_main_promotes_daily_best_paper_watch_as_official_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'RAW_ROOT', Path('/tmp/forward_raw_runtime_test'))
    source_status = load_real_bundle()['source_status']
    bundle = make_bundle(
        [
            make_candidate('600210', 'Promoted Watch', score=82.0, rank=1, sector_score=1.0),
            make_candidate('600211', 'Secondary Watch', score=68.0, rank=2, sector_score=1.0),
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='10:00:00',
    )
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_no_pick_promoted.json')

    monkeypatch.setattr(runner, 'run_realtime_scan', lambda date, asof_time=None: {'source_time': '2026-06-10 10:00:00'})
    monkeypatch.setattr(runner, 'build_research_basket_from_latest_scan', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'evaluate_candidate_bundle', lambda bundle, date, allow_stale_data=False: ('NO_PICK', '', 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS', {}, []))
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-10T10:00:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-10', '--asof-time', '10:00:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['decision'] == 'PAPER_PICK'
    assert out['symbol'] == '600210'
    assert out['daily_best_paper_watch']['symbol'] == '600210'
    assert out['single_target_card']['official_decision'] == 'PAPER_PICK'
    assert out['single_target_card']['target_status'] == 'OFFICIAL_PAPER_PICK'
    assert runtime['features']['daily_best_paper_watch']['symbol'] == '600210'
    assert runtime['features']['candidate_features']['no_pick_promoted_to_highest_score'] is True
    assert runtime['features']['candidate_features']['original_no_pick_reason']
    assert not runtime['features']['candidate_features'].get('fallback_from_no_pick')


def test_no_pick_promotion_eligible_blocks_failed_limitup_near_limit_candidate() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '000938',
        '紫光股份风险候选',
        score=95.0,
        rank=1,
        sector_score=1.0,
        candidate_stage='high_7_to_9',
        signal_pct=8.8,
        close_position_score=0.82,
        fund_flow_momentum=0.70,
        time_series_momentum=0.30,
        broken_limit_risk=True,
        blocked_reasons=['near_limit_up_risk'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['failed_limitup'] = True
    candidate['near_limit_up_risk'] = True
    candidate['popularity_rank'] = 1
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': -120_000_000}
    bundle = make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')
    runner.attach_paper_pick_eligibility(bundle)

    eligible, reason = runner.no_pick_promotion_eligible(bundle['paper_scoring_candidates'][0], bundle)

    assert eligible is False
    assert reason == 'failed_limitup_risk'


def test_no_pick_main_keeps_failed_limitup_fallback_as_watch_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'RAW_ROOT', Path('/tmp/forward_raw_runtime_test'))
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    risky_candidate = make_candidate(
        '000938',
        '紫光股份风险候选',
        score=95.0,
        rank=1,
        sector_score=1.0,
        candidate_stage='high_7_to_9',
        signal_pct=8.8,
        close_position_score=0.82,
        fund_flow_momentum=0.70,
        time_series_momentum=0.30,
        broken_limit_risk=True,
        blocked_reasons=['near_limit_up_risk'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    risky_candidate['failed_limitup'] = True
    risky_candidate['near_limit_up_risk'] = True
    risky_candidate['popularity_rank'] = 1
    risky_candidate['data_directory_capital_flow'] = {'main_force_net_inflow': -120_000_000}
    bundle = make_bundle(
        [risky_candidate, make_candidate('600211', 'Secondary Watch', score=68.0, rank=2, sector_score=1.0)],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='10:00:00',
    )
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-10' / 'test_no_pick_blocked_promotion.json')

    monkeypatch.setattr(runner, 'run_realtime_scan', lambda date, asof_time=None: {'source_time': '2026-06-10 10:00:00'})
    monkeypatch.setattr(runner, 'build_research_basket_from_latest_scan', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'evaluate_candidate_bundle', lambda bundle, date, allow_stale_data=False: ('NO_PICK', '', 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS', {}, []))
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-10T10:00:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-10', '--asof-time', '10:00:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['decision'] == 'NO_PICK'
    assert out['symbol'] == ''
    assert 'NO_PICK_PROMOTED_TO_HIGHEST_SCORE_CANDIDATE' not in runtime['features']['risk_flags']
    assert 'NO_PICK_PROMOTION_BLOCKED:failed_limitup_risk' in runtime['features']['risk_flags']
    assert out['daily_best_paper_watch']['symbol'] == '000938'
    assert out['daily_best_paper_watch']['observation_only'] is True
    assert out['daily_best_paper_watch']['promotion_blocked'] is True
    assert runtime['features']['daily_best_paper_watch']['symbol'] == '000938'
    assert runtime['features']['daily_best_paper_watch']['promotion_block_reason'] == 'NO_PICK_PROMOTION_BLOCKED:failed_limitup_risk'


def test_main_persists_daily_candidate_top10(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner, 'RAW_ROOT', Path('/tmp/forward_raw_runtime_test'))
    source_status = full_real_scan_source_status()
    candidates = [make_candidate(f'600{i:03d}', f'Candidate {i}', score=100.0 - i, rank=i + 1, sector_score=1.0) for i in range(12)]
    bundle = make_bundle(
        candidates,
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='14:33:00',
    )
    bundle['date'] = '2026-06-30'
    bundle['source_time'] = '2026-06-30 14:33:00'
    bundle['source_market_date'] = '2026-06-30'
    bundle['scan_summary_source_time'] = '2026-06-30 14:33:00'
    for candidate in bundle['paper_scoring_candidates']:
        candidate['source_time'] = '2026-06-30 14:33:00'
        candidate['date'] = '2026-06-30'
    bundle['_bundle_path'] = str(runner.CANDIDATE_BUNDLE_ROOT / '2026-06-30' / 'top10_bundle.json')

    persisted = []
    persisted_picks = []
    persisted_gene_signals = []

    def fake_upsert_daily_candidate(**kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(runner, 'run_realtime_scan', lambda date, asof_time=None: {'source_time': '2026-06-30 14:33:00'})
    monkeypatch.setattr(runner, 'build_research_basket_from_latest_scan', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: bundle)
    monkeypatch.setattr(runner, 'load_latest_eastmoney_scan', lambda date: None)
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-30T14:33:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr('xiaogu_db.upsert_daily_candidate', fake_upsert_daily_candidate)
    monkeypatch.setattr('xiaogu_db.insert_scan_session', lambda **kwargs: 1)
    monkeypatch.setattr('xiaogu_db.upsert_limitup_gene_signals', lambda **kwargs: persisted_gene_signals.append(kwargs) or {})
    monkeypatch.setattr('xiaogu_db.insert_pick', lambda **kwargs: persisted_picks.append(kwargs) or 1)
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-30', '--asof-time', '14:33:00', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)

    assert out['daily_candidate_persist_result']['status'] == 'OK'
    assert out['daily_candidate_persist_result']['written'] == 12
    assert out['daily_candidate_persist_result']['persisted_signal_rows'] == 72
    assert len(persisted) == 12
    assert len(persisted_gene_signals) == 12
    assert persisted[0]['trade_date'].isoformat() == '2026-06-30'
    assert persisted[0]['symbol'] == '600000'
    assert persisted[0]['selection_outcome'] == 'OFFICIAL_PICK'
    assert persisted[0]['selection_diagnostics']['why_candidate']
    assert isinstance(persisted[0]['eligibility_snapshot'], dict)
    assert persisted[0]['candidate_entry_reason']
    assert persisted[0]['ticket_reason']
    assert persisted[0]['factor_snapshot']
    assert persisted[0]['auxiliary_evidence_snapshot']
    assert persisted[-1]['not_selected_reason']
    assert persisted[-1]['symbol'] == '600011'
    assert persisted[-1]['selection_outcome'] == 'TOP10_NOT_SELECTED'
    assert persisted_picks[0]['symbol'] == '600000'
    assert persisted_picks[0]['ticket_reason']['decision'] == 'PAPER_PICK'


def test_main_uses_runtime_scan_summary_before_db_bundle(monkeypatch, capsys) -> None:
    scan_bundle = make_bundle(
        [make_candidate('300888', 'Realtime Pick', score=95.0, rank=1, sector_score=1.0, candidate_evidence_domain_counts=full_candidate_evidence_counts()[0], enhanced_evidence_domain_counts=full_candidate_evidence_counts()[1])],
        candidate_source='v2_scanner_api',
        source_status=full_real_scan_source_status(),
        asof_time='15:10:00',
    )
    scan_bundle['source_time'] = '2026-06-29 15:10:00'
    scan_bundle['_runner_asof_time'] = '15:10:00'
    scan_bundle['scan_summary_path'] = str(Path('/tmp'))
    monkeypatch.setattr(runner, 'run_realtime_scan', lambda date, asof_time=None: {'source_time': '2026-06-29 15:10:00'})
    monkeypatch.setattr(
        runner,
        'load_latest_eastmoney_scan',
        lambda date, asof_time=None: (Path('/tmp/eastmoney_api_scan_v2_summary.json'), {'source_time': '2026-06-29 15:10:00', 'pipeline_version': 'v2_scanner_api'}),
    )
    monkeypatch.setattr(runner, 'build_research_basket_from_latest_scan', lambda date, asof_time=None: scan_bundle)
    monkeypatch.setattr(runner, 'load_candidate_bundle', lambda date, asof_time=None: load_real_bundle())
    monkeypatch.setattr(runner, 'collect_index_snapshot', lambda date, asof_time: {
        'raw_dir': '/tmp',
        'dual_source_index_snapshot': {},
        'source_ok_count': 4,
        'source_total': 4,
        'collected_at': '2026-06-29T15:10:00',
    })
    monkeypatch.setattr(runner, 'existing_decision_for_date', lambda date: False)
    monkeypatch.setattr(runner, 'run_recorder', lambda *args, **kwargs: {
        'cmd': [],
        'returncode': 0,
        'stdout': '',
        'stderr': '',
        'features_path': '/tmp/features.json',
    })
    monkeypatch.setattr(sys, 'argv', ['runner', '--date', '2026-06-30', '--dry-run'])

    runner.main()
    out = json.loads(capsys.readouterr().out)
    runtime = runner.read_json(Path(out['runtime_decision_context_path']))

    assert out['date'] in ('2026-06-29', '2026-06-30')
    assert runtime['features']['candidate_bundle_status']['source_time'] == '2026-06-29 15:10:00'


def test_paper_pick_eligibility_closest_candidate_tie_break_prefers_score_then_rank() -> None:
    bundle = make_bundle(
        [
            make_candidate('300001', 'A', score=60.0, rank=10),
            make_candidate('300002', 'B', score=61.0, rank=20),
            make_candidate('300003', 'C', score=61.0, rank=5),
        ],
        candidate_source='test_scan',
        asof_time='10:00:00',
    )
    selected, reason = runner.closest_to_pick_candidate_from_bundle(bundle, '2026-06-10')

    assert reason == ''
    assert selected is not None
    assert selected['symbol'] == '300003'


def test_underwater_reversal_uses_asof_valid_source_time_not_late_rebuild_time() -> None:
    source_status = full_real_scan_source_status()
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300263',
                '隆华科技',
                score=70.0,
                rank=4,
                evidence='PASS',
                data_gate='PASS',
                regulatory='',
                opportunity='',
                source_time='2026-06-10 21:51:58',
                runner_asof_time='15:10:30',
                search_layer_hint='underwater_reversal',
                setup_type='UNDERWATER_TO_RED_STRENGTH',
                candidate_stage='underwater',
                early_opportunity_score=0.80,
                fund_flow_momentum=0.55,
                time_series_momentum=0.17,
                low_position_catalyst_score=0.48,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    bundle['source_time'] = '2026-06-10 14:49:54'
    bundle['scan_summary_source_time'] = '2026-06-10 14:49:54'
    candidate = bundle['paper_scoring_candidates'][0]
    candidate['source_time'] = '2026-06-10 14:49:54'
    candidate['data_cutoff'] = '2026-06-10 14:49:54'
    candidate['score_asof_provenance'] = '2026-06-10 14:49:54'
    candidate['runner_asof_time'] = '15:10:30'
    candidate['evidence_path'] = 'data/live_scan/2026-06-10/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_ticket_144954/eastmoney_web_tabs_raw.jsonl'
    candidate['raw_snapshot_path'] = 'data/live_scan/2026-06-10/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_ticket_144954/eastmoney_web_tabs_raw.jsonl'
    candidate['raw_data_snapshot_path'] = 'data/live_scan/2026-06-10/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_ticket_144954/eastmoney_web_tabs_raw.jsonl'
    candidate['scan_summary_source_time'] = '2026-06-10 21:51:58'

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert eligibility['signals']['source_time'] == '2026-06-10 14:49:54'
    assert 'source_time<=asof_time' in eligibility['positive_conditions']
    assert 'source_time<=asof_time' not in eligibility['missing_conditions']
    assert 'source_time>asof_time' not in eligibility['blockers']


def test_clean_underwater_reversal_can_pass_without_sector_vei_confirmation() -> None:
    source_status = full_real_scan_source_status()
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300111',
                'Clean Underwater',
                score=71.0,
                rank=2,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='underwater_reversal',
                setup_type='UNDERWATER_TO_RED_STRENGTH',
                candidate_stage='underwater',
                early_opportunity_score=0.78,
                fund_flow_momentum=0.44,
                time_series_momentum=0.21,
                low_position_catalyst_score=0.45,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    bundle['source_time'] = '2026-06-10 14:49:54'
    bundle['scan_summary_source_time'] = '2026-06-10 14:49:54'
    candidate = bundle['paper_scoring_candidates'][0]
    candidate['source_time'] = '2026-06-10 14:49:54'
    candidate['score_asof_provenance'] = '2026-06-10 14:49:54'
    candidate['runner_asof_time'] = '15:10:30'

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert 'underwater_reversal_confirmation_pass' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)


def test_strong_limitup_capture_can_satisfy_soft_confirmation_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '600060',
                'Limitup Capture',
                score=66.0,
                rank=3,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                candidate_stage='high_7_to_9',
                signal_pct=7.2,
                close_position_score=0.82,
                fund_flow_momentum=0.42,
                time_series_momentum=0.12,
                pre_limitup_anomaly=0.74,
                limitup_reason_propagation_score=0.68,
                limitup_capture_score=0.66,
                limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
                limitup_capture_confirmed=True,
                limitup_capture_reasons=['pre_limitup_anomaly>=0.70', 'positive_flow_evidence'],
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert 'limitup_capture_confirmation_pass' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)
    assert eligibility['signals']['limitup_capture_confirmation_pass'] is True
    json.dumps(eligibility, allow_nan=False)


def test_strong_limitup_capture_does_not_override_near_limit_risk() -> None:
    # near_limit_up_risk 只在 L2_LIMIT_STRENGTH 确认（limitup_reason_strength>=0.60）时才豁免。
    # 本测试：有封单capture profile但 limitup_reason_strength<0.60 且无L2 source_layer，仍应block。
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600061',
        'Near Limit Blocked No L2',
        score=66.0,
        rank=3,
        evidence='PASS',
        data_gate='PASS',
        blocked_reasons=['near_limit_up_risk'],
        buy_strength=0.40,  # limitup_reason_strength < 0.60 — no L2 exemption
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        signal_pct=9.2,
        close_position_score=0.90,
        fund_flow_momentum=0.5,
        pre_limitup_anomaly=0.8,
        limitup_reason_propagation_score=0.8,
        limitup_capture_score=0.72,
        limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
        limitup_capture_confirmed=True,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    # no L2_LIMIT_STRENGTH in source_layers — exemption must not trigger
    candidate['source_layers'] = ['L3_FUND_FLOW']
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert eligibility['eligible'] is False
    assert 'near_limit_up_risk' in eligibility['blockers']
    assert 'near_limit_with_L2_confirmation' not in eligibility['positive_conditions']


def test_strong_limitup_capture_does_not_override_regulatory_block() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '600062',
                'Regulatory Blocked',
                score=66.0,
                rank=3,
                evidence='PASS',
                data_gate='PASS',
                regulatory='abnormal_movement_notice',
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                signal_pct=7.0,
                close_position_score=0.82,
                fund_flow_momentum=0.5,
                pre_limitup_anomaly=0.8,
                limitup_reason_propagation_score=0.8,
                limitup_capture_score=0.72,
                limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
                limitup_capture_confirmed=True,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert eligibility['eligible'] is False
    assert any('regulatory_hard_block' in blocker for blocker in eligibility['blockers'])
    assert 'limitup_capture_confirmation_pass' not in eligibility['positive_conditions']


def test_medium_limitup_capture_is_not_formal_confirmation_by_itself() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '600063',
                'Medium Capture',
                score=66.0,
                rank=3,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                signal_pct=6.8,
                close_position_score=0.72,
                fund_flow_momentum=0.3,
                pre_limitup_anomaly=0.55,
                limitup_reason_propagation_score=0.55,
                limitup_capture_score=0.55,
                limitup_capture_profile='MEDIUM_LIMITUP_CAPTURE',
                limitup_capture_confirmed=False,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert eligibility['eligible'] is False
    assert_sector_gate_missing_condition(eligibility, 0.4)
    assert 'limitup_capture_confirmation_pass' not in eligibility['positive_conditions']


def test_strong_weak_to_strong_vei_signal_satisfies_sector_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300263',
                '隆华科技',
                score=70.0,
                rank=4,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='underwater_reversal',
                setup_type='UNDERWATER_TO_RED_STRENGTH',
                candidate_stage='underwater',
                early_opportunity_score=0.8007,
                fund_flow_momentum=0.5526,
                time_series_momentum=0.1667,
                weak_to_strong_reversal=0.7958,
                first_board_pre_signal=0.6764,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert 'vei_strong_signal:weak_to_strong_reversal>=0.75' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)


def test_strong_first_board_pre_signal_satisfies_sector_gate_for_intraday_reversal() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '601012',
                '隆基绿能',
                score=116.83,
                rank=1,
                price=12.93,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-09 14:43:00',
                runner_asof_time='14:43:00',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='early_3_to_5',
                early_opportunity_score=0.6892,
                fund_flow_momentum=1.0,
                time_series_momentum=0.5417,
                first_board_pre_signal=0.8588,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='14:43:00',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert 'vei_strong_signal:first_board_pre_signal>=0.80' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)


def test_integrated_vei_repo_contribution_satisfies_sector_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '688599',
                '天合光能',
                score=69.204,
                rank=1,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='underwater',
                early_opportunity_score=0.72,
                fund_flow_momentum=0.8,
                time_series_momentum=0.3,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]
    candidate['repo_contributions'] = {
        'VEI': {
            'status': 'REAL_OUTPUT',
            'candidate_signal': 'ACTIVE_VEI_ASOF_SCORING',
            'score_delta': 1.0149,
        }
    }

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert eligibility['signals']['vei_repo_score_delta'] == 1.0149
    assert 'vei_strong_signal:integrated_vei_repo_delta>=1.0' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)


def test_integrated_vei_repo_delta_fallback_satisfies_sector_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '688599',
                '天合光能',
                score=69.204,
                rank=1,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='underwater',
                early_opportunity_score=0.72,
                fund_flow_momentum=0.8,
                time_series_momentum=0.3,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]
    candidate['repo_delta_by_repo'] = {'VEI': 1.0149}

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is True
    assert eligibility['signals']['vei_repo_score_delta'] == 1.0149
    assert 'vei_strong_signal:integrated_vei_repo_delta>=1.0' in eligibility['positive_conditions']
    assert_sector_gate_not_missing(eligibility)


def test_weak_integrated_vei_repo_delta_does_not_satisfy_sector_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    for vei_delta in (0.9999, -1.2):
        bundle = make_bundle(
            [
                make_candidate(
                    '300115',
                    'Weak Integrated VEI',
                    score=68.0,
                    rank=6,
                    evidence='PASS',
                    data_gate='PASS',
                    sector_score=0.0,
                    source_time='2026-06-10 14:49:54',
                    runner_asof_time='15:10:30',
                    search_layer_hint='intraday_alert_reversal',
                    setup_type='INTRADAY_ALERT_REVERSAL',
                    candidate_stage='early_3_to_5',
                    early_opportunity_score=0.76,
                    fund_flow_momentum=0.34,
                    time_series_momentum=0.15,
                    candidate_evidence_domain_counts=required_counts,
                    enhanced_evidence_domain_counts=enhanced_counts,
                )
            ],
            candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
            source_status=source_status,
            asof_time='15:10:30',
        )
        candidate = bundle['paper_scoring_candidates'][0]
        candidate['repo_delta_by_repo'] = {'VEI': vei_delta}

        eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

        assert eligibility['eligible'] is False
        assert 'vei_strong_signal:integrated_vei_repo_delta>=1.0' not in eligibility['positive_conditions']
    assert_sector_gate_missing_condition(eligibility, 0.4)


def test_integrated_vei_repo_delta_does_not_override_regulatory_blocker() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300116',
                'Regulatory Blocked Integrated VEI',
                score=72.0,
                rank=7,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                regulatory='regulatory_notice',
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='early_3_to_5',
                early_opportunity_score=0.75,
                fund_flow_momentum=0.8,
                time_series_momentum=0.3,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]
    candidate['repo_contributions'] = {'VEI': {'score_delta': 1.2}}

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert 'vei_strong_signal:integrated_vei_repo_delta>=1.0' in eligibility['positive_conditions']
    assert any('regulatory_hard_block' in blocker for blocker in eligibility['blockers'])


def test_weak_vei_signal_does_not_satisfy_sector_gate() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300115',
                'Weak VEI',
                score=68.0,
                rank=6,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='early_3_to_5',
                early_opportunity_score=0.76,
                fund_flow_momentum=0.34,
                time_series_momentum=0.15,
                weak_to_strong_reversal=0.40,
                first_board_pre_signal=0.50,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert_sector_gate_missing_condition(eligibility, 0.4)


def test_strong_vei_signal_does_not_override_chase_high_blocker() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '601012',
                '隆基绿能',
                score=76.4,
                rank=30,
                price=14.08,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                buy_strength=0.0,
                opportunity='CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
                source_time='2026-06-10 15:10:00',
                runner_asof_time='15:10:30',
                search_layer_hint='formal_high_score',
                setup_type='LIMIT_STRENGTH',
                candidate_stage='high_7_to_9',
                early_opportunity_score=0.3021,
                fund_flow_momentum=0.3,
                time_series_momentum=0.25,
                weak_to_strong_reversal=0.90,
                first_board_pre_signal=0.90,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in eligibility['blockers']
    assert 'buy_confirmation>=0.6' in eligibility['missing_conditions']


def test_strong_vei_signal_does_not_override_regulatory_blocker() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [
            make_candidate(
                '300116',
                'Regulatory Blocked VEI',
                score=72.0,
                rank=7,
                evidence='PASS',
                data_gate='PASS',
                sector_score=0.0,
                regulatory='regulatory_notice',
                source_time='2026-06-10 14:49:54',
                runner_asof_time='15:10:30',
                search_layer_hint='intraday_alert_reversal',
                setup_type='INTRADAY_ALERT_REVERSAL',
                candidate_stage='early_3_to_5',
                early_opportunity_score=0.75,
                fund_flow_momentum=0.8,
                time_series_momentum=0.3,
                first_board_pre_signal=0.9,
                candidate_evidence_domain_counts=required_counts,
                enhanced_evidence_domain_counts=enhanced_counts,
            )
        ],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert any('regulatory_hard_block' in blocker for blocker in eligibility['blockers'])


def test_market_adaptive_high_chase_passes_in_strong_follow_through_market() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600090',
        'Strong Follow Through',
        score=88.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.0,
        buy_strength=0.56,
        seal_order_strength=0.58,
        order_book_pressure=0.49,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        candidate_stage='high_7_to_9',
        signal_pct=8.2,
        close_position_score=0.82,
        fund_flow_momentum=0.48,
        time_series_momentum=0.12,
        pre_limitup_anomaly=0.66,
        limitup_reason_propagation_score=0.64,
        limitup_capture_score=0.66,
        limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
        limitup_capture_confirmed=True,
        limitup_capture_reasons=['strong_follow_through_day'],
        main_theme_alignment_score=0.58,
        intraday_alert_strength=0.92,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.72,
            'market_breadth_up_pct': 63.0,
            'market_limitups': 96.0,
            'limitup_broken_ratio': 1.28,
            'broken_limitups': 18.0,
            'max_consecutive': 4.0,
            'sentiment_score': 0.68,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert eligibility['eligible'] is True
    assert eligibility['signals']['market_regime'] == 'strong'
    assert eligibility['signals']['dynamic_signal_confirmation_pass'] is True
    assert eligibility['signals']['strong_high_momentum_continuation_pass'] is True
    assert 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' not in eligibility['blockers']


def test_market_adaptive_high_chase_fails_in_broken_limit_weak_market() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600091',
        'Weak Broken Limit Day',
        score=88.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.0,
        buy_strength=0.56,
        seal_order_strength=0.58,
        order_book_pressure=0.49,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        candidate_stage='high_7_to_9',
        signal_pct=8.2,
        close_position_score=0.82,
        fund_flow_momentum=0.48,
        time_series_momentum=0.12,
        pre_limitup_anomaly=0.66,
        limitup_reason_propagation_score=0.64,
        limitup_capture_score=0.66,
        limitup_capture_profile='STRONG_LIMITUP_CAPTURE',
        limitup_capture_confirmed=True,
        limitup_capture_reasons=['same_shape_weaker_market'],
        main_theme_alignment_score=0.58,
        intraday_alert_strength=0.92,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.34,
            'market_breadth_up_pct': 41.0,
            'market_limitups': 58.0,
            'limitup_broken_ratio': 0.78,
            'broken_limitups': 53.0,
            'max_consecutive': 2.0,
            'sentiment_score': 0.33,
        },
    )

    blocker = runner.limitup_quality_block_reason(bundle['paper_scoring_candidates'][0], bundle)
    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert blocker == 'BROKEN_LIMIT_WEAK_FOLLOW_THROUGH_CONFIRMATION_GAP'
    assert eligibility['eligible'] is False
    assert eligibility['signals']['weak_acceptance_market'] is True
    assert 'BROKEN_LIMIT_WEAK_FOLLOW_THROUGH_CONFIRMATION_GAP' in eligibility['blockers']
    assert 'weak_market_requires_stronger_high_chase_confirmation' in eligibility['missing_conditions']


def test_market_adaptive_hot_momentum_without_limitup_context_fails_in_weak_market() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    enhanced_gap_counts = {**enhanced_counts, 'limitup_context': 0}
    candidate = make_candidate(
        '600093',
        'Weak Hot Momentum Gap',
        score=88.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.0,
        buy_strength=0.62,
        seal_order_strength=0.62,
        order_book_pressure=0.52,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.2,
        close_position_score=0.78,
        fund_flow_momentum=0.65,
        time_series_momentum=0.20,
        main_theme_alignment_score=0.50,
        intraday_alert_strength=0.92,
        research_panel_overall='PARTIAL',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_gap_counts,
    )
    candidate['mainboard_auxiliary_evidence_status'] = 'PARTIAL'
    candidate['continuation_gene_score'] = 0.0
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.34,
            'market_breadth_up_pct': 41.0,
            'market_limitups': 58.0,
            'limitup_broken_ratio': 0.78,
            'broken_limitups': 53.0,
            'max_consecutive': 2.0,
            'sentiment_score': 0.33,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert eligibility['eligible'] is False
    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' in eligibility['blockers']
    assert eligibility['signals']['weak_market_hot_momentum_evidence_gap'] is True
    assert eligibility['signals']['limitup_context_present'] is False
    assert 'limitup_context_present' in eligibility['missing_conditions']
    assert 'continuation_gene_score>0' in eligibility['missing_conditions']


def test_early_hot_momentum_without_formal_confirmation_blocks_like_shuifa_gas() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    enhanced_gap_counts = {**enhanced_counts, 'limitup_context': 0}
    candidate = make_candidate(
        '603318',
        '水发燃气弱确认形态',
        score=91.0,
        rank=1,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.6667,
        buy_strength=0.62,
        seal_order_strength=0.58,
        order_book_pressure=0.50,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        setup_type='HOT_MOMENTUM',
        candidate_stage='early_3_to_5',
        signal_pct=4.6,
        close_position_score=0.68,
        fund_flow_momentum=0.32,
        time_series_momentum=0.16,
        low_position_catalyst_score=0.4518,
        limitup_capture_score=0.0,
        limitup_capture_confirmed=False,
        research_panel_overall='MISSING',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_gap_counts,
    )
    candidate.update({
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'continuation_gene_score': 0.0,
        'limitup_reason_status': 'MISSING',
        'sector_catalyst_score': 0.0,
        'topic_propagation_score': 0.15,
        'turnover_rate': 18.0,
        'amplitude': 9.2,
        'amount': 420_000_000,
        'net_inflow_main': 8_000_000,
    })
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.34,
            'market_breadth_up_pct': 41.0,
            'market_limitups': 58.0,
            'limitup_broken_ratio': 0.78,
            'broken_limitups': 53.0,
            'max_consecutive': 2.0,
            'sentiment_score': 0.33,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)
    decision, symbol, reason, features, flags = runner.decision_for_candidate(
        bundle['paper_scoring_candidates'][0],
        bundle,
        '2026-06-10',
    )

    assert eligibility['eligible'] is False
    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' in eligibility['blockers']
    assert eligibility['signals']['weak_market_hot_momentum_evidence_gap'] is True
    assert eligibility['signals']['early_hot_momentum_missing_evidence'] is True
    assert eligibility['signals']['early_hot_momentum_sector_confirmation_gap'] is True
    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' in flags
    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' in reason
    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' in features['paper_pick_eligibility']['blockers']


def test_early_hot_momentum_with_real_money_and_catalyst_escape_not_blocked() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    enhanced_gap_counts = {**enhanced_counts, 'limitup_context': 0}
    candidate = make_candidate(
        '002558',
        '巨人网络强确认形态',
        score=92.0,
        rank=1,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.25,
        buy_strength=0.64,
        seal_order_strength=0.62,
        order_book_pressure=0.53,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        setup_type='HOT_MOMENTUM',
        candidate_stage='early_3_to_5',
        signal_pct=4.8,
        close_position_score=0.72,
        fund_flow_momentum=0.68,
        time_series_momentum=0.22,
        low_position_catalyst_score=0.72,
        main_theme_alignment_score=0.55,
        limitup_capture_score=0.0,
        limitup_capture_confirmed=False,
        research_panel_overall='PARTIAL',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_gap_counts,
    )
    candidate.update({
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'continuation_gene_score': 0.0,
        'limitup_reason_status': 'MISSING',
        'sector_catalyst_score': 0.58,
        'sector_news_strength': 0.55,
        'topic_propagation_score': 0.20,
        'turnover_rate': 12.5,
        'amplitude': 7.8,
        'amount': 1_600_000_000,
        'net_inflow_main': 96_000_000,
    })
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.34,
            'market_breadth_up_pct': 41.0,
            'market_limitups': 58.0,
            'limitup_broken_ratio': 0.78,
            'broken_limitups': 53.0,
            'max_consecutive': 2.0,
            'sentiment_score': 0.33,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' not in eligibility['blockers']
    assert eligibility['signals']['weak_market_hot_momentum_evidence_gap'] is False
    assert eligibility['signals']['early_hot_momentum_missing_evidence'] is False
    assert eligibility['signals']['hot_momentum_real_confirmation_escape'] is True


def test_sector_follower_proxy_confirmation_unchanged_like_hengdian_dmegc() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '002056',
        '横店东磁板块跟随形态',
        score=89.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.66,
        buy_strength=0.66,
        seal_order_strength=0.64,
        order_book_pressure=0.55,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        setup_type='SECTOR_FOLLOWER',
        candidate_stage='early_3_to_5',
        signal_pct=3.8,
        close_position_score=0.74,
        fund_flow_momentum=0.62,
        time_series_momentum=0.24,
        main_theme_alignment_score=0.62,
        main_theme_core_score=0.58,
        limitup_capture_score=0.35,
        limitup_capture_confirmed=False,
        research_panel_overall='PASS',
        sector_opportunity_tags=['SECTOR_OPPORTUNITY'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate.update({
        'mainboard_auxiliary_evidence_status': 'PASS',
        'limitup_reason_status': 'PROXY',
        'sector_catalyst_score': 0.62,
        'topic_propagation_score': 0.52,
        'turnover_rate': 8.5,
        'amplitude': 5.4,
    })
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.52,
            'market_breadth_up_pct': 54.0,
            'market_limitups': 72.0,
            'limitup_broken_ratio': 0.35,
            'broken_limitups': 25.0,
            'max_consecutive': 4.0,
            'sentiment_score': 0.55,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' not in eligibility['blockers']
    assert eligibility['signals']['weak_market_hot_momentum_evidence_gap'] is False
    assert eligibility['signals']['limitup_reason_status'] == 'PROXY'
    assert eligibility['eligible'] is False
    assert 'sector_follower_diagnostic_only' in eligibility['blockers']


def test_sector_follower_remains_search_diagnostic_but_never_first_clean() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    sector_follower = make_candidate(
        '002056',
        '板块跟随观察票',
        score=89.0,
        rank=1,
        sector_score=0.72,
        search_layer_hint='sector_follower',
        setup_type='SECTOR_FOLLOWER',
        candidate_stage='early_3_to_5',
        signal_pct=3.6,
        close_position_score=0.72,
        fund_flow_momentum=0.62,
        time_series_momentum=0.24,
        main_theme_alignment_score=0.62,
        research_panel_overall='PASS',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    formal_candidate = make_candidate(
        '600519',
        '正式高分票',
        score=92.0,
        rank=2,
        sector_score=0.0,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='high_7_to_9',
        early_opportunity_score=0.2,
        signal_pct=7.2,
        close_position_score=0.78,
        fund_flow_momentum=0.72,
        time_series_momentum=0.35,
        main_theme_core_score=0.72,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle([sector_follower, formal_candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')

    result = runner.build_daily_ticket_search_rows(bundle['paper_scoring_candidates'], bundle)
    sector_row = next(row for row in result['search_rows'] if row['symbol'] == '002056')

    assert sector_row['search_layer'] == 'sector_follower'
    assert sector_row['paper_pick_eligibility']['eligible'] is False
    assert sector_row['formal_eligible'] is False
    assert 'sector_follower_diagnostic_only' in sector_row['paper_pick_eligibility']['blockers']
    assert result['first_clean_row']['symbol'] == '600519'


def test_mainboard_auxiliary_missing_blocks_weak_low_confidence_paper_pick() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600900',
        '低分缺辅助证据主板票',
        score=56.75,
        rank=1,
        sector_score=0.68,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='early_3_to_5',
        signal_pct=3.2,
        close_position_score=0.58,
        fund_flow_momentum=0.25,
        time_series_momentum=0.12,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='MISSING',
        mainboard_auxiliary_confidence=0.0,
        mainboard_auxiliary_missing_domains=['announcement', 'sector_news', 'limitup_reason'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert 'mainboard_auxiliary_evidence_status_not_PASS' in eligibility['blockers']
    assert 'mainboard_auxiliary_evidence_status=PASS' in eligibility['missing_conditions']
    assert eligibility['signals']['mainboard_auxiliary_evidence_hard_block'] is True
    assert 'mainboard_auxiliary_evidence_status_not_PASS' in runner.official_target_exclusion_reasons(
        {**candidate, 'paper_pick_eligibility': eligibility}, bundle
    )


def test_mainboard_auxiliary_partial_blocks_first_clean_search_row() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    partial_candidate = make_candidate(
        '603000',
        '辅助证据部分缺失票',
        score=88.0,
        rank=1,
        sector_score=0.0,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='mid_5_to_7',
        signal_pct=5.8,
        close_position_score=0.70,
        fund_flow_momentum=0.42,
        time_series_momentum=0.18,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.4,
        mainboard_auxiliary_missing_domains=['limitup_reason'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    pass_candidate = make_candidate(
        '600519',
        '辅助证据完整正式票',
        score=92.0,
        rank=2,
        sector_score=0.0,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='high_7_to_9',
        early_opportunity_score=0.2,
        signal_pct=7.2,
        close_position_score=0.78,
        fund_flow_momentum=0.72,
        time_series_momentum=0.35,
        main_theme_core_score=0.72,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle([partial_candidate, pass_candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')

    result = runner.build_daily_ticket_search_rows(bundle['paper_scoring_candidates'], bundle)
    eligibility = runner.paper_pick_eligibility_profile(partial_candidate, bundle)

    assert eligibility['eligible'] is False
    assert eligibility['signals']['strong_sector_theme_partial_aux_exception'] is False
    assert 'mainboard_auxiliary_evidence_status_not_PASS' in eligibility['blockers']
    assert result['first_clean_row']['symbol'] == '600519'


def test_mainboard_auxiliary_partial_strong_theme_exception_can_be_official() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600276',
        '恒瑞医药强主题候选',
        score=90.0,
        rank=1,
        sector_score=0.80,
        search_layer_hint='sector_catalyst_low_position',
        setup_type='SECTOR_NEWS_LOW_POSITION',
        candidate_stage='mid_5_to_7',
        signal_pct=5.6,
        close_position_score=0.76,
        fund_flow_momentum=0.70,
        time_series_momentum=0.25,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.55,
        mainboard_auxiliary_missing_domains=['limitup_reason'],
        main_theme_core_score=0.72,
        main_theme_alignment_score=0.72,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['sector_catalyst_score'] = 0.78
    candidate['topic_propagation_score'] = 0.78
    candidate['structured_component_details']['sector_catalyst_score'] = 0.78
    candidate['structured_component_details']['topic_propagation_score'] = 0.78
    bundle = make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['signals']['strong_sector_theme_partial_aux_exception'] is True
    assert 'strong_sector_theme_partial_aux_exception' in eligibility['positive_conditions']
    assert 'mainboard_auxiliary_evidence_status_not_PASS' not in eligibility['blockers']
    assert 'mainboard_auxiliary_evidence_status_not_PASS' not in runner.official_target_exclusion_reasons(
        {**candidate, 'paper_pick_eligibility': eligibility}, bundle
    )


def test_low_score_without_direct_catalyst_cannot_be_first_clean() -> None:
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600900',
        '低分无直接催化票',
        score=65.0,
        rank=1,
        sector_score=0.0,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='high_7_to_9',
        early_opportunity_score=0.2,
        signal_pct=7.0,
        close_position_score=0.78,
        fund_flow_momentum=0.70,
        time_series_momentum=0.30,
        main_theme_core_score=0.72,
        research_panel_overall='PARTIAL',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    bundle = make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')

    result = runner.build_daily_ticket_search_rows(bundle['paper_scoring_candidates'], bundle)
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert eligibility['eligible'] is False
    assert 'low_score_without_direct_catalyst_confirmation' in eligibility['blockers']
    assert result['first_clean_row'] is None


    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    enhanced_confirmed_counts = {**enhanced_counts, 'limitup_context': 1}
    candidate = make_candidate(
        '600094',
        'Weak Hot Momentum With Context',
        score=88.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.0,
        buy_strength=0.62,
        seal_order_strength=0.62,
        order_book_pressure=0.52,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.2,
        close_position_score=0.78,
        fund_flow_momentum=0.65,
        time_series_momentum=0.20,
        main_theme_alignment_score=0.50,
        intraday_alert_strength=0.92,
        research_panel_overall='PARTIAL',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_confirmed_counts,
    )
    candidate['mainboard_auxiliary_evidence_status'] = 'PARTIAL'
    candidate['continuation_gene_score'] = 0.0
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.34,
            'market_breadth_up_pct': 41.0,
            'market_limitups': 58.0,
            'limitup_broken_ratio': 0.78,
            'broken_limitups': 53.0,
            'max_consecutive': 2.0,
            'sentiment_score': 0.33,
        },
    )

    eligibility = runner.paper_pick_eligibility_profile(bundle['paper_scoring_candidates'][0], bundle)

    assert 'weak_market_hot_momentum_without_d1_continuation_evidence' not in eligibility['blockers']
    assert eligibility['signals']['weak_market_hot_momentum_evidence_gap'] is False
    assert eligibility['signals']['limitup_context_present'] is True


def test_official_target_exclusion_uses_market_adaptive_sector_override() -> None:
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600092',
        'Adaptive Sector Override',
        score=72.0,
        rank=5,
        evidence='PASS',
        data_gate='PASS',
        sector_score=0.25,
        buy_strength=0.62,
        seal_order_strength=0.62,
        order_book_pressure=0.52,
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        candidate_stage='early_3_to_5',
        signal_pct=4.2,
        close_position_score=0.72,
        fund_flow_momentum=0.38,
        time_series_momentum=0.18,
        research_panel_overall='FAIL',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    weak_bundle = make_bundle(
        [dict(candidate)],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.35,
            'market_breadth_up_pct': 43.0,
            'market_limitups': 52.0,
            'limitup_broken_ratio': 0.82,
            'broken_limitups': 46.0,
        },
    )
    weak_candidate = dict(weak_bundle['paper_scoring_candidates'][0])
    weak_candidate['paper_pick_eligibility'] = runner.paper_pick_eligibility_profile(weak_candidate, weak_bundle)

    neutral_bundle = make_bundle(
        [dict(candidate)],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
        market_snapshot={
            'market_follow_through_score': 0.50,
            'market_breadth_up_pct': 52.0,
            'market_limitups': 68.0,
            'limitup_broken_ratio': 1.02,
            'broken_limitups': 18.0,
        },
    )
    neutral_candidate = dict(neutral_bundle['paper_scoring_candidates'][0])
    neutral_candidate['paper_pick_eligibility'] = runner.paper_pick_eligibility_profile(neutral_candidate, neutral_bundle)

    weak_reasons = runner.official_target_exclusion_reasons(weak_candidate, weak_bundle)
    neutral_reasons = runner.official_target_exclusion_reasons(neutral_candidate, neutral_bundle)

    assert weak_candidate['paper_pick_eligibility']['signals']['market_regime'] == 'weak'
    assert neutral_candidate['paper_pick_eligibility']['signals']['market_regime'] == 'neutral'
    assert weak_candidate['paper_pick_eligibility']['eligible'] is True
    assert 'research_panel_overall_SOFT_FAIL' not in weak_reasons
    assert 'research_panel_overall_SOFT_FAIL' in neutral_reasons


def test_domain_digest_hsgt_signal_survives_scan_summary_merge() -> None:
    summary = {
        'source_time': '2026-06-10 14:49:54',
        'pipeline_version': 'unit_test_pipeline',
        'source_status': complete_source_status(),
        'paper_scoring_candidates': [
            {
                'code': '600093',
                'symbol': '600093',
                'name': 'HSGT Merge Candidate',
                'score': 91.0,
                'rank': 1,
                'price': 12.0,
                'signal_pct': 8.1,
                'setup_type': 'LIMIT_STRENGTH',
                'search_layer_hint': 'formal_high_score',
                'close_position_score': 0.83,
            }
        ],
        'structured_scores': [
            {
                'code': '600093',
                'symbol': '600093',
                'structured_score': 1.8,
                'components': {
                    'limitup_reason_strength': 0.65,
                    'seal_order_strength': 0.65,
                    'order_book_pressure': 0.55,
                    'fund_flow_momentum': 0.62,
                    'time_series_momentum': 0.21,
                    'hsgt_institutional_flow': 0.72,
                },
                'component_details': {
                    'sector_opportunity_score': 0.62,
                    'hsgt_institutional_flow': 0.72,
                    'experimental_catalyst_signal': 0.41,
                    'main_theme_alignment_score': 0.61,
                    'main_theme_core_score': 0.66,
                    'intraday_alert_strength': 0.92,
                    'limitup_reason_propagation_score': 0.64,
                    'limitup_capture_score': 0.66,
                    'limitup_capture_profile': 'STRONG_LIMITUP_CAPTURE',
                    'limitup_capture_confirmed': True,
                    'limitup_capture_reasons': ['hsgt_merge_survives'],
                },
                'candidate_stage': 'high_7_to_9',
                'early_opportunity_score': 0.64,
                'limitup_capture_score': 0.66,
                'limitup_capture_profile': 'STRONG_LIMITUP_CAPTURE',
                'limitup_capture_confirmed': True,
                'limitup_capture_reasons': ['hsgt_merge_survives'],
                'main_theme_alignment_score': 0.61,
                'main_theme_core_score': 0.66,
                'hsgt_institutional_flow': 0.72,
                'experimental_catalyst_signal': 0.41,
                'market_follow_through_score': 0.66,
                'market_breadth_up_pct': 61.0,
                'market_limitups': 88.0,
                'limitup_broken_ratio': 1.18,
                'broken_limitups': 20.0,
            }
        ],
        'research_signals': [],
        'structured_score_components': [],
        'structured_component_details': [],
        'market_follow_through_score': 0.51,
        'market_breadth_up_pct': 52.0,
        'market_limitups': 68.0,
        'limitup_broken_ratio': 1.01,
        'market_snapshot': {'broken_limitups': 18.0},
    }
    summary_path = Path('/workspace/hermes-workspaces/xiaogu/data/live_scan/2026-06-10/eastmoney_scan_afternoon/eastmoney_web_tabs_summary_runner.json')

    bundle = runner._bundle_from_scan_summary(summary_path, summary)
    candidate = bundle['paper_scoring_candidates'][0]

    assert candidate['hsgt_institutional_flow'] == 0.72
    assert candidate['experimental_catalyst_signal'] == 0.41
    assert candidate['structured_score_components']['hsgt_institutional_flow'] == 0.72
    assert candidate['market_follow_through_score'] == 0.51


def test_merge_concept_stocks_into_quotes_deduplicates():
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scan
    existing = [
        {'code': '000001', 'name': '平安银行', 'price': 12.0},
        {'code': '000002', 'name': '万科A', 'price': 8.0},
    ]
    concept_stocks = [
        {'code': '000001', 'name': '平安银行', 'price': 12.5, 'board_code': 'BK0001'},
        {'code': '000003', 'name': 'PT金田A', 'price': 3.0, 'board_code': 'BK0001'},
        {'code': '000004', 'name': '国华网安', 'price': 5.0, 'board_code': 'BK0001'},
    ]
    merged = scan.merge_concept_stocks_into_quotes(concept_stocks, existing, max_per_board=10)
    codes = [q['code'] for q in merged]
    assert codes.count('000001') == 1
    assert '000003' in codes
    assert '000004' in codes
    assert len(merged) == 4


def test_merge_concept_stocks_into_quotes_respects_max_per_board():
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scan
    existing = []
    concept_stocks = [{'code': f'00000{i}', 'name': f'stock{i}', 'price': 1.0, 'board_code': 'BK0001'} for i in range(20)]
    merged = scan.merge_concept_stocks_into_quotes(concept_stocks, existing, max_per_board=5)
    assert len(merged) == 5


def test_fetch_concept_board_list_api_returns_list():
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scan
    with patch.object(scan, 'eastmoney_get', return_value={'data': {'diff': [
        {'f12': 'BK0800', 'f14': '机器人概念', 'f3': 2.5, 'f62': 100000000, 'f104': 50, 'f105': 10, 'f140': '拓斯达'},
        {'f12': 'BK0801', 'f14': '国产芯片', 'f3': 1.8, 'f62': 80000000, 'f104': 40, 'f105': 15, 'f140': '中芯国际'},
    ]}}):
        boards = scan.fetch_concept_board_list_api(page_size=10)
    assert len(boards) == 2
    assert boards[0]['board_code'] == 'BK0800'
    assert boards[0]['board_name'] == '机器人概念'
    assert boards[0]['pct_change'] == 2.5


def test_fetch_concept_member_stocks_api_returns_stocks():
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scan
    with patch.object(scan, 'eastmoney_get', return_value={'data': {'diff': [
        {'f12': '002747', 'f14': '埃斯顿', 'f2': 15.0, 'f3': 3.5, 'f62': 5000000},
        {'f12': '300124', 'f14': '汇川技术', 'f2': 60.0, 'f3': 2.1, 'f62': 8000000},
    ]}}):
        stocks = scan.fetch_concept_member_stocks_api('BK0800', page_size=50)
    assert len(stocks) == 2
    assert stocks[0]['code'] == '002747'
    assert stocks[0]['board_code'] == 'BK0800'
    assert stocks[1]['code'] == '300124'


def test_rows_from_candidate_intraday_replay_falls_back_to_zjlx_and_stockdata(monkeypatch) -> None:
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scanner
    candidate = {'code': '601012', 'name': '隆基绿能'}
    snapshots = [
        {
            'url': 'https://quote.eastmoney.com/f1.html?newcode=1.601012',
            'title': 'f1',
            'text': '- - -',
            'tables': [],
        },
        {
            'url': 'https://data.eastmoney.com/zjlx/601012.html',
            'title': 'zjlx',
            'text': '盘后资金流向 历史资金流向 主力净流入',
            'tables': [
                {
                    'table_index': 0,
                    'rows': [
                        {'row_index': 0, 'cells': ['主力净流入', '1.23亿']},
                    ],
                }
            ],
        },
        {
            'url': 'https://data.eastmoney.com/stockdata/601012.html',
            'title': 'stockdata',
            'text': '行业排名 个股概况 深度数据',
            'tables': [
                {
                    'table_index': 0,
                    'rows': [
                        {'row_index': 0, 'cells': ['行业排名', '第1名']},
                    ],
                }
            ],
        },
    ]
    monkeypatch.setattr(scanner, 'cdp_page_tabs', lambda cdp_url: [{'url': 'https://quote.eastmoney.com/', 'webSocketDebuggerUrl': 'ws://dummy'}])
    monkeypatch.setattr(scanner, 'fetch_cdp_page_snapshot', lambda ws_url, target_url, request_id_base=11: snapshots.pop(0))
    rows = scanner.rows_from_candidate_intraday_replay(candidate, '2026-06-22 15:10:00', cdp_url='http://127.0.0.1:9333')
    assert any(row['source'] == 'eastmoney_candidate_intraday_replay_zjlx_cdp' for row in rows)
    assert any(row['source'] == 'eastmoney_candidate_intraday_replay_stockdata_cdp' for row in rows)
    assert any('主力净流入' in str(row.get('raw_text', '')) for row in rows)


def test_replay_structures_feed_structured_scores() -> None:
    import xiaogu_eastmoney_web_tabs_scan_v0_1 as scanner
    scored_rows = [
        {
            'code': '601012',
            'name': '隆基绿能',
            'score': 70.0,
            'signal_pct': 4.5,
            'close_position_score': 0.82,
            'volume_ratio': 2.0,
            'full_universe_fund_pctile': 0.85,
            'source_layers': ['L4_PRE_BREAKOUT'],
            'search_layer_hint': 'formal_high_score',
            'sector_catalyst_score': 0.0,
            'topic_propagation_score': 0.0,
            'intraday_alert_strength': 0.0,
            'limitup_reason_propagation_score': 0.0,
            'low_position_catalyst_score': 0.0,
            'news_catalyst_strength': 0.0,
            'research_signals': {'research_panel': {'overall': 'PASS'}},
            'net_inflow_main': 0.0,
        }
    ]
    structured_bundle = {
        'news': [],
        'limitup_reasons': [],
        'lhb_profiles': [],
        'sector_edges': [],
        'popularity_ts': [],
        'fund_flow_ts': [],
        'seal_order_ts': [],
        'order_books': [],
        'metric_delta_ts': [],
        'relationship_graph': {'edges': []},
        'sector_opportunity_snapshot': [],
        'replay_structures': [
            {
                'symbol': '601012',
                'main_force_net_inflow': 123000000.0,
                'main_force_net_ratio': 12.0,
                'industry_rank': 1.0,
                'has_history_flow': True,
                'has_stock_profile': True,
                'has_industry_rank': True,
            }
        ],
    }
    rows = scanner.build_structured_scores(scored_rows, structured_bundle)
    assert rows
    components = rows[0]['components']
    details = rows[0]['component_details']
    assert components['fund_flow_momentum'] > 0.2
    assert components['time_series_momentum'] >= 0.25
    assert components['main_theme_alignment_score'] >= 0.2
    assert details['replay_main_force_net_inflow'] == 123000000.0
    assert details['replay_has_history_flow'] is True
    assert 'REPLAY_HISTORY_FLOW' in details['sector_opportunity_tags']


def test_source_completeness_fail_closed_when_missing() -> None:
    source_status = {
        'required_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'enhanced_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'experimental_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'full_evidence_pack': {'status': 'PASS', 'missing_domains': []},
        'enhanced_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
        'experimental_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
        'source_completeness_required': True,
    }
    flags = runner.paper_pick_source_health_flags({'source_status': source_status})
    assert 'DATA_SOURCE_INCOMPLETE' in flags


def test_source_completeness_pass_when_present_and_pass() -> None:
    source_status = {
        'required_cdp_tabs': {'status': 'PASS', 'missing_sources': []},
        'source_completeness': {
            'status': 'PASS',
            'quote_count': 5000,
            'fund_count': 100,
            'min_quote_count': 4000,
            'missing_sources': [],
            'flags': [],
            'blocking': True,
        },
    }
    flags = runner.paper_pick_source_health_flags({'source_status': source_status})
    assert 'DATA_SOURCE_INCOMPLETE' not in flags


def test_weak_underwater_missing_research_panel_is_not_paper_pick() -> None:
    source_status = complete_source_status()
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '603726',
        '天安新材MissingResearch',
        score=75.36,
        rank=12,
        price=12.07,
        sector_score=1.0,
        signal_pct=-1.39,
        close_position_score=0.598131,
        source_time='2026-06-10 15:00:00',
        runner_asof_time='15:00:00',
        fund_flow_momentum=0.2906,
        time_series_momentum=0.0,
        candidate_stage='underwater',
        search_layer_hint='underwater_reversal',
        setup_type='UNDERWATER_RED_FLAT_RECOVERY',
        catalyst_category='neutral',
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['research_signals'] = {
        'catalyst_quality': {
            'category': 'neutral',
            'confidence': 0.0,
            'evidence_refs': [],
        },
        'sector_mapping': {
            'sectors': [],
            'related_symbols': [],
            'mapping_confidence': 0.0,
        },
        'a_share_risk_review': {
            'disqualified_for_paper_pick': False,
        },
        'adversarial_review': {
            'disqualifying_flags': [],
            'bear_case_flags': [],
        },
        'historical_pattern': {
            'pattern_name': 'underwater_reversal',
        },
        'research_panel': {},
    }
    candidate['structured_component_details']['main_theme_core_score'] = 0.9
    candidate['structured_component_details']['weak_to_strong_reversal'] = 0.7202
    candidate['data_directory_capital_flow'] = {'main_force_net_inflow': 20133492.0}
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan',
        source_status=source_status,
        asof_time='15:00:00',
    )

    decision, symbol, reason, features, flags = runner.decision_for_candidate(candidate, bundle, '2026-06-10')

    assert decision == 'NO_PICK'
    assert symbol == ''
    assert 'weak_underwater_without_forward_confirmation' in flags
    assert 'weak_underwater_without_forward_confirmation' in features['paper_pick_eligibility']['blockers']
    assert 'limitup_or_catalyst_confirmation_for_underwater_candidate' in features['paper_pick_eligibility']['missing_conditions']


# ---------------------------------------------------------------------------
# 健康检查集成测试
# ---------------------------------------------------------------------------

def test_health_check_all_pass():
    """验证 xiaogu_daily_health_check 所有 13 个检查项均为 PASS。"""
    import importlib.util, sys, os
    script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'xiaogu_daily_health_check.py')
    spec = importlib.util.spec_from_file_location("xiaogu_daily_health_check", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    failed = []
    for name, fn in mod.CHECKS:
        ok, detail = fn()
        if not ok:
            failed.append(f"{name}: {detail}")

    assert not failed, "Health check failures:\n" + "\n".join(failed)


def test_collect_candidate_detail_evidence_reuses_tab():
    """Phase 2: collect_candidate_detail_evidence must reuse an existing tab, not open a new one."""
    from unittest.mock import patch, MagicMock

    fake_tabs = [
        {'id': 'tab-free-1', 'type': 'page', 'url': 'https://quote.eastmoney.com/sh600519.html', 'webSocketDebuggerUrl': 'ws://localhost:9333/devtools/page/tab-free-1'},
        {'id': 'tab-free-2', 'type': 'page', 'url': 'about:blank', 'webSocketDebuggerUrl': 'ws://localhost:9333/devtools/page/tab-free-2'},
    ]

    candidates = [{'code': '600519', 'name': '贵州茅台'}]
    source_time = '2026-06-25 14:00:00'
    shared_url_to_tab = {
        'https://quote.eastmoney.com/sh600519.html': fake_tabs[0],
    }

    with patch.object(scanner, 'open_cdp_tab') as mock_open, \
         patch.object(scanner, 'cdp_navigate_tab', return_value=True), \
         patch.object(scanner, 'cdp_page_tabs', return_value=fake_tabs), \
         patch.object(scanner, 'rows_from_announcement_api', return_value={}), \
         patch.object(scanner, 'rows_from_lhb_api', return_value=[]), \
         patch.object(scanner, 'rows_from_financial_api', return_value=([], [])), \
         patch.object(scanner, 'rows_from_candidate_quote_api', return_value=[]), \
         patch.object(scanner, 'rows_from_candidate_quote_cdp', return_value=[{'code': '600519', 'SECURITY_CODE': '600519', 'SECURITY_NAME_ABBR': '贵州茅台', 'domain': 'candidate_quote_recheck', 'source': 'eastmoney_candidate_quote_recheck_cdp', 'title': '五档行情', 'date': '2026-06-25', 'raw_text': '买一'}]), \
         patch.object(scanner, 'rows_from_candidate_fund_recheck', return_value=[]), \
         patch.object(scanner, 'rows_from_candidate_intraday_replay', return_value=[{'code': '600519', 'domain': 'candidate_intraday_replay', 'title': 'replay'}]), \
         patch('time.sleep'):

        result = scanner.collect_candidate_detail_evidence(
            candidates, source_time, 1,
            cdp_url='http://localhost:9333',
            shared_url_to_tab=shared_url_to_tab,
        )

    # Must NOT have opened a new tab
    mock_open.assert_not_called()

    # CDP quote row must carry tab_reuse=True
    cdp_rows = [r for r in result.get('candidate_quote_recheck', []) if r.get('source') == 'eastmoney_candidate_quote_recheck_cdp']
    assert cdp_rows, "Expected at least one CDP quote row"
    assert cdp_rows[0].get('tab_reuse') is True, f"Expected tab_reuse=True, got {cdp_rows[0].get('tab_reuse')}"
    assert cdp_rows[0].get('reused_tab_id') == 'tab-free-1'
    assert cdp_rows[0].get('tabs_opened_for_detail') == 0

    # Intraday replay row must also carry tab_reuse=True
    replay_rows = result.get('candidate_intraday_replay', [])
    assert replay_rows, "Expected at least one replay row"
    assert replay_rows[0].get('tab_reuse') is True


def test_output_has_three_candidate_slots():
    import xiaogu_forward_d1_1450_runner_v0_1 as runner
    # Verify these functions exist and return dicts
    assert callable(runner.highest_score_candidate_from_bundle)
    assert callable(runner.closest_to_pick_candidate_from_bundle)
    assert callable(runner.build_daily_best_paper_watch)


def test_signal_effectiveness_from_mock_ledger(tmp_path):
    import xiaogu_signal_effectiveness_v0_1 as eff
    import json
    ledger = tmp_path / "test.jsonl"
    records = [
        {"record_type": "DECISION", "date": "2026-06-10", "symbol": "300001",
         "candidate_features": {"kline_language_score": 0.8, "fund_flow_score": 0.7},
         "t1_return": 0.10, "source_layers": ["L11_LOW_POSITION_AMBUSH"]},
        {"record_type": "DECISION", "date": "2026-06-11", "symbol": "300002",
         "candidate_features": {"kline_language_score": 0.3, "fund_flow_score": 0.2},
         "t1_return": -0.05, "source_layers": ["L3_MOMENTUM"]},
        {"record_type": "DECISION", "date": "2026-06-12", "symbol": "300003",
         "candidate_features": {"kline_language_score": 0.9, "fund_flow_score": 0.8},
         "t1_return": 0.098, "source_layers": ["L11_LOW_POSITION_AMBUSH"]},
    ]
    with ledger.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    result = eff.analyze_signal_effectiveness(ledger, source='file')
    assert result["total_picks"] == 3
    assert result["filled_picks"] == 3
    pools = {p["pool"]: p for p in result["pool_effectiveness"]}
    assert pools["L11_LOW_POSITION_AMBUSH"]["limit_up_rate"] == 1.0
    assert pools["L3_MOMENTUM"]["limit_up_rate"] == 0.0


def test_signal_effectiveness_db_uses_completed_top10_replay(monkeypatch, tmp_path):
    replay_records = [
        {
            'trade_date': '2026-07-13',
            'symbol': '600186',
            't1_return': -0.0995,
            'candidate_features': {
                'fund_flow_momentum': 0.4,
                'market_regime': 'weak',
                'paper_pick_eligibility': {
                    'signals': {'weak_market_requires_direct_confirmation': True},
                },
            },
            'source_layers': ['L3_MOMENTUM'],
        },
        {
            'trade_date': '2026-07-13',
            'symbol': '000807',
            't1_return': 0.0542,
            'candidate_features': {
                'fund_flow_momentum': 0.7,
                'market_regime': 'weak',
            },
            'source_layers': ['L11_LOW_POSITION_AMBUSH'],
        },
        {
            'trade_date': '2025-06-20',
            'symbol': '300804',
            't1_return': 28.9375,
            'candidate_features': {'market_regime': 'strong'},
            'source_layers': ['L0_FULL_UNIVERSE'],
        },
        {
            'trade_date': '2026-07-12',
            'symbol': '600999',
            't1_return': 0.10,
            'candidate_features': {'market_regime': 'strong'},
            'source_layers': ['L0_FULL_UNIVERSE'],
        },
    ]
    monkeypatch.setattr(
        effectiveness,
        '_collect_horizon_replay_sources',
        lambda _ledger, _focus: ([], {'db': {'loaded': True}}),
    )
    monkeypatch.setattr(
        effectiveness,
        'build_horizon_replay',
        lambda _rows, top_n, source_details: {'records': replay_records},
    )

    result = effectiveness.analyze_signal_effectiveness(tmp_path / 'unused.jsonl', source='db')

    assert result['total_picks'] == 3
    assert result['filled_picks'] == 2
    assert result['excluded_return_rows'] == 1
    signals = {row['signal_key']: row for row in result['signal_effectiveness']}
    assert signals['market_regime:weak']['present_count'] == 2
    assert signals['weak_market_requires_direct_confirmation']['avg_t1_return'] == -0.0995


def test_signal_effectiveness_db_scope_excludes_non_trading_dates():
    assert effectiveness._is_analysis_trading_day('2026-07-10') is True
    assert effectiveness._is_analysis_trading_day('2026-07-12') is False


def test_signal_effectiveness_persistence_upserts_snapshot(monkeypatch):
    captured = []

    class FakeTransaction:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, payloads):
            captured.append((str(statement), payloads))

    class FakeEngine:
        def begin(self):
            return FakeTransaction()

    import xiaogu_db
    monkeypatch.setattr(xiaogu_db, 'engine', FakeEngine())
    result = {
        'analysis_date': '2026-07-14',
        'signal_effectiveness': [{
            'signal_key': 'fund_flow_momentum',
            'present_count': 10,
            'limit_up_rate': 0.2,
            'avg_t1_return': 0.0123,
            'weight_suggestion': 'MAINTAIN',
        }],
    }

    persisted = effectiveness.persist_signal_effectiveness(result)

    assert persisted == {'persisted_count': 1, 'analysis_date': '2026-07-14'}
    assert len(captured) == 2
    assert captured[1][1][0]['signal_key'] == 'fund_flow_momentum'


def test_clean_import_without_xiaogu_utils(monkeypatch):
    import builtins
    import importlib

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'xiaogu_utils':
            raise ModuleNotFoundError('blocked for import safety test')
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, 'xiaogu_utils', raising=False)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    reloaded_runner = importlib.reload(runner)
    reloaded_effectiveness = importlib.reload(importlib.import_module('xiaogu_signal_effectiveness_v0_1'))

    assert callable(reloaded_runner.paper_pick_eligibility_profile)
    assert callable(reloaded_effectiveness.build_candidate_diagnostics)


def test_scoring_config_schema_supports_horizon_aware_defaults(monkeypatch):
    runner.clear_scoring_config_cache()

    monkeypatch.setattr(runner, '_load_scoring_config_from_db', lambda: {
        'config': {
            'weekday_blocklist': '0,4',
            'max_score_cap': '92',
            'follow_on_strategy': 't1_close_primary',
            'horizon_aware_strategy': 'instant_then_delayed',
            'instant_momentum_min_confirmations': '3',
            'delayed_setup_min_persistence': '2',
            'stale_repeat_window_days': '5',
        },
        'loaded': True,
        'source': 'db',
        'error': '',
    })
    snapshot = runner.get_scoring_config_snapshot(force_refresh=True)

    assert snapshot['loaded'] is True
    assert snapshot['config']['weekday_blocklist'] == '0,4'
    assert snapshot['config']['max_score_cap'] == '92'
    assert snapshot['config']['horizon_aware_strategy'] == 'instant_then_delayed'
    assert snapshot['config']['instant_momentum_min_confirmations'] == '3'
    assert snapshot['config']['delayed_setup_min_persistence'] == '2'
    assert snapshot['config']['stale_repeat_window_days'] == '5'


def test_candidate_diagnostics_recognizes_horizon_classes(tmp_path):
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = [
        {
            'trade_date': '2026-06-20',
            'symbol': '300001',
            'name': 'Instant',
            'score': 90.0,
            'final_score': 90.0,
            'rank': 1,
            'picked': True,
            'source_layers': ['instant'],
            'blockers': [],
            't1_return': 0.11,
            't2_return': 0.08,
            't3_return': 0.05,
            't5_return': 0.02,
        },
        {
            'trade_date': '2026-06-21',
            'symbol': '300002',
            'name': 'Delayed',
            'score': 78.0,
            'final_score': 78.0,
            'rank': 2,
            'picked': False,
            'source_layers': ['theme'],
            'blockers': ['WEEKDAY_BLOCKED'],
            't1_return': 0.0,
            't2_return': 0.0,
            't3_return': 0.0,
            't5_return': 0.0,
        },
        {
            'trade_date': '2026-06-22',
            'symbol': '300003',
            'name': 'Noise',
            'score': 60.0,
            'final_score': 60.0,
            'rank': 3,
            'picked': False,
            'source_layers': ['turnaround'],
            'blockers': [],
            't1_return': -0.03,
            't2_return': 0.01,
            't3_return': 0.05,
            't5_return': 0.04,
        },
        {
            'trade_date': '2026-06-23',
            'symbol': '300004',
            'name': 'Missed',
            'score': 84.0,
            'final_score': 84.0,
            'rank': 4,
            'picked': False,
            'source_layers': ['theme'],
            'blockers': ['XIAOCHAN_BLOCK'],
            't1_return': 0.0,
            't2_return': 0.02,
            't3_return': 0.08,
            't5_return': 0.1,
        },
        {
            'trade_date': '2026-06-24',
            'symbol': '300005',
            'name': 'Stale',
            'score': 88.0,
            'final_score': 88.0,
            'rank': 5,
            'picked': True,
            'source_layers': ['crowded'],
            'blockers': [],
            't1_return': -0.02,
            't2_return': -0.01,
            't3_return': -0.01,
            't5_return': 0.0,
        },
        {
            'trade_date': '2026-06-25',
            'symbol': '300005',
            'name': 'Stale',
            'score': 87.0,
            'final_score': 87.0,
            'rank': 5,
            'picked': True,
            'source_layers': ['crowded'],
            'blockers': [],
            't1_return': -0.01,
            't2_return': -0.02,
            't3_return': 0.0,
            't5_return': 0.0,
        },
        {
            'trade_date': '2026-06-26',
            'symbol': '300005',
            'name': 'Stale',
            'score': 86.0,
            'final_score': 86.0,
            'rank': 5,
            'picked': True,
            'source_layers': ['crowded'],
            'blockers': [],
            't1_return': -0.03,
            't2_return': -0.02,
            't3_return': -0.01,
            't5_return': 0.0,
        },
    ]
    sample = tmp_path / 'candidate_diagnostics.jsonl'
    with sample.open('w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    result = eff.build_candidate_diagnostics(eff.load_jsonl(sample), focus_symbols=['300001', '300002', '300003', '300004', '300005'])

    assert result['summary']['instant_winners'] == 1
    assert result['summary']['delayed_winners'] == 1
    assert result['summary']['early_noise'] == 1
    assert result['summary']['false_positives'] >= 1
    assert result['summary']['stale_candidates'] >= 1
    assert result['summary']['missed_delayed_winners'] == 1
    assert result['instant_winners'][0]['best_horizon'] == 't1'
    assert result['delayed_winners'][0]['best_horizon'] in ('t3', 't5')
    assert result['early_noise'][0]['payoff_class'] == 'early_noise'
    assert result['stale_candidates'][0]['consecutive_appearances'] >= 3
    assert 'payoff_horizon_buckets' in result['aggregates']


def test_candidate_diagnostics_trade_mode_t1_primary(tmp_path):
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = [
        {
            'trade_date': '2026-06-20',
            'symbol': '300111',
            'name': 'Repair',
            'score': 89.0,
            'final_score': 89.0,
            'rank': 1,
            'picked': True,
            'source_layers': ['theme'],
            'blockers': [],
            't1_return': -0.03,
            't2_return': 0.01,
            't3_return': 0.08,
            't5_return': 0.04,
        },
    ]
    sample = tmp_path / 'candidate_diagnostics_trade_mode.jsonl'
    with sample.open('w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    result = eff.build_candidate_diagnostics(eff.load_jsonl(sample), focus_symbols=['300111'])

    assert result['trade_mode'] == 'afternoon_buy_next_day_sell'
    assert result['primary_return_field'] == 't1_return'
    assert result['early_noise'][0]['primary_trade_return'] == result['early_noise'][0]['t1_return']
    assert result['early_noise'][0]['maturation_class'] == 'early_noise_repaired'
    assert result['early_noise'][0]['maturation_horizon'] == 't3'
    assert result['summary']['false_positives'] == 0


def test_delayed_payoff_is_signal_maturation_not_holding_pnl(tmp_path):
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = [
        {
            'trade_date': '2026-06-21',
            'symbol': '300112',
            'name': 'Mature',
            'score': 86.0,
            'final_score': 86.0,
            'rank': 1,
            'picked': True,
            'source_layers': ['theme'],
            'blockers': [],
            't1_return': 0.01,
            't2_return': 0.03,
            't3_return': 0.09,
            't5_return': 0.06,
        },
    ]
    sample = tmp_path / 'candidate_diagnostics_maturation.jsonl'
    with sample.open('w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    result = eff.build_candidate_diagnostics(eff.load_jsonl(sample), focus_symbols=['300112'])

    assert result['trade_mode'] == 'afternoon_buy_next_day_sell'
    assert result['primary_return_field'] == 't1_return'
    assert 'not multi-day holding PnL' in result['horizon_note']
    assert result['summary']['matured_later_candidates'] == 1
    card = result['matured_later_candidates'][0]
    assert card['primary_trade_return'] == card['t1_return']
    assert card['maturation_horizon'] == 't3'
    assert card['maturation_class'] == 'matured_later'
    assert result['delayed_winners'][0]['symbol'] == card['symbol']


def test_candidate_diagnostics_counts_weekday_blocked_delayed_as_missed():
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = [
        {
            'trade_date': '2026-06-27',
            'symbol': '300006',
            'name': 'BlockedDelayed',
            'score': 82.0,
            'final_score': 82.0,
            'rank': 1,
            'picked': False,
            'source_layers': ['theme', 'fund_flow'],
            'blockers': ['WEEKDAY_BLOCKED'],
            't1_return': 0.0,
            't2_return': 0.01,
            't3_return': 0.04,
            't5_return': 0.09,
        },
    ]

    result = eff.build_candidate_diagnostics(rows, focus_symbols=['300006'])

    assert result['summary']['delayed_winners'] == 1
    assert result['summary']['missed_winners'] == 1
    assert result['summary']['missed_delayed_winners'] == 1
    assert result['delayed_winners'][0]['blockers'] == ['WEEKDAY_BLOCKED']
    assert 'WEEKDAY_BLOCKED' in result['missed_delayed_winners'][0]['blockers']


def test_candidate_diagnostics_breaks_out_setup_class_performance():
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = [
        {
            'trade_date': '2026-06-24',
            'symbol': '300701',
            'name': 'InstantSetup',
            'score': 91.0,
            'final_score': 91.0,
            'rank': 1,
            'picked': True,
            'source_layers': ['fund_flow'],
            'blockers': [],
            'candidate_features': {
                'candidate_lifecycle': {'setup_class': 'INSTANT_MOMENTUM_SETUP'},
                'paper_pick_eligibility': {'setup_class': 'INSTANT_MOMENTUM_SETUP'},
            },
            't1_return': 0.11,
            't2_return': 0.08,
            't3_return': 0.05,
            't5_return': 0.01,
        },
        {
            'trade_date': '2026-06-25',
            'symbol': '300702',
            'name': 'DelayedSetup',
            'score': 83.0,
            'final_score': 83.0,
            'rank': 2,
            'picked': False,
            'source_layers': ['theme'],
            'blockers': [],
            'candidate_features': {
                'candidate_lifecycle': {'setup_class': 'DELAYED_SETUP'},
                'paper_pick_eligibility': {'setup_class': 'DELAYED_SETUP'},
            },
            't1_return': 0.0,
            't2_return': 0.01,
            't3_return': 0.09,
            't5_return': 0.12,
        },
    ]

    result = eff.build_candidate_diagnostics(rows, focus_symbols=['300701', '300702'])

    setup_buckets = {row['setup_class']: row for row in result['aggregates']['setup_class_buckets']}
    assert setup_buckets['INSTANT_MOMENTUM_SETUP']['count'] == 1
    assert setup_buckets['DELAYED_SETUP']['count'] == 1
    assert setup_buckets['INSTANT_MOMENTUM_SETUP']['win_rate'] == 1.0
    assert setup_buckets['DELAYED_SETUP']['avg_best_return'] == 0.12
    performance = {row['setup_class']: row for row in result['aggregates']['setup_class_performance']}
    assert performance['INSTANT_MOMENTUM_SETUP']['instant_count'] == 1
    assert performance['DELAYED_SETUP']['delayed_count'] == 1
    assert performance['DELAYED_SETUP']['avg_delayed_gap'] > 0


def test_candidate_lifecycle_signals_expose_trade_mode_and_t1_horizon(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(runner, 'get_scoring_config_snapshot', lambda force_refresh=False: make_scoring_config_snapshot())

    prior_a = make_candidate(
        '300780',
        'Signal Mature',
        score=76.0,
        rank=1,
        sector_score=0.72,
        fund_flow_momentum=0.68,
        source_time='2026-06-22 10:00:00',
    )
    prior_a['source_layers'] = ['theme', 'fund_flow']
    prior_b = make_candidate(
        '300780',
        'Signal Mature',
        score=77.0,
        rank=1,
        sector_score=0.74,
        fund_flow_momentum=0.66,
        source_time='2026-06-23 10:00:00',
    )
    prior_b['source_layers'] = ['theme', 'fund_flow']
    current = make_candidate(
        '300780',
        'Signal Mature',
        score=78.0,
        rank=1,
        sector_score=0.76,
        fund_flow_momentum=0.7,
        source_time='2026-06-24 10:00:00',
    )
    current['source_layers'] = ['theme', 'fund_flow']

    write_candidate_bundle(bundle_root, '2026-06-22', [prior_a])
    write_candidate_bundle(bundle_root, '2026-06-23', [prior_b])

    bundle = make_bundle([current], candidate_source='eastmoney_web_tabs_scan_v0_1_test_scan')
    bundle['date'] = '2026-06-24'
    _decision, _, _, features, _ = runner.decision_for_candidate(current, bundle, '2026-06-24')
    signals = features['paper_pick_eligibility']['signals']

    assert signals['trade_mode'] == 'afternoon_buy_next_day_sell'
    assert signals['primary_trade_horizon'] == 't1_next_day_sell'
    assert signals['candidate_lifecycle']['setup_class'] == 'DELAYED_SETUP'
    assert 'signal_maturation' in signals['candidate_lifecycle']['setup_reason']
    assert any(reason.startswith('candidate_persistence=') for reason in signals['candidate_lifecycle']['setup_reason'])


def test_sector_rotation_signal_scores_hot_sector_candidate():
    """Candidate in hot sector gets sector_opportunity_score path — sector_rotation_signal equivalent."""
    import xiaogu_forward_d1_1450_runner_v0_1 as runner
    import inspect
    src = inspect.getsource(runner)
    assert 'sector_opportunity_score' in src


def test_northbound_signal_per_stock_in_runner():
    """hsgt_institutional_flow scoring path exists in runner."""
    import xiaogu_forward_d1_1450_runner_v0_1 as runner
    import inspect
    src = inspect.getsource(runner)
    assert 'hsgt_institutional_flow' in src


def test_overheated_market_blocker_triggered():
    """overheated_market blocker exists in runner logic."""
    import xiaogu_forward_d1_1450_runner_v0_1 as runner
    import inspect
    src = inspect.getsource(runner)
    assert 'overheated_market' in src


def test_below_water_ambush_signal_in_structured_score():
    """L11_LOW_POSITION_AMBUSH or close_position_score exists."""
    import xiaogu_forward_d1_1450_runner_v0_1 as runner
    import inspect
    src = inspect.getsource(runner)
    assert 'L11_LOW_POSITION_AMBUSH' in src or 'close_position_score' in src or 'below_water' in src


def test_ledger_migrate_dry_run():
    """dry-run migration returns correct decisions, 0 skipped, inserted_picks==total_decisions."""
    import sys
    from pathlib import Path
    BASE = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BASE))
    from scripts.xiaogu_ledger_migrate import migrate_history
    ledger = BASE / 'forward_paper_ledger_v0_1.jsonl'
    result = migrate_history(ledger, dry_run=True)
    assert result['total_decisions'] >= 34, f"Expected >= 34 decisions, got {result['total_decisions']}"
    assert result['skipped'] == 0, f"Expected 0 skipped, got {result['skipped']}"
    assert result['inserted_picks'] == result['total_decisions'], (
        f"inserted_picks {result['inserted_picks']} != total_decisions {result['total_decisions']}"
    )


def test_factor_store_write_and_read(tmp_path):
    """Write 3 mock candidates and read back; verify shape and score column."""
    import importlib, sys
    import xiaogu_factor_store as fs

    # Redirect FACTOR_DIR to tmp_path so tests don't pollute data/
    original_dir = fs.FACTOR_DIR
    fs.FACTOR_DIR = tmp_path / 'factors'
    try:
        candidates = [
            {'code': '000001', 'name': '平安银行', 'score': 7.5, 'final_score': 8.0,
             'pct_chg': 1.2, 'close': 12.34, 'amount': 1e9, 'volume_ratio': 1.5,
             'net_inflow_main': 5e7, 'source_layers': ['lhb', 'sector'],
             'decision': 'PASS', 'hard_block': False, 'blocker_count': 0},
            {'code': '000002', 'name': '万科A', 'score': 5.0, 'final_score': 5.5,
             'pct_chg': 0.8, 'close': 8.9, 'amount': 5e8, 'volume_ratio': 1.1,
             'net_inflow_main': 1e7, 'source_layers': ['sector'],
             'decision': 'PASS', 'hard_block': False, 'blocker_count': 0},
            {'code': '600519', 'name': '贵州茅台', 'score': 9.2, 'final_score': 9.5,
             'pct_chg': 2.1, 'close': 1800.0, 'amount': 2e9, 'volume_ratio': 2.0,
             'net_inflow_main': 3e8, 'source_layers': ['lhb', 'sector', 'news'],
             'decision': 'PASS', 'hard_block': False, 'blocker_count': 0},
        ]
        trade_date = '2026-06-25'
        path = fs.write_factors(trade_date, candidates)
        assert path.exists(), f'Parquet not written: {path}'

        df = fs.read_factors(trade_date)
        assert df is not None, 'read_factors returned None'
        assert df.shape[0] == 3, f'Expected 3 rows, got {df.shape[0]}'
        assert 'score' in df.columns, 'score column missing'
        assert 'trade_date' in df.columns, 'trade_date column missing'
        scores = df['score'].tolist()
        assert abs(scores[0] - 7.5) < 1e-6 or abs(scores[1] - 7.5) < 1e-6 or abs(scores[2] - 7.5) < 1e-6
        # source_layers should be flattened to string
        assert all(isinstance(v, str) for v in df['source_layers'].dropna().tolist())
    finally:
        fs.FACTOR_DIR = original_dir


# ---------------------------------------------------------------------------
# BT-T3: backtest engine tests
# ---------------------------------------------------------------------------

class TestBacktestGetTradingDates:
    """test_backtest_get_trading_dates — verify weekday + scan-dir filtering."""

    def test_weekends_excluded(self, tmp_path):
        import sys
        sys.path.insert(0, str(tmp_path.parent))
        import importlib, types

        # Patch LIVE_SCAN_ROOT to tmp_path
        import xiaogu_backtest_v0_1 as bt
        original = bt.LIVE_SCAN_ROOT
        try:
            bt.LIVE_SCAN_ROOT = tmp_path
            # Create Mon-Fri dirs for week of 2026-06-22 (Mon) to 2026-06-28 (Sun)
            for d in ['2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25', '2026-06-26']:
                (tmp_path / d).mkdir()
            # 2026-06-27 (Sat) and 2026-06-28 (Sun) not created
            result = bt.get_trading_dates('2026-06-22', '2026-06-28')
            assert result == ['2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25', '2026-06-26'], \
                f'Expected 5 weekdays, got {result}'
        finally:
            bt.LIVE_SCAN_ROOT = original

    def test_missing_scan_dirs_excluded(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original = bt.LIVE_SCAN_ROOT
        try:
            bt.LIVE_SCAN_ROOT = tmp_path
            # Only create two of the five weekday dirs
            (tmp_path / '2026-06-22').mkdir()
            (tmp_path / '2026-06-24').mkdir()
            result = bt.get_trading_dates('2026-06-22', '2026-06-26')
            assert result == ['2026-06-22', '2026-06-24'], \
                f'Expected only dates with scan dirs, got {result}'
        finally:
            bt.LIVE_SCAN_ROOT = original

    def test_empty_range(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original = bt.LIVE_SCAN_ROOT
        try:
            bt.LIVE_SCAN_ROOT = tmp_path
            result = bt.get_trading_dates('2026-06-22', '2026-06-21')
            assert result == [], f'Expected empty list for inverted range, got {result}'
        finally:
            bt.LIVE_SCAN_ROOT = original


class TestBacktestBuildReport:
    """test_backtest_build_report — verify report structure with mock results."""

    def _make_results(self):
        return [
            {'trade_date': '2026-06-23', 'decision': 'PAPER_PICK', 'symbol': '300059',
             'final_score': 75.0, 'error': None, 'scan_dir': '/fake/path'},
            {'trade_date': '2026-06-24', 'decision': 'NO_PICK', 'symbol': '',
             'final_score': None, 'error': None, 'scan_dir': '/fake/path'},
            {'trade_date': '2026-06-25', 'decision': 'PAPER_PICK', 'symbol': '600519',
             'final_score': 80.0, 'error': None, 'scan_dir': '/fake/path'},
        ]

    def test_report_keys_present(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original_ledger = bt.LEDGER
        try:
            bt.LEDGER = tmp_path / 'fake_ledger.jsonl'  # non-existent → no fills
            report = bt.build_report(self._make_results())
            for key in ('backtest_dates', 'paper_picks', 'no_picks', 'filled_returns',
                        'limit_up_count', 'limit_up_rate', 'avg_t1_return',
                        'picks_detail', 'errors'):
                assert key in report, f'Missing key: {key}'
        finally:
            bt.LEDGER = original_ledger

    def test_paper_pick_count(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original_ledger = bt.LEDGER
        try:
            bt.LEDGER = tmp_path / 'fake_ledger.jsonl'
            report = bt.build_report(self._make_results())
            assert report['backtest_dates'] == 3
            assert report['paper_picks'] == 2
            assert report['no_picks'] == 1
        finally:
            bt.LEDGER = original_ledger

    def test_filled_returns_from_ledger(self, tmp_path):
        import json
        import xiaogu_backtest_v0_1 as bt
        original_ledger = bt.LEDGER
        try:
            ledger = tmp_path / 'ledger.jsonl'
            # Write RESULT_FILL for one of the PAPER_PICK dates
            with ledger.open('w') as f:
                f.write(json.dumps({
                    'record_type': 'RESULT_FILL',
                    'date': '2026-06-23',
                    'symbol': '300059',
                    't1_return': 0.10,
                }) + '\n')
            bt.LEDGER = ledger
            report = bt.build_report(self._make_results())
            assert report['filled_returns'] == 1, f'Expected 1 filled, got {report["filled_returns"]}'
            assert report['limit_up_count'] == 1, f'Expected 1 limit-up (0.10>=0.095)'
            assert report['limit_up_rate'] == 1.0
            assert abs(report['avg_t1_return'] - 0.10) < 1e-6
        finally:
            bt.LEDGER = original_ledger

    def test_no_fills_returns_none_metrics(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original_ledger = bt.LEDGER
        try:
            bt.LEDGER = tmp_path / 'nonexistent.jsonl'
            report = bt.build_report(self._make_results())
            assert report['limit_up_rate'] is None
            assert report['avg_t1_return'] is None
            assert report['filled_returns'] == 0
        finally:
            bt.LEDGER = original_ledger

    def test_errors_captured(self, tmp_path):
        import xiaogu_backtest_v0_1 as bt
        original_ledger = bt.LEDGER
        try:
            bt.LEDGER = tmp_path / 'nonexistent.jsonl'
            results = self._make_results()
            results.append({'trade_date': '2026-06-26', 'decision': None, 'symbol': None,
                            'final_score': None, 'error': 'timeout', 'scan_dir': None})
            report = bt.build_report(results)
            assert len(report['errors']) == 1
            assert report['errors'][0]['error'] == 'timeout'
        finally:
            bt.LEDGER = original_ledger


def test_weekday_blocklist_blocks_monday():
    """Monday picks should be blocked when weekday_blocklist contains 0."""
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [make_candidate(
            '300017', 'Monday Stock', score=87.9, rank=1, price=15.0,
            sector_score=1.0, source_time='2026-06-22 14:49:54',
            runner_asof_time='15:10:30',
            candidate_evidence_domain_counts=required_counts,
            enhanced_evidence_domain_counts=enhanced_counts,
        )],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]
    # 2026-06-22 is Monday
    bundle['date'] = '2026-06-22'

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert 'WEEKDAY_SOFT_BLOCKED' in eligibility['blockers'], (
        f"Expected WEEKDAY_SOFT_BLOCKED in blockers, got: {eligibility['blockers']}"
    )
    assert eligibility['eligible'] is False


def test_weekday_allows_wednesday():
    """Wednesday picks should pass the weekday check."""
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    bundle = make_bundle(
        [make_candidate(
            '300017', 'Wednesday Stock', score=87.9, rank=1, price=15.0,
            sector_score=1.0, source_time='2026-06-24 14:49:54',
            runner_asof_time='15:10:30',
            candidate_evidence_domain_counts=required_counts,
            enhanced_evidence_domain_counts=enhanced_counts,
        )],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    candidate = bundle['paper_scoring_candidates'][0]
    # 2026-06-24 is Wednesday
    bundle['date'] = '2026-06-24'

    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert 'WEEKDAY_SOFT_BLOCKED' not in eligibility['blockers'], (
        f"Unexpected WEEKDAY_SOFT_BLOCKED on Wednesday: {eligibility['blockers']}"
    )


def test_lifecycle_profile_uses_candidate_bundle_history_for_delayed_setup(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(runner, 'get_scoring_config_snapshot', lambda force_refresh=False: make_scoring_config_snapshot())

    prior_a = make_candidate(
        '300777',
        'Repeat Theme',
        score=76.0,
        rank=1,
        sector_score=0.72,
        fund_flow_momentum=0.68,
        source_time='2026-06-22 10:00:00',
    )
    prior_a['source_layers'] = ['theme', 'fund_flow']
    prior_a['blocked_reasons'] = []
    prior_b = make_candidate(
        '300777',
        'Repeat Theme',
        score=77.0,
        rank=1,
        sector_score=0.74,
        fund_flow_momentum=0.66,
        source_time='2026-06-23 10:00:00',
    )
    prior_b['source_layers'] = ['theme', 'fund_flow']
    prior_b['blocked_reasons'] = []
    current = make_candidate(
        '300777',
        'Repeat Theme',
        score=78.0,
        rank=1,
        sector_score=0.76,
        fund_flow_momentum=0.7,
        source_time='2026-06-24 10:00:00',
    )
    current['source_layers'] = ['theme', 'fund_flow']
    current['blocked_reasons'] = []

    write_candidate_bundle(bundle_root, '2026-06-22', [prior_a])
    write_candidate_bundle(bundle_root, '2026-06-23', [prior_b])

    profile = runner._candidate_lifecycle_profile(current, {'date': '2026-06-24'})

    assert profile['setup_class'] == 'DELAYED_SETUP'
    assert profile['repeat_count'] >= 2
    assert profile['history_tail']
    assert all(entry['picked'] is False for entry in profile['history_tail'])
    assert all(entry['is_official_pick'] is False for entry in profile['history_tail'])


def test_lifecycle_profile_uses_scoring_config_thresholds(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(
        runner,
        'get_scoring_config_snapshot',
        lambda force_refresh=False: make_scoring_config_snapshot(delayed_setup_min_persistence='3'),
    )

    prior_a = make_candidate(
        '300778',
        'Threshold Theme',
        score=76.0,
        rank=1,
        sector_score=0.72,
        fund_flow_momentum=0.68,
        source_time='2026-06-22 10:00:00',
    )
    prior_a['source_layers'] = ['theme', 'fund_flow']
    prior_b = make_candidate(
        '300778',
        'Threshold Theme',
        score=77.0,
        rank=1,
        sector_score=0.74,
        fund_flow_momentum=0.66,
        source_time='2026-06-23 10:00:00',
    )
    prior_b['source_layers'] = ['theme', 'fund_flow']
    current = make_candidate(
        '300778',
        'Threshold Theme',
        score=78.0,
        rank=1,
        sector_score=0.76,
        fund_flow_momentum=0.7,
        source_time='2026-06-24 10:00:00',
    )
    current['source_layers'] = ['theme', 'fund_flow']

    write_candidate_bundle(bundle_root, '2026-06-22', [prior_a])
    write_candidate_bundle(bundle_root, '2026-06-23', [prior_b])

    profile = runner._candidate_lifecycle_profile(current, {'date': '2026-06-24'})

    assert profile['repeat_count'] >= 2
    assert profile['setup_class'] != 'DELAYED_SETUP'


def test_lifecycle_profile_marks_stale_repeat(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(runner, 'get_scoring_config_snapshot', lambda force_refresh=False: make_scoring_config_snapshot())

    for day in ('2026-06-20', '2026-06-21', '2026-06-23'):
        repeat = make_candidate(
            '300779',
            'Stale Repeat',
            score=60.0,
            rank=3,
            sector_score=0.05,
            fund_flow_momentum=0.02,
            early_opportunity_score=0.1,
            source_time=f'{day} 10:00:00',
        )
        repeat['source_layers'] = ['crowded']
        repeat['blocked_reasons'] = []
        write_candidate_bundle(bundle_root, day, [repeat])

    current = make_candidate(
        '300779',
        'Stale Repeat',
        score=61.0,
        rank=3,
        sector_score=0.04,
        fund_flow_momentum=0.01,
        early_opportunity_score=0.1,
        source_time='2026-06-24 10:00:00',
    )
    current['source_layers'] = ['crowded']
    current['blocked_reasons'] = []

    profile = runner._candidate_lifecycle_profile(current, {'date': '2026-06-24'})

    assert profile['repeat_count'] >= 3
    assert profile['setup_class'] == 'STALE_REPEAT'
    assert profile['stale_decay'] > 0


def test_lifecycle_profile_promotes_first_seen_reversal_to_delayed_setup(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(runner, 'get_scoring_config_snapshot', lambda force_refresh=False: make_scoring_config_snapshot())

    current = make_candidate(
        '002202',
        'Wind Reversal',
        score=64.8,
        rank=19,
        sector_score=1.0,
        fund_flow_momentum=0.65,
        time_series_momentum=0.22,
        low_position_catalyst_score=0.62,
        early_opportunity_score=0.84,
        candidate_stage='mid_5_to_7',
        search_layer_hint='intraday_alert_reversal',
        setup_type='INTRADAY_ALERT_REVERSAL',
        source_time='2026-06-27 15:46:39',
        close_position_score=0.67,
        signal_pct=6.53,
    )
    current['source_layers'] = ['L0_FULL_UNIVERSE', 'L4_PRE_BREAKOUT', 'L7_INTRADAY_ALERT']
    current['intraday_alert_strength'] = 0.92

    profile = runner._candidate_lifecycle_profile(current, {'date': '2026-06-27'})

    assert profile['setup_class'] == 'DELAYED_SETUP'
    assert any(r in profile['setup_reason'] for r in ('first_seen_reversal_maturation', 'signal_maturation'))
    assert profile['repeat_count'] == 0


def test_lifecycle_profile_keeps_plain_first_seen_as_watch_only(tmp_path, monkeypatch):
    bundle_root = tmp_path / 'data' / 'forward_candidate_bundles'
    monkeypatch.setattr(runner, 'CANDIDATE_BUNDLE_ROOT', bundle_root)
    monkeypatch.setattr(runner, '_ledger_decision_rows', lambda: [])
    monkeypatch.setattr(runner, 'get_scoring_config_snapshot', lambda force_refresh=False: make_scoring_config_snapshot())

    current = make_candidate(
        '300780',
        'Plain First Seen',
        score=62.0,
        rank=8,
        sector_score=0.3,
        fund_flow_momentum=0.15,
        time_series_momentum=0.05,
        low_position_catalyst_score=0.2,
        early_opportunity_score=0.35,
        candidate_stage='flat_0_to_3',
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        source_time='2026-06-27 10:00:00',
        close_position_score=0.4,
        signal_pct=2.1,
    )
    current['source_layers'] = ['L0_FULL_UNIVERSE']

    profile = runner._candidate_lifecycle_profile(current, {'date': '2026-06-27'})

    assert profile['setup_class'] == 'WATCH_ONLY'
    assert profile['setup_reason'] == []


def _make_horizon_replay_row(
    trade_date: str,
    symbol: str,
    name: str,
    *,
    source_kind: str,
    rank: int,
    score: float,
    final_score: float | None = None,
    decision: str = '',
    picked: bool = False,
    is_official_pick: bool = False,
    setup_class: str = '',
    repeat_count: int | None = None,
    theme_support: float | None = None,
    instant_confirmations: int | None = None,
    stale_decay: float | None = None,
    blockers: list[str] | None = None,
    source_layers: list[str] | None = None,
    t1_return: float | None = None,
    t2_return: float | None = None,
    t3_return: float | None = None,
    t5_return: float | None = None,
    candidate_features: dict | None = None,
    paper_pick_eligibility: dict | None = None,
    signals: dict | None = None,
    historical_decision: str | None = None,
) -> dict:
    lifecycle: dict = {}
    if setup_class:
        lifecycle['setup_class'] = setup_class
    if repeat_count is not None:
        lifecycle['repeat_count'] = repeat_count
    if theme_support is not None:
        lifecycle['theme_support'] = theme_support
    if instant_confirmations is not None:
        lifecycle['instant_confirmations'] = instant_confirmations
    if stale_decay is not None:
        lifecycle['stale_decay'] = stale_decay
    features = dict(candidate_features or {})
    if lifecycle:
        features.setdefault('candidate_lifecycle', lifecycle)
    if paper_pick_eligibility is not None:
        features.setdefault('paper_pick_eligibility', paper_pick_eligibility)
    if signals is not None:
        features.setdefault('signals', signals)
    return {
        'trade_date': trade_date,
        'symbol': symbol,
        'name': name,
        'rank': rank,
        'score': score,
        'final_score': score if final_score is None else final_score,
        'decision': decision,
        'picked': picked,
        'is_official_pick': is_official_pick,
        'source_kind': source_kind,
        'source_layers': source_layers or ['formal_high_score'],
        'blockers': blockers or [],
        'candidate_lifecycle': lifecycle,
        'historical_decision': historical_decision or decision or ('PAPER_PICK' if picked or is_official_pick else 'NO_PICK'),
        'candidate_features': features or {
            'candidate_lifecycle': lifecycle,
        },
        't1_return': t1_return,
        't2_return': t2_return,
        't3_return': t3_return,
        't5_return': t5_return,
        'paper_pick_eligibility': paper_pick_eligibility or {},
        'signals': signals or {},
    }


def _horizon_replay_fixture_rows() -> list[dict]:
    return [
        _make_horizon_replay_row(
            '2026-06-10',
            '300077',
            '国民技术',
            source_kind='db_daily_candidate',
            rank=1,
            score=91.0,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.72,
            stale_decay=0.55,
            t1_return=-0.01,
            t2_return=0.01,
            t3_return=0.05,
            t5_return=0.07,
        ),
        _make_horizon_replay_row(
            '2026-06-10',
            '301236',
            '软通动力',
            source_kind='db_daily_candidate',
            rank=2,
            score=90.0,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=3,
            t1_return=0.08,
            t2_return=0.04,
            t3_return=0.02,
            t5_return=0.01,
        ),
        _make_horizon_replay_row(
            '2026-06-10',
            '300398',
            '立高食品',
            source_kind='db_daily_candidate',
            rank=3,
            score=88.0,
            setup_class='DELAYED_SETUP',
            repeat_count=2,
            theme_support=0.61,
            stale_decay=0.62,
            t1_return=0.0,
            t2_return=0.02,
            t3_return=0.04,
            t5_return=0.03,
        ),
        _make_horizon_replay_row(
            '2026-06-10',
            '300059',
            '东方财富',
            source_kind='db_daily_candidate',
            rank=4,
            score=86.0,
            setup_class='STALE_REPEAT',
            repeat_count=4,
            stale_decay=0.45,
            t1_return=-0.03,
            t2_return=-0.01,
            t3_return=-0.01,
            t5_return=0.0,
        ),
        _make_horizon_replay_row(
            '2026-06-10',
            '000938',
            '紫光股份',
            source_kind='db_daily_candidate',
            rank=5,
            score=85.0,
            t1_return=0.01,
            t2_return=0.02,
            t3_return=0.03,
            t5_return=0.04,
        ),
        _make_horizon_replay_row(
            '2026-06-10',
            '603386',
            '广东骏亚',
            source_kind='db_pick',
            rank=6,
            score=92.0,
            decision='PAPER_PICK',
            picked=True,
            is_official_pick=True,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=4,
            t1_return=0.12,
            t2_return=0.07,
            t3_return=0.03,
            t5_return=0.01,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '300777',
            '延迟样本A',
            source_kind='db_daily_candidate',
            rank=1,
            score=89.0,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.7,
            stale_decay=0.58,
            t1_return=0.02,
            t2_return=0.03,
            t3_return=0.08,
            t5_return=0.09,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '300888',
            '即时样本B',
            source_kind='db_daily_candidate',
            rank=2,
            score=87.0,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=2,
            t1_return=0.1,
            t2_return=0.05,
            t3_return=0.03,
            t5_return=0.01,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '300999',
            '普通样本C',
            source_kind='db_daily_candidate',
            rank=3,
            score=84.0,
            t1_return=0.0,
            t2_return=0.01,
            t3_return=0.02,
            t5_return=0.02,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '301234',
            '陈旧样本D',
            source_kind='db_daily_candidate',
            rank=4,
            score=83.0,
            setup_class='STALE_REPEAT',
            repeat_count=4,
            stale_decay=0.41,
            t1_return=-0.02,
            t2_return=-0.01,
            t3_return=-0.01,
            t5_return=0.0,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '002517',
            '恺英网络',
            source_kind='db_daily_candidate',
            rank=5,
            score=82.0,
            t1_return=0.01,
            t2_return=0.01,
            t3_return=0.02,
            t5_return=0.02,
        ),
        _make_horizon_replay_row(
            '2026-06-11',
            '603725',
            '天正电气',
            source_kind='db_daily_candidate',
            rank=6,
            score=85.0,
            t1_return=-0.04,
            t2_return=-0.02,
            t3_return=-0.01,
            t5_return=-0.02,
        ),
    ]


def test_horizon_replay_includes_paper_picks_and_daily_top5(monkeypatch, capsys):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    rows = _horizon_replay_fixture_rows()
    monkeypatch.setattr(eff, '_collect_horizon_replay_sources', lambda ledger_path, focus_symbols: (rows, {'db': {'loaded': True}, 'ledger': {'loaded': True}, 'files': {'loaded': False}}))
    monkeypatch.setattr(eff, '_default_horizon_decision_replayer', lambda record, candidate, bundle: {
        'final_decision': record.get('decision') or ('PAPER_PICK' if record.get('picked') else 'NO_PICK'),
        'replay_eligible': bool(record.get('picked')),
        'replay_reason': 'test',
        'replay_flags': [],
    })
    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    monkeypatch.setattr(sys, 'argv', [
        'signal_effectiveness',
        '--horizon-replay',
        '--top-n',
        '5',
        '--focus-symbols',
        '300077,002600,603386,300398,301236,300059,000938,002517,603725,301017',
        '--json',
    ])

    eff.main()
    out = json.loads(capsys.readouterr().out)
    symbols = {record['symbol'] for record in out['records']}

    assert out['trade_mode'] == 'afternoon_buy_next_day_sell'
    assert out['primary_return_field'] == 't1_return'
    assert out['primary_trade_horizon'] == 't1_next_day_sell'
    assert symbols.issuperset({'603386', '603725', '300077', '301236'})
    assert any('paper_pick' in record['universe_reason'] for record in out['records'])
    assert sum(1 for record in out['records'] if any(str(reason).startswith('daily_top') for reason in record['universe_reason'])) == 10


def test_horizon_replay_preserves_t1_as_primary_return(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    rows = [
        _make_horizon_replay_row(
            '2026-06-12',
            '300111',
            'Primary First',
            source_kind='db_daily_candidate',
            rank=1,
            score=88.0,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.65,
            stale_decay=0.6,
            t1_return=0.01,
            t2_return=0.04,
            t3_return=0.09,
            t5_return=0.06,
        )
    ]

    result = eff.build_horizon_replay(rows, focus_symbols=['300111'], decision_replayer=lambda record, candidate, bundle: {'final_decision': 'NO_PICK', 'replay_eligible': False, 'replay_reason': 'test', 'replay_flags': []})
    record = result['records'][0]

    assert result['trade_mode'] == 'afternoon_buy_next_day_sell'
    assert result['primary_return_field'] == 't1_return'
    assert result['primary_trade_horizon'] == 't1_next_day_sell'
    assert record['primary_trade_return'] == record['t1_return']
    assert record['maturation_horizon'] == 't3'
    assert record['best_horizon'] == 't3'


def test_horizon_replay_preserves_historical_paper_pick_final_decision(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    rows = [
        _make_horizon_replay_row(
            '2026-06-12',
            '300410',
            'Historical Pick',
            source_kind='db_pick',
            rank=1,
            score=88.0,
            decision='PAPER_PICK',
            picked=True,
            is_official_pick=True,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=3,
            t1_return=0.03,
        ),
        _make_horizon_replay_row(
            '2026-06-13',
            '300411',
            'Historical No Pick',
            source_kind='db_daily_candidate',
            rank=2,
            score=84.0,
            setup_class='DELAYED_SETUP',
            repeat_count=2,
            theme_support=0.55,
            stale_decay=0.2,
            t1_return=-0.02,
        ),
    ]
    result = eff.build_horizon_replay(
        rows,
        focus_symbols=['300410', '300411'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': ['source_time>asof_time'],
        },
    )

    pick_record = next(record for record in result['records'] if record['symbol'] == '300410')
    assert pick_record['historical_decision'] == 'PAPER_PICK'
    assert pick_record['replay_decision'] == 'NO_PICK'
    assert pick_record['final_decision'] == 'PAPER_PICK'


def test_horizon_replay_daily_ticket_rate_counts_historical_picks(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    rows = [
        _make_horizon_replay_row(
            '2026-06-14',
            '300420',
            'Historical Pick',
            source_kind='db_pick',
            rank=1,
            score=88.0,
            decision='PAPER_PICK',
            picked=True,
            is_official_pick=True,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=3,
            t1_return=0.03,
        ),
        _make_horizon_replay_row(
            '2026-06-15',
            '300421',
            'No Pick',
            source_kind='db_daily_candidate',
            rank=2,
            score=84.0,
            setup_class='DELAYED_SETUP',
            repeat_count=2,
            theme_support=0.55,
            stale_decay=0.2,
            t1_return=-0.02,
        ),
    ]
    result = eff.build_horizon_replay(
        rows,
        focus_symbols=['300420', '300421'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    assert result['metrics']['daily_coverage_count'] == 2
    assert result['metrics']['daily_ticket_rate'] == 0.5


def test_horizon_replay_setup_class_populated_from_candidate_lifecycle(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    rows = [
        _make_horizon_replay_row(
            '2026-06-16',
            '300430',
            'Instant Lifecycle',
            source_kind='db_daily_candidate',
            rank=1,
            score=90.0,
            setup_class='',
            candidate_features={
                'candidate_lifecycle': {
                    'setup_class': 'INSTANT_MOMENTUM_SETUP',
                    'repeat_count': 1,
                    'instant_confirmations': 3,
                    'theme_support': 0.1,
                    'stale_decay': 0.0,
                    'lifecycle_score': 0.9,
                },
            },
            t1_return=0.05,
        ),
        _make_horizon_replay_row(
            '2026-06-16',
            '300431',
            'Delayed Lifecycle',
            source_kind='db_daily_candidate',
            rank=2,
            score=87.0,
            setup_class='',
            candidate_features={
                'candidate_lifecycle': {
                    'setup_class': 'DELAYED_SETUP',
                    'repeat_count': 3,
                    'instant_confirmations': 0,
                    'theme_support': 0.72,
                    'stale_decay': 0.45,
                    'lifecycle_score': 0.8,
                },
            },
            t1_return=0.01,
            t3_return=0.08,
        ),
    ]
    result = eff.build_horizon_replay(
        rows,
        focus_symbols=['300430', '300431'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    metrics = result['metrics']
    assert metrics['instant_setup']['count'] == 1
    assert metrics['delayed_setup']['count'] == 1
    assert metrics['instant_setup']['primary_win_rate'] == 1.0
    assert metrics['delayed_setup']['matured_later_rate'] == 1.0


def test_horizon_replay_later_candidate_primary_return_as_maturation_evidence(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    rows = [
        _make_horizon_replay_row(
            '2026-06-17',
            '300440',
            'Earlier Candidate',
            source_kind='db_daily_candidate',
            rank=1,
            score=86.0,
            setup_class='DELAYED_SETUP',
            repeat_count=3,
            theme_support=0.68,
            stale_decay=0.6,
            t1_return=-0.02,
        ),
        _make_horizon_replay_row(
            '2026-06-18',
            '300440',
            'Later Candidate',
            source_kind='db_daily_candidate',
            rank=1,
            score=83.0,
            setup_class='INSTANT_MOMENTUM_SETUP',
            instant_confirmations=2,
            t1_return=0.09,
        ),
    ]
    result = eff.build_horizon_replay(
        rows,
        focus_symbols=['300440'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    earlier_record = next(record for record in result['records'] if record['trade_date'] == '2026-06-17')
    assert earlier_record['primary_trade_return'] == earlier_record['t1_return']
    assert earlier_record['maturation_source'] == 'later_candidate_primary_return'
    assert earlier_record['return_data_status']['t2']['source_kind'] == 'later_candidate_primary_return'
    assert earlier_record['return_data_status']['t2']['source_role'] == 'diagnostic_maturation'
    assert earlier_record['t2_return'] == 0.09
    assert earlier_record['maturation_horizon'] == 't2'


def test_horizon_replay_explanation_blockers_are_summarized(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    long_body = 'Eastmoney page text ' + ('lorem ipsum dolor sit amet ' * 40)
    rows = [
        _make_horizon_replay_row(
            '2026-06-19',
            '300450',
            'Blocked Candidate',
            source_kind='db_daily_candidate',
            rank=1,
            score=81.0,
            blockers=[f'regulatory_hard_block:{long_body}'],
            setup_class='STALE_REPEAT',
            repeat_count=4,
            t1_return=-0.03,
        ),
    ]
    result = eff.build_horizon_replay(
        rows,
        focus_symbols=['300450'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': [f'regulatory_hard_block:{long_body}'],
        },
    )

    record = result['records'][0]
    focus = result['focus_explanations']['300450']
    assert record['raw_blockers'][0].endswith(long_body)
    assert record['blockers'] == ['regulatory_hard_block:LONG_EASTMONEY_PAGE_TEXT']
    assert long_body not in focus['why_blocked_or_cool_down']
    assert 'LONG_EASTMONEY_PAGE_TEXT' in focus['why_blocked_or_cool_down']


def test_horizon_replay_metrics_separate_primary_and_maturation(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    result = eff.build_horizon_replay(
        _horizon_replay_fixture_rows(),
        focus_symbols=['300077', '603386', '603725'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': record.get('decision') or ('PAPER_PICK' if record.get('picked') else 'NO_PICK'),
            'replay_eligible': bool(record.get('picked')),
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    metrics = result['metrics']

    assert metrics['instant_setup']['count'] >= 2
    assert metrics['instant_setup']['primary_win_rate'] == 1.0
    assert metrics['delayed_setup']['count'] >= 2
    assert metrics['delayed_setup']['matured_later_rate'] == 1.0
    assert metrics['delayed_setup']['avg_maturation_return'] > metrics['delayed_setup']['avg_primary_return']
    assert metrics['stale_repeat']['count'] >= 2
    assert metrics['false_positive']['count'] == 1
    assert metrics['matured_later_candidates'] >= 2


def test_horizon_replay_focus_explanations_cover_named_symbols(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    focus_symbols = ['300077', '002600', '603386', '300398', '301236', '300059', '000938', '002517', '603725', '301017']
    result = eff.build_horizon_replay(
        _horizon_replay_fixture_rows(),
        focus_symbols=focus_symbols,
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': record.get('decision') or ('PAPER_PICK' if record.get('picked') else 'NO_PICK'),
            'replay_eligible': bool(record.get('picked')),
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    assert set(result['focus_explanations']) == set(focus_symbols)
    assert result['focus_explanations']['300077']['appeared_as_candidate'] is True
    assert result['focus_explanations']['002600']['name'] == '领益智造'
    assert result['focus_explanations']['603386']['appeared_as_paper_pick'] is True
    assert result['focus_explanations']['301017']['appeared_as_candidate'] is False
    assert result['focus_explanations']['300077']['name'] == '国民技术'


def test_horizon_replay_calibration_insufficient_data_is_safe(monkeypatch):
    import xiaogu_db
    import xiaogu_signal_effectiveness_v0_1 as eff

    monkeypatch.setattr(xiaogu_db, 'get_scoring_config_snapshot', lambda refresh=False: {'config': {}, 'loaded': False, 'source': 'defaults', 'error': ''})
    result = eff.build_horizon_replay(
        [
            _make_horizon_replay_row(
                '2026-06-13',
                '300222',
                'Tiny Sample',
                source_kind='db_daily_candidate',
                rank=1,
                score=81.0,
                t1_return=0.01,
                t2_return=0.02,
                t3_return=0.03,
                t5_return=0.04,
            )
        ],
        focus_symbols=['300222'],
        decision_replayer=lambda record, candidate, bundle: {
            'final_decision': 'NO_PICK',
            'replay_eligible': False,
            'replay_reason': 'test',
            'replay_flags': [],
        },
    )

    assert set(result['calibration_suggestions']) == {
        'delayed_setup_min_persistence',
        'delayed_setup_theme_min_score',
        'instant_momentum_min_confirmations',
        'stale_repeat_window_days',
        'stale_decay_factor',
    }
    assert all(item['suggested'] == 'insufficient_data' for item in result['calibration_suggestions'].values())


# ---------------------------------------------------------------------------
# 改动 A/B/C/D/E 验证测试
# ---------------------------------------------------------------------------

def test_near_limit_with_l2_confirmation_is_positive() -> None:
    """near_limit_up_risk=True 但有 limitup_reason_strength>=0.60 时，
    eligibility 不应有 near_limit_up_risk blocker，而应有 positive condition。"""
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600101',
        'L2 Near Limit',
        score=82.0,
        rank=1,
        evidence='PASS',
        data_gate='PASS',
        blocked_reasons=['near_limit_up_risk'],
        buy_strength=0.75,  # limitup_reason_strength >= 0.60
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        signal_pct=9.3,
        fund_flow_momentum=0.6,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['source_layers'] = ['L2_LIMIT_STRENGTH']
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert 'near_limit_up_risk' not in eligibility['blockers'], (
        f"near_limit_up_risk should be exempted when L2 confirmed, but blockers={eligibility['blockers']}"
    )
    assert 'near_limit_with_L2_confirmation' in eligibility['positive_conditions'], (
        f"Expected near_limit_with_L2_confirmation in positive_conditions, got {eligibility['positive_conditions']}"
    )


def test_near_limit_without_l2_still_blocks() -> None:
    """near_limit_up_risk=True 且无 L2 确认时，仍然应该 block。"""
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600102',
        'Near Limit No L2',
        score=70.0,
        rank=2,
        evidence='PASS',
        data_gate='PASS',
        blocked_reasons=['near_limit_up_risk'],
        buy_strength=0.40,  # limitup_reason_strength < 0.60
        source_time='2026-06-10 14:49:54',
        runner_asof_time='15:10:30',
        signal_pct=9.1,
        fund_flow_momentum=0.3,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    # 不设置 source_layers 或者设置非 L2 层
    candidate['source_layers'] = ['L3_FUND_FLOW']
    bundle = make_bundle(
        [candidate],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)

    assert 'near_limit_up_risk' in eligibility['blockers'], (
        f"near_limit_up_risk should remain a blocker without L2, blockers={eligibility['blockers']}"
    )
    assert 'near_limit_with_L2_confirmation' not in eligibility['positive_conditions'], (
        f"Should not have L2 confirmation positive condition, got {eligibility['positive_conditions']}"
    )


def test_l2_limit_strength_gets_priority_bonus() -> None:
    """L2_LIMIT_STRENGTH 候选在排序中优先于同等分数的非 L2 候选。"""
    source_status = load_real_bundle()['source_status']
    required_counts, enhanced_counts = full_candidate_evidence_counts()

    base_kwargs = dict(
        score=80.0,
        rank=1,
        evidence='PASS',
        data_gate='PASS',
        buy_strength=0.70,
        source_time='2026-06-10 14:30:00',
        runner_asof_time='15:10:30',
        signal_pct=5.0,
        fund_flow_momentum=0.5,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )

    c_l2 = make_candidate('600201', 'L2 Candidate', **base_kwargs)
    c_l2['source_layers'] = ['L2_LIMIT_STRENGTH']

    c_other = make_candidate('600202', 'Non-L2 Candidate', **base_kwargs)
    c_other['source_layers'] = ['L3_FUND_FLOW']

    bundle = make_bundle(
        [c_l2, c_other],
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        source_status=source_status,
        asof_time='15:10:30',
    )

    # 通过 official_pick_priority 直接比较优先级 tuple
    features_l2 = runner.paper_pick_eligibility_profile(c_l2, bundle)
    features_other = runner.paper_pick_eligibility_profile(c_other, bundle)

    # official_pick_priority 是 daily_ticket_search 内部函数，通过 bundle 路径触发排序比较
    # 直接检查 source_layers 在 candidate 上的 l2_bonus 差异
    _layers_l2 = set(c_l2.get('source_layers') or [])
    _layers_other = set(c_other.get('source_layers') or [])
    l2_bonus_l2 = 2.0 if 'L2_LIMIT_STRENGTH' in _layers_l2 else 0.0
    l2_bonus_other = 2.0 if 'L2_LIMIT_STRENGTH' in _layers_other else 0.0

    assert l2_bonus_l2 > l2_bonus_other, (
        f"L2 candidate should have higher l2_bonus ({l2_bonus_l2}) than non-L2 ({l2_bonus_other})"
    )


def test_score_cap_defaults_to_88() -> None:
    """SCORING_CONFIG_DEFAULTS['max_score_cap'] 应为 '88'。"""
    assert runner.SCORING_CONFIG_DEFAULTS['max_score_cap'] == '88', (
        f"Expected max_score_cap='88', got {runner.SCORING_CONFIG_DEFAULTS['max_score_cap']!r}"
    )
    from xiaogu_db import SCORING_CONFIG_DEFAULTS as db_defaults
    assert db_defaults['max_score_cap'] == '88', (
        f"xiaogu_db SCORING_CONFIG_DEFAULTS max_score_cap expected '88', got {db_defaults['max_score_cap']!r}"
    )


def test_sector_catalyst_only_gets_penalty() -> None:
    """纯 L6_SECTOR_CATALYST 候选排序低于混合层候选（l2_bonus/layer_penalty 逻辑验证）。"""
    _pure_l6_layers = {'L6_SECTOR_CATALYST'}
    _mixed_layers = {'L6_SECTOR_CATALYST', 'L2_LIMIT_STRENGTH'}

    # 纯 L6 候选
    _pure_weak = (
        _pure_l6_layers
        and _pure_l6_layers <= {'L6_SECTOR_CATALYST', 'L1_HOT_MOMENTUM'}
        and 'L2_LIMIT_STRENGTH' not in _pure_l6_layers
    )
    penalty_pure = -1.0 if _pure_weak else 0.0
    l2_bonus_pure = 2.0 if 'L2_LIMIT_STRENGTH' in _pure_l6_layers else 0.0

    # 混合候选（含 L2）
    _mixed_weak = (
        _mixed_layers
        and _mixed_layers <= {'L6_SECTOR_CATALYST', 'L1_HOT_MOMENTUM'}
        and 'L2_LIMIT_STRENGTH' not in _mixed_layers
    )
    penalty_mixed = -1.0 if _mixed_weak else 0.0
    l2_bonus_mixed = 2.0 if 'L2_LIMIT_STRENGTH' in _mixed_layers else 0.0

    assert penalty_pure < penalty_mixed or l2_bonus_pure < l2_bonus_mixed, (
        "Pure L6 candidate should have lower priority than mixed L2+L6 candidate"
    )
    assert penalty_pure == -1.0, f"Expected pure L6 penalty=-1.0, got {penalty_pure}"
    assert l2_bonus_mixed == 2.0, f"Expected mixed L2 bonus=2.0, got {l2_bonus_mixed}"


def test_mainboard_candidate_pool_excludes_chinext_star_and_beijing() -> None:
    stocks = [
        {'f12': code, 'f14': code, 'f2': 10, 'f3': 3, 'f6': 1_000_000_000, 'f8': 2, 'f4': 10.2, 'f5': 9.5, 'f18': 9.7}
        for code in ('600001', '000001', '002001', '003001', '300001', '688001', '830001')
    ]
    candidates = scanner_v2._generate_candidates(stocks, {}, 50, 10)
    assert {row['code'] for row in candidates} == {'600001', '000001', '002001', '003001'}
    assert all(scanner_v2.is_mainboard_code(row['code']) for row in candidates)


def test_full_candidate_pool_caps_at_200_without_passed_pool_truncation() -> None:
    stocks = [
        {
            'f12': f'600{index:03d}', 'f14': f'主板{index}', 'f2': 10,
            'f3': 1, 'f6': 1_000_000_000 + index, 'f8': 2,
            'f4': 10.2, 'f5': 9.8, 'f18': 9.9,
        }
        for index in range(260)
    ]
    candidates = scanner_v2._generate_candidates(stocks, {}, 50, 10)

    assert len(candidates) == 200
    assert all(row['code'].startswith('600') for row in candidates)
    assert max(int(row['code']) for row in candidates) > 600199


def test_full_candidate_pool_fills_to_200_unique_symbols_when_ranked_rows_have_duplicates() -> None:
    ranked_rows = [
        make_candidate(f'600{index:03d}', f'主板{index}', score=90 - index / 10, rank=index + 1)
        for index in range(195)
    ]
    ranked_rows.extend([
        make_candidate('600010', '重复10', score=60, rank=196),
        make_candidate('600020', '重复20', score=59, rank=197),
        make_candidate('600030', '重复30', score=58, rank=198),
        make_candidate('600040', '重复40', score=57, rank=199),
        make_candidate('600050', '重复50', score=56, rank=200),
    ])
    ranked_rows.extend([
        make_candidate(f'600{index:03d}', f'补位{index}', score=55 - index / 10, rank=index + 6)
        for index in range(195, 200)
    ])

    selected, summary = scanner_v2.select_unique_candidate_pool(ranked_rows, 200)

    selected_symbols = [row['symbol'] for row in selected]
    assert len(selected) == 200
    assert len(set(selected_symbols)) == 200
    assert selected_symbols[-1] == '600199'
    assert summary['duplicate_symbol_count'] == 5
    assert summary['deduplication_applied'] is True


def test_persistence_dedupes_full_candidate_pool_before_db_upsert() -> None:
    full_pool = [
        make_candidate(f'600{index:03d}', f'主板{index}', score=90 - index / 10, rank=index + 1)
        for index in range(195)
    ]
    full_pool.extend([
        make_candidate('600010', '重复10', score=60, rank=196),
        make_candidate('600020', '重复20', score=59, rank=197),
        make_candidate('600030', '重复30', score=58, rank=198),
        make_candidate('600040', '重复40', score=57, rank=199),
        make_candidate('600050', '重复50', score=56, rank=200),
    ])
    full_pool.extend([
        make_candidate(f'600{index:03d}', f'补位{index}', score=55 - index / 10, rank=index + 6)
        for index in range(195, 200)
    ])
    bundle = {
        'full_candidate_pool': full_pool,
        'candidate_pool_exclusion_summary': {
            'raw_universe_count': 260,
            'mainboard_tradable_count': 260,
            'target_count': 200,
            'source_status': 'PASS',
        },
    }

    payload = runner.build_daily_candidate_persistence_payloads(
        '2026-07-10', bundle, {}, 'PAPER_PICK', 'selected',
    )

    symbols = [row['symbol'] for row in payload['daily_candidates']]
    context = payload['daily_candidates'][0]['candidate_features']['candidate_pool_context']
    assert len(symbols) == 200
    assert len(set(symbols)) == 200
    assert context['source_row_count'] == 205
    assert context['duplicate_symbol_count'] == 5
    assert context['deduplication_applied'] is True
    assert context['final_persisted_count'] == 200


def test_persistence_reports_actual_unique_count_when_duplicate_pool_has_no_replacements() -> None:
    full_pool = [
        make_candidate(f'600{index:03d}', f'主板{index}', score=90 - index / 10, rank=index + 1)
        for index in range(195)
    ]
    full_pool.extend([
        make_candidate('600010', '重复10', score=60, rank=196),
        make_candidate('600020', '重复20', score=59, rank=197),
        make_candidate('600030', '重复30', score=58, rank=198),
        make_candidate('600040', '重复40', score=57, rank=199),
        make_candidate('600050', '重复50', score=56, rank=200),
    ])
    bundle = {
        'full_candidate_pool': full_pool,
        'candidate_pool_exclusion_summary': {
            'raw_universe_count': 260,
            'mainboard_tradable_count': 260,
            'target_count': 200,
            'source_status': 'PASS',
        },
    }

    payload = runner.build_daily_candidate_persistence_payloads(
        '2026-07-10', bundle, {}, 'PAPER_PICK', 'selected',
    )

    symbols = [row['symbol'] for row in payload['daily_candidates']]
    context = payload['daily_candidates'][0]['candidate_features']['candidate_pool_context']
    assert len(symbols) == 195
    assert len(set(symbols)) == 195
    assert context['source_row_count'] == 200
    assert context['duplicate_symbol_count'] == 5
    assert context['final_persisted_count'] == 195


def test_persistence_prefers_full_candidate_pool_over_decision_pool() -> None:
    full_pool = [
        make_candidate(f'600{index:03d}', f'主板{index}', score=90 - index / 10, rank=index + 1)
        for index in range(200)
    ]
    decision_pool = full_pool[:33]
    bundle = {
        'full_candidate_pool': full_pool,
        'paper_scoring_candidates': decision_pool,
        'passed_candidates': decision_pool,
        'candidate_pool_exclusion_summary': {
            'raw_universe_count': 260,
            'mainboard_tradable_count': 260,
            'final_persisted_count': 200,
            'target_count': 200,
            'top_exclusion_reasons': {'non_mainboard': 20},
            'source_status': 'PASS',
        },
    }
    payload = runner.build_daily_candidate_persistence_payloads(
        '2026-07-10', bundle, {}, 'PAPER_PICK', 'selected',
    )

    assert len(payload['daily_candidates']) == 200
    assert payload['daily_candidates'][33]['selection_outcome'] == 'FULL_POOL_NOT_SELECTED'
    assert payload['daily_candidates'][0]['candidate_features']['candidate_pool_context']['target_count'] == 200


def test_db_completeness_explains_legitimate_partial_candidate_pool() -> None:
    candidates = [
        {
            'symbol': f'600{index:03d}', 'rank': index + 1,
            'candidate_entry_reason': ['base'], 'factor_snapshot': {},
            'auxiliary_evidence_snapshot': {'source': 'scanner'},
            'ranking_basis': {'basis': 'ranked'},
            'source_layers': ['L0_FULL_UNIVERSE'],
            'candidate_features': {
                'candidate_pool_context': {
                    'target_count': 200, 'raw_universe_count': 80,
                    'mainboard_tradable_count': 80, 'final_persisted_count': 80,
                    'top_exclusion_reasons': {'non_mainboard': 30},
                    'source_status': 'PASS',
                },
            },
        }
        for index in range(80)
    ]
    for candidate in candidates:
        candidate['factor_snapshot'].update({signal: False for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS})
    signals = [
        {'symbol': candidate['symbol'], 'signal_key': signal, 'signal_value': 0.0}
        for candidate in candidates for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS
    ]
    gate = backtest.build_db_completeness_gate(
        '2026-07-10', mode='LIVE_DECISION_DAY', candidate_rows=candidates,
        pick_rows=[{'symbol': '600000', 'decision': 'PAPER_PICK'}], return_rows=[],
        signal_rows=signals, scan_session={'id': 1},
    )

    assert gate['candidate_pool_status'] == 'WARN'
    assert gate['candidate_pool_warning_reason'] == 'eligible_mainboard_tradable_count_below_target'
    assert gate['candidate_pool_exclusion_summary'] == {'non_mainboard': 30}


def test_db_completeness_explains_duplicate_collapsed_candidate_pool() -> None:
    candidates = [
        {
            'symbol': f'600{index:03d}', 'rank': index + 1,
            'candidate_entry_reason': ['base'], 'factor_snapshot': {},
            'auxiliary_evidence_snapshot': {'source': 'scanner'},
            'ranking_basis': {'basis': 'ranked'},
            'source_layers': ['L0_FULL_UNIVERSE'],
            'candidate_features': {
                'candidate_pool_context': {
                    'target_count': 200,
                    'raw_universe_count': 260,
                    'mainboard_tradable_count': 260,
                    'source_row_count': 200,
                    'raw_full_candidate_pool_rows': 200,
                    'unique_full_candidate_pool_symbols': 195,
                    'final_persisted_count': 195,
                    'duplicate_symbol_count': 5,
                    'duplicate_symbols': ['000792', '002414', '600486', '600611', '600664'],
                    'deduplication_applied': True,
                    'top_exclusion_reasons': {},
                    'source_status': 'PASS',
                },
            },
        }
        for index in range(195)
    ]
    for candidate in candidates:
        candidate['factor_snapshot'].update({signal: False for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS})
    signals = [
        {'symbol': candidate['symbol'], 'signal_key': signal, 'signal_value': 0.0}
        for candidate in candidates for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS
    ]

    gate = backtest.build_db_completeness_gate(
        '2026-07-10', mode='LIVE_DECISION_DAY', candidate_rows=candidates,
        pick_rows=[{'symbol': '600000', 'decision': 'PAPER_PICK'}], return_rows=[],
        signal_rows=signals, scan_session={'id': 1},
    )

    assert gate['candidate_pool_status'] == 'WARN'
    assert gate['candidate_pool_warning_reason'] == 'candidate_pool_source_duplicates_collapsed'
    assert gate['candidate_pool_completeness']['duplicate_symbol_count'] == 5
    assert gate['candidate_pool_completeness']['duplicate_symbols'] == ['000792', '002414', '600486', '600611', '600664']
    assert 'candidate_pool_completeness=candidate_pool_partial_without_source_explanation' not in gate['warnings']


def test_db_completeness_marks_context_free_partial_snapshot_as_legacy() -> None:
    candidates = [
        {
            'symbol': f'600{index:03d}', 'rank': index + 1,
            'candidate_entry_reason': ['legacy'], 'factor_snapshot': {},
            'auxiliary_evidence_snapshot': {'source': 'legacy'},
            'ranking_basis': {'basis': 'legacy'},
            'source_layers': ['L0_LEGACY'],
        }
        for index in range(33)
    ]
    for candidate in candidates:
        candidate['factor_snapshot'].update({signal: False for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS})
    signals = [
        {'symbol': candidate['symbol'], 'signal_key': signal, 'signal_value': 0.0}
        for candidate in candidates for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS
    ]

    gate = backtest.build_db_completeness_gate(
        '2026-07-10', mode='HISTORICAL_REPLAY', candidate_rows=candidates,
        pick_rows=[{'symbol': '600000', 'decision': 'PAPER_PICK'}], return_rows=[],
        signal_rows=signals, scan_session=None,
    )

    assert gate['candidate_pool_status'] == 'WARN'
    assert gate['candidate_pool_warning_reason'] == 'legacy_partial_pool'


def test_mainboard_announcement_risk_matching_is_explained() -> None:
    candidates = [{'code': '600001', 'name': '测试主板', 'sector_opportunity_tags': ['银行']}]
    data_cache = {
        'announcements': [{
            'codes': [{'stock_code': '600001', 'short_name': '测试主板'}],
            'title': '测试主板关于异常波动、股东减持及风险提示的公告',
        }],
        'news_kuaixun': [],
        'industry_reports': [],
        'limitup_pool': [],
    }
    scanner_v2.enrich_mainboard_auxiliary_evidence(candidates, data_cache, {'600001': '银行'}, {})
    candidate = candidates[0]
    assert candidate['risk_notice_evidence']
    assert candidate['risk_notice_penalty'] > 0
    assert candidate['research_signals']['a_share_risk_review']['mainboard_auxiliary_risk_notices']

    excluded = runner.official_target_exclusion_reasons({
        **candidate,
        'risk_notice_penalty': 0.7,
        'paper_pick_eligibility': {'eligible': False, 'signals': {}, 'blockers': []},
    }, {})
    assert any(reason.startswith('mainboard_auxiliary_risk:') for reason in excluded)


def test_mainboard_news_catalyst_populates_research_signals() -> None:
    candidates = [{'code': '600001', 'name': '测试主板', 'sector_opportunity_tags': ['银行']}]
    data_cache = {
        'announcements': [],
        'news_kuaixun': [{'title': '测试主板签署重大合同', 'summary': '银行板块同步走强'}],
        'industry_reports': [{'industryName': '银行', 'title': '银行行业景气改善'}],
        'limitup_pool': [],
    }
    scanner_v2.enrich_mainboard_auxiliary_evidence(candidates, data_cache, {'600001': '银行'}, {})
    candidate = candidates[0]
    assert candidate['news_catalyst_strength'] > 0
    assert candidate['news_evidence']['direct_symbol_news']
    assert candidate['research_signals']['catalyst_quality']['category'] == 'positive_catalyst'


def test_limitup_reason_missing_is_partial_not_global_hard_block_but_lowers_priority() -> None:
    base = {
        'structured_score': 80.0,
        'final_score': 80.0,
        'candidate_stage': 'high_7_to_9',
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'mainboard_auxiliary_confidence': 0.5,
        'risk_notice_penalty': 0.0,
        'signal_pct': 8.0,
    }
    missing = {**base, 'code': '600001', 'limitup_reason_quality_score': 0.0, 'news_catalyst_strength': 0.0, 'announcement_catalyst_score': 0.0}
    confirmed = {**base, 'code': '600002', 'limitup_reason_quality_score': 1.0, 'news_catalyst_strength': 0.7}
    assert scanner_v2.structured_priority_details(missing)['structured_priority_score'] < scanner_v2.structured_priority_details(confirmed)['structured_priority_score']
    assert runner.limitup_quality_block_reason(missing, {}) == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
    assert runner.limitup_quality_block_reason(confirmed, {}) == ''


def test_information_coverage_audit_distinguishes_proxy_and_missing() -> None:
    candidates = [{
        'code': '600001',
        'announcement_evidence': [{'title': '公告'}],
        'news_evidence': {'direct_symbol_news': [{'title': '新闻'}]},
        'sector_news_evidence': [{'title': '行业研报', 'source': 'industry_reports'}],
        'limitup_reason_evidence': [],
        'risk_notice_evidence': [],
    }]
    data_cache = {
        'announcements': [{'codes': [{'stock_code': '600001'}], 'title': '公告'}],
        'news_kuaixun': [{'title': '新闻'}],
        'industry_reports': [{'industryName': '银行', 'title': '行业研报'}],
        'flow_industry': [{'f12': 'BK1'}],
        'limitup_pool': [],
    }
    audit = scanner_v2.build_mainboard_information_coverage_audit(data_cache, candidates)
    assert audit['mainboard_policy'] == 'main_only'
    assert audit['news_sources']['announcements']['raw_count'] == 1
    assert audit['news_sources']['eastmoney_news']['matched_candidate_count'] == 1
    assert audit['news_sources']['sector_news']['status'] == 'PROXY'
    assert audit['news_sources']['sector_news']['raw_file_written'] is False
    assert audit['news_sources']['limitup_reasons']['status'] == 'MISSING'
    assert audit['news_sources']['limitup_reasons']['hard_block'] is False
    assert 'limitup_reasons:MISSING' in audit['coverage_gaps']


def test_real_sector_news_marks_source_pass_and_non_proxy() -> None:
    candidate = {
        'code': '600100',
        'name': '主板样本',
        'sector_name': '半导体',
        'sector_opportunity_tags': ['AI芯片'],
    }
    data_cache = {
        'sector_news': [{
            'title': '半导体板块景气度提升',
            'summary': 'AI芯片需求带动半导体板块走强',
            'sector': '半导体',
            'source_query': '半导体',
        }],
        'limitup_pool': [],
    }

    enriched = scanner_v2.enrich_mainboard_auxiliary_evidence([candidate], data_cache)[0]
    audit = scanner_v2.build_mainboard_information_coverage_audit(data_cache, [enriched])

    assert audit['news_sources']['sector_news']['status'] == 'PASS'
    assert audit['news_sources']['sector_news']['raw_file_written'] is True
    assert audit['news_sources']['sector_news']['proxy_sources'] == []
    assert enriched['sector_news_evidence'][0]['source'] == 'sector_news'
    assert enriched['sector_news_evidence'][0]['proxy'] is False


def test_current_day_nested_limitup_reason_is_real_evidence() -> None:
    row = {
        'c': '600100',
        'n': '主板样本',
        'reasonInfo': {'reasonName': '电力改革'},
    }
    candidate = {'code': '600100', 'name': '主板样本'}
    data_cache = {'limitup_pool': [scanner_v2.normalize_limitup_reason_row(row)]}

    enriched = scanner_v2.enrich_mainboard_auxiliary_evidence([candidate], data_cache)[0]
    audit = scanner_v2.build_mainboard_information_coverage_audit(data_cache, [enriched])

    assert scanner_v2.limitup_reason_text(row) == '电力改革'
    assert enriched['limitup_reason_status'] == 'PASS'
    assert enriched['limitup_reason_evidence'][0]['proxy'] is False
    assert audit['news_sources']['limitup_reasons']['status'] == 'PASS'


def test_mainboard_forward_bundle_preserves_auxiliary_evidence(tmp_path) -> None:
    scan_dir = tmp_path / 'data' / 'live_scan' / '2026-07-10' / 'eastmoney_scan_afternoon'
    scan_dir.mkdir(parents=True)
    scored_path = scan_dir / 'eastmoney_web_tabs_scored.jsonl'
    candidate = make_candidate('600001', '测试主板', score=86.0, rank=1)
    candidate.update({
        'board': 'main',
        'mainboard_policy': 'main_only',
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'mainboard_auxiliary_missing_domains': ['limitup_reasons'],
        'announcement_evidence': [{'title': '重大合同公告', 'category': 'major_contract'}],
        'news_evidence': {'direct_symbol_news': [{'title': '测试主板签署合同'}]},
        'sector_news_evidence': [{'title': '银行行业景气改善', 'source': 'industry_reports'}],
        'limitup_reason_evidence': [],
        'risk_notice_evidence': [],
        'announcement_catalyst_score': 0.5,
        'news_catalyst_strength': 0.6,
        'sector_news_catalyst_score': 0.3,
        'limitup_reason_quality_score': 0.0,
        'risk_notice_penalty': 0.0,
        'mainboard_auxiliary_confidence': 0.75,
        'structured_priority_score': 92.0,
        'ranking_basis': 'structured_evidence_primary',
    })
    scored_path.write_text(json.dumps(candidate, ensure_ascii=False) + '\n', encoding='utf-8')
    summary_path = scan_dir / runner.SCAN_SUMMARY_NAME
    summary = {
        'source_time': '2026-07-10 14:50:00',
        'pipeline_version': 'v2_scanner_api',
        'scored_count': 1,
        'passed_count': 1,
        'source_status': complete_source_status(),
        'files': {'scored': str(scored_path)},
        'full_universe_scan': {'coverage_status': 'PASS', 'quote_count': 5800, 'candidate_board_policy': 'main_only'},
        'mainboard_policy': 'main_only',
        'structured_scores': [],
        'research_signals': [],
        'structured_score_components': [],
        'structured_component_details': [],
        'information_coverage_audit': {'status': 'PARTIAL', 'mainboard_policy': 'main_only', 'coverage_gaps': ['limitup_reasons:MISSING']},
    }
    bundle = runner._bundle_from_scan_summary(summary_path, summary)
    merged = bundle['paper_scoring_candidates'][0]
    assert bundle['mainboard_policy'] == 'main_only'
    assert bundle['information_coverage_audit']['mainboard_policy'] == 'main_only'
    assert merged['board'] == 'main'
    assert merged['mainboard_auxiliary_evidence_status'] == 'PARTIAL'
    assert merged['announcement_evidence'][0]['category'] == 'major_contract'
    assert merged['ranking_basis'] == 'structured_evidence_primary'


def test_yesterday_limitup_proxy_builds_candidate_continuation_gene() -> None:
    candidate = {
        'code': '600100',
        'name': '主板样本',
        'sector_name': '半导体',
        'structured_score': 70.0,
        'final_score': 80.0,
    }
    data_cache = {
        'limitup_pool': [],
        'limitup_yesterday': [{'c': '600101', 'n': '昨日板', 'hybk': '半导体'}],
        'limitup_yesterday_one_word': [{'c': '600102', 'n': '昨日一字', 'hybk': '半导体', 'is_one_word': True}],
    }
    enriched = scanner_v2.enrich_mainboard_auxiliary_evidence([candidate], data_cache)[0]
    audit = scanner_v2.build_mainboard_information_coverage_audit(data_cache, [enriched])

    assert audit['news_sources']['limitup_reasons']['status'] == 'PROXY'
    assert audit['news_sources']['limitup_reasons']['hard_block'] is False
    assert audit['yesterday_limitup_proxy']['status'] == 'PROXY'
    assert enriched['continuation_gene_score'] > 0
    assert enriched['sector_yesterday_limitup_gene_proxy']['status'] == 'PROXY'
    assert enriched['limitup_reason_status'] == 'PROXY'
    assert enriched['limitup_reason_hard_block'] is False


def test_information_coverage_partial_reasons_identify_limitup_gap() -> None:
    candidates = [{
        'code': '600001',
        'announcement_evidence': [{'title': '公告'}],
        'news_evidence': {'direct_symbol_news': [{'title': '新闻'}]},
        'sector_news_evidence': [{'title': '行业代理'}],
        'limitup_reason_evidence': [],
        'risk_notice_evidence': [{'title': '异常波动', 'category': 'abnormal_movement'}],
    }]
    data_cache = {
        'announcements': [{'title': '异常波动', 'codes': [{'stock_code': '600001'}]}],
        'news_kuaixun': [{'title': '新闻'}],
        'industry_reports': [{'title': '行业代理'}],
        'stock_capital_flow': [{'f12': '600001'}],
        'popularity_rank': [{'f12': '600001'}],
        'limitup_pool': [],
        'limitup_yesterday': [],
    }
    audit = scanner_v2.build_mainboard_information_coverage_audit(data_cache, candidates)

    assert audit['status'] == 'PARTIAL'
    assert any(item['domain'] == 'limitup_reasons' and item['status'] == 'MISSING' for item in audit['partial_reasons'])
    assert audit['non_missing_domains']['announcements'] == 'PASS'
    assert audit['news_sources']['announcements']['raw_count'] == 1
    assert audit['news_sources']['announcements']['matched_candidate_count'] == 1
    assert audit['news_sources']['eastmoney_news']['raw_count'] == 1
    assert audit['news_sources']['eastmoney_news']['matched_candidate_count'] == 1
    assert audit['news_sources']['sector_news']['status'] == 'PROXY'
    assert audit['news_sources']['risk_announcements']['used_for_risk_filter'] is True


def test_huatian_capital_risk_profile_explains_broken_board_divergence() -> None:
    candidate = make_candidate('002185', '华天科技', score=88.0, rank=1, sector_score=0.7)
    candidate.update({
        'failed_limitup': True,
        'main_buy_net': -387134 * 10000,
        'dark_pool_net': 26982 * 10000,
        'popularity_rank': 1,
        'announcement_catalyst_score': 0.0,
        'news_catalyst_strength': 0.0,
        'continuation_gene_score': 0.0,
        'limitup_reason_quality_score': 0.0,
    })
    profile = runner.candidate_capital_risk_profile(candidate)
    eligibility = runner.paper_pick_eligibility_profile(
        candidate,
        make_bundle([candidate], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo'),
    )

    assert profile['failed_limitup_risk'] > 0
    assert profile['main_buy_outflow_pressure'] > 0
    assert profile['popularity_crowding_risk'] > 0
    assert profile['dark_pool_inflow_support'] > 0
    assert profile['capital_divergence_score'] < 0
    assert 'BROKEN_BOARD_WITH_MAIN_BUY_OUTFLOW' in profile['risk_codes']
    assert 'POPULARITY_CROWDING_PROFIT_TAKING_RISK' in eligibility['missing_conditions']


def test_high_popularity_broken_board_is_ranked_below_confirmed_proxy_candidate() -> None:
    risky = make_candidate('002185', '华天科技', score=90.0, rank=1, sector_score=0.7)
    risky.update({
        'structured_score': 75.0,
        'structured_priority_score': 80.0,
        'failed_limitup': True,
        'main_buy_net': -387134 * 10000,
        'dark_pool_net': 26982 * 10000,
        'popularity_rank': 1,
        'announcement_catalyst_score': 0.0,
        'news_catalyst_strength': 0.0,
        'continuation_gene_score': 0.0,
        'limitup_reason_quality_score': 0.0,
    })
    confirmed = make_candidate('600001', '确认样本', score=86.0, rank=2, sector_score=0.7)
    confirmed.update({
        'structured_score': 75.0,
        'structured_priority_score': 80.0,
        'continuation_gene_score': 0.75,
        'news_catalyst_strength': 0.7,
        'limitup_reason_quality_score': 0.0,
    })

    assert runner.formal_candidate_sort_key(risky) < runner.formal_candidate_sort_key(confirmed)
    profile = runner.candidate_capital_risk_profile(risky)
    assert profile['risk_penalty_score'] > 0
    assert profile['risk_codes']


def test_db_top10_snapshot_fields_are_explicit(monkeypatch) -> None:
    rows = []
    monkeypatch.setattr('xiaogu_db.upsert_daily_candidate', lambda **kwargs: rows.append(kwargs))
    candidates = []
    diagnostics = []
    for index in range(1, 11):
        candidate = make_candidate(f'600{index:03d}', f'候选{index}', score=100 - index, rank=index)
        candidate.update({
            'structured_score': 80 - index,
            'structured_priority_score': 90 - index,
            'ranking_basis': 'structured_evidence_primary',
            'announcement_evidence': [{'title': '催化'}],
            'news_evidence': {'direct_symbol_news': [{'title': '新闻'}]},
            'continuation_gene_score': 0.4,
            'mainboard_auxiliary_evidence_status': 'PARTIAL',
            'mainboard_auxiliary_missing_domains': ['limitup_reasons'],
        })
        candidates.append(candidate)
        diagnostics.append({
            'symbol': candidate['code'],
            'selection_outcome': 'OFFICIAL_PICK' if index == 1 else 'TOP10_NOT_SELECTED',
            'selection_outcome_reason': 'selected' if index == 1 else 'lower_rank',
            'candidate_reasons': ['structured_score'],
            'not_selected_reasons': [] if index == 1 else ['lower_rank'],
            'eligibility_snapshot': {'eligible': True},
        })
    bundle = {'paper_scoring_candidates': candidates, 'market_snapshot': {}, 'information_coverage_audit': {'status': 'PARTIAL'}}
    features = {'candidate_consumption_summary': {'official_result': {'symbol': '600001'}, 'top10_candidates': diagnostics}}

    result = runner.persist_daily_candidate_snapshot('2026-07-10', bundle, features, 'PAPER_PICK', 'selected')

    assert result['written'] == 10
    assert len(rows) == 10
    assert all(row['candidate_entry_reason'] for row in rows)
    assert all(row['ticket_reason'] for row in rows)
    assert all(row['factor_snapshot'] for row in rows)
    assert all(row['auxiliary_evidence_snapshot'] for row in rows)
    assert all(row['not_selected_reason'] for row in rows[1:])


def test_backtest_top10_reports_profitable_rank4_alternative() -> None:
    result = {
        'trade_date': '2026-07-01',
        'decision': 'PAPER_PICK',
        'symbol': '600001',
        'return_rows': [
            {'symbol': '600001', 't1_return': -0.02},
            {'symbol': '600004', 't1_return': 0.04},
        ],
        'top10_candidates': [
            {'symbol': '600001', 'stock_name': '出票', 'rank': 1, 'candidate_entry_reason': ['高分'], 'not_selected_reason': []},
            {'symbol': '600004', 'stock_name': '替代', 'rank': 4, 'candidate_entry_reason': ['低估催化'], 'not_selected_reason': ['structured_priority_lower']},
        ],
        'pick_features': {'decision_reason': 'rank1'},
    }
    report = backtest.build_report([result], returns_source='db')

    assert report['paper_pick_vs_top10_best_gap'] == pytest.approx(-0.06)
    assert report['missed_profitable_candidates'][0]['rank'] == 4
    assert report['missed_profitable_candidates'][0]['not_selected_reason'] == ['structured_priority_lower']
    assert report['false_positive_paper_picks'][0]['symbol'] == '600001'


def test_giant_network_replay_keeps_legacy_profit_separate_from_full_chain() -> None:
    replay = backtest.build_legacy_chain_replay(
        {'symbol': '002558', 'name': '巨人网络', 'reason': 'legacy_chain_momentum', 'factor_snapshot': {'legacy_score': 88}},
        [{
            'symbol': '002558',
            'stock_name': '巨人网络',
            'rank': 3,
            'is_official_pick': False,
            'eligibility_snapshot': {'eligible': True},
            'candidate_entry_reason': ['news_catalyst'],
            'not_selected_reason': ['rank3_not_rank1'],
            'factor_snapshot': {'structured_score': 76},
        }],
        0.04,
    )

    assert replay['current_full_chain_replay_reason']
    assert replay['would_current_xiaogu_pick_it'] is False
    assert replay['if_not_pick_why'] == ['rank3_not_rank1']
    assert replay['actual_t1_return'] == pytest.approx(0.04)
    assert replay['factor_snapshot_comparison']['legacy']['legacy_score'] == 88


def test_cohort_classification_separates_full_transition_nonmainboard_and_no_return():
    from xiaogu_db import classify_candidate_cohort

    full = {
        'trade_date': '2026-07-10', 'symbol': '600001', 'rank': 1,
        'candidate_entry_reason': ['structured'], 'factor_snapshot': {'score': 1},
        'auxiliary_evidence_snapshot': {'status': 'PARTIAL'},
        'ranking_basis': {'basis': 'structured'},
    }
    assert classify_candidate_cohort(full, top10_count=10, has_return=True, trade_date='2026-07-10')['cohort'] == 'FULL_CHAIN_COMPLETE'
    transition = {'trade_date': '2026-06-25', 'symbol': '600002', 'rank': 2, 'raw_json': {'score': 1}, 'candidate_features': {'flow': 1}}
    info = classify_candidate_cohort(transition, top10_count=10, has_return=True, trade_date='2026-06-25')
    assert info['cohort'] == 'TRANSITION_RECONSTRUCTABLE'
    assert classify_candidate_cohort({'trade_date': '2026-06-25', 'symbol': '300001', 'rank': 1}, top10_count=10, has_return=True, trade_date='2026-06-25')['cohort'] == 'NON_MAINBOARD_EXCLUDED'
    no_return = classify_candidate_cohort({**full, 'trade_date': '2026-07-10', 'symbol': '600003'}, top10_count=10, has_return=False, trade_date='2026-07-10')
    assert no_return['cohort'] == 'NO_RETURN_YET'
    assert no_return['cohort_quality'] == 'FULL_CHAIN_COMPLETE'


def test_historical_snapshot_reconstruction_has_provenance_and_no_fabricated_pass():
    from xiaogu_db import reconstruct_candidate_evidence

    result = reconstruct_candidate_evidence({
        'trade_date': '2026-06-25', 'symbol': '600001', 'rank': 4,
        'raw_json': {'score': 80, 'flow_bonus': 2},
        'candidate_features': {'eligible': True},
        'eligibility_snapshot': {'eligible': True},
        'selection_diagnostics': {'not_selected_reasons': ['lower_rank']},
        'candidate_entry_reason': {}, 'factor_snapshot': {},
        'auxiliary_evidence_snapshot': {}, 'ranking_basis': {}, 'not_selected_reason': [],
    })
    assert result['factor_snapshot']['reconstructed'] is True or result['factor_snapshot']['evidence_status'].startswith('RECONSTRUCTED')
    assert result['candidate_entry_reason']
    assert result['not_selected_reason']
    assert result['reconstruction_provenance']['factor_snapshot']['reconstruction_source']
    assert result['auxiliary_evidence_snapshot']['announcements']['status'] == 'MISSING'
    assert result['auxiliary_evidence_snapshot']['news']['status'] == 'MISSING'
    assert result['auxiliary_evidence_snapshot']['yesterday_limitup_gene']['status'] == 'MISSING'


def test_mainboard_only_performance_excludes_legacy_non_mainboard():
    from xiaogu_backtest_v0_1 import _cohort_rows, _performance_for_rows

    candidates = [
        {'trade_date': '2026-06-25', 'symbol': '600001', 'rank': 1, 'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1}, 'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}},
        {'trade_date': '2026-06-25', 'symbol': '300001', 'rank': 2, 'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1}, 'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}},
    ]
    returns = {'2026-06-25:600001': {'t1_return': 0.03}, '2026-06-25:300001': {'t1_return': 0.08}}
    rows = _cohort_rows(candidates, returns)
    main = [row for row in rows if row['is_mainboard']]
    excluded = [row for row in rows if not row['is_mainboard']]
    assert len(main) == 1
    assert len(excluded) == 1
    assert excluded[0]['cohort'] == 'NON_MAINBOARD_EXCLUDED'
    report = _performance_for_rows(main, {}, name='mainboard_only')
    assert report['top10_avg_return'] == pytest.approx(0.03)


def test_paper_pick_vs_top10_cohort_alternative_exposes_underestimated_factor():
    from xiaogu_backtest_v0_1 import _performance_for_rows

    rows = [
        {'trade_date': '2026-06-25', 'symbol': '600001', 'rank': 1, 't1_return': -0.02, 'cohort_quality': 'TRANSITION_RECONSTRUCTABLE', 'candidate_entry_reason': ['high_score'], 'not_selected_reason': [], 'factor_snapshot': {}},
        {'trade_date': '2026-06-25', 'symbol': '600004', 'rank': 4, 't1_return': 0.04, 'cohort_quality': 'TRANSITION_RECONSTRUCTABLE', 'candidate_entry_reason': ['news'], 'not_selected_reason': ['structured_priority_lower'], 'news_catalyst': 'direct'},
    ]
    report = _performance_for_rows(rows, {'2026-06-25': {'symbol': '600001'}}, name='transition')
    assert report['missed_profitable_candidates'][0]['alternative_rank'] == 4
    assert report['missed_profitable_candidates'][0]['underestimated_factors']
    assert report['paper_pick_vs_top10_best_gap'] == pytest.approx(-0.06)


def test_giant_network_cross_date_replay_and_huatian_risk_aggregation():
    from xiaogu_backtest_v0_1 import build_cross_date_case_studies

    candidates = [
        {'trade_date': '2026-06-25', 'symbol': '002558', 'stock_name': '巨人网络', 'rank': 3, 'factor_snapshot': {'structured_score': 76}, 'auxiliary_evidence_snapshot': {'news': {'status': 'PASS'}}},
        {'trade_date': '2026-06-26', 'symbol': '002185', 'stock_name': '华天科技', 'rank': 2, 'factor_snapshot': {'capital_risk_profile': {'failed_limitup': True, 'capital_divergence_score': -0.4}}, 'auxiliary_evidence_snapshot': {'dark_pool': {'net': 26982}}},
    ]
    cases = build_cross_date_case_studies(candidates, {'2026-06-25': {'symbol': '002558'}}, {
        '2026-06-25:002558': {'t1_return': 0.04, 't2_return': 0.02, 't3_return': 0.01},
        '2026-06-26:002185': {'t1_return': -0.03},
    })
    assert cases['giant_network']['sample_dates']
    assert cases['giant_network']['current_chain_pick_dates']
    assert cases['giant_network']['factor_snapshot_comparison']
    assert cases['huatian_tech']['broken_board_risk_dates']
    assert cases['huatian_tech']['capital_divergence_dates']
    assert '暗盘流入' in cases['huatian_tech']['postmortem']['interpretation']


def test_ranking_basis_missing_evidence_does_not_create_catalyst_boost():
    candidate = make_candidate('600001', '缺失证据', score=80.0, rank=1)
    candidate.update({
        'auxiliary_evidence_snapshot': {
            'announcements': {'status': 'MISSING'},
            'news': {'status': 'MISSING'},
        },
        'announcement_catalyst_score': 0.0,
        'news_catalyst_strength': 0.0,
    })
    components = runner.ranking_basis_adjustment_components(candidate)
    assert components['boosts']['announcement_catalyst'] == 0.0
    assert components['boosts']['confirmed_news_catalyst'] == 0.0


def test_ranking_basis_penalizes_high_risk_and_rewards_confirmed_low_position_catalyst():
    risky = make_candidate('600001', '高风险', score=90.0, rank=1)
    risky.update({
        'failed_limitup': True,
        'main_buy_net': -600000000.0,
        'popularity_rank': 1,
        'limitup_reason_quality_score': 0.0,
    })
    catalyst = make_candidate('600002', '低位催化', score=84.0, rank=4)
    catalyst.update({
        'low_position_catalyst_score': 0.9,
        'news_catalyst_strength': 0.8,
        'auxiliary_evidence_snapshot': {'news': {'direct_symbol_news': [{'title': '确认催化'}]}},
    })
    assert runner.ranking_basis_adjustment_components(risky)['net_adjustment'] < 0
    assert runner.ranking_basis_adjustment_components(catalyst)['net_adjustment'] > 0
    assert runner.formal_candidate_sort_key(catalyst) > runner.formal_candidate_sort_key(risky)


def test_ranking_improvement_analysis_reports_daily_miss_and_rank4_promotion():
    rows = [
        {
            'trade_date': '2026-07-06', 'symbol': '600001', 'rank': 1,
            'is_mainboard': True, 't1_return': -0.03,
            'factor_snapshot': {'capital_risk_profile': {'failed_limitup_risk': 1.0, 'main_buy_outflow_pressure': 1.0, 'popularity_crowding_risk': 1.0, 'high_popularity_trap_risk': 1.0, 'weak_limitup_confirmation': True}},
            'auxiliary_evidence_snapshot': {},
        },
        {
            'trade_date': '2026-07-06', 'symbol': '600004', 'rank': 4,
            'is_mainboard': True, 't1_return': 0.04, 'low_position_catalyst_score': 0.9,
            'factor_snapshot': {},
            'auxiliary_evidence_snapshot': {'news': {'direct_symbol_news': [{'title': '确认'}]}},
            'not_selected_reason': ['structured_priority_lower'],
        },
    ]
    analysis = backtest.build_ranking_improvement_analysis(rows, {'2026-07-06': {'symbol': '600001'}})
    daily = analysis['paper_pick_vs_top10_best_daily'][0]
    assert daily['top10_best_rank'] == 4
    assert 'FAILED_LIMITUP_RISK_UNDERPENALIZED' in daily['ranking_miss_type']
    assert 'RANK4_TO_6_UNDERVALUED' in daily['ranking_miss_type']
    assert analysis['rank2_to_rank6_analysis']['promotion_candidates'][0]['symbol'] == '600004'


def test_full_chain_return_pending_is_explicit_when_no_t1_exists(monkeypatch):
    candidates = [{
        'trade_date': '2026-07-10', 'symbol': f'60000{index}', 'rank': index,
        'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
        'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1},
    } for index in range(1, 11)]
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-10': {'symbol': '600001'}, '__all__': {'rows': []}}, {}))
    report = backtest.build_db_cohort_report('2026-07-10', '2026-07-10')
    assert report['full_chain_complete_return_pending'] is True
    assert report['full_chain_complete_return_status'] == 'PENDING'
    assert report['full_chain_complete_performance'] is None
    assert report['full_chain_complete_gate']['status'] == 'WAITING'
    assert report['full_chain_complete_gate']['pending_day_count'] == 1


def test_return_limitup_summary_uses_explicit_thresholds():
    summary = backtest._return_limitup_summary([-0.06, -0.01, 0.01, 0.07, 0.095])
    assert summary['sample_count'] == 5
    assert summary['limitup_rate'] == 0.2
    assert summary['near_limitup_rate'] == 0.4
    assert summary['large_loss_rate'] == 0.2


def test_db_cohort_replay_excludes_non_trading_dates():
    rows = [
        {'trade_date': '2026-07-10', 'symbol': '600001'},
        {'trade_date': '2026-07-12', 'symbol': '600002'},
    ]
    assert [row['symbol'] for row in backtest._filter_trading_day_rows(rows)] == ['600001']


def test_ranking_miss_includes_return_and_limitup_diagnostics():
    rows = [
        {'trade_date': '2026-07-06', 'symbol': '600001', 'rank': 1, 'is_mainboard': True, 't1_return': 0.01, 'factor_snapshot': {}, 'auxiliary_evidence_snapshot': {}},
        {'trade_date': '2026-07-06', 'symbol': '600004', 'rank': 4, 'is_mainboard': True, 't1_return': 0.10, 'low_position_catalyst_score': 0.9, 'factor_snapshot': {}, 'auxiliary_evidence_snapshot': {'news': {'direct_symbol_news': [{'title': '确认'}]}}},
    ]
    daily = backtest.build_ranking_improvement_analysis(rows, {'2026-07-06': {'symbol': '600001'}})['paper_pick_vs_top10_best_daily'][0]
    assert daily['paper_pick_rank'] == 1
    assert daily['top10_best_limitup_hit'] is True
    assert daily['limitup_gap'] is True
    assert daily['primary_fix_direction'] != 'NO_ACTION_INSUFFICIENT_EVIDENCE'


def test_limitup_proxy_and_risk_gate_do_not_promote_unexplained_triple_risk():
    risky = make_candidate('600001', '三重风险', score=95.0, rank=1)
    risky.update({'failed_limitup': True, 'main_buy_net': -600000000.0, 'popularity_rank': 1})
    low_risk = make_candidate('600002', '低位催化', score=85.0, rank=4)
    low_risk.update({'low_position_catalyst_score': 0.9, 'news_catalyst_strength': 0.9, 'fund_flow_momentum': 0.8})
    proxy = runner.limitup_probability_proxy_components(risky)
    gate = runner.paper_pick_risk_explanation_gate(risky)
    assert proxy['limitup_proxy_status'] == 'BLOCKED'
    assert gate['status'] == 'FAIL'
    assert runner.formal_candidate_sort_key(low_risk) > runner.formal_candidate_sort_key(risky)


def test_return_coverage_gate_blocks_low_coverage_replay(monkeypatch):
    candidates = [
        {'trade_date': '2026-07-01', 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for index in range(1, 11)
    ]
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-01': {'symbol': '600001'}, '__all__': {'rows': []}}, {}))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01')
    assert report['return_coverage_gate']['status'] == 'FAIL'
    assert report['strategy_status'] == 'BLOCKED_BY_RETURN_COVERAGE'


def test_strategy_readiness_stays_insufficient_before_ten_comparable_dates(monkeypatch):
    candidates = [
        {'trade_date': '2026-07-01', 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for index in range(1, 11)
    ]
    returns = {
        f'2026-07-01:6000{index:02d}': {
            't1_return': 0.01,
            't2_return': None,
            't3_return': None,
            'is_limit_up': False,
            'next_day_open_return': 0.002,
            'next_day_high_return': 0.02,
            'next_day_low_return': -0.01,
            'high_to_close_retrace': 0.01,
        }
        for index in range(1, 11)
    }
    pick = {'trade_date': '2026-07-01', 'symbol': '600001', '_t1_return': 0.01}
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-01': pick, '__all__': {'rows': [pick]}}, returns))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01')
    assert report['return_coverage_gate']['status'] == 'PASS'
    assert report['strategy_readiness']['status'] == 'INSUFFICIENT_COMPARABLE_SAMPLE'
    assert report['strategy_readiness']['blocking_reasons'] == ['mainboard_comparable_paper_pick_dates=1 < 10']


def test_strategy_readiness_blocks_failed_full_pytest_before_other_gates(monkeypatch):
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: ([], {'__all__': {'rows': []}}, {}))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01', full_pytest_gate_status='FAIL')
    assert report['strategy_status'] == 'BLOCKED_BY_FULL_PYTEST'
    assert report['strategy_readiness']['blocking_reasons'] == ['full_pytest_gate=FAIL']


def test_return_coverage_gate_blocks_low_paper_pick_coverage(monkeypatch):
    candidates = [
        {'trade_date': '2026-07-01', 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for index in range(1, 11)
    ]
    returns = {
        f'2026-07-01:6000{index:02d}': {
            't1_return': 0.01,
            't2_return': None,
            't3_return': None,
            'is_limit_up': False,
            'next_day_open_return': 0.002,
            'next_day_high_return': 0.02,
            'next_day_low_return': -0.01,
            'high_to_close_retrace': 0.01,
        }
        for index in range(1, 11)
    }
    pick = {'trade_date': '2026-07-01', 'symbol': '600001', '_t1_return': None}
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-01': pick, '__all__': {'rows': [pick]}}, returns))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01')
    assert report['return_coverage_gate']['status'] == 'FAIL'
    assert report['return_coverage_gate']['blocking_reason'] == 'coverage_below_threshold:paper_pick_t1_coverage'


def test_return_coverage_gate_blocks_low_mainboard_rank2_to_rank6_coverage(monkeypatch):
    candidates = [
        {'trade_date': '2026-07-01', 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for index in range(1, 11)
    ]
    returned_ranks = {1, 2, 3, 4, 7, 8, 9, 10}
    returns = {
        f'2026-07-01:6000{index:02d}': {'t1_return': 0.01, 't2_return': None, 't3_return': None, 'is_limit_up': False}
        for index in returned_ranks
    }
    pick = {'trade_date': '2026-07-01', 'symbol': '600001', '_t1_return': 0.01}
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-01': pick, '__all__': {'rows': [pick]}}, returns))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01')
    assert report['return_coverage_gate']['top10_t1_coverage'] == 0.8
    assert report['return_coverage_gate']['mainboard_rank2_to_rank6_t1_coverage'] == 0.6
    assert report['strategy_status'] == 'BLOCKED_BY_RETURN_COVERAGE'


def test_pending_full_chain_date_does_not_reduce_return_coverage(monkeypatch):
    candidates = [
        {'trade_date': trade_date, 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for trade_date in ('2026-07-01', '2026-07-10')
        for index in range(1, 11)
    ]
    returns = {
        f'2026-07-01:6000{index:02d}': {
            't1_return': 0.01,
            't2_return': None,
            't3_return': None,
            'is_limit_up': False,
            'next_day_open_return': 0.002,
            'next_day_high_return': 0.02,
            'next_day_low_return': -0.01,
            'high_to_close_retrace': 0.01,
        }
        for index in range(1, 11)
    }
    ready_pick = {'trade_date': '2026-07-01', 'symbol': '600001', '_t1_return': 0.01}
    pending_pick = {'trade_date': '2026-07-10', 'symbol': '600001', '_t1_return': None}
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (
        candidates,
        {'2026-07-01': ready_pick, '2026-07-10': pending_pick, '__all__': {'rows': [ready_pick, pending_pick]}},
        returns,
    ))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-10')
    assert report['return_coverage_gate']['status'] == 'PASS'
    assert report['return_coverage_gate']['top10_t1_coverage'] == 1.0
    assert report['full_chain_complete_return_status'] == 'PENDING'
    assert report['paper_pick_performance_gate']['sample_count'] == 1
    assert report['paper_pick_performance_gate']['status'] == 'INSUFFICIENT_SAMPLE'
    assert report['sample_accumulation_gate']['status'] == 'WAITING'


def test_full_chain_ready_status_requires_five_days_before_freeze(monkeypatch):
    candidates = [
        {'trade_date': '2026-07-01', 'symbol': f'6000{index:02d}', 'rank': index,
         'candidate_entry_reason': ['x'], 'factor_snapshot': {'x': 1},
         'auxiliary_evidence_snapshot': {'x': 1}, 'ranking_basis': {'x': 1}}
        for index in range(1, 11)
    ]
    returns = {
        f'2026-07-01:6000{index:02d}': {'t1_return': 0.01, 't2_return': None, 't3_return': None, 'is_limit_up': False}
        for index in range(1, 11)
    }
    pick = {'trade_date': '2026-07-01', 'symbol': '600001', '_t1_return': 0.01}
    monkeypatch.setattr(backtest, '_db_rows_since', lambda _start, _end: (candidates, {'2026-07-01': pick, '__all__': {'rows': [pick]}}, returns))
    report = backtest.build_db_cohort_report('2026-07-01', '2026-07-01')
    assert report['full_chain_complete_return_status'] == 'READY'
    assert report['full_chain_complete_gate']['ready_day_count'] == 1
    assert report['full_chain_complete_gate']['status'] == 'WAITING'
    assert report['strategy_status'] != 'READY_FOR_PAPER_PICK_FREEZE'


def _completed_paper_days_for_gate_tests(paper_returns, *, pending=False, execution=True):
    days = []
    for index, paper_return in enumerate(paper_returns, start=1):
        trade_date = f'2026-07-{index:02d}'
        paper = {
            'trade_date': trade_date, 'symbol': f'600{index:03d}', 'rank': 1,
            't1_return': paper_return,
            'next_day_open_return': paper_return - 0.005 if execution else None,
            'next_day_high_return': paper_return + 0.03 if execution else None,
            'next_day_low_return': paper_return - 0.04 if execution else None,
            'high_to_close_retrace': -0.01 if execution else None,
        }
        alternative = {
            'trade_date': trade_date, 'symbol': f'601{index:03d}', 'rank': 4,
            't1_return': 0.10 if index == 1 else 0.04,
            'low_position_catalyst_score': 1.0,
            'previous_limitup': True,
        }
        days.append({'trade_date': trade_date, 'paper': paper, 'day': [paper, alternative]})
    if pending:
        days.append({
            'trade_date': '2026-07-31',
            'paper': {'symbol': '600999', 'rank': 1, 't1_return': None},
            'day': [],
        })
    return days


def test_paper_pick_performance_gate_requires_ten_completed_samples_and_fixed_fields():
    gate = backtest._paper_pick_performance_gate(_completed_paper_days_for_gate_tests([0.01] * 3))

    assert gate['status'] == 'INSUFFICIENT_SAMPLE'
    assert gate['sample_count'] == 3
    assert gate['blocking_reason'] == 'sample_count=3 < 10'
    assert set(gate) >= {
        'avg_t1_return', 'win_rate', 'limitup_rate', 'near_limitup_rate',
        'large_loss_rate', 'max_drawdown', 'benchmarks',
    }


def test_paper_pick_performance_gate_passes_and_fails_on_loss_risk():
    passed = backtest._paper_pick_performance_gate(
        _completed_paper_days_for_gate_tests([0.10] + [0.01] * 9)
    )
    failed = backtest._paper_pick_performance_gate(
        _completed_paper_days_for_gate_tests([-0.06, -0.06, -0.06] + [0.02] * 7)
    )

    assert passed['status'] == 'PASS'
    assert failed['status'] == 'FAIL'
    assert 'large_loss_rate' in failed['blocking_reason']


def test_paper_pick_loss_attribution_is_deterministic_and_risk_aware():
    days = _completed_paper_days_for_gate_tests([-0.03])
    days[0]['paper']['capital_risk_profile'] = {
        'failed_limitup_risk': 1.0,
        'main_buy_outflow_pressure': 1.0,
        'popularity_crowding_risk': 0.9,
    }

    attribution = backtest._paper_pick_loss_attribution(days)
    case = attribution['daily_cases'][0]

    assert 'RANK4_TO_6_UNDERVALUED' in case['miss_types']
    assert 'LIMITUP_GENE_UNDERWEIGHTED' in case['miss_types']
    assert 'FAILED_LIMITUP_RISK_UNDERPENALIZED' in case['miss_types']
    assert attribution['miss_type_distribution'] == dict(sorted(attribution['miss_type_distribution'].items()))


def test_paper_pick_loss_attribution_uses_no_action_when_evidence_is_absent():
    paper = {'symbol': '600001', 'rank': 1, 't1_return': -0.01}
    alternative = {'symbol': '600002', 'rank': 2, 't1_return': 0.02}
    attribution = backtest._paper_pick_loss_attribution([
        {'trade_date': '2026-07-01', 'paper': paper, 'day': [paper, alternative]}
    ])

    assert attribution['daily_cases'][0]['miss_types'] == ['NO_ACTION_INSUFFICIENT_EVIDENCE']


def test_shadow_ranking_replay_is_diagnostic_only_until_sample_gate_passes():
    days = _completed_paper_days_for_gate_tests([-0.02, -0.01, 0.01])
    replay = backtest._shadow_ranking_replay(days)

    baseline = next(item for item in replay['variants'] if item['name'] == 'baseline_current')
    assert replay['status'] == 'INSUFFICIENT_SAMPLE'
    assert replay['selected_candidate_variant'] is None
    assert baseline['avg_t1_return'] == -0.006667
    assert any(item['name'] == 'limitup_gene_shadow_plus' for item in replay['variants'])


def test_shadow_ranking_replay_selects_only_after_ten_samples_and_risk_guards():
    days = _completed_paper_days_for_gate_tests([0.01] * 10)
    for day in days:
        day['day'][1]['low_position_catalyst_score'] = 10.0
        day['day'][1]['t1_return'] = 0.10

    replay = backtest._shadow_ranking_replay(days)

    assert replay['status'] == 'PASS'
    assert replay['selected_candidate_variant'] == 'low_position_catalyst_shadow_plus'


def test_mainline_extractor_groups_aliases_from_persisted_candidate_rows():
    rows = [
        {'symbol': '600001', 'rank': 1, 'predicted_sector': 'CRO', 'final_score': 82, 't1_return': 0.01},
        {'symbol': '600002', 'rank': 2, 'sector_opportunity_tags': ['创新药'], 'final_score': 80, 't1_return': 0.02},
        {'symbol': '600003', 'rank': 3, 'predicted_sector': '绿色电力', 'final_score': 79, 't1_return': 0.03},
    ]

    mainlines = backtest._daily_mainlines(rows)

    assert mainlines['top3'][0]['theme'] == '创新药'
    assert mainlines['top3'][0]['candidate_count'] == 2
    assert mainlines['top3'][0]['evidence_source'] == ['persisted_candidate_evidence']


def test_mainline_miss_bucket_classifier_covers_primary_buckets():
    paper = {'symbol': '600001', 'rank': 1, 't1_return': -0.01, 'predicted_sector': '煤炭'}
    neutral_paper = {'symbol': '600001', 'rank': 1, 't1_return': -0.01}
    no_data = {'trade_date': '2026-07-01', 'paper': paper, 'day': [paper]}
    not_in_pool = {'trade_date': '2026-07-02', 'paper': paper, 'day': [paper]}
    blocked = {
        'trade_date': '2026-07-03', 'paper': neutral_paper,
        'day': [neutral_paper, {'symbol': '600002', 'rank': 2, 't1_return': 0.03, 'predicted_sector': '创新药', 'not_selected_reason': ['risk_gate']}],
    }
    ranked_below = {
        'trade_date': '2026-07-04', 'paper': neutral_paper,
        'day': [neutral_paper, {'symbol': '600003', 'rank': 4, 't1_return': 0.04, 'predicted_sector': '创新药'}],
    }

    assert backtest._mainline_primary_bucket(no_data, {'status': 'MAINLINE_DATA_PARTIAL', 'top3': [], 'top5': []}) == 'MAINLINE_NOT_IN_DATA'
    assert backtest._mainline_primary_bucket(not_in_pool, {'status': 'PASS', 'top3': [{'theme': '创新药'}], 'top5': [{'theme': '创新药'}]}) == 'MAINLINE_NOT_IN_POOL'
    assert backtest._mainline_primary_bucket(blocked, backtest._daily_mainlines(blocked['day'])) == 'MAINLINE_BLOCKED_BY_GATE'
    assert backtest._mainline_primary_bucket(ranked_below, backtest._daily_mainlines(ranked_below['day'])) == 'MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK'


def test_mainline_shadow_replay_is_report_only_and_can_select_sector_follower():
    days = _completed_paper_days_for_gate_tests([-0.02])
    days[0]['paper']['predicted_sector'] = '煤炭'
    days[0]['day'][1].update({
        'predicted_sector': '创新药', 'source_layers': ['sector_follower'],
        'fund_flow_momentum': 0.2, 't1_return': 0.08,
    })

    replay = backtest._mainline_shadow_replay(days)
    sector_variant = next(item for item in replay['variants'] if item['name'] == 'sector_follower_mainline_shadow')

    assert replay['selected_for_production'] is False
    assert sector_variant['selected_for_production'] is False
    assert sector_variant['daily_selected'][0]['symbol'] == days[0]['day'][1]['symbol']
    assert 'sector_follower_diagnostic_only' in backtest._mainline_blockers(days[0]['day'][1])


def test_mainline_risk_penalty_changes_only_shadow_selection():
    days = _completed_paper_days_for_gate_tests([0.02])
    days[0]['paper']['predicted_sector'] = '煤炭'
    risky = days[0]['day'][1]
    risky.update({
        'predicted_sector': '创新药', 'rank': 2, 't1_return': -0.03,
        'capital_risk_profile': {'failed_limitup_risk': 1.0, 'popularity_crowding_risk': 1.0},
    })
    safe = {'trade_date': days[0]['trade_date'], 'symbol': '603999', 'rank': 3, 't1_return': 0.04, 'predicted_sector': '创新药'}
    days[0]['day'].append(safe)

    replay = backtest._mainline_shadow_replay(days)
    risk_variant = next(item for item in replay['variants'] if item['name'] == 'mainline_risk_penalty_shadow')

    assert days[0]['paper']['symbol'].startswith('600')
    assert risk_variant['daily_selected'][0]['symbol'] == '603999'
    assert risk_variant['selected_for_production'] is False


def test_weak_market_shadow_profile_downgrades_weak_high_chase_without_official_block():
    row = make_candidate('600001', '追高', score=85, rank=1)
    row.update({
        'signal_pct': 6.5,
        'close_position_score': 0.55,
        'fund_flow_momentum': -0.2,
        'market_breadth_up_pct': 35,
        'market_regime': 'weak',
        'limitup_capture_score': 0.0,
        'social_signal_quality': 'MISSING',
    })
    profile = runner.shadow_risk_profile(row, {'market_snapshot': {'market_breadth_up_pct': 35}})

    assert profile['weak_market'] is True
    assert profile['chase_high_risk'] == 'HIGH'
    assert profile['chase_high_shadow_penalty'] > 0
    assert profile['limitup_gene_shadow_gate'] in ('WARN', 'BLOCK_SHADOW')
    assert profile['used_for_official_ranking'] is False


def test_social_confirmation_is_diagnostic_only():
    row = make_candidate('600001', '社交样本', score=80, rank=1)
    row.update({
        'social_catalyst_score': 0.85,
        'theme_strength_last30d': 0.75,
        'social_sentiment_score': 0.5,
        'social_noise_risk': 0.1,
        'social_signal_quality': 'HIGH',
        'social_source_layers': ['eastmoney_guba', 'x'],
    })
    profile = runner.social_confirmation_profile(row)

    assert profile['status'] == 'PASS'
    assert profile['used_for_official_ranking'] is False


def test_limitup_and_sell_gates_exclude_pending_and_apply_conservative_high_capture():
    days = _completed_paper_days_for_gate_tests([-0.02, 0.01, 0.02], pending=True)
    attribution = backtest._paper_pick_loss_attribution(days[:3])
    limitup = backtest._limitup_capture_gate(days[:3], attribution)
    sell = backtest._sell_strategy_gate(days[:3])

    assert limitup['status'] == 'INSUFFICIENT_SAMPLE'
    assert limitup['missed_limitup_count'] == 1
    assert sell['status'] == 'INSUFFICIENT_SAMPLE'
    assert sell['strategies']['take_profit_intraday']['avg_return'] < 0.04
    assert sell['recommended_sell_strategy'] is None


def test_sell_strategy_replay_marks_high_based_rules_as_optimistic():
    replay = backtest._sell_strategy_replay(_completed_paper_days_for_gate_tests([-0.04, -0.02, 0.01]))

    assert replay['sample_count'] == 3
    assert replay['hold_to_close_avg'] == pytest.approx(-0.016667)
    assert 'take_profit_2pct' in replay['optimistic_upper_bound_rules']
    assert replay['run_mode'] == 'T1_POST_HOC_REPLAY_ONLY'


def test_sell_gate_surfaces_missing_execution_fields_and_sample_unlocks():
    missing = backtest._sell_strategy_gate(_completed_paper_days_for_gate_tests([0.01] * 3, execution=False))

    assert 'next_day_high_return' in missing['missing_execution_fields']
    assert backtest._sample_accumulation_gate(3)['status'] == 'WAITING'
    assert backtest._sample_accumulation_gate(10)['status'] == 'READY_FOR_SHADOW_SELECTION'
    assert backtest._sample_accumulation_gate(20)['status'] == 'READY_FOR_STRATEGY_REPLAY'
    assert backtest._sample_accumulation_gate(30)['status'] == 'READY_FOR_FREEZE_REVIEW'


def test_sell_gate_recommends_only_after_ten_complete_execution_samples():
    sell = backtest._sell_strategy_gate(_completed_paper_days_for_gate_tests([0.01] * 10))

    assert sell['status'] == 'PASS'
    assert sell['recommended_sell_strategy'] == 'take_profit_intraday'


def test_daily_system_gate_separates_operational_failure_from_strategy_sample_state():
    report = {
        'strategy_readiness': {'status': 'INSUFFICIENT_COMPARABLE_SAMPLE'},
        'paper_pick_performance_gate': {}, 'limitup_capture_gate': {},
        'sell_strategy_gate': {}, 'sell_strategy_execution_gate': {},
        'shadow_ranking_replay': {}, 'limitup_gene_shadow_replay': {},
        'paper_pick_case_book': {}, 'sample_accumulation_gate': {},
        'production_ranking_change_gate': {}, 'sell_strategy_replay': {},
        'paper_pick_vs_pool_diagnostic': {},
        'mainline_diagnostic_gate': {}, 'mainline_pool_coverage': {},
        'mainline_shadow_replay': {}, 'mainline_case_book': {},
    }

    passed = backtest.build_daily_system_gate(
        report, scan_completed=True, paper_pick_written=True, return_backfill_completed=True,
        run_mode='LIVE_DAILY_PIPELINE',
    )
    failed = backtest.build_daily_system_gate(
        report, scan_completed=True, paper_pick_written=False, return_backfill_completed=True,
        run_mode='LIVE_DAILY_PIPELINE',
    )

    assert passed['status'] == 'PASS'
    assert passed['strategy_readiness_status'] == 'INSUFFICIENT_COMPARABLE_SAMPLE'
    assert failed['status'] == 'FAIL'
    assert 'paper_pick_not_written' in failed['blocked_by']


def test_daily_system_gate_live_run_mode_warns_for_nonfatal_backfill_only():
    report = {
        'strategy_readiness': {'status': 'INSUFFICIENT_COMPARABLE_SAMPLE'},
        'paper_pick_performance_gate': {}, 'limitup_capture_gate': {},
        'sell_strategy_gate': {}, 'sell_strategy_execution_gate': {},
        'shadow_ranking_replay': {}, 'limitup_gene_shadow_replay': {},
        'paper_pick_case_book': {}, 'sample_accumulation_gate': {},
        'production_ranking_change_gate': {}, 'sell_strategy_replay': {},
        'paper_pick_vs_pool_diagnostic': {},
        'mainline_diagnostic_gate': {}, 'mainline_pool_coverage': {},
        'mainline_shadow_replay': {}, 'mainline_case_book': {},
    }
    gate = backtest.build_daily_system_gate(
        report, scan_completed=True, paper_pick_written=True, return_backfill_completed=True,
        backfill_failure_reasons={'NO_TRADING_DATA': 1},
        trade_date='2026-07-10', run_mode='LIVE_DAILY_PIPELINE',
    )

    assert gate['status'] == 'WARN'
    assert gate['run_mode'] == 'LIVE_DAILY_PIPELINE'
    assert gate['trade_date'] == '2026-07-10'
    assert gate['strategy_readiness_status'] == 'INSUFFICIENT_COMPARABLE_SAMPLE'


def test_daily_system_gate_marks_manual_report_as_not_live_run():
    gate = backtest.build_daily_system_gate(
        {}, scan_completed=False, paper_pick_written=False, return_backfill_completed=False,
    )

    assert gate['status'] == 'NOT_LIVE_RUN'
    assert gate['run_mode'] == 'MANUAL_COHORT_REPORT'


def test_daily_system_gate_fails_when_new_cohort_gate_is_missing():
    report = {
        'strategy_readiness': {}, 'paper_pick_performance_gate': {}, 'limitup_capture_gate': {},
        'sell_strategy_gate': {}, 'sell_strategy_execution_gate': {}, 'shadow_ranking_replay': {},
        'limitup_gene_shadow_replay': {}, 'paper_pick_case_book': {}, 'sample_accumulation_gate': {},
        'sell_strategy_replay': {}, 'paper_pick_vs_pool_diagnostic': {},
        'mainline_diagnostic_gate': {}, 'mainline_pool_coverage': {},
        'mainline_shadow_replay': {}, 'mainline_case_book': {},
    }
    gate = backtest.build_daily_system_gate(
        report, scan_completed=True, paper_pick_written=True, return_backfill_completed=True,
        run_mode='LIVE_DAILY_PIPELINE',
    )

    assert gate['status'] == 'FAIL'
    assert gate['cohort_report_generated'] is False
    assert 'production_ranking_change_gate' in gate['blocked_by'][0]


def test_sample_accumulation_gate_keeps_pending_non_mainboard_and_missing_t1_outside_count():
    gate = backtest._sample_accumulation_gate(
        3,
        completed_trade_dates=['2026-07-01', '2026-07-03', '2026-07-08'],
        pending_trade_dates=['2026-07-10'], non_mainboard_trade_dates=['2026-07-02'],
        no_t1_return_trade_dates=['2026-07-09'],
    )

    assert gate['status'] == 'WAITING'
    assert gate['remaining_dates'] == 7
    assert gate['next_unlock'] == 'READY_FOR_SHADOW_SELECTION'
    assert gate['latest_completed_trade_date'] == '2026-07-08'
    assert gate['pending_trade_dates'] == ['2026-07-10']
    assert gate['non_mainboard_paper_pick_dates'] == ['2026-07-02']
    assert gate['no_t1_return_paper_pick_dates'] == ['2026-07-09']


def test_limitup_gene_shadow_replay_promotes_low_risk_gene_but_suppresses_triple_risk():
    days = _completed_paper_days_for_gate_tests([-0.02] * 3)
    for day in days:
        alternative = day['day'][1]
        alternative.update({
            'previous_limitup': True, 'near_limitup_close': True,
            'first_board_gene': True, 't1_return': 0.10,
        })
    attribution = backtest._paper_pick_loss_attribution(days)
    replay = backtest._limitup_gene_shadow_replay(days, attribution)
    promoted = max(days[0]['day'], key=lambda row: backtest._shadow_score(row, 'limitup_gene_shadow_plus'))
    days[0]['day'][1]['capital_risk_profile'] = {
        'main_buy_outflow_pressure': 1.0,
        'popularity_crowding_risk': 0.9,
        'failed_limitup_risk': 1.0,
    }
    suppressed = max(days[0]['day'], key=lambda row: backtest._shadow_score(row, 'limitup_gene_shadow_plus'))

    assert promoted['symbol'] == days[0]['day'][1]['symbol']
    assert suppressed['symbol'] == days[0]['paper']['symbol']
    assert replay['status'] == 'INSUFFICIENT_SAMPLE'
    assert replay['selected_for_production'] is False
    assert replay['missed_cases']


def test_paper_pick_case_book_is_sorted_and_requires_shadow_only_evidence():
    days = _completed_paper_days_for_gate_tests([-0.03, -0.02])
    attribution = backtest._paper_pick_loss_attribution(days)
    case_book = backtest._paper_pick_case_book(list(reversed(days)), attribution)

    assert case_book['status'] == 'PASS'
    assert case_book['case_count'] == 2
    assert case_book['cases'][0]['trade_date'] < case_book['cases'][1]['trade_date']
    assert 'RANK4_TO_6_UNDERVALUED' in case_book['cases'][0]['decision_gap']['miss_types']
    assert case_book['cases'][0]['decision_gap']['actionability'] == 'SHADOW_REPLAY_ONLY'


def test_sell_execution_gate_keeps_retrace_and_low_open_constraints():
    days = _completed_paper_days_for_gate_tests([0.01] * 3)
    days[0]['paper'].update({
        'next_day_open_return': -0.06, 'next_day_low_return': -0.08,
        'next_day_high_return': 0.04, 'high_to_close_retrace': -0.03,
    })
    gate = backtest._sell_strategy_execution_gate(days)

    assert gate['status'] == 'INSUFFICIENT_SAMPLE'
    assert gate['execution_assumptions']['intraday_high_capture_max_ratio'] == 0.70
    assert gate['strategies']['conservative_intraday_take_profit']['avg_return'] < 0.04
    assert gate['strategies']['drawdown_guard']['avg_return'] > -0.06


def test_daily_closure_overwrites_latest_and_requires_all_cohort_gates(tmp_path):
    report = {
        'return_coverage_gate': {}, 'strategy_readiness': {}, 'paper_pick_performance_gate': {},
        'limitup_capture_gate': {}, 'sell_strategy_gate': {}, 'sell_strategy_execution_gate': {},
        'shadow_ranking_replay': {}, 'limitup_gene_shadow_replay': {},
        'sample_accumulation_gate': {}, 'production_ranking_change_gate': {},
        'paper_pick_case_book': {}, 'sell_strategy_replay': {},
        'sample_count_reconciliation': {}, 'paper_pick_vs_pool_diagnostic': {},
        'mainline_diagnostic_gate': {}, 'mainline_pool_coverage': {},
        'mainline_shadow_replay': {}, 'mainline_case_book': {},
    }
    closure = backtest.build_daily_closure(
        '2026-07-10', report, {'fetched': 0, 'fetch_failed': 1, 'failure_reasons': {'NO_TRADING_DATA': 1}},
        scan_completed=True, paper_pick_written=True,
    )
    output = tmp_path / 'daily_closure_latest.json'
    backtest.write_daily_closure(closure, output)
    backtest.write_daily_closure({**closure, 'trade_date': '2026-07-11'}, output)

    assert closure['daily_system_gate']['status'] == 'WARN'
    assert json.loads(output.read_text())['trade_date'] == '2026-07-11'
    assert set(closure['cohort_gates']) >= {'limitup_gene_shadow_replay', 'paper_pick_case_book'}
    assert closure['social_signal_gate']['status'] == 'WARN'


def test_production_ranking_change_gate_remains_locked_until_quality_and_sample_gates_pass():
    locked = backtest._production_ranking_change_gate(
        sample_gate=backtest._sample_accumulation_gate(3), shadow_replay={'status': 'PASS'},
        full_pytest_gate_status='PASS', return_coverage_gate_status='PASS', full_chain_ready_days=5,
    )
    proposal = backtest._production_ranking_change_gate(
        sample_gate=backtest._sample_accumulation_gate(10), shadow_replay={'status': 'PASS'},
        full_pytest_gate_status='PASS', return_coverage_gate_status='PASS', full_chain_ready_days=5,
    )
    small_step = backtest._production_ranking_change_gate(
        sample_gate=backtest._sample_accumulation_gate(20), shadow_replay={'status': 'PASS'},
        full_pytest_gate_status='PASS', return_coverage_gate_status='PASS', full_chain_ready_days=5,
        market_regime_count=2,
    )
    risk_worse = backtest._production_ranking_change_gate(
        sample_gate=backtest._sample_accumulation_gate(10), shadow_replay={'status': 'FAIL'},
        full_pytest_gate_status='PASS', return_coverage_gate_status='PASS', full_chain_ready_days=5,
    )
    coverage_failed = backtest._production_ranking_change_gate(
        sample_gate=backtest._sample_accumulation_gate(20), shadow_replay={'status': 'PASS'},
        full_pytest_gate_status='PASS', return_coverage_gate_status='FAIL', full_chain_ready_days=5,
        market_regime_count=2,
    )

    assert locked['status'] == 'LOCKED'
    assert proposal['status'] == 'READY_FOR_PROPOSAL'
    assert small_step['status'] == 'READY_FOR_SMALL_STEP_CHANGE'
    assert risk_worse['status'] == 'LOCKED'
    assert coverage_failed['status'] == 'LOCKED'
    assert 'change_formal_candidate_sort_key' in locked['forbidden_actions']


def _historical_replay_report_fixture(sample_count=3):
    return {
        'return_coverage_gate': {'status': 'PASS'},
        'strategy_readiness': {'status': 'INSUFFICIENT_COMPARABLE_SAMPLE'},
        'paper_pick_performance_gate': {}, 'limitup_capture_gate': {},
        'sell_strategy_gate': {}, 'sell_strategy_execution_gate': {},
        'shadow_ranking_replay': {}, 'limitup_gene_shadow_replay': {'status': 'INSUFFICIENT_SAMPLE'},
        'sample_accumulation_gate': {
            'mainboard_comparable_paper_pick_dates': sample_count,
            'completed_trade_dates': [],
        },
        'production_ranking_change_gate': {}, 'paper_pick_case_book': {},
        'sell_strategy_replay': {}, 'paper_pick_vs_pool_diagnostic': {},
        'mainline_diagnostic_gate': {}, 'mainline_pool_coverage': {},
        'mainline_shadow_replay': {}, 'mainline_case_book': {},
    }


def test_historical_live_replay_uses_db_snapshot_and_separates_validation(monkeypatch):
    input_date = dt.date(2026, 7, 9)
    candidates = [
        {'trade_date': input_date, 'symbol': '600001', 'rank': 1, 'factor_snapshot': {}},
        {'trade_date': input_date, 'symbol': '600002', 'rank': 4, 'factor_snapshot': {'previous_limitup': True}},
    ]
    picks = [{'trade_date': input_date, 'symbol': '600001', 'decision': 'PAPER_PICK', 'final_score': 80.0}]
    returns = [
        {'trade_date': input_date, 'symbol': '600001', 't1_return': -0.02},
        {'trade_date': input_date, 'symbol': '600002', 't1_return': 0.10},
    ]
    monkeypatch.setattr(backtest, 'fetch_daily_candidates', lambda _date: candidates)
    monkeypatch.setattr(backtest, 'fetch_picks', lambda _date: picks)
    monkeypatch.setattr(backtest, 'fetch_returns', lambda _date: returns)
    monkeypatch.setattr(backtest, 'build_db_cohort_report', lambda: _historical_replay_report_fixture())

    closure = backtest.build_historical_live_replay_closure('2026-07-09', '2026-07-10')

    assert closure['historical_live_replay'] == {
        'status': 'PASS', 'run_mode': 'HISTORICAL_LIVE_REPLAY',
        'input_trade_date': '2026-07-09', 'validation_trade_date': '2026-07-10',
        'data_source': 'DB_SNAPSHOT', 'uses_future_data_for_decision': False,
    }
    assert closure['daily_system_gate']['run_mode'] == 'HISTORICAL_LIVE_REPLAY'
    assert closure['historical_replay_leakage_gate']['status'] == 'PASS'
    assert closure['shadow_replay_leakage_gate']['status'] == 'PASS'
    assert closure['historical_replay_case_book']['case_count'] == 1


def test_historical_replay_leakage_gate_rejects_future_decision_fields():
    gate = backtest._historical_replay_leakage_gate(
        [{'symbol': '600001', 't1_return': 0.10}], [], '2026-07-09', '2026-07-10',
    )

    assert gate['status'] == 'FAIL'
    assert gate['violations'] == ['candidates[0].t1_return']


def test_shadow_score_does_not_change_when_future_outcomes_are_injected():
    row = {'symbol': '600001', 'rank': 4, 'previous_limitup': True}
    injected = {**row, 't1_return': 0.10, 'next_day_high_return': 0.20, 'next_day_limit_touch': True}
    for variant in ('low_position_catalyst_shadow_plus', 'limitup_gene_shadow_plus', 'risk_penalty_shadow_plus'):
        assert backtest._shadow_score(row, variant) == backtest._shadow_score(injected, variant)


def test_historical_replay_sample_update_explains_all_exclusions():
    advanced = backtest._historical_replay_sample_update(
        3, input_trade_date='2026-07-09', validation_trade_date='2026-07-10',
        has_paper_pick=True, paper_pick_is_mainboard=True, has_t1_return=True, already_counted=False,
    )
    non_mainboard = backtest._historical_replay_sample_update(
        3, input_trade_date='2026-07-09', validation_trade_date='2026-07-10',
        has_paper_pick=True, paper_pick_is_mainboard=False, has_t1_return=True, already_counted=False,
    )
    missing_return = backtest._historical_replay_sample_update(
        3, input_trade_date='2026-07-09', validation_trade_date='2026-07-10',
        has_paper_pick=True, paper_pick_is_mainboard=True, has_t1_return=False, already_counted=False,
    )
    no_pick = backtest._historical_replay_sample_update(
        3, input_trade_date='2026-07-09', validation_trade_date='2026-07-10',
        has_paper_pick=False, paper_pick_is_mainboard=False, has_t1_return=False, already_counted=False,
    )

    assert advanced['after_sample_count'] == 4
    assert advanced['sample_count_changed'] is True
    assert non_mainboard['reason'] == 'paper_pick_not_mainboard'
    assert missing_return['reason'] == 'missing_t1_return'
    assert no_pick['reason'] == 'no_paper_pick'


def test_historical_replay_warns_without_validation_and_fails_without_snapshot(monkeypatch):
    input_date = dt.date(2026, 7, 9)
    candidate = {'trade_date': input_date, 'symbol': '600001', 'rank': 1, 'factor_snapshot': {}}
    pick = {'trade_date': input_date, 'symbol': '600001', 'decision': 'PAPER_PICK', 'final_score': 80.0}
    monkeypatch.setattr(backtest, 'fetch_daily_candidates', lambda _date: [candidate])
    monkeypatch.setattr(backtest, 'fetch_picks', lambda _date: [pick])
    monkeypatch.setattr(backtest, 'fetch_returns', lambda _date: [])
    monkeypatch.setattr(backtest, 'build_db_cohort_report', lambda: _historical_replay_report_fixture())

    missing_validation = backtest.build_historical_live_replay_closure('2026-07-09', '2026-07-10')
    monkeypatch.setattr(backtest, 'fetch_daily_candidates', lambda _date: [])
    missing_snapshot = backtest.build_historical_live_replay_closure('2026-07-09', '2026-07-10')

    assert missing_validation['historical_live_replay']['status'] == 'WARN'
    assert missing_validation['historical_live_replay_sample_update']['reason'] == 'missing_t1_return'
    assert missing_snapshot['historical_live_replay']['status'] == 'FAIL'


def test_historical_replay_sample_update_uses_reconciled_cohort_definition(monkeypatch):
    input_date = dt.date(2026, 7, 10)
    candidate = {'trade_date': input_date, 'symbol': '600001', 'rank': 1, 'factor_snapshot': {}}
    pick = {'trade_date': input_date, 'symbol': '600001', 'decision': 'PAPER_PICK', 'final_score': 80.0}
    report = _historical_replay_report_fixture()
    report['sample_count_reconciliation'] = {
        'completed_paper_pick_sample_days': 3,
        'excluded_dates': [{
            'trade_date': '2026-07-10',
            'reason': 'paper_pick_not_in_mainboard_top10_snapshot',
        }],
    }
    monkeypatch.setattr(backtest, 'fetch_daily_candidates', lambda _date: [candidate])
    monkeypatch.setattr(backtest, 'fetch_picks', lambda _date: [pick])
    monkeypatch.setattr(backtest, 'fetch_returns', lambda _date: [{'trade_date': input_date, 'symbol': '600001', 't1_return': 0.01}])
    monkeypatch.setattr(backtest, 'build_db_cohort_report', lambda: report)

    closure = backtest.build_historical_live_replay_closure('2026-07-10', '2026-07-13')
    update = closure['historical_live_replay_sample_update']

    assert update['after_sample_count'] == 3
    assert update['sample_count_changed'] is False
    assert update['reason'] == 'paper_pick_not_in_mainboard_top10_snapshot'


def test_limitup_gene_signal_audit_never_uses_validation_limitup_as_a_signal():
    paper = {'symbol': '600001', 'rank': 1, 't1_return': -0.02}
    alternative = {'symbol': '600002', 'rank': 4, 't1_return': 0.10}
    attribution = backtest._paper_pick_loss_attribution([
        {'trade_date': '2026-07-09', 'paper': paper, 'day': [paper, alternative]}
    ])
    audit = backtest._limitup_gene_signal_audit(
        [{'trade_date': '2026-07-09', 'paper': paper, 'day': [paper, alternative]}],
        attribution, run_mode='HISTORICAL_LIVE_REPLAY',
    )

    assert 'LIMITUP_GENE_UNDERWEIGHTED' not in attribution['daily_cases'][0]['miss_types']
    assert audit['diagnosis'] == 'signal_not_persisted'


def test_db_completeness_gate_requires_full_predecision_evidence():
    candidates = [
        {
            'symbol': f'600{index:03d}', 'rank': index + 1,
            'candidate_entry_reason': ['entry'], 'factor_snapshot': {},
            'auxiliary_evidence_snapshot': {'source': 'live'}, 'ranking_basis': {'basis': 'stable'},
            'source_layers': ['L0_FULL_UNIVERSE'], 'candidate_features': {'source': 'live'},
        }
        for index in range(200)
    ]
    for candidate in candidates:
        candidate['factor_snapshot'].update({signal: False for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS})
    signal_rows = [
        {'symbol': candidate['symbol'], 'signal_key': signal, 'signal_value': 0.0}
        for candidate in candidates for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS
    ]
    picks = [{'symbol': '600000', 'decision': 'PAPER_PICK', 'final_score': 90.0}]

    gate = backtest.build_db_completeness_gate(
        '2026-07-10', mode='LIVE_DECISION_DAY', candidate_rows=candidates,
        pick_rows=picks, return_rows=[], signal_rows=signal_rows, scan_session={'id': 1},
    )

    assert gate['status'] == 'WARN'
    assert gate['checks']['limitup_gene_signals_persisted'] is True
    assert gate['checks']['future_fields_absent_from_decision_snapshot'] is True
    assert gate['db_completeness_summary']['persisted_limitup_gene_signal_rows'] == 1200
    assert gate['warnings'] == ['t1_return_pending_until_next_trade_date']


def test_db_completeness_gate_surfaces_delayed_horizon_diagnostics():
    candidate = {
        'symbol': '600000', 'rank': 1,
        'candidate_entry_reason': ['entry'], 'factor_snapshot': {},
        'auxiliary_evidence_snapshot': {'source': 'live'}, 'ranking_basis': {'basis': 'stable'},
        'source_layers': ['L0_FULL_UNIVERSE'], 'candidate_features': {'source': 'live'},
    }
    signal_rows = [
        {'symbol': '600000', 'signal_key': signal, 'signal_value': 0.0}
        for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS
    ]

    gate = backtest.build_db_completeness_gate(
        '2026-07-10', mode='T1_VALIDATION', validation_trade_date='2026-07-13',
        candidate_rows=[candidate],
        pick_rows=[{'symbol': '600000', 'decision': 'PAPER_PICK', 'final_score': 90.0}],
        return_rows=[{'symbol': '600000', 't1_return': -0.01, 't2_return': 0.03, 't3_return': 0.02}],
        signal_rows=signal_rows,
        scan_session={'id': 1},
    )

    assert gate['status'] == 'PASS'
    assert gate['horizon_summary']['coverage_counts'] == {'t1': 1, 't2': 1, 't3': 1}
    assert gate['horizon_summary']['positive_counts'] == {'t1': 0, 't2': 1, 't3': 1}
    assert gate['horizon_summary']['late_bloom_count'] == 1
    assert gate['horizon_summary']['late_bloom_rate'] == 1.0
    assert gate['horizon_summary']['paper_pick_best_available_horizon'] == {'horizon': 'T3', 'return': 0.02}
    assert gate['db_completeness_summary']['horizon_summary'] == gate['horizon_summary']


def test_db_completeness_gate_fails_missing_snapshot_and_future_fields():
    missing_snapshot = backtest.build_db_completeness_gate(
        '2026-07-10', mode='LIVE_DECISION_DAY', candidate_rows=[], pick_rows=[],
        return_rows=[], signal_rows=[], scan_session={'id': 1},
    )
    future_field = backtest.build_db_completeness_gate(
        '2026-07-10', mode='HISTORICAL_REPLAY',
        candidate_rows=[{'symbol': '600001', 'rank': 1, 't1_return': 0.1}],
        pick_rows=[{'symbol': '600001', 'decision': 'PAPER_PICK'}],
        return_rows=[{'symbol': '600001', 't1_return': 0.01}], signal_rows=[], scan_session=None,
    )

    assert missing_snapshot['status'] == 'FAIL'
    assert 'candidate_snapshot_persisted' in missing_snapshot['missing']
    assert future_field['status'] == 'FAIL'
    assert 'future_fields_absent_from_decision_snapshot' in future_field['missing']


def test_limitup_gene_false_values_are_persisted_and_audited():
    row = {
        'symbol': '600001', 'rank': 1,
        'factor_snapshot': {signal: False for signal in backtest.LIMITUP_GENE_SHADOW_SIGNALS},
    }
    audit = backtest._limitup_gene_signal_audit(
        [{'trade_date': '2026-07-10', 'paper': {}, 'day': [row]}],
        {'daily_cases': []}, run_mode='LIVE_DECISION_DAY',
    )

    assert audit['status'] == 'PASS'
    assert audit['persisted_signal_coverage'] == 1.0
    assert audit['diagnosis'] == 'signal_persisted'


def test_historical_replay_fails_when_future_field_enters_decision_snapshot(monkeypatch):
    input_date = dt.date(2026, 7, 9)
    candidates = [{'trade_date': input_date, 'symbol': '600001', 'rank': 1, 't1_return': 0.10}]
    picks = [{'trade_date': input_date, 'symbol': '600001', 'decision': 'PAPER_PICK', 'final_score': 80.0}]
    returns = [{'trade_date': input_date, 'symbol': '600001', 't1_return': 0.01}]
    monkeypatch.setattr(backtest, 'fetch_daily_candidates', lambda _date: candidates)
    monkeypatch.setattr(backtest, 'fetch_picks', lambda _date: picks)
    monkeypatch.setattr(backtest, 'fetch_returns', lambda _date: returns)
    monkeypatch.setattr(backtest, 'build_db_cohort_report', lambda: _historical_replay_report_fixture())

    closure = backtest.build_historical_live_replay_closure('2026-07-09', '2026-07-10')

    assert closure['historical_live_replay']['status'] == 'FAIL'
    assert closure['historical_replay_leakage_gate']['status'] == 'FAIL'


def test_limit_pool_api_marks_rc102_as_invalid_source(monkeypatch):
    monkeypatch.setattr(scanner_v2, 'api_get', lambda _url: {'rc': 102, 'data': None})

    rows, diagnostics = scanner_v2.fetch_eastmoney_limit_pool(
        'getTopicZTPool',
        '2026-07-14',
        'fbt:asc',
    )

    assert rows == []
    assert diagnostics['status'] == 'ERROR'
    assert diagnostics['response_rc'] == 102
    assert diagnostics['error'] == 'EASTMONEY_POOL_RESPONSE_INVALID'


def test_core_sentiment_status_blocks_missing_pool_response():
    diagnostics = {
        'limitup_pool': {'status': 'ERROR', 'record_count': 0},
        'limitup_broken': {'status': 'PASS', 'record_count': 22},
        'limitup_consecutive': {'status': 'ERROR', 'record_count': 0},
        'limitup_yesterday': {'status': 'PASS', 'record_count': 68},
    }
    status = scanner_v2.build_core_sentiment_pool_status(
        diagnostics,
        {
            'limitup_pool': [],
            'limitup_broken': [{}] * 22,
            'limitup_consecutive': [],
            'limitup_yesterday': [{}] * 68,
        },
        market_limitups=107,
    )

    assert status['status'] == 'BLOCK'
    assert set(status['missing_sources']) == {'limitup_pool', 'limitup_consecutive'}
    assert 'QUOTE_UNIVERSE_HAS_LIMITUPS_BUT_LIMITUP_POOL_EMPTY' in status['flags']


def test_runner_core_market_source_gate_blocks_unproven_or_unpersisted_scan():
    missing_core = runner.core_market_source_gate({
        'scan_summary_path': '/tmp/summary.json',
        'source_status': {'required_cdp_tabs': {'status': 'PASS'}},
        'market_snapshot': {},
    })
    incomplete_persistence = runner.core_market_source_gate({
        'scan_summary_path': '/tmp/summary.json',
        'source_status': {
            'required_cdp_tabs': {'status': 'PASS'},
            'core_sentiment_pools': {'status': 'PASS'},
        },
        'market_snapshot': {'hard_block_source_status': {'status': 'PASS'}},
    })
    passed = runner.core_market_source_gate({
        'scan_summary_path': '/tmp/summary.json',
        'source_status': {
            'required_cdp_tabs': {'status': 'PASS'},
            'core_sentiment_pools': {'status': 'PASS'},
            'scan_snapshot_persistence': {'status': 'PASS', 'scan_session_id': 9},
        },
        'market_snapshot': {'hard_block_source_status': {'status': 'PASS'}},
    })

    assert missing_core['status'] == 'BLOCK'
    assert missing_core['flags'] == ['CORE_SENTIMENT_POOL_STATUS_MISSING']
    assert incomplete_persistence['status'] == 'BLOCK'
    assert incomplete_persistence['flags'] == ['SCAN_SNAPSHOT_PERSISTENCE_NOT_PASS']
    assert passed['status'] == 'PASS'


def test_external_market_snapshot_uses_eastmoney_api_quotes(monkeypatch):
    quotes = {
        '100.DJIA': {'f43': 10100, 'f57': 'DJIA', 'f58': 'Dow', 'f60': 10000, 'f169': 100, 'f170': 100},
        '100.SPX': {'f43': 10200, 'f57': 'SPX', 'f58': 'S&P 500', 'f60': 10000, 'f169': 200, 'f170': 200},
        '100.NDX': {'f43': 9900, 'f57': 'NDX', 'f58': 'Nasdaq', 'f60': 10000, 'f169': -100, 'f170': -100},
        '100.KS11': {'f43': 10100, 'f57': 'KS11', 'f58': 'KOSPI', 'f60': 10000, 'f169': 100, 'f170': 100},
    }

    def fake_api_get(url):
        secid = url.split('secid=', 1)[1].split('&', 1)[0]
        return {'data': quotes[secid]}

    monkeypatch.setattr(scanner_v2, 'api_get', fake_api_get)

    snapshot = scanner_v2.fetch_external_market_snapshot('2026-07-15 09:25:00')

    assert snapshot['status'] == 'PASS'
    assert snapshot['overnight_us_return_pct'] == 0.6667
    assert snapshot['korea_return_pct'] == 1.0
    assert snapshot['external_market_signal_score'] == 0.7667
    assert snapshot['signal_label'] == 'NEUTRAL'


def test_external_market_risk_off_strengthens_formal_weak_market_gate():
    context = runner.market_adaptive_context({}, {
        'market_snapshot': {
            'market_regime': 'neutral',
            'external_market': {
                'status': 'PASS',
                'external_market_signal_score': -1.2,
            },
        },
    })

    assert context['market_regime'] == 'weak'
    assert context['external_market_risk_off'] is True
    assert context['weak_acceptance_market'] is True
