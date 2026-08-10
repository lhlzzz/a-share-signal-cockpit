"""P0/P1/P2: formal rank alignment, rank diagnostic, DEFENSIVE pe=0 shell demotion."""
from __future__ import annotations

import xiaogu_forward_d1_1450_runner_v0_1 as runner
from xiaogu_evidence_card import build_compact_evidence_card, evidence_card_to_selection_reason
from tests.test_xiaogu_a_share_forward_runner import (
    full_candidate_evidence_counts,
    make_bundle,
    make_candidate,
)


def _defensive_ctx():
    return {
        'favored_sectors': ['电力'],
        'risk_sectors': [],
        'confidence': 0.75,
        'market_stance': 'DEFENSIVE_ROTATION',
        'selected_for_production': False,
        'soft_context_valid': True,
        'high_confidence_allowed': True,
        'post_count': 8,
        'live_post_count': 4,
        'seed_post_count': 0,
        'cache_post_count': 4,
        'soft_context_source': 'live',
        'asof': '2026-07-28',
    }


def _strict_bundle(candidates):
    ranked = runner.apply_formal_profit_ranks([dict(candidate) for candidate in candidates])
    return {
        'available': True,
        'date': '2026-08-05',
        'source_market_date': '2026-08-05',
        'candidate_source': 'v2_scanner_api',
        'production_chain_mode': runner.PRODUCTION_CHAIN_MODE,
        'production_snapshot_origin': 'scan_formal_snapshot',
        'ranking_view': runner.PRODUCTION_RANKING_VIEW,
        'strict_production_chain': True,
        'formal_ranked_pool': ranked,
        'paper_scoring_candidates': ranked,
        'full_candidate_pool': ranked,
        'candidate': ranked[0] if ranked else {},
        'formal_rank_snapshot_id': ranked[0].get('formal_rank_snapshot_id') if ranked else '',
        'formal_rank_snapshot_version': ranked[0].get('formal_rank_snapshot_version') if ranked else '',
    }


def test_strict_production_chain_rejects_legacy_bundle_without_formal_snapshot():
    legacy_candidate = make_candidate('601899', 'Legacy', score=100.0, rank=1)
    bundle = {
        'available': True,
        'date': '2026-08-05',
        'source_market_date': '2026-08-05',
        'candidate_source': 'v2_scanner_api',
        'production_chain_mode': runner.PRODUCTION_CHAIN_MODE,
        'production_snapshot_origin': 'legacy_bundle',
        'ranking_view': runner.PRODUCTION_RANKING_VIEW,
        'strict_production_chain': True,
        'paper_scoring_candidates': [legacy_candidate],
        'candidate': legacy_candidate,
    }

    validation = runner.validate_active_production_chain(bundle, '2026-08-05')
    runner.quarantine_nonproduction_bundle(bundle, validation)

    assert validation['valid'] is False
    assert bundle['paper_scoring_candidates'] == []
    assert bundle['formal_ranked_pool'] == []
    assert bundle['legacy_candidate_basket'][0]['symbol'] == '601899'
    decision = runner.evaluate_candidate_bundle(bundle, '2026-08-05')
    assert decision[0] == 'NO_PICK'
    assert decision[1] == ''
    assert 'ACTIVE_PRODUCTION_CHAIN_NOT_VALID' in decision[2]


def test_strict_production_chain_never_uses_fallback_candidate_for_watch_or_pick():
    bundle = _strict_bundle([make_candidate('601126', '四方股份', score=100.0, rank=1)])
    bundle['paper_scoring_candidates'] = []
    bundle['formal_ranked_pool'] = []
    bundle['full_candidate_pool'] = []

    selected, reason = runner.formal_diagnostic_candidate_from_bundle(bundle)

    assert selected is None
    assert reason == 'no_formal_diagnostic_candidate_available'


def test_strict_persistence_rejects_old_bundle_instead_of_rebuilding_formal_score():
    bundle = {
        'strict_production_chain': True,
        'production_chain_mode': runner.PRODUCTION_CHAIN_MODE,
        'production_snapshot_origin': 'legacy_bundle',
        'ranking_view': runner.PRODUCTION_RANKING_VIEW,
        'paper_scoring_candidates': [
            make_candidate('601899', '紫金矿业', score=79.0, rank=14),
        ],
    }

    payload = runner.build_daily_candidate_persistence_payloads(
        '2026-08-05',
        bundle,
        {},
        'PAPER_PICK',
        'legacy',
    )

    assert payload['status'] == 'PRODUCTION_CHAIN_INVALID'
    assert payload['daily_candidates'] == []


def test_apply_formal_profit_ranks_preserves_pool_rank_and_rewrites_rank():
    """P1: scanner rank becomes pool_rank; rank becomes formal_profit_first order."""
    shell = {
        'symbol': '600900',
        'name': '长江电力',
        'rank': 1,
        'price': 28.0,
        'signal_pct': 2.0,
        'fund_flow_momentum': 0.85,
        'main_theme_core_score': 0.9,
        'continuation_gene_score': 0.0,
        'structured_priority_score': 90.0,
        'capital_risk_profile': {'risk_penalty_score': 0.0},
        'pre_pick_market_context_soft': {
            'market_stance': 'DEFENSIVE_ROTATION',
            'soft_context_valid': True,
            'confidence': 0.7,
            'soft_boost': 0.0,
            'soft_penalty': 0.0,
            'net_soft_bias': 0.0,
        },
    }
    profit = {
        'symbol': '000428',
        'name': '华天酒店',
        'rank': 40,
        'price': 12.0,
        'signal_pct': 9.8,
        'fund_flow_momentum': 0.35,
        'main_theme_core_score': 0.25,
        'continuation_gene_score': 0.70,
        'structured_priority_score': 45.0,
        'sector_yesterday_limitup_gene_proxy': {
            'status': 'PASS',
            'continuation_gene_score': 0.60,
            'sector_matches': [{'count': 3}],
        },
        'capital_risk_profile': {'risk_penalty_score': 0.0},
        'pre_pick_market_context_soft': {
            'market_stance': 'DEFENSIVE_ROTATION',
            'soft_context_valid': True,
            'confidence': 0.7,
            'soft_boost': 0.0,
            'soft_penalty': 0.0,
            'net_soft_bias': 0.0,
        },
    }
    ordered = runner.apply_formal_profit_ranks([shell, profit])
    by_sym = {row['symbol']: row for row in ordered}
    assert by_sym['000428']['pool_rank'] == 40
    assert by_sym['600900']['pool_rank'] == 1
    assert by_sym['000428']['formal_rank'] == 1
    assert by_sym['000428']['rank'] == 1
    assert by_sym['000428']['rank_source'] == 'formal_profit_first'
    assert by_sym['600900']['formal_rank'] > by_sym['000428']['formal_rank']
    assert ordered[0]['symbol'] == '000428'


def test_apply_formal_profit_ranks_stamps_production_score_without_legacy_score_fields():
    row = {
        'symbol': '600001',
        'rank': 1,
        'score': 91.0,
        'final_score': 91.0,
        'signal_pct': 3.0,
        'fund_flow_momentum': 0.7,
        'time_series_momentum': 0.4,
        'low_position_catalyst_score': 0.8,
        'sector_opportunity_score': 0.7,
        'main_theme_core_score': 0.6,
        'main_theme_alignment_score': 0.6,
    }

    ranked = runner.apply_formal_profit_ranks([row])[0]

    assert ranked['rank_source'] == 'formal_profit_first'
    assert ranked['ranking_view'] == 'main_force_behavior_chain'
    assert ranked['score_source'] == 'formal_t1_profit_components'
    assert 'legacy_score' not in ranked
    assert 'legacy_final_score' not in ranked
    assert ranked['production_score'] == ranked['formal_primary_score']
    assert ranked['score'] == ranked['production_score']
    assert ranked['final_score'] == ranked['production_score']
    assert 0.0 <= ranked['production_score'] <= 100.0


def test_synchronize_formal_profit_rank_state_shares_full_pool_score():
    full = {
        'symbol': '600001',
        'rank': 1,
        'score': 91.0,
        'final_score': 91.0,
        'signal_pct': 3.0,
        'fund_flow_momentum': 0.7,
        'expected_t1_profit_score': 0.7,
    }
    decision = {
        **full,
        'search_layer': 'formal_high_score',
        'final_score': 12.0,
        'score': 12.0,
    }
    bundle = {
        'full_candidate_pool': [full],
        'paper_scoring_candidates': [decision],
        'candidate': decision,
    }

    runner.synchronize_formal_profit_rank_state(bundle)

    full_score = bundle['full_candidate_pool'][0]['final_score']
    decision_row = bundle['paper_scoring_candidates'][0]
    assert decision_row['final_score'] == full_score
    assert decision_row['formal_primary_score'] == full_score
    assert decision_row['rank'] == bundle['full_candidate_pool'][0]['rank']
    assert decision_row['search_layer'] == 'formal_high_score'
    assert bundle['candidate']['final_score'] == full_score


def test_freeze_formal_snapshot_promotes_full_pool_rank_over_layered_search_pool():
    first = make_candidate(
        '601126',
        '四方股份',
        score=100.0,
        rank=31,
        signal_pct=5.4,
        fund_flow_momentum=0.95,
    )
    first['continuation_gene_score'] = 0.7
    second = make_candidate(
        '601899',
        '紫金矿业',
        score=78.0,
        rank=1,
        signal_pct=6.1,
        fund_flow_momentum=0.92,
    )
    second['main_theme_core_score'] = 1.0
    full = [first, second]
    bundle = {
        'full_candidate_pool': full,
        'paper_scoring_candidates': [dict(full[1])],
        'daily_ticket_search_result': {},
    }

    runner.freeze_formal_production_snapshot(bundle)

    assert bundle['paper_scoring_candidates'][0]['symbol'] == bundle['full_candidate_pool'][0]['symbol']
    assert bundle['paper_scoring_candidates'][0]['formal_rank'] == 1
    assert {
        row['formal_rank_snapshot_id'] for row in bundle['formal_ranked_pool']
    } == {bundle['formal_rank_snapshot_id']}
    assert bundle['formal_rank_snapshot_validation']['valid'] is True
    assert bundle['daily_ticket_search_result']['production_selection_source'] == 'formal_ranked_full_pool'


def test_persistence_consumes_frozen_snapshot_without_recomputing_formal_rank(monkeypatch):
    full = make_candidate('600001', '冻结快照', score=88.0, rank=1)
    ranked = runner.apply_formal_profit_ranks([full])
    bundle = {
        'full_candidate_pool': ranked,
        'formal_ranked_pool': ranked,
        'paper_scoring_candidates': ranked,
        't1_profit_gate_enabled': False,
        'candidate_pool_exclusion_summary': {'target_count': 1},
    }

    def fail_recompute(_rows):
        raise AssertionError('frozen formal snapshot was recomputed')

    monkeypatch.setattr(runner, 'apply_formal_profit_ranks', fail_recompute)
    payload = runner.build_daily_candidate_persistence_payloads(
        '2026-08-05',
        bundle,
        {'candidate_consumption_summary': {'official_result': {'symbol': '600001'}}},
        'PAPER_PICK',
        'selected',
    )

    assert payload['status'] == 'OK'
    assert payload['daily_candidates'][0]['ranking_basis']['formal_rank_snapshot_id'] == ranked[0]['formal_rank_snapshot_id']


def test_formal_sort_and_official_priority_use_t1_profit_score_first():
    lower_structure = {
        'symbol': '600900',
        'name': '防御壳',
        'rank': 1,
        'price': 28.0,
        'signal_pct': 2.0,
        'expected_t1_profit_score': 0.50,
        'main_theme_core_score': 0.95,
        'fund_flow_momentum': 0.90,
        'structured_priority_score': 95.0,
    }
    continuation = {
        'symbol': '603178',
        'name': '圣龙股份',
        'rank': 80,
        'price': 16.0,
        'signal_pct': 1.44,
        'expected_t1_profit_score': 0.86,
        'continuation_gene_score': 0.70,
        'previous_limitup': True,
        'fund_flow_momentum': 0.60,
        'close_position_score': 0.90,
        'volume_ratio': 2.20,
        'structured_priority_score': 55.0,
    }
    assert runner.formal_candidate_sort_key(continuation) > runner.formal_candidate_sort_key(lower_structure)


def test_official_evidence_card_is_chinese_and_excludes_repo_noise():
    card = build_compact_evidence_card(
        {
            'symbol': '600475',
            'name': '华光环能',
            'final_score': 78.57,
            'signal_pct': 8.53,
            'fund_flow_momentum': 0.58,
            'net_inflow_main': 50_000_000,
            'volume_ratio': 2.91,
            'close_position_score': 0.96,
            'continuation_gene_score': 0.70,
            'expected_t1_profit_score': 1.0,
            'announcement_catalyst_score': 0.80,
            'repo_contribution_summary': 'NOISE_SHOULD_NOT_BE_VISIBLE',
        },
        decision='PAPER_PICK',
        reason='ALL_FORWARD_PAPER_HARD_GATES_PASS',
    )
    reason = evidence_card_to_selection_reason(card)
    visible = ' '.join(
        [card['one_liner'], *card['fund_flow'], *card['main_theme'], card['decision_reason']]
    )
    assert 'NOISE_SHOULD_NOT_BE_VISIBLE' not in visible
    assert '主力资金动量' in visible
    assert 'T+1获利证据分' in ' '.join(card['profit_evidence'])
    assert '全部正式出票门禁通过' in visible
    assert reason['legacy_repo_summary'] == ''


def test_single_target_card_does_not_show_controlled_continuation_as_blocked(monkeypatch):
    candidate = {
        'symbol': '600475',
        'name': '华光环能',
        'price': 20.0,
        'opportunity_hard_block': 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        'fund_flow_momentum': 0.40,
    }
    bundle = {'market_regime': 'weak'}
    monkeypatch.setattr(
        runner,
        'broken_limitup_continuation_exception',
        lambda row, context: {'eligible': True, 'reasons_zh': ['昨日涨停延续']},
    )

    card = runner.build_single_target_card(
        'NO_PICK',
        '600475',
        'CONTROLLED_CONTINUATION_REVIEW',
        candidate,
        bundle,
        ['CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'],
        False,
    )

    assert 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' not in card['blockers']
    assert card['hard_gate_status']['chase_high_without_limitup_confirmation'] is False
    assert card['hard_gate_status']['limitup_continuation_exception'] is True


def test_production_lifecycle_ignores_later_horizon_payoffs(monkeypatch):
    monkeypatch.setattr(
        runner,
        '_candidate_lifecycle_history',
        lambda symbol, target_date: (
            {
                'trade_date': '2026-07-30',
                't1_return': -0.02,
                't2_return': 0.08,
                't3_return': 0.12,
                'payoff_class': 'delayed_winner',
            },
        ),
    )
    profile = runner._candidate_lifecycle_profile(
        {
            'symbol': '600475',
            'trade_date': '2026-07-31',
            'main_theme_core_score': 0.9,
            'main_theme_alignment_score': 0.9,
        },
        {'date': '2026-07-31'},
    )

    assert profile['production_policy'] == 'T_DAY_BUY_T1_PROFIT'
    assert profile['primary_trade_horizon'] == 't1_close'
    assert profile['history_has_delayed_winner'] is False
    assert profile['delayed_support'] is False
    assert profile['setup_class'] != 'DELAYED_SETUP'


def test_defensive_stance_adds_pe0_shell_penalty(monkeypatch):
    """P2: DEFENSIVE + pe=0 + hot fund → defensive_pe0_hot_fund_shell soft penalty."""
    monkeypatch.setattr(runner, 'load_pre_pick_market_context', lambda trade_date='': _defensive_ctx())
    shell = {
        'symbol': '002487',
        'name': '大金重工',
        'trade_date': '2026-07-27',
        'price': 22.0,
        'signal_pct': 6.15,
        'fund_flow_momentum': 0.90,
        'main_theme_core_score': 0.85,
        'continuation_gene_score': 0.0,
        'capital_risk_profile': {'risk_penalty_score': 0.0},
    }
    edge = {
        'symbol': '002309',
        'name': '中利集团',
        'trade_date': '2026-07-27',
        'price': 8.0,
        'signal_pct': 9.9,
        'fund_flow_momentum': 0.40,
        'main_theme_core_score': 0.30,
        'continuation_gene_score': 0.70,
        'sector_yesterday_limitup_gene_proxy': {
            'status': 'PASS',
            'continuation_gene_score': 0.60,
            'sector_matches': [{'count': 4}],
        },
        'capital_risk_profile': {'risk_penalty_score': 0.0},
    }
    shell_adj = runner.ranking_basis_adjustment_components(shell)
    edge_adj = runner.ranking_basis_adjustment_components(edge)
    assert float(shell_adj.get('profit_edge_score') or 0) < 0.15
    assert float((shell_adj.get('penalties') or {}).get('hot_fund_shell_without_profit_edge') or 0) >= 0.55
    assert float((shell_adj.get('penalties') or {}).get('defensive_pe0_hot_fund_shell') or 0) > 0
    assert float(edge_adj.get('profit_edge_score') or 0) >= 0.25
    assert float((edge_adj.get('penalties') or {}).get('defensive_pe0_hot_fund_shell') or 0) == 0
    assert runner.formal_candidate_sort_key(edge) > runner.formal_candidate_sort_key(shell)


def test_proxy_only_continuation_gene_does_not_boost_formal_rank():
    proxy_only = make_candidate(
        '002452',
        '代理基因',
        score=84.0,
        rank=1,
        signal_pct=6.0,
        fund_flow_momentum=0.45,
        main_theme_core_score=0.20,
        main_theme_alignment_score=0.20,
    )
    proxy_only.update({
        'continuation_gene_score': 0.70,
        'limitup_reason_status': 'PROXY',
        'sector_yesterday_limitup_gene_proxy': {
            'status': 'PROXY',
            'continuation_gene_score': 0.70,
            'sector_matches': [{'sector': '电网设备', 'count': 2, 'proxy': True}],
        },
        'yesterday_limitup_gene_evidence': {
            'status': 'MISSING',
            'candidate_was_yesterday_limitup': False,
            'records': [],
        },
    })

    evidence = runner.continuation_gene_evidence(proxy_only)
    adjustment = runner.ranking_basis_adjustment_components(proxy_only)

    assert evidence['proxy_only'] is True
    assert evidence['effective_score'] == 0.0
    assert adjustment['boosts']['continuation_gene_score'] == 0.0
    assert adjustment['boosts']['sector_yesterday_limitup_gene_proxy'] == 0.0
    assert adjustment['continuation_gene_evidence']['source'] == 'sector_yesterday_limitup_proxy_only'


def test_candidate_yesterday_limitup_gene_is_preserved_against_sector_proxy():
    confirmed = make_candidate(
        '002039',
        '真实基因',
        score=84.0,
        rank=1,
        signal_pct=6.0,
        fund_flow_momentum=0.45,
        main_theme_core_score=0.20,
        main_theme_alignment_score=0.20,
    )
    confirmed.update({
        'continuation_gene_score': 0.70,
        'limitup_reason_status': 'PROXY',
        'sector_yesterday_limitup_gene_proxy': {
            'status': 'PROXY',
            'continuation_gene_score': 0.70,
            'sector_matches': [{'sector': '电力', 'count': 2, 'proxy': True}],
        },
        'yesterday_limitup_gene_evidence': {
            'status': 'PROXY',
            'candidate_was_yesterday_limitup': True,
            'records': [{'symbol': '002039', 'source': 'limitup_yesterday'}],
        },
    })

    evidence = runner.continuation_gene_evidence(confirmed)
    adjustment = runner.ranking_basis_adjustment_components(confirmed)

    assert evidence['proxy_only'] is False
    assert evidence['effective_score'] == 0.70
    assert adjustment['boosts']['continuation_gene_score'] > 0.0


def test_rank_alignment_diagnostic_exposes_pool_vs_formal():
    """P0: diagnostic surfaces pool_rank vs formal_rank top divergence."""
    rows = runner.apply_formal_profit_ranks([
        {
            'symbol': '600001',
            'name': '池顶',
            'rank': 1,
            'price': 10.0,
            'signal_pct': 1.0,
            'fund_flow_momentum': 0.2,
            'main_theme_core_score': 0.1,
            'continuation_gene_score': 0.0,
            'structured_priority_score': 99.0,
            'capital_risk_profile': {'risk_penalty_score': 0.0},
        },
        {
            'symbol': '600002',
            'name': '利润边',
            'rank': 25,
            'price': 11.0,
            'signal_pct': 8.5,
            'fund_flow_momentum': 0.4,
            'main_theme_core_score': 0.2,
            'continuation_gene_score': 0.65,
            'structured_priority_score': 40.0,
            'sector_yesterday_limitup_gene_proxy': {
                'status': 'PASS',
                'continuation_gene_score': 0.55,
                'sector_matches': [{'count': 3}],
            },
            'capital_risk_profile': {'risk_penalty_score': 0.0},
        },
    ])
    diag = runner.build_rank_alignment_diagnostic(rows, rows[0], top_n=2)
    assert diag['rank_source'] == 'formal_profit_first'
    assert diag['candidate_count'] == 2
    assert diag['formal_top'][0]['symbol'] == '600002'
    assert diag['pool_top'][0]['symbol'] == '600001'
    assert diag['formal_top'][0]['pool_rank'] == 25
    assert diag['first_clean']['symbol'] == rows[0]['symbol']


def test_first_clean_formal_challenge_replaces_pe0_shell(monkeypatch):
    """P2: layer-order pe=0 shell can be challenged by formal profit-edge clean row."""
    monkeypatch.setattr(runner, 'load_pre_pick_market_context', lambda trade_date='': _defensive_ctx())
    required, enhanced = full_candidate_evidence_counts()

    def _eligible_ok(row, bundle):
        return {
            'eligible': True,
            'blockers': [],
            'missing_conditions': [],
            'signals': {},
        }

    monkeypatch.setattr(runner, 'paper_pick_eligibility_profile', _eligible_ok)
    monkeypatch.setattr(runner, 'official_target_exclusion_reasons', lambda row, bundle: [])

    shell = make_candidate(
        '002487',
        '大金重工',
        score=88.0,
        rank=1,
        price=22.0,
        sector_score=0.9,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='mid_5_to_7',
        signal_pct=6.15,
        close_position_score=0.70,
        fund_flow_momentum=0.90,
        time_series_momentum=0.20,
        main_theme_core_score=0.85,
        candidate_evidence_domain_counts=required,
        enhanced_evidence_domain_counts=enhanced,
    )
    shell['continuation_gene_score'] = 0.0
    shell['search_layer'] = 'formal_high_score'
    shell['trade_date'] = '2026-07-27'

    profit = make_candidate(
        '000428',
        '华天酒店',
        score=70.0,
        rank=40,
        price=12.0,
        sector_score=0.4,
        search_layer_hint='profit_continuation',
        setup_type='LIMIT_STRENGTH',
        candidate_stage='near_limit_9_plus',
        signal_pct=9.8,
        close_position_score=0.82,
        fund_flow_momentum=0.35,
        time_series_momentum=0.40,
        main_theme_core_score=0.25,
        candidate_evidence_domain_counts=required,
        enhanced_evidence_domain_counts=enhanced,
    )
    profit['continuation_gene_score'] = 0.70
    profit['search_layer'] = 'profit_continuation'
    profit['trade_date'] = '2026-07-27'
    profit['sector_yesterday_limitup_gene_proxy'] = {
        'status': 'PASS',
        'continuation_gene_score': 0.60,
        'sector_matches': [{'count': 3}],
    }

    selected, meta = runner.select_first_clean_with_formal_challenge([shell, profit], {})
    assert selected is not None
    symbol = selected.get('symbol') or selected.get('code')
    assert symbol == '000428'
    assert meta['challenged'] is True
    assert 'formal_rank_replaced_layer_order' in str(meta.get('challenge_reason') or '')


def test_build_daily_ticket_search_rows_stamps_formal_rank(monkeypatch):
    """Search path stamps formal ranks on enriched pool (P0/P1 wire)."""
    monkeypatch.setattr(runner, 'load_pre_pick_market_context', lambda trade_date='': _defensive_ctx())
    required, enhanced = full_candidate_evidence_counts()
    low = make_candidate(
        '600900',
        '长江电力',
        score=90.0,
        rank=1,
        price=28.0,
        sector_score=0.8,
        search_layer_hint='formal_high_score',
        setup_type='FORMAL_HIGH_SCORE',
        candidate_stage='flat_0_to_3',
        signal_pct=1.5,
        close_position_score=0.55,
        fund_flow_momentum=0.80,
        time_series_momentum=0.10,
        main_theme_core_score=0.80,
        candidate_evidence_domain_counts=required,
        enhanced_evidence_domain_counts=enhanced,
    )
    low['continuation_gene_score'] = 0.0
    high = make_candidate(
        '000428',
        '华天酒店',
        score=72.0,
        rank=40,
        price=12.0,
        sector_score=0.5,
        search_layer_hint='profit_continuation',
        setup_type='LIMIT_STRENGTH',
        candidate_stage='near_limit_9_plus',
        signal_pct=9.5,
        close_position_score=0.85,
        fund_flow_momentum=0.40,
        time_series_momentum=0.35,
        main_theme_core_score=0.30,
        candidate_evidence_domain_counts=required,
        enhanced_evidence_domain_counts=enhanced,
    )
    high['continuation_gene_score'] = 0.65
    high['sector_yesterday_limitup_gene_proxy'] = {
        'status': 'PASS',
        'continuation_gene_score': 0.55,
        'sector_matches': [{'count': 3}],
    }
    bundle = make_bundle([low, high], candidate_source='eastmoney_api_scan_v2')
    result = runner.build_daily_ticket_search_rows([low, high], bundle)
    formal_pool = result.get('formal_ranked_pool') or []
    by_sym = {str(row.get('symbol') or row.get('code')): row for row in formal_pool}
    assert '000428' in by_sym
    assert by_sym['000428'].get('rank_source') == 'formal_profit_first'
    assert by_sym['000428'].get('pool_rank') == 40
    assert int(by_sym['000428'].get('formal_rank') or 99) <= int(by_sym['600900'].get('formal_rank') or 99)
    diag = result.get('rank_alignment_diagnostic') or {}
    assert diag.get('rank_source') == 'formal_profit_first'
    assert diag.get('candidate_count') >= 2
