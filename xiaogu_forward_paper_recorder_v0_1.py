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
from typing import Any, Dict, Tuple
from xiaogu_utils import (
    PRODUCTION_TRADE_MODE,
    append_jsonl,
    now_iso,
    read_json as load_json,
)
from xiaogu_forward_result_filler_v0_1 import EVALUATION_DAYS

BASE = Path(__file__).resolve().parent
RULE_FREEZE = BASE / 'rule_freeze_v0_1.json'
FORWARD_LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
SNAPSHOT_ROOT = BASE / 'data' / 'forward_snapshots'
RECORDABLE_DECISIONS = {"BUY", "HOLD", "REDUCE", "SELL"}
VALID_DECISIONS = RECORDABLE_DECISIONS
LOCKED_AT_GENERATION = ['date','generated_at','asof_time','symbol','decision','rule_version','features_used','raw_data_snapshot_path','decision_reason','paper_only','no_trade','production_ready']
PENDING_OUTCOME_FIELDS = tuple(
    field
    for horizon in EVALUATION_DAYS
    for field in (
        f'future_{horizon}d_date',
        f'future_{horizon}d_open',
        f'future_{horizon}d_high',
        f'future_{horizon}d_low',
        f'future_{horizon}d_close',
        f'future_{horizon}d_return',
        f'future_{horizon}d_mfe',
        f'future_{horizon}d_mae',
        f'future_{horizon}d_net_return',
    )
) + (
    'profit_window_target', 'execution_cost_rate', 'daily_outcomes',
    'max_daily_bar_profit_opportunity_5d', 'first_profit_day', 'time_to_profit',
    'max_mae_5d', 'net_profit_window', 'profit_window', 'data_status',
    'realizability_level', 'outcome_complete', 'available_days', 'partial_status',
)


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False), encoding='utf-8')
    os.replace(tmp, path)


def memory_root() -> Path:
    configured = os.environ.get("XIAOGU_MEMORY_ROOT")
    if configured:
        return Path(configured)
    return Path("/mnt/d/obisidian/Obsidian/Project/A股") / "xiaogu_memory"


def _memory_symbol(value: Any) -> str:
    return "".join(char for char in str(value or "UNKNOWN").zfill(6) if char.isalnum() or char in "._-")


def _memory_note_path(state: str, record: Dict[str, Any]) -> Path:
    section = "EXIT" if state in {"SELL", "REDUCE"} else state
    return memory_root() / "decisions" / section / f"{record.get('date')}_{_memory_symbol(record.get('symbol'))}.md"


def _markdown(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value if value not in (None, "") else "UNKNOWN")


def write_trade_memory(record: Dict[str, Any]) -> str:
    """Write the single human-readable trade note for this decision."""
    state = str(record.get("decision") or "WATCH").upper()
    if state not in RECORDABLE_DECISIONS:
        return ""
    features = record.get("features_used") or {}
    alpha = features.get("core_alpha") or {}
    research = features.get("research_context") or {}
    thesis = record.get("thesis") or alpha.get("thesis") or {}
    note = f"""---
decision_id: {record.get('id') or record.get('decision_id') or 'UNKNOWN'}
symbol: {_memory_symbol(record.get('symbol'))}
decision: {state}
date: {record.get('date')}
signal_time: {record.get('signal_time') or record.get('asof_time')}
entry_price: {record.get('entry_price')}
alpha_version: {record.get('alpha_version') or alpha.get('alpha_version')}
feature_version: {record.get('feature_version') or alpha.get('feature_version')}
---

# XIAOGU {state}

## Decision
- Decision ID: `{record.get('id') or record.get('decision_id') or 'UNKNOWN'}`
- Entry price: `{record.get('entry_price') or 'UNKNOWN'}`
- Reason: {_markdown(record.get('decision_reason'))}
- Previous state: {_markdown(record.get('previous_state'))}

## Capital Views
- Institution: {_markdown((research.get('capital') or {}).get('institution_behavior'))}
- Main force: {_markdown((research.get('capital') or {}).get('main_force_behavior'))}
- Hot money: {_markdown((research.get('capital') or {}).get('hot_money_behavior'))}
- Capital convergence: {_markdown(alpha.get('capital_convergence'))}

## Repricing Thesis
- Future demand: {_markdown(alpha.get('future_demand'))}
- Business quality: {_markdown(alpha.get('business_quality'))}
- Supply absorption: {_markdown(alpha.get('supply_absorption'))}
- Pricing gap: {_markdown(alpha.get('pricing_gap'))}
- Future buyer capacity: {_markdown(alpha.get('future_buyer_capacity'))}
- Repricing state: {_markdown(alpha.get('repricing_state'))}
- Repricing evidence score: {_markdown(alpha.get('repricing_evidence_score'))}
- Thesis: {_markdown(thesis)}

## 5D Risk Contract
- Profit window probability: {_markdown(alpha.get('profit_window_probability'))}
- Expected max profit 5D: {_markdown(alpha.get('expected_max_profit_5d'))}
- Expected MAE 5D: {_markdown(alpha.get('expected_mae_5d'))}
- Maximum holding days: 5
- Invalidation: {_markdown(thesis.get('invalidation'))}

## Outcome
Pending T+1..T+5 outcome update.
"""
    path = _memory_note_path(state, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(note, encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def update_trade_memory(result: Dict[str, Any]) -> str | None:
    """Append outcome/review facts to the existing decision note."""
    symbol = _memory_symbol(result.get("symbol"))
    date = result.get("date")
    candidates = list((memory_root() / "decisions").glob(f"*/{date}_{symbol}.md"))
    if not candidates:
        return None
    path = candidates[-1]
    outcome = result.get("daily_outcomes") or []
    review = "SUCCESS" if result.get("profit_window") else "FAILURE" if result.get("outcome_complete") else "PENDING"
    block = f"""
## T+1..T+5 Outcome
- Status: `{result.get('data_status')}`
- Available days: `{result.get('available_days', 0)}`
- Profit window: `{result.get('profit_window')}`
- First profit day: `{result.get('first_profit_day') or 'NONE'}`
- Max daily-bar profit opportunity 5D: `{result.get('max_daily_bar_profit_opportunity_5d') or 'PENDING'}`
- Max MAE 5D: `{result.get('max_mae_5d') or 'PENDING'}`
- Review: `{review}`
- Capital/repricing states: `{_markdown([(item.get('day'), item.get('capital_state'), item.get('repricing_state')) for item in outcome])}`
- Daily outcomes: `{_markdown(outcome)}`
"""
    existing = path.read_text(encoding="utf-8")
    marker = "\n## T+1..T+5 Outcome"
    content = existing.split(marker, 1)[0] + block if marker in existing else existing + block
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    if result.get("outcome_complete"):
        review_path = memory_root() / "post_trade_review" / f"{date}_{symbol}.md"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review = result.get("post_trade_review") or {}
        review_path.write_text(
            "# POST TRADE REVIEW\n\n"
            f"- Decision ID: `{result.get('decision_id')}`\n"
            f"- Status: `{review.get('status', 'UNKNOWN')}`\n"
            f"- Attribution: `{review.get('attribution', 'UNKNOWN')}`\n"
            f"- Profit window day: `{review.get('profit_window_day') or 'NONE'}`\n"
            f"- Maximum favorable excursion: `{review.get('maximum_favorable_excursion') or 'UNKNOWN'}`\n"
            f"- Maximum adverse excursion: `{review.get('maximum_adverse_excursion') or 'UNKNOWN'}`\n"
            f"- Exit reason: `{review.get('exit_reason', 'UNKNOWN')}`\n\n"
            "## BUY Thesis\n"
            f"{_markdown(review.get('thesis'))}\n",
            encoding="utf-8",
        )
        pattern_dir = memory_root() / "patterns" / ("success" if review.get("status") == "SUCCESS" else "failure")
        pattern_dir.mkdir(parents=True, exist_ok=True)
        pattern_path = pattern_dir / f"{review.get('attribution', 'UNKNOWN')}.md"
        with pattern_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"- {date} `{symbol}` decision `{result.get('decision_id')}` "
                f"window_day={review.get('profit_window_day') or 'NONE'}\n"
            )
    return str(path)


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
        not rule.get('allow_forward_paper')
        or rule.get('auto_order')
        or rule.get('broker_connected')
        or not rule.get('manual_paper_execution_allowed')
    ):
        raise SystemExit(
            'rule_freeze requires forward paper evaluation with auto/broker execution disabled'
        )


def _snapshot_and_record(
    *,
    date: str,
    asof_time: str,
    symbol: str,
    decision: str,
    features: Dict[str, Any],
    decision_reason: str,
    generated_at: str,
    rule_version: str,
    production_run_id: str = '',
    correction_of: str = '',
    source: str = 'forward_paper_recorder_v0_1',
) -> Tuple[Path, Dict[str, Any], Dict[str, Any]]:
    from xiaogu_forward_result_filler_v0_1 import historical_entry_contract

    snap = snapshot_path_for(date, asof_time, rule_version, symbol)
    snapshot = {
        'date': date,
        'generated_at': generated_at,
        'asof_time': asof_time,
        'symbol': symbol,
        'decision': decision,
        'rule_version': rule_version,
        'production_run_id': production_run_id or None,
        'features_used': features,
        'source': source,
        'decision_id': features.get('decision_id'),
        'signal_time': features.get('signal_time') or asof_time,
        'entry_time': features.get('entry_time') or asof_time,
        'entry_price': features.get('entry_price') or (
            features.get('canonical_snapshot', {}).get('price')
            if isinstance(features.get('canonical_snapshot'), dict)
            else None
        ),
        'entry_price_source': features.get('entry_price_source') or 'canonical_snapshot.price',
        'capital_convergence': features.get('capital_convergence') or (
            features.get('core_alpha') or {}
        ).get('capital_convergence'),
        'capital_behavior': (features.get('research_context') or {}).get('capital'),
        'supply_absorption': features.get('supply_absorption') or (
            features.get('core_alpha') or {}
        ).get('supply_absorption'),
        'repricing_state': features.get('repricing_state') or (
            features.get('core_alpha') or {}
        ).get('repricing_state'),
        'profit_window_probability': features.get('profit_window_probability') or (
            features.get('core_alpha') or {}
        ).get('profit_window_probability'),
        'expected_max_profit_5d': features.get('expected_max_profit_5d') or (
            features.get('core_alpha') or {}
        ).get('expected_max_profit_5d'),
        'expected_mae_5d': features.get('expected_mae_5d') or (
            features.get('core_alpha') or {}
        ).get('expected_mae_5d'),
        'expected_net_profit_window': features.get('expected_net_profit_window') or (
            features.get('core_alpha') or {}
        ).get('expected_net_profit_window'),
        'model_version': features.get('model_version') or (
            features.get('core_alpha') or {}
        ).get('model_id'),
        'model_status': (features.get('core_alpha') or {}).get('model_status'),
        'feature_version': features.get('feature_version') or (
            features.get('core_alpha') or {}
        ).get('feature_version'),
        'decision_version': features.get('decision_version'),
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_forward_paper': True,
        'manual_paper_execution_allowed': decision == 'BUY',
        'auto_order': False,
        'broker_connected': False,
        'note': 'T-day visible snapshot only; T+1..T+5 window outcomes are appended after the decision.',
    }
    entry_contract = historical_entry_contract({
        'signal_time': asof_time if 'T' in asof_time else f'{date}T{asof_time}+08:00',
        'execution_price': features.get('canonical_snapshot', {}).get('price')
        if isinstance(features.get('canonical_snapshot'), dict)
        else None,
    })
    snapshot['entry_contract'] = entry_contract
    snapshot_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    snapshot['snapshot_sha256'] = snapshot_hash
    canonical = features.get('canonical_snapshot') if isinstance(features.get('canonical_snapshot'), dict) else {}
    record = {
        'id': features.get('decision_id'),
        'record_type': 'CORRECTION' if correction_of else 'DECISION',
        'correction_of': correction_of or None,
        'date': date,
        'generated_at': generated_at,
        'asof_time': asof_time,
        'symbol': symbol,
        'decision': decision,
        'rule_version': rule_version,
        'production_run_id': production_run_id or None,
        'features_used': features,
        'entry_contract': entry_contract,
        'raw_data_snapshot_path': str(snap),
        'raw_data_snapshot_sha256': snapshot_hash,
        'decision_reason': decision_reason,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_forward_paper': True,
        'manual_paper_execution_allowed': decision == 'BUY',
        'auto_order': False,
        'broker_connected': False,
        'result_status': 'PENDING',
        **{field: None for field in PENDING_OUTCOME_FIELDS},
        'result_filled_at': None,
        'post_result_locked': False,
        'locked_fields': LOCKED_AT_GENERATION,
        'append_only_policy': 'Never overwrite old records. Corrections append a new CORRECTION record.',
        'data_leakage_check': {'t_plus_fields_at_generation': False, 'status': 'PASS'},
        'production_trade_mode': PRODUCTION_TRADE_MODE,
        'production_target': 'PROFIT_WINDOW_5D',
        'production_return_formula': 'max_daily_bar_profit_opportunity_5d at DAILY_BAR_APPROXIMATION',
        'previous_state': features.get('portfolio_state_before'),
        'new_state': features.get('state') or decision,
        'signal_time': features.get('signal_time') or canonical.get('source_time'),
        'entry_time': features.get('entry_time') or features.get('signal_time') or canonical.get('source_time'),
        'entry_price': features.get('entry_price') or canonical.get('price'),
        'entry_price_source': features.get('entry_price_source') or 'canonical_snapshot.price',
        'decision_version': features.get('decision_version'),
        'alpha_version': features.get('alpha_version'),
        'feature_version': features.get('feature_version'),
        'model_version': features.get('model_version') or (features.get('core_alpha') or {}).get('model_id'),
        'capital_convergence': features.get('capital_convergence') or (features.get('core_alpha') or {}).get('capital_convergence'),
        'capital_behavior': (features.get('research_context') or {}).get('capital'),
        'supply_absorption': features.get('supply_absorption') or (features.get('core_alpha') or {}).get('supply_absorption'),
        'repricing_state': features.get('repricing_state') or (features.get('core_alpha') or {}).get('repricing_state'),
        'profit_window_probability': features.get('profit_window_probability') or (features.get('core_alpha') or {}).get('profit_window_probability'),
        'expected_max_profit_5d': features.get('expected_max_profit_5d') or (features.get('core_alpha') or {}).get('expected_max_profit_5d'),
        'expected_mae_5d': features.get('expected_mae_5d') or (features.get('core_alpha') or {}).get('expected_mae_5d'),
        'expected_net_profit_window': features.get('expected_net_profit_window') or (features.get('core_alpha') or {}).get('expected_net_profit_window'),
        'thesis': features.get('thesis'),
        'holding_days': features.get('holding_days', 0),
        'renewal_count': features.get('renewal_count', 0),
        'state_transition_timestamp': features.get('state_transition', {}).get('timestamp') if isinstance(features.get('state_transition'), dict) else features.get('signal_time'),
        'state_transition': features.get('state_transition'),
        'entry': features.get('entry_price') or canonical.get('price'),
        'exit': features.get('exit_price'),
        'pnl': features.get('realized_pnl'),
        'costs': (features.get('core_alpha') or {}).get('execution_constraints', {}).get('cost_rate'),
        'exit_reason': features.get('reason') if decision in {'REDUCE', 'SELL'} else None,
    }
    return snap, snapshot, record


def append_production_decision(decision: Dict[str, Any]) -> Tuple[Path, Dict[str, Any]]:
    """Persist a production decision through the sole append-only ledger owner."""
    rule = load_json(RULE_FREEZE)
    if str(decision.get("state") or "") not in RECORDABLE_DECISIONS:
        raise ValueError("RECORDER_ACCEPTS_PRODUCTION_EVENTS_ONLY")
    validate_generation(str(decision['state']), rule)
    canonical = decision['canonical_snapshot']
    snap, snapshot, record = _snapshot_and_record(
        date=str(canonical['trade_date']),
        asof_time=str(canonical.get('source_time') or '000000'),
        symbol=str(canonical['symbol']),
        decision=str(decision['state']),
        features=decision,
        decision_reason=str(decision['reason']),
        generated_at=now_iso(),
        rule_version=str(rule['rule_version']),
        production_run_id=str(decision.get('production_run_id') or ''),
        source='xiaogu_forward_runner',
    )
    from xiaogu_db import record_decision, record_snapshot
    try:
        record_snapshot(canonical)
        record_decision(decision)
        record['database_persistence'] = {'status': 'PASS'}
    except Exception as exc:
        record['database_persistence'] = {'status': 'FAILED', 'error': repr(exc)}
        raise
    memory_error = None
    memory_path = None
    for _attempt in range(2):
        try:
            memory_path = write_trade_memory(record) or None
            memory_error = None
            break
        except OSError as exc:
            memory_error = repr(exc)
    record['memory_path'] = memory_path
    if memory_error:
        record['memory_error'] = memory_error
        retry_path = BASE / 'logs' / 'obsidian_retry_queue.jsonl'
        retry_path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(retry_path, {
            'record_type': 'MEMORY_RETRY',
            'decision_id': record.get('id') or decision.get('decision_id'),
            'symbol': record.get('symbol'),
            'date': record.get('date'),
            'error': memory_error,
        })
    dump_json(snap, snapshot)
    append_jsonl(FORWARD_LEDGER, record)
    return snap, record


def main() -> None:
    ap = argparse.ArgumentParser(description='append one forward paper record')
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--asof-time', default='14:50:00')
    ap.add_argument('--symbol', default='')
    ap.add_argument('--decision', required=True, choices=sorted(RECORDABLE_DECISIONS))
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

    symbol = args.symbol
    snap, snapshot, rec = _snapshot_and_record(
        date=args.date,
        asof_time=args.asof_time,
        symbol=symbol,
        decision=args.decision,
        features=features,
        decision_reason=args.decision_reason,
        generated_at=args.generated_at,
        rule_version=rule_version,
        production_run_id=args.production_run_id,
        correction_of=args.correction_of,
    )
    rec['xiaochan_gate_status'] = args.xiaochan_gate_status
    rec['xiaoshuju_data_gate_status'] = args.xiaoshuju_data_gate_status

    if args.dry_run:
        preview = {'would_write_snapshot': str(snap), 'would_append_ledger': str(FORWARD_LEDGER), 'record': rec}
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    dump_json(snap, snapshot)
    try:
        rec['memory_path'] = write_trade_memory(rec) or None
    except OSError as exc:
        rec['memory_path'] = None
        rec['memory_error'] = repr(exc)
    append_jsonl(FORWARD_LEDGER, rec)
    print(json.dumps({'appended': True, 'ledger': str(FORWARD_LEDGER), 'snapshot': str(snap), 'record': rec}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
