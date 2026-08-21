"""Buyability: infer sealed limit-up from pct even when sealed_limit_up flag is missing."""
from __future__ import annotations

import xiaogu_forward_runner as runner  # noqa: F401 — binds eligibility host
from xiaogu_forward_eligibility import (
    _inferred_sealed_limit_up,
    _mainboard_like_limit_seal_threshold,
    broken_limitup_continuation_exception,
    filter_current_day_tradable_candidates,
    filter_t1_profit_candidates,
    paper_pick_buyability_block_reason,
    paper_pick_eligibility_profile,
    t1_profit_candidate_profile,
)


def test_mainboard_seal_threshold():
    assert _mainboard_like_limit_seal_threshold('002212') == 9.5
    assert _mainboard_like_limit_seal_threshold('600900') == 9.5
    assert _mainboard_like_limit_seal_threshold('300001') == 19.0
    assert _mainboard_like_limit_seal_threshold('688001') == 19.0


def test_infer_sealed_from_pct_without_flag():
    row = {
        'symbol': '002212',
        'name': '天融信',
        'signal_pct': 10.0,
        'sealed_limit_up': False,
        'in_limitup_pool': True,
    }
    assert _inferred_sealed_limit_up(row) is True
    assert paper_pick_buyability_block_reason(row) == 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP'


def test_infer_not_sealed_at_mid_move():
    row = {
        'symbol': '002452',
        'name': '长高电气',
        'signal_pct': 2.74,
        'sealed_limit_up': False,
        'in_limitup_pool': False,
    }
    assert _inferred_sealed_limit_up(row) is False
    assert paper_pick_buyability_block_reason(row) == ''


def test_explicit_flag_still_blocks():
    row = {'symbol': '002298', 'signal_pct': 5.0, 'sealed_limit_up': True}
    assert paper_pick_buyability_block_reason(row) == 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP'


def test_current_day_limitup_is_removed_but_underwater_candidate_survives():
    rows = [
        {'symbol': '002212', 'name': '天融信', 'signal_pct': 10.0},
        {'symbol': '002452', 'name': '长高电气', 'signal_pct': 2.74},
        {'symbol': '002452', 'name': '长高电气 T+1 下跌样本', 'signal_pct': -2.21},
        {'symbol': '600900', 'name': '长江电力', 'signal_pct': 9.49},
    ]

    kept, summary = filter_current_day_tradable_candidates(rows)

    assert [row['signal_pct'] for row in kept] == [2.74, -2.21, 9.49]
    assert summary['dropped_count'] == 1
    assert summary['drop_reasons'] == {'CURRENT_DAY_LIMIT_UP_NOT_TRADABLE': 1}
    assert summary['dropped'][0]['symbol'] == '002212'


def test_regulatory_hard_block_is_removed_from_tradable_pool():
    row = {
        'symbol': '002969',
        'name': '嘉美包装',
        'signal_pct': 4.2,
        'research_signals': {
            'catalyst_quality': {
                'category': 'regulatory_notice',
                'regulatory_hard_block': True,
            },
            'a_share_risk_review': {
                'disqualified_for_paper_pick': True,
            },
        },
    }

    kept, summary = filter_current_day_tradable_candidates([row])

    assert kept == []
    assert summary['drop_reasons'] == {'REGULATORY_HARD_BLOCK:regulatory_notice': 1}


def test_single_historical_nonprofit_result_does_not_remove_candidate(monkeypatch):
    row = {
        'symbol': '002452',
        'name': '长高电气',
        'signal_pct': 4.2,
        'close_position_score': 0.82,
        'volume_ratio': 1.55,
        'net_inflow_main': 80_000_000,
        'fund_flow_momentum': 0.62,
        'continuation_gene_score': 0.20,
        'main_theme_core_score': 0.70,
        'main_theme_alignment_score': 0.85,
        'in_limitup_pool': False,
    }

    monkeypatch.setattr(runner, 'historical_t1_loss_streak_before', lambda *_args: (1, -0.047279))
    kept, summary = filter_t1_profit_candidates([row], {'date': '2026-07-29'})

    assert len(kept) == 1
    assert 'RECENT_T1_NONPROFIT_HARD_BLOCK' not in summary['drop_reasons']


def _strong_limitup_continuation_row() -> dict:
    return {
        'symbol': '600475',
        'name': '华光环能',
        'signal_pct': 8.53,
        'close_position_score': 0.96,
        'volume_ratio': 2.91,
        'fund_flow_momentum': 0.58,
        'net_inflow_main': 50_000_000,
        'continuation_gene_score': 0.70,
        'previous_limitup': True,
        'mainboard_auxiliary_evidence_status': 'PARTIAL',
        'structured_component_details': {
            'pre_limitup_anomaly': 0.86,
            'intraday_alert_strength': 0.78,
        },
    }


def test_controlled_limitup_continuation_exception_admits_strong_buyable_row():
    result = broken_limitup_continuation_exception(
        _strong_limitup_continuation_row(),
        {
            'market_regime': 'weak',
            'market_snapshot': {
                'market_regime': 'weak',
                'limitup_broken_ratio': 0.98,
            },
        },
    )

    assert result['eligible'] is True
    assert '昨日涨停延续，当前仍可交易' in result['reasons_zh']
    assert '涨停基因/延续性' in result['reasons_zh']


def test_controlled_limitup_continuation_exception_keeps_hard_blocks():
    sealed = _strong_limitup_continuation_row()
    sealed.update({'signal_pct': 10.0, 'in_limitup_pool': True})
    assert broken_limitup_continuation_exception(sealed)['eligible'] is False

    regulatory = _strong_limitup_continuation_row()
    regulatory['regulatory_hard_block'] = '监管风险提示'
    result = broken_limitup_continuation_exception(regulatory)
    assert result['eligible'] is False
    assert any('监管硬阻断' in item for item in result['hard_blockers'])


def test_controlled_limitup_continuation_exception_rejects_weak_close_in_weak_market():
    row = _strong_limitup_continuation_row()
    row['close_position_score'] = 0.80
    result = broken_limitup_continuation_exception(
        row,
        {
            'market_regime': 'weak',
            'market_snapshot': {
                'market_regime': 'weak',
                'limitup_broken_ratio': 0.78,
            },
        },
    )

    assert result['eligible'] is False
    assert any('收盘位置不足0.84' in item for item in result['hard_blockers'])


def test_consecutive_historical_nonprofit_results_remove_candidate(monkeypatch):
    row = {
        'symbol': '002452',
        'name': '长高电气',
        'signal_pct': 4.2,
        'close_position_score': 0.82,
        'volume_ratio': 1.55,
        'net_inflow_main': 80_000_000,
        'fund_flow_momentum': 0.62,
        'continuation_gene_score': 0.20,
        'main_theme_core_score': 0.70,
        'main_theme_alignment_score': 0.85,
        'in_limitup_pool': False,
    }

    monkeypatch.setattr(runner, 'historical_t1_loss_streak_before', lambda *_args: (2, -0.047279))
    kept, summary = filter_t1_profit_candidates([row], {'date': '2026-07-29'})

    assert kept == []
    assert summary['drop_reasons']['RECENT_T1_NONPROFIT_HARD_BLOCK'] == 1


def test_underwater_reversal_can_enter_profit_candidate_pool():
    row = {
        'symbol': '600400',
        'name': '水下反转样本',
        'signal_pct': -3.68,
        'candidate_stage': 'underwater',
        'search_layer_hint': 'underwater_reversal',
        'setup_type': 'UNDERWATER_TO_RED_STRENGTH',
        'close_position_score': 0.69,
        'volume_ratio': 2.45,
        'net_inflow_main': 17_932_392,
        'fund_flow_momentum': 0.4062,
        'underwater_recovery_score': 95.6042,
        'weak_to_strong_reversal': 0.8079,
        'first_board_pre_signal': 0.6867,
        'main_theme_core_score': 0.90,
        'sector_opportunity_score': 1.0,
        'in_limitup_pool': False,
    }

    profile = t1_profit_candidate_profile(row)
    assert profile['eligible'] is True
    assert 'underwater_reversal' in profile['confirmations']

    kept, summary = filter_t1_profit_candidates([row])
    assert len(kept) == 1
    assert summary['t1_profit_gate']['admitted_count'] == 1


def test_historical_intraday_reversal_nested_snapshot_enters_profit_pool():
    row = {
        'symbol': '603928',
        'name': '兴业股份',
        'signal_pct': -2.70,
        'raw_json': {
            'setup_type': 'LIMITUP_REASON_PROPAGATION',
            'search_layer_hint': 'intraday_alert_reversal',
            'volume_ratio': 5.43,
            'turnover_rate': 21.28,
            'net_inflow_main': 1.0,
            'paper_pick_eligibility': {
                'signals': {
                    'intraday_alert_strength': 0.4958,
                    'limitup_reason_propagation_score': 0.35,
                },
            },
            'regulatory_hard_block': 'risk_notice',
        },
    }

    profile = t1_profit_candidate_profile(row)

    assert profile['eligible'] is True
    assert 'intraday_reversal' in profile['confirmations']
    assert profile['features']['intraday_reversal'] is True
    assert profile['features']['signal_pct'] == -2.7


def test_historical_underwater_continuation_nested_snapshot_enters_profit_pool():
    row = {
        'symbol': '000807',
        'name': '云铝股份',
        'signal_pct': -0.39,
        'candidate_stage': 'underwater',
        'close_position_score': 0.50,
        'volume_ratio': 1.10,
        'turnover_rate': 2.67,
        'net_inflow_main': 321930165.0,
        'structured_score_components': {
            'fund_flow_momentum': 0.9267,
        },
        'factor_snapshot': {
            'structured_component_details': {
                'sector_news_catalyst_score': 0.92,
                'low_position_catalyst_score': 0.5275,
                'intraday_alert_strength': 0.4034,
            },
        },
    }

    profile = t1_profit_candidate_profile(row)

    assert profile['eligible'] is True
    assert 'underwater_continuation' in profile['confirmations']
    assert profile['features']['underwater_continuation'] is True


def test_longgao_style_weak_t_day_setup_is_not_a_profit_candidate():
    row = {
        'symbol': '002452',
        'name': '长高电气',
        'signal_pct': 2.74,
        'close_position_score': 0.50,
        'volume_ratio': 0.88,
        'net_inflow_main': 2827974.0,
        'fund_flow_momentum': 0.2518,
        'continuation_gene_score': 0.32,
        'previous_limitup': True,
        'main_theme_core_score': 0.50,
        'main_theme_alignment_score': 1.0,
        'announcement_catalyst_score': 1.0,
        'sector_news_catalyst_score': 0.8,
        'direct_symbol_news_count': 0,
        'in_limitup_pool': False,
    }

    profile = t1_profit_candidate_profile(row)
    assert profile['eligible'] is False
    assert profile['reason'] == 'T1_PROFIT_EVIDENCE_INSUFFICIENT'

    kept, summary = filter_t1_profit_candidates([row])
    assert kept == []
    assert summary['drop_reasons']['T1_PROFIT_EVIDENCE_INSUFFICIENT'] == 1


def test_profit_candidate_requires_confirmation_but_admits_momentum_flow_setup():
    row = {
        'symbol': '600001',
        'name': '确认型样本',
        'signal_pct': 4.2,
        'close_position_score': 0.82,
        'volume_ratio': 1.55,
        'net_inflow_main': 80_000_000,
        'fund_flow_momentum': 0.62,
        'continuation_gene_score': 0.20,
        'main_theme_core_score': 0.70,
        'main_theme_alignment_score': 0.85,
        'in_limitup_pool': False,
    }

    profile = t1_profit_candidate_profile(row)
    assert profile['eligible'] is True
    assert 'momentum_and_flow' in profile['confirmations']

    kept, summary = filter_t1_profit_candidates([row])
    assert len(kept) == 1
    assert kept[0]['t1_profit_candidate'] is True
    assert kept[0]['expected_t1_profit_score'] == profile['expected_t1_profit_score']
    assert summary['t1_profit_gate']['admitted_count'] == 1


def test_eligibility_marks_tianrongxin_unbuyable():
    """7/29-style: pct=10, no sealed flag → not final_pick_buyable."""
    row = {
        'symbol': '002212',
        'stock_name': '天融信',
        'price': 6.6,
        'signal_pct': 10.0,
        'pct_chg': 10.0,
        'final_score': 95.0,
        'score': 95.0,
        'rank': 1,
        'candidate_stage': 'near_limit_9_plus',
        'setup_type': 'LIMIT_STRENGTH',
        'in_limitup_pool': True,
        'sealed_limit_up': False,
        'one_lot_cost': 660.0,
        'fund_flow_momentum': 0.9,
        'sector_opportunity_score': 1.0,
        'mainboard_auxiliary_evidence_status': 'PASS',
        'candidate_evidence_status': 'PASS',
        'data_gate_status': 'PASS',
    }
    elig = paper_pick_eligibility_profile(row, {'data_gate_status': 'PASS'})
    assert elig['signals']['final_pick_buyable'] is False
    assert elig['signals']['buyability_hard_block'] == 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP'
    assert 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP' in (elig.get('blockers') or [])
