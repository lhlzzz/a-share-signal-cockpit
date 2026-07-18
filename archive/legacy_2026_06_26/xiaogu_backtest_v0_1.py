#!/usr/bin/env python3
"""
Backtest engine — replay runner over historical scan data.

Usage:
    python3 xiaogu_backtest_v0_1.py --start 2026-06-06 --end 2026-06-25
    python3 xiaogu_backtest_v0_1.py --date 2026-06-25
    python3 xiaogu_backtest_v0_1.py --all --report
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent
LIVE_SCAN_ROOT = BASE / 'data' / 'live_scan'
BUNDLE_ROOT = BASE / 'data' / 'forward_candidate_bundles'
BACKTEST_OUTPUT_ROOT = BASE / 'data' / 'backtest'
LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
PYTHON = sys.executable


def find_best_scan_for_date(trade_date: str) -> Optional[Path]:
    """Find the latest scan directory for a given trade date."""
    scan_dir = LIVE_SCAN_ROOT / trade_date
    if not scan_dir.exists():
        return None
    # Prefer realtime scans around 14:30 window, fall back to latest
    candidates = sorted([
        d for d in scan_dir.iterdir()
        if d.is_dir() and 'realtime' in d.name
    ])
    if not candidates:
        return None
    # Prefer scan closest to 14:30 (1430), fall back to latest
    def scan_time_key(d):
        name = d.name
        for part in name.split('_'):
            if part.isdigit() and len(part) == 6:
                return int(part)
        return 0
    # Find scan <= 1500 if possible, else latest
    afternoon = [d for d in candidates if 1400 <= scan_time_key(d) <= 1500]
    return afternoon[-1] if afternoon else candidates[-1]


def find_bundle_for_date(trade_date: str) -> Optional[Path]:
    """Find candidate bundle for a given trade date."""
    bundle_dir = BUNDLE_ROOT / trade_date
    if not bundle_dir.exists():
        return None
    bundles = list(bundle_dir.glob('*.json'))
    return bundles[0] if bundles else None


def run_backtest_for_date(trade_date: str) -> Dict[str, Any]:
    """Run runner dry-run for a single date, return result dict."""
    result = {
        'trade_date': trade_date,
        'decision': None,
        'symbol': None,
        'final_score': None,
        'error': None,
        'scan_dir': None,
    }

    scan_dir = find_best_scan_for_date(trade_date)
    if scan_dir:
        result['scan_dir'] = str(scan_dir)
        # Extract asof_time from dir name
        asof_time = '14:30:00'
        for part in scan_dir.name.split('_'):
            if part.isdigit() and len(part) == 6:
                h, m, s = part[:2], part[2:4], part[4:6]
                asof_time = f'{h}:{m}:{s}'
                break

    try:
        cmd = [
            PYTHON, 'xiaogu_forward_d1_1450_runner_v0_1.py',
            '--date', trade_date,
            '--dry-run',
        ]
        if scan_dir:
            cmd += ['--asof-time', asof_time]

        proc = subprocess.run(
            cmd, cwd=str(BASE), capture_output=True, text=True, timeout=120
        )
        output = proc.stdout
        # Find last JSON object
        depth = 0
        start = -1
        for i, ch in enumerate(output):
            if ch == '{':
                if depth == 0: start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(output[start:i+1])
                        if obj.get('decision') in ('PAPER_PICK', 'NO_PICK', 'RESEARCH_CANDIDATE'):
                            result['decision'] = obj.get('decision')
                            result['symbol'] = obj.get('symbol') or ''
                            sc = obj.get('single_target_card') or {}
                            result['final_score'] = sc.get('final_score') or sc.get('score')
                            break
                    except Exception:
                        pass
    except Exception as exc:
        result['error'] = str(exc)

    return result


def load_ledger_returns() -> Dict[str, float]:
    """Load t1_returns from ledger fills."""
    returns = {}
    if not LEDGER.exists():
        return returns
    with LEDGER.open(encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get('record_type') == 'RESULT_FILL':
                    key = f"{r.get('date')}:{r.get('symbol')}"
                    t1 = r.get('t1_return')
                    if t1 is not None:
                        returns[key] = float(t1)
            except Exception:
                pass
    return returns


def build_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build backtest performance report."""
    ledger_returns = load_ledger_returns()
    paper_picks = [r for r in results if r.get('decision') == 'PAPER_PICK']
    no_picks = [r for r in results if r.get('decision') == 'NO_PICK']

    enriched = []
    for r in paper_picks:
        key = f"{r['trade_date']}:{r.get('symbol','')}"
        t1 = ledger_returns.get(key)
        enriched.append({**r, 't1_return': t1,
                         'is_limit_up': (t1 >= 0.095) if t1 is not None else None})

    filled = [e for e in enriched if e['t1_return'] is not None]
    limit_ups = [e for e in filled if e.get('is_limit_up')]
    avg_t1 = sum(e['t1_return'] for e in filled) / len(filled) if filled else None
    limit_up_rate = len(limit_ups) / len(filled) if filled else None

    return {
        'backtest_dates': len(results),
        'paper_picks': len(paper_picks),
        'no_picks': len(no_picks),
        'filled_returns': len(filled),
        'limit_up_count': len(limit_ups),
        'limit_up_rate': round(limit_up_rate, 3) if limit_up_rate is not None else None,
        'avg_t1_return': round(avg_t1, 4) if avg_t1 is not None else None,
        'picks_detail': enriched,
        'errors': [r for r in results if r.get('error')],
    }


def get_trading_dates(start: str, end: str) -> List[str]:
    """Get weekday dates between start and end inclusive."""
    dates = []
    cur = datetime.strptime(start, '%Y-%m-%d').date()
    end_d = datetime.strptime(end, '%Y-%m-%d').date()
    while cur <= end_d:
        if cur.weekday() < 5:  # Mon-Fri
            # Only include dates with scan data
            if (LIVE_SCAN_ROOT / cur.isoformat()).exists():
                dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def main():
    ap = argparse.ArgumentParser(description='xiaogu backtest engine')
    ap.add_argument('--date', help='Single date YYYY-MM-DD')
    ap.add_argument('--start', help='Start date YYYY-MM-DD')
    ap.add_argument('--end', help='End date YYYY-MM-DD')
    ap.add_argument('--all', action='store_true', help='All available scan dates')
    ap.add_argument('--report', action='store_true', help='Print performance report')
    args = ap.parse_args()

    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dates = get_trading_dates(args.start, args.end)
    elif args.all:
        dates = sorted([d.name for d in LIVE_SCAN_ROOT.iterdir()
                       if d.is_dir() and not d.name.startswith('.')])
    else:
        ap.print_help()
        return

    print(f"Running backtest over {len(dates)} dates...")
    results = []
    for d in dates:
        r = run_backtest_for_date(d)
        status = f"{'✅' if r['decision']=='PAPER_PICK' else '❌'} {r['trade_date']} {r.get('decision')} {r.get('symbol','')} score={r.get('final_score')}"
        print(status)
        results.append(r)

    if args.report or len(dates) > 1:
        report = build_report(results)
        print('\n=== BACKTEST REPORT ===')
        print(f"Dates:          {report['backtest_dates']}")
        print(f"PAPER_PICK:     {report['paper_picks']}")
        print(f"NO_PICK:        {report['no_picks']}")
        print(f"Filled returns: {report['filled_returns']}")
        print(f"Limit-up rate:  {report['limit_up_rate']:.1%}" if report['limit_up_rate'] else "Limit-up rate:  N/A")
        print(f"Avg T+1 return: {report['avg_t1_return']:+.2%}" if report['avg_t1_return'] else "Avg T+1 return: N/A")
        if report['errors']:
            print(f"Errors:         {len(report['errors'])}")

        # Save to file
        BACKTEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as dt
        out_path = BACKTEST_OUTPUT_ROOT / f"backtest_{dt.now().strftime('%Y%m%d_%H%M%S')}.json"
        with out_path.open('w') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nReport saved: {out_path}")


if __name__ == '__main__':
    main()
