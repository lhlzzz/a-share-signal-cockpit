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
import concurrent.futures
import datetime as dt
import hashlib
from collections import Counter
from functools import lru_cache
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from xiaogu_utils import now_iso, read_json, load_jsonl
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
LEGACY_ONE_LOT_COST_CAP = 7000.0
MANUAL_AVAILABLE_CASH_VALUE = 7000.0
MANUAL_AVAILABLE_CASH_ACCOUNT_MODE = 'manual_available_cash_7000'
LEGACY_MANUAL_AVAILABLE_CASH_ACCOUNT_MODE = 'manual_available_cash_6800'
MANUAL_AVAILABLE_CASH_SOURCE = 'user_manual_sell_scenario'
DEFAULT_ACCOUNT_SNAPSHOT_PATH = BASE / 'data' / 'account_snapshot' / 'latest.json'
WEAK_MARKET_SHADOW_BREADTH_GATE = 20.0
SCAN_SUMMARY_NAME = 'eastmoney_web_tabs_summary.json'
SCAN_SUMMARY_RUNNER_NAME = 'eastmoney_web_tabs_summary_runner.json'
REQUIRED_EASTMONEY_EVIDENCE_DOMAINS = ('announcements', 'risk_alerts', 'lhb', 'concept_industry', 'financials')
REQUIRED_EASTMONEY_CDP_TAB_SOURCES = ('quote_rank', 'fund_flow', 'watchlist', 'announcements', 'lhb', 'concept_industry', 'financials')
REQUIRED_EASTMONEY_DEFAULT_ENHANCED_CDP_TAB_SOURCES = (
    'limitup_pool', 'broken_limit_pool', 'consecutive_limit_pool', 'yesterday_limit_pool',
    'popularity_rank', 'industry_board', 'sector_fund_flow',
)
REQUIRED_EASTMONEY_EXPERIMENTAL_ENHANCED_CDP_TAB_SOURCES = (
    'margin_trading', 'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS = (
    'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength', 'yesterday_limit_strength',
    'popularity_heat', 'industry_board', 'sector_fund_flow', 'candidate_quote_recheck',
    'candidate_fund_recheck', 'candidate_lhb_recheck', 'candidate_announcement_recheck',
)
REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS = (
    'margin_trading', 'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS = (
    'candidate_quote_recheck', 'candidate_fund_recheck', 'candidate_lhb_recheck', 'candidate_announcement_recheck',
)
ALLOWED_A_SHARE_SOURCE_TOKENS = ('eastmoney_web_tabs', 'v2_scanner_api', 'eastmoney_api_scan_v2')
API_A_SHARE_SOURCE_TOKENS = ('v2_scanner_api', 'eastmoney_api_scan_v2')
DISALLOWED_GOVERNANCE_TOKENS = ('archive', 'backup', '.bak_', 'rollback', 'crypto', 'bitget', 'us_stock', 'yfinance', 'research_only', 'research-only', 'historical_validation')
REQUIRED_EASTMONEY_CDP_URL = 'http://127.0.0.1:9333'


def is_active_api_source(source: Any) -> bool:
    text = str(source or '')
    return any(token in text for token in API_A_SHARE_SOURCE_TOKENS)


INDEX_CODES = {
    'sh000001': {'sina': 'sh000001', 'tencent': 'sh000001', 'name': '上证指数'},
    'sz399001': {'sina': 'sz399001', 'tencent': 'sz399001', 'name': '深证成指'},
    'sz399006': {'sina': 'sz399006', 'tencent': 'sz399006', 'name': '创业板指'},
}

LOCKED_SAFETY = {
    'paper_only': True,
    'no_trade': True,
    'production_ready': False,
    'allow_trade': False,
    'auto_order': False,
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


def clean_blocker_text(text: str, max_len: int = 200) -> str:
    s = str(text)
    if len(s) > max_len:
        return s[:max_len] + '...'
    return s


SCORING_CONFIG_DEFAULTS = {
    'weekday_blocklist': '',
    'max_score_cap': '88',
    'follow_on_strategy': 't1_close_primary',
    'follow_on_t1_weight': '1.0',
    'follow_on_t2_weight': '0.45',
    'follow_on_t3_weight': '0.25',
    'follow_on_limit_up_threshold': '0.095',
    'horizon_aware_strategy': 'instant_then_delayed',
    'instant_momentum_min_confirmations': '2',
    'delayed_setup_min_persistence': '2',
    'delayed_setup_floor_score': '75',
    'delayed_setup_theme_min_score': '0.5',
    'stale_repeat_window_days': '5',
    'stale_decay_factor': '0.65',
    'l2_limit_strength_bonus': '2.0',
    'sector_catalyst_penalty': '1.0',
    'near_limit_l2_exemption': 'true',
}

TRADE_MODE = 'afternoon_buy_next_day_sell'
PRIMARY_RETURN_FIELD = 't1_return'
PRIMARY_TRADE_HORIZON = 't1_next_day_sell'
HORIZON_NOTE = 'T+2/T+3/T+5 are signal-maturation diagnostics, not multi-day holding PnL.'


def _parse_flow_amount(flow_str: str) -> float:
    import re as _re
    m = _re.match(r'([-\d.]+)(亿|万)?', str(flow_str).strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    if m.group(2) == '万':
        val /= 10000
    return val


def build_capital_flow_lookup(evidence_by_stock: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    precomputed = {}
    import pathlib as _pl
    for p in sorted(_pl.Path('data/live_scan').rglob('eastmoney_web_tabs_evidence.json'), reverse=True):
        try:
            import json as _json
            ev = _json.loads(p.read_text())
            if 'stock_capital_flow_map' in ev:
                precomputed = ev['stock_capital_flow_map']
                break
        except Exception:
            continue

    if precomputed:
        return precomputed

    ccf = {}
    sff = {}

    import re as _re
    stock_flows = {}
    for sym, evidence in evidence_by_stock.items():
        if sym.startswith('_'):
            continue
        ci_rows = evidence.get('concept_industry', []) if isinstance(evidence, dict) else []
        tags = set()
        for r in ci_rows:
            text = r.get('text', '') if isinstance(r, dict) else ''
            parts = text.split()
            if parts:
                tags.add(parts[0])
            for tag in _re.findall(r'[\u4e00-\u9fa5A-Za-z0-9]{2,10}(?:\u6982\u5ff5|\u677f\u5757|\u82af\u7247|\u7535\u5b50|\u901a\u4fe1|\u79d1\u6280|\u5143\u4ef6|\u5238\u5546|\u53c2\u80a1)', text):
                tags.add(tag)
            for tag in _re.findall(r'([\u4e00-\u9fa5]{2,6}[\u2160\u2161\u2162\u2163]?)\s', text):
                if len(tag) >= 2:
                    tags.add(tag)

        best_concept_flow = 0.0
        best_sector_flow = 0.0
        for tag in tags:
            for name, data in ccf.items():
                if tag in name or name in tag:
                    best_concept_flow = max(best_concept_flow, _parse_flow_amount(data['flow']))
            for name, data in sff.items():
                if tag in name or name in tag:
                    best_sector_flow = max(best_sector_flow, _parse_flow_amount(data['flow']))

        stock_flows[sym] = {
            'concept_flow_100m': best_concept_flow,
            'sector_flow_100m': best_sector_flow,
            'tags': list(tags)[:5],
        }
    return stock_flows


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


_LEDGER_CACHE = None

def _preload_ledger():
    """预加载ledger到内存，避免多次读取大文件"""
    global _LEDGER_CACHE
    if _LEDGER_CACHE is not None:
        return
    import gc
    print(f'LEDGER: preloading {LEDGER.name} ({LEDGER.stat().st_size / 1024 / 1024:.0f}MB)...', file=sys.stderr, flush=True)
    _LEDGER_CACHE = list(load_jsonl(LEDGER))
    print(f'LEDGER: loaded {len(_LEDGER_CACHE)} rows', file=sys.stderr, flush=True)
    gc.collect()

def _cached_ledger_rows() -> Tuple[Dict[str, Any], ...]:
    global _LEDGER_CACHE
    if _LEDGER_CACHE is not None:
        return tuple(_LEDGER_CACHE)
    _LEDGER_CACHE = list(load_jsonl(LEDGER))
    return tuple(_LEDGER_CACHE)


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
            for key in ('t1_return', 't2_return', 't3_return', 't5_return'):
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
    horizons = {
        't1': safe_float(record.get('t1_return')),
        't2': safe_float(record.get('t2_return')),
        't3': safe_float(record.get('t3_return')),
        't5': safe_float(record.get('t5_return')),
    }
    realized = [(horizon, value) for horizon, value in horizons.items() if value is not None]
    best_horizon = None
    best_value = None
    if realized:
        best_horizon, best_value = max(realized, key=lambda item: (item[1], -int(item[0][1]) if len(item[0]) > 1 and item[0][1].isdigit() else 0))
    day_map = {'t1': 1, 't2': 2, 't3': 3, 't5': 5}
    days_to_payoff = day_map.get(best_horizon) if best_horizon else None
    primary_trade_return = horizons['t1']
    later_values = [(horizon, value) for horizon, value in horizons.items() if horizon != 't1' and value is not None]
    maturation_horizon = None
    maturation_return = None
    def later_beats_primary(value: float) -> bool:
        if primary_trade_return is None:
            return value > 0
        if primary_trade_return < 0:
            return value > 0
        return value > primary_trade_return
    if later_values:
        later_candidates = [(horizon, value) for horizon, value in later_values if later_beats_primary(value)]
        if later_candidates:
            maturation_horizon, maturation_return = max(
                later_candidates,
                key=lambda item: (item[1], -int(item[0][1]) if len(item[0]) > 1 and item[0][1].isdigit() else 0),
            )
    days_to_maturation = day_map.get(maturation_horizon) if maturation_horizon else None
    if maturation_horizon is None:
        if primary_trade_return is None:
            maturation_class = 'unresolved' if best_value is None else 'weak_multi_horizon'
        elif best_value is None:
            maturation_class = 'unresolved'
        elif primary_trade_return > 0 and best_horizon == 't1':
            maturation_class = 'same_day_next_day_winner'
        else:
            maturation_class = 'weak_multi_horizon'
    elif primary_trade_return is not None and primary_trade_return < 0 and maturation_return is not None and maturation_return > 0:
        maturation_class = 'early_noise_repaired'
    elif primary_trade_return is not None and primary_trade_return > 0 and maturation_return is not None and maturation_return > primary_trade_return:
        maturation_class = 'matured_later'
    else:
        maturation_class = 'matured_later'
    delayed_gap = None
    if primary_trade_return is not None and best_value is not None:
        delayed_gap = round(best_value - primary_trade_return, 4)
    return {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'primary_trade_return': primary_trade_return,
        **{f'{horizon}_return': value for horizon, value in horizons.items()},
        'max_realized_return': best_value,
        'best_horizon': best_horizon,
        'days_to_payoff': days_to_payoff,
        'maturation_horizon': maturation_horizon,
        'maturation_return': maturation_return,
        'days_to_maturation': days_to_maturation,
        'maturation_class': maturation_class,
        'delayed_gap': delayed_gap,
    }


def _ledger_horizon_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    profile = _horizon_profile(record)
    t1 = profile['t1_return']
    best = profile['max_realized_return']
    best_horizon = profile['best_horizon']
    payoff_class = 'unresolved'
    if best is None:
        payoff_class = 'unresolved'
    elif t1 is not None and t1 > 0 and best_horizon == 't1':
        payoff_class = 'instant_winner'
    elif t1 is not None and t1 < 0 and best_horizon in ('t2', 't3', 't5') and best > 0:
        payoff_class = 'early_noise'
    elif best_horizon in ('t2', 't3', 't5') and best > 0:
        payoff_class = 'delayed_winner'
    elif best <= 0:
        payoff_class = 'weak'
    profile['payoff_class'] = payoff_class
    return profile


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
    for key in ('score', 'final_score', 't1_return', 't2_return', 't3_return', 't5_return',
                'max_realized_return', 'best_horizon', 'days_to_payoff', 'delayed_gap', 'payoff_class'):
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


def _candidate_lifecycle_profile(candidate: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
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
    delayed_setup_min_persistence = int(safe_float(scoring_config_values.get('delayed_setup_min_persistence')) or safe_float(SCORING_CONFIG_DEFAULTS['delayed_setup_min_persistence']) or 2)
    delayed_setup_theme_min_score = safe_float(scoring_config_values.get('delayed_setup_theme_min_score'))
    if delayed_setup_theme_min_score is None:
        delayed_setup_theme_min_score = safe_float(SCORING_CONFIG_DEFAULTS['delayed_setup_theme_min_score']) or 0.5
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
    recent_payoffs = [row for row in recent_history if row.get('payoff_class') in ('instant_winner', 'delayed_winner')]
    repeat_count = len(recent_history)
    history_has_delay = any(row.get('payoff_class') == 'delayed_winner' for row in recent_history)
    history_has_instant = any(row.get('payoff_class') == 'instant_winner' for row in recent_history)

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
    candidate_stage = str(profile.get('candidate_stage') or '')
    delayed_support = (
        theme_support >= delayed_setup_theme_min_score
        or (safe_float(profile.get('early_opportunity_score')) or 0.0) >= 0.65
        or (safe_float(profile.get('low_position_catalyst_score')) or 0.0) >= 0.60
    )
    first_seen_reversal_maturation = (
        (profile.get('search_layer_hint') in ('intraday_alert_reversal', 'underwater_reversal')
         or profile.get('setup_type') in ('INTRADAY_ALERT_REVERSAL', 'UNDERWATER_TO_RED_STRENGTH'))
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
        and delayed_support
        and (safe_float(profile.get('intraday_alert_strength')) or 0.0) >= 0.8
        and (safe_float(profile.get('fund_flow_momentum')) or 0.0) > 0
    )
    persistence_support = repeat_count >= delayed_setup_min_persistence
    no_hard_block = not bool(
        (profile.get('regulatory_hard_block') and not is_routine_regulatory_block(str(profile.get('regulatory_hard_block', ''))))
        or profile.get('a_share_risk_review_disqualified_for_paper_pick')
    )

    setup_class = 'WATCH_ONLY'
    setup_reason: List[str] = []
    if no_hard_block and delayed_support and (persistence_support or history_has_delay or history_has_instant or first_seen_reversal_maturation):
        # DELAYED_SETUP means the signal is still maturing and should be re-evaluated later,
        # not that the trade should be held for multiple days.
        setup_class = 'DELAYED_SETUP'
        setup_reason.append('signal_maturation')
        if first_seen_reversal_maturation and not repeat_count:
            setup_reason.append('first_seen_reversal_maturation')
        if repeat_count:
            setup_reason.append(f'candidate_persistence={repeat_count}')
    elif no_hard_block and instant_signal_present and instant_confirmations >= instant_momentum_min_confirmations:
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
        'DELAYED_SETUP': 2.0,
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
        'setup_class': setup_class,
        'setup_rank': setup_rank,
        'setup_reason': setup_reason,
        'repeat_count': repeat_count,
        'recent_history_count': len(recent_history),
        'recent_payoff_count': len(recent_payoffs),
        'history_has_delayed_winner': history_has_delay,
        'history_has_instant_winner': history_has_instant,
        'theme_support': round(theme_support, 4),
        'delayed_support': bool(delayed_support),
        'instant_confirmations': instant_confirmations,
        'stale_decay': stale_decay,
        'lifecycle_score': lifecycle_score,
        'history_tail': recent_history[-3:],
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


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


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_obj = _json_safe_value(obj)
    path.write_text(json.dumps(safe_obj, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def fetch_url(url: str, timeout: int = 8) -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 XiaoguForwardPaper/0.1', 'Referer': 'https://finance.sina.com.cn/'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('gbk', errors='replace')
        return True, data
    except Exception as e:
        return False, repr(e)


def collect_index_snapshot(date: str, asof_time: str) -> Dict[str, Any]:
    raw_dir = RAW_ROOT / date / asof_time.replace(':', '')
    results = {}
    ok_count = 0
    request_results: Dict[Tuple[str, str], Tuple[bool, str]] = {}
    tasks: List[Tuple[str, str, str]] = []
    for code, meta in INDEX_CODES.items():
        tasks.append((code, 'sina', 'https://hq.sinajs.cn/list=' + meta['sina']))
        tasks.append((code, 'tencent', 'https://qt.gtimg.cn/q=' + meta['tencent']))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(tasks)))) as executor:
        future_map = {
            executor.submit(fetch_url, url): (code, source)
            for code, source, url in tasks
        }
        for future, (code, source) in future_map.items():
            ok, text = future.result()
            request_results[(code, source)] = (ok, text)
    for code, meta in INDEX_CODES.items():
        sina_ok, sina_text = request_results.get((code, 'sina'), (False, ''))
        tencent_ok, tencent_text = request_results.get((code, 'tencent'), (False, ''))
        write_text(raw_dir / f'{code}_sina_raw.txt', sina_text)
        write_text(raw_dir / f'{code}_tencent_raw.txt', tencent_text)
        ok_count += int(sina_ok) + int(tencent_ok)
        results[code] = {
            'name': meta['name'],
            'sina_ok': sina_ok,
            'tencent_ok': tencent_ok,
            'sina_raw_path': str(raw_dir / f'{code}_sina_raw.txt'),
            'tencent_raw_path': str(raw_dir / f'{code}_tencent_raw.txt'),
            'sina_len': len(sina_text),
            'tencent_len': len(tencent_text),
        }
    return {
        'raw_dir': str(raw_dir),
        'dual_source_index_snapshot': results,
        'source_ok_count': ok_count,
        'source_total': len(INDEX_CODES) * 2,
        'collected_at': now_iso(),
    }


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
    scenario_name = str(snapshot.get('scenario_name') or bundle.get('scenario_name') or '')
    bundle_date = str(bundle.get('date') or '')
    account_modes = {str(bundle.get('decision_account_mode') or ''), str(bundle.get('account_mode') or '')}
    manual_available_cash_override = (
        snapshot_source == MANUAL_AVAILABLE_CASH_SOURCE
        or scenario_name.startswith('post_manual_sell')
        or MANUAL_AVAILABLE_CASH_ACCOUNT_MODE in account_modes
        or LEGACY_MANUAL_AVAILABLE_CASH_ACCOUNT_MODE in account_modes
        or bundle_date == '2026-06-09'
    )
    cash = account_available_cash(snapshot)
    background_snapshot = snapshot if snapshot and snapshot_source != MANUAL_AVAILABLE_CASH_SOURCE else {}
    holdings = snapshot.get('positions') if isinstance(snapshot.get('positions'), list) else []
    total_assets = safe_float(snapshot.get('total_assets'))
    if total_assets is None and isinstance(snapshot.get('account_summary'), dict):
        total_assets = safe_float(snapshot['account_summary'].get('total_assets'))
    if manual_available_cash_override:
        return {
            'source': MANUAL_AVAILABLE_CASH_ACCOUNT_MODE,
            'account_mode': MANUAL_AVAILABLE_CASH_ACCOUNT_MODE,
            'available_cash': MANUAL_AVAILABLE_CASH_VALUE,
            'one_lot_cost_cap': MANUAL_AVAILABLE_CASH_VALUE,
            'total_assets': MANUAL_AVAILABLE_CASH_VALUE,
            'holdings_for_decision': [],
            '600396_assumed_manually_sold': True,
            'snapshot': snapshot,
            'background_account_snapshot': background_snapshot,
        }
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
        'source': 'legacy_static_cap',
        'account_mode': 'legacy_static_cap',
        'available_cash': None,
        'one_lot_cost_cap': LEGACY_ONE_LOT_COST_CAP,
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
                    COUNT(COALESCE(t1_return_close, t1_return)) AS filled,
                    ROUND(AVG(COALESCE(t1_return_close, t1_return))::numeric, 4) AS avg_t1,
                    COUNT(*) FILTER (WHERE COALESCE(t1_return_close, t1_return) > 0) AS wins,
                    COUNT(t2_return) AS t2_filled,
                    COUNT(*) FILTER (WHERE t2_return > 0) AS t2_wins,
                    COUNT(t3_return) AS t3_filled,
                    COUNT(*) FILTER (WHERE t3_return > 0) AS t3_wins,
                    COUNT(*) FILTER (WHERE COALESCE(t1_return_close, t1_return) <= 0) AS t1_non_positive,
                    COUNT(*) FILTER (WHERE COALESCE(t1_return_close, t1_return) <= 0 AND t2_return > 0) AS late_bloom_t2_count,
                    COUNT(*) FILTER (
                        WHERE COALESCE(t1_return_close, t1_return) IS NOT NULL
                           OR t2_return IS NOT NULL
                           OR t3_return IS NOT NULL
                    ) AS any_filled,
                    COUNT(*) FILTER (
                        WHERE COALESCE(t1_return_close, t1_return) > 0
                           OR t2_return > 0
                           OR t3_return > 0
                    ) AS any_wins
                FROM returns
            """)).fetchone()
            result_count = row[0] or 0
            filled = row[1] or 0
            avg_t1 = float(row[2]) if row[2] is not None else None
            wins = row[3] or 0
            t2_filled = row[4] or 0
            t2_wins = row[5] or 0
            t3_filled = row[6] or 0
            t3_wins = row[7] or 0
            t1_non_positive = row[8] or 0
            late_bloom_t2_count = row[9] or 0
            any_filled = row[10] or 0
            any_wins = row[11] or 0
            t1_positive_rate = wins / filled if filled else None
            return {
                'result_count': result_count,
                't1_positive_rate': t1_positive_rate,
                'avg_t1_return': avg_t1,
                't2_positive_rate': t2_wins / t2_filled if t2_filled else None,
                't3_positive_rate': t3_wins / t3_filled if t3_filled else None,
                'late_bloom_t2_count': late_bloom_t2_count,
                'late_bloom_rate': late_bloom_t2_count / t1_non_positive if t1_non_positive else None,
                'win_any_t1_t3_rate': any_wins / any_filled if any_filled else None,
                'recent_results': [],
            }
    except Exception:
        return {
            'result_count': 0,
            't1_positive_rate': None,
            'avg_t1_return': None,
            't2_positive_rate': None,
            't3_positive_rate': None,
            'late_bloom_t2_count': 0,
            'late_bloom_rate': None,
            'win_any_t1_t3_rate': None,
            'recent_results': [],
        }


@lru_cache(maxsize=1)
def replay_win_stats(topk: int) -> Dict[str, Any]:
    summary = forward_ledger_win_stats()
    return {
        'ticket_count': summary.get('result_count'),
        't1_positive_rate': summary.get('t1_positive_rate'),
        'avg_t1_return': summary.get('avg_t1_return'),
        'win_any_t1_t3_rate': summary.get('win_any_t1_t3_rate'),
    }


def repo_contribution_summary_text(repo_contributions: Dict[str, Any]) -> str:
    if not isinstance(repo_contributions, dict) or not repo_contributions:
        return ''
    ordered = ('tradingagent_a', 'VEI', 'Qlib', 'QuantDinger', 'UZI_Skill')
    parts: List[str] = []
    for repo_name in ordered:
        entry = repo_contributions.get(repo_name)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get('status') or '')
        signal = str(entry.get('candidate_signal') or '')
        delta = safe_float(entry.get('score_delta')) or 0.0
        parts.append(f'{repo_name}:{status}[{signal}]={delta:+.4f}')
    for repo_name in sorted(repo_contributions):
        if repo_name in ordered:
            continue
        entry = repo_contributions.get(repo_name)
        if not isinstance(entry, dict):
            continue
        status = str(entry.get('status') or '')
        signal = str(entry.get('candidate_signal') or '')
        delta = safe_float(entry.get('score_delta')) or 0.0
        parts.append(f'{repo_name}:{status}[{signal}]={delta:+.4f}')
    return '; '.join(parts)


def candidate_repo_delta_by_repo(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo_delta_by_repo = candidate.get('repo_delta_by_repo') or candidate.get('score_delta_by_repo') or {}
    return repo_delta_by_repo if isinstance(repo_delta_by_repo, dict) else {}


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
    original_repo_contributions = candidate.get('repo_contributions') if isinstance(candidate.get('repo_contributions'), dict) else {}
    repo_contributions = original_repo_contributions
    score_delta_by_repo = candidate.get('score_delta_by_repo') or candidate.get('repo_delta_by_repo') or {}
    if not isinstance(score_delta_by_repo, dict):
        score_delta_by_repo = {}
    scan_repo_delta_by_repo = candidate_repo_delta_by_repo(candidate)
    repo_contribution_summary = str(candidate.get('repo_contribution_summary') or '')
    if not repo_contribution_summary:
        repo_contribution_summary = repo_contribution_summary_text(repo_contributions)
    final_score = candidate.get('final_score') if candidate.get('final_score') is not None else candidate.get('score')
    final_score_explanation = str(candidate.get('final_score_explanation') or '')
    evidence_context_present = any(candidate.get(key) not in (None, '', {}) for key in ('source_time', 'source_row_hash', 'evidence_path', 'raw_snapshot_path', 'raw_data_snapshot_path'))
    if not repo_contributions and evidence_context_present:
        try:
            repo_signals = aggregate_four_repo_native_signals(candidate)
            repo_contributions = repo_signals.get('repo_contributions', repo_contributions)
            score_delta_by_repo = repo_signals.get('score_delta_by_repo', score_delta_by_repo)
            if not isinstance(score_delta_by_repo, dict):
                score_delta_by_repo = {}
            repo_contribution_summary = str(repo_signals.get('repo_contribution_summary') or repo_contribution_summary or '')
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
    if chase_high:
        sector_opp = safe_float(candidate.get('sector_opportunity_score') or candidate.get('candidate_features', {}).get('sector_catalyst_score')) or 0.0
        fund_mom = safe_float(candidate.get('fund_flow_momentum') or candidate.get('candidate_features', {}).get('fund_flow_momentum')) or 0.0
        if sector_opp < 1.5 and fund_mom < 0.8:
            hard_blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')
    if any('sector_opportunity_score' in str(mc) for mc in missing_conditions):
        pass
    if not can_afford_one_lot:
        hard_blockers.append('one_lot_cost>cap')
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
    if chase_high:
        sector_opp = safe_float(candidate.get('sector_opportunity_score') or candidate.get('candidate_features', {}).get('sector_catalyst_score')) or 0.0
        fund_mom = safe_float(candidate.get('fund_flow_momentum') or candidate.get('candidate_features', {}).get('fund_flow_momentum')) or 0.0
        if sector_opp < 1.5 and fund_mom < 0.8:
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
        'target_status': single_target_card_status(decision, candidate, flags, can_afford_one_lot),
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
            'chase_high_without_limitup_confirmation': bool(candidate.get('opportunity_hard_block') == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' or any('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' in str(flag) for flag in flags)),
            'qualified_candidate': bool(candidate.get('qualified_candidate')),
        },
        'historical_win_stats': historical_win_stats,
        'manual_trade_only': True,
        'paper_only': True,
        'no_trade': True,
        'allow_trade': False,
        'auto_order': False,
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
    for key in ('final_score', 'score', 'final_shadow_score', 'structured_score'):
        value = safe_float(candidate.get(key))
        if value is not None:
            return value
    return None


def _positive_numeric(value: Any) -> float:
    numeric = safe_float(value) or 0.0
    return max(0.0, numeric)


def archetype_score_adjustments(candidate: Dict[str, Any]) -> Dict[str, Any]:
    features = candidate.get('candidate_features') if isinstance(candidate.get('candidate_features'), dict) else candidate
    profile = structured_signal_profile(candidate)
    details = profile.get('structured_component_details') if isinstance(profile.get('structured_component_details'), dict) else {}
    score = candidate_score_value(candidate) or 50.0
    score_boost = 0.0
    score_penalty = 0.0
    reasons: List[str] = []

    raw_flow = candidate.get('data_directory_capital_flow') if isinstance(candidate.get('data_directory_capital_flow'), dict) else {}
    capital_flow = _positive_numeric(raw_flow.get('main_force_net_inflow')) or _positive_numeric(features.get('net_inflow_main'))
    if capital_flow > 0:
        boost = min(3.5, math.log10(capital_flow / 1000000.0 + 1.0) * 1.5)
        score_boost += boost
        reasons.append(f'positive_capital_flow:+{boost:.2f}')

    sector_strength = max(
        _positive_numeric(features.get('sector_opportunity_score')),
        _positive_numeric(features.get('sector_catalyst_score')),
        _positive_numeric(features.get('main_theme_core_score')),
        _positive_numeric(features.get('main_theme_alignment_score')),
    )
    # 板块催化加分：只在板块强度适中时加分（历史数据: 0.5-0.8最优）
    if 0.5 <= sector_strength <= 0.8:
        boost = min(3.5, sector_strength * 2.4)
        score_boost += boost
        reasons.append(f'sector_theme_catalyst_optimal:+{boost:.2f}')
    elif sector_strength > 0 and sector_strength < 0.5:
        boost = min(2.0, sector_strength * 2.0)
        score_boost += boost
        reasons.append(f'sector_theme_catalyst_mild:+{boost:.2f}')
    # 板块强度>=0.8时不加分（在contrarian_re_score中动态惩罚）

    # 中等分数(80-90) + 低板块催化 = 最优组合（历史数据: 85.7%胜率）
    score_val = candidate_score_value(candidate) or 50.0
    if 80 <= score_val <= 90 and sector_strength < 0.5:
        boost = 3.0
        score_boost += boost
        reasons.append(f'sweet_spot_medium_score_low_catalyst:+{boost:.2f}')

    intraday_alert = max(
        _positive_numeric(features.get('intraday_alert_strength')),
        _positive_numeric(details.get('pre_limitup_anomaly')),
        _positive_numeric(details.get('weak_to_strong_reversal')),
        _positive_numeric(features.get('limitup_reason_propagation_score')),
    )
    if intraday_alert > 0:
        boost = min(2.8, intraday_alert * 2.1)
        score_boost += boost
        reasons.append(f'intraday_alert:+{boost:.2f}')

    candidate_stage = str(features.get('candidate_stage') or profile.get('candidate_stage') or '')
    recovery_signal = max(
        _positive_numeric(features.get('close_position_score')),
        _positive_numeric(features.get('low_position_catalyst_score')),
        _positive_numeric(features.get('early_opportunity_score')),
    )
    if candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        boost = 1.0 + min(1.6, recovery_signal * 1.6)
        score_boost += boost
        reasons.append(f'underwater_recovery:+{boost:.2f}')

    one_lot_cost = safe_float(features.get('one_lot_cost'))
    if one_lot_cost is None:
        price = safe_float(features.get('price'))
        if price is not None:
            one_lot_cost = price * 100
    cap = safe_float(features.get('one_lot_cost_cap')) or LEGACY_ONE_LOT_COST_CAP
    if one_lot_cost is not None and cap is not None and one_lot_cost <= cap:
        score_boost += 0.6
        reasons.append('affordable_lot:+0.60')

    regulatory_block = str(features.get('regulatory_hard_block') or '')
    if not regulatory_block or is_routine_regulatory_block(regulatory_block):
        score_boost += 0.4
        reasons.append('non_hard_regulatory:+0.40')

    limitup_confirmation = max(
        _positive_numeric(features.get('limitup_reason_strength')),
        _positive_numeric(features.get('limitup_capture_score')),
        _positive_numeric(features.get('seal_order_strength')),
        _positive_numeric(features.get('order_book_pressure')),
    )
    news_catalyst_strength = _positive_numeric(features.get('news_catalyst_strength'))
    signal_pct = _positive_numeric(features.get('signal_pct'))
    fund_flow_momentum = _positive_numeric(features.get('fund_flow_momentum'))
    time_series_momentum = _positive_numeric(features.get('time_series_momentum'))
    market_cap_proxy = max(
        _positive_numeric(features.get('full_universe_amount_pctile')),
        _positive_numeric(features.get('full_universe_fund_pctile')),
        _positive_numeric(features.get('amount_pctile_rule')),
    )
    sector_tags = ' '.join(normalize_tag_list(features.get('sector_opportunity_tags')))
    name_text = f"{features.get('name') or ''} {features.get('sector_name') or ''} {features.get('industry_name') or ''}"
    is_financial = any(token in f"{sector_tags} {name_text}" for token in ('金融', '银行', '证券', '保险'))

    if score >= 88 and sector_strength < 0.4 and limitup_confirmation < 0.55:
        penalty = 4.5
        score_penalty += penalty
        reasons.append(f'high_score_no_real_confirmation:-{penalty:.2f}')
    if candidate_stage in ('high_7_to_9', 'near_limit_9_plus') and limitup_confirmation < 0.55:
        penalty = 2.5
        if signal_pct >= 8.0:
            penalty += 0.7
        score_penalty += penalty
        reasons.append(f'chase_high_without_confirmation:-{penalty:.2f}')
    if news_catalyst_strength > 0 and sector_strength < 0.35 and limitup_confirmation < 0.35:
        penalty = 1.6
        score_penalty += penalty
        reasons.append(f'news_only_without_limitup_reason:-{penalty:.2f}')
    if market_cap_proxy >= 0.85:
        penalty = min(3.0, 0.8 + (market_cap_proxy - 0.75) * 4.0)
        score_penalty += penalty
        reasons.append(f'crowded_or_large_cap_proxy:-{penalty:.2f}')
    if is_financial:
        penalty = 1.8
        score_penalty += penalty
        reasons.append(f'financial_or_banking_drag:-{penalty:.2f}')
    if score >= 85 and fund_flow_momentum < 0.2 and time_series_momentum < 0.15:
        penalty = 1.4
        score_penalty += penalty
        reasons.append(f'weak_next_day_proxy:-{penalty:.2f}')
    # 板块过热惩罚（历史数据: sector_cat>=0.8 胜率18%，需动态判断）
    # 在弱势/震荡市场，板块热是坏事；在强势市场，板块热可以追
    market_breadth = _positive_numeric(features.get('market_breadth_up_pct'))
    if sector_strength >= 0.8 and market_breadth < 60:  # 弱势/震荡市场
        penalty = 8.0
        score_penalty += penalty
        reasons.append(f'sector_overheated_weak_market:-{penalty:.2f}')
    elif sector_strength >= 0.8 and market_breadth < 70:  # 中性市场
        penalty = 4.0
        score_penalty += penalty
        reasons.append(f'sector_overheated_neutral_market:-{penalty:.2f}')
    if score >= 90 and recovery_signal < 0.45 and limitup_confirmation < 0.45:
        penalty = 1.2
        score_penalty += penalty
        reasons.append(f'no_follow_through_support:-{penalty:.2f}')

    return {
        'score_boost': round(score_boost, 4),
        'score_penalty': round(score_penalty, 4),
        'net_adjustment': round(score_boost - score_penalty, 4),
        'reasons': reasons,
    }


def _normalize_news_kuaixun_rows(rows: Any) -> List[Dict[str, str]]:
    news_list: List[Dict[str, str]] = []
    items = rows if isinstance(rows, list) else ((rows.get('list') or rows.get('items') or []) if isinstance(rows, dict) else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get('title', '') or item.get('showTitle', '') or ''
        content = item.get('content', '') or item.get('digest', '') or title or ''
        if title or content:
            news_list.append({'title': str(title).strip(), 'content': str(content).strip()[:500]})
    return news_list


def _load_news_kuaixun(target_date: str = '') -> List[Dict[str, str]]:
    """加载东财7x24快讯新闻；official path 只读 scanner summary/DB raw payload。"""
    if not target_date:
        target_date = dt.date.today().isoformat()

    candidate_paths = [
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan_afternoon' / 'news_kuaixun.jsonl',
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan_morning' / 'news_kuaixun.jsonl',
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan_v2' / 'news_kuaixun.jsonl',
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan' / 'news_kuaixun.jsonl',
    ]
    try:
        for summary_path in scan_summary_paths(target_date):
            summary = read_json(Path(summary_path))
            files = summary.get('files') if isinstance(summary, dict) else {}
            news_file = files.get('news_kuaixun') if isinstance(files, dict) else None
            if isinstance(news_file, dict):
                news_file = news_file.get('path') or news_file.get('file')
            if news_file:
                candidate_paths.append(Path(news_file))
    except Exception:
        pass

    for news_path in candidate_paths:
        if not news_path.exists():
            continue
        try:
            rows = []
            with open(news_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            news_list = _normalize_news_kuaixun_rows(rows)
            if news_list:
                return news_list
        except Exception:
            continue

    try:
        from xiaogu_db import fetch_latest_api_scan_session_with_market_data, fetch_scan_market_data_payloads
        session = fetch_latest_api_scan_session_with_market_data(_parse_date(target_date))
        if session and session.get('id'):
            payloads = fetch_scan_market_data_payloads(int(session['id']))
            news_list = _normalize_news_kuaixun_rows(payloads.get('news_kuaixun'))
            if news_list:
                return news_list
    except Exception as exc:
        print(f'WARN: NEWS_KUAIXUN_DB_SOURCE_UNAVAILABLE: {exc}', file=sys.stderr)

    print('WARN: NEWS_KUAIXUN_SOURCE_MISSING', file=sys.stderr)
    return []


def _load_sector_names() -> List[str]:
    """从v2 scanner数据加载实际板块名称。"""
    today = dt.date.today().isoformat()
    sector_names = []

    # 加载概念板块
    concept_path = BASE / 'data' / 'live_scan' / today / 'eastmoney_scan_v2' / 'sector_concept.jsonl'
    if concept_path.exists():
        try:
            with open(concept_path, 'r') as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        name = item.get('f14', '')
                        if name:
                            sector_names.append(name)
        except:
            pass

    # 加载行业板块
    industry_path = BASE / 'data' / 'live_scan' / today / 'eastmoney_scan_v2' / 'sector_industry.jsonl'
    if industry_path.exists():
        try:
            with open(industry_path, 'r') as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        name = item.get('f14', '')
                        if name:
                            sector_names.append(name)
        except:
            pass

    return sector_names


# 缓存板块名称
_SECTOR_NAMES_CACHE: List[str] = []


def _get_sector_names() -> List[str]:
    """获取板块名称（带缓存）。"""
    global _SECTOR_NAMES_CACHE
    if not _SECTOR_NAMES_CACHE:
        _SECTOR_NAMES_CACHE = _load_sector_names()
    return _SECTOR_NAMES_CACHE


def _analyze_news_sentiment(news_list: List[Dict[str, str]]) -> Dict[str, float]:
    """分析新闻情感，返回各板块得分（动态匹配实际板块名称）。"""
    # 核心关键词（用于从新闻中提取信号）
    core_keywords = {
        '银行': ['银行', '降准', '降息', 'LPR', '金融改革'],
        '创新药': ['创新药', 'CXO', '医药', '生物制品', '疫苗', '单抗', '减肥药'],
        '红利': ['红利', '高股息', '央企估值', '破净修复', '中特估'],
        '机器人': ['人形机器人', '特斯拉机器人', '减速器', '伺服电机'],
        'AI': ['大模型', '算力', 'GPU', 'AI应用', '人工智能'],
        '半导体': ['芯片法案', '半导体补贴', '国产替代', '光刻机'],
        '军工': ['军费增长', '装备订单', '航天发射', '卫星互联网'],
        '煤炭': ['煤炭', '能源安全', '煤价', '限产'],
        '新能源': ['光伏补贴', '储能政策', '锂电池', '充电桩'],
    }

    # 从API加载实际板块名称
    sector_names = _get_sector_names()

    # 计算每个核心关键词的得分
    keyword_scores = {}
    for keyword, synonyms in core_keywords.items():
        score = 0
        match_count = 0
        for news in news_list:
            text = (news.get('title', '') + ' ' + news.get('content', '')).lower()
            for kw in synonyms:
                if kw.lower() in text:
                    score += 15
                    match_count += 1
                    break
        if match_count > 0:
            keyword_scores[keyword] = min(100, score)

    # 将核心关键词得分映射到实际板块名称
    sector_scores = {}
    for sector_name in sector_names:
        sector_lower = sector_name.lower()
        for keyword, score in keyword_scores.items():
            # 如果板块名称包含关键词，继承该关键词的得分
            if keyword.lower() in sector_lower:
                sector_scores[sector_name] = max(sector_scores.get(sector_name, 0), score)
                break

    return sector_scores


# 缓存新闻数据（每session只加载一次）
_NEWS_CACHE: Optional[Tuple[List[Dict], Dict[str, float]]] = None
_NEWS_CACHE_DATE: str = ''


def _get_news_analysis(target_date: str = '') -> Tuple[List[Dict], Dict[str, float]]:
    """获取新闻分析结果（带缓存）。"""
    global _NEWS_CACHE, _NEWS_CACHE_DATE
    if not target_date:
        target_date = dt.date.today().isoformat()
    if _NEWS_CACHE is None or _NEWS_CACHE_DATE != target_date:
        news_list = _load_news_kuaixun(target_date)
        sector_scores = _analyze_news_sentiment(news_list)
        _NEWS_CACHE = (news_list, sector_scores)
        _NEWS_CACHE_DATE = target_date
    return _NEWS_CACHE


def contrarian_re_score(candidate: Dict[str, Any]) -> float:
    """Dynamic regime-aware re-scoring: reward momentum in strong markets, contrarian in weak.
    
    Data insight:
      强势市场: 高信号+4.79% vs 低信号+0.04% → 追强有效
      弱势市场: 高信号-3.74% vs 低信号+0.85% → 反共识有效
    """
    features = candidate.get('candidate_features') or candidate
    original = candidate_score_value(candidate) or 50.0
    scoring_config = get_scoring_config_snapshot()
    config = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(config, dict):
        config = dict(SCORING_CONFIG_DEFAULTS)

    signal_pct = safe_float(features.get('signal_pct')) or 0.5
    close_pos = safe_float(features.get('close_position_score')) or 0.5
    fund_mom = safe_float(features.get('fund_flow_momentum')) or 0.5
    sector_cat = safe_float(features.get('sector_catalyst_score')) or 0.5
    early_opp = safe_float(features.get('early_opportunity_score')) or 0.5
    market_regime = str(features.get('market_regime', '') or '').lower()

    # 加载新闻分析
    news_list, news_sector_scores = _get_news_analysis()

    # 市场级别信号（更准确的市场风格判断）
    market_breadth = safe_float(features.get('market_breadth_up_pct')) or 50.0
    market_limitups = safe_float(features.get('market_limitups')) or 0
    market_bigups = safe_float(features.get('market_bigups')) or 0

    # Detect market regime from both candidate signals AND market-level signals
    # Strong: high signal_pct + high fund_flow + positive momentum + strong market breadth
    # Weak: low signal_pct or negative fund_flow OR weak market breadth
    momentum_signals = signal_pct * 0.3 + fund_mom * 0.2 + sector_cat * 0.2
    market_signals = (market_breadth / 100.0) * 0.2 + min(1.0, market_limitups / 100.0) * 0.1

    combined_signal = momentum_signals + market_signals

    if combined_signal > 1.2 or 'bull' in market_regime or 'strong' in market_regime:
        regime = 'strong'
    elif combined_signal < 0.6 or 'bear' in market_regime or 'weak' in market_regime or market_breadth < 40:
        regime = 'weak'
    else:
        regime = 'sideways'

    if regime == 'strong':
        # 追强: reward high signal, high flow, hot sector
        signal_adj = signal_pct * 3          # 高信号加分
        position_adj = close_pos * 5         # 高位加分（动量延续）
        flow_adj = fund_mom * 4              # 高资金流加分
        sector_adj = sector_cat * 3          # 热板块加分
        early_adj = early_opp * 2            # 早期机会少量加分
    elif regime == 'weak':
        # 反共识: reward low signal, low position, low flow
        signal_adj = max(0, (1.0 - signal_pct / 5.0)) * 15
        position_adj = max(0, (0.9 - close_pos)) * 20
        flow_adj = max(0, (0.9 - fund_mom)) * 10
        # 弱势市场：板块热是坏事（历史数据: sector_cat>=0.8 胜率18%）
        if sector_cat >= 0.8:
            sector_adj = -15  # 主动惩罚
        elif sector_cat >= 0.5:
            sector_adj = -5   # 轻微惩罚
        else:
            sector_adj = max(0, (0.9 - sector_cat)) * 8  # 冷门加分
        early_adj = early_opp * 12
    else:
        # 震荡: balanced
        signal_adj = (1.0 - abs(signal_pct - 1.5) / 3.0) * 8   # 中等信号最优
        position_adj = max(0, (0.85 - close_pos)) * 15           # 中低位最优
        flow_adj = max(0, (0.85 - fund_mom)) * 8                 # 中等资金流最优
        # 震荡市场：板块热也要小心
        if sector_cat >= 0.8:
            sector_adj = -8   # 惩罚
        elif sector_cat >= 0.5:
            sector_adj = max(0, (0.85 - sector_cat)) * 6  # 中等最优
        else:
            sector_adj = max(0, (0.85 - sector_cat)) * 6  # 冷门加分
        early_adj = early_opp * 10                                # 早期机会加分

    archetype_adjustment = archetype_score_adjustments(candidate)
    re_score = original + signal_adj + position_adj + flow_adj + sector_adj + early_adj
    re_score += archetype_adjustment['net_adjustment']

    # 新闻驱动加分：如果候选板块有新闻催化，加分
    if news_sector_scores:
        candidate_name = str(features.get('name') or '') + ' ' + str(features.get('sector_name') or '')
        news_boost = 0
        for sector, score in news_sector_scores.items():
            if sector in candidate_name:
                # 新闻分数越高，加分越多（最多+10）
                news_boost = min(10, score / 10)
                break
        if news_boost > 0:
            re_score += news_boost
            archetype_adjustment.setdefault('reasons', []).append(f'news_catalyst:+{news_boost:.2f}')

    # Social signals are intentionally diagnostic/shadow-only until the
    # production ranking gate is unlocked. Do not add them to this score.

    # Score cap: penalize candidates above threshold (100+ scores have 0% win rate)
    cap = safe_float(config.get('max_score_cap')) or safe_float(SCORING_CONFIG_DEFAULTS['max_score_cap']) or 95.0
    if original > cap:
        penalty = (original - cap) * 0.3
        re_score -= penalty
        archetype_adjustment.setdefault('reasons', []).append(f'max_score_cap_penalty:-{penalty:.2f}')

    # 风险调整
    # 涨幅风险惩罚: signal_pct > 8% 时追高风险大 (历史数据: >8% 胜率 0%)
    # 只对有实际分数的 case 应用
    if original > 50 and signal_pct > 8.0:
        overextend_penalty = (signal_pct - 8.0) * 5  # 每超过 1% 扣 5 分
        re_score -= overextend_penalty
        archetype_adjustment.setdefault('reasons', []).append(f'overextend_risk_penalty:-{overextend_penalty:.2f}')

    # Climax 市场惩罚: 高潮期容易见顶 (历史数据: climax 胜率 50%, 平均收益 0.04%)
    # 无条件应用，因为 climax 市场下所有候选都危险
    if 'climax' in market_regime:
        climax_penalty = 20  # 固定惩罚 20 分（从 15 提高到 20）
        re_score -= climax_penalty
        archetype_adjustment.setdefault('reasons', []).append(f'climax_market_penalty:-{climax_penalty:.2f}')

    # 强势市场奖励: 强势市场最安全 (历史数据: strong 胜率 80%, 平均收益 1.85%)
    if 'strong' in market_regime or 'bull' in market_regime:
        strong_bonus = 10  # 固定奖励 10 分
        re_score += strong_bonus
        archetype_adjustment.setdefault('reasons', []).append(f'strong_market_bonus:+{strong_bonus:.2f}')

    candidate['_re_score_archetype_adjustment'] = archetype_adjustment
    candidate['_re_score_scoring_config_source'] = str(scoring_config.get('source') or 'defaults')
    candidate['_re_score_scoring_config_loaded'] = bool(scoring_config.get('loaded'))
    candidate['_re_score_scoring_config_error'] = str(scoring_config.get('error') or '')

    return round(re_score, 1)


def candidate_rank_value(candidate: Dict[str, Any]) -> int:
    rank = safe_int(candidate.get('rank'))
    return rank if rank is not None else 999999


def scan_summary_for_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    summary_path = str(bundle.get('scan_summary_path') or '')
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            try:
                summary = read_json(path)
                if isinstance(summary, dict):
                    return summary
            except Exception:
                pass
    source_evidence = bundle.get('source_evidence') if isinstance(bundle.get('source_evidence'), dict) else {}
    summary_path = str(source_evidence.get('summary_path') or '')
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            try:
                summary = read_json(path)
                if isinstance(summary, dict):
                    return summary
            except Exception:
                pass
    return {}


def bundle_metric(bundle: Dict[str, Any], key: str, default: Any = None) -> Any:
    summary = scan_summary_for_bundle(bundle)
    for source in (summary, bundle.get('market_snapshot'), bundle.get('source_status')):
        if isinstance(source, dict):
            value = source.get(key)
            if value not in (None, ''):
                return value
    return default


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
    return bool(one_lot_cost is not None and decision_cap is not None and one_lot_cost <= decision_cap)


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
        'target_status': single_target_card_status(decision or 'NO_PICK', candidate, flags, candidate_can_afford_one_lot(candidate, bundle)),
        'official_decision_if_evaluated': decision,
        'official_decision_reason_if_evaluated': reason,
        'missing_conditions': missing_conditions,
        'blockers': blockers,
        'positive_conditions': positive_conditions,
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


def highest_score_candidate_from_bundle(bundle: Dict[str, Any], fallback_candidates: List[Dict[str, Any]] | None = None) -> Tuple[Dict[str, Any] | None, str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidates = [candidate for candidate in bundle.get('paper_scoring_candidates', []) if isinstance(candidate, dict)]

    def _score_or_default(c):
        s = candidate_score_value(c)
        if s is not None:
            return s
        # Fallback: use rank + signal_pct for unscored candidates (Phase 1 summary case)
        rank = safe_float(c.get('rank')) or 999.0
        signal = safe_float(c.get('signal_pct')) or 0.0
        return max(0, 100 - rank) + signal * 0.1

    scored_candidates = [candidate for candidate in candidates if symbol_for(candidate)]
    if scored_candidates:
        for c in scored_candidates:
            c['_effective_score'] = _score_or_default(c)
            c['_has_real_score'] = 1 if candidate_score_value(c) is not None else 0
        # Prefer candidates with real scores over fallback scores
        best = max(scored_candidates, key=lambda candidate: (
            candidate.get('_has_real_score', 0),
            candidate.get('_effective_score', 0),
            -candidate_rank_value(candidate),
            symbol_for(candidate),
        ))
        return best, ''
    fallback_candidates = [candidate for candidate in (fallback_candidates or []) if isinstance(candidate, dict)]
    fallback_candidates = [candidate for candidate in fallback_candidates if symbol_for(candidate)]
    if fallback_candidates:
        for c in fallback_candidates:
            c['_contrarian_score'] = contrarian_re_score(c)
            c['_effective_score'] = _score_or_default(c)
        best = max(fallback_candidates, key=lambda candidate: (
            candidate.get('_effective_score', 0),
            -candidate_rank_value(candidate),
            symbol_for(candidate),
        ))
        return best, ''
    return None, 'no_scored_candidate_available_in_paper_scoring_candidates_or_fallback'


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
    # v2 scanner数据不需要完整的evidence检查
    source = str(bundle.get('candidate_source') or bundle.get('source') or bundle.get('pipeline_version') or '')
    if 'v2_scanner_api' in source or 'eastmoney_api_scan_v2' in source:
        # v2 scanner只检查基础数据完整性
        quote_count = int(bundle.get('market_snapshot', {}).get('universe_quote_count') or bundle.get('universe_quote_count') or 0)
        if quote_count <= 0:
            return ['ZERO_QUOTE_READ']
        return []

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
    required_cdp_tabs = source_status.get('required_cdp_tabs') if isinstance(source_status.get('required_cdp_tabs'), dict) else {}
    if required_cdp_tabs.get('status') != 'PASS':
        flags.append('DATA_SOURCE_INCOMPLETE')
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
    fallback_candidates: List[Dict[str, Any]] | None = None,
) -> Tuple[List[Dict[str, Any]], str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    candidates = [candidate for candidate in bundle.get('paper_scoring_candidates', []) if isinstance(candidate, dict)]
    if not candidates and fallback_candidates:
        candidates = [candidate for candidate in fallback_candidates if isinstance(candidate, dict)]
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
        score = safe_float(features.get('_contrarian_score'))
        if score is None:
            score = safe_float(candidate.get('_contrarian_score'))
        if score is None:
            score = candidate_score_value(features)
        if score is None:
            score = contrarian_re_score(features)
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
    fallback_candidates: List[Dict[str, Any]] | None = None,
) -> Tuple[Dict[str, Any] | None, str]:
    evaluations, reason = ranked_no_pick_candidate_evaluations(bundle, target_date, fallback_candidates)
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
    card = no_pick_diagnostics.get('highest_score_candidate')
    if not isinstance(card, dict) or not symbol_for(card):
        ranked = no_pick_diagnostics.get('ranked_no_pick_candidates')
        card = ranked[0] if isinstance(ranked, list) and ranked and isinstance(ranked[0], dict) else None
    if not isinstance(card, dict) or not symbol_for(card):
        card = no_pick_diagnostics.get('closest_to_pick_candidate')
    if not isinstance(card, dict) or not symbol_for(card):
        return None
    return {
        'status': 'DAILY_BEST_PAPER_WATCH',
        'official_decision': 'NO_PICK',
        'not_official_paper_pick': True,
        'observation_only': True,
        'watch_status': 'DAILY_BEST_PAPER_WATCH',
        'selection_source': str(card.get('diagnostic_role') or 'highest_score_candidate'),
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
        'selection_basis': no_pick_diagnostics.get('selection_basis') or [],
        'explanation': 'Best current paper-watch candidate when official decision is NO_PICK; official gates and trading safety are unchanged.',
        **LOCKED_SAFETY,
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
    highest_candidate, highest_reason = highest_score_candidate_from_bundle(bundle, [first_rejected_candidate])
    ranked_evaluations, ranked_reason = ranked_no_pick_candidate_evaluations(bundle, target_date, [first_rejected_candidate])
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
        'highest_score_candidate': None,
        'highest_score_candidate_reason': highest_reason,
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
        diagnostics['highest_score_candidate'] = build_candidate_diagnostic_card(
            highest_features,
            bundle,
            target_date,
            highest_decision,
            highest_evaluated_reason,
            highest_flags,
            'highest_score_candidate',
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

    explanation_parts = [
        f"scan_passed_count={diagnostics['scan_passed_count']}" if diagnostics['scan_passed_count'] is not None else 'scan_passed_count=null',
        f"scan_scored_count={diagnostics['scan_scored_count']}" if diagnostics['scan_scored_count'] is not None else 'scan_scored_count=null',
        f"paper_scoring_candidates_count={diagnostics['paper_scoring_candidates_count']}",
        f"ranked_no_pick_candidates_total={diagnostics['ranked_no_pick_candidates_total']}",
        f"ranked_no_pick_candidates_shown={diagnostics['ranked_no_pick_candidates_shown']}",
        f"ranked_no_pick_candidates_omitted={diagnostics['ranked_no_pick_candidates_omitted']}",
        'scan_passed_count is broader than paper_scoring_candidates_count; only formal basket candidates are ranked here',
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
        'default behavior is load_latest_eastmoney_scan(...) / existing bundle first; live scraping runs only when --trigger-scan is explicitly requested',
    ]
    if domain_summary['partial'] or domain_summary['missing']:
        notes.append('partial or missing sections indicate data that was not available in the current API snapshot and remain explicit for governance')
    return {
        'candidate_source': str(bundle.get('candidate_source') or bundle.get('source') or ''),
        'consumption_policy': 'consume_existing_scan_first_then_current_bundle_only_trigger_scan_on_explicit_flag',
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
    evaluations, evaluation_reason = ranked_no_pick_candidate_evaluations(bundle, target_date, candidates)
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

    highest_candidate, highest_reason = highest_score_candidate_from_bundle(bundle, [candidate_features] if candidate_features else None)
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
            'highest_score_candidate',
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
        'highest_score_candidate': highest_card,
        'highest_score_candidate_reason': highest_reason,
        'closest_to_pick_candidate': closest_card,
    }
    if decision == 'NO_PICK' or candidate_features.get('no_pick_promoted_to_highest_score'):
        watch = build_daily_best_paper_watch({
            'highest_score_candidate': highest_card,
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


def symbol_for(candidate: Dict[str, Any]) -> str:
    symbol = candidate.get('symbol') or candidate.get('code') or ''
    return str(symbol).zfill(6) if symbol else ''


def normalize_market_regime_for_db(value: Any) -> str:
    regime = str(value or '').strip()
    if not regime:
        return 'eastmoney_web_tabs'
    if regime == 'eastmoney_web_tabs_live':
        return 'eastmoney_web_tabs'
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


def missing_coverage_items(source_status: Dict[str, Any], section_name: str, missing_key: str, expected_items: Tuple[str, ...]) -> List[str]:
    section = source_status.get(section_name, {}) if isinstance(source_status, dict) else {}
    if not isinstance(section, dict) or not section:
        return list(expected_items)
    explicit_missing = section.get(missing_key)
    if isinstance(explicit_missing, list):
        missing = [str(item) for item in explicit_missing if item]
        if section.get('status') != 'PASS' and not missing:
            return list(expected_items)
        return missing
    if section.get('status') != 'PASS':
        return list(expected_items)
    return []


def web_tabs_evidence_missing_flags(bundle: Dict[str, Any]) -> List[str]:
    candidate_source = str(bundle.get('candidate_source', ''))
    if 'eastmoney_web_tabs' not in candidate_source:
        return []
    is_four_repo_source = 'four_repo' in candidate_source
    is_formal_web_tabs_source = 'eastmoney_web_tabs_scan_v0_1' in candidate_source
    flags = []
    source_status = bundle.get('source_status') or bundle.get('market_snapshot', {}).get('source_status') or {}
    full_universe = bundle.get('full_universe_scan') or bundle.get('market_snapshot', {}).get('full_universe_scan') or {}
    has_full_universe_status = bool(full_universe) or bundle.get('market_snapshot', {}).get('universe_quote_count') is not None
    if has_full_universe_status:
        quote_count = full_universe.get('quote_count') if isinstance(full_universe, dict) else None
        if quote_count is None:
            quote_count = bundle.get('market_snapshot', {}).get('universe_quote_count')
        try:
            quote_count_num = int(quote_count)
        except (TypeError, ValueError):
            quote_count_num = 0
        coverage_status = full_universe.get('coverage_status') if isinstance(full_universe, dict) else None
        if (coverage_status and coverage_status != 'PASS') or quote_count_num < 4000:
            flags.append('FULL_UNIVERSE_SCAN_INCOMPLETE')
    cdp_tabs_status = source_status.get('required_cdp_tabs', {}) if isinstance(source_status, dict) else {}
    if cdp_tabs_status:
        missing_tabs = cdp_tabs_status.get('missing_sources') or []
        if cdp_tabs_status.get('status') != 'PASS' or missing_tabs:
            flags.append('EASTMONEY_REQUIRED_CDP_TABS_MISSING_' + ','.join(str(source) for source in missing_tabs))
    else:
        tabs = bundle.get('eastmoney_web_tabs') or bundle.get('market_snapshot', {}).get('eastmoney_web_tabs') or []
        if not tabs:
            flags.append('EASTMONEY_REQUIRED_CDP_TABS_MISSING_legacy_no_visible_tabs')
    missing = []
    for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS:
        status = source_status.get(domain, {}) if isinstance(source_status, dict) else {}
        if status.get('status') not in ('PASS', 'PARTIAL'):
            missing.append(domain)
    if missing:
        flags.append('EASTMONEY_FULL_EVIDENCE_PACK_MISSING_' + ','.join(missing))
    default_enhanced_tabs_missing = missing_coverage_items(source_status, 'enhanced_cdp_tabs', 'missing_sources', REQUIRED_EASTMONEY_DEFAULT_ENHANCED_CDP_TAB_SOURCES)
    if default_enhanced_tabs_missing:
        if is_formal_web_tabs_source:
            flags.append('EASTMONEY_ENHANCED_CDP_TABS_MISSING_' + ','.join(default_enhanced_tabs_missing))
        else:
            flags.append('EASTMONEY_DEFAULT_ENHANCED_CDP_TABS_MISSING_' + ','.join(default_enhanced_tabs_missing))
    enhanced_missing = missing_coverage_items(source_status, 'enhanced_evidence_coverage', 'missing_domains', REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS)
    if enhanced_missing:
        flags.append('EASTMONEY_ENHANCED_EVIDENCE_PACK_MISSING_' + ','.join(enhanced_missing))
    if not is_four_repo_source:
        experimental_missing = missing_coverage_items(
            source_status,
            'experimental_evidence_coverage',
            'missing_domains',
            REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS,
        )
        if experimental_missing:
            flags.append('EASTMONEY_EXPERIMENTAL_EVIDENCE_PACK_MISSING_' + ','.join(experimental_missing))
    return flags


def soft_no_pick_flag(flag: str) -> bool:
    text = str(flag or '')
    return (
        text.startswith('EASTMONEY_')
        or text.startswith('FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW')
        or text.startswith('ZERO_QUOTE_READ')
        or text.startswith('ZERO_FUND_FLOW_READ')
        or text == 'SOURCE_COMPLETENESS_MISSING'
        or text == 'DATA_SOURCE_INCOMPLETE'
        or text == 'candidate_evidence_status!=PASS'
        or text.startswith('buy_confirmation_below_threshold')
        or text == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
        or text == 'OPPORTUNITY_HARD_BLOCK_CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
    )


def candidate_evidence_missing_flags(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> List[str]:
    candidate_source = str(bundle.get('candidate_source', ''))
    if 'eastmoney_web_tabs' not in candidate_source:
        return []
    flags = []
    status = candidate.get('candidate_evidence_status')
    explicit_missing = candidate.get('candidate_evidence_missing_domains') or []
    if explicit_missing:
        flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING_' + ','.join(str(domain) for domain in explicit_missing))
    else:
        counts = candidate.get('candidate_evidence_domain_counts') or {}
        if isinstance(counts, dict):
            # risk_alerts empty = no risk = PASS (not missing)
            OPTIONAL_EVIDENCE_DOMAINS = {'risk_alerts'}
            missing = [domain for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS if domain not in OPTIONAL_EVIDENCE_DOMAINS and not counts.get(domain)]
            if missing:
                flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING_' + ','.join(missing))
        elif status != 'PASS':
            flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING')
    enhanced_counts = candidate.get('enhanced_evidence_domain_counts') or {}
    if isinstance(enhanced_counts, dict):
        missing_recheck = [domain for domain in REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS if not enhanced_counts.get(domain)]
    else:
        explicit_enhanced_missing = candidate.get('enhanced_evidence_missing_domains') or []
        missing_recheck = [domain for domain in REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS if domain in explicit_enhanced_missing]
        if not explicit_enhanced_missing:
            missing_recheck = list(REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS)
    if missing_recheck:
        flags.append('EASTMONEY_CANDIDATE_RECHECK_EVIDENCE_MISSING_' + ','.join(missing_recheck))
    return flags


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
    """判断是否为强势龙头候选（用于 climax 市场高涨幅分层）。

    三层逻辑：
    1. 高涨幅强确认：保留（tag='high_pct_strong_confirmed'）
    2. 高涨幅但分歧：降权（tag='high_pct_divergence', confidence *= 0.75）
    3. 高涨幅假强：过滤（tag='high_pct_without_confirmation'）

    条件：
    1. 属于当日主线板块（sector_opportunity_score >= 0.6 或有 SECTOR_OPPORTUNITY 标签）
    2. 封板质量好（sealed_limit_up 或 close_position_score >= 0.95）
    3. 涨停原因传播强（limitup_reason_propagation_score >= 0.6）
    4. 量能合理（turnover_rate < 20%）
    5. 排名靠前（rank <= 15）
    """
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

    # 计算满足条件数
    conditions_met = sum([
        is_main_sector,
        has_good_close,
        has_strong_reason,
        reasonable_turnover,
        is_top_rank,
    ])

    # 三层判断
    if conditions_met >= 4:
        # 高涨幅强确认：满足4个以上条件
        return True
    elif conditions_met >= 2:
        # 高涨幅但分歧：满足2-3个条件，降权但保留
        return True
    else:
        # 高涨幅假强：只满足0-1个条件，过滤
        return False


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
    sealed_limit_up = bool(get('sealed_limit_up', False))
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
    opportunity_block = opportunity_hard_block_reason(candidate, bundle) or limitup_quality_block_reason(candidate, bundle)
    candidate_stage = str(_cached_structured_signal_profile(candidate, bundle).get('candidate_stage') or candidate.get('candidate_stage') or '')
    is_near_limit = 'near_limit' in candidate_stage
    evidence_missing_flags = web_tabs_evidence_missing_flags(bundle)
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
            and (not is_near_limit or stock_level_limitup_expectation_pass)
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
        if eligibility_blocker in (paper_pick_eligibility.get('blockers') or []):
            flags.append(eligibility_blocker)
    stock_level_limitup_expectation_pass = bool((paper_pick_eligibility.get('signals') or {}).get('stock_level_limitup_expectation_pass'))
    filtered_blocked = [
        r for r in blocked_reasons
        if not (
            str(r) == 'opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
            and paper_pick_eligibility.get('eligible')
            and (not is_near_limit or stock_level_limitup_expectation_pass)
        )
    ]
    if filtered_blocked: flags.append('CANDIDATE_BLOCKED_' + ','.join(str(r) for r in filtered_blocked))
    if risk_f != 0: flags.append('RISK_PENALTY_NOT_ZERO')
    if asof_leakage_flag: flags.append('ASOF_LEAKAGE_FLAG_TRUE')
    if price_f is None or price_f <= 0: flags.append('PRICE_INVALID')
    if lot_f is None:
        flags.append('ONE_LOT_COST_GT_CAP_OR_INVALID')
    elif one_lot_cap is None:
        flags.append('ACCOUNT_AVAILABLE_CASH_MISSING_OR_INVALID')
    else:
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
        hard_blockers = {'DATA_GATE_NOT_PASS', 'XIAOCHAN_BLOCK', 'ASOF_LEAKAGE_FLAG_TRUE', 'STALE_BUNDLE_DATE', 'STALE_SOURCE_MARKET_DATE', 'SOURCE_MARKET_DATE_MISSING', 'STALE_SOURCE_TIME', 'SOURCE_TIME_MISSING', 'candidate_lifecycle_stale_repeat'}
        has_scan_freshness_blocker = any(flag.startswith('SCAN_TOO_OLD_') or flag.startswith('SCAN_AFTER_RUNNER_ASOF_') for flag in flags)
        has_candidate_date_blocker = any(flag.startswith('CANDIDATE_ROW_DATE_MISMATCH_') for flag in flags)
        has_source_health_blocker = any(flag in hard_blockers for flag in source_health_flags)
        if requested_decision_class == 'RESEARCH_CANDIDATE' and not any(flag in hard_blockers for flag in flags) and not has_scan_freshness_blocker and not has_candidate_date_blocker:
            return 'RESEARCH_CANDIDATE', symbol, 'RESEARCH_BASKET_ONLY:' + ';'.join(flags), features, flags
        if 'SEALED_LIMIT_UP_BUYABILITY_FAIL' in flags and xiaochan != 'BLOCK':
            return 'RESEARCH_CANDIDATE', symbol, 'HARD_GATE_NOT_ALL_PASS:' + ';'.join(flags), features, flags
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


def structured_component(row: Dict[str, Any], key: str) -> Optional[float]:
    components = row.get('structured_score_components') or row.get('components') or {}
    if not isinstance(components, dict):
        return None
    return safe_float(components.get(key))


def normalize_tag_list(tags: Any) -> List[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        tags = [tags]
    normalized: List[str] = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        clean = tag.strip()
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)
    return normalized


def normalize_vei_phase_d_tags(tags: Any) -> List[str]:
    return normalize_tag_list(tags)


def inferred_vei_phase_d_tags(details: Dict[str, Any]) -> List[str]:
    if not isinstance(details, dict):
        details = {}
    inferred: List[str] = []
    if safe_float(details.get('pre_limitup_anomaly')) and safe_float(details.get('pre_limitup_anomaly')) > 0:
        inferred.append('PRE_LIMITUP_ANOMALY')
    if safe_float(details.get('weak_to_strong_reversal')) and safe_float(details.get('weak_to_strong_reversal')) > 0:
        inferred.append('WEAK_TO_STRONG_REVERSAL')
    if safe_float(details.get('first_board_pre_signal')) and safe_float(details.get('first_board_pre_signal')) > 0:
        inferred.append('FIRST_BOARD_PRE_SIGNAL')
    if safe_float(details.get('sector_opportunity_score')) and safe_float(details.get('sector_opportunity_score')) > 0:
        inferred.append('SECTOR_OPPORTUNITY')
    return inferred


def signal_stage_bucket(signal_pct: Any) -> str:
    pct = safe_float(signal_pct)
    if pct is None:
        return 'unknown'
    if pct < 0:
        return 'underwater'
    if pct < 3:
        return 'flat_0_to_3'
    if pct < 5:
        return 'early_3_to_5'
    if pct < 7:
        return 'mid_5_to_7'
    if pct < 9:
        return 'high_7_to_9'
    return 'near_limit_9_plus'


def market_adaptive_context(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    market_snapshot = bundle.get('market_snapshot') if isinstance(bundle.get('market_snapshot'), dict) else {}
    external_market = market_snapshot.get('external_market') if isinstance(market_snapshot.get('external_market'), dict) else {}

    def market_float(key: str) -> float | None:
        value = safe_float(row.get(key))
        if value is None:
            value = safe_float(bundle.get(key))
        if value is None:
            value = safe_float(market_snapshot.get(key))
        return value

    market_follow_through_score = market_float('market_follow_through_score')
    market_breadth_up_pct = market_float('market_breadth_up_pct')
    market_limitups = market_float('market_limitups')
    limitup_broken_ratio = market_float('limitup_broken_ratio')
    broken_limitups = market_float('broken_limitups')
    max_consecutive = market_float('max_consecutive')
    sentiment_score = market_float('sentiment_score')
    market_regime = str(row.get('market_regime') or bundle.get('market_regime') or market_snapshot.get('market_regime') or '').lower()
    external_market_status = str(external_market.get('status') or 'MISSING')
    external_market_signal_score = safe_float(external_market.get('external_market_signal_score'))
    external_market_risk_off = bool(
        external_market_status == 'PASS'
        and external_market_signal_score is not None
        and external_market_signal_score <= -1.0
    )
    external_market_supportive = bool(
        external_market_status == 'PASS'
        and external_market_signal_score is not None
        and external_market_signal_score >= 1.0
    )

    broken_limit_pressure = bool(
        (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.95)
        or (
            market_limitups is not None
            and broken_limitups is not None
            and broken_limitups >= max(18.0, market_limitups * 0.75)
        )
    )
    if not market_regime:
        if (
            market_follow_through_score is not None and market_follow_through_score >= 0.62
            and market_breadth_up_pct is not None and market_breadth_up_pct >= 58
            and limitup_broken_ratio is not None and limitup_broken_ratio >= 1.2
        ):
            market_regime = 'strong'
        elif (
            (market_follow_through_score is not None and market_follow_through_score <= 0.38)
            or (market_breadth_up_pct is not None and market_breadth_up_pct <= 45)
            or (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.85)
            or broken_limit_pressure
        ):
            market_regime = 'weak'
        else:
            market_regime = 'neutral'
    if external_market_risk_off and market_regime != 'strong':
        market_regime = 'weak'

    supportive_market = bool(
        market_regime == 'strong'
        or (market_follow_through_score is not None and market_follow_through_score >= 0.62)
        or (
            market_breadth_up_pct is not None
            and market_breadth_up_pct >= 58
            and limitup_broken_ratio is not None
            and limitup_broken_ratio >= 1.2
        )
    )
    supportive_market = supportive_market or (
        external_market_supportive and market_regime != 'weak'
    )
    weak_acceptance_market = bool(
        market_regime == 'weak'
        or (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.85)
        or (
            market_limitups is not None
            and broken_limitups is not None
            and broken_limitups >= max(20.0, market_limitups * 0.9)
        )
    )
    weak_acceptance_market = weak_acceptance_market or external_market_risk_off
    overheated_market = bool(
        (market_breadth_up_pct is not None and market_breadth_up_pct >= 80)
        or (market_limitups is not None and market_limitups >= 150)
        or (
            sentiment_score is not None and sentiment_score >= 0.75
            and max_consecutive is not None and max_consecutive >= 5
        )
    )
    return {
        'market_regime': market_regime,
        'market_follow_through_score': market_follow_through_score,
        'market_breadth_up_pct': market_breadth_up_pct,
        'market_limitups': market_limitups,
        'limitup_broken_ratio': limitup_broken_ratio,
        'broken_limitups': broken_limitups,
        'max_consecutive': max_consecutive,
        'sentiment_score': sentiment_score,
        'external_market_status': external_market_status,
        'external_market_signal_score': external_market_signal_score,
        'external_market_risk_off': external_market_risk_off,
        'external_market_supportive': external_market_supportive,
        'supportive_market': supportive_market,
        'weak_acceptance_market': weak_acceptance_market,
        'broken_limit_pressure': broken_limit_pressure,
        'overheated_market': overheated_market,
    }


def market_adaptive_thresholds(candidate_stage: str, market_context: Dict[str, Any]) -> Dict[str, float]:
    weak_acceptance_market = bool(market_context.get('weak_acceptance_market'))
    supportive_market = bool(market_context.get('supportive_market')) and not weak_acceptance_market
    overheated_market = bool(market_context.get('overheated_market'))
    if candidate_stage == 'near_limit_9_plus':
        return {
            'component_min': 0.74 if weak_acceptance_market else (0.70 if overheated_market else (0.64 if supportive_market else 0.66)),
            'buy_confirmation_min': 0.68 if (weak_acceptance_market or overheated_market) else (0.58 if supportive_market else 0.60),
            'order_book_min': 0.56 if (weak_acceptance_market or overheated_market) else (0.48 if supportive_market else 0.50),
            'dynamic_required_confirmations': 4 if supportive_market else 5,
            'dynamic_close_position_min': 0.84,
            'dynamic_fund_flow_min': 0.60 if (weak_acceptance_market or overheated_market) else (0.50 if supportive_market else 0.55),
            'dynamic_time_series_min': 0.20 if (weak_acceptance_market or overheated_market) else 0.10,
        }
    if candidate_stage == 'high_7_to_9':
        return {
            'component_min': 0.70 if weak_acceptance_market else (0.66 if overheated_market else (0.55 if supportive_market else 0.60)),
            'buy_confirmation_min': 0.68 if weak_acceptance_market else (0.62 if overheated_market else (0.55 if supportive_market else 0.60)),
            'order_book_min': 0.55 if weak_acceptance_market else (0.52 if overheated_market else (0.48 if supportive_market else 0.50)),
            'dynamic_required_confirmations': 3 if supportive_market else 4,
            'dynamic_close_position_min': 0.76 if supportive_market else 0.78,
            'dynamic_fund_flow_min': 0.55 if (weak_acceptance_market or overheated_market) else 0.45,
            'dynamic_time_series_min': 0.20 if weak_acceptance_market else 0.10,
        }
    return {
        'component_min': 0.60 if weak_acceptance_market else (0.58 if overheated_market else (0.52 if supportive_market else 0.55)),
        'buy_confirmation_min': 0.62 if weak_acceptance_market else (0.60 if overheated_market else (0.55 if supportive_market else 0.60)),
        'order_book_min': 0.52 if weak_acceptance_market else (0.50 if overheated_market else (0.46 if supportive_market else 0.48)),
        'dynamic_required_confirmations': 3 if supportive_market else 4,
        'dynamic_close_position_min': 0.65,
        'dynamic_fund_flow_min': 0.50 if weak_acceptance_market else (0.45 if supportive_market else 0.48),
        'dynamic_time_series_min': 0.15 if weak_acceptance_market else 0.10,
    }


def sector_gate_threshold_for_market(market_context: Dict[str, Any]) -> float:
    market_regime = str(market_context.get('market_regime') or '')
    if market_regime == 'weak':
        return 0.2
    if market_regime == 'neutral':
        return 0.4
    return 0.5


def normalize_bundle_vei_tags(bundle: Dict[str, Any]) -> Dict[str, Any]:
    def normalize_candidate(candidate: Any) -> Any:
        if isinstance(candidate, dict):
            candidate['vei_phase_d_tags'] = normalize_vei_phase_d_tags(candidate.get('vei_phase_d_tags'))
        return candidate

    if not isinstance(bundle, dict):
        return bundle

    normalize_candidate(bundle.get('candidate'))
    normalize_candidate(bundle.get('candidate_features'))
    for key in ('paper_scoring_candidates', 'structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            bundle[key] = [normalize_candidate(item) for item in items]
    impact = bundle.get('structured_formal_impact')
    if isinstance(impact, dict):
        for key in ('top_structured_only_candidates', 'sector_opportunity_candidates', 'structured_observation_candidates'):
            items = impact.get(key)
            if isinstance(items, list):
                impact[key] = [normalize_candidate(item) for item in items]
    return bundle


def structured_signal_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    details = row.get('structured_component_details') or row.get('component_details') or {}
    if not isinstance(details, dict):
        details = {}
    components = row.get('structured_score_components') or row.get('components') or {}
    if not isinstance(components, dict):
        components = {}
    research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    research_catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    regulatory_hard_block = str(row.get('regulatory_hard_block') or '')
    if not regulatory_hard_block:
        research_catalyst_category = str(research_catalyst_quality.get('category') or '')
        if research_catalyst_category in ('regulatory_notice', 'risk_notice'):
            regulatory_hard_block = research_catalyst_category
        elif bool((research_signals.get('a_share_risk_review') or {}).get('disqualified_for_paper_pick')):
            regulatory_hard_block = 'a_share_risk_review_disqualified'
    tags = normalize_tag_list(details.get('sector_opportunity_tags') or row.get('sector_opportunity_tags') or [])
    vei_tags = normalize_vei_phase_d_tags((row.get('vei_phase_d_tags') or []) + inferred_vei_phase_d_tags(details))
    sector_opportunity_score = safe_float(details.get('sector_opportunity_score'))
    if sector_opportunity_score is None:
        sector_opportunity_score = safe_float(row.get('sector_opportunity_score'))
    search_layer_hint = str(
        row.get('search_layer_hint')
        or details.get('search_layer_hint')
        or ''
    )
    early_opportunity_score = row.get('early_opportunity_score')
    if early_opportunity_score is None:
        early_opportunity_score = details.get('early_opportunity_score')
    news_catalyst_strength = safe_float(row.get('news_catalyst_strength'))
    if news_catalyst_strength is None:
        news_catalyst_strength = safe_float(details.get('news_catalyst_strength'))
    sector_news_strength = safe_float(row.get('sector_news_strength'))
    if sector_news_strength is None:
        sector_news_strength = safe_float(details.get('sector_news_strength'))
    sector_catalyst_score = safe_float(row.get('sector_catalyst_score'))
    if sector_catalyst_score is None:
        sector_catalyst_score = safe_float(details.get('sector_catalyst_score'))
    news_catalyst_quality_categories = normalize_tag_list(row.get('news_catalyst_quality_categories') or details.get('news_catalyst_quality_categories') or [])
    topic_propagation_score = safe_float(row.get('topic_propagation_score'))
    if topic_propagation_score is None:
        topic_propagation_score = safe_float(details.get('topic_propagation_score'))
    intraday_alert_strength = safe_float(row.get('intraday_alert_strength'))
    if intraday_alert_strength is None:
        intraday_alert_strength = safe_float(details.get('intraday_alert_strength'))
    limitup_reason_propagation_score = safe_float(row.get('limitup_reason_propagation_score'))
    if limitup_reason_propagation_score is None:
        limitup_reason_propagation_score = safe_float(details.get('limitup_reason_propagation_score'))
    limitup_capture_score = safe_float(row.get('limitup_capture_score'))
    if limitup_capture_score is None:
        limitup_capture_score = safe_float(details.get('limitup_capture_score'))
    limitup_capture_profile = str(row.get('limitup_capture_profile') or details.get('limitup_capture_profile') or '')
    limitup_capture_confirmed = bool(row.get('limitup_capture_confirmed') or details.get('limitup_capture_confirmed'))
    limitup_capture_reasons = normalize_tag_list(row.get('limitup_capture_reasons') or details.get('limitup_capture_reasons') or [])
    low_position_catalyst_score = safe_float(row.get('low_position_catalyst_score'))
    if low_position_catalyst_score is None:
        low_position_catalyst_score = safe_float(details.get('low_position_catalyst_score'))
    main_theme_alignment_score = safe_float(row.get('main_theme_alignment_score'))
    if main_theme_alignment_score is None:
        main_theme_alignment_score = safe_float(details.get('main_theme_alignment_score'))
    main_theme_core_score = safe_float(row.get('main_theme_core_score'))
    if main_theme_core_score is None:
        main_theme_core_score = safe_float(details.get('main_theme_core_score'))
    announcement_catalyst_score = safe_float(row.get('announcement_catalyst_score'))
    if announcement_catalyst_score is None:
        announcement_catalyst_score = safe_float(details.get('announcement_catalyst_score'))
    sector_news_catalyst_score = safe_float(row.get('sector_news_catalyst_score'))
    if sector_news_catalyst_score is None:
        sector_news_catalyst_score = safe_float(details.get('sector_news_catalyst_score'))
    limitup_reason_quality_score = safe_float(row.get('limitup_reason_quality_score'))
    if limitup_reason_quality_score is None:
        limitup_reason_quality_score = safe_float(details.get('limitup_reason_quality_score'))
    risk_notice_penalty = safe_float(row.get('risk_notice_penalty'))
    if risk_notice_penalty is None:
        risk_notice_penalty = safe_float(details.get('risk_notice_penalty'))
    mainboard_auxiliary_confidence = safe_float(row.get('mainboard_auxiliary_confidence'))
    if mainboard_auxiliary_confidence is None:
        mainboard_auxiliary_confidence = safe_float(details.get('mainboard_auxiliary_confidence'))
    profile = {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'structured_score': safe_float(row.get('structured_score')),
        'final_shadow_score': safe_float(row.get('final_shadow_score')),
        'base_score': safe_float(row.get('score')),
        'sector_opportunity_score': sector_opportunity_score,
        'sector_opportunity_tags': tags,
        'vei_phase_d_tags': vei_tags,
        'candidate_stage': str(row.get('candidate_stage') or details.get('candidate_stage') or ''),
        'early_opportunity_score': safe_float(early_opportunity_score),
        'setup_type': str(row.get('setup_type') or row.get('setup_type_refined') or ''),
        'search_layer_hint': search_layer_hint,
        'structured_component_details': details,
        'structured_score_components': components,
        'limitup_reason_strength': safe_float(components.get('limitup_reason_strength')),
        'seal_order_strength': safe_float(components.get('seal_order_strength')),
        'order_book_pressure': safe_float(components.get('order_book_pressure')),
        'fund_flow_momentum': safe_float(components.get('fund_flow_momentum')),
        'time_series_momentum': safe_float(components.get('time_series_momentum')),
        'news_catalyst_strength': news_catalyst_strength,
        'sector_news_strength': sector_news_strength,
        'sector_catalyst_score': sector_catalyst_score,
        'news_catalyst_quality_categories': news_catalyst_quality_categories,
        'topic_propagation_score': topic_propagation_score,
        'intraday_alert_strength': intraday_alert_strength,
        'limitup_reason_propagation_score': limitup_reason_propagation_score,
        'limitup_capture_score': limitup_capture_score,
        'limitup_capture_profile': limitup_capture_profile,
        'limitup_capture_confirmed': limitup_capture_confirmed,
        'limitup_capture_reasons': limitup_capture_reasons,
        'low_position_catalyst_score': low_position_catalyst_score,
        'main_theme_alignment_score': main_theme_alignment_score,
        'main_theme_core_score': main_theme_core_score,
        'mainboard_auxiliary_evidence_status': str(row.get('mainboard_auxiliary_evidence_status') or details.get('mainboard_auxiliary_evidence_status') or ''),
        'mainboard_auxiliary_missing_domains': normalize_tag_list(row.get('mainboard_auxiliary_missing_domains') or details.get('mainboard_auxiliary_missing_domains') or []),
        'announcement_catalyst_score': announcement_catalyst_score,
        'sector_news_catalyst_score': sector_news_catalyst_score,
        'limitup_reason_quality_score': limitup_reason_quality_score,
        'risk_notice_penalty': risk_notice_penalty,
        'mainboard_auxiliary_confidence': mainboard_auxiliary_confidence,
        'announcement_evidence': row.get('announcement_evidence') or [],
        'news_evidence': row.get('news_evidence') or {},
        'sector_news_evidence': row.get('sector_news_evidence') or [],
        'limitup_reason_evidence': row.get('limitup_reason_evidence') or [],
        'risk_notice_evidence': row.get('risk_notice_evidence') or [],
        'close_position_score': safe_float(row.get('close_position_score')),
        'hsgt_institutional_flow': safe_float(row.get('hsgt_institutional_flow')) or safe_float((row.get('data_directory_capital_flow') or {}).get('hsgt_institutional_flow')),
        'volume_ratio': safe_float(row.get('volume_ratio')),
        'signal_pct': safe_float(row.get('signal_pct')),
        'full_universe_fund_pctile': safe_float(row.get('full_universe_fund_pctile')),
        'full_universe_amount_pctile': safe_float(row.get('full_universe_amount_pctile')),
        'risk_penalty': safe_float(row.get('risk_penalty')),
        'data_gate_status': str(row.get('data_gate_status') or row.get('data_gate') or ''),
        'candidate_evidence_status': str(row.get('candidate_evidence_status') or ''),
        'source_time': str(row.get('source_time') or ''),
        'runner_asof_time': str(row.get('runner_asof_time') or row.get('_runner_asof_time') or ''),
        'one_lot_cost': safe_float(row.get('one_lot_cost')),
        'regulatory_hard_block': regulatory_hard_block,
        'opportunity_hard_block': str(row.get('opportunity_hard_block') or ''),
        'blocked_reasons': [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)],
    }
    profile['research_signals'] = build_research_signals_from_profile(profile, row, bundle)
    profile['research_panel_overall'] = str((profile['research_signals'].get('research_panel') or {}).get('overall') or '')
    profile['catalyst_quality_category'] = str((profile['research_signals'].get('catalyst_quality') or {}).get('category') or '')
    profile['a_share_risk_review_disqualified_for_paper_pick'] = bool((profile['research_signals'].get('a_share_risk_review') or {}).get('disqualified_for_paper_pick'))
    profile['historical_pattern_name'] = str((profile['research_signals'].get('historical_pattern') or {}).get('pattern_name') or '')
    return profile


def build_research_signals_from_profile(profile: Dict[str, Any], row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    row = row if isinstance(row, dict) else {}
    bundle = bundle if isinstance(bundle, dict) else {}
    raw = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    source_time = str(profile.get('source_time') or bundle.get('source_time') or row.get('source_time') or '')
    sector_tags = normalize_tag_list((raw.get('sector_mapping') or {}).get('sectors') or profile.get('sector_opportunity_tags') or row.get('sector_opportunity_tags') or [])
    industry_chain_tags = normalize_tag_list(raw.get('industry_chain_tags') or sector_tags or profile.get('sector_opportunity_tags') or [])

    quality = dict(raw.get('catalyst_quality') or {})
    if not quality:
        category = str(profile.get('catalyst_quality_category') or '')
        if not category:
            news_categories = normalize_tag_list(profile.get('news_catalyst_quality_categories') or [])
            if 'regulatory_notice' in news_categories:
                category = 'regulatory_notice'
            elif 'risk_notice' in news_categories:
                category = 'risk_notice'
            elif 'positive_catalyst' in news_categories:
                category = 'positive_catalyst'
            elif 'sector_catalyst' in news_categories:
                category = 'sector_catalyst'
            elif profile.get('news_catalyst_strength') or profile.get('sector_news_strength') or profile.get('sector_catalyst_score'):
                category = 'sector_catalyst' if (profile.get('sector_news_strength') or profile.get('sector_catalyst_score') or sector_tags) else 'positive_catalyst'
            else:
                category = 'neutral'
        quality = {
            'category': category,
            'confidence': round(min(1.0, max(
                safe_float(profile.get('news_catalyst_strength')) or 0.0,
                safe_float(profile.get('sector_news_strength')) or 0.0,
                safe_float(profile.get('sector_catalyst_score')) or 0.0,
            )), 4),
            'freshness_minutes': None,
            'evidence_refs': [],
            'usable_for_candidate_generation': category in ('positive_catalyst', 'sector_catalyst'),
            'usable_for_paper_pick': category in ('positive_catalyst', 'sector_catalyst'),
            'risk_terms': [],
            'positive_terms': [],
            'sector_terms': sector_tags,
            'regulatory_hard_block': category in ('regulatory_notice', 'risk_notice'),
            'observation': 'risk_observation' if category in ('regulatory_notice', 'risk_notice') else ('catalyst_observation' if category in ('positive_catalyst', 'sector_catalyst') else 'neutral_observation'),
        }
    else:
        quality.setdefault('category', str(profile.get('catalyst_quality_category') or 'neutral'))
        quality.setdefault('confidence', round(min(1.0, max(
            safe_float(profile.get('news_catalyst_strength')) or 0.0,
            safe_float(profile.get('sector_news_strength')) or 0.0,
            safe_float(profile.get('sector_catalyst_score')) or 0.0,
        )), 4))
        quality.setdefault('freshness_minutes', None)
        quality.setdefault('evidence_refs', [])
        quality.setdefault('usable_for_candidate_generation', quality.get('category') in ('positive_catalyst', 'sector_catalyst'))
        quality.setdefault('usable_for_paper_pick', quality.get('usable_for_candidate_generation'))
        quality.setdefault('risk_terms', [])
        quality.setdefault('positive_terms', [])
        quality.setdefault('sector_terms', sector_tags)
        quality.setdefault('regulatory_hard_block', quality.get('category') in ('regulatory_notice', 'risk_notice'))
        quality.setdefault('observation', 'risk_observation' if quality.get('category') in ('regulatory_notice', 'risk_notice') else ('catalyst_observation' if quality.get('category') in ('positive_catalyst', 'sector_catalyst') else 'neutral_observation'))
        quality['sector_terms'] = normalize_tag_list(quality.get('sector_terms') or sector_tags)
        if quality.get('category') == 'neutral' and profile.get('catalyst_quality_category') and profile.get('catalyst_quality_category') != 'neutral':
            quality['category'] = profile.get('catalyst_quality_category')

    sector_mapping = dict(raw.get('sector_mapping') or {})
    if not sector_mapping:
        sector_mapping = {
            'sectors': sector_tags,
            'related_symbols': [],
            'mapping_confidence': round(min(1.0, max(
                safe_float(profile.get('sector_opportunity_score')) or 0.0,
                safe_float(profile.get('sector_catalyst_score')) or 0.0,
                safe_float(profile.get('news_catalyst_strength')) or 0.0,
            )), 4),
        }
    else:
        sector_mapping['sectors'] = normalize_tag_list(sector_mapping.get('sectors') or sector_tags)
        sector_mapping['related_symbols'] = normalize_tag_list(sector_mapping.get('related_symbols') or [])
        sector_mapping['mapping_confidence'] = round(min(1.0, safe_float(sector_mapping.get('mapping_confidence')) or max(
            safe_float(profile.get('sector_opportunity_score')) or 0.0,
            safe_float(profile.get('sector_catalyst_score')) or 0.0,
            safe_float(profile.get('news_catalyst_strength')) or 0.0,
        )), 4)

    risk_review = dict(raw.get('a_share_risk_review') or {})
    if not risk_review:
        disqualified = bool(profile.get('a_share_risk_review_disqualified_for_paper_pick') or profile.get('regulatory_hard_block'))
        risk_review = {
            'abnormal_movement_notice': disqualified,
            'risk_warning_notice': disqualified,
            'reduction_risk': False,
            'financial_red_flags': [],
            'lhb_risk_flags': [],
            'disqualified_for_paper_pick': disqualified,
        }
    else:
        risk_review['abnormal_movement_notice'] = bool(risk_review.get('abnormal_movement_notice'))
        risk_review['risk_warning_notice'] = bool(risk_review.get('risk_warning_notice'))
        risk_review['reduction_risk'] = bool(risk_review.get('reduction_risk'))
        risk_review['financial_red_flags'] = normalize_tag_list(risk_review.get('financial_red_flags') or [])
        risk_review['lhb_risk_flags'] = normalize_tag_list(risk_review.get('lhb_risk_flags') or [])
        risk_review['disqualified_for_paper_pick'] = bool(risk_review.get('disqualified_for_paper_pick') or risk_review['abnormal_movement_notice'] or risk_review['risk_warning_notice'] or risk_review['reduction_risk'] or risk_review['financial_red_flags'] or risk_review['lhb_risk_flags'])

    adversarial_review = dict(raw.get('adversarial_review') or {})
    if not adversarial_review:
        bear_case_flags = []
        disqualifying_flags = []
        if quality.get('category') == 'stale':
            bear_case_flags.append('stale_news')
        if (profile.get('sector_opportunity_score') or 0.0) > 0 and (profile.get('news_catalyst_strength') or 0.0) <= 0.1 and (profile.get('sector_news_strength') or 0.0) <= 0.1:
            bear_case_flags.append('concept_hype_without_company_link')
        if (profile.get('volume_ratio') or 0.0) < 1.2 and (profile.get('close_position_score') or 0.0) < 0.55:
            bear_case_flags.append('weak_fund_confirmation')
        if (profile.get('signal_pct') or 0.0) >= 7.0 or (profile.get('close_position_score') or 0.0) >= 0.9:
            bear_case_flags.append('near_limit_chase')
        if quality.get('category') in ('risk_notice', 'regulatory_notice'):
            disqualifying_flags.extend(['risk_notice_as_catalyst', 'regulatory_hard_block'])
        if risk_review.get('financial_red_flags'):
            disqualifying_flags.append('financial_red_flag')
        if not sector_mapping.get('sectors') and not quality.get('evidence_refs'):
            disqualifying_flags.append('evidence_missing')
        adversarial_review = {
            'bear_case_flags': bear_case_flags,
            'disqualifying_flags': list(dict.fromkeys(disqualifying_flags)),
        }
    else:
        adversarial_review['bear_case_flags'] = normalize_tag_list(adversarial_review.get('bear_case_flags') or [])
        adversarial_review['disqualifying_flags'] = normalize_tag_list(adversarial_review.get('disqualifying_flags') or [])

    historical_pattern = dict(raw.get('historical_pattern') or {})
    if not historical_pattern:
        pattern_name = 'formal_high_score'
        setup_type = str(profile.get('setup_type') or row.get('setup_type') or row.get('setup_type_refined') or '')
        search_layer_hint = str(profile.get('search_layer_hint') or row.get('search_layer_hint') or '')
        candidate_stage = str(profile.get('candidate_stage') or '')
        if search_layer_hint == 'news_catalyst_low_position' or setup_type in ('NEWS_CATALYST_LOW_POSITION', 'TOPIC_FUND_IGNITION'):
            pattern_name = 'news_catalyst_low_position'
        elif search_layer_hint == 'sector_catalyst_low_position' or setup_type == 'SECTOR_NEWS_LOW_POSITION':
            pattern_name = 'sector_catalyst_low_position'
        elif search_layer_hint == 'intraday_alert_reversal' or setup_type == 'INTRADAY_ALERT_REVERSAL':
            pattern_name = 'intraday_alert_reversal'
        elif candidate_stage == 'underwater' or 'UNDERWATER' in setup_type:
            pattern_name = 'underwater_reversal'
        elif (profile.get('low_position_catalyst_score') or 0.0) >= 0.6:
            pattern_name = 'topic_fund_ignition'
        elif profile.get('base_score') is not None and (profile.get('signal_pct') or 0.0) >= 7.0:
            pattern_name = 'formal_high_score'
        sector_label = ''
        for value in sector_mapping.get('sectors') or []:
            if value:
                sector_label = str(value)
                break
        if not sector_label:
            for value in industry_chain_tags:
                if value in SECTOR_RESEARCH_MAP:
                    sector_label = str(value)
                    break
        if not sector_label:
            sector_label = str(row.get('code') or row.get('symbol') or 'generic')
        historical_pattern = {
            'pattern_name': pattern_name,
            'backtest_score': None,
            'forward_evidence_count': 0,
            'requires_forward_tracking': True,
            'forward_eval_key': f'{pattern_name}:{sector_label}',
        }
    else:
        historical_pattern.setdefault('pattern_name', 'formal_high_score')
        historical_pattern.setdefault('backtest_score', None)
        historical_pattern.setdefault('forward_evidence_count', 0)
        historical_pattern.setdefault('requires_forward_tracking', True)
        historical_pattern.setdefault('forward_eval_key', f"{historical_pattern.get('pattern_name') or 'formal_high_score'}:{(sector_mapping.get('sectors') or [str(row.get('code') or row.get('symbol') or 'generic')])[0]}")

    research_signals = {
        'industry_chain_tags': industry_chain_tags,
        'catalyst_quality': quality,
        'sector_mapping': sector_mapping,
        'a_share_risk_review': risk_review,
        'adversarial_review': adversarial_review,
        'historical_pattern': historical_pattern,
    }
    research_panel = dict(raw.get('research_panel') or {})
    if not research_panel or 'overall' not in research_panel:
        research_panel = build_research_panel(research_signals, row)
    else:
        research_panel['news_analyst'] = research_panel.get('news_analyst') or 'PARTIAL'
        research_panel['sector_analyst'] = research_panel.get('sector_analyst') or 'PARTIAL'
        research_panel['technical_analyst'] = research_panel.get('technical_analyst') or 'PARTIAL'
        research_panel['risk_analyst'] = research_panel.get('risk_analyst') or 'PASS'
        research_panel['bear_case'] = research_panel.get('bear_case') or 'PASS'
        research_panel['overall'] = research_panel.get('overall') or 'PARTIAL'
    research_signals['research_panel'] = research_panel
    candidate_code = str(row.get('code') or row.get('symbol') or '').strip()
    if candidate_code and bundle:
        content_by_code = bundle.get('data_directory_content_by_code') or {}
        matched_content = content_by_code.get(candidate_code) or []
        if matched_content:
            research_signals['data_directory_content_evidence'] = {
                'record_count': len(matched_content),
                'item_keys': sorted(set(str(r.get('item_key') or '') for r in matched_content if r.get('item_key'))),
                'section_titles': sorted(set(str(r.get('section_title') or '') for r in matched_content if r.get('section_title'))),
                'records': matched_content[:20],
            }
            for rec in matched_content:
                item_key = str(rec.get('item_key') or '')
                if 'research_reports' in item_key or 'report' in item_key:
                    quality.setdefault('positive_terms', []).append(str(rec.get('title') or '')[:80])
                if 'financial' in item_key or 'earnings' in item_key:
                    quality.setdefault('positive_terms', []).append(str(rec.get('title') or '')[:80])
                if 'halt' in item_key or 'trading_halts' in item_key:
                    quality['regulatory_hard_block'] = True
                    quality.setdefault('risk_terms', []).append(str(rec.get('title') or '')[:80])
    fund_flow = (bundle or {}).get('data_directory_capital_flow_by_code', {}).get(candidate_code, {})
    if fund_flow:
        research_signals['data_directory_capital_flow'] = fund_flow
        net_inflow = safe_float(fund_flow.get('main_force_net_inflow')) or 0.0
        if net_inflow > 0:
            quality.setdefault('positive_terms', []).append(f'主力净流入{net_inflow/100000000:.2f}亿')
            existing_inflow = safe_float(row.get('net_inflow_main')) or 0.0
            if existing_inflow <= 0:
                row['net_inflow_main'] = net_inflow
                row['_net_inflow_main_from_data_directory'] = True
    return research_signals


def build_research_panel(research_signals: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    research_signals = research_signals if isinstance(research_signals, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    quality = research_signals.get('catalyst_quality') or {}
    sector_mapping = research_signals.get('sector_mapping') or {}
    risk_review = research_signals.get('a_share_risk_review') or {}
    adversarial_review = research_signals.get('adversarial_review') or {}

    if quality.get('category') in ('positive_catalyst', 'sector_catalyst'):
        news_analyst = 'PASS'
    elif quality.get('category') == 'neutral' and (
        quality.get('evidence_refs') or quality.get('industry_chain_tags') or quality.get('positive_terms')
    ):
        news_analyst = 'PARTIAL'
    else:
        news_analyst = 'FAIL'

    mapping_confidence = safe_float(sector_mapping.get('mapping_confidence'))
    if mapping_confidence is None:
        mapping_confidence = max(
            safe_float(candidate.get('sector_opportunity_score')) or 0.0,
            safe_float(candidate.get('sector_catalyst_score')) or 0.0,
            safe_float(candidate.get('news_catalyst_strength')) or 0.0,
        )
    if mapping_confidence >= 0.5:
        sector_analyst = 'PASS'
    elif sector_mapping.get('sectors') or sector_mapping.get('related_symbols'):
        sector_analyst = 'PARTIAL'
    else:
        sector_analyst = 'FAIL'

    low_position_catalyst_score = safe_float(candidate.get('low_position_catalyst_score')) or 0.0
    early_opportunity_score = safe_float(candidate.get('early_opportunity_score')) or 0.0
    if low_position_catalyst_score >= 0.6 or early_opportunity_score >= 0.65:
        technical_analyst = 'PASS'
    elif (
        (safe_float(candidate.get('volume_ratio')) or 0.0) >= 1.2
        or (safe_float(candidate.get('close_position_score')) or 0.0) >= 0.55
        or (safe_float(candidate.get('full_universe_fund_pctile')) or 0.0) >= 0.4
        or (safe_float(candidate.get('time_series_momentum')) or 0.0) >= 0.15
        or (safe_float(candidate.get('fund_flow_momentum')) or 0.0) >= 0.25
    ):
        technical_analyst = 'PARTIAL'
    else:
        technical_analyst = 'FAIL'

    risk_analyst = 'FAIL' if risk_review.get('disqualified_for_paper_pick') else 'PASS'
    if adversarial_review.get('disqualifying_flags'):
        bear_case = 'FAIL'
    elif adversarial_review.get('bear_case_flags'):
        bear_case = 'PARTIAL'
    else:
        bear_case = 'PASS'

    statuses = [news_analyst, sector_analyst, technical_analyst, risk_analyst, bear_case]
    if 'FAIL' in (risk_analyst, bear_case):
        overall = 'FAIL'
    elif statuses.count('PASS') >= 3:
        overall = 'PASS'
    else:
        overall = 'PARTIAL'

    return {
        'news_analyst': news_analyst,
        'sector_analyst': sector_analyst,
        'technical_analyst': technical_analyst,
        'risk_analyst': risk_analyst,
        'bear_case': bear_case,
        'overall': overall,
    }


def early_opportunity_score_for_row(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> float:
    profile = _cached_structured_signal_profile(row, bundle) if isinstance(bundle, dict) else structured_signal_profile(row)
    existing = profile['early_opportunity_score']
    if existing is not None:
        return max(0.0, min(1.0, existing))
    signal_pct = profile['signal_pct']
    candidate_stage = profile['candidate_stage'] or signal_stage_bucket(signal_pct)
    score = {
        'underwater': 0.26,
        'flat_0_to_3': 0.24,
        'early_3_to_5': 0.20,
        'mid_5_to_7': 0.10,
        'high_7_to_9': -0.08,
        'near_limit_9_plus': -0.18,
    }.get(candidate_stage, 0.0)
    score += min(0.18, (profile['sector_opportunity_score'] or 0.0) * 0.18)
    score += min(0.14, (profile['fund_flow_momentum'] or 0.0) * 0.14)
    score += min(0.12, (profile['time_series_momentum'] or 0.0) * 0.12)
    score += min(0.14, (profile['structured_component_details'].get('weak_to_strong_reversal') or 0.0) * 0.14)
    score += min(0.12, (profile['structured_component_details'].get('pre_limitup_anomaly') or 0.0) * 0.12)
    score += min(0.18, (profile['low_position_catalyst_score'] or 0.0) * 0.18)
    score += min(0.08, max(0.0, profile['close_position_score'] or 0.0) * 0.08)
    score += min(0.06, max(0.0, profile['hsgt_institutional_flow'] or 0.0) * 0.06)
    score += min(0.08, (profile['full_universe_fund_pctile'] or 0.0) * 0.08)
    score += min(0.10, min(1.0, max(0.0, profile['volume_ratio'] or 0.0) / 3.0) * 0.10)
    if signal_pct is not None and signal_pct >= 8.0:
        score -= min(0.22, ((signal_pct - 8.0) / 2.0) * 0.16 + 0.05)
    if signal_pct is not None and signal_pct <= 0.0:
        score += 0.04
    return max(0.0, min(1.0, score))


def structured_signal_present(row: Dict[str, Any]) -> bool:
    profile = structured_signal_profile(row)
    return (
        profile['structured_score'] is not None
        or profile['final_shadow_score'] is not None
        or profile['sector_opportunity_score'] is not None
        or bool(profile['vei_phase_d_tags'])
        or bool(profile['structured_component_details'])
    )


def normalized_block_bucket(reason: str) -> str:
    text = str(reason or '').lower()
    if 'near_limit_up_risk' in text or 'near_limit_up' in text:
        return 'near_limit_up_risk'
    if 'main_board_breadth_too_low' in text:
        return 'main_board_breadth_too_low'
    if 'opp_too_low' in text:
        return 'opp_too_low'
    if 'risk_too_high' in text:
        return 'risk_too_high'
    return ''


def block_reason_bucket(reason: str) -> str:
    normalized = normalized_block_bucket(reason)
    if normalized:
        return normalized
    text = str(reason or '')
    if text.startswith('main_board_breadth_too_low:'):
        return 'main_board_breadth_too_low'
    if ':' in text:
        text = text.split(':', 1)[1]
    return text.strip() or 'unknown'


def formal_blockers_for_row(row: Dict[str, Any]) -> List[str]:
    reasons = [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)]
    regulatory_block = regulatory_hard_block_reason(row, {})
    opportunity_block = opportunity_hard_block_reason(row, {}) or limitup_quality_block_reason(row, {})
    if regulatory_block:
        reasons.append('regulatory_hard_block:' + regulatory_block)
    if opportunity_block:
        reasons.append('opportunity_hard_block:' + opportunity_block)
    unique: List[str] = []
    seen = set()
    for reason in reasons:
        if reason not in seen:
            unique.append(reason)
            seen.add(reason)
    return unique


def why_not_formal_candidate(row: Dict[str, Any]) -> str:
    reasons = formal_blockers_for_row(row)
    parts: List[str] = []
    if safe_float(row.get('score')) is None:
        parts.append('score=null')
    if reasons:
        parts.append('blocked=' + ','.join(reasons))
    elif safe_float(row.get('score')) is not None:
        parts.append('not_selected_in_formal_basket')
    return ';'.join(parts) if parts else 'not_selected_in_formal_basket'


def structured_observation_candidate(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = structured_signal_profile(row)
    eligibility = paper_pick_eligibility_profile(row, bundle)
    formal_blockers = eligibility['blockers']
    observation_class = 'STRUCTURED_SHADOW_SIGNAL'
    if profile['sector_opportunity_score'] and profile['sector_opportunity_score'] > 0:
        observation_class = 'STRUCTURED_SECTOR_OPPORTUNITY'
    elif profile['vei_phase_d_tags']:
        observation_class = 'STRUCTURED_VEI_SIGNAL'
    return {
        'symbol': symbol_for(row),
        'name': row.get('name'),
        'observation_class': observation_class,
        'price': row.get('price'),
        'one_lot_cost': row.get('one_lot_cost') if row.get('one_lot_cost') is not None else (
            safe_float(row.get('price')) * 100 if safe_float(row.get('price')) is not None else None
        ),
        'source_time': profile['source_time'] or str((bundle or {}).get('source_time') or ''),
        'runner_asof_time': profile['runner_asof_time'] or str((bundle or {}).get('_runner_asof_time') or (bundle or {}).get('runner_asof_time') or (bundle or {}).get('asof_time') or ''),
        'data_gate_status': profile['data_gate_status'] or str((bundle or {}).get('data_gate_status') or ''),
        'candidate_evidence_status': profile['candidate_evidence_status'] or str((bundle or {}).get('candidate_evidence_status') or ''),
        'candidate_evidence_domain_counts': row.get('candidate_evidence_domain_counts', {}),
        'candidate_evidence_matched_domains': row.get('candidate_evidence_matched_domains', []),
        'candidate_evidence_missing_domains': row.get('candidate_evidence_missing_domains', []),
        'enhanced_evidence_domain_counts': row.get('enhanced_evidence_domain_counts', {}),
        'enhanced_evidence_matched_domains': row.get('enhanced_evidence_matched_domains', []),
        'enhanced_evidence_missing_domains': row.get('enhanced_evidence_missing_domains', []),
        'experimental_evidence_domain_counts': row.get('experimental_evidence_domain_counts', {}),
        'experimental_evidence_matched_domains': row.get('experimental_evidence_matched_domains', []),
        'experimental_evidence_missing_domains': row.get('experimental_evidence_missing_domains', []),
        'structured_score_components': row.get('structured_score_components') or row.get('components'),
        'structured_score_mode': row.get('structured_score_mode') or row.get('mode'),
        'risk_penalty': profile['risk_penalty'],
        'regulatory_hard_block': profile['regulatory_hard_block'],
        'opportunity_hard_block': profile['opportunity_hard_block'],
        'candidate_stage': profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']),
        'early_opportunity_score': early_opportunity_score_for_row(row),
        'setup_type': profile['setup_type'] or str(row.get('setup_type') or row.get('setup_type_refined') or ''),
        'near_limit_up_risk': bool(row.get('near_limit_up_risk')) or any(
            normalized_block_bucket(reason) == 'near_limit_up_risk'
            for reason in (row.get('blocked_reasons') or [])
        ),
        'blocked_reasons': row.get('blocked_reasons') or [],
        'formal_eligible': eligibility['eligible'],
        'formal_blockers': formal_blockers,
        'paper_pick_eligibility': eligibility,
        'structured_score': profile['structured_score'],
        'final_shadow_score': profile['final_shadow_score'],
        'base_score': profile['base_score'],
        'score': profile['base_score'],
        'sector_opportunity_score': profile['sector_opportunity_score'],
        'sector_opportunity_tags': profile['sector_opportunity_tags'],
        'sector_news_strength': profile['sector_news_strength'],
        'vei_phase_d_tags': profile['vei_phase_d_tags'],
        'search_layer_hint': profile['search_layer_hint'],
        'news_catalyst_strength': profile['news_catalyst_strength'],
        'mainboard_auxiliary_evidence_status': profile['mainboard_auxiliary_evidence_status'],
        'mainboard_auxiliary_missing_domains': profile['mainboard_auxiliary_missing_domains'],
        'announcement_catalyst_score': profile['announcement_catalyst_score'],
        'sector_news_catalyst_score': profile['sector_news_catalyst_score'],
        'limitup_reason_quality_score': profile['limitup_reason_quality_score'],
        'risk_notice_penalty': profile['risk_notice_penalty'],
        'mainboard_auxiliary_confidence': profile['mainboard_auxiliary_confidence'],
        'news_catalyst_quality_categories': profile['news_catalyst_quality_categories'],
        'sector_catalyst_score': profile['sector_catalyst_score'],
        'topic_propagation_score': profile['topic_propagation_score'],
        'intraday_alert_strength': profile['intraday_alert_strength'],
        'limitup_reason_propagation_score': profile['limitup_reason_propagation_score'],
        'low_position_catalyst_score': profile['low_position_catalyst_score'],
        'structured_component_details': profile['structured_component_details'],
        'research_signals': profile['research_signals'],
        'research_panel_overall': profile['research_panel_overall'],
        'catalyst_quality_category': profile['catalyst_quality_category'],
        'a_share_risk_review_disqualified_for_paper_pick': profile['a_share_risk_review_disqualified_for_paper_pick'],
        'historical_pattern_name': profile['historical_pattern_name'],
        'why_not_formal_candidate': why_not_formal_candidate(row),
        **repo_contribution_context(row),
    }


def structured_formal_impact_summary(enriched_rows: List[Dict[str, Any]], formal_rows: List[Dict[str, Any]], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    formal_symbols = {symbol_for(row) for row in formal_rows if symbol_for(row)}
    structured_rows = [row for row in enriched_rows if structured_signal_present(row)]
    structured_only_rows = [
        row for row in structured_rows
        if safe_float(row.get('score')) is None and symbol_for(row) not in formal_symbols
    ]
    stage_priority = {
        'underwater': 5,
        'flat_0_to_3': 4,
        'early_3_to_5': 3,
        'mid_5_to_7': 2,
        'high_7_to_9': 1,
        'near_limit_9_plus': 0,
        'unknown': 0,
    }

    def structured_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        profile = structured_signal_profile(row)
        shadow_score = safe_float(row.get('final_shadow_score'))
        if shadow_score is None:
            shadow_score = safe_float(row.get('score')) or 0.0
        return (
            early_opportunity_score_for_row(row),
            stage_priority.get(profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']), 0),
            profile['sector_opportunity_score'] or 0.0,
            shadow_score,
            safe_float(row.get('amount_pctile_rule')) or 0.0,
        )

    sector_opportunity_rows = [
        row for row in structured_only_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) > 0
    ]
    structured_only_non_sector_rows = [
        row for row in structured_only_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) <= 0
    ]
    structured_only_sorted = sorted(structured_only_non_sector_rows, key=structured_sort_key, reverse=True)

    def sector_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        profile = structured_signal_profile(row)
        return (
            profile['sector_opportunity_score'] or 0.0,
            early_opportunity_score_for_row(row),
            stage_priority.get(profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']), 0),
            safe_float(row.get('structured_score')) or 0.0,
        )

    counts = Counter()
    for row in structured_only_rows:
        buckets = {normalized_block_bucket(reason) for reason in formal_blockers_for_row(row)}
        for bucket in buckets:
            if bucket:
                counts[bucket] += 1

    top_structured_only_candidates = [structured_observation_candidate(row, bundle) for row in structured_only_sorted[:RESEARCH_BASKET_SIZE]]
    sector_opportunity_candidates = [
        structured_observation_candidate(row, bundle)
        for row in sorted(sector_opportunity_rows, key=sector_sort_key, reverse=True)
    ]
    structured_observation_candidates = []
    seen_symbols = set()
    for candidate in top_structured_only_candidates + sector_opportunity_candidates:
        symbol = candidate.get('symbol') or candidate.get('code')
        if not symbol or symbol in seen_symbols:
            continue
        structured_observation_candidates.append(candidate)
        seen_symbols.add(symbol)
    structured_sector_opportunity_count = sum(
        1 for row in structured_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) > 0
    )
    return {
        'structured_candidate_count': len(structured_rows),
        'structured_sector_opportunity_count': structured_sector_opportunity_count,
        'structured_only_not_in_formal_basket_count': len(structured_only_rows),
        'top_structured_only_candidates': top_structured_only_candidates,
        'sector_opportunity_candidates': sector_opportunity_candidates,
        'structured_observation_candidates': structured_observation_candidates,
        'block_reason_counts': dict(counts),
    }


def limitup_quality_block_reason(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> str:
    bundle = bundle if isinstance(bundle, dict) else {}
    signal_pct = safe_float(row.get('signal_pct'))
    close_position_score = safe_float(row.get('close_position_score'))
    if signal_pct is None:
        return ''

    candidate_stage = str(row.get('candidate_stage') or signal_stage_bucket(signal_pct))
    if signal_pct < 5.0 and candidate_stage not in ('high_7_to_9', 'near_limit_9_plus'):
        return ''
    if signal_pct < 7.0 and candidate_stage not in ('high_7_to_9', 'near_limit_9_plus') and (close_position_score is None or close_position_score < 0.70):
        return ''

    market_context = market_adaptive_context(row, bundle)
    supportive_market = bool(market_context.get('supportive_market'))
    weak_acceptance_market = bool(market_context.get('weak_acceptance_market'))
    broken_limit_pressure = bool(market_context.get('broken_limit_pressure'))
    thresholds = market_adaptive_thresholds(candidate_stage, market_context)

    limitup_reason = structured_component(row, 'limitup_reason_strength')
    seal_order = structured_component(row, 'seal_order_strength')
    order_book = structured_component(row, 'order_book_pressure')
    components_seen = [x for x in (limitup_reason, seal_order, order_book) if x is not None]
    auxiliary_status = str(row.get('mainboard_auxiliary_evidence_status') or '')
    auxiliary_limitup_quality = safe_float(row.get('limitup_reason_quality_score')) or 0.0
    auxiliary_news = safe_float(row.get('news_catalyst_strength')) or 0.0
    auxiliary_announcement = safe_float(row.get('announcement_catalyst_score')) or 0.0
    auxiliary_sector_news = safe_float(row.get('sector_news_catalyst_score')) or 0.0
    auxiliary_confirmation = max(auxiliary_limitup_quality, auxiliary_news, auxiliary_announcement, auxiliary_sector_news)
    if candidate_stage in ('high_7_to_9', 'near_limit_9_plus') and auxiliary_confirmation >= 0.65:
        return ''
    if not components_seen:
        return 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' if auxiliary_status else ''

    if max(components_seen) >= thresholds['component_min']:
        return ''

    limitup_capture_score = safe_float(row.get('limitup_capture_score'))
    if limitup_capture_score is None:
        limitup_capture_score = safe_float((row.get('structured_component_details') or {}).get('limitup_capture_score'))
    limitup_capture_profile = str(row.get('limitup_capture_profile') or (row.get('structured_component_details') or {}).get('limitup_capture_profile') or '')
    limitup_reason_propagation_score = safe_float(row.get('limitup_reason_propagation_score'))
    if limitup_reason_propagation_score is None:
        limitup_reason_propagation_score = safe_float((row.get('structured_component_details') or {}).get('limitup_reason_propagation_score'))
    intraday_alert_strength = safe_float(row.get('intraday_alert_strength'))
    if intraday_alert_strength is None:
        intraday_alert_strength = safe_float((row.get('structured_component_details') or {}).get('intraday_alert_strength'))
    main_theme_alignment_score = safe_float(row.get('main_theme_alignment_score'))
    if main_theme_alignment_score is None:
        main_theme_alignment_score = safe_float((row.get('structured_component_details') or {}).get('main_theme_alignment_score'))
    main_theme_core_score = safe_float(row.get('main_theme_core_score'))
    if main_theme_core_score is None:
        main_theme_core_score = safe_float((row.get('structured_component_details') or {}).get('main_theme_core_score'))

    if (
        supportive_market
        and not weak_acceptance_market
        and candidate_stage == 'high_7_to_9'
        and close_position_score is not None
        and close_position_score >= 0.84
        and sum(1 for value in components_seen if value >= 0.52) >= 2
    ):
        return ''

    if (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and not weak_acceptance_market
        and close_position_score is not None
        and close_position_score >= (0.82 if candidate_stage == 'near_limit_9_plus' else 0.80)
        and limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE'
        and (limitup_capture_score or 0.0) >= 0.62
        and (limitup_reason_propagation_score or 0.0) >= 0.60
        and (
            (intraday_alert_strength or 0.0) >= 0.90
            or (main_theme_alignment_score or 0.0) >= 0.55
            or (main_theme_core_score or 0.0) >= 0.60
        )
    ):
        return ''

    if (
        broken_limit_pressure
        and candidate_stage == 'high_7_to_9'
        and close_position_score is not None
        and close_position_score < 0.84
    ):
        return 'BROKEN_LIMIT_WEAK_FOLLOW_THROUGH_CONFIRMATION_GAP'
    return 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'


def candidate_capital_risk_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    def first_value(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)

    capital_flow = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
    failed_limitup = bool(
        row.get('failed_limitup')
        or row.get('broken_limit_risk')
        or row.get('is_broken_limit')
        or row.get('opened_limit_up')
        or row.get('炸板')
    )
    main_buy_net = first_value(
        safe_float(row.get('main_buy_net')),
        safe_float(row.get('main_buy_net_inflow')),
        safe_float(capital_flow.get('main_buy_net')),
        safe_float(capital_flow.get('main_buy_net_inflow')),
        safe_float(capital_flow.get('main_force_net_inflow')),
        safe_float(row.get('net_inflow_main')),
        0.0,
    )
    dark_pool_net = first_value(
        safe_float(row.get('dark_pool_net')),
        safe_float(row.get('dark_pool_net_inflow')),
        safe_float(row.get('hidden_fund_net_inflow')),
        safe_float(capital_flow.get('dark_pool_net')),
        safe_float(capital_flow.get('dark_pool_net_inflow')),
        safe_float(capital_flow.get('hidden_fund_net_inflow')),
        0.0,
    )
    popularity_value = row.get('popularity_rank')
    if isinstance(popularity_value, dict):
        popularity_value = first_value(
            safe_float(popularity_value.get('rank')),
            safe_float(popularity_value.get('ranking')),
            safe_float(popularity_value.get('current_rank')),
        )
    popularity_rank = safe_float(popularity_value)
    failed_limitup_risk = 1.0 if failed_limitup else 0.0
    main_buy_outflow_pressure = min(1.0, abs(main_buy_net) / 500000000.0) if main_buy_net < 0 else 0.0
    dark_pool_inflow_support = min(1.0, dark_pool_net / 500000000.0) if dark_pool_net > 0 else 0.0
    if popularity_rank is None or popularity_rank <= 0:
        popularity_crowding_risk = 0.0
    elif popularity_rank == 1:
        popularity_crowding_risk = 1.0
    elif popularity_rank <= 3:
        popularity_crowding_risk = 0.8
    elif popularity_rank <= 10:
        popularity_crowding_risk = 0.5
    else:
        popularity_crowding_risk = 0.0
    announcement_strength = safe_float(row.get('announcement_catalyst_score')) or 0.0
    news_strength = safe_float(row.get('news_catalyst_strength')) or 0.0
    sector_news_strength = safe_float(row.get('sector_news_catalyst_score')) or 0.0
    continuation_gene = safe_float(row.get('continuation_gene_score')) or 0.0
    direct_catalyst_strength = max(announcement_strength, news_strength)
    catalyst_strength = max(direct_catalyst_strength, sector_news_strength * 0.50)
    weak_limitup_confirmation = max(safe_float(row.get('limitup_reason_quality_score')) or 0.0, continuation_gene) < 0.45
    profit_taking_pressure = min(
        1.0,
        failed_limitup_risk * 0.45
        + popularity_crowding_risk * 0.35
        + (0.20 if bool((row.get('yesterday_one_word_limitup_gene_evidence') or {}).get('candidate_was_yesterday_one_word_limitup')) else 0.0),
    )
    post_broken_board_selloff_risk = min(1.0, failed_limitup_risk * 0.55 + main_buy_outflow_pressure * 0.45)
    high_popularity_trap_risk = min(
        1.0,
        failed_limitup_risk * 0.40 + main_buy_outflow_pressure * 0.35 + popularity_crowding_risk * 0.25,
    ) if catalyst_strength < 0.60 and weak_limitup_confirmation else 0.0
    risk_softened_by_dark_pool_inflow = bool(
        dark_pool_inflow_support >= 0.35
        and (continuation_gene >= 0.35 or direct_catalyst_strength >= 0.50)
    )
    gross_risk = max(post_broken_board_selloff_risk, high_popularity_trap_risk, profit_taking_pressure)
    softened_risk = max(0.0, gross_risk - (dark_pool_inflow_support * 0.30 if risk_softened_by_dark_pool_inflow else 0.0))
    risk_codes = []
    if failed_limitup and main_buy_outflow_pressure > 0:
        risk_codes.append('BROKEN_BOARD_WITH_MAIN_BUY_OUTFLOW')
    if popularity_crowding_risk >= 0.8 and profit_taking_pressure > 0:
        risk_codes.append('POPULARITY_CROWDING_PROFIT_TAKING_RISK')
    if weak_limitup_confirmation and (failed_limitup or popularity_crowding_risk >= 0.8):
        risk_codes.append('HIGH_CHASE_WITH_WEAK_LIMITUP_CONFIRMATION')
    if risk_softened_by_dark_pool_inflow:
        risk_codes.append('risk_softened_by_dark_pool_inflow')
    return {
        'failed_limitup': failed_limitup,
        'main_buy_net': main_buy_net,
        'dark_pool_net': dark_pool_net,
        'popularity_rank': int(popularity_rank) if popularity_rank is not None else None,
        'failed_limitup_risk': round(failed_limitup_risk, 4),
        'main_buy_outflow_pressure': round(main_buy_outflow_pressure, 4),
        'dark_pool_inflow_support': round(dark_pool_inflow_support, 4),
        'popularity_crowding_risk': round(popularity_crowding_risk, 4),
        'profit_taking_pressure': round(profit_taking_pressure, 4),
        'post_broken_board_selloff_risk': round(post_broken_board_selloff_risk, 4),
        'high_popularity_trap_risk': round(high_popularity_trap_risk, 4),
        'capital_divergence_score': round(dark_pool_inflow_support - main_buy_outflow_pressure, 4),
        'risk_softened_by_dark_pool_inflow': risk_softened_by_dark_pool_inflow,
        'risk_penalty_score': round(softened_risk, 4),
        'risk_codes': risk_codes,
        'catalyst_strength': round(catalyst_strength, 4),
        'continuation_gene_score': round(continuation_gene, 4),
        'weak_limitup_confirmation': weak_limitup_confirmation,
    }


def limitup_probability_proxy_components(
    row: Dict[str, Any],
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Explainable auxiliary proxy; it never independently determines PAPER_PICK."""
    profile = profile if isinstance(profile, dict) else structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    sector_proxy = row.get('sector_yesterday_limitup_gene_proxy') or auxiliary.get('sector_yesterday_limitup_gene_proxy') or {}
    sector_gene = safe_float(sector_proxy.get('continuation_gene_score')) if isinstance(sector_proxy, dict) else None
    sector_gene = sector_gene if sector_gene is not None else (safe_float(row.get('continuation_gene_score')) or 0.0)
    positive = {
        'sector_yesterday_limitup_gene_proxy': min(1.0, sector_gene) * 0.13,
        'limitup_reason_strength': min(1.0, profile.get('limitup_reason_strength') or 0.0) * 0.10,
        'seal_order_strength': min(1.0, profile.get('seal_order_strength') or 0.0) * 0.10,
        'close_position_score': min(1.0, profile.get('close_position_score') or 0.0) * 0.08,
        'volume_ratio': min(1.0, (profile.get('volume_ratio') or 0.0) / 3.0) * 0.07,
        'fund_flow_momentum': min(1.0, max(0.0, profile.get('fund_flow_momentum') or 0.0)) * 0.10,
        'time_series_momentum': min(1.0, max(0.0, profile.get('time_series_momentum') or 0.0)) * 0.06,
        'confirmed_news_catalyst': min(1.0, profile.get('news_catalyst_strength') or 0.0) * 0.09,
        'announcement_catalyst': min(1.0, profile.get('announcement_catalyst_score') or 0.0) * 0.08,
        'low_position_catalyst_score': min(1.0, safe_float(row.get('low_position_catalyst_score')) or 0.0) * 0.09,
        'main_theme_alignment_score': min(1.0, profile.get('main_theme_alignment_score') or 0.0) * 0.05,
        'continuation_gene_score': min(1.0, safe_float(row.get('continuation_gene_score')) or 0.0) * 0.05,
    }
    negative = {
        'failed_limitup_risk': min(1.0, capital.get('failed_limitup_risk') or 0.0) * 0.25,
        'weak_limitup_confirmation': 0.12 if capital.get('weak_limitup_confirmation') else 0.0,
        'open_board_risk': min(1.0, capital.get('open_board_risk') or 0.0) * 0.15,
        'main_buy_outflow_pressure': min(1.0, capital.get('main_buy_outflow_pressure') or 0.0) * 0.20,
        'popularity_crowding_risk': min(1.0, capital.get('popularity_crowding_risk') or 0.0) * 0.10,
        'high_popularity_trap_risk': min(1.0, capital.get('high_popularity_trap_risk') or 0.0) * 0.15,
        'risk_notice_evidence': min(1.0, profile.get('risk_notice_penalty') or 0.0) * 0.10,
        'capital_risk_penalty': min(1.0, capital.get('risk_penalty_score') or 0.0) * 0.10,
    }
    score = max(0.0, min(1.0, sum(positive.values()) - sum(negative.values())))
    blocked = negative['failed_limitup_risk'] >= 0.20 and negative['main_buy_outflow_pressure'] >= 0.15
    status = 'BLOCKED' if blocked else ('STRONG' if score >= 0.55 else ('MEDIUM' if score >= 0.28 else 'WEAK'))
    return {
        'limitup_probability_proxy': round(score, 4),
        'limitup_proxy_positive_components': [key for key, value in positive.items() if value > 0],
        'limitup_proxy_negative_components': [key for key, value in negative.items() if value > 0],
        'limitup_proxy_status': status,
    }


def social_confirmation_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    """Describe social evidence without promoting a candidate to PAPER_PICK."""
    catalyst = safe_float(row.get('social_catalyst_score'))
    theme = safe_float(row.get('theme_strength_last30d'))
    sentiment = safe_float(row.get('social_sentiment_score'))
    noise = safe_float(row.get('social_noise_risk'))
    quality = str(row.get('social_signal_quality') or 'MISSING').upper()
    source_layers = list(row.get('social_source_layers') or [])
    collection_status = str(row.get('social_signal_collection_status') or '').upper()
    collection_errors = list(row.get('social_signal_error') or [])
    reasons: List[str] = []
    if quality == 'MISSING' or not source_layers:
        status = 'MISSING'
        reasons.append('social_signal_missing')
    elif (noise or 0.0) >= 0.70:
        status = 'NOISY'
        reasons.append('social_noise_risk_high')
    elif (catalyst or 0.0) >= 0.60 and (theme or 0.0) >= 0.50:
        status = 'PASS'
        reasons.append('social_theme_confirmation')
    else:
        status = 'WEAK'
        reasons.append('social_confirmation_below_shadow_threshold')
    if collection_status == 'WARN':
        reasons.append('social_collection_warn')
    if collection_errors:
        reasons.append('social_collection_error_recorded')
    return {
        'status': status,
        'social_catalyst_score': catalyst,
        'theme_strength_last30d': theme,
        'social_sentiment_score': sentiment,
        'social_noise_risk': noise,
        'social_signal_quality': quality,
        'source_count': len(source_layers),
        'source_layers': source_layers,
        'collection_status': collection_status or ('PASS' if source_layers else 'MISSING'),
        'collection_errors': collection_errors,
        'reason': reasons,
        'used_for_official_ranking': False,
    }


def shadow_risk_profile(
    row: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Produce explainable weak-market and chase diagnostics for shadow replay."""
    bundle = bundle if isinstance(bundle, dict) else {}
    profile = profile if isinstance(profile, dict) else structured_signal_profile(row, bundle)
    market = market_adaptive_context(row, bundle)
    breadth = safe_float(market.get('market_breadth_up_pct')) or 50.0
    broken_ratio = safe_float(market.get('limitup_broken_ratio')) or 0.0
    broken_count = safe_float(market.get('broken_limitups')) or 0.0
    index_returns = (bundle.get('market_snapshot') or {}).get('index_returns') or row.get('index_returns') or {}
    if not isinstance(index_returns, dict):
        index_returns = {}
    negative_indexes = [
        name for name, value in index_returns.items()
        if (safe_float(value) or 0.0) <= (-2.5 if '500' in str(name) else -2.0)
    ]
    risk_reasons = []
    risk_points = 0
    if negative_indexes:
        risk_points += 2
        risk_reasons.append('index_drawdown:' + ','.join(sorted(map(str, negative_indexes))))
    if breadth < 40:
        risk_points += 2
        risk_reasons.append('market_breadth_below_40pct')
    elif breadth < 48:
        risk_points += 1
        risk_reasons.append('market_breadth_below_48pct')
    if market.get('weak_acceptance_market'):
        risk_points += 2
        risk_reasons.append('weak_acceptance_market')
    if market.get('broken_limit_pressure') or broken_ratio < 1.0 or broken_count >= 30:
        risk_points += 1
        risk_reasons.append('broken_limit_pressure')
    risk_level = 'EXTREME' if risk_points >= 5 else ('HIGH' if risk_points >= 3 else ('MEDIUM' if risk_points >= 1 else 'LOW'))
    weak_market = risk_level in ('HIGH', 'EXTREME')

    proxy = limitup_probability_proxy_components(row, profile)
    social = social_confirmation_profile(row)
    signal_pct = profile.get('signal_pct') or 0.0
    close_position = profile.get('close_position_score') or 0.0
    fund_flow = profile.get('fund_flow_momentum') or 0.0
    high_stage = signal_stage_bucket(signal_pct) in ('mid_5_to_7', 'high_7_to_9')
    chase_reasons = []
    penalty = 0.0
    if signal_pct >= 5.0 and proxy['limitup_proxy_status'] in ('WEAK', 'BLOCKED'):
        penalty += 7.0
        chase_reasons.append('high_pct_with_weak_limitup_proxy')
    if close_position < 0.70:
        penalty += 3.0
        chase_reasons.append('close_position_not_strong')
    if fund_flow <= 0:
        penalty += 3.0
        chase_reasons.append('fund_flow_not_confirmed')
    if social['status'] in ('MISSING', 'WEAK', 'NOISY'):
        penalty += 2.0
        chase_reasons.append('no_clean_social_confirmation')
    if weak_market and high_stage:
        penalty += 5.0
        chase_reasons.append('weak_market_high_chase_stage')
    penalty = min(20.0, penalty)
    chase_level = 'HIGH' if penalty >= 12 else ('MEDIUM' if penalty >= 6 else 'LOW')

    defensive = 0.0
    defensive_reasons = []
    if signal_pct <= 3.0:
        defensive += 0.25
        defensive_reasons.append('low_pct_start')
    if 0.45 <= close_position <= 0.85:
        defensive += 0.15
        defensive_reasons.append('non_climax_close_position')
    if fund_flow > 0:
        defensive += 0.25
        defensive_reasons.append('fund_flow_stable')
    if str(row.get('sector_name') or row.get('industry') or '') in ('电力', '银行', '运营商', '公用事业'):
        defensive += 0.20
        defensive_reasons.append('defensive_industry')
    if (safe_float(row.get('turnover_rate')) or 0.0) <= 5.0:
        defensive += 0.15
        defensive_reasons.append('low_turnover')
    defensive = round(min(1.0, defensive), 4)

    gene_strength = 'STRONG' if proxy['limitup_proxy_status'] == 'STRONG' else (
        'MEDIUM' if proxy['limitup_proxy_status'] == 'MEDIUM' else (
            'WEAK' if proxy['limitup_proxy_status'] == 'WEAK' else 'NONE'
        )
    )
    gene_reasons = []
    gene_gate = 'PASS'
    if weak_market and gene_strength == 'WEAK' and signal_pct >= 5.0:
        confluence = (
            (profile.get('sector_catalyst_score') or 0.0) >= 0.60
            and fund_flow > 0
            and social['status'] == 'PASS'
        )
        if not confluence:
            gene_gate = 'BLOCK_SHADOW'
            gene_reasons.append('weak_market_weak_gene_without_sector_fund_social_confluence')
        else:
            gene_gate = 'WARN'
            gene_reasons.append('weak_market_weak_gene_confluence_only')
    elif gene_strength == 'WEAK':
        gene_gate = 'WARN'
        gene_reasons.append('weak_gene_risk_notice')

    return {
        'market_regime_risk': risk_level,
        'weak_market': weak_market,
        'market_regime_risk_reason': risk_reasons,
        'chase_high_risk': chase_level,
        'chase_high_shadow_penalty': round(penalty, 4),
        'chase_high_reason': chase_reasons,
        'defensive_carry_score': defensive,
        'defensive_reason': defensive_reasons,
        'limitup_gene_strength': gene_strength,
        'limitup_gene_shadow_gate': gene_gate,
        'limitup_gene_block_reason': gene_reasons,
        'social_confirmation': social,
        'used_for_official_ranking': False,
    }


def paper_pick_risk_explanation_gate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Reject an unexplained broken-board/outflow/popularity-trap PAPER_PICK."""
    profile = structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    proxy = limitup_probability_proxy_components(row)
    triple_risk = bool(
        (capital.get('failed_limitup_risk') or 0.0) > 0
        and (capital.get('main_buy_outflow_pressure') or 0.0) > 0
        and ((capital.get('high_popularity_trap_risk') or 0.0) > 0 or (capital.get('popularity_crowding_risk') or 0.0) >= 0.8)
    )
    strong_rebuttals = []
    if (profile.get('news_catalyst_strength') or 0.0) >= 0.75:
        strong_rebuttals.append('confirmed_news_catalyst_strong')
    if (profile.get('announcement_catalyst_score') or 0.0) >= 0.75:
        strong_rebuttals.append('announcement_catalyst_strong')
    if (safe_float(row.get('continuation_gene_score')) or 0.0) >= 0.70:
        strong_rebuttals.append('sector_yesterday_limitup_gene_proxy_strong')
    if proxy['limitup_probability_proxy'] >= 0.65 and proxy['limitup_proxy_status'] != 'BLOCKED':
        strong_rebuttals.append('limitup_probability_proxy_strong')
    return {
        'status': 'PASS' if not triple_risk or strong_rebuttals else 'FAIL',
        'triple_risk': triple_risk,
        'strong_rebuttals': strong_rebuttals,
        'rule': 'failed_limitup + outflow + high_popularity requires explicit catalyst/risk rebuttal',
    }


def ranking_basis_adjustment_components(row: Dict[str, Any]) -> Dict[str, Any]:
    """Explainable adjustments within the existing structured ranking basis."""
    profile = structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    sector_proxy = row.get('sector_yesterday_limitup_gene_proxy') or auxiliary.get('sector_yesterday_limitup_gene_proxy') or {}
    sector_proxy_score = safe_float(sector_proxy.get('continuation_gene_score')) if isinstance(sector_proxy, dict) else None
    if sector_proxy_score is None:
        sector_proxy_score = safe_float(row.get('continuation_gene_score')) or 0.0
    capital_flow = auxiliary.get('capital_flow') if isinstance(auxiliary.get('capital_flow'), dict) else {}
    capital_flow_quality = 1.0 if capital_flow else (1.0 if isinstance(row.get('data_directory_capital_flow'), dict) and row.get('data_directory_capital_flow') else 0.0)
    news_evidence = auxiliary.get('news') if isinstance(auxiliary.get('news'), dict) else {}
    announcement_evidence = auxiliary.get('announcements')
    announcement_confirmed = bool(announcement_evidence) and not (
        isinstance(announcement_evidence, dict) and announcement_evidence.get('status') == 'MISSING'
    )
    if isinstance(announcement_evidence, list):
        announcement_confirmed = any(
            not (isinstance(item, dict) and item.get('status') == 'MISSING')
            for item in announcement_evidence
        )
    news_confirmed = bool(news_evidence.get('direct_symbol_news')) and news_evidence.get('status') != 'MISSING'
    confirmed_news = min(1.0, max(profile.get('news_catalyst_strength') or 0.0, 1.0 if news_confirmed else 0.0))
    announcement = min(1.0, max(profile.get('announcement_catalyst_score') or 0.0, 1.0 if announcement_confirmed else 0.0))
    low_position = min(1.0, safe_float(row.get('low_position_catalyst_score')) or 0.0)
    risk_notice = min(1.0, profile.get('risk_notice_penalty') or 0.0)
    limitup_proxy = limitup_probability_proxy_components(row)
    risk_gate = paper_pick_risk_explanation_gate(row)
    penalties = {
        'failed_limitup_risk': (capital.get('failed_limitup_risk') or 0.0) * 0.85,
        'main_buy_outflow_pressure': (capital.get('main_buy_outflow_pressure') or 0.0) * 0.75,
        'popularity_crowding_risk': (capital.get('popularity_crowding_risk') or 0.0) * 0.45,
        'high_popularity_trap_risk': (capital.get('high_popularity_trap_risk') or 0.0) * 0.85,
        'weak_limitup_confirmation': 0.35 if capital.get('weak_limitup_confirmation') else 0.0,
        'risk_notice_evidence': risk_notice * 0.60,
        'high_popularity_trap_combo_penalty': 1.25 if risk_gate['status'] == 'FAIL' else 0.0,
    }
    boosts = {
        'confirmed_news_catalyst': confirmed_news * 0.50,
        'announcement_catalyst': announcement * 0.45,
        'sector_yesterday_limitup_gene_proxy': min(1.0, sector_proxy_score or 0.0) * 0.35,
        'low_position_catalyst_score': low_position * 0.50,
        'capital_flow_evidence_quality': capital_flow_quality * 0.20,
        'limitup_probability_proxy': limitup_proxy['limitup_probability_proxy'] * 0.30,
    }
    return {
        'boosts': {key: round(value, 4) for key, value in boosts.items()},
        'penalties': {key: round(value, 4) for key, value in penalties.items()},
        'boost_total': round(sum(boosts.values()), 4),
        'penalty_total': round(sum(penalties.values()), 4),
        'net_adjustment': round(sum(boosts.values()) - sum(penalties.values()), 4),
        **limitup_proxy,
        'paper_pick_risk_explanation_gate': risk_gate,
    }


def formal_candidate_sort_key(row: Dict[str, Any]) -> Tuple[float, ...]:
    profile = structured_signal_profile(row)
    signal_pct = profile['signal_pct'] or 0.0
    close_position = profile['close_position_score'] or 0.0
    volume_ratio = profile['volume_ratio'] or 0.0
    fund_flow = profile['fund_flow_momentum'] or 0.0
    time_series = profile['time_series_momentum'] or 0.0
    main_theme_alignment = profile.get('main_theme_alignment_score') or 0.0
    main_theme_core = profile.get('main_theme_core_score') or 0.0
    auxiliary_confidence = profile.get('mainboard_auxiliary_confidence') or 0.0
    auxiliary_catalyst = max(
        profile.get('announcement_catalyst_score') or 0.0,
        profile.get('news_catalyst_strength') or 0.0,
        profile.get('sector_news_catalyst_score') or 0.0,
        profile.get('limitup_reason_quality_score') or 0.0,
    )
    auxiliary_risk = profile.get('risk_notice_penalty') or 0.0
    capital_risk = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    capital_risk_penalty = safe_float(capital_risk.get('risk_penalty_score')) or 0.0
    adjustment = ranking_basis_adjustment_components(row)
    ranking_adjustment = adjustment['net_adjustment']
    limitup_proxy = adjustment['limitup_probability_proxy']
    continuation_gene = safe_float(row.get('continuation_gene_score')) or 0.0
    continuation_expectation = 0.0
    if signal_pct >= 6.0:
        continuation_expectation += min(2.0, signal_pct / 10.0)
        if close_position >= 0.82:
            continuation_expectation += 1.0
        if volume_ratio >= 2.0:
            continuation_expectation += 1.0
        if fund_flow >= 0.5:
            continuation_expectation += 1.0
        if time_series >= 0.2:
            continuation_expectation += 0.8
        if (profile['sector_opportunity_score'] or 0.0) >= 0.5:
            continuation_expectation += 0.8
        if main_theme_alignment >= 0.5:
            continuation_expectation += 1.2
    return (
        main_theme_core + ranking_adjustment * 0.15,
        auxiliary_catalyst + continuation_gene * 0.40 + limitup_proxy * 0.20 - auxiliary_risk - capital_risk_penalty + ranking_adjustment,
        ranking_adjustment,
        limitup_proxy,
        -capital_risk_penalty,
        auxiliary_confidence,
        safe_float(row.get('structured_priority_score')) or 0.0,
        continuation_expectation,
        safe_float(row.get('structured_score')) or 0.0,
        close_position,
        volume_ratio,
        fund_flow,
        profile['sector_opportunity_score'] or 0.0,
        main_theme_alignment,
        profile['limitup_reason_strength'] or 0.0,
        profile['seal_order_strength'] or 0.0,
        safe_float(row.get('amount_pctile_rule')) or 0.0,
        profile['order_book_pressure'] or 0.0,
        safe_float(row.get('final_shadow_score')) or safe_float(row.get('score')) or 0.0,
        -(safe_float(row.get('rank')) or 999.0),
    )


def replay_only_sector_opportunity(profile: Dict[str, Any], row: Dict[str, Any] | None = None) -> bool:
    details = profile.get('structured_component_details') if isinstance(profile.get('structured_component_details'), dict) else {}
    tags = normalize_tag_list(profile.get('sector_opportunity_tags')) + normalize_tag_list(details.get('sector_opportunity_tags'))
    orig_tags = normalize_tag_list(row.get('sector_opportunity_tags')) if isinstance(row, dict) else []
    all_tags = tags + orig_tags
    if not all_tags:
        return False
    non_replay_tags = [tag for tag in all_tags if not str(tag).startswith('REPLAY_')]
    has_replay = any(str(tag).startswith('REPLAY_') for tag in tags)
    return has_replay and not non_replay_tags


def paper_pick_eligibility_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    profile = _cached_structured_signal_profile(row, bundle)
    scoring_config = get_scoring_config_snapshot()
    scoring_config_values = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(scoring_config_values, dict):
        scoring_config_values = dict(SCORING_CONFIG_DEFAULTS)
    price = safe_float(row.get('price'))
    evidence_missing_flags = web_tabs_evidence_missing_flags(bundle)
    candidate_evidence_flags = candidate_evidence_missing_flags(row, bundle)
    normalized_source_time = normalized_source_time_for_candidate(row, bundle)
    repo_contributions = row.get('repo_contributions') if isinstance(row.get('repo_contributions'), dict) else {}
    vei_repo_contribution = repo_contributions.get('VEI') if isinstance(repo_contributions.get('VEI'), dict) else {}
    vei_repo_score_delta = safe_float(vei_repo_contribution.get('score_delta'))
    if vei_repo_score_delta is None:
        vei_repo_score_delta = safe_float(candidate_repo_delta_by_repo(row).get('VEI'))
    signals = {
        'data_gate_status': str(bundle.get('data_gate_status') or profile['data_gate_status'] or ''),
        'candidate_evidence_status': profile['candidate_evidence_status'] or str(bundle.get('candidate_evidence_status') or ''),
        'source_time': normalized_source_time or profile['source_time'] or str(bundle.get('source_time') or ''),
        'runner_asof_time': profile['runner_asof_time'] or str(bundle.get('_runner_asof_time') or bundle.get('runner_asof_time') or ''),
        'trade_mode': profile['trade_mode'],
        'primary_return_field': profile['primary_return_field'],
        'primary_trade_horizon': profile['primary_trade_horizon'],
        'scoring_config_loaded': bool(scoring_config.get('loaded')),
        'scoring_config_source': str(scoring_config.get('source') or 'defaults'),
        'scoring_config_error': str(scoring_config.get('error') or ''),
        'weekday_blocklist': str(scoring_config_values.get('weekday_blocklist') or SCORING_CONFIG_DEFAULTS['weekday_blocklist']),
        'max_score_cap': safe_float(scoring_config_values.get('max_score_cap')) or safe_float(SCORING_CONFIG_DEFAULTS['max_score_cap']),
        'follow_on_strategy': str(scoring_config_values.get('follow_on_strategy') or SCORING_CONFIG_DEFAULTS['follow_on_strategy']),
        'follow_on_t1_weight': safe_float(scoring_config_values.get('follow_on_t1_weight')) or safe_float(SCORING_CONFIG_DEFAULTS['follow_on_t1_weight']),
        'follow_on_t2_weight': safe_float(scoring_config_values.get('follow_on_t2_weight')) or safe_float(SCORING_CONFIG_DEFAULTS['follow_on_t2_weight']),
        'follow_on_t3_weight': safe_float(scoring_config_values.get('follow_on_t3_weight')) or safe_float(SCORING_CONFIG_DEFAULTS['follow_on_t3_weight']),
        'follow_on_limit_up_threshold': safe_float(scoring_config_values.get('follow_on_limit_up_threshold')) or safe_float(SCORING_CONFIG_DEFAULTS['follow_on_limit_up_threshold']),
        'horizon_aware_strategy': str(scoring_config_values.get('horizon_aware_strategy') or SCORING_CONFIG_DEFAULTS['horizon_aware_strategy']),
        'instant_momentum_min_confirmations': safe_float(scoring_config_values.get('instant_momentum_min_confirmations')) or safe_float(SCORING_CONFIG_DEFAULTS['instant_momentum_min_confirmations']),
        'delayed_setup_min_persistence': safe_float(scoring_config_values.get('delayed_setup_min_persistence')) or safe_float(SCORING_CONFIG_DEFAULTS['delayed_setup_min_persistence']),
        'delayed_setup_floor_score': safe_float(scoring_config_values.get('delayed_setup_floor_score')) or safe_float(SCORING_CONFIG_DEFAULTS['delayed_setup_floor_score']),
        'delayed_setup_theme_min_score': safe_float(scoring_config_values.get('delayed_setup_theme_min_score')) or safe_float(SCORING_CONFIG_DEFAULTS['delayed_setup_theme_min_score']),
        'stale_repeat_window_days': safe_float(scoring_config_values.get('stale_repeat_window_days')) or safe_float(SCORING_CONFIG_DEFAULTS['stale_repeat_window_days']),
        'stale_decay_factor': safe_float(scoring_config_values.get('stale_decay_factor')) or safe_float(SCORING_CONFIG_DEFAULTS['stale_decay_factor']),
        'one_lot_cost': profile['one_lot_cost'] if profile['one_lot_cost'] is not None else (price * 100 if price is not None else None),
        'one_lot_cost_cap': safe_float(paper_sizing_context(bundle).get('one_lot_cost_cap')),
        'risk_penalty': profile['risk_penalty'],
        'regulatory_hard_block': profile['regulatory_hard_block'] or regulatory_hard_block_reason(row, bundle),
        'opportunity_hard_block': profile['opportunity_hard_block'] or opportunity_hard_block_reason(row, bundle) or limitup_quality_block_reason(row, bundle),
        'limitup_reason_strength': profile['limitup_reason_strength'],
        'seal_order_strength': profile['seal_order_strength'],
        'order_book_pressure': profile['order_book_pressure'],
        'sector_opportunity_score': profile['sector_opportunity_score'],
        'sector_opportunity_tags': profile['sector_opportunity_tags'],
        'sector_news_strength': profile['sector_news_strength'],
        'fund_flow_momentum': profile['fund_flow_momentum'],
        'time_series_momentum': profile['time_series_momentum'],
        'search_layer_hint': profile['search_layer_hint'],
        'news_catalyst_strength': profile['news_catalyst_strength'],
        'mainboard_auxiliary_evidence_status': profile['mainboard_auxiliary_evidence_status'],
        'mainboard_auxiliary_missing_domains': profile['mainboard_auxiliary_missing_domains'],
        'announcement_catalyst_score': profile['announcement_catalyst_score'],
        'sector_news_catalyst_score': profile['sector_news_catalyst_score'],
        'limitup_reason_quality_score': profile['limitup_reason_quality_score'],
        'risk_notice_penalty': profile['risk_notice_penalty'],
        'mainboard_auxiliary_confidence': profile['mainboard_auxiliary_confidence'],
        'news_catalyst_quality_categories': profile['news_catalyst_quality_categories'],
        'sector_catalyst_score': profile['sector_catalyst_score'],
        'topic_propagation_score': profile['topic_propagation_score'],
        'intraday_alert_strength': profile['intraday_alert_strength'],
        'limitup_reason_propagation_score': profile['limitup_reason_propagation_score'],
        'limitup_capture_score': profile['limitup_capture_score'],
        'limitup_capture_profile': profile['limitup_capture_profile'],
        'limitup_capture_confirmed': profile['limitup_capture_confirmed'],
        'limitup_capture_reasons': profile['limitup_capture_reasons'],
        'close_position_score': profile['close_position_score'],
        'low_position_catalyst_score': profile['low_position_catalyst_score'],
        'candidate_stage': profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']),
        'early_opportunity_score': profile['early_opportunity_score'] if profile['early_opportunity_score'] is not None else early_opportunity_score_for_row(row, bundle),
        'setup_type': profile['setup_type'],
        'research_signals': profile['research_signals'],
        'research_panel_overall': profile['research_panel_overall'],
        'catalyst_quality_category': profile['catalyst_quality_category'],
        'a_share_risk_review_disqualified_for_paper_pick': profile['a_share_risk_review_disqualified_for_paper_pick'],
        'historical_pattern_name': profile['historical_pattern_name'],
        'vei_repo_score_delta': vei_repo_score_delta,
    }
    candidate_lifecycle = _candidate_lifecycle_profile(row, bundle)
    signals['candidate_lifecycle'] = candidate_lifecycle
    signals['trade_mode'] = candidate_lifecycle.get('trade_mode') or signals['trade_mode']
    signals['primary_return_field'] = candidate_lifecycle.get('primary_return_field') or signals['primary_return_field']
    signals['primary_trade_horizon'] = candidate_lifecycle.get('primary_trade_horizon') or signals['primary_trade_horizon']
    signals['setup_class'] = candidate_lifecycle.get('setup_class')
    signals['setup_rank'] = candidate_lifecycle.get('setup_rank')
    signals['setup_reason'] = candidate_lifecycle.get('setup_reason')
    signals['repeat_count'] = candidate_lifecycle.get('repeat_count')
    signals['stale_decay'] = candidate_lifecycle.get('stale_decay')
    signals['lifecycle_score'] = candidate_lifecycle.get('lifecycle_score')
    blockers: List[str] = []
    positive_conditions: List[str] = []
    missing_conditions: List[str] = []
    capital_risk_profile = candidate_capital_risk_profile(row)
    signals['capital_risk_profile'] = capital_risk_profile
    signals['continuation_gene_score'] = safe_float(row.get('continuation_gene_score')) or 0.0
    signals['limitup_reason_status'] = str(row.get('limitup_reason_status') or 'MISSING')
    signals['limitup_reason_hard_block'] = bool(row.get('limitup_reason_hard_block', False))
    if capital_risk_profile.get('risk_codes'):
        missing_conditions.extend(capital_risk_profile['risk_codes'])
    if capital_risk_profile.get('risk_softened_by_dark_pool_inflow'):
        positive_conditions.append('risk_softened_by_dark_pool_inflow')
    auxiliary_status = signals.get('mainboard_auxiliary_evidence_status')
    auxiliary_status_normalized = str(auxiliary_status or '').upper()
    if auxiliary_status_normalized in ('PASS', 'OK'):
        positive_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    elif auxiliary_status:
        missing_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    if (signals.get('risk_notice_penalty') or 0.0) >= 0.60:
        blockers.append('mainboard_auxiliary_risk_notice_penalty>=0.60')
    elif (signals.get('risk_notice_penalty') or 0.0) > 0:
        positive_conditions.append('mainboard_auxiliary_risk_notice_under_review')

    price = safe_float(row.get('price'))
    data_gate_status = str(signals['data_gate_status'] or bundle.get('data_gate_status') or 'PASS')
    candidate_evidence_status = str(
        signals['candidate_evidence_status']
        or ('PASS' if not candidate_evidence_flags and not evidence_missing_flags else 'PARTIAL_OR_FAIL')
    )
    source_time = str(signals['source_time'] or '')
    runner_asof_time = str(signals['runner_asof_time'] or '')
    one_lot_cost = safe_float(signals['one_lot_cost'])
    if one_lot_cost is None and price is not None:
        one_lot_cost = price * 100
    one_lot_cap = safe_float(signals['one_lot_cost_cap'])
    risk_penalty = safe_float(signals['risk_penalty'])
    regulatory_block = str(signals['regulatory_hard_block'] or '')
    opportunity_block = str(signals['opportunity_hard_block'] or '')
    limitup_reason_strength = profile['limitup_reason_strength']
    seal_order_strength = profile['seal_order_strength']
    order_book_pressure = profile['order_book_pressure']
    replay_only_sector = replay_only_sector_opportunity(profile, row)
    raw_sector_opportunity_score = profile['sector_opportunity_score'] if profile['sector_opportunity_score'] is not None else 0.0
    sector_opportunity_score = 0.0 if replay_only_sector else raw_sector_opportunity_score
    sector_opportunity_tags = profile['sector_opportunity_tags']
    main_theme_alignment_score = profile.get('main_theme_alignment_score') or 0.0
    main_theme_core_score = profile.get('main_theme_core_score') or 0.0
    fund_flow_momentum = profile['fund_flow_momentum']
    time_series_momentum = profile['time_series_momentum']
    candidate_stage = profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct'])
    early_opportunity_score = profile['early_opportunity_score'] if profile['early_opportunity_score'] is not None else early_opportunity_score_for_row(row, bundle)
    research_signals = profile.get('research_signals') if isinstance(profile.get('research_signals'), dict) else {}
    research_panel = research_signals.get('research_panel') if isinstance(research_signals.get('research_panel'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    a_share_risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    adversarial_review = research_signals.get('adversarial_review') if isinstance(research_signals.get('adversarial_review'), dict) else {}
    catalyst_quality_category_global = str(catalyst_quality.get('category') or profile['catalyst_quality_category'] or '')
    research_layer = str(profile['search_layer_hint'] or profile['setup_type'] or '')
    blocked_reasons = [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)]
    near_limit_up_risk = bool(row.get('near_limit_up_risk')) or any(normalized_block_bucket(reason) == 'near_limit_up_risk' for reason in blocked_reasons)
    source_date = str(bundle.get('date') or row.get('date') or source_time[:10] or '')
    age_minutes = scan_age_minutes(source_time, source_date, runner_asof_time) if source_time and runner_asof_time and source_date else None
    time_gate_known = bool(source_time and runner_asof_time and source_date and age_minutes is not None)

    market_context = market_adaptive_context(row, bundle)
    market_regime = str(market_context.get('market_regime') or '')
    market_follow_through_score = safe_float(market_context.get('market_follow_through_score'))
    market_limitups = safe_float(market_context.get('market_limitups'))
    market_breadth_up_pct = safe_float(market_context.get('market_breadth_up_pct'))
    limitup_broken_ratio = safe_float(market_context.get('limitup_broken_ratio'))
    broken_limitups = safe_float(market_context.get('broken_limitups'))
    market_supports_high_chase = bool(market_context.get('supportive_market')) and not bool(market_context.get('weak_acceptance_market'))
    weak_acceptance_market = bool(market_context.get('weak_acceptance_market'))
    broken_limit_pressure = bool(market_context.get('broken_limit_pressure'))
    overheated_market = bool(market_context.get('overheated_market'))
    signals['market_regime'] = market_regime
    signals['market_follow_through_score'] = market_follow_through_score
    signals['limitup_broken_ratio'] = limitup_broken_ratio
    signals['broken_limitups'] = broken_limitups
    signals['market_supports_high_chase'] = market_supports_high_chase
    signals['weak_acceptance_market'] = weak_acceptance_market
    signals['broken_limit_pressure'] = broken_limit_pressure
    signals['overheated_market'] = overheated_market
    shadow_profile = shadow_risk_profile(row, bundle, profile)
    signals.update({
        'market_regime_risk': shadow_profile['market_regime_risk'],
        'weak_market': shadow_profile['weak_market'],
        'market_regime_risk_reason': shadow_profile['market_regime_risk_reason'],
        'chase_high_risk': shadow_profile['chase_high_risk'],
        'chase_high_shadow_penalty': shadow_profile['chase_high_shadow_penalty'],
        'chase_high_reason': shadow_profile['chase_high_reason'],
        'defensive_carry_score': shadow_profile['defensive_carry_score'],
        'defensive_reason': shadow_profile['defensive_reason'],
        'limitup_gene_strength': shadow_profile['limitup_gene_strength'],
        'limitup_gene_shadow_gate': shadow_profile['limitup_gene_shadow_gate'],
        'limitup_gene_block_reason': shadow_profile['limitup_gene_block_reason'],
        'social_confirmation': shadow_profile['social_confirmation'],
    })
    enhanced_counts = row.get('enhanced_evidence_domain_counts') if isinstance(row.get('enhanced_evidence_domain_counts'), dict) else {}
    limitup_context_present = (safe_float(enhanced_counts.get('limitup_context')) or 0.0) > 0.0
    research_panel_overall = str(research_panel.get('overall') or signals.get('research_panel_overall') or '')
    setup_type_signal = str(signals.get('setup_type') or profile.get('setup_type') or row.get('setup_type') or '')
    sector_follower_diagnostic_only = bool(
        str(profile.get('search_layer_hint') or '') == 'sector_follower'
        or str(row.get('search_layer') or '') == 'sector_follower'
        or setup_type_signal == 'SECTOR_FOLLOWER'
    )
    signals['sector_follower_diagnostic_only'] = sector_follower_diagnostic_only
    if sector_follower_diagnostic_only:
        blockers.append('sector_follower_diagnostic_only')
        missing_conditions.append('official_layer_not_sector_follower')
    continuation_gene_score = safe_float(signals.get('continuation_gene_score')) or 0.0
    limitup_capture_score_for_gap = safe_float(signals.get('limitup_capture_score')) or 0.0
    limitup_reason_status_for_gap = str(signals.get('limitup_reason_status') or '').upper()
    sector_catalyst_score_for_gap = safe_float(signals.get('sector_catalyst_score')) or 0.0
    topic_propagation_score_for_gap = safe_float(signals.get('topic_propagation_score')) or 0.0
    sector_news_strength_for_gap = safe_float(signals.get('sector_news_strength')) or 0.0
    news_catalyst_strength_for_gap = safe_float(signals.get('news_catalyst_strength')) or 0.0
    announcement_catalyst_score_for_gap = safe_float(signals.get('announcement_catalyst_score')) or 0.0
    limitup_reason_quality_score_for_gap = safe_float(signals.get('limitup_reason_quality_score')) or 0.0
    low_position_catalyst_score_for_gap = safe_float(signals.get('low_position_catalyst_score')) or 0.0
    amount_for_gap = safe_float(row.get('amount')) or safe_float(row.get('成交额')) or 0.0
    net_inflow_for_gap = safe_float(row.get('net_inflow_main')) or 0.0
    if net_inflow_for_gap <= 0:
        capital_flow_for_gap = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
        net_inflow_for_gap = safe_float(capital_flow_for_gap.get('main_force_net_inflow')) or 0.0
    amount_pctile_for_gap = max(
        safe_float(row.get('full_universe_amount_pctile')) or 0.0,
        safe_float(row.get('amount_pctile_rule')) or 0.0,
    )
    fund_pctile_for_gap = max(
        safe_float(row.get('full_universe_fund_pctile')) or 0.0,
        safe_float(row.get('fund_pctile_rule')) or 0.0,
    )
    turnover_rate_for_gap = safe_float(row.get('turnover_rate')) or 0.0
    amplitude_for_gap = safe_float(row.get('amplitude')) or 0.0
    in_limitup_pool_for_gap = bool(
        row.get('sealed_limit_up')
        or row.get('is_limit_up')
        or row.get('is_limitup')
        or row.get('limit_up')
        or row.get('limitup_pool_member')
    )
    consecutive_limit_for_gap = max(
        safe_float(row.get('consecutive_limit_count')) or 0.0,
        safe_float(row.get('consecutive_limit_days')) or 0.0,
        safe_float(row.get('consecutive_limitups')) or 0.0,
    )
    weak_limitup_reason_status = limitup_reason_status_for_gap in ('', 'MISSING', 'PARTIAL', 'PARTIAL_OR_FAIL', 'FAIL', 'FAILED', 'WEAK', 'UNKNOWN', 'NONE')
    has_direct_gap_confirmation = bool(
        limitup_context_present
        or signals.get('limitup_capture_confirmed')
        or continuation_gene_score > 0.0
        or in_limitup_pool_for_gap
        or consecutive_limit_for_gap > 0.0
        or limitup_reason_status_for_gap in ('PASS', 'OK', 'CONFIRMED', 'PROXY')
        or research_panel_overall == 'PASS'
        or max(
            announcement_catalyst_score_for_gap,
            news_catalyst_strength_for_gap,
            limitup_reason_quality_score_for_gap,
        ) >= 0.75
    )
    strong_money_confirmation = bool(
        (amount_for_gap >= 1_000_000_000 and net_inflow_for_gap >= 50_000_000)
        or (amount_pctile_for_gap >= 0.90 and fund_pctile_for_gap >= 0.80 and net_inflow_for_gap >= 30_000_000)
    )
    low_position_sector_news_confirmation = bool(
        low_position_catalyst_score_for_gap >= 0.60
        and max(
            sector_opportunity_score,
            sector_catalyst_score_for_gap,
            sector_news_strength_for_gap,
            news_catalyst_strength_for_gap,
            main_theme_alignment_score,
            main_theme_core_score,
        ) >= 0.45
    )
    early_sector_confirmation_gap = bool(
        sector_opportunity_score <= 0.0
        or (
            sector_opportunity_score < 0.80
            and sector_catalyst_score_for_gap <= 0.0
            and topic_propagation_score_for_gap < 0.30
        )
    )
    early_market_pressure = bool(
        weak_acceptance_market
        or broken_limit_pressure
        or market_regime.lower() == 'weak'
        or (limitup_broken_ratio is not None and limitup_broken_ratio >= 0.70)
    )
    early_hot_momentum_missing_evidence = bool(
        early_market_pressure
        and candidate_stage == 'early_3_to_5'
        and limitup_capture_score_for_gap <= 0.0
        and weak_limitup_reason_status
        and early_sector_confirmation_gap
        and sector_catalyst_score_for_gap <= 0.0
        and topic_propagation_score_for_gap < 0.30
        and not has_direct_gap_confirmation
        and not strong_money_confirmation
        and not low_position_sector_news_confirmation
        and (turnover_rate_for_gap >= 10.0 or amplitude_for_gap >= 7.0)
    )
    weak_market_hot_momentum_evidence_gap = bool(
        setup_type_signal in ('HOT_MOMENTUM', 'L1_HOT_MOMENTUM')
        and (
            (
                (weak_acceptance_market or broken_limit_pressure)
                and candidate_stage in ('mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
                and not limitup_context_present
                and not signals.get('limitup_capture_confirmed')
                and continuation_gene_score <= 0.0
                and str(signals.get('mainboard_auxiliary_evidence_status') or '') in ('', 'PARTIAL', 'MISSING', 'PARTIAL_OR_FAIL')
                and research_panel_overall != 'PASS'
            )
            or early_hot_momentum_missing_evidence
        )
    )
    signals['limitup_context_present'] = limitup_context_present
    signals['weak_market_hot_momentum_evidence_gap'] = weak_market_hot_momentum_evidence_gap
    signals['early_hot_momentum_missing_evidence'] = early_hot_momentum_missing_evidence
    signals['early_hot_momentum_market_pressure'] = early_market_pressure
    signals['early_hot_momentum_sector_confirmation_gap'] = early_sector_confirmation_gap
    signals['hot_momentum_real_confirmation_escape'] = bool(
        has_direct_gap_confirmation
        or strong_money_confirmation
        or low_position_sector_news_confirmation
    )

    signal_pct = profile['signal_pct']
    adaptive_thresholds = market_adaptive_thresholds(candidate_stage, market_context)
    buy_confirmation_min = adaptive_thresholds['buy_confirmation_min']
    order_book_confirmation_min = adaptive_thresholds['order_book_min']
    stock_level_limitup_expectation_pass = False
    strong_high_momentum_continuation_pass = False

    if data_gate_status in ('PASS', 'OK', True):
        positive_conditions.append('data_gate_status=PASS')
    else:
        blockers.append('data_gate_status!=PASS')
        missing_conditions.append('data_gate_status=PASS')

    if candidate_evidence_status == 'PASS':
        positive_conditions.append('candidate_evidence_status=PASS')
    else:
        positive_conditions.append('candidate_evidence_status!=PASS')
        missing_conditions.append('candidate_evidence_status=PASS')

    if time_gate_known:
        if age_minutes is not None and age_minutes >= 0:
            positive_conditions.append('source_time<=asof_time')
        else:
            blockers.append('source_time>asof_time')
            missing_conditions.append('source_time<=asof_time')
    else:
        missing_conditions.append('source_time<=asof_time')

    if one_lot_cost is None or one_lot_cost <= 0:
        blockers.append('one_lot_cost_invalid')
        missing_conditions.append('one_lot_cost_valid')
    else:
        positive_conditions.append('one_lot_cost_valid')
        if one_lot_cap is not None and one_lot_cost <= one_lot_cap:
            positive_conditions.append('one_lot_cost<=cap')
        elif one_lot_cap is not None:
            blockers.append('one_lot_cost>cap')
            missing_conditions.append('one_lot_cost<=cap')

    if risk_penalty is None or risk_penalty == 0:
        positive_conditions.append('risk_penalty=0')
    else:
        blockers.append('risk_penalty!=0')
        missing_conditions.append('risk_penalty=0')

    if regulatory_block:
        # Routine blocks (异常波动/风险提示) can be bypassed by strong momentum
        if is_routine_regulatory_block(regulatory_block):
            _close_pos = profile['close_position_score'] or safe_float(row.get('close_position_score'))
            _capture_profile = str(profile.get('limitup_capture_profile') or '')
            _capture_score = safe_float(profile.get('limitup_capture_score')) or 0.0
            has_strong_momentum = (
                (limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min)
                or (seal_order_strength is not None and seal_order_strength >= buy_confirmation_min)
                or (_close_pos is not None and _close_pos >= max(0.70, adaptive_thresholds['dynamic_close_position_min']))
                or (_capture_profile == 'STRONG_LIMITUP_CAPTURE' and _capture_score >= 0.50)
                or (signal_pct is not None and signal_pct >= 5.0 and not weak_acceptance_market)
                or (fund_flow_momentum is not None and fund_flow_momentum >= max(0.3, adaptive_thresholds['dynamic_fund_flow_min'] - 0.1))
            )
            if has_strong_momentum:
                positive_conditions.append('routine_regulatory_bypassed_by_momentum')
            else:
                blockers.append('regulatory_soft_block:' + regulatory_block)
                missing_conditions.append('no_regulatory_hard_block_or_strong_momentum')
        else:
            blockers.append('regulatory_hard_block:' + regulatory_block)
            missing_conditions.append('no_regulatory_hard_block')
    else:
        positive_conditions.append('no_regulatory_hard_block')

    near_limit_l2_confirmed = False
    near_limit_gate_blocked = near_limit_up_risk
    if near_limit_up_risk:
        # L2 exemption: near_limit + L2_LIMIT_STRENGTH confirmed = positive momentum, not a blocker
        _source_layers = list(row.get('source_layers') or [])
        _l2_in_layers = 'L2_LIMIT_STRENGTH' in _source_layers
        _l2_by_strength = (limitup_reason_strength is not None and limitup_reason_strength >= 0.60)
        _near_limit_l2_exemption = str(
            (scoring_config_values or {}).get('near_limit_l2_exemption') or
            SCORING_CONFIG_DEFAULTS.get('near_limit_l2_exemption') or ''
        ).lower() == 'true'
        if _near_limit_l2_exemption and (_l2_in_layers or _l2_by_strength):
            near_limit_l2_confirmed = True
            near_limit_gate_blocked = False
            positive_conditions.append('near_limit_with_L2_confirmation')
        else:
            blockers.append('near_limit_up_risk')
            missing_conditions.append('no_near_limit_up_risk')
    else:
        positive_conditions.append('no_near_limit_up_risk')
    signals['near_limit_with_l2_confirmation'] = near_limit_l2_confirmed

    weekday_blocklist_raw = str(signals['weekday_blocklist'] or '')
    if weekday_blocklist_raw and source_date:
        try:
            blocked_days = [int(d.strip()) for d in weekday_blocklist_raw.split(',') if d.strip()]
            weekday = dt.date.fromisoformat(source_date).weekday()
            if weekday in blocked_days:
                blockers.append('WEEKDAY_SOFT_BLOCKED')
                missing_conditions.append(f'weekday_not_in_{blocked_days}')
        except Exception:
            blockers.append('WEEKDAY_BLOCKLIST_INVALID_SOFT')
            missing_conditions.append('weekday_blocklist_valid')

    if evidence_missing_flags:
        positive_conditions.append('evidence_coverage_partial')
        missing_conditions.append('evidence_coverage_complete')
        signals['evidence_missing_flags'] = evidence_missing_flags
    if candidate_evidence_flags:
        positive_conditions.append('candidate_evidence_partial')
        missing_conditions.append('candidate_evidence_complete')
        signals['candidate_evidence_flags'] = candidate_evidence_flags
    if candidate_lifecycle.get('setup_class') == 'STALE_REPEAT' and not (
        (profile.get('limitup_capture_confirmed') or False)
        or (profile.get('seal_order_strength') or 0.0) >= 0.60
        or (profile.get('order_book_pressure') or 0.0) >= 0.50
    ):
        blockers.append('candidate_lifecycle_stale_repeat')
        missing_conditions.append('candidate_lifecycle_non_stale')

    buy_confirmation_hits: List[str] = []
    if limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min:
        positive_conditions.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
        buy_confirmation_hits.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
    if seal_order_strength is not None and seal_order_strength >= buy_confirmation_min:
        positive_conditions.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
        buy_confirmation_hits.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
    if order_book_pressure is not None and order_book_pressure >= order_book_confirmation_min:
        positive_conditions.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
        buy_confirmation_hits.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
    if candidate_lifecycle.get('setup_class') == 'INSTANT_MOMENTUM_SETUP':
        positive_conditions.append('candidate_lifecycle=INSTANT_MOMENTUM_SETUP')
    elif candidate_lifecycle.get('setup_class') == 'DELAYED_SETUP':
        positive_conditions.append('candidate_lifecycle=DELAYED_SETUP')
        if candidate_lifecycle.get('repeat_count'):
            positive_conditions.append(f"candidate_lifecycle_repeat_count>={candidate_lifecycle.get('repeat_count')}")

    sector_gate_pass = False
    all_sector_tags = list(sector_opportunity_tags) + list(row.get('sector_opportunity_tags') or [])
    has_real_sector_tag = bool([t for t in all_sector_tags if not str(t).startswith('REPLAY_')])
    if replay_only_sector and not has_real_sector_tag:
        blockers.append('replay_only_sector_opportunity_not_official_gate')
        missing_conditions.append('live_sector_or_limitup_confirmation')
    sector_gate_threshold = sector_gate_threshold_for_market(market_context)
    if sector_opportunity_score >= sector_gate_threshold:
        positive_conditions.append(f'sector_opportunity_score>={sector_gate_threshold}')
        sector_gate_pass = True
    if (not replay_only_sector) and 'SECTOR_OPPORTUNITY' in sector_opportunity_tags:
        positive_conditions.append('vei_phase_d_tags includes SECTOR_OPPORTUNITY')
        sector_gate_pass = True
    if (not replay_only_sector) and 'SECTOR_OPPORTUNITY' in profile['vei_phase_d_tags']:
        positive_conditions.append('vei_phase_d_tags includes SECTOR_OPPORTUNITY')
        sector_gate_pass = True
    if main_theme_core_score >= 0.6:
        positive_conditions.append('main_theme_core_score>=0.6')
        sector_gate_pass = True
    elif main_theme_alignment_score >= 0.5 and fund_flow_momentum is not None and fund_flow_momentum >= 0.5:
        positive_conditions.append('main_theme_alignment_score>=0.5_with_flow')
        sector_gate_pass = True
    component_details = profile['structured_component_details'] if isinstance(profile['structured_component_details'], dict) else {}
    weak_to_strong_reversal = safe_float(component_details.get('weak_to_strong_reversal'))
    first_board_pre_signal = safe_float(component_details.get('first_board_pre_signal'))
    pre_limitup_anomaly = safe_float(component_details.get('pre_limitup_anomaly'))
    limitup_capture_score = profile['limitup_capture_score'] or safe_float(component_details.get('limitup_capture_score')) or 0.0
    limitup_capture_profile = profile['limitup_capture_profile'] or str(component_details.get('limitup_capture_profile') or '')
    limitup_capture_reasons = profile['limitup_capture_reasons'] or normalize_tag_list(component_details.get('limitup_capture_reasons') or [])
    close_position_score = profile['close_position_score'] or 0.0
    limitup_capture_confirmation_pass = False
    strong_vei_stage = candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
    strong_vei_momentum = (
        early_opportunity_score is not None
        and early_opportunity_score >= 0.65
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and time_series_momentum is not None
        and time_series_momentum > 0
    )
    if strong_vei_stage and strong_vei_momentum:
        if weak_to_strong_reversal is not None and weak_to_strong_reversal >= 0.75:
            positive_conditions.append('vei_strong_signal:weak_to_strong_reversal>=0.75')
            sector_gate_pass = True
        if first_board_pre_signal is not None and first_board_pre_signal >= 0.80:
            positive_conditions.append('vei_strong_signal:first_board_pre_signal>=0.80')
            sector_gate_pass = True
        if vei_repo_score_delta is not None and vei_repo_score_delta >= 1.0:
            positive_conditions.append('vei_strong_signal:integrated_vei_repo_delta>=1.0')
            sector_gate_pass = True
    if (
        limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE'
        and limitup_capture_score >= 0.62
        and close_position_score >= 0.70
        and (pre_limitup_anomaly or 0.0) >= 0.55
        and (profile['limitup_reason_propagation_score'] or 0.0) >= 0.60
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and not near_limit_gate_blocked
        and not regulatory_block
    ):
        limitup_capture_confirmation_pass = True
        positive_conditions.append('limitup_capture_confirmation_pass')
        positive_conditions.append(f'limitup_capture_profile={limitup_capture_profile}')
        positive_conditions.append(f'limitup_capture_score={limitup_capture_score:.4f}')
        for reason in limitup_capture_reasons:
            positive_conditions.append('limitup_capture:' + str(reason))

    dynamic_confirmation_hits: List[str] = []
    if limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min:
        dynamic_confirmation_hits.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
    if seal_order_strength is not None and seal_order_strength >= buy_confirmation_min:
        dynamic_confirmation_hits.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
    if order_book_pressure is not None and order_book_pressure >= order_book_confirmation_min:
        dynamic_confirmation_hits.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
    if limitup_capture_confirmation_pass:
        dynamic_confirmation_hits.append('limitup_capture_confirmation_pass')
    if (profile['intraday_alert_strength'] or 0.0) >= 0.90:
        dynamic_confirmation_hits.append('intraday_alert_strength>=0.9')
    if near_limit_l2_confirmed:
        dynamic_confirmation_hits.append('near_limit_with_L2_confirmation')
    if main_theme_core_score >= 0.60 or main_theme_alignment_score >= 0.55:
        dynamic_confirmation_hits.append('theme_alignment_confirmation')

    dynamic_required_confirmations = int(adaptive_thresholds['dynamic_required_confirmations'])
    dynamic_signal_confirmation_pass = (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and close_position_score is not None
        and close_position_score >= adaptive_thresholds['dynamic_close_position_min']
        and fund_flow_momentum is not None
        and fund_flow_momentum >= adaptive_thresholds['dynamic_fund_flow_min']
        and time_series_momentum is not None
        and time_series_momentum >= adaptive_thresholds['dynamic_time_series_min']
        and len(dynamic_confirmation_hits) >= dynamic_required_confirmations
        and not regulatory_block
        and not near_limit_gate_blocked
    )
    signals['dynamic_confirmation_hits'] = dynamic_confirmation_hits
    signals['dynamic_required_confirmations'] = dynamic_required_confirmations
    signals['dynamic_signal_confirmation_pass'] = dynamic_signal_confirmation_pass
    if dynamic_signal_confirmation_pass:
        positive_conditions.append('dynamic_signal_confirmation_pass')

    strong_high_momentum_continuation_pass = (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and bool(signals.get('dynamic_signal_confirmation_pass'))
        and not weak_market_hot_momentum_evidence_gap
        and (
            (market_supports_high_chase and not weak_acceptance_market)
            or limitup_capture_confirmation_pass
        )
    )
    if strong_high_momentum_continuation_pass:
        positive_conditions.append('strong_high_momentum_continuation_pass')
        buy_confirmation_hits.append('strong_high_momentum_continuation_pass')
    if strong_high_momentum_continuation_pass:
        sector_gate_pass = True
        positive_conditions.append('high_momentum_continuation_bypasses_sector_vei_gate')
    elif (
        candidate_stage in ('mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
        and close_position_score >= 0.65
        and ((fund_flow_momentum is not None and fund_flow_momentum >= 0.5) or (profile['intraday_alert_strength'] or 0.0) >= 0.95)
        and ((profile['intraday_alert_strength'] or 0.0) >= 0.9 or (limitup_reason_strength or 0.0) >= 0.6)
        and ((main_theme_alignment_score or 0.0) >= 0.5 or candidate_stage == 'high_7_to_9')
        and bool(buy_confirmation_hits)
    ):
        sector_gate_pass = True
        positive_conditions.append('strong_continuation_bypasses_sector_vei_gate')
    elif (
        candidate_stage in ('flat_0_to_3', 'mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
        and close_position_score >= 0.75
        and fund_flow_momentum is not None and fund_flow_momentum >= 0.6
        and (time_series_momentum is None or time_series_momentum >= 0.15)
        and bool(buy_confirmation_hits)
        and (main_theme_core_score >= 0.4 or main_theme_alignment_score >= 0.3)
    ):
        sector_gate_pass = True
        positive_conditions.append('strong_stock_priority_bypasses_sector_vei_gate')
    if not (sector_gate_pass or limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
        missing_conditions.append(f'sector_opportunity_score>={sector_gate_threshold} or VEI strong signal')

    if research_layer in ('news_catalyst_low_position', 'sector_catalyst_low_position', 'TOPIC_FUND_IGNITION'):
        catalyst_quality_category = str(catalyst_quality.get('category') or profile['catalyst_quality_category'] or '')
        if catalyst_quality_category in ('risk_notice', 'regulatory_notice'):
            blockers.append('research_catalyst_quality:' + catalyst_quality_category)
            missing_conditions.append('research_catalyst_quality_not_risk_notice')
        elif catalyst_quality_category:
            positive_conditions.append('research_catalyst_quality=' + catalyst_quality_category)
        if a_share_risk_review.get('disqualified_for_paper_pick'):
            blockers.append('a_share_risk_review_soft_disqualified')
            missing_conditions.append('a_share_risk_review_clean')
        if research_panel.get('overall') == 'FAIL':
            blockers.append('research_panel_overall_SOFT_FAIL')
            missing_conditions.append('research_panel_overall!=FAIL')
        elif research_panel.get('overall') in ('PASS', 'PARTIAL'):
            positive_conditions.append('research_panel_overall=' + str(research_panel.get('overall')))
        if adversarial_review.get('disqualifying_flags'):
            for flag in adversarial_review.get('disqualifying_flags', []):
                blockers.append('adversarial_review_soft:' + str(flag))
            missing_conditions.append('adversarial_review_clean')

    if fund_flow_momentum is not None and fund_flow_momentum > 0:
        positive_conditions.append('fund_flow_momentum>0')
    if time_series_momentum is not None and time_series_momentum > 0:
        positive_conditions.append('time_series_momentum>0')
    if candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        positive_conditions.append('candidate_stage=' + candidate_stage)
    if early_opportunity_score is not None and early_opportunity_score >= 0.65 and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        positive_conditions.append('early_opportunity_score>=0.65')
        buy_confirmation_hits.append('early_opportunity_score>=0.65')
    if (
        profile['low_position_catalyst_score'] is not None
        and profile['low_position_catalyst_score'] >= 0.60
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
    ):
        positive_conditions.append('low_position_catalyst_score>=0.6')
        buy_confirmation_hits.append('low_position_catalyst_score>=0.6')

    capital_flow = row.get('data_directory_capital_flow') or {}
    capital_flow_net = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
    if capital_flow_net > 0:
        positive_conditions.append('data_directory_capital_flow>0')
    score_value = candidate_score_value(row) or 0.0
    code_text = str(row.get('code') or row.get('symbol') or '')
    inferred_board = 'chinext' if code_text.startswith(('300', '301')) else ('main' if code_text.startswith(('600', '601', '603', '605', '000', '001', '002', '003')) else '')
    board = str(row.get('board') or inferred_board or '')
    signal_pct_value = signal_pct or 0.0
    stock_level_limitup_expectation_pass = (
        board == 'chinext'
        and candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and score_value >= 90.0
        and signal_pct_value >= 12.0
        and close_position_score >= 0.75
        and fund_flow_momentum is not None and fund_flow_momentum >= 0.75
        and time_series_momentum is not None and time_series_momentum >= 0.45
        and (order_book_pressure or 0.0) >= 0.45
        and capital_flow_net >= 50000000
        and sector_gate_pass
        and not regulatory_block
        and candidate_evidence_status == 'PASS'
    )

    if stock_level_limitup_expectation_pass:
        signals['stock_level_limitup_expectation_pass'] = True
        positive_conditions.append('stock_level_limitup_expectation_pass')
        positive_conditions.append('stock_level_confirmation:data_directory_capital_flow>=5000w')
        buy_confirmation_hits.append('stock_level_limitup_expectation_pass')

    if weak_market_hot_momentum_evidence_gap:
        blockers.append('weak_market_hot_momentum_without_d1_continuation_evidence')
        missing_conditions.extend([
            'limitup_context_present',
            'limitup_capture_confirmed',
            'continuation_gene_score>0',
            'mainboard_auxiliary_evidence_status=PASS',
            'research_panel_overall=PASS',
        ])

    direct_catalyst_confirmation = max(
        safe_float(signals.get('announcement_catalyst_score')) or 0.0,
        safe_float(signals.get('news_catalyst_strength')) or 0.0,
        safe_float(signals.get('limitup_reason_quality_score')) or 0.0,
    ) >= 0.75
    weak_market_requires_direct_confirmation = (
        (weak_acceptance_market or broken_limit_pressure)
        and research_panel_overall in ('', 'MISSING', 'PARTIAL')
        and not direct_catalyst_confirmation
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
        and not stock_level_limitup_expectation_pass
        and (safe_float(signals.get('continuation_gene_score')) or 0.0) < 0.70
    )
    signals['direct_catalyst_confirmation'] = direct_catalyst_confirmation
    signals['sector_gate_pass'] = sector_gate_pass
    signals['weak_market_requires_direct_confirmation'] = weak_market_requires_direct_confirmation
    strong_sector_theme_partial_aux_exception = bool(
        board == 'main'
        and auxiliary_status_normalized == 'PARTIAL'
        and research_panel_overall in ('PARTIAL', 'PASS')
        and (
            sector_gate_pass
            or main_theme_core_score >= 0.70
            or main_theme_alignment_score >= 0.70
            or sector_catalyst_score_for_gap >= 0.75
            or topic_propagation_score_for_gap >= 0.75
        )
        and not near_limit_up_risk
        and not regulatory_block
        and not opportunity_block
        and not capital_risk_profile.get('risk_codes')
    )
    signals['strong_sector_theme_partial_aux_exception'] = strong_sector_theme_partial_aux_exception
    strong_official_exception = bool(
        limitup_capture_confirmation_pass
        or strong_high_momentum_continuation_pass
        or stock_level_limitup_expectation_pass
        or strong_sector_theme_partial_aux_exception
    )
    weak_low_confidence_mainboard = bool(
        weak_acceptance_market
        or broken_limit_pressure
        or score_value < 70.0
        or research_panel_overall in ('', 'MISSING', 'PARTIAL')
        or not direct_catalyst_confirmation
        or candidate_stage in ('early_3_to_5', 'mid_5_to_7')
    )
    mainboard_auxiliary_hard_block = bool(
        board == 'main'
        and auxiliary_status_normalized not in ('PASS', 'OK')
        and weak_low_confidence_mainboard
        and not strong_official_exception
    )
    signals['mainboard_auxiliary_evidence_hard_block'] = mainboard_auxiliary_hard_block
    if strong_sector_theme_partial_aux_exception:
        positive_conditions.append('strong_sector_theme_partial_aux_exception')
    if mainboard_auxiliary_hard_block:
        blockers.append('mainboard_auxiliary_evidence_status_not_PASS')
        missing_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    low_score_without_direct_catalyst_confirmation = bool(
        score_value < 70.0
        and not direct_catalyst_confirmation
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
        and not stock_level_limitup_expectation_pass
        and research_panel_overall != 'PASS'
    )
    signals['low_score_without_direct_catalyst_confirmation'] = low_score_without_direct_catalyst_confirmation
    if low_score_without_direct_catalyst_confirmation:
        blockers.append('low_score_without_direct_catalyst_confirmation')
        missing_conditions.append('score>=70_or_direct_catalyst_confirmation')
    if weak_market_requires_direct_confirmation:
        blockers.append('weak_market_requires_direct_confirmation')
        missing_conditions.append(
            'direct_catalyst_limitup_research_PASS_or_sector_confirmation'
        )

    is_high_position = candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
    if is_high_position and weak_acceptance_market and not (
        strong_high_momentum_continuation_pass
        or stock_level_limitup_expectation_pass
    ):
        blockers.append('broken_limit_weak_feedback_high_chase_soft' if broken_limit_pressure else 'weak_acceptance_market_high_chase_soft')
        missing_conditions.append('weak_market_requires_stronger_high_chase_confirmation')

    raw_research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    raw_research_panel = raw_research_signals.get('research_panel') if isinstance(raw_research_signals.get('research_panel'), dict) else {}
    raw_research_panel_missing = bool(raw_research_signals and not raw_research_panel.get('overall'))
    weak_underwater_without_confirmation = (
        candidate_stage == 'underwater'
        and signal_pct is not None
        and signal_pct <= 0
        and (research_panel.get('overall') == 'FAIL' or raw_research_panel_missing)
        and catalyst_quality_category_global in ('', 'neutral')
        and not limitup_capture_confirmation_pass
        and not stock_level_limitup_expectation_pass
    )
    if weak_underwater_without_confirmation:
        blockers.append('weak_underwater_without_forward_confirmation')
        missing_conditions.append('limitup_or_catalyst_confirmation_for_underwater_candidate')

    if buy_confirmation_hits:
        positive_conditions.append('buy_confirmation>=0.6')
    else:
        positive_conditions.append('buy_confirmation_below_threshold')
        missing_conditions.append('buy_confirmation>=0.6')
        if opportunity_block == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION':
            blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')

    underwater_reversal_confirmation_pass = False
    if (
        data_gate_status in ('PASS', 'OK', True)
        and candidate_evidence_status == 'PASS'
        and time_gate_known
        and age_minutes is not None
        and age_minutes >= 0
        and one_lot_cost is not None
        and one_lot_cost > 0
        and one_lot_cap is not None
        and one_lot_cost <= one_lot_cap
        and (risk_penalty in (None, 0))
        and not regulatory_block
        and not near_limit_up_risk
        and not evidence_missing_flags
        and not candidate_evidence_flags
        and not opportunity_block
        and not blockers
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
        and (profile['search_layer_hint'] == 'underwater_reversal' or profile['setup_type'] == 'UNDERWATER_TO_RED_STRENGTH')
        and early_opportunity_score is not None
        and early_opportunity_score >= 0.65
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and time_series_momentum is not None
        and time_series_momentum > 0
        and bool(buy_confirmation_hits)
    ):
        underwater_reversal_confirmation_pass = True
        positive_conditions.append('underwater_reversal_confirmation_pass')
        missing_conditions = [item for item in missing_conditions if not (item.startswith('sector_opportunity_score>=') and 'VEI strong signal' in item)]

    if opportunity_block and opportunity_block != 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION':
        blockers.append(opportunity_block)

    # Keep blockers deduplicated and preserve first-seen order.
    unique_blockers: List[str] = []
    seen_blockers = set()
    for blocker in blockers:
        if blocker not in seen_blockers:
            unique_blockers.append(blocker)
            seen_blockers.add(blocker)

    unique_positive: List[str] = []
    seen_positive = set()
    for condition in positive_conditions:
        if condition not in seen_positive:
            unique_positive.append(condition)
            seen_positive.add(condition)

    unique_missing: List[str] = []
    seen_missing = set()
    for condition in missing_conditions:
        if condition not in seen_missing:
            unique_missing.append(condition)
            seen_missing.add(condition)

    eligible = not unique_blockers and data_gate_status in ('PASS', 'OK', True) and (sector_gate_pass or underwater_reversal_confirmation_pass or limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass)
    if one_lot_cost is not None and one_lot_cost > 0 and one_lot_cap is not None and one_lot_cost > one_lot_cap:
        eligible = False
    if risk_penalty not in (None, 0):
        eligible = False
    if regulatory_block:
        # Routine blocks can be bypassed by strong momentum
        if is_routine_regulatory_block(regulatory_block):
            has_strong_momentum = (
                (limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min)
                or (seal_order_strength is not None and seal_order_strength >= buy_confirmation_min)
                or (close_position_score is not None and close_position_score >= max(0.82, adaptive_thresholds['dynamic_close_position_min']))
                or (limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE' and limitup_capture_score >= 0.62)
            )
            if not has_strong_momentum:
                eligible = False
        else:
            eligible = False
    if near_limit_gate_blocked:
        eligible = False
    if opportunity_block == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION':
        if stock_level_limitup_expectation_pass:
            positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_STOCK_LEVEL_LIMITUP_EXPECTATION')
        elif dynamic_signal_confirmation_pass and market_supports_high_chase and not weak_acceptance_market:
            positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_DYNAMIC_MARKET_CONFIRMATION')
        else:
            sector_opp = safe_float(row.get('sector_opportunity_score')) or 0.0
            fund_mom = safe_float(fund_flow_momentum) or 0.0
            if sector_opp >= 1.5 or fund_mom >= 0.8:
                positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_SECTOR_OR_FUND_FLOW')
            else:
                blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')
    if time_gate_known and age_minutes is not None and age_minutes < 0:
        eligible = False

    # Minimum factor requirements - prevent blind selection
    setup_type = str(profile.get('setup_type') or '')
    has_valid_setup = bool(setup_type and setup_type not in ('', 'NONE', 'UNKNOWN'))
    has_close_position = close_position_score is not None and close_position_score > 0
    has_signal_pct = signal_pct is not None and signal_pct > 0
    
    # Require at least setup_type OR close_position_score for eligibility
    if not has_valid_setup and not has_close_position:
        blockers.append('missing_critical_factors')
        missing_conditions.append('setup_type_or_close_position_required')
        eligible = False
    
    # Minimum score threshold - prevent very low score picks
    min_score_threshold = 50.0
    if score_value < min_score_threshold:
        blockers.append(f'score_below_minimum:{score_value:.1f}')
        missing_conditions.append(f'score>={min_score_threshold}')
        eligible = False

    capital_flow = row.get('data_directory_capital_flow') or {}
    capital_flow_net = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
    if capital_flow_net < -50000000:
        blockers.append('main_force_heavy_sell_soft')
        missing_conditions.append('main_force_not_heavy_sell')
        eligible = False

    market_snapshot = bundle.get('market_snapshot') or {}
    market_limitups = safe_float(market_snapshot.get('market_limitups')) or safe_float(bundle.get('market_limitups')) or 0.0
    market_breadth_up_pct = safe_float(market_snapshot.get('market_breadth_up_pct')) or safe_float(bundle.get('market_breadth_up_pct')) or 0.0
    is_high_position = candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
    if overheated_market:
        if is_high_position and not (limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
            blockers.append('overheated_market_no_strong_confirmation_soft')
            missing_conditions.append('overheated_market_requires_strong_confirmation')
            eligible = False

    research_signals_local = profile.get('research_signals') if isinstance(profile.get('research_signals'), dict) else {}
    catalyst_quality_local = research_signals_local.get('catalyst_quality') if isinstance(research_signals_local.get('catalyst_quality'), dict) else {}
    news_category = str(catalyst_quality_local.get('category') or '')
    if news_category in ('risk_notice', 'regulatory_notice'):
        if not (limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
            blockers.append('news_catalyst_risk_notice_soft')
            missing_conditions.append('news_catalyst_not_risk_notice')
            eligible = False

    lhb_rows = row.get('lhb_profiles') or []
    hot_money_buy = False
    for lhb in lhb_rows:
        seat_name = str(lhb.get('seat_name') or '')
        net_buy = safe_float(lhb.get('net_buy')) or 0.0
        if net_buy > 0 and ('游资' in seat_name or '营业部' in seat_name):
            hot_money_buy = True
            break
    if hot_money_buy and capital_flow_net < 0:
        if not (limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
            blockers.append('hot_money_buy_with_main_sell_soft')
            missing_conditions.append('no_hot_money_with_main_sell')
            eligible = False
    if underwater_reversal_confirmation_pass:
        signals['underwater_reversal_confirmation_pass'] = True
    if limitup_capture_confirmation_pass:
        signals['limitup_capture_confirmation_pass'] = True
    if strong_high_momentum_continuation_pass:
        signals['strong_high_momentum_continuation_pass'] = True

    for blocker in blockers:
        if blocker not in seen_blockers:
            unique_blockers.append(blocker)
            seen_blockers.add(blocker)
    for condition in positive_conditions:
        if condition not in seen_positive:
            unique_positive.append(condition)
            seen_positive.add(condition)
    for condition in missing_conditions:
        if condition not in seen_missing:
            unique_missing.append(condition)
            seen_missing.add(condition)

    return {
        'eligible': eligible,
        'blockers': unique_blockers,
        'positive_conditions': unique_positive,
        'missing_conditions': unique_missing,
        'signals': signals,
        'shadow_risk_profile': shadow_profile,
        'candidate_lifecycle': candidate_lifecycle,
        'setup_class': candidate_lifecycle.get('setup_class'),
        'setup_rank': candidate_lifecycle.get('setup_rank'),
        'stale_decay': candidate_lifecycle.get('stale_decay'),
        'repeat_count': candidate_lifecycle.get('repeat_count'),
        'lifecycle_score': candidate_lifecycle.get('lifecycle_score'),
    }


def official_target_exclusion_reasons(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> List[str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    row = row if isinstance(row, dict) else {}
    reasons: List[str] = []
    regulatory_block = regulatory_hard_block_reason(row, bundle)
    if regulatory_block:
        reasons.append('regulatory_hard_block:' + regulatory_block)

    eligibility = row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    risk_explanation_gate = eligibility.get('paper_pick_risk_explanation_gate') if isinstance(eligibility.get('paper_pick_risk_explanation_gate'), dict) else paper_pick_risk_explanation_gate(row)
    if risk_explanation_gate.get('status') == 'FAIL':
        reasons.append('PAPER_PICK_RISK_EXPLANATION_GATE_FAIL')
    signals = eligibility.get('signals') if isinstance(eligibility.get('signals'), dict) else {}
    profile = row
    if row.get('sector_opportunity_score') is None:
        profile = _cached_structured_signal_profile(row, bundle)
    market_context = market_adaptive_context(row, bundle)
    sector_gate_threshold = sector_gate_threshold_for_market(market_context)
    strong_sector = (profile.get('sector_opportunity_score') or 0.0) >= sector_gate_threshold and not replay_only_sector_opportunity(profile, row)
    strong_momentum_override = bool(
        signals.get('strong_high_momentum_continuation_pass')
        or signals.get('dynamic_signal_confirmation_pass')
        or signals.get('limitup_capture_confirmation_pass')
        or signals.get('stock_level_limitup_expectation_pass')
    )
    soft_exclusion_override = bool(eligibility.get('eligible')) and (strong_sector or strong_momentum_override)
    hard_diagnostic_blockers = {
        'sector_follower_diagnostic_only',
        'mainboard_auxiliary_evidence_status_not_PASS',
        'low_score_without_direct_catalyst_confirmation',
    }
    partial_aux_exception = bool(signals.get('strong_sector_theme_partial_aux_exception'))
    for blocker in [str(item) for item in (eligibility.get('blockers') or []) if item]:
        if blocker == 'mainboard_auxiliary_evidence_status_not_PASS' and partial_aux_exception:
            continue
        if blocker in hard_diagnostic_blockers:
            reasons.append(blocker)
        elif any(token in blocker for token in ('regulatory_hard_block', 'risk_too_high')):
            reasons.append(blocker)
        elif 'a_share_risk_review_soft_disqualified' in blocker and not soft_exclusion_override:
            reasons.append(blocker)
        elif 'research_panel_overall_SOFT_FAIL' in blocker and not soft_exclusion_override:
            reasons.append(blocker)
        elif blocker.startswith('adversarial_review_soft:') and not soft_exclusion_override:
            reasons.append(blocker)

    research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    catalyst_category = str(catalyst_quality.get('category') or '')
    if catalyst_category in ('risk_notice', 'regulatory_notice') and not (soft_exclusion_override and catalyst_category == 'risk_notice'):
        reasons.append('research_catalyst_quality:' + catalyst_category)
    a_share_risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    if a_share_risk_review.get('disqualified_for_paper_pick') and not soft_exclusion_override:
        reasons.append('a_share_risk_review_soft_disqualified')
    research_panel = research_signals.get('research_panel') if isinstance(research_signals.get('research_panel'), dict) else {}
    if research_panel.get('overall') == 'FAIL' and not soft_exclusion_override:
        reasons.append('research_panel_overall_SOFT_FAIL')
    adversarial_review = research_signals.get('adversarial_review') if isinstance(research_signals.get('adversarial_review'), dict) else {}
    if not soft_exclusion_override:
        for flag in [str(item) for item in (adversarial_review.get('disqualifying_flags') or []) if item]:
            reasons.append('adversarial_review_soft:' + flag)

    enhanced_missing = [str(item) for item in (row.get('enhanced_evidence_missing_domains') or []) if item]
    if 'candidate_fund_recheck' in enhanced_missing and not soft_exclusion_override:
        reasons.append('candidate_fund_recheck_missing')
    if any('fund_flow_conflict' in str(item) or 'weak_fund' in str(item) for item in (row.get('blocked_reasons') or [])) and not soft_exclusion_override:
        reasons.append('funding_quality_conflict')
    if any('WEEKDAY_BLOCKED' in str(item) for item in (row.get('blocked_reasons') or [])) and not soft_exclusion_override:
        reasons.append('weekday_blocked_soft')
    if (safe_float(row.get('risk_notice_penalty')) or 0.0) >= 0.60:
        for notice in row.get('risk_notice_evidence') or []:
            category = str(notice.get('category') or 'risk_notice')
            title = str(notice.get('title') or '')[:120]
            reasons.append(f'mainboard_auxiliary_risk:{category}:{title}')

    return unique_text_values(reasons)


def attach_paper_pick_eligibility(bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return bundle

    def enrich(candidate: Any) -> Any:
        if not isinstance(candidate, dict):
            return candidate
        updated = dict(candidate)
        capital_risk_profile = candidate_capital_risk_profile(updated)
        updated['capital_risk_profile'] = capital_risk_profile
        for key in (
            'failed_limitup_risk', 'main_buy_outflow_pressure', 'dark_pool_inflow_support',
            'popularity_crowding_risk', 'profit_taking_pressure', 'post_broken_board_selloff_risk',
            'high_popularity_trap_risk', 'capital_divergence_score', 'risk_softened_by_dark_pool_inflow',
        ):
            updated[key] = capital_risk_profile.get(key)
        eligibility = _cached_paper_pick_eligibility_profile(updated, bundle)
        eligibility_signals = eligibility.setdefault('signals', {})
        eligibility_signals['capital_risk_profile'] = capital_risk_profile
        risk_gate = paper_pick_risk_explanation_gate(updated)
        eligibility['paper_pick_risk_explanation_gate'] = risk_gate
        eligibility_signals['paper_pick_risk_explanation_gate'] = risk_gate
        if risk_gate['status'] == 'FAIL':
            eligibility['eligible'] = False
            eligibility['blockers'] = unique_text_values([
                *(eligibility.get('blockers') or []),
                'PAPER_PICK_RISK_EXPLANATION_GATE_FAIL',
            ])
            eligibility['missing_conditions'] = unique_text_values([
                *(eligibility.get('missing_conditions') or []),
                'explicit_catalyst_or_risk_rebuttal_required',
            ])
        if capital_risk_profile.get('risk_codes'):
            eligibility['missing_conditions'] = unique_text_values([
                *(eligibility.get('missing_conditions') or []),
                *capital_risk_profile['risk_codes'],
            ])
        if capital_risk_profile.get('risk_softened_by_dark_pool_inflow'):
            eligibility['positive_conditions'] = unique_text_values([
                *(eligibility.get('positive_conditions') or []),
                'risk_softened_by_dark_pool_inflow',
            ])
        updated['paper_pick_eligibility'] = eligibility
        exclusion_reasons = official_target_exclusion_reasons(updated, bundle)
        updated['official_target_excluded'] = bool(exclusion_reasons)
        updated['official_target_exclusion_reasons'] = exclusion_reasons
        if exclusion_reasons:
            updated['diagnostic_only'] = True
        updated['structured_formal_paper_pick_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
        updated['formal_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
        return updated

    primary_enriched_by_key: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    primary_items = bundle.get('paper_scoring_candidates')
    if isinstance(primary_items, list):
        enriched_primary_items = [enrich(item) for item in primary_items]
        bundle['paper_scoring_candidates'] = enriched_primary_items
        primary_enriched_by_key = {
            _candidate_runtime_cache_key(item): item
            for item in enriched_primary_items
            if isinstance(item, dict)
        }

    for key in ('candidate', 'candidate_features'):
        if key not in bundle:
            continue
        item = bundle.get(key)
        if isinstance(item, dict):
            cached_item = primary_enriched_by_key.get(_candidate_runtime_cache_key(item))
            bundle[key] = dict(cached_item) if isinstance(cached_item, dict) else enrich(item)
        else:
            bundle[key] = enrich(item)
    for key in ('structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            bundle[key] = [enrich(item) for item in items]
    impact = bundle.get('structured_formal_impact')
    if isinstance(impact, dict):
        for key in ('top_structured_only_candidates', 'sector_opportunity_candidates', 'structured_observation_candidates'):
            items = impact.get(key)
            if isinstance(items, list):
                impact[key] = [enrich(item) for item in items]
    return bundle


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
        'legacy_rank': row.get('legacy_rank'),
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
        'final_shadow_score': row.get('final_shadow_score'),
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
            profile['final_shadow_score'] or profile['structured_score'] or 0.0,
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
        and (safe_float(row.get('final_shadow_score')) is not None or safe_float(row.get('score')) is not None)
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

    layer_specs = [
        ('limitup_capture', limitup_capture_rows, limitup_capture_layer_sort_key),
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
    first_clean_row = next(
        (
            row for row in search_rows
            if paper_pick_eligibility_profile(row, bundle_context)['eligible']
            and not official_target_exclusion_reasons(row, bundle_context)
        ),
        None,
    )
    blocked_candidate_diagnostics = [
        {
            'symbol': symbol_for(row),
            'name': row.get('name'),
            'rank': row.get('rank'),
            'search_layer': row.get('search_layer'),
            'official_target_exclusion_reasons': row.get('official_target_exclusion_reasons') or [],
        }
        for row in search_rows
        if row.get('official_target_excluded')
    ]
    return {
        'search_rows': search_rows,
        'first_clean_row': first_clean_row,
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
        return dict(summary['information_coverage_audit'])
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
    return bundle


def _db_scan_summary_from_session(session: Dict[str, Any], trade_date: str) -> Dict[str, Any]:
    quotes_count = int(session.get('quotes_count') or 0)
    scored_count = int(session.get('scored_count') or 0)
    passed_count = int(session.get('passed_count') or 0)
    scan_dir = str(session.get('scan_dir') or '')
    scan_time = str(session.get('scan_time') or '')
    stored_source_status = session.get('source_status') if isinstance(session.get('source_status'), dict) else {}
    stored_market_snapshot = session.get('market_snapshot') if isinstance(session.get('market_snapshot'), dict) else {}
    stored_full_universe_scan = stored_market_snapshot.get('full_universe_scan') if isinstance(stored_market_snapshot.get('full_universe_scan'), dict) else {}
    source_status = stored_source_status or {
        'required_cdp_tabs': {
            'status': 'MISSING',
            'missing_sources': ['core_sentiment_pools'],
            'mode': 'db_legacy_without_source_proof',
        },
        'core_sentiment_pools': {
            'status': 'BLOCK',
            'missing_sources': ['core_sentiment_pools'],
        },
    }
    return {
        'trade_date': trade_date,
        'source_time': scan_time,
        'pipeline_version': 'eastmoney_web_tabs_db_daily_candidates',
        'source': 'eastmoney_web_tabs_db_daily_candidates',
        'cdp_url': str(session.get('cdp_url') or REQUIRED_EASTMONEY_CDP_URL),
        'files': {},
        'source_status': source_status,
        'full_universe_scan': {
            'enabled': True,
            'quote_count': quotes_count,
            'tradable_count': passed_count,
            'coverage_status': 'PASS' if quotes_count >= 4000 else 'LOW_SAMPLE',
            'min_quote_count': 4000,
            'board_counts': stored_full_universe_scan.get('board_counts') or {'main': passed_count, 'chinext': 0},
        },
        'information_coverage_audit': dict(MISSING_INFORMATION_COVERAGE_AUDIT),
        'sector_catalyst_diagnostics': dict(MISSING_SECTOR_CATALYST_DIAGNOSTICS),
        'market_snapshot': {
            **stored_market_snapshot,
            'universe_quote_count': quotes_count,
            'passed_count': passed_count,
            'scored_count': scored_count,
            'source_status': source_status,
            'full_universe_scan': {
                'enabled': True,
                'quote_count': quotes_count,
                'tradable_count': passed_count,
                'coverage_status': 'PASS' if quotes_count >= 4000 else 'LOW_SAMPLE',
                'min_quote_count': 4000,
                'board_counts': stored_full_universe_scan.get('board_counts') or {'main': passed_count, 'chinext': 0},
            },
            'eastmoney_web_tabs': [],
            'eastmoney_cdp_url': str(session.get('cdp_url') or REQUIRED_EASTMONEY_CDP_URL),
        },
        'source_evidence': {
            'summary_path': str(CANDIDATE_BUNDLE_ROOT / trade_date / 'scan_sessions.db'),
            'scored_path': str(CANDIDATE_BUNDLE_ROOT / trade_date / 'daily_candidates.db'),
            'scan_files': {},
            'cdp_url': str(session.get('cdp_url') or REQUIRED_EASTMONEY_CDP_URL),
        },
        '_bundle_path': str(CANDIDATE_BUNDLE_ROOT / trade_date / f'{trade_date}_db_daily_candidates.json'),
    }


def build_research_basket_from_db(date: str) -> Dict[str, Any]:
    try:
        from xiaogu_db import (
            fetch_daily_candidates,
            fetch_latest_api_scan_session_with_market_data,
            fetch_latest_scan_session,
            fetch_scan_data_directory_catalog,
            fetch_scan_data_directory_content,
            fetch_scan_market_data_payloads,
        )
    except Exception as exc:
        return {'available': False, 'reason': 'DB_HELPERS_UNAVAILABLE', 'error': repr(exc)}

    raw_domain_payloads: Dict[str, Any] = {}
    try:
        api_session = fetch_latest_api_scan_session_with_market_data(_parse_date(date))
        session = api_session or fetch_latest_scan_session(_parse_date(date))
        if api_session and api_session.get('id'):
            raw_domain_payloads = fetch_scan_market_data_payloads(int(api_session['id']))
    except Exception as exc:
        return {'available': False, 'reason': 'DB_SCAN_SESSION_UNAVAILABLE', 'error': repr(exc)}
    if not session:
        return {'available': False, 'reason': 'NO_DB_SCAN_SESSION_FOR_DATE'}
    if not raw_domain_payloads and str(session.get('cdp_url') or '') == 'manual_pipeline_snapshot':
        latest_scan = load_latest_eastmoney_scan(date)
        if latest_scan is not None:
            return build_research_basket_from_latest_scan(date)
        return {'available': False, 'reason': 'NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE'}

    try:
        rows = fetch_daily_candidates(_parse_date(date))
    except Exception as exc:
        return {'available': False, 'reason': 'DB_DAILY_CANDIDATES_UNAVAILABLE', 'error': repr(exc)}
    if not rows:
        return {'available': False, 'reason': 'NO_DB_DAILY_CANDIDATES_FOR_DATE'}

    try:
        catalog_rows = fetch_scan_data_directory_catalog(_parse_date(date))
    except Exception:
        catalog_rows = []
    try:
        content_rows = fetch_scan_data_directory_content(_parse_date(date))
    except Exception:
        content_rows = []

    source_time = str(session.get('scan_time') or '')
    today = dt.date.today()
    bundle_date = _parse_date(date) or today
    if bundle_date > today:
        print(f'WARN: bundle date {bundle_date} is in the future (today={today}), clamping', file=_sys.stderr, flush=True)
        bundle_date = today
    date = bundle_date.isoformat()
    bundle = _db_scan_summary_from_session(session, date)
    bundle.update({
        'date': date,
        'source_market_date': date,
        'source_time': source_time,
        '_runner_asof_time': source_time[11:] if len(source_time) >= 19 else '',
        'candidate_source': 'eastmoney_web_tabs_db_daily_candidates',
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'data_gate_status': 'PASS' if int(session.get('quotes_count') or 0) > 0 else 'PARTIAL_OR_FAIL',
        'source_status': dict(bundle.get('source_status') or {}),
        'full_universe_scan': dict(bundle.get('full_universe_scan') or {}),
        'market_snapshot': dict(bundle.get('market_snapshot') or {}),
        'paper_scoring_candidates': [],
        'candidate': {},
        'daily_ticket_search_result': {
            'searched_layers': ['db_daily_candidates'],
            'first_paper_pick_layer': None,
            'no_pick_reason_if_none': 'PENDING_EVALUATION',
        },
        'weak_market_shadow_ticket': None,
        'structured_observation_basket': [],
        'structured_sector_observation_basket': [],
        'structured_formal_impact': {},
        'eastmoney_web_tabs': [],
        'data_directory_catalog': {
            'path': str(CANDIDATE_BUNDLE_ROOT / date / 'scan_data_directory_catalog.db'),
            'rows': len(catalog_rows),
            'record_count': len(catalog_rows),
            'section_count': len({str(row.get('section_key') or '') for row in catalog_rows if row.get('section_key')}),
        },
        'data_directory_content': {
            'path': str(CANDIDATE_BUNDLE_ROOT / date / 'scan_data_directory_content.db'),
            'rows': len(content_rows),
            'record_count': len(content_rows),
            'tab_count': len({str(row.get('item_key') or '') for row in content_rows if row.get('item_key')}),
        },
        'scan_market_data_payload_domains': sorted(raw_domain_payloads.keys()),
        'scan_market_data_payloads_loaded': bool(raw_domain_payloads),
    })
    if raw_domain_payloads:
        bundle['scan_market_data_payloads'] = raw_domain_payloads
        domain_status = {
            domain: {
                'collection_status': 'PERSISTED',
                'usage': 'scoring' if domain == 'stock_capital_flow' else ('proxy' if domain in ('news_kuaixun', 'sector_news') else 'unused'),
                'item_count': len(payload) if isinstance(payload, list) else (len(payload) if isinstance(payload, dict) else 0),
            }
            for domain, payload in raw_domain_payloads.items()
        }
        bundle['information_coverage_audit'] = {
            **(bundle.get('information_coverage_audit') if isinstance(bundle.get('information_coverage_audit'), dict) else {}),
            'status': 'PASS',
            'domain_status': domain_status,
            'optional_or_proxy_gaps': [],
        }

    bundle_context = {
        'date': date,
        'source_market_date': date,
        'source_time': source_time,
        '_runner_asof_time': source_time[11:] if len(source_time) >= 19 else '',
        'candidate_source': 'eastmoney_web_tabs_db_daily_candidates',
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'data_gate_status': bundle.get('data_gate_status', 'PASS'),
        'source_status': bundle.get('source_status', {}),
        'full_universe_scan': bundle.get('full_universe_scan', {}),
        'market_snapshot': bundle.get('market_snapshot', {}),
    }

    candidates: List[Dict[str, Any]] = []
    try:
        from xiaogu_db import fetch_signals
        signal_rows = fetch_signals(_parse_date(date))
    except Exception:
        signal_rows = []
    signals_by_symbol: Dict[str, Dict[str, Any]] = {}
    for signal_row in signal_rows:
        symbol = str(signal_row.get('symbol') or '').strip()
        key = str(signal_row.get('signal_key') or '').strip()
        if not symbol or not key:
            continue
        entry = signals_by_symbol.setdefault(symbol, {})
        entry[key] = signal_row.get('signal_value')
        raw_json = signal_row.get('raw_json') if isinstance(signal_row.get('raw_json'), dict) else {}
        if raw_json and 'value' in raw_json and key not in entry:
            entry[key] = raw_json.get('value')
    for row in rows:
        raw_json = row.get('raw_json') if isinstance(row.get('raw_json'), dict) else {}
        candidate_features = row.get('candidate_features') if isinstance(row.get('candidate_features'), dict) else {}
        source_layers = row.get('source_layers') if isinstance(row.get('source_layers'), list) else []
        blockers = row.get('blockers') if isinstance(row.get('blockers'), list) else []
        signal_boost = signals_by_symbol.get(str(row.get('symbol') or '').strip(), {})
        candidate = {
            'trade_date': date,
            'signal_date': date,
            'asof_time': source_time[11:] if len(source_time) >= 19 else '',
            'code': str(row.get('symbol') or ''),
            'symbol': str(row.get('symbol') or ''),
            'name': str(row.get('stock_name') or raw_json.get('name') or ''),
            'rank': row.get('rank'),
            'final_score': row.get('final_score'),
            'score': row.get('final_score'),
            'price': row.get('close_price') if row.get('close_price') is not None else (row.get('open_price') if row.get('open_price') is not None else raw_json.get('price')),
            'open': row.get('open_price'),
            'close': row.get('close_price'),
            'high': row.get('high_price'),
            'low': row.get('low_price'),
            'signal_pct': row.get('signal_pct') if row.get('signal_pct') is not None else raw_json.get('signal_pct'),
            'close_position_score': row.get('close_position_score') if row.get('close_position_score') is not None else raw_json.get('close_position_score'),
            'fund_flow_momentum': row.get('fund_flow_momentum') if row.get('fund_flow_momentum') is not None else raw_json.get('fund_flow_momentum'),
            'sector_catalyst_score': row.get('sector_catalyst_score') if row.get('sector_catalyst_score') is not None else raw_json.get('sector_catalyst_score'),
            'sector_opportunity_score': row.get('sector_catalyst_score') if row.get('sector_catalyst_score') is not None else raw_json.get('sector_opportunity_score'),
            'early_opportunity_score': row.get('early_opportunity_score') if row.get('early_opportunity_score') is not None else raw_json.get('early_opportunity_score'),
            'topic_propagation_score': row.get('topic_propagation_score') if row.get('topic_propagation_score') is not None else raw_json.get('topic_propagation_score'),
            'market_regime': row.get('market_regime') or raw_json.get('market_regime') or 'eastmoney_web_tabs',
            'blockers': blockers,
            'source_layers': source_layers,
            'candidate_features': candidate_features,
            'paper_only': True,
            'no_trade': True,
            'decision': row.get('decision') or ('PAPER_PICK' if row.get('is_official_pick') else 'NO_PICK'),
            'is_official_pick': bool(row.get('is_official_pick')),
            'candidate_evidence_status': 'PASS',
            'data_gate_status': 'PASS',
            'source_time': source_time,
            'runner_asof_time': source_time[11:] if len(source_time) >= 19 else '',
            'source_row_hash': raw_json.get('source_row_hash') or f"db:{date}:{row.get('symbol') or ''}",
            'paper_pick_eligibility': raw_json.get('paper_pick_eligibility') if isinstance(raw_json.get('paper_pick_eligibility'), dict) else {},
            'research_signals': raw_json.get('research_signals') if isinstance(raw_json.get('research_signals'), dict) else {},
            'structured_component_details': raw_json.get('structured_component_details') if isinstance(raw_json.get('structured_component_details'), dict) else {},
            'structured_score_components': raw_json.get('structured_score_components') if isinstance(raw_json.get('structured_score_components'), dict) else {},
            'vei_phase_d_tags': normalize_vei_phase_d_tags(raw_json.get('vei_phase_d_tags') if isinstance(raw_json.get('vei_phase_d_tags'), list) else []),
        }
        if signal_boost:
            candidate['signals'] = dict(signal_boost)
            if candidate.get('fund_flow_momentum') is None and signal_boost.get('fund_flow_momentum') is not None:
                candidate['fund_flow_momentum'] = signal_boost.get('fund_flow_momentum')
            if candidate.get('sector_opportunity_score') is None and signal_boost.get('sector_opportunity_score') is not None:
                candidate['sector_opportunity_score'] = signal_boost.get('sector_opportunity_score')
            if candidate.get('topic_propagation_score') is None and signal_boost.get('topic_propagation_score') is not None:
                candidate['topic_propagation_score'] = signal_boost.get('topic_propagation_score')
            if candidate.get('early_opportunity_score') is None and signal_boost.get('early_opportunity_score') is not None:
                candidate['early_opportunity_score'] = signal_boost.get('early_opportunity_score')
            if candidate.get('low_position_catalyst_score') is None and signal_boost.get('low_position_catalyst_score') is not None:
                candidate['low_position_catalyst_score'] = signal_boost.get('low_position_catalyst_score')
            if candidate.get('close_position_score') is None and signal_boost.get('close_position_score') is not None:
                candidate['close_position_score'] = signal_boost.get('close_position_score')
            if candidate.get('limitup_reason_propagation_score') is None and signal_boost.get('limitup_reason_propagation_score') is not None:
                candidate['limitup_reason_propagation_score'] = signal_boost.get('limitup_reason_propagation_score')
            if candidate.get('intraday_alert_strength') is None and signal_boost.get('intraday_alert_strength') is not None:
                candidate['intraday_alert_strength'] = signal_boost.get('intraday_alert_strength')
            if candidate.get('structured_score') is None and signal_boost.get('structured_score') is not None:
                candidate['structured_score'] = signal_boost.get('structured_score')
        if candidate_features:
            candidate.update(candidate_features)
        if raw_json:
            for key in ('setup_type', 'search_layer_hint', 'structured_component_details', 'structured_score_components', 'vei_phase_d_tags'):
                if key in raw_json and raw_json.get(key) is not None:
                    candidate[key] = raw_json.get(key)
        if not candidate.get('structured_component_details'):
            candidate['structured_component_details'] = {
                'sector_opportunity_score': candidate.get('sector_opportunity_score') or candidate.get('sector_catalyst_score') or 0.0,
                'pre_limitup_anomaly': raw_json.get('structured_component_details', {}).get('pre_limitup_anomaly') if isinstance(raw_json.get('structured_component_details'), dict) else 0.0,
                'weak_to_strong_reversal': raw_json.get('structured_component_details', {}).get('weak_to_strong_reversal') if isinstance(raw_json.get('structured_component_details'), dict) else 0.0,
                'first_board_pre_signal': raw_json.get('structured_component_details', {}).get('first_board_pre_signal') if isinstance(raw_json.get('structured_component_details'), dict) else 0.0,
            }
        if not candidate.get('structured_score_components'):
            candidate['structured_score_components'] = {
                'fund_flow_momentum': candidate.get('fund_flow_momentum') or 0.0,
                'time_series_momentum': raw_json.get('structured_score_components', {}).get('time_series_momentum') if isinstance(raw_json.get('structured_score_components'), dict) else 0.0,
                'low_position_catalyst_score': candidate.get('low_position_catalyst_score') or 0.0,
            }
        if not candidate.get('vei_phase_d_tags'):
            candidate['vei_phase_d_tags'] = normalize_vei_phase_d_tags(
                candidate.get('structured_component_details', {}).get('vei_phase_d_tags')
                if isinstance(candidate.get('structured_component_details'), dict)
                else []
            )
        candidates.append(candidate)

    if content_rows:
        fund_by_code = parse_capital_flow_from_content_records(content_rows)
        if fund_by_code:
            for candidate in candidates:
                code = str(candidate.get('code') or candidate.get('symbol') or '').strip()
                if not code or code not in fund_by_code:
                    continue
                flow = fund_by_code[code]
                candidate['data_directory_capital_flow'] = flow
                if candidate.get('price') is None and flow.get('price') is not None:
                    candidate['price'] = flow.get('price')

    enriched_rows = [dict(candidate) for candidate in candidates]
    search_context = build_daily_ticket_search_rows(enriched_rows, bundle_context)
    search_rows = search_context['search_rows']
    selected_rows = [dict(row) for row in search_rows]
    structured_formal_impact = structured_formal_impact_summary(enriched_rows, selected_rows, bundle_context)

    bundle['paper_scoring_candidates'] = selected_rows or candidates
    bundle['candidate'] = search_context['first_clean_row'] if search_context['first_clean_row'] is not None else (bundle['paper_scoring_candidates'][0] if bundle['paper_scoring_candidates'] else {})
    bundle['daily_ticket_search_result'] = search_context['daily_ticket_search_result']
    bundle['paper_pick_candidate_stage_distribution'] = dict(search_context['paper_pick_candidate_stage_distribution'])
    bundle['candidate_stage_blocker_distribution'] = {
        stage: dict(counts) for stage, counts in search_context['candidate_stage_blocker_distribution'].items()
    }
    bundle['official_target_excluded_count'] = search_context['official_target_excluded_count']
    bundle['first_excluded_candidate'] = search_context['first_excluded_candidate']
    bundle['structured_observation_basket'] = structured_formal_impact['structured_observation_candidates']
    bundle['structured_sector_observation_basket'] = structured_formal_impact['sector_opportunity_candidates']
    bundle['structured_formal_impact'] = structured_formal_impact

    if catalog_rows:
        bundle['data_directory_catalog_records'] = catalog_rows
    if content_rows:
        bundle['data_directory_content_records'] = content_rows
        content_by_code: Dict[str, List[Dict[str, Any]]] = {}
        for content_row in content_rows:
            code = str(content_row.get('code') or '').strip()
            if code:
                content_by_code.setdefault(code, []).append(content_row)
        if content_by_code:
            bundle['data_directory_content_by_code'] = content_by_code

    bundle['candidate_source'] = 'eastmoney_web_tabs_db_daily_candidates'
    bundle['source'] = 'eastmoney_web_tabs_db_daily_candidates'
    bundle['available'] = True
    bundle['decision_reason'] = 'DB_DAILY_CANDIDATES'
    raw_fund_by_code = stock_capital_flow_by_code_from_payload(raw_domain_payloads.get('stock_capital_flow')) if raw_domain_payloads else {}
    if raw_fund_by_code:
        bundle['data_directory_capital_flow_by_code'] = raw_fund_by_code
        inject_capital_flow_boost(bundle, raw_fund_by_code)
    if content_rows:
        fund_by_code = parse_capital_flow_from_content_records(content_rows)
        if fund_by_code:
            bundle['data_directory_capital_flow_by_code'] = {**fund_by_code, **bundle.get('data_directory_capital_flow_by_code', {})}
            inject_capital_flow_boost(bundle, fund_by_code)
    if not raw_domain_payloads:
        attach_scan_summary_information_coverage_audit(bundle, None, None)
    normalize_bundle_vei_tags(bundle)
    try:
        bundle_path = Path(str(bundle.get('_bundle_path') or (CANDIDATE_BUNDLE_ROOT / date / f'{date}_db_daily_candidates.json')))
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(bundle_path, bundle)
        bundle['_bundle_path'] = str(bundle_path)
    except Exception:
        pass
    return bundle


def build_research_basket_from_latest_scan(date: str, asof_time: str | None = None) -> Dict[str, Any]:
    # Prefer runner-consumable minimal summary
    # Prefer current API snapshots by session recency.
    scan_dir = None
    for label in ('eastmoney_scan_afternoon', 'eastmoney_scan_morning', 'eastmoney_scan_v2', 'eastmoney_scan'):
        candidate = LIVE_SCAN_ROOT / date / label
        if candidate.exists():
            scan_dir = candidate
            break
    if scan_dir is None:
        scan_dir = LIVE_SCAN_ROOT / date / 'eastmoney_scan'
    runner_summary = scan_dir / SCAN_SUMMARY_RUNNER_NAME
    if runner_summary.exists():
        try:
            summary = read_json(runner_summary)
            source_time = str(summary.get('source_time', ''))
            if source_time.startswith(date):
                return _bundle_from_scan_summary(runner_summary, summary)
        except Exception:
            pass
    summaries = scan_summary_paths(date)
    if not summaries:
        return {'available': False, 'reason': 'NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE'}

    if asof_time:
        filtered: List[Path] = []
        for summary_path in summaries:
            try:
                summary = read_json(summary_path)
            except Exception:
                continue
            source_time = str(summary.get('source_time', ''))
            if not source_time.startswith(date):
                continue
            age_minutes = scan_age_minutes(source_time, date, asof_time)
            if age_minutes is not None and age_minutes >= 0:
                filtered.append(summary_path)
        if filtered:
            summaries = filtered

    summary_path = summaries[0]
    try:
        summary = read_json(summary_path)
    except Exception as exc:
        return {'available': False, 'reason': 'SAME_DAY_SCAN_SUMMARY_UNREADABLE', 'summary_path': str(summary_path), 'error': repr(exc)}
    return _bundle_from_scan_summary(summary_path, summary)


def parse_capital_flow_from_content_records(content_records):
    fund_by_code = {}
    for rec in content_records:
        item_key = str(rec.get('item_key') or '')
        if item_key != 'stock_capital_flow':
            continue
        code = str(rec.get('code') or rec.get('SECURITY_CODE') or '').strip()
        if not code:
            continue
        cells = rec.get('cells') or []
        if len(cells) < 7:
            continue
        def parse_money(val):
            s = str(val).replace('\xa0', ' ').strip()
            if not s or s in ('-', '--', ''):
                return 0.0
            m = re.search(r'(-?[\d.]+)\s*(亿|万)?', s)
            if not m:
                return 0.0
            v = float(m.group(1))
            if m.group(2) == '亿':
                v *= 100000000
            elif m.group(2) == '万':
                v *= 10000
            return v
        def parse_pct(val):
            s = str(val).replace('\xa0', ' ').strip()
            m = re.search(r'(-?[\d.]+)%', s)
            return float(m.group(1)) / 100.0 if m else 0.0
        main_net = parse_money(cells[6]) if len(cells) > 6 else 0.0
        super_large_net = parse_money(cells[7]) if len(cells) > 7 else 0.0
        large_net = parse_money(cells[8]) if len(cells) > 8 else 0.0
        medium_net = parse_money(cells[9]) if len(cells) > 9 else 0.0
        small_net = parse_money(cells[10]) if len(cells) > 10 else 0.0
        price = float(cells[4]) if len(cells) > 4 and cells[4] not in ('-', '') else 0.0
        pct_chg = parse_pct(cells[5]) if len(cells) > 5 else 0.0
        if code not in fund_by_code or abs(main_net) > abs(fund_by_code[code].get('main_force_net_inflow', 0)):
            fund_by_code[code] = {
                'main_force_net_inflow': main_net,
                'main_force_net_inflow_pct': parse_money(cells[7]) / 100000000 if len(cells) > 7 else 0.0,
                'super_large_net_inflow': super_large_net,
                'large_net_inflow': large_net,
                'medium_net_inflow': medium_net,
                'small_net_inflow': small_net,
                'price': price,
                'pct_chg': pct_chg,
                'source': 'data_directory_content_stock_capital_flow',
            }
    return fund_by_code


def stock_capital_flow_by_code_from_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize scanner raw-domain stock_capital_flow payload to runner flow map."""
    rows = payload if isinstance(payload, list) else ((payload.get('rows') or payload.get('data') or []) if isinstance(payload, dict) else [])
    fund_by_code: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_code = row.get('code') or row.get('symbol') or row.get('f12') or ''
        code = str(raw_code).strip().zfill(6)
        if len(code) != 6:
            continue
        main_net = safe_float(row.get('main_force_net_inflow'))
        if main_net is None:
            main_net = safe_float(row.get('net_inflow_main'))
        if main_net is None:
            main_net = safe_float(row.get('f62')) or 0.0
        fund_by_code[code] = {
            'main_force_net_inflow': main_net,
            'main_force_net_inflow_pct': safe_float(row.get('main_force_net_inflow_pct')) or safe_float(row.get('f184')) or 0.0,
            'super_large_net_inflow': safe_float(row.get('super_large_net_inflow')) or safe_float(row.get('f66')) or 0.0,
            'large_net_inflow': safe_float(row.get('large_net_inflow')) or safe_float(row.get('f72')) or 0.0,
            'medium_net_inflow': safe_float(row.get('medium_net_inflow')) or safe_float(row.get('f78')) or 0.0,
            'small_net_inflow': safe_float(row.get('small_net_inflow')) or safe_float(row.get('f84')) or 0.0,
            'source': 'scan_market_data_stock_capital_flow',
        }
    return fund_by_code


def inject_capital_flow_boost(bundle, fund_by_code):
    for key in ('candidate', 'candidate_features'):
        cand = bundle.get(key)
        if isinstance(cand, dict):
            code = str(cand.get('code') or cand.get('symbol') or '').strip()
            if code and code in fund_by_code:
                flow = fund_by_code[code]
                cand['data_directory_capital_flow'] = flow
                if cand.get('price') is None and flow.get('price') is not None:
                    cand['price'] = flow.get('price')
    for key in ('paper_scoring_candidates', 'structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    code = str(item.get('code') or item.get('symbol') or '').strip()
                    if code and code in fund_by_code:
                        flow = fund_by_code[code]
                        item['data_directory_capital_flow'] = flow
                        if item.get('price') is None and flow.get('price') is not None:
                            item['price'] = flow.get('price')
                        if item.get('score') is None:
                            net = fund_by_code[code].get('main_force_net_inflow', 0)
                            if net > 0:
                                item['score'] = round(min(100, 50 + net / 100000000 * 2), 4)
                                item['_score_from_data_directory_capital_flow'] = True


def fetch_candidate_fund_flow_live(codes, timeout=5):
    """Official runner no longer performs live direct Eastmoney fund-flow fetches."""
    return {}


def inject_live_fund_flow_into_candidates(bundle):
    candidates = bundle.get('paper_scoring_candidates') or []
    if not candidates:
        return
    ranked_candidates = sorted(
        [candidate for candidate in candidates if isinstance(candidate, dict)],
        key=lambda candidate: (safe_float(candidate.get('rank')) or 999999.0, -(safe_float(candidate.get('final_score')) or safe_float(candidate.get('score')) or 0.0)),
    )[:10]
    codes = []
    for candidate in ranked_candidates:
        existing_flow = candidate.get('data_directory_capital_flow') if isinstance(candidate.get('data_directory_capital_flow'), dict) else {}
        existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
        scanner_net = safe_float(candidate.get('net_inflow_main'))
        if existing_net is None and scanner_net is not None:
            continue
        code = str(candidate.get('code') or candidate.get('symbol') or '').strip()
        if code and len(code) == 6:
            codes.append(code)
    if not codes:
        return
    live_fund = fetch_candidate_fund_flow_live(codes)
    if codes and not live_fund:
        bundle['candidate_fund_recheck_missing'] = True
        for candidate in ranked_candidates:
            if isinstance(candidate, dict):
                candidate['candidate_fund_recheck_missing'] = True
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        code = str(cand.get('code') or cand.get('symbol') or '').strip()
        if code in live_fund:
            existing_flow = cand.get('data_directory_capital_flow') if isinstance(cand.get('data_directory_capital_flow'), dict) else {}
            existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
            live_net = safe_float(live_fund[code].get('main_force_net_inflow')) or 0.0
            if existing_net is None or existing_net == 0.0:
                cand['data_directory_capital_flow'] = live_fund[code]
                if live_net > 0:
                    existing = safe_float(cand.get('net_inflow_main')) or 0
                    if existing <= 0:
                        cand['net_inflow_main'] = live_net
                        cand['_net_inflow_main_from_live_fund_flow'] = True
            else:
                cand['data_directory_capital_flow_live_supplement'] = live_fund[code]
    for key in ('structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    code = str(item.get('code') or item.get('symbol') or '').strip()
                    if code in live_fund:
                        existing_flow = item.get('data_directory_capital_flow') if isinstance(item.get('data_directory_capital_flow'), dict) else {}
                        existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
                        if existing_net is None or existing_net == 0.0:
                            item['data_directory_capital_flow'] = live_fund[code]
                        else:
                            item['data_directory_capital_flow_live_supplement'] = live_fund[code]
    content_by_code = bundle.get('data_directory_content_by_code') or {}
    fund_content = {}
    for code, recs in content_by_code.items():
        for rec in recs:
            if rec.get('item_key') == 'stock_capital_flow':
                fund_content[code] = rec
                break
    top_fund_codes = sorted(fund_content.keys(), key=lambda c: len(fund_content.get(c, {}).get('raw_text', '')), reverse=True)[:10]
    existing_codes = {str(c.get('code') or '').strip() for c in candidates if isinstance(c, dict)}
    for code in top_fund_codes:
        if code in existing_codes or len(code) != 6:
            continue
        rec = fund_content.get(code, {})
        raw = rec.get('raw_text', '')
        nums = re.findall(r'-?[\d]+(?:\.[\d]+)?(?:亿|万)?', raw)
        cleaned = []
        for n in nums:
            val = float(n.replace('亿', '').replace('万', ''))
            if '亿' in n:
                val *= 100000000
            elif '万' in n:
                val *= 10000
            cleaned.append(val)
        main_net = cleaned[5] if len(cleaned) > 5 else 0.0
        if main_net <= 0:
            continue
        name_match = re.search(r'\d{6}\s+([一-龥A-Za-z0-9*]+)', raw)
        name = name_match.group(1) if name_match else ''
        fund_cand = {
            'code': code,
            'symbol': code,
            'name': name,
            'price': float(cleaned[0]) if cleaned else 0,
            'signal_pct': float(nums[2].replace('%', '')) if len(nums) > 2 and '%' in str(nums[2]) else 0,
            'signal_amount': cleaned[3] if len(cleaned) > 3 else 0,
            'rank': int(nums[0]) if nums else 999,
            'net_inflow_main': main_net,
            'data_directory_capital_flow': {
                'main_force_net_inflow': main_net,
                'source': 'data_directory_content_stock_capital_flow',
            },
            '_from_data_directory_capital_flow': True,
            'paper_only': True,
            'no_trade': True,
        }
        candidates.append(fund_cand)
    bundle['paper_scoring_candidates'] = candidates


def load_candidate_bundle(date: str, asof_time: str | None = None) -> Dict[str, Any]:
    db_bundle = build_research_basket_from_db(date)
    if isinstance(db_bundle, dict) and db_bundle.get('available'):
        source_time = str(db_bundle.get('source_time', '') or '')
        if not asof_time:
            latest_scan = load_latest_eastmoney_scan(date, asof_time)
            if latest_scan is not None:
                return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
            return db_bundle
        if source_time.startswith(date):
            age_minutes = scan_age_minutes(source_time, date, asof_time)
            if age_minutes is None or age_minutes <= 0:
                latest_scan = load_latest_eastmoney_scan(date, asof_time)
                if latest_scan is not None:
                    return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
                return db_bundle

    patterns = [
        str(CANDIDATE_BUNDLE_ROOT / date / '*.json'),
        str(BASE / f'forward_candidate_bundle_{date}.json'),
    ]
    files: List[str] = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    latest_scan = load_latest_eastmoney_scan(date, asof_time)
    if asof_time and latest_scan is not None:
        return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
    if not files:
        if latest_scan is not None:
            return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
        return build_research_basket_from_latest_scan(date, asof_time)
    latest = max(files, key=os.path.getmtime)
    if latest_scan is not None and os.path.getmtime(latest_scan[0]) > os.path.getmtime(latest):
        return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
    try:
        data = read_json(Path(latest))
        normalize_bundle_vei_tags(data)
        data['_bundle_path'] = latest
        data['available'] = True
        attach_scan_summary_information_coverage_audit(
            data,
            latest_scan[0] if latest_scan is not None else None,
            latest_scan[1] if latest_scan is not None else None,
        )
        data['sector_catalyst_diagnostics'] = scan_summary_sector_catalyst_diagnostics(
            latest_scan[1] if latest_scan is not None else None
        )
        if latest_scan is not None and (
            active_chain_governance_flags(data, date)
            or 'structured_observation_basket' not in data
            or 'structured_sector_observation_basket' not in data
            or 'structured_formal_impact' not in data
            or 'sector_opportunity_candidates' not in (data.get('structured_formal_impact') or {})
            or 'paper_pick_eligibility' not in (data.get('candidate') or {})
        ):
            if latest_scan is not None:
                return _ensure_current_realtime_bundle_path(date, build_research_basket_from_latest_scan(date, asof_time), latest_scan[0])
            return build_research_basket_from_latest_scan(date, asof_time)
        return data
    except Exception as e:
        return {'available': False, 'reason': 'CANDIDATE_BUNDLE_UNREADABLE', 'error': repr(e), 'path': latest}


def _bundle_from_scan_summary(summary_path: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    source_time = str(summary.get('source_time', ''))
    data_gate_status = 'PASS' if source_time.startswith(summary_path.parent.parent.name) else 'PARTIAL_OR_FAIL'
    full_pool_rows = (
        summary_bundle_rows(summary, 'full_candidate_pool')
        or summary_file_rows(summary_path, summary, 'full_candidate_pool')
    )
    scored_rows = (
        summary_bundle_rows(summary, 'scored_candidates')
        or summary_bundle_rows(summary, 'paper_scoring_candidates')
        or summary_bundle_rows(summary, 'scored_rows')
        or summary_file_rows(summary_path, summary, 'scored')
    )
    if not full_pool_rows:
        full_pool_rows = scored_rows[:200]
    structured_scores = summary_bundle_rows(summary, 'structured_scores') or summary_file_rows(summary_path, summary, 'structured_scores')
    if not scored_rows:
        return {'available': False, 'reason': 'NO_SCORED_ROWS_FOR_SAME_DAY_SCAN', 'summary_path': str(summary_path)}
    structured_scores_by_symbol = {symbol_for(row): row for row in structured_scores if symbol_for(row)}

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
        merged['final_shadow_score'] = first_defined(structured.get('final_shadow_score'), row.get('final_shadow_score'))
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

    enriched_rows = [with_structured_score(r) for r in scored_rows]
    enriched_full_pool = [with_structured_score(r) for r in full_pool_rows]
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
        'candidate_source': summary.get('pipeline_version') or summary.get('source') or 'eastmoney_web_tabs_scan',
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
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
            'max_consecutive': summary.get('max_consecutive'),
            'sentiment_score': summary.get('sentiment_score'),
            'market_main_inflow': summary.get('market_main_inflow'),
            'market_regime': summary.get('market_regime') or (summary.get('market_snapshot') or {}).get('market_regime'),
            'external_market': summary.get('external_market') or (summary.get('market_snapshot') or {}).get('external_market') or {},
            'sector_snapshot': summary.get('sector_snapshot') or (summary.get('market_snapshot') or {}).get('sector_snapshot') or [],
            'source_status': summary.get('source_status', {}),
            'hard_block_source_status': summary.get('hard_block_source_status', {}),
            'eastmoney_web_tabs': summary.get('eastmoney_web_tabs', []),
            'eastmoney_cdp_url': summary.get('eastmoney_cdp_url') or summary.get('cdp_url', ''),
        },
    }
    search_context = build_daily_ticket_search_rows(enriched_rows, bundle_context)
    search_rows = search_context['search_rows']
    first_clean_row = search_context['first_clean_row']
    paper_pick_candidate_stage_distribution = search_context['paper_pick_candidate_stage_distribution']
    candidate_stage_blocker_distribution = search_context['candidate_stage_blocker_distribution']
    daily_ticket_search_result = search_context['daily_ticket_search_result']
    blocked_candidate_diagnostics = search_context.get('blocked_candidate_diagnostics', [])
    decision_class = 'PAPER_PICK' if search_rows else 'RESEARCH_CANDIDATE'
    selected = [basket_candidate(r, decision_class) for r in search_rows]
    structured_formal_impact = structured_formal_impact_summary(enriched_rows, selected, bundle_context)
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
        'candidate_source': summary.get('pipeline_version') or summary.get('source') or 'eastmoney_web_tabs_scan',
        'rule_version': RULE_VERSION,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'data_gate_status': data_gate_status,
        'full_candidate_pool': enriched_full_pool,
        'scored_candidates': enriched_rows,
        'passed_candidates': selected,
        'decision_candidates': selected,
        'candidate_pool_exclusion_summary': dict(summary.get('pool_exclusion_summary') or {}),
        'xiaochan_gate_status': 'ALLOW_FORWARD_PAPER_NO_TRADE',
        'paper_scoring_candidates': selected,
        'candidate': first_clean_row if first_clean_row is not None else {},
        'first_search_candidate_diagnostic': selected[0] if selected else {},
        'blocked_candidate_diagnostics': blocked_candidate_diagnostics,
        'official_target_excluded_count': search_context.get('official_target_excluded_count', 0),
        'first_excluded_candidate': search_context.get('first_excluded_candidate'),
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
        'research_signals': summary_bundle_rows(summary, 'research_signals') or summary_file_rows(summary_path, summary, 'research_signals'),
        'structured_score_components': summary_bundle_rows(summary, 'structured_score_components') or summary_file_rows(summary_path, summary, 'structured_score_components'),
        'structured_component_details': summary_bundle_rows(summary, 'structured_component_details') or summary_file_rows(summary_path, summary, 'structured_component_details'),
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
        'eastmoney_web_tabs': summary.get('eastmoney_web_tabs', []),
        'eastmoney_cdp_url': summary.get('eastmoney_cdp_url') or summary.get('cdp_url', ''),
        'risk_flags': [] if search_rows else ['NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS'],
        'decision_reason': 'SAME_DAY_SCAN_PASSED_CANDIDATE' if search_rows else 'NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS',
        'source_evidence': {
            'summary_path': str(summary_path),
            'scan_files': summary.get('files', {}),
            'cdp_url': summary.get('cdp_url', ''),
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


def load_latest_eastmoney_scan(date: str, asof_time: str | None = None) -> Tuple[Path, Dict[str, Any]] | None:
    # Prefer runner-consumable minimal summary (113KB vs 26MB)
    # Prefer current API snapshots by session recency.
    scan_dir = None
    for label in ('eastmoney_scan_afternoon', 'eastmoney_scan_morning', 'eastmoney_scan_v2', 'eastmoney_scan'):
        candidate = LIVE_SCAN_ROOT / date / label
        if candidate.exists():
            scan_dir = candidate
            break
    if scan_dir is None:
        scan_dir = LIVE_SCAN_ROOT / date / 'eastmoney_scan'
    runner_summary = scan_dir / SCAN_SUMMARY_RUNNER_NAME
    if runner_summary.exists():
        try:
            summary = read_json(runner_summary)
            source_time = str(summary.get('source_time', ''))
            # 允许使用同日或次日的数据（历史回放场景）
            if source_time[:10] == date or source_time[:10] > date:
                return runner_summary, summary
        except Exception:
            pass
    # Fallback to full summary
    candidates = []
    for summary_path in scan_summary_paths(date):
        try:
            summary = read_json(summary_path)
        except Exception:
            continue
        source_time = str(summary.get('source_time', ''))
        if not source_time.startswith(date):
            continue
        if asof_time:
            age_minutes = scan_age_minutes(source_time, date, asof_time)
            if age_minutes is None or age_minutes > 0:
                continue
        candidates.append((summary_path, summary, source_time))
    if not candidates:
        return None
    # Return the latest scan by source_time
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[0][0], candidates[0][1]


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


def scan_date_for_runtime(target_date: str) -> str:
    try:
        requested = dt.date.fromisoformat(target_date)
    except Exception:
        requested = latest_completed_trading_day()
    today = latest_completed_trading_day()
    return min(requested, today).isoformat()


def run_realtime_scan(date: str, asof_time: str | None = None) -> Dict[str, Any]:
    # 使用v2 scanner（API直接获取，不需要CDP）
    scan_module_path = BASE / 'scrapy_scanner' / 'runner_v2.py'

    # 根据时间选择扫描目录
    import datetime
    now = datetime.datetime.now()
    if now.hour < 12:
        scan_label = 'morning'
    else:
        scan_label = 'afternoon'

    scan_dir = LIVE_SCAN_ROOT / date / f'eastmoney_scan_{scan_label}'
    scan_args = [
        sys.executable,
        str(scan_module_path),
        '--output-dir', str(scan_dir),
    ]
    proc = subprocess.run(scan_args, cwd=str(BASE), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f'SCAN_FAILED:{proc.returncode}:{proc.stderr[-4000:]}')

    # Preserve the canonical API scan alias for compatibility consumers.
    eastmoney_scan = LIVE_SCAN_ROOT / date / 'eastmoney_scan'
    if eastmoney_scan.exists():
        eastmoney_scan.unlink()
    eastmoney_scan.symlink_to(scan_dir)

    summary_path = scan_dir / 'eastmoney_web_tabs_summary.json'
    if not summary_path.exists():
        summary_candidates = sorted((LIVE_SCAN_ROOT / date).rglob('eastmoney_web_tabs_summary.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not summary_candidates:
            raise RuntimeError('SCAN_SUMMARY_NOT_FOUND')
        summary_path = summary_candidates[0]
    summary = read_json(summary_path)
    if isinstance(summary, dict) and summary.get('available') is not False:
        return summary
    raise RuntimeError('SCAN_SUMMARY_UNREADABLE')


def _ensure_current_realtime_bundle_path(date: str, bundle: Dict[str, Any], summary_path: Path | None = None) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return bundle
    updated = dict(bundle)
    candidate_path = CANDIDATE_BUNDLE_ROOT / date / f'{date}_eastmoney_web_tabs_v0_1_realtime.json'
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

    scanner_provenance = bool(
        bundle.get('scan_summary_path')
        or source_status.get('required_cdp_tabs')
        or source_status.get('core_sentiment_pools')
    )
    if not scanner_provenance:
        return {'status': 'LEGACY_NOT_APPLICABLE', 'missing_sources': [], 'flags': []}

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
    if not bundle.get('available'):
        return 'NO_PICK', '', bundle.get('reason', 'NO_VERIFIED_CANDIDATE'), {}, ['NO_VERIFIED_CANDIDATE_BUNDLE']
    governance_flags = active_chain_governance_flags(bundle, target_date, allow_stale_data=allow_stale_data)
    if governance_flags:
        # Still find highest-scoring candidate for fallback output
        _gcandidates = []
        if isinstance(bundle.get('paper_scoring_candidates'), list) and bundle['paper_scoring_candidates']:
            _gcandidates.extend([c for c in bundle['paper_scoring_candidates'] if isinstance(c, dict)])
        elif isinstance(bundle.get('candidate'), dict) and symbol_for(bundle['candidate']):
            _gcandidates.append(bundle['candidate'])
        _gcandidates = [c for c in _gcandidates if symbol_for(c)]
        if _gcandidates:
            for c in _gcandidates:
                c['_effective_score'] = (safe_float(c.get('final_score')) or safe_float(c.get('score')) or
                    max(0, 100 - (safe_float(c.get('rank')) or 999.0)) + (safe_float(c.get('signal_pct')) or 0.0) * 0.1)
            _gbest = max(_gcandidates, key=lambda c: c.get('_effective_score', 0))
            _gfeatures = dict(_gbest)
            _gfeatures['governance_gate_bypass'] = True
            _gfeatures['governance_flags'] = governance_flags
            return 'NO_PICK', symbol_for(_gbest), 'ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS:' + ';'.join(governance_flags), _gfeatures, governance_flags
        return 'NO_PICK', '', 'ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS:' + ';'.join(governance_flags), {}, governance_flags

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
    elif isinstance(bundle.get('candidate'), dict) and symbol_for(bundle['candidate']):
        candidates.append(bundle['candidate'])
    if not candidates:
        return 'NO_PICK', '', 'BUNDLE_HAS_NO_PAPER_SCORING_CANDIDATE', {}, ['NO_PAPER_SCORING_CANDIDATE']

    _capital_flow_lookup = build_capital_flow_lookup(bundle)
    sizing = paper_sizing_context(bundle)
    selection_trace: List[Dict[str, Any]] = []
    priority_labels = [
        'setup_rank_x10',
        'golden_pair_bonus',
        'capital_flow_bonus',
        'l2_bonus',
        'confluence_bonus',
        'layer_penalty',
        'lifecycle_score',
        'negative_stale_decay',
        'blended_score',
        'structured_score',
        'sector_opportunity_score',
        'main_theme_core_score',
        'main_theme_alignment_score',
        'pre_limitup_anomaly',
        'first_board_pre_signal',
        'net_inflow',
        'from_data_directory',
        'data_directory_score',
        'negative_rank',
    ]

    def official_pick_priority(candidate: Dict[str, Any], features: Dict[str, Any]) -> Tuple[float, ...]:
        profile = _cached_structured_signal_profile(candidate, bundle)
        capital_flow = candidate.get('data_directory_capital_flow') if isinstance(candidate.get('data_directory_capital_flow'), dict) else {}
        net_inflow = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
        from_data_directory = 1.0 if candidate.get('_from_data_directory_capital_flow') or capital_flow.get('source') == 'data_directory_content_stock_capital_flow' else 0.0
        data_directory_score = 1.0 if candidate.get('_score_from_data_directory_capital_flow') else 0.0
        main_theme_core = safe_float(profile.get('main_theme_core_score')) or 0.0
        main_theme_alignment = safe_float(profile.get('main_theme_alignment_score')) or 0.0
        sector_opportunity = safe_float(profile.get('sector_opportunity_score')) or 0.0
        pre_limitup = safe_float((profile.get('structured_component_details') or {}).get('pre_limitup_anomaly')) or 0.0
        first_board = safe_float((profile.get('structured_component_details') or {}).get('first_board_pre_signal')) or 0.0
        score = candidate_score_value(features) or candidate_score_value(candidate) or 0.0
        structured = safe_float(profile.get('structured_score')) or 0.0
        rank = candidate_rank_value(candidate)
        blended = score * 0.6 + structured * 0.4
        lifecycle = features.get('candidate_lifecycle') if isinstance(features.get('candidate_lifecycle'), dict) else {}
        if not lifecycle:
            lifecycle = _candidate_lifecycle_profile(candidate, bundle)
        setup_rank = safe_float(features.get('setup_rank')) or safe_float(lifecycle.get('setup_rank')) or 0.0
        lifecycle_score = safe_float(features.get('lifecycle_score')) or safe_float(lifecycle.get('lifecycle_score')) or 0.0
        stale_decay = safe_float(features.get('stale_decay')) or safe_float(lifecycle.get('stale_decay')) or 0.0
        # L2_LIMIT_STRENGTH bonus (改动 C): L2候选优先于同分非L2候选
        _cand_source_layers = set(candidate.get('source_layers') or [])
        _feat_source_layers = set(features.get('source_layers') or [])
        _all_layers = _cand_source_layers | _feat_source_layers
        _signal_layers = {l for l in _all_layers if not l.startswith('L0')}
        l2_bonus = 2.0 if 'L2_LIMIT_STRENGTH' in _all_layers else 0.0

        # Multi-layer confluence bonus: 2-3 layers is the sweet spot
        n_layers = len(_signal_layers)
        if n_layers == 2:
            confluence_bonus = 3.0
        elif n_layers == 3:
            confluence_bonus = 2.0
        elif n_layers >= 4:
            confluence_bonus = -2.0  # over-exposure penalty
        else:
            confluence_bonus = 0.0

        # L2+L3 GOLDEN PAIR: highest limit-up rate (19%)
        has_l2_l3 = 'L2_LIMIT_STRENGTH' in _signal_layers and 'L3_FUND_FLOW' in _signal_layers
        golden_pair_bonus = 5.0 if has_l2_l3 else 0.0

        # L6/L1 penalty (改动 D): 纯板块热点或纯热度追高降权
        _pure_weak = (
            _signal_layers
            and _signal_layers <= {'L6_SECTOR_CATALYST', 'L1_HOT_MOMENTUM'}
            and 'L2_LIMIT_STRENGTH' not in _signal_layers
        )
        layer_penalty = -1.0 if _pure_weak else 0.0

        # Capital flow bonus: strong sector/concept fund inflow = bullish
        _sym = candidate.get('symbol', '') or candidate.get('code', '')
        _flow = _capital_flow_lookup.get(_sym, {}) if isinstance(_capital_flow_lookup, dict) else {}
        _concept_flow = safe_float(_flow.get('concept_flow_100m')) or 0.0
        _sector_flow = safe_float(_flow.get('sector_flow_100m')) or 0.0
        _max_flow = max(_concept_flow, _sector_flow)
        if _max_flow >= 50:
            capital_flow_bonus = 4.0
        elif _max_flow >= 20:
            capital_flow_bonus = 2.0
        elif _max_flow >= 5:
            capital_flow_bonus = 1.0
        elif _max_flow <= -10:
            capital_flow_bonus = -2.0
        else:
            capital_flow_bonus = 0.0

        # Priority ordering based on real data:
        # 1. setup_rank: INSTANT_MOMENTUM_SETUP has best performance (57.1% win rate, 28.6% limit-up rate)
        # 2. golden_pair_bonus: L2+L3 = best combo (19% limit-up)
        # 3. l2_bonus: L2 presence = strong signal
        # 4. confluence_bonus: 2-3 layers sweet spot
        # 5. layer_penalty: pure L6/L1 = weak
        # 6. lifecycle, blended score within tier
        return (
            setup_rank * 10.0,    # INSTANT_MOMENTUM_SETUP dominates (57.1% win rate)
            golden_pair_bonus,    # L2+L3 golden pair dominates
            capital_flow_bonus,   # Sector/concept fund inflow direction
            l2_bonus,             # L2 presence is second tier
            confluence_bonus,     # 2-3 layers sweet spot
            layer_penalty,        # pure sector/momentum penalised
            lifecycle_score,
            -stale_decay,
            blended,
            structured,
            sector_opportunity,
            main_theme_core,
            main_theme_alignment,
            pre_limitup,
            first_board,
            net_inflow,
            from_data_directory,
            data_directory_score,
            -rank,
        )

    first_research: Tuple[str, str, str, Dict[str, Any], List[str]] | None = None
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
        if decision == 'RESEARCH_CANDIDATE' and first_research is None:
            first_research = (decision, symbol, reason, features, flags)
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

    if first_research is not None:
        decision, symbol, reason, features, flags = first_research
        bundle['first_research_candidate_diagnostic'] = {
            'decision': decision,
            'symbol': symbol,
            'reason': reason,
            'features': features,
            'flags': flags,
        }
        finalize_daily_ticket_search_result(None, 'NO_PICK')
        return first_research
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


def run_recorder(
    date: str,
    asof_time: str,
    decision: str,
    symbol: str,
    features: Dict[str, Any],
    reason: str,
    dry_run: bool,
    *,
    correction_of: str = "",
) -> Dict[str, Any]:
    features_path = RAW_ROOT / date / asof_time.replace(':', '') / 'recorder_features.json'
    write_json(features_path, features)
    cmd = [
        sys.executable, str(RECORDER),
        '--date', date,
        '--asof-time', asof_time,
        '--decision', decision,
        '--symbol', symbol or '',
        '--features-json', str(features_path),
        '--decision-reason', reason,
        '--xiaochan-gate-status', str(features.get('xiaochan_gate_status', 'ALLOW_FORWARD_PAPER_NO_TRADE')),
        '--xiaoshuju-data-gate-status', str(features.get('xiaoshuju_data_gate_status', features.get('data_gate_status', 'NOT_CALLED'))),
    ]
    if correction_of:
        cmd.extend(['--correction-of', correction_of])
    if dry_run:
        cmd.append('--dry-run')
    try:
        cp = subprocess.run(cmd, cwd=str(BASE), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return {'cmd': cmd, 'returncode': cp.returncode, 'stdout': cp.stdout, 'stderr': cp.stderr, 'features_path': str(features_path)}
    except subprocess.TimeoutExpired as exc:
        return {'cmd': cmd, 'returncode': 124, 'stdout': exc.stdout or '', 'stderr': 'RECORDER_TIMEOUT', 'features_path': str(features_path)}


def _unique_persistence_candidates(candidates: List[Dict[str, Any]], target_count: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    seen = set()
    duplicate_symbols = []
    for candidate in candidates:
        symbol = str(candidate.get('symbol') or candidate.get('code') or '').strip()
        if not symbol:
            continue
        symbol = symbol.zfill(6)
        if symbol in seen:
            duplicate_symbols.append(symbol)
            continue
        seen.add(symbol)
        selected.append(candidate)
        if len(selected) >= target_count:
            break
    duplicate_unique = sorted(set(duplicate_symbols))
    return selected, {
        'source_row_count': len(candidates),
        'raw_full_candidate_pool_rows': len(candidates),
        'target_count': target_count,
        'unique_symbol_count': len(selected),
        'unique_full_candidate_pool_symbols': len(selected),
        'selected_unique_count': len(selected),
        'duplicate_symbol_count': len(duplicate_unique),
        'duplicate_symbols': duplicate_unique,
        'deduplication_applied': bool(duplicate_unique),
        'final_persisted_count': len(selected),
    }


def build_daily_candidate_persistence_payloads(date: str, bundle: Dict[str, Any], features: Dict[str, Any], decision: str, reason: str) -> Dict[str, Any]:
    import datetime as _dt
    from xiaogu_db import classify_candidate_cohort, limitup_gene_signal_values

    full_candidate_pool = bundle.get('full_candidate_pool')
    full_pool_is_explicit = isinstance(full_candidate_pool, list) and bool(full_candidate_pool)
    source_candidates = [candidate for candidate in (full_candidate_pool or bundle.get('paper_scoring_candidates') or []) if isinstance(candidate, dict)]
    if not source_candidates:
        return {'status': 'NO_CANDIDATES', 'scan_session': None, 'daily_candidates': [], 'limitup_gene_signals': []}

    trade_date = _dt.date.fromisoformat(date)
    pool_exclusion_summary = dict(bundle.get('candidate_pool_exclusion_summary') or bundle.get('pool_exclusion_summary') or {})
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
    if dedup_summary['deduplication_applied'] or not pool_exclusion_summary.get('deduplication_applied'):
        pool_exclusion_summary.update(dedup_summary)
    else:
        for key, value in dedup_summary.items():
            pool_exclusion_summary.setdefault(key, value)
        pool_exclusion_summary['final_persisted_count'] = len(candidates)
    pool_exclusion_summary.setdefault('target_count', target_count)
    pool_exclusion_summary.setdefault('legacy_partial_pool', not bool(full_candidate_pool))
    pool_exclusion_summary.setdefault('source_status', 'LEGACY' if not full_candidate_pool else 'PASS')
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
    scan_session = {
        'trade_date': trade_date,
        'scan_time': _dt.datetime.now(),
        'cdp_url': 'manual_pipeline_snapshot',
        'quotes_count': int((bundle.get('market_snapshot') or {}).get('universe_quote_count') or len(candidates)),
        'scored_count': len([candidate for candidate in (bundle.get('scored_candidates') or candidates) if isinstance(candidate, dict)]),
        'passed_count': len([candidate for candidate in (bundle.get('passed_candidates') or []) if isinstance(candidate, dict)]),
        'scan_dir': scan_dir,
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
        candidate_features.setdefault('source_layers', list(row.get('source_layers') or []))
        candidate_features.setdefault('selection_reason', row.get('selection_reason') or reason)
        candidate_features['candidate_pool_context'] = {
            **pool_exclusion_summary,
            'pool_type': 'full_candidate_pool',
            'persisted_candidate_count': len(candidates),
        }
        candidate_diagnostic = dict(top10_by_symbol.get(symbol) or {})
        evaluated_decision = str(
            candidate_diagnostic.get('official_decision_if_evaluated')
            or row.get('decision')
            or ('PAPER_PICK' if official_pick_symbol and symbol == official_pick_symbol else 'CANDIDATE')
        )
        is_top10 = int(row.get('rank') or 999999) <= 10
        selection_outcome = str(candidate_diagnostic.get('selection_outcome') or (
            'OFFICIAL_PICK' if official_pick_symbol and symbol == official_pick_symbol
            else ('TOP10_NOT_SELECTED' if is_top10 or not full_pool_is_explicit else 'FULL_POOL_NOT_SELECTED')
        ))
        selection_outcome_reason = str(candidate_diagnostic.get('selection_outcome_reason') or candidate_diagnostic.get('official_decision_reason_if_evaluated') or reason or '')
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
            'official_decision_if_evaluated': candidate_diagnostic.get('official_decision_if_evaluated') or row.get('decision') or '',
            'official_decision_reason_if_evaluated': candidate_diagnostic.get('official_decision_reason_if_evaluated') or selection_outcome_reason,
        }
        candidate_entry_reason = unique_text_values([
            *(candidate_diagnostic.get('candidate_reasons') or []),
            *(candidate_diagnostic.get('positive_conditions') or []),
            row.get('selection_reason') or '',
            row.get('final_score_explanation') or '',
            'full_candidate_pool_base_filter',
        ])
        not_selected_reason = [] if selection_outcome == 'OFFICIAL_PICK' else unique_text_values([
            *(candidate_diagnostic.get('not_selected_reasons') or []),
            selection_outcome_reason,
        ])
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
            'basis': row.get('ranking_basis') or 'structured_evidence_primary',
            'rank': row.get('rank'),
            'selection_key': list(candidate_diagnostic.get('selection_key') or []),
            'structured_priority_score': row.get('structured_priority_score'),
            'ranking_basis_adjustment_components': ranking_basis_adjustment_components(row),
            'limitup_probability_proxy': limitup_probability_proxy_components(row),
            'paper_pick_risk_explanation_gate': paper_pick_risk_explanation_gate(row),
        }
        ticket_reason = {
            'decision': evaluated_decision,
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
            'rank': row.get('rank'),
            'final_score': row.get('final_score') or row.get('score'),
            'decision': evaluated_decision,
            'is_official_pick': bool(official_pick_symbol and symbol == official_pick_symbol),
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
                't2_return': None,
                't3_return': None,
                't5_return': None,
                'is_limit_up': None,
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
    }


def persist_daily_candidate_snapshot(
    date: str,
    bundle: Dict[str, Any],
    features: Dict[str, Any],
    decision: str,
    reason: str,
    *,
    replace_existing: bool = False,
    correction_of: str = "",
) -> Dict[str, Any]:
    try:
        from xiaogu_db import (
            fetch_daily_candidates,
            has_returns_for_trade_date,
            insert_scan_session,
            prune_daily_candidates_to_symbols,
            upsert_daily_candidate,
            upsert_limitup_gene_signals,
        )
        payloads = build_daily_candidate_persistence_payloads(date, bundle, features, decision, reason)
    except Exception as exc:
        return {'status': 'UNAVAILABLE', 'error': repr(exc), 'written': 0}

    candidates = payloads.get('daily_candidates') or []
    if not candidates:
        return {'status': payloads.get('status') or 'NO_CANDIDATES', 'written': 0}

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

    written = 0
    persisted_signal_rows = 0
    errors: List[Dict[str, Any]] = []
    try:
        if payloads.get('scan_session'):
            insert_scan_session(**payloads['scan_session'])
    except Exception as exc:
        errors.append({'scan_session': 'persist_failed', 'error': repr(exc)[:300]})

    signal_payloads = payloads.get('limitup_gene_signals') or []
    for index, candidate_kwargs in enumerate(candidates):
        symbol = str(candidate_kwargs.get('symbol') or '')
        signal_kwargs = signal_payloads[index] if index < len(signal_payloads) and isinstance(signal_payloads[index], dict) else {}
        try:
            upsert_daily_candidate(**candidate_kwargs)
            written += 1
            if signal_kwargs:
                upsert_limitup_gene_signals(**signal_kwargs)
                persisted_signal_rows += 6
        except Exception as exc:
            errors.append({'symbol': symbol, 'error': repr(exc)[:300]})
            continue
    pruned_stale_count = 0
    if replace_existing and not errors:
        try:
            pruned_stale_count = prune_daily_candidates_to_symbols(
                dt.date.fromisoformat(date),
                [str(candidate.get('symbol') or '') for candidate in candidates],
            )
        except Exception as exc:
            errors.append({'candidate_snapshot_prune': 'failed', 'error': repr(exc)[:300]})
    status = 'OK' if not errors else ('PARTIAL' if written else 'FAILED')
    return {
        'status': status,
        'written': written,
        'candidate_count_expected': len(candidates),
        'candidate_pool_exclusion_summary': payloads.get('candidate_pool_exclusion_summary') or {},
        'persisted_signal_rows': persisted_signal_rows,
        'expected_signal_rows': len(candidates) * 6,
        'replaced_existing_snapshot': bool(replace_existing),
        'pruned_stale_count': pruned_stale_count,
        'correction_archive': correction_archive,
        'errors': errors,
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=latest_completed_trading_day().isoformat())
    ap.add_argument('--asof-time', default='', help='Auto-detect latest scan if empty')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--account-snapshot-json', default='')
    ap.add_argument('--force', action='store_true', help='Append a correction for an existing decision and replace its active DB snapshot')
    ap.add_argument('--no-runtime-date-adjust', action='store_true', help='Keep the requested date even if it is not the latest completed trading day')
    ap.add_argument('--trigger-scan', action='store_true', help='Allow runner to trigger scanner if no existing scan found')
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

    scan_summary = None

    # NN1: runner 先从 scanner 产物 / DB 快照消费，不主动触发在线抓取
    latest = load_latest_eastmoney_scan(args.date)
    if latest:
        scan_summary = latest[1]
        source_time = str(scan_summary.get('source_time', ''))
        if source_time and len(source_time) >= 19:
            args.asof_time = source_time[11:19]
            print(f'LOADED_EXISTING_SCAN: {source_time}', file=sys.stderr, flush=True)
    elif getattr(args, 'trigger_scan', False):
        # 仅在显式 --trigger-scan 时才在线采集
        try:
            scan_summary = run_realtime_scan(args.date, args.asof_time or None)
            scan_source_time = str(scan_summary.get('source_time', ''))
            if scan_source_time and len(scan_source_time) >= 19:
                args.asof_time = scan_source_time[11:19]
                print(f'RUNTIME_SCAN_SOURCE_TIME: {scan_source_time}', file=sys.stderr, flush=True)
        except Exception as exc:
            print(f'RUNTIME_SCAN_FAILED: {exc}', file=sys.stderr, flush=True)
    if not args.asof_time:
        args.asof_time = dt.datetime.now().strftime('%H:%M:%S')

    rule = read_json(RULE_FREEZE)
    if rule.get('rule_version') != RULE_VERSION or rule.get('allow_trade') or not rule.get('paper_only') or not rule.get('no_trade') or rule.get('production_ready'):
        raise SystemExit('rule freeze safety check not pass')

    if scan_summary is not None:
        bundle = build_research_basket_from_latest_scan(args.date, args.asof_time)
        if not isinstance(bundle, dict) or not bundle.get('available'):
            bundle = load_candidate_bundle(args.date, args.asof_time)
    else:
        bundle = load_candidate_bundle(args.date, args.asof_time)
    
    # Enrich candidates with v2 scanner data
    try:
        from xiaogu_eastmoney_web_tabs_scan_v0_1 import load_v2_scanner_data, enrich_candidates_with_v2_data
        v2_data = load_v2_scanner_data(args.date)
        if v2_data:
            candidates = bundle.get('paper_scoring_candidates', [])
            if candidates:
                enriched = enrich_candidates_with_v2_data(candidates, v2_data)
                bundle['paper_scoring_candidates'] = enriched
    except Exception:
        pass
    
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
        # 检查涨停预期，如果强烈则绕过风险控制
        limit_up_expectation = 0
        if candidate_features and isinstance(candidate_features, dict):
            limit_up_expectation = (
                candidate_features.get('limit_up_potential', 0) +
                candidate_features.get('leader_bonus', 0) +
                candidate_features.get('topic_heat_bonus', 0) +
                candidate_features.get('news_bonus', 0)
            )
        # 动态阈值：根据市场强弱调整
        # 市场宽度<30%（下跌）：阈值=8
        # 市场宽度30%-50%（震荡）：阈值=12
        # 市场宽度>50%（上涨）：阈值=15
        market_breadth = float(bundle.get('market_snapshot', {}).get('market_breadth_up_pct') or 0)
        if market_breadth < 30:
            threshold = 8  # 市场下跌，降低阈值
        elif market_breadth < 50:
            threshold = 12  # 市场震荡
        else:
            threshold = 15  # 市场上涨，提高阈值
        if limit_up_expectation >= threshold:
            # 涨停预期强烈，绕过风险控制和数据源过期检查
            reason = reason + f';BYPASS_COOLDOWN:limit_up_expectation={limit_up_expectation},threshold={threshold},breadth={market_breadth}'
            # 同时移除STALE_SOURCE标志，允许出票
            risk_flags = [f for f in risk_flags if f not in ('STALE_SOURCE_MARKET_DATE', 'STALE_SOURCE_TIME', 'SCAN_AFTER_RUNNER_ASOF')]
        elif decision == 'PAPER_PICK' and int(bundle.get('market_snapshot', {}).get('passed_count') or 0) < 1:
            decision = 'RESEARCH_CANDIDATE'
            reason = 'RECENT_T1_NONPROFIT_COOLDOWN:' + reason
        elif decision == 'RESEARCH_CANDIDATE':
            reason = reason + ';RECENT_T1_NONPROFIT_COOLDOWN_USER_CONFIRMED'

    eastmoney_scan = load_latest_eastmoney_scan(args.date)
    if eastmoney_scan is not None:
        summary_path, scan_summary = eastmoney_scan
        raw_files = scan_summary.get('files', {})
        raw_path = raw_files.get('raw', '')
        snapshot = {
            'market_data_source': scan_summary.get('pipeline_version') or scan_summary.get('source') or 'eastmoney_scan',
            'eastmoney_scan_summary_path': str(summary_path),
            'eastmoney_scan_summary': scan_summary,
            'raw_dir': str(Path(raw_path).parent) if raw_path else '',
            'dual_source_index_snapshot': {},
            'source_ok_count': 1,
            'source_total': 1,
            'collected_at': now_iso(),
        }
        data_gate_status = 'PASS'
    else:
        snapshot = collect_index_snapshot(args.date, args.asof_time)
        data_gate_status = 'PASS' if snapshot['source_ok_count'] >= 4 else 'PARTIAL_OR_FAIL'
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

    if decision == 'NO_PICK':
        promoted_candidate, promoted_reason = highest_score_candidate_from_bundle(bundle)
        if isinstance(promoted_candidate, dict) and symbol_for(promoted_candidate):
            promotion_allowed, promotion_block_reason = no_pick_promotion_eligible(promoted_candidate, bundle)
            if promotion_allowed:
                original_no_pick_reason = reason
                original_no_pick_flags = list(risk_flags)
                candidate_features = dict(promoted_candidate)
                symbol = symbol_for(candidate_features)
                decision = 'PAPER_PICK'
                reason = 'NO_PICK_PROMOTED_TO_HIGHEST_SCORE_CANDIDATE'
                candidate_features['original_no_pick_reason'] = original_no_pick_reason
                candidate_features['original_no_pick_flags'] = original_no_pick_flags
                candidate_features['no_pick_promoted_to_highest_score'] = True
                candidate_features['selection_outcome'] = 'OFFICIAL_PICK'
                risk_flags = unique_text_values([*risk_flags, 'NO_PICK_PROMOTED_TO_HIGHEST_SCORE_CANDIDATE'])
                single_target_card = build_single_target_card(
                    decision,
                    symbol,
                    reason,
                    candidate_features,
                    bundle,
                    risk_flags,
                    bool(not args.dry_run),
                )
                candidate_consumption_summary = build_candidate_consumption_summary(
                    bundle,
                    args.date,
                    decision,
                    symbol,
                    reason,
                    candidate_features,
                    risk_flags,
                )
            else:
                promotion_block_flag = 'NO_PICK_PROMOTION_BLOCKED:' + str(promotion_block_reason or 'promotion_gate_not_pass')
                risk_flags = unique_text_values([*risk_flags, promotion_block_flag])
                if isinstance(daily_best_paper_watch, dict) and daily_best_paper_watch.get('symbol') == symbol_for(promoted_candidate):
                    daily_best_paper_watch['promotion_blocked'] = True
                    daily_best_paper_watch['promotion_block_reason'] = promotion_block_flag
                summary_watch = candidate_consumption_summary.get('daily_best_paper_watch') if isinstance(candidate_consumption_summary, dict) else None
                if isinstance(summary_watch, dict) and summary_watch.get('symbol') == symbol_for(promoted_candidate):
                    summary_watch['promotion_blocked'] = True
                    summary_watch['promotion_block_reason'] = promotion_block_flag
                print(f'WARN: NO_PICK promotion blocked: {promotion_block_flag}', file=sys.stderr)
        elif promoted_reason:
            print(f'WARN: NO_PICK promotion unavailable: {promoted_reason}', file=sys.stderr)

    features = {
        'runner': 'xiaogu_forward_d1_1450_runner_v0_1',
        'date': args.date,
        'asof_time': args.asof_time,
        'generated_at': now_iso(),
        'rule_version': RULE_VERSION,
        'runtime_market_snapshot': snapshot,
        'candidate_bundle_status': bundle,
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
        'structured_observation_basket': bundle.get('structured_observation_basket', []),
        'structured_sector_observation_basket': bundle.get('structured_sector_observation_basket', []),
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
        'data_gate_status': data_gate_status,
        'core_market_source_gate': core_source_gate,
        'xiaochan_gate_status': candidate_features.get('xiaochan_gate_status', 'ALLOW_FORWARD_PAPER_NO_TRADE'),
        'xiaoshuju_data_gate_status': candidate_features.get('xiaoshuju_data_gate_status', data_gate_status),
        'risk_flags': risk_flags,
        'asof_leakage_flag': False,
        'loader_semantics_restored': loader_semantics_restored,
        **LOCKED_SAFETY,
    }

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

    # Write runtime file AFTER validation
    runtime_snapshot_path = RAW_ROOT / args.date / args.asof_time.replace(':','') / 'runtime_decision_context.json'
    if correction_of:
        features['correction_context'] = {
            'record_type': 'CORRECTION',
            'correction_of': correction_of,
            'reason': 'LATEST_HEALTHY_CHAIN_SUPERSEDES_STALE_SAME_DAY_DECISION',
        }
    write_json(runtime_snapshot_path, {'features': features, 'decision': decision, 'symbol': symbol, 'decision_reason': reason, 'single_target_card': single_target_card})
    features['runtime_decision_context_path'] = str(runtime_snapshot_path)

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

    daily_candidate_persist_result = persist_daily_candidate_snapshot(
        args.date,
        bundle,
        features,
        decision,
        reason,
        replace_existing=bool(args.force),
        correction_of=correction_of,
    )
    daily_candidate_persist_retry_payload_path = ''
    if daily_candidate_persist_result.get('status') in ('PARTIAL', 'FAILED', 'UNAVAILABLE'):
        daily_candidate_persist_retry_payload_path = write_daily_candidate_persist_retry_payload(
            runtime_snapshot_path,
            args.date,
            bundle,
            features,
            decision,
            reason,
            daily_candidate_persist_result,
        )
    if args.force and daily_candidate_persist_result.get('status') != 'OK':
        print(json.dumps({
            'status': 'CORRECTION_NOT_RECORDED',
            'date': args.date,
            'reason': 'CANDIDATE_SNAPSHOT_REPLACEMENT_NOT_COMPLETE',
            'daily_candidate_persist_result': daily_candidate_persist_result,
        }, ensure_ascii=False, indent=2))
        clear_runner_file_cache()
        raise SystemExit(2)
    if correction_of:
        features['candidate_snapshot_correction'] = {
            **(daily_candidate_persist_result.get('correction_archive') or {}),
            'pruned_stale_count': daily_candidate_persist_result.get('pruned_stale_count', 0),
            'status': daily_candidate_persist_result.get('status'),
        }
        write_json(runtime_snapshot_path, {
            'features': features,
            'decision': decision,
            'symbol': symbol,
            'decision_reason': reason,
            'single_target_card': single_target_card,
        })

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

    # Write pick to PostgreSQL (best-effort; never block ticket output on DB failure)
    db_pick_correction: Dict[str, Any] = {}
    try:
        import datetime as _dt
        from xiaogu_db import insert_pick, mark_pick_active_correction, supersede_active_picks_for_correction
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
        inserted_pick_id = insert_pick(
            trade_date=_dt.date.fromisoformat(args.date),
            symbol=symbol or '',
            decision=decision,
            final_score=float(_score) if _score is not None else None,
            blockers=_blockers,
            features=_pick_features,
            source_layers=_layers,
            rule_version=RULE_VERSION,
            scan_dir=str(snapshot.get('raw_dir') or ''),
            dry_run=bool(args.dry_run),
            stock_name=str(_candidate.get('name') or _candidate.get('stock_name') or ''),
            rank=int(_candidate.get('rank')) if safe_float(_candidate.get('rank')) is not None else None,
            structured_score=safe_float(_candidate.get('structured_score')),
            ranking_basis={
                'basis': _candidate.get('ranking_basis') or 'structured_evidence_primary',
                'structured_priority_score': _candidate.get('structured_priority_score'),
                'rank': _candidate.get('rank'),
            },
            ticket_reason={
                'decision': decision,
                'reason': reason,
                'structured_reasons': features.get('structured_reasons', []),
            },
            selection_reason={
                'candidate_entry_reason': (_pick_features.get('official_explanation_summary') or {}).get('why_selected') or [],
                'decision_reason': reason,
            },
            paper_pick_eligibility=dict(_candidate.get('paper_pick_eligibility') or {}),
            official_target_exclusion_reasons=list(_candidate.get('official_target_exclusion_reasons') or []),
            risk_flags=unique_text_values([*risk_flags, *((_candidate.get('capital_risk_profile') or {}).get('risk_codes') or [])]),
            auxiliary_evidence_status=str(_candidate.get('mainboard_auxiliary_evidence_status') or ''),
            information_coverage_audit_snapshot=dict(bundle.get('information_coverage_audit') or {}),
            source_summary_path=str(bundle.get('scan_summary_path') or ''),
        )
        if correction_of:
            try:
                recorder_payload = json.loads(rec.get('stdout') or '{}')
            except (TypeError, json.JSONDecodeError):
                recorder_payload = {}
            correction_reference = str(
                ((recorder_payload.get('record') or {}).get('raw_data_snapshot_sha256'))
                or correction_of
            )
            superseded_count = supersede_active_picks_for_correction(
                _dt.date.fromisoformat(args.date),
                correction_of=correction_reference,
                replacement_symbol=symbol or '',
                replacement_decision=decision,
                reason='LATEST_HEALTHY_CHAIN_SUPERSEDES_STALE_SAME_DAY_DECISION',
            )
            active_count = mark_pick_active_correction(
                _dt.date.fromisoformat(args.date),
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
    except Exception as exc:
        print(f'WARN: insert_pick failed: {exc}', file=sys.stderr, flush=True)

    out = {
        'date': args.date,
        'asof_time': args.asof_time,
        'decision': decision,
        'symbol': symbol,
        'single_target_card': single_target_card,
        'source_consumption_summary': candidate_consumption_summary.get('source_consumption_summary', {}),
        'candidate_consumption_summary': candidate_consumption_summary,
        'structured_reasons': features.get('structured_reasons', []),
        'buy_plan': features.get('buy_plan', {}),
        'sell_plan': features.get('sell_plan', {}),
        'risk_factors': features.get('risk_factors', []),
        'failure_conditions': features.get('failure_conditions', []),
        'climax_risk': features.get('climax_risk', False),
        **({'no_pick_candidate_diagnostics': no_pick_candidate_diagnostics} if no_pick_candidate_diagnostics is not None else {}),
        **({'daily_best_paper_watch': daily_best_paper_watch} if daily_best_paper_watch is not None else {}),
        'rule_version': RULE_VERSION,
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
