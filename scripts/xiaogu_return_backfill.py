#!/usr/bin/env python3
"""Backfill the single official T+1 close return for PAPER_PICK records.

Uses baostock API to fetch close prices (forward-adjusted).
Calculates ``(T+1 close - T-day entry close) / T-day entry close`` and updates
the returns table. Other historical return columns are intentionally ignored.
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
from xiaogu_db import (
    engine,
    record_return_backfill_failure,
    update_production_run_step,
    upsert_return,
)
from xiaogu_forward_result_filler_v0_1 import (
    fetch_eastmoney_klines, parse_klines as parse_eastmoney_klines,
    secid_for,
)

# Rate limit: 50ms between requests
REQUEST_DELAY = 0.05

HORIZON_OFFSETS = {'t1': 1}

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


def close_on_date(klines: list, target_date: str) -> Optional[float]:
    """Extract close price for a specific date from kline data."""
    return price_on_date(klines, target_date, 'close')


def calculate_return(entry_price: float, exit_price: float) -> Optional[float]:
    """Calculate return as (exit - entry) / entry."""
    if entry_price is None or exit_price is None or entry_price <= 0:
        return None
    return round((exit_price - entry_price) / entry_price, 6)


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
    production_run_id: str | None = None,
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
        'eastmoney_same_source_merge_count': 0,
        'return_backfill_config': {
            'per_symbol_timeout_seconds': per_symbol_timeout_seconds,
            'batch_soft_timeout_seconds': batch_soft_timeout_seconds,
            'resume_enabled': True,
            'skip_existing_success': True,
        },
        'input_trade_date': input_trade_date,
        'validation_trade_date': validation_trade_date,
        'production_run_id': production_run_id,
        'run_settlement': {},
    }

    if input_trade_date and validation_trade_date:
        expected_validation_date = next_trading_day(date.fromisoformat(input_trade_date), 1).isoformat()
        if validation_trade_date != expected_validation_date:
            stats['fatal_error'] = 'VALIDATION_TRADE_DATE_MISMATCH'
            stats['expected_validation_trade_date'] = expected_validation_date
            return stats

    def record_failure(trade_date, symbol, reason, run_id, candidate_snapshot_id):
        """Count a standardized failure and persist its audit payload when possible."""
        normalized_reason = reason if reason in FAILURE_REASONS else 'UNKNOWN'
        stats['fetch_failed'] += 1
        stats['new_failure_count'] += 1
        stats['failed_return_symbols'].append({
            'trade_date': str(trade_date), 'symbol': str(symbol), 'reason': normalized_reason,
        })
        stats['failed_return_reasons'][normalized_reason] = stats['failed_return_reasons'].get(normalized_reason, 0) + 1
        stats['failure_reasons'][normalized_reason] = stats['failure_reasons'].get(normalized_reason, 0) + 1
        if run_id:
            run_state = stats['run_settlement'].setdefault(run_id, {'failed': 0, 'settled': 0, 'pending': 0})
            run_state['failed'] += 1
        if dry_run:
            return
        try:
            record_return_backfill_failure(
                trade_date,
                str(symbol),
                normalized_reason,
                production_run_id=run_id,
                candidate_snapshot_id=candidate_snapshot_id,
            )
        except Exception as exc:
            print(f'  ERROR: failure persistence failed for {trade_date} {symbol}: {exc}')
            if normalized_reason != 'DB_WRITE_FAILED':
                stats['fetch_failed'] += 1
                stats['new_failure_count'] += 1
                stats['failed_return_reasons']['DB_WRITE_FAILED'] = stats['failed_return_reasons'].get('DB_WRITE_FAILED', 0) + 1
                stats['failure_reasons']['DB_WRITE_FAILED'] = stats['failure_reasons'].get('DB_WRITE_FAILED', 0) + 1

    # Every production candidate is settled against its own immutable run.
    candidate_scope = 'AND dc.rank <= 10' if top10_only else ''
    candidate_date_scope = 'dc.trade_date = :input_trade_date' if input_trade_date else 'dc.trade_date >= :start_date'
    date_params = {'input_trade_date': input_trade_date} if input_trade_date else {'start_date': start_date}
    if production_run_id:
        active_run_join = ''
        run_scope = 'AND dc.production_run_id = :production_run_id'
        date_params['production_run_id'] = production_run_id
    else:
        active_run_join = """
            JOIN production_run_active pra
              ON pra.trade_date = dc.trade_date
             AND pra.production_run_id = dc.production_run_id
        """
        run_scope = ''
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT dc.trade_date, dc.symbol, dc.final_score,
                   COALESCE(dc.is_official_pick, FALSE) AS is_official_pick,
                   dc.rank, dc.production_run_id, dc.candidate_snapshot_id,
                   p.id AS pick_id
            FROM daily_candidates dc
            {active_run_join}
            LEFT JOIN picks p
              ON p.production_run_id = dc.production_run_id
             AND p.symbol = dc.symbol
             AND p.decision = 'PAPER_PICK'
            WHERE {candidate_date_scope}
              {candidate_scope}
              {run_scope}
              AND dc.production_run_id IS NOT NULL
              AND dc.symbol IS NOT NULL
              AND dc.symbol != ''
              AND dc.symbol != '000000'
            ORDER BY dc.trade_date, dc.production_run_id, dc.symbol, dc.rank NULLS LAST
        """).bindparams(**date_params)).fetchall()

    stats['total_picks'] = len(rows)
    print(f'Found {len(rows)} top10 candidate records with valid symbols')
    run_ids = sorted({str(row[5]) for row in rows if row[5]})
    for run_id in run_ids:
        stats['run_settlement'].setdefault(run_id, {'failed': 0, 'settled': 0, 'pending': 0})
        if not dry_run:
            update_production_run_step(
                run_id,
                't1_settlement',
                'RUNNING',
                required=True,
                retry_command=f'python3 scripts/xiaogu_return_backfill.py --production-run-id {run_id}',
            )

    # Check which ones already have returns
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT production_run_id, symbol, t1_return
            FROM returns
            WHERE production_run_id IS NOT NULL
        """)).fetchall()

    existing_map = {}
    for r in existing:
        key = (str(r[0]), str(r[1]).zfill(6))
        existing_map[key] = {'t1': r[2]}

    target_keys = {(str(row[5]), str(row[1]).zfill(6)) for row in rows}
    filled_before = sum(1 for key in target_keys if existing_map.get(key, {}).get('t1') is not None)
    stats['return_coverage_before'] = round(filled_before / len(target_keys), 4) if target_keys else None
    stats['skipped_existing_success_count'] = filled_before
    mainboard_keys = {
        key for key in target_keys
        if str(key[1]).startswith(('600', '601', '603', '605', '000', '001', '002', '003'))
    }
    rank26_keys = {
        (str(row[5]), str(row[1]).zfill(6)) for row in rows
        if len(row) > 4 and 2 <= int(row[4] or 999) <= 6
        and str(row[1]).zfill(6).startswith(('600', '601', '603', '605', '000', '001', '002', '003'))
    }
    paper_rows = []
    if not dry_run:
        with engine.connect() as conn:
            paper_active_join = '' if production_run_id else """
            JOIN production_run_active pra
              ON pra.trade_date = p.trade_date
             AND pra.production_run_id = p.production_run_id
            """
            paper_rows = conn.execute(text(f"""
            SELECT p.production_run_id, p.symbol FROM picks p
            {paper_active_join}
            WHERE {candidate_date_scope.replace('dc.', 'p.')} AND p.decision='PAPER_PICK'
              {'' if not production_run_id else 'AND p.production_run_id = :production_run_id'}
              AND p.symbol IS NOT NULL AND p.symbol <> ''
            """).bindparams(**date_params)).fetchall()
    paper_keys = {(str(row[0]), str(row[1]).zfill(6)) for row in paper_rows}
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
        for row in rows:
            record_failure(
                row[0],
                row[1],
                'BAOSTOCK_LOGIN_FAILED',
                str(row[5]),
                str(row[6] or row[5]),
            )
        stats['return_backfill_timeout_gate'] = build_return_backfill_timeout_gate(stats['failure_reasons'])
        if not dry_run:
            for run_id in run_ids:
                update_production_run_step(
                    run_id,
                    't1_settlement',
                    'FAIL',
                    required=True,
                    error_message='BAOSTOCK_LOGIN_FAILED',
                    retry_command=f'python3 scripts/xiaogu_return_backfill.py --production-run-id {run_id}',
                )
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
            run_id = str(row[5] or '')
            candidate_snapshot_id = str(row[6] or run_id)
            pick_id = row[7]
            try:
                symbol = str(raw_symbol).zfill(6)
                baostock_symbol(raw_symbol)
            except ValueError:
                record_failure(
                    trade_date, raw_symbol, 'SYMBOL_FORMAT_ERROR', run_id, candidate_snapshot_id,
                )
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

            key = (run_id, symbol)
            existing_ret = existing_map.get(key, {})

            # The official lifecycle has exactly one result: T+1 close.
            needs = {}
            if existing_ret.get('t1') is not None:
                stats['already_filled'] += 1
                stats['run_settlement'][run_id]['settled'] += 1
            else:
                target_date = next_trading_day(trade_date, 1)
                if target_date <= as_of_date:
                    needs['t1'] = (1, target_date)
                else:
                    stats['run_settlement'][run_id]['pending'] += 1

            if not needs:
                continue

            stats['missing_returns'] += 1

            # Fetch kline data
            max_offset = max(v[0] for v in needs.values())
            max_target = next_trading_day(trade_date, max_offset)

            try:
                with per_symbol_timeout(per_symbol_timeout_seconds):
                    klines = fetch_kline_range_baostock(symbol, str(trade_date), str(max_target))
            except ReturnFetchTimeout as exc:
                record_failure(trade_date, symbol, 'BAOSTOCK_TIMEOUT', run_id, candidate_snapshot_id)
                continue
            except BaoStockQueryError:
                record_failure(trade_date, symbol, 'BAOSTOCK_QUERY_FAILED', run_id, candidate_snapshot_id)
                continue
            except Exception as exc:
                reason = 'NETWORK_ERROR' if type(exc).__name__ in {'ConnectionError', 'TimeoutError'} else 'UNKNOWN'
                record_failure(trade_date, symbol, reason, run_id, candidate_snapshot_id)
                continue
            time.sleep(REQUEST_DELAY)

            target_dates = {str(target_date) for _, target_date in needs.values()}
            have_dates = {row.get('date') for row in klines}
            if target_dates - have_dates:
                try:
                    klines, merged_same_source = merge_eastmoney_missing_klines(
                        symbol, str(trade_date), str(max_target), klines,
                    )
                    if merged_same_source:
                        stats['eastmoney_same_source_merge_count'] += 1
                except Exception:
                    pass

            if not klines:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA', run_id, candidate_snapshot_id)
                continue

            # Get entry price
            entry_price = price_on_date(klines, str(trade_date))
            if entry_price is None:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA', run_id, candidate_snapshot_id)
                continue

            stats['fetched'] += 1

            # Calculate the only production return from the final T+1 close.
            returns = {}
            t1_date = next_trading_day(trade_date, 1)
            if t1_date <= as_of_date:
                t1_close = close_on_date(klines, str(t1_date))
                if t1_close is not None:
                    returns['t1'] = calculate_return(entry_price, t1_close)
                    if returns['t1'] is not None:
                        stats['t1_filled'] += 1

            for horizon, (offset, target_date) in needs.items():
                exit_price = price_on_date(klines, str(target_date))
                if horizon == 't1' and exit_price is not None and 't1' not in returns:
                    ret = calculate_return(entry_price, exit_price)
                    returns[horizon] = ret
                    if ret is not None:
                        stats[f'{horizon}_filled'] += 1

            if not returns:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA', run_id, candidate_snapshot_id)
                continue

            # Update database
            if not dry_run and returns:
                try:
                    upsert_return(
                        trade_date=trade_date,
                        symbol=symbol,
                        pick_id=pick_id,
                        t1_return=returns.get('t1'),
                        production_run_id=run_id,
                        candidate_snapshot_id=candidate_snapshot_id,
                        return_status='SETTLED',
                        settlement_evidence={
                            'provider': 'baostock',
                            'entry_trade_date': str(trade_date),
                            'exit_trade_date': str(t1_date),
                            'price_source': 'T_PLUS_1_CLOSE',
                        },
                    )
                    existing_map[key] = {'t1': returns.get('t1')}
                except Exception as e:
                    print(f'  ERROR: upsert_return failed for {trade_date} {symbol}: {e}')
                    record_failure(trade_date, symbol, 'DB_WRITE_FAILED', run_id, candidate_snapshot_id)
                    continue

            stats['results'].append({
                'date': str(trade_date),
                'symbol': symbol,
                'score': score,
                'entry': entry_price,
                'returns': {k: round(v, 4) if v is not None else None for k, v in returns.items()},
                'production_trade_mode': 'T_DAY_BUY_T1_CLOSE_SELL',
            })
            stats['new_success_count'] += 1
            stats['run_settlement'][run_id]['settled'] += 1

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

    filled_after = sum(
        existing_map.get(key, {}).get('t1') is not None
        for key in target_keys
    )
    mainboard_filled = sum(
        existing_map.get(key, {}).get('t1') is not None
        for key in mainboard_keys
    )
    rank26_filled = sum(
        existing_map.get(key, {}).get('t1') is not None
        for key in rank26_keys
    )
    paper_filled = sum(
        existing_map.get(key, {}).get('t1') is not None
        for key in paper_keys
    )
    stats['return_coverage_after'] = round(filled_after / len(target_keys), 4) if target_keys else None
    stats['top10_return_coverage'] = stats['return_coverage_after']
    stats['paper_pick_return_coverage'] = round(paper_filled / len(paper_keys), 4) if paper_keys else None
    stats['mainboard_top10_return_coverage'] = (
        round(mainboard_filled / len(mainboard_keys), 4) if mainboard_keys else None
    )
    stats['rank2_to_rank6_return_coverage'] = (
        round(rank26_filled / len(rank26_keys), 4) if rank26_keys else None
    )
    stats['after'] = {
        'top10_t1_coverage': stats['top10_return_coverage'],
        'mainboard_top10_t1_coverage': stats['mainboard_top10_return_coverage'],
        'paper_pick_t1_coverage': stats['paper_pick_return_coverage'],
        'rank2_to_rank6_t1_coverage': stats['rank2_to_rank6_return_coverage'],
    }
    stats['return_backfill_timeout_gate'] = build_return_backfill_timeout_gate(stats['failure_reasons'])
    if not dry_run:
        for run_id, run_state in stats['run_settlement'].items():
            if run_state['failed']:
                status = 'FAIL'
                error_message = f"T1_SETTLEMENT_FAILURES:{run_state['failed']}"
            elif run_state['pending']:
                status = 'PENDING'
                error_message = ''
            else:
                status = 'PASS'
                error_message = ''
            update_production_run_step(
                run_id,
                't1_settlement',
                status,
                required=True,
                error_message=error_message,
                retry_command=(
                    f'python3 scripts/xiaogu_return_backfill.py --production-run-id {run_id}'
                    if status != 'PASS' else ''
                ),
                metadata=run_state,
            )
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

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Backfill missing returns')
    ap.add_argument('--dry-run', action='store_true', help='Do not write to DB')
    ap.add_argument('--per-symbol-timeout', type=int, default=DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS, help='Baostock request timeout seconds')
    ap.add_argument('--batch-soft-timeout', type=int, default=DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS, help='Soft limit for one resume batch')
    ap.add_argument('--trade-date', default=None, help='Decision date to backfill without rescanning')
    ap.add_argument('--validate-on', default=None, help='Expected next trading date for T+1 validation')
    ap.add_argument('--production-run-id', default='', help='Explicit historical production run to settle')
    args = ap.parse_args()

    print('Starting return backfill...')
    stats = backfill_returns(
        dry_run=args.dry_run,
        per_symbol_timeout_seconds=args.per_symbol_timeout,
        batch_soft_timeout_seconds=args.batch_soft_timeout,
        input_trade_date=args.trade_date,
        validation_trade_date=args.validate_on,
        production_run_id=args.production_run_id or None,
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
    if not args.dry_run and (stats.get('fatal_error') or stats.get('new_failure_count')):
        raise SystemExit(1)
