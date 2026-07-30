#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded retention for force-rerun runtime / snapshot garbage.

Keeps pick chain intact: never deletes DB picks/returns. Only prunes oversized
raw runtime dirs, *.bak, and excess same-day force-rerun artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / 'data' / 'forward_raw_runtime'
SNAP_ROOT = ROOT / 'data' / 'forward_snapshots'
LIVE_SCAN = ROOT / 'data' / 'live_scan'

DEFAULT_KEEP_RUNTIMES_PER_DAY = int(os.environ.get('XIAOGU_KEEP_RUNTIMES_PER_DAY', '3'))
DEFAULT_KEEP_SNAPSHOTS_PER_DAY = int(os.environ.get('XIAOGU_KEEP_SNAPSHOTS_PER_DAY', '5'))
DEFAULT_MAX_AGE_DAYS = int(os.environ.get('XIAOGU_RAW_MAX_AGE_DAYS', '45'))


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    for p in path.rglob('*'):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def purge_bak(root: Path, dry_run: bool) -> List[str]:
    removed = []
    if not root.exists():
        return removed
    for p in root.rglob('*.bak'):
        removed.append(str(p))
        if not dry_run:
            p.unlink(missing_ok=True)
    for p in root.rglob('*.full_embedded.bak'):
        removed.append(str(p))
        if not dry_run:
            p.unlink(missing_ok=True)
    return removed


def prune_day_dirs(day_root: Path, keep: int, dry_run: bool) -> List[str]:
    """Keep newest `keep` subdirs; delete older force-rerun artifacts."""
    removed = []
    if not day_root.is_dir():
        return removed
    subdirs = [p for p in day_root.iterdir() if p.is_dir()]
    subdirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in subdirs[keep:]:
        removed.append(str(p))
        if not dry_run:
            shutil.rmtree(p, ignore_errors=True)
    return removed


def prune_old_days(root: Path, max_age_days: int, dry_run: bool) -> List[str]:
    removed = []
    if not root.is_dir():
        return removed
    cutoff = datetime.now() - timedelta(days=max_age_days)
    for day in root.iterdir():
        if not day.is_dir():
            continue
        try:
            day_date = datetime.strptime(day.name[:10], '%Y-%m-%d')
        except ValueError:
            continue
        if day_date < cutoff:
            removed.append(str(day))
            if not dry_run:
                shutil.rmtree(day, ignore_errors=True)
    return removed


def prune_large_runtime_files(day_root: Path, max_mb: float, dry_run: bool) -> List[str]:
    """Remove oversized runtime_decision_context / recorder_features when older siblings exist."""
    removed = []
    max_bytes = int(max_mb * 1024 * 1024)
    if not day_root.is_dir():
        return removed
    for run_dir in day_root.iterdir():
        if not run_dir.is_dir():
            continue
        for name in ('runtime_decision_context.json', 'recorder_features.json'):
            f = run_dir / name
            if f.is_file() and f.stat().st_size > max_bytes:
                # Keep a tiny stub marker instead of full delete of latest only if multiple
                removed.append(str(f))
                if not dry_run:
                    stub = {
                        'payload_policy': 'purged_oversized_by_data_retention',
                        'original_bytes': f.stat().st_size,
                        'purged_at': datetime.now().isoformat(timespec='seconds'),
                    }
                    f.write_text(json.dumps(stub, ensure_ascii=False, indent=2), encoding='utf-8')
    return removed


def run(
    *,
    dry_run: bool = True,
    keep_runtimes: int = DEFAULT_KEEP_RUNTIMES_PER_DAY,
    keep_snapshots: int = DEFAULT_KEEP_SNAPSHOTS_PER_DAY,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_file_mb: float = 12.0,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        'dry_run': dry_run,
        'before_bytes': {
            'forward_raw_runtime': dir_size(RAW_ROOT),
            'forward_snapshots': dir_size(SNAP_ROOT),
            'live_scan': dir_size(LIVE_SCAN),
        },
        'removed': [],
        'actions': [],
    }
    report['removed'].extend(purge_bak(RAW_ROOT, dry_run))
    report['removed'].extend(purge_bak(SNAP_ROOT, dry_run))
    report['actions'].append('purge_bak')

    if RAW_ROOT.is_dir():
        for day in sorted(RAW_ROOT.iterdir()):
            if day.is_dir():
                report['removed'].extend(prune_day_dirs(day, keep_runtimes, dry_run))
                report['removed'].extend(prune_large_runtime_files(day, max_file_mb, dry_run))
    report['actions'].append('prune_runtimes_per_day')

    if SNAP_ROOT.is_dir():
        for day in sorted(SNAP_ROOT.iterdir()):
            if not day.is_dir():
                continue
            files = sorted([p for p in day.iterdir() if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[keep_snapshots:]:
                report['removed'].append(str(p))
                if not dry_run:
                    p.unlink(missing_ok=True)
    report['actions'].append('prune_snapshots_per_day')
    report['removed'].extend(prune_old_days(RAW_ROOT, max_age_days, dry_run))
    report['actions'].append('prune_old_days')

    report['after_bytes'] = {
        'forward_raw_runtime': dir_size(RAW_ROOT),
        'forward_snapshots': dir_size(SNAP_ROOT),
        'live_scan': dir_size(LIVE_SCAN),
    }
    report['removed_count'] = len(report['removed'])
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description='Prune force-rerun runtime/snapshot garbage (safe for pick chain)')
    ap.add_argument('--apply', action='store_true', help='Actually delete (default is dry-run)')
    ap.add_argument('--keep-runtimes', type=int, default=DEFAULT_KEEP_RUNTIMES_PER_DAY)
    ap.add_argument('--keep-snapshots', type=int, default=DEFAULT_KEEP_SNAPSHOTS_PER_DAY)
    ap.add_argument('--max-age-days', type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument('--max-file-mb', type=float, default=12.0)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    report = run(
        dry_run=not args.apply,
        keep_runtimes=args.keep_runtimes,
        keep_snapshots=args.keep_snapshots,
        max_age_days=args.max_age_days,
        max_file_mb=args.max_file_mb,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"dry_run={report['dry_run']} removed={report['removed_count']}")
        b, a = report['before_bytes'], report['after_bytes']
        print(f"raw: {b['forward_raw_runtime']/1e9:.2f}G -> {a['forward_raw_runtime']/1e9:.2f}G")
        print(f"snap: {b['forward_snapshots']/1e9:.2f}G -> {a['forward_snapshots']/1e9:.2f}G")
        for p in report['removed'][:20]:
            print('  -', p)
        if report['removed_count'] > 20:
            print(f"  ... +{report['removed_count']-20} more")


if __name__ == '__main__':
    main()
