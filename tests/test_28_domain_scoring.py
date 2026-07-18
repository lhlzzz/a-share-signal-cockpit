#!/usr/bin/env python3
"""Tests for 28-domain evidence scoring integration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaogu_v2_1_six_repo_real_integrated import evidence_domain_features, integrated_score


def test_evidence_domain_features_extracts_all_28():
    candidate = {
        'code': '000001',
        'risk_reasons': ['异常波动公告'],
        'catalysts': ['中标公告'],
        'lhb_risk_flags': [],
        'concept_industry_tags': ['银行', '金融'],
        'financial_risk_flags': [],
        'limitup_strength_tags': ['涨停封板'],
        'broken_limit_risk_flags': [],
        'popularity_heat': 15.0,
        'board_strength_tags': ['银行板块领涨'],
        'sector_fund_flow': '净流入2.5亿',
        'concept_capital_flow': '净流入1.8亿',
        'candidate_quote_recheck_tags': ['买一堆积'],
        'candidate_fund_recheck_tags': ['主力净流入'],
        'candidate_lhb_recheck': [{'text': '机构买入'}],
        'candidate_announcement_recheck': [{'text': '中标'}],
        'candidate_intraday_replay': [{'text': '资金流向'}],
        'margin_risk_flags': [],
        'block_trade_flags': [],
        'lockup_risk_flags': [],
        'shareholder_change_flags': ['增持'],
        'research_rating_tags': ['买入评级'],
        'earnings_preview_flags': ['预增'],
        'ipo_calendar_tags': [],
        'trading_halt_flags': [],
        'data_directory_content_evidence': {'record_count': 3},
        'historical_risk_notes': [],
        # 新增因子
        'hsgt_net_inflow': 50000,
        'hsgt_consecutive_days': 3,
        'turnover_rate': 12.5,
        'lockup_days_to_expiry': 30,
        'lockup_amount_ratio': 2.0,
        'announcement_sentiment': 'positive',
        'macro_liquidity_score': 75,
    }
    features = evidence_domain_features(candidate)
    assert isinstance(features, dict)
    assert len(features) == 34  # 28 original + 6 new
    assert 'limitup_momentum' in features
    assert 'broken_limit_penalty' in features
    assert 'sector_flow_boost' in features
    assert 'research_rating_boost' in features
    assert 'hsgt_boost' in features
    assert 'turnover_tier_boost' in features
    assert 'earnings_surprise_boost' in features
    assert 'lockup_pressure_penalty' in features
    assert 'announcement_sentiment_boost' in features
    assert 'macro_liquidity_boost' in features
    assert features['limitup_momentum'] > 0
    assert features['broken_limit_penalty'] == 0
    assert features['sector_flow_boost'] > 0
    assert features['hsgt_boost'] > 0  # 50000 net inflow + 3 consecutive days
    assert features['turnover_tier_boost'] > 0  # 12.5% is medium tier
    assert features['earnings_surprise_boost'] > 0  # 预增
    assert features['lockup_pressure_penalty'] < 0  # 30 days, 2亿 shares
    assert features['announcement_sentiment_boost'] > 0  # positive
    assert features['macro_liquidity_boost'] > 0  # 65 > 50


def test_evidence_domain_features_empty_candidate():
    features = evidence_domain_features({})
    assert isinstance(features, dict)
    assert len(features) == 34  # 28 original + 6 new
    # turnover_tier_boost is -0.2 when turnover is 0 (low turnover)
    non_zero = {k: v for k, v in features.items() if v != 0}
    assert len(non_zero) <= 1  # only turnover_tier_boost may be non-zero
    assert features['turnover_tier_boost'] == -0.2  # 0% turnover = low tier


def test_evidence_domain_features_risk_penalty():
    candidate = {
        'risk_reasons': ['严重异常波动'],
        'margin_risk_flags': ['融券余额大增'],
        'lockup_risk_flags': ['解禁'],
        'trading_halt_flags': ['停牌'],
    }
    features = evidence_domain_features(candidate)
    assert features['regulatory_penalty'] > 0
    assert features['margin_risk_penalty'] > 0
    assert features['lockup_risk_penalty'] > 0
    assert features['halt_block'] > 0


def test_integrated_score_with_evidence_domains():
    base_candidate = {
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

    score_clean, reasons_clean, regime_clean = integrated_score(base_candidate)

    candidate_with_evidence = {
        **base_candidate,
        'catalysts': ['中标公告'],
        'concept_industry_tags': ['机器人', 'AI'],
        'limitup_strength_tags': ['涨停封板'],
        'board_strength_tags': ['机器人板块领涨'],
        'research_rating_tags': ['买入评级'],
        'earnings_preview_flags': ['预增'],
    }
    score_boosted, reasons_boosted, regime_boosted = integrated_score(candidate_with_evidence)

    candidate_with_risk = {
        **base_candidate,
        'risk_reasons': ['异常波动'],
        'broken_limit_risk_flags': ['炸板'],
        'margin_risk_flags': ['融券余额大增'],
        'lockup_risk_flags': ['大额解禁'],
    }
    score_penalized, reasons_penalized, regime_penalized = integrated_score(candidate_with_risk)

    if score_clean is not None and score_boosted is not None:
        assert score_boosted >= score_clean, f"boosted={score_boosted} should >= clean={score_clean}"

    if score_clean is not None and score_penalized is not None:
        assert score_penalized <= score_clean, f"penalized={score_penalized} should <= clean={score_clean}"
