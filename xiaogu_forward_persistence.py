#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production recorder invocation and run-finalization ownership."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from xiaogu_forward_host_binding import create_host_binding


_HOST = None

REQUIRED_FROM_HOST = (
    'BASE',
    'RAW_ROOT',
    'RECORDER',
    'write_json',
)

bind_host, _inject_host, _with_host = create_host_binding(globals(), REQUIRED_FROM_HOST)


@_with_host
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
    from xiaogu_runtime_payload import (
        enforce_runtime_memory_gate,
        maybe_force_gc,
        payload_bytes,
        slim_features_for_recorder,
    )

    features_path = RAW_ROOT / date / asof_time.replace(':', '') / 'recorder_features.json'
    full_embed = os.environ.get('XIAOGU_RECORDER_FULL_EMBED', '').strip().lower() in ('1', 'true', 'yes')
    recorder_features = features if full_embed else slim_features_for_recorder(features)
    mem_gate = enforce_runtime_memory_gate(stage='run_recorder')
    if mem_gate.get('status') in ('WARN', 'HARD') and not full_embed:
        recorder_features = slim_features_for_recorder(recorder_features)
        maybe_force_gc()
    write_json(features_path, recorder_features)
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
    production_run_id = str(features.get('production_run_id') or '').strip()
    if production_run_id:
        cmd.extend(['--production-run-id', production_run_id])
    if correction_of:
        cmd.extend(['--correction-of', correction_of])
    if dry_run:
        cmd.append('--dry-run')
    try:
        cp = subprocess.run(cmd, cwd=str(BASE), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return {
            'cmd': cmd,
            'returncode': cp.returncode,
            'stdout': cp.stdout,
            'stderr': cp.stderr,
            'features_path': str(features_path),
            'recorder_payload_bytes': payload_bytes(recorder_features),
            'memory_gate': mem_gate,
            'payload_policy': recorder_features.get('payload_policy') if isinstance(recorder_features, dict) else '',
        }
    except subprocess.TimeoutExpired as exc:
        return {
            'cmd': cmd,
            'returncode': 124,
            'stdout': exc.stdout or '',
            'stderr': 'RECORDER_TIMEOUT',
            'features_path': str(features_path),
        }


def finalize_production_run(
    trade_day: dt.date,
    production_run_id: str,
    *,
    candidate_snapshot_id: str,
    active_pick_id: Optional[int],
    publish_active: bool,
) -> None:
    """Commit the terminal run state and active pointer as one DB transaction."""
    from xiaogu_db import get_db, set_active_production_run, update_production_run_status

    with get_db() as db:
        update_production_run_status(production_run_id, 'PASS', db=db)
        if publish_active:
            set_active_production_run(
                trade_day,
                production_run_id,
                candidate_snapshot_id=candidate_snapshot_id,
                active_pick_id=active_pick_id,
                db=db,
            )


def _unique_persistence_candidates(
    candidates: List[Dict[str, Any]],
    target_count: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
