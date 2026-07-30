"""P0/P1/P2: formal rank alignment, rank diagnostic, DEFENSIVE pe=0 shell demotion."""
from __future__ import annotations

import xiaogu_forward_d1_1450_runner_v0_1 as runner
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
    assert 'formal_challenge' in str(meta.get('challenge_reason') or '')


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
    bundle = make_bundle([low, high], candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo')
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
