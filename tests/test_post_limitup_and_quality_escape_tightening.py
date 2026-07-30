"""Regression: 7/23 山金 hollow seed soft + post-limitup weak continuation."""
from __future__ import annotations

import xiaogu_forward_d1_1450_runner_v0_1 as runner
from tests.test_xiaogu_a_share_forward_runner import (
    full_candidate_evidence_counts,
    make_bundle,
    make_candidate,
)


def _weak_market_bundle(candidates):
    return make_bundle(
        candidates,
        candidate_source='eastmoney_web_tabs_scan_v0_1_four_repo',
        market_snapshot={
            'market_breadth_up_pct': 26.0,
            'market_limitups': 78,
            'broken_limitups': 36,
            'limitup_broken_ratio': 1.27,
            'market_regime': 'weak',
        },
        market_regime='weak',
    )


def test_seed_soft_hollow_theme_cannot_hard_waive_quality_escape(monkeypatch):
    """7/23 山金 path: seed soft + main_theme_core=0 must not waive hard blocks."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': ['贵金属'],
            'risk_sectors': [],
            'confidence': 0.55,
            'market_stance': 'RISK_OFF_TECH_DEFENSIVE',
            'selected_for_production': False,
            'soft_context_valid': True,
            'high_confidence_allowed': False,
            'post_count': 5,
            'live_post_count': 0,
            'seed_post_count': 5,
            'cache_post_count': 0,
            'soft_context_source': 'seed',
            'asof': '2026-07-23',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '000975',
        '山金国际',
        score=73.55,
        rank=22,
        price=22.81,
        sector_score=1.0,
        search_layer_hint='formal_high_score',
        setup_type='ACCUMULATION_READY',
        candidate_stage='flat_0_to_3',
        signal_pct=1.29,
        close_position_score=0.55,
        fund_flow_momentum=0.1774,
        time_series_momentum=0.15,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.25,
        mainboard_auxiliary_missing_domains=['announcements', 'direct_symbol_news'],
        sector_opportunity_tags=['贵金属', '黄金'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-23'
    candidate['main_theme_core_score'] = 0.0
    candidate['main_theme_alignment_score'] = 0.0
    candidate['theme_tags'] = ['黄金']
    candidate['source_layers'] = ['L0_FULL_UNIVERSE']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    # May still mark diagnostic quality_escape, but hard waive must fail.
    assert signals.get('quality_escape_hard_waive_ok') is False
    assert signals.get('quality_escape_partial_aux_exception') is not True
    assert 'mainboard_auxiliary_evidence_status_not_PASS' in (eligibility.get('blockers') or [])


def test_post_limitup_weak_continuation_blocks_paper_pick(monkeypatch):
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': ['贵金属'],
            'risk_sectors': [],
            'confidence': 1.0,
            'market_stance': 'RISK_OFF_TECH_DEFENSIVE',
            'selected_for_production': False,
            'soft_context_valid': True,
            'high_confidence_allowed': True,
            'post_count': 5,
            'live_post_count': 3,
            'seed_post_count': 0,
            'cache_post_count': 2,
            'soft_context_source': 'live+cache',
            'asof': '2026-07-23',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '000975',
        '山金国际',
        score=80.0,
        rank=5,
        price=22.81,
        sector_score=1.0,
        search_layer_hint='formal_high_score',
        setup_type='ACCUMULATION_READY',
        candidate_stage='flat_0_to_3',
        signal_pct=1.29,
        close_position_score=0.70,
        fund_flow_momentum=0.20,
        time_series_momentum=0.20,
        research_panel_overall='PASS',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.80,
        sector_opportunity_tags=['贵金属', '黄金'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-23'
    candidate['main_theme_core_score'] = 0.1
    candidate['main_theme_alignment_score'] = 0.1
    candidate['theme_tags'] = ['黄金']
    candidate['yesterday_limitup_gene_evidence'] = {
        'status': 'PASS',
        'candidate_was_yesterday_limitup': True,
    }
    candidate['prev_day_pct_chg'] = 10.01
    candidate['source_layers'] = ['L0_FULL_UNIVERSE']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    assert (eligibility.get('signals') or {}).get('post_limitup_weak_continuation') is True
    assert 'post_limitup_weak_continuation' in (eligibility.get('blockers') or [])
    assert eligibility.get('eligible') is False


def test_live_soft_with_theme_still_allows_quality_hard_waive(monkeypatch):
    """Control: live soft + real theme can still hard-waive PARTIAL aux."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': ['有色'],
            'risk_sectors': [],
            'confidence': 1.0,
            'market_stance': 'RISK_OFF_TECH_DEFENSIVE',
            'selected_for_production': False,
            'soft_context_valid': True,
            'high_confidence_allowed': True,
            'post_count': 5,
            'live_post_count': 3,
            'seed_post_count': 0,
            'cache_post_count': 2,
            'soft_context_source': 'live+cache',
            'asof': '2026-07-22',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '601899',
        '紫金矿业',
        score=90.0,
        rank=14,
        price=18.0,
        sector_score=0.85,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.5,
        close_position_score=0.80,
        fund_flow_momentum=0.92,
        time_series_momentum=0.25,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.25,
        mainboard_auxiliary_missing_domains=['announcements', 'direct_symbol_news', 'limitup_reasons'],
        sector_opportunity_tags=['有色金属', '黄金概念'],
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-22'
    candidate['main_theme_core_score'] = 0.55
    candidate['main_theme_alignment_score'] = 0.50
    candidate['source_layers'] = ['L0_FULL_UNIVERSE', 'L1_HOT_MOMENTUM']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('quality_escape_hard_waive_ok') is True
    assert signals.get('quality_escape_partial_aux_exception') is True
    assert 'mainboard_auxiliary_evidence_status_not_PASS' not in (eligibility.get('blockers') or [])


def test_hollow_theme_fund_shell_blocks_lianhua_style(monkeypatch):
    """7/13 莲花型: empty theme + strong fund shell must hard-block PAPER_PICK."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': [],
            'risk_sectors': [],
            'confidence': 0.0,
            'market_stance': 'NEUTRAL',
            'selected_for_production': False,
            'soft_context_valid': False,
            'high_confidence_allowed': False,
            'post_count': 0,
            'live_post_count': 0,
            'seed_post_count': 0,
            'cache_post_count': 0,
            'soft_context_source': '',
            'asof': '2026-07-13',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '600186',
        '莲花控股',
        score=65.87,
        rank=4,
        price=11.22,
        sector_score=0.2,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='flat_0_to_3',
        signal_pct=1.19,
        close_position_score=0.50,
        fund_flow_momentum=1.0,
        time_series_momentum=0.20,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.80,
        sector_opportunity_tags=['消费', '食品'],
        main_theme_core_score=0.0,
        main_theme_alignment_score=0.0,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-13'
    candidate['news_catalyst_strength'] = 0.0
    candidate['announcement_catalyst_score'] = 0.0
    candidate['sector_catalyst_score'] = 0.0
    candidate['sector_news_catalyst_score'] = 0.36
    candidate['source_layers'] = ['L0_FULL_UNIVERSE']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('hollow_theme_fund_shell') is True
    assert 'hollow_theme_fund_shell' in (eligibility.get('blockers') or [])
    assert eligibility.get('eligible') is False


def test_hollow_theme_weak_fund_not_hard_blocked(monkeypatch):
    """Weak-fund hollow (not fund shell) stays out of R1 hard — avoid 亿道/新天然气-class FP."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': [],
            'risk_sectors': [],
            'confidence': 0.0,
            'market_stance': 'NEUTRAL',
            'selected_for_production': False,
            'soft_context_valid': False,
            'high_confidence_allowed': False,
            'post_count': 0,
            'live_post_count': 0,
            'seed_post_count': 0,
            'cache_post_count': 0,
            'soft_context_source': '',
            'asof': '2026-07-16',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '001314',
        '亿道信息',
        score=58.51,
        rank=10,
        price=62.4,
        sector_score=0.2,
        search_layer_hint='formal_high_score',
        setup_type='ACCUMULATION_READY',
        candidate_stage='flat_0_to_3',
        signal_pct=1.23,
        close_position_score=0.55,
        fund_flow_momentum=0.53,
        time_series_momentum=0.20,
        research_panel_overall='PASS',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.80,
        main_theme_core_score=0.0,
        main_theme_alignment_score=0.0,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-16'
    candidate['news_catalyst_strength'] = 0.0
    candidate['announcement_catalyst_score'] = 0.0
    candidate['sector_catalyst_score'] = 0.0
    candidate['sector_news_catalyst_score'] = 0.0
    candidate['source_layers'] = ['L0_FULL_UNIVERSE']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('hollow_theme_empty') is True
    assert signals.get('fund_shell_strong') is False
    assert signals.get('hollow_theme_fund_shell') is not True
    assert 'hollow_theme_fund_shell' not in (eligibility.get('blockers') or [])


def test_real_theme_not_blocked_by_hollow_fund_shell(monkeypatch):
    """Control: real theme core + strong fund is not R1 hollow fund shell."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': ['有色'],
            'risk_sectors': [],
            'confidence': 1.0,
            'market_stance': 'RISK_OFF_TECH_DEFENSIVE',
            'selected_for_production': False,
            'soft_context_valid': True,
            'high_confidence_allowed': True,
            'post_count': 5,
            'live_post_count': 3,
            'seed_post_count': 0,
            'cache_post_count': 2,
            'soft_context_source': 'live+cache',
            'asof': '2026-07-22',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '601899',
        '紫金矿业',
        score=90.0,
        rank=14,
        price=18.0,
        sector_score=0.85,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.5,
        close_position_score=0.80,
        fund_flow_momentum=0.92,
        time_series_momentum=0.25,
        research_panel_overall='PASS',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.85,
        sector_opportunity_tags=['有色金属', '黄金概念'],
        main_theme_core_score=0.55,
        main_theme_alignment_score=0.50,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-22'
    candidate['source_layers'] = ['L0_FULL_UNIVERSE', 'L1_HOT_MOMENTUM']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('hollow_theme_fund_shell') is not True
    assert 'hollow_theme_fund_shell' not in (eligibility.get('blockers') or [])


def _live_soft_pre_pick(favored=None, asof='2026-07-21'):
    return {
        'favored_sectors': favored or ['电子'],
        'risk_sectors': [],
        'confidence': 1.0,
        'market_stance': 'RISK_OFF_TECH_DEFENSIVE',
        'selected_for_production': False,
        'soft_context_valid': True,
        'high_confidence_allowed': True,
        'post_count': 5,
        'live_post_count': 3,
        'seed_post_count': 0,
        'cache_post_count': 2,
        'soft_context_source': 'live+cache',
        'asof': asof,
    }


def test_quality_escape_partial_aux_edge_blocks_haixing_style(monkeypatch):
    """R2: quality_escape must not waive PARTIAL aux on PROXY/near-cap weak-core edge."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': _live_soft_pre_pick(['电子', '元件'], '2026-07-21'),
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '603115',
        '海星股份',
        score=75.0,
        rank=25,
        price=69.79,
        sector_score=0.72,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.29,
        close_position_score=0.70,
        fund_flow_momentum=0.55,
        time_series_momentum=0.18,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.4,
        mainboard_auxiliary_missing_domains=['announcements', 'direct_symbol_news'],
        sector_opportunity_tags=['元件', '电子'],
        main_theme_core_score=0.0,
        main_theme_alignment_score=0.0,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-21'
    candidate['source_layers'] = ['L0_FULL_UNIVERSE', 'L1_HOT_MOMENTUM']
    candidate.update({
        'limitup_reason_status': 'PROXY',
        'limitup_reason_evidence': [
            {
                'reason': '元件板块涨停',
                'source': 'limitup_pool_sector_proxy',
                'proxy': True,
                'sector': '元件',
            },
        ],
        'limitup_reason_quality_score': 0.55,
        'news_catalyst_strength': 0.0,
        'announcement_catalyst_score': 0.0,
        'sector_catalyst_score': 0.55,
        'topic_propagation_score': 0.40,
        'continuation_gene_score': 0.0,
    })
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('near_price_cap') is True
    assert signals.get('limitup_reason_evidence_class') == 'PROXY'
    assert signals.get('quality_escape_partial_aux_edge_block') is True
    assert signals.get('quality_escape_partial_aux_exception') is not True
    assert 'quality_escape_partial_aux_exception' not in (eligibility.get('positive_conditions') or [])
    assert 'mainboard_auxiliary_evidence_status_not_PASS' in (eligibility.get('blockers') or [])
    assert eligibility.get('eligible') is False


def test_quality_escape_partial_aux_still_ok_without_edge(monkeypatch):
    """Control: quality_escape + PARTIAL with real theme core and no PROXY/near-cap still waives."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': _live_soft_pre_pick(['有色'], '2026-07-22'),
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '601899',
        '紫金矿业',
        score=90.0,
        rank=14,
        price=18.0,
        sector_score=0.85,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.5,
        close_position_score=0.80,
        fund_flow_momentum=0.92,
        time_series_momentum=0.25,
        research_panel_overall='PARTIAL',
        mainboard_auxiliary_evidence_status='PARTIAL',
        mainboard_auxiliary_confidence=0.25,
        mainboard_auxiliary_missing_domains=['announcements', 'direct_symbol_news', 'limitup_reasons'],
        sector_opportunity_tags=['有色金属', '黄金概念'],
        main_theme_core_score=0.55,
        main_theme_alignment_score=0.50,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-22'
    candidate['source_layers'] = ['L0_FULL_UNIVERSE', 'L1_HOT_MOMENTUM']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('quality_escape_partial_aux_edge_block') is not True
    assert signals.get('quality_escape_partial_aux_exception') is True
    assert 'mainboard_auxiliary_evidence_status_not_PASS' not in (eligibility.get('blockers') or [])


def test_high_core_chase_weak_fund_blocks_pingmei_style(monkeypatch):
    """R3: high core/align + chase pct + weak fund + no stock catalyst hard-blocks."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': [],
            'risk_sectors': [],
            'confidence': 0.0,
            'market_stance': 'NEUTRAL',
            'selected_for_production': False,
            'soft_context_valid': False,
            'high_confidence_allowed': False,
            'post_count': 0,
            'live_post_count': 0,
            'seed_post_count': 0,
            'cache_post_count': 0,
            'soft_context_source': '',
            'asof': '2026-07-20',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '601666',
        '平煤股份',
        score=59.5,
        rank=8,
        price=12.5,
        sector_score=0.8,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.83,
        close_position_score=0.65,
        fund_flow_momentum=0.376,
        time_series_momentum=0.20,
        research_panel_overall='PASS',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.80,
        sector_opportunity_tags=['煤炭'],
        main_theme_core_score=0.95,
        main_theme_alignment_score=1.0,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-20'
    candidate['news_catalyst_strength'] = 0.0
    candidate['announcement_catalyst_score'] = 0.0
    candidate['continuation_gene_score'] = 0.10
    candidate['source_layers'] = ['L0_FULL_UNIVERSE', 'L1_HOT_MOMENTUM']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('high_core_chase_weak_fund') is True
    assert 'high_core_chase_weak_fund' in (eligibility.get('blockers') or [])
    assert eligibility.get('eligible') is False


def test_high_core_with_strong_fund_not_r3_blocked(monkeypatch):
    """Control: high core + strong fund is not R3 chase-weak-fund hard."""
    monkeypatch.setattr(
        runner,
        'load_pre_pick_market_context',
        lambda trade_date='': {
            'favored_sectors': ['煤炭'],
            'risk_sectors': [],
            'confidence': 0.0,
            'market_stance': 'NEUTRAL',
            'selected_for_production': False,
            'soft_context_valid': False,
            'high_confidence_allowed': False,
            'post_count': 0,
            'live_post_count': 0,
            'seed_post_count': 0,
            'cache_post_count': 0,
            'soft_context_source': '',
            'asof': '2026-07-20',
        },
    )
    required_counts, enhanced_counts = full_candidate_evidence_counts()
    candidate = make_candidate(
        '601666',
        '平煤股份-强资金对照',
        score=72.0,
        rank=3,
        price=12.5,
        sector_score=0.8,
        search_layer_hint='formal_high_score',
        setup_type='HOT_MOMENTUM',
        candidate_stage='mid_5_to_7',
        signal_pct=6.83,
        close_position_score=0.70,
        fund_flow_momentum=0.70,
        time_series_momentum=0.25,
        research_panel_overall='PASS',
        mainboard_auxiliary_evidence_status='PASS',
        mainboard_auxiliary_confidence=0.85,
        sector_opportunity_tags=['煤炭'],
        main_theme_core_score=0.95,
        main_theme_alignment_score=1.0,
        candidate_evidence_domain_counts=required_counts,
        enhanced_evidence_domain_counts=enhanced_counts,
    )
    candidate['trade_date'] = '2026-07-20'
    candidate['news_catalyst_strength'] = 0.0
    candidate['announcement_catalyst_score'] = 0.0
    candidate['continuation_gene_score'] = 0.10
    candidate['source_layers'] = ['L0_FULL_UNIVERSE']
    bundle = _weak_market_bundle([candidate])
    eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
    signals = eligibility.get('signals') or {}
    assert signals.get('high_core_chase_weak_fund') is not True
    assert 'high_core_chase_weak_fund' not in (eligibility.get('blockers') or [])
