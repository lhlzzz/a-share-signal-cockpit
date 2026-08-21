#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Signal effectiveness analysis — read-only ledger analysis for weight suggestions."""
import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
DEFAULT_LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
CANDIDATE_BUNDLE_ROOT = BASE / 'data' / 'forward_candidate_bundles'
LIVE_SCAN_ROOT = BASE / 'data' / 'live_scan'

SIGNAL_FIELDS = [
    'kline_language_score',
    'fund_flow_score',
    'theme_strength_score',
    'announcement_catalyst_score',
    'news_catalyst_strength',
    'small_account_score',
    'sealed_limit_up',
    'hsgt_institutional_flow',
    'data_directory_capital_flow',
    'signal_pct',
    'close_position_score',
    'fund_flow_momentum',
    'sector_catalyst_score',
    'early_opportunity_score',
    'topic_propagation_score',
    'continuation_gene_score',
    'limitup_capture_score',
    'risk_penalty',
    'MAINLINE_HIT_TOP3',
    'MAINLINE_HIT_TOP5',
    'MAINLINE_AVAILABLE_BUT_NOT_SELECTED',
    'SECTOR_FOLLOWER_SHADOW',
    'MAINLINE_LIMITUP_GENE',
    'FAILED_LIMIT_REVERSAL_RISK',
]
CATEGORICAL_SIGNAL_FIELDS = ('market_regime',)
VALID_MARKET_REGIMES = {'strong', 'weak', 'neutral', 'sideways', 'climax'}

LIMIT_UP_THRESHOLD = 0.095  # ~10% conservative limit-up detection
PRODUCTION_ANALYSIS_START = '2026-06-20'
MAX_VALID_T1_RETURN = 0.31
DEFAULT_MIN_SAMPLES = 20
DELAYED_WINNER_DELTA = 0.03
HIGH_SCORE_THRESHOLD = 80.0
HORIZON_ORDER = ('t1', 't2', 't3', 't5')
HORIZON_DAY_MAP = {'t1': 1, 't2': 2, 't3': 3, 't5': 5}
TRADE_MODE = 'afternoon_buy_next_day_sell'
PRIMARY_RETURN_FIELD = 't1_return'
PRIMARY_TRADE_HORIZON = 't1_next_day_sell'
HORIZON_NOTE = 'T+2/T+3/T+5 are signal-maturation diagnostics, not multi-day holding PnL.'
HORIZON_REPLAY_TOP_N_DEFAULT = 5
HORIZON_REPLAY_MIN_SAMPLES = 3
FOCUS_SYMBOL_NAME_HINTS = {
    '300077': '国民技术',
    '002600': '领益智造',
    '301236': '软通动力',
    '300059': '东方财富',
    '301017': '漱玉平民',
}


def _is_analysis_trading_day(value: Any) -> bool:
    try:
        trade_date = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value or '')[:10])
    except (TypeError, ValueError):
        return False
    try:
        from xiaogu_scheduler import is_trading_day
        return bool(is_trading_day(trade_date))
    except Exception:
        return trade_date.weekday() < 5


def _fnum(v: Any) -> Optional[float]:
    try:
        if v in (None, ''):
            return None
        return float(str(v).replace('%', '').replace(',', ''))
    except (TypeError, ValueError):
        return None


def _present_signal_keys(features: Dict[str, Any]) -> List[str]:
    """Return measurable numeric factors and explicit categorical risk states."""
    keys: List[str] = []
    for field in SIGNAL_FIELDS:
        value = _fnum(features.get(field))
        if value is not None and value > 0:
            keys.append(field)

    for field in CATEGORICAL_SIGNAL_FIELDS:
        value = str(features.get(field) or '').strip().lower()
        if field != 'market_regime' or value in VALID_MARKET_REGIMES:
            keys.append(f'{field}:{value}')

    eligibility = features.get('paper_pick_eligibility')
    if isinstance(eligibility, dict):
        signals = eligibility.get('signals')
        if isinstance(signals, dict) and signals.get('weak_market_requires_direct_confirmation'):
            keys.append('weak_market_requires_direct_confirmation')

    return list(dict.fromkeys(keys))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError:
                pass
    return rows


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ''):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return json.loads(text)
        except Exception:
            return value
    return value


def _listify(value: Any) -> List[str]:
    parsed = _parse_jsonish(value)
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    if isinstance(parsed, dict):
        return [str(k) for k in parsed.keys() if str(k).strip()]
    if parsed in (None, ''):
        return []
    text = str(parsed)
    if text.startswith('[') and text.endswith(']'):
        try:
            maybe = json.loads(text)
            if isinstance(maybe, list):
                return [str(item) for item in maybe if str(item).strip()]
        except Exception:
            pass
    parts = [part.strip() for part in text.replace('|', ',').split(',')]
    return [part for part in parts if part]


def _as_symbol(value: Any) -> str:
    text = str(value or '').strip()
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def _parse_date(value: Any) -> Optional[dt.date]:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    return _fnum(v)


def _bucket_score(score: Any) -> str:
    value = _safe_float(score)
    if value is None:
        return 'score:unknown'
    if value >= 95:
        return 'score:95+'
    if value >= 90:
        return 'score:90-94.9'
    if value >= 80:
        return 'score:80-89.9'
    if value >= 70:
        return 'score:70-79.9'
    return 'score:<70'


def _bucket_rank(rank: Any) -> str:
    value = _safe_float(rank)
    if value is None:
        return 'rank:unknown'
    if value <= 1:
        return 'rank:1'
    if value <= 3:
        return 'rank:2-3'
    if value <= 10:
        return 'rank:4-10'
    if value <= 20:
        return 'rank:11-20'
    return 'rank:21+'


def _return_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return compatibility horizon fields plus explicit trade-mode semantics.

    `t1_return` remains the primary trade return for the afternoon-buy / next-day-sell
    workflow. `t2`/`t3`/`t5` are signal-maturation diagnostics only.
    """
    horizons = {key: _safe_float(record.get(f'{key}_return')) for key in HORIZON_ORDER}
    values = [(key, value) for key, value in horizons.items() if value is not None]
    best_horizon = None
    best = None
    if values:
        best_horizon, best = max(values, key=lambda item: (item[1], -HORIZON_DAY_MAP.get(item[0], 99)))
    days_to_payoff = HORIZON_DAY_MAP.get(best_horizon) if best_horizon else None
    t1 = horizons['t1']
    later_values = [(key, value) for key, value in horizons.items() if key != 't1' and value is not None]
    matured_horizon = None
    matured_return = None
    def later_beats_primary(value: float) -> bool:
        if t1 is None:
            return value > 0
        if t1 < 0:
            return value > 0
        return value > t1
    if later_values:
        later_candidates = [(key, value) for key, value in later_values if later_beats_primary(value)]
        if later_candidates:
            matured_horizon, matured_return = max(
                later_candidates,
                key=lambda item: (item[1], -HORIZON_DAY_MAP.get(item[0], 99)),
            )
    days_to_maturation = HORIZON_DAY_MAP.get(matured_horizon) if matured_horizon else None
    if matured_horizon is None:
        if t1 is None:
            maturation_class = 'unresolved' if best is None else 'weak_multi_horizon'
        elif best is None:
            maturation_class = 'unresolved'
        elif t1 > 0 and best_horizon == 't1':
            maturation_class = 'same_day_next_day_winner'
        elif best <= 0:
            maturation_class = 'weak_multi_horizon'
        else:
            maturation_class = 'weak_multi_horizon'
    elif t1 is not None and t1 < 0 and matured_return is not None and matured_return > 0:
        maturation_class = 'early_noise_repaired'
    elif t1 is not None and t1 > 0 and matured_return is not None and matured_return > t1:
        maturation_class = 'matured_later'
    else:
        maturation_class = 'matured_later'
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
    return {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'primary_trade_return': t1,
        't1_return': t1,
        't2_return': horizons['t2'],
        't3_return': horizons['t3'],
        't5_return': horizons['t5'],
        'best_return': best,
        'max_realized_return': best,
        'best_horizon': best_horizon,
        'days_to_payoff': days_to_payoff,
        'maturation_horizon': matured_horizon,
        'maturation_return': matured_return,
        'days_to_maturation': days_to_maturation,
        'maturation_class': maturation_class,
        'payoff_class': payoff_class,
        'win': t1 is not None and t1 > 0,
        'limit_up': best is not None and best >= LIMIT_UP_THRESHOLD,
        'delayed_gap': None if t1 is None or best is None else round(best - t1, 4),
    }


def _merge_record(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    if not isinstance(update, dict):
        return merged
    for key, value in update.items():
        if value in (None, '', [], {}):
            continue
        if key in ('source_layers', 'blockers', 'candidate_features', 'feature_fields'):
            left = merged.get(key)
            if key == 'candidate_features':
                current = left if isinstance(left, dict) else {}
                extra = value if isinstance(value, dict) else {}
                merged[key] = {**current, **extra}
            else:
                current = _listify(left)
                incoming = _listify(value)
                merged[key] = list(dict.fromkeys([*current, *incoming]))
            continue
        if key in ('t1_return', 't2_return', 't3_return', 't5_return', 'score', 'final_score', 'rank', 'decision', 'pick_decision', 'is_official_pick', 'picked'):
            merged[key] = value if value not in (None, '') else merged.get(key)
            continue
        if merged.get(key) in (None, '', [], {}):
            merged[key] = value
    return merged


def _record_from_candidate(candidate: Dict[str, Any], trade_date: str, source: str) -> Dict[str, Any]:
    features = candidate.get('candidate_features') if isinstance(candidate.get('candidate_features'), dict) else {}
    paper_pick_eligibility = candidate.get('paper_pick_eligibility') if isinstance(candidate.get('paper_pick_eligibility'), dict) else {}
    raw_blockers = _listify(candidate.get('blockers')) + _listify(candidate.get('blocked_reasons'))
    raw_blockers += _listify(paper_pick_eligibility.get('blockers'))
    raw_blockers += _listify(candidate.get('official_target_exclusion_reasons'))
    source_layers = _listify(candidate.get('source_layers'))
    if isinstance(candidate.get('source_layer'), str):
        source_layers.append(str(candidate.get('source_layer')))
    return {
        'trade_date': str(trade_date or candidate.get('trade_date') or candidate.get('date') or '')[:10],
        'symbol': _as_symbol(candidate.get('symbol') or candidate.get('code')),
        'name': candidate.get('name') or candidate.get('stock_name') or '',
        'rank': candidate.get('rank'),
        'score': candidate.get('score') if candidate.get('score') is not None else candidate.get('final_score'),
        'final_score': candidate.get('final_score') if candidate.get('final_score') is not None else candidate.get('score'),
        'decision': candidate.get('decision') or candidate.get('pick_decision') or '',
        'picked': bool(candidate.get('picked') or candidate.get('is_official_pick') or str(candidate.get('decision') or '').upper() == 'PAPER_PICK'),
        'is_official_pick': bool(candidate.get('is_official_pick') or str(candidate.get('decision') or '').upper() == 'PAPER_PICK'),
        'source_layers': source_layers,
        'blockers': list(dict.fromkeys(raw_blockers)),
        'candidate_features': {**features, **(candidate if isinstance(candidate, dict) else {})},
        'source': source,
        'source_path': candidate.get('source_path') or candidate.get('_bundle_path') or candidate.get('scan_summary_path') or '',
        't1_return': candidate.get('t1_return'),
        't2_return': candidate.get('t2_return'),
        't3_return': candidate.get('t3_return'),
        't5_return': candidate.get('t5_return'),
    }


def _file_candidate_rows(focus_symbols: set[str]) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    records_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    sources: Dict[str, int] = {'bundles': 0, 'live_scan': 0, 'ledger': 0}

    bundle_files = sorted(CANDIDATE_BUNDLE_ROOT.rglob('*_research_basket_candidate.json'))
    for path in bundle_files:
        bundle = read_json(path)
        if not bundle:
            continue
        trade_date = str(bundle.get('date') or bundle.get('source_market_date') or path.parent.name or '')[:10]
        candidates = bundle.get('paper_scoring_candidates')
        if not isinstance(candidates, list):
            candidates = []
        if isinstance(bundle.get('candidate'), dict):
            candidates = [bundle.get('candidate'), *candidates]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            record = _record_from_candidate({**bundle, **candidate, 'source_path': str(path)}, trade_date, 'bundle')
            key = (record['trade_date'], record['symbol'])
            if focus_symbols and record['symbol'] not in focus_symbols:
                continue
            records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
            sources['bundles'] += 1

    live_scan_files = sorted(LIVE_SCAN_ROOT.rglob('xiaogu_scan_summary.json'))
    for path in live_scan_files:
        summary = read_json(path)
        rows = []
        if summary:
            rows = summary.get('paper_scoring_candidates') or summary.get('scored_rows') or []
        if not rows:
            continue
        trade_date = ''
        if summary:
            trade_date = str(summary.get('source_time') or summary.get('date') or '')[:10]
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _as_symbol(row.get('symbol') or row.get('code'))
            if focus_symbols and symbol not in focus_symbols:
                continue
            record = _record_from_candidate({**row, 'source_path': str(path)}, trade_date or str(row.get('trade_date') or row.get('date') or '')[:10], 'live_scan')
            key = (record['trade_date'], record['symbol'])
            records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
            sources['live_scan'] += 1

    for row in _iter_ledger_rows(DEFAULT_LEDGER):
        if not isinstance(row, dict):
            continue
        symbol = _as_symbol(row.get('symbol'))
        trade_date = str(row.get('date') or row.get('trade_date') or '')[:10]
        if focus_symbols and symbol not in focus_symbols:
            continue
        key = (trade_date, symbol)
        record = {
            'trade_date': trade_date,
            'symbol': symbol,
            'decision': row.get('decision') or row.get('result_status') or '',
            'picked': str(row.get('decision') or '').upper() == 'PAPER_PICK',
            'is_official_pick': str(row.get('decision') or '').upper() == 'PAPER_PICK',
            'score': _safe_float((row.get('features_used') or {}).get('score')) if isinstance(row.get('features_used'), dict) else None,
            'final_score': _safe_float((row.get('features_used') or {}).get('final_score')) if isinstance(row.get('features_used'), dict) else None,
            'source_layers': _listify((row.get('features_used') or {}).get('source_layers')) if isinstance(row.get('features_used'), dict) else [],
            'blockers': _listify((row.get('features_used') or {}).get('blockers')) if isinstance(row.get('features_used'), dict) else [],
            'candidate_features': row.get('features_used') if isinstance(row.get('features_used'), dict) else {},
            't1_return': row.get('t1_return'),
            't2_return': row.get('t2_return'),
            't3_return': row.get('t3_return'),
            't5_return': row.get('t5_return'),
            'source': 'ledger',
        }
        records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
        sources['ledger'] += 1

    return list(records_by_key.values()), sources


def _iter_ledger_rows(path: Path):
    if not path.exists():
        return
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                continue


def _db_candidate_rows(focus_symbols: set[str]) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    try:
        from xiaogu_db import engine as _eng
        from sqlalchemy import text as _sql
    except Exception as exc:
        return [], {'mode': 'db', 'loaded': False, 'error': repr(exc)}

    records_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    source_counts = {'daily_candidates': 0, 'picks': 0, 'returns': 0}

    try:
        with _eng.connect() as conn:
            daily_rows = conn.execute(_sql(
                """
                SELECT
                    trade_date, symbol, stock_name, rank, final_score, decision, signal_pct,
                    close_position_score, fund_flow_momentum, sector_catalyst_score, early_opportunity_score,
                    topic_propagation_score, market_regime, blockers, hard_gate_status, raw_json
                FROM daily_candidates
                """
            )).mappings().all()
            for row in daily_rows:
                symbol = _as_symbol(row.get('symbol'))
                if focus_symbols and symbol not in focus_symbols:
                    continue
                trade_date = str(row.get('trade_date') or '')[:10]
                if not _is_analysis_trading_day(trade_date):
                    continue
                candidate_features = {
                    'signal_pct': row.get('signal_pct'),
                    'close_position_score': row.get('close_position_score'),
                    'fund_flow_momentum': row.get('fund_flow_momentum'),
                    'sector_catalyst_score': row.get('sector_catalyst_score'),
                    'early_opportunity_score': row.get('early_opportunity_score'),
                    'topic_propagation_score': row.get('topic_propagation_score'),
                    'market_regime': row.get('market_regime'),
                    'hard_gate_status': _parse_jsonish(row.get('hard_gate_status')),
                    'raw_json': _parse_jsonish(row.get('raw_json')),
                    'source_layers': _listify(row.get('source_layers')),
                    'candidate_lifecycle': (_parse_jsonish(row.get('raw_json')) or {}).get('candidate_lifecycle') if isinstance(_parse_jsonish(row.get('raw_json')), dict) else {},
                }
                raw_json = _parse_jsonish(row.get('raw_json'))
                if isinstance(raw_json, dict):
                    if isinstance(raw_json.get('paper_pick_eligibility'), dict):
                        candidate_features['paper_pick_eligibility'] = raw_json.get('paper_pick_eligibility')
                    if isinstance(raw_json.get('signals'), dict):
                        candidate_features['signals'] = raw_json.get('signals')
                    if isinstance(raw_json.get('structured_score'), dict):
                        candidate_features['structured_score'] = raw_json.get('structured_score')
                record = {
                    'trade_date': trade_date,
                    'symbol': symbol,
                    'name': row.get('stock_name') or '',
                    'rank': row.get('rank'),
                    'score': row.get('final_score'),
                    'final_score': row.get('final_score'),
                    'decision': row.get('decision') or '',
                    'picked': bool(row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
                    'is_official_pick': bool(row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
                    'source_layers': _listify(row.get('source_layers')),
                    'blockers': _listify(row.get('blockers')),
                    'candidate_features': candidate_features,
                    'candidate_lifecycle': candidate_features.get('candidate_lifecycle') if isinstance(candidate_features.get('candidate_lifecycle'), dict) else {},
                    'paper_pick_eligibility': candidate_features.get('paper_pick_eligibility') if isinstance(candidate_features.get('paper_pick_eligibility'), dict) else {},
                    'signals': candidate_features.get('signals') if isinstance(candidate_features.get('signals'), dict) else {},
                    'structured_score': candidate_features.get('structured_score') if isinstance(candidate_features.get('structured_score'), dict) else {},
                    'setup_class': str((candidate_features.get('candidate_lifecycle') or {}).get('setup_class') or (candidate_features.get('paper_pick_eligibility') or {}).get('setup_class') or (candidate_features.get('signals') or {}).get('setup_class') or ''),
                    'source': 'db_daily_candidates',
                }
                key = (trade_date, symbol)
                records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
                source_counts['daily_candidates'] += 1

            pick_rows = conn.execute(_sql(
                """
                SELECT trade_date, symbol, decision, final_score, blockers, features, source_layers
                FROM picks
                """
            )).mappings().all()
            for row in pick_rows:
                symbol = _as_symbol(row.get('symbol'))
                if focus_symbols and symbol not in focus_symbols:
                    continue
                trade_date = str(row.get('trade_date') or '')[:10]
                if not _is_analysis_trading_day(trade_date):
                    continue
                key = (trade_date, symbol)
                record = {
                    'trade_date': trade_date,
                    'symbol': symbol,
                    'decision': row.get('decision') or '',
                    'picked': str(row.get('decision') or '').upper() == 'PAPER_PICK',
                    'is_official_pick': str(row.get('decision') or '').upper() == 'PAPER_PICK',
                    'score': row.get('final_score'),
                    'final_score': row.get('final_score'),
                    'blockers': _listify(row.get('blockers')),
                    'source_layers': _listify(row.get('source_layers')),
                    'candidate_features': _parse_jsonish(row.get('features')) if isinstance(_parse_jsonish(row.get('features')), dict) else {},
                    'candidate_lifecycle': ((_parse_jsonish(row.get('features')) or {}).get('candidate_lifecycle') if isinstance(_parse_jsonish(row.get('features')), dict) else {}),
                    'paper_pick_eligibility': ((_parse_jsonish(row.get('features')) or {}).get('paper_pick_eligibility') if isinstance(_parse_jsonish(row.get('features')), dict) else {}),
                    'signals': ((_parse_jsonish(row.get('features')) or {}).get('signals') if isinstance(_parse_jsonish(row.get('features')), dict) else {}),
                    'structured_score': ((_parse_jsonish(row.get('features')) or {}).get('structured_score') if isinstance(_parse_jsonish(row.get('features')), dict) else {}),
                    'setup_class': str(((_parse_jsonish(row.get('features')) or {}).get('candidate_lifecycle') or {}).get('setup_class') if isinstance(_parse_jsonish(row.get('features')), dict) else ''),
                    'source': 'db_picks',
                }
                records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
                source_counts['picks'] += 1

            return_rows = conn.execute(_sql(
                """
                SELECT trade_date, symbol, t1_return, t2_return, t3_return, t5_return
                FROM returns
                """
            )).mappings().all()
            for row in return_rows:
                symbol = _as_symbol(row.get('symbol'))
                if focus_symbols and symbol not in focus_symbols:
                    continue
                trade_date = str(row.get('trade_date') or '')[:10]
                if not _is_analysis_trading_day(trade_date):
                    continue
                key = (trade_date, symbol)
                record = {
                    'trade_date': trade_date,
                    'symbol': symbol,
                    't1_return': row.get('t1_return'),
                    't2_return': row.get('t2_return'),
                    't3_return': row.get('t3_return'),
                    't5_return': row.get('t5_return'),
                    'source': 'db_returns',
                }
                records_by_key[key] = _merge_record(records_by_key.get(key, {}), record)
                source_counts['returns'] += 1
    except Exception as exc:
        return [], {'mode': 'db', 'loaded': False, 'error': repr(exc)}

    return list(records_by_key.values()), {'mode': 'db', 'loaded': bool(records_by_key), 'error': '', **source_counts}


def _weight_suggestion(limit_up_rate: float, count: int, min_samples: int) -> str:
    if count < min_samples:
        return 'INSUFFICIENT_DATA'
    if limit_up_rate > 0.5:
        return 'INCREASE'
    if limit_up_rate < 0.2:
        return 'DECREASE'
    return 'MAINTAIN'


def analyze_signal_effectiveness(
    ledger_path: Path,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    source: str = 'db',
    trade_dates: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Aggregate factor effectiveness from the canonical replay population.

    The DB path measures the previous-day Top-10 candidate cohort plus
    PAPER_PICK records, joined to the returns table by the horizon replayer.
    Ledger mode is retained only for explicit offline analysis.
    """
    source_details: Dict[str, Any] = {}
    if source == 'db':
        try:
            rows, source_details = _collect_horizon_replay_sources(ledger_path, [])
        except Exception:
            rows, source_details = [], {'db': {'loaded': False, 'error': 'db_replay_source_unavailable'}}
        replay = build_horizon_replay(rows, top_n=10, source_details=source_details)
        decisions = list(replay.get('records') or [])
    else:
        rows = load_jsonl(ledger_path)
        decisions = [r for r in rows if r.get('record_type') in ('DECISION', 'CORRECTION')]

    if source == 'db':
        decisions = [
            row for row in decisions
            if _is_analysis_trading_day(str(row.get('trade_date') or row.get('date') or '')[:10])
        ]

    normalized_trade_dates = {
        str(value)[:10]
        for value in (trade_dates or set())
        if str(value).strip()
    }
    if normalized_trade_dates:
        decisions = [
            row for row in decisions
            if str(row.get('trade_date') or row.get('date') or '')[:10] in normalized_trade_dates
        ]
    total_picks = len(decisions)

    fills: Dict[str, float] = {}
    if source != 'db':
        for row in rows:
            if row.get('record_type') == 'RESULT_FILL':
                key = f"{row.get('date')}:{row.get('symbol')}"
                t1 = _fnum(row.get('t1_return'))
                if t1 is not None:
                    fills[key] = t1

    effective = []
    excluded_return_rows = 0
    for decision in decisions:
        key = f"{decision.get('trade_date') or decision.get('date')}:{decision.get('symbol')}"
        t1 = _fnum(decision.get('t1_return'))
        if t1 is None:
            t1 = fills.get(key)
        if t1 is None:
            continue
        trade_date = str(decision.get('trade_date') or decision.get('date') or '')[:10]
        if source == 'db' and (
            trade_date < PRODUCTION_ANALYSIS_START
            or abs(t1) > MAX_VALID_T1_RETURN
        ):
            excluded_return_rows += 1
            continue
        effective.append({'decision': decision, 't1_return': t1})

    filled_picks = len(effective)

    # Overall stats
    if effective:
        limit_ups = [e for e in effective if e['t1_return'] >= LIMIT_UP_THRESHOLD]
        overall_limit_up_rate = round(len(limit_ups) / filled_picks, 3)
        overall_avg_t1 = round(sum(e['t1_return'] for e in effective) / filled_picks, 4)
    else:
        overall_limit_up_rate = 0.0
        overall_avg_t1 = 0.0

    # Per-signal aggregation
    signal_stats: Dict[str, Dict[str, Any]] = {}
    for effective_record in effective:
        decision = effective_record['decision']
        features = decision.get('candidate_features') or {}
        structured = decision.get('structured_score') or {}
        components = structured.get('components') or {}
        merged = {**features, **components}
        eligibility = decision.get('paper_pick_eligibility')
        if isinstance(eligibility, dict):
            merged['paper_pick_eligibility'] = eligibility

        for signal_key in _present_signal_keys(merged):
            stats = signal_stats.setdefault(signal_key, {'count': 0, 'limit_ups': 0, 'returns': []})
            stats['count'] += 1
            stats['returns'].append(effective_record['t1_return'])
            if effective_record['t1_return'] >= LIMIT_UP_THRESHOLD:
                stats['limit_ups'] += 1

    signal_effectiveness = []
    for field, stats in sorted(signal_stats.items(), key=lambda x: -x[1]['count']):
        cnt = stats['count']
        lu_rate = round(stats['limit_ups'] / cnt, 3) if cnt else 0.0
        avg_ret = round(sum(stats['returns']) / cnt, 4) if cnt else 0.0
        signal_effectiveness.append({
            'signal_key': field,
            'present_count': cnt,
            'limit_up_rate': lu_rate,
            'avg_t1_return': avg_ret,
            'weight_suggestion': _weight_suggestion(lu_rate, cnt, min_samples),
        })

    # Sort by limit_up_rate desc
    signal_effectiveness.sort(key=lambda x: -x['limit_up_rate'])

    # Per-pool aggregation
    pool_stats: Dict[str, Dict[str, Any]] = {}
    for effective_record in effective:
        layers = _listify(effective_record['decision'].get('source_layers'))
        for pool in layers:
            stats = pool_stats.setdefault(pool, {'count': 0, 'limit_ups': 0, 'returns': []})
            stats['count'] += 1
            stats['returns'].append(effective_record['t1_return'])
            if effective_record['t1_return'] >= LIMIT_UP_THRESHOLD:
                stats['limit_ups'] += 1

    pool_effectiveness = []
    for pool, stats in sorted(pool_stats.items(), key=lambda x: -x[1]['count']):
        cnt = stats['count']
        lu_rate = round(stats['limit_ups'] / cnt, 3) if cnt else 0.0
        avg_ret = round(sum(stats['returns']) / cnt, 4) if cnt else 0.0
        pool_effectiveness.append({
            'pool': pool,
            'count': cnt,
            'limit_up_rate': lu_rate,
            'avg_return': avg_ret,
        })

    pool_effectiveness.sort(key=lambda x: -x['limit_up_rate'])

    return {
        'analysis_date': dt.date.today().isoformat(),
        'total_picks': total_picks,
        'filled_picks': filled_picks,
        'overall_limit_up_rate': overall_limit_up_rate,
        'overall_avg_t1_return': overall_avg_t1,
        'signal_effectiveness': signal_effectiveness,
        'pool_effectiveness': pool_effectiveness,
        'source': source,
        'source_details': source_details,
        'excluded_return_rows': excluded_return_rows,
    }


def persist_signal_effectiveness(
    analysis_result: Dict[str, Any],
    analysis_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a factor-analysis snapshot without deleting prior DB evidence."""
    target_date = str(analysis_date or analysis_result.get('analysis_date') or dt.date.today().isoformat())[:10]
    from sqlalchemy import text as _sql
    from xiaogu_db import engine as _engine

    signals = analysis_result.get('signal_effectiveness') or []
    statement = _sql("""
        INSERT INTO signal_effectiveness (
            analysis_date, signal_key, present_count, limit_up_rate,
            avg_t1_return, weight_suggestion, data_version
        ) VALUES (
            CAST(:analysis_date AS date), :signal_key, :present_count, :limit_up_rate,
            :avg_t1_return, :weight_suggestion, :data_version
        )
        ON CONFLICT (analysis_date, signal_key) DO UPDATE SET
            present_count = EXCLUDED.present_count,
            limit_up_rate = EXCLUDED.limit_up_rate,
            avg_t1_return = EXCLUDED.avg_t1_return,
            weight_suggestion = EXCLUDED.weight_suggestion,
            data_version = EXCLUDED.data_version,
            updated_at = NOW()
    """)
    payloads = [
        {
            'analysis_date': target_date,
            'signal_key': str(signal['signal_key']),
            'present_count': int(signal['present_count']),
            'limit_up_rate': float(signal['limit_up_rate']),
            'avg_t1_return': float(signal['avg_t1_return']),
            'weight_suggestion': str(signal['weight_suggestion']),
            'data_version': 'signal_effectiveness_v0_2',
        }
        for signal in signals
    ]
    with _engine.begin() as conn:
        if payloads:
            conn.execute(statement, payloads)
    return {'persisted_count': len(payloads), 'analysis_date': target_date}


def _normalize_candidate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    features = record.get('candidate_features') if isinstance(record.get('candidate_features'), dict) else {}
    raw_json = features.get('raw_json') if isinstance(features.get('raw_json'), dict) else {}
    if not raw_json and isinstance(record.get('raw_json'), dict):
        raw_json = record.get('raw_json')
    candidate_lifecycle = record.get('candidate_lifecycle') if isinstance(record.get('candidate_lifecycle'), dict) else {}
    if not candidate_lifecycle and isinstance(features.get('candidate_lifecycle'), dict):
        candidate_lifecycle = features.get('candidate_lifecycle')
    if not candidate_lifecycle and isinstance(raw_json.get('candidate_lifecycle'), dict):
        candidate_lifecycle = raw_json.get('candidate_lifecycle')
    paper_pick_eligibility = record.get('paper_pick_eligibility') if isinstance(record.get('paper_pick_eligibility'), dict) else {}
    if not paper_pick_eligibility and isinstance(features.get('paper_pick_eligibility'), dict):
        paper_pick_eligibility = features.get('paper_pick_eligibility')
    if not paper_pick_eligibility and isinstance(raw_json.get('paper_pick_eligibility'), dict):
        paper_pick_eligibility = raw_json.get('paper_pick_eligibility')
    signals = record.get('signals') if isinstance(record.get('signals'), dict) else {}
    if not signals and isinstance(features.get('signals'), dict):
        signals = features.get('signals')
    if not signals and isinstance(raw_json.get('signals'), dict):
        signals = raw_json.get('signals')
    structured_score = record.get('structured_score') if isinstance(record.get('structured_score'), dict) else {}
    if not structured_score and isinstance(features.get('structured_score'), dict):
        structured_score = features.get('structured_score')
    if not structured_score and isinstance(raw_json.get('structured_score'), dict):
        structured_score = raw_json.get('structured_score')
    setup_class = str(
        record.get('setup_class')
        or candidate_lifecycle.get('setup_class')
        or paper_pick_eligibility.get('setup_class')
        or (paper_pick_eligibility.get('signals') or {}).get('setup_class')
        or signals.get('setup_class')
        or raw_json.get('setup_class')
        or ''
    )
    return_profile = _return_profile(record)
    source_layers = _listify(record.get('source_layers'))
    blockers = _listify(record.get('blockers'))
    if 'paper_pick_eligibility' in record and isinstance(record.get('paper_pick_eligibility'), dict):
        blockers.extend(_listify(record['paper_pick_eligibility'].get('blockers')))
    blockers.extend(_listify(record.get('official_target_exclusion_reasons')))
    return {
        'trade_date': str(record.get('trade_date') or record.get('date') or '')[:10],
        'symbol': _as_symbol(record.get('symbol') or record.get('code')),
        'name': record.get('name') or record.get('stock_name') or '',
        'rank': record.get('rank'),
        'score': _safe_float(record.get('score')) if record.get('score') is not None else _safe_float(record.get('final_score')),
        'final_score': _safe_float(record.get('final_score')) if record.get('final_score') is not None else _safe_float(record.get('score')),
        'decision': str(record.get('decision') or record.get('pick_decision') or ''),
        'picked': bool(record.get('picked') or record.get('is_official_pick') or str(record.get('decision') or '').upper() == 'PAPER_PICK'),
        'is_official_pick': bool(record.get('is_official_pick') or str(record.get('decision') or '').upper() == 'PAPER_PICK'),
        'source_layers': list(dict.fromkeys(source_layers)),
        'blockers': list(dict.fromkeys(blockers)),
        'candidate_features': {**features, **record},
        'candidate_lifecycle': candidate_lifecycle,
        'paper_pick_eligibility': paper_pick_eligibility,
        'signals': signals,
        'structured_score': structured_score,
        'setup_class': setup_class,
        'source': record.get('source') or record.get('source_path') or 'unknown',
        'source_path': str(record.get('source_path') or ''),
        **return_profile,
    }


def _group_stats(records: List[Dict[str, Any]], bucket_key: str) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for record in records:
        values = _listify(record.get(bucket_key))
        if bucket_key == 'score_bucket':
            values = [_bucket_score(record.get('score') if record.get('score') is not None else record.get('final_score'))]
        elif bucket_key == 'rank_bucket':
            values = [_bucket_rank(record.get('rank'))]
        if not values:
            values = ['unknown']
        ret = _return_profile(record)
        for value in values:
            group = groups.setdefault(value, {'count': 0, 'wins': 0, 'limit_ups': 0, 'returns': [], 'best_returns': []})
            group['count'] += 1
            group['wins'] += int(bool(ret['win']))
            group['limit_ups'] += int(bool(ret['limit_up']))
            if ret['t1_return'] is not None:
                group['returns'].append(ret['t1_return'])
            if ret['best_return'] is not None:
                group['best_returns'].append(ret['best_return'])
    output: List[Dict[str, Any]] = []
    for bucket, stats in sorted(groups.items(), key=lambda item: (-item[1]['count'], item[0])):
        total = stats['count'] or 1
        returns = stats['returns']
        best_returns = stats['best_returns']
        output.append({
            bucket_key: bucket,
            'count': stats['count'],
            'win_rate': round(stats['wins'] / total, 3),
            'limit_up_rate': round(stats['limit_ups'] / total, 3),
            'avg_return': round(sum(returns) / len(returns), 4) if returns else 0.0,
            'avg_best_return': round(sum(best_returns) / len(best_returns), 4) if best_returns else 0.0,
        })
    return output


def _diagnostic_record_view(record: Dict[str, Any]) -> Dict[str, Any]:
    ret = _return_profile(record)
    score = _safe_float(record.get('score')) if record.get('score') is not None else _safe_float(record.get('final_score'))
    risk_score = _safe_float(record.get('risk_adjusted_score'))
    if risk_score is None:
        risk_score = score
    candidate_lifecycle = record.get('candidate_lifecycle') if isinstance(record.get('candidate_lifecycle'), dict) else {}
    setup_class = str(record.get('setup_class') or candidate_lifecycle.get('setup_class') or '')
    return {
        'trade_date': record.get('trade_date'),
        'symbol': record.get('symbol'),
        'name': record.get('name'),
        'decision': record.get('decision'),
        'picked': bool(record.get('picked')),
        'score': score,
        'final_score': _safe_float(record.get('final_score')) if record.get('final_score') is not None else score,
        'risk_adjusted_score': risk_score,
        'rank': record.get('rank'),
        'source_layers': record.get('source_layers') or [],
        'blockers': record.get('blockers') or [],
        'trade_mode': ret['trade_mode'],
        'primary_return_field': ret['primary_return_field'],
        'primary_trade_horizon': ret['primary_trade_horizon'],
        'primary_trade_return': ret['primary_trade_return'],
        't1_return': ret['t1_return'],
        't2_return': ret['t2_return'],
        't3_return': ret['t3_return'],
        't5_return': ret['t5_return'],
        'best_return': ret['best_return'],
        'max_realized_return': ret['max_realized_return'],
        'best_horizon': ret['best_horizon'],
        'days_to_payoff': ret['days_to_payoff'],
        'maturation_horizon': ret['maturation_horizon'],
        'maturation_return': ret['maturation_return'],
        'days_to_maturation': ret['days_to_maturation'],
        'maturation_class': ret['maturation_class'],
        'payoff_class': record.get('payoff_class') or ret['payoff_class'],
        'returns': {
            't1': ret['t1_return'],
            't2': ret['t2_return'],
            't3': ret['t3_return'],
            't5': ret['t5_return'],
            'best': ret['best_return'],
        },
        'win': ret['win'],
        'limit_up': ret['limit_up'],
        'delayed_gap': ret['delayed_gap'],
        'source': record.get('source'),
        'consecutive_appearances': record.get('consecutive_appearances'),
        'repeat_window_count': record.get('repeat_window_count'),
        'setup_class': setup_class,
        'stale_decay': record.get('stale_decay'),
    }


def _repeat_bucket(count: int) -> str:
    if count <= 1:
        return 'repeat:1'
    if count == 2:
        return 'repeat:2'
    if count == 3:
        return 'repeat:3'
    return 'repeat:4+'


def build_candidate_diagnostics(
    records: List[Dict[str, Any]],
    focus_symbols: Optional[List[str]] = None,
    source: str = 'unknown',
    source_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_focus = [_as_symbol(symbol) for symbol in (focus_symbols or []) if _as_symbol(symbol)]
    focus_set = set(normalized_focus)
    normalized_records: List[Dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        normalized = _normalize_candidate_record(record)
        if focus_set and normalized['symbol'] not in focus_set:
            continue
        normalized_records.append(normalized)

    if not normalized_records:
        return {
            'mode': 'candidate_diagnostics',
            'trade_mode': TRADE_MODE,
            'primary_return_field': PRIMARY_RETURN_FIELD,
            'horizon_note': 'T+2/T+3/T+5 are signal-maturation diagnostics, not multi-day holding PnL.',
            'source': source,
            'focus_symbols': normalized_focus,
            'record_count': 0,
            'summary': {
                'instant_winners': 0,
                'delayed_winners': 0,
                'matured_later_candidates': 0,
                'missed_winners': 0,
                'missed_delayed_winners': 0,
                'false_positives': 0,
                'early_noise': 0,
                'stale_candidates': 0,
                'focus_symbol_count': len(normalized_focus),
            },
            'instant_winners': [],
            'delayed_winners': [],
            'matured_later_candidates': [],
            'missed_winners': [],
            'missed_delayed_winners': [],
            'false_positives': [],
            'early_noise': [],
            'stale_candidates': [],
            'aggregates': {
                'source_layers': [],
                'blockers': [],
                'score_buckets': [],
                'rank_buckets': [],
                'setup_class_buckets': [],
                'consecutive_appearance_buckets': [],
                'payoff_horizon_buckets': [],
                'feature_fields': {
                    'win_rate': 0.0,
                    'limit_up_rate': 0.0,
                    'avg_return': 0.0,
                    'avg_best_return': 0.0,
                },
            },
            'source_details': source_details or {},
        }

    streak_by_index = [1] * len(normalized_records)
    symbol_groups: Dict[str, List[tuple[int, Optional[dt.date]]]] = {}
    for index, record in enumerate(normalized_records):
        symbol_groups.setdefault(record['symbol'], []).append((index, _parse_date(record.get('trade_date'))))
    for entries in symbol_groups.values():
        entries.sort(key=lambda item: (item[1] or dt.date.min, item[0]))
        streak = 0
        prev_date: Optional[dt.date] = None
        for index, current_date in entries:
            if current_date is None:
                streak = 1
            elif prev_date is not None and 0 <= (current_date - prev_date).days <= 5:
                streak += 1
            else:
                streak = 1
            streak_by_index[index] = streak
            prev_date = current_date

    instant_winners: List[Dict[str, Any]] = []
    matured_later_candidates: List[Dict[str, Any]] = []
    missed_winners: List[Dict[str, Any]] = []
    missed_matured_later_candidates: List[Dict[str, Any]] = []
    false_positives: List[Dict[str, Any]] = []
    early_noise: List[Dict[str, Any]] = []
    stale_candidates: List[Dict[str, Any]] = []

    for index, record in enumerate(normalized_records):
        ret = _return_profile(record)
        picked = bool(record.get('picked'))
        blocked = bool(record.get('blockers'))
        weekday_blocked = 'WEEKDAY_BLOCKED' in set(_listify(record.get('blockers')))
        score = _safe_float(record.get('score')) if record.get('score') is not None else _safe_float(record.get('final_score'))
        risk_score = _safe_float(record.get('risk_adjusted_score'))
        if risk_score is None:
            risk_score = score
        consecutive_appearances = streak_by_index[index]
        repeat_bucket = _repeat_bucket(consecutive_appearances)
        payoff_horizon = ret['best_horizon'] or 'unknown'
        payoff_class = ret['payoff_class']
        diag_record = {
            **record,
            'risk_adjusted_score': risk_score,
            'consecutive_appearances': consecutive_appearances,
            'repeat_window_count': consecutive_appearances,
            'consecutive_appearance_bucket': repeat_bucket,
            'payoff_horizon': payoff_horizon,
            'payoff_class': payoff_class,
            'stale_decay': record.get('stale_decay'),
        }
        diag_view = _diagnostic_record_view(diag_record)
        if payoff_class == 'instant_winner':
            instant_winners.append(diag_view)
        if payoff_class == 'delayed_winner':
            matured_later_candidates.append(diag_view)
        if ret['maturation_class'] == 'early_noise_repaired':
            early_noise.append(diag_view)
        if payoff_class in ('instant_winner', 'delayed_winner') and (blocked or not picked):
            missed_winners.append(diag_view)
        if payoff_class == 'delayed_winner' and (blocked or not picked):
            missed_matured_later_candidates.append(diag_view)
        primary_trade_return = ret['primary_trade_return']
        weak_multi_horizon = ret['maturation_class'] in ('weak_multi_horizon', 'unresolved')
        if (
            (picked or (score is not None and score >= HIGH_SCORE_THRESHOLD))
            and primary_trade_return is not None
            and primary_trade_return <= 0
            and weak_multi_horizon
        ):
            false_positives.append(diag_view)
        if consecutive_appearances >= 3 and payoff_class in ('weak', 'unresolved') and ret['max_realized_return'] is not None and ret['max_realized_return'] <= 0.02:
            stale_candidates.append(diag_view)

    aggregates = {
        'source_layers': _group_stats(normalized_records, 'source_layers'),
        'blockers': _group_stats(normalized_records, 'blockers'),
        'score_buckets': _group_stats(normalized_records, 'score_bucket'),
        'rank_buckets': _group_stats(normalized_records, 'rank_bucket'),
        'setup_class_buckets': _group_stats(normalized_records, 'setup_class'),
        'consecutive_appearance_buckets': _group_stats([
            {**record, 'consecutive_appearance_bucket': _repeat_bucket(streak_by_index[idx])}
            for idx, record in enumerate(normalized_records)
        ], 'consecutive_appearance_bucket'),
        'payoff_horizon_buckets': _group_stats([
            {**record, 'payoff_horizon': _return_profile(record)['best_horizon'] or 'unknown'}
            for record in normalized_records
        ], 'payoff_horizon'),
    }
    aggregates['feature_fields'] = {
        'win_rate': round(sum(1 for record in normalized_records if _return_profile(record)['win']) / len(normalized_records), 3) if normalized_records else 0.0,
        'limit_up_rate': round(sum(1 for record in normalized_records if _return_profile(record)['limit_up']) / len(normalized_records), 3) if normalized_records else 0.0,
        'avg_return': round(
            sum(v for v in (_return_profile(record)['t1_return'] for record in normalized_records) if v is not None) /
            max(1, sum(1 for record in normalized_records if _return_profile(record)['t1_return'] is not None)),
            4,
        ) if normalized_records else 0.0,
        'avg_best_return': round(
            sum(v for v in (_return_profile(record)['best_return'] for record in normalized_records) if v is not None) /
            max(1, sum(1 for record in normalized_records if _return_profile(record)['best_return'] is not None)),
            4,
        ) if normalized_records else 0.0,
    }
    aggregates['setup_class_performance'] = _setup_class_performance(normalized_records)

    return {
        'mode': 'candidate_diagnostics',
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'horizon_note': 'T+2/T+3/T+5 are signal-maturation diagnostics, not multi-day holding PnL.',
        'source': source,
        'focus_symbols': normalized_focus,
        'record_count': len(normalized_records),
        'summary': {
            'instant_winners': len(instant_winners),
            'delayed_winners': len(matured_later_candidates),
            'matured_later_candidates': len(matured_later_candidates),
            'missed_winners': len(missed_winners),
            'missed_delayed_winners': len(missed_matured_later_candidates),
            'false_positives': len(false_positives),
            'early_noise': len(early_noise),
            'stale_candidates': len(stale_candidates),
            'focus_symbol_count': len(normalized_focus),
        },
        'instant_winners': instant_winners,
        'missed_winners': missed_winners,
        'missed_delayed_winners': missed_matured_later_candidates,
        'false_positives': false_positives,
        'matured_later_candidates': matured_later_candidates,
        # Compatibility alias: older callers still expect delayed_winners.
        'delayed_winners': matured_later_candidates,
        'early_noise': early_noise,
        'stale_candidates': stale_candidates,
        'aggregates': aggregates,
        'source_details': source_details or {},
    }


def _setup_class_performance(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for record in records:
        setup_class = str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or record.get('paper_pick_eligibility', {}).get('setup_class') or 'UNKNOWN')
        ret = _return_profile(record)
        group = groups.setdefault(setup_class, {
            'count': 0,
            'instant_count': 0,
            'delayed_count': 0,
            'wins': 0,
            'limit_ups': 0,
            't1_returns': [],
            'best_returns': [],
            'delayed_gaps': [],
        })
        group['count'] += 1
        group['wins'] += int(bool(ret['win']))
        group['limit_ups'] += int(bool(ret['limit_up']))
        if ret['payoff_class'] == 'instant_winner':
            group['instant_count'] += 1
        if ret['payoff_class'] == 'delayed_winner':
            group['delayed_count'] += 1
        if ret['t1_return'] is not None:
            group['t1_returns'].append(ret['t1_return'])
        if ret['best_return'] is not None:
            group['best_returns'].append(ret['best_return'])
        if ret['delayed_gap'] is not None:
            group['delayed_gaps'].append(ret['delayed_gap'])

    result: List[Dict[str, Any]] = []
    for setup_class, stats in sorted(groups.items(), key=lambda item: (-item[1]['count'], item[0])):
        count = stats['count'] or 1
        t1_returns = stats['t1_returns']
        best_returns = stats['best_returns']
        delayed_gaps = stats['delayed_gaps']
        result.append({
            'setup_class': setup_class,
            'count': stats['count'],
            'instant_count': stats['instant_count'],
            'delayed_count': stats['delayed_count'],
            'win_rate': round(stats['wins'] / count, 3),
            'limit_up_rate': round(stats['limit_ups'] / count, 3),
            'avg_t1_return': round(sum(t1_returns) / len(t1_returns), 4) if t1_returns else 0.0,
            'avg_best_return': round(sum(best_returns) / len(best_returns), 4) if best_returns else 0.0,
            'avg_delayed_gap': round(sum(delayed_gaps) / len(delayed_gaps), 4) if delayed_gaps else 0.0,
        })
    return result


def analyze_candidate_diagnostics(
    focus_symbols: Optional[List[str]] = None,
    ledger_path: Path = DEFAULT_LEDGER,
) -> Dict[str, Any]:
    normalized_focus = [_as_symbol(symbol) for symbol in (focus_symbols or []) if _as_symbol(symbol)]
    focus_set = set(normalized_focus)
    db_records, db_info = _db_candidate_rows(focus_set)
    if db_records or db_info.get('loaded'):
        file_records: List[Dict[str, Any]] = []
        if focus_set:
            missing_focus = focus_set - {_as_symbol(record.get('symbol')) for record in db_records}
            if missing_focus:
                file_records, file_info = _file_candidate_rows(missing_focus)
                db_records.extend(file_records)
                db_info = {**db_info, 'file_fallback': file_info, 'file_fallback_used': True}
        return build_candidate_diagnostics(db_records, normalized_focus, source='db', source_details=db_info)

    file_records, file_info = _file_candidate_rows(focus_set)
    if not file_records:
        ledger_records = list(_iter_ledger_rows(ledger_path))
        file_info = {**file_info, 'ledger_records': len(ledger_records)}
        return build_candidate_diagnostics(ledger_records, normalized_focus, source='ledger', source_details=file_info)
    return build_candidate_diagnostics(file_records, normalized_focus, source='filesystem', source_details=file_info)


def _runner_module():
    import xiaogu_forward_runner as _runner
    return _runner


def _merge_unique_text(values: List[Any]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for value in values:
        for item in _listify(value):
            if item in seen:
                continue
            merged.append(item)
            seen.add(item)
    return merged


def _summarize_horizon_blocker(reason: Any) -> str:
    text = ' '.join(str(reason or '').split()).strip()
    if not text:
        return ''
    lower = text.lower()
    if 'weak_underwater_without_forward_confirmation' in lower:
        return 'weak_underwater_without_forward_confirmation'
    if 'chase_high_without_limitup_confirmation' in lower:
        return 'opportunity_hard_block:CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
    if 'regulatory_hard_block' in lower:
        if len(text) > 160 or any(token in lower for token in ('eastmoney', 'html', 'http', 'body', 'div', 'script')) or '\n' in str(reason or ''):
            return 'regulatory_hard_block:LONG_EASTMONEY_PAGE_TEXT'
        if ':' in text:
            suffix = text.split(':', 1)[1].strip()
            if suffix:
                return 'regulatory_hard_block:' + suffix[:120]
        return 'regulatory_hard_block'
    if len(text) > 200 or '\n' in str(reason or ''):
        if any(token in lower for token in ('eastmoney', '东财', 'html', 'http', 'body', 'page')):
            return 'LONG_EASTMONEY_PAGE_TEXT'
        return text[:120].rstrip() + '...'
    return text


def _clean_horizon_blockers(blockers: Any) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for blocker in _listify(blockers):
        summary = _summarize_horizon_blocker(blocker)
        if summary and summary not in seen:
            cleaned.append(summary)
            seen.add(summary)
    return cleaned


def _normalize_horizon_source_row(row: Dict[str, Any], source_kind: str) -> Dict[str, Any]:
    candidate_features = row.get('candidate_features') if isinstance(row.get('candidate_features'), dict) else {}
    feature_payload = row.get('features') if isinstance(row.get('features'), dict) else {}
    if not candidate_features and feature_payload:
        candidate_features = feature_payload
    factor_snapshot = row.get('factor_snapshot') if isinstance(row.get('factor_snapshot'), dict) else {}
    if factor_snapshot:
        candidate_features = {**candidate_features, **factor_snapshot}
    raw_json = row.get('raw_json') if isinstance(row.get('raw_json'), dict) else {}
    if not candidate_features and raw_json:
        candidate_features = raw_json
    candidate_lifecycle = row.get('candidate_lifecycle') if isinstance(row.get('candidate_lifecycle'), dict) else {}
    if not candidate_lifecycle and isinstance(candidate_features, dict):
        lifecycle_from_features = candidate_features.get('candidate_lifecycle')
        if isinstance(lifecycle_from_features, dict):
            candidate_lifecycle = lifecycle_from_features
    if not candidate_lifecycle and isinstance(raw_json, dict):
        lifecycle_from_raw = raw_json.get('candidate_lifecycle')
        if isinstance(lifecycle_from_raw, dict):
            candidate_lifecycle = lifecycle_from_raw
    paper_pick_eligibility = row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    if not paper_pick_eligibility and isinstance(candidate_features, dict):
        nested_eligibility = candidate_features.get('paper_pick_eligibility')
        if isinstance(nested_eligibility, dict):
            paper_pick_eligibility = nested_eligibility
    signals = row.get('signals') if isinstance(row.get('signals'), dict) else {}
    if not signals and isinstance(candidate_features, dict):
        nested_signals = candidate_features.get('signals')
        if isinstance(nested_signals, dict):
            signals = nested_signals
    if not candidate_lifecycle and isinstance(paper_pick_eligibility.get('signals'), dict):
        lifecycle_from_eligibility = paper_pick_eligibility.get('signals', {}).get('candidate_lifecycle')
        if isinstance(lifecycle_from_eligibility, dict):
            candidate_lifecycle = lifecycle_from_eligibility
    setup_class = str(
        row.get('setup_class')
        or candidate_lifecycle.get('setup_class')
        or signals.get('setup_class')
        or paper_pick_eligibility.get('setup_class')
        or (paper_pick_eligibility.get('signals') or {}).get('setup_class')
        or ''
    )
    source_layers = _merge_unique_text([
        row.get('source_layers'),
        row.get('source_layer'),
        candidate_features.get('source_layers') if isinstance(candidate_features, dict) else [],
    ])
    blockers = _merge_unique_text([
        row.get('blockers'),
        row.get('blocked_reasons'),
        candidate_features.get('blockers') if isinstance(candidate_features, dict) else [],
        candidate_features.get('blocked_reasons') if isinstance(candidate_features, dict) else [],
    ])
    trade_date = str(row.get('trade_date') or row.get('date') or row.get('source_market_date') or row.get('trade_day') or '')[:10]
    symbol = _as_symbol(row.get('symbol') or row.get('code') or row.get('stock_code'))
    name = row.get('name') or row.get('stock_name') or row.get('stockName') or ''
    if not name and isinstance(candidate_features, dict):
        name = candidate_features.get('name') or candidate_features.get('stock_name') or ''
    return {
        'source_kind': source_kind,
        'trade_date': trade_date,
        'symbol': symbol,
        'name': name,
        'rank': row.get('rank'),
        'score': _safe_float(row.get('score')) if row.get('score') is not None else _safe_float(row.get('final_score')),
        'final_score': _safe_float(row.get('final_score')) if row.get('final_score') is not None else _safe_float(row.get('score')),
        'decision': str(row.get('decision') or row.get('pick_decision') or ''),
        'historical_decision': str(row.get('historical_decision') or row.get('decision') or row.get('pick_decision') or ''),
        'picked': bool(row.get('picked') or row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
        'is_official_pick': bool(row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
        'source_layers': source_layers,
        'blockers': blockers,
        'paper_pick_eligibility': paper_pick_eligibility,
        'signals': signals,
        'candidate_features': {**candidate_features, **(raw_json if isinstance(raw_json, dict) else {})},
        'candidate_lifecycle': candidate_lifecycle,
        'setup_class': setup_class,
        'price': _safe_float(row.get('price')) if row.get('price') is not None else _safe_float(row.get('close_price')) if row.get('close_price') is not None else _safe_float(row.get('open_price')),
        'one_lot_cost': _safe_float(row.get('one_lot_cost')),
        't1_return': row.get('t1_return'),
        't2_return': row.get('t2_return'),
        't3_return': row.get('t3_return'),
        't5_return': row.get('t5_return'),
        'source': row.get('source') or source_kind,
        'source_path': str(row.get('source_path') or row.get('candidate_bundle_path') or row.get('_bundle_path') or ''),
        'candidate_pool_kind': row.get('candidate_pool_kind') or ('daily_candidate' if source_kind in ('db_daily_candidate', 'bundle_candidate') else ''),
        'decision_source_kind': row.get('decision_source_kind') or ('paper_pick' if source_kind in ('db_pick', 'ledger_decision') else ''),
    }


def _trace_roles(trace: Dict[str, Any]) -> List[str]:
    source_kind = str(trace.get('source_kind') or '')
    roles = []
    if source_kind == 'db_mixed':
        return ['candidate', 'decision', 'return']
    if source_kind in ('db_daily_candidate', 'bundle_candidate'):
        roles.append('candidate')
    if source_kind in ('db_pick', 'ledger_decision') or bool(trace.get('decision')):
        roles.append('decision')
        roles.append('candidate')
    if source_kind in ('db_return', 'ledger_result_fill') or any(trace.get(key) is not None for key in ('t1_return', 't2_return', 't3_return', 't5_return')):
        roles.append('return')
    return roles


def _merge_horizon_record(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if key == 'source_traces':
            merged.setdefault('source_traces', [])
            merged['source_traces'].extend(value if isinstance(value, list) else [value])
            continue
        if value in (None, '', [], {}):
            continue
        if key in ('source_layers', 'blockers'):
            merged[key] = list(dict.fromkeys([* _listify(merged.get(key)), * _listify(value)]))
            continue
        if key == 'candidate_features':
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            incoming = value if isinstance(value, dict) else {}
            merged[key] = {**current, **incoming}
            continue
        if key in ('paper_pick_eligibility', 'signals'):
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            incoming = value if isinstance(value, dict) else {}
            merged[key] = {**current, **incoming}
            continue
        if key == 'candidate_lifecycle':
            current = merged.get(key) if isinstance(merged.get(key), dict) else {}
            incoming = value if isinstance(value, dict) else {}
            merged[key] = {**current, **incoming}
            continue
        if key == 'historical_decision' and value:
            merged[key] = str(value)
            continue
        if merged.get(key) in (None, '', [], {}):
            merged[key] = value
    return merged


def _resolve_horizon_returns(record: Dict[str, Any]) -> Dict[str, Any]:
    traces = record.get('source_traces') if isinstance(record.get('source_traces'), list) else []
    resolved = {'t1_return': None, 't2_return': None, 't3_return': None, 't5_return': None}
    status = {'sources': [], 'maturation_source': ''}
    priority = {'db_return': 0, 'ledger_result_fill': 1, 'bundle_candidate': 2, 'db_daily_candidate': 2, 'db_pick': 2, 'ledger_decision': 2}
    ordered_traces = sorted(traces, key=lambda item: priority.get(str(item.get('source_kind') or ''), 99))
    for horizon in ('t1', 't2', 't3', 't5'):
        key = f'{horizon}_return'
        source_entry = {
            'status': 'missing',
            'source_kind': 'missing',
            'source_role': 'trade_return' if horizon == 't1' else 'signal_maturation',
            'source_trade_date': '',
            'source_symbol': _as_symbol(record.get('symbol')),
            'source_value': None,
        }
        for trace in ordered_traces:
            value = _safe_float(trace.get(key))
            if value is None:
                continue
            resolved[key] = value
            source_kind = str(trace.get('source_kind') or 'unknown')
            source_entry = {
                'status': 'available',
                'source_kind': source_kind,
                'source_role': 'trade_return' if horizon == 't1' else 'signal_maturation',
                'source_trade_date': str(trace.get('trade_date') or ''),
                'source_symbol': _as_symbol(trace.get('symbol') or record.get('symbol')),
                'source_value': value,
            }
            status['sources'].append(source_kind)
            break
        status[horizon] = source_entry
    return {
        **resolved,
        'return_data_status': status,
        'return_data_complete': all(resolved[key] is not None for key in resolved),
    }


def _apply_later_candidate_maturation_evidence(records: List[Dict[str, Any]]) -> None:
    by_symbol: Dict[str, List[tuple[dt.date, Dict[str, Any]]]] = {}
    for record in records:
        symbol = _as_symbol(record.get('symbol'))
        trade_date = _parse_date(record.get('trade_date'))
        if not symbol or trade_date is None:
            continue
        by_symbol.setdefault(symbol, []).append((trade_date, record))

    horizon_for_days = {2: 't2_return', 3: 't3_return', 5: 't5_return'}
    for symbol, dated_records in by_symbol.items():
        dated_records.sort(key=lambda item: item[0])
        for index, (trade_date, record) in enumerate(dated_records):
            status = record.get('return_data_status') if isinstance(record.get('return_data_status'), dict) else {}
            maturation_sources: List[Dict[str, Any]] = []
            for later_date, later_record in dated_records[index + 1:]:
                later_primary = _safe_float(later_record.get('t1_return'))
                if later_primary is None:
                    continue
                horizon_days = (later_date - trade_date).days + 1
                return_key = horizon_for_days.get(horizon_days)
                if not return_key or record.get(return_key) is not None:
                    continue
                record[return_key] = later_primary
                horizon_name = return_key.replace('_return', '')
                status[horizon_name] = {
                    'status': 'maturation_evidence',
                    'source_kind': 'later_candidate_primary_return',
                    'source_role': 'diagnostic_maturation',
                    'source_trade_date': str(later_record.get('trade_date') or ''),
                    'source_symbol': _as_symbol(later_record.get('symbol') or symbol),
                    'source_value': later_primary,
                    'days_ahead': horizon_days,
                }
                status.setdefault('sources', [])
                status['sources'].append('later_candidate_primary_return')
                maturation_sources.append(status[horizon_name])
            if maturation_sources:
                record['maturation_source'] = 'later_candidate_primary_return'
                record['maturation_sources'] = maturation_sources
                record['return_data_status'] = status
                refreshed = _return_profile(record)
                record.update(refreshed)
                record['return_data_complete'] = all(record.get(f'{key}_return') is not None for key in HORIZON_ORDER)
                explanation = record.get('explanation') if isinstance(record.get('explanation'), dict) else {}
                if explanation:
                    explanation = dict(explanation)
                    explanation.update({
                        'maturation_horizon': record.get('maturation_horizon'),
                        'maturation_return': record.get('maturation_return'),
                        'maturation_source': record.get('maturation_source') or '',
                        'primary_trade_return': record.get('primary_trade_return'),
                        't1_return': record.get('t1_return'),
                        't2_return': record.get('t2_return'),
                        't3_return': record.get('t3_return'),
                        't5_return': record.get('t5_return'),
                        'best_horizon': record.get('best_horizon'),
                    })
                    record['explanation'] = explanation


def _default_horizon_decision_replayer(record: Dict[str, Any], candidate: Dict[str, Any], bundle: Dict[str, Any]) -> Dict[str, Any]:
    try:
        runner = _runner_module()
        decision, symbol, reason, features, flags = runner.decision_for_candidate(candidate, bundle, record.get('trade_date') or '')
        return {
            'final_decision': decision,
            'symbol': symbol,
            'replay_reason': reason,
            'replay_eligible': bool(decision == 'PAPER_PICK'),
            'replay_mode': 'runner_decision',
            'replay_features': features,
            'replay_flags': flags,
        }
    except Exception as exc:
        eligibility = {}
        try:
            runner = _runner_module()
            eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
        except Exception:
            eligibility = {}
        return {
            'final_decision': str(record.get('decision') or ('PAPER_PICK' if record.get('is_paper_pick') else 'NO_PICK')),
            'symbol': str(record.get('symbol') or candidate.get('symbol') or ''),
            'replay_reason': f'eligibility_only_fallback:{type(exc).__name__}',
            'replay_eligible': bool(eligibility.get('eligible')) if isinstance(eligibility, dict) else bool(record.get('is_paper_pick')),
            'replay_mode': 'fallback',
            'replay_features': eligibility.get('signals') if isinstance(eligibility, dict) else {},
            'replay_flags': eligibility.get('blockers') if isinstance(eligibility, dict) else [],
        }


def _collect_horizon_replay_sources(
    ledger_path: Path,
    focus_symbols: List[str],
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    normalized_focus = [_as_symbol(symbol) for symbol in focus_symbols if _as_symbol(symbol)]
    focus_set = set(normalized_focus)
    rows: List[Dict[str, Any]] = []
    details: Dict[str, Any] = {
        'db': {'loaded': False, 'error': ''},
        'ledger': {'loaded': False, 'error': ''},
        'files': {'loaded': False, 'error': ''},
    }
    try:
        from sqlalchemy import text as _sql
        from xiaogu_db import engine as _eng
        with _eng.connect() as conn:
            daily_rows = conn.execute(_sql(
                """
                SELECT
                    trade_date, symbol, stock_name, rank, final_score, is_official_pick, decision,
                    open_price, close_price, high_price, low_price, signal_pct, close_position_score,
                    fund_flow_momentum, sector_catalyst_score, early_opportunity_score,
                    topic_propagation_score, market_regime, blockers, hard_gate_status, raw_json,
                    candidate_features, factor_snapshot, eligibility_snapshot, selection_diagnostics,
                    source_layers
                FROM daily_candidates
                """
            )).mappings().all()
            pick_rows = conn.execute(_sql(
                """
                SELECT trade_date, symbol, decision, final_score, blockers, features, source_layers
                FROM picks
                """
            )).mappings().all()
            return_rows = conn.execute(_sql(
                """
                SELECT trade_date, symbol, t1_return, t2_return, t3_return, t5_return
                FROM returns
                """
            )).mappings().all()
        daily_records = []
        for row in daily_rows:
            daily_records.append(_normalize_horizon_source_row({
                'trade_date': row.get('trade_date'),
                'symbol': row.get('symbol'),
                'name': row.get('stock_name'),
                'rank': row.get('rank'),
                'score': row.get('final_score'),
                'final_score': row.get('final_score'),
                'decision': row.get('decision') or ('PAPER_PICK' if row.get('is_official_pick') else ''),
                'picked': bool(row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
                'is_official_pick': bool(row.get('is_official_pick') or str(row.get('decision') or '').upper() == 'PAPER_PICK'),
                'price': row.get('close_price') if row.get('close_price') is not None else row.get('open_price'),
                'close_price': row.get('close_price'),
                'open_price': row.get('open_price'),
                'high_price': row.get('high_price'),
                'low_price': row.get('low_price'),
                'signal_pct': row.get('signal_pct'),
                'close_position_score': row.get('close_position_score'),
                'fund_flow_momentum': row.get('fund_flow_momentum'),
                'sector_catalyst_score': row.get('sector_catalyst_score'),
                'early_opportunity_score': row.get('early_opportunity_score'),
                'topic_propagation_score': row.get('topic_propagation_score'),
                'market_regime': row.get('market_regime'),
                'blockers': row.get('blockers'),
                'source_layers': row.get('source_layers'),
                'candidate_features': row.get('candidate_features') if isinstance(row.get('candidate_features'), dict) else _parse_jsonish(row.get('candidate_features')),
                'factor_snapshot': row.get('factor_snapshot') if isinstance(row.get('factor_snapshot'), dict) else _parse_jsonish(row.get('factor_snapshot')),
                'paper_pick_eligibility': row.get('eligibility_snapshot') if isinstance(row.get('eligibility_snapshot'), dict) else _parse_jsonish(row.get('eligibility_snapshot')),
                'selection_diagnostics': row.get('selection_diagnostics') if isinstance(row.get('selection_diagnostics'), dict) else _parse_jsonish(row.get('selection_diagnostics')),
                'raw_json': row.get('raw_json') if isinstance(row.get('raw_json'), dict) else _parse_jsonish(row.get('raw_json')),
            }, 'db_daily_candidate'))
        pick_records = []
        for row in pick_rows:
            features = _parse_jsonish(row.get('features'))
            pick_records.append(_normalize_horizon_source_row({
                'trade_date': row.get('trade_date'),
                'symbol': row.get('symbol'),
                'decision': row.get('decision'),
                'score': row.get('final_score'),
                'final_score': row.get('final_score'),
                'blockers': row.get('blockers'),
                'source_layers': row.get('source_layers'),
                'features': features if isinstance(features, dict) else {},
                'candidate_features': features if isinstance(features, dict) else {},
            }, 'db_pick'))
        return_records = []
        for row in return_rows:
            return_records.append(_normalize_horizon_source_row({
                'trade_date': row.get('trade_date'),
                'symbol': row.get('symbol'),
                't1_return': row.get('t1_return'),
                't2_return': row.get('t2_return'),
                't3_return': row.get('t3_return'),
                't5_return': row.get('t5_return'),
            }, 'db_return'))
        rows.extend(daily_records)
        rows.extend(pick_records)
        rows.extend(return_records)
        details['db'] = {
            'loaded': bool(daily_records or pick_records or return_records),
            'error': '',
            'record_count': len(daily_records) + len(pick_records) + len(return_records),
            'daily_candidates': len(daily_records),
            'picks': len(pick_records),
            'returns': len(return_records),
        }
    except Exception as exc:
        details['db'] = {'loaded': False, 'error': repr(exc), 'record_count': 0, 'daily_candidates': 0, 'picks': 0, 'returns': 0}

    ledger_rows = []
    try:
        for row in _iter_ledger_rows(ledger_path):
            if not isinstance(row, dict):
                continue
            record_type = str(row.get('record_type') or '').upper()
            if record_type in ('DECISION', 'CORRECTION'):
                ledger_rows.append(_normalize_horizon_source_row({
                    'trade_date': row.get('date') or row.get('trade_date'),
                    'symbol': row.get('symbol'),
                    'name': row.get('name') or row.get('stock_name') or '',
                    'rank': (row.get('features_used') or {}).get('rank') if isinstance(row.get('features_used'), dict) else row.get('rank'),
                    'score': (row.get('features_used') or {}).get('score') if isinstance(row.get('features_used'), dict) else row.get('score'),
                    'final_score': (row.get('features_used') or {}).get('final_score') if isinstance(row.get('features_used'), dict) else row.get('final_score'),
                    'decision': row.get('decision'),
                    'blockers': (row.get('features_used') or {}).get('blockers') if isinstance(row.get('features_used'), dict) else row.get('blockers'),
                    'source_layers': (row.get('features_used') or {}).get('source_layers') if isinstance(row.get('features_used'), dict) else row.get('source_layers'),
                    'candidate_features': (row.get('features_used') or {}).get('candidate_features') if isinstance(row.get('features_used'), dict) else {},
                    't1_return': row.get('t1_return'),
                    't2_return': row.get('t2_return'),
                    't3_return': row.get('t3_return'),
                    't5_return': row.get('t5_return'),
                    'source_path': ledger_path,
                }, 'ledger_decision'))
            elif record_type == 'RESULT_FILL':
                ledger_rows.append(_normalize_horizon_source_row({
                    'trade_date': row.get('date') or row.get('trade_date'),
                    'symbol': row.get('symbol'),
                    't1_return': row.get('t1_return'),
                    't2_return': row.get('t2_return'),
                    't3_return': row.get('t3_return'),
                    't5_return': row.get('t5_return'),
                    'source_path': ledger_path,
                }, 'ledger_result_fill'))
    except Exception as exc:
        details['ledger'] = {'loaded': False, 'error': repr(exc)}
    else:
        details['ledger'] = {'loaded': bool(ledger_rows), 'record_count': len(ledger_rows), 'error': ''}
    rows.extend(ledger_rows)

    focus_seen = {_as_symbol(row.get('symbol')) for row in rows if _as_symbol(row.get('symbol'))}
    missing_focus = [symbol for symbol in normalized_focus if symbol and symbol not in focus_seen]
    file_rows: List[Dict[str, Any]] = []
    if missing_focus or not rows:
        try:
            file_candidates, file_info = _file_candidate_rows(set(missing_focus))
            for row in file_candidates:
                source = str(row.get('source') or '')
                if source in ('bundle', 'live_scan'):
                    source_kind = 'bundle_candidate'
                elif source == 'ledger':
                    source_kind = 'ledger_decision' if row.get('decision') else 'ledger_result_fill'
                else:
                    source_kind = 'bundle_candidate'
                file_rows.append({**row, 'source_kind': source_kind})
            details['files'] = file_info
            details['files']['record_count'] = len(file_candidates)
        except Exception as exc:
            details['files'] = {'loaded': False, 'error': repr(exc)}
    rows.extend(file_rows)

    return rows, details


def _finalize_horizon_record(record: Dict[str, Any], top_n: int, focus_set: set[str], decision_replayer: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    traces = record.get('source_traces') if isinstance(record.get('source_traces'), list) else []
    candidate_traces = [trace for trace in traces if 'candidate' in _trace_roles(trace)]
    decision_traces = [trace for trace in traces if 'decision' in _trace_roles(trace)]
    return_traces = [trace for trace in traces if 'return' in _trace_roles(trace)]
    base = dict(record)
    base['source_layers'] = _merge_unique_text([base.get('source_layers')])
    for trace in candidate_traces:
        base = _merge_horizon_record(base, trace)
    for trace in decision_traces:
        base = _merge_horizon_record(base, trace)
        if trace.get('decision'):
            base['decision'] = trace.get('decision')
            base['is_paper_pick'] = bool(str(trace.get('decision') or '').upper() == 'PAPER_PICK')
            base['picked'] = bool(base.get('is_paper_pick'))
    base['historical_decision'] = str(
        base.get('historical_decision')
        or base.get('decision')
        or ('PAPER_PICK' if base.get('is_paper_pick') else 'NO_PICK')
        or 'NO_PICK'
    ).upper()
    base['raw_blockers'] = _merge_unique_text([base.get('blockers')])
    base['blockers'] = _clean_horizon_blockers(base.get('raw_blockers'))
    if not base.get('source_layers'):
        base['source_layers'] = []
    if not base.get('blockers'):
        base['blockers'] = []
    if not base.get('candidate_features'):
        base['candidate_features'] = {}
    resolved_returns = _resolve_horizon_returns({**base, 'source_traces': return_traces or traces})
    base.update(resolved_returns)
    base.update(_return_profile({**base, **resolved_returns}))
    if base.get('maturation_return') is not None:
        primary_trade_return = _safe_float(base.get('t1_return'))
        maturation_return = _safe_float(base.get('maturation_return'))
        if maturation_return is not None and (primary_trade_return is None or maturation_return > primary_trade_return):
            base['maturation_class'] = 'matured_later'
    if not base.get('candidate_lifecycle') and isinstance(base.get('candidate_features'), dict):
        lifecycle = base['candidate_features'].get('candidate_lifecycle')
        if isinstance(lifecycle, dict):
            base['candidate_lifecycle'] = lifecycle
    if not isinstance(base.get('paper_pick_eligibility'), dict) and isinstance(base.get('candidate_features'), dict):
        eligibility = base['candidate_features'].get('paper_pick_eligibility')
        if isinstance(eligibility, dict):
            base['paper_pick_eligibility'] = eligibility
    if not isinstance(base.get('signals'), dict) and isinstance(base.get('candidate_features'), dict):
        signals = base['candidate_features'].get('signals')
        if isinstance(signals, dict):
            base['signals'] = signals
    candidate = {
        **(base.get('candidate_features') if isinstance(base.get('candidate_features'), dict) else {}),
        'symbol': base.get('symbol'),
        'code': base.get('symbol'),
        'name': base.get('name'),
        'rank': base.get('rank'),
        'score': base.get('score'),
        'final_score': base.get('final_score'),
        'price': base.get('price'),
        'one_lot_cost': base.get('one_lot_cost'),
        'source_layers': base.get('source_layers'),
        'blockers': base.get('blockers'),
        'paper_pick_eligibility': base.get('paper_pick_eligibility') if isinstance(base.get('paper_pick_eligibility'), dict) else {},
        'signals': base.get('signals') if isinstance(base.get('signals'), dict) else {},
        'candidate_features': base.get('candidate_features'),
        'candidate_lifecycle': base.get('candidate_lifecycle') if isinstance(base.get('candidate_lifecycle'), dict) else {},
    }
    if not candidate.get('candidate_lifecycle') and isinstance(base.get('candidate_features'), dict):
        lifecycle = base['candidate_features'].get('candidate_lifecycle')
        if isinstance(lifecycle, dict):
            candidate['candidate_lifecycle'] = lifecycle
    bundle = {
        'available': True,
        'date': base.get('trade_date'),
        'source_market_date': base.get('trade_date'),
        '_runner_asof_time': f"{base.get('trade_date') or ''} 14:50:00",
        'data_gate_status': 'PASS',
        'candidate_source': base.get('source'),
        'candidate': candidate,
        'paper_scoring_candidates': [candidate],
    }
    replay_result: Dict[str, Any]
    if decision_replayer is None:
        replay_result = _default_horizon_decision_replayer(base, candidate, bundle)
    else:
        replay_raw = decision_replayer(base, candidate, bundle)
        if isinstance(replay_raw, dict):
            replay_result = dict(replay_raw)
        elif isinstance(replay_raw, (list, tuple)):
            replay_result = {
                'final_decision': replay_raw[0] if len(replay_raw) > 0 else 'NO_PICK',
                'symbol': replay_raw[1] if len(replay_raw) > 1 else base.get('symbol'),
                'replay_reason': replay_raw[2] if len(replay_raw) > 2 else '',
                'replay_features': replay_raw[3] if len(replay_raw) > 3 else {},
                'replay_flags': replay_raw[4] if len(replay_raw) > 4 else [],
                'replay_eligible': bool((replay_raw[0] if len(replay_raw) > 0 else '') == 'PAPER_PICK'),
                'replay_mode': 'callable',
            }
        else:
            replay_result = {'final_decision': 'NO_PICK', 'replay_reason': 'invalid_replay_result', 'replay_eligible': False, 'replay_mode': 'invalid'}
    replay_decision = str(replay_result.get('final_decision') or '').upper() or 'NO_PICK'
    historical_decision = str(base.get('historical_decision') or base.get('decision') or ('PAPER_PICK' if base.get('is_paper_pick') else 'NO_PICK')).upper()
    base['replay_decision'] = replay_decision
    base['current_rule_decision'] = replay_decision
    if historical_decision == 'PAPER_PICK':
        final_decision = 'PAPER_PICK'
    else:
        final_decision = replay_decision or historical_decision or 'NO_PICK'
    eligibility = candidate.get('paper_pick_eligibility') if isinstance(candidate.get('paper_pick_eligibility'), dict) else {}
    if not eligibility:
        try:
            runner = _runner_module()
            eligibility = runner.paper_pick_eligibility_profile(candidate, bundle)
        except Exception:
            eligibility = {}
    setup_class = ''
    if isinstance(base.get('candidate_lifecycle'), dict):
        setup_class = str(base['candidate_lifecycle'].get('setup_class') or '')
    if not setup_class and isinstance(base.get('paper_pick_eligibility'), dict):
        setup_class = str((base['paper_pick_eligibility'].get('signals') or {}).get('setup_class') or base['paper_pick_eligibility'].get('setup_class') or '')
    if not setup_class and isinstance(eligibility, dict):
        setup_class = str((eligibility.get('signals') or {}).get('setup_class') or eligibility.get('setup_class') or '')
    if not setup_class:
        try:
            runner = _runner_module()
            lifecycle_fn = getattr(runner, '_candidate_lifecycle_profile', None)
            if callable(lifecycle_fn):
                lifecycle = lifecycle_fn(candidate, bundle)
                if isinstance(lifecycle, dict):
                    base['candidate_lifecycle'] = {**(base.get('candidate_lifecycle') if isinstance(base.get('candidate_lifecycle'), dict) else {}), **lifecycle}
                    setup_class = str(lifecycle.get('setup_class') or '')
        except Exception:
            setup_class = ''
    setup_class = setup_class or 'UNKNOWN'
    base['setup_class'] = setup_class
    if isinstance(base.get('candidate_lifecycle'), dict) and not base['candidate_lifecycle'].get('setup_class'):
        base['candidate_lifecycle']['setup_class'] = setup_class
    primary_trade_return = base.get('t1_return')
    later_horizon = base.get('maturation_horizon')
    classification = 'unresolved'
    if base.get('blockers') or not bool(replay_result.get('replay_eligible', False)):
        classification = 'blocked'
    if setup_class == 'STALE_REPEAT':
        classification = 'stale_false_positive' if (base.get('t1_return') is not None and base.get('t1_return') <= 0) else 'stale_repeat'
    elif base.get('maturation_class') == 'matured_later':
        classification = 'matured_later'
    elif setup_class == 'INSTANT_MOMENTUM_SETUP' and base.get('t1_return') is not None and base.get('t1_return') > 0:
        classification = 'instant'
    elif base.get('t1_return') is not None and base.get('t1_return') <= 0 and base.get('maturation_class') in ('weak_multi_horizon', 'unresolved'):
        classification = 'false_positive'
    explanation = {
        'classification': classification,
        'setup_class': setup_class or 'UNKNOWN',
        'historical_decision': historical_decision,
        'replay_decision': replay_decision,
        'current_rule_decision': replay_decision,
        'final_decision': final_decision,
        'replay_eligible': bool(replay_result.get('replay_eligible', False)),
        'replay_reason': replay_result.get('replay_reason') or ('eligible' if replay_result.get('replay_eligible') else 'blocked'),
        'decision_flags': _clean_horizon_blockers(replay_result.get('replay_flags') or []),
        'raw_decision_flags': _listify(replay_result.get('replay_flags') or []),
        'blockers': base.get('blockers') or [],
        'raw_blockers': base.get('raw_blockers') or [],
        'primary_trade_return': primary_trade_return,
        'maturation_horizon': base.get('maturation_horizon'),
        'maturation_return': base.get('maturation_return'),
        'maturation_source': base.get('maturation_source') or '',
        't1_return': base.get('t1_return'),
        't2_return': base.get('t2_return'),
        't3_return': base.get('t3_return'),
        't5_return': base.get('t5_return'),
        'best_horizon': base.get('best_horizon'),
        'trade_mode': TRADE_MODE,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'horizon_note': 'T+2/T+3/T+5 are signal-maturation diagnostics, not multi-day holding PnL.',
        'source_kinds': sorted({str(trace.get('source_kind') or '') for trace in traces if trace.get('source_kind')}),
    }
    if base.get('symbol') in FOCUS_SYMBOL_NAME_HINTS:
        explanation['focus_name_hint'] = FOCUS_SYMBOL_NAME_HINTS[base.get('symbol')]
    base.update({
        'replay_eligible': bool(replay_result.get('replay_eligible', False)),
        'replay_reason': replay_result.get('replay_reason') or ('eligible' if replay_result.get('replay_eligible') else 'blocked'),
        'final_decision': final_decision,
        'historical_decision': historical_decision,
        'replay_decision': replay_decision,
        'current_rule_decision': replay_decision,
        'replay_mode': replay_result.get('replay_mode') or 'unknown',
        'explanation': explanation,
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'horizon_note': HORIZON_NOTE,
        'is_paper_pick': bool(base.get('is_paper_pick') or base.get('picked') or historical_decision == 'PAPER_PICK'),
    })
    base['universe_reason'] = []
    return base


def _build_focus_explanation(symbol: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    symbol = _as_symbol(symbol)
    symbol_records = [record for record in records if _as_symbol(record.get('symbol')) == symbol]
    display_name = ''
    for record in symbol_records:
        if record.get('name'):
            display_name = str(record.get('name'))
            break
    if not display_name:
        display_name = FOCUS_SYMBOL_NAME_HINTS.get(symbol, '')
    if not symbol_records:
        return {
            'symbol': symbol,
            'name': display_name,
            'appeared_as_candidate': False,
            'appeared_as_paper_pick': False,
            'classification': 'unresolved',
            'highest_score': None,
            'earliest_candidate_date': None,
            'latest_candidate_date': None,
            'why_blocked_or_cool_down': 'no_historical_record',
            'new_lifecycle_handling': {
                'setup_class': 'UNKNOWN',
                'final_decision': 'NO_PICK',
                'replay_reason': 'no_historical_record',
            },
            'maturity_diagnostics': {
                't1_primary': None,
                't2': None,
                't3': None,
                't5': None,
            },
            'special_notes': [],
        }

    ordered = sorted(
        symbol_records,
        key=lambda item: (
            str(item.get('trade_date') or ''),
            _safe_float(item.get('rank')) if _safe_float(item.get('rank')) is not None else 999999.0,
        ),
    )
    highest_score_record = max(symbol_records, key=lambda item: (_safe_float(item.get('final_score')) or _safe_float(item.get('score')) or -1e9, str(item.get('trade_date') or '')))
    classifications = Counter(str(record.get('explanation', {}).get('classification') or 'unresolved') for record in symbol_records)
    dominant_classification = classifications.most_common(1)[0][0] if classifications else 'unresolved'
    paper_pick_records = [record for record in symbol_records if bool(record.get('is_paper_pick'))]
    stale_records = [record for record in symbol_records if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') == 'STALE_REPEAT']
    blocked_records = [record for record in symbol_records if record.get('blockers')]
    latest = ordered[-1]
    reason = 'insufficient_history'
    if blocked_records:
        reason = 'previously_blocked:' + ','.join(sorted({str(blocker) for record in blocked_records for blocker in _listify(record.get('blockers'))[:3]}))
    elif stale_records:
        reason = 'cool_down_recommended:stale_repeat'
    elif dominant_classification == 'matured_later':
        reason = 'matured_later_candidate'
    elif dominant_classification == 'instant':
        reason = 'instant_setup'
    elif dominant_classification == 'false_positive':
        reason = 'false_positive_primary_loss'

    notes: List[str] = []
    if symbol == '300077':
        notes.append('previously_blocked_or_cool_down_should_be_re-evaluated_with_lifecycle')
    if symbol == '301236':
        notes.append('T+1 is primary only; later horizons are diagnostics, not hold claims')
    if symbol in ('300059', '301017'):
        notes.append('cool_down_recommended_due_to_repeat_or_unresolved_history')
    if dominant_classification == 'instant':
        notes.append('instant_setup')
    if dominant_classification == 'matured_later':
        notes.append('matured_later')
    if dominant_classification == 'stale_false_positive':
        notes.append('stale_false_positive')
    return {
        'symbol': symbol,
        'name': display_name,
        'appeared_as_candidate': True,
        'appeared_as_paper_pick': bool(paper_pick_records),
        'paper_pick_count': len(paper_pick_records),
        'classification': dominant_classification,
        'highest_score': _safe_float(highest_score_record.get('final_score')) if highest_score_record.get('final_score') is not None else _safe_float(highest_score_record.get('score')),
        'highest_score_date': highest_score_record.get('trade_date'),
        'earliest_candidate_date': ordered[0].get('trade_date'),
        'latest_candidate_date': latest.get('trade_date'),
        'why_blocked_or_cool_down': reason,
        'new_lifecycle_handling': {
            'setup_class': latest.get('setup_class') or latest.get('candidate_lifecycle', {}).get('setup_class') or 'UNKNOWN',
            'final_decision': latest.get('final_decision') or latest.get('decision') or ('PAPER_PICK' if latest.get('is_paper_pick') else 'NO_PICK'),
            'replay_reason': latest.get('replay_reason') or 'history_replayed',
        },
        'maturity_diagnostics': {
            't1_primary': latest.get('t1_return'),
            't2': latest.get('t2_return'),
            't3': latest.get('t3_return'),
            't5': latest.get('t5_return'),
        },
        'special_notes': list(dict.fromkeys(notes)),
    }


def _build_calibration_suggestion(
    config_key: str,
    current: Any,
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    evidence = {
        'sample_count': len(records),
        'current_value': current,
    }
    if len(records) < HORIZON_REPLAY_MIN_SAMPLES:
        return {
            'config_key': config_key,
            'current': current,
            'suggested': 'insufficient_data',
            'reason': 'sample_size_below_threshold',
            'evidence': evidence,
        }
    if config_key == 'delayed_setup_min_persistence':
        delayed_counts = [int(_safe_float(record.get('candidate_lifecycle', {}).get('repeat_count')) or 0) for record in records if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') == 'DELAYED_SETUP']
        if not delayed_counts:
            return {
                'config_key': config_key,
                'current': current,
                'suggested': 'insufficient_data',
                'reason': 'no_delayed_setup_samples',
                'evidence': evidence,
            }
        suggested = max(1, int(round(sum(delayed_counts) / len(delayed_counts))))
        evidence['delayed_repeat_counts'] = delayed_counts
        return {
            'config_key': config_key,
            'current': current,
            'suggested': suggested,
            'reason': 'observed_delayed_setup_repeat_count',
            'evidence': evidence,
        }
    if config_key == 'delayed_setup_theme_min_score':
        delayed_scores = [float(_safe_float(record.get('candidate_lifecycle', {}).get('theme_support')) or 0.0) for record in records if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') == 'DELAYED_SETUP']
        if not delayed_scores:
            return {
                'config_key': config_key,
                'current': current,
                'suggested': 'insufficient_data',
                'reason': 'no_delayed_setup_theme_samples',
                'evidence': evidence,
            }
        suggested = round(sum(delayed_scores) / len(delayed_scores), 2)
        evidence['delayed_theme_support_scores'] = delayed_scores
        return {
            'config_key': config_key,
            'current': current,
            'suggested': suggested,
            'reason': 'observed_delayed_setup_theme_support',
            'evidence': evidence,
        }
    if config_key == 'instant_momentum_min_confirmations':
        confirmations = [int(_safe_float(record.get('candidate_lifecycle', {}).get('instant_confirmations')) or 0) for record in records if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') == 'INSTANT_MOMENTUM_SETUP']
        if not confirmations:
            return {
                'config_key': config_key,
                'current': current,
                'suggested': 'insufficient_data',
                'reason': 'no_instant_setup_samples',
                'evidence': evidence,
            }
        suggested = max(1, int(round(sum(confirmations) / len(confirmations))))
        evidence['instant_confirmations'] = confirmations
        return {
            'config_key': config_key,
            'current': current,
            'suggested': suggested,
            'reason': 'observed_instant_confirmation_count',
            'evidence': evidence,
        }
    if config_key == 'stale_repeat_window_days':
        stale_gaps: List[int] = []
        by_symbol: Dict[str, List[dt.date]] = {}
        for record in records:
            if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') != 'STALE_REPEAT':
                continue
            trade_date = _parse_date(record.get('trade_date'))
            symbol = _as_symbol(record.get('symbol'))
            if trade_date is None or not symbol:
                continue
            by_symbol.setdefault(symbol, []).append(trade_date)
        for dates in by_symbol.values():
            dates.sort()
            for left, right in zip(dates, dates[1:]):
                stale_gaps.append(max(1, (right - left).days))
        if not stale_gaps:
            return {
                'config_key': config_key,
                'current': current,
                'suggested': 'insufficient_data',
                'reason': 'no_stale_repeat_gap_samples',
                'evidence': evidence,
            }
        suggested = max(1, int(round(sum(stale_gaps) / len(stale_gaps))))
        evidence['stale_repeat_gaps'] = stale_gaps
        return {
            'config_key': config_key,
            'current': current,
            'suggested': suggested,
            'reason': 'observed_stale_repeat_spacing',
            'evidence': evidence,
        }
    if config_key == 'stale_decay_factor':
        stale_decay = [_safe_float(record.get('candidate_lifecycle', {}).get('stale_decay')) for record in records if _safe_float(record.get('candidate_lifecycle', {}).get('stale_decay')) is not None]
        if not stale_decay:
            return {
                'config_key': config_key,
                'current': current,
                'suggested': 'insufficient_data',
                'reason': 'no_stale_decay_samples',
                'evidence': evidence,
            }
        suggested = round(sum(stale_decay) / len(stale_decay), 2)
        evidence['stale_decay_values'] = stale_decay
        return {
            'config_key': config_key,
            'current': current,
            'suggested': suggested,
            'reason': 'observed_stale_decay',
            'evidence': evidence,
        }
    return {
        'config_key': config_key,
        'current': current,
        'suggested': current,
        'reason': 'no_change_supported_by_evidence',
        'evidence': evidence,
    }


def build_horizon_replay(
    records: List[Dict[str, Any]],
    top_n: int = HORIZON_REPLAY_TOP_N_DEFAULT,
    focus_symbols: Optional[List[str]] = None,
    decision_replayer: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], Any]] = None,
    source_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_focus = [_as_symbol(symbol) for symbol in (focus_symbols or []) if _as_symbol(symbol)]
    focus_set = set(normalized_focus)
    merged: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in records or []:
        if not isinstance(row, dict):
            continue
        source_kind = str(row.get('source_kind') or row.get('source') or 'unknown')
        normalized = _normalize_horizon_source_row(row, source_kind)
        key = (normalized['trade_date'], normalized['symbol'])
        if not normalized['trade_date'] or not normalized['symbol']:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = {**normalized, 'source_traces': [normalized]}
        else:
            merged[key] = _merge_horizon_record(existing, {**normalized, 'source_traces': [normalized]})

    candidate_pool_by_date: Dict[str, List[Dict[str, Any]]] = {}
    daily_candidate_dates: set[str] = set()
    for record in merged.values():
        source_kinds = {str(trace.get('source_kind') or '') for trace in record.get('source_traces', [])}
        trade_date = str(record.get('trade_date') or '')
        if 'db_daily_candidate' in source_kinds:
            daily_candidate_dates.add(trade_date)
        if any(kind in ('db_daily_candidate', 'bundle_candidate') for kind in source_kinds):
            candidate_pool_by_date.setdefault(trade_date, []).append(record)

    top_keys: set[tuple[str, str]] = set()
    for trade_date, candidates in candidate_pool_by_date.items():
        if not candidates:
            continue
        if trade_date in daily_candidate_dates:
            pool = [record for record in candidates if any(str(trace.get('source_kind') or '') == 'db_daily_candidate' for trace in record.get('source_traces', []))]
            if not pool:
                pool = candidates
        else:
            pool = [record for record in candidates if any(str(trace.get('source_kind') or '') == 'bundle_candidate' for trace in record.get('source_traces', []))]
            if not pool:
                pool = candidates
        pool_sorted = sorted(
            pool,
            key=lambda item: (
                _safe_float(item.get('rank')) if _safe_float(item.get('rank')) is not None else 999999.0,
                -(_safe_float(item.get('final_score')) if item.get('final_score') is not None else _safe_float(item.get('score')) or -1e9),
                item.get('symbol') or '',
            ),
        )
        for record in pool_sorted[: max(0, int(top_n))]:
            top_keys.add((record['trade_date'], record['symbol']))

    selected: List[Dict[str, Any]] = []
    for key, record in merged.items():
        reasons: List[str] = []
        if bool(record.get('is_paper_pick') or record.get('picked') or str(record.get('decision') or '').upper() == 'PAPER_PICK'):
            reasons.append('paper_pick')
        if key in top_keys:
            reasons.append(f'daily_top{int(top_n)}_candidate')
        if record.get('symbol') in focus_set:
            reasons.append('focus_symbol')
        if not reasons:
            continue
        finalized = _finalize_horizon_record(record, top_n, focus_set, decision_replayer=decision_replayer)
        finalized['universe_reason'] = reasons
        finalized['universe_reason_text'] = ','.join(reasons)
        selected.append(finalized)

    selected.sort(key=lambda item: (str(item.get('trade_date') or ''), 0 if 'paper_pick' in _listify(item.get('universe_reason')) else 1, _safe_float(item.get('rank')) if _safe_float(item.get('rank')) is not None else 999999.0, item.get('symbol') or ''))
    _apply_later_candidate_maturation_evidence(selected)

    daily_dates = sorted({record.get('trade_date') for record in selected if record.get('trade_date')})
    historical_ticket_days = {
        record.get('trade_date')
        for record in selected
        if str(record.get('historical_decision') or '').upper() == 'PAPER_PICK' or bool(record.get('is_paper_pick'))
    }
    replay_ticket_days = {
        record.get('trade_date')
        for record in selected
        if str(record.get('final_decision') or '').upper() == 'PAPER_PICK'
    }
    ticket_days = historical_ticket_days | replay_ticket_days
    paper_pick_count = sum(1 for record in selected if bool(record.get('is_paper_pick')))
    daily_top_n_candidate_count = sum(1 for record in selected if any(str(reason).startswith('daily_top') for reason in _listify(record.get('universe_reason'))))
    focus_symbol_count = len(normalized_focus)

    def _records_for_setup(setup_name: str) -> List[Dict[str, Any]]:
        return [record for record in selected if str(record.get('setup_class') or record.get('candidate_lifecycle', {}).get('setup_class') or '') == setup_name]

    def _avg(values: List[Optional[float]]) -> Optional[float]:
        clean = [value for value in values if value is not None]
        if not clean:
            return None
        return round(sum(clean) / len(clean), 4)

    instant_records = _records_for_setup('INSTANT_MOMENTUM_SETUP')
    delayed_records = _records_for_setup('DELAYED_SETUP')
    stale_records = _records_for_setup('STALE_REPEAT')
    false_positive_records = [record for record in selected if str(record.get('explanation', {}).get('classification') or '') == 'false_positive']
    matured_later_records = [record for record in selected if str(record.get('maturation_class') or '') == 'matured_later']

    metrics = {
        'paper_pick_count': paper_pick_count,
        'daily_top_n_candidate_count': daily_top_n_candidate_count,
        'focus_symbol_count': focus_symbol_count,
        'daily_coverage_count': len(daily_dates),
        'daily_ticket_rate': round(len(ticket_days) / len(daily_dates), 4) if daily_dates else 0.0,
        'instant_setup': {
            'count': len(instant_records),
            'primary_win_rate': round(sum(1 for record in instant_records if (record.get('t1_return') or 0) > 0) / len(instant_records), 4) if instant_records else 0.0,
            'primary_limit_up_rate': round(sum(1 for record in instant_records if (record.get('t1_return') or 0) >= LIMIT_UP_THRESHOLD) / len(instant_records), 4) if instant_records else 0.0,
            'avg_primary_return': _avg([record.get('t1_return') for record in instant_records]) or 0.0,
        },
        'delayed_setup': {
            'count': len(delayed_records),
            'primary_win_rate': round(sum(1 for record in delayed_records if (record.get('t1_return') or 0) > 0) / len(delayed_records), 4) if delayed_records else 0.0,
            'matured_later_rate': round(sum(1 for record in delayed_records if str(record.get('maturation_class') or '') == 'matured_later') / len(delayed_records), 4) if delayed_records else 0.0,
            'avg_primary_return': _avg([record.get('t1_return') for record in delayed_records]) or 0.0,
            'avg_maturation_return': _avg([record.get('maturation_return') for record in delayed_records]) or 0.0,
        },
        'stale_repeat': {
            'count': len(stale_records),
            'false_positive_rate': round(sum(1 for record in stale_records if str(record.get('explanation', {}).get('classification') or '') == 'false_positive') / len(stale_records), 4) if stale_records else 0.0,
            'avg_primary_return': _avg([record.get('t1_return') for record in stale_records]) or 0.0,
        },
        'false_positive': {
            'count': len(false_positive_records),
            'avg_primary_return': _avg([record.get('t1_return') for record in false_positive_records]) or 0.0,
        },
        'matured_later_candidates': len(matured_later_records),
    }
    try:
        from xiaogu_db import get_scoring_config_snapshot as _get_scoring_config_snapshot
        scoring_snapshot = _get_scoring_config_snapshot(refresh=False)
    except Exception:
        scoring_snapshot = {'config': {}, 'loaded': False, 'source': 'defaults', 'error': 'scoring_config_unavailable'}
    scoring_config = scoring_snapshot.get('config') if isinstance(scoring_snapshot.get('config'), dict) else {}
    calibration_suggestions: Dict[str, Dict[str, Any]] = {}
    for config_key in (
        'delayed_setup_min_persistence',
        'delayed_setup_theme_min_score',
        'instant_momentum_min_confirmations',
        'stale_repeat_window_days',
        'stale_decay_factor',
    ):
        current_raw = scoring_config.get(config_key)
        current = _safe_float(current_raw)
        if current is not None and str(current).endswith('.0'):
            current = int(current)
        calibration_suggestions[config_key] = _build_calibration_suggestion(
            config_key,
            current_raw if current_raw is not None else current,
            selected,
        )

    focus_explanations = {symbol: _build_focus_explanation(symbol, selected) for symbol in normalized_focus}
    for symbol in normalized_focus:
        focus_explanations.setdefault(symbol, _build_focus_explanation(symbol, selected))
    return {
        'mode': 'horizon_replay',
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'horizon_note': HORIZON_NOTE,
        'top_n': int(top_n),
        'focus_symbols': normalized_focus,
        'record_count': len(selected),
        'records': selected,
        'metrics': metrics,
        'summary': metrics,
        'calibration_suggestions': calibration_suggestions,
        'focus_explanations': focus_explanations,
        'source_details': source_details or {},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description='Analyze signal effectiveness from ledger')
    ap.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    ap.add_argument('--min-samples', type=int, default=DEFAULT_MIN_SAMPLES)
    ap.add_argument('--source', choices=('db', 'ledger'), default='db')
    ap.add_argument('--candidate-diagnostics', action='store_true', dest='candidate_diagnostics')
    ap.add_argument('--horizon-replay', action='store_true', dest='horizon_replay')
    ap.add_argument('--top-n', type=int, default=HORIZON_REPLAY_TOP_N_DEFAULT)
    ap.add_argument('--focus-symbols', default='')
    ap.add_argument('--json', action='store_true', dest='as_json')
    ap.add_argument('--persist', action='store_true',
                    help='Upsert the factor-analysis snapshot into signal_effectiveness')
    args = ap.parse_args()

    focus_symbols = [symbol.strip() for symbol in str(args.focus_symbols or '').split(',') if symbol.strip()]
    if args.horizon_replay:
        rows, source_details = _collect_horizon_replay_sources(args.ledger, focus_symbols)
        result = build_horizon_replay(rows, top_n=args.top_n, focus_symbols=focus_symbols, source_details=source_details)
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        print(f"Horizon replay source: {result['source_details'].get('db', {}).get('loaded') and 'db/files' or 'filesystem'}")
        print(f"Records              : {result['record_count']}")
        print(f"Paper picks           : {result['metrics']['paper_pick_count']}")
        print(f"Daily ticket rate     : {result['metrics']['daily_ticket_rate']:.1%}")
        return
    if args.candidate_diagnostics:
        result = analyze_candidate_diagnostics(focus_symbols=focus_symbols, ledger_path=args.ledger)
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        print(f"Candidate diagnostics source: {result['source']}")
        print(f"Records                : {result['record_count']}")
        print(f"Instant winners         : {result['summary']['instant_winners']}")
        print(f"Matured later candidates: {result['summary']['matured_later_candidates']} (compat delayed_winners)")
        print(f"Missed winners         : {result['summary']['missed_winners']}")
        print(f"False positives        : {result['summary']['false_positives']}")
        print(f"Early noise            : {result['summary']['early_noise']}")
        print(f"Stale candidates       : {result['summary']['stale_candidates']}")
        return

    result = analyze_signal_effectiveness(args.ledger, args.min_samples, source=args.source)
    if args.persist:
        result['persistence'] = persist_signal_effectiveness(result)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"Analysis date   : {result['analysis_date']}")
    print(f"Total picks     : {result['total_picks']}")
    print(f"Filled picks    : {result['filled_picks']}")
    print(f"Overall LU rate : {result['overall_limit_up_rate']:.1%}")
    print(f"Overall avg T1  : {result['overall_avg_t1_return']:+.2%}")
    print()
    print("Signal effectiveness:")
    for s in result['signal_effectiveness']:
        print(f"  {s['signal_key']:<40} count={s['present_count']:3d}  LU={s['limit_up_rate']:.1%}  avg={s['avg_t1_return']:+.2%}  → {s['weight_suggestion']}")
    print()
    print("Pool effectiveness:")
    for p in result['pool_effectiveness']:
        print(f"  {p['pool']:<40} count={p['count']:3d}  LU={p['limit_up_rate']:.1%}  avg={p['avg_return']:+.2%}")


if __name__ == '__main__':
    main()
