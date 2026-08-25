#!/usr/bin/env python3
"""Backfill canonical T+1 labels for PAPER_PICK records.

Uses unadjusted daily OHLC and the explicit T-day close proxy execution
contract. Legacy return columns remain compatible, but canonical labels are
the only research target.
"""
import json
import signal
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from xiaogu_db import (
    engine,
    record_return_backfill_failure,
    update_production_run_step,
    upsert_return,
)
from xiaogu_forward_result_filler_v0_1 import (
    CANONICAL_LABEL_VERSION,
    CANONICAL_MARKET_DATA_SOURCE,
    CANONICAL_PRICE_BASIS,
    build_execution_contract,
    calculate_t1_labels,
    fetch_canonical_daily_ohlc,
    shadow_execution_profile,
    target_quality_gate,
)
from xiaogu_scheduler import is_trading_day as _scheduler_is_trading_day

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
    'INVALID_T1_LABELS',
    'UNKNOWN',
}
DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS = 8
DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS = 600


class ReturnFetchTimeout(TimeoutError):
    pass


def _stored_execution_label_patch(row: dict) -> Optional[dict]:
    """Return a canonical label patch from persisted execution evidence.

    Older settlement rows already contain the costed execution result inside
    ``settlement_evidence.execution_model`` but were written before the
    dedicated return columns were added.  This migration only fills missing
    canonical fields; it never recomputes or replaces an existing net label.
    """
    if not isinstance(row, dict) or row.get('t1_net_return') is not None:
        return None
    evidence = row.get('settlement_evidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    execution = evidence.get('execution_model')
    execution = execution if isinstance(execution, dict) else {}
    net_return = execution.get('net_return')
    if net_return in (None, ''):
        return None
    try:
        net_return = float(net_return)
    except (TypeError, ValueError):
        return None

    labels = {
        't1_open_return': row.get('t1_open_return'),
        't1_high_return': row.get('t1_high_return'),
        't1_low_return': row.get('t1_low_return'),
        't1_close_return': row.get('t1_close_return'),
        't1_mfe': row.get('t1_mfe'),
        't1_mae': row.get('t1_mae'),
        't1_vwap_return': row.get('t1_vwap_return'),
        't1_gap_return': row.get('t1_gap_return'),
        't1_net_return': net_return,
        'execution_price': row.get('entry_price') or execution.get('entry_reference_price'),
        'slippage': (
            float(execution.get('buy_slippage') or 0.0)
            + float(execution.get('sell_slippage') or 0.0)
        ),
        'commission': execution.get('commission'),
        'stamp_duty': execution.get('stamp_duty'),
        'transfer_fee': execution.get('transfer_fee'),
        'market_impact': execution.get('impact_cost'),
        'label_status': row.get('label_status') or 'SETTLED',
    }
    return labels


def repair_stored_canonical_net_returns(
    *,
    dry_run: bool = False,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    production_run_id: Optional[str] = None,
) -> dict:
    """Backfill missing net labels without fetching or changing market data.

    This is intentionally part of the existing return-backfill owner.  It is
    a label migration for already settled rows, not a second settlement path.
    """
    clauses = [
        "label_version = :label_version",
        "label_status = 'SETTLED'",
        "t1_net_return IS NULL",
        "settlement_evidence -> 'execution_model' ->> 'net_return' IS NOT NULL",
    ]
    params = {'label_version': CANONICAL_LABEL_VERSION}
    if start_date:
        clauses.append('trade_date >= CAST(:start_date AS date)')
        params['start_date'] = start_date
    if end_date:
        clauses.append('trade_date <= CAST(:end_date AS date)')
        params['end_date'] = end_date
    if production_run_id:
        clauses.append('production_run_id = :production_run_id')
        params['production_run_id'] = production_run_id
    query = text(f"""
        SELECT id, trade_date, symbol, pick_id, production_run_id,
               candidate_snapshot_id, t1_return,
               t1_open_return, t1_high_return, t1_low_return,
               t1_close_return, t1_mfe, t1_mae, t1_vwap_return,
               t1_gap_return, t1_net_return, entry_price,
               label_status, settlement_evidence
        FROM returns
        WHERE {' AND '.join(clauses)}
        ORDER BY trade_date, symbol, id
    """)
    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(query, params).mappings()]

    result = {
        'status': 'DRY_RUN' if dry_run else 'COMPLETED',
        'candidate_rows': len(rows),
        'repaired': 0,
        'skipped_invalid_evidence': 0,
        'rows': [],
    }
    for row in rows:
        patch = _stored_execution_label_patch(row)
        if not patch:
            result['skipped_invalid_evidence'] += 1
            continue
        evidence = row.get('settlement_evidence')
        evidence = dict(evidence) if isinstance(evidence, dict) else {}
        evidence['t1_metrics'] = {
            **(evidence.get('t1_metrics') if isinstance(evidence.get('t1_metrics'), dict) else {}),
            **patch,
        }
        evidence['label_backfill'] = {
            'status': 'APPLIED' if not dry_run else 'PLANNED',
            'reason': 'RECOVERED_FROM_PERSISTED_EXECUTION_MODEL',
            'source_row_id': row.get('id'),
            'label_version': CANONICAL_LABEL_VERSION,
        }
        item = {
            'id': row.get('id'),
            'trade_date': str(row.get('trade_date')),
            'symbol': str(row.get('symbol')).zfill(6),
            't1_net_return': patch['t1_net_return'],
        }
        if not dry_run:
            upsert_return(
                trade_date=row['trade_date'],
                symbol=item['symbol'],
                pick_id=row.get('pick_id'),
                # Do not pass the legacy close-return column here.  Some
                # historical rows use a different rounding/source; the
                # canonical label patch owns t1_close_return for this repair.
                t1_return=None,
                production_run_id=row.get('production_run_id'),
                candidate_snapshot_id=row.get('candidate_snapshot_id') or '',
                return_status='SETTLED',
                settlement_evidence=evidence,
                t1_labels=patch,
            )
        result['repaired'] += 1
        result['rows'].append(item)
    return result


@contextmanager
def per_symbol_timeout(seconds: int):
    """Bound a blocking market-data request without abandoning the full run."""
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
    """Use the project's XSHG calendar, with a weekday fallback."""
    try:
        return bool(_scheduler_is_trading_day(d))
    except Exception:
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


def price_on_date(klines: list, target_date: str, field: str = 'close') -> Optional[float]:
    """Extract price for a specific date from kline data."""
    for row in klines:
        if row['date'] == target_date:
            return row.get(field)
    return None


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
    top10_only: bool = False,
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
        'existing_candidate_label_sync_count': 0,
        'failure_reasons': {},
        'before': {},
        'after': {},
        'batch_soft_timeout_exceeded': False,
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
    print(f'Found {len(rows)} candidate records with valid symbols')
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
                SELECT production_run_id, symbol, t1_return, settlement_evidence,
                   t1_open_return, t1_high_return, t1_low_return,
                   t1_close_return, t1_mfe, t1_mae,
                   t1_vwap_return, t1_gap_return, t1_net_return,
                   label_source, label_version
            FROM returns
            WHERE production_run_id IS NOT NULL
        """)).fetchall()

    existing_map = {}
    for r in existing:
        key = (str(r[0]), str(r[1]).zfill(6))
        stored_evidence = r[3] if len(r) > 3 and isinstance(r[3], dict) else {}
        existing_map[key] = {
            't1': r[2],
            'settlement_evidence': stored_evidence,
            'legacy_shape': len(r) < 10,
            'label_source': r[13] if len(r) > 13 else None,
            'label_version': r[14] if len(r) > 14 else None,
            'labels': {
                field: r[index] if len(r) > index else (
                    stored_evidence.get('t1_metrics', {}).get(field)
                    if isinstance(stored_evidence.get('t1_metrics'), dict)
                    else None
                )
                for field, index in zip(
                    ('t1_open_return', 't1_high_return', 't1_low_return',
                     't1_close_return', 't1_mfe', 't1_mae',
                     't1_vwap_return', 't1_gap_return', 't1_net_return'),
                    range(4, 13),
                )
            },
        }

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
                if not raw_symbol.isdigit() or len(raw_symbol) > 6:
                    raise ValueError('SYMBOL_FORMAT_ERROR')
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

            # A legacy close-only row must be revisited until all canonical
            # labels exist; close-only coverage is not target readiness.
            needs = {}
            labels_complete = existing_ret.get('legacy_shape') or (
                existing_ret.get('label_source') == CANONICAL_MARKET_DATA_SOURCE
                and existing_ret.get('label_version') == CANONICAL_LABEL_VERSION
                and all(
                existing_ret.get('labels', {}).get(field) is not None
                for field in (
                    't1_open_return', 't1_high_return', 't1_low_return',
                    't1_close_return', 't1_mfe', 't1_mae', 't1_net_return',
                )
                )
            )
            if existing_ret.get('t1') is not None and labels_complete:
                stats['already_filled'] += 1
                stats['run_settlement'][run_id]['settled'] += 1
                if not dry_run:
                    try:
                        upsert_return(
                            trade_date=trade_date,
                            symbol=symbol,
                            pick_id=pick_id,
                            t1_return=existing_ret['t1'],
                            t1_labels=existing_ret.get('labels') or {},
                            production_run_id=run_id,
                            candidate_snapshot_id=candidate_snapshot_id,
                            return_status='SETTLED',
                            settlement_evidence=existing_ret.get('settlement_evidence') or {},
                        )
                        stats['existing_candidate_label_sync_count'] += 1
                    except Exception:
                        record_failure(
                            trade_date,
                            symbol,
                            'DB_WRITE_FAILED',
                            run_id,
                            candidate_snapshot_id,
                        )
                        continue
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
                    klines = fetch_canonical_daily_ohlc(symbol, str(trade_date), str(max_target))
            except ReturnFetchTimeout as exc:
                record_failure(trade_date, symbol, 'BAOSTOCK_TIMEOUT', run_id, candidate_snapshot_id)
                continue
            except Exception as exc:
                reason = 'NETWORK_ERROR' if type(exc).__name__ in {'ConnectionError', 'TimeoutError'} else 'UNKNOWN'
                record_failure(trade_date, symbol, reason, run_id, candidate_snapshot_id)
                continue
            time.sleep(REQUEST_DELAY)

            if not klines:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA', run_id, candidate_snapshot_id)
                continue

            # Get entry price
            entry_price = price_on_date(klines, str(trade_date))
            if entry_price is None:
                record_failure(trade_date, symbol, 'NO_TRADING_DATA', run_id, candidate_snapshot_id)
                continue

            stats['fetched'] += 1

            t1_date = next_trading_day(trade_date, 1)
            t1_row = next(
                (row for row in klines if row.get('date') == str(t1_date)),
                None,
            )
            execution_contract = build_execution_contract(
                {
                    'date': str(trade_date),
                    'asof_time': '15:00:00',
                    'symbol': symbol,
                },
                entry_price_override=entry_price,
                entry_price_source='BACKFILL_T_DAY_CLOSE',
                entry_price_basis=CANONICAL_PRICE_BASIS,
            )
            previous_rows = [
                row for row in klines
                if row.get('date') and row.get('date') < str(t1_date)
            ]
            previous_row = max(previous_rows, key=lambda row: row.get('date')) if previous_rows else None
            t1_metrics = calculate_t1_labels(
                entry_price,
                t1_row,
                previous_row=previous_row,
                include_extended=False,
            )
            quality = target_quality_gate(execution_contract, t1_row, t1_metrics)
            if quality.get('status') != 'PASS':
                record_failure(trade_date, symbol, 'INVALID_T1_LABELS', run_id, candidate_snapshot_id)
                continue
            gross_return = t1_metrics.get('t1_close_return')
            execution_evidence = shadow_execution_profile(
                {
                    'date': str(trade_date),
                    'symbol': symbol,
                    'execution_contract': execution_contract,
                    'features_used': {
                        'candidate_features': {
                            'entry_price': entry_price,
                            'execution_input_quality': 'BACKFILL_RECONSTRUCTED',
                        },
                    },
                },
                t1_row,
                previous_row,
                gross_return=gross_return,
            )
            t1_metrics = calculate_t1_labels(
                entry_price,
                t1_row,
                previous_row=previous_row,
                execution_profile=execution_evidence,
                include_extended=True,
            )
            returns = {'t1': t1_metrics.get('t1_net_return')}
            if returns['t1'] is not None:
                stats['t1_filled'] += 1
            public_t1_metrics = {
                key: t1_metrics.get(key)
                for key in (
                    't1_open_return', 't1_high_return', 't1_low_return',
                    't1_close_return', 't1_mfe', 't1_mae', 't1_vwap_return',
                    't1_gap_return', 't1_net_return', 'execution_price',
                    'slippage', 'commission', 'stamp_duty', 'transfer_fee',
                    'market_impact', 'label_status',
                )
            }

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
                            'provider': 'eastmoney',
                            'source': CANONICAL_MARKET_DATA_SOURCE,
                            'entry_trade_date': str(trade_date),
                            'exit_trade_date': str(t1_date),
                            'price_source': CANONICAL_MARKET_DATA_SOURCE,
                            'price_basis': CANONICAL_PRICE_BASIS,
                            'entry_price': entry_price,
                            'entry_price_source': 'BACKFILL_T_DAY_CLOSE',
                            'entry_price_basis': CANONICAL_PRICE_BASIS,
                            'exit_open': t1_row.get('open'),
                            'exit_high': t1_row.get('high'),
                            'exit_low': t1_row.get('low'),
                            'exit_close': t1_row.get('close'),
                            'label_version': CANONICAL_LABEL_VERSION,
                            'label_status': t1_metrics.get('label_status'),
                            'market_data_source': CANONICAL_MARKET_DATA_SOURCE,
                            'trading_calendar_source': 'xiaogu_scheduler',
                            'generated_at': datetime.now().isoformat(),
                            'execution_contract': execution_contract,
                            't1_metrics': t1_metrics,
                            'quality_gate': quality,
                            'execution_model': execution_evidence,
                        },
                        t1_labels=t1_metrics,
                    )
                    existing_map[key] = {'t1': returns.get('t1'), 'labels': t1_metrics}
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
                't1_metrics': public_t1_metrics,
                'execution_model': execution_evidence,
                'production_trade_mode': 'T_DAY_BUY_T1_CLOSE_SELL',
            })
            stats['new_success_count'] += 1
            stats['run_settlement'][run_id]['settled'] += 1

            # Progress indicator
            if (i + 1) % 20 == 0:
                print(f'  Progress: {i + 1}/{len(rows)} picks, {stats["fetched"]} fetched, {stats["t1_filled"]} T+1 filled')

    finally:
        pass

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
    ap.add_argument(
        '--repair-stored-net-labels',
        action='store_true',
        help='Fill missing canonical t1_net_return from persisted execution evidence',
    )
    ap.add_argument('--start-date', default=None, help='Lower bound for stored-label repair')
    ap.add_argument('--end-date', default=None, help='Upper bound for stored-label repair')
    ap.add_argument('--per-symbol-timeout', type=int, default=DEFAULT_PER_SYMBOL_TIMEOUT_SECONDS, help='Baostock request timeout seconds')
    ap.add_argument('--batch-soft-timeout', type=int, default=DEFAULT_BATCH_SOFT_TIMEOUT_SECONDS, help='Soft limit for one resume batch')
    ap.add_argument('--trade-date', default=None, help='Decision date to backfill without rescanning')
    ap.add_argument('--validate-on', default=None, help='Expected next trading date for T+1 validation')
    ap.add_argument('--production-run-id', default='', help='Explicit historical production run to settle')
    ap.add_argument('--top10-only', action='store_true', help='Bounded diagnostic scope; production defaults to the full candidate snapshot')
    args = ap.parse_args()

    print('Starting return backfill...')
    if args.repair_stored_net_labels:
        repair_result = repair_stored_canonical_net_returns(
            dry_run=args.dry_run,
            start_date=args.start_date,
            end_date=args.end_date,
            production_run_id=args.production_run_id or None,
        )
        print(
            'Stored canonical net-label repair: '
            f"{repair_result['status']} "
            f"candidate_rows={repair_result['candidate_rows']} "
            f"repaired={repair_result['repaired']}"
        )
        if args.dry_run:
            output_path = ROOT / 'summary' / 'return_backfill_results.json'
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w') as f:
                json.dump(repair_result, f, indent=2, ensure_ascii=False, default=str)
            raise SystemExit(0)
        output_path = ROOT / 'summary' / 'return_backfill_results.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(repair_result, f, indent=2, ensure_ascii=False, default=str)
        raise SystemExit(0)
    stats = backfill_returns(
        dry_run=args.dry_run,
        top10_only=args.top10_only,
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
