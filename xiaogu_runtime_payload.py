#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slim runtime / recorder payloads to prevent OOM from full-pool embedding.

Production default: never embed full_candidate_pool / scored_candidates / deep
eligibility trees into runtime_decision_context.json or recorder_features.json.
Full baskets stay in memory for scoring only; disk gets bounded evidence.
"""
from __future__ import annotations

import json
import os
import resource
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Soft caps (bytes) for on-disk payloads. Override via env for debug.
DEFAULT_RUNTIME_MAX_BYTES = int(os.environ.get('XIAOGU_RUNTIME_MAX_BYTES', str(8 * 1024 * 1024)))
DEFAULT_RECORDER_MAX_BYTES = int(os.environ.get('XIAOGU_RECORDER_MAX_BYTES', str(4 * 1024 * 1024)))
DEFAULT_RSS_WARN_MB = float(os.environ.get('XIAOGU_RSS_WARN_MB', '2800'))
DEFAULT_RSS_HARD_MB = float(os.environ.get('XIAOGU_RSS_HARD_MB', '4200'))

CANDIDATE_SLIM_KEYS = (
    'symbol', 'code', 'name', 'stock_name', 'rank', 'score', 'final_score',
    'structured_score', 'structured_priority_score', 'signal_pct', 'price',
    'setup_type', 'candidate_stage', 'board',
    'main_theme_core_score', 'main_theme_alignment_score', 'main_theme_source',
    'sector_opportunity_score', 'fund_flow_momentum', 'volume_ratio',
    'close_position_score', 'continuation_gene_score',
    'announcement_catalyst_score', 'news_catalyst_strength',
    'sector_news_catalyst_score', 'limitup_reason_quality_score',
    'mainboard_auxiliary_evidence_status', 'mainboard_auxiliary_confidence',
    'risk_notice_penalty', 'sector_opportunity_tags', 'theme_tags',
    'industry', 'sector', 'predicted_sector', 'main_theme',
    'selection_reason', 'final_score_explanation',
    'social_sentiment_score', 'repo_contribution_summary',
    'score_delta', 'ranking_basis', 'formal_eligible',
)

ELIGIBILITY_SLIM_KEYS = (
    'eligible', 'blockers', 'missing_conditions', 'positive_conditions',
    'score_band', 'board',
)

BUNDLE_META_KEYS = (
    'date', 'asof_time', 'generated_at', 'source_market_date', 'source_time',
    '_runner_asof_time', 'candidate_source', 'rule_version', 'paper_only',
    'no_trade', 'production_ready', 'data_gate_status', 'xiaochan_gate_status',
    'scan_summary_path', 'scan_summary_source_time', 'pipeline_version',
    'source', '_bundle_path',
)


def process_rss_mb() -> float:
    """Current process RSS in MiB (Linux ru_maxrss is KiB)."""
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux: ru_maxrss is kilobytes; macOS is bytes. Prefer /proc when present.
        proc = Path('/proc/self/status')
        if proc.exists():
            for line in proc.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.startswith('VmRSS:'):
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024.0
        return float(usage.ru_maxrss) / 1024.0
    except Exception:
        return 0.0


def payload_bytes(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return -1


def slim_eligibility(eligibility: Any) -> Dict[str, Any]:
    if not isinstance(eligibility, dict):
        return {}
    out = {k: eligibility.get(k) for k in ELIGIBILITY_SLIM_KEYS if k in eligibility}
    signals = eligibility.get('signals') if isinstance(eligibility.get('signals'), dict) else {}
    if signals:
        keep_signal_keys = (
            'quality_daily_ticket_escape', 'sszcw_favored_quality_escape',
            'quality_escape_partial_aux_exception', 'main_theme_core_score',
            'main_theme_alignment_score', 'research_panel_overall',
            'setup_class', 'setup_rank', 'candidate_stage',
            'soft_context_valid', 'soft_context_source',
            'pre_pick_market_context_soft',
        )
        slim_signals = {k: signals.get(k) for k in keep_signal_keys if k in signals}
        pre = slim_signals.get('pre_pick_market_context_soft')
        if isinstance(pre, dict):
            slim_signals['pre_pick_market_context_soft'] = {
                k: pre.get(k)
                for k in (
                    'favored_hits', 'risk_hits', 'net_soft_bias', 'confidence',
                    'high_confidence_favored', 'high_confidence_risk',
                    'market_stance', 'soft_context_valid', 'soft_context_source',
                    'hard_gate', 'force_pick',
                )
                if k in pre
            }
        out['signals'] = slim_signals
    return out


def slim_candidate_row(row: Any, *, include_eligibility: bool = True) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in CANDIDATE_SLIM_KEYS:
        if key in row and row[key] is not None:
            out[key] = row[key]
    symbol = str(row.get('symbol') or row.get('code') or '').zfill(6) if (row.get('symbol') or row.get('code')) else ''
    if symbol:
        out['symbol'] = symbol
    if include_eligibility and isinstance(row.get('paper_pick_eligibility'), dict):
        out['paper_pick_eligibility'] = slim_eligibility(row['paper_pick_eligibility'])
    capital = row.get('capital_risk_profile')
    if isinstance(capital, dict):
        out['capital_risk_profile'] = {
            k: capital.get(k)
            for k in ('status', 'risk_penalty_score', 'risk_codes', 'risk_flags')
            if k in capital
        }
    if isinstance(row.get('evidence_card'), dict):
        out['evidence_card'] = row['evidence_card']
    return out


def slim_candidate_list(rows: Any, limit: int = 20) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    return [slim_candidate_row(row) for row in rows[: max(0, limit)] if isinstance(row, dict)]


def slim_bundle_for_runtime(bundle: Any, *, top_n: int = 15) -> Dict[str, Any]:
    """Bounded stand-in for full candidate_bundle_status (was ~20MB)."""
    if not isinstance(bundle, dict):
        return {'status': 'MISSING_BUNDLE'}
    out: Dict[str, Any] = {k: bundle.get(k) for k in BUNDLE_META_KEYS if k in bundle}
    paper = bundle.get('paper_scoring_candidates') or []
    full_pool = bundle.get('full_candidate_pool') or []
    scored = bundle.get('scored_candidates') or []
    out['paper_scoring_candidates'] = slim_candidate_list(paper, limit=top_n)
    out['paper_scoring_candidates_count'] = len(paper) if isinstance(paper, list) else 0
    out['full_candidate_pool_count'] = len(full_pool) if isinstance(full_pool, list) else 0
    out['scored_candidates_count'] = len(scored) if isinstance(scored, list) else 0
    # Never embed full pools / structured_scores / research_signals lists.
    out['payload_policy'] = 'slim_runtime_v1_no_full_pool_embed'
    cand = bundle.get('candidate')
    if isinstance(cand, dict):
        out['candidate'] = slim_candidate_row(cand)
    pool_ex = bundle.get('candidate_pool_exclusion_summary')
    if isinstance(pool_ex, dict):
        out['candidate_pool_exclusion_summary'] = {
            k: pool_ex.get(k)
            for k in (
                'target_count', 'selected_count', 'excluded_count',
                'main_board_only', 'price_cap', 'pct_range',
            )
            if k in pool_ex
        }
    for key in (
        'paper_pick_candidate_stage_distribution',
        'candidate_stage_blocker_distribution',
        'daily_ticket_search_result',
        'weak_market_shadow_ticket',
        'sector_catalyst_diagnostics',
        'first_search_candidate_diagnostic',
    ):
        if key in bundle and bundle[key] is not None:
            val = bundle[key]
            # Cap nested size roughly by dropping large lists.
            if isinstance(val, dict):
                out[key] = {
                    k: v for k, v in val.items()
                    if not (isinstance(v, list) and len(v) > 30)
                }
            elif not isinstance(val, list) or len(val) <= 20:
                out[key] = val
    market = bundle.get('market_snapshot')
    if isinstance(market, dict):
        out['market_snapshot'] = {
            k: market.get(k)
            for k in (
                'market_regime', 'market_breadth_up_pct', 'market_limitups',
                'broken_limitups', 'limitup_broken_ratio', 'market_follow_through_score',
                'sentiment_score', 'max_consecutive',
            )
            if k in market
        }
    return out


def slim_candidate_features(features: Any) -> Dict[str, Any]:
    """Slim candidate-level feature dict before embedding in runtime/recorder."""
    if not isinstance(features, dict):
        return {}
    out = slim_candidate_row(features, include_eligibility=True)
    # Preserve critical decision fields not in CANDIDATE_SLIM_KEYS
    for key in (
        'decision', 'decision_reason', 'blockers', 'risk_flags',
        'source_layers', 'xiaochan_gate_status', 'data_gate_status',
        'score_delta_by_repo', 'repo_delta_by_repo', 'repo_contributions',
        'repo_contribution_summary', 'final_score_explanation',
        'structured_formal_paper_pick_eligible', 'formal_eligible',
        'candidate_lifecycle', 'setup_class', 'setup_rank', 'setup_reason',
        'climax_risk', 'climax_layer', 'climax_tag',
        'selection_outcome', 'selection_outcome_reason', 'buy_plan', 'sell_plan',
        'structured_reasons', 'risk_factors', 'failure_conditions',
        'evidence_card', 'similar_cases', 'main_theme_source',
        'leader_chain_score', 'soft_context_valid', 'soft_context_source',
        'score_delta', 'native_runtime_summary',
        'candidate_bundle_path', 'scan_age_minutes',
        'account_available_cash', 'paper_one_lot_cost_cap',
        # NO_PICK → highest-score promotion diagnostics (main runtime path)
        'no_pick_promoted_to_highest_score',
        'original_no_pick_reason',
        'original_no_pick_flags',
        'fallback_from_no_pick',
        'core_market_source_gate',
    ):
        if key in features and features[key] is not None:
            out[key] = features[key]
    # Basket: top slim only, never full pool
    basket = features.get('paper_candidate_basket')
    if isinstance(basket, list):
        out['paper_candidate_basket'] = slim_candidate_list(basket, limit=12)
        out['paper_candidate_basket_count'] = len(basket)
    if isinstance(features.get('paper_pick_eligibility'), dict):
        out['paper_pick_eligibility'] = slim_eligibility(features['paper_pick_eligibility'])
    # Drop known heavy nests
    for heavy in (
        'full_candidate_pool', 'scored_candidates', 'structured_scores',
        'structured_component_details_list', 'eastmoney_account_snapshot',
        'data_directory_content', 'candidate_validations',
    ):
        out.pop(heavy, None)
    return out


def slim_features_for_recorder(features: Any) -> Dict[str, Any]:
    """Default slim features written to recorder_features.json / ledger snapshot."""
    if not isinstance(features, dict):
        return {}
    # Prefer nested candidate_features if this is the outer runtime features bag
    candidate = features.get('candidate_features') if isinstance(features.get('candidate_features'), dict) else features
    out = slim_candidate_features(candidate)
    # Outer decision envelope
    for key in (
        'date', 'asof_time', 'generated_at', 'rule_version', 'runner',
        'decision', 'symbol', 'decision_reason', 'structured_reasons',
        'buy_plan', 'sell_plan', 'risk_factors', 'failure_conditions',
        'evidence_card', 'similar_cases', 'single_target_card',
        'official_explanation_summary', 'source_consumption_summary',
        'repo_contribution_summary', 'score_delta_by_repo',
        'soft_context_valid', 'soft_context_source', 'pre_pick_market_context_soft',
        'payload_policy', 'runtime_payload_bytes', 'rss_mb_at_write',
    ):
        if key in features and features[key] is not None:
            out[key] = features[key]
    # Slim single_target_card / consumption if huge
    if isinstance(out.get('single_target_card'), dict):
        card = out['single_target_card']
        if payload_bytes(card) > 200_000:
            out['single_target_card'] = {
                k: card.get(k)
                for k in (
                    'symbol', 'name', 'decision', 'score', 'selection_reason',
                    'evidence_card', 'eligibility_snapshot', 'risk_flags',
                )
                if k in card
            }
    out['payload_policy'] = 'slim_recorder_v1'
    return out


def slim_runtime_features(features: Any) -> Dict[str, Any]:
    """Outer features bag for runtime_decision_context.json."""
    if not isinstance(features, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in features.items():
        if key == 'candidate_bundle_status':
            out[key] = slim_bundle_for_runtime(value) if isinstance(value, dict) else value
        elif key == 'candidate_features':
            out[key] = slim_candidate_features(value)
        elif key == 'candidate_validations':
            if isinstance(value, list):
                slim_vals = []
                for item in value[:10]:
                    if not isinstance(item, dict):
                        continue
                    slim_vals.append({
                        'symbol': item.get('symbol'),
                        'validation_passed': item.get('validation_passed'),
                        'combined_verdict': item.get('combined_verdict'),
                        'external_validation_skipped': item.get('external_validation_skipped'),
                        'eligibility_snapshot': slim_eligibility(item.get('eligibility_snapshot')),
                    })
                out[key] = slim_vals
        elif key in (
            'paper_candidate_basket', 'full_candidate_pool', 'scored_candidates',
            'data_directory_content',
        ):
            continue
        elif key == 'candidate_consumption_summary' and isinstance(value, dict):
            # Drop nested full candidate rows if present
            slim_summary = dict(value)
            for heavy in ('ranked_candidates', 'all_candidates', 'paper_scoring_candidates'):
                if isinstance(slim_summary.get(heavy), list) and len(slim_summary[heavy]) > 15:
                    slim_summary[heavy] = slim_candidate_list(slim_summary[heavy], limit=10)
            out[key] = slim_summary
        elif key == 'runtime_market_snapshot' and isinstance(value, dict):
            # Keep paths/meta only — not nested giant blobs
            out[key] = {
                k: value.get(k)
                for k in (
                    'date', 'asof_time', 'raw_dir', 'scan_dir', 'source',
                    'status', 'generated_at',
                )
                if k in value
            }
        else:
            out[key] = value
    out['payload_policy'] = 'slim_runtime_v1'
    out['rss_mb_at_write'] = round(process_rss_mb(), 1)
    return out


def build_runtime_decision_context(
    features: Dict[str, Any],
    decision: str,
    symbol: str,
    reason: str,
    single_target_card: Any = None,
) -> Dict[str, Any]:
    slim_features = slim_runtime_features(features)
    card = single_target_card
    if isinstance(card, dict) and payload_bytes(card) > 300_000:
        card = {
            k: card.get(k)
            for k in (
                'symbol', 'name', 'decision', 'score', 'selection_reason',
                'evidence_card', 'eligibility_snapshot', 'risk_flags',
            )
            if k in card
        }
    payload = {
        'features': slim_features,
        'decision': decision,
        'symbol': symbol,
        'decision_reason': reason,
        'single_target_card': card,
        'payload_policy': 'slim_runtime_v1',
    }
    size = payload_bytes(payload)
    payload['runtime_payload_bytes'] = size
    if size > DEFAULT_RUNTIME_MAX_BYTES:
        # Emergency strip: keep only decision + evidence + slim pick
        payload = {
            'features': {
                'date': slim_features.get('date'),
                'asof_time': slim_features.get('asof_time'),
                'rule_version': slim_features.get('rule_version'),
                'candidate_features': slim_features.get('candidate_features') or {},
                'evidence_card': slim_features.get('evidence_card'),
                'similar_cases': slim_features.get('similar_cases'),
                'official_explanation_summary': slim_features.get('official_explanation_summary'),
                'payload_policy': 'slim_runtime_v1_emergency_cap',
                'runtime_payload_bytes_before_cap': size,
                'rss_mb_at_write': round(process_rss_mb(), 1),
            },
            'decision': decision,
            'symbol': symbol,
            'decision_reason': reason,
            'single_target_card': card if isinstance(card, dict) else {},
            'payload_policy': 'slim_runtime_v1_emergency_cap',
            'runtime_payload_bytes': 0,
        }
        payload['runtime_payload_bytes'] = payload_bytes(payload)
    return payload


def enforce_runtime_memory_gate(*, stage: str = 'pre_write') -> Dict[str, Any]:
    """Report RSS; hard gate is advisory log + flag (never crash pick chain silently)."""
    rss = process_rss_mb()
    warn = DEFAULT_RSS_WARN_MB
    hard = DEFAULT_RSS_HARD_MB
    status = 'OK'
    if rss >= hard:
        status = 'HARD'
    elif rss >= warn:
        status = 'WARN'
    return {
        'stage': stage,
        'rss_mb': round(rss, 1),
        'warn_mb': warn,
        'hard_mb': hard,
        'status': status,
        'policy': 'slim_payload_required' if status != 'OK' else 'normal',
    }


def maybe_force_gc() -> None:
    import gc
    gc.collect()
