#!/usr/bin/env python3
"""E2E tests for 28-domain scoring integration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaogu_v2_1_six_repo_real_integrated import integrated_score, evidence_domain_features


def _base_candidate():
    return {
        'code': '300001',
        'price': 15.0,
        'signal_pct': 6.5,
        'amount_pctile_rule': 0.7,
        'rank': 20,
        'net_inflow_main': 50000000,
        'market_breadth_up_pct': 50.0,
        'market_limitups': 40,
        'market_bigups': 80,
        'close_position_score': 0.75,
        'volume_ratio': 2.0,
        'full_universe_fund_pctile': 0.6,
        'theme_strength': 5.0,
        'source_layers': ['L2_LIMIT_STRENGTH', 'L3_FUND_FLOW'],
        'search_layer_hint': '',
        'setup_type': '',
        'candidate_stage': '',
        'limitup_capture_score': 0.5,
        'limitup_capture_profile': 'MEDIUM_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': False,
        'limitup_reason_propagation_score': 0.3,
        'sector_opportunity_score': 0.4,
        'sector_catalyst_score': 0.4,
    }


def test_full_candidate_with_all_28_domains():
    candidate = {
        **_base_candidate(),
        'catalysts': ['中标公告5亿元'],
        'risk_reasons': [],
        'lhb_risk_flags': [],
        'concept_industry_tags': ['机器人', 'AI'],
        'financial_risk_flags': [],
        'limitup_strength_tags': ['涨停封板', '连板'],
        'broken_limit_risk_flags': [],
        'popularity_heat': 25.0,
        'board_strength_tags': ['机器人板块领涨'],
        'sector_fund_flow': '净流入3.2亿',
        'concept_capital_flow': '净流入2.1亿',
        'candidate_quote_recheck_tags': ['买一堆积', '委比正'],
        'candidate_fund_recheck_tags': ['主力净流入1.5亿'],
        'candidate_lhb_recheck': [{'text': '机构专用买入'}],
        'candidate_announcement_recheck': [{'text': '中标公告'}],
        'candidate_intraday_replay': [{'text': '资金持续流入'}],
        'margin_risk_flags': [],
        'block_trade_flags': [],
        'lockup_risk_flags': [],
        'shareholder_change_flags': ['增持'],
        'research_rating_tags': ['买入评级', '目标价25元'],
        'earnings_preview_flags': ['预增50%'],
        'ipo_calendar_tags': [],
        'trading_halt_flags': [],
        'data_directory_content_evidence': {'record_count': 5},
        'historical_risk_notes': [],
    }

    features = evidence_domain_features(candidate)
    assert len(features) == 34  # 28 original + 6 new
    assert features['catalyst_boost'] > 0
    assert features['limitup_momentum'] > 0
    assert features['consecutive_limit_bonus'] > 0
    assert features['sector_flow_boost'] > 0
    assert features['concept_flow_boost'] > 0
    assert features['research_rating_boost'] > 0
    assert features['earnings_signal'] > 0
    assert features['regulatory_penalty'] == 0
    assert features['halt_block'] == 0

    score, reasons, regime = integrated_score(candidate)
    assert score is not None, f"Should not be blocked, reasons: {reasons}"
    assert score > 0


def test_candidate_blocked_by_halt_domain():
    candidate = {
        **_base_candidate(),
        'trading_halt_flags': ['重大事项停牌'],
    }
    score, reasons, regime = integrated_score(candidate)
    assert score is None
    assert any('halt_block' in r for r in reasons)


def test_candidate_penalized_by_risk_domains():
    clean_score, _, _ = integrated_score(_base_candidate())
    risky = {
        **_base_candidate(),
        'broken_limit_risk_flags': ['炸板'],
        'margin_risk_flags': ['融券余额大增'],
        'lockup_risk_flags': ['大额解禁'],
    }
    risky_score, risky_reasons, _ = integrated_score(risky)
    if clean_score is not None and risky_score is not None:
        assert risky_score < clean_score, f"risky={risky_score} should < clean={clean_score}"


def test_empty_evidence_candidate():
    score, reasons, regime = integrated_score(_base_candidate())
    assert score is not None, f"Clean candidate should pass, reasons: {reasons}"
