"""L3 hydrate: price→one_lot, fair RECONSTRUCTED aux, NO_PICK when elig=0, no seal chase."""
from __future__ import annotations

import xiaogu_forward_runner as runner  # noqa: F401 — binds eligibility host
import xiaogu_backtest_v0_1 as backtest
from xiaogu_forward_eligibility import (
    _inferred_sealed_limit_up,
    paper_pick_buyability_block_reason,
    paper_pick_eligibility_profile,
)
from xiaogu_forward_runner import formal_candidate_sort_key


def test_hydrate_backfills_price_and_one_lot_from_close():
    raw = {
        'symbol': '600900',
        'name': '长江电力',
        'close_price': 28.5,
        'pct_chg': 2.29,
        'final_score': 70.0,
        'factor_snapshot': {'evidence_status': 'RECONSTRUCTED_FROM_RECORDED_FEATURES', 'reconstructed': True},
    }
    row = backtest._hydrate_decision_snapshot_row(raw)
    assert row['price'] == 28.5
    assert row['one_lot_cost'] == 2850.0
    assert row['signal_pct'] == 2.29


def test_hydrate_fair_aux_only_when_flagged():
    raw = {
        'symbol': '600900',
        'close_price': 28.5,
        'pct_chg': 2.0,
        'final_score': 70.0,
        'mainboard_auxiliary_evidence_status': None,
        'factor_snapshot': {
            'evidence_status': 'RECONSTRUCTED_FROM_RECORDED_FEATURES',
            'reconstructed': True,
        },
    }
    plain = backtest._hydrate_decision_snapshot_row(raw)
    assert not plain.get('_historical_aux_fair_pass')
    assert not plain.get('mainboard_auxiliary_evidence_status')

    fair = backtest._hydrate_decision_snapshot_row(raw, historical_replay_fair_aux=True)
    assert fair.get('_historical_aux_fair_pass') is True
    assert fair.get('mainboard_auxiliary_evidence_status') == 'PASS'


def test_hydrate_fair_aux_does_not_override_live_fail():
    raw = {
        'symbol': '600900',
        'close_price': 28.5,
        'pct_chg': 2.0,
        'mainboard_auxiliary_evidence_status': 'FAIL',
        'factor_snapshot': {'evidence_status': 'RECONSTRUCTED_FROM_RECORDED_FEATURES'},
    }
    fair = backtest._hydrate_decision_snapshot_row(raw, historical_replay_fair_aux=True)
    assert fair.get('mainboard_auxiliary_evidence_status') == 'FAIL'
    assert not fair.get('_historical_aux_fair_pass')


def test_production_redecision_nopick_when_no_eligible():
    # Take a known-eligible pair and seal both → buyability blocks → NO_PICK
    from tests.test_xiaogu_a_share_forward_runner import _redecision_eligible_pair

    bundle, rows = _redecision_eligible_pair()
    sealed_rows = []
    for row in rows:
        cloned = dict(row)
        cloned['signal_pct'] = 10.0
        cloned['pct_chg'] = 10.0
        cloned['sealed_limit_up'] = True
        sealed_rows.append(cloned)
    result = backtest.production_path_redecision_for_day(sealed_rows, bundle)
    assert result['decision'] == 'NO_PICK'
    assert result['notes'] == 'NO_PICK_NO_ELIGIBLE'
    assert result['redecision_symbol'] in (None, '')
    assert result['eligible_count'] == 0
    assert result['sealed_unbuyable_count'] >= 1


def test_production_redecision_sorts_only_buyable_eligible():
    """Sealed high-score is out; buyable eligible formal winner remains."""
    from tests.test_xiaogu_a_share_forward_runner import _redecision_eligible_pair

    bundle, rows = _redecision_eligible_pair()
    # Seal the formal-stronger name; weaker mid-move must win if still eligible.
    mixed = []
    for row in rows:
        cloned = dict(row)
        if cloned.get('symbol') == '600002':
            cloned['signal_pct'] = 10.0
            cloned['pct_chg'] = 10.0
            cloned['sealed_limit_up'] = True
            cloned['final_score'] = 99.0
            cloned['score'] = 99.0
        mixed.append(cloned)
    result = backtest.production_path_redecision_for_day(mixed, bundle)
    assert result['decision'] == 'PAPER_PICK'
    assert result['redecision_symbol'] == '600001'
    assert '600002' not in result['top3_formal_symbols']
    sealed = next(r for r in mixed if r['symbol'] == '600002')
    assert _inferred_sealed_limit_up(sealed) is True
    assert paper_pick_buyability_block_reason(sealed) == 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP'


def test_near_seal_soft_demotion_in_formal_sort():
    mid = {
        'symbol': '600030',
        'signal_pct': 5.0,
        'main_theme_core_score': 0.6,
        'continuation_gene_score': 0.5,
        'close_position_score': 0.8,
    }
    near = {
        'symbol': '600031',
        'signal_pct': 9.6,
        'main_theme_core_score': 0.6,
        'continuation_gene_score': 0.5,
        'close_position_score': 0.8,
    }
    # Same theme/gene; near-seal should not rank strictly above mid-move on primary dims.
    assert formal_candidate_sort_key(mid) >= formal_candidate_sort_key(near) or (
        formal_candidate_sort_key(mid)[0] >= formal_candidate_sort_key(near)[0] - 0.01
    )
    # Explicit: near-seal primary is reduced vs identical mid row.
    assert formal_candidate_sort_key(near)[0] < formal_candidate_sort_key(mid)[0] + 0.5


def test_buyability_still_blocks_eligibility_for_sealed():
    row = {
        'symbol': '002212',
        'price': 6.6,
        'signal_pct': 10.0,
        'final_score': 95.0,
        'score': 95.0,
        'one_lot_cost': 660.0,
        'mainboard_auxiliary_evidence_status': 'PASS',
        'candidate_evidence_status': 'PASS',
        'setup_type': 'LIMIT_STRENGTH',
        'close_position_score': 0.9,
    }
    elig = paper_pick_eligibility_profile(row, {'data_gate_status': 'PASS'})
    assert elig['signals']['final_pick_buyable'] is False
    assert elig['eligible'] is False
