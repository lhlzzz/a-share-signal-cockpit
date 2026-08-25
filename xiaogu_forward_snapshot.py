#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-owner extraction from the production forward runner.

The production entry remains ``xiaogu_forward_runner.py``. This module only
owns the responsibility named in its filename and is host-bound so existing
imports and test monkeypatches retain their behavior.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple
from xiaogu_forward_host_binding import create_host_binding

_HOST = None
REQUIRED_FROM_HOST = ('FORMAL_RANK_SNAPSHOT_VERSION', 'PRODUCTION_CHAIN_MODE', 'PRODUCTION_RANKING_VIEW', 'PRODUCTION_RANK_SOURCE', 'PRODUCTION_SCORE_SOURCE', 'PRODUCTION_SNAPSHOT_ORIGINS', 'decision_feature_lineage_status', 'stamp_decision_feature_lineage', 'formal_candidate_sort_key', 'safe_float', 'safe_int', 'symbol_for', 't1_production_prediction', 'unique_text_values')

bind_host, _inject_host, _with_host = create_host_binding(
    globals(), REQUIRED_FROM_HOST, preserve_existing_on_missing=True,
)

_FORMAL_RANK_STATE_FIELDS = (
    'rank',
    'pool_rank',
    'scanner_rank',
    'formal_rank',
    'rank_source',
    'formal_primary_score',
    'formal_prediction_valid',
    'formal_prediction_reason',
    'production_score',
    'score',
    'final_score',
    'score_source',
    'ranking_view',
    'formal_rank_snapshot_id',
    'formal_rank_snapshot_version',
)

def apply_formal_profit_ranks(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Align canonical rank with profit-first formal_candidate_sort_key (P1).

    Preserves scanner structured rank as pool_rank / scanner_rank.
    Sets formal_rank and overwrites rank so TOP10 / FULL_POOL outcomes match
    production formal ordering (not scanner structured_priority alone).
    """
    stamped: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out = dict(row)
        existing_pool = safe_int(out.get('pool_rank'))
        existing_rank = safe_int(out.get('rank'))
        existing_scanner = safe_int(out.get('scanner_rank'))
        if existing_pool is None:
            if existing_scanner is not None:
                out['pool_rank'] = existing_scanner
            elif existing_rank is not None:
                out['pool_rank'] = existing_rank
        if out.get('scanner_rank') is None and existing_rank is not None and out.get('rank_source') != PRODUCTION_RANK_SOURCE:
            out['scanner_rank'] = existing_rank
        stamped.append(out)
    ordered = sorted(stamped, key=formal_candidate_sort_key, reverse=True)
    for idx, row in enumerate(ordered, 1):
        row['formal_rank'] = idx
        row['rank'] = idx
        row['rank_source'] = PRODUCTION_RANK_SOURCE
        prediction = t1_production_prediction(row)
        row['formal_prediction_valid'] = bool(prediction.get('valid'))
        row['formal_prediction_reason'] = str(prediction.get('reason') or '')
        row['formal_primary_score'] = (
            round(float(prediction['tradable_edge']), 4)
            if prediction.get('valid') else None
        )
        row['production_score'] = row['formal_primary_score']
        row['score'] = row['production_score']
        row['final_score'] = row['production_score']
        row['ranking_view'] = PRODUCTION_RANKING_VIEW
        row['score_source'] = PRODUCTION_SCORE_SOURCE
    snapshot_id = _formal_rank_snapshot_id(ordered)
    for row in ordered:
        row['formal_rank_snapshot_id'] = snapshot_id
        row['formal_rank_snapshot_version'] = FORMAL_RANK_SNAPSHOT_VERSION
    return ordered

def _formal_rank_snapshot_id(rows: List[Dict[str, Any]]) -> str:
    payload = [
        {
            'symbol': symbol_for(row),
            'formal_rank': row.get('formal_rank'),
            'formal_primary_score': row.get('formal_primary_score'),
            'formal_prediction_valid': row.get('formal_prediction_valid'),
            'formal_prediction_reason': row.get('formal_prediction_reason'),
        }
        for row in rows
        if isinstance(row, dict) and symbol_for(row)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]

def validate_formal_rank_snapshot(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify that persisted rank fields still describe the active formal sorter."""
    errors: List[Dict[str, Any]] = []
    snapshot_ids = set()
    for row in rows or []:
        if not isinstance(row, dict) or not symbol_for(row):
            continue
        expected = formal_candidate_sort_key(row)
        actual_score = safe_float(row.get('formal_primary_score'))
        actual_rank = safe_int(row.get('formal_rank'))
        prediction = t1_production_prediction(row)
        prediction_valid = bool(prediction.get('valid'))
        if bool(row.get('formal_prediction_valid')) != prediction_valid:
            errors.append({
                'symbol': symbol_for(row),
                'field': 'formal_prediction_valid',
                'expected': prediction_valid,
                'actual': row.get('formal_prediction_valid'),
            })
        if str(row.get('formal_prediction_reason') or '') != str(prediction.get('reason') or ''):
            errors.append({
                'symbol': symbol_for(row),
                'field': 'formal_prediction_reason',
                'expected': prediction.get('reason') or '',
                'actual': row.get('formal_prediction_reason') or '',
            })
        if prediction_valid and (
            actual_score is None or abs(actual_score - float(expected[0])) > 1e-4
        ):
            errors.append({
                'symbol': symbol_for(row),
                'field': 'formal_primary_score',
                'expected': round(float(expected[0]), 6),
                'actual': actual_score,
            })
        if not prediction_valid and actual_score is not None:
            errors.append({
                'symbol': symbol_for(row),
                'field': 'formal_primary_score',
                'expected': None,
                'actual': actual_score,
            })
        if actual_rank is None or safe_int(row.get('rank')) != actual_rank:
            errors.append({
                'symbol': symbol_for(row),
                'field': 'rank',
                'expected': actual_rank,
                'actual': safe_int(row.get('rank')),
            })
        if row.get('formal_rank_snapshot_id'):
            snapshot_ids.add(str(row.get('formal_rank_snapshot_id')))
    return {
        'valid': not errors and len(snapshot_ids) <= 1,
        'errors': errors,
        'snapshot_ids': sorted(snapshot_ids),
        'snapshot_version': FORMAL_RANK_SNAPSHOT_VERSION,
    }

def validate_active_production_chain(bundle: Dict[str, Any], target_date: str = '') -> Dict[str, Any]:
    """Validate the one production chain without reconstructing missing state."""
    bundle = bundle if isinstance(bundle, dict) else {}
    errors: List[str] = []
    if str(bundle.get('production_chain_mode') or '') != PRODUCTION_CHAIN_MODE:
        errors.append('PRODUCTION_CHAIN_MODE_NOT_STRICT')
    if str(bundle.get('ranking_view') or '') != PRODUCTION_RANKING_VIEW:
        errors.append('PRODUCTION_RANKING_VIEW_MISMATCH')
    if str(bundle.get('production_snapshot_origin') or '') not in PRODUCTION_SNAPSHOT_ORIGINS:
        errors.append('PRODUCTION_SNAPSHOT_ORIGIN_NOT_VERIFIED')
    formal_pool = [
        row for row in (bundle.get('formal_ranked_pool') or [])
        if isinstance(row, dict) and symbol_for(row)
    ]
    if not formal_pool:
        errors.append('FORMAL_RANKED_POOL_MISSING')
    snapshot_validation = validate_formal_rank_snapshot(formal_pool) if formal_pool else {
        'valid': False,
        'errors': [],
        'snapshot_ids': [],
        'snapshot_version': FORMAL_RANK_SNAPSHOT_VERSION,
    }
    if not snapshot_validation.get('valid'):
        errors.append('FORMAL_RANK_SNAPSHOT_INVALID')
    for row in formal_pool:
        lineage_validation = decision_feature_lineage_status(row, bundle, required=True)
        if not lineage_validation.get('valid'):
            errors.append(f'DECISION_FEATURE_LINEAGE_INVALID:{symbol_for(row)}')
    expected_snapshot_id = str(bundle.get('formal_rank_snapshot_id') or '')
    expected_snapshot_version = str(bundle.get('formal_rank_snapshot_version') or '')
    if not expected_snapshot_id:
        errors.append('FORMAL_RANK_SNAPSHOT_ID_MISSING')
    if expected_snapshot_version != FORMAL_RANK_SNAPSHOT_VERSION:
        errors.append('FORMAL_RANK_SNAPSHOT_VERSION_MISMATCH')
    for row in formal_pool:
        for field, expected in (
            ('ranking_view', PRODUCTION_RANKING_VIEW),
            ('rank_source', PRODUCTION_RANK_SOURCE),
            ('score_source', PRODUCTION_SCORE_SOURCE),
        ):
            if str(row.get(field) or '') != expected:
                errors.append(f'{field.upper()}_MISMATCH:{symbol_for(row)}')
        if bool(row.get('formal_prediction_valid')) and safe_float(row.get('production_score')) is None:
            errors.append(f'PRODUCTION_SCORE_MISSING:{symbol_for(row)}')
        if bool(row.get('formal_prediction_valid')) and safe_float(row.get('formal_primary_score')) is None:
            errors.append(f'FORMAL_PRIMARY_SCORE_MISSING:{symbol_for(row)}')
        if safe_int(row.get('formal_rank')) is None:
            errors.append(f'FORMAL_RANK_MISSING:{symbol_for(row)}')
        if str(row.get('formal_rank_snapshot_id') or '') != expected_snapshot_id:
            errors.append(f'FORMAL_RANK_SNAPSHOT_ID_MISMATCH:{symbol_for(row)}')
    if target_date:
        source_date = str(bundle.get('source_market_date') or bundle.get('date') or '')[:10]
        if source_date and source_date != str(target_date)[:10]:
            errors.append(f'PRODUCTION_SOURCE_DATE_MISMATCH:{source_date}')
    return {
        'valid': not errors,
        'mode': bundle.get('production_chain_mode'),
        'ranking_view': bundle.get('ranking_view'),
        'rank_source': PRODUCTION_RANK_SOURCE,
        'score_source': PRODUCTION_SCORE_SOURCE,
        'snapshot_origin': bundle.get('production_snapshot_origin'),
        'candidate_count': len(formal_pool),
        'errors': unique_text_values(errors),
        'snapshot_validation': snapshot_validation,
    }

def quarantine_nonproduction_bundle(bundle: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    """Keep legacy rows for audit while removing them from production inputs."""
    if not isinstance(bundle, dict):
        return bundle
    legacy_rows = []
    for key in ('formal_ranked_pool', 'full_candidate_pool', 'paper_scoring_candidates', 'candidate'):
        value = bundle.get(key)
        if isinstance(value, list):
            legacy_rows.extend(row for row in value if isinstance(row, dict))
        elif isinstance(value, dict) and symbol_for(value):
            legacy_rows.append(dict(value))
    if legacy_rows:
        bundle['legacy_candidate_basket'] = legacy_rows
    bundle['production_chain_blocked'] = True
    bundle['production_chain_validation'] = dict(validation or {})
    bundle['formal_ranked_pool'] = []
    bundle['full_candidate_pool'] = []
    bundle['paper_scoring_candidates'] = []
    bundle['candidate'] = {}
    return bundle

def synchronize_formal_profit_rank_state(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Use one final formal rank state for the full pool and decision pool."""
    if not isinstance(bundle, dict):
        return bundle

    full_pool = [
        row for row in (bundle.get('full_candidate_pool') or [])
        if isinstance(row, dict)
    ]
    decision_pool = [
        row for row in (bundle.get('paper_scoring_candidates') or [])
        if isinstance(row, dict)
    ]
    if not full_pool:
        if decision_pool:
            ranked = apply_formal_profit_ranks(decision_pool)
            bundle['paper_scoring_candidates'] = ranked
            bundle['formal_ranked_pool'] = ranked
            bundle['candidate'] = ranked[0] if ranked else bundle.get('candidate') or {}
        return bundle

    ranked_full = [
        dict(row)
        for row in (bundle.get('formal_ranked_pool') or [])
        if isinstance(row, dict)
    ]
    if not ranked_full:
        ranked_full = apply_formal_profit_ranks(full_pool)
        snapshot_id = _formal_rank_snapshot_id(ranked_full)
        for row in ranked_full:
            row['formal_rank_snapshot_id'] = snapshot_id
            row['formal_rank_snapshot_version'] = FORMAL_RANK_SNAPSHOT_VERSION
    else:
        snapshot_id = str(ranked_full[0].get('formal_rank_snapshot_id') or '')
        if not snapshot_id:
            snapshot_id = _formal_rank_snapshot_id(ranked_full)
            for row in ranked_full:
                row['formal_rank_snapshot_id'] = snapshot_id
                row['formal_rank_snapshot_version'] = FORMAL_RANK_SNAPSHOT_VERSION
    ranked_by_symbol = {
        symbol_for(row): row
        for row in ranked_full
        if symbol_for(row)
    }

    synchronized_decision_pool: List[Dict[str, Any]] = []
    for row in decision_pool:
        updated = dict(row)
        ranked = ranked_by_symbol.get(symbol_for(row))
        if ranked:
            for field in _FORMAL_RANK_STATE_FIELDS:
                updated[field] = ranked.get(field)
        synchronized_decision_pool.append(updated)

    bundle['full_candidate_pool'] = ranked_full
    bundle['paper_scoring_candidates'] = synchronized_decision_pool
    bundle['formal_ranked_pool'] = ranked_full
    bundle['formal_rank_snapshot_id'] = snapshot_id
    bundle['formal_rank_snapshot_version'] = FORMAL_RANK_SNAPSHOT_VERSION
    bundle['formal_rank_snapshot_validation'] = validate_formal_rank_snapshot(ranked_full)
    candidate = bundle.get('candidate')
    ranked_candidate = ranked_by_symbol.get(symbol_for(candidate)) if isinstance(candidate, dict) else None
    if ranked_candidate:
        updated_candidate = dict(candidate)
        for field in _FORMAL_RANK_STATE_FIELDS:
            updated_candidate[field] = ranked_candidate.get(field)
        bundle['candidate'] = updated_candidate
    return bundle

def freeze_formal_production_snapshot(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze one final T-day ranking for both official selection and persistence."""
    if not isinstance(bundle, dict):
        return bundle
    if bundle.get('strict_production_chain'):
        stamp_decision_feature_lineage(bundle)
        validation = validate_active_production_chain(bundle, str(bundle.get('date') or ''))
        bundle['production_chain_validation'] = validation
        if not validation.get('valid'):
            return quarantine_nonproduction_bundle(bundle, validation)
        ranked = [dict(row) for row in bundle.get('formal_ranked_pool') or [] if isinstance(row, dict)]
        for row in ranked:
            if safe_float(row.get('one_lot_cost')) is None:
                price = safe_float(
                    row.get('price')
                    if row.get('price') is not None
                    else row.get('close')
                    if row.get('close') is not None
                    else row.get('close_price')
                )
                if price is not None and price > 0:
                    row['one_lot_cost'] = round(price * 100.0, 4)
            eligibility = row.get('paper_pick_eligibility')
            signals = eligibility.get('signals') if isinstance(eligibility, dict) else {}
            if isinstance(signals, dict):
                if row.get('one_lot_cost') is None and signals.get('one_lot_cost') is not None:
                    row['one_lot_cost'] = signals.get('one_lot_cost')
                if row.get('one_lot_cost_cap') is None and signals.get('one_lot_cost_cap') is not None:
                    row['one_lot_cost_cap'] = signals.get('one_lot_cost_cap')
        bundle['formal_ranked_pool'] = ranked
        bundle['full_candidate_pool'] = ranked
        bundle['scored_candidates'] = ranked
        bundle['paper_scoring_candidates'] = ranked
        bundle['candidate'] = ranked[0] if ranked else {}
        if isinstance(bundle.get('daily_ticket_search_result'), dict):
            bundle['daily_ticket_search_result']['production_selection_source'] = 'formal_ranked_full_pool'
        return bundle
    source = bundle.get('full_candidate_pool') or bundle.get('paper_scoring_candidates') or []
    source_rows = [dict(row) for row in source if isinstance(row, dict) and symbol_for(row)]
    if not source_rows:
        bundle['formal_ranked_pool'] = []
        bundle['paper_scoring_candidates'] = []
        bundle['formal_rank_snapshot_validation'] = {
            'valid': True,
            'errors': [],
            'snapshot_ids': [],
            'snapshot_version': FORMAL_RANK_SNAPSHOT_VERSION,
        }
        return bundle

    ranked = apply_formal_profit_ranks(source_rows)
    for row in ranked:
        if safe_float(row.get('one_lot_cost')) is None:
            price = safe_float(
                row.get('price')
                if row.get('price') is not None
                else row.get('close')
                if row.get('close') is not None
                else row.get('close_price')
            )
            if price is not None and price > 0:
                row['one_lot_cost'] = round(price * 100.0, 4)
        eligibility = row.get('paper_pick_eligibility')
        signals = eligibility.get('signals') if isinstance(eligibility, dict) else {}
        if isinstance(signals, dict):
            if row.get('one_lot_cost') is None and signals.get('one_lot_cost') is not None:
                row['one_lot_cost'] = signals.get('one_lot_cost')
            if row.get('one_lot_cost_cap') is None and signals.get('one_lot_cost_cap') is not None:
                row['one_lot_cost_cap'] = signals.get('one_lot_cost_cap')
    snapshot_id = str(ranked[0].get('formal_rank_snapshot_id') or '')
    bundle['formal_ranked_pool'] = ranked
    bundle['full_candidate_pool'] = ranked
    bundle['scored_candidates'] = ranked
    # Layered search remains diagnostic. The official evaluator consumes the
    # complete formal pool so a lower layer cannot hide a higher-ranked row.
    bundle['paper_scoring_candidates'] = ranked
    bundle['candidate'] = ranked[0] if ranked else {}
    bundle['formal_rank_snapshot_id'] = snapshot_id
    bundle['formal_rank_snapshot_version'] = FORMAL_RANK_SNAPSHOT_VERSION
    bundle['formal_rank_snapshot_validation'] = validate_formal_rank_snapshot(ranked)
    if isinstance(bundle.get('daily_ticket_search_result'), dict):
        bundle['daily_ticket_search_result']['production_selection_source'] = 'formal_ranked_full_pool'
    return bundle


for _name, _value in tuple(globals().items()):
    if (
        callable(_value)
        and getattr(_value, '__module__', None) == __name__
        and _name not in {'bind_host', '_inject_host', '_with_host'}
    ):
        globals()[_name] = _with_host(_value)
