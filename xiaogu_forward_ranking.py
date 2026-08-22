#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-owner extraction from the production forward runner.

The production entry remains ``xiaogu_forward_runner.py``. This module only
owns the responsibility named in its filename and is host-bound so existing
imports and test monkeypatches retain their behavior.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple
from xiaogu_forward_host_binding import create_host_binding

_HOST = None
REQUIRED_FROM_HOST = ('_strip_replay_production_contributions', 'candidate_capital_risk_profile', 'classify_limitup_reason_evidence', 'continuation_gene_evidence', 'limitup_probability_proxy_components', 'official_target_exclusion_reasons', 'paper_pick_eligibility_profile', 'paper_pick_risk_explanation_gate', 'resolve_ranking_evidence_scales_for_row', 'safe_float', 'soft_mainline_fund_bias', 'structured_signal_profile', 'symbol_for')

bind_host, _inject_host, _with_host = create_host_binding(
    globals(), REQUIRED_FROM_HOST, preserve_existing_on_missing=True,
)

def ranking_basis_adjustment_components(row: Dict[str, Any]) -> Dict[str, Any]:
    """Explainable adjustments within the existing structured ranking basis.

    Objective (production ranking): expected next-day *profit*, not limit-up rate.
    Limit-up / near-limit is a bonus only when continuation / mainline / catalyst
    evidence supports forward edge. Bare chase-high and hot-fund shells are demoted.
    """
    row = _strip_replay_production_contributions(row)
    profile = structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    sector_proxy = row.get('sector_yesterday_limitup_gene_proxy') or auxiliary.get('sector_yesterday_limitup_gene_proxy') or {}
    gene_evidence = continuation_gene_evidence(row)
    sector_proxy_score = safe_float(sector_proxy.get('continuation_gene_score')) if isinstance(sector_proxy, dict) else None
    if sector_proxy_score is None:
        sector_proxy_score = gene_evidence['effective_score']
    if (
        gene_evidence['proxy_only']
        and gene_evidence['sector_proxy_match_count'] < 3
    ):
        sector_proxy_score = 0.0
    # Sector match without explicit gene score still counts as continuation structure.
    # Strength must scale with match breadth: single-name (count=1) is noise (7/27 亨通型 FP),
    # multi-name sector (≥3) is real board-width continuation (中利/贵金属型).
    sector_proxy_status = str(sector_proxy.get('status') or '').strip().upper() if isinstance(sector_proxy, dict) else ''
    sector_match_items = []
    if isinstance(sector_proxy, dict):
        for key in ('sector_matches', 'one_word_sector_matches'):
            raw_matches = sector_proxy.get(key) or []
            if isinstance(raw_matches, list):
                sector_match_items.extend([m for m in raw_matches if isinstance(m, dict)])
    sector_proxy_has_match = bool(sector_match_items)
    sector_proxy_match_counts: Dict[str, int] = {}
    for match in sector_match_items:
        try:
            sector_name = str(match.get('sector') or '').strip().lower()
            match_key = sector_name or f'unknown_{len(sector_proxy_match_counts)}'
            match_count = max(1, int(safe_float(match.get('count')) or 1))
            sector_proxy_match_counts[match_key] = max(
                sector_proxy_match_counts.get(match_key, 0),
                match_count,
            )
        except (TypeError, ValueError):
            match_key = f'unknown_{len(sector_proxy_match_counts)}'
            sector_proxy_match_counts[match_key] = max(
                sector_proxy_match_counts.get(match_key, 0),
                1,
            )
    sector_proxy_match_count = sum(sector_proxy_match_counts.values())
    proxy_only_narrow_sector_suppress = 0.0
    if gene_evidence['proxy_only'] and sector_proxy_match_count < 3:
        # Two or fewer sector matches are context, not board-width continuation.
        proxy_only_narrow_sector_suppress = 1.25
    # Floor by breadth only — never grant 0.55 on a lone sector hit.
    if not gene_evidence['proxy_only'] and sector_proxy_has_match and (sector_proxy_score or 0.0) < 0.55:
        if sector_proxy_match_count >= 3:
            sector_proxy_score = max(float(sector_proxy_score or 0.0), 0.55)
        elif sector_proxy_match_count >= 2:
            sector_proxy_score = max(float(sector_proxy_score or 0.0), 0.40)
        else:
            # count=1: soft hint only; cannot mint profit_edge / strong_continuation alone.
            sector_proxy_score = max(float(sector_proxy_score or 0.0), 0.22)
    capital_flow = auxiliary.get('capital_flow') if isinstance(auxiliary.get('capital_flow'), dict) else {}
    if not capital_flow and isinstance(row.get('data_directory_capital_flow'), dict):
        capital_flow = row.get('data_directory_capital_flow') or {}
    capital_flow_quality = 1.0 if capital_flow else 0.0
    capital_flow_net = max(
        0.0,
        safe_float(capital_flow.get('main_force_net_inflow'))
        or safe_float(capital_flow.get('main_buy_net_inflow'))
        or safe_float(capital_flow.get('main_buy_net'))
        or safe_float(row.get('net_inflow_main'))
        or 0.0,
    )
    # Normalize the observed main-force amount; positive flow is evidence,
    # while negative flow remains handled by the existing risk profile.
    capital_flow_strength = min(1.0, capital_flow_net / 500_000_000.0)
    news_evidence = auxiliary.get('news') if isinstance(auxiliary.get('news'), dict) else {}
    announcement_confirmed = (profile.get('announcement_catalyst_score') or 0.0) >= 0.75
    news_confirmed = bool(news_evidence.get('direct_symbol_news')) and news_evidence.get('status') != 'MISSING'
    confirmed_news = min(1.0, max(profile.get('news_catalyst_strength') or 0.0, 1.0 if news_confirmed else 0.0))
    # Announcement inventory is not a catalyst by itself. The scanner's
    # freshness/quality score is the only production announcement strength.
    announcement = min(1.0, max(profile.get('announcement_catalyst_score') or 0.0, 0.0))
    low_position = min(1.0, safe_float(row.get('low_position_catalyst_score')) or 0.0)
    risk_notice = min(1.0, profile.get('risk_notice_penalty') or 0.0)
    limitup_proxy = limitup_probability_proxy_components(row)
    risk_gate = paper_pick_risk_explanation_gate(row)
    sector_heat = min(1.0, max(profile.get('sector_opportunity_score') or 0.0, 0.0))
    theme_alignment = min(1.0, max(profile.get('main_theme_alignment_score') or 0.0, 0.0))
    theme_core = min(1.0, max(profile.get('main_theme_core_score') or 0.0, 0.0))
    signal_pct = profile.get('signal_pct') or safe_float(row.get('signal_pct')) or 0.0
    # Profile may omit fund_flow_momentum on sparse rows; fall back to row/components.
    fund_flow = (
        profile.get('fund_flow_momentum')
        if profile.get('fund_flow_momentum') is not None
        else None
    )
    if fund_flow is None:
        comps = row.get('structured_score_components') if isinstance(row.get('structured_score_components'), dict) else {}
        fund_flow = safe_float(row.get('fund_flow_momentum'))
        if fund_flow is None:
            fund_flow = safe_float(comps.get('fund_flow_momentum'))
    fund_flow = float(fund_flow or 0.0)
    real_catalyst = max(
        confirmed_news,
        announcement if announcement_confirmed else 0.0,
        min(1.0, sector_proxy_score or 0.0),
        low_position,
    )
    sector_news = min(1.0, profile.get('sector_news_catalyst_score') or 0.0)
    continuation_gene = gene_evidence['effective_score']
    real_catalyst = max(real_catalyst, sector_news, continuation_gene * 0.5)
    # High extension without catalyst/gene is a large-loss pathway (factor audit).
    chase_high = 0.0
    if signal_pct >= 7.0 and real_catalyst < 0.45 and (sector_proxy_score or 0.0) < 0.45:
        chase_high = min(1.0, (signal_pct - 6.0) / 4.0)
    if fund_flow >= 0.75 and real_catalyst < 0.45 and signal_pct >= 4.0:
        chase_high = max(chase_high, min(1.0, fund_flow))
    # Hollow theme-core (high core, weak catalyst/gene) must not dominate production.
    hollow_theme = 0.0
    if theme_core >= 0.60 and real_catalyst < 0.45 and (sector_proxy_score or 0.0) < 0.50:
        hollow_theme = theme_core
    # Pure PROXY limitup reason + edge (mt/ma≈0) + near price-cap: soft ranking suppress (M1/M2/M5).
    limitup_class = classify_limitup_reason_evidence(row)
    evidence_class = str(limitup_class.get('limitup_reason_evidence_class') or 'MISSING')
    continuation_gene_support = limitup_class.get('limitup_reason_has_gene') or False
    # Strong continuation requires breadth or own gene — bare PROXY+any match is FP bait.
    strong_sector_proxy = bool(
        (sector_proxy_score or 0.0) >= 0.55 and sector_proxy_match_count >= 3
    )
    strong_continuation_limitup = bool(
        (
            continuation_gene_support
            and (
                continuation_gene >= 0.55
                or evidence_class == 'DIRECT'
            )
        )
        or strong_sector_proxy
        or continuation_gene >= 0.55
    )
    # Profit-edge evidence (July audit): continuation / sector gene / direct news —
    # NOT bare theme_core, NOT bare fund_flow, NOT low_position alone.
    # Weak single-name sector proxy (count=1) does not mint profit_edge by itself.
    mainline_soft_early = soft_mainline_fund_bias(row)
    mainline_boost_early = float(mainline_soft_early.get('soft_boost') or 0.0)
    sector_proxy_for_edge = min(1.0, float(sector_proxy_score or 0.0))
    if sector_proxy_match_count < 2 and continuation_gene < 0.25 and not confirmed_news:
        sector_proxy_for_edge = min(sector_proxy_for_edge, 0.30)
    profit_edge = max(
        continuation_gene,
        sector_proxy_for_edge if sector_proxy_match_count >= 2 or continuation_gene >= 0.20 else 0.0,
        confirmed_news,
        0.55 if strong_continuation_limitup else 0.0,
    )
    # Own gene / multi-name sector / mainline can absorb soft sector-news / announcement.
    # Bare announcement alone must not manufacture profit_edge (7/27 亨通 announcement=1.0 FP).
    if continuation_gene >= 0.25 or sector_proxy_match_count >= 2 or mainline_boost_early >= 0.20:
        profit_edge = max(profit_edge, sector_news * 0.65, announcement * 0.40)
    elif continuation_gene >= 0.15 or float(sector_proxy_score or 0.0) >= 0.35:
        profit_edge = max(profit_edge, sector_news * 0.40, announcement * 0.20)

    # Production view switch: model the next-day attack chain instead of
    # treating raw stock flow or a high legacy score as the primary signal.
    # Every component is T-day observable and bounded; a missing link limits
    # the product rather than being hidden by a large single input.
    event_score = min(
        1.0,
        max(
            confirmed_news,
            announcement,
            sector_news * 0.80,
            continuation_gene * 0.75,
            min(1.0, float(sector_proxy_score or 0.0)) * 0.60,
            (profile.get('limitup_reason_quality_score') or 0.0) * 0.40,
        ),
    )
    sector_attack_score = min(
        1.0,
        max(
            0.0,
            sector_heat * 0.45
            + theme_alignment * 0.25
            + min(1.0, max(0.0, mainline_boost_early)) * 0.20
            + sector_news * 0.10,
        ),
    )
    auxiliary_status = str(
        profile.get('mainboard_auxiliary_evidence_status') or ''
    ).strip().upper()
    direct_stock_catalyst = bool(
        confirmed_news
        or announcement_confirmed
        or sector_news >= 0.75
        or evidence_class == 'DIRECT'
    )
    if auxiliary_status == 'PARTIAL' and not direct_stock_catalyst:
        # PARTIAL evidence can rank diagnostically, but cannot have the same
        # confidence as a fully confirmed stock-level behavior chain.
        event_score *= 0.70
        sector_attack_score *= 0.70
    order_book_pressure = safe_float(profile.get('order_book_pressure')) or 0.0
    volume_confirmation = min(1.0, max(0.0, (safe_float(profile.get('volume_ratio')) or 0.0) / 3.0))
    close_position = safe_float(profile.get('close_position_score')) or 0.0
    time_series = safe_float(profile.get('time_series_momentum')) or 0.0
    flow_confirmation_score = min(
        1.0,
        max(
            0.0,
            fund_flow * 0.40
            + min(1.0, max(0.0, close_position)) * 0.20
            + volume_confirmation * 0.15
            + min(1.0, max(0.0, time_series)) * 0.15
            + min(1.0, max(0.0, order_book_pressure)) * 0.10,
        ),
    )
    # Keep the production capital dimension directly observable. Do not infer
    # an unobservable "main-force intent" from a multiplicative actor chain.
    capital_behavior_score = min(
        1.0,
        flow_confirmation_score + capital_flow_strength * 0.30,
    )
    stage_room = {
        'underwater': 0.90,
        'flat_0_to_3': 1.00,
        'early_3_to_5': 0.85,
        'mid_5_to_7': 0.60,
        'high_7_to_9': 0.30,
        'near_limit_9_plus': 0.10,
    }.get(str(profile.get('candidate_stage') or row.get('candidate_stage') or ''), 0.50)
    t1_room_score = min(
        1.0,
        max(
            0.0,
            low_position * 0.50
            + stage_room * 0.30
            + max(0.0, 1.0 - min(10.0, max(0.0, signal_pct)) / 10.0) * 0.20,
        ),
    )
    distribution_risk_score = min(
        1.0,
        max(
            0.0,
            min(1.0, (safe_float(capital.get('risk_penalty_score')) or 0.0) / 2.0) * 0.45
            + min(1.0, safe_float(capital.get('main_buy_outflow_pressure')) or 0.0) * 0.20
            + min(1.0, safe_float(capital.get('popularity_crowding_risk')) or 0.0) * 0.15
            + (0.15 if signal_pct >= 7.0 and event_score < 0.45 else 0.0)
            + (0.15 if signal_pct >= 8.0 and close_position >= 0.90 and low_position < 0.35 else 0.0),
        ),
    )
    catalyst_type = 'none'
    if event_score > 0:
        declared_catalyst_type = str(row.get('catalyst_type') or '').strip().lower()
        if declared_catalyst_type in {'policy', 'announcement', 'direct_news', 'sector_news', 'continuation_structure'}:
            catalyst_type = declared_catalyst_type
        event_text = ' '.join(
            [
                str(row.get('catalyst_type') or ''),
                str(row.get('announcement_catalyst') or ''),
                str(row.get('news_catalyst') or ''),
                str(row.get('theme_catalyst') or ''),
                str(row.get('positive_catalyst') or ''),
            ]
        )
        if catalyst_type != 'none':
            pass
        elif any(token in event_text for token in ('政策', '规划', '指导意见', '专项行动')):
            catalyst_type = 'policy'
        elif announcement >= max(confirmed_news, sector_news):
            catalyst_type = 'announcement'
        elif confirmed_news >= sector_news:
            catalyst_type = 'direct_news'
        elif sector_news > 0:
            catalyst_type = 'sector_news'
        elif continuation_gene > 0 or sector_proxy_score:
            catalyst_type = 'continuation_structure'
    counter_evidence: List[str] = []
    if event_score < 0.35:
        counter_evidence.append('missing_fresh_direct_catalyst')
    if sector_attack_score < 0.35:
        counter_evidence.append('sector_attack_not_confirmed')
    if capital_behavior_score < 0.35:
        counter_evidence.append('flow_confirmation_weak')
    if t1_room_score < 0.35:
        counter_evidence.append('t1_room_limited')
    if distribution_risk_score >= 0.55:
        counter_evidence.append('distribution_risk_high')
    # Hot fund + high theme without profit-edge ≈ next-day mean reversion shell (e.g. 7/27 大金型).
    hot_fund_no_profit = 0.0
    if fund_flow >= 0.70 and profit_edge < 0.45 and continuation_gene < 0.35:
        hot_fund_no_profit = min(1.15, fund_flow * 0.90)
        if theme_core >= 0.70:
            hot_fund_no_profit = min(1.25, hot_fund_no_profit + 0.30)
        if signal_pct >= 5.0 and signal_pct < 9.5:
            # Mid-extension fund shell (not sealed board): common July loss path.
            hot_fund_no_profit = min(1.30, hot_fund_no_profit + 0.15)
    price = safe_float(row.get('price'))
    edge_proxy_penalty = 0.0
    if evidence_class == 'PROXY' and real_catalyst < 0.45:
        edge_proxy_penalty += 0.55
        if theme_core <= 0.05 and theme_alignment <= 0.05:
            edge_proxy_penalty += 0.35
    # Pool-wide identical theme tags make alignment untrustworthy (M5). Soft only.
    hollow_tags_signal = bool(
        row.get('theme_tags_hollow')
        or (isinstance(row.get('theme_tags_hollow_meta'), dict) and row.get('theme_tags_hollow_meta', {}).get('hollow'))
    )
    hollow_tags_penalty = 0.0
    if hollow_tags_signal:
        hollow_tags_penalty = 0.55
        if theme_alignment >= 0.40 or theme_core >= 0.40:
            hollow_tags_penalty += 0.35
        if real_catalyst < 0.45:
            hollow_tags_penalty += 0.25
    # Already near/at limit without low-position setup is often next-day profit-taking.
    # Full waive only for DIRECT catalyst or multi-name sector board-width.
    # Own gene alone keeps residual suppress (7/15 大有 gene=0.7 near-limit still lost).
    near_limit_extension = 0.0
    if signal_pct >= 9.5 and low_position < 0.45:
        near_limit_extension = min(1.0, 0.55 + max(0.0, theme_core - 0.5) * 0.5)
        if evidence_class == 'DIRECT' and (continuation_gene >= 0.45 or strong_sector_proxy):
            near_limit_extension = 0.0
        elif strong_sector_proxy and continuation_gene >= 0.30:
            near_limit_extension *= 0.12
        elif continuation_gene >= 0.55:
            # High gene near sealed board: residual only (not bare chase, not free pass).
            near_limit_extension *= 0.28
        elif continuation_gene_support or continuation_gene >= 0.30 or profit_edge >= 0.50:
            near_limit_extension *= 0.40
        elif mainline_boost_early >= 0.25:
            near_limit_extension *= 0.45
    # When tags are hollow, do not allow alignment boost to fake mainline quality.
    alignment_boost_scale = 0.0 if hollow_tags_signal else 1.0
    # scoring_config + regime: self_evolve weights actually move production ranking.
    evidence_scales = resolve_ranking_evidence_scales_for_row(row)
    limitup_scale = float(evidence_scales.get('limitup_scale') or 1.0)
    catalyst_scale = float(evidence_scales.get('catalyst_scale') or 1.0)
    broken_scale = float(evidence_scales.get('broken_scale') or 1.0)
    # Profit-continuation soft: raise ranking only when edge evidence exists.
    profit_continuation_soft = 0.0
    if profit_edge >= 0.25 or strong_continuation_limitup:
        profit_continuation_soft = min(
            1.45,
            continuation_gene * 0.55 * limitup_scale
            + min(1.0, float(sector_proxy_score or 0.0)) * 0.35 * limitup_scale
            + (0.28 if strong_continuation_limitup else 0.0)
            + min(0.35, mainline_boost_early * 0.50),
        )
    penalties = {
        'failed_limitup_risk': (capital.get('failed_limitup_risk') or 0.0) * 1.20 * broken_scale,
        'main_buy_outflow_pressure': (capital.get('main_buy_outflow_pressure') or 0.0) * 1.15 * broken_scale,
        'popularity_crowding_risk': (capital.get('popularity_crowding_risk') or 0.0) * 0.70,
        'high_popularity_trap_risk': (capital.get('high_popularity_trap_risk') or 0.0) * 1.10,
        'weak_limitup_confirmation': 0.45 if capital.get('weak_limitup_confirmation') else 0.0,
        'risk_notice_evidence': risk_notice * 0.60,
        'high_popularity_trap_combo_penalty': (1.60 * broken_scale) if risk_gate['status'] == 'FAIL' else 0.0,
        'chase_high_without_catalyst': chase_high * 1.10,
        'hollow_theme_core_without_catalyst': hollow_theme * 0.90,
        'hollow_theme_tags_pollution': min(1.2, hollow_tags_penalty),
        'near_limit_extension_without_low_position': near_limit_extension * 0.85,
        'edge_proxy_near_cap_soft_suppress': min(1.5, edge_proxy_penalty),
        'proxy_only_narrow_sector_gene': proxy_only_narrow_sector_suppress,
        # Profit-first: hot money without continuation edge is not "strength".
        'hot_fund_shell_without_profit_edge': hot_fund_no_profit * 1.05,
    }
    # P2: under DEFENSIVE / RISK_OFF stances, pe≈0 hot-fund shells get extra soft demotion
    # so utility/defensive survivors do not dominate formal rank / first_clean.
    stance = str(row.get('market_stance') or '').upper()
    defensive_shell_extra = 0.0
    if stance in ('DEFENSIVE_ROTATION', 'AVOID_CLIMAX_TECH', 'RISK_OFF_TECH_DEFENSIVE'):
        if profit_edge < 0.15 and profit_continuation_soft < 0.20:
            if hot_fund_no_profit >= 0.55:
                defensive_shell_extra = min(1.15, 0.55 + hot_fund_no_profit * 0.35)
            elif fund_flow >= 0.55 and theme_core >= 0.55 and continuation_gene < 0.25:
                # Theme+fund shell with no profit edge (7/27 大金 / 电力大票 path).
                defensive_shell_extra = min(0.95, 0.40 + fund_flow * 0.25 + theme_core * 0.15)
            elif signal_pct < 3.0 and theme_core >= 0.40 and profit_edge < 0.10:
                # Pure defensive low-elasticity names (电力大票) under defensive stance.
                defensive_shell_extra = min(0.70, 0.35 + theme_core * 0.20)
        if defensive_shell_extra > 0:
            penalties['defensive_pe0_hot_fund_shell'] = round(defensive_shell_extra, 4)
    # P1: day fund-flow mainline alignment (soft only; official gates unchanged).
    mainline_soft = mainline_soft_early
    boosts = {
        'confirmed_news_catalyst': confirmed_news * 0.50 * catalyst_scale,
        'announcement_catalyst': announcement * 0.45 * catalyst_scale,
        'sector_yesterday_limitup_gene_proxy': min(1.0, sector_proxy_score or 0.0) * 0.75 * limitup_scale,
        'low_position_catalyst_score': low_position * 0.90 * catalyst_scale,
        'sector_news_catalyst_score': sector_news * 0.30 * catalyst_scale,
        # July audit: continuation_gene was the strongest positive vs T+1; raise soft weight.
        'continuation_gene_score': gene_evidence['effective_score'] * 0.55 * limitup_scale,
        'sector_heat_opportunity': sector_heat * 0.35,
        'main_theme_alignment_boost': theme_alignment * 0.35 * alignment_boost_scale,
        'capital_flow_evidence_quality': capital_flow_quality * 0.20,
        'limitup_probability_proxy': limitup_proxy['limitup_probability_proxy'] * 0.55 * limitup_scale,
        # Day mainline fund-flow alignment (industry/concept net inflow top).
        'mainline_fund_flow_soft': float(mainline_soft.get('soft_boost') or 0.0),
        # Explicit profit-edge boost (gene/mainline/continuation) — not bare limit-up.
        'profit_continuation_soft': round(profit_continuation_soft, 4),
    }
    # Similar-loss soft demotion lives in formal_candidate_sort_key via
    # similar_cases_boost (asymmetric weight). Expose meta here for explainability only.
    similar_meta = row.get('similar_cases_meta') if isinstance(row.get('similar_cases_meta'), dict) else {}
    if not similar_meta and row.get('similar_cases_boost') is not None:
        similar_meta = {
            'boost': float(safe_float(row.get('similar_cases_boost')) or 0.0),
            'soft_only': True,
            'hard_gate': False,
            'force_pick': False,
        }
    return {
        'boosts': {key: round(value, 4) for key, value in boosts.items()},
        'penalties': {key: round(value, 4) for key, value in penalties.items()},
        'boost_total': round(sum(boosts.values()), 4),
        'penalty_total': round(sum(penalties.values()), 4),
        'net_adjustment': round(sum(boosts.values()) - sum(penalties.values()), 4),
        'profit_edge_score': round(float(profit_edge), 4),
        'profit_objective': 'expected_t1_profit',
        'ranking_view': 'main_force_behavior_chain',
        'capital_behavior_score': round(capital_behavior_score, 4),
        'capital_flow_strength': round(capital_flow_strength, 4),
        'catalyst_type': catalyst_type,
        'event_score': round(event_score, 4),
        'sector_attack_score': round(sector_attack_score, 4),
        'flow_confirmation_score': round(flow_confirmation_score, 4),
        't1_room_score': round(t1_room_score, 4),
        'distribution_risk_score': round(distribution_risk_score, 4),
        'counter_evidence': counter_evidence,
        **limitup_proxy,
        'paper_pick_risk_explanation_gate': risk_gate,
        'mainline_fund_flow_soft': mainline_soft,
        'similar_cases_soft': similar_meta,
        'continuation_gene_evidence': continuation_gene_evidence(row),
        'ranking_evidence_scales': evidence_scales,
    }

def ensure_leader_chain_main_theme(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Fill main_theme_* when scanner left 0 so formal sort can compete on leader-chain.

    Does not invent theme from pure price. Uses sector heat + fund-flow mainline hits.
    Writes leader_chain_score / main_theme_source onto the row copy return value.
    """
    out = _strip_replay_production_contributions(row)
    core = safe_float(out.get('main_theme_core_score'))
    align = safe_float(out.get('main_theme_alignment_score'))
    details = out.get('structured_component_details') if isinstance(out.get('structured_component_details'), dict) else {}
    if core is None:
        core = safe_float(details.get('main_theme_core_score'))
    if align is None:
        align = safe_float(details.get('main_theme_alignment_score'))
    sector_opp = safe_float(out.get('sector_opportunity_score'))
    if sector_opp is None:
        sector_opp = safe_float(details.get('sector_opportunity_score')) or 0.0
    fund = safe_float((out.get('structured_score_components') or {}).get('fund_flow_momentum')) if isinstance(out.get('structured_score_components'), dict) else None
    if fund is None:
        fund = safe_float(out.get('fund_flow_momentum')) or 0.0
    close_pos = safe_float(out.get('close_position_score')) or 0.0
    vol = safe_float(out.get('volume_ratio')) or 0.0
    signal_pct = safe_float(out.get('signal_pct')) or 0.0
    mainline_soft = soft_mainline_fund_bias(out)
    mainline_hits = list(mainline_soft.get('mainline_hits') or [])
    # Leader-chain proxy when theme core is hollow but sector/fund/mainline evidence is real.
    leader = 0.0
    if (core or 0.0) < 0.15:
        if sector_opp >= 0.35:
            leader += min(0.40, sector_opp * 0.45)
        if fund >= 0.35:
            leader += min(0.30, fund * 0.35)
        if close_pos >= 0.78 and vol >= 1.5:
            leader += 0.12
        if signal_pct >= 3.0:
            leader += min(0.12, signal_pct / 40.0)
        # Day fund-flow mainline hits: bounded soft leader lift (not hard gate).
        if mainline_hits:
            leader += min(0.28, 0.10 * len(mainline_hits) + float(mainline_soft.get('soft_boost') or 0.0) * 0.35)
        leader = min(0.90, leader)
    out['mainline_fund_flow_soft'] = mainline_soft
    if (core or 0.0) <= 0.0 and leader > 0.0:
        out['main_theme_core_score'] = round(leader, 4)
        out['main_theme_alignment_score'] = round(
            max(align or 0.0, min(1.0, leader * 0.85)),
            4,
        )
        out['main_theme_source'] = 'leader_chain_proxy'
        out['leader_chain_score'] = round(leader, 4)
    else:
        out['main_theme_core_score'] = core if core is not None else 0.0
        out['main_theme_alignment_score'] = align if align is not None else 0.0
        if leader > 0:
            out['leader_chain_score'] = round(leader, 4)
            if not out.get('main_theme_source'):
                out['main_theme_source'] = 'scanner'
        elif not out.get('main_theme_source'):
            out['main_theme_source'] = 'scanner' if (core or 0) > 0 else 'missing'
    return out

def formal_candidate_sort_key(row: Dict[str, Any]) -> Tuple[float, ...]:
    """Return the single production score for T-day buy / T+1 close profit.

    The first tuple element is a 0-100 score built only from T-day evidence.
    Legacy scanner scores remain audit fields and never decide the formal rank.
    """
    row = _strip_replay_production_contributions(row)
    row = ensure_leader_chain_main_theme(row)
    profile = structured_signal_profile(row)
    adjustment = ranking_basis_adjustment_components(row)
    evidence_scales = adjustment.get('ranking_evidence_scales') if isinstance(
        adjustment.get('ranking_evidence_scales'), dict
    ) else resolve_ranking_evidence_scales_for_row(row)
    limitup_scale = float(evidence_scales.get('limitup_scale') or 1.0)
    catalyst_scale = float(evidence_scales.get('catalyst_scale') or 1.0)
    broken_scale = float(evidence_scales.get('broken_scale') or 1.0)

    def bounded(value: Any, upper: float = 1.0) -> float:
        return min(upper, max(0.0, float(safe_float(value) or 0.0)))

    event_score = bounded(adjustment.get('event_score'))
    sector_score = bounded(adjustment.get('sector_attack_score'))
    capital_score = bounded(adjustment.get('capital_behavior_score'))
    room_score = bounded(adjustment.get('t1_room_score'))
    profit_edge = bounded(adjustment.get('profit_edge_score'))
    profit_soft = bounded(
        (adjustment.get('boosts') or {}).get('profit_continuation_soft'),
        1.45,
    ) / 1.45
    continuation_score = bounded(max(profit_edge, profit_soft))
    expected_t1 = bounded(row.get('expected_t1_profit_score'))

    direct_capital_evidence = any(
        bounded(profile.get(field)) > 0.0
        for field in ('fund_flow_momentum', 'time_series_momentum', 'order_book_pressure')
    )
    if not direct_capital_evidence:
        # Do not manufacture main-force strength from close position or volume.
        capital_score = 0.0

    capital_risk = row.get('capital_risk_profile')
    if not isinstance(capital_risk, dict):
        capital_risk = candidate_capital_risk_profile(row)
    risk_penalty = bounded(adjustment.get('distribution_risk_score'))
    broken_risk = bounded(
        (safe_float(capital_risk.get('risk_penalty_score')) or 0.0)
        * 1.25
        * broken_scale,
        2.0,
    ) / 2.0
    penalties = adjustment.get('penalties') if isinstance(adjustment.get('penalties'), dict) else {}
    shell_pressure = bounded(
        (safe_float(penalties.get('hot_fund_shell_without_profit_edge')) or 0.0)
        + (safe_float(penalties.get('defensive_pe0_hot_fund_shell')) or 0.0),
        2.0,
    ) / 2.0
    near_seal_penalty = bounded(
        (safe_float(penalties.get('near_limit_extension_without_low_position')) or 0.0)
        + (safe_float(penalties.get('edge_proxy_near_cap_soft_suppress')) or 0.0),
        2.0,
    ) / 2.0
    auxiliary_risk = bounded(profile.get('risk_notice_penalty'))

    # Main-force behavior is primary: capital confirmation and executable room
    # outweigh an isolated high score, theme tag, announcement, or limit-up proxy.
    positive = (
        capital_score * 30.0
        + room_score * 20.0
        + sector_score * 15.0
        + event_score * 10.0 * catalyst_scale
        + continuation_score * 10.0 * limitup_scale
        + expected_t1 * 5.0
    )
    low_position_score = bounded(row.get('low_position_catalyst_score'))
    limitup_proxy = bounded(
        limitup_probability_proxy_components(row).get('limitup_probability_proxy')
    )
    # Low-position and continuation evidence are main-force attack signals:
    # they describe whether capital can still create a T+1 move, rather than
    # adding an investor-view factor or a competing ranking path.
    main_force_attack_score = bounded(
        continuation_score * 0.45
        + low_position_score * 0.25
        + limitup_proxy * 0.20
        + expected_t1 * 0.10
    )
    positive += main_force_attack_score * 10.0 * limitup_scale
    negative = (
        risk_penalty * 18.0
        + broken_risk * 8.0
        + shell_pressure * 12.0
        + near_seal_penalty * 8.0
        + auxiliary_risk * 4.0
    )
    mainline = adjustment.get('mainline_fund_flow_soft')
    mainline_boost = bounded(mainline.get('soft_boost')) if isinstance(mainline, dict) else 0.0
    similar_boost = safe_float(row.get('similar_cases_boost'))
    if similar_boost is None and isinstance(row.get('similar_cases_meta'), dict):
        similar_boost = safe_float(row['similar_cases_meta'].get('boost'))
    similar_boost = max(-1.0, min(1.0, float(similar_boost or 0.0)))
    production_score = max(
        0.0,
        positive - negative + mainline_boost * 2.0 + similar_boost * 2.0,
    )

    secondary_score = (
        capital_score * 0.32
        + room_score * 0.21
        + sector_score * 0.17
        + event_score * 0.12
        + continuation_score * 0.10
        + expected_t1 * 0.08
        - risk_penalty * 0.18
        - broken_risk * 0.08
        - shell_pressure * 0.12
        - near_seal_penalty * 0.08
    )
    return (
        round(production_score, 6),
        round(secondary_score, 6),
        round(capital_score - risk_penalty, 6),
        round(continuation_score, 6),
        round(room_score, 6),
        round(sector_score, 6),
        round(event_score, 6),
        round(expected_t1, 6),
        round(main_force_attack_score, 6),
        round(mainline_boost, 6),
        round(similar_boost, 6),
        -(safe_float(row.get('rank')) or 999.0),
    )

def select_first_clean_with_formal_challenge(
    search_rows: List[Dict[str, Any]],
    bundle_context: Dict[str, Any],
) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    """Pick the highest formal-ranked candidate after all hard gates pass."""
    clean_rows: List[Dict[str, Any]] = []
    for row in search_rows or []:
        if not isinstance(row, dict):
            continue
        if not paper_pick_eligibility_profile(row, bundle_context)['eligible']:
            continue
        if official_target_exclusion_reasons(row, bundle_context):
            continue
        clean_rows.append(row)
    meta: Dict[str, Any] = {
        'clean_count': len(clean_rows),
        'challenged': False,
        'challenge_reason': '',
        'layer_order_symbol': symbol_for(clean_rows[0]) if clean_rows else '',
        'formal_best_symbol': '',
        'selected_symbol': '',
    }
    if not clean_rows:
        return None, meta
    formal_best = max(clean_rows, key=formal_candidate_sort_key)
    meta['formal_best_symbol'] = symbol_for(formal_best)
    meta['selected_symbol'] = symbol_for(formal_best)
    meta['selection_policy'] = 'formal_rank_after_gates'
    meta['challenged'] = bool(clean_rows and symbol_for(clean_rows[0]) != symbol_for(formal_best))
    meta['challenge_reason'] = (
        f'formal_rank_replaced_layer_order:{symbol_for(clean_rows[0])}->{symbol_for(formal_best)}'
        if meta['challenged'] else ''
    )
    selected = dict(formal_best)
    if meta['challenged']:
        selected['first_clean_challenged_from'] = symbol_for(clean_rows[0])
        selected['first_clean_challenge_reason'] = meta['challenge_reason']
    return selected, meta


for _name, _value in tuple(globals().items()):
    if (
        callable(_value)
        and getattr(_value, '__module__', None) == __name__
        and _name not in {'bind_host', '_inject_host', '_with_host'}
    ):
        globals()[_name] = _with_host(_value)
