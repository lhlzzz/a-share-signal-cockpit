#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xiaogu data repair and backfill script."""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://xiaogu:xiaogu@localhost:5432/xiaogu')
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def get_missing_returns_dates() -> List[date]:
    """Get dates with picks but missing returns."""
    with engine.connect() as conn:
        result = conn.execute(text('''
            SELECT DISTINCT p.trade_date
            FROM picks p
            LEFT JOIN returns r
              ON p.trade_date = r.trade_date
             AND p.symbol = r.symbol
             AND r.production_run_id IS NULL
            WHERE p.decision = 'PAPER_PICK'
              AND p.production_run_id IS NULL
              AND r.trade_date IS NULL
            ORDER BY p.trade_date DESC
        '''))
        return [row[0] for row in result.fetchall()]


def get_missing_signals_dates() -> List[date]:
    """Get dates with picks but missing signals."""
    with engine.connect() as conn:
        result = conn.execute(text('''
            SELECT DISTINCT p.trade_date
            FROM picks p
            LEFT JOIN signals s ON p.trade_date = s.trade_date
            WHERE p.decision = 'PAPER_PICK' AND s.trade_date IS NULL
            ORDER BY p.trade_date DESC
        '''))
        return [row[0] for row in result.fetchall()]


def fetch_kline_from_db(symbol: str, start_date: date, end_date: date) -> List[Dict[str, Any]]:
    """Fetch kline data from daily_candidates table."""
    with engine.connect() as conn:
        result = conn.execute(text('''
            SELECT trade_date, open_price, close_price, high_price, low_price, volume, amount
            FROM daily_candidates
            WHERE symbol = :symbol
              AND trade_date >= :start_date
              AND trade_date <= :end_date
            ORDER BY trade_date
        '''), {
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date,
        })
        rows = result.fetchall()

    return [{
        'date': row[0].isoformat(),
        'open': fnum(row[1]),
        'close': fnum(row[2]),
        'high': fnum(row[3]),
        'low': fnum(row[4]),
        'volume': fnum(row[5]),
        'amount': fnum(row[6]),
    } for row in rows if row[2] and row[2] > 0]


def compute_returns(entry_price: float, klines: List[Dict[str, Any]], limit_threshold: float = 0.095) -> Dict[str, Any]:
    """Compute the only production result: T+1 close return."""
    if not klines or entry_price <= 0:
        return {}

    if not klines:
        return {}
    t1_close = klines[0]['close']
    t1_return = (t1_close - entry_price) / entry_price
    return {
        't1_return': t1_return,
        'is_limit_up': t1_return >= limit_threshold,
    }


def backfill_returns(trade_date: date, dry_run: bool = True) -> Dict[str, Any]:
    """Backfill returns for a specific date."""
    print(f'\n=== Backfilling returns for {trade_date} ===')

    with engine.connect() as conn:
        # Get picks for this date
        result = conn.execute(text('''
            SELECT p.id, p.trade_date, p.symbol, p.features
            FROM picks p
            LEFT JOIN returns r
              ON p.trade_date = r.trade_date
             AND p.symbol = r.symbol
             AND r.production_run_id IS NULL
            WHERE p.trade_date = :trade_date
              AND p.decision = 'PAPER_PICK'
              AND p.production_run_id IS NULL
              AND r.trade_date IS NULL
        '''), {'trade_date': trade_date})
        picks = [dict(row) for row in result.mappings().all()]

    if not picks:
        print(f'  No picks to fill for {trade_date}')
        return {'filled': 0, 'skipped': 0}

    filled = 0
    skipped = 0

    for pick in picks:
        symbol = pick['symbol']
        features = pick.get('features') or {}

        # Get entry price from features (try multiple locations)
        candidate_features = features.get('candidate_features') or {}
        entry_price = fnum(
            features.get('price') or
            features.get('entry_price') or
            candidate_features.get('price') or
            candidate_features.get('entry_price')
        )
        if entry_price <= 0:
            print(f'  Skip {symbol}: no entry price')
            skipped += 1
            continue

        # Fetch the pick day and the next trading day only.
        start_date = trade_date
        end_date = trade_date + timedelta(days=10)  # buffer for weekends
        all_klines = fetch_kline_from_db(symbol, start_date, end_date)

        # Remove first kline (pick day) - we want T+1 onwards
        if all_klines and all_klines[0]['date'] == trade_date.isoformat():
            klines = all_klines[1:]
        else:
            klines = all_klines

        # Check if we have T+1 data
        if not klines:
            print(f'  Skip {symbol}: no T+1 kline data yet')
            skipped += 1
            continue

        if not klines:
            print(f'  Skip {symbol}: no kline data')
            skipped += 1
            continue

        # Compute returns
        returns = compute_returns(entry_price, klines)
        if not returns:
            print(f'  Skip {symbol}: could not compute returns')
            skipped += 1
            continue

        # Insert into returns table
        if dry_run:
            print(f'  [DRY RUN] Would fill {symbol}: t1={returns.get("t1_return", 0):.4f}')
        else:
            from xiaogu_db import upsert_return

            upsert_return(
                trade_date=trade_date,
                symbol=symbol,
                pick_id=pick['id'],
                t1_return=returns.get('t1_return'),
                legacy_backfill=True,
            )
            print(f'  Filled {symbol}: t1={returns.get("t1_return", 0):.4f}')
        filled += 1

    return {'filled': filled, 'skipped': skipped}


def generate_signal_effectiveness(trade_date: date, dry_run: bool = True) -> Dict[str, Any]:
    """Generate one historical factor snapshot through the canonical analyzer."""
    print(f'\n=== Generating signal effectiveness for {trade_date} ===')
    from xiaogu_signal_effectiveness_v0_1 import (
        analyze_signal_effectiveness,
        persist_signal_effectiveness,
    )

    result = analyze_signal_effectiveness(
        ledger_path=BASE / 'forward_paper_ledger_v0_1.jsonl',
        source='db',
        trade_dates={trade_date.isoformat()},
    )
    analyzed = len(result['signal_effectiveness'])
    if not analyzed:
        print(f'  No completed factor-return pairs for {trade_date}')
        return {'analyzed': 0}
    if dry_run:
        for signal in result['signal_effectiveness']:
            print(
                f"  [DRY RUN] {signal['signal_key']}: "
                f"count={signal['present_count']}, "
                f"limit_up_rate={signal['limit_up_rate']:.2f}, "
                f"avg_return={signal['avg_t1_return']:.4f}"
            )
        return {'analyzed': analyzed}

    persistence = persist_signal_effectiveness(result, analysis_date=trade_date.isoformat())
    return {'analyzed': analyzed, **persistence}


def record_research_run(trade_date: date, run_type: str = 'backfill', dry_run: bool = True) -> Dict[str, Any]:
    """Record a research run entry."""
    print(f'\n=== Recording research run for {trade_date} ===')

    with engine.connect() as conn:
        # Count quotes and candidates
        result = conn.execute(text('''
            SELECT
                COUNT(DISTINCT symbol) as quotes_count,
                COUNT(DISTINCT CASE WHEN decision = 'PAPER_PICK' THEN symbol END) as passed_count
            FROM picks
            WHERE trade_date = :trade_date
        '''), {'trade_date': trade_date})
        row = result.fetchone()
        quotes_count = row[0] if row else 0
        passed_count = row[1] if row else 0

    if dry_run:
        print(f'  [DRY RUN] Would record: quotes={quotes_count}, passed={passed_count}')
        return {'recorded': True}
    else:
        with engine.connect() as conn:
            conn.execute(text('''
                INSERT INTO research_runs (trade_date, run_type, run_time, rule_version, quotes_count, passed_count)
                VALUES (:trade_date, :run_type, NOW(), 'v0.1_backfill', :quotes_count, :passed_count)
            '''), {
                'trade_date': trade_date,
                'run_type': run_type,
                'quotes_count': quotes_count,
                'passed_count': passed_count,
            })
            conn.commit()
        print(f'  Recorded: quotes={quotes_count}, passed={passed_count}')
        return {'recorded': True}


def main():
    parser = argparse.ArgumentParser(description='xiaogu data repair tool')
    parser.add_argument('--task', choices=['returns', 'signals', 'effectiveness', 'research', 'all'],
                        default='all', help='Which task to run')
    parser.add_argument('--date', help='Specific date to repair (YYYY-MM-DD)')
    parser.add_argument('--days', type=int, default=30, help='Number of days to look back')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')
    args = parser.parse_args()

    print(f'=== xiaogu Data Repair Tool ===')
    print(f'Task: {args.task}')
    print(f'Dry run: {args.dry_run}')
    print()

    if args.date:
        target_dates = [date.fromisoformat(args.date)]
    else:
        target_dates = None

    # Task: returns
    if args.task in ['returns', 'all']:
        if target_dates:
            missing_dates = [d for d in target_dates if d in get_missing_returns_dates()]
        else:
            missing_dates = get_missing_returns_dates()[:args.days]

        print(f'Found {len(missing_dates)} dates with missing returns')
        total_filled = 0
        total_skipped = 0
        for d in missing_dates:
            result = backfill_returns(d, dry_run=args.dry_run)
            total_filled += result['filled']
            total_skipped += result['skipped']
        print(f'\nReturns summary: filled={total_filled}, skipped={total_skipped}')

    # Task: signals (signals are generated during scanning, not easily backfilled)
    if args.task in ['signals', 'all']:
        if target_dates:
            missing_dates = [d for d in target_dates if d in get_missing_signals_dates()]
        else:
            missing_dates = get_missing_signals_dates()[:args.days]

        print(f'\nFound {len(missing_dates)} dates with missing signals')
        print('Note: Signals are generated during live scanning and cannot be easily backfilled.')
        print('Consider running the scanner for these dates.')

    # Task: effectiveness
    if args.task in ['effectiveness', 'all']:
        with engine.connect() as conn:
            result = conn.execute(text('''
                SELECT DISTINCT trade_date FROM signals
                WHERE trade_date NOT IN (SELECT DISTINCT analysis_date FROM signal_effectiveness)
                ORDER BY trade_date DESC
                LIMIT :days
            '''), {'days': args.days})
            dates_to_analyze = [row[0] for row in result.fetchall()]

        print(f'\nFound {len(dates_to_analyze)} dates to analyze for signal effectiveness')
        analyzed = 0
        for d in dates_to_analyze:
            result = generate_signal_effectiveness(d, dry_run=args.dry_run)
            analyzed += result['analyzed']
        print(f'\nEffectiveness summary: analyzed={analyzed} signals')

    # Task: research
    if args.task in ['research', 'all']:
        with engine.connect() as conn:
            result = conn.execute(text('''
                SELECT DISTINCT trade_date FROM picks
                WHERE trade_date NOT IN (SELECT DISTINCT trade_date FROM research_runs)
                ORDER BY trade_date DESC
                LIMIT :days
            '''), {'days': args.days})
            dates_to_record = [row[0] for row in result.fetchall()]

        print(f'\nFound {len(dates_to_record)} dates to record as research runs')
        for d in dates_to_record:
            record_research_run(d, dry_run=args.dry_run)
        print(f'\nResearch summary: recorded={len(dates_to_record)} runs')

    print('\n=== Repair complete ===')


if __name__ == '__main__':
    main()
