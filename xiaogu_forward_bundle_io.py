#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle load/persist helpers extracted from the forward runner.

Call bind_host(runner_module) after shared helpers exist. Each public call
re-injects host symbols (monkeypatch-safe). Production entry remains
xiaogu_forward_runner.py (re-exports).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import datetime as dt
import glob
import hashlib
import json
import os

from xiaogu_forward_host_binding import create_host_binding
from xiaogu_utils import eastmoney_quote_prices


_HOST = None

REQUIRED_FROM_HOST = (
    'BASE',
    'CANDIDATE_BUNDLE_ROOT',
    'LIVE_SCAN_ROOT',
    'RAW_ROOT',
    'PRODUCTION_CHAIN_MODE',
    'PRODUCTION_RANKING_VIEW',
    'PRODUCTION_RANK_SOURCE',
    'PRODUCTION_SCORE_SOURCE',
    'RULE_VERSION',
    'SCAN_SUMMARY_NAME',
    'SCAN_SUMMARY_RUNNER_NAME',
    'SCORING_CONFIG_DEFAULTS',
    '_ensure_current_realtime_bundle_path',
    '_json_safe_value',
    '_unique_persistence_candidates',
    'active_chain_governance_flags',
    'apply_formal_profit_ranks',
    'attach_scan_summary_information_coverage_audit',
    'basket_candidate',
    'build_daily_ticket_search_rows',
    'build_rank_alignment_diagnostic',
    'build_research_basket_from_latest_scan',
    'build_weak_market_shadow_ticket',
    'candidate_capital_risk_profile',
    'filter_current_day_tradable_candidates',
    'filter_t1_profit_candidates',
    'is_active_api_source',
    'latest_completed_trading_day',
    'limitup_probability_proxy_components',
    'load_jsonl',
    'normalize_bundle_vei_tags',
    'normalize_market_regime_for_db',
    'normalize_vei_phase_d_tags',
    'now_iso',
    'paper_pick_risk_explanation_gate',
    'ranking_basis_adjustment_components',
    'read_json',
    'safe_float',
    'scan_age_minutes',
    'scan_summary_sector_catalyst_diagnostics',
    'shadow_risk_profile',
    'social_confirmation_profile',
    'structured_formal_impact_summary',
    'symbol_for',
    't1_profit_candidate_profile',
    'unique_text_values',
    'validate_active_production_chain',
    'validate_formal_rank_snapshot',
)

bind_host, _inject_host, _with_host = create_host_binding(
    globals(),
    REQUIRED_FROM_HOST,
    (
        'LIVE_SCAN_ROOT', 'CANDIDATE_BUNDLE_ROOT', 'RAW_ROOT', 'BASE',
        'SCAN_SUMMARY_NAME', 'SCAN_SUMMARY_RUNNER_NAME', 'RULE_VERSION',
        'SCORING_CONFIG_DEFAULTS', 'ALLOWED_A_SHARE_SOURCE_TOKENS',
    ),
    preserve_existing_on_missing=True,
)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')

def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_obj = _json_safe_value(obj)
    path.write_text(json.dumps(safe_obj, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

def scan_summary_paths(date: str) -> List[Path]:
    matches = glob.glob(str(LIVE_SCAN_ROOT / date / '**' / SCAN_SUMMARY_NAME), recursive=True)
    summaries = []
    for path in sorted((Path(match) for match in matches), key=os.path.getmtime, reverse=True):
        try:
            summary = read_json(path)
        except Exception:
            continue
        source = summary.get('pipeline_version') or summary.get('source')
        if is_active_api_source(source):
            summaries.append(path)
    return summaries

def summary_bundle_rows(summary: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    rows = summary.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

def summary_file_rows(summary_path: Path, summary: Dict[str, Any], file_key: str) -> List[Dict[str, Any]]:
    files = summary.get('files') if isinstance(summary.get('files'), dict) else {}
    raw_path = str(files.get(file_key) or '').strip()
    if not raw_path:
        return []
    candidates = [Path(raw_path)]
    for marker in (
        '/workspace/hermes-workspaces/xiaogu/',
        '/root/hermes/company-ai-system/workspaces/xiaogu/',
    ):
        if marker in raw_path:
            candidates.append(BASE / raw_path.split(marker, 1)[1])
    if not Path(raw_path).is_absolute():
        candidates.append(summary_path.parent / raw_path)
    for path in candidates:
        try:
            if path.exists():
                return [row for row in load_jsonl(path) if isinstance(row, dict)]
        except Exception:
            continue
    return []

def load_candidate_bundle(date: str, asof_time: str | None = None) -> Dict[str, Any]:
    latest_scan = load_latest_eastmoney_scan(date, asof_time)
    if latest_scan is not None:
        return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
    return {'available': False, 'reason': 'PRODUCTION_SCAN_REQUIRED', 'date': date}

def _bundle_from_scan_summary(summary_path: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    source_time = str(summary.get('source_time', ''))
    data_gate_status = 'PASS' if source_time.startswith(summary_path.parent.parent.name) else 'PARTIAL_OR_FAIL'
    stock_rows = summary_file_rows(summary_path, summary, 'stock_all_a')
    quote_truth_by_symbol: Dict[str, Dict[str, Any]] = {}
    for stock in stock_rows:
        symbol = str(stock.get('f12') or stock.get('symbol') or stock.get('code') or '').zfill(6)
        if not symbol:
            continue
        quote = eastmoney_quote_prices(stock)
        if quote.get('close') and quote.get('high') and quote.get('low') and quote.get('open'):
            quote_truth_by_symbol[symbol] = quote

    def apply_quote_truth(row: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(row.get('symbol') or row.get('code') or '').zfill(6)
        quote = quote_truth_by_symbol.get(symbol)
        if not quote:
            return row
        merged = dict(row)
        merged.update({
            'price': quote['close'],
            'close': quote['close'],
            'open': quote['open'],
            'high': quote['high'],
            'low': quote['low'],
            'prev_close': quote['prev_close'],
            'quote_truth_source': 'stock_all_a',
            'quote_truth_fields': 'f2/f15/f16/f17_or_f43/f44/f45/f46',
        })
        high = float(quote['high'])
        low = float(quote['low'])
        close = float(quote['close'])
        merged['close_position_score'] = round((close - low) / (high - low), 6) if high > low else 0.5
        return merged

    full_pool_rows = summary_bundle_rows(summary, 'full_candidate_pool')
    scored_rows = summary_bundle_rows(summary, 'scored_candidates')
    if not full_pool_rows:
        full_pool_rows = scored_rows[:200]
    structured_scores = summary_bundle_rows(summary, 'structured_scores')
    if not scored_rows:
        return {'available': False, 'reason': 'NO_SCORED_ROWS_FOR_SAME_DAY_SCAN', 'summary_path': str(summary_path)}
    structured_scores_by_symbol = {symbol_for(row): row for row in structured_scores if symbol_for(row)}
    broken_limitup_rows = (
        summary_bundle_rows(summary, 'limitup_broken')
        or summary_file_rows(summary_path, summary, 'limitup_broken')
    )
    broken_limitup_by_symbol = {}
    for broken_row in broken_limitup_rows:
        if not isinstance(broken_row, dict):
            continue
        broken_symbol = str(
            broken_row.get('c')
            or broken_row.get('f12')
            or broken_row.get('symbol')
            or broken_row.get('code')
            or ''
        ).strip().zfill(6)
        if broken_symbol:
            broken_limitup_by_symbol[broken_symbol] = broken_row

    def with_structured_score(row: Dict[str, Any]) -> Dict[str, Any]:
        structured = structured_scores_by_symbol.get(symbol_for(row))
        if not structured:
            return row
        structured_details = structured.get('component_details') or {}
        merged = dict(row)

        def first_defined(*values):
            for value in values:
                if value is not None:
                    return value
            return None

        merged['structured_score'] = first_defined(structured.get('structured_score'), row.get('structured_score'))
        merged['structured_score_components'] = first_defined(structured.get('components'), row.get('structured_score_components'))
        merged['structured_component_details'] = first_defined(structured.get('component_details'), row.get('structured_component_details'))
        merged['vei_phase_d_tags'] = normalize_vei_phase_d_tags(first_defined(structured.get('vei_phase_d_tags'), row.get('vei_phase_d_tags')))
        merged['candidate_stage'] = first_defined(structured.get('candidate_stage'), structured_details.get('candidate_stage'), row.get('candidate_stage'))
        merged['early_opportunity_score'] = first_defined(structured.get('early_opportunity_score'), structured_details.get('early_opportunity_score'), row.get('early_opportunity_score'))
        merged['limitup_capture_score'] = first_defined(structured.get('limitup_capture_score'), structured_details.get('limitup_capture_score'), row.get('limitup_capture_score'))
        merged['limitup_capture_profile'] = first_defined(structured.get('limitup_capture_profile'), structured_details.get('limitup_capture_profile'), row.get('limitup_capture_profile'))
        merged['limitup_capture_confirmed'] = first_defined(structured.get('limitup_capture_confirmed'), structured_details.get('limitup_capture_confirmed'), row.get('limitup_capture_confirmed'))
        merged['limitup_capture_reasons'] = first_defined(structured.get('limitup_capture_reasons'), structured_details.get('limitup_capture_reasons'), row.get('limitup_capture_reasons'))
        merged['structured_score_mode'] = first_defined(structured.get('mode'), row.get('structured_score_mode'))
        merged['search_layer_hint'] = first_defined(structured_details.get('search_layer_hint'), row.get('search_layer_hint'))
        merged['news_catalyst_strength'] = first_defined(structured_details.get('news_catalyst_strength'), row.get('news_catalyst_strength'))
        merged['sector_catalyst_score'] = first_defined(structured_details.get('sector_catalyst_score'), row.get('sector_catalyst_score'))
        merged['topic_propagation_score'] = first_defined(structured_details.get('topic_propagation_score'), row.get('topic_propagation_score'))
        merged['intraday_alert_strength'] = first_defined(structured_details.get('intraday_alert_strength'), row.get('intraday_alert_strength'))
        merged['limitup_reason_propagation_score'] = first_defined(structured_details.get('limitup_reason_propagation_score'), row.get('limitup_reason_propagation_score'))
        merged['low_position_catalyst_score'] = first_defined(structured_details.get('low_position_catalyst_score'), row.get('low_position_catalyst_score'))
        merged['main_theme_alignment_score'] = first_defined(structured.get('main_theme_alignment_score'), structured_details.get('main_theme_alignment_score'), row.get('main_theme_alignment_score'))
        merged['main_theme_core_score'] = first_defined(structured.get('main_theme_core_score'), structured_details.get('main_theme_core_score'), row.get('main_theme_core_score'))
        merged['hsgt_institutional_flow'] = first_defined(structured.get('hsgt_institutional_flow'), structured_details.get('hsgt_institutional_flow'), row.get('hsgt_institutional_flow'))
        merged['experimental_catalyst_signal'] = first_defined(structured.get('experimental_catalyst_signal'), structured_details.get('experimental_catalyst_signal'), row.get('experimental_catalyst_signal'))
        merged['research_signals'] = first_defined(structured.get('research_signals'), row.get('research_signals'))
        research_signals = merged.get('research_signals') if isinstance(merged.get('research_signals'), dict) else {}
        merged['research_panel_overall'] = research_signals.get('research_panel', {}).get('overall') if isinstance(research_signals.get('research_panel'), dict) else row.get('research_panel_overall')
        merged['catalyst_quality_category'] = research_signals.get('catalyst_quality', {}).get('category') if isinstance(research_signals.get('catalyst_quality'), dict) else row.get('catalyst_quality_category')
        merged['a_share_risk_review_disqualified_for_paper_pick'] = bool((research_signals.get('a_share_risk_review') or {}).get('disqualified_for_paper_pick')) if isinstance(research_signals, dict) else bool(row.get('a_share_risk_review_disqualified_for_paper_pick'))
        catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
        if catalyst_quality.get('regulatory_hard_block') or catalyst_quality.get('category') in ('risk_notice', 'regulatory_notice'):
            merged['regulatory_hard_block'] = str(catalyst_quality.get('category') or 'regulatory_notice')
        elif merged['a_share_risk_review_disqualified_for_paper_pick']:
            merged['regulatory_hard_block'] = 'a_share_risk_review_disqualified'
        merged['historical_pattern_name'] = research_signals.get('historical_pattern', {}).get('pattern_name') if isinstance(research_signals.get('historical_pattern'), dict) else row.get('historical_pattern_name')
        market_snapshot = summary.get('market_snapshot') or {}
        merged['market_regime'] = first_defined(summary.get('market_regime'), market_snapshot.get('market_regime'), row.get('market_regime'), structured.get('market_regime'))
        merged['market_breadth_up_pct'] = first_defined(summary.get('market_breadth_up_pct'), market_snapshot.get('market_breadth_up_pct'), row.get('market_breadth_up_pct'), structured.get('market_breadth_up_pct'))
        merged['market_limitups'] = first_defined(summary.get('market_limitups'), market_snapshot.get('market_limitups'), row.get('market_limitups'), structured.get('market_limitups'))
        merged['market_bigups'] = first_defined(summary.get('market_bigups'), market_snapshot.get('market_bigups'), row.get('market_bigups'), structured.get('market_bigups'))
        merged['market_follow_through_score'] = first_defined(summary.get('market_follow_through_score'), market_snapshot.get('market_follow_through_score'), row.get('market_follow_through_score'), structured.get('market_follow_through_score'))
        merged['limitup_broken_ratio'] = first_defined(summary.get('limitup_broken_ratio'), market_snapshot.get('limitup_broken_ratio'), row.get('limitup_broken_ratio'), structured.get('limitup_broken_ratio'))
        merged['broken_limitups'] = first_defined(summary.get('broken_limitups'), market_snapshot.get('broken_limitups'), row.get('broken_limitups'), structured.get('broken_limitups'))
        return merged

    def with_broken_limitup_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
        symbol = symbol_for(row)
        broken_row = broken_limitup_by_symbol.get(symbol)
        if not broken_row:
            return row
        merged = dict(row)
        merged['broken_limitup'] = True
        merged['failed_limitup'] = True
        merged['broken_limitup_evidence'] = {
            'eligible': True,
            'status': 'PASS',
            'source': 'limitup_broken',
            'symbol': symbol,
            'record': broken_row,
        }
        return merged

    enriched_rows = [
        apply_quote_truth(with_broken_limitup_evidence(with_structured_score(r)))
        for r in scored_rows
    ]
    enriched_full_pool = [
        apply_quote_truth(with_broken_limitup_evidence(with_structured_score(r)))
        for r in full_pool_rows
    ]
    full_pool_tradable, full_pool_tradable_filter = filter_current_day_tradable_candidates(
        enriched_full_pool,
        {'date': source_time[:10], 'source_time': source_time},
    )
    full_pool_tradable_symbols = {
        symbol_for(row) for row in full_pool_tradable if symbol_for(row)
    }
    t1_profile_by_symbol = {}
    for row in enriched_full_pool:
        symbol = symbol_for(row)
        if not symbol:
            continue
        profile = t1_profit_candidate_profile(
            row,
            {'date': source_time[:10], 'source_time': source_time},
        )
        t1_profile_by_symbol[symbol] = (
            bool(profile.get('eligible')),
            dict(profile),
            '' if symbol in full_pool_tradable_symbols else 'CURRENT_DAY_TRADABLE_FILTER',
        )
    annotated_full_pool = []
    for row in enriched_full_pool:
        annotated = dict(row)
        admission = t1_profile_by_symbol.get(symbol_for(annotated))
        if admission is not None:
            eligible, profile, reason = admission
            annotated['t1_profit_candidate'] = eligible
            annotated['t1_profit_profile'] = profile
            annotated['expected_t1_profit_score'] = profile.get('expected_t1_profit_score')
            annotated['t1_profit_admission_reason'] = reason or str(profile.get('reason') or '')
        annotated_full_pool.append(annotated)
    enriched_full_pool = annotated_full_pool
    try:
        from xiaogu_social_sentiment import attach_social_features
        attach_social_features(enriched_rows, source_time[:10])
        attach_social_features(enriched_full_pool, source_time[:10])
    except Exception:
        # The social sidecar remains optional and cannot block runner intake.
        pass
    bundle_context = {
        'date': source_time[:10],
        'source_market_date': source_time[:10] if len(source_time) >= 10 else '',
        'source_time': source_time,
        '_runner_asof_time': source_time[11:] if len(source_time) >= 19 else '',
        'candidate_source': summary.get('source') or 'eastmoney_api_scan_v2',
        'producer_version': summary.get('producer_version') or summary.get('pipeline_version') or '',
        'pipeline_version': summary.get('pipeline_version') or '',
        'ranking_view': PRODUCTION_RANKING_VIEW,
        'rank_source': PRODUCTION_RANK_SOURCE,
        'score_source': PRODUCTION_SCORE_SOURCE,
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        # T+1 profit evidence is a T-day feature group only. It must not
        # pre-filter the production universe before the sole formal sorter.
        't1_profit_gate_enabled': False,
        't1_profit_gate_policy': 'DIAGNOSTIC_FEATURE_ONLY',
        'production_eligibility_mode': 'HARD_GATES_ONLY',
        'data_gate_status': data_gate_status,
        'source_status': summary.get('source_status', {}),
        'full_universe_scan': summary.get('full_universe_scan', {}),
        'market_regime': summary.get('market_regime') or (summary.get('market_snapshot') or {}).get('market_regime') or '',
        'market_snapshot': {
            'universe_quote_count': summary.get('universe_quote_count'),
            'full_universe_scan': summary.get('full_universe_scan', {}),
            'market_breadth_up_pct': summary.get('market_breadth_up_pct'),
            'market_limitups': summary.get('market_limitups'),
            'market_bigups': summary.get('market_bigups'),
            'passed_count': summary.get('passed_count'),
            'scored_count': summary.get('scored_count'),
            'blocked_reasons': summary.get('blocked_reasons'),
            'market_follow_through_score': summary.get('market_follow_through_score'),
            'limitup_broken_ratio': summary.get('limitup_broken_ratio'),
            'broken_limitups': (summary.get('market_snapshot') or {}).get('broken_limitups'),
            'limitup_broken_evidence_count': len(broken_limitup_by_symbol),
            'max_consecutive': summary.get('max_consecutive'),
            'sentiment_score': summary.get('sentiment_score'),
            'market_main_inflow': summary.get('market_main_inflow'),
            'market_regime': summary.get('market_regime') or (summary.get('market_snapshot') or {}).get('market_regime'),
            'external_market': summary.get('external_market') or (summary.get('market_snapshot') or {}).get('external_market') or {},
            'sector_snapshot': summary.get('sector_snapshot') or (summary.get('market_snapshot') or {}).get('sector_snapshot') or [],
            'source_status': summary.get('source_status', {}),
            'hard_block_source_status': summary.get('hard_block_source_status', {}),
            'scanner_sources': summary.get('scanner_sources', []),
            'scanner_transport': summary.get('scanner_transport') or 'direct_api',
        },
    }
    search_context = build_daily_ticket_search_rows(enriched_rows, bundle_context)
    search_rows = search_context['search_rows']
    first_clean_row = search_context['first_clean_row']
    paper_pick_candidate_stage_distribution = search_context['paper_pick_candidate_stage_distribution']
    candidate_stage_blocker_distribution = search_context['candidate_stage_blocker_distribution']
    daily_ticket_search_result = search_context['daily_ticket_search_result']
    blocked_candidate_diagnostics = search_context.get('blocked_candidate_diagnostics', [])
    # P1: full pool rank = formal profit-first (preserve scanner as pool_rank).
    formal_ranked_full_pool = apply_formal_profit_ranks(enriched_full_pool)
    # Prefer search-path formal ranks for scored rows when present.
    formal_ranked_scored = search_context.get('formal_ranked_pool') or apply_formal_profit_ranks(enriched_rows)
    rank_alignment_diagnostic = search_context.get('rank_alignment_diagnostic') or build_rank_alignment_diagnostic(
        formal_ranked_full_pool or formal_ranked_scored,
        first_clean_row,
    )
    # A bundle never creates a second research-ticket path. It either
    # contains formal candidates for the runner or produces the single
    # official NO_PICK outcome.
    decision_class = 'PAPER_PICK' if search_rows else 'NO_PICK'
    # The legacy search layers are diagnostics only. Production must receive
    # the complete formal pool so PATH/setup/theme admission cannot become a
    # hidden whitelist before the sole T+1 decision owner runs.
    selected = [basket_candidate(r, decision_class) for r in formal_ranked_full_pool]
    structured_formal_impact = structured_formal_impact_summary(formal_ranked_scored, selected, bundle_context)
    weak_market_shadow_ticket = build_weak_market_shadow_ticket(
        selected,
        {
            **bundle_context,
            'market_snapshot': {
                **bundle_context['market_snapshot'],
                'market_breadth_up_pct': summary.get('market_breadth_up_pct'),
            },
        },
        summary_path.parent.parent.name,
    )
    bundle = {
        'date': summary_path.parent.parent.name,
        'asof_time': source_time[11:] if len(source_time) >= 19 else '',
        'generated_at': now_iso(),
        'source_market_date': source_time[:10] if len(source_time) >= 10 else '',
        'source_time': source_time,
        '_runner_asof_time': source_time[11:] if len(source_time) >= 19 else '',
        'candidate_source': summary.get('source') or 'eastmoney_api_scan_v2',
        'producer_version': summary.get('producer_version') or summary.get('pipeline_version') or '',
        'pipeline_version': summary.get('pipeline_version') or '',
        'ranking_view': PRODUCTION_RANKING_VIEW,
        'rank_source': PRODUCTION_RANK_SOURCE,
        'score_source': PRODUCTION_SCORE_SOURCE,
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        # T+1 profit evidence is a T-day feature group only. It must not
        # pre-filter the production universe before the sole formal sorter.
        't1_profit_gate_enabled': False,
        't1_profit_gate_policy': 'DIAGNOSTIC_FEATURE_ONLY',
        'production_eligibility_mode': 'HARD_GATES_ONLY',
        'data_gate_status': data_gate_status,
        'full_candidate_pool': formal_ranked_full_pool,
        'scored_candidates': formal_ranked_scored,
        'passed_candidates': selected,
        'decision_candidates': selected,
        'candidate_pool_exclusion_summary': dict(summary.get('pool_exclusion_summary') or {}),
        'current_day_tradable_filter': {
            'scored_candidates': search_context.get('current_day_tradable_filter') or {},
            'full_candidate_pool': full_pool_tradable_filter,
        },
        'xiaochan_gate_status': 'ALLOW_FORWARD_PAPER_NO_TRADE',
        'paper_scoring_candidates': selected,
        'formal_ranked_pool': formal_ranked_full_pool,
        'formal_rank_snapshot_id': (
            formal_ranked_full_pool[0].get('formal_rank_snapshot_id')
            if formal_ranked_full_pool else ''
        ),
        'formal_rank_snapshot_version': (
            formal_ranked_full_pool[0].get('formal_rank_snapshot_version')
            if formal_ranked_full_pool else ''
        ),
        'candidate': first_clean_row if first_clean_row is not None else {},
        'first_search_candidate_diagnostic': selected[0] if selected else {},
        'blocked_candidate_diagnostics': blocked_candidate_diagnostics,
        'official_target_excluded_count': search_context.get('official_target_excluded_count', 0),
        'first_excluded_candidate': search_context.get('first_excluded_candidate'),
        'rank_alignment_diagnostic': rank_alignment_diagnostic,
        'first_clean_challenge_meta': search_context.get('first_clean_challenge_meta') or {},
        'paper_pick_candidate_stage_distribution': dict(paper_pick_candidate_stage_distribution),
        'candidate_stage_blocker_distribution': {stage: dict(counts) for stage, counts in candidate_stage_blocker_distribution.items()},
        'daily_ticket_search_result': daily_ticket_search_result,
        'weak_market_shadow_ticket': weak_market_shadow_ticket,
        'sector_catalyst_diagnostics': scan_summary_sector_catalyst_diagnostics(summary),
        'structured_observation_basket': structured_formal_impact['structured_observation_candidates'],
        'structured_sector_observation_basket': structured_formal_impact['sector_opportunity_candidates'],
        'structured_formal_impact': structured_formal_impact,
        'market_regime': bundle_context.get('market_regime') or '',
        'market_snapshot': {
            **bundle_context['market_snapshot'],
        },
        'structured_scores': structured_scores,
        'research_signals': summary_bundle_rows(summary, 'research_signals'),
        'structured_score_components': summary_bundle_rows(summary, 'structured_score_components'),
        'structured_component_details': summary_bundle_rows(summary, 'structured_component_details'),
        'mainboard_policy': summary.get('mainboard_policy') or (summary.get('full_universe_scan') or {}).get('candidate_board_policy') or 'main_only',
        'hsgt_diagnostics': summary.get('hsgt_diagnostics', {}),
        'domain_timings': summary.get('domain_timings', {}),
        'scanner_elapsed_seconds': summary.get('scanner_elapsed_seconds'),
        'data_directory_catalog': summary.get('data_directory_catalog', {}),
        'data_directory_catalog_records': summary_bundle_rows(summary, 'data_directory_catalog_records'),
        'data_directory_catalog_records_path': '',
        'data_directory_content': summary.get('data_directory_content', {}),
        'data_directory_content_records': summary_bundle_rows(summary, 'data_directory_content_records'),
        'data_directory_content_records_path': '',
        'source_status': summary.get('source_status', {}),
        'full_universe_scan': summary.get('full_universe_scan', {}),
        'scanner_sources': summary.get('scanner_sources', []),
        'scanner_transport': summary.get('scanner_transport') or 'direct_api',
        'risk_flags': [] if search_rows else ['NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS'],
        'decision_reason': 'SAME_DAY_SCAN_PASSED_CANDIDATE' if search_rows else 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS',
        'production_chain_mode': PRODUCTION_CHAIN_MODE,
        'production_snapshot_origin': 'scan_formal_snapshot',
        'source_evidence': {
            'summary_path': str(summary_path),
            'scan_files': summary.get('files', {}),
            'scanner_transport': summary.get('scanner_transport') or 'direct_api',
        },
        'scan_summary_path': str(summary_path),
        'scan_summary_source_time': source_time,
        '_bundle_path': str(summary_path),
        'available': True,
    }
    attach_scan_summary_information_coverage_audit(bundle, summary_path, summary)
    bundle['sector_catalyst_diagnostics'] = scan_summary_sector_catalyst_diagnostics(summary)
    normalize_bundle_vei_tags(bundle)
    return bundle

def load_latest_eastmoney_scan(
    date: str,
    asof_time: str | None = None,
    *,
    historical_replay: bool = False,
    historical_summary_path: Path | None = None,
) -> Tuple[Path, Dict[str, Any]] | None:
    # Directory labels are scheduling metadata only. Select the latest valid
    # same-day direct-API snapshot by its recorded source_time. Historical
    # replay also accepts the prior API scanner's scrapy_api transport label;
    # live production remains direct_api-only. A historical caller may pin
    # one immutable summary instead of re-selecting among same-day retries.
    if historical_summary_path is not None:
        if not historical_replay:
            return None
        summary_paths = [Path(historical_summary_path)]
    else:
        summary_paths = sorted(
            (
                Path(path)
                for path in glob.glob(
                    str(LIVE_SCAN_ROOT / date / '**' / SCAN_SUMMARY_RUNNER_NAME),
                    recursive=True,
                )
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    candidates: List[Tuple[dt.datetime, Path, Dict[str, Any]]] = []
    for runner_summary in summary_paths:
        try:
            summary = read_json(runner_summary)
        except Exception:
            continue
        source_time = str(summary.get('source_time', ''))
        if source_time[:10] != date:
            continue
        if not is_active_api_source(summary.get('pipeline_version') or summary.get('source')):
            continue
        transport = str(summary.get('scanner_transport') or '')
        if transport != 'direct_api' and not (
            historical_replay and transport == 'scrapy_api'
        ):
            continue
        try:
            source_dt = dt.datetime.fromisoformat(source_time.replace('Z', '+00:00')).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if asof_time:
            age_minutes = scan_age_minutes(source_time, date, asof_time)
            if age_minutes is None or age_minutes > 0:
                continue
        candidates.append((source_dt, runner_summary, summary))

    if not candidates:
        return None
    source_dt, runner_summary, summary = max(candidates, key=lambda item: item[0])
    return runner_summary, summary

def scan_date_for_runtime(target_date: str) -> str:
    try:
        requested = dt.date.fromisoformat(target_date)
    except Exception:
        requested = latest_completed_trading_day()
    today = latest_completed_trading_day()
    return min(requested, today).isoformat()

def build_daily_candidate_persistence_payloads(date: str, bundle: Dict[str, Any], features: Dict[str, Any], decision: str, reason: str) -> Dict[str, Any]:
    import datetime as _dt
    from xiaogu_db import classify_candidate_cohort, limitup_gene_signal_values

    strict_production = bundle.get('strict_production_chain') is True
    formal_snapshot = bundle.get('formal_ranked_pool')
    formal_snapshot_is_explicit = isinstance(formal_snapshot, list) and bool(formal_snapshot)
    full_candidate_pool = bundle.get('full_candidate_pool')
    full_pool_is_explicit = isinstance(full_candidate_pool, list) and bool(full_candidate_pool)
    source_candidates = [
        candidate
        for candidate in (
            formal_snapshot
            if formal_snapshot_is_explicit
            else (full_candidate_pool or bundle.get('paper_scoring_candidates') or [])
        )
        if isinstance(candidate, dict)
    ]
    if strict_production:
        validation = validate_active_production_chain(bundle, date)
        if not validation.get('valid'):
            return {
                'status': 'PRODUCTION_CHAIN_INVALID',
                'error': validation,
                'scan_session': None,
                'daily_candidates': [],
                'limitup_gene_signals': [],
            }
        source_candidates = [
            candidate for candidate in formal_snapshot
            if isinstance(candidate, dict)
        ]
        current_day_tradable_filter = dict(bundle.get('current_day_tradable_filter') or {})
    elif formal_snapshot_is_explicit:
        validation = validate_formal_rank_snapshot(source_candidates)
        if not validation.get('valid'):
            return {
                'status': 'FORMAL_RANK_SNAPSHOT_INVALID',
                'error': validation,
                'scan_session': None,
                'daily_candidates': [],
                'limitup_gene_signals': [],
            }
        current_day_tradable_filter = dict(bundle.get('current_day_tradable_filter') or {})
    else:
        source_candidates, current_day_tradable_filter = filter_t1_profit_candidates(
            source_candidates,
            bundle,
            enforce=bool(bundle.get('t1_profit_gate_enabled')),
        )
        # Research/replay callers may rebuild an observation snapshot.
        # Strict production bundles are rejected above and never enter here.
        source_candidates = apply_formal_profit_ranks(source_candidates)
    if not source_candidates:
        return {'status': 'NO_CANDIDATES', 'scan_session': None, 'daily_candidates': [], 'limitup_gene_signals': []}

    trade_date = _dt.date.fromisoformat(date)
    pool_exclusion_summary = dict(bundle.get('candidate_pool_exclusion_summary') or bundle.get('pool_exclusion_summary') or {})
    pool_exclusion_summary['current_day_tradable_filter'] = current_day_tradable_filter
    target_count = int(pool_exclusion_summary.get('target_count') or 200)
    with_candidates = sorted(
        source_candidates,
        key=lambda item: (
            safe_float(item.get('rank')) if safe_float(item.get('rank')) is not None else 999999.0,
            -(safe_float(item.get('final_score')) if item.get('final_score') is not None else safe_float(item.get('score')) or -1e9),
            item.get('symbol') or item.get('code') or '',
        ),
    )
    candidates, dedup_summary = _unique_persistence_candidates(with_candidates, target_count)
    if not candidates:
        return {'status': 'NO_CANDIDATES', 'scan_session': None, 'daily_candidates': [], 'limitup_gene_signals': []}
    # Upstream select/cut may already record pre-cut universe size (source_row_count).
    # Do not let persistence-local dedup overwrite that diagnostic when present.
    existing_source_row_count = pool_exclusion_summary.get('source_row_count')
    if dedup_summary['deduplication_applied'] or not pool_exclusion_summary.get('deduplication_applied'):
        pool_exclusion_summary.update(dedup_summary)
    else:
        for key, value in dedup_summary.items():
            pool_exclusion_summary.setdefault(key, value)
        pool_exclusion_summary['final_persisted_count'] = len(candidates)
    if existing_source_row_count is not None:
        pool_exclusion_summary['source_row_count'] = existing_source_row_count
    pool_exclusion_summary.setdefault('target_count', target_count)
    pool_exclusion_summary.setdefault('legacy_partial_pool', not bool(full_candidate_pool))
    pool_exclusion_summary.setdefault('source_status', 'LEGACY' if not full_candidate_pool else 'PASS')
    pool_exclusion_summary['formal_rank_snapshot_id'] = (
        source_candidates[0].get('formal_rank_snapshot_id') if source_candidates else ''
    )
    pool_exclusion_summary['formal_rank_snapshot_version'] = (
        source_candidates[0].get('formal_rank_snapshot_version')
        if source_candidates else ''
    )
    drop_diagnostics = [
        item for item in (bundle.get('candidate_drop_diagnostics') or [])
        if isinstance(item, dict)
    ]
    pool_exclusion_summary.setdefault('candidate_drop_diagnostic_count', len(drop_diagnostics))
    daily_pick_symbol = str(symbol_for(features.get('daily_best_paper_watch') or {}) or '') if isinstance(features, dict) else ''
    official_pick_symbol = str((features.get('candidate_consumption_summary') or {}).get('official_result', {}).get('symbol') or '') if isinstance(features, dict) else ''
    top10_diagnostics = (features.get('candidate_consumption_summary') or {}).get('top10_candidates', []) if isinstance(features, dict) else []
    top10_by_symbol = {
        str(item.get('symbol') or ''): item
        for item in top10_diagnostics
        if isinstance(item, dict) and item.get('symbol')
    }
    with_candidates = candidates
    scan_dir = str(bundle.get('_bundle_path') or bundle.get('raw_dir') or '')
    market_snap = bundle.get('market_snapshot') or {}
    scan_session = {
        'trade_date': trade_date,
        'scan_time': _dt.datetime.now(),
        'source_id': 'eastmoney_api_scan_v2',
        'quotes_count': int(market_snap.get('universe_quote_count') or len(candidates)),
        'scored_count': len([candidate for candidate in (bundle.get('scored_candidates') or candidates) if isinstance(candidate, dict)]),
        'passed_count': len([candidate for candidate in (bundle.get('passed_candidates') or []) if isinstance(candidate, dict)]),
        'scan_dir': scan_dir,
        'market_snapshot': market_snap if market_snap else None,
        'source_status': bundle.get('source_status'),
        'source_counts': bundle.get('source_counts'),
    }
    daily_candidates = []
    limitup_gene_signals = []
    top10_count = sum(1 for candidate in candidates if int(candidate.get('rank') or 999999) <= 10)

    for row in with_candidates:
        symbol = str(row.get('symbol') or row.get('code') or '').strip()
        if not symbol:
            continue
        raw_json = row if isinstance(row, dict) else {}
        candidate_features = dict(row.get('candidate_features') or row.get('structured_component_details') or {})
        candidate_features.setdefault('symbol', symbol)
        candidate_features.setdefault('code', symbol)
        candidate_features.setdefault('name', row.get('name') or row.get('stock_name') or '')
        candidate_features.setdefault('final_score', row.get('final_score') or row.get('score'))
        # Persist the canonical production contract inside the candidate
        # feature snapshot as well as ranking_basis, so downstream readers
        # cannot fall back to an unlabeled legacy score.
        candidate_features['production_score'] = (
            row.get('production_score')
            or row.get('formal_primary_score')
            or row.get('final_score')
            or row.get('score')
        )
        candidate_features['formal_primary_score'] = (
            row.get('formal_primary_score')
            or candidate_features.get('production_score')
        )
        candidate_features['rank_source'] = row.get('rank_source') or 't1_net_return_prediction'
        candidate_features['ranking_view'] = row.get('ranking_view') or 't1_net_return_model'
        candidate_features['score_source'] = row.get('score_source') or 't1_net_return_prediction'
        candidate_features['formal_rank_snapshot_id'] = row.get('formal_rank_snapshot_id') or ''
        candidate_features['formal_rank_snapshot_version'] = (
            row.get('formal_rank_snapshot_version') or ''
        )
        candidate_features.setdefault('source_layers', list(row.get('source_layers') or []))
        candidate_features.setdefault('selection_reason', row.get('selection_reason') or reason)
        candidate_features['candidate_pool_context'] = {
            **pool_exclusion_summary,
            'pool_type': 'full_candidate_pool',
            'persisted_candidate_count': len(candidates),
        }
        candidate_diagnostic = dict(top10_by_symbol.get(symbol) or {})
        candidate_evaluation_decision = str(
            candidate_diagnostic.get('official_decision_if_evaluated')
            or row.get('decision')
            or ('PAPER_PICK' if official_pick_symbol and symbol == official_pick_symbol else 'NO_PICK')
        )
        if candidate_evaluation_decision not in {'PAPER_PICK', 'NO_PICK'}:
            candidate_evaluation_decision = 'NO_PICK'
        is_official_pick = bool(official_pick_symbol and symbol == official_pick_symbol)
        # A candidate-level gate pass is diagnostic evidence only. The
        # production decision is owned by the single official pick symbol.
        evaluated_decision = 'PAPER_PICK' if is_official_pick else 'NO_PICK'
        is_top10 = int(row.get('rank') or 999999) <= 10
        selection_outcome = str(candidate_diagnostic.get('selection_outcome') or (
            'OFFICIAL_PICK' if official_pick_symbol and symbol == official_pick_symbol
            else ('TOP10_NOT_SELECTED' if is_top10 or not full_pool_is_explicit else 'FULL_POOL_NOT_SELECTED')
        ))
        full_pool_not_selected_reason = 'outside_top10_full_candidate_pool; ranked_below_top10'
        selection_outcome_reason = str(
            candidate_diagnostic.get('selection_outcome_reason')
            or candidate_diagnostic.get('official_decision_reason_if_evaluated')
            or (
                full_pool_not_selected_reason
                if selection_outcome == 'FULL_POOL_NOT_SELECTED'
                else (reason or '')
            )
        )
        # Never leave FULL_POOL rows with global pick reason "selected".
        if (
            selection_outcome == 'FULL_POOL_NOT_SELECTED'
            and selection_outcome_reason in ('', 'selected', 'PAPER_PICK', 'NO_PICK')
        ):
            selection_outcome_reason = full_pool_not_selected_reason
        eligibility_snapshot = dict(candidate_diagnostic.get('eligibility_snapshot') or candidate_features.get('paper_pick_eligibility') or {})
        selection_diagnostics = {
            'selection_key': list(candidate_diagnostic.get('selection_key') or []),
            'search_layer': candidate_diagnostic.get('search_layer') or row.get('search_layer') or row.get('search_layer_hint') or '',
            'source_layers': list(candidate_diagnostic.get('source_layers') or row.get('source_layers') or []),
            'candidate_stage': candidate_diagnostic.get('candidate_stage') or row.get('candidate_stage') or '',
            'hard_gate_status': dict(candidate_diagnostic.get('hard_gate_status') or row.get('hard_gate_status') or {}),
            'candidate_reasons': list(candidate_diagnostic.get('candidate_reasons') or candidate_diagnostic.get('positive_conditions') or []),
            'not_selected_reasons': list(candidate_diagnostic.get('not_selected_reasons') or candidate_diagnostic.get('blockers') or []),
            'why_candidate': list(candidate_diagnostic.get('why_candidate') or []),
            'why_not_selected': list(candidate_diagnostic.get('why_not_selected') or []),
            'candidate_evaluation_decision': candidate_evaluation_decision,
            'candidate_evaluation_reason': candidate_diagnostic.get('official_decision_reason_if_evaluated') or selection_outcome_reason,
            'official_decision_if_evaluated': candidate_diagnostic.get('official_decision_if_evaluated') or row.get('decision') or '',
            'official_decision_reason_if_evaluated': candidate_diagnostic.get('official_decision_reason_if_evaluated') or selection_outcome_reason,
            'candidate_pool_context': candidate_features['candidate_pool_context'],
        }
        candidate_entry_reason = unique_text_values([
            *(candidate_diagnostic.get('candidate_reasons') or []),
            *(candidate_diagnostic.get('positive_conditions') or []),
            row.get('selection_reason') or '',
            row.get('final_score_explanation') or '',
            'full_candidate_pool_base_filter',
        ])
        explicit_not_selected = [
            str(item) for item in (candidate_diagnostic.get('not_selected_reasons') or []) if item
        ]
        if selection_outcome == 'OFFICIAL_PICK':
            not_selected_reason = []
        elif explicit_not_selected:
            not_selected_reason = unique_text_values(explicit_not_selected)
        elif selection_outcome == 'FULL_POOL_NOT_SELECTED':
            not_selected_reason = [full_pool_not_selected_reason]
        else:
            not_selected_reason = unique_text_values([selection_outcome_reason])
        if selection_outcome == 'FULL_POOL_NOT_SELECTED' and not selection_diagnostics.get('why_not_selected'):
            selection_diagnostics['why_not_selected'] = list(not_selected_reason)
            selection_diagnostics['not_selected_reasons'] = list(
                selection_diagnostics.get('not_selected_reasons') or not_selected_reason
            )
        factor_snapshot = {
            'score': row.get('score'),
            'final_score': row.get('final_score'),
            'structured_score': row.get('structured_score'),
            'structured_priority_score': row.get('structured_priority_score'),
            'structured_components': row.get('structured_components') or row.get('structured_score_components') or {},
            'structured_component_details': row.get('structured_component_details') or {},
            'research_signals': row.get('research_signals') or {},
            'continuation_gene_score': row.get('continuation_gene_score'),
            'capital_risk_profile': row.get('capital_risk_profile') or candidate_capital_risk_profile(row),
            'ranking_basis_adjustment_components': ranking_basis_adjustment_components(row),
            'limitup_probability_proxy': limitup_probability_proxy_components(row),
            'paper_pick_risk_explanation_gate': paper_pick_risk_explanation_gate(row),
            'shadow_risk_profile': shadow_risk_profile(row, bundle),
            'social_confirmation': social_confirmation_profile(row),
            'candidate_pool_context': candidate_features['candidate_pool_context'],
        }
        factor_snapshot.update(limitup_gene_signal_values({**row, 'factor_snapshot': factor_snapshot}))
        auxiliary_evidence_snapshot = {
            'status': row.get('mainboard_auxiliary_evidence_status') or row.get('auxiliary_evidence_status'),
            'missing_domains': list(row.get('mainboard_auxiliary_missing_domains') or []),
            'announcements': list(row.get('announcement_evidence') or []),
            'news': dict(row.get('news_evidence') or {}),
            'sector_news': list(row.get('sector_news_evidence') or []),
            'limitup_reasons': list(row.get('limitup_reason_evidence') or []),
            'yesterday_limitup_gene': dict(row.get('yesterday_limitup_gene_evidence') or {}),
            'sector_yesterday_limitup_gene_proxy': dict(row.get('sector_yesterday_limitup_gene_proxy') or {}),
            'risk_notices': list(row.get('risk_notice_evidence') or []),
            'capital_flow': dict(row.get('data_directory_capital_flow') or {}),
            'popularity_rank': (row.get('capital_risk_profile') or {}).get('popularity_rank') if isinstance(row.get('capital_risk_profile'), dict) else row.get('popularity_rank'),
            'information_coverage_audit': dict(bundle.get('information_coverage_audit') or {}),
        }
        ranking_basis_snapshot = {
            'basis': row.get('ranking_basis') or row.get('rank_source') or 't1_net_return_prediction',
            'ranking_view': row.get('ranking_view') or 't1_net_return_model',
            'score_source': row.get('score_source') or 't1_net_return_prediction',
            'rank': row.get('rank'),
            'pool_rank': row.get('pool_rank'),
            'formal_rank': row.get('formal_rank'),
            'scanner_rank': row.get('scanner_rank'),
            'rank_source': row.get('rank_source') or 't1_net_return_prediction',
            'formal_primary_score': row.get('formal_primary_score'),
            'production_score': row.get('production_score') or row.get('formal_primary_score'),
            'formal_rank_snapshot_id': row.get('formal_rank_snapshot_id') or '',
            'formal_rank_snapshot_version': row.get('formal_rank_snapshot_version') or '',
            'selection_key': list(candidate_diagnostic.get('selection_key') or []),
            'structured_priority_score': row.get('structured_priority_score'),
            'ranking_basis_adjustment_components': ranking_basis_adjustment_components(row),
            'limitup_probability_proxy': limitup_probability_proxy_components(row),
            'paper_pick_risk_explanation_gate': paper_pick_risk_explanation_gate(row),
            'candidate_pool_context': candidate_features['candidate_pool_context'],
        }
        ticket_reason = {
            'decision': evaluated_decision,
            'candidate_evaluation_decision': candidate_evaluation_decision,
            'selection_outcome': selection_outcome,
            'reason': selection_outcome_reason,
        }
        for key in (
            'limit_up_potential', 'market_bonus', 'capital_bonus', 'fundamental_bonus',
            'sector_rotation_bonus', 'topic_heat_bonus', 'leader_bonus', 'flow_bonus',
            'market_mood_bonus', 'news_bonus', 'risk_penalty', 'sentiment_bonus',
        ):
            candidate_features.setdefault(key, row.get(key, 0))
        candidate_features['decision'] = evaluated_decision
        candidate_features['candidate_evaluation_decision'] = candidate_evaluation_decision
        candidate_features['candidate_evaluation_reason'] = (
            candidate_diagnostic.get('official_decision_reason_if_evaluated')
            or selection_outcome_reason
        )
        candidate_features['selection_outcome'] = selection_outcome
        candidate_features['selection_outcome_reason'] = selection_outcome_reason
        candidate_features['selection_diagnostics'] = selection_diagnostics
        if eligibility_snapshot:
            candidate_features['paper_pick_eligibility'] = eligibility_snapshot
        if decision == 'NO_PICK' and daily_pick_symbol and symbol == daily_pick_symbol and isinstance(features.get('daily_best_paper_watch'), dict):
            candidate_features['daily_best_paper_watch'] = dict(features['daily_best_paper_watch'])
        cohort_info = classify_candidate_cohort(
            {
                'trade_date': trade_date,
                'symbol': symbol,
                'rank': row.get('rank'),
                'candidate_entry_reason': candidate_entry_reason,
                'factor_snapshot': factor_snapshot,
                'auxiliary_evidence_snapshot': auxiliary_evidence_snapshot,
                'ranking_basis': ranking_basis_snapshot,
            },
            top10_count=top10_count,
            has_return=False,
            trade_date=trade_date,
        )
        daily_candidates.append({
            'trade_date': trade_date,
            'symbol': symbol,
            'stock_name': str(row.get('name') or row.get('stock_name') or ''),
            # Canonical rank is accepted T+1 net-return prediction order.
            'rank': row.get('rank'),
            'final_score': row.get('final_score') or row.get('score'),
            'decision': evaluated_decision,
            'is_official_pick': is_official_pick,
            'open_price': row.get('open') or row.get('open_price'),
            'close_price': row.get('close') or row.get('close_price') or row.get('price'),
            'high_price': row.get('high') or row.get('high_price'),
            'low_price': row.get('low') or row.get('low_price'),
            'volume': row.get('volume'),
            'amount': row.get('amount'),
            'pct_chg': row.get('pct_chg') or row.get('signal_pct'),
            'turnover_rate': row.get('turnover_rate'),
            'signal_pct': row.get('signal_pct'),
            'close_position_score': row.get('close_position_score'),
            'fund_flow_momentum': row.get('fund_flow_momentum'),
            'sector_catalyst_score': row.get('sector_catalyst_score') or row.get('sector_opportunity_score'),
            'early_opportunity_score': row.get('early_opportunity_score'),
            'topic_propagation_score': row.get('topic_propagation_score'),
            'market_regime': normalize_market_regime_for_db(row.get('market_regime') or bundle.get('market_snapshot', {}).get('market_regime')),
            'sentiment_catalyst': str(row.get('sentiment_catalyst') or ''),
            'theme_catalyst': str(row.get('theme_catalyst') or ''),
            'news_catalyst': str(row.get('news_catalyst') or ''),
            'positive_catalyst': str(row.get('positive_catalyst') or ''),
            'selection_reason': str(row.get('selection_reason') or reason or ''),
            'selection_outcome': selection_outcome,
            'selection_outcome_reason': selection_outcome_reason,
            'blockers': list(row.get('blockers') or []),
            'hard_gate_status': dict(row.get('hard_gate_status') or {}),
            'eligibility_snapshot': eligibility_snapshot,
            'selection_diagnostics': selection_diagnostics,
            'source_layers': list(row.get('source_layers') or []),
            'candidate_features': candidate_features,
            'raw_json': raw_json,
            'candidate_entry_reason': candidate_entry_reason,
            'ticket_reason': ticket_reason,
            'not_selected_reason': not_selected_reason,
            'factor_snapshot': factor_snapshot,
            'auxiliary_evidence_snapshot': auxiliary_evidence_snapshot,
            'ranking_basis': ranking_basis_snapshot,
            'postmortem_snapshot': {},
            'future_return_fields_placeholder': {
                't1_return': None,
            },
            'cohort': cohort_info['cohort'],
            'cohort_quality': cohort_info['cohort_quality'],
            'cohort_status_flags': cohort_info['status_flags'],
            'reconstruction_provenance': {},
        })
        limitup_gene_signals.append({
            'trade_date': trade_date,
            'symbol': symbol,
            'candidate': {**row, 'factor_snapshot': factor_snapshot},
        })
    return {
        'status': 'OK',
        'scan_session': scan_session,
        'daily_candidates': daily_candidates,
        'limitup_gene_signals': limitup_gene_signals,
        'candidate_pool_exclusion_summary': pool_exclusion_summary,
        'candidate_drop_diagnostics': drop_diagnostics,
    }

def persist_daily_candidate_snapshot(
    date: str,
    bundle: Dict[str, Any],
    features: Dict[str, Any],
    decision: str,
    reason: str,
    *,
    dry_run: bool = False,
    replace_existing: bool = False,
    correction_of: str = "",
    production_run_id: str = "",
    pick_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        from xiaogu_db import (
            create_production_run,
            fetch_daily_candidates,
            get_db,
            has_returns_for_trade_date,
            insert_scan_session,
            insert_pick,
            update_production_run_status,
            update_production_run_step,
            upsert_daily_candidate,
            upsert_limitup_gene_signals,
        )
        # Prefer host attribute so tests can monkeypatch runner.build_daily_...
        # (local name is this module's original; host re-export is patch target).
        build_fn = (
            getattr(_HOST, 'build_daily_candidate_persistence_payloads', None)
            if _HOST is not None
            else None
        )
        if build_fn is None:
            build_fn = build_daily_candidate_persistence_payloads
        payloads = build_fn(date, bundle, features, decision, reason)
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': repr(exc), 'written': 0}

    candidates = payloads.get('daily_candidates') or []
    if not candidates:
        return {'status': payloads.get('status') or 'NO_CANDIDATES', 'written': 0}
    if dry_run:
        return {
            'status': 'DRY_RUN',
            'written': 0,
            'candidate_count_expected': len(candidates),
            'candidate_pool_exclusion_summary': payloads.get('candidate_pool_exclusion_summary') or {},
            'candidate_drop_diagnostics': payloads.get('candidate_drop_diagnostics') or [],
            'dry_run': True,
        }

    correction_archive: Dict[str, Any] = {}
    prior_rows: List[Dict[str, Any]] = []
    if replace_existing:
        try:
            trade_date = dt.date.fromisoformat(date)
            if has_returns_for_trade_date(trade_date):
                return {
                    'status': 'REFUSED',
                    'written': 0,
                    'error': 'CANDIDATE_SNAPSHOT_CORRECTION_BLOCKED_AFTER_RETURNS',
                }
            prior_rows = fetch_daily_candidates(trade_date)
            archive_path = RAW_ROOT / date / str(bundle.get('_runner_asof_time') or dt.datetime.now().strftime('%H%M%S')).replace(':', '') / 'candidate_snapshot_correction_archive.json'
            archive = {
                'archive_type': 'daily_candidate_snapshot_before_correction',
                'trade_date': date,
                'correction_of': correction_of,
                'archived_at': now_iso(),
                'row_count': len(prior_rows),
                'rows': prior_rows,
            }
            encoded = json.dumps(archive, ensure_ascii=False, sort_keys=True, default=str)
            archive['sha256'] = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            write_json(archive_path, archive)
            correction_archive = {
                'archive_path': str(archive_path),
                'archive_sha256': archive['sha256'],
                'previous_candidate_count': len(prior_rows),
                'correction_of': correction_of,
            }
        except Exception as exc:
            return {
                'status': 'UNAVAILABLE',
                'written': 0,
                'error': f'CANDIDATE_SNAPSHOT_CORRECTION_ARCHIVE_FAILED:{exc!r}',
            }

    run_id = str(production_run_id or features.get('production_run_id') or bundle.get('production_run_id') or '')
    candidate_snapshot_id = str(bundle.get('candidate_snapshot_id') or run_id or '')
    retry_command = f'python3 xiaogu_forward_runner.py --date {date} --force'
    written = 0
    persisted_signal_rows = 0
    errors: List[Dict[str, Any]] = []
    if run_id:
        try:
            with get_db() as db:
                if not isinstance(pick_payload, dict):
                    raise RuntimeError('PICK_PAYLOAD_REQUIRED')
                scan_payload = dict(payloads.get('scan_session') or {})
                if not scan_payload:
                    raise RuntimeError('SCAN_SESSION_PAYLOAD_REQUIRED')
                scan_payload.update({'production_run_id': run_id, 'db': db})
                scan_session_id = insert_scan_session(**scan_payload)
                if scan_session_id < 0:
                    raise RuntimeError('SCAN_SESSION_INSERT_DID_NOT_RETURN_ID')
                create_production_run(
                    dt.date.fromisoformat(date),
                    run_id,
                    scan_session_id=scan_session_id,
                    rule_version=str(features.get('rule_version') or RULE_VERSION),
                    scoring_config_snapshot=dict(features.get('scoring_config_snapshot') or {}),
                    scoring_config_hash=str(features.get('scoring_config_hash') or ''),
                    input_payload_hash=str(features.get('input_payload_hash') or ''),
                    db=db,
                )
                update_production_run_step(
                    run_id,
                    'scanner',
                    'PASS',
                    required=True,
                    metadata={'scan_session_id': scan_session_id},
                    db=db,
                )
                update_production_run_step(
                    run_id, 'candidate_snapshot', 'RUNNING', required=True,
                    retry_command=retry_command, db=db,
                )
                update_production_run_step(
                    run_id, 'pick_persistence', 'RUNNING', required=True,
                    retry_command=retry_command, db=db,
                )
                signal_payloads = payloads.get('limitup_gene_signals') or []
                for index, candidate_kwargs in enumerate(candidates):
                    candidate_payload = dict(candidate_kwargs)
                    candidate_payload.update({
                        'production_run_id': run_id,
                        'candidate_snapshot_id': candidate_snapshot_id,
                        'db': db,
                    })
                    upsert_daily_candidate(**candidate_payload)
                    written += 1
                    signal_kwargs = (
                        signal_payloads[index]
                        if index < len(signal_payloads) and isinstance(signal_payloads[index], dict)
                        else {}
                    )
                    if not signal_kwargs:
                        raise RuntimeError(
                            f'LIMITUP_SIGNAL_PAYLOAD_REQUIRED:{candidate_payload.get("symbol") or ""}'
                        )
                    upsert_limitup_gene_signals(
                        **signal_kwargs,
                        db=db,
                        production_run_id=run_id,
                    )
                    persisted_signal_rows += 6
                update_production_run_step(
                    run_id, 'candidate_snapshot', 'PASS', required=True,
                    metadata={'candidate_count': written, 'candidate_snapshot_id': candidate_snapshot_id}, db=db,
                )
                persisted_pick_payload = dict(pick_payload)
                persisted_pick_payload.update({
                    'production_run_id': run_id,
                    'db': db,
                })
                pick_id = insert_pick(**persisted_pick_payload)
                if pick_id < 0:
                    raise RuntimeError('PICK_INSERT_DID_NOT_RETURN_ID')
                update_production_run_step(
                    run_id, 'pick_persistence', 'PASS', required=True,
                    metadata={'pick_id': pick_id}, db=db,
                )
        except Exception as exc:
            error = repr(exc)[:500]
            errors.append({'persistence': 'rollback', 'error': error})
            try:
                create_production_run(
                    dt.date.fromisoformat(date),
                    run_id,
                    rule_version=str(features.get('rule_version') or RULE_VERSION),
                    scoring_config_snapshot=dict(features.get('scoring_config_snapshot') or {}),
                    scoring_config_hash=str(features.get('scoring_config_hash') or ''),
                    input_payload_hash=str(features.get('input_payload_hash') or ''),
                )
                update_production_run_step(
                    run_id, 'candidate_snapshot', 'FAILED_PERSISTENCE', required=True,
                    error_message=error, retry_command=retry_command,
                )
                update_production_run_step(
                    run_id, 'pick_persistence', 'FAILED_PERSISTENCE', required=True,
                    error_message=error, retry_command=retry_command,
                )
                update_production_run_status(
                    run_id, 'FAILED_PERSISTENCE',
                    error_message=error,
                    retry_command=retry_command,
                )
            except Exception as step_exc:
                errors.append({'production_run_step': 'persist_failed', 'error': repr(step_exc)[:300]})
            return {
                'status': 'FAILED_PERSISTENCE', 'written': 0, 'candidate_count_expected': len(candidates),
                'persisted_signal_rows': 0, 'expected_signal_rows': len(candidates) * 6,
                'production_run_id': run_id, 'candidate_snapshot_id': candidate_snapshot_id,
                'error': error, 'retry_command': retry_command, 'errors': errors,
            }
    else:
        scan_payload = dict(payloads.get('scan_session') or {})
        if scan_payload:
            try:
                insert_scan_session(**scan_payload)
            except Exception as exc:
                errors.append({'scan_session': 'persist_failed', 'error': repr(exc)[:300]})
        signal_payloads = payloads.get('limitup_gene_signals') or []
        for index, candidate_kwargs in enumerate(candidates):
            symbol = str(candidate_kwargs.get('symbol') or '')
            signal_kwargs = (
                signal_payloads[index]
                if index < len(signal_payloads) and isinstance(signal_payloads[index], dict)
                else {}
            )
            try:
                upsert_daily_candidate(**candidate_kwargs)
                written += 1
                if signal_kwargs:
                    upsert_limitup_gene_signals(**signal_kwargs)
                    persisted_signal_rows += 6
            except Exception as exc:
                errors.append({'symbol': symbol, 'error': repr(exc)[:300]})
    preserved_stale_count = 0
    if replace_existing and prior_rows:
        current_symbols = {
            str(candidate.get('symbol') or '').strip()
            for candidate in candidates
            if str(candidate.get('symbol') or '').strip()
        }
        preserved_stale_count = sum(
            1
            for row in prior_rows
            if str(row.get('symbol') or '').strip() not in current_symbols
        )
    status = 'OK' if not errors else ('PARTIAL' if written else 'FAILED')
    return {
        'status': status,
        'written': written,
        'candidate_count_expected': len(candidates),
        'candidate_pool_exclusion_summary': payloads.get('candidate_pool_exclusion_summary') or {},
        'persisted_signal_rows': persisted_signal_rows,
        'expected_signal_rows': len(candidates) * 6,
        'replaced_existing_snapshot': bool(replace_existing),
        'pruned_stale_count': 0,
        'preserved_stale_count': preserved_stale_count,
        'correction_archive': correction_archive,
        'errors': errors,
        **(
            {
                'production_run_id': run_id,
                'candidate_snapshot_id': candidate_snapshot_id,
                'pick_id': pick_id,
            }
            if run_id else {}
        ),
    }

def write_daily_candidate_persist_retry_payload(path: Path, date: str, bundle: Dict[str, Any], features: Dict[str, Any], decision: str, reason: str, persist_result: Dict[str, Any]) -> str:
    retry_path = path.parent / 'db_persistence_retry_payload.json'
    write_json(retry_path, {
        'payload_version': 'daily_candidate_snapshot_v1',
        'status': 'PENDING_DB_REPLAY',
        'date': date,
        'decision': decision,
        'reason': reason,
        'bundle': bundle,
        'features': features,
        'persist_result': persist_result,
    })
    return str(retry_path)



# Live host re-bind wrappers (public API)
write_text = _with_host(write_text)
write_json = _with_host(write_json)
scan_summary_paths = _with_host(scan_summary_paths)
summary_bundle_rows = _with_host(summary_bundle_rows)
summary_file_rows = _with_host(summary_file_rows)
load_candidate_bundle = _with_host(load_candidate_bundle)
_bundle_from_scan_summary = _with_host(_bundle_from_scan_summary)
load_latest_eastmoney_scan = _with_host(load_latest_eastmoney_scan)
scan_date_for_runtime = _with_host(scan_date_for_runtime)
build_daily_candidate_persistence_payloads = _with_host(build_daily_candidate_persistence_payloads)
persist_daily_candidate_snapshot = _with_host(persist_daily_candidate_snapshot)
write_daily_candidate_persist_retry_payload = _with_host(write_daily_candidate_persist_retry_payload)
