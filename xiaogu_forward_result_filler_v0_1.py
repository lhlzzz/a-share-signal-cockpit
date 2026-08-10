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
    dump_json,
    load_jsonl,
    now_iso,
    read_json,
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
ACTIVE_DECISION_RECORD_TYPES = {'DECISION', 'CORRECTION'}


def fnum(value: Any) -> Optional[float]:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace('%', '').replace(',', ''))
    except (TypeError, ValueError):
        return None


def decision_symbol(decision: Dict[str, Any]) -> str:
    symbol = decision.get('symbol')
    if symbol and symbol != 'NO_PICK':
        return str(symbol).zfill(6)
    features = decision.get('features_used', {}).get('candidate_features', {})
    symbol = features.get('symbol') or features.get('code')
    return str(symbol).zfill(6) if symbol else ''


def entry_price(decision: Dict[str, Any]) -> Optional[float]:
    features = decision.get('features_used', {}).get('candidate_features', {})
    for key in ('entry_price', 'price', 'signal_close'):
        parsed = fnum(features.get(key))
        if parsed is not None:
            return parsed
    return None



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
    source_name = 'eastmoney_push2his_daily_kline'
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


def decision_record_id(row: Dict[str, Any]) -> str:
    return '_'.join([
        str(row.get('date') or ''),
        str(row.get('asof_time') or ''),
        str(row.get('record_type', 'DECISION')),
        decision_symbol(row),
    ])


def has_decision_payload(row: Dict[str, Any]) -> bool:
    record_type = row.get('record_type', 'DECISION')
    return record_type == 'DECISION' or (record_type == 'CORRECTION' and bool(row.get('decision')))


def corrected_decision_ids(rows: List[Dict[str, Any]]) -> set[str]:
    return {
        str(row.get('correction_of'))
        for row in rows
        if row.get('record_type') == 'CORRECTION' and row.get('decision') and row.get('correction_of')
    }


def superseded_decision_keys(rows: List[Dict[str, Any]]) -> set[Tuple[str, str]]:
    keys = set()
    for row in rows:
        if not has_decision_payload(row):
            continue
        supersedes = (row.get('features_used') or {}).get('supersedes') or {}
        date = supersedes.get('date')
        symbol = supersedes.get('symbol')
        if date and symbol:
            keys.add((str(date), str(symbol).zfill(6)))
    return keys


def is_active_decision_record(row: Dict[str, Any], corrected_ids: set[str], superseded: set[Tuple[str, str]]) -> bool:
    if row.get('record_type', 'DECISION') not in ACTIVE_DECISION_RECORD_TYPES or not has_decision_payload(row):
        return False
    symbol = decision_symbol(row)
    if decision_record_id(row) in corrected_ids:
        return False
    if (str(row.get('date')), symbol) in superseded:
        return False
    return True


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



def fetch_eastmoney_klines(symbol: str, begin: str, end: str, retries: int = 2) -> Dict[str, Any]:
    params = '&'.join([
        'secid=' + secid_for(symbol),
        'fields1=f1,f2,f3,f4,f5,f6',
        'fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt=101',
        'fqt=1',
        'beg=' + begin.replace('-', ''),
        'end=' + end.replace('-', ''),
    ])
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + params
    last_error = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 XiaoguForwardResult/0.1', 'Referer': 'https://quote.eastmoney.com/'})
            with DIRECT_OPENER.open(req, timeout=20) as resp:
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
    price = entry_price(decision)
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
    ret = (sell_price - price) / price
    request_url = str(payload.get('_request_url', ''))
    adjustment = 'qfq' if 'fqt=1' in request_url or request_url.endswith(',qfq') else 'unadjusted'
    evidence = {
        'status': 'PASS',
        'source': source,
        'evidence_path': str(evidence_path),
        'entry_price': price,
        'entry_date': decision['date'],
        'exit_date': exit_row['date'],
        'exit_high': exit_row.get('high'),
        'exit_close': exit_row['close'],
        'return_formula': '(exit_close-entry_price)/entry_price',
        'kline_adjustment': adjustment,
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
    source_name = 'eastmoney_push2his_daily_kline'
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
