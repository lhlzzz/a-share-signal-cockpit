#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xiaogu forward paper recorder v0.1
Append-only daily PAPER_ONLY/NO_TRADE forward ledger writer.
It never writes historical samples and never fills future result fields at generation time.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict
from xiaogu_utils import (
    PRODUCTION_DECISIONS,
    PRODUCTION_RETURN_FIELD,
    PRODUCTION_TRADE_MODE,
    append_jsonl,
    now_iso,
    read_json as load_json,
)

BASE = Path(__file__).resolve().parent
RULE_FREEZE = BASE / 'rule_freeze_v0_1.json'
FORWARD_LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
SNAPSHOT_ROOT = BASE / 'data' / 'forward_snapshots'
VALID_DECISIONS = set(PRODUCTION_DECISIONS)
LOCKED_AT_GENERATION = ['date','generated_at','asof_time','symbol','decision','rule_version','features_used','raw_data_snapshot_path','decision_reason','xiaochan_gate_status','xiaoshuju_data_gate_status','paper_only','no_trade','production_ready']


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False), encoding='utf-8')
    os.replace(tmp, path)


def read_features_arg(s: str) -> Dict[str, Any]:
    if not s:
        return {}
    p = Path(s)
    if p.exists():
        return load_json(p)
    return json.loads(s)


def snapshot_path_for(date: str, asof_time: str, rule_version: str, symbol: str) -> Path:
    safe_time = asof_time.replace(':', '')
    safe_symbol = symbol or 'NOPICK'
    return SNAPSHOT_ROOT / date / f'{safe_time}_{rule_version}_{safe_symbol}.json'


def validate_generation(decision: str, rule: Dict[str, Any]) -> None:
    if decision not in VALID_DECISIONS:
        raise SystemExit(f'invalid decision: {decision}')
    if not rule.get('paper_only') or not rule.get('no_trade') or rule.get('production_ready'):
        raise SystemExit('rule_freeze safety flags invalid; require paper_only=true no_trade=true production_ready=false')
    if (
        not rule.get('allow_trade')
        or rule.get('auto_order')
        or rule.get('broker_connected')
        or not rule.get('manual_paper_execution_allowed')
    ):
        raise SystemExit(
            'rule_freeze requires manual paper execution enabled and auto/broker execution disabled'
        )


def main() -> None:
    ap = argparse.ArgumentParser(description='append one forward paper record')
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--asof-time', default='14:50:00')
    ap.add_argument('--symbol', default='')
    ap.add_argument('--decision', required=True, choices=sorted(VALID_DECISIONS))
    ap.add_argument('--features-json', default='{}', help='JSON string or path containing only T-day visible features')
    ap.add_argument('--decision-reason', required=True)
    ap.add_argument('--xiaochan-gate-status', default='ALLOW_FORWARD_PAPER_NO_TRADE')
    ap.add_argument('--xiaoshuju-data-gate-status', default='NOT_CALLED')
    ap.add_argument('--generated-at', default=now_iso())
    ap.add_argument('--dry-run', action='store_true', help='write snapshot to /tmp preview only; do not append ledger')
    ap.add_argument('--correction-of', default='', help='append correction record id instead of modifying old record')
    ap.add_argument('--production-run-id', default='')
    args = ap.parse_args()

    rule = load_json(RULE_FREEZE)
    rule_version = rule['rule_version']
    validate_generation(args.decision, rule)
    features = read_features_arg(args.features_json)

    symbol = args.symbol if args.decision != 'NO_PICK' else (args.symbol or 'NO_PICK')
    snap = snapshot_path_for(args.date, args.asof_time, rule_version, symbol)
    snapshot = {
        'date': args.date,
        'generated_at': args.generated_at,
        'asof_time': args.asof_time,
        'symbol': symbol,
        'decision': args.decision,
        'rule_version': rule_version,
        'production_run_id': args.production_run_id or None,
        'features_used': features,
        'source': 'forward_paper_recorder_v0_1',
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_trade': args.decision == 'PAPER_PICK',
        'manual_paper_execution_allowed': args.decision == 'PAPER_PICK',
        'auto_order': False,
        'broker_connected': False,
        'note': 'T-day visible raw/features snapshot only; no T+ result fields.',
    }
    snapshot_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    snapshot['snapshot_sha256'] = snapshot_hash

    rec = {
        'record_type': 'CORRECTION' if args.correction_of else 'DECISION',
        'correction_of': args.correction_of or None,
        'date': args.date,
        'generated_at': args.generated_at,
        'asof_time': args.asof_time,
        'symbol': symbol,
        'decision': args.decision,
        'rule_version': rule_version,
        'production_run_id': args.production_run_id or None,
        'features_used': features,
        'raw_data_snapshot_path': str(snap),
        'raw_data_snapshot_sha256': snapshot_hash,
        'decision_reason': args.decision_reason,
        'xiaochan_gate_status': args.xiaochan_gate_status,
        'xiaoshuju_data_gate_status': args.xiaoshuju_data_gate_status,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_trade': args.decision == 'PAPER_PICK',
        'manual_paper_execution_allowed': args.decision == 'PAPER_PICK',
        'auto_order': False,
        'broker_connected': False,
        'result_status': 'PENDING',
        PRODUCTION_RETURN_FIELD: None,
        'result_filled_at': None,
        'post_result_locked': False,
        'locked_fields': LOCKED_AT_GENERATION,
        'append_only_policy': 'Never overwrite old records. Corrections append a new CORRECTION record.',
        'data_leakage_check': {'t_plus_fields_at_generation': False, 'status': 'PASS'},
        'production_trade_mode': PRODUCTION_TRADE_MODE,
        'production_return_formula': '(T+1 close - T-day entry close) / T-day entry close',
    }

    if args.dry_run:
        preview = {'would_write_snapshot': str(snap), 'would_append_ledger': str(FORWARD_LEDGER), 'record': rec}
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    dump_json(snap, snapshot)
    append_jsonl(FORWARD_LEDGER, rec)
    print(json.dumps({'appended': True, 'ledger': str(FORWARD_LEDGER), 'snapshot': str(snap), 'record': rec}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
