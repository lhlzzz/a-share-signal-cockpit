#!/usr/bin/env python3
"""Backfill missing T+1/T+2/T+3/T+5 returns for all PAPER_PICK records.

Uses baostock API to fetch close prices (forward-adjusted).
Calculates returns and updates the returns table.
"""
import json
import signal
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import baostock as bs
from sqlalchemy import text
from xiaogu_db import engine, upsert_return, record_return_backfill_failure, backfill_return_pick_ids
from xiaogu_forward_result_filler_v0_1 import (
    fetch_eastmoney_klines, fetch_tencent_klines, parse_klines as parse_eastmoney_klines,
    secid_for,
)

# Rate limit: 50ms between requests
REQUEST_DELAY = 0.05

HORIZON_OFFSETS = {
    't1': 1,
    't2': 2,
    't3': 3,
    't5': 5,
}

FAILURE_REASONS = {
    'BAOSTOCK_TIMEOUT',
    'NO_TRADING_DATA',
    'SYMBOL_FORMAT_ERROR',
    'NETWORK_ERROR',
    'BAOSTOCK_LOGIN_FAILED',
    'BAOSTOCK_QUERY_FAILED',
    'DB_WRITE_FAILED',
    'UNKNOWN',
}
DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS = 8
DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS = 600
MAX_INTRADAY_HIGH_CAPTURE_RATIO = 0.70


class ReturnFetchTimeout(TimeoutError):
    pass


class BaoStockQueryError(RuntimeError):
    pass


@contextmanager
def per_symbol_timeout(seconds: int):
    """Bound a blocking baostock request without abandoning the full resume run."""
    if seconds <= 0:
        yield
        return
    def handler(_signum, _frame):
        raise ReturnFetchTimeout(f'baostock_timeout_{seconds}s')
    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def is_trading_day(d: date) -> bool:
    """Check if date is a weekday (simplified - no holiday calendar)."""
    return d.weekday() < 5


def next_trading_day(d: date, offset: int) -> date:
    """Get the Nth trading day after d (skip weekends)."""
    current = d
    count = 0
    while count < offset:
        current += timedelta(days=1)
        if is_trading_day(current):
            count += 1
    return current


def baostock_symbol(symbol: str) -> str:
    """Convert to baostock format (sh.XXXXXX or sz.XXXXXX)."""
    raw = str(symbol or '').strip()
    if not raw.isdigit() or len(raw) > 6:
        raise ValueError('SYMBOL_FORMAT_ERROR')
    s = raw.zfill(6)
    if s.startswith('6') or s.startswith('9'):
        return f'sh.{s}'
    return f'sz.{s}'


def fetch_kline_range_baostock(symbol: str, start: str, end: str) -> list:
    """Fetch daily klines from baostock API for a date range."""
    bs_symbol = baostock_symbol(symbol)
    rs = bs.query_history_k_data_plus(
        bs_symbol,
        'date,open,high,low,close',
        start_date=start,
        end_date=end,
        frequency='d',
        adjustflag='2'  # 前复权
    )
    if rs.error_code != '0':
        raise BaoStockQueryError(rs.error_msg or 'baostock query failed')

    data = []
    while rs.next():
        row = rs.get_row_data()
        if row[4]:  # Has close price
            data.append({
                'date': row[0],
                'open': float(row[1]) if row[1] else None,
                'high': float(row[2]) if row[2] else None,
                'low': float(row[3]) if row[3] else None,
                'close': float(row[4]),
            })
    return data


def fetch_eastmoney_realtime_ohlc(symbol: str, trade_date: str) -> Optional[dict]:
    """Fetch current-day OHLC from Eastmoney quote endpoint after close.

    Only stamps the live quote onto *today*. Using this for a future/past
    target date previously labeled same-day OHLC as T+1 (t1_return=0 pollution).
    """
    if str(trade_date)[:10] != date.today().isoformat():
        return None
    params = '&'.join([
        'ut=fa5fd1943c7b386f172d6893dbfba10b',
        'fltt=2',
        'invt=2',
        'fields=f43,f44,f45,f46',
        'secid=' + secid_for(symbol),
        '_=' + str(int(time.time() * 1000)),
    ])
    url = 'https://push2delay.eastmoney.com/api/qt/stock/get?' + params
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 XiaoguReturnBackfill/0.1', 'Referer': 'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = (json.loads(resp.read().decode('utf-8')).get('data') or {})
    if not all(data.get(key) for key in ('f43', 'f44', 'f45', 'f46')):
        return None
    return {
        'date': trade_date,
        'open': float(data['f46']),
        'high': float(data['f44']),
        'low': float(data['f45']),
        'close': float(data['f43']),
    }


def merge_eastmoney_missing_klines(symbol: str, start: str, end: str, klines: list) -> tuple[list, bool]:
    """Fill missing same-day klines from Eastmoney when baostock has not published them yet."""
    existing_dates = {row.get('date') for row in klines}
    if end in existing_dates:
        return klines, False
    payload = fetch_eastmoney_klines(symbol, start, end, retries=3)
    eastmoney_rows = parse_eastmoney_klines(payload)
    if not eastmoney_rows:
        payload = fetch_tencent_klines(symbol, start, end)
        eastmoney_rows = parse_eastmoney_klines(payload)
    merged = {row['date']: row for row in klines if row.get('date')}
    for row in eastmoney_rows:
        merged.setdefault(row['date'], {
            'date': row['date'],
            'open': row.get('open'),
            'high': row.get('high'),
            'low': row.get('low'),
            'close': row.get('close'),
        })
    if end not in merged:
        try:
            realtime_row = fetch_eastmoney_realtime_ohlc(symbol, end)
        except Exception:
            realtime_row = None
        if realtime_row:
            merged[end] = realtime_row
    return [merged[key] for key in sorted(merged)], end in merged and end not in existing_dates


def price_on_date(klines: list, target_date: str, field: str = 'close') -> Optional[float]:
    """Extract price for a specific date from kline data."""
    for row in klines:
        if row['date'] == target_date:
            return row.get(field)
    return None


def open_on_date(klines: list, target_date: str) -> Optional[float]:
    """Extract open price for a specific date from kline data."""
    return price_on_date(klines, target_date, 'open')


def high_on_date(klines: list, target_date: str) -> Optional[float]:
    """Extract high price for a specific date from kline data."""
    return price_on_date(klines, target_date, 'high')


def low_on_date(klines: list, target_date: str) -> Optional[float]:
    """Extract low price for a specific date from kline data."""
    return price_on_date(klines, target_date, 'low')


def close_on_date(klines: list, target_date: str) -> Optional[float]:
    """Extract close price for a specific date from kline data."""
    return price_on_date(klines, target_date, 'close')


def calculate_return(entry_price: float, exit_price: float) -> Optional[float]:
    """Calculate return as (exit - entry) / entry."""
    if entry_price is None or exit_price is None or entry_price <= 0:
        return None
    return round((exit_price - entry_price) / entry_price, 6)


def is_limit_up(high_price: float, entry_price: float) -> bool:
    """Check if stock hit limit up (涨停) on the day."""
    if high_price is None or entry_price is None or entry_price <= 0:
        return False
    # 涨停幅度：主板10%，创业板/科创板20%
    return (high_price / entry_price - 1) >= 0.095


def _clamp_profit(value: float, high_return: Optional[float], low_return: Optional[float]) -> float:
    upper = high_return if high_return is not None else value
    lower = low_return if low_return is not None else value
    return round(min(max(value, lower), upper), 6)


def _capture_ratio(profit: Optional[float], high_return: Optional[float]) -> Optional[float]:
    if profit is None or high_return is None or high_return <= 0:
        return None
    return round(profit / high_return, 6)


def estimate_sellable_profit(
    entry_price: float,
    open_return: Optional[float],
    high_return: Optional[float],
    low_return: Optional[float],
    close_return: Optional[float],
    high_to_close_retrace: Optional[float],
    limit_touched: bool,
) -> dict:
    """Estimate rule-based T+1 sellable profit from daily OHLC.

    This is an executable approximation for backtesting when minute/VWAP data is
    unavailable. It deliberately discounts the high return instead of treating it
    as a fillable exit.
    """
    open_r = open_return if open_return is not None else 0.0
    high_r = high_return if high_return is not None else open_r
    low_r = low_return if low_return is not None else open_r
    close_r = close_return if close_return is not None else open_r
    retrace = high_to_close_retrace if high_to_close_retrace is not None else 0.0

    panic_sell_avoided = open_r < 0 and high_r > open_r
    should_wait_rebound = open_r < 0 and (high_r > 0 or (high_r - open_r) >= 0.02)
    failure_exit_triggered = low_r <= -0.04 and high_r <= 0.015

    if failure_exit_triggered:
        strategy = '失败止损卖'
        signal_time = '10:00'
    elif limit_touched and retrace <= -0.025:
        strategy = '涨停炸板卖'
        signal_time = '14:30'
    elif limit_touched:
        strategy = '涨停回封观察'
        signal_time = '14:50'
    elif open_r > 0 and high_r - open_r >= 0.02:
        strategy = '高开冲高卖'
        signal_time = '09:45'
    elif high_r >= 0.04 and retrace <= -0.025:
        strategy = '冲高回落卖'
        signal_time = '10:30'
    elif open_r < 0 and should_wait_rebound:
        strategy = '低开等待反弹'
        signal_time = '10:00'
    elif close_r > 0 and retrace <= -0.015:
        strategy = '分时均价线卖'
        signal_time = '10:45'
    elif high_r >= 0.03:
        strategy = '分批止盈'
        signal_time = '10:20'
    else:
        strategy = '10:00前不强卖'
        signal_time = '10:00'

    if failure_exit_triggered:
        conservative = max(low_r * 0.55, -0.035)
        normal = max(low_r * 0.65, -0.03)
        aggressive = max(low_r * 0.75, -0.045)
    else:
        conservative = high_r * 0.55
        normal = high_r * MAX_INTRADAY_HIGH_CAPTURE_RATIO
        aggressive = high_r * MAX_INTRADAY_HIGH_CAPTURE_RATIO
        if open_r > 0:
            conservative = min(conservative, open_r + 0.015)
            normal = min(normal, open_r + 0.03)
        if limit_touched:
            normal = high_r * (0.65 if retrace <= -0.025 else MAX_INTRADAY_HIGH_CAPTURE_RATIO)
            aggressive = high_r * MAX_INTRADAY_HIGH_CAPTURE_RATIO
        if strategy == '分批止盈':
            normal = high_r * MAX_INTRADAY_HIGH_CAPTURE_RATIO
        if strategy == '低开等待反弹':
            conservative = max(conservative, min(high_r * 0.45, 0.018))
            normal = max(normal, min(high_r * 0.66, 0.035))

    conservative = _clamp_profit(conservative, high_r, low_r)
    normal = _clamp_profit(normal, high_r, low_r)
    aggressive = _clamp_profit(aggressive, high_r, low_r)
    if high_return is not None and high_r > 0:
        high_capture_cap = high_r * MAX_INTRADAY_HIGH_CAPTURE_RATIO
        conservative = min(conservative, high_capture_cap)
        normal = min(normal, high_capture_cap)
        aggressive = min(aggressive, high_capture_cap)
    sellable = normal

    return {
        'sellable_profit': sellable,
        'sellable_profit_v1_conservative': conservative,
        'sellable_profit_v2_normal': normal,
        'sellable_profit_v3_aggressive': aggressive,
        'sell_strategy_used': strategy,
        'sell_signal_time': signal_time,
        'sell_signal_price': round(entry_price * (1 + sellable), 2) if entry_price else None,
        'max_profit_before_sell': round(high_r, 6) if high_return is not None else None,
        'profit_capture_ratio': _capture_ratio(sellable, high_r),
        'missed_profit': round(high_r - sellable, 6) if high_return is not None else None,
        'panic_sell_avoided': bool(panic_sell_avoided and sellable > open_r),
        'should_wait_rebound': bool(should_wait_rebound),
        'failure_exit_triggered': bool(failure_exit_triggered),
    }


def build_return_backfill_timeout_gate(failure_reasons: dict) -> dict:
    """Expose baostock timeout concentration without conflating it with coverage."""
    failure_count = sum(failure_reasons.values())
    timeout_count = failure_reasons.get('BAOSTOCK_TIMEOUT', 0)
    ratio = round(timeout_count / failure_count, 4) if failure_count else 0.0
    if ratio > 0.60:
        status = 'FAIL'
    elif ratio > 0.30:
        status = 'WARN'
    else:
        status = 'PASS'
    return {
        'status': status,
        'timeout_count': timeout_count,
        'failure_count': failure_count,
        'timeout_failure_ratio': ratio,
        'threshold': 0.30,
        'fail_threshold': 0.60,
    }


def backfill_returns(
    dry_run: bool = False,
    start_date: str = '2026-06-20',
    top10_only: bool = True,
    per_symbol_timeout_seconds: int = DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS,
    batch_soft_timeout_seconds: int = DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS,
    input_trade_date: str | None = None,
    validation_trade_date: str | None = None,
) -> dict:
    """Main backfill logic."""
    stats = {
        'total_picks': 0,
        'valid_picks': 0,
        'missing_returns': 0,
        'fetched': 0,
        'fetch_failed': 0,
        'already_filled': 0,
        't1_filled': 0,
        't2_filled': 0,
        't3_filled': 0,
        't5_filled': 0,
        'results': [],
        'return_coverage_before': None,
        'return_coverage_after': None,
        'paper_pick_return_coverage': None,
        'top10_return_coverage': None,
        'mainboard_top10_return_coverage': None,
        'failed_return_symbols': [],
        'failed_return_reasons': {},
        'new_success_count': 0,
        'new_failure_count': 0,
        'skipped_existing_success_count': 0,
        'failure_reasons': {},
        'before': {},
        'after': {},
        'batch_soft_timeout_exceeded': False,
        'eastmoney_fallback_count': 0,
        'return_backfill_config': {
            'per_symbol_timeout_seconds': per_symbol_timeout_seconds,
            'batch_soft_timeout_seconds': batch_soft_timeout_seconds,
            'resume_enabled': True,
            'skip_existing_success': True,
        },
        'input_trade_date': input_trade_date,
        'validation_trade_date': validation_trade_date,
    }

    if input_trade_date and validation_trade_date:
        expected_validation_date = next_trading_day(date.fromisoformat(input_trade_date), 1).isoformat()
        if validation_trade_date != expected_validation_date:
            stats['fatal_error'] = 'VALIDATION_TRADE_DATE_MISMATCH'
            stats['expected_validation_trade_date'] = expected_validation_date
            return stats

    def record_failure(trade_date, symbol, reason):
        """Count a standardized failure and persist its audit payload when possible."""
        normalized_reason = reason if reason in FAILURE_REASONS else 'UNKNOWN'
        stats['fetch_failed'] += 1
        stats['new_failure_count'] += 1
        stats['failed_return_symbols'].append({
            'trade_date': str(trade_date), 'symbol': str(symbol), 'reason': normalized_reason,
        })
        stats['failed_return_reasons'][normalized_reason] = stats['failed_return_reasons'].get(normalized_reason, 0) + 1
        stats['failure_reasons'][normalized_reason] = stats['failure_reasons'].get(normalized_reason, 0) + 1
        if dry_run:
            return
        try:
            record_return_backfill_failure(trade_date, str(symbol), normalized_reason)
        except Exception as exc:
            print(f'  ERROR: failure persistence failed for {trade_date} {symbol}: {exc}')
            if normalized_reason != 'DB_WRITE_FAILED':
                stats['fetch_failed'] += 1
                stats['new_failure_count'] += 1
                stats['failed_return_reasons']['DB_WRITE_FAILED'] = stats['failed_return_reasons'].get('DB_WRITE_FAILED', 0) + 1
                stats['failure_reasons']['DB_WRITE_FAILED'] = stats['failure_reasons'].get('DB_WRITE_FAILED', 0) + 1

    # Replay every stored top10 row, not only PAPER_PICK. This is the same
    # population used by the cohort backtest and keeps alternatives measurable.
    scope = 'AND dc.rank <= 10' if top10_only else ''
    candidate_date_scope = 'dc.trade_date = :input_trade_date' if input_trade_date else 'dc.trade_date >= :start_date'
    pick_date_scope = 'p.trade_date = :input_trade_date' if input_trade_date else 'p.trade_date >= :start_date'
    date_params = {'input_trade_date': input_trade_date} if input_trade_date else {'start_date': start_date}
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT DISTINCT ON (trade_date, symbol)
                   trade_date, symbol, final_score, is_official_pick, rank
            FROM (
                SELECT dc.trade_date, dc.symbol, dc.final_score,
                       dc.is_official_pick, dc.rank
                FROM daily_candidates dc
                WHERE {candidate_date_scope}
                  AND dc.symbol IS NOT NULL
                  AND dc.symbol != ''
                  AND dc.symbol != '000000'
                  {scope}
                UNION ALL
                SELECT p.trade_date, p.symbol, p.final_score,
                       TRUE AS is_official_pick, p.rank
                FROM picks p
                WHERE {pick_date_scope}
                  AND p.decision = 'PAPER_PICK'
                  AND p.symbol IS NOT NULL
                  AND p.symbol != ''
                  AND p.symbol != '000000'
            ) scoped_rows
            ORDER BY trade_date, symbol, is_official_pick DESC, rank NULLS LAST
        """).bindparams(**date_params)).fetchall()

    stats['total_picks'] = len(rows)
    print(f'Found {len(rows)} top10 candidate records with valid symbols')

    # Check which ones already have returns
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT trade_date, symbol, t1_return, t2_return, t3_return, t5_return, t1_return_high
            FROM returns
        """)).fetchall()

    existing_map = {}
    for r in existing:
        key = (r[0], r[1])
        existing_map[key] = {
            't1': r[2], 't2': r[3], 't3': r[4], 't5': r[5],
            't1_high': r[6] if len(r) > 6 else None
        }

    target_keys = {(row[0], str(row[1]).zfill(6)) for row in rows}
    filled_before = sum(1 for key in target_keys if existing_map.get(key, {}).get('t1') is not None)
    stats['return_coverage_before'] = round(filled_before / len(target_keys), 4) if target_keys else None
    stats['skipped_existing_success_count'] = filled_before
    mainboard_keys = {
        key for key in target_keys
        if str(key[1]).startswith(('600', '601', '603', '605', '000', '001', '002', '003'))
    }
    rank26_keys = {
        (row[0], str(row[1]).zfill(6)) for row in rows
        if len(row) > 4 and 2 <= int(row[4] or 999) <= 6
        and str(row[1]).zfill(6).startswith(('600', '601', '603', '605', '000', '001', '002', '003'))
    }
    paper_rows = []
    if not dry_run:
        with engine.connect() as conn:
            paper_rows = conn.execute(text(f"""
            SELECT p.trade_date, p.symbol FROM picks p
            WHERE {pick_date_scope} AND p.decision='PAPER_PICK'
              AND p.symbol IS NOT NULL AND p.symbol <> ''
            """).bindparams(**date_params)).fetchall()
    paper_keys = {(row[0], str(row[1]).zfill(6)) for row in paper_rows}
    def key_coverage(keys):
        return round(sum(existing_map.get(key, {}).get('t1') is not None for key in keys) / len(keys), 4) if keys else None
    stats['before'] = {
        'top10_t1_coverage': stats['return_coverage_before'],
        'mainboard_top10_t1_coverage': key_coverage(mainboard_keys),
        'paper_pick_t1_coverage': key_coverage(paper_keys),
        'rank2_to_rank6_t1_coverage': key_coverage(rank26_keys),
    }

    # Login to baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f'ERROR: baostock login failed: {lg.error_msg}')
        for trade_date, symbol, *_ in rows:
            record_failure(trade_date, symbol, 'BAOSTOCK_LOGIN_FAILED')
        stats['return_backfill_timeout_gate'] = build_return_backfill_timeout_gate(stats['failure_reasons'])
        return stats

    try:
        # Process each pick
        as_of_date = date.fromisoformat(validation_trade_date) if validation_trade_date else date.today()
        batch_started_at = time.monotonic()
        for i, row in enumerate(rows):
            if batch_soft_timeout_seconds > 0 and time.monotonic() - batch_started_at > batch_soft_timeout_seconds:
                stats['batch_soft_timeout_exceeded'] = True
                print(f'WARN: batch soft timeout reached after {batch_soft_timeout_seconds}s')
                break
            trade_date = row[0]
            raw_symbol = str(row[1] or '').strip()
            try:
                symbol = str(raw_symbol).zfill(6)
                baostock_symbol(raw_symbol)
            except ValueError:
                record_failure(trade_date, raw_symbol, 'SYMBOL_FORMAT_ERROR')
                continue
            score = row[2]

            # Skip if trade_date is as_of_date or future
            if trade_date >= as_of_date:
                stats['failed_return_reasons']['future_or_current_date'] = stats['failed_return_reasons'].get('future_or_current_date', 0) + 1
                continue

            # Skip weekend dates
            if not is_trading_day(trade_date):
                stats['failed_return_reasons']['non_trading_date'] = stats['failed_return_reasons'].get('non_trading_date', 0) + 1
                continue

            key = (trade_date, symbol)
            existing_ret = existing_map.get(key, {})

            # Determine which horizons need filling
            needs = {}
            need_high_update = existing_ret.get('t1') is not None and existing_ret.get('t1_high') is None
            for horizon, offset in HORIZON_OFFSETS.items():
                if existing_ret.get(horizon) is not None and not (horizon == 't1' and need_high_update):
                    stats['already_filled'] += 1
                    continue
                target_date = next_trading_day(trade_date, offset)
                if target_date > as_of_date:
                    continue
                needs[horizon] = (offset, target_date)

            if not needs and not need_high_update:
                continue

            stats['missing_returns'] += 1

            # Fetch kline data
            max_offset = max(v[0] for v in needs.values())
            max_target = next_trading_day(trade_date, max_offset)

            try:
                with per_symbol_timeout(per_symbol_timeout_seconds):
                    klines = fetch_kline_range_baostock(symbol, str(trade_date), str(max_target))
            except ReturnFetchTimeout as exc:
                record_failure(trade_date, symbol, 'BAOSTOCK_TIMEOUT')
                continue
            except BaoStockQueryError:
                record_failure(trade_date, symbol, 'BAOSTOCK_QUERY_FAILED')
                continue
            except Exception as exc:
                reason = 'NETWORK_ERROR' if type(exc).__name__ in {'ConnectionError', 'TimeoutError'} else 'UNKNOWN'
                record_failure(trade_date, symbol, reason)
                continue
            time.sleep(REQUEST_DELAY)

            target_dates = {str(target_date) for _, target_date in needs.values()}
            have_dates = {row.get('date') for row in klines}
            if target_dates - have_dates:
                try:
                    klines, used_fallback = merge_eastmoney_missing_klines(
                        symbol, str(trade_date), str(max_target), klines,
                    )
                    if used_fallback:
                        stats['eastmoney_fallback_count'] += 1
                except Exception:
                    pass

            if not klines:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA')
                continue

            # Get entry price
            entry_price = price_on_date(klines, str(trade_date))
            if entry_price is None:
                stats['fetch_failed'] += 1
                stats['new_failure_count'] += 1
                reason = 'NO_TRADING_DATA'
                stats['failed_return_symbols'].append({'trade_date': str(trade_date), 'symbol': symbol, 'reason': reason})
                stats['failed_return_reasons'][reason] = stats['failed_return_reasons'].get(reason, 0) + 1
                stats['failure_reasons'][reason] = stats['failure_reasons'].get(reason, 0) + 1
                if not dry_run:
                    record_return_backfill_failure(trade_date, symbol, reason)
                continue

            stats['fetched'] += 1

            # Calculate returns
            returns = {}
            next_day_high = None
            next_day_limit_touch = False
            next_day_open = None
            next_day_low = None
            next_day_close = None
            next_day_gap = None
            next_day_drawdown = None
            high_to_close_retrace = None
            sellable_profile = {}

            # Get T+1 data
            t1_date = next_trading_day(trade_date, 1)
            if t1_date <= as_of_date:
                t1_open = open_on_date(klines, str(t1_date))
                t1_high = high_on_date(klines, str(t1_date))
                t1_low = low_on_date(klines, str(t1_date))
                t1_close = close_on_date(klines, str(t1_date))

                if t1_open is not None:
                    next_day_open = calculate_return(entry_price, t1_open)
                if t1_high is not None:
                    next_day_high = calculate_return(entry_price, t1_high)
                    next_day_limit_touch = is_limit_up(t1_high, entry_price)
                if t1_low is not None:
                    next_day_low = calculate_return(entry_price, t1_low)
                if t1_close is not None:
                    next_day_close = calculate_return(entry_price, t1_close)

                # Gap return (开盘相对昨日收盘)
                if t1_open is not None and entry_price is not None and entry_price > 0:
                    next_day_gap = round((t1_open / entry_price - 1), 6)

                # Drawdown (从最高到最低的回撤)
                if t1_high is not None and t1_low is not None and t1_high > 0:
                    next_day_drawdown = round((t1_low - t1_high) / t1_high, 6)

                # High to close retrace (从最高到收盘的回撤)
                if t1_high is not None and t1_close is not None and t1_high > 0:
                    high_to_close_retrace = round((t1_close - t1_high) / t1_high, 6)

                if next_day_high is not None:
                    sellable_profile = estimate_sellable_profit(
                        entry_price=entry_price,
                        open_return=next_day_open,
                        high_return=next_day_high,
                        low_return=next_day_low,
                        close_return=next_day_close,
                        high_to_close_retrace=high_to_close_retrace,
                        limit_touched=next_day_limit_touch,
                    )

            for horizon, (offset, target_date) in needs.items():
                exit_price = price_on_date(klines, str(target_date))
                if exit_price is not None:
                    ret = calculate_return(entry_price, exit_price)
                    returns[horizon] = ret
                    if ret is not None:
                        stats[f'{horizon}_filled'] += 1

            if not returns:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA')
                continue

            # Update database
            if not dry_run and returns:
                try:
                    upsert_return(
                        trade_date=trade_date,
                        symbol=symbol,
                        pick_id=None,
                        t1_return=returns.get('t1'),
                        t1_return_close=next_day_close,
                        t2_return=returns.get('t2'),
                        t3_return=returns.get('t3'),
                        t5_return=returns.get('t5'),
                        t1_return_high=next_day_high,
                        is_limit_up=next_day_limit_touch,
                        next_day_open_return=next_day_open,
                        next_day_high_return=next_day_high,
                        next_day_low_return=next_day_low,
                        next_day_gap_return=next_day_gap,
                        next_day_drawdown=next_day_drawdown,
                        high_to_close_retrace=high_to_close_retrace,
                    )
                except Exception as e:
                    print(f'  ERROR: upsert_return failed for {trade_date} {symbol}: {e}')
                    record_failure(trade_date, symbol, 'DB_WRITE_FAILED')
                    continue

            stats['results'].append({
                'date': str(trade_date),
                'symbol': symbol,
                'score': score,
                'entry': entry_price,
                'returns': {k: round(v, 4) if v is not None else None for k, v in returns.items()},
                'next_day_close_return': round(next_day_close, 4) if next_day_close is not None else None,
                'next_day_high_return': round(next_day_high, 4) if next_day_high is not None else None,
                'next_day_limit_touch': next_day_limit_touch,
                'next_day_open_return': round(next_day_open, 4) if next_day_open is not None else None,
                'next_day_low_return': round(next_day_low, 4) if next_day_low is not None else None,
                'next_day_gap_return': round(next_day_gap, 4) if next_day_gap is not None else None,
                'next_day_drawdown': round(next_day_drawdown, 4) if next_day_drawdown is not None else None,
                'high_to_close_retrace': round(high_to_close_retrace, 4) if high_to_close_retrace is not None else None,
                **sellable_profile,
            })
            stats['new_success_count'] += 1

            # Progress indicator
            if (i + 1) % 20 == 0:
                print(f'  Progress: {i + 1}/{len(rows)} picks, {stats["fetched"]} fetched, {stats["t1_filled"]} T+1 filled')

    finally:
        bs.logout()

    if dry_run:
        stats['return_coverage_after'] = stats['return_coverage_before']
        stats['top10_return_coverage'] = stats['return_coverage_before']
        stats['paper_pick_return_coverage'] = stats['before']['paper_pick_t1_coverage']
        stats['mainboard_top10_return_coverage'] = stats['before']['mainboard_top10_t1_coverage']
        stats['rank2_to_rank6_return_coverage'] = stats['before']['rank2_to_rank6_t1_coverage']
        stats['after'] = dict(stats['before'])
        stats['return_backfill_timeout_gate'] = build_return_backfill_timeout_gate(stats['failure_reasons'])
        return stats

    with engine.connect() as conn:
        filled_after = conn.execute(text(f"""
            SELECT COUNT(*) FROM daily_candidates dc
            JOIN returns r ON r.trade_date = dc.trade_date AND r.symbol = dc.symbol
            WHERE {candidate_date_scope} {scope} AND r.t1_return IS NOT NULL
        """).bindparams(**date_params)).scalar() if not dry_run else None
        target_count = conn.execute(text(f"SELECT COUNT(*) FROM daily_candidates dc WHERE {candidate_date_scope} {scope}").bindparams(**date_params)).scalar()
        paper_count = conn.execute(text(f"""
            SELECT COUNT(*) FROM picks p WHERE {pick_date_scope} AND p.decision='PAPER_PICK' AND p.symbol IS NOT NULL AND p.symbol <> ''
        """).bindparams(**date_params)).scalar()
        paper_filled = conn.execute(text(f"""
            SELECT COUNT(*) FROM picks p JOIN returns r ON r.trade_date=p.trade_date AND r.symbol=p.symbol
            WHERE {pick_date_scope} AND p.decision='PAPER_PICK' AND r.t1_return IS NOT NULL
        """).bindparams(**date_params)).scalar()
    stats['return_coverage_after'] = round(filled_after / target_count, 4) if filled_after is not None and target_count else stats['return_coverage_before']
    stats['top10_return_coverage'] = stats['return_coverage_after']
    stats['paper_pick_return_coverage'] = round(paper_filled / paper_count, 4) if paper_count else None
    mainboard_target = target_count
    if not dry_run:
        with engine.connect() as conn:
            mainboard_target = conn.execute(text(f"SELECT COUNT(*) FROM daily_candidates dc WHERE {candidate_date_scope} {scope} AND dc.symbol ~ '^(600|601|603|605|000|001|002|003)'").bindparams(**date_params)).scalar()
            mainboard_filled = conn.execute(text(f"""
                SELECT COUNT(*) FROM daily_candidates dc JOIN returns r ON r.trade_date=dc.trade_date AND r.symbol=dc.symbol
                WHERE {candidate_date_scope} {scope} AND dc.symbol ~ '^(600|601|603|605|000|001|002|003)' AND r.t1_return IS NOT NULL
            """).bindparams(**date_params)).scalar()
        stats['mainboard_top10_return_coverage'] = round(mainboard_filled / mainboard_target, 4) if mainboard_target else None
    else:
        stats['mainboard_top10_return_coverage'] = stats['return_coverage_before']
    with engine.connect() as conn:
        rank26_target = conn.execute(text(f"""
            SELECT COUNT(*) FROM daily_candidates dc
            WHERE {candidate_date_scope} {scope}
              AND dc.rank BETWEEN 2 AND 6
              AND dc.symbol ~ '^(600|601|603|605|000|001|002|003)'
        """).bindparams(**date_params)).scalar()
        rank26_filled = conn.execute(text(f"""
            SELECT COUNT(*) FROM daily_candidates dc
            JOIN returns r ON r.trade_date=dc.trade_date AND r.symbol=dc.symbol
            WHERE {candidate_date_scope} {scope}
              AND dc.rank BETWEEN 2 AND 6
              AND dc.symbol ~ '^(600|601|603|605|000|001|002|003)'
              AND r.t1_return IS NOT NULL
        """).bindparams(**date_params)).scalar()
    stats['rank2_to_rank6_return_coverage'] = round(rank26_filled / rank26_target, 4) if rank26_target else None
    stats['after'] = {
        'top10_t1_coverage': stats['top10_return_coverage'],
        'mainboard_top10_t1_coverage': stats['mainboard_top10_return_coverage'],
        'paper_pick_t1_coverage': stats['paper_pick_return_coverage'],
        'rank2_to_rank6_t1_coverage': stats['rank2_to_rank6_return_coverage'],
    }
    stats['return_backfill_timeout_gate'] = build_return_backfill_timeout_gate(stats['failure_reasons'])
    # Hygiene: always re-link returns.pick_id after writes so pick_id JOIN path stays usable.
    if not dry_run:
        try:
            stats['pick_id_backfill'] = backfill_return_pick_ids()
        except Exception as exc:
            stats['pick_id_backfill'] = {'error': str(exc)}
    return stats


def print_analysis(stats: dict):
    """Print win rate analysis after backfill."""
    print('\n' + '=' * 60)
    print('BACKFILL SUMMARY')
    print('=' * 60)
    print(f'Total PAPER_PICK records: {stats["total_picks"]}')
    print(f'Already had returns: {stats["already_filled"]}')
    print(f'Needed backfill: {stats["missing_returns"]}')
    print(f'Successfully fetched: {stats["fetched"]}')
    print(f'Fetch failed: {stats["fetch_failed"]}')
    print(f'T+1 filled: {stats["t1_filled"]}')
    print(f'T+2 filled: {stats["t2_filled"]}')
    print(f'T+3 filled: {stats["t3_filled"]}')
    print(f'T+5 filled: {stats["t5_filled"]}')
    pick_id_stats = stats.get('pick_id_backfill') or {}
    if pick_id_stats:
        print(
            f"returns.pick_id linked={pick_id_stats.get('linked')} "
            f"null_remaining={pick_id_stats.get('null_pick_id_remaining')} "
            f"total={pick_id_stats.get('returns_total')}"
        )

    # Analyze T+1 returns
    t1_returns = [r['returns']['t1'] for r in stats['results']
                  if isinstance(r.get('returns'), dict) and r['returns'].get('t1') is not None]

    if t1_returns:
        wins = sum(1 for r in t1_returns if r > 0)
        losses = sum(1 for r in t1_returns if r < 0)
        flat = sum(1 for r in t1_returns if r == 0)
        avg_win = sum(r for r in t1_returns if r > 0) / max(wins, 1)
        avg_loss = sum(r for r in t1_returns if r < 0) / max(losses, 1)
        avg_return = sum(t1_returns) / len(t1_returns)

        print('\n' + '=' * 60)
        print('T+1 RETURN ANALYSIS (收盘收益)')
        print('=' * 60)
        print(f'Total with T+1 return: {len(t1_returns)}')
        print(f'Wins: {wins} ({wins/len(t1_returns)*100:.1f}%)')
        print(f'Losses: {losses} ({losses/len(t1_returns)*100:.1f}%)')
        print(f'Flat: {flat} ({flat/len(t1_returns)*100:.1f}%)')
        print(f'Avg win: +{avg_win*100:.2f}%')
        print(f'Avg loss: {avg_loss*100:.2f}%')
        print(f'Avg return: {avg_return*100:.2f}%')
        ratio = f'{abs(avg_win / avg_loss):.2f}' if losses and avg_loss else 'N/A'
        print(f'Win/Loss ratio: {ratio}')

        # Distribution
        buckets = [
            ('< -5%', lambda r: r < -0.05),
            ('-5% ~ -2%', lambda r: -0.05 <= r < -0.02),
            ('-2% ~ 0%', lambda r: -0.02 <= r < 0),
            ('0% ~ 2%', lambda r: 0 <= r < 0.02),
            ('2% ~ 5%', lambda r: 0.02 <= r < 0.05),
            ('> 5%', lambda r: r >= 0.05),
        ]
        print('\nReturn distribution:')
        for label, cond in buckets:
            count = sum(1 for r in t1_returns if cond(r))
            bar = '#' * min(count, 50)
            print(f'  {label:>10}: {count:3d} ({count/len(t1_returns)*100:5.1f}%) {bar}')

    # Analyze next_day_high_return (次日最高收益)
    high_returns = [r.get('next_day_high_return') for r in stats['results']
                    if r.get('next_day_high_return') is not None]
    limit_touches = [r for r in stats['results'] if r.get('next_day_limit_touch')]

    if high_returns:
        print('\n' + '=' * 60)
        print('NEXT DAY HIGH RETURN ANALYSIS (次日最高收益)')
        print('=' * 60)
        print(f'Total with high return data: {len(high_returns)}')
        avg_high = sum(high_returns) / len(high_returns)
        max_high = max(high_returns)
        positive_high = sum(1 for r in high_returns if r > 0)
        print(f'Avg high return: +{avg_high*100:.2f}%')
        print(f'Max high return: +{max_high*100:.2f}%')
        print(f'Positive high: {positive_high}/{len(high_returns)} ({positive_high/len(high_returns)*100:.1f}%)')

    if limit_touches:
        print(f'\nLimit touch (涨停触及): {len(limit_touches)}/{len(stats["results"])} ({len(limit_touches)/max(len(stats["results"]),1)*100:.1f}%)')

    # Analyze gap return (跳空缺口)
    gap_returns = [r.get('next_day_gap_return') for r in stats['results']
                   if r.get('next_day_gap_return') is not None]
    if gap_returns:
        print('\n' + '=' * 60)
        print('NEXT DAY GAP RETURN ANALYSIS (次日跳空缺口)')
        print('=' * 60)
        avg_gap = sum(gap_returns) / len(gap_returns)
        positive_gap = sum(1 for r in gap_returns if r > 0)
        negative_gap = sum(1 for r in gap_returns if r < 0)
        print(f'Avg gap: {avg_gap*100:+.2f}%')
        print(f'High open: {positive_gap}/{len(gap_returns)} ({positive_gap/len(gap_returns)*100:.1f}%)')
        print(f'Low open: {negative_gap}/{len(gap_returns)} ({negative_gap/len(gap_returns)*100:.1f}%)')

    # Analyze drawdown (日内回撤)
    drawdowns = [r.get('next_day_drawdown') for r in stats['results']
                 if r.get('next_day_drawdown') is not None]
    if drawdowns:
        print('\n' + '=' * 60)
        print('NEXT DAY DRAWDOWN ANALYSIS (日内回撤)')
        print('=' * 60)
        avg_dd = sum(drawdowns) / len(drawdowns)
        max_dd = min(drawdowns)  # Most negative
        severe_dd = sum(1 for r in drawdowns if r < -0.03)
        print(f'Avg drawdown: {avg_dd*100:.2f}%')
        print(f'Max drawdown: {max_dd*100:.2f}%')
        print(f'Severe (>3%): {severe_dd}/{len(drawdowns)} ({severe_dd/len(drawdowns)*100:.1f}%)')

    # Analyze high_to_close_retrace (高点回撤)
    retraces = [r.get('high_to_close_retrace') for r in stats['results']
                if r.get('high_to_close_retrace') is not None]
    if retraces:
        print('\n' + '=' * 60)
        print('HIGH TO CLOSE RETRACE ANALYSIS (高点回撤)')
        print('=' * 60)
        avg_retrace = sum(retraces) / len(retraces)
        severe_retrace = sum(1 for r in retraces if r < -0.03)
        print(f'Avg retrace: {avg_retrace*100:.2f}%')
        print(f'Severe (>3%): {severe_retrace}/{len(retraces)} ({severe_retrace/len(retraces)*100:.1f}%)')

    # Sellable profit analysis (可卖出利润)
    print('\n' + '=' * 60)
    print('SELLABLE PROFIT ANALYSIS (可卖出利润)')
    print('=' * 60)
    print('低开不能恐慌卖: 历史低开票仍可能盘中修复，先看9:30-10:00承接与翻红能力。')
    print('等待反弹条件: 未跌破关键低点、板块未同步崩、缩量企稳或快速翻红。')
    print('必须止损条件: 10:00前跌破关键低点且反抽无力，或跌破分时均价线后无法收回。')
    if high_returns and gap_returns:
        # 模拟不同卖出策略
        strategies = {
            '高开冲高卖': lambda h, g: min(h, g + 0.03) if g > 0 else h * 0.5,
            '冲高回落卖': lambda h, g: h * 0.7,
            '10:00前不强卖': lambda h, g: h * 0.8,
        }
        for name, strategy in strategies.items():
            profits = [strategy(r['next_day_high_return'], r.get('next_day_gap_return', 0))
                      for r in stats['results']
                      if r.get('next_day_high_return') is not None]
            if profits:
                avg_profit = sum(profits) / len(profits)
                positive = sum(1 for p in profits if p > 0)
                print(f'{name}: avg={avg_profit*100:+.2f}%, positive={positive}/{len(profits)} ({positive/len(profits)*100:.1f}%)')

    sellable_rows = [r for r in stats['results'] if r.get('sellable_profit') is not None]
    if sellable_rows:
        sellable = [r['sellable_profit'] for r in sellable_rows]
        capture_ratios = [r['profit_capture_ratio'] for r in sellable_rows
                          if r.get('profit_capture_ratio') is not None]
        avg_sellable = sum(sellable) / len(sellable)
        avg_capture = sum(capture_ratios) / len(capture_ratios) if capture_ratios else None
        low_open_rows = [r for r in sellable_rows if (r.get('next_day_open_return') or 0) < 0]
        low_open_rebounds = [r for r in low_open_rows if (r.get('next_day_high_return') or 0) > 0]
        panic_saved = [
            r['sellable_profit'] - r['next_day_open_return']
            for r in sellable_rows
            if r.get('panic_sell_avoided') and r.get('next_day_open_return') is not None
        ]

        print('\n' + '=' * 60)
        print('RULE-BASED SELLABLE PROFIT (规则可卖收益)')
        print('=' * 60)
        print(f'Avg sellable profit: {avg_sellable*100:+.2f}%')
        if avg_capture is not None:
            print(f'Avg profit capture ratio: {avg_capture*100:.1f}%')
        print(f'Positive sellable: {sum(1 for r in sellable if r > 0)}/{len(sellable)} ({sum(1 for r in sellable if r > 0)/len(sellable)*100:.1f}%)')
        if low_open_rows:
            rebound_rate = len(low_open_rebounds) / len(low_open_rows)
            print(f'Low-open rebound probability: {len(low_open_rebounds)}/{len(low_open_rows)} ({rebound_rate*100:.1f}%)')
        if panic_saved:
            print(f'Panic-sell avoided benefit: {sum(panic_saved)/len(panic_saved)*100:+.2f}% avg vs open sell')

        style_fields = {
            'conservative': 'sellable_profit_v1_conservative',
            'normal': 'sellable_profit_v2_normal',
            'aggressive': 'sellable_profit_v3_aggressive',
        }
        style_avgs = {}
        for label, field in style_fields.items():
            values = [r[field] for r in sellable_rows if r.get(field) is not None]
            if values:
                style_avgs[label] = sum(values) / len(values)
                print(f'{label} avg: {style_avgs[label]*100:+.2f}%')
        if style_avgs:
            best_style = max(style_avgs.items(), key=lambda item: item[1])
            lowest_drawdown_style = min(style_avgs.items(), key=lambda item: abs(min(0.0, item[1])))
            print(f'Highest average style: {best_style[0]} ({best_style[1]*100:+.2f}%)')
            print(f'Lowest average downside style: {lowest_drawdown_style[0]} ({lowest_drawdown_style[1]*100:+.2f}%)')

        by_strategy = {}
        for row in sellable_rows:
            by_strategy.setdefault(row.get('sell_strategy_used') or 'unknown', []).append(row)
        print('\nSell strategy performance:')
        for name, rows in sorted(by_strategy.items()):
            profits = [r['sellable_profit'] for r in rows]
            drawdowns_for_rows = [r.get('next_day_drawdown') for r in rows if r.get('next_day_drawdown') is not None]
            avg_profit = sum(profits) / len(profits)
            avg_drawdown = sum(drawdowns_for_rows) / len(drawdowns_for_rows) if drawdowns_for_rows else None
            suffix = f', avg_drawdown={avg_drawdown*100:.2f}%' if avg_drawdown is not None else ''
            print(f'  {name}: n={len(rows)}, avg_sellable={avg_profit*100:+.2f}%{suffix}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Backfill missing returns')
    ap.add_argument('--dry-run', action='store_true', help='Do not write to DB')
    ap.add_argument('--per-symbol-timeout', type=int, default=DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS, help='Baostock request timeout seconds')
    ap.add_argument('--batch-soft-timeout', type=int, default=DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS, help='Soft limit for one resume batch')
    ap.add_argument('--trade-date', default=None, help='Decision date to backfill without rescanning')
    ap.add_argument('--validate-on', default=None, help='Expected next trading date for T+1 validation')
    args = ap.parse_args()

    print('Starting return backfill...')
    stats = backfill_returns(
        dry_run=args.dry_run,
        per_symbol_timeout_seconds=args.per_symbol_timeout,
        batch_soft_timeout_seconds=args.batch_soft_timeout,
        input_trade_date=args.trade_date,
        validation_trade_date=args.validate_on,
    )
    print_analysis(stats)

    # Save detailed results
    output_path = ROOT / 'summary' / 'return_backfill_results.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False, default=str)
    print(f'\nDetailed results saved to {output_path}')
    if not args.dry_run and stats.get('pick_id_backfill'):
        print(f"  pick_id hygiene: {stats['pick_id_backfill']}")
