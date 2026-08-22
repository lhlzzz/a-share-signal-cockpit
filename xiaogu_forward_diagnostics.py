#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-owner extraction from the production forward runner.

The production entry remains ``xiaogu_forward_runner.py``. This module only
owns the responsibility named in its filename and is host-bound so existing
imports and test monkeypatches retain their behavior.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple
from xiaogu_forward_host_binding import create_host_binding

_HOST = None
REQUIRED_FROM_HOST = ('LOCKED_SAFETY', 'MISSING_INFORMATION_COVERAGE_AUDIT', 'NO_PICK_DIAGNOSTIC_CANDIDATE_LIMIT', '_cached_decision_for_candidate', '_cached_paper_pick_eligibility_profile', 'attach_paper_pick_eligibility', 'bundle_metric', 'candidate_capital_risk_profile', 'candidate_rank_value', 'candidate_score_value', 'formal_candidate_sort_key', 'load_profit_shadow_watchlist', 'normalized_block_bucket', 'official_target_exclusion_reasons', 'paper_pick_risk_explanation_gate', 'paper_sizing_context', 'ranking_basis_adjustment_components', 'safe_float', 'safe_int', 'single_target_card_status', 'symbol_for', 'unique_text_values')

bind_host, _inject_host, _with_host = create_host_binding(
    globals(), REQUIRED_FROM_HOST, preserve_existing_on_missing=True,
)

NO_PICK_HARD_BLOCK_PREFIXES = (
    'regulatory_hard_block',
    'risk_too_high',
    'DATA_GATE_NOT_PASS',
    'SCAN_TOO_OLD_',
    'SCAN_AFTER_RUNNER_ASOF_',
    'STALE_BUNDLE_DATE',
    'STALE_SOURCE_MARKET_DATE',
    'SOURCE_MARKET_DATE_MISSING',
    'STALE_SOURCE_TIME',
    'SOURCE_TIME_MISSING',
    'near_limit_up_risk',
    'QUALIFIED_CANDIDATE_FALSE',
    'ONE_LOT_COST_GT_ACCOUNT_AVAILABLE_CASH',
    'ONE_LOT_COST_GT_CAP_OR_INVALID',
    'XIAOCHAN_BLOCK',
    'ASOF_LEAKAGE_FLAG_TRUE',
)

def candidate_can_afford_one_lot(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> bool:
    sizing = paper_sizing_context(bundle)
    available_cash = safe_float(sizing.get('available_cash'))
    one_lot_cap = safe_float(sizing.get('one_lot_cost_cap'))
    decision_cap_candidates = [cap for cap in (available_cash, one_lot_cap) if cap is not None]
    decision_cap = min(decision_cap_candidates) if decision_cap_candidates else None
    price = safe_float(candidate.get('price'))
    one_lot_cost = safe_float(candidate.get('one_lot_cost'))
    if one_lot_cost is None and price is not None:
        one_lot_cost = price * 100
    # Missing account data must not demote a production pick. It only means
    # execution sizing cannot be asserted from this run.
    return bool(one_lot_cost is not None and (decision_cap is None or one_lot_cost <= decision_cap))

def build_candidate_diagnostic_card(
    candidate: Dict[str, Any],
    bundle: Dict[str, Any],
    target_date: str,
    decision: str,
    reason: str,
    flags: List[str],
    diagnostic_role: str,
) -> Dict[str, Any] | None:
    candidate = candidate if isinstance(candidate, dict) else {}
    if not candidate:
        return None
    eligibility = candidate.get('paper_pick_eligibility') if isinstance(candidate.get('paper_pick_eligibility'), dict) else {}
    missing_conditions = [str(item) for item in (eligibility.get('missing_conditions') or []) if item]
    blockers = [str(item) for item in (eligibility.get('blockers') or []) if item]
    positive_conditions = [str(item) for item in (eligibility.get('positive_conditions') or []) if item]
    selection_reasons_zh = [
        str(item)
        for item in (
            candidate.get('selection_reasons_zh')
            or eligibility.get('selection_reasons_zh')
            or []
        )
        if item
    ]
    signals = eligibility.get('signals') if isinstance(eligibility.get('signals'), dict) else {}
    why_not_official_pick = unique_text_values([
        *([part for part in str(reason or '').split(';') if part]),
        *missing_conditions,
        *blockers,
    ])
    diagnostic_score = candidate_score_value(candidate)
    raw_score = candidate.get('score')
    profile = candidate
    selection_reason = candidate.get('selection_reason') or candidate.get('final_score_explanation') or ''
    candidate_reasons = unique_text_values([
        selection_reason,
        *positive_conditions,
    ])
    capital_risk_profile = candidate.get('capital_risk_profile') if isinstance(candidate.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(candidate)
    return {
        'diagnostic_role': diagnostic_role,
        'symbol': symbol_for(candidate),
        'name': candidate.get('name'),
        'rank': candidate.get('rank'),
        'score': raw_score if raw_score is not None else diagnostic_score,
        'final_score': diagnostic_score,
        'selection_reason': selection_reason,
        'sentiment_catalyst': candidate.get('sentiment_catalyst'),
        'theme_catalyst': candidate.get('theme_catalyst'),
        'news_catalyst': candidate.get('news_catalyst'),
        'positive_catalyst': candidate.get('positive_catalyst'),
        'structured_score': candidate.get('structured_score'),
        'structured_components': candidate.get('structured_components') or candidate.get('structured_score_components') or {},
        'structured_component_details': candidate.get('structured_component_details') or {},
        'ranking_basis': candidate.get('ranking_basis') or '',
        'auxiliary_evidence_status': candidate.get('mainboard_auxiliary_evidence_status') or candidate.get('auxiliary_evidence_status'),
        'known_missing_evidence': list(candidate.get('mainboard_auxiliary_missing_domains') or []),
        'announcement_evidence': list(candidate.get('announcement_evidence') or []),
        'news_evidence': dict(candidate.get('news_evidence') or {}),
        'sector_news_evidence': list(candidate.get('sector_news_evidence') or []),
        'limitup_reason_evidence': list(candidate.get('limitup_reason_evidence') or []),
        'yesterday_limitup_gene_evidence': dict(candidate.get('yesterday_limitup_gene_evidence') or {
            'status': 'MISSING',
            'candidate_was_yesterday_limitup': False,
            'records': [],
            'source': 'limitup_yesterday',
        }),
        'sector_yesterday_limitup_gene_proxy': dict(candidate.get('sector_yesterday_limitup_gene_proxy') or {
            'status': 'MISSING',
            'sector_matches': [],
            'one_word_sector_matches': [],
            'proxy_sources': ['limitup_yesterday', 'limitup_yesterday_one_word'],
        }),
        'continuation_gene_score': safe_float(candidate.get('continuation_gene_score')) or 0.0,
        'limitup_reason_status': candidate.get('limitup_reason_status') or 'MISSING',
        'limitup_reason_hard_block': bool(candidate.get('limitup_reason_hard_block', False)),
        'risk_notice_evidence': list(candidate.get('risk_notice_evidence') or []),
        'capital_flow_evidence': dict(candidate.get('data_directory_capital_flow') or {}),
        'popularity_rank': capital_risk_profile.get('popularity_rank'),
        'capital_risk_profile': capital_risk_profile,
        'risk_flags': list(capital_risk_profile.get('risk_codes') or []),
        'expected_edge': candidate.get('expected_edge') or candidate.get('final_score_explanation') or '',
        'contrarian_score': safe_float(candidate.get('_contrarian_score')),
        'score_archetype_adjustment': candidate.get('_re_score_archetype_adjustment') if isinstance(candidate.get('_re_score_archetype_adjustment'), dict) else {},
        'candidate_evidence_status': candidate.get('candidate_evidence_status'),
        'source_layers': list(candidate.get('source_layers') or []),
        'search_layer': candidate.get('search_layer') or candidate.get('search_layer_hint') or profile.get('search_layer_hint') or '',
        'candidate_stage': profile.get('candidate_stage') or candidate.get('candidate_stage') or '',
        'hard_gate_status': {
            'official_decision': decision,
            'candidate_evidence_status': candidate.get('candidate_evidence_status'),
            'data_gate_status': candidate.get('data_gate_status') or candidate.get('data_gate'),
            'paper_pick_eligible': bool(eligibility.get('eligible')),
            'flags': list(flags or []),
        },
        'target_status': single_target_card_status(
            decision or 'NO_PICK',
            candidate,
            flags,
            candidate_can_afford_one_lot(candidate, bundle),
            bundle,
        ),
        'official_decision_if_evaluated': decision,
        'official_decision_reason_if_evaluated': reason,
        'missing_conditions': missing_conditions,
        'blockers': blockers,
        'positive_conditions': positive_conditions,
        'selection_reasons_zh': selection_reasons_zh,
        'signals': signals,
        'missing_condition_count': len(missing_conditions),
        'blocker_count': len(blockers),
        'positive_condition_count': len(positive_conditions),
        'candidate_reasons': candidate_reasons,
        'why_candidate': candidate_reasons,
        'not_selected_reasons': why_not_official_pick,
        'why_not_selected': why_not_official_pick,
        'why_not_official_pick': why_not_official_pick,
    }

def candidate_is_selection_eligible(candidate: Dict[str, Any]) -> bool:
    symbol = symbol_for(candidate)
    price = safe_float(candidate.get('price'))
    return bool(symbol and symbol.isdigit() and len(symbol) == 6 and price is not None and price > 0)

def formal_diagnostic_candidate_from_bundle(bundle: Dict[str, Any]) -> Tuple[Dict[str, Any] | None, str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidates = [candidate for candidate in bundle.get('paper_scoring_candidates', []) if isinstance(candidate, dict)]
    eligible = [
        candidate
        for candidate in candidates
        if candidate_is_selection_eligible(candidate)
    ]
    if not eligible:
        return None, 'no_formal_diagnostic_candidate_available'
    return max(
        eligible,
        key=lambda candidate: (
            formal_candidate_sort_key(candidate),
            -candidate_rank_value(candidate),
            symbol_for(candidate),
        ),
    ), ''

def is_no_pick_hard_blocker(text: str) -> bool:
    normalized = str(text or '')
    return any(prefix in normalized for prefix in NO_PICK_HARD_BLOCK_PREFIXES)

def no_pick_promotion_eligible(candidate: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Tuple[bool, str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    if not isinstance(candidate, dict) or not candidate_is_selection_eligible(candidate):
        return False, 'candidate_not_selection_eligible'

    candidate_with_gates = dict(candidate)
    capital_risk_profile = candidate_with_gates.get('capital_risk_profile')
    if not isinstance(capital_risk_profile, dict):
        capital_risk_profile = candidate_capital_risk_profile(candidate_with_gates)
        candidate_with_gates['capital_risk_profile'] = capital_risk_profile
    if (
        candidate_with_gates.get('failed_limitup')
        or capital_risk_profile.get('failed_limitup')
        or (safe_float(capital_risk_profile.get('failed_limitup_risk')) or 0.0) > 0
    ):
        return False, 'failed_limitup_risk'
    if capital_risk_profile.get('risk_codes'):
        return False, 'capital_risk:' + str(capital_risk_profile['risk_codes'][0])

    eligibility = candidate_with_gates.get('paper_pick_eligibility')
    if not isinstance(eligibility, dict):
        eligibility = _cached_paper_pick_eligibility_profile(candidate_with_gates, bundle)
        candidate_with_gates['paper_pick_eligibility'] = eligibility
    blockers = [str(item) for item in (eligibility.get('blockers') or []) if item]
    if candidate_with_gates.get('near_limit_up_risk') or any(normalized_block_bucket(item) == 'near_limit_up_risk' or 'near_limit_up_risk' in item for item in blockers):
        return False, 'near_limit_up_risk'
    for blocker in blockers:
        if blocker == 'mainboard_auxiliary_evidence_status_not_PASS':
            return False, blocker
        if blocker.startswith('regulatory_hard_block:'):
            return False, blocker
        if is_no_pick_hard_blocker(blocker):
            return False, blocker
    if not bool(eligibility.get('eligible')):
        return False, blockers[0] if blockers else 'paper_pick_eligibility_not_eligible'

    risk_gate = eligibility.get('paper_pick_risk_explanation_gate') if isinstance(eligibility.get('paper_pick_risk_explanation_gate'), dict) else paper_pick_risk_explanation_gate(candidate_with_gates)
    if risk_gate.get('status') == 'FAIL':
        return False, 'PAPER_PICK_RISK_EXPLANATION_GATE_FAIL'
    candidate_with_gates['paper_pick_eligibility'] = {**eligibility, 'paper_pick_risk_explanation_gate': risk_gate}
    exclusion_reasons = official_target_exclusion_reasons(candidate_with_gates, bundle)
    if exclusion_reasons:
        return False, str(exclusion_reasons[0])
    return True, ''

def paper_pick_source_health_flags(bundle: Dict[str, Any]) -> List[str]:
    source_status = bundle.get('source_status') if isinstance(bundle.get('source_status'), dict) else {}
    source_completeness = source_status.get('source_completeness') if isinstance(source_status.get('source_completeness'), dict) else {}
    if not source_completeness:
        completeness_required = bool(
            bundle.get('source_completeness_required')
            or source_status.get('source_completeness_required')
            or source_status.get('blocking_source_completeness_required')
        )
        if completeness_required:
            return ['SOURCE_COMPLETENESS_MISSING', 'DATA_SOURCE_INCOMPLETE']
        return []
    flags: List[str] = []
    completeness_flags = [str(item) for item in (source_completeness.get('flags') or []) if item]
    flags.extend(completeness_flags)
    quote_count = int(
        bundle.get('market_snapshot', {}).get('universe_quote_count')
        or bundle.get('universe_quote_count')
        or source_completeness.get('quote_count')
        or 0
    )
    if quote_count < int(source_completeness.get('min_quote_count') or 0):
        flags.append('FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW')
    if int(source_completeness.get('quote_count') or 0) <= 0:
        flags.append('ZERO_QUOTE_READ')
    if int(source_completeness.get('fund_count') or 0) <= 0:
        flags.append('ZERO_FUND_FLOW_READ')
    return unique_text_values(flags)

def ranked_no_pick_candidate_evaluations(
    bundle: Dict[str, Any],
    target_date: str,
) -> Tuple[List[Dict[str, Any]], str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidates = [candidate for candidate in bundle.get('paper_scoring_candidates', []) if isinstance(candidate, dict)]
    if not candidates:
        return [], 'no_paper_scoring_candidates_available'

    evaluations: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not candidate_is_selection_eligible(candidate):
            continue
        decision, symbol, reason, features, flags = _cached_decision_for_candidate(candidate, bundle, target_date)
        eligibility = features.get('paper_pick_eligibility') if isinstance(features.get('paper_pick_eligibility'), dict) else {}
        blockers = [str(item) for item in (eligibility.get('blockers') or []) if item]
        hard_block_count = sum(1 for blocker in blockers if is_no_pick_hard_blocker(blocker))
        evidence_penalty = 0 if str(features.get('candidate_evidence_status') or '').upper() == 'PASS' else 1
        blocker_count = len(blockers)
        formal_key = formal_candidate_sort_key(candidate)
        score = float(formal_key[0]) if formal_key else None
        rank = candidate_rank_value(features)
        score_sort = -score if score is not None else 1_000_000_000.0
        selection_key = (
            hard_block_count,
            evidence_penalty,
            blocker_count,
            score_sort,
            rank,
            symbol or symbol_for(candidate),
        )
        features = dict(features)
        features['paper_pick_eligibility'] = dict(features.get('paper_pick_eligibility') or {})
        features['paper_pick_eligibility']['blockers'] = blockers
        evaluations.append({
            'selection_key': selection_key,
            'features': features,
            'decision': decision,
            'reason': reason,
            'flags': list(flags or []),
            'blockers': blockers,
            'hard_block_count': hard_block_count,
            'evidence_penalty': evidence_penalty,
            'blocker_count': blocker_count,
            'score': score,
            'rank': rank,
            'symbol': symbol or symbol_for(candidate),
        })

    if not evaluations:
        return [], 'no_selection_candidate_met_symbol_and_price_requirements'
    evaluations.sort(key=lambda item: item['selection_key'])
    return evaluations, ''

def closest_to_pick_candidate_from_bundle(
    bundle: Dict[str, Any],
    target_date: str,
) -> Tuple[Dict[str, Any] | None, str]:
    evaluations, reason = ranked_no_pick_candidate_evaluations(bundle, target_date)
    if not evaluations:
        return None, reason
    return evaluations[0]['features'], ''

def summarize_evaluation_text_counts(evaluations: List[Dict[str, Any]], eligibility_key: str) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for evaluation in evaluations:
        features = evaluation.get('features') if isinstance(evaluation.get('features'), dict) else {}
        eligibility = features.get('paper_pick_eligibility') if isinstance(features.get('paper_pick_eligibility'), dict) else {}
        for item in eligibility.get(eligibility_key) or []:
            text = str(item).strip()
            if text:
                counter[text] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))

def summarize_evaluation_reason_counts(evaluations: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for evaluation in evaluations:
        for item in str(evaluation.get('reason') or '').split(';'):
            text = item.strip()
            if text:
                counter[text] += 1
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))

def build_daily_best_paper_watch(no_pick_diagnostics: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(no_pick_diagnostics, dict):
        return None
    # The watch is a view of the same production ordering, not a second
    # quality-proximity policy. Main-force production score is primary.
    closest = no_pick_diagnostics.get('closest_to_pick_candidate')
    highest = no_pick_diagnostics.get('formal_diagnostic_candidate')
    ranked = no_pick_diagnostics.get('ranked_no_pick_candidates')
    ranked0 = ranked[0] if isinstance(ranked, list) and ranked and isinstance(ranked[0], dict) else None

    def _watch_card_quality(card: Dict[str, Any] | None) -> Tuple[float, int, float]:
        if not isinstance(card, dict) or not symbol_for(card):
            return (-1.0, 99, 9999.0)
        score = float(safe_float(card.get('final_score') if card.get('final_score') is not None else card.get('score')) or 0.0)
        rank = float(safe_float(card.get('rank')) or 9999.0)
        blockers = len(list(card.get('blockers') or []))
        # The watch is a diagnostic of the same production chain, not a second
        # quality policy. Formal score is therefore the primary ordering key.
        return (score, -blockers, -rank)

    labeled: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(highest, dict) and symbol_for(highest):
        # A watch candidate must come from the same production score path.
        # Other diagnostics are context only and cannot replace it.
        labeled.append(('formal_diagnostic_candidate', highest))
    else:
        if isinstance(closest, dict) and symbol_for(closest):
            labeled.append(('closest_to_pick_candidate', closest))
        if isinstance(ranked0, dict) and symbol_for(ranked0):
            labeled.append(('ranked_no_pick_candidates', ranked0))
    if not labeled:
        return None
    selection_source, card = min(
        labeled,
        key=lambda item: (
            (
                -_watch_card_quality(item[1])[0],
                -_watch_card_quality(item[1])[1],
                -_watch_card_quality(item[1])[2],
            ),
        ),
    )
    if not isinstance(card, dict) or not symbol_for(card):
        return None
    return {
        'status': 'DAILY_BEST_PAPER_WATCH',
        'official_decision': 'NO_PICK',
        'not_official_paper_pick': True,
        'observation_only': True,
        'watch_status': 'DAILY_BEST_PAPER_WATCH',
        'selection_source': selection_source,
        'symbol': symbol_for(card),
        'name': card.get('name'),
        'rank': card.get('rank'),
        'score': card.get('score'),
        'final_score': card.get('final_score'),
        'target_status': card.get('target_status'),
        'candidate_evidence_status': card.get('candidate_evidence_status'),
        'official_decision_if_evaluated': card.get('official_decision_if_evaluated'),
        'official_decision_reason_if_evaluated': card.get('official_decision_reason_if_evaluated'),
        'missing_conditions': card.get('missing_conditions') or [],
        'blockers': card.get('blockers') or [],
        'positive_conditions': card.get('positive_conditions') or [],
        'why_not_official_pick': card.get('why_not_official_pick') or [],
        'signals': card.get('signals') if isinstance(card.get('signals'), dict) else {},
        'selection_basis': [
            'main_force_behavior_chain production_score desc',
            'blocker_count asc',
            'formal_rank asc',
        ],
        'explanation': (
            'Paper-watch follows the main-force production score and formal rank; it is diagnostic only '
            'when the official decision is NO_PICK.'
        ),
        **LOCKED_SAFETY,
        'allow_trade': False,
        'manual_paper_execution_allowed': False,
    }

def build_no_pick_candidate_diagnostics(
    bundle: Dict[str, Any],
    target_date: str,
    first_rejected_candidate: Dict[str, Any],
    first_rejected_decision: str,
    first_rejected_reason: str,
    first_rejected_flags: List[str],
) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    attach_paper_pick_eligibility(bundle)
    highest_candidate, highest_reason = formal_diagnostic_candidate_from_bundle(bundle)
    ranked_evaluations, ranked_reason = ranked_no_pick_candidate_evaluations(bundle, target_date)
    closest_candidate = ranked_evaluations[0]['features'] if ranked_evaluations else None
    closest_reason = ranked_reason
    diagnostic_limit = NO_PICK_DIAGNOSTIC_CANDIDATE_LIMIT
    shown_evaluations = ranked_evaluations[:diagnostic_limit]

    ranked_cards: List[Dict[str, Any]] = []
    for index, evaluation in enumerate(shown_evaluations, start=1):
        card = build_candidate_diagnostic_card(
            evaluation['features'],
            bundle,
            target_date,
            evaluation['decision'],
            evaluation['reason'],
            evaluation['flags'],
            'ranked_no_pick_candidate',
        )
        if card is None:
            continue
        card.update({
            'diagnostic_rank': index,
            'selection_key': list(evaluation['selection_key']),
            'hard_block_count': evaluation['hard_block_count'],
            'evidence_penalty': evaluation['evidence_penalty'],
        })
        ranked_cards.append(card)

    diagnostics = {
        'first_rejected_candidate': build_candidate_diagnostic_card(
            first_rejected_candidate,
            bundle,
            target_date,
            first_rejected_decision,
            first_rejected_reason,
            first_rejected_flags,
            'first_rejected_candidate',
        ),
        'formal_diagnostic_candidate': None,
        'formal_diagnostic_candidate_reason': highest_reason,
        'closest_to_pick_candidate': None,
        'closest_to_pick_candidate_reason': closest_reason,
        'paper_scoring_candidates_count': len([candidate for candidate in (bundle.get('paper_scoring_candidates') or []) if isinstance(candidate, dict)]),
        'scan_passed_count': safe_int(bundle_metric(bundle, 'passed_count')),
        'scan_scored_count': safe_int(bundle_metric(bundle, 'scored_count')),
        'selection_basis': [
            'hard_block_count asc',
            'candidate_evidence_status PASS first',
            'blocker_count asc',
            'risk_adjusted_score desc',
            'rank asc',
        ],
        'diagnostic_candidate_limit': diagnostic_limit,
        'ranked_no_pick_candidates': ranked_cards,
        'ranked_no_pick_candidates_reason': ranked_reason,
        'ranked_no_pick_candidates_total': len(ranked_evaluations),
        'ranked_no_pick_candidates_shown': len(ranked_cards),
        'ranked_no_pick_candidates_omitted': max(0, len(ranked_evaluations) - len(ranked_cards)),
        'blocker_summary': summarize_evaluation_text_counts(ranked_evaluations, 'blockers'),
        'missing_condition_summary': summarize_evaluation_text_counts(ranked_evaluations, 'missing_conditions'),
        'positive_condition_summary': summarize_evaluation_text_counts(ranked_evaluations, 'positive_conditions'),
        'decision_reason_summary': summarize_evaluation_reason_counts(ranked_evaluations),
        'diagnostic_scope_explanation': 'ranked_no_pick_candidates evaluates formal paper_scoring_candidates only; official gates are unchanged; A-share output remains MANUAL_TRADE_ONLY / paper_only / no_trade',
    }

    if diagnostics['first_rejected_candidate'] is not None and diagnostics['scan_passed_count'] is None:
        diagnostics['scan_passed_count'] = safe_int(bundle_metric(bundle, 'passed_count'))

    if highest_candidate is not None:
        highest_decision, _, highest_evaluated_reason, highest_features, highest_flags = _cached_decision_for_candidate(highest_candidate, bundle, target_date)
        diagnostics['formal_diagnostic_candidate'] = build_candidate_diagnostic_card(
            highest_features,
            bundle,
            target_date,
            highest_decision,
            highest_evaluated_reason,
            highest_flags,
            'formal_diagnostic_candidate',
        )
    if closest_candidate is not None:
        closest_decision, _, closest_evaluated_reason, closest_features, closest_flags = _cached_decision_for_candidate(closest_candidate, bundle, target_date)
        diagnostics['closest_to_pick_candidate'] = build_candidate_diagnostic_card(
            closest_features,
            bundle,
            target_date,
            closest_decision,
            closest_evaluated_reason,
            closest_flags,
            'closest_to_pick_candidate',
        )
    diagnostics['daily_best_paper_watch'] = build_daily_best_paper_watch(diagnostics)
    if isinstance(diagnostics.get('daily_best_paper_watch'), dict):
        diagnostics['daily_best_paper_watch']['official_decision'] = 'NO_PICK'
        diagnostics['daily_best_paper_watch']['not_official_paper_pick'] = True
        diagnostics['daily_best_paper_watch']['observation_only'] = True
        diagnostics['daily_best_paper_watch']['watch_status'] = 'DAILY_BEST_PAPER_WATCH'

    # P0: attach profit-shadow topN as observation-only when official is NO_PICK.
    profit_shadow_watch = load_profit_shadow_watchlist(target_date, top_n=5)
    diagnostics['profit_candidate_shadow_watch'] = profit_shadow_watch
    if isinstance(diagnostics.get('daily_best_paper_watch'), dict):
        diagnostics['daily_best_paper_watch']['profit_shadow_top'] = list(
            profit_shadow_watch.get('candidates') or []
        )
        diagnostics['daily_best_paper_watch']['profit_shadow_mainline_tags'] = list(
            profit_shadow_watch.get('mainline_tags') or []
        )[:8]
        diagnostics['daily_best_paper_watch']['profit_shadow_status'] = profit_shadow_watch.get('status')

    explanation_parts = [
        f"scan_passed_count={diagnostics['scan_passed_count']}" if diagnostics['scan_passed_count'] is not None else 'scan_passed_count=null',
        f"scan_scored_count={diagnostics['scan_scored_count']}" if diagnostics['scan_scored_count'] is not None else 'scan_scored_count=null',
        f"paper_scoring_candidates_count={diagnostics['paper_scoring_candidates_count']}",
        f"ranked_no_pick_candidates_total={diagnostics['ranked_no_pick_candidates_total']}",
        f"ranked_no_pick_candidates_shown={diagnostics['ranked_no_pick_candidates_shown']}",
        f"ranked_no_pick_candidates_omitted={diagnostics['ranked_no_pick_candidates_omitted']}",
        f"profit_shadow_watch_status={profit_shadow_watch.get('status')}",
        f"profit_shadow_candidates={len(profit_shadow_watch.get('candidates') or [])}",
        'scan_passed_count is broader than paper_scoring_candidates_count; only formal basket candidates are ranked here',
        'profit_candidate_shadow_watch is observation-only; official gates and trading safety are unchanged',
        'diagnostics visibility changed only; official gates and trading safety are unchanged',
    ]
    diagnostics['explanation'] = '; '.join(explanation_parts)
    return diagnostics

def _reason_parts(text: Any) -> List[str]:
    return [part.strip() for part in str(text or '').split(';') if part and str(part).strip()]

def _source_consumption_domain_summary(bundle: Dict[str, Any]) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    source_status = bundle.get('source_status') if isinstance(bundle.get('source_status'), dict) else {}
    available: List[str] = []
    partial: List[str] = []
    missing: List[str] = []
    for key, value in source_status.items():
        if not isinstance(value, dict):
            continue
        status = str(value.get('status') or '').upper()
        missing_items = [
            str(item) for item in (
                value.get('missing_domains')
                or value.get('missing_sources')
                or value.get('flags')
                or []
            ) if item
        ]
        if status in ('PASS', 'OK', 'AVAILABLE') and not missing_items:
            available.append(key)
        elif missing_items or status in ('PARTIAL', 'WARN', 'LOW_SAMPLE', 'PARTIAL_OR_FAIL'):
            partial.append(key)
        else:
            missing.append(key)
    if isinstance(bundle.get('information_coverage_audit'), dict):
        coverage_status = str(bundle['information_coverage_audit'].get('status') or '')
        if coverage_status and coverage_status not in ('PASS', 'OK'):
            partial.append('information_coverage_audit')
    return {
        'available': unique_text_values(available),
        'partial': unique_text_values(partial),
        'missing': unique_text_values(missing),
    }

def build_source_consumption_summary(bundle: Dict[str, Any]) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    domain_summary = _source_consumption_domain_summary(bundle)
    information_coverage_audit = dict(bundle.get('information_coverage_audit') or MISSING_INFORMATION_COVERAGE_AUDIT)
    source_flags = paper_pick_source_health_flags(bundle)
    notes = [
        'runner consumes all currently available scan and DB evidence from the selected bundle; missing or partial domains remain visible rather than silently ignored',
        'production behavior is consume-only: the runner never starts a scanner and fails closed when the same-day direct API artifact is absent',
    ]
    if domain_summary['partial'] or domain_summary['missing']:
        notes.append('partial or missing sections indicate data that was not available in the current API snapshot and remain explicit for governance')
    return {
        'candidate_source': str(bundle.get('candidate_source') or bundle.get('source') or ''),
        'consumption_policy': 'consume_same_day_direct_api_artifact_only',
        'scan_summary_path': str(bundle.get('scan_summary_path') or ''),
        'scan_summary_source_time': str(bundle.get('scan_summary_source_time') or bundle.get('source_time') or ''),
        'information_coverage_audit': information_coverage_audit,
        'source_health_flags': source_flags,
        'available_sections': domain_summary['available'],
        'partial_sections': domain_summary['partial'],
        'missing_sections': domain_summary['missing'],
        'available_section_count': len(domain_summary['available']),
        'partial_section_count': len(domain_summary['partial']),
        'missing_section_count': len(domain_summary['missing']),
        'notes': notes,
    }

def build_candidate_consumption_summary(
    bundle: Dict[str, Any],
    target_date: str,
    decision: str,
    symbol: str,
    reason: str,
    candidate_features: Dict[str, Any],
    risk_flags: List[str],
) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidate_features = candidate_features if isinstance(candidate_features, dict) else {}
    candidates = [candidate for candidate in (bundle.get('paper_scoring_candidates') or []) if isinstance(candidate, dict)]
    evaluations, evaluation_reason = ranked_no_pick_candidate_evaluations(bundle, target_date)
    evaluation_by_symbol = {str(item.get('symbol') or ''): item for item in evaluations if item.get('symbol')}

    def resolve_candidate_evaluation(candidate_row: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str, str, Dict[str, Any], List[str]]:
        candidate_symbol = symbol_for(candidate_row)
        evaluation = evaluation_by_symbol.get(candidate_symbol, {})
        if evaluation:
            return (
                evaluation,
                str(evaluation.get('decision') or ''),
                str(evaluation.get('symbol') or candidate_symbol),
                str(evaluation.get('reason') or ''),
                dict(evaluation.get('features') or {}),
                list(evaluation.get('flags') or []),
            )
        evaluated_decision, evaluated_symbol, evaluated_reason, evaluated_features, evaluated_flags = _cached_decision_for_candidate(candidate_row, bundle, target_date)
        return {}, evaluated_decision, evaluated_symbol, evaluated_reason, evaluated_features, evaluated_flags
    top10_candidates = sorted(
        candidates,
        key=lambda item: (
            candidate_rank_value(item),
            -(candidate_score_value(item) if candidate_score_value(item) is not None else -1e9),
            symbol_for(item),
        ),
    )[:10]

    official_card = None
    if candidate_features:
        official_card = build_candidate_diagnostic_card(
            candidate_features,
            bundle,
            target_date,
            decision,
            reason,
            risk_flags,
            'official_result',
        )

    top10_cards: List[Dict[str, Any]] = []
    for index, candidate in enumerate(top10_candidates, start=1):
        evaluation, evaluated_decision, evaluated_symbol, evaluated_reason, evaluated_features, evaluated_flags = resolve_candidate_evaluation(candidate)
        card = build_candidate_diagnostic_card(
            evaluated_features,
            bundle,
            target_date,
            evaluated_decision,
            evaluated_reason,
            evaluated_flags,
            'top10_candidate',
        )
        if card is None:
            continue
        card['top10_rank'] = index
        card['selection_key'] = list(evaluation.get('selection_key') or []) if isinstance(evaluation.get('selection_key'), tuple) else list(evaluation.get('selection_key') or [])
        card['selection_outcome'] = 'OFFICIAL_PICK' if decision == 'PAPER_PICK' and symbol and card['symbol'] == symbol else 'TOP10_NOT_SELECTED'
        card['selection_outcome_reason'] = reason if card['selection_outcome'] == 'OFFICIAL_PICK' else evaluated_reason
        card['eligibility_snapshot'] = dict(evaluated_features.get('paper_pick_eligibility') or {}) if isinstance(evaluated_features, dict) else {}
        card['hard_gate_status'] = {
            **(card.get('hard_gate_status') if isinstance(card.get('hard_gate_status'), dict) else {}),
            'selection_key': card['selection_key'],
        }
        card['candidate_reasons'] = unique_text_values([
            *(card.get('candidate_reasons') or []),
            *(card.get('positive_conditions') or []),
        ])
        card['why_candidate'] = list(card['candidate_reasons'])
        card['not_selected_reasons'] = unique_text_values([
            *(_reason_parts(evaluated_reason) if card['selection_outcome'] != 'OFFICIAL_PICK' else []),
            *(card.get('missing_conditions') or []),
            *(card.get('blockers') or []),
        ])
        card['why_not_selected'] = list(card['not_selected_reasons'])
        top10_cards.append(card)

    highest_candidate, highest_reason = formal_diagnostic_candidate_from_bundle(bundle)
    highest_card = None
    if highest_candidate is not None:
        _, highest_decision, _, highest_evaluated_reason, highest_features, highest_flags = resolve_candidate_evaluation(highest_candidate)
        highest_card = build_candidate_diagnostic_card(
            highest_features,
            bundle,
            target_date,
            highest_decision,
            highest_evaluated_reason,
            highest_flags,
            'formal_diagnostic_candidate',
        )
    closest_card = None
    if evaluations:
        closest_evaluation = evaluations[0]
        closest_card = build_candidate_diagnostic_card(
            dict(closest_evaluation.get('features') or {}),
            bundle,
            target_date,
            str(closest_evaluation.get('decision') or ''),
            str(closest_evaluation.get('reason') or ''),
            list(closest_evaluation.get('flags') or []),
            'closest_to_pick_candidate',
        )
    summary = {
        'official_result': {
            'decision': decision,
            'symbol': symbol,
            'reason': reason,
            'risk_flags': list(risk_flags or []),
            'why_selected': list((official_card or {}).get('candidate_reasons') or []),
            'why_not_selected': _reason_parts(reason) if decision != 'PAPER_PICK' else [],
            'single_target_card_status': (official_card or {}).get('target_status'),
        },
        'source_consumption_summary': build_source_consumption_summary(bundle),
        'top10_candidates': top10_cards,
        'top10_candidate_limit': 10,
        'top10_candidates_total': len(candidates),
        'ranked_selection_candidates_total': len(evaluations),
        'ranked_selection_candidates_reason': evaluation_reason,
        'formal_diagnostic_candidate': highest_card,
        'formal_diagnostic_candidate_reason': highest_reason,
        'closest_to_pick_candidate': closest_card,
    }
    if decision == 'NO_PICK':
        watch = build_daily_best_paper_watch({
            'formal_diagnostic_candidate': highest_card,
            'closest_to_pick_candidate': closest_card,
            'ranked_no_pick_candidates': top10_cards,
            'selection_basis': [
                'hard_block_count asc',
                'candidate_evidence_status PASS first',
                'blocker_count asc',
                'risk_adjusted_score desc',
                'rank asc',
            ],
        })
        summary['daily_best_paper_watch'] = watch
        if isinstance(watch, dict):
            watch_symbol = symbol_for(watch)
            for card in top10_cards:
                if card.get('symbol') == watch_symbol:
                    if decision == 'NO_PICK':
                        card['selection_outcome'] = 'DAILY_BEST_PAPER_WATCH'
                    card['selection_outcome_reason'] = str(watch.get('explanation') or card.get('selection_outcome_reason') or '')
                    break
    return summary

def build_rank_alignment_diagnostic(
    rows: List[Dict[str, Any]],
    first_clean_row: Dict[str, Any] | None = None,
    *,
    top_n: int = 10,
) -> Dict[str, Any]:
    """P0: explain pool_rank (scanner) vs formal_rank vs first_clean divergence."""
    usable = [row for row in (rows or []) if isinstance(row, dict) and symbol_for(row)]
    by_pool = sorted(
        usable,
        key=lambda row: (
            safe_int(row.get('pool_rank')) if safe_int(row.get('pool_rank')) is not None else 999999,
            -(safe_float(row.get('structured_priority_score')) or 0.0),
        ),
    )
    by_formal = sorted(
        usable,
        key=lambda row: (
            safe_int(row.get('formal_rank')) if safe_int(row.get('formal_rank')) is not None else 999999,
            formal_candidate_sort_key(row),
        ),
    )
    # formal_rank ascending is best-first; when missing, formal_candidate_sort_key desc.
    if not any(safe_int(row.get('formal_rank')) is not None for row in usable):
        by_formal = sorted(usable, key=formal_candidate_sort_key, reverse=True)

    def _compact(row: Dict[str, Any], idx: int | None = None) -> Dict[str, Any]:
        adj = ranking_basis_adjustment_components(row)
        return {
            'symbol': symbol_for(row),
            'name': row.get('name') or row.get('stock_name'),
            'pool_rank': safe_int(row.get('pool_rank')),
            'formal_rank': safe_int(row.get('formal_rank')) if safe_int(row.get('formal_rank')) is not None else idx,
            'scanner_rank': safe_int(row.get('scanner_rank')),
            'rank': safe_int(row.get('rank')),
            'rank_source': row.get('rank_source') or '',
            'search_layer': row.get('search_layer') or row.get('search_layer_hint') or '',
            'structured_priority_score': safe_float(row.get('structured_priority_score')),
            'formal_primary_score': safe_float(row.get('formal_primary_score')) or round(float(formal_candidate_sort_key(row)[0]), 4),
            'profit_edge_score': adj.get('profit_edge_score'),
            'capital_behavior_score': adj.get('capital_behavior_score'),
            'catalyst_type': adj.get('catalyst_type'),
            'sector_attack_score': adj.get('sector_attack_score'),
            'stock_role': adj.get('stock_role'),
            'flow_confirmation_score': adj.get('flow_confirmation_score'),
            't1_room_score': adj.get('t1_room_score'),
            'distribution_risk_score': adj.get('distribution_risk_score'),
            'counter_evidence': adj.get('counter_evidence') or [],
            'profit_continuation_soft': (adj.get('boosts') or {}).get('profit_continuation_soft'),
            'hot_fund_shell': (adj.get('penalties') or {}).get('hot_fund_shell_without_profit_edge'),
            'defensive_shell_penalty': (adj.get('penalties') or {}).get('defensive_pe0_hot_fund_shell'),
        }

    pool_top = [_compact(row, i) for i, row in enumerate(by_pool[:top_n], 1)]
    formal_top = [_compact(row, i) for i, row in enumerate(by_formal[:top_n], 1)]
    pool_top_syms = {item['symbol'] for item in pool_top if item.get('symbol')}
    formal_top_syms = {item['symbol'] for item in formal_top if item.get('symbol')}
    overlap = sorted(pool_top_syms & formal_top_syms)
    formal_only = [_compact(row) for row in by_formal[:top_n] if symbol_for(row) not in pool_top_syms]
    pool_only = [_compact(row) for row in by_pool[:top_n] if symbol_for(row) not in formal_top_syms]
    first_clean_diag = None
    if isinstance(first_clean_row, dict) and symbol_for(first_clean_row):
        first_clean_diag = _compact(first_clean_row)
        first_clean_diag['challenged_from'] = first_clean_row.get('first_clean_challenged_from')
        first_clean_diag['challenge_reason'] = first_clean_row.get('first_clean_challenge_reason')
    return {
        'rank_source': 'formal_profit_first',
        'pool_rank_basis': 'scanner_structured_priority',
        'formal_rank_basis': 'formal_candidate_sort_key',
        'candidate_count': len(usable),
        'top_n': top_n,
        'pool_formal_top_overlap_count': len(overlap),
        'pool_formal_top_overlap_symbols': overlap,
        'pool_top': pool_top,
        'formal_top': formal_top,
        'formal_top_not_in_pool_top': formal_only,
        'pool_top_not_in_formal_top': pool_only,
        'first_clean': first_clean_diag,
        'divergence_note': (
            'pool_rank is scanner structured_priority; daily_candidates.rank is formal_profit_first; '
            'FULL_POOL/TOP10 outcomes follow formal rank after P1 alignment.'
        ),
    }


for _name, _value in tuple(globals().items()):
    if (
        callable(_value)
        and getattr(_value, '__module__', None) == __name__
        and _name not in {'bind_host', '_inject_host', '_with_host'}
    ):
        globals()[_name] = _with_host(_value)
