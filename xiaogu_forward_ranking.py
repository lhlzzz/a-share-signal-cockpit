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


def _bounded_score(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    if parsed is None:
        parsed = default
    return min(1.0, max(0.0, float(parsed)))


def _entry_quality_profile(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Score T-day entry distance without using any post-decision fields."""
    signal_pct = safe_float(profile.get('signal_pct'))
    if signal_pct is None:
        signal_pct = safe_float(row.get('signal_pct'))
    signal_pct = float(signal_pct or 0.0)
    if 2.0 <= signal_pct <= 5.0:
        distance_score = 1.0
    elif 0.0 <= signal_pct < 2.0:
        distance_score = 0.78
    elif 5.0 < signal_pct <= 7.0:
        distance_score = 0.72
    elif 7.0 < signal_pct < 9.5:
        distance_score = 0.40
    elif signal_pct >= 9.5:
        distance_score = 0.10
    else:
        distance_score = 0.35
    low_position = _bounded_score(
        row.get('low_position_catalyst_score')
        if row.get('low_position_catalyst_score') is not None
        else profile.get('low_position_catalyst_score'),
    )
    close_position = _bounded_score(profile.get('close_position_score'))
    volume_ratio = max(0.0, safe_float(profile.get('volume_ratio')) or 0.0)
    volume_confirmation = min(1.0, volume_ratio / 2.0)
    score = (
        distance_score * 0.55
        + low_position * 0.20
        + close_position * 0.15
        + volume_confirmation * 0.10
    )
    result = {
        'entry_quality_score': round(min(1.0, max(0.0, score)), 4),
        'entry_distance_score': round(distance_score, 4),
        'entry_signal_pct': round(signal_pct, 4),
        'entry_quality_class': (
            'sweet_spot' if 2.0 <= signal_pct <= 5.0
            else 'extended' if signal_pct > 7.0
            else 'early'
        ),
    }
    return result


def _catalyst_credibility_profile(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Map existing catalyst evidence into policy/industry/company/noise tiers."""
    raw_category = str(
        row.get('catalyst_type')
        or row.get('catalyst_quality_category')
        or profile.get('catalyst_quality_category')
        or ''
    ).strip().lower()
    raw_reason_evidence = row.get('limitup_reason_evidence')
    if not isinstance(raw_reason_evidence, dict):
        raw_reason_evidence = {}
    evidence_class = str(
        row.get('limitup_reason_evidence_class')
        or raw_reason_evidence.get('class')
        or ''
    ).strip().upper()
    text = ' '.join(
        str(row.get(key) or '')
        for key in ('catalyst_type', 'announcement_catalyst', 'news_catalyst', 'theme_catalyst', 'positive_catalyst')
    )
    if any(token in text for token in ('政策', '规划', '指导意见', '专项行动')) or raw_category == 'policy':
        tier, tier_score = 'policy', 1.0
    elif raw_category in ('sector_catalyst', 'sector_news') or safe_float(profile.get('sector_news_catalyst_score')) or safe_float(profile.get('sector_catalyst_score')):
        tier, tier_score = 'industry', 0.8
    elif (
        raw_category in ('positive_catalyst', 'announcement', 'direct_news', 'company')
        or evidence_class == 'DIRECT'
        or (safe_float(profile.get('announcement_catalyst_score')) or 0.0) >= 0.65
        or (safe_float(profile.get('news_catalyst_strength')) or 0.0) >= 0.65
    ):
        tier, tier_score = 'company', 0.6
    elif raw_category in ('rumor', 'social', 'market_rumor'):
        tier, tier_score = 'noise', 0.0
    else:
        tier, tier_score = 'unconfirmed', 0.25
    raw_strength = max(
        safe_float(profile.get('news_catalyst_strength')) or 0.0,
        safe_float(profile.get('announcement_catalyst_score')) or 0.0,
        safe_float(profile.get('sector_news_catalyst_score')) or 0.0,
        safe_float(profile.get('sector_catalyst_score')) or 0.0,
    )
    return {
        'catalyst_credibility_score': round(min(1.0, raw_strength) * tier_score, 4),
        'catalyst_credibility_tier': tier,
        'catalyst_credibility_multiplier': tier_score,
        'catalyst_evidence_class': evidence_class or 'MISSING',
    }


def _theme_cycle_profile(row: Dict[str, Any], profile: Dict[str, Any], market_regime: str) -> Dict[str, Any]:
    """Approximate topic phase from same-day propagation, breadth and price stage."""
    signal_pct = safe_float(profile.get('signal_pct')) or safe_float(row.get('signal_pct')) or 0.0
    stage = str(profile.get('candidate_stage') or row.get('candidate_stage') or '').strip()
    topic = _bounded_score(profile.get('topic_propagation_score'))
    sector_heat = _bounded_score(profile.get('sector_opportunity_score'))
    alignment = _bounded_score(profile.get('main_theme_alignment_score'))
    core = _bounded_score(profile.get('main_theme_core_score'))
    breadth = topic * 0.45 + sector_heat * 0.25 + alignment * 0.20 + core * 0.10
    if stage in ('underwater', 'flat_0_to_3', 'early_3_to_5') and breadth >= 0.45:
        phase, points = 'startup', 20.0
    elif stage in ('early_3_to_5', 'mid_5_to_7') and breadth >= 0.35:
        phase, points = 'fermentation', 15.0
    elif stage in ('high_7_to_9', 'near_limit_9_plus') and breadth >= 0.60:
        phase, points = 'climax', 0.0
    elif (
        market_regime in ('weak', 'climax', 'no_main')
        or (stage in ('high_7_to_9', 'near_limit_9_plus') and breadth < 0.35)
    ):
        phase, points = 'retreat', -20.0
    else:
        phase, points = 'neutral', 5.0
    if signal_pct > 7.0 and phase in ('startup', 'fermentation'):
        phase, points = 'climax', 0.0
    return {
        'theme_cycle_score': round((points + 20.0) / 40.0, 4),
        'theme_cycle_points': points,
        'theme_cycle_phase': phase,
        'theme_breadth_score': round(breadth, 4),
    }


def _market_environment_profile(row: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    context = row.get('market_adaptive_context') if isinstance(row.get('market_adaptive_context'), dict) else {}
    regime = str(
        context.get('production_regime')
        or row.get('production_regime')
        or row.get('market_regime')
        or profile.get('market_regime')
        or ''
    ).strip().lower()
    risk_off = bool(
        context.get('external_market_risk_off')
        or row.get('external_market_risk_off')
        or str(row.get('external_market_status') or '').upper() == 'RISK_OFF'
    )
    if risk_off:
        score = 0.0
        label = 'risk_off'
    elif regime == 'strong':
        score, label = 1.0, 'risk_on'
    elif regime == 'climax':
        score, label = 0.35, 'climax'
    elif regime in ('weak', 'no_main'):
        score, label = 0.20, 'risk_off'
    else:
        score, label = 0.60, 'neutral'
    return {
        'market_environment_score': score,
        'market_environment_label': label,
        'market_environment_regime': regime or 'sideways',
        'external_market_risk_off': risk_off,
    }


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
    market_environment = _market_environment_profile(row, profile)
    entry_quality = _entry_quality_profile(row, profile)
    catalyst_credibility = _catalyst_credibility_profile(row, profile)
    theme_cycle = _theme_cycle_profile(
        row,
        profile,
        market_environment['market_environment_regime'],
    )
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
    credible_event_score = min(
        1.0,
        event_score * float(catalyst_credibility['catalyst_credibility_multiplier']),
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
    room_score = min(
        1.0,
        max(
            0.0,
            t1_room_score * 0.65
            + float(entry_quality['entry_quality_score']) * 0.35,
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
        # A high intraday limit-up capture score describes extension already
        # consumed on T day; keep it as a soft T+1 profit demotion.
        'limitup_capture_extension': min(
            1.0,
            max(0.0, safe_float(profile.get('limitup_capture_score')) or 0.0),
        ),
        'risk_off_market_chase': (
            0.90
            if market_environment['external_market_risk_off']
            and signal_pct >= 7.0
            and not direct_stock_catalyst
            and not strong_continuation_limitup
            else 0.0
        ),
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
    result = {
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
        'credible_event_score': round(credible_event_score, 4),
        **catalyst_credibility,
        'sector_attack_score': round(sector_attack_score, 4),
        'flow_confirmation_score': round(flow_confirmation_score, 4),
        't1_room_score': round(t1_room_score, 4),
        'room_score': round(room_score, 4),
        **entry_quality,
        **theme_cycle,
        **market_environment,
        'distribution_risk_score': round(distribution_risk_score, 4),
        'counter_evidence': counter_evidence,
        **limitup_proxy,
        'paper_pick_risk_explanation_gate': risk_gate,
        'mainline_fund_flow_soft': mainline_soft,
        'similar_cases_soft': similar_meta,
        'continuation_gene_evidence': continuation_gene_evidence(row),
        'ranking_evidence_scales': evidence_scales,
    }
    result.update(_t1_alpha_components(row, result))
    return result

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


FORMAL_ALPHA_WEIGHTS = {
    't1_expected_payoff': 0.35,
    't1_reversal_safety': 0.25,
    'marginal_demand': 0.20,
    'state_fit': 0.10,
    'execution_quality': 0.10,
}


def _alpha_value(row: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return float(value)
    return float(default)


def _alpha_norm(value: Any, denominator: float = 1.0) -> float:
    parsed = safe_float(value)
    if parsed is None:
        return 0.0
    if denominator != 1.0:
        parsed = parsed / denominator
    return min(1.0, max(0.0, float(parsed)))


def _primary_alpha_paths(row: Dict[str, Any]) -> List[str]:
    """Return the only four production alpha-path classes.

    The path names are deliberately owned here so ranking and eligibility
    cannot invent separate confirmation vocabularies. All inputs are T-day
    observations; settlement fields are ignored by construction.
    """
    profile = structured_signal_profile(row)
    nested = row.get('raw_json') if isinstance(row.get('raw_json'), dict) else {}
    nested_eligibility = (
        nested.get('paper_pick_eligibility')
        if isinstance(nested.get('paper_pick_eligibility'), dict)
        else {}
    )
    nested_signals = (
        nested_eligibility.get('signals')
        if isinstance(nested_eligibility.get('signals'), dict)
        else {}
    )
    pct = _alpha_value(row, 'signal_pct', 'pct_chg', default=0.0)
    stage = str(
        row.get('candidate_stage')
        or profile.get('candidate_stage')
        or nested.get('candidate_stage')
        or ''
    ).strip().lower()
    close_position = _alpha_norm(
        row.get('close_position_score')
        if row.get('close_position_score') is not None
        else profile.get('close_position_score'),
    )
    volume_ratio = max(
        0.0,
        _alpha_value(
            row,
            'volume_ratio',
            default=_alpha_value(
                profile,
                'volume_ratio',
                default=_alpha_value(nested, 'volume_ratio'),
            ),
        ),
    )
    low_position = _alpha_norm(
        row.get('low_position_catalyst_score')
        if row.get('low_position_catalyst_score') is not None
        else profile.get('low_position_catalyst_score'),
    )
    flow = _alpha_norm(
        row.get('fund_flow_momentum')
        if row.get('fund_flow_momentum') is not None
        else profile.get('fund_flow_momentum')
        if profile.get('fund_flow_momentum') is not None
        else nested.get('fund_flow_momentum')
        if nested.get('fund_flow_momentum') is not None
        else nested_signals.get('fund_flow_momentum'),
    )
    flow_acceleration = _alpha_norm(
        row.get('capital_acceleration')
        if row.get('capital_acceleration') is not None
        else row.get('fund_flow_acceleration')
        if row.get('fund_flow_acceleration') is not None
        else row.get('new_buyer_pressure')
        if row.get('new_buyer_pressure') is not None
        else flow * 0.45
        + _alpha_norm(profile.get('time_series_momentum')) * 0.30
        + _alpha_norm(profile.get('order_book_pressure')) * 0.25,
    )
    nested_net_inflow = _alpha_value(
        nested,
        'net_inflow_main',
        'fund_inflow_positive',
        'main_force_net_inflow',
        default=_alpha_value(nested_signals, 'net_inflow_main', default=0.0),
    )
    if (
        flow_acceleration <= 0.0
        and max(
            _alpha_value(row, 'net_inflow_main', default=0.0),
            nested_net_inflow,
        ) > 0
    ):
        flow_acceleration = 0.35
    recovery = max(
        _alpha_norm(row.get('underwater_recovery_score'), 100.0),
        _alpha_norm(row.get('weak_to_strong_reversal')),
        _alpha_norm(row.get('first_board_pre_signal')),
        _alpha_norm(row.get('pre_limitup_anomaly')),
        _alpha_norm(nested.get('intraday_alert_strength')),
        _alpha_norm(nested.get('limitup_reason_propagation_score')),
        _alpha_norm(
            nested_signals.get('intraday_alert_strength'),
        ),
    )
    sector_diffusion = max(
        _alpha_norm(row.get('sector_capital_diffusion')),
        _alpha_norm(row.get('sector_propagation_score')),
        _alpha_norm(profile.get('sector_opportunity_score')),
        _alpha_norm(row.get('main_theme_alignment_score')),
    )
    leader_confirmation = max(
        _alpha_norm(row.get('leader_confirmation')),
        _alpha_norm(row.get('main_theme_core_score')),
        _alpha_norm(row.get('continuation_gene_score')),
    )
    chip_pressure = max(
        _alpha_norm(row.get('profit_chip_ratio')),
        _alpha_norm(row.get('short_term_profit_pressure')),
        _alpha_norm(row.get('overhead_supply_score')),
    )
    not_fully_priced = min(
        1.0,
        max(
            0.0,
            0.55 * max(0.0, 1.0 - max(0.0, pct) / 10.0)
            + 0.45 * max(0.0, 1.0 - close_position),
        ),
    )
    paths: List[str] = []
    if flow_acceleration >= 0.30 and not_fully_priced >= 0.35:
        paths.append('PATH_A')
    if (
        low_position >= 0.45
        and pct >= 1.5
        and volume_ratio >= 1.05
        and chip_pressure <= 0.65
    ):
        paths.append('PATH_B')
    if (
        (pct < 0.0 or stage in ('underwater', 'flat_0_to_3'))
        and recovery >= 0.35
        and flow_acceleration >= 0.25
        and str(row.get('market_regime') or profile.get('market_regime') or '').lower() not in ('climax', 'weak')
    ):
        paths.append('PATH_C')
    if (
        leader_confirmation >= 0.45
        and sector_diffusion >= 0.35
        and pct < 8.5
        and stage not in ('near_limit_9_plus', 'high_7_to_9')
        and (
            flow_acceleration >= 0.30
            or (volume_ratio >= 1.0 and leader_confirmation >= 0.55)
        )
    ):
        paths.append('PATH_D')
    return paths


def _t1_alpha_components(
    row: Dict[str, Any],
    adjustment: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the five formal T+1 modules from T-day evidence only."""
    profile = structured_signal_profile(row)
    paths = _primary_alpha_paths(row)
    pct = _alpha_value(row, 'signal_pct', 'pct_chg', default=0.0)
    close_position = _alpha_norm(
        row.get('close_position_score')
        if row.get('close_position_score') is not None
        else profile.get('close_position_score'),
    )
    volume_ratio = max(0.0, _alpha_value(row, 'volume_ratio', default=_alpha_value(profile, 'volume_ratio')))
    turnover = max(0.0, _alpha_value(row, 'turnover_rate', default=_alpha_value(profile, 'turnover_rate')))
    low_position = _alpha_norm(row.get('low_position_catalyst_score'))
    entry_quality = _alpha_norm(adjustment.get('entry_quality_score'))
    flow = _alpha_norm(
        row.get('fund_flow_momentum')
        if row.get('fund_flow_momentum') is not None
        else profile.get('fund_flow_momentum'),
    )
    time_series = _alpha_norm(profile.get('time_series_momentum'))
    order_book = _alpha_norm(profile.get('order_book_pressure'))
    explicit_acceleration = row.get('capital_acceleration')
    if explicit_acceleration is None:
        explicit_acceleration = row.get('fund_flow_acceleration')
    capital_acceleration = _alpha_norm(explicit_acceleration)
    if explicit_acceleration is None:
        capital_acceleration = min(
            1.0,
            flow * 0.40
            + time_series * 0.25
            + order_book * 0.15
            + min(1.0, volume_ratio / 2.0) * 0.20,
        )
    new_buyer_pressure = _alpha_norm(
        row.get('new_buyer_pressure'),
        1.0,
    )
    sector_diffusion = _alpha_norm(
        row.get('sector_capital_diffusion')
        if row.get('sector_capital_diffusion') is not None
        else row.get('sector_propagation_score')
        if row.get('sector_propagation_score') is not None
        else profile.get('sector_opportunity_score'),
    )
    leader_confirmation = _alpha_norm(
        row.get('leader_confirmation')
        if row.get('leader_confirmation') is not None
        else row.get('continuation_gene_score'),
    )
    profit_chip_ratio = _alpha_norm(row.get('profit_chip_ratio'))
    overhead_supply = _alpha_norm(row.get('overhead_supply_score'))
    short_term_profit_pressure = _alpha_norm(row.get('short_term_profit_pressure'))
    chip_pressure = max(profit_chip_ratio, overhead_supply, short_term_profit_pressure)
    if not any(row.get(key) is not None for key in (
        'profit_chip_ratio', 'overhead_supply_score', 'short_term_profit_pressure',
    )):
        chip_pressure = min(
            1.0,
            max(0.0, (close_position - 0.70) / 0.30) * 0.55
            + max(0.0, (pct - 5.0) / 5.0) * 0.30
            + min(1.0, turnover / 20.0) * 0.15,
        )
    not_fully_priced = min(
        1.0,
        max(
            0.0,
            (1.0 - max(0.0, pct) / 10.0) * 0.55
            + (1.0 - close_position) * 0.25
            + low_position * 0.20,
        ),
    )
    payoff = min(
        1.0,
        max(
            0.0,
            not_fully_priced * 0.30
            + entry_quality * 0.20
            + (1.0 - chip_pressure) * 0.20
            + max(paths and 0.75 or 0.0, capital_acceleration) * 0.20
            + sector_diffusion * 0.10,
        ),
    )
    capital_risk = row.get('capital_risk_profile')
    if not isinstance(capital_risk, dict):
        capital_risk = candidate_capital_risk_profile(row)
    failed_limitup = _alpha_norm(capital_risk.get('failed_limitup_risk'))
    outflow = _alpha_norm(capital_risk.get('main_buy_outflow_pressure'))
    crowding = max(
        _alpha_norm(row.get('crowding_acceleration')),
        _alpha_norm(row.get('popularity_crowding_risk')),
        _alpha_norm(capital_risk.get('popularity_crowding_risk')),
    )
    limitup_capture = _alpha_norm(profile.get('limitup_capture_score'))
    late_acceleration = _alpha_norm(row.get('late_day_acceleration'))
    if row.get('late_day_acceleration') is None:
        late_acceleration = max(0.0, min(1.0, (close_position - 0.82) / 0.18))
    overreaction = _alpha_norm(row.get('intraday_overreaction'))
    if row.get('intraday_overreaction') is None:
        overreaction = min(1.0, max(0.0, (max(0.0, pct) - 6.0) / 4.0))
    turnover_stress = _alpha_norm(row.get('turnover_stress'))
    if row.get('turnover_stress') is None:
        turnover_stress = min(1.0, max(0.0, (turnover - 12.0) / 18.0))
    distribution = _alpha_norm(adjustment.get('distribution_risk_score'))
    reversal_risk = min(
        1.0,
        max(
            0.0,
            overreaction * 0.15
            + min(1.0, max(0.0, pct) / 10.0) * 0.10
            + crowding * 0.12
            + chip_pressure * 0.16
            + _alpha_norm(row.get('overhead_supply_score')) * 0.12
            + late_acceleration * 0.08
            + turnover_stress * 0.07
            + limitup_capture * 0.08
            + distribution * 0.07
            + failed_limitup * 0.08
            + outflow * 0.07,
        ),
    )
    regime = str(
        row.get('market_regime')
        or profile.get('market_regime')
        or row.get('production_regime')
        or 'sideways'
    ).strip().lower()
    state_fit = {
        'ice': 0.45,
        'repair': 0.70,
        'startup': 0.85,
        'expansion': 0.80,
        'strong': 0.85,
        'main_rise': 0.78,
        'climax': 0.30,
        'divergence': 0.55,
        'retreat': 0.25,
        'weak': 0.30,
        'risk_off': 0.20,
        'neutral': 0.60,
        'sideways': 0.60,
    }.get(regime, 0.55)
    state_fit = min(
        1.0,
        max(
            0.0,
            state_fit * 0.60
            + sector_diffusion * 0.20
            + leader_confirmation * 0.20,
        ),
    )
    amount = max(0.0, _alpha_value(row, 'amount', '成交额'))
    liquidity = _alpha_norm(row.get('liquidity'))
    if row.get('liquidity') is None:
        liquidity = min(1.0, amount / 500_000_000.0) if amount else min(1.0, turnover / 12.0)
    spread = _alpha_norm(row.get('spread'))
    depth = _alpha_norm(row.get('order_book_depth'))
    slippage = _alpha_norm(row.get('expected_slippage'), 0.02)
    capacity = _alpha_norm(row.get('position_capacity'), 100_000.0)
    execution_quality = min(
        1.0,
        max(
            0.0,
            liquidity * 0.35
            + depth * 0.20
            + (1.0 - spread) * 0.15
            + (1.0 - slippage) * 0.15
            + capacity * 0.15,
        ),
    )
    modules = {
        't1_expected_payoff': round(payoff, 6),
        't1_reversal_risk': round(reversal_risk, 6),
        'marginal_demand_score': round(
            min(
                1.0,
                max(
                    0.0,
                    capital_acceleration * 0.45
                    + new_buyer_pressure * 0.20
                    + sector_diffusion * 0.15
                    + leader_confirmation * 0.10
                    + min(1.0, max(0.0, close_position)) * 0.10,
                ),
            ),
            6,
        ),
        'state_fit': round(state_fit, 6),
        'execution_quality': round(execution_quality, 6),
        'capital_acceleration': round(capital_acceleration, 6),
        'profit_chip_ratio': round(profit_chip_ratio, 6),
        'overhead_supply_score': round(overhead_supply, 6),
        'primary_alpha_paths': paths,
        'market_regime': regime,
        't1_alpha_weights': dict(FORMAL_ALPHA_WEIGHTS),
    }
    modules['t1_alpha_score'] = round(
        (
            modules['t1_expected_payoff'] * FORMAL_ALPHA_WEIGHTS['t1_expected_payoff']
            + (1.0 - modules['t1_reversal_risk']) * FORMAL_ALPHA_WEIGHTS['t1_reversal_safety']
            + modules['marginal_demand_score'] * FORMAL_ALPHA_WEIGHTS['marginal_demand']
            + modules['state_fit'] * FORMAL_ALPHA_WEIGHTS['state_fit']
            + modules['execution_quality'] * FORMAL_ALPHA_WEIGHTS['execution_quality']
        ) * 100.0,
        6,
    )
    # Alpha paths are retained as categorical T-day evidence. They do not
    # create a candidate gate or reserve a formal-ranking seat.
    modules['alpha_path_eligible'] = bool(paths)
    modules['used_for_official_ranking'] = False
    modules['alpha_paths_used_as'] = 'T_DAY_FEATURE_GROUP_ONLY'
    return modules


_INVALID_T1_PRODUCTION_RANK = -1_000_000.0
_T1_PREDICTION_FIELDS = (
    'expected_t1_net_return',
    'cross_sectional_edge',
    'p_win',
    'expected_downside',
    'uncertainty',
    'execution_cost',
    'tradable_edge',
)


def _prediction_timestamp(value: Any) -> str:
    """Normalize an ISO-like timestamp for the prediction availability audit."""
    return str(value or '').strip().replace('T', ' ')


def t1_production_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the sole production input for a T+1 net-return decision.

    Raw T-day fields may be used by the trained model before it creates this
    record. The production selector intentionally cannot inspect those fields:
    it receives only a timestamp-audited prediction and its uncertainty/cost
    terms. This prevents legacy same-day-strength composites from becoming an
    implicit fallback.
    """
    prediction = row.get('t1_alpha_prediction')
    if not isinstance(prediction, dict):
        return {'valid': False, 'reason': 'T1_ALPHA_PREDICTION_MISSING'}

    model_id = str(prediction.get('model_id') or '').strip()
    model_status = str(prediction.get('model_status') or '').strip().upper()
    if not model_id:
        return {'valid': False, 'reason': 'T1_ALPHA_MODEL_ID_MISSING'}
    if model_status != 'PRODUCTION':
        return {
            'valid': False,
            'reason': 'T1_ALPHA_MODEL_NOT_PRODUCTION',
            'model_id': model_id,
            'model_status': model_status or 'MISSING',
        }

    signal_time = _prediction_timestamp(prediction.get('signal_time'))
    feature_timestamp = _prediction_timestamp(prediction.get('feature_timestamp'))
    feature_available_at = _prediction_timestamp(prediction.get('feature_available_at'))
    prediction_available_at = _prediction_timestamp(prediction.get('prediction_available_at'))
    if not signal_time:
        return {'valid': False, 'reason': 'T1_ALPHA_SIGNAL_TIME_MISSING', 'model_id': model_id}
    if not feature_timestamp or not feature_available_at or not prediction_available_at:
        return {
            'valid': False,
            'reason': 'T1_ALPHA_AVAILABILITY_METADATA_MISSING',
            'model_id': model_id,
        }
    if (
        feature_timestamp > signal_time
        or feature_available_at > signal_time
        or prediction_available_at > signal_time
    ):
        return {
            'valid': False,
            'reason': 'T1_ALPHA_AVAILABILITY_AFTER_SIGNAL_TIME',
            'model_id': model_id,
            'signal_time': signal_time,
            'feature_timestamp': feature_timestamp,
            'feature_available_at': feature_available_at,
            'prediction_available_at': prediction_available_at,
        }

    values: Dict[str, float] = {}
    for field in _T1_PREDICTION_FIELDS:
        value = safe_float(prediction.get(field))
        if value is None or not math.isfinite(value):
            return {
                'valid': False,
                'reason': 'T1_ALPHA_PREDICTION_FIELD_INVALID:' + field,
                'model_id': model_id,
            }
        values[field] = float(value)
    if not 0.0 <= values['p_win'] <= 1.0:
        return {'valid': False, 'reason': 'T1_ALPHA_P_WIN_OUT_OF_RANGE', 'model_id': model_id}
    if any(values[field] < 0.0 for field in ('expected_downside', 'uncertainty', 'execution_cost')):
        return {'valid': False, 'reason': 'T1_ALPHA_RISK_TERM_NEGATIVE', 'model_id': model_id}

    return {
        'valid': True,
        'reason': '',
        'model_id': model_id,
        'model_status': model_status,
        'signal_time': signal_time,
        'feature_timestamp': feature_timestamp,
        'feature_available_at': feature_available_at,
        'prediction_available_at': prediction_available_at,
        **{field: round(values[field], 10) for field in _T1_PREDICTION_FIELDS},
    }


def formal_candidate_sort_key(row: Dict[str, Any]) -> Tuple[float, ...]:
    """Return the sole prediction-first T+1 net-return rank tuple."""
    prediction = t1_production_prediction(row)
    if not prediction['valid']:
        return (_INVALID_T1_PRODUCTION_RANK,) * len(_T1_PREDICTION_FIELDS)
    return (
        prediction['tradable_edge'],
        prediction['expected_t1_net_return'],
        prediction['cross_sectional_edge'],
        prediction['p_win'],
        -prediction['expected_downside'],
        -prediction['uncertainty'],
        -prediction['execution_cost'],
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
