#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only forward result filler."""
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xiaogu_utils import (
    PRODUCTION_RETURN_FIELD,
    PRODUCTION_TRADE_MODE,
    append_jsonl,
    corrected_decision_ids,
    decision_record_id,
    decision_symbol,
    dump_json,
    has_decision_payload,
    is_active_decision_record,
    load_jsonl,
    now_iso,
    read_json,
    superseded_decision_keys,
)

BASE = Path(__file__).resolve().parent
FORWARD_LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
EVIDENCE_BASE = BASE / 'data' / 'forward_result_evidence'
EVIDENCE_ROOT = EVIDENCE_BASE / 'eastmoney'
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FILLABLE_FIELDS = [PRODUCTION_RETURN_FIELD, 'result_status', 'result_filled_at', 'post_result_locked']
IMMUTABLE_FIELDS = ['decision_reason', 'features_used', 'rule_version', 'generated_at', 'asof_time', 'raw_data_snapshot_path']
VALID_HORIZONS = {'t1': PRODUCTION_RETURN_FIELD}
HORIZON_INDEX = {'t1': 1}
EXECUTION_MODEL_VERSION = 'shadow_execution_v0_1'
EXECUTION_MODE = 'EXPLICIT'
EXECUTION_SEMANTICS = 'T_DAY_CLOSE_REFERENCE_T1_CLOSE_EXIT'
CANONICAL_PRICE_BASIS = 'UNADJUSTED_DAILY_OHLC'
CANONICAL_LABEL_VERSION = 'canonical_t1_v1'
CANONICAL_MARKET_DATA_SOURCE = 'eastmoney_push2his_daily_kline_fqt0'
CANONICAL_T1_LABEL_FIELDS = (
    't1_open_return',
    't1_high_return',
    't1_low_return',
    't1_close_return',
    't1_mfe',
    't1_mae',
)
CANONICAL_T1_EXTENDED_FIELDS = (
    't1_vwap_return',
    't1_gap_return',
    't1_net_return',
    'execution_price',
    'slippage',
    'commission',
    'stamp_duty',
    'transfer_fee',
    'market_impact',
)
EXECUTION_DEFAULTS = {
    'share_lot': 100,
    'buy_slippage': 0.0010,
    'sell_slippage': 0.0010,
    'impact_cost': 0.0005,
    'commission_rate': 0.0003,
    'minimum_commission': 5.0,
    'stamp_duty_rate': 0.0005,
    'transfer_fee_rate': 0.00001,
}


def fnum(value: Any) -> Optional[float]:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace('%', '').replace(',', ''))
    except (TypeError, ValueError):
        return None


def _candidate_features(decision: Dict[str, Any]) -> Dict[str, Any]:
    features_used = decision.get('features_used')
    if not isinstance(features_used, dict):
        return {}
    features = features_used.get('candidate_features')
    return features if isinstance(features, dict) else {}


def build_execution_contract(
    decision: Dict[str, Any],
    *,
    entry_price_override: Optional[float] = None,
    entry_price_source: Optional[str] = None,
    entry_price_basis: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve one explicit T-day paper execution contract.

    Production snapshots must persist ``execution_contract.execution_price``.
    Historical rows without that contract are migration candidates only; they
    are never silently converted into a valid production entry.
    """
    features = _candidate_features(decision)
    recorded = decision.get('execution_contract')
    recorded = recorded if isinstance(recorded, dict) else {}
    parsed_recorded = fnum(recorded.get('execution_price'))
    parsed_recorded_entry = fnum(recorded.get('entry_price'))
    parsed_feature_entry = fnum(features.get('entry_price'))
    parsed_override = fnum(entry_price_override)
    errors: List[str] = []
    recorded_prices = [
        value for value in (parsed_recorded, parsed_recorded_entry)
        if value is not None
    ]
    if len(recorded_prices) == 2 and recorded_prices[0] != recorded_prices[1]:
        errors.append('EXECUTION_PRICE_CONFLICT')
    if (
        parsed_feature_entry is not None
        and recorded_prices
        and any(abs(parsed_feature_entry - value) > 1e-9 for value in recorded_prices)
    ):
        errors.append('ENTRY_PRICE_CONFLICT')
    if entry_price_override is not None:
        if entry_price_source in (None, ''):
            errors.append('ENTRY_PRICE_SOURCE_REQUIRED')
        if entry_price_basis in (None, ''):
            errors.append('ENTRY_PRICE_BASIS_REQUIRED')
    if parsed_override is not None and any(
        abs(parsed_override - value) > 1e-9 for value in recorded_prices
    ):
        errors.append('ENTRY_PRICE_OVERRIDE_CONFLICT')
    execution_price = (
        parsed_override
        if entry_price_override is not None
        else recorded_prices[0] if recorded_prices else None
    )
    source = (
        entry_price_source
        or recorded.get('entry_price_source')
        or ('execution_contract.execution_price' if parsed_recorded is not None else None)
    )
    signal_date = str(decision.get('date') or recorded.get('signal_date') or '')[:10]
    signal_time = str(
        decision.get('asof_time')
        or features.get('source_time')
        or recorded.get('signal_time')
        or ''
    )
    if not source:
        errors.append('ENTRY_PRICE_SOURCE_MISSING')
    if not recorded_prices and entry_price_override is None:
        errors.append('EXECUTION_CONTRACT_REQUIRED')
    if not signal_date:
        errors.append('SIGNAL_DATE_MISSING')
    if execution_price is None or execution_price <= 0:
        errors.append('ENTRY_PRICE_MISSING')
    status = 'VALID' if not errors else 'INVALID'
    return {
        'signal_date': signal_date,
        'signal_time': signal_time,
        'signal_price': execution_price,
        'execution_mode': recorded.get('execution_mode') or EXECUTION_MODE,
        'execution_semantics': recorded.get('execution_semantics') or EXECUTION_SEMANTICS,
        'execution_time': recorded.get('execution_time') or signal_time,
        'execution_price': execution_price,
        'entry_price': execution_price,
        'entry_source': source,
        'entry_price_source': source,
        'price_basis': (
            entry_price_basis
            or recorded.get('price_basis')
            or CANONICAL_PRICE_BASIS
        ),
        'contract_provenance': (
            'EXPLICIT_OVERRIDE'
            if entry_price_override is not None
            else 'RECORDED_EXECUTION_CONTRACT'
            if recorded_prices
            else 'MIGRATION_REQUIRED'
        ),
        'status': status,
        'errors': list(dict.fromkeys(errors)),
    }


def resolve_canonical_entry_price(decision: Dict[str, Any]) -> Optional[float]:
    contract = build_execution_contract(decision)
    return fnum(contract.get('execution_price')) if contract.get('status') == 'VALID' else None


def entry_price(decision: Dict[str, Any]) -> Optional[float]:
    """Compatibility wrapper for callers; all resolution is canonical."""
    return resolve_canonical_entry_price(decision)


def calculate_t1_labels(
    entry_price_value: Optional[float],
    t1_row: Optional[Dict[str, Any]],
    *,
    previous_row: Optional[Dict[str, Any]] = None,
    execution_profile: Optional[Dict[str, Any]] = None,
    include_extended: bool = False,
) -> Dict[str, Any]:
    """Calculate canonical T+1 labels from T+1 OHLC and explicit execution data.

    The default six-field shape is retained for old callers. Production
    settlement paths pass ``include_extended=True`` so research receives the
    richer target without introducing a second label implementation.
    """
    entry = fnum(entry_price_value)
    row = t1_row if isinstance(t1_row, dict) else {}
    previous = previous_row if isinstance(previous_row, dict) else {}

    def return_from_price(value: Any) -> Optional[float]:
        parsed = fnum(value)
        if parsed is None or entry in (None, 0):
            return None
        return round((parsed - float(entry)) / float(entry), 6)

    labels = {
        't1_open_return': return_from_price(row.get('open')),
        't1_high_return': return_from_price(row.get('high')),
        't1_low_return': return_from_price(row.get('low')),
        't1_close_return': return_from_price(row.get('close')),
    }
    labels['t1_mfe'] = (
        round((max(fnum(row.get(key)) for key in ('open', 'high', 'close')) - float(entry)) / float(entry), 6)
        if entry not in (None, 0) and all(fnum(row.get(key)) is not None for key in ('open', 'high', 'close'))
        else None
    )
    labels['t1_mae'] = (
        round((min(fnum(row.get(key)) for key in ('open', 'low', 'close')) - float(entry)) / float(entry), 6)
        if entry not in (None, 0) and all(fnum(row.get(key)) is not None for key in ('open', 'low', 'close'))
        else None
    )
    labels['label_status'] = (
        'SETTLED'
        if entry is not None and entry > 0 and all(labels.get(key) is not None for key in CANONICAL_T1_LABEL_FIELDS)
        else 'INVALID'
    )
    if include_extended:
        vwap = fnum(row.get('vwap')) or fnum(row.get('average_price'))
        if vwap is None:
            volume = fnum(row.get('volume'))
            amount = fnum(row.get('amount'))
            if volume not in (None, 0) and amount is not None:
                vwap = amount / volume
        previous_close = (
            fnum(row.get('previous_close'))
            or fnum(row.get('pre_close'))
            or fnum(previous.get('close'))
        )
        labels['t1_vwap_return'] = return_from_price(vwap)
        labels['t1_gap_return'] = (
            round((fnum(row.get('open')) - previous_close) / previous_close, 6)
            if fnum(row.get('open')) is not None and previous_close not in (None, 0)
            else None
        )
        profile = execution_profile if isinstance(execution_profile, dict) else {}
        labels['t1_net_return'] = fnum(profile.get('net_return'))
        labels['execution_price'] = entry
        labels['slippage'] = (
            fnum(profile.get('buy_slippage')) + fnum(profile.get('sell_slippage'))
            if fnum(profile.get('buy_slippage')) is not None
            and fnum(profile.get('sell_slippage')) is not None
            else None
        )
        labels['commission'] = fnum(profile.get('commission'))
        labels['stamp_duty'] = fnum(profile.get('stamp_duty'))
        labels['transfer_fee'] = fnum(profile.get('transfer_fee'))
        labels['market_impact'] = fnum(profile.get('impact_cost'))
    return labels


def target_quality_gate(
    execution_contract: Dict[str, Any],
    t1_row: Optional[Dict[str, Any]],
    labels: Dict[str, Any],
) -> Dict[str, Any]:
    row = t1_row if isinstance(t1_row, dict) else {}
    errors = []
    if execution_contract.get('status') != 'VALID':
        errors.append('INVALID_EXECUTION_CONTRACT')
    if execution_contract.get('execution_mode') != EXECUTION_MODE:
        errors.append('EXECUTION_MODE_NOT_EXPLICIT')
    if execution_contract.get('price_basis') != CANONICAL_PRICE_BASIS:
        errors.append('PRICE_BASIS_MISMATCH')
    if labels.get('label_status') != 'SETTLED':
        errors.append('T1_LABELS_INCOMPLETE')
    open_price, high_price, low_price, close_price = (
        fnum(row.get('open')), fnum(row.get('high')), fnum(row.get('low')), fnum(row.get('close'))
    )
    if high_price is not None and open_price is not None and high_price < open_price:
        errors.append('T1_HIGH_BELOW_OPEN')
    if high_price is not None and close_price is not None and high_price < close_price:
        errors.append('T1_HIGH_BELOW_CLOSE')
    if low_price is not None and open_price is not None and low_price > open_price:
        errors.append('T1_LOW_ABOVE_OPEN')
    if low_price is not None and close_price is not None and low_price > close_price:
        errors.append('T1_LOW_ABOVE_CLOSE')
    return {'status': 'PASS' if not errors else 'REJECT', 'errors': errors}


def target_dataset_quality_report(
    rows: List[Dict[str, Any]],
    *,
    minimum_coverage: float = 0.95,
) -> Dict[str, Any]:
    """Summarize target coverage without converting missing values to zero."""
    total = len(rows or [])
    fields = CANONICAL_T1_LABEL_FIELDS + CANONICAL_T1_EXTENDED_FIELDS
    coverage = {
        field: round(
            sum(row.get(field) is not None for row in rows or []) / total,
            4,
        ) if total else 0.0
        for field in fields
    }
    statuses = [
        str(row.get('label_status') or row.get('return_status') or 'UNKNOWN').upper()
        for row in rows or []
    ]
    status_counts = {
        status: statuses.count(status)
        for status in sorted(set(statuses))
    }
    invalid = sum(status not in {'SETTLED', 'PASS'} for status in statuses)
    unknown = sum(status in {'UNKNOWN', ''} for status in statuses)
    not_fillable = sum(status in {'NOT_FILLABLE', 'T1_NOT_SELLABLE'} for status in statuses)
    pending = sum(status in {'PENDING', 'NO_EXIT_ROW_AVAILABLE', 'NO_KLINE_ROWS'} for status in statuses)
    # A row without the cost-adjusted target is not trainable even when its
    # OHLC/MFE/MAE diagnostics are complete. Keep it in the report so the
    # missing-target reason remains observable instead of becoming zero.
    core_fields = CANONICAL_T1_LABEL_FIELDS + ('t1_net_return',)
    core_coverage = {field: coverage[field] for field in core_fields}
    passed = bool(total) and not invalid and all(
        value >= minimum_coverage for value in core_coverage.values()
    )
    return {
        'status': 'READY' if passed else 'TARGET_NOT_READY',
        'total_rows': total,
        'invalid_rows': invalid,
        'unknown_rows': unknown,
        'not_fillable_rows': not_fillable,
        'pending_rows': pending,
        'status_counts': status_counts,
        'minimum_coverage': minimum_coverage,
        'coverage': coverage,
        'core_coverage': core_coverage,
        'extended_fields_are_diagnostic': True,
    }


def _market_limit_percent(decision: Dict[str, Any]) -> float:
    features = _candidate_features(decision)
    explicit = fnum(
        features.get('limit_percent')
        or features.get('price_limit_pct')
        or decision.get('limit_percent')
    )
    if explicit is not None and 0 < explicit <= 30:
        return explicit
    if bool(features.get('is_st') or features.get('st')) or 'ST' in str(
        features.get('name') or decision.get('name') or ''
    ).upper():
        return 5.0
    symbol = decision_symbol(decision)
    if symbol.startswith(('300', '301', '688', '689')):
        return 20.0
    return 10.0


def _has_trade_block(value: Any) -> bool:
    text = str(value or '').strip().upper()
    return text in {
        'SUSPENDED',
        'HALTED',
        '停牌',
        '临停',
        '退市整理',
        'RISK_WARNING',
    }


def _locked_limit_state(
    row: Optional[Dict[str, Any]],
    decision: Dict[str, Any],
    *,
    direction: str,
) -> bool:
    if not isinstance(row, dict):
        return False
    features = _candidate_features(decision)
    if direction == 'buy' and any(
        bool(features.get(key) or decision.get(key))
        for key in ('sealed_limit_up', 'is_sealed_limit_up', 'limit_up_sealed')
    ):
        return True
    if direction == 'sell' and any(
        bool(row.get(key))
        for key in ('sealed_limit_down', 'is_sealed_limit_down', 'limit_down_sealed')
    ):
        return True
    pct = fnum(row.get('pct_chg'))
    if pct is None:
        return False
    limit_pct = _market_limit_percent(decision)
    at_limit = abs(abs(pct) - limit_pct) <= 0.25
    high = fnum(row.get('high'))
    low = fnum(row.get('low'))
    close = fnum(row.get('close'))
    if not at_limit or high is None or low is None or close is None:
        return False
    locked = max(abs(high - close), abs(close - low)) <= max(0.01, close * 0.0005)
    return locked and ((direction == 'buy' and pct >= 0) or (direction == 'sell' and pct <= 0))


def _round_money(value: Optional[float]) -> Optional[float]:
    return round(float(value), 8) if value is not None else None


def price_source_consistency(
    source_prices: Any,
    *,
    tolerance: float = 0.005,
) -> Dict[str, Any]:
    """Compare same-basis prices from available providers without guessing."""
    if not isinstance(source_prices, dict):
        return {
            'status': 'NOT_AVAILABLE',
            'provider_count': 0,
            'max_relative_difference': None,
            'conflict': False,
        }
    values = {
        str(provider): fnum(value)
        for provider, value in source_prices.items()
        if fnum(value) is not None and fnum(value) > 0
    }
    if len(values) < 2:
        return {
            'status': 'NOT_AVAILABLE',
            'provider_count': len(values),
            'providers': sorted(values),
            'max_relative_difference': None,
            'conflict': False,
        }
    prices = list(values.values())
    baseline = max(prices)
    relative_difference = (max(prices) - min(prices)) / baseline if baseline else 0.0
    conflict = relative_difference > max(0.0, float(tolerance))
    return {
        'status': 'CONFLICT' if conflict else 'PASS',
        'provider_count': len(values),
        'providers': sorted(values),
        'max_relative_difference': _round_money(relative_difference),
        'tolerance': tolerance,
        'conflict': conflict,
    }


def shadow_execution_profile(
    decision: Dict[str, Any],
    exit_row: Optional[Dict[str, Any]] = None,
    previous_row: Optional[Dict[str, Any]] = None,
    *,
    gross_return: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a conservative, auditable execution shadow without broker access."""
    features = _candidate_features(decision)
    source_consistency = price_source_consistency(
        features.get('price_sources')
        or decision.get('price_sources')
    )
    entry_reference = entry_price(decision)
    exit_reference = fnum(exit_row.get('close')) if isinstance(exit_row, dict) else None
    entry_blocked = any(
        _has_trade_block(features.get(key) or decision.get(key))
        for key in ('trading_status', 'status', 'suspension_status')
    )
    exit_blocked = _has_trade_block(
        (exit_row or {}).get('trading_status')
        or (exit_row or {}).get('status')
        or (exit_row or {}).get('suspension_status')
    )

    if entry_reference is None:
        entry_status = 'UNKNOWN'
        entry_reason = 'ENTRY_REFERENCE_PRICE_MISSING'
    elif entry_blocked or _locked_limit_state(features, decision, direction='buy'):
        entry_status = 'NOT_FILLABLE'
        entry_reason = 'ENTRY_SUSPENDED_OR_LOCKED_LIMIT_UP'
    else:
        entry_status = 'FILLED'
        entry_reason = 'CONSERVATIVE_T_DAY_REFERENCE_FILL'

    if exit_reference is None:
        exit_status = 'UNKNOWN'
        exit_reason = 'EXIT_REFERENCE_CLOSE_MISSING'
    elif exit_blocked:
        exit_status = 'NOT_FILLABLE'
        exit_reason = 'EXIT_SUSPENDED'
    elif _locked_limit_state(exit_row, decision, direction='sell'):
        exit_status = 'NOT_FILLABLE'
        exit_reason = 'EXIT_LOCKED_LIMIT_DOWN'
    else:
        exit_status = 'FILLED'
        exit_reason = 'CONSERVATIVE_T1_FINAL_CLOSE_FILL'

    limit_percent = _market_limit_percent(decision)
    if entry_status == 'NOT_FILLABLE':
        entry_trade_state = 'T_DAY_NOT_BUYABLE'
    elif entry_status == 'UNKNOWN':
        entry_trade_state = 'T_DAY_BUYABILITY_UNKNOWN'
    else:
        entry_trade_state = 'T_DAY_BUYABLE'
    if exit_status == 'NOT_FILLABLE':
        exit_trade_state = 'T1_NOT_SELLABLE'
    elif exit_status == 'UNKNOWN':
        exit_trade_state = 'T1_SELLABILITY_UNKNOWN'
    else:
        exit_trade_state = 'T1_SELLABLE'

    entry_ratio = fnum(features.get('entry_fill_ratio'))
    exit_ratio = fnum((exit_row or {}).get('exit_fill_ratio'))
    if entry_status == 'FILLED' and entry_ratio is not None and 0 < entry_ratio < 1:
        entry_status = 'PARTIAL'
        entry_reason = 'EXPLICIT_ENTRY_FILL_RATIO'
        entry_trade_state = 'T_DAY_PARTIAL_FILL'
    if exit_status == 'FILLED' and exit_ratio is not None and 0 < exit_ratio < 1:
        exit_status = 'PARTIAL'
        exit_reason = 'EXPLICIT_EXIT_FILL_RATIO'
        exit_trade_state = 'T1_PARTIAL_FILL'
    entry_ratio = (
        max(0.0, min(1.0, entry_ratio))
        if entry_ratio is not None
        else (1.0 if entry_status == 'FILLED' else 0.0 if entry_status == 'NOT_FILLABLE' else None)
    )
    exit_ratio = (
        max(0.0, min(1.0, exit_ratio))
        if exit_ratio is not None
        else (1.0 if exit_status == 'FILLED' else 0.0 if exit_status == 'NOT_FILLABLE' else None)
    )
    statuses = (entry_status, exit_status)
    if 'NOT_FILLABLE' in statuses:
        execution_status = 'NOT_FILLABLE'
    elif 'UNKNOWN' in statuses:
        execution_status = 'UNKNOWN'
    elif 'PARTIAL' in statuses:
        execution_status = 'PARTIAL'
    else:
        execution_status = 'FILLED'

    defaults = EXECUTION_DEFAULTS
    buy_slippage = max(0.0, fnum(features.get('buy_slippage')) or defaults['buy_slippage'])
    sell_slippage = max(0.0, fnum(features.get('sell_slippage')) or defaults['sell_slippage'])
    impact_cost = max(0.0, fnum(features.get('impact_cost')) or defaults['impact_cost'])
    share_lot = max(100, int(fnum(features.get('share_lot')) or defaults['share_lot']))
    buy_exec = (
        entry_reference * (1.0 + buy_slippage + impact_cost)
        if entry_reference is not None and entry_status in {'FILLED', 'PARTIAL'}
        else None
    )
    sell_exec = (
        exit_reference * (1.0 - sell_slippage - impact_cost)
        if exit_reference is not None and exit_status in {'FILLED', 'PARTIAL'}
        else None
    )
    buy_notional = buy_exec * share_lot if buy_exec is not None else None
    sell_notional = sell_exec * share_lot if sell_exec is not None else None
    commission = (
        max(defaults['minimum_commission'], buy_notional * defaults['commission_rate'])
        + max(defaults['minimum_commission'], sell_notional * defaults['commission_rate'])
        if buy_notional is not None and sell_notional is not None
        else None
    )
    stamp_duty = sell_notional * defaults['stamp_duty_rate'] if sell_notional is not None else None
    shanghai = decision_symbol(decision).startswith(('600', '601', '603', '605'))
    transfer_fee = (
        (buy_notional + sell_notional) * defaults['transfer_fee_rate']
        if buy_notional is not None and sell_notional is not None and shanghai
        else 0.0 if sell_notional is not None else None
    )
    total_cost = (
        (commission or 0.0) + (stamp_duty or 0.0) + (transfer_fee or 0.0)
        if commission is not None
        else None
    )
    net_return = None
    if (
        total_cost is not None
        and buy_notional
        and sell_notional is not None
        and execution_status in {'FILLED', 'PARTIAL'}
    ):
        net_return = (sell_notional - buy_notional - total_cost) / buy_notional
    exit_low = fnum((exit_row or {}).get('low'))
    worst_sell = (
        exit_low * (1.0 - sell_slippage - impact_cost)
        if exit_low is not None and exit_status in {'FILLED', 'PARTIAL'}
        else None
    )
    worst_return = None
    if worst_sell is not None and buy_notional:
        worst_sell_notional = worst_sell * share_lot
        worst_cost = (
            max(defaults['minimum_commission'], worst_sell_notional * defaults['commission_rate'])
            + worst_sell_notional * defaults['stamp_duty_rate']
            + ((buy_notional + worst_sell_notional) * defaults['transfer_fee_rate'] if shanghai else 0.0)
        )
        worst_return = (worst_sell_notional - buy_notional - worst_cost) / buy_notional

    return {
        'model_version': EXECUTION_MODEL_VERSION,
        'entry_reference_price': _round_money(entry_reference),
        'entry_execution_price': _round_money(buy_exec),
        'entry_execution_policy': entry_reason,
        'entry_fill_status': entry_status,
        'entry_fill_probability': entry_ratio,
        'next_day_exit_reference_price': _round_money(exit_reference),
        'next_day_exit_execution_price': _round_money(sell_exec),
        'exit_execution_policy': exit_reason,
        'exit_fill_status': exit_status,
        'exit_fill_probability': exit_ratio,
        'execution_status': execution_status,
        'execution_statistics_eligible': execution_status == 'FILLED',
        'entry_trade_state': entry_trade_state,
        'exit_trade_state': exit_trade_state,
        't_plus_one_sell_restriction': 'T_DAY_BUY_NOT_SELLABLE_UNTIL_T1',
        'board_limit_percent': limit_percent,
        'board_rule_source': 'symbol_and_candidate_features',
        'share_lot_constraint': 'MINIMUM_100_SHARES',
        'buy_slippage': buy_slippage,
        'sell_slippage': sell_slippage,
        'commission': _round_money(commission),
        'stamp_duty': _round_money(stamp_duty),
        'transfer_fee': _round_money(transfer_fee),
        'impact_cost': impact_cost,
        'gross_return': _round_money(gross_return),
        'net_return': _round_money(net_return),
        'worst_case_return': _round_money(worst_return),
        'price_basis': 'unadjusted',
        'share_lot': share_lot,
        'not_fillable_is_not_loss': True,
        'unknown_is_not_silently_excluded': True,
        'previous_close': _round_money(fnum((previous_row or {}).get('close'))),
        'price_basis_consistent': source_consistency.get('status') != 'CONFLICT',
        'price_source_consistency': source_consistency,
    }



def cached_kline_return(decision: Dict[str, Any], horizon: str) -> Tuple[Optional[float], Dict[str, Any]]:
    symbol = decision_symbol(decision)
    price = entry_price(decision)
    if not symbol or price is None:
        return None, {'status': 'NO_SYMBOL_OR_ENTRY_PRICE'}
    try:
        begin, end = trading_window(decision['date'])
    except (TypeError, ValueError) as exc:
        return None, {'status': 'DECISION_DATE_INVALID', 'date': decision.get('date'), 'error': repr(exc)}
    evidence_path = EVIDENCE_ROOT / decision['date'] / f'{symbol}_{begin}_{end}.json'
    source_name = CANONICAL_MARKET_DATA_SOURCE
    if not evidence_path.exists():
        return None, {'status': 'NO_LOCAL_KLINE_EVIDENCE', 'source': source_name, 'evidence_path': str(evidence_path)}
    try:
        cached = read_json(evidence_path)
    except Exception as exc:
        return None, {'status': 'LOCAL_KLINE_EVIDENCE_READ_FAILED', 'source': source_name, 'evidence_path': str(evidence_path), 'error': repr(exc)}
    payload = cached.get('payload') if isinstance(cached.get('payload'), dict) else cached
    rows = parse_klines(payload or {})
    if not rows:
        return None, {'status': 'NO_KLINE_ROWS', 'source': source_name, 'evidence_path': str(evidence_path), 'provider_error': (payload or {}).get('error')}
    final_status = local_kline_cache_final_status(cached, evidence_path, decision, horizon, rows)
    if final_status.get('status') != 'PASS':
        return None, final_status
    ret, evidence = return_from_rows(decision, horizon, rows, evidence_path, payload or {}, source_name)
    if ret is None:
        return None, evidence
    evidence['cache_hit'] = True
    evidence['cache_source'] = source_name
    return ret, evidence


def find_decision(rows: List[Dict[str, Any]], date: str, symbol: str) -> Dict[str, Any]:
    norm = str(symbol).zfill(6)
    corrected_ids = corrected_decision_ids(rows)
    superseded = superseded_decision_keys(rows)
    matches = [
        r for r in rows
        if is_active_decision_record(r, corrected_ids, superseded)
        and r.get('date') == date
        and decision_symbol(r) == norm
    ]
    if not matches:
        raise SystemExit(f'no active decision record found for date={date} symbol={symbol}')
    return matches[-1]


def pending_decisions(rows: List[Dict[str, Any]], include_research: bool = False, horizon: Optional[str] = None) -> List[Dict[str, Any]]:
    decisions = []
    # Research/watch outputs are diagnostics only and never part of the
    # production return lifecycle.
    del include_research
    allowed = {'PAPER_PICK'}
    corrected_ids = corrected_decision_ids(rows)
    superseded = superseded_decision_keys(rows)
    for row in rows:
        if not is_active_decision_record(row, corrected_ids, superseded):
            continue
        if row.get('decision') not in allowed:
            continue
        symbol = decision_symbol(row)
        if not symbol:
            continue
        # entry_price check moved to fill time (can fetch from market data)
        if horizon:
            fills = existing_fills(rows, row.get('date'), symbol)
            state = merged_result_state(row, fills)
            if state.get(VALID_HORIZONS[horizon]) is not None:
                continue
        decisions.append(row)
    return decisions


def is_result_fill_record(row: Dict[str, Any]) -> bool:
    return row.get('record_type') == 'RESULT_FILL' or (row.get('record_type') == 'CORRECTION' and not row.get('decision'))


def existing_fills(rows: List[Dict[str, Any]], date: str, symbol: str) -> List[Dict[str, Any]]:
    norm = str(symbol).zfill(6)
    return [r for r in rows if is_result_fill_record(r) and r.get('date') == date and decision_symbol(r) == norm]


def merged_result_state(decision: Dict[str, Any], fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    state = {k: decision.get(k) for k in FILLABLE_FIELDS}
    for fill in fills:
        for k in fill.get('clear_result_fields') or []:
            if k == PRODUCTION_RETURN_FIELD:
                state[k] = None
        for k in [PRODUCTION_RETURN_FIELD]:
            if fill.get(k) is not None:
                state[k] = fill.get(k)
        state['result_status'] = fill.get('result_status', state.get('result_status'))
        state['result_filled_at'] = fill.get('result_filled_at', state.get('result_filled_at'))
        state['post_result_locked'] = fill.get('post_result_locked', state.get('post_result_locked'))
    return state


def status_from_state(state: Dict[str, Any]) -> str:
    if state.get(PRODUCTION_RETURN_FIELD) is not None:
        return 'T1_FILLED'
    return 'PENDING'


def secid_for(symbol: str) -> str:
    code = str(symbol).zfill(6)
    market = '1' if code.startswith(('600', '601', '603', '605', '688', '689')) else '0'
    return f'{market}.{code}'



def fetch_eastmoney_klines(
    symbol: str,
    begin: str,
    end: str,
    retries: int = 2,
    timeout_seconds: int = 20,
) -> Dict[str, Any]:
    params = '&'.join([
        'secid=' + secid_for(symbol),
        'fields1=f1,f2,f3,f4,f5,f6',
        'fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt=101',
        'fqt=0',
        'beg=' + begin.replace('-', ''),
        'end=' + end.replace('-', ''),
    ])
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + params
    last_error = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 XiaoguForwardResult/0.1', 'Referer': 'https://quote.eastmoney.com/'})
            with DIRECT_OPENER.open(req, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
            payload['_request_url'] = url
            return payload
        except Exception as exc:
            last_error = repr(exc)
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return {'error': last_error, '_request_url': url, 'data': None}


def fetch_realtime_final_bar(symbol: str) -> Optional[Dict[str, Any]]:
    """Official same-day OHLC after market close when history kline lags.

    Only when exit_date == today and daily_kline_is_final. Never invents past bars.
    Use only the Eastmoney push2 quote endpoint.
    """
    code = str(symbol).zfill(6)
    today = dt.date.today().isoformat()
    if not daily_kline_is_final(today):
        return None

    def _bar(open_p, high, low, close, *, source: str, url: str, volume: float = 0.0, amount: float = 0.0, pct_chg: float = 0.0) -> Optional[Dict[str, Any]]:
        if close is None or close <= 0:
            return None
        high = high if high is not None and high >= close else close
        low = low if low is not None and low <= close else close
        open_p = open_p if open_p is not None else close
        return {
            'date': today,
            'open': open_p,
            'close': close,
            'high': high,
            'low': low,
            'volume': volume,
            'amount': amount,
            'amplitude_pct': 0.0,
            'pct_chg': pct_chg,
            'chg': 0.0,
            'turnover_rate': 0.0,
            'row_source': source,
            'source_time': now_iso(),
            '_request_url': url,
        }

    # Eastmoney push2: f43 price*100, f44 high, f45 low, f46 open, f170 pct*100
    try:
        url = (
            f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid_for(code)}'
            f'&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f170'
        )
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 XiaoguForwardResult/0.1', 'Referer': 'https://quote.eastmoney.com/'},
        )
        with DIRECT_OPENER.open(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        data = payload.get('data') or {}

        def _p100(v: Any) -> Optional[float]:
            n = fnum(v)
            return (n / 100.0) if n is not None else None

        bar = _bar(
            _p100(data.get('f46')),
            _p100(data.get('f44')),
            _p100(data.get('f45')),
            _p100(data.get('f43')),
            source='eastmoney_push2_realtime_final',
            url=url,
            volume=float(fnum(data.get('f47')) or 0.0),
            amount=float(fnum(data.get('f48')) or 0.0),
            pct_chg=float(fnum(data.get('f170')) or 0.0) / 100.0 if data.get('f170') is not None else 0.0,
        )
        if bar:
            return bar
    except Exception:
        pass

    return None


def ensure_today_exit_bar(rows: List[Dict[str, Any]], symbol: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """If history kline lags today's final bar after 15:05, append realtime final close."""
    today = dt.date.today().isoformat()
    if not daily_kline_is_final(today):
        return rows, None
    if any(str(r.get('date')) == today for r in rows):
        return rows, None
    bar = fetch_realtime_final_bar(symbol)
    if not bar:
        return rows, None
    out = list(rows) + [{k: v for k, v in bar.items() if not str(k).startswith('_')}]
    return out, {
        'status': 'APPENDED_REALTIME_FINAL_BAR',
        'date': today,
        'row_source': bar.get('row_source'),
        'close': bar.get('close'),
        'high': bar.get('high'),
        'source_time': bar.get('source_time'),
        'request_url': bar.get('_request_url'),
        'note': 'history kline missing today bar; official realtime close used after 15:05 only',
    }


def fetch_canonical_daily_ohlc(
    symbol: str,
    begin: str,
    end: str,
    retries: int = 2,
) -> List[Dict[str, Any]]:
    """Fetch the single production T+1 market-data basis: Eastmoney fqt=0."""
    payload = fetch_eastmoney_klines(
        symbol,
        begin,
        end,
        retries=retries,
        timeout_seconds=8,
    )
    rows = parse_klines(payload)
    rows, _ = ensure_today_exit_bar(rows, symbol)
    return rows


def trading_window(date: str, days: int = 12) -> Tuple[str, str]:
    start = dt.date.fromisoformat(date) - dt.timedelta(days=1)
    end = dt.date.fromisoformat(date) + dt.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def parse_klines(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for raw in (payload.get('data') or {}).get('klines') or []:
        parts = str(raw).split(',')
        if len(parts) < 11:
            continue
        try:
            dt.date.fromisoformat(parts[0])
        except ValueError:
            continue
        values = [fnum(parts[i]) for i in range(1, 11)]
        if any(v is None for v in values):
            continue
        rows.append({
            'date': parts[0],
            'open': values[0],
            'close': values[1],
            'high': values[2],
            'low': values[3],
            'volume': values[4],
            'amount': values[5],
            'amplitude_pct': values[6],
            'pct_chg': values[7],
            'chg': values[8],
            'turnover_rate': values[9],
        })
    return rows



def parse_source_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return dt.datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None



def daily_kline_is_final(exit_date: str) -> bool:
    now = dt.datetime.now()
    date = dt.date.fromisoformat(exit_date)
    if date < now.date():
        return True
    if date > now.date():
        return False
    return now.time() >= dt.time(15, 5)


def local_kline_cache_final_status(cached: Dict[str, Any], evidence_path: Path, decision: Dict[str, Any], horizon: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    future_rows = [r for r in rows if r['date'] > decision['date']]
    idx = HORIZON_INDEX[horizon] - 1
    if len(future_rows) <= idx:
        return {'status': 'PASS'}
    exit_date = dt.date.fromisoformat(future_rows[idx]['date'])
    observed_at = parse_source_time(cached.get('fetched_at') or cached.get('observed_at'))
    observed_source = 'metadata'
    if observed_at is None:
        try:
            observed_at = dt.datetime.fromtimestamp(evidence_path.stat().st_mtime)
            observed_source = 'file_mtime'
        except OSError:
            observed_at = None
    if observed_at and (observed_at.date() > exit_date or (observed_at.date() == exit_date and observed_at.time() >= dt.time(15, 5))):
        return {'status': 'PASS'}
    return {
        'status': 'LOCAL_KLINE_CACHE_NOT_FINAL',
        'source': cached.get('source', 'local_kline_cache'),
        'evidence_path': str(evidence_path),
        'entry_date': decision['date'],
        'exit_date': exit_date.isoformat(),
        'cache_observed_at': observed_at.isoformat(timespec='seconds') if observed_at else None,
        'cache_observed_source': observed_source,
        'earliest_final_cache_time': exit_date.isoformat() + 'T15:05:00',
    }


def return_from_rows(decision: Dict[str, Any], horizon: str, rows: List[Dict[str, Any]], evidence_path: Path, payload: Dict[str, Any], source: str) -> Tuple[Optional[float], Dict[str, Any]]:
    symbol = decision_symbol(decision)
    execution_contract = build_execution_contract(decision)
    price = fnum(execution_contract.get('execution_price'))
    if execution_contract.get('status') != 'VALID':
        return None, {
            'status': 'INVALID_ENTRY_PRICE',
            'source': source,
            'evidence_path': str(evidence_path),
            'execution_contract': execution_contract,
        }
    if not rows:
        return None, {'status': 'NO_KLINE_ROWS', 'source': source, 'evidence_path': str(evidence_path), 'provider_error': payload.get('error')}
    future_rows = [r for r in rows if r['date'] > decision['date']]
    idx = HORIZON_INDEX[horizon] - 1
    if len(future_rows) <= idx:
        return None, {'status': 'NO_EXIT_ROW_AVAILABLE', 'source': source, 'evidence_path': str(evidence_path), 'rows': rows}
    exit_row = future_rows[idx]
    if not daily_kline_is_final(exit_row['date']):
        return None, {
            'status': 'EXIT_DAY_NOT_FINAL',
            'source': source,
            'evidence_path': str(evidence_path),
            'entry_date': decision['date'],
            'exit_date': exit_row['date'],
            'earliest_fill_time': exit_row['date'] + 'T15:05:00',
            'observed_at': now_iso(),
            'intraday_exit_high': exit_row.get('high'),
            'intraday_exit_close': exit_row.get('close'),
        }
    sell_price = exit_row.get('close')
    if sell_price in (None, ''):
        return None, {
            'status': 'NO_FINAL_CLOSE',
            'source': source,
            'evidence_path': str(evidence_path),
            'entry_date': decision['date'],
            'exit_date': exit_row['date'],
        }
    request_url = str(payload.get('_request_url', ''))
    adjustment = 'qfq' if 'fqt=1' in request_url or request_url.endswith(',qfq') else 'unadjusted'
    if adjustment != 'unadjusted':
        return None, {
            'status': 'PRICE_BASIS_MISMATCH',
            'source': source,
            'evidence_path': str(evidence_path),
            'entry_price_basis': 'snapshot_unadjusted_unknown',
            'exit_price_basis': adjustment,
            'required_action': 'REFETCH_UNADJUSTED_KLINE',
        }
    t1_metrics = calculate_t1_labels(price, exit_row)
    quality = target_quality_gate(execution_contract, exit_row, t1_metrics)
    if quality.get('status') != 'PASS':
        return None, {
            'status': 'T1_LABEL_QUALITY_REJECTED',
            'source': source,
            'evidence_path': str(evidence_path),
            'execution_contract': execution_contract,
            't1_metrics': t1_metrics,
            'quality_gate': quality,
        }
    ret = t1_metrics['t1_close_return']
    previous_rows = [r for r in rows if r.get('date') < exit_row.get('date')]
    previous_row = max(previous_rows, key=lambda r: r.get('date')) if previous_rows else None
    execution = shadow_execution_profile(
        decision,
        exit_row,
        previous_row,
        gross_return=ret,
    )
    t1_metrics = calculate_t1_labels(
        price,
        exit_row,
        previous_row=previous_row,
        execution_profile=execution,
        include_extended=True,
    )
    evidence = {
        'status': 'PASS',
        'source': CANONICAL_MARKET_DATA_SOURCE,
        'evidence_path': str(evidence_path),
        'entry_price': price,
        'entry_price_source': execution_contract.get('entry_price_source'),
        'entry_price_basis': execution_contract.get('price_basis'),
        'entry_date': decision['date'],
        'exit_date': exit_row['date'],
        'execution_contract': execution_contract,
        'exit_high': exit_row.get('high'),
        'exit_low': exit_row.get('low'),
        'exit_open': exit_row.get('open'),
        'exit_close': exit_row['close'],
        'return_formula': '(exit_close-entry_price)/entry_price',
        'kline_adjustment': adjustment,
        'price_basis': CANONICAL_PRICE_BASIS,
        'label_version': CANONICAL_LABEL_VERSION,
        'label_status': 'SETTLED',
        'market_data_source': CANONICAL_MARKET_DATA_SOURCE,
        'market_data_source_detail': source,
        'source_trade_date': exit_row.get('date'),
        'generated_at': now_iso(),
        't1_metrics': t1_metrics,
        't1_target_version': CANONICAL_LABEL_VERSION,
        't1_target_fields': list(CANONICAL_T1_LABEL_FIELDS + CANONICAL_T1_EXTENDED_FIELDS),
        'quality_gate': quality,
        'execution_model': execution,
        'klines_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }
    if exit_row.get('row_source'):
        evidence['exit_row_source'] = exit_row.get('row_source')
    if exit_row.get('source_time'):
        evidence['exit_source_time'] = exit_row.get('source_time')
    return ret, evidence


def auto_return(decision: Dict[str, Any], horizon: str) -> Tuple[Optional[float], Dict[str, Any]]:
    cached_ret, cached_evidence = cached_kline_return(decision, horizon)
    if cached_ret is not None:
        return cached_ret, cached_evidence
    symbol = decision_symbol(decision)
    price = entry_price(decision)
    if not symbol or price is None:
        return None, {'status': 'NO_SYMBOL_OR_ENTRY_PRICE'}
    try:
        begin, end = trading_window(decision['date'])
    except (TypeError, ValueError) as exc:
        return None, {'status': 'DECISION_DATE_INVALID', 'date': decision.get('date'), 'error': repr(exc)}
    payload = fetch_eastmoney_klines(symbol, begin, end)
    evidence_path = EVIDENCE_ROOT / decision['date'] / f'{symbol}_{begin}_{end}.json'
    rows = parse_klines(payload)
    source_name = CANONICAL_MARKET_DATA_SOURCE
    # After 15:05: history APIs can lag today's final bar; use the same
    # Eastmoney quote source to append its official final bar.
    rows, realtime_meta = ensure_today_exit_bar(rows, symbol)
    if realtime_meta:
        source_name = f"{source_name}+{realtime_meta.get('row_source')}"
        if isinstance(payload, dict):
            payload = dict(payload)
            payload['_realtime_final_bar'] = realtime_meta
    dump_json(evidence_path, {
        'symbol': symbol,
        'decision_date': decision['date'],
        'entry_price': price,
        'source': source_name,
        'fetched_at': now_iso(),
        'payload': payload,
        'rows_used': rows,
        'realtime_final_bar': realtime_meta,
    })
    return return_from_rows(decision, horizon, rows, evidence_path, payload, source_name)


def build_fill(rows: List[Dict[str, Any]], decision: Dict[str, Any], horizon: str, return_value: float, filled_at: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    symbol = decision_symbol(decision)
    fills = existing_fills(rows, decision['date'], symbol)
    state = merged_result_state(decision, fills)
    ret_field = VALID_HORIZONS[horizon]
    if state.get(ret_field) is not None:
        raise SystemExit(f'{decision["date"]} {symbol} {ret_field} already filled; append explicit correction record manually, do not overwrite')
    state[ret_field] = return_value
    state['result_status'] = status_from_state(state)
    state['result_filled_at'] = filled_at
    state['post_result_locked'] = True
    t1_metrics = evidence.get('t1_metrics') if isinstance(evidence, dict) else {}
    return {
        'record_type': 'RESULT_FILL',
        'date': decision['date'],
        'symbol': symbol,
        'production_run_id': decision.get('production_run_id'),
        'filled_horizon': horizon,
        PRODUCTION_RETURN_FIELD: state.get(PRODUCTION_RETURN_FIELD),
        'result_status': state.get('result_status'),
        'result_filled_at': filled_at,
        'post_result_locked': True,
        'decision_record_line': decision.get('_line'),
        'original_rule_version': decision.get('rule_version'),
        'original_decision_reason_sha256': hashlib.sha256(str(decision.get('decision_reason')).encode()).hexdigest(),
        'immutable_fields_not_modified': IMMUTABLE_FIELDS,
        'fillable_result_fields': FILLABLE_FIELDS,
        'result_source_evidence': evidence,
        't1_labels': dict(t1_metrics) if isinstance(t1_metrics, dict) else {},
        'execution_contract': dict(evidence.get('execution_contract') or {}) if isinstance(evidence, dict) else {},
        'production_trade_mode': PRODUCTION_TRADE_MODE,
        'production_return_field': PRODUCTION_RETURN_FIELD,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'append_only_policy': 'This RESULT_FILL is appended; original DECISION record is not rewritten.',
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='append forward result fill record without rewriting decision')
    ap.add_argument('--date')
    ap.add_argument('--symbol')
    ap.add_argument('--horizon', choices=sorted(VALID_HORIZONS), default='t1')
    ap.add_argument('--return-value', type=float, help='decimal return, e.g. 0.032 means +3.2%%')
    ap.add_argument('--filled-at', default=now_iso())
    ap.add_argument('--auto-eastmoney', action='store_true')
    ap.add_argument('--fill-all-pending', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--production-run-id', default='')
    args = ap.parse_args()

    rows = load_jsonl(FORWARD_LEDGER)
    if args.fill_all_pending:
        decisions = pending_decisions(rows, horizon=args.horizon)
    else:
        if not args.date or not args.symbol:
            raise SystemExit('--date and --symbol are required unless --fill-all-pending is used')
        decisions = [find_decision(rows, args.date, args.symbol)]

    fills_to_append = []
    skipped = []
    for decision in decisions:
        symbol = decision_symbol(decision)
        try:
            if args.auto_eastmoney:
                ret, evidence = auto_return(decision, args.horizon)
                if ret is None:
                    skipped.append({'date': decision['date'], 'symbol': symbol, 'reason': evidence})
                    continue
                return_value = ret
            else:
                if args.return_value is None:
                    raise SystemExit('--return-value is required without --auto-eastmoney')
                return_value = args.return_value
                evidence = {'status': 'MANUAL_INPUT', 'source': 'user_provided_return_value'}
            fills_to_append.append(build_fill(rows, decision, args.horizon, return_value, args.filled_at, evidence))
        except SystemExit as exc:
            skipped.append({'date': decision.get('date'), 'symbol': symbol, 'reason': str(exc)})

    out = {'would_append_ledger': str(FORWARD_LEDGER), 'fills': fills_to_append, 'skipped': skipped}
    if args.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    for fill in fills_to_append:
        embedded_run_id = str(fill.get('production_run_id') or '').strip()
        requested_run_id = str(args.production_run_id or '').strip()
        if embedded_run_id and requested_run_id and embedded_run_id != requested_run_id:
            raise SystemExit('RESULT_FILL_PRODUCTION_RUN_MISMATCH')
        if args.fill_all_pending and not embedded_run_id:
            raise SystemExit('FILL_ALL_PENDING_REQUIRES_EMBEDDED_PRODUCTION_RUN_ID')
        production_run_id = str(embedded_run_id or requested_run_id).strip()
        if not production_run_id:
            raise SystemExit('PRODUCTION_RUN_ID_REQUIRED_FOR_RESULT_FILL')
        try:
            import datetime as _dt
            from xiaogu_db import (
                fetch_daily_candidates,
                resolve_pick_id,
                update_production_run_step,
                upsert_return,
            )
            candidate_rows = fetch_daily_candidates(
                _dt.date.fromisoformat(fill['date']),
                production_run_id=production_run_id,
            )
            candidate = next(
                (
                    row for row in candidate_rows
                    if str(row.get('symbol') or '').zfill(6) == str(fill['symbol']).zfill(6)
                ),
                None,
            )
            if not candidate or not candidate.get('candidate_snapshot_id'):
                raise ValueError('PRODUCTION_RETURN_CANDIDATE_SNAPSHOT_NOT_FOUND')
            pick_id = resolve_pick_id(
                _dt.date.fromisoformat(fill['date']),
                fill['symbol'],
                production_run_id=production_run_id,
            )
            update_production_run_step(
                production_run_id,
                't1_settlement',
                'RUNNING',
                required=True,
                retry_command=(
                    f'python3 xiaogu_forward_result_filler_v0_1.py --date {fill["date"]} '
                    f'--symbol {fill["symbol"]} --production-run-id {production_run_id} --auto-eastmoney'
                ),
            )
            upsert_return(
                trade_date=_dt.date.fromisoformat(fill['date']),
                symbol=fill['symbol'],
                pick_id=pick_id,
                t1_return=fill.get('t1_return'),
                t1_labels=dict(fill.get('t1_labels') or {}),
                production_run_id=production_run_id,
                candidate_snapshot_id=str(candidate['candidate_snapshot_id']),
                return_status='SETTLED',
                settlement_evidence=dict(fill.get('result_source_evidence') or {}),
            )
            append_jsonl(FORWARD_LEDGER, fill)
            update_production_run_step(
                production_run_id,
                't1_settlement',
                'PASS',
                required=True,
                metadata={'symbol': fill['symbol'], 'return_field': PRODUCTION_RETURN_FIELD},
            )
        except Exception as exc:
            try:
                update_production_run_step(
                    production_run_id,
                    't1_settlement',
                    'FAIL',
                    required=True,
                    error_message=repr(exc)[:500],
                )
            except Exception:
                pass
            raise SystemExit(f'RESULT_FILL_PERSISTENCE_FAILED:{exc!r}')
    print(json.dumps({'appended_count': len(fills_to_append), **out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
