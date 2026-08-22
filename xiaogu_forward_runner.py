#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xiaogu D1 14:50 forward paper runner v0.1
Conservative official forward paper generator.
- Uses only T-day visible data collected at runtime.
- Writes exactly one append-only forward_paper_ledger record for the date unless one already exists.
- Defaults to NO_PICK unless a verified candidate bundle is explicitly present and all hard gates pass.
- Never trades, never orders.
"""
import argparse
import datetime as dt
import hashlib
from collections import Counter
from functools import lru_cache
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from xiaogu_utils import (
        PRODUCTION_RETURN_FIELD,
        PRODUCTION_RETURN_FORMULA,
        PRODUCTION_TRADE_MODE,
        now_iso,
        read_json,
        load_jsonl,
    )
except Exception:
    def now_iso() -> str:
        return dt.datetime.now().isoformat(timespec='seconds')

    _RUNNER_FILE_CACHE: Dict[str, Any] = {}

    def read_json(path: Path, use_cache=True) -> Dict[str, Any]:
        path_str = str(path)
        if use_cache and path_str in _RUNNER_FILE_CACHE:
            return _RUNNER_FILE_CACHE[path_str]
        with Path(path).open('r', encoding='utf-8') as fh:
            result = json.load(fh)
        if use_cache:
            _RUNNER_FILE_CACHE[path_str] = result
        return result

    def load_jsonl(path: Path, use_cache=True) -> List[Dict[str, Any]]:
        path_str = str(path)
        if use_cache and path_str in _RUNNER_FILE_CACHE:
            return _RUNNER_FILE_CACHE[path_str]
        rows: List[Dict[str, Any]] = []
        if not Path(path).exists():
            return rows
        with Path(path).open('r', encoding='utf-8') as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    rows.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
        if use_cache:
            _RUNNER_FILE_CACHE[path_str] = rows
        return rows

    def clear_runner_file_cache():
        _RUNNER_FILE_CACHE.clear()

from six_repo_integration_real_v2_1 import aggregate_four_repo_native_signals

if 'clear_runner_file_cache' not in dir():
    def clear_runner_file_cache():
        pass

BASE = Path(__file__).resolve().parent
RECORDER = BASE / 'xiaogu_forward_paper_recorder_v0_1.py'
LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
RULE_FREEZE = BASE / 'rule_freeze_v0_1.json'
RAW_ROOT = BASE / 'data' / 'forward_raw_runtime'
LIVE_SCAN_ROOT = BASE / 'data' / 'live_scan'
CANDIDATE_BUNDLE_ROOT = BASE / 'data' / 'forward_candidate_bundles'
RULE_VERSION = 'historical_backtest_rule_v0_3'
RESEARCH_BASKET_SIZE = 10  # 从3增加到10，扩大候选池
NO_PICK_DIAGNOSTIC_CANDIDATE_LIMIT = 8
MAX_SCAN_STALENESS_MINUTES = 120
# No fixed account-size or price cap belongs to the production chain.
# A real account snapshot may still provide an execution-only cash check.
DEFAULT_ACCOUNT_SNAPSHOT_PATH = BASE / 'data' / 'account_snapshot' / 'latest.json'
WEAK_MARKET_SHADOW_BREADTH_GATE = 20.0
SCAN_SUMMARY_NAME = 'xiaogu_scan_summary.json'
SCAN_SUMMARY_RUNNER_NAME = 'xiaogu_scan_summary_runner.json'
# Evidence/gate constants + helpers live in xiaogu_forward_gates (single owner).
# Re-export here so existing `from xiaogu_forward_runner import ...` keeps working.
from xiaogu_forward_gates import (  # noqa: E402
    REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS,
    REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS,
    REQUIRED_EASTMONEY_EVIDENCE_DOMAINS,
    REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS,
    candidate_evidence_missing_flags,
    is_api_scan_source,
    missing_coverage_items,
    production_evidence_missing_flags,
    soft_no_pick_flag,
)

ALLOWED_A_SHARE_SOURCE_TOKENS = ('eastmoney_api_scan_v2', 'v2_scanner_api')
API_A_SHARE_SOURCE_TOKENS = ('v2_scanner_api', 'eastmoney_api_scan_v2')
PRODUCTION_CHAIN_MODE = 'strict'
PRODUCTION_RANKING_VIEW = 'main_force_behavior_chain'
PRODUCTION_RANK_SOURCE = 'formal_profit_first'
PRODUCTION_SCORE_SOURCE = 'formal_t1_profit_components'
PRODUCTION_SNAPSHOT_ORIGINS = {'scan_formal_snapshot'}
DISALLOWED_GOVERNANCE_TOKENS = ('archive', 'backup', '.bak_', 'rollback', 'crypto', 'bitget', 'us_stock', 'yfinance', 'research_only', 'research-only', 'historical_validation')


def is_active_api_source(source: Any) -> bool:
    text = str(source or '')
    return any(token in text for token in API_A_SHARE_SOURCE_TOKENS)


LOCKED_SAFETY = {
    'paper_only': True,
    'no_trade': True,
    'production_ready': False,
    'allow_trade': True,
    'manual_paper_execution_allowed': True,
    'auto_order': False,
    'broker_connected': False,
}

ROUTINE_REGULATORY_KEYWORDS = [
    '股票交易异常波动', '股价异常波动', '交易风险提示', '股票交易风险提示',
    '股票交易异常波动暨风险提示', '股价异常波动暨风险提示',
]
SERIOUS_REGULATORY_KEYWORDS = ['监管函', '行政处罚', '立案调查', '行政处罚事先告知', '强制退市']


def is_routine_regulatory_block(blocker_text: str) -> bool:
    text = str(blocker_text)
    if any(kw in text for kw in SERIOUS_REGULATORY_KEYWORDS):
        return False
    return any(kw in text for kw in ROUTINE_REGULATORY_KEYWORDS)


SCORING_CONFIG_DEFAULTS = {
    # Empty = no weekday ban. Explicit e.g. "0,4" bans Mon/Fri. Never treat '' as missing.
    'weekday_blocklist': '',
    'max_score_cap': '88',
    'instant_momentum_min_confirmations': '2',
    'stale_repeat_window_days': '5',
    'stale_decay_factor': '0.65',
    'l2_limit_strength_bonus': '2.0',
    'sector_catalyst_penalty': '1.0',
    'near_limit_l2_exemption': 'true',
    # Production ranking evidence knobs (self_evolve + formal_sort/ranking_basis consume these).
    # Defaults match xiaogu_db; hardcodes were tuned at these → scale 1.0 preserves prior behavior.
    'evidence_catalyst_boost_weight': '0.5',
    'evidence_limitup_momentum_weight': '0.7',
    'evidence_broken_limit_penalty_weight': '1.5',
}

TRADE_MODE = PRODUCTION_TRADE_MODE
PRIMARY_RETURN_FIELD = PRODUCTION_RETURN_FIELD
PRIMARY_TRADE_HORIZON = 't1_close'
PRODUCTION_RETURN_FORMULA_TEXT = PRODUCTION_RETURN_FORMULA
PRODUCTION_POLICY = 'T_DAY_BUY_T1_PROFIT'
PRODUCTION_POLICY_ZH = 'T日买入，T+1日交易并以获利为唯一目标'
HORIZON_NOTE = PRODUCTION_POLICY_ZH
FORMAL_RANK_SNAPSHOT_VERSION = 'formal_profit_first_t1_close_v1'






def _load_scoring_config_from_db() -> Dict[str, Any]:
    try:
        from xiaogu_db import get_scoring_config_snapshot as _db_get_scoring_config_snapshot
        snapshot = _db_get_scoring_config_snapshot(refresh=True)
        if isinstance(snapshot, dict):
            return snapshot
    except Exception:
        pass
    return {
        'config': dict(SCORING_CONFIG_DEFAULTS),
        'loaded': False,
        'source': 'defaults',
        'error': 'scoring_config_snapshot_unavailable',
    }


@lru_cache(maxsize=1)
def _cached_scoring_config_snapshot() -> Tuple[Tuple[Tuple[str, str], ...], bool, str, str]:
    snapshot = {
        'config': dict(SCORING_CONFIG_DEFAULTS),
        'loaded': False,
        'source': 'defaults',
        'error': '',
    }
    try:
        loaded_snapshot = _load_scoring_config_from_db()
        if isinstance(loaded_snapshot, dict):
            config = loaded_snapshot.get('config') if isinstance(loaded_snapshot.get('config'), dict) else {}
            snapshot['config'].update({str(key): '' if value is None else str(value) for key, value in config.items()})
            snapshot['loaded'] = bool(loaded_snapshot.get('loaded'))
            snapshot['source'] = str(loaded_snapshot.get('source') or ('db' if snapshot['loaded'] else 'defaults'))
            snapshot['error'] = str(loaded_snapshot.get('error') or '')
    except Exception as exc:
        snapshot['error'] = repr(exc)
    return tuple(sorted(snapshot['config'].items())), bool(snapshot['loaded']), str(snapshot['source']), str(snapshot['error'])


def get_scoring_config_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    if force_refresh:
        _cached_scoring_config_snapshot.cache_clear()
    config_items, loaded, source, error = _cached_scoring_config_snapshot()
    return {
        'config': dict(config_items),
        'loaded': loaded,
        'source': source,
        'error': error,
    }


def clear_scoring_config_cache() -> None:
    _cached_scoring_config_snapshot.cache_clear()


def production_regime_from_row(row: Dict[str, Any] | None = None) -> str:
    """Best-effort production_regime for ranking scales (no hard gate side effects)."""
    if not isinstance(row, dict):
        return 'sideways'
    prod = str(row.get('production_regime') or '').lower().strip()
    if prod:
        return prod
    for key in ('market_adaptive_context', 'market_context', 'regime_policy'):
        nested = row.get(key)
        if isinstance(nested, dict):
            prod = str(nested.get('production_regime') or '').lower().strip()
            if prod:
                return prod
            if key == 'regime_policy':
                continue
            # nested may carry market_regime only
            mr = str(nested.get('market_regime') or '').lower().strip()
            if mr:
                return mr
    mr = str(row.get('market_regime') or '').lower().strip()
    if mr:
        return mr
    return 'sideways'


def resolve_ranking_evidence_scales_for_row(row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Load scoring_config + regime → scales used by ranking_basis and formal_candidate_sort_key.

    self_evolve writes evidence_* weights into scoring_config; this is the production consumer.
    """
    from xiaogu_regime_policy import resolve_ranking_evidence_scales as _resolve_scales

    scoring_config = get_scoring_config_snapshot()
    config = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(config, dict):
        config = dict(SCORING_CONFIG_DEFAULTS)
    regime = production_regime_from_row(row)
    scales = _resolve_scales(config, regime)
    scales['scoring_config_source'] = (
        str(scoring_config.get('source') or '') if isinstance(scoring_config, dict) else ''
    )
    return scales


_LEDGER_CACHE = None
# Ledger rows embed multi-MB feature payloads; never preload the whole 1GB+ file.
_LEDGER_TAIL_BYTES = int(os.environ.get('XIAOGU_LEDGER_TAIL_BYTES', str(80 * 1024 * 1024)))


def _iter_ledger_tail_records(max_bytes: int | None = None):
    """Yield raw JSON objects from the end of the ledger without full-file load."""
    if not LEDGER.exists():
        return
    limit = int(max_bytes if max_bytes is not None else _LEDGER_TAIL_BYTES)
    with LEDGER.open('rb') as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max(1, limit)))
        chunk = fh.read().decode('utf-8', 'replace')
    # If we started mid-record, drop the partial prefix.
    start = chunk.find('{"record_type"')
    if start < 0:
        return
    chunk = chunk[start:]
    for line in chunk.splitlines():
        text = line.strip()
        if not text or not text.startswith('{'):
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def _preload_ledger():
    """Memory-safe: index only recent DECISION/CORRECTION metadata from ledger tail."""
    global _LEDGER_CACHE
    if _LEDGER_CACHE is not None:
        return
    import gc
    size_mb = LEDGER.stat().st_size / 1024 / 1024 if LEDGER.exists() else 0.0
    print(f'LEDGER: indexing tail of {LEDGER.name} ({size_mb:.0f}MB file)...', file=sys.stderr, flush=True)
    rows = []
    for row in _iter_ledger_tail_records():
        record_type = str(row.get('record_type') or '').upper()
        if record_type not in ('DECISION', 'CORRECTION', 'RESULT_FILL'):
            continue
        # Drop heavy feature payloads after extracting identity fields.
        slim = {
            'record_type': row.get('record_type'),
            'date': row.get('date') or row.get('trade_date'),
            'trade_date': row.get('trade_date') or row.get('date'),
            'symbol': row.get('symbol'),
            'decision': row.get('decision'),
            'rule_version': row.get('rule_version'),
            'asof_time': row.get('asof_time'),
            'raw_data_snapshot_sha256': row.get('raw_data_snapshot_sha256') or row.get('snapshot_sha256'),
            'snapshot_sha256': row.get('snapshot_sha256'),
            'correction_of': row.get('correction_of'),
            PRIMARY_RETURN_FIELD: row.get(PRIMARY_RETURN_FIELD),
        }
        # Keep name/score if present without embedding full features_used.
        if row.get('name') is not None:
            slim['name'] = row.get('name')
        if row.get('score') is not None:
            slim['score'] = row.get('score')
        features = row.get('features_used') if isinstance(row.get('features_used'), dict) else {}
        if features:
            for key in ('name', 'score', 'price', 'signal_pct'):
                if key in features and key not in slim:
                    slim[key] = features.get(key)
        rows.append(slim)
    _LEDGER_CACHE = rows
    print(f'LEDGER: indexed {len(_LEDGER_CACHE)} tail decision/fill rows', file=sys.stderr, flush=True)
    gc.collect()


def _cached_ledger_rows() -> Tuple[Dict[str, Any], ...]:
    global _LEDGER_CACHE
    if _LEDGER_CACHE is None:
        _preload_ledger()
    return tuple(_LEDGER_CACHE or ())


def _ledger_decision_rows() -> List[Dict[str, Any]]:
    rows = []
    fills: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in _cached_ledger_rows():
        if str(row.get('record_type') or '').upper() == 'RESULT_FILL':
            key = (str(row.get('date') or row.get('trade_date') or '')[:10], symbol_for(row))
            fills[key] = row
    for row in _cached_ledger_rows():
        record_type = str(row.get('record_type') or '').upper()
        if record_type not in ('DECISION', 'CORRECTION'):
            continue
        symbol = symbol_for(row)
        trade_date = str(row.get('date') or row.get('trade_date') or '')[:10]
        if not symbol or not trade_date:
            continue
        merged = dict(row)
        fill = fills.get((trade_date, symbol))
        if isinstance(fill, dict):
            for key in (PRIMARY_RETURN_FIELD,):
                if fill.get(key) is not None:
                    merged[key] = fill.get(key)
        rows.append(merged)
    return rows


def _parse_date(value: Any) -> Optional[dt.date]:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except Exception:
        return None


def _horizon_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    primary_trade_return = safe_float(record.get(PRIMARY_RETURN_FIELD))
    payoff_class = (
        'unresolved'
        if primary_trade_return is None
        else ('profitable_t1_close' if primary_trade_return > 0 else 'non_profitable_t1_close')
    )
    return {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'primary_trade_return': primary_trade_return,
        't1_return': primary_trade_return,
        'payoff_class': payoff_class,
        'profit_target': 't1_return > 0',
    }


def _merge_lifecycle_history_records(
    base: Dict[str, Any] | None,
    update: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not isinstance(base, dict):
        return dict(update or {})
    if not isinstance(update, dict):
        return dict(base)
    merged = dict(base)
    for key in ('decision', 'source', 'candidate_bundle_path', 'candidate_source'):
        value = update.get(key)
        if value and not merged.get(key):
            merged[key] = value
    merged['picked'] = bool(merged.get('picked')) or bool(update.get('picked'))
    merged['is_official_pick'] = bool(merged.get('is_official_pick')) or bool(update.get('is_official_pick'))
    merged['source_layers'] = list(dict.fromkeys(
        [str(item) for item in (merged.get('source_layers') or []) if item] +
        [str(item) for item in (update.get('source_layers') or []) if item]
    ))
    merged['blockers'] = list(dict.fromkeys(
        [str(item) for item in (merged.get('blockers') or []) if item] +
        [str(item) for item in (update.get('blockers') or []) if item]
    ))
    base_features = merged.get('candidate_features') if isinstance(merged.get('candidate_features'), dict) else {}
    update_features = update.get('candidate_features') if isinstance(update.get('candidate_features'), dict) else {}
    if update_features:
        base_features = {**base_features, **update_features}
    merged['candidate_features'] = base_features
    for key in ('score', 'final_score', PRIMARY_RETURN_FIELD, 'payoff_class'):
        value = update.get(key)
        if value is not None:
            merged[key] = value
    if update.get('trade_date'):
        merged['trade_date'] = update['trade_date']
    if update.get('symbol'):
        merged['symbol'] = update['symbol']
    return merged


@lru_cache(maxsize=4)
def _cached_candidate_bundle_history(bundle_root: str) -> Tuple[Dict[str, Any], ...]:
    root = Path(bundle_root)
    if not root.exists():
        return tuple()
    history_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for bundle_path in sorted(root.rglob('*candidate.json')):
        try:
            bundle = read_json(bundle_path)
        except Exception:
            continue
        if not isinstance(bundle, dict):
            continue
        trade_date = str(bundle.get('date') or bundle.get('source_market_date') or bundle_path.parent.name or '')[:10]
        if _parse_date(trade_date) is None:
            continue
        candidates: List[Dict[str, Any]] = []
        if isinstance(bundle.get('paper_scoring_candidates'), list):
            candidates.extend([candidate for candidate in bundle['paper_scoring_candidates'] if isinstance(candidate, dict)])
        if isinstance(bundle.get('candidate'), dict):
            candidates.append(bundle['candidate'])
        for candidate in candidates:
            symbol = _as_symbol(candidate.get('symbol') or candidate.get('code'))
            if not symbol:
                continue
            candidate_features = candidate.get('candidate_features') if isinstance(candidate.get('candidate_features'), dict) else {}
            if not candidate_features and isinstance(candidate.get('features_used'), dict):
                candidate_features = candidate['features_used']
            if not candidate_features:
                candidate_features = dict(candidate)
            record = {
                'trade_date': trade_date,
                'symbol': symbol,
                'picked': False,
                'is_official_pick': False,
                'decision': str(candidate.get('decision') or ''),
                'score': safe_float(candidate.get('score')) if candidate.get('score') is not None else safe_float(candidate_features.get('score')),
                'final_score': safe_float(candidate.get('final_score')) if candidate.get('final_score') is not None else safe_float(candidate_features.get('final_score')),
                'source_layers': [str(item) for item in (candidate.get('source_layers') or candidate_features.get('source_layers') or []) if item],
                'blockers': [str(item) for item in (candidate.get('blockers') or candidate.get('blocked_reasons') or candidate_features.get('blockers') or candidate_features.get('blocked_reasons') or []) if item],
                'candidate_features': dict(candidate_features),
                'candidate_bundle_path': str(bundle_path),
                'candidate_source': str(candidate.get('candidate_source') or bundle.get('candidate_source') or bundle.get('source') or ''),
                'source': 'candidate_bundle',
            }
            key = (trade_date, symbol)
            existing = history_by_key.get(key)
            history_by_key[key] = _merge_lifecycle_history_records(existing, record) if existing else record
    return tuple(sorted(history_by_key.values(), key=lambda item: (item['trade_date'], item['symbol'], str(item.get('candidate_bundle_path') or ''))))


@lru_cache(maxsize=512)
def _candidate_lifecycle_history(symbol: str, target_date: str) -> Tuple[Dict[str, Any], ...]:
    symbol = _as_symbol(symbol)
    cutoff = _parse_date(target_date)
    if not symbol or cutoff is None:
        return tuple()
    history_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in _cached_candidate_bundle_history(str(CANDIDATE_BUNDLE_ROOT)):
        if symbol_for(row) != symbol:
            continue
        row_date = _parse_date(row.get('trade_date') or row.get('date'))
        if row_date is None or row_date >= cutoff:
            continue
        key = (row_date.isoformat(), symbol)
        existing = history_by_key.get(key)
        history_by_key[key] = _merge_lifecycle_history_records(existing, row) if existing else dict(row)
    default_bundle_root = (BASE / 'data' / 'forward_candidate_bundles').resolve()
    active_bundle_root = Path(CANDIDATE_BUNDLE_ROOT).resolve()
    if active_bundle_root != default_bundle_root:
        return tuple(sorted(history_by_key.values(), key=lambda item: item['trade_date']))
    # 从DB查询替代JSONL ledger读取 (避免读取4.2GB文件)
    try:
        from xiaogu_db import engine as _db_engine
        from sqlalchemy import text as _sql_text
        with _db_engine.connect() as _conn:
            _rows = _conn.execute(_sql_text(
                "SELECT trade_date, symbol, decision, final_score, raw_json "
                "FROM daily_candidates WHERE symbol = :sym AND trade_date < :cutoff "
                "ORDER BY trade_date"
            ), {'sym': symbol, 'cutoff': cutoff.isoformat()}).fetchall()
        for row in _rows:
            row_date = _parse_date(row[0])
            if row_date is None:
                continue
            raw_json = row[4] if isinstance(row[4], dict) else {}
            merged = {
                'trade_date': row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0]),
                'symbol': symbol,
                'picked': str(row[2] or '').upper() == 'PAPER_PICK',
                'is_official_pick': str(row[2] or '').upper() == 'PAPER_PICK',
                'decision': str(row[2] or ''),
                'score': None,
                'final_score': row[3],
                'source_layers': [str(item) for item in (raw_json.get('source_layers') or []) if item],
                'blockers': [str(item) for item in (raw_json.get('blockers') or []) if item],
                'candidate_features': raw_json,
                'source': 'db',
            }
            key = (row_date.isoformat(), symbol)
            existing = history_by_key.get(key)
            history_by_key[key] = _merge_lifecycle_history_records(existing, merged) if existing else merged
    except Exception:
        pass
    return tuple(sorted(history_by_key.values(), key=lambda item: item['trade_date']))


def _candidate_lifecycle_profile(
    candidate: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    profile = _cached_structured_signal_profile(candidate, bundle)
    symbol = symbol_for(candidate)
    target_date = str(bundle.get('date') or candidate.get('trade_date') or candidate.get('date') or profile.get('source_time') or '')[:10]
    history = list(_candidate_lifecycle_history(symbol, target_date))
    scoring_config = get_scoring_config_snapshot()
    scoring_config_values = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(scoring_config_values, dict):
        scoring_config_values = dict(SCORING_CONFIG_DEFAULTS)
    window_days = int(safe_float(scoring_config_values.get('stale_repeat_window_days')) or safe_float(SCORING_CONFIG_DEFAULTS['stale_repeat_window_days']) or 5)
    instant_momentum_min_confirmations = int(safe_float(scoring_config_values.get('instant_momentum_min_confirmations')) or safe_float(SCORING_CONFIG_DEFAULTS['instant_momentum_min_confirmations']) or 2)
    stale_decay_factor = safe_float(scoring_config_values.get('stale_decay_factor'))
    if stale_decay_factor is None:
        stale_decay_factor = safe_float(SCORING_CONFIG_DEFAULTS['stale_decay_factor']) or 0.65
    default_stale_decay_factor = safe_float(SCORING_CONFIG_DEFAULTS['stale_decay_factor']) or 0.65
    stale_decay_scale = stale_decay_factor / default_stale_decay_factor if default_stale_decay_factor else 1.0
    target_dt = _parse_date(target_date)
    recent_history: List[Dict[str, Any]] = []
    if target_dt is not None:
        for row in history:
            row_dt = _parse_date(row.get('trade_date'))
            if row_dt is None:
                continue
            days_back = (target_dt - row_dt).days
            if 1 <= days_back <= window_days:
                recent_history.append(row)
    recent_payoffs = [
        row for row in recent_history
        if safe_float(row.get('t1_return')) is not None
        and safe_float(row.get('t1_return')) > 0
    ]
    repeat_count = len(recent_history)
    history_has_delay = False
    history_has_instant = any(
        safe_float(row.get('t1_return')) is not None
        and safe_float(row.get('t1_return')) > 0
        for row in recent_history
    )

    instant_confirmations = 0
    if (profile.get('limitup_capture_confirmed') or False):
        instant_confirmations += 1
    if (profile.get('seal_order_strength') or 0.0) >= 0.60:
        instant_confirmations += 1
    if (profile.get('order_book_pressure') or 0.0) >= 0.50:
        instant_confirmations += 1
    if (profile.get('limitup_reason_strength') or 0.0) >= 0.60:
        instant_confirmations += 1
    limitup_capture_score = safe_float(profile.get('limitup_capture_score')) or 0.0
    if limitup_capture_score >= 0.62:
        instant_confirmations += 1
    instant_signal_present = bool(profile.get('limitup_capture_confirmed')) or limitup_capture_score >= 0.62

    theme_support = max(
        safe_float(profile.get('main_theme_core_score')) or 0.0,
        safe_float(profile.get('main_theme_alignment_score')) or 0.0,
        safe_float(profile.get('sector_opportunity_score')) or 0.0,
        safe_float(profile.get('fund_flow_momentum')) or 0.0,
    )
    no_hard_block = not bool(
        (profile.get('regulatory_hard_block') and not is_routine_regulatory_block(str(profile.get('regulatory_hard_block', ''))))
        or profile.get('a_share_risk_review_disqualified_for_paper_pick')
    )

    setup_class = 'WATCH_ONLY'
    setup_reason: List[str] = []
    if no_hard_block and instant_signal_present and instant_confirmations >= instant_momentum_min_confirmations:
        setup_class = 'INSTANT_MOMENTUM_SETUP'
        setup_reason.append('instant_confirmations')
    elif repeat_count >= 3 and not recent_payoffs:
        setup_class = 'STALE_REPEAT'
        setup_reason.append('repeat_without_payoff')

    stale_decay = 0.0
    if setup_class == 'STALE_REPEAT':
        stale_decay = round(min(0.45, 0.12 * max(1, repeat_count - 1) * stale_decay_scale), 4)
    elif repeat_count >= 2 and not recent_payoffs and not history_has_instant:
        stale_decay = round(min(0.30, 0.08 * repeat_count * stale_decay_scale), 4)

    setup_rank = {
        'INSTANT_MOMENTUM_SETUP': 3.0,
        'WATCH_ONLY': 1.0,
        'STALE_REPEAT': 0.0,
    }.get(setup_class, 0.0)

    lifecycle_score = round(
        max(0.0, setup_rank - stale_decay + (0.15 if history_has_delay else 0.0) + (0.1 if history_has_instant else 0.0)),
        4,
    )
    return {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'production_policy': PRODUCTION_POLICY,
        'production_policy_zh': PRODUCTION_POLICY_ZH,
        'setup_class': setup_class,
        'setup_rank': setup_rank,
        'setup_reason': setup_reason,
        'repeat_count': repeat_count,
        'recent_history_count': len(recent_history),
        'recent_payoff_count': len(recent_payoffs),
        'history_has_delayed_winner': history_has_delay,
        'history_has_instant_winner': history_has_instant,
        'theme_support': round(theme_support, 4),
        'delayed_support': False,
        'instant_confirmations': instant_confirmations,
        'stale_decay': stale_decay,
        'lifecycle_score': lifecycle_score,
        'history_tail': [
            {
                key: row.get(key)
                for key in (
                    'trade_date', 'symbol', 'decision', 'final_score',
                    't1_return', 'is_official_pick', 'picked',
                )
                if row.get(key) is not None
            }
            for row in recent_history[-3:]
        ],
    }



def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            if key == '_runtime_eval_cache':
                continue
            converted[str(key)] = _json_safe_value(item)
        return converted
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value



def _bundle_runtime_cache(bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return {}
    cache = bundle.get('_runtime_eval_cache')
    if not isinstance(cache, dict):
        cache = {}
        bundle['_runtime_eval_cache'] = cache
    return cache


def _bundle_runtime_cache_id(bundle: Dict[str, Any] | None) -> str:
    if not isinstance(bundle, dict):
        return ''
    return str(
        bundle.get('_bundle_path')
        or bundle.get('scan_summary_path')
        or bundle.get('source_time')
        or bundle.get('candidate_source')
        or ''
    )


def _candidate_runtime_cache_key(candidate: Dict[str, Any] | None) -> Tuple[Any, ...]:
    candidate = candidate if isinstance(candidate, dict) else {}
    details = candidate.get('structured_component_details') if isinstance(candidate.get('structured_component_details'), dict) else {}
    aux_missing = candidate.get('mainboard_auxiliary_missing_domains')
    if isinstance(aux_missing, (list, tuple, set)):
        aux_missing_key = tuple(sorted(str(x) for x in aux_missing))
    else:
        aux_missing_key = (str(aux_missing or ''),)
    return (
        symbol_for(candidate),
        str(candidate.get('source_row_hash') or ''),
        str(candidate.get('candidate_source') or ''),
        str(candidate.get('search_layer_hint') or details.get('search_layer_hint') or ''),
        str(candidate.get('candidate_stage') or details.get('candidate_stage') or ''),
        safe_int(candidate.get('rank')),
        safe_float(candidate.get('final_score')),
        safe_float(candidate.get('score')),
        safe_float(candidate.get('price')),
        safe_float(candidate.get('one_lot_cost')),
        str(candidate.get('data_gate') or candidate.get('data_gate_status') or ''),
        str(candidate.get('candidate_evidence_status') or ''),
        str(candidate.get('source_time') or ''),
        str(candidate.get('runner_asof_time') or ''),
        str(candidate.get('name') or ''),
        # Aux status is eligibility-critical; shallow copies that only flip
        # mainboard_auxiliary_* must not reuse PARTIAL/PASS structured profiles.
        str(
            candidate.get('mainboard_auxiliary_evidence_status')
            or candidate.get('auxiliary_evidence_status')
            or details.get('mainboard_auxiliary_evidence_status')
            or ''
        ),
        safe_float(
            candidate.get('mainboard_auxiliary_confidence')
            if candidate.get('mainboard_auxiliary_confidence') is not None
            else details.get('mainboard_auxiliary_confidence')
        ),
        aux_missing_key,
    )


def _cached_structured_signal_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return structured_signal_profile(row, bundle)
    cache = _bundle_runtime_cache(bundle)
    structured_cache = cache.setdefault('structured_signal_profile', {})
    cache_key = (_bundle_runtime_cache_id(bundle), _candidate_runtime_cache_key(row))
    cached = structured_cache.get(cache_key)
    if isinstance(cached, dict):
        return dict(cached)
    profile = structured_signal_profile(row, bundle)
    structured_cache[cache_key] = dict(profile)
    return dict(profile)


def _cached_paper_pick_eligibility_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return paper_pick_eligibility_profile(row, bundle)
    cache = _bundle_runtime_cache(bundle)
    eligibility_cache = cache.setdefault('paper_pick_eligibility_profile', {})
    cache_key = (_bundle_runtime_cache_id(bundle), _candidate_runtime_cache_key(row))
    cached = eligibility_cache.get(cache_key)
    if isinstance(cached, dict):
        return dict(cached)
    profile = paper_pick_eligibility_profile(row, bundle)
    eligibility_cache[cache_key] = dict(profile)
    return dict(profile)


@lru_cache(maxsize=256)
def historical_t1_loss_streak_before(trade_date: str, symbol: str) -> Tuple[int, float | None]:
    """Return consecutive prior non-profit T+1 results and the latest return.

    A single old loss is not a durable symbol ban. The cooldown is reserved for
    a current losing streak, matching the project rule's consecutive-loss policy.
    """
    try:
        from sqlalchemy import text
        from xiaogu_db import engine
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT t1_return
                    FROM returns
                    WHERE symbol = :symbol
                      AND trade_date < CAST(:trade_date AS date)
                      AND t1_return IS NOT NULL
                    ORDER BY trade_date DESC, id DESC
                    LIMIT 5
                """),
                {'symbol': str(symbol or '').zfill(6), 'trade_date': trade_date},
            ).fetchall()
        streak = 0
        latest = None
        for index, row in enumerate(rows):
            value = float(row[0])
            if index == 0:
                latest = value
            if value > 0:
                break
            streak += 1
        return streak, latest
    except Exception:
        return 0, None


def _cached_decision_for_candidate(
    candidate: Dict[str, Any],
    bundle: Dict[str, Any],
    target_date: str,
    allow_stale_data: bool = False,
) -> Tuple[str, str, str, Dict[str, Any], List[str]]:
    if not isinstance(bundle, dict):
        return decision_for_candidate(candidate, bundle, target_date, allow_stale_data=allow_stale_data)
    cache = _bundle_runtime_cache(bundle)
    decision_cache = cache.setdefault('decision_for_candidate', {})
    cache_key = (
        _bundle_runtime_cache_id(bundle),
        target_date,
        bool(allow_stale_data),
        _candidate_runtime_cache_key(candidate),
    )
    cached = decision_cache.get(cache_key)
    if isinstance(cached, tuple) and len(cached) == 5:
        cached_features = cached[3] if isinstance(cached[3], dict) else {}
        cached_flags = cached[4] if isinstance(cached[4], list) else []
        return cached[0], cached[1], cached[2], dict(cached_features), list(cached_flags)
    result = decision_for_candidate(candidate, bundle, target_date, allow_stale_data=allow_stale_data)
    decision_cache[cache_key] = (
        result[0],
        result[1],
        result[2],
        dict(result[3]) if isinstance(result[3], dict) else {},
        list(result[4]) if isinstance(result[4], list) else [],
    )
    return result[0], result[1], result[2], dict(result[3]), list(result[4])


def _latest_ledger_decision_row_for_date(date: str) -> Optional[Dict[str, Any]]:
    latest_row = None
    for row in _ledger_decision_rows():
        if str(row.get('date') or row.get('trade_date') or '')[:10] != date:
            continue
        if str(row.get('rule_version') or '') != RULE_VERSION:
            continue
        latest_row = row
    return dict(latest_row) if isinstance(latest_row, dict) else None


def correction_reference_for_date(date: str) -> str:
    """Return a stable audit reference for the latest append-only decision."""
    row = _latest_ledger_decision_row_for_date(date)
    if not row:
        return ""
    reference = str(row.get("raw_data_snapshot_sha256") or row.get("snapshot_sha256") or "").strip()
    if reference:
        return reference
    serialized = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def existing_decision_for_date(date: str) -> bool:
    return _latest_ledger_decision_row_for_date(date) is not None


def daily_candidate_snapshot_exists_for_date(date: str) -> bool:
    """Return whether DB already has the candidate snapshot for this decision date."""
    try:
        from xiaogu_db import fetch_daily_candidates
        rows = fetch_daily_candidates(dt.date.fromisoformat(date))
    except Exception:
        return True
    return bool(rows)


def should_skip_existing_decision_for_date(date: str, *, dry_run: bool, force: bool) -> bool:
    if dry_run or force:
        return False
    if not existing_decision_for_date(date):
        return False
    if daily_candidate_snapshot_exists_for_date(date):
        return True
    print(f'LEDGER_DECISION_EXISTS_BUT_DB_SNAPSHOT_MISSING: {date}; rebuilding DB snapshot', file=sys.stderr, flush=True)
    return False


def existing_paper_pick_symbol_for_date(date: str) -> Optional[str]:
    """Return the symbol if the latest decision for this date is PAPER_PICK, else None."""
    latest_row = _latest_ledger_decision_row_for_date(date)
    if not isinstance(latest_row, dict):
        return None
    if str(latest_row.get('decision') or '').upper() != 'PAPER_PICK':
        return None
    symbol = symbol_for(latest_row)
    return symbol or None


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


ACCOUNT_AVAILABLE_CASH_KEYS = ('available_cash', 'cash_available', 'buying_power', 'available_funds', 'cash', '可用资金')


def account_snapshot_from_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    for key in ('account_snapshot', 'eastmoney_account_snapshot'):
        snapshot = bundle.get(key)
        if isinstance(snapshot, dict):
            return snapshot
    return {}


def account_available_cash(snapshot: Dict[str, Any]) -> float | None:
    if not snapshot:
        return None
    sections = [snapshot]
    for key in ('account', 'account_summary', 'summary', 'balances'):
        section = snapshot.get(key)
        if isinstance(section, dict):
            sections.append(section)
    for section in sections:
        for key in ACCOUNT_AVAILABLE_CASH_KEYS:
            value = safe_float(section.get(key))
            if value is not None:
                return value
    return None


def paper_sizing_context(bundle: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = account_snapshot_from_bundle(bundle)
    snapshot_source = str(snapshot.get('source') or bundle.get('account_mode') or '')
    cash = account_available_cash(snapshot)
    background_snapshot = snapshot if snapshot else {}
    holdings = snapshot.get('positions') if isinstance(snapshot.get('positions'), list) else []
    total_assets = safe_float(snapshot.get('total_assets'))
    if total_assets is None and isinstance(snapshot.get('account_summary'), dict):
        total_assets = safe_float(snapshot['account_summary'].get('total_assets'))
    if snapshot and cash is None:
        return {
            'source': 'eastmoney_account_snapshot',
            'account_mode': 'actual_account',
            'available_cash': None,
            'one_lot_cost_cap': None,
            'total_assets': total_assets,
            'holdings_for_decision': holdings,
            '600396_assumed_manually_sold': False,
            'snapshot': snapshot,
            'background_account_snapshot': background_snapshot or snapshot,
        }
    if cash is not None:
        return {
            'source': 'eastmoney_account_snapshot',
            'account_mode': 'actual_account',
            'available_cash': cash,
            'one_lot_cost_cap': cash,
            'total_assets': total_assets if total_assets is not None else cash,
            'holdings_for_decision': holdings,
            '600396_assumed_manually_sold': False,
            'snapshot': snapshot,
            'background_account_snapshot': background_snapshot or snapshot,
        }
    return {
        'source': 'account_snapshot_unavailable',
        'account_mode': 'account_snapshot_unavailable',
        'available_cash': None,
        'one_lot_cost_cap': None,
        'total_assets': None,
        'holdings_for_decision': [],
        '600396_assumed_manually_sold': False,
        'snapshot': {},
        'background_account_snapshot': {},
    }


POSITION_SYMBOL_KEYS = ('symbol', 'code', '证券代码', '股票代码', 'sec_code', 'security_code')
POSITION_PROFIT_PCT_KEYS = ('pnl_pct', 'profit_pct', 'return_pct', '浮盈比例', '收益率')
POSITION_QUANTITY_KEYS = ('quantity', 'shares', 'volume', '持仓数量', '可用股份')
POSITION_COST_PRICE_KEYS = ('cost_price', 'avg_cost', '成本价', '成本')
POSITION_CURRENT_PRICE_KEYS = ('current_price', 'latest_price', 'price', '现价', '最新价')


def position_symbol(position: Dict[str, Any]) -> str:
    for key in POSITION_SYMBOL_KEYS:
        text = str(position.get(key) or '').strip()
        match = re.search(r'\d{6}', text)
        if match:
            return match.group(0)
    return ''


def _normalized_profit_pct(value: Any) -> float | None:
    pct = safe_float(value)
    if pct is None:
        return None
    return pct / 100.0 if abs(pct) > 1.5 else pct


def position_profit_pct(position: Dict[str, Any]) -> float | None:
    for key in POSITION_PROFIT_PCT_KEYS:
        if key in position:
            pct = _normalized_profit_pct(position.get(key))
            if pct is not None:
                return pct
    current_price = next((safe_float(position.get(key)) for key in POSITION_CURRENT_PRICE_KEYS if safe_float(position.get(key)) is not None), None)
    cost_price = next((safe_float(position.get(key)) for key in POSITION_COST_PRICE_KEYS if safe_float(position.get(key)) is not None), None)
    if current_price is None or cost_price is None or cost_price <= 0:
        return None
    return (current_price - cost_price) / cost_price


def _first_position_float(position: Dict[str, Any], keys: Tuple[str, ...]) -> float | None:
    for key in keys:
        value = safe_float(position.get(key))
        if value is not None:
            return value
    return None


def position_management_action(profile: Dict[str, Any]) -> str:
    if not profile.get('already_held'):
        return 'NO_POSITION_NEW_BUY_REVIEW'
    profit_pct = safe_float(profile.get('profit_pct'))
    if profit_pct is not None and profit_pct >= 0.15:
        return 'HELD_PROFIT_PROTECT'
    if profit_pct is not None and profit_pct >= 0.05:
        return 'HELD_TRAILING_STOP_WATCH'
    return 'HELD_RISK_REVIEW'


def position_profile_for_candidate(candidate: Dict[str, Any], sizing: Dict[str, Any]) -> Dict[str, Any]:
    candidate_symbol = symbol_for(candidate)
    profile: Dict[str, Any] = {
        'already_held': False,
        'symbol': candidate_symbol,
        'name': candidate.get('name'),
    }
    holdings = sizing.get('holdings_for_decision') if isinstance(sizing, dict) else []
    if not isinstance(holdings, list) or not candidate_symbol:
        return profile
    for position in holdings:
        if not isinstance(position, dict) or position_symbol(position) != candidate_symbol:
            continue
        profile.update({
            'already_held': True,
            'symbol': candidate_symbol,
            'name': position.get('name') or position.get('证券名称') or candidate.get('name'),
            'quantity': _first_position_float(position, POSITION_QUANTITY_KEYS),
            'cost_price': _first_position_float(position, POSITION_COST_PRICE_KEYS),
            'current_price': _first_position_float(position, POSITION_CURRENT_PRICE_KEYS),
            'profit_pct': position_profit_pct(position),
            'holding_days': safe_int(position.get('holding_days') or position.get('持仓天数')),
            'source': sizing.get('source'),
        })
        return {key: value for key, value in profile.items() if value is not None and value != ''}
    return profile


def account_mode_from_bundle(bundle: Dict[str, Any]) -> str:
    return str(paper_sizing_context(bundle).get('account_mode') or 'actual_account')


@lru_cache(maxsize=1)
def forward_ledger_win_stats() -> Dict[str, Any]:
    """Compute win stats from DB instead of loading the full JSONL ledger."""
    try:
        from xiaogu_db import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                    COUNT(*) AS result_count,
                    COUNT(t1_return) AS filled,
                    ROUND(AVG(t1_return)::numeric, 4) AS avg_t1,
                    COUNT(*) FILTER (WHERE t1_return > 0) AS wins
                FROM returns
            """)).fetchone()
            result_count = row[0] or 0
            filled = row[1] or 0
            avg_t1 = float(row[2]) if row[2] is not None else None
            wins = row[3] or 0
            t1_positive_rate = wins / filled if filled else None
            return {
                'result_count': result_count,
                't1_positive_rate': t1_positive_rate,
                'avg_t1_return': avg_t1,
                'profit_target': 't1_return > 0',
                'production_trade_mode': TRADE_MODE,
                'production_return_formula': PRODUCTION_RETURN_FORMULA_TEXT,
                'recent_results': [],
            }
    except Exception:
        return {
            'result_count': 0,
            't1_positive_rate': None,
            'avg_t1_return': None,
            'profit_target': 't1_return > 0',
            'production_trade_mode': TRADE_MODE,
            'production_return_formula': PRODUCTION_RETURN_FORMULA_TEXT,
            'recent_results': [],
        }


@lru_cache(maxsize=1)
def replay_win_stats(topk: int) -> Dict[str, Any]:
    summary = forward_ledger_win_stats()
    return {
        'ticket_count': summary.get('result_count'),
        't1_positive_rate': summary.get('t1_positive_rate'),
        'avg_t1_return': summary.get('avg_t1_return'),
        'production_trade_mode': summary.get('production_trade_mode'),
        'production_return_formula': summary.get('production_return_formula'),
    }


def repo_contribution_summary_text(repo_contributions: Dict[str, Any]) -> str:
    if not isinstance(repo_contributions, dict) or not repo_contributions:
        return ''
    ordered = ('tradingagent_a', 'VEI', 'Qlib', 'UZI_Skill', 'Kaixin_Factors')
    parts: List[str] = []
    for repo_name in ordered:
        entry = repo_contributions.get(repo_name)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get('status') or '')
        signal = str(entry.get('candidate_signal') or '')
        delta = safe_float(entry.get('score_delta')) or 0.0
        parts.append(f'{repo_name}:{status}[{signal}]={delta:+.4f}')
    return '; '.join(parts)


def active_repo_mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    active = ('tradingagent_a', 'VEI', 'Qlib', 'UZI_Skill', 'Kaixin_Factors')
    return {
        repo_name: value[repo_name]
        for repo_name in active
        if repo_name in value
    }


def active_repo_summary(value: Any) -> str:
    if not value:
        return ''
    parts = []
    for part in str(value).split('; '):
        repo_name = part.split(':', 1)[0].strip()
        if repo_name in ('tradingagent_a', 'VEI', 'Qlib', 'UZI_Skill', 'Kaixin_Factors'):
            parts.append(part)
    return '; '.join(parts)


def candidate_repo_delta_by_repo(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo_delta_by_repo = candidate.get('repo_delta_by_repo') or candidate.get('score_delta_by_repo') or {}
    return active_repo_mapping(repo_delta_by_repo)


def vei_candidate_signal_source(candidate: Dict[str, Any]) -> str:
    tags = candidate.get('vei_phase_d_tags') or []
    if not isinstance(tags, list):
        tags = [tags]
    normalized_tags = {str(tag).strip() for tag in tags if str(tag).strip()}
    details = candidate.get('structured_component_details') or candidate.get('component_details') or {}
    if not isinstance(details, dict):
        details = {}

    if 'FIRST_BOARD_PRE_SIGNAL' in normalized_tags or (safe_float(details.get('first_board_pre_signal')) or 0.0) > 0:
        return 'FBP / first_board_pre_signal'
    if 'WEAK_TO_STRONG_REVERSAL' in normalized_tags or (safe_float(details.get('weak_to_strong_reversal')) or 0.0) > 0:
        return 'WTR / weak_to_strong_reversal'
    if 'PRE_LIMITUP_ANOMALY' in normalized_tags or (safe_float(details.get('pre_limitup_anomaly')) or 0.0) > 0:
        return 'PLA / pre_limitup_anomaly'
    if 'SECTOR_OPPORTUNITY' in normalized_tags or (safe_float(details.get('sector_opportunity_score')) or 0.0) > 0:
        return 'SECTOR / sector_opportunity'
    return 'ACTIVE_VEI_ASOF_SCORING'


def synthesize_vei_repo_contribution(candidate: Dict[str, Any], score_delta: float) -> Dict[str, Any]:
    details = candidate.get('structured_component_details') or candidate.get('component_details') or {}
    if not isinstance(details, dict):
        details = {}
    score_delta_f = round(safe_float(score_delta) or 0.0, 4)
    candidate_signal = vei_candidate_signal_source(candidate)
    weak_medium = 0.0 < abs(score_delta_f) < 1.0
    status = 'WEAK_OR_PARTIAL' if weak_medium else 'REAL_OUTPUT'
    return {
        'status': status,
        'candidate_signal': candidate_signal,
        'score_delta': score_delta_f,
        'explanation': (
            f"scan-level VEI {'weak-medium' if weak_medium else 'active'} signal from "
            f"{candidate_signal}; preserved scan delta={score_delta_f:.4f}; status={status}"
        ),
        'components': {
            'pre_limitup_anomaly': round(safe_float(details.get('pre_limitup_anomaly')) or 0.0, 4),
            'weak_to_strong_reversal': round(safe_float(details.get('weak_to_strong_reversal')) or 0.0, 4),
            'first_board_pre_signal': round(safe_float(details.get('first_board_pre_signal')) or 0.0, 4),
            'sector_opportunity_score': round(safe_float(details.get('sector_opportunity_score')) or 0.0, 4),
        },
    }


def repo_contribution_context(candidate: Dict[str, Any]) -> Dict[str, Any]:
    candidate = candidate if isinstance(candidate, dict) else {}
    original_repo_contributions = active_repo_mapping(candidate.get('repo_contributions'))
    repo_contributions = dict(original_repo_contributions)
    score_delta_by_repo = active_repo_mapping(
        candidate.get('score_delta_by_repo') or candidate.get('repo_delta_by_repo')
    )
    scan_repo_delta_by_repo = candidate_repo_delta_by_repo(candidate)
    repo_contribution_summary = active_repo_summary(candidate.get('repo_contribution_summary'))
    if not repo_contribution_summary:
        repo_contribution_summary = repo_contribution_summary_text(repo_contributions)
    final_score = candidate.get('final_score') if candidate.get('final_score') is not None else candidate.get('score')
    final_score_explanation = str(candidate.get('final_score_explanation') or '')
    if '; repo_contributions=' in final_score_explanation:
        final_score_explanation = final_score_explanation.split('; repo_contributions=', 1)[0]
    evidence_context_present = any(candidate.get(key) not in (None, '', {}) for key in ('source_time', 'source_row_hash', 'evidence_path', 'raw_snapshot_path', 'raw_data_snapshot_path'))
    if not repo_contributions and evidence_context_present:
        try:
            repo_signals = aggregate_four_repo_native_signals(candidate)
            repo_contributions = active_repo_mapping(repo_signals.get('repo_contributions'))
            score_delta_by_repo = active_repo_mapping(repo_signals.get('score_delta_by_repo'))
            repo_contribution_summary = active_repo_summary(
                repo_signals.get('repo_contribution_summary') or repo_contribution_summary
            )
            final_score_explanation = str(repo_signals.get('final_score_explanation') or final_score_explanation or '')
        except Exception:
            pass
    scan_vei_delta = safe_float(scan_repo_delta_by_repo.get('VEI')) if scan_repo_delta_by_repo else None
    if scan_vei_delta not in (None, 0.0) and not original_repo_contributions.get('VEI'):
        score_delta_by_repo = dict(score_delta_by_repo)
        score_delta_by_repo['VEI'] = round(scan_vei_delta, 4)
        repo_contributions = dict(repo_contributions)
        repo_contributions['VEI'] = synthesize_vei_repo_contribution(candidate, scan_vei_delta)
        repo_contribution_summary = repo_contribution_summary_text(repo_contributions)
    if not final_score_explanation:
        final_score_text = 'None' if final_score is None else f'{safe_float(final_score):.4f}'
        final_score_explanation = f'final_score={final_score_text}'
    if repo_contribution_summary and 'repo_contributions=' not in final_score_explanation:
        final_score_explanation += f'; repo_contributions={repo_contribution_summary}'
    return {
        'score_delta_by_repo': score_delta_by_repo,
        'repo_delta_by_repo': score_delta_by_repo,
        'repo_contributions': repo_contributions,
        'repo_contribution_summary': repo_contribution_summary,
        'final_score_explanation': final_score_explanation,
    }


def single_target_card_status(
    decision: str,
    candidate: Dict[str, Any],
    flags: List[str],
    can_afford_one_lot: bool,
    bundle: Optional[Dict[str, Any]] = None,
) -> str:
    if decision == 'PAPER_PICK':
        return 'OFFICIAL_PAPER_PICK'
    if not symbol_for(candidate):
        return 'NO_OFFICIAL_TARGET'

    hard_blockers: List[str] = []
    paper_pick_eligibility = candidate.get('paper_pick_eligibility') if isinstance(candidate.get('paper_pick_eligibility'), dict) else {}
    missing_conditions = [str(item) for item in (paper_pick_eligibility.get('missing_conditions') or []) if item]
    reg_block = str(candidate.get('regulatory_hard_block') or '')
    if reg_block and not is_routine_regulatory_block(reg_block):
        hard_blockers.append('regulatory_hard_block')
    _near_limit = bool(candidate.get('near_limit_up_risk')) or any('near_limit_up_risk' in str(flag) for flag in flags)
    _has_l2 = 'L2_LIMIT_STRENGTH' in set(candidate.get('source_layers') or [])
    _exempt_near_limit = str(get_scoring_config_snapshot().get('config', {}).get('near_limit_l2_exemption', 'true')).lower() == 'true'
    if _near_limit and not (_has_l2 and _exempt_near_limit):
        hard_blockers.append('near_limit_up_risk')
    chase_high = bool(candidate.get('opportunity_hard_block') == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION') or any('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in str(flag) for flag in flags)
    continuation_exception = broken_limitup_continuation_exception(candidate, bundle or {}) if chase_high else {}
    if chase_high:
        sector_opp = safe_float(candidate.get('sector_opportunity_score') or candidate.get('candidate_features', {}).get('sector_catalyst_score')) or 0.0
        fund_mom = safe_float(candidate.get('fund_flow_momentum') or candidate.get('candidate_features', {}).get('fund_flow_momentum')) or 0.0
        if (
            sector_opp < 1.5
            and fund_mom < 0.8
            and not continuation_exception.get('eligible')
        ):
            hard_blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')
    if str((paper_pick_eligibility.get('signals') or {}).get('buyability_hard_block') or ''):
        hard_blockers.append('FINAL_PICK_MUST_BE_BUYABLE')
    if any('sector_opportunity_score' in str(mc) for mc in missing_conditions):
        pass
    if not can_afford_one_lot:
        hard_blockers.append('one_lot_cost>cap')
    if any(str(flag).startswith('FINAL_PICK_MUST_BE_BUYABLE') for flag in flags):
        hard_blockers.append('FINAL_PICK_MUST_BE_BUYABLE')
    if any(str(flag).startswith('DATA_GATE_NOT_PASS') for flag in flags):
        hard_blockers.append('DATA_GATE_NOT_PASS')
    if any(str(flag).startswith('XIAOCHAN_BLOCK') for flag in flags):
        hard_blockers.append('XIAOCHAN_BLOCK')
    if hard_blockers:
        return 'BLOCKED_TARGET'
    return 'NO_OFFICIAL_TARGET'


def build_single_target_card(
    decision: str,
    symbol: str,
    reason: str,
    candidate: Dict[str, Any],
    bundle: Dict[str, Any],
    flags: List[str],
    ledger_line_added: bool,
) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    sizing = paper_sizing_context(bundle if isinstance(bundle, dict) else {})
    current_position_profile = position_profile_for_candidate(candidate, sizing)
    position_action = position_management_action(current_position_profile)
    snapshot = account_snapshot_from_bundle(bundle if isinstance(bundle, dict) else {})
    available_cash = safe_float(sizing.get('available_cash'))
    one_lot_cap = safe_float(sizing.get('one_lot_cost_cap'))
    decision_cap_candidates = [cap for cap in (available_cash, one_lot_cap) if cap is not None]
    decision_cap = min(decision_cap_candidates) if decision_cap_candidates else None
    normalized_source_time = normalized_source_time_for_candidate(candidate, bundle)
    candidate_for_repo = dict(candidate)
    if normalized_source_time:
        candidate_for_repo['source_time'] = normalized_source_time
    for key in ('source_row_hash', 'evidence_path', 'raw_snapshot_path', 'raw_data_snapshot_path', 'candidate_source', 'score_asof_provenance'):
        if candidate_for_repo.get(key) in (None, '', []):
            bundle_value = bundle.get(key)
            if bundle_value not in (None, '', []):
                candidate_for_repo[key] = bundle_value
    if not candidate_for_repo.get('candidate_source'):
        candidate_for_repo['candidate_source'] = bundle.get('candidate_source') or bundle.get('source') or bundle.get('pipeline_version')
    candidate_for_repo['account_available_cash'] = available_cash
    candidate_for_repo['paper_one_lot_cost_cap'] = one_lot_cap
    candidate_for_repo['account_sizing_source'] = sizing.get('source')
    repo_context = repo_contribution_context(candidate_for_repo)
    no_official_target = decision != 'PAPER_PICK' and not symbol_for(candidate)
    price = safe_float(candidate.get('price'))
    one_lot_cost = safe_float(candidate.get('one_lot_cost'))
    if one_lot_cost is None and price is not None:
        one_lot_cost = price * 100
    can_afford_one_lot = bool(one_lot_cost is not None and decision_cap is not None and one_lot_cost <= decision_cap)
    eligibility = candidate.get('paper_pick_eligibility') if isinstance(candidate.get('paper_pick_eligibility'), dict) else {}
    blockers: List[str] = []
    if isinstance(eligibility, dict):
        blockers.extend(str(item) for item in (eligibility.get('blockers') or []) if item)
        if not eligibility.get('eligible'):
            blockers.extend(f'missing {item}' for item in (eligibility.get('missing_conditions') or []) if item)
    if not no_official_target and (bool(candidate.get('qualified_candidate')) is False or 'QUALIFIED_CANDIDATE_FALSE' in flags):
        blockers.append('QUALIFIED_CANDIDATE_FALSE')
    if not no_official_target and not can_afford_one_lot:
        blockers.append('ONE_LOT_COST_GT_ACCOUNT_AVAILABLE_CASH')
    if candidate.get('regulatory_hard_block'):
        blockers.append('regulatory_hard_block' if not is_routine_regulatory_block(str(candidate.get('regulatory_hard_block'))) else 'regulatory_soft_block')
    if bool(candidate.get('near_limit_up_risk')) or any('near_limit_up_risk' in str(flag) for flag in flags):
        blockers.append('near_limit_up_risk')
    chase_high = bool(candidate.get('opportunity_hard_block') == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION') or any('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in str(flag) for flag in flags)
    continuation_exception = broken_limitup_continuation_exception(candidate, bundle) if chase_high else {}
    if chase_high:
        sector_opp = safe_float(candidate.get('sector_opportunity_score') or candidate.get('candidate_features', {}).get('sector_catalyst_score')) or 0.0
        fund_mom = safe_float(candidate.get('fund_flow_momentum') or candidate.get('candidate_features', {}).get('fund_flow_momentum')) or 0.0
        if (
            sector_opp < 1.5
            and fund_mom < 0.8
            and not continuation_exception.get('eligible')
        ):
            blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')
    blockers = list(dict.fromkeys(blockers))

    why_not_official_pick = ''
    if decision != 'PAPER_PICK':
        reasons = [part for part in str(reason or '').split(':', 1)[-1].split(';') if part]
        if isinstance(eligibility, dict):
            reasons.extend(f'missing {item}' for item in (eligibility.get('missing_conditions') or []) if item)
        if repo_context['repo_contribution_summary']:
            reasons.append('repo_contributions=' + repo_context['repo_contribution_summary'])
        why_not_official_pick = '; '.join(dict.fromkeys(reasons))

    forward_stats = forward_ledger_win_stats()
    replay_top1 = replay_win_stats(1)
    replay_top2 = replay_win_stats(2)
    historical_win_stats = {
        'forward_ledger_t1_positive_rate': forward_stats.get('t1_positive_rate'),
        'forward_ledger_avg_t1_return': forward_stats.get('avg_t1_return'),
        'replay_top1_t1_positive_rate': replay_top1.get('t1_positive_rate'),
        'replay_top1_avg_t1_return': replay_top1.get('avg_t1_return'),
        'replay_top2_t1_positive_rate': replay_top2.get('t1_positive_rate'),
        'replay_top2_avg_t1_return': replay_top2.get('avg_t1_return'),
        'production_trade_mode': TRADE_MODE,
        'production_return_formula': PRODUCTION_RETURN_FORMULA_TEXT,
    }

    return {
        'symbol': symbol if decision == 'PAPER_PICK' else '',
        'name': candidate.get('name'),
        'price': price,
        'signal_pct': candidate.get('signal_pct'),
        'selection_reason': candidate.get('selection_reason') or candidate.get('final_score_explanation') or '',
        'sentiment_catalyst': candidate.get('sentiment_catalyst'),
        'theme_catalyst': candidate.get('theme_catalyst'),
        'news_catalyst': candidate.get('news_catalyst'),
        'positive_catalyst': candidate.get('positive_catalyst'),
        'target_status': single_target_card_status(decision, candidate, flags, can_afford_one_lot, bundle),
        'official_decision': decision,
        'score': candidate.get('score'),
        'final_score': (
            candidate.get('_effective_score')
            if candidate.get('_effective_score') is not None
            else candidate.get('_contrarian_score')
            if candidate.get('_contrarian_score') is not None
            else candidate.get('final_score')
            if candidate.get('final_score') is not None
            else candidate.get('score')
        ),
        'market': 'a_share',
        'board': candidate.get('board'),
        'account_mode': account_mode_from_bundle(bundle),
        'official_decision_reason': reason,
        'available_cash': available_cash,
        'total_assets': safe_float(sizing.get('total_assets')),
        'one_lot_cost_cap': one_lot_cap,
        'one_lot_cost': one_lot_cost,
        'can_afford_one_lot': can_afford_one_lot,
        'holdings_for_decision': sizing.get('holdings_for_decision') or [],
        'current_position_profile': current_position_profile,
        'already_held': bool(current_position_profile.get('already_held')),
        'position_profit_pct': current_position_profile.get('profit_pct'),
        'position_management_action': position_action,
        '600396_assumed_manually_sold': bool(sizing.get('600396_assumed_manually_sold')),
        'background_account_snapshot': sizing.get('background_account_snapshot') or {},
        'blockers': blockers,
        'why_not_official_pick': why_not_official_pick,
        'score_delta_by_repo': repo_context['score_delta_by_repo'],
        'repo_delta_by_repo': repo_context['repo_delta_by_repo'],
        'repo_contributions': repo_context['repo_contributions'],
        'repo_contribution_summary': repo_context['repo_contribution_summary'],
        'final_score_explanation': repo_context['final_score_explanation'],
        'hard_gate_status': {
            'regulatory_hard_block': candidate.get('regulatory_hard_block') or '',
            'risk_notice': bool(candidate.get('catalyst_quality_category') in ('risk_notice', 'regulatory_notice') or candidate.get('a_share_risk_review_disqualified_for_paper_pick')),
            'near_limit_up_risk': bool(candidate.get('near_limit_up_risk') or any('near_limit_up_risk' in str(flag) for flag in flags)),
            'chase_high_without_limitup_confirmation': bool(
                chase_high and not continuation_exception.get('eligible')
            ),
            'limitup_continuation_exception': bool(continuation_exception.get('eligible')),
            'qualified_candidate': bool(candidate.get('qualified_candidate')),
        },
        'historical_win_stats': historical_win_stats,
        'manual_trade_only': True,
        'paper_only': True,
        'no_trade': True,
        'allow_trade': decision == 'PAPER_PICK',
        'manual_paper_execution_allowed': decision == 'PAPER_PICK',
        'auto_order': False,
        'broker_connected': False,
        'ledger_line_added': ledger_line_added,
        'source_time': normalized_source_time or candidate.get('source_time') or bundle.get('source_time'),
        'runner_asof_time': candidate.get('runner_asof_time') or bundle.get('_runner_asof_time') or bundle.get('runner_asof_time') or bundle.get('asof_time'),
    }


def unique_text_values(values: List[Any]) -> List[str]:
    unique: List[str] = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        unique.append(text)
        seen.add(text)
    return unique


def safe_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def candidate_score_value(candidate: Dict[str, Any]) -> float | None:
    """Return the canonical production score, never the scanner structured score."""
    for key in ('production_score', 'formal_primary_score', 'final_score', 'score'):
        value = safe_float(candidate.get(key))
        if value is not None:
            return value
    return None


def _positive_numeric(value: Any) -> float:
    numeric = safe_float(value) or 0.0
    return max(0.0, numeric)










# 缓存板块名称






# 缓存新闻数据（每session只加载一次）
















































def blocked_score(reasons: Any) -> float:
    if not isinstance(reasons, list):
        return 0.0
    scores = []
    for reason in reasons:
        if not isinstance(reason, str) or ':' not in reason:
            continue
        try:
            scores.append(float(reason.rsplit(':', 1)[1]))
        except ValueError:
            continue
    return max(scores) if scores else 0.0



def symbol_for(candidate: Dict[str, Any]) -> str:
    symbol = candidate.get('symbol') or candidate.get('code') or ''
    return str(symbol).zfill(6) if symbol else ''


def normalize_market_regime_for_db(value: Any) -> str:
    regime = str(value or '').strip()
    if not regime:
        return 'direct_api'
    return regime[:20]


def _as_symbol(value: Any) -> str:
    text = str(value or '').strip()
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def contains_disallowed_governance_token(value: Any) -> str:
    text = str(value).lower()
    for token in DISALLOWED_GOVERNANCE_TOKENS:
        if token in text:
            return token
    return ''


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        allowed_equivalent_roots = {
            tuple(Path('/root/hermes/company-ai-system/workspaces/xiaogu').resolve().parts),
            tuple(Path('/workspace/hermes-workspaces/xiaogu').resolve().parts),
        }
        path_parts = tuple(path.resolve().parts)
        root_parts = tuple(root.resolve().parts)
        return path_parts in allowed_equivalent_roots and root_parts in allowed_equivalent_roots


def active_chain_governance_flags(bundle: Dict[str, Any], target_date: str, allow_stale_data: bool = False) -> List[str]:
    flags: List[str] = []
    rule_version = bundle.get('rule_version') or bundle.get('active_rule_version')
    if rule_version != RULE_VERSION:
        flags.append('ACTIVE_RULE_VERSION_MISMATCH_' + str(rule_version or 'missing'))

    source = str(bundle.get('candidate_source') or bundle.get('source') or bundle.get('pipeline_version') or '')
    if not any(token in source for token in ALLOWED_A_SHARE_SOURCE_TOKENS):
        flags.append('ACTIVE_A_SHARE_SOURCE_DEGRADED_' + (source or 'missing'))
    token = contains_disallowed_governance_token(source)
    if token:
        flags.append('DISALLOWED_SOURCE_TOKEN_' + token)

    market_tag = bundle.get('market_tag') or bundle.get('market') or bundle.get('asset_class')
    if market_tag and str(market_tag).lower() not in ('a_share', 'ashare', 'cn_a_share'):
        flags.append('NON_A_SHARE_MARKET_TAG_' + str(market_tag))

    bundle_path = str(bundle.get('_bundle_path') or '')
    if not bundle_path:
        flags.append('ACTIVE_CANDIDATE_BUNDLE_PATH_DEGRADED')
    else:
        path = Path(bundle_path)
        # Allow paths under candidate_bundles OR under live_scan (runner summary)
        valid_parent = CANDIDATE_BUNDLE_ROOT / target_date
        valid_scan_parent = LIVE_SCAN_ROOT / target_date
        if not (path_is_under(path, valid_parent) or path_is_under(path, valid_scan_parent)):
            flags.append('ACTIVE_CANDIDATE_BUNDLE_PATH_DEGRADED')
        token = contains_disallowed_governance_token(bundle_path)
        if token:
            flags.append('DISALLOWED_BUNDLE_PATH_TOKEN_' + token)

    evidence = bundle.get('source_evidence') or {}
    if isinstance(evidence, dict):
        values = [evidence.get('summary_path')]
        files = evidence.get('scan_files') or {}
        if isinstance(files, dict):
            values.extend(files.values())
        for value in values:
            if not value:
                continue
            token = contains_disallowed_governance_token(value)
            if token:
                flags.append('DISALLOWED_SOURCE_EVIDENCE_TOKEN_' + token)
                break
    return flags


def parse_source_datetime(value: Any) -> dt.datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def parse_runner_asof_datetime(target_date: str, runner_asof_time: Any) -> dt.datetime | None:
    text = str(runner_asof_time or '').strip()
    if not text:
        return None
    parsed = parse_source_datetime(text)
    if parsed is not None:
        return parsed
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return dt.datetime.strptime(f'{target_date} {text}', fmt)
        except ValueError:
            continue
    return None


def scan_age_minutes(source_time: Any, target_date: str, runner_asof_time: Any) -> float | None:
    source_dt = parse_source_datetime(source_time)
    runner_dt = parse_runner_asof_datetime(target_date, runner_asof_time)
    if source_dt is None or runner_dt is None:
        return None
    if source_dt.tzinfo is not None:
        source_dt = source_dt.replace(tzinfo=None)
    if runner_dt.tzinfo is not None:
        runner_dt = runner_dt.replace(tzinfo=None)
    return (runner_dt - source_dt).total_seconds() / 60.0


def select_asof_valid_source_time(
    times: List[Any],
    target_date: str,
    runner_asof_time: Any,
) -> str:
    valid: List[Tuple[dt.datetime, str]] = []
    for value in times:
        text = str(value or '').strip()
        if not text:
            continue
        parsed = parse_source_datetime(text)
        if parsed is None:
            continue
        if not str(text).startswith(target_date):
            continue
        if runner_asof_time:
            runner_dt = parse_runner_asof_datetime(target_date, runner_asof_time)
            if runner_dt is not None and parsed > runner_dt:
                continue
        valid.append((parsed, text))
    if valid:
        return max(valid, key=lambda item: item[0])[1]
    return str(times[0]).strip() if times else ''


def candidate_source_times(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> List[str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    row = row if isinstance(row, dict) else {}
    candidates: List[Any] = [
        row.get('source_time'),
        row.get('data_cutoff'),
        row.get('score_asof_provenance'),
        row.get('scan_summary_source_time'),
        bundle.get('source_time'),
        bundle.get('scan_summary_source_time'),
    ]
    source_evidence = bundle.get('source_evidence') if isinstance(bundle.get('source_evidence'), dict) else {}
    if isinstance(source_evidence, dict):
        candidates.append(source_evidence.get('summary_path'))
    for key in ('raw_snapshot_path', 'raw_data_snapshot_path', 'evidence_path'):
        candidates.append(row.get(key))
    return [str(item).strip() for item in candidates if str(item or '').strip()]


def normalized_source_time_for_candidate(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> str:
    bundle = bundle if isinstance(bundle, dict) else {}
    runner_asof_time = str(
        row.get('runner_asof_time')
        or row.get('_runner_asof_time')
        or bundle.get('_runner_asof_time')
        or bundle.get('runner_asof_time')
        or bundle.get('asof_time')
        or ''
    )
    target_date = str(
        bundle.get('date')
        or row.get('date')
        or (str(row.get('source_time') or '')[:10])
        or (str(bundle.get('source_time') or '')[:10])
        or ''
    )
    if not target_date:
        return str(row.get('source_time') or bundle.get('source_time') or '')
    selected = select_asof_valid_source_time(candidate_source_times(row, bundle), target_date, runner_asof_time)
    if selected:
        return selected
    fallback = str(row.get('source_time') or bundle.get('source_time') or '')
    if fallback and any(sep in fallback for sep in ('T', ' ')) and ':' in fallback:
        return fallback
    return ''


# Evidence coverage and soft NO_PICK classification are centralized in
# xiaogu_forward_gates.


def regulatory_hard_block_reason(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    symbol = symbol_for(candidate)
    explicit = candidate.get('regulatory_hard_block') or candidate.get('regulatory_manual_block') or candidate.get('regulatory_block_reason')
    if explicit:
        return str(explicit)
    manual_blocks = bundle.get('manual_regulatory_blocks') or bundle.get('regulatory_hard_blocks') or {}
    if isinstance(manual_blocks, dict) and symbol in manual_blocks:
        return str(manual_blocks[symbol])
    try:
        rule_blocks = read_json(RULE_FREEZE).get('regulatory_hard_blocks', {})
    except Exception:
        rule_blocks = {}
    if isinstance(rule_blocks, dict) and symbol in rule_blocks:
        block = rule_blocks[symbol]
        if isinstance(block, dict):
            return str(block.get('reason') or block.get('type') or 'REGULATORY_HARD_BLOCK')
        return str(block)
    return ''


def opportunity_hard_block_reason(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    symbol = symbol_for(candidate)
    explicit = candidate.get('opportunity_hard_block') or candidate.get('opportunity_block_reason')
    if explicit:
        return str(explicit)
    manual_blocks = bundle.get('manual_opportunity_blocks') or bundle.get('opportunity_hard_blocks') or {}
    if isinstance(manual_blocks, dict) and symbol in manual_blocks:
        block = manual_blocks[symbol]
        if isinstance(block, dict):
            return str(block.get('type') or block.get('reason') or 'OPPORTUNITY_HARD_BLOCK')
        return str(block)
    try:
        rule_blocks = read_json(RULE_FREEZE).get('opportunity_hard_blocks', {})
    except Exception:
        rule_blocks = {}
    if isinstance(rule_blocks, dict) and symbol in rule_blocks:
        block = rule_blocks[symbol]
        if isinstance(block, dict):
            return str(block.get('type') or block.get('reason') or 'OPPORTUNITY_HARD_BLOCK')
        return str(block)
    return ''


def _count_leader_conditions(features: Dict[str, Any]) -> int:
    """计算强势龙头条件满足数（用于三层判断）。"""
    # 1. 主线板块确认
    sector_score = safe_float(features.get('sector_opportunity_score')) or 0
    sector_tags = features.get('sector_opportunity_tags') or []
    has_sector_tag = 'SECTOR_OPPORTUNITY' in sector_tags if isinstance(sector_tags, list) else False
    is_main_sector = sector_score >= 0.6 or has_sector_tag

    # 2. 封板质量
    sealed_limit_up = bool(features.get('sealed_limit_up', False))
    close_pos = safe_float(features.get('close_position_score')) or 0
    has_good_close = sealed_limit_up or close_pos >= 0.95

    # 3. 涨停原因传播
    limitup_reason_score = safe_float(features.get('limitup_reason_propagation_score')) or 0
    has_strong_reason = limitup_reason_score >= 0.6

    # 4. 量能合理
    turnover = safe_float(features.get('turnover_rate')) or 0
    reasonable_turnover = turnover < 20

    # 5. 排名靠前
    rank = safe_float(features.get('rank')) or 999
    is_top_rank = rank <= 15

    return sum([is_main_sector, has_good_close, has_strong_reason, reasonable_turnover, is_top_rank])


def is_strong_leader_candidate(features: Dict[str, Any]) -> bool:
    """Return whether a candidate meets the minimum leader confirmation."""
    return _count_leader_conditions(features) >= 2


def decision_for_candidate(candidate: Dict[str, Any], bundle: Dict[str, Any], target_date: str, allow_stale_data: bool = False) -> Tuple[str, str, str, Dict[str, Any], List[str]]:
    flags = []
    def get(k, default=None): return candidate.get(k, bundle.get(k, default))
    _source_layers = set(candidate.get('source_layers') or [])
    has_l2_l3_golden_pair = 'L2_LIMIT_STRENGTH' in _source_layers and 'L3_FUND_FLOW' in _source_layers
    requested_decision_class = get('decision_class')
    price = get('price')
    one_lot_cost = get('one_lot_cost')
    risk_penalty = get('risk_penalty')
    xiaochan = get('xiaochan_gate_status', 'NOT_CALLED')
    asof_leakage_flag = bool(get('asof_leakage_flag', False))
    # Prefer explicit flag; also infer from pct≈board so risk flags match buyability gate.
    sealed_limit_up = bool(get('sealed_limit_up', False))
    if not sealed_limit_up:
        try:
            from xiaogu_forward_eligibility import _inferred_sealed_limit_up as _infer_seal
            sealed_limit_up = bool(_infer_seal(candidate))
        except Exception:
            sealed_limit_up = False
    weak_close_risk = bool(get('weak_close_risk', False))
    high_open_low_close_risk = bool(get('high_open_low_close_risk', False))
    broken_limit_risk = bool(get('broken_limit_risk', False))
    broken_limit_risk_flags = get('broken_limit_risk_flags', []) or []
    broken_limit_risk_reason = get('broken_limit_risk_reason')
    intraday_pullback_risk = bool(get('intraday_pullback_risk', False))
    market_block = bool(get('market_systemic_risk_block', False))
    data_gate = get('data_gate', get('data_gate_status', 'UNKNOWN'))
    blocked_reasons = get('blocked_reasons', []) or []
    regulatory_block = regulatory_hard_block_reason(candidate, bundle)
    research_signals = candidate.get('research_signals') if isinstance(candidate.get('research_signals'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    if not regulatory_block:
        if catalyst_quality.get('regulatory_hard_block') or catalyst_quality.get('category') in ('risk_notice', 'regulatory_notice'):
            regulatory_block = str(catalyst_quality.get('category') or 'regulatory_notice')
        elif risk_review.get('disqualified_for_paper_pick'):
            regulatory_block = 'a_share_risk_review_disqualified'
    opportunity_block = opportunity_hard_block_reason(candidate, bundle) or limitup_quality_block_reason(candidate, bundle)
    candidate_stage = str(_cached_structured_signal_profile(candidate, bundle).get('candidate_stage') or candidate.get('candidate_stage') or '')
    is_near_limit = 'near_limit' in candidate_stage
    evidence_missing_flags = production_evidence_missing_flags(bundle)
    candidate_evidence_flags = candidate_evidence_missing_flags(candidate, bundle)
    source_health_flags = paper_pick_source_health_flags(bundle)
    qualified_candidate = get('qualified_candidate', True)
    bundle_date = bundle.get('date')
    source_market_date = bundle.get('source_market_date')
    source_time = normalized_source_time_for_candidate(candidate, bundle) or bundle.get('source_time') or get('source_time')
    runner_asof_time = bundle.get('_runner_asof_time') or get('runner_asof_time')
    symbol = symbol_for(candidate)
    price_f = safe_float(price)
    lot_f = safe_float(one_lot_cost)
    risk_f = safe_float(risk_penalty)
    age_minutes = scan_age_minutes(source_time, target_date, runner_asof_time)
    sizing = paper_sizing_context(bundle)
    available_cash = safe_float(sizing.get('available_cash'))
    one_lot_cap = safe_float(sizing.get('one_lot_cost_cap'))

    features = dict(candidate)
    if regulatory_block:
        features['regulatory_hard_block'] = regulatory_block
    if opportunity_block:
        features['opportunity_hard_block'] = opportunity_block
    if age_minutes is not None:
        features['scan_age_minutes'] = round(age_minutes, 2)
        features['max_scan_staleness_minutes'] = MAX_SCAN_STALENESS_MINUTES
    features['candidate_source'] = candidate.get('candidate_source') or bundle.get('candidate_source') or bundle.get('source') or candidate.get('source')
    features['source_time'] = source_time
    features['source_row_hash'] = candidate.get('source_row_hash') or bundle.get('source_row_hash')
    features['evidence_path'] = candidate.get('evidence_path') or bundle.get('evidence_path')
    features['raw_snapshot_path'] = candidate.get('raw_snapshot_path') or bundle.get('raw_snapshot_path')
    features['raw_data_snapshot_path'] = candidate.get('raw_data_snapshot_path') or bundle.get('raw_data_snapshot_path')
    features['account_sizing_source'] = sizing['source']
    features['account_available_cash'] = sizing['available_cash']
    features['paper_one_lot_cost_cap'] = sizing['one_lot_cost_cap']
    features['eastmoney_account_snapshot'] = sizing['snapshot']
    features['candidate_bundle_path'] = bundle.get('_bundle_path')
    features['paper_candidate_basket'] = bundle.get('paper_scoring_candidates', [])
    repo_signals = aggregate_four_repo_native_signals(features)
    repo_contribution_summary = str(repo_signals.get('repo_contribution_summary') or '')
    final_score = features.get('final_score') if features.get('final_score') is not None else features.get('score')
    final_score_text = 'None' if final_score is None else f'{safe_float(final_score):.4f}'
    final_score_explanation = f'final_score={final_score_text}'
    if repo_contribution_summary:
        final_score_explanation += f'; repo_contributions={repo_contribution_summary}'
    repo_context = {
        'score_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
        'repo_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
        'repo_contributions': repo_signals.get('repo_contributions', {}),
        'repo_contribution_summary': repo_contribution_summary,
        'final_score_explanation': final_score_explanation,
    }
    features['score_delta_by_repo'] = repo_context['score_delta_by_repo']
    features['repo_delta_by_repo'] = repo_context['repo_delta_by_repo']
    features['repo_contributions'] = repo_context['repo_contributions']
    features['repo_contribution_summary'] = repo_context['repo_contribution_summary']
    features['final_score_explanation'] = repo_context['final_score_explanation']
    paper_pick_candidate = dict(candidate)
    paper_pick_candidate['score_delta_by_repo'] = repo_context['score_delta_by_repo']
    paper_pick_candidate['repo_delta_by_repo'] = repo_context['repo_delta_by_repo']
    paper_pick_candidate['repo_contributions'] = repo_context['repo_contributions']
    paper_pick_candidate['repo_contribution_summary'] = repo_context['repo_contribution_summary']
    paper_pick_candidate['final_score_explanation'] = repo_context['final_score_explanation']
    paper_pick_eligibility = _cached_paper_pick_eligibility_profile(paper_pick_candidate, bundle)
    limitup_continuation = broken_limitup_continuation_exception(paper_pick_candidate, bundle)
    candidate_stage = str(_cached_structured_signal_profile(candidate, bundle).get('candidate_stage') or candidate.get('candidate_stage') or '')
    is_near_limit = 'near_limit' in candidate_stage

    if not symbol.isdigit() or len(symbol) != 6: flags.append('A_SHARE_SYMBOL_INVALID_' + (symbol or 'missing'))
    # 历史数据回测时跳过时效性检查
    # 检查涨停预期，如果强烈则允许使用历史数据
    limit_up_expectation = 0
    if isinstance(candidate, dict):
        limit_up_expectation = (
            candidate.get('limit_up_potential', 0) +
            candidate.get('leader_bonus', 0) +
            candidate.get('topic_heat_bonus', 0) +
            candidate.get('news_bonus', 0)
        )
    # 动态阈值：根据市场强弱调整
    market_breadth = float(bundle.get('market_snapshot', {}).get('market_breadth_up_pct') or 0) if isinstance(bundle, dict) else 0
    if market_breadth < 30:
        stale_threshold = 8  # 市场下跌，降低阈值
    elif market_breadth < 50:
        stale_threshold = 12  # 市场震荡
    else:
        stale_threshold = 15  # 市场上涨，提高阈值
    # 如果涨停预期强烈，跳过时效性检查
    bypass_stale = limit_up_expectation >= stale_threshold
    if not allow_stale_data and not bypass_stale:
        if bundle_date and bundle_date != target_date: flags.append('STALE_BUNDLE_DATE')
        if not source_market_date: flags.append('SOURCE_MARKET_DATE_MISSING')
        elif source_market_date != target_date: flags.append('STALE_SOURCE_MARKET_DATE')
        if not source_time: flags.append('SOURCE_TIME_MISSING')
        elif not str(source_time).startswith(target_date): flags.append('STALE_SOURCE_TIME')
        candidate_row_date = candidate.get('date') or candidate.get('data_date') or candidate.get('source_date')
        if candidate_row_date and str(candidate_row_date)[:10] != target_date:
            flags.append('CANDIDATE_ROW_DATE_MISMATCH_' + str(candidate_row_date)[:10])
        if age_minutes is not None:
            if age_minutes > MAX_SCAN_STALENESS_MINUTES:
                flags.append(f'SCAN_TOO_OLD_{age_minutes:.1f}M_GT_{MAX_SCAN_STALENESS_MINUTES}M')
            elif age_minutes < -1:
                flags.append(f'SCAN_AFTER_RUNNER_ASOF_{abs(age_minutes):.1f}M')
    if data_gate not in ('PASS', 'OK', True): flags.append('DATA_GATE_NOT_PASS')
    flags.extend(source_health_flags)
    if xiaochan == 'BLOCK': flags.append('XIAOCHAN_BLOCK')
    if regulatory_block:
        flags.append(regulatory_block)
        flags.append('REGULATORY_HARD_BLOCK_' + regulatory_block)
    if opportunity_block:
        stock_level_limitup_expectation_pass = bool((paper_pick_eligibility.get('signals') or {}).get('stock_level_limitup_expectation_pass'))
        if (
            opportunity_block == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
            and paper_pick_eligibility.get('eligible')
            and (not is_near_limit or stock_level_limitup_expectation_pass or limitup_continuation.get('eligible'))
        ):
            pass
        else:
            flags.append(opportunity_block)
            flags.append('OPPORTUNITY_HARD_BLOCK_' + opportunity_block)
    flags.extend(evidence_missing_flags)
    flags.extend(candidate_evidence_flags)
    enforce_formal_eligibility = candidate.get('paper_pick_eligibility') is not None
    if enforce_formal_eligibility:
        qualified_candidate = bool(paper_pick_eligibility['eligible'])
    else:
        qualified_candidate = bool(qualified_candidate)
    if qualified_candidate is False: flags.append('QUALIFIED_CANDIDATE_FALSE')
    for eligibility_blocker in (
        'weak_underwater_without_forward_confirmation',
        'overheated_market_no_strong_confirmation',
        'weak_market_requires_direct_confirmation',
        'weak_market_hot_momentum_without_d1_continuation_evidence',
    ):
        if (
            eligibility_blocker in (paper_pick_eligibility.get('blockers') or [])
            and not limitup_continuation.get('eligible')
        ):
            flags.append(eligibility_blocker)
    stock_level_limitup_expectation_pass = bool((paper_pick_eligibility.get('signals') or {}).get('stock_level_limitup_expectation_pass'))
    buyability_block_reason = str((paper_pick_eligibility.get('signals') or {}).get('buyability_hard_block') or '')
    if buyability_block_reason:
        flags.append(buyability_block_reason)
    filtered_blocked = [
        r for r in blocked_reasons
        if not (
            str(r) == 'opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
            and paper_pick_eligibility.get('eligible')
            and (not is_near_limit or stock_level_limitup_expectation_pass or limitup_continuation.get('eligible'))
        )
    ]
    if filtered_blocked: flags.append('CANDIDATE_BLOCKED_' + ','.join(str(r) for r in filtered_blocked))
    if risk_f != 0: flags.append('RISK_PENALTY_NOT_ZERO')
    if asof_leakage_flag: flags.append('ASOF_LEAKAGE_FLAG_TRUE')
    if price_f is None or price_f <= 0: flags.append('PRICE_INVALID')
    if lot_f is None:
        flags.append('ONE_LOT_COST_GT_CAP_OR_INVALID')
    elif one_lot_cap is not None:
        decision_cap_candidates = [cap for cap in (available_cash, one_lot_cap) if cap is not None]
        decision_cap = min(decision_cap_candidates) if decision_cap_candidates else None
        if decision_cap is not None and lot_f > decision_cap:
            if sizing.get('source') == 'eastmoney_account_snapshot':
                flags.append('ONE_LOT_COST_GT_ACCOUNT_AVAILABLE_CASH')
            else:
                flags.append('ONE_LOT_COST_GT_CAP_OR_INVALID')
    if sealed_limit_up: flags.append('SEALED_LIMIT_UP_BUYABILITY_FAIL')
    if weak_close_risk: flags.append('WEAK_CLOSE_RISK')
    if high_open_low_close_risk: flags.append('HIGH_OPEN_LOW_CLOSE_RISK')
    if broken_limit_risk:
        flags.append('BROKEN_LIMIT_RISK' + (':' + str(broken_limit_risk_reason) if broken_limit_risk_reason else ''))
    if broken_limit_risk_flags:
        flags.append('BROKEN_LIMIT_RISK_FLAGS_' + ','.join(str(flag) for flag in broken_limit_risk_flags))
    if intraday_pullback_risk: flags.append('INTRADAY_PULLBACK_RISK')
    if market_block: flags.append('MARKET_SYSTEMIC_RISK_BLOCK')

    features = dict(candidate)
    if regulatory_block:
        features['regulatory_hard_block'] = regulatory_block
    if opportunity_block:
        features['opportunity_hard_block'] = opportunity_block
    if age_minutes is not None:
        features['scan_age_minutes'] = round(age_minutes, 2)
        features['max_scan_staleness_minutes'] = MAX_SCAN_STALENESS_MINUTES
    features['candidate_source'] = candidate.get('candidate_source') or bundle.get('candidate_source') or bundle.get('source') or candidate.get('source')
    features['source_time'] = source_time
    features['source_row_hash'] = candidate.get('source_row_hash') or bundle.get('source_row_hash')
    features['evidence_path'] = candidate.get('evidence_path') or bundle.get('evidence_path')
    features['raw_snapshot_path'] = candidate.get('raw_snapshot_path') or bundle.get('raw_snapshot_path')
    features['raw_data_snapshot_path'] = candidate.get('raw_data_snapshot_path') or bundle.get('raw_data_snapshot_path')
    features['account_sizing_source'] = sizing['source']
    features['account_available_cash'] = sizing['available_cash']
    features['paper_one_lot_cost_cap'] = sizing['one_lot_cost_cap']
    features['eastmoney_account_snapshot'] = sizing['snapshot']
    features['candidate_bundle_path'] = bundle.get('_bundle_path')
    features['paper_candidate_basket'] = bundle.get('paper_scoring_candidates', [])
    features['paper_pick_eligibility'] = paper_pick_eligibility
    features['production_policy'] = PRODUCTION_POLICY
    features['production_policy_zh'] = PRODUCTION_POLICY_ZH
    features['research_signals'] = paper_pick_eligibility['signals'].get('research_signals')
    features['research_panel_overall'] = paper_pick_eligibility['signals'].get('research_panel_overall')
    features['catalyst_quality_category'] = paper_pick_eligibility['signals'].get('catalyst_quality_category')
    features['a_share_risk_review_disqualified_for_paper_pick'] = paper_pick_eligibility['signals'].get('a_share_risk_review_disqualified_for_paper_pick')
    features['historical_pattern_name'] = paper_pick_eligibility['signals'].get('historical_pattern_name')
    features['structured_formal_paper_pick_eligible'] = bool(paper_pick_eligibility['eligible'])
    features['formal_eligible'] = bool(paper_pick_eligibility['eligible'])
    features['candidate_lifecycle'] = paper_pick_eligibility['signals'].get('candidate_lifecycle')
    features['setup_class'] = paper_pick_eligibility['signals'].get('setup_class')
    features['setup_rank'] = paper_pick_eligibility['signals'].get('setup_rank')
    features['setup_reason'] = paper_pick_eligibility['signals'].get('setup_reason')
    features['repeat_count'] = paper_pick_eligibility['signals'].get('repeat_count')
    features['stale_decay'] = paper_pick_eligibility['signals'].get('stale_decay')
    features['lifecycle_score'] = paper_pick_eligibility['signals'].get('candidate_lifecycle', {}).get('lifecycle_score') if isinstance(paper_pick_eligibility['signals'].get('candidate_lifecycle'), dict) else None
    candidate_eligible = bool(paper_pick_eligibility.get('eligible'))
    repo_signals = aggregate_four_repo_native_signals(features)
    repo_contribution_summary = str(repo_signals.get('repo_contribution_summary') or '')
    final_score = features.get('final_score') if features.get('final_score') is not None else features.get('score')
    final_score_text = 'None' if final_score is None else f'{safe_float(final_score):.4f}'
    final_score_explanation = f'final_score={final_score_text}'
    if repo_contribution_summary:
        final_score_explanation += f'; repo_contributions={repo_contribution_summary}'
    repo_context = {
        'score_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
        'repo_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
        'repo_contributions': repo_signals.get('repo_contributions', {}),
        'repo_contribution_summary': repo_contribution_summary,
        'final_score_explanation': final_score_explanation,
    }
    features['score_delta_by_repo'] = repo_context['score_delta_by_repo']
    features['repo_delta_by_repo'] = repo_context['repo_delta_by_repo']
    features['repo_contributions'] = repo_context['repo_contributions']
    features['repo_contribution_summary'] = repo_context['repo_contribution_summary']
    features['final_score_explanation'] = repo_context['final_score_explanation']

    if flags:
        hard_blockers = {'DATA_GATE_NOT_PASS', 'XIAOCHAN_BLOCK', 'ASOF_LEAKAGE_FLAG_TRUE', 'STALE_BUNDLE_DATE', 'STALE_SOURCE_MARKET_DATE', 'SOURCE_MARKET_DATE_MISSING', 'STALE_SOURCE_TIME', 'SOURCE_TIME_MISSING'}
        has_scan_freshness_blocker = any(flag.startswith('SCAN_TOO_OLD_') or flag.startswith('SCAN_AFTER_RUNNER_ASOF_') for flag in flags)
        has_candidate_date_blocker = any(flag.startswith('CANDIDATE_ROW_DATE_MISMATCH_') for flag in flags)
        has_source_health_blocker = any(flag in hard_blockers for flag in source_health_flags)
        if 'SEALED_LIMIT_UP_BUYABILITY_FAIL' in flags and xiaochan != 'BLOCK':
            return 'NO_PICK', symbol, 'HARD_GATE_NOT_ALL_PASS:' + ';'.join(flags), features, flags
        if has_source_health_blocker:
            return 'NO_PICK', '', 'HARD_GATE_NOT_ALL_PASS:' + ';'.join(flags), features, flags
        non_blocking_flags = {f for f in flags if not soft_no_pick_flag(f) and not f.startswith('SCAN_')}
        if non_blocking_flags or not candidate_eligible:
            return 'NO_PICK', '', 'HARD_GATE_NOT_ALL_PASS:' + ';'.join(flags), features, flags

        # Climax Risk Gate: 高潮市场下高涨幅分层处理
        _market_regime = str(features.get('market_regime', '') or '').lower()
        _signal_pct = safe_float(features.get('signal_pct'))
        if 'climax' in _market_regime:
            features['climax_risk'] = True
            if _signal_pct is not None and _signal_pct >= 5.0:
                # 高涨幅：三层判断
                _is_strong_leader = is_strong_leader_candidate(features)
                _conditions_met = _count_leader_conditions(features)

                if _conditions_met >= 4:
                    # 第一层：高涨幅强确认 → 保留
                    features['climax_layer'] = 'strong_confirmed'
                    features['climax_tag'] = 'high_pct_strong_confirmed'
                    features['confidence_penalty'] = 0.85  # 轻微降低
                    flags.append('CLIMAX_HIGH_PCT_STRONG_CONFIRMED')
                elif _conditions_met >= 2:
                    # 第二层：高涨幅但分歧 → 降权保留
                    features['climax_layer'] = 'divergence'
                    features['climax_tag'] = 'high_pct_divergence'
                    features['confidence_penalty'] = 0.65  # 较大降低
                    flags.append('CLIMAX_HIGH_PCT_DIVERGENCE')
                else:
                    # 第三层：高涨幅假强 → 过滤
                    features['climax_layer'] = 'fake_strong'
                    features['climax_tag'] = 'high_pct_without_confirmation'
                    return 'NO_PICK', symbol, 'CLIMAX_RISK_GATE:high_pct_fake_strong_in_climax', features, flags + ['CLIMAX_RISK_HIGH_PCT_FAKE_STRONG']

        return 'PAPER_PICK', symbol, 'ALL_FORWARD_PAPER_HARD_GATES_PASS', features, unique_text_values(flags)
    return 'PAPER_PICK', symbol, 'ALL_FORWARD_PAPER_HARD_GATES_PASS', features, []
















# Mainline theme synonyms keep sector/fund-flow matching explainable and
# independent of any external social feed.





























































































def basket_candidate(row: Dict[str, Any], decision_class: str) -> Dict[str, Any]:
    reasons = row.get('blocked_reasons') or []
    regulatory_block = regulatory_hard_block_reason(row, {})
    opportunity_block = opportunity_hard_block_reason(row, {}) or limitup_quality_block_reason(row, {})
    if regulatory_block:
        reasons = [*reasons, 'regulatory_hard_block:' + regulatory_block]
    if opportunity_block:
        reasons = [*reasons, 'opportunity_hard_block:' + opportunity_block]
    score = row.get('score')
    price = safe_float(row.get('price'))
    paper_pick_eligibility = row.get('paper_pick_eligibility')
    if isinstance(paper_pick_eligibility, dict):
        qualified_candidate = bool(paper_pick_eligibility.get('eligible'))
    else:
        qualified_candidate = (score is not None and not reasons) or bool(row.get('structured_formal_paper_pick_eligible'))
    return {
        'code': symbol_for(row),
        'symbol': symbol_for(row),
        'name': row.get('name'),
        'board': row.get('board'),
        'price': row.get('price'),
        'one_lot_cost': price * 100 if price is not None else None,
        'signal_date': row.get('signal_date') or row.get('date'),
        'asof_time': row.get('asof_time'),
        'source_time': row.get('source_time'),
        'data_cutoff': row.get('data_cutoff'),
        'source_row_hash': row.get('source_row_hash'),
        'evidence_path': row.get('evidence_path'),
        'candidate_source': row.get('candidate_source') or row.get('source'),
        'score_asof_provenance': row.get('score_asof_provenance'),
        'signal_pct': row.get('signal_pct'),
        'signal_amount': row.get('signal_amount'),
        'rank': row.get('rank'),
        'pool_rank': row.get('pool_rank'),
        'formal_rank': row.get('formal_rank'),
        'scanner_rank': row.get('scanner_rank'),
        'rank_source': row.get('rank_source'),
        'formal_primary_score': row.get('formal_primary_score'),
        'structured_priority_score': row.get('structured_priority_score'),
        'ranking_basis': row.get('ranking_basis'),
        'ranking_basis_details': row.get('ranking_basis_details', {}),
        'amount_pctile_rule': row.get('amount_pctile_rule'),
        'setup_type': row.get('setup_type'),
        'search_layer': row.get('search_layer'),
        'search_layer_hint': row.get('search_layer_hint'),
        'candidate_stage': row.get('candidate_stage'),
        'early_opportunity_score': row.get('early_opportunity_score'),
        'limitup_capture_score': row.get('limitup_capture_score'),
        'limitup_capture_profile': row.get('limitup_capture_profile'),
        'limitup_capture_confirmed': row.get('limitup_capture_confirmed'),
        'limitup_capture_reasons': row.get('limitup_capture_reasons', []),
        'news_catalyst_strength': row.get('news_catalyst_strength'),
        'mainboard_policy': row.get('mainboard_policy') or 'main_only',
        'mainboard_auxiliary_evidence_status': row.get('mainboard_auxiliary_evidence_status'),
        'mainboard_auxiliary_missing_domains': row.get('mainboard_auxiliary_missing_domains', []),
        'mainboard_auxiliary_confidence': row.get('mainboard_auxiliary_confidence'),
        'announcement_evidence': row.get('announcement_evidence', []),
        'announcement_catalyst_score': row.get('announcement_catalyst_score'),
        'news_evidence': row.get('news_evidence', {}),
        'sector_news_evidence': row.get('sector_news_evidence', []),
        'sector_news_catalyst_score': row.get('sector_news_catalyst_score'),
        'limitup_reason_evidence': row.get('limitup_reason_evidence', []),
        'limitup_reason_quality_score': row.get('limitup_reason_quality_score'),
        'risk_notice_evidence': row.get('risk_notice_evidence', []),
        'risk_notice_penalty': row.get('risk_notice_penalty'),
        'sector_news_strength': row.get('sector_news_strength'),
        'sector_catalyst_score': row.get('sector_catalyst_score'),
        'sector_opportunity_tags': row.get('sector_opportunity_tags', []),
        'sector_opportunity_score': row.get('sector_opportunity_score'),
        'news_catalyst_quality_categories': row.get('news_catalyst_quality_categories', []),
        'topic_propagation_score': row.get('topic_propagation_score'),
        'intraday_alert_strength': row.get('intraday_alert_strength'),
        'limitup_reason_propagation_score': row.get('limitup_reason_propagation_score'),
        'low_position_catalyst_score': row.get('low_position_catalyst_score'),
        'source_layers': row.get('source_layers', []),
        'underwater_recovery_score': row.get('underwater_recovery_score'),
        'full_universe_rank': row.get('full_universe_rank'),
        'full_universe_quote_count': row.get('full_universe_quote_count'),
        'full_universe_tradable_count': row.get('full_universe_tradable_count'),
        'full_universe_amount_pctile': row.get('full_universe_amount_pctile'),
        'full_universe_fund_pctile': row.get('full_universe_fund_pctile'),
        'market_breadth_up_pct': row.get('market_breadth_up_pct'),
        'market_limitups': row.get('market_limitups'),
        'market_bigups': row.get('market_bigups'),
        'market_regime': row.get('market_regime'),
        'market_follow_through_score': row.get('market_follow_through_score'),
        'limitup_broken_ratio': row.get('limitup_broken_ratio'),
        'broken_limitups': row.get('broken_limitups'),
        'max_consecutive': row.get('max_consecutive'),
        'sentiment_score': row.get('sentiment_score'),
        'market_main_inflow': row.get('market_main_inflow'),
        'net_inflow_main': row.get('net_inflow_main'),
        'close_position_score': row.get('close_position_score'),
        'turnover_rate': row.get('turnover_rate'),
        'volume_ratio': row.get('volume_ratio'),
        'score': score,
        'repo_delta_by_repo': row.get('repo_delta_by_repo', {}),
        'candidate_evidence_status': row.get('candidate_evidence_status'),
        'candidate_evidence_domain_counts': row.get('candidate_evidence_domain_counts', {}),
        'candidate_evidence_matched_domains': row.get('candidate_evidence_matched_domains', []),
        'candidate_evidence_missing_domains': row.get('candidate_evidence_missing_domains', []),
        'enhanced_evidence_domain_counts': row.get('enhanced_evidence_domain_counts', {}),
        'enhanced_evidence_matched_domains': row.get('enhanced_evidence_matched_domains', []),
        'enhanced_evidence_missing_domains': row.get('enhanced_evidence_missing_domains', []),
        'experimental_evidence_domain_counts': row.get('experimental_evidence_domain_counts', {}),
        'experimental_evidence_matched_domains': row.get('experimental_evidence_matched_domains', []),
        'experimental_evidence_missing_domains': row.get('experimental_evidence_missing_domains', []),
        'structured_score': row.get('structured_score'),
        'structured_score_components': row.get('structured_score_components') or row.get('components'),
        'structured_component_details': row.get('structured_component_details') or row.get('component_details'),
        'main_theme_alignment_score': row.get('main_theme_alignment_score'),
        'main_theme_core_score': row.get('main_theme_core_score'),
        'hsgt_institutional_flow': row.get('hsgt_institutional_flow'),
        'experimental_catalyst_signal': row.get('experimental_catalyst_signal'),
        'vei_phase_d_tags': normalize_vei_phase_d_tags(row.get('vei_phase_d_tags')),
        'limitup_reason_strength': structured_component(row, 'limitup_reason_strength'),
        'seal_order_strength': structured_component(row, 'seal_order_strength'),
        'order_book_pressure': structured_component(row, 'order_book_pressure'),
        'fund_flow_momentum': structured_component(row, 'fund_flow_momentum'),
        'time_series_momentum': structured_component(row, 'time_series_momentum'),
        't1_profit_candidate': bool(row.get('t1_profit_candidate')),
        't1_profit_profile': row.get('t1_profit_profile') or {},
        'expected_t1_profit_score': row.get('expected_t1_profit_score'),
        'structured_score_mode': row.get('structured_score_mode') or row.get('mode'),
        'final_score': score,
        'blocked_score': None if score is not None else blocked_score(reasons),
        'blocked_reasons': reasons,
        'regulatory_hard_block': regulatory_block or None,
        'opportunity_hard_block': opportunity_block or None,
        'paper_pick_eligibility': paper_pick_eligibility,
        'official_target_excluded': bool(row.get('official_target_excluded')),
        'official_target_exclusion_reasons': row.get('official_target_exclusion_reasons', []),
        'diagnostic_only': bool(row.get('diagnostic_only')),
        'research_signals': row.get('research_signals'),
        'research_panel_overall': row.get('research_panel_overall') or ((row.get('research_signals') or {}).get('research_panel') or {}).get('overall'),
        'catalyst_quality_category': row.get('catalyst_quality_category') or ((row.get('research_signals') or {}).get('catalyst_quality') or {}).get('category'),
        'a_share_risk_review_disqualified_for_paper_pick': bool(row.get('a_share_risk_review_disqualified_for_paper_pick') or ((row.get('research_signals') or {}).get('a_share_risk_review') or {}).get('disqualified_for_paper_pick')),
        'historical_pattern_name': row.get('historical_pattern_name') or ((row.get('research_signals') or {}).get('historical_pattern') or {}).get('pattern_name'),
        'structured_formal_paper_pick_eligible': bool(row.get('structured_formal_paper_pick_eligible')),
        'qualified_candidate': qualified_candidate,
        'formal_eligible': qualified_candidate,
        'decision_class': decision_class,
        'risk_penalty': 0,
        'asof_leakage_flag': False,
        'data_gate': 'PASS',
        'data_gate_status': 'PASS',
        'xiaochan_gate_status': 'ALLOW_FORWARD_PAPER_NO_TRADE',
        'xiaoshuju_data_gate_status': 'PASS',
        **repo_contribution_context(row),
        **LOCKED_SAFETY,
    }


def build_daily_ticket_search_rows(enriched_rows: List[Dict[str, Any]], bundle_context: Dict[str, Any]) -> Dict[str, Any]:
    from xiaogu_forward_eligibility import (
        filter_current_day_tradable_candidates,
        filter_t1_profit_candidates,
    )

    # Preserve regulatory rows for diagnosis; the final official gate still blocks them.
    bundle_context = bundle_context if isinstance(bundle_context, dict) else {}
    source_rows = list(enriched_rows or [])
    if bundle_context.get('t1_profit_gate_enabled'):
        enriched_rows, current_day_tradable_filter = filter_t1_profit_candidates(
            enriched_rows,
            bundle_context,
            enforce=True,
        )
    else:
        enriched_rows, current_day_tradable_filter = filter_current_day_tradable_candidates(
            enriched_rows,
            bundle_context,
        )
    regulatory_diagnostic_rows = [
        row for row in source_rows
        if isinstance(row, dict)
        and (
            regulatory_hard_block_reason(row, bundle_context)
            or ((row.get('research_signals') or {}).get('catalyst_quality') or {}).get('regulatory_hard_block')
            or ((row.get('research_signals') or {}).get('a_share_risk_review') or {}).get('disqualified_for_paper_pick')
        )
        and row not in (enriched_rows or [])
    ]
    if regulatory_diagnostic_rows:
        enriched_rows = [*(enriched_rows or []), *regulatory_diagnostic_rows]
        current_day_tradable_filter = {
            **current_day_tradable_filter,
            'diagnostic_retained_regulatory_count': len(regulatory_diagnostic_rows),
        }
    try:
        market_ctx = market_adaptive_context({}, bundle_context)
        stamped_regime = str(market_ctx.get('production_regime') or '').lower() or 'sideways'
    except Exception:
        stamped_regime = str(
            bundle_context.get('production_regime')
            or (bundle_context.get('market_snapshot') or {}).get('market_regime')
            or bundle_context.get('market_regime')
            or 'sideways'
        ).lower()
        market_ctx = {'production_regime': stamped_regime}
    stamped_enriched: List[Dict[str, Any]] = []
    for row in (enriched_rows or []):
        if not isinstance(row, dict):
            continue
        stamped = dict(row)
        if not stamped.get('production_regime'):
            stamped['production_regime'] = stamped_regime
        if not isinstance(stamped.get('market_adaptive_context'), dict):
            stamped['market_adaptive_context'] = market_ctx
        stamped_enriched.append(stamped)
    # P1: stamp pool_rank from scanner, then rewrite rank via formal profit-first sort.
    enriched_rows = apply_formal_profit_ranks(stamped_enriched)

    def row_is_searchable(row: Dict[str, Any]) -> bool:
        symbol = symbol_for(row)
        price = safe_float(row.get('price'))
        return bool(symbol and symbol.isdigit() and len(symbol) == 6 and price is not None and price > 0)

    def candidate_stage_for_row_local(row: Dict[str, Any]) -> str:
        profile = structured_signal_profile(row)
        return profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct'])

    def _is_limit_up(row: Dict[str, Any]) -> bool:
        """检查是否已涨停或接近涨停"""
        code = str(row.get('code') or row.get('symbol') or '')
        pct = safe_float(row.get('signal_pct')) or 0
        if code.startswith(('30', '688')):
            return pct >= 19.5
        return pct >= 9.5

    def _signal_pct(row: Dict[str, Any], default: float | None = None) -> float | None:
        pct = safe_float(row.get('signal_pct'))
        return default if pct is None else pct

    stage_priority = {
        'underwater': 5,
        'flat_0_to_3': 4,
        'early_3_to_5': 3,
        'mid_5_to_7': 2,
        'high_7_to_9': 1,
        'near_limit_9_plus': 0,
        'unknown': 0,
    }

    def row_has_low_position_signal(row: Dict[str, Any]) -> bool:
        profile = structured_signal_profile(row)
        stage = candidate_stage_for_row_local(row)
        source_layers = set(row.get('source_layers') or [])
        return (
            profile['search_layer_hint'] in ('news_catalyst_low_position', 'sector_catalyst_low_position', 'intraday_alert_reversal')
            or (profile['low_position_catalyst_score'] or 0.0) >= 0.35
            or (profile['sector_opportunity_score'] or 0) > 0
            or (early_opportunity_score_for_row(row) >= 0.65 and stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'))
            or stage in ('underwater', 'flat_0_to_3')
            or 'L4_UNDERWATER_RECOVERY' in source_layers
        )

    def formal_layer_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float, float, float, float]:
        return formal_candidate_sort_key(row)

    def sector_layer_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
        profile = structured_signal_profile(row)
        return (
            safe_float(row.get('structured_priority_score')) or 0.0,
            profile.get('structured_score') or 0.0,
            profile['sector_opportunity_score'] or 0.0,
            early_opportunity_score_for_row(row),
            stage_priority.get(candidate_stage_for_row_local(row), 0),
            safe_float(row.get('final_score')) or safe_float(row.get('score')) or 0.0,
        )

    def early_layer_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
        profile = structured_signal_profile(row)
        return (
            early_opportunity_score_for_row(row),
            stage_priority.get(candidate_stage_for_row_local(row), 0),
            profile['sector_opportunity_score'] or 0.0,
            profile['fund_flow_momentum'] or 0.0,
            profile['time_series_momentum'] or 0.0,
            safe_float(row.get('amount_pctile_rule')) or 0.0,
        )

    def underwater_layer_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float, float, float, float]:
        profile = structured_signal_profile(row)
        details = profile['structured_component_details'] if isinstance(profile['structured_component_details'], dict) else {}
        close_position = profile['close_position_score'] or 0.0
        intraday_alert = profile['intraday_alert_strength'] or 0.0
        volume_ratio = profile['volume_ratio'] or 0.0
        fund_flow = profile['fund_flow_momentum'] or 0.0
        true_underwater_score = 0.0
        if close_position >= 0.80:
            true_underwater_score += 1.0
        if intraday_alert >= 0.80:
            true_underwater_score += 1.0
        if volume_ratio >= 1.8:
            true_underwater_score += 0.8
        if fund_flow >= 0.5:
            true_underwater_score += 0.8
        if (profile['sector_opportunity_score'] or 0.0) >= 0.5:
            true_underwater_score += 0.6
        false_underwater_penalty = 0.0
        if close_position < 0.75:
            false_underwater_penalty += 2.0
        if intraday_alert < 0.75:
            false_underwater_penalty += 1.5
        if volume_ratio < 1.4:
            false_underwater_penalty += 1.0
        return (
            true_underwater_score,
            -(false_underwater_penalty),
            details.get('weak_to_strong_reversal') or 0.0,
            early_opportunity_score_for_row(row),
            close_position,
            intraday_alert,
            profile['sector_opportunity_score'] or 0.0,
            profile['structured_score'] or 0.0,
        )

    def limitup_capture_layer_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        profile = structured_signal_profile(row)
        details = profile['structured_component_details'] if isinstance(profile['structured_component_details'], dict) else {}
        return (
            profile['limitup_capture_score'] or 0.0,
            safe_float(row.get('structured_priority_score')) or 0.0,
            details.get('pre_limitup_anomaly') or 0.0,
            profile['limitup_reason_propagation_score'] or 0.0,
            profile['fund_flow_momentum'] or 0.0,
        )

    shadow_high_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and structured_signal_present(row)
        and not row_has_low_position_signal(row)
    ]
    structured_sector_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (structured_signal_profile(row)['sector_opportunity_score'] or 0) > 0
        and (_signal_pct(row) is not None and _signal_pct(row) < 9.5)  # 排除接近涨停
    ]
    news_catalyst_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (
            structured_signal_profile(row)['search_layer_hint'] == 'news_catalyst_low_position'
            or structured_signal_profile(row)['setup_type'] in ('NEWS_CATALYST_LOW_POSITION', 'TOPIC_FUND_IGNITION')
        )
    ]
    sector_catalyst_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (
            structured_signal_profile(row)['search_layer_hint'] == 'sector_catalyst_low_position'
            or structured_signal_profile(row)['setup_type'] in ('SECTOR_NEWS_LOW_POSITION', 'TOPIC_FUND_IGNITION')
        )
    ]
    # 板块内弱势反转：强势板块中的弱势股（历史模式: 弱势股+强势板块→反转涨停）
    # 条件: sector_opportunity_score>=0.5 + signal_pct<0 或 position<0.4 + 排除涨停
    sector_contrarian_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (structured_signal_profile(row)['sector_opportunity_score'] or 0) >= 0.5
        and (
            (_signal_pct(row, 0.0) < 0)
            or (structured_signal_profile(row)['close_position_score'] or 1.0) < 0.4
        )
        and structured_signal_profile(row)['search_layer_hint'] != 'limitup_capture'
        and not _is_limit_up(row)  # 排除已涨停
    ]
    # 板块跟风股逆袭：强势板块中非龙头但有正向动量的股票
    # 条件: sector_opportunity_score>=0.5 + signal_pct>0 + 不是板块龙头 + 排除涨停
    # 逻辑: 龙头过热时，资金会轮动到板块内其他股票
    sector_follower_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (structured_signal_profile(row)['sector_opportunity_score'] or 0) >= 0.5
        and (_signal_pct(row) is not None and 0 < _signal_pct(row) < 9.5)  # 排除接近涨停
        and (structured_signal_profile(row)['close_position_score'] or 1.0) < 0.8  # 不追高位
        and structured_signal_profile(row)['search_layer_hint'] != 'limitup_capture'
        and not _is_limit_up(row)  # 排除已涨停
    ]
    intraday_alert_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (
            structured_signal_profile(row)['search_layer_hint'] == 'intraday_alert_reversal'
            or structured_signal_profile(row)['setup_type'] == 'INTRADAY_ALERT_REVERSAL'
        )
    ]
    underwater_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (
            candidate_stage_for_row_local(row) in ('underwater', 'flat_0_to_3')
            or 'L4_UNDERWATER_RECOVERY' in (row.get('source_layers') or [])
        )
    ]
    limitup_capture_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and structured_signal_profile(row)['limitup_capture_profile'] == 'STRONG_LIMITUP_CAPTURE'
    ]
    low_position_ambush_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and 'L11_LOW_POSITION_AMBUSH' in (row.get('source_layers') or [])
    ]
    # 动量延续：昨日强势（signal_pct>5%）且有成交量支撑，但排除已涨停的票
    # 逻辑：强势股可能继续上涨，但已涨停的票买不进去
    momentum_continuation_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (_signal_pct(row) is not None and 5 < _signal_pct(row) < 9.5)  # 排除接近涨停
        and (safe_float(row.get('amount')) or 0) > 5e8  # 成交额>5亿
        and (structured_signal_profile(row)['close_position_score'] or 1.0) < 0.9  # 不追极高位
        and not _is_limit_up(row)  # 排除已涨停
    ]
    # T+1 利润优先层：近板/高位续涨结构（中利/顺钠/华天型）必须进入 live 决策池。
    # 旧层把 pct>=9.5 从 momentum/sector_follower 剔除，且 limitup_capture 依赖
    # STRONG_LIMITUP_CAPTURE 画像常为 NONE → 利润票进不了 search_rows，只剩防守壳。
    def _has_profit_continuation_structure(row: Dict[str, Any]) -> bool:
        profile = structured_signal_profile(row)
        details = profile.get('structured_component_details') if isinstance(profile.get('structured_component_details'), dict) else {}
        adj = ranking_basis_adjustment_components(row)
        pe = float(safe_float(adj.get('profit_edge_score')) or 0.0)
        cont = float((adj.get('boosts') or {}).get('profit_continuation_soft') or 0.0)
        shell = float((adj.get('penalties') or {}).get('hot_fund_shell_without_profit_edge') or 0.0)
        gene = max(
            float(safe_float(row.get('continuation_gene_score')) or 0.0),
            float(safe_float(profile.get('continuation_gene_score')) or 0.0),
            float(safe_float(details.get('continuation_gene_score')) or 0.0),
        )
        setup = str(row.get('setup_type') or profile.get('setup_type') or '')
        proxy = row.get('sector_yesterday_limitup_gene_proxy')
        proxy_ok = isinstance(proxy, dict) and str(proxy.get('status') or '').upper() in (
            'PROXY', 'PASS', 'OK', 'CONFIRMED'
        )
        if pe >= 0.25 or cont >= 0.30:
            return True
        if gene >= 0.35 or proxy_ok:
            return True
        # Entry-layer relaxation only: a direct stock-level limitup reason on
        # LIMIT_STRENGTH is enough to preserve the candidate for final gates.
        # The shell penalty remains active in ranking; eligibility still applies
        # regulatory, buyability, and risk hard blocks.
        reason_status = str(
            row.get('limitup_reason_status')
            or details.get('limitup_reason_status')
            or ''
        ).upper()
        reason_class = str(
            classify_limitup_reason_evidence(row).get('limitup_reason_evidence_class')
            or ''
        ).upper()
        direct_reason = (
            reason_status in ('PASS', 'OK', 'CONFIRMED', 'DIRECT')
            and reason_class == 'DIRECT'
        )
        if (
            direct_reason
            and setup in ('LIMIT_STRENGTH', 'L2_LIMIT_STRENGTH', 'STRONG_LIMITUP_CAPTURE')
            and shell >= 0.55
        ):
            return True
        if setup in ('LIMIT_STRENGTH', 'L2_LIMIT_STRENGTH', 'STRONG_LIMITUP_CAPTURE') and shell < 0.55:
            return True
        return False

    profit_continuation_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and not bool(row.get('sealed_limit_up'))
        and (_signal_pct(row) is not None and 7.0 <= float(_signal_pct(row)) <= 10.5)
        and candidate_stage_for_row_local(row) in ('high_7_to_9', 'near_limit_9_plus', 'mid_5_to_7')
        and _has_profit_continuation_structure(row)
    ]
    # 横盘突破：昨日涨幅接近0（-1%~+1%），但成交量放大
    # 逻辑：横盘后放量可能预示突破
    flat_breakout_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (_signal_pct(row) is not None and -1.0 <= _signal_pct(row) <= 1.0)
        and (safe_float(row.get('amount')) or 0) > 1e9  # 成交额>10亿
        and (structured_signal_profile(row)['close_position_score'] or 1.0) < 0.6  # 位置不高
    ]
    # 个股催化剂：有龙虎榜或公告的股票
    # 逻辑：消息面驱动的个股可能有独立行情
    individual_catalyst_rows = [
        row for row in enriched_rows
        if row_is_searchable(row)
        and (
            'LHB' in str(row.get('source_layers', []))
            or structured_signal_profile(row)['search_layer_hint'] == 'news_catalyst_low_position'
        )
    ]

    def low_position_ambush_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
        profile = structured_signal_profile(row)
        details = profile['structured_component_details'] if isinstance(profile['structured_component_details'], dict) else {}
        return (
            profile['sector_opportunity_score'] or 0.0,
            profile['low_position_catalyst_score'] or 0.0,
            safe_float(row.get('amplitude')) or 0.0,
            safe_float(row.get('turnover_rate')) or 0.0,
            safe_float(row.get('amount')) or 0.0,
            profile['close_position_score'] or 0.0,
        )

    def sector_contrarian_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """板块内弱势反转排序：板块强度高 + 股票弱势 = 反转机会大"""
        profile = structured_signal_profile(row)
        sector_score = profile['sector_opportunity_score'] or 0.0
        # 弱势程度：signal_pct越低、position越低，反转潜力越大
        weakness = max(0, 1.0 - (safe_float(row.get('signal_pct')) or 0) / 10.0)
        position_weakness = max(0, 1.0 - (profile['close_position_score'] or 0.5))
        return (sector_score, weakness * 0.5 + position_weakness * 0.5, safe_float(row.get('amount')) or 0.0, 0.0)

    def sector_follower_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """板块跟风股排序：板块强 + 动量适中 + 位置不高 = 逆袭机会"""
        profile = structured_signal_profile(row)
        sector_score = profile['sector_opportunity_score'] or 0.0
        # 动量适中（不要太高避免追高，不要太低避免弱势）
        signal_pct = safe_float(row.get('signal_pct')) or 0
        momentum_score = max(0, 10 - abs(signal_pct - 3.0))  # 3%附近最优
        # 位置不高
        position_score = max(0, 1.0 - (profile['close_position_score'] or 0.5))
        # 成交量（越大越好）
        amount_score = min(1.0, (safe_float(row.get('amount')) or 0) / 1e9)
        return (sector_score, momentum_score * 0.4 + position_score * 0.3 + amount_score * 0.3, 0.0, 0.0)

    def momentum_continuation_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """动量延续排序：涨幅高 + 成交量大 + 位置不过高"""
        signal_pct = safe_float(row.get('signal_pct')) or 0
        amount = safe_float(row.get('amount')) or 0
        profile = structured_signal_profile(row)
        position = profile['close_position_score'] or 0.5
        return (
            safe_float(row.get('structured_priority_score')) or 0.0,
            profile['structured_score'] or 0.0,
            signal_pct,
            min(10.0, amount / 1e9) * max(0, 1.0 - position),
        )

    def flat_breakout_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float]:
        """横盘突破排序：成交量大 + 位置低 + 涨幅接近0"""
        amount = safe_float(row.get('amount')) or 0
        profile = structured_signal_profile(row)
        position = profile['close_position_score'] or 0.5
        signal_pct = abs(safe_float(row.get('signal_pct')) or 0)
        return (min(10.0, amount / 1e9), max(0, 1.0 - position), max(0, 1.0 - signal_pct))

    def individual_catalyst_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float]:
        """个股催化剂排序：有龙虎榜 + 有公告 + 位置不高"""
        profile = structured_signal_profile(row)
        has_lhb = 1.0 if 'LHB' in str(row.get('source_layers', [])) else 0.0
        has_news = 1.0 if profile['search_layer_hint'] == 'news_catalyst_low_position' else 0.0
        position = profile['close_position_score'] or 0.5
        return (has_lhb + has_news, max(0, 1.0 - position), safe_float(row.get('amount')) or 0)

    # Soft similar-loss demotion for formal ranking: attach pgvector neighbors
    # before formal_high_score sort (bounded; never hard gate / force_pick).
    formal_trade_date = str(
        bundle_context.get('trade_date')
        or bundle_context.get('date')
        or (bundle_context.get('market_snapshot') or {}).get('trade_date')
        or ''
    )
    try:
        from xiaogu_case_vector_store import attach_similar_cases_soft_bias
        for _row in shadow_high_rows:
            if not isinstance(_row, dict):
                continue
            if not _row.get('trade_date') and formal_trade_date:
                _row['trade_date'] = formal_trade_date
            attach_similar_cases_soft_bias(
                _row,
                exclude_trade_date=formal_trade_date or None,
                limit=5,
            )
    except Exception:
        pass

    layer_specs = [
        ('limitup_capture', limitup_capture_rows, limitup_capture_layer_sort_key),
        # Profit-first entry: near-limit continuation before defensive low-position layers.
        ('profit_continuation', profit_continuation_rows, formal_layer_sort_key),
        ('momentum_continuation', momentum_continuation_rows, momentum_continuation_sort_key),
        ('news_catalyst_low_position', news_catalyst_rows, sector_layer_sort_key),
        ('sector_catalyst_low_position', sector_catalyst_rows, sector_layer_sort_key),
        ('sector_contrarian', sector_contrarian_rows, sector_contrarian_sort_key),
        ('sector_follower', sector_follower_rows, sector_follower_sort_key),
        ('individual_catalyst', individual_catalyst_rows, individual_catalyst_sort_key),
        ('flat_breakout', flat_breakout_rows, flat_breakout_sort_key),
        ('intraday_alert_reversal', intraday_alert_rows, early_layer_sort_key),
        ('underwater_reversal', underwater_rows, underwater_layer_sort_key),
        ('low_position_ambush', low_position_ambush_rows, low_position_ambush_sort_key),
        ('structured_sector', structured_sector_rows, sector_layer_sort_key),
        ('formal_high_score', shadow_high_rows, formal_layer_sort_key),
    ]

    search_rows: List[Dict[str, Any]] = []
    seen_symbols: set[str] = set()
    layer_counts: Dict[str, int] = {}
    for layer_name, layer_rows, sort_key in layer_specs:
        layer_sorted = sorted(layer_rows, key=sort_key, reverse=True)[:RESEARCH_BASKET_SIZE]
        layer_counts[layer_name] = len(layer_sorted)
        for row in layer_sorted:
            symbol = symbol_for(row)
            if not symbol or symbol in seen_symbols:
                continue
            selected_row = dict(row)
            eligibility = paper_pick_eligibility_profile(selected_row, bundle_context)
            selected_row['paper_pick_eligibility'] = eligibility
            exclusion_reasons = official_target_exclusion_reasons(selected_row, bundle_context)
            selected_row['official_target_excluded'] = bool(exclusion_reasons)
            selected_row['official_target_exclusion_reasons'] = exclusion_reasons
            if exclusion_reasons:
                selected_row['diagnostic_only'] = True
            selected_row['structured_formal_paper_pick_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
            selected_row['formal_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
            selected_row['search_layer_hint'] = structured_signal_profile(row)['search_layer_hint'] or str(row.get('search_layer_hint') or '')
            selected_row['search_layer'] = layer_name
            search_rows.append(selected_row)
            seen_symbols.add(symbol)

    paper_pick_candidate_stage_distribution = Counter(candidate_stage_for_row_local(row) for row in search_rows)
    candidate_stage_blocker_distribution: Dict[str, Counter] = {}
    for row in enriched_rows:
        blockers = formal_blockers_for_row(row)
        if not blockers:
            continue
        stage = candidate_stage_for_row_local(row)
        bucket = candidate_stage_blocker_distribution.setdefault(stage, Counter())
        for reason in blockers:
            bucket[block_reason_bucket(reason)] += 1

    daily_ticket_search_result = {
        'searched_layers': [layer_name for layer_name, _rows, _key in layer_specs],
        'layer_counts': layer_counts,
        'first_paper_pick_layer': None,
        'no_pick_reason_if_none': 'PENDING_EVALUATION',
    }
    first_clean_row, first_clean_meta = select_first_clean_with_formal_challenge(
        search_rows, bundle_context
    )
    if isinstance(first_clean_row, dict) and first_clean_meta.get('challenged'):
        daily_ticket_search_result['first_clean_challenge'] = first_clean_meta
    rank_alignment_diagnostic = build_rank_alignment_diagnostic(
        enriched_rows,
        first_clean_row,
        top_n=RESEARCH_BASKET_SIZE,
    )
    rank_alignment_diagnostic['search_row_count'] = len(search_rows)
    rank_alignment_diagnostic['first_clean_meta'] = first_clean_meta
    blocked_candidate_diagnostics = [
        {
            'symbol': symbol_for(row),
            'name': row.get('name'),
            'rank': row.get('rank'),
            'pool_rank': row.get('pool_rank'),
            'formal_rank': row.get('formal_rank'),
            'search_layer': row.get('search_layer'),
            'official_target_exclusion_reasons': row.get('official_target_exclusion_reasons') or [],
        }
        for row in search_rows
        if row.get('official_target_excluded')
    ]
    return {
        'search_rows': search_rows,
        'first_clean_row': first_clean_row,
        'formal_ranked_pool': enriched_rows,
        'current_day_tradable_filter': current_day_tradable_filter,
        'rank_alignment_diagnostic': rank_alignment_diagnostic,
        'first_clean_challenge_meta': first_clean_meta,
        'blocked_candidate_diagnostics': blocked_candidate_diagnostics,
        'official_target_excluded_count': len(blocked_candidate_diagnostics),
        'first_excluded_candidate': blocked_candidate_diagnostics[0] if blocked_candidate_diagnostics else None,
        'paper_pick_candidate_stage_distribution': paper_pick_candidate_stage_distribution,
        'candidate_stage_blocker_distribution': candidate_stage_blocker_distribution,
        'daily_ticket_search_result': daily_ticket_search_result,
    }


def build_weak_market_shadow_ticket(selected_rows: List[Dict[str, Any]], bundle: Dict[str, Any], target_date: str) -> Dict[str, Any] | None:
    market_snapshot = bundle.get('market_snapshot') or {}
    market_breadth = safe_float(market_snapshot.get('market_breadth_up_pct'))
    if market_breadth is None or market_breadth >= WEAK_MARKET_SHADOW_BREADTH_GATE:
        return None
    if not selected_rows:
        return {
            'candidate': None,
            'would_pick_without_main_board_breadth_gate': False,
            'blocker': f'main_board_breadth_too_low:{market_breadth:.2f}',
            'paper_only_observation': True,
            'market_breadth_up_pct': market_breadth,
        }

    shadow_candidate: Dict[str, Any] | None = None
    shadow_decision = ''
    shadow_reason = ''
    shadow_flags: List[str] = []
    for candidate in selected_rows:
        decision, _symbol, reason, _features, flags = _cached_decision_for_candidate(candidate, bundle, target_date)
        shadow_candidate = candidate
        shadow_decision = decision
        shadow_reason = reason
        shadow_flags = flags
        if decision == 'PAPER_PICK':
            break

    return {
        'candidate': shadow_candidate,
        'would_pick_without_main_board_breadth_gate': shadow_decision == 'PAPER_PICK',
        'blocker': f'main_board_breadth_too_low:{market_breadth:.2f}',
        'paper_only_observation': True,
        'market_breadth_up_pct': market_breadth,
        'decision': shadow_decision,
        'decision_reason': shadow_reason,
        'decision_flags': shadow_flags,
    }


MISSING_INFORMATION_COVERAGE_AUDIT = {
    'status': 'MISSING_FROM_SCAN_SUMMARY',
    'reason': 'scan output generated before scanner upgrade or source unavailable',
}


def scan_summary_information_coverage_audit(summary: Dict[str, Any] | None) -> Dict[str, Any]:
    if isinstance(summary, dict) and isinstance(summary.get('information_coverage_audit'), dict):
        audit = dict(summary['information_coverage_audit'])
        auxiliary_sources = {
            name: dict(record)
            for name, record in (audit.get('auxiliary_sources') or {}).items()
            if isinstance(record, dict)
        }
        quarantined_domains = ('block_trades', 'trading_halts', 'popularity_rank')
        for name in quarantined_domains:
            record = auxiliary_sources.get(name)
            if not record:
                continue
            raw_count = int(record.get('raw_count') or 0)
            if raw_count > 0:
                record.update({
                    'status': 'PROXY',
                    'source_type': 'PROXY',
                    'quality_status': 'PROXY_QUARANTINED',
                    'production_use': 'DISABLED_UNTIL_SPECIALIZED_SOURCE',
                    'quality_gaps': ['specialized_source_unavailable'],
                    'used_for_scoring': False,
                    'used_for_risk_filter': False,
                    'hard_block': False,
                })
            else:
                record.update({
                    'status': 'MISSING',
                    'source_type': 'MISSING',
                    'quality_status': 'MISSING',
                    'production_use': 'UNAVAILABLE',
                    'quality_gaps': ['source_missing'],
                    'used_for_scoring': False,
                    'used_for_risk_filter': False,
                    'hard_block': False,
                })
            auxiliary_sources[name] = record
        if auxiliary_sources:
            audit['auxiliary_sources'] = auxiliary_sources
        non_missing_domains = dict(audit.get('non_missing_domains') or {})
        if 'block_trades' in auxiliary_sources:
            non_missing_domains['block_trades'] = auxiliary_sources['block_trades']['status']
        if 'trading_halts' in auxiliary_sources:
            non_missing_domains['trading_halts'] = auxiliary_sources['trading_halts']['status']
        if 'popularity_rank' in auxiliary_sources:
            non_missing_domains['popularity_rank'] = auxiliary_sources['popularity_rank']['status']
        yesterday = dict(audit.get('yesterday_limitup_proxy') or {})
        yesterday_count = int(yesterday.get('raw_count') or 0)
        yesterday.update({
            'source_status': 'DIRECT' if yesterday_count else 'MISSING',
            'source_type': 'DIRECT' if yesterday_count else 'MISSING',
            'quality_status': 'PASS' if yesterday_count else 'MISSING',
            'production_use': 'CONTINUATION_PROXY_ONLY' if yesterday_count else 'UNAVAILABLE',
            'evidence_role': 'CONTINUATION_PROXY',
            'quality_gaps': [] if yesterday_count else ['source_missing'],
        })
        audit['yesterday_limitup_proxy'] = yesterday
        non_missing_domains['yesterday_limitup_proxy'] = 'DIRECT' if yesterday_count else 'MISSING'
        one_word = dict(audit.get('yesterday_one_word_limitup_proxy') or {})
        one_word_count = int(one_word.get('raw_count') or one_word.get('one_word_raw_count') or 0)
        source_mode = str(one_word.get('source_mode') or one_word.get('one_word_source_mode') or '')
        one_word_type = 'DIRECT' if one_word_count and source_mode == 'explicit_source' else (
            'DERIVED' if one_word_count else 'MISSING'
        )
        one_word.update({
            'source_status': one_word_type,
            'source_type': one_word_type,
            'quality_status': 'PASS' if one_word_type == 'DIRECT' else ('DERIVED' if one_word_type == 'DERIVED' else 'MISSING'),
            'production_use': 'CONTINUATION_PROXY_ONLY' if one_word_count else 'UNAVAILABLE',
            'evidence_role': 'CONTINUATION_PROXY',
            'quality_gaps': (
                ['derived_from_yesterday_limitup'] if one_word_type == 'DERIVED'
                else ([] if one_word_count else ['source_missing'])
            ),
            'source_mode': source_mode,
        })
        audit['yesterday_one_word_limitup_proxy'] = one_word
        non_missing_domains['yesterday_one_word_limitup_proxy'] = one_word_type
        audit['non_missing_domains'] = non_missing_domains
        hsgt_evidence = dict(audit.get('hsgt_evidence') or {})
        holdings_count = int(hsgt_evidence.get('holdings_count') or 0)
        fallback_sources = list(hsgt_evidence.get('proxy_sources') or [])
        fallback_available = bool(hsgt_evidence.get('fallback_available')) or bool(fallback_sources)
        fallback_used = bool(hsgt_evidence.get('fallback_used')) or bool(fallback_available and not holdings_count)
        hsgt_evidence.update({
            'source_type': 'DIRECT' if holdings_count else ('FALLBACK' if fallback_used else 'MISSING'),
            'quality_status': 'PASS' if holdings_count else ('PROXY' if fallback_used else 'MISSING'),
            'production_use': 'ENABLED' if holdings_count else 'OPTIONAL_FALLBACK_ONLY',
            'fallback_available': fallback_available,
            'fallback_used': fallback_used,
            'proxy_available': fallback_used,
            'hard_block': False,
        })
        audit['hsgt_evidence'] = hsgt_evidence
        return audit
    return dict(MISSING_INFORMATION_COVERAGE_AUDIT)


MISSING_SECTOR_CATALYST_DIAGNOSTICS = {
    'status': 'MISSING_FROM_SCAN_SUMMARY',
    'reason': 'scan output generated before sector catalyst diagnostics upgrade or source unavailable',
}


def scan_summary_sector_catalyst_diagnostics(summary: Dict[str, Any] | None) -> Dict[str, Any]:
    if isinstance(summary, dict) and isinstance(summary.get('sector_catalyst_diagnostics'), dict):
        return dict(summary['sector_catalyst_diagnostics'])
    return dict(MISSING_SECTOR_CATALYST_DIAGNOSTICS)


def attach_scan_summary_information_coverage_audit(bundle: Dict[str, Any], summary_path: Path | None, summary: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return bundle
    bundle['scan_summary_path'] = str(summary_path) if summary_path else ''
    bundle['scan_summary_source_time'] = str(summary.get('source_time', '')) if isinstance(summary, dict) else ''
    bundle['information_coverage_audit'] = scan_summary_information_coverage_audit(summary)
    hsgt_diagnostics = dict(bundle.get('hsgt_diagnostics') or {})
    holdings_count = int(hsgt_diagnostics.get('holdings_count') or 0)
    proxy_sources = list(hsgt_diagnostics.get('proxy_sources') or [])
    fallback_available = bool(hsgt_diagnostics.get('fallback_available')) or bool(proxy_sources)
    fallback_used = bool(hsgt_diagnostics.get('fallback_used')) or bool(fallback_available and not holdings_count)
    hsgt_diagnostics.update({
        'fallback_available': fallback_available,
        'fallback_used': fallback_used,
        'proxy_available': fallback_used,
        'proxy_sources': proxy_sources,
        'source_type': 'DIRECT' if holdings_count else ('FALLBACK' if fallback_used else 'MISSING'),
        'quality_status': 'PASS' if holdings_count else ('PROXY' if fallback_used else 'MISSING'),
        'production_use': 'ENABLED' if holdings_count else 'OPTIONAL_FALLBACK_ONLY',
        'hard_block': False,
    })
    bundle['hsgt_diagnostics'] = hsgt_diagnostics
    source_status = dict(bundle.get('source_status') or {})
    proxy_api_sources = {
        name: dict(record)
        for name, record in (source_status.get('proxy_api_sources') or {}).items()
        if isinstance(record, dict)
    }
    for name in ('block_trades', 'trading_halts', 'popularity_rank'):
        if name in proxy_api_sources:
            proxy_api_sources[name].update({
                'status': 'PROXY',
                'quality_status': 'PROXY_QUARANTINED',
                'production_use': 'DISABLED_UNTIL_SPECIALIZED_SOURCE',
                'specialized_datacenter_source': False,
                'hard_block': False,
            })
    if proxy_api_sources:
        source_status['proxy_api_sources'] = proxy_api_sources
        bundle['source_status'] = source_status
    return bundle


def build_research_basket_from_latest_scan(
    date: str,
    asof_time: str | None = None,
    *,
    historical_replay: bool = False,
    historical_summary_path: Path | None = None,
) -> Dict[str, Any]:
    # Directory labels are scheduling metadata only. The shared loader selects
    # the latest valid same-day direct-API snapshot by source_time. The
    # historical compatibility flag is only used by non-active replay callers.
    latest = load_latest_eastmoney_scan(
        date,
        asof_time,
        historical_replay=historical_replay,
        historical_summary_path=historical_summary_path,
    )
    if latest is None:
        return {'available': False, 'reason': 'NO_CANONICAL_DIRECT_API_SCAN'}
    runner_summary, summary = latest
    try:
        bundle = _bundle_from_scan_summary(runner_summary, summary)
        if isinstance(bundle, dict):
            bundle['ranking_view'] = 'main_force_behavior_chain'
        return bundle
    except Exception:
        return {'available': False, 'reason': 'NO_CANONICAL_DIRECT_API_SCAN'}













def latest_completed_trading_day(now: dt.datetime | None = None) -> dt.date:
    """Return the latest trading day. Uses current date during trading hours (9:15-15:30),
    falls back to previous trading day only when market is closed (before 9:15 or after 15:30)."""
    current = now or dt.datetime.now()
    day = current.date()
    t = current.time()
    # Only fall back to yesterday when market is fully closed (before 9:15 or after 15:30)
    if t < dt.time(9, 15) or t >= dt.time(15, 30):
        # If after 15:30, today's data is complete - use today
        # If before 9:15, today's data doesn't exist yet - use yesterday
        if t < dt.time(9, 15):
            day -= dt.timedelta(days=1)
    # Weekend handling
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day



def _ensure_current_realtime_bundle_path(date: str, bundle: Dict[str, Any], summary_path: Path | None = None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return bundle
    updated = dict(bundle)
    candidate_path = CANDIDATE_BUNDLE_ROOT / date / f'{date}_api_scan_v2_realtime.json'
    if summary_path is not None:
        updated['scan_summary_path'] = str(summary_path)
        updated.setdefault('source_evidence', {})
        if isinstance(updated['source_evidence'], dict):
            updated['source_evidence'] = dict(updated['source_evidence'])
            updated['source_evidence'].setdefault('summary_path', str(summary_path))
    updated['_bundle_path'] = str(candidate_path)
    try:
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(candidate_path, updated)
    except Exception:
        pass
    return updated


def core_market_source_gate(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Require complete, persisted intraday sentiment sources before a paper pick."""
    market_snapshot = bundle.get('market_snapshot') if isinstance(bundle.get('market_snapshot'), dict) else {}
    source_status = bundle.get('source_status') if isinstance(bundle.get('source_status'), dict) else {}
    if not source_status:
        source_status = market_snapshot.get('source_status') if isinstance(market_snapshot.get('source_status'), dict) else {}
    hard_block = market_snapshot.get('hard_block_source_status')
    if not isinstance(hard_block, dict):
        hard_block = bundle.get('hard_block_source_status') if isinstance(bundle.get('hard_block_source_status'), dict) else {}

    scanner_provenance = bool(bundle.get('scan_summary_path'))
    if not scanner_provenance:
        return {
            'status': 'BLOCK',
            'missing_sources': ['scan_summary_path'],
            'flags': ['DIRECT_API_SCAN_PROVENANCE_MISSING'],
        }

    core_pools = source_status.get('core_sentiment_pools')
    if not isinstance(core_pools, dict):
        return {
            'status': 'BLOCK',
            'missing_sources': ['core_sentiment_pools'],
            'flags': ['CORE_SENTIMENT_POOL_STATUS_MISSING'],
        }
    if str(core_pools.get('status') or '') != 'PASS':
        return {
            'status': 'BLOCK',
            'missing_sources': list(core_pools.get('missing_sources') or ['core_sentiment_pools']),
            'flags': list(core_pools.get('flags') or ['CORE_SENTIMENT_POOL_STATUS_NOT_PASS']),
        }

    persistence = source_status.get('scan_snapshot_persistence')
    if not isinstance(persistence, dict) or str(persistence.get('status') or '') != 'PASS':
        return {
            'status': 'BLOCK',
            'missing_sources': ['postgres_market_snapshot'],
            'flags': ['SCAN_SNAPSHOT_PERSISTENCE_NOT_PASS'],
        }
    if hard_block and str(hard_block.get('status') or '') != 'PASS':
        return {
            'status': 'BLOCK',
            'missing_sources': list(hard_block.get('missing_sources') or ['hard_block_source_status']),
            'flags': list(hard_block.get('flags') or ['HARD_BLOCK_SOURCE_STATUS_NOT_PASS']),
        }
    return {'status': 'PASS', 'missing_sources': [], 'flags': []}


def evaluate_candidate_bundle(bundle: Dict[str, Any], target_date: str, allow_stale_data: bool = False) -> Tuple[str, str, str, Dict[str, Any], List[str]]:
    """Return decision, symbol, reason, candidate_features, risk_flags."""
    from xiaogu_forward_eligibility import (
        filter_current_day_tradable_candidates,
        filter_t1_profit_candidates,
    )

    if not bundle.get('available'):
        return 'NO_PICK', '', bundle.get('reason', 'NO_VERIFIED_CANDIDATE'), {}, ['NO_VERIFIED_CANDIDATE_BUNDLE']
    if bundle.get('strict_production_chain'):
        validation = bundle.get('production_chain_validation')
        if not isinstance(validation, dict):
            validation = validate_active_production_chain(bundle, target_date)
        if not validation.get('valid') or bundle.get('production_chain_blocked'):
            flags = ['ACTIVE_PRODUCTION_CHAIN_NOT_VALID', *list(validation.get('errors') or [])]
            return 'NO_PICK', '', 'ACTIVE_PRODUCTION_CHAIN_NOT_VALID:' + ';'.join(unique_text_values(flags)), {}, unique_text_values(flags)
    governance_flags = active_chain_governance_flags(bundle, target_date, allow_stale_data=allow_stale_data)
    if governance_flags:
        flags = unique_text_values(['ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS', *governance_flags])
        return 'NO_PICK', '', 'ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS:' + ';'.join(governance_flags), {}, flags

    def finalize_daily_ticket_search_result(candidate: Dict[str, Any] | None, decision: str) -> None:
        daily_ticket_search_result = bundle.get('daily_ticket_search_result')
        if not isinstance(daily_ticket_search_result, dict):
            return
        if decision == 'PAPER_PICK' and isinstance(candidate, dict):
            daily_ticket_search_result['first_paper_pick_layer'] = candidate.get('search_layer') or None
            daily_ticket_search_result['no_pick_reason_if_none'] = ''
            return
        daily_ticket_search_result['first_paper_pick_layer'] = None
        daily_ticket_search_result['no_pick_reason_if_none'] = 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS'

    candidates: List[Dict[str, Any]] = []
    if isinstance(bundle.get('paper_scoring_candidates'), list) and bundle['paper_scoring_candidates']:
        candidates.extend([c for c in bundle['paper_scoring_candidates'] if isinstance(c, dict)])
    elif not bundle.get('strict_production_chain') and isinstance(bundle.get('candidate'), dict) and symbol_for(bundle['candidate']):
        candidates.append(bundle['candidate'])
    candidates, current_day_tradable_filter = filter_t1_profit_candidates(
        candidates,
        bundle,
        enforce=bool(bundle.get('t1_profit_gate_enabled')),
    )
    bundle['current_day_tradable_filter'] = current_day_tradable_filter
    if not candidates:
        return 'NO_PICK', '', 'BUNDLE_HAS_NO_PAPER_SCORING_CANDIDATE', {}, ['NO_PAPER_SCORING_CANDIDATE']

    _capital_flow_lookup = build_capital_flow_lookup(bundle)
    sizing = paper_sizing_context(bundle)
    selection_trace: List[Dict[str, Any]] = []
    # There is one production sorter. Shadow/research paths may diagnose
    # alternatives, but they cannot create a competing PAPER_PICK priority.
    priority_labels = [f'formal_sort_{idx}' for idx in range(24)]

    def official_pick_priority(candidate: Dict[str, Any], features: Dict[str, Any]) -> Tuple[float, ...]:
        del features
        return formal_candidate_sort_key(candidate)

    first_reject: Tuple[str, str, str, Dict[str, Any], List[str]] | None = None
    paper_pick_results: List[Tuple[Tuple[float, ...], Dict[str, Any], Tuple[str, str, str, Dict[str, Any], List[str]]]] = []
    held_position_results: List[Tuple[Tuple[float, ...], Dict[str, Any], Tuple[str, str, str, Dict[str, Any], List[str]]]] = []
    for candidate in candidates:
        decision, symbol, reason, features, flags = _cached_decision_for_candidate(candidate, bundle, target_date, allow_stale_data=allow_stale_data)
        if decision == 'PAPER_PICK':
            priority = official_pick_priority(candidate, features)
            position_profile = position_profile_for_candidate(candidate, sizing)
            position_action = position_management_action(position_profile)
            trace_entry = {
                'symbol': symbol or symbol_for(candidate),
                'name': candidate.get('name'),
                'rank': candidate.get('rank'),
                'final_score': candidate.get('final_score'),
                'official_priority': dict(zip(priority_labels, priority)),
                'already_held': bool(position_profile.get('already_held')),
                'skipped_for_new_buy': bool(position_profile.get('already_held')),
                'position_management_action': position_action,
            }
            selection_trace.append(trace_entry)
            features = dict(features)
            features['current_position_profile'] = position_profile
            features['already_held'] = bool(position_profile.get('already_held'))
            features['position_profit_pct'] = position_profile.get('profit_pct')
            features['position_management_action'] = position_action
            if position_profile.get('already_held'):
                features['position_management_watch'] = {
                    'symbol': symbol or symbol_for(candidate),
                    'name': candidate.get('name'),
                    'reason': 'already_held_not_new_buy_ticket',
                    'current_position_profile': position_profile,
                    'position_management_action': position_action,
                }
                flags = unique_text_values([*flags, 'ALREADY_HELD_POSITION_REVIEW_ONLY'])
                held_position_results.append((priority, candidate, ('NO_PICK', symbol, reason, features, flags)))
                continue
            paper_pick_results.append((priority, candidate, (decision, symbol, reason, features, flags)))
            continue
        if first_reject is None:
            first_reject = (decision, symbol, reason, features, flags)

    bundle['paper_pick_selection_trace'] = selection_trace

    if paper_pick_results:
        _priority, candidate, result = max(paper_pick_results, key=lambda item: item[0])
        decision, symbol, reason, features, flags = result
        finalize_daily_ticket_search_result(candidate, decision)
        return decision, symbol, reason, features, flags

    if held_position_results:
        _priority, candidate, result = max(held_position_results, key=lambda item: item[0])
        _held_decision, _held_symbol, _held_reason, features, flags = result
        bundle['held_position_results'] = [item[2][3].get('position_management_watch') for item in held_position_results]
        finalize_daily_ticket_search_result(None, 'NO_PICK')
        return 'NO_PICK', '', 'HELD_POSITION_REVIEW_ONLY:already_held_no_new_buy_ticket', features, flags

    if first_reject is not None:
        decision, symbol, reason, features, flags = first_reject
        bundle['first_rejected_candidate_diagnostic'] = {
            'decision': decision,
            'symbol': symbol,
            'reason': reason,
            'features': features,
            'flags': flags,
        }
        finalize_daily_ticket_search_result(None, 'NO_PICK')
        return decision, symbol, reason, features, flags
    finalize_daily_ticket_search_result(None, 'NO_PICK')
    return 'NO_PICK', '', 'BUNDLE_HAS_NO_PAPER_SCORING_CANDIDATE', {}, ['NO_PAPER_SCORING_CANDIDATE']


# --- responsibility extraction: bind all owners, then re-export compatibly ---
import xiaogu_forward_features as _forward_features
import xiaogu_forward_ranking as _forward_ranking
import xiaogu_forward_diagnostics as _forward_diagnostics
import xiaogu_forward_snapshot as _forward_snapshot

_analyze_news_sentiment = _forward_features._analyze_news_sentiment
_get_news_analysis = _forward_features._get_news_analysis
_get_sector_names = _forward_features._get_sector_names
_historical_t1_return_map_for_date = _forward_features._historical_t1_return_map_for_date
_load_news_kuaixun = _forward_features._load_news_kuaixun
_load_sector_names = _forward_features._load_sector_names
_normalize_news_kuaixun_rows = _forward_features._normalize_news_kuaixun_rows
_parse_flow_amount = _forward_features._parse_flow_amount
_strip_replay_production_contributions = _forward_features._strip_replay_production_contributions
archetype_score_adjustments = _forward_features.archetype_score_adjustments
block_reason_bucket = _forward_features.block_reason_bucket
build_capital_flow_lookup = _forward_features.build_capital_flow_lookup
build_research_panel = _forward_features.build_research_panel
build_research_signals_from_profile = _forward_features.build_research_signals_from_profile
bundle_metric = _forward_features.bundle_metric
candidate_capital_risk_profile = _forward_features.candidate_capital_risk_profile
candidate_rank_value = _forward_features.candidate_rank_value
candidate_theme_tag_set = _forward_features.candidate_theme_tag_set
candidate_theme_text = _forward_features.candidate_theme_text
classify_limitup_reason_evidence = _forward_features.classify_limitup_reason_evidence
continuation_gene_evidence = _forward_features.continuation_gene_evidence
contrarian_re_score = _forward_features.contrarian_re_score
detect_pool_hollow_theme_tags = _forward_features.detect_pool_hollow_theme_tags
early_opportunity_score_for_row = _forward_features.early_opportunity_score_for_row
fetch_candidate_fund_flow_live = _forward_features.fetch_candidate_fund_flow_live
formal_blockers_for_row = _forward_features.formal_blockers_for_row
inferred_vei_phase_d_tags = _forward_features.inferred_vei_phase_d_tags
inject_capital_flow_boost = _forward_features.inject_capital_flow_boost
inject_live_fund_flow_into_candidates = _forward_features.inject_live_fund_flow_into_candidates
limitup_probability_proxy_components = _forward_features.limitup_probability_proxy_components
limitup_quality_block_reason = _forward_features.limitup_quality_block_reason
limitup_reason_supports_hard_confirmation = _forward_features.limitup_reason_supports_hard_confirmation
load_mainline_fund_flow_context = _forward_features.load_mainline_fund_flow_context
load_profit_shadow_watchlist = _forward_features.load_profit_shadow_watchlist
market_adaptive_context = _forward_features.market_adaptive_context
market_adaptive_thresholds = _forward_features.market_adaptive_thresholds
normalize_bundle_vei_tags = _forward_features.normalize_bundle_vei_tags
normalize_tag_list = _forward_features.normalize_tag_list
normalize_vei_phase_d_tags = _forward_features.normalize_vei_phase_d_tags
normalized_block_bucket = _forward_features.normalized_block_bucket
paper_pick_risk_explanation_gate = _forward_features.paper_pick_risk_explanation_gate
parse_capital_flow_from_content_records = _forward_features.parse_capital_flow_from_content_records
replay_only_sector_opportunity = _forward_features.replay_only_sector_opportunity
scan_summary_for_bundle = _forward_features.scan_summary_for_bundle
sector_gate_threshold_for_market = _forward_features.sector_gate_threshold_for_market
shadow_risk_profile = _forward_features.shadow_risk_profile
signal_stage_bucket = _forward_features.signal_stage_bucket
social_confirmation_profile = _forward_features.social_confirmation_profile
soft_mainline_fund_bias = _forward_features.soft_mainline_fund_bias
stock_capital_flow_by_code_from_payload = _forward_features.stock_capital_flow_by_code_from_payload
strong_sector_theme_partial_aux_exception_allowed = _forward_features.strong_sector_theme_partial_aux_exception_allowed
structured_component = _forward_features.structured_component
structured_formal_impact_summary = _forward_features.structured_formal_impact_summary
structured_observation_candidate = _forward_features.structured_observation_candidate
structured_signal_present = _forward_features.structured_signal_present
structured_signal_profile = _forward_features.structured_signal_profile
theme_token_hits = _forward_features.theme_token_hits
why_not_formal_candidate = _forward_features.why_not_formal_candidate
ensure_leader_chain_main_theme = _forward_ranking.ensure_leader_chain_main_theme
formal_candidate_sort_key = _forward_ranking.formal_candidate_sort_key
ranking_basis_adjustment_components = _forward_ranking.ranking_basis_adjustment_components
select_first_clean_with_formal_challenge = _forward_ranking.select_first_clean_with_formal_challenge
_reason_parts = _forward_diagnostics._reason_parts
_source_consumption_domain_summary = _forward_diagnostics._source_consumption_domain_summary
build_candidate_consumption_summary = _forward_diagnostics.build_candidate_consumption_summary
build_candidate_diagnostic_card = _forward_diagnostics.build_candidate_diagnostic_card
build_daily_best_paper_watch = _forward_diagnostics.build_daily_best_paper_watch
build_no_pick_candidate_diagnostics = _forward_diagnostics.build_no_pick_candidate_diagnostics
build_source_consumption_summary = _forward_diagnostics.build_source_consumption_summary
candidate_can_afford_one_lot = _forward_diagnostics.candidate_can_afford_one_lot
candidate_is_selection_eligible = _forward_diagnostics.candidate_is_selection_eligible
closest_to_pick_candidate_from_bundle = _forward_diagnostics.closest_to_pick_candidate_from_bundle
formal_diagnostic_candidate_from_bundle = _forward_diagnostics.formal_diagnostic_candidate_from_bundle
is_no_pick_hard_blocker = _forward_diagnostics.is_no_pick_hard_blocker
no_pick_promotion_eligible = _forward_diagnostics.no_pick_promotion_eligible
paper_pick_source_health_flags = _forward_diagnostics.paper_pick_source_health_flags
ranked_no_pick_candidate_evaluations = _forward_diagnostics.ranked_no_pick_candidate_evaluations
summarize_evaluation_reason_counts = _forward_diagnostics.summarize_evaluation_reason_counts
summarize_evaluation_text_counts = _forward_diagnostics.summarize_evaluation_text_counts
_formal_rank_snapshot_id = _forward_snapshot._formal_rank_snapshot_id
apply_formal_profit_ranks = _forward_snapshot.apply_formal_profit_ranks
freeze_formal_production_snapshot = _forward_snapshot.freeze_formal_production_snapshot
quarantine_nonproduction_bundle = _forward_snapshot.quarantine_nonproduction_bundle
synchronize_formal_profit_rank_state = _forward_snapshot.synchronize_formal_profit_rank_state
validate_active_production_chain = _forward_snapshot.validate_active_production_chain
validate_formal_rank_snapshot = _forward_snapshot.validate_formal_rank_snapshot
build_rank_alignment_diagnostic = _forward_diagnostics.build_rank_alignment_diagnostic
MAINLINE_THEME_SYNONYMS = _forward_features.MAINLINE_THEME_SYNONYMS
_NEWS_CACHE = _forward_features._NEWS_CACHE
_NEWS_CACHE_DATE = _forward_features._NEWS_CACHE_DATE
_SECTOR_NAMES_CACHE = _forward_features._SECTOR_NAMES_CACHE
NO_PICK_HARD_BLOCK_PREFIXES = _forward_diagnostics.NO_PICK_HARD_BLOCK_PREFIXES
_FORMAL_RANK_STATE_FIELDS = _forward_snapshot._FORMAL_RANK_STATE_FIELDS

# --- eligibility extract: bind host helpers then re-export ---
import xiaogu_forward_eligibility as _forward_eligibility

_forward_eligibility.bind_host(__import__(__name__))
paper_pick_eligibility_profile = _forward_eligibility.paper_pick_eligibility_profile
official_target_exclusion_reasons = _forward_eligibility.official_target_exclusion_reasons
structure_block_machine_codes = _forward_eligibility.structure_block_machine_codes
attach_paper_pick_eligibility = _forward_eligibility.attach_paper_pick_eligibility
current_day_tradable_filter_reason = _forward_eligibility.current_day_tradable_filter_reason
filter_current_day_tradable_candidates = _forward_eligibility.filter_current_day_tradable_candidates
filter_t1_profit_candidates = _forward_eligibility.filter_t1_profit_candidates
t1_profit_candidate_profile = _forward_eligibility.t1_profit_candidate_profile
broken_limitup_continuation_exception = _forward_eligibility.broken_limitup_continuation_exception

# Bind after eligibility exports exist so all extracted owners resolve through
# the complete single runner host.
_runner_host = __import__(__name__)
_forward_features.bind_host(_runner_host)
_forward_ranking.bind_host(_runner_host)
_forward_diagnostics.bind_host(_runner_host)
_forward_snapshot.bind_host(_runner_host)

import xiaogu_forward_persistence as _forward_persistence

_unique_persistence_candidates = _forward_persistence._unique_persistence_candidates

import xiaogu_forward_bundle_io as _forward_bundle_io

_forward_bundle_io.bind_host(__import__(__name__))
write_text = _forward_bundle_io.write_text
write_json = _forward_bundle_io.write_json
scan_summary_paths = _forward_bundle_io.scan_summary_paths
summary_bundle_rows = _forward_bundle_io.summary_bundle_rows
summary_file_rows = _forward_bundle_io.summary_file_rows
load_candidate_bundle = _forward_bundle_io.load_candidate_bundle
_bundle_from_scan_summary = _forward_bundle_io._bundle_from_scan_summary
load_latest_eastmoney_scan = _forward_bundle_io.load_latest_eastmoney_scan
scan_date_for_runtime = _forward_bundle_io.scan_date_for_runtime
build_daily_candidate_persistence_payloads = _forward_bundle_io.build_daily_candidate_persistence_payloads
persist_daily_candidate_snapshot = _forward_bundle_io.persist_daily_candidate_snapshot
write_daily_candidate_persist_retry_payload = _forward_bundle_io.write_daily_candidate_persist_retry_payload

_forward_persistence.bind_host(__import__(__name__))
run_recorder = _forward_persistence.run_recorder
finalize_production_run = _forward_persistence.finalize_production_run



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=latest_completed_trading_day().isoformat())
    ap.add_argument('--asof-time', default='', help='Auto-detect latest scan if empty')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--account-snapshot-json', default='')
    ap.add_argument('--force', action='store_true', help='Append a correction for an existing decision and replace its active DB snapshot')
    ap.add_argument('--no-runtime-date-adjust', action='store_true', help='Keep the requested date even if it is not the latest completed trading day')
    ap.add_argument('--allow-stale-data', action='store_true', help='Allow historical data for backtesting (bypass freshness checks)')
    args = ap.parse_args()

    if should_skip_existing_decision_for_date(args.date, dry_run=args.dry_run, force=args.force):
        print(json.dumps({'status': 'SKIP_ALREADY_HAS_DECISION_FOR_DATE', 'date': args.date, 'ledger': str(LEDGER)}, ensure_ascii=False, indent=2))
        return
    correction_of = correction_reference_for_date(args.date) if args.force else ''

    if not args.no_runtime_date_adjust:
        runtime_date = scan_date_for_runtime(args.date)
        if runtime_date != args.date:
            print(f'RUNTIME_DATE_ADJUSTED: {args.date} -> {runtime_date}', file=sys.stderr, flush=True)
            args.date = runtime_date
    production_run_id = ''
    if not args.dry_run:
        production_run_id = str(
            os.environ.get('XIAOGU_PRODUCTION_RUN_ID', '').strip() or uuid.uuid4()
        )

    # The runner consumes only the same-day direct API scan produced by the pipeline.
    latest = load_latest_eastmoney_scan(args.date)
    if latest is None:
        raise SystemExit(f'PRODUCTION_SCAN_REQUIRED:{args.date}:run scrapy_scanner/runner_v2.py first')
    scan_summary = latest[1]
    source_time = str(scan_summary.get('source_time', ''))
    if source_time and len(source_time) >= 19:
        args.asof_time = source_time[11:19]
        print(f'LOADED_CURRENT_API_SCAN: {source_time}', file=sys.stderr, flush=True)
    if not args.asof_time:
        args.asof_time = dt.datetime.now().strftime('%H:%M:%S')

    rule = read_json(RULE_FREEZE)
    if (
        rule.get('rule_version') != RULE_VERSION
        or not rule.get('allow_trade')
        or not rule.get('paper_only')
        or not rule.get('no_trade')
        or rule.get('production_ready')
        or rule.get('auto_order')
        or rule.get('broker_connected')
    ):
        raise SystemExit('rule freeze safety check not pass')

    bundle = build_research_basket_from_latest_scan(args.date, args.asof_time)
    if not isinstance(bundle, dict) or not bundle.get('available'):
        raise SystemExit(f'PRODUCTION_SCAN_INVALID:{args.date}')

    if isinstance(bundle, dict) and bundle.get('production_chain_mode') == PRODUCTION_CHAIN_MODE:
        bundle['strict_production_chain'] = True
    if production_run_id:
        bundle['production_run_id'] = production_run_id
        bundle['candidate_snapshot_id'] = production_run_id
    
    try:
        from xiaogu_db import fetch_scan_data_directory_content
        trade_date = dt.date.fromisoformat(args.date)
        content_rows = fetch_scan_data_directory_content(trade_date)
    except Exception:
        content_rows = []
    if not content_rows:
        content_rows = summary_bundle_rows(bundle, 'data_directory_content_records')
        if not content_rows:
            content_records_path = bundle.get('data_directory_content_records_path') or ''
            if content_records_path and Path(content_records_path).exists():
                content_rows = load_jsonl(Path(content_records_path))
    if content_rows:
        content_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for rec in content_rows:
            code = str(rec.get('code') or rec.get('SECURITY_CODE') or '').strip()
            if code:
                content_by_code.setdefault(code, []).append(rec)
        bundle['data_directory_content_by_code'] = content_by_code
        bundle['data_directory_content_loaded_count'] = sum(len(v) for v in content_by_code.values())
        fund_by_code = parse_capital_flow_from_content_records(
            [rec for recs in content_by_code.values() for rec in recs]
        )
        bundle['data_directory_capital_flow_by_code'] = fund_by_code
        inject_capital_flow_boost(bundle, fund_by_code)
    inject_live_fund_flow_into_candidates(bundle)
    account_snapshot_json = args.account_snapshot_json or os.environ.get('XIAOGU_ACCOUNT_SNAPSHOT_JSON', '')
    if not account_snapshot_json and DEFAULT_ACCOUNT_SNAPSHOT_PATH.exists():
        account_snapshot_json = str(DEFAULT_ACCOUNT_SNAPSHOT_PATH)
    if account_snapshot_json:
        bundle['eastmoney_account_snapshot'] = read_json(Path(account_snapshot_json))
    bundle['_runner_asof_time'] = args.asof_time
    attach_paper_pick_eligibility(bundle)
    if bundle.get('strict_production_chain'):
        validation = validate_active_production_chain(bundle, args.date)
        bundle['production_chain_validation'] = validation
        if not validation.get('valid'):
            quarantine_nonproduction_bundle(bundle, validation)
    freeze_formal_production_snapshot(bundle)
    decision, symbol, reason, candidate_features, risk_flags = evaluate_candidate_bundle(bundle, args.date, allow_stale_data=args.allow_stale_data)

    # Symbol-level dedup: if this date already has a PAPER_PICK for a different symbol, skip
    if decision == 'PAPER_PICK' and not args.force:
        existing_symbol = existing_paper_pick_symbol_for_date(args.date)
        if existing_symbol and existing_symbol != symbol:
            decision = 'NO_PICK'
            reason = f'SYMBOL_LEVEL_DEDUP:already_have_PICK_for_{existing_symbol}'
            risk_flags.append('SYMBOL_LEVEL_DEDUP')

    cooldown = rule.get('recent_t1_nonprofit_cooldown', {})
    if cooldown.get('enabled'):
        risk_flags.append('RECENT_T1_NONPROFIT_COOLDOWN_USER_CONFIRMED')
        prior_loss_streak, prior_t1_return = historical_t1_loss_streak_before(args.date, symbol)
        if prior_loss_streak >= 2 and prior_t1_return is not None and prior_t1_return <= 0:
            decision = 'NO_PICK'
            symbol = ''
            reason = (
                'RECENT_T1_NONPROFIT_HARD_BLOCK:'
                f'latest_prior_t1_return={prior_t1_return:.6f};'
                f'prior_loss_streak={prior_loss_streak}'
            )
            risk_flags.append('RECENT_T1_NONPROFIT_HARD_BLOCK')
        elif decision == 'PAPER_PICK' and int(bundle.get('market_snapshot', {}).get('passed_count') or 0) < 1:
            decision = 'NO_PICK'
            reason = 'RECENT_T1_NONPROFIT_COOLDOWN:' + reason

    eastmoney_scan = load_latest_eastmoney_scan(args.date)
    if eastmoney_scan is None:
        raise SystemExit(f'PRODUCTION_SCAN_REQUIRED:{args.date}')
    summary_path, scan_summary = eastmoney_scan
    raw_files = scan_summary.get('files', {})
    raw_path = raw_files.get('raw', '')
    snapshot = {
        'market_data_source': scan_summary.get('pipeline_version') or scan_summary.get('source') or 'eastmoney_api_scan_v2',
        'eastmoney_scan_summary_path': str(summary_path),
        'eastmoney_scan_summary': scan_summary,
        'raw_dir': str(Path(raw_path).parent) if raw_path else '',
        'dual_source_index_snapshot': {},
        'source_ok_count': 1,
        'source_total': 1,
        'collected_at': now_iso(),
    }
    data_gate_status = 'PASS'
    if data_gate_status != 'PASS' and decision == 'PAPER_PICK':
        decision, symbol, reason = 'NO_PICK', '', 'DATA_GATE_NOT_PASS_RUNTIME_INDEX_SNAPSHOT'
        risk_flags.append('RUNTIME_DATA_GATE_NOT_PASS')
    core_source_gate = core_market_source_gate(bundle)
    if core_source_gate['status'] == 'BLOCK':
        decision, symbol = 'NO_PICK', ''
        reason = 'CORE_MARKET_SOURCE_GATE_NOT_PASS:' + ';'.join(
            unique_text_values([
                *core_source_gate.get('missing_sources', []),
                *core_source_gate.get('flags', []),
            ])
        )
        risk_flags = unique_text_values([
            *risk_flags,
            'CORE_MARKET_SOURCE_GATE_NOT_PASS',
            *core_source_gate.get('missing_sources', []),
            *core_source_gate.get('flags', []),
        ])
        candidate_features = dict(candidate_features or {})
        candidate_features['core_market_source_gate'] = core_source_gate

    daily_ticket_search_result = bundle.get('daily_ticket_search_result')
    if isinstance(daily_ticket_search_result, dict):
        if decision == 'PAPER_PICK':
            daily_ticket_search_result['first_paper_pick_layer'] = candidate_features.get('search_layer') or None
            daily_ticket_search_result['no_pick_reason_if_none'] = ''
        else:
            daily_ticket_search_result['first_paper_pick_layer'] = None
            daily_ticket_search_result['no_pick_reason_if_none'] = 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS'

    loader_semantics_restored = bool(bundle.get('scan_summary_path'))

    target_card_candidate = candidate_features
    if decision != 'PAPER_PICK' and isinstance(candidate_features, dict) and candidate_features.get('diagnostic_only'):
        target_card_candidate = {}
    if not target_card_candidate.get('price') and isinstance(bundle.get('candidate'), dict):
        for k in ('price', 'high', 'low', 'open', 'prev_close', 'signal_pct', 'net_inflow_main', 'volume_ratio', 'close_position_score'):
            if not target_card_candidate.get(k) and bundle['candidate'].get(k) is not None:
                target_card_candidate[k] = bundle['candidate'][k]
    single_target_card = build_single_target_card(
        decision,
        symbol,
        reason,
        target_card_candidate,
        bundle,
        risk_flags,
        bool(decision == 'PAPER_PICK' and not args.dry_run),
    )
    no_pick_candidate_diagnostics = None
    daily_best_paper_watch = None
    profit_candidate_shadow_watch = None
    diagnostic_record = bundle.get('first_rejected_candidate_diagnostic') if isinstance(bundle.get('first_rejected_candidate_diagnostic'), dict) else {}
    diagnostic_features = candidate_features if isinstance(candidate_features, dict) and candidate_features else diagnostic_record.get('features')
    diagnostic_reason = reason if isinstance(candidate_features, dict) and candidate_features else diagnostic_record.get('reason', reason)
    diagnostic_flags = risk_flags if isinstance(candidate_features, dict) and candidate_features else diagnostic_record.get('flags', risk_flags)
    diagnostic_decision = decision if isinstance(candidate_features, dict) and candidate_features else diagnostic_record.get('decision', decision)
    if decision == 'NO_PICK' and isinstance(diagnostic_features, dict) and diagnostic_features:
        no_pick_candidate_diagnostics = build_no_pick_candidate_diagnostics(
            bundle,
            args.date,
            diagnostic_features,
            diagnostic_decision,
            diagnostic_reason,
            diagnostic_flags,
        )
        daily_best_paper_watch = no_pick_candidate_diagnostics.get('daily_best_paper_watch')
        profit_candidate_shadow_watch = no_pick_candidate_diagnostics.get('profit_candidate_shadow_watch')
    candidate_consumption_summary = build_candidate_consumption_summary(
        bundle,
        args.date,
        decision,
        symbol,
        reason,
        candidate_features,
        risk_flags,
    )
    if decision == 'NO_PICK' and daily_best_paper_watch is None:
        daily_best_paper_watch = candidate_consumption_summary.get('daily_best_paper_watch')
    if decision == 'NO_PICK' and profit_candidate_shadow_watch is None:
        profit_candidate_shadow_watch = load_profit_shadow_watchlist(args.date, top_n=5)

    if decision == 'NO_PICK':
        # There is one official selection path: evaluate_candidate_bundle()
        # already applied all formal gates and the formal sorter. A diagnostic
        # candidate must never be promoted into a second PAPER_PICK path.
        diagnostic_candidate, diagnostic_reason = formal_diagnostic_candidate_from_bundle(bundle)
        if isinstance(diagnostic_candidate, dict) and symbol_for(diagnostic_candidate):
            promotion_block_flag = 'NO_PICK_PROMOTION_DISABLED_SINGLE_PATH:formal_diagnostic_only'
            risk_flags = unique_text_values([*risk_flags, promotion_block_flag])
            if isinstance(daily_best_paper_watch, dict) and daily_best_paper_watch.get('symbol') == symbol_for(diagnostic_candidate):
                daily_best_paper_watch['promotion_blocked'] = True
                daily_best_paper_watch['promotion_block_reason'] = promotion_block_flag
            summary_watch = candidate_consumption_summary.get('daily_best_paper_watch') if isinstance(candidate_consumption_summary, dict) else None
            if isinstance(summary_watch, dict) and summary_watch.get('symbol') == symbol_for(diagnostic_candidate):
                summary_watch['promotion_blocked'] = True
                summary_watch['promotion_block_reason'] = promotion_block_flag
            print(f'INFO: NO_PICK promotion disabled: {promotion_block_flag}', file=sys.stderr)
        elif diagnostic_reason:
            print(f'INFO: NO_PICK diagnostic unavailable: {diagnostic_reason}', file=sys.stderr)

    # Production default: never embed full bundle / full paper basket into runtime features.
    from xiaogu_runtime_payload import (
        slim_bundle_for_runtime,
        slim_candidate_list,
        build_runtime_decision_context,
        enforce_runtime_memory_gate,
        maybe_force_gc,
        payload_bytes,
    )
    from xiaogu_evidence_card import build_compact_evidence_card, evidence_card_to_selection_reason
    from xiaogu_case_vector_store import (
        search_similar_cases,
        upsert_pick_case,
        similar_cases_ranking_boost,
    )

    if isinstance(candidate_features, dict) and isinstance(candidate_features.get('paper_candidate_basket'), list):
        _basket = candidate_features['paper_candidate_basket']
        candidate_features['paper_candidate_basket_count'] = len(_basket)
        candidate_features['paper_candidate_basket'] = slim_candidate_list(_basket, limit=12)

    # Drop heavy list nests from outer features (counts only; full lists stay in-memory on bundle).
    _obs = bundle.get('structured_observation_basket') or []
    _sector_obs = bundle.get('structured_sector_observation_basket') or []

    features = {
        'runner': 'xiaogu_forward_runner',
        'date': args.date,
        'asof_time': args.asof_time,
        'generated_at': now_iso(),
        'rule_version': RULE_VERSION,
        'production_run_id': production_run_id,
        'candidate_snapshot_id': production_run_id,
        'formal_rank_snapshot_id': str(bundle.get('formal_rank_snapshot_id') or ''),
        'formal_rank_snapshot_version': str(bundle.get('formal_rank_snapshot_version') or ''),
        'scoring_config_snapshot': get_scoring_config_snapshot(),
        'runtime_market_snapshot': snapshot,
        'candidate_bundle_status': slim_bundle_for_runtime(bundle),
        'research_signals': candidate_features.get('research_signals') or (bundle.get('candidate') or {}).get('research_signals') or {},
        'information_coverage_audit': dict(bundle.get('information_coverage_audit') or MISSING_INFORMATION_COVERAGE_AUDIT),
        'source_consumption_summary': candidate_consumption_summary.get('source_consumption_summary', {}),
        'candidate_consumption_summary': candidate_consumption_summary,
        'official_explanation_summary': candidate_consumption_summary.get('official_result', {}),
        'scan_summary_path': bundle.get('scan_summary_path', ''),
        'scan_summary_source_time': bundle.get('scan_summary_source_time', ''),
        'data_directory_content_loaded_count': bundle.get('data_directory_content_loaded_count', 0),
        'data_directory_content_record_count': (bundle.get('data_directory_content') or {}).get('record_count', 0),
        'data_directory_content_tab_count': (bundle.get('data_directory_content') or {}).get('tab_count', 0),
        'structured_observation_basket': slim_candidate_list(_obs, limit=10) if isinstance(_obs, list) else [],
        'structured_observation_basket_count': len(_obs) if isinstance(_obs, list) else 0,
        'structured_sector_observation_basket': slim_candidate_list(_sector_obs, limit=10) if isinstance(_sector_obs, list) else [],
        'structured_sector_observation_basket_count': len(_sector_obs) if isinstance(_sector_obs, list) else 0,
        'structured_formal_impact': bundle.get('structured_formal_impact', {}),
        'sector_catalyst_diagnostics': dict(bundle.get('sector_catalyst_diagnostics') or MISSING_SECTOR_CATALYST_DIAGNOSTICS),
        'daily_ticket_search_result': bundle.get('daily_ticket_search_result', {}),
        'paper_pick_candidate_stage_distribution': bundle.get('paper_pick_candidate_stage_distribution', {}),
        'candidate_stage_blocker_distribution': bundle.get('candidate_stage_blocker_distribution', {}),
        'weak_market_shadow_ticket': bundle.get('weak_market_shadow_ticket'),
        'candidate_features': candidate_features,
        'single_target_card': single_target_card,
        **({'no_pick_candidate_diagnostics': no_pick_candidate_diagnostics} if no_pick_candidate_diagnostics is not None else {}),
        **({'daily_best_paper_watch': daily_best_paper_watch} if daily_best_paper_watch is not None else {}),
        **({'profit_candidate_shadow_watch': profit_candidate_shadow_watch} if profit_candidate_shadow_watch is not None else {}),
        'data_gate_status': data_gate_status,
        'core_market_source_gate': core_source_gate,
        'xiaochan_gate_status': candidate_features.get('xiaochan_gate_status', 'ALLOW_FORWARD_PAPER_NO_TRADE'),
        'xiaoshuju_data_gate_status': candidate_features.get('xiaoshuju_data_gate_status', data_gate_status),
        'risk_flags': risk_flags,
        'asof_leakage_flag': False,
        'loader_semantics_restored': loader_semantics_restored,
        'payload_policy': 'slim_runtime_v1',
        **LOCKED_SAFETY,
    }
    features['scoring_config_hash'] = hashlib.sha256(
        json.dumps(features['scoring_config_snapshot'], ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()
    features['input_payload_hash'] = hashlib.sha256(
        json.dumps(
            {
                'bundle_path': bundle.get('_bundle_path') or bundle.get('scan_summary_path') or '',
                'source_time': bundle.get('scan_summary_source_time') or source_time,
                'formal_rank_snapshot_id': features['formal_rank_snapshot_id'],
                'formal_rank_snapshot_version': features['formal_rank_snapshot_version'],
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode('utf-8')
    ).hexdigest()

    def generate_structured_reasons(features: Dict[str, Any], bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成结构化出票理由。"""
        reasons = []
        market_regime = str(features.get('market_regime', '') or '').lower()

        # 1. 政策/消息驱动
        news_catalyst = features.get('news_catalyst', '')
        if news_catalyst:
            reasons.append({
                'reason_type': 'policy_or_news',
                'reason': str(news_catalyst),
                'impact': '个股',
                'strength': 'medium',
                'is_priced_in': False,
            })

        # 2. 资金驱动
        fund_flow = safe_float(features.get('fund_flow_momentum'))
        if fund_flow is not None and fund_flow > 0.5:
            reasons.append({
                'reason_type': 'capital_flow',
                'reason': f'资金流入动量 {fund_flow:.2f}',
                'evidence': 'fund_flow_momentum',
                'strength': 'strong' if fund_flow > 0.8 else 'medium',
            })

        # 3. 预期差驱动
        sector_score = safe_float(features.get('sector_opportunity_score'))
        if sector_score is not None and sector_score >= 0.6:
            reasons.append({
                'reason_type': 'expectation_gap',
                'reason': f'板块机会评分 {sector_score:.2f}',
                'expected_next_catalyst': '板块继续发酵',
                'strength': 'strong' if sector_score >= 0.8 else 'medium',
            })

        # 4. 技术/盘口驱动
        close_pos = safe_float(features.get('close_position_score'))
        if close_pos is not None and close_pos >= 0.9:
            reasons.append({
                'reason_type': 'technical_or_tape',
                'reason': f'收盘强度 {close_pos:.3f}',
                'key_signal': '承接强',
                'strength': 'strong' if close_pos >= 0.95 else 'medium',
            })

        # 5. 情绪/地位驱动
        sealed = bool(features.get('sealed_limit_up', False))
        if sealed:
            reasons.append({
                'reason_type': 'sentiment_position',
                'reason': '涨停封板',
                'position': 'leader',
                'strength': 'strong',
            })

        # 如果没有足够理由，添加默认理由
        if len(reasons) < 3:
            signal_pct = safe_float(features.get('signal_pct'))
            if signal_pct is not None:
                reasons.append({
                    'reason_type': 'technical_or_tape',
                    'reason': f'涨幅 {signal_pct:.2f}%',
                    'key_signal': '动量',
                    'strength': 'medium',
                })

        return reasons[:5]  # 最多返回5个理由

    def generate_buy_plan(features: Dict[str, Any], bundle: Dict[str, Any]) -> Dict[str, Any]:
        """生成买入计划。"""
        signal_pct = safe_float(features.get('signal_pct')) or 0
        close_pos = safe_float(features.get('close_position_score')) or 0
        sealed = bool(features.get('sealed_limit_up', False))
        market_regime = str(features.get('market_regime', '') or '').lower()

        # 判断买入场景
        if sealed:
            preferred_entry = '打板确认'
            entry_trigger = '封板稳定，板块助攻'
            position_style = 'normal'
        elif signal_pct > 5:
            preferred_entry = '开盘回踩承接'
            entry_trigger = '回踩不破均价线，缩量承接'
            position_style = 'light'
        elif 'climax' in market_regime:
            preferred_entry = '弱转强确认'
            entry_trigger = '低开后快速翻红，板块同步'
            position_style = 'very_light'
        else:
            preferred_entry = '竞价确认'
            entry_trigger = '竞价高开2%-6%，板块同步强'
            position_style = 'normal'

        return {
            'preferred_entry': preferred_entry,
            'entry_zone': '参考分时均价线',
            'entry_trigger': entry_trigger,
            'avoid_entry': '放量滞涨，尾盘跳水，板块不跟',
            'position_style': position_style,
        }

    def generate_sell_plan(features: Dict[str, Any], bundle: Dict[str, Any]) -> Dict[str, Any]:
        """生成卖出计划。"""
        sealed = bool(features.get('sealed_limit_up', False))
        signal_pct = safe_float(features.get('signal_pct')) or 0
        close_pos = safe_float(features.get('close_position_score')) or 0
        sector_score = safe_float(features.get('sector_opportunity_score')) or 0
        support_desc = '昨日收盘价/分时均价线'
        if close_pos >= 0.9:
            support_desc = '分时均价线；强势票不破开盘承接低点'
        sector_desc = '板块同步走弱且资金流出' if sector_score < 0.5 else '板块由强转弱或核心股掉队'

        execution_plan = (
            '## 次日卖出执行计划\n\n'
            '### 1. 竞价判断\n\n'
            '- 如果竞价高开：先看开盘后是否放量上攻，高开无量冲高优先兑现。\n'
            '- 如果竞价平开：等待9:30-10:00方向选择，站上分时均价线再持有。\n'
            '- 如果竞价低开：不恐慌卖，先观察承接和板块是否同步崩。\n'
            '- 如果竞价严重不及预期：跌破昨日关键支撑且反抽无力，降低风险。\n\n'
            '### 2. 开盘 30 分钟\n\n'
            '- 什么情况继续持有：快速翻红、缩量回踩不破均价线、板块同步走强。\n'
            '- 什么情况等待反弹：低开但未破关键低点，盘口有承接，板块未同步杀跌。\n'
            '- 什么情况必须降低风险：10:00前跌破关键低点，反抽不过均价线。\n\n'
            '### 3. 冲高卖出\n\n'
            '- 第一止盈区：盘中浮盈2%-4%，先兑现一部分。\n'
            '- 第二止盈区：浮盈5%-8%或接近涨停但封不住，继续减仓。\n'
            '- 冲高不封板处理：放量滞涨或跌回分时均价线，按规则卖出。\n'
            '- 从高点回落处理：高点回撤2%-3%且反抽不过，卖出剩余仓位。\n\n'
            '### 4. 涨停处理\n\n'
            '- 快速涨停：封单稳定则观察，不主动砸板。\n'
            '- 涨停炸板：炸板后回封失败，卖出或大幅降仓。\n'
            '- 炸板回封：快速回封且板块仍强，保留观察仓。\n'
            '- 封单减弱：封单持续缩小并反复开板，按炸板卖处理。\n\n'
            '### 5. 失败条件\n\n'
            f'- 跌破哪个位置说明失败：跌破{support_desc}。\n'
            f'- 板块什么表现说明失败：{sector_desc}。\n'
            '- 分时什么信号说明失败：跌破均价线后反抽不过，或低开后10:00前无修复。\n'
        )

        if sealed:
            return {
                'target_scenario': '涨停持有观察',
                'take_profit_zone': '炸板后反弹高点',
                'stop_loss_zone': '跌破昨日收盘价',
                'time_based_exit': '14:30前未封住则考虑',
                'failure_exit': '反复炸板，封单持续缩小',
                'next_day_execution_plan': execution_plan,
            }
        elif signal_pct > 5:
            return {
                'target_scenario': '高开冲高卖',
                'take_profit_zone': '开盘30分钟内高点',
                'stop_loss_zone': '跌破开盘价',
                'time_based_exit': '10:30前不创新高则考虑',
                'failure_exit': '高开低走，分时背离',
                'next_day_execution_plan': execution_plan,
            }
        else:
            return {
                'target_scenario': '冲高回落卖',
                'take_profit_zone': '分时高点',
                'stop_loss_zone': '跌破均价线',
                'time_based_exit': '14:00后观察',
                'failure_exit': '全天弱势，无反弹',
                'next_day_execution_plan': execution_plan,
            }

    def generate_risk_factors(features: Dict[str, Any], bundle: Dict[str, Any]) -> List[str]:
        """生成风险因素。"""
        risks = []
        market_regime = str(features.get('market_regime', '') or '').lower()

        if 'climax' in market_regime:
            risks.append('高潮市场，分歧风险大')
        if safe_float(features.get('turnover_rate', 0)) > 15:
            risks.append('换手率过高，筹码松动')
        if safe_float(features.get('signal_pct', 0)) > 8:
            risks.append('涨幅过大，追高风险')
        if not bool(features.get('sealed_limit_up', False)):
            risks.append('未封板，次日溢价不确定')

        return risks[:3]  # 最多返回3个风险

    def generate_failure_conditions(features: Dict[str, Any], bundle: Dict[str, Any]) -> List[str]:
        """生成失败条件。"""
        conditions = []
        market_regime = str(features.get('market_regime', '') or '').lower()

        if 'climax' in market_regime:
            conditions.append('板块集体回落')
        conditions.append('竞价大幅低开')
        conditions.append('开盘后持续走弱')
        if not bool(features.get('sealed_limit_up', False)):
            conditions.append('高开低走不反弹')

        return conditions[:3]  # 最多返回3个条件

    # Serenity + Buffett validation for ALL candidates (BEFORE writing runtime file)
    pick_validation = None
    candidate_validations = []
    try:
        from xiaogu_pick_validator import validate_pick

        def validate_pick_bounded(candidate_symbol: str, candidate_row: Dict[str, Any]) -> Dict[str, Any]:
            import signal as _signal

            if not hasattr(_signal, 'SIGALRM'):
                return validate_pick(candidate_symbol, candidate_row)

            def timeout_handler(signum, frame):
                raise TimeoutError(f'validate_pick_timeout:{candidate_symbol}')

            previous_handler = _signal.signal(_signal.SIGALRM, timeout_handler)
            _signal.setitimer(_signal.ITIMER_REAL, 12.0)
            try:
                return validate_pick(candidate_symbol, candidate_row)
            except TimeoutError as exc:
                return {
                    'symbol': candidate_symbol,
                    'validation_passed': False,
                    'validation_override': False,
                    'combined_verdict': 'VALIDATION_TIMEOUT_NON_BLOCKING',
                    'error': str(exc),
                }
            finally:
                _signal.setitimer(_signal.ITIMER_REAL, 0.0)
                _signal.signal(_signal.SIGALRM, previous_handler)
        
        # Validate the picked candidate
        if symbol:
            validate_pick_externally = os.environ.get('XIAOGU_VALIDATE_PICK_EXTERNALLY', '').strip().lower() in ('1', 'true', 'yes')
            if validate_pick_externally:
                pick_validation = validate_pick_bounded(symbol, candidate_features or {})
            else:
                local_eligibility = candidate_features.get('paper_pick_eligibility') if isinstance(candidate_features.get('paper_pick_eligibility'), dict) else {}
                local_capital_risk = candidate_features.get('capital_risk_profile') if isinstance(candidate_features.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(candidate_features or {})
                pick_validation = {
                    'symbol': symbol,
                    'validation_passed': bool(local_eligibility.get('eligible')),
                    'validation_override': False,
                    'combined_verdict': 'LOCAL_ELIGIBILITY_AND_RISK_SNAPSHOT',
                    'eligibility_snapshot': local_eligibility,
                    'capital_risk_profile': local_capital_risk,
                    'external_validation_skipped': True,
                }
            features['pick_validation'] = pick_validation
            
            # If validation fails for PAPER_PICK, log warning
            if decision == 'PAPER_PICK' and not pick_validation.get('validation_passed'):
                print(f'WARN: Pick validation failed for {symbol}: {pick_validation.get("combined_verdict")}', file=sys.stderr)
        
        # Top10 already carries eligibility/risk snapshots. External per-symbol
        # validation is opt-in because its network path is not part of the
        # scanner-first ticket contract and can otherwise block the daily run.
        candidates = bundle.get('paper_scoring_candidates', [])
        validate_top10_externally = os.environ.get('XIAOGU_VALIDATE_TOP10_EXTERNALLY', '').strip().lower() in ('1', 'true', 'yes')
        for cand in candidates[:10]:  # Top 10 candidates
            cand_symbol = cand.get('symbol') or cand.get('code')
            if cand_symbol:
                if validate_top10_externally:
                    cand_validation = validate_pick_bounded(cand_symbol, cand)
                else:
                    eligibility = cand.get('paper_pick_eligibility') if isinstance(cand.get('paper_pick_eligibility'), dict) else {}
                    capital_risk = cand.get('capital_risk_profile') if isinstance(cand.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(cand)
                    cand_validation = {
                        'symbol': cand_symbol,
                        'validation_passed': bool(eligibility.get('eligible')),
                        'validation_override': False,
                        'combined_verdict': 'LOCAL_ELIGIBILITY_AND_RISK_SNAPSHOT',
                        'eligibility_snapshot': eligibility,
                        'capital_risk_profile': capital_risk,
                        'external_validation_skipped': True,
                    }
                candidate_validations.append(cand_validation)
        
        # Store all candidate validations
        if candidate_validations:
            features['candidate_validations'] = candidate_validations
        
        # If NO_PICK but a candidate has strong validation, consider override
        if decision == 'NO_PICK' and candidate_validations:
            strong_candidates = [
                v for v in candidate_validations 
                if v.get('validation_override')
            ]
            if strong_candidates:
                best = strong_candidates[0]
                print(f'INFO: Validation override candidate found: {best["symbol"]} ({best["combined_verdict"]})', file=sys.stderr)
                features['validation_override_candidate'] = best
    except Exception as e:
        print(f'WARN: Pick validation error: {e}', file=sys.stderr)

    # Compact evidence card + pgvector similar cases (rules/multi-factor; not LLM×400).
    evidence_card: Dict[str, Any] = {}
    similar_cases: List[Dict[str, Any]] = []
    similar_boost_meta: Dict[str, Any] = {}
    selection_reason_payload: Dict[str, Any] = {}
    try:
        if isinstance(candidate_features, dict):
            # Prefer cases already attached during formal enrich when present.
            if isinstance(candidate_features.get('similar_cases'), list) and candidate_features.get('similar_cases'):
                similar_cases = list(candidate_features.get('similar_cases') or [])[:5]
                similar_boost_meta = dict(candidate_features.get('similar_cases_meta') or {}) or similar_cases_ranking_boost(similar_cases)
        if not similar_cases:
            similar_cases = search_similar_cases(
                symbol=symbol or '',
                name=str((candidate_features or {}).get('name') or (candidate_features or {}).get('stock_name') or ''),
                features=candidate_features if isinstance(candidate_features, dict) else {},
                exclude_trade_date=args.date,
                limit=5,
            )
            similar_boost_meta = similar_cases_ranking_boost(similar_cases)
        evidence_card = build_compact_evidence_card(
            candidate_features if isinstance(candidate_features, dict) else {},
            features=features,
            similar_cases=similar_cases,
            decision=decision,
            reason=reason,
        )
        selection_reason_payload = evidence_card_to_selection_reason(evidence_card, legacy_reason=reason)
        features['evidence_card'] = evidence_card
        features['similar_cases'] = similar_cases
        features['similar_cases_boost'] = similar_boost_meta
        features['selection_reason'] = selection_reason_payload
        if isinstance(candidate_features, dict):
            candidate_features['evidence_card'] = evidence_card
            candidate_features['similar_cases'] = similar_cases
            candidate_features['similar_cases_boost'] = float(similar_boost_meta.get('boost') or 0.0)
            candidate_features['selection_reason'] = selection_reason_payload
            features['candidate_features'] = candidate_features
        if isinstance(single_target_card, dict):
            single_target_card = dict(single_target_card)
            single_target_card['evidence_card'] = evidence_card
            single_target_card['selection_reason'] = selection_reason_payload
            features['single_target_card'] = single_target_card
    except Exception as exc:
        print(f'WARN: evidence_card/similar_cases failed: {exc}', file=sys.stderr, flush=True)

    # Write runtime file AFTER validation — always slim (production default, not emergency-only).
    runtime_snapshot_path = RAW_ROOT / args.date / args.asof_time.replace(':','') / 'runtime_decision_context.json'
    if correction_of:
        features['correction_context'] = {
            'record_type': 'CORRECTION',
            'correction_of': correction_of,
            'reason': 'LATEST_HEALTHY_CHAIN_SUPERSEDES_STALE_SAME_DAY_DECISION',
        }
    mem_gate = enforce_runtime_memory_gate(stage='pre_runtime_write')
    features['runtime_memory_gate'] = mem_gate
    if mem_gate.get('status') in ('WARN', 'HARD'):
        maybe_force_gc()
    runtime_payload = build_runtime_decision_context(
        features, decision, symbol, reason, single_target_card,
    )
    write_json(runtime_snapshot_path, runtime_payload)
    features['runtime_decision_context_path'] = str(runtime_snapshot_path)
    features['runtime_payload_bytes'] = runtime_payload.get('runtime_payload_bytes')
    features['payload_policy'] = runtime_payload.get('payload_policy') or 'slim_runtime_v1'

    # 生成结构化出票理由
    structured_reasons = generate_structured_reasons(features, bundle)
    features['structured_reasons'] = structured_reasons

    # 生成买入计划
    buy_plan = generate_buy_plan(features, bundle)
    features['buy_plan'] = buy_plan

    # 生成卖出计划
    sell_plan = generate_sell_plan(features, bundle)
    features['sell_plan'] = sell_plan

    # 生成风险因素
    risk_factors = generate_risk_factors(features, bundle)
    features['risk_factors'] = risk_factors

    # 生成失败条件
    failure_conditions = generate_failure_conditions(features, bundle)
    features['failure_conditions'] = failure_conditions

    daily_candidate_persist_result: Dict[str, Any] = {'status': 'PENDING'}
    daily_candidate_persist_retry_payload_path = ''
    rec: Dict[str, Any] = {
        'returncode': 0,
        'stdout': '',
        'stderr': '',
    }

    # Production decision persistence is required. Recorder publication follows
    # only after scan, candidates, pick, and active pointer commit together.
    db_pick_correction: Dict[str, Any] = {}
    production_run_published = False
    try:
        import datetime as _dt
        from xiaogu_db import (
            fetch_user_locked_official_pick,
            insert_pick,
            mark_pick_active_correction,
            supersede_active_picks_for_correction,
            update_production_run_status,
            update_production_run_step,
        )
        trade_day = _dt.date.fromisoformat(args.date)
        # Evening force / same-day re-run: if a USER_LOCKED formal pick already exists,
        # do not write a competing PAPER_PICK (7/23 山金 superseded 华银 chaos).
        pre_locked = fetch_user_locked_official_pick(trade_day)
        pre_locked_symbol = str((pre_locked or {}).get('symbol') or '').zfill(6) if pre_locked else ''
        new_symbol_norm = str(symbol or '').zfill(6)
        if (
            correction_of
            and pre_locked
            and pre_locked_symbol
            and pre_locked_symbol not in ('', '000000', 'NO_PICK')
            and new_symbol_norm
            and new_symbol_norm not in ('', '000000', 'NO_PICK')
            and pre_locked_symbol != new_symbol_norm
            and str(decision or '').upper() == 'PAPER_PICK'
        ):
            print(
                f'USER_LOCKED_OFFICIAL_BLOCKS_EVENING_FORCE: keep={pre_locked_symbol} '
                f'skip_write={new_symbol_norm}',
                file=sys.stderr,
                flush=True,
            )
            decision = 'NO_PICK'
            symbol = ''
            reason = (
                f'USER_LOCKED_OFFICIAL_BLOCKS_EVENING_FORCE:keep={pre_locked_symbol};'
                f'superseded_attempt={new_symbol_norm}'
            )
            risk_flags = unique_text_values(
                [*risk_flags, 'USER_LOCKED_OFFICIAL_BLOCKS_EVENING_FORCE']
            )
            db_pick_correction = {
                'record_type': 'CORRECTION_BLOCKED_BY_USER_LOCK_PREWRITE',
                'correction_of': correction_of,
                'locked_symbol': pre_locked_symbol,
                'attempted_symbol': new_symbol_norm,
                'inserted_pick_id': None,
                'superseded_count': 0,
                'active_pick_count': 0,
            }
            # Keep observation of attempted evening name without writing DB pick.
            features['evening_force_blocked'] = {
                'locked_symbol': pre_locked_symbol,
                'attempted_symbol': new_symbol_norm,
                'attempted_decision': 'PAPER_PICK',
            }
        _blockers = list(candidate_features.get('blockers') or []) if isinstance(candidate_features, dict) else []
        _layers = list(candidate_features.get('source_layers') or []) if isinstance(candidate_features, dict) else []
        _score = None
        if isinstance(candidate_features, dict):
            _score = candidate_features.get('final_score')
            if _score is None:
                _score = candidate_features.get('_effective_score')
            if _score is None:
                _score = candidate_features.get('_contrarian_score')
            if _score is None:
                _score = candidate_features.get('score')
        _candidate = candidate_features if isinstance(candidate_features, dict) else {}
        _pick_features = {
            'candidate_features': _candidate,
            'single_target_card': single_target_card,
            'daily_best_paper_watch': daily_best_paper_watch,
            'profit_candidate_shadow_watch': profit_candidate_shadow_watch,
            'candidate_consumption_summary': candidate_consumption_summary,
            'official_explanation_summary': candidate_consumption_summary.get('official_result', {}),
            'source_consumption_summary': candidate_consumption_summary.get('source_consumption_summary', {}),
            'decision_reason': reason,
            'symbol': symbol,
            'decision': decision,
            # Top-level fields for easy DB querying
            'setup_type': _candidate.get('setup_type', ''),
            'signal_pct': _candidate.get('signal_pct'),
            'close_position_score': _candidate.get('close_position_score'),
            'fund_flow_momentum': _candidate.get('fund_flow_momentum'),
            'sector_catalyst_score': _candidate.get('sector_catalyst_score'),
            'turnover_rate': _candidate.get('turnover_rate'),
            'volume_ratio': _candidate.get('volume_ratio'),
            'net_inflow_main': _candidate.get('net_inflow_main'),
            # Serenity + Buffett validation
            'pick_validation': pick_validation,
        }
        if correction_of:
            _pick_features['correction_context'] = dict(features.get('correction_context') or {})
            _pick_features['candidate_snapshot_correction'] = dict(features.get('candidate_snapshot_correction') or {})
        pick_payload = {
            'trade_date': trade_day,
            'symbol': symbol or '',
            'decision': decision,
            'final_score': float(_score) if _score is not None else None,
            'blockers': _blockers,
            'features': _pick_features,
            'source_layers': _layers,
            'rule_version': RULE_VERSION,
            'scan_dir': str(snapshot.get('raw_dir') or ''),
            'dry_run': bool(args.dry_run),
            'stock_name': str(_candidate.get('name') or _candidate.get('stock_name') or ''),
            'rank': int(_candidate.get('rank')) if safe_float(_candidate.get('rank')) is not None else None,
            'structured_score': safe_float(_candidate.get('structured_score')),
            'ranking_basis': {
                'basis': 'capital_behavior_t1_profit',
                'ranking_view': 'main_force_behavior_chain',
                'rank_source': _candidate.get('rank_source') or 'formal_profit_first',
                'formal_rank': _candidate.get('formal_rank'),
                'formal_primary_score': _candidate.get('formal_primary_score'),
                'formal_sort_tuple': list(_candidate.get('formal_sort_tuple') or ()),
                'capital_behavior_score': (
                    _candidate.get('capital_behavior_score')
                    or (_candidate.get('ranking_basis_adjustment_components') or {}).get('capital_behavior_score')
                ),
                'structured_priority_score': _candidate.get('structured_priority_score'),
                'rank': _candidate.get('rank'),
            },
            'ticket_reason': {
                'decision': decision,
                'reason': reason,
                'structured_reasons': features.get('structured_reasons', []),
                'evidence_card_one_liner': (
                    (features.get('evidence_card') or {}).get('one_liner')
                    if isinstance(features.get('evidence_card'), dict) else ''
                ),
            },
            'selection_reason': (
                features.get('selection_reason')
                if isinstance(features.get('selection_reason'), dict)
                else {
                    'format': 'legacy_repo_summary',
                    'candidate_entry_reason': (
                        _pick_features.get('official_explanation_summary') or {}
                    ).get('why_selected') or [],
                    'decision_reason': reason,
                    'evidence_card': features.get('evidence_card') or {},
                }
            ),
            'paper_pick_eligibility': dict(_candidate.get('paper_pick_eligibility') or {}),
            'official_target_exclusion_reasons': list(_candidate.get('official_target_exclusion_reasons') or []),
            'risk_flags': unique_text_values(
                [*risk_flags, *((_candidate.get('capital_risk_profile') or {}).get('risk_codes') or [])]
            ),
            'auxiliary_evidence_status': str(_candidate.get('mainboard_auxiliary_evidence_status') or ''),
            'information_coverage_audit_snapshot': dict(bundle.get('information_coverage_audit') or {}),
            'source_summary_path': str(bundle.get('scan_summary_path') or ''),
            'formal_rank_snapshot_id': str(
                _candidate.get('formal_rank_snapshot_id') or features.get('formal_rank_snapshot_id') or ''
            ),
            'formal_rank_snapshot_version': str(
                _candidate.get('formal_rank_snapshot_version')
                or features.get('formal_rank_snapshot_version') or ''
            ),
            'scoring_config_hash': str(features.get('scoring_config_hash') or ''),
        }
        daily_candidate_persist_result = persist_daily_candidate_snapshot(
            args.date,
            bundle,
            features,
            decision,
            reason,
            dry_run=bool(args.dry_run),
            replace_existing=bool(args.force),
            correction_of=correction_of,
            production_run_id=production_run_id,
            pick_payload=pick_payload if production_run_id else None,
        )
        if daily_candidate_persist_result.get('status') not in ('OK', 'DRY_RUN'):
            daily_candidate_persist_retry_payload_path = write_daily_candidate_persist_retry_payload(
                runtime_snapshot_path,
                args.date,
                bundle,
                features,
                decision,
                reason,
                daily_candidate_persist_result,
            )
            print(json.dumps({
                'status': 'FAILED_PERSISTENCE',
                'date': args.date,
                'production_run_id': production_run_id,
                'daily_candidate_persist_result': daily_candidate_persist_result,
            }, ensure_ascii=False, indent=2))
            clear_runner_file_cache()
            raise SystemExit(2)
        if args.dry_run:
            inserted_pick_id = insert_pick(**pick_payload)
        else:
            inserted_pick_id = daily_candidate_persist_result.get('pick_id')
        if correction_of:
            features['candidate_snapshot_correction'] = {
                **(daily_candidate_persist_result.get('correction_archive') or {}),
                'pruned_stale_count': daily_candidate_persist_result.get('pruned_stale_count', 0),
                'status': daily_candidate_persist_result.get('status'),
            }
            write_json(
                runtime_snapshot_path,
                build_runtime_decision_context(features, decision, symbol, reason, single_target_card),
            )
        rec = run_recorder(
            args.date,
            args.asof_time,
            decision,
            symbol,
            features,
            reason,
            args.dry_run,
            correction_of=correction_of,
        )
        if production_run_id:
            recorder_status = 'PASS' if rec['returncode'] == 0 else 'FAIL'
            update_production_run_step(
                production_run_id,
                'recorder',
                recorder_status,
                required=True,
                error_message='' if rec['returncode'] == 0 else str(rec.get('stderr') or 'RECORDER_FAILED')[:500],
                retry_command=(
                    f'XIAOGU_PRODUCTION_RUN_ID={production_run_id} '
                    f'python3 xiaogu_forward_runner.py --date {args.date} --force'
                ),
            )
            if rec['returncode'] != 0:
                update_production_run_status(
                    production_run_id,
                    'FAIL',
                    error_message=str(rec.get('stderr') or 'RECORDER_FAILED')[:500],
                )
                raise SystemExit(rec['returncode'])
        # pgvector: store pick case so future formal sort can retrieve similar winners.
        if (
            decision == 'PAPER_PICK'
            and not args.dry_run
            and db_pick_correction.get('record_type') != 'CORRECTION_BLOCKED_BY_USER_LOCK_PREWRITE'
        ):
            try:
                upsert_result = upsert_pick_case(
                    trade_date=args.date,
                    symbol=symbol or '',
                    decision=decision,
                    stock_name=str(_candidate.get('name') or _candidate.get('stock_name') or ''),
                    final_score=float(_score) if _score is not None else None,
                    evidence_card=features.get('evidence_card') if isinstance(features.get('evidence_card'), dict) else None,
                    features=_candidate,
                    reason=reason,
                    metadata={
                        'rule_version': RULE_VERSION,
                        'similar_cases_boost': features.get('similar_cases_boost'),
                    },
                    dry_run=bool(args.dry_run),
                )
                features['case_vector_upsert'] = upsert_result
            except Exception as _vec_exc:
                print(f'WARN: upsert_pick_case failed: {_vec_exc}', file=sys.stderr, flush=True)
        if correction_of and db_pick_correction.get('record_type') != 'CORRECTION_BLOCKED_BY_USER_LOCK_PREWRITE':
            try:
                recorder_payload = json.loads(rec.get('stdout') or '{}')
            except (TypeError, json.JSONDecodeError):
                recorder_payload = {}
            correction_reference = str(
                ((recorder_payload.get('record') or {}).get('raw_data_snapshot_sha256'))
                or correction_of
            )
            locked = fetch_user_locked_official_pick(trade_day)
            locked_symbol = str((locked or {}).get('symbol') or '').zfill(6) if locked else ''
            new_symbol = str(symbol or '').zfill(6)
            if locked and locked_symbol and locked_symbol != new_symbol:
                # User-locked formal pick wins: never replace it with evening force / re-run.
                # Supersede the newly written non-locked correction instead.
                print(
                    f'USER_LOCKED_OFFICIAL_PRESERVED: keep={locked_symbol} '
                    f'supersede_new={new_symbol} decision={decision}',
                    file=sys.stderr,
                    flush=True,
                )
                superseded_count = supersede_active_picks_for_correction(
                    trade_day,
                    correction_of=correction_reference,
                    replacement_symbol=locked_symbol,
                    replacement_decision='PAPER_PICK',
                    reason='USER_LOCKED_OFFICIAL_BLOCKS_FORCE_REPLACEMENT',
                )
                active_count = mark_pick_active_correction(
                    trade_day,
                    symbol=locked_symbol,
                    decision='PAPER_PICK',
                    correction_of=correction_reference,
                )
                db_pick_correction = {
                    'record_type': 'CORRECTION_BLOCKED_BY_USER_LOCK',
                    'correction_of': correction_of,
                    'correction_reference': correction_reference,
                    'inserted_pick_id': inserted_pick_id,
                    'locked_symbol': locked_symbol,
                    'attempted_symbol': new_symbol,
                    'superseded_count': superseded_count,
                    'active_pick_count': active_count,
                }
            else:
                superseded_count = supersede_active_picks_for_correction(
                    trade_day,
                    correction_of=correction_reference,
                    replacement_symbol=symbol or '',
                    replacement_decision=decision,
                    reason='LATEST_HEALTHY_CHAIN_SUPERSEDES_STALE_SAME_DAY_DECISION',
                )
                active_count = mark_pick_active_correction(
                    trade_day,
                    symbol=symbol or '',
                    decision=decision,
                    correction_of=correction_reference,
                )
                db_pick_correction = {
                    'record_type': 'CORRECTION',
                    'correction_of': correction_of,
                    'correction_reference': correction_reference,
                    'inserted_pick_id': inserted_pick_id,
                    'superseded_count': superseded_count,
                    'active_pick_count': active_count,
                }
        if production_run_id:
            publish_active = db_pick_correction.get('record_type') not in {
                'CORRECTION_BLOCKED_BY_USER_LOCK_PREWRITE',
                'CORRECTION_BLOCKED_BY_USER_LOCK',
            }
            finalize_production_run(
                trade_day,
                production_run_id,
                candidate_snapshot_id=str(daily_candidate_persist_result.get('candidate_snapshot_id') or production_run_id),
                active_pick_id=inserted_pick_id,
                publish_active=publish_active,
            )
            production_run_published = publish_active
    except Exception as exc:
        if production_run_id:
            try:
                from xiaogu_db import update_production_run_status, update_production_run_step
                failure = repr(exc)[:500]
                update_production_run_step(
                    production_run_id,
                    'pick_persistence',
                    'FAILED_PERSISTENCE',
                    required=True,
                    error_message=failure,
                    retry_command=(
                        f'XIAOGU_PRODUCTION_RUN_ID={production_run_id} '
                        f'python3 xiaogu_forward_runner.py --date {args.date} --force'
                    ),
                )
                update_production_run_status(
                    production_run_id,
                    'FAILED_PERSISTENCE',
                    error_message=failure,
                )
            except Exception:
                pass
        print(f'FAILED_PERSISTENCE: {exc}', file=sys.stderr, flush=True)
        clear_runner_file_cache()
        raise SystemExit(2)

    out = {
        'date': args.date,
        'asof_time': args.asof_time,
        'decision': decision,
        'symbol': symbol,
        'single_target_card': single_target_card,
        'source_consumption_summary': candidate_consumption_summary.get('source_consumption_summary', {}),
        'candidate_consumption_summary': candidate_consumption_summary,
        'structured_reasons': features.get('structured_reasons', []),
        'evidence_card': features.get('evidence_card') or {},
        'selection_reason': features.get('selection_reason') or {},
        'similar_cases': features.get('similar_cases') or [],
        'similar_cases_boost': features.get('similar_cases_boost') or {},
        'case_vector_upsert': features.get('case_vector_upsert') or {},
        'runtime_payload_bytes': features.get('runtime_payload_bytes'),
        'payload_policy': features.get('payload_policy') or 'slim_runtime_v1',
        'runtime_memory_gate': features.get('runtime_memory_gate') or {},
        'buy_plan': features.get('buy_plan', {}),
        'sell_plan': features.get('sell_plan', {}),
        'risk_factors': features.get('risk_factors', []),
        'failure_conditions': features.get('failure_conditions', []),
        'climax_risk': features.get('climax_risk', False),
        **({'no_pick_candidate_diagnostics': no_pick_candidate_diagnostics} if no_pick_candidate_diagnostics is not None else {}),
        **({'daily_best_paper_watch': daily_best_paper_watch} if daily_best_paper_watch is not None else {}),
        **({'profit_candidate_shadow_watch': profit_candidate_shadow_watch} if profit_candidate_shadow_watch is not None else {}),
        'rule_version': RULE_VERSION,
        'production_run_id': production_run_id,
        'production_run_published': production_run_published,
        'ledger_path': str(LEDGER),
        'runtime_decision_context_path': str(runtime_snapshot_path),
        'raw_runtime_snapshot_dir': snapshot.get('raw_dir'),
        'ledger_line_added': (rec['returncode'] == 0 and not args.dry_run),
        'daily_candidate_persist_result': daily_candidate_persist_result,
        'daily_candidate_persist_retry_payload_path': daily_candidate_persist_retry_payload_path,
        'db_pick_correction': db_pick_correction,
        'recorder_returncode': rec['returncode'],
        'recorder_stdout': rec['stdout'],
        'recorder_stderr': rec['stderr'],
        'loader_semantics_restored': loader_semantics_restored,
        'data_directory_content_loaded_count': bundle.get('data_directory_content_loaded_count', 0),
        'data_directory_content_record_count': (bundle.get('data_directory_content') or {}).get('record_count', 0),
        'data_directory_content_tab_count': (bundle.get('data_directory_content') or {}).get('tab_count', 0),
        **LOCKED_SAFETY,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    clear_runner_file_cache()
    if rec['returncode'] != 0:
        raise SystemExit(rec['returncode'])


if __name__ == '__main__':
    main()
