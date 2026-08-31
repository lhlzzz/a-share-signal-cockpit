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
from urllib.request import Request, urlopen
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
MEMORY_RETRY_QUEUE = BASE / 'logs' / 'obsidian_retry_queue.jsonl'
RECORDABLE_DECISIONS = {"BUY", "HOLD", "REDUCE", "SELL"}
PRODUCTION_DECISION_STATES = RECORDABLE_DECISIONS | {"WATCH", "READY"}
PAPER_OBSERVATION_STATUS = "PAPER_OBSERVATION"
PAPER_OBSERVATION_STATES = {"OBSERVED", "CLOSED"}
PAPER_POSITION_STATES = {"PAPER_FLAT", "PAPER_LONG"}
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


def _memory_symbol(value: Any) -> str:
    return "".join(char for char in str(value or "UNKNOWN").zfill(6) if char.isalnum() or char in "._-")


def _memory_note_path(state: str, record: Dict[str, Any]) -> str:
    section = "EXIT" if state in {"SELL", "REDUCE"} else state
    return f"xiaogu_memory/decisions/{section}/{record.get('date')}_{_memory_symbol(record.get('symbol'))}.md"


def _memory_bridge_url() -> str:
    return str(os.environ.get("XIAOGU_OBSIDIAN_BRIDGE_URL") or "").rstrip("/")


def _queue_memory_retry(operation: str, payload: Dict[str, Any], error: str) -> None:
    append_jsonl(MEMORY_RETRY_QUEUE, {
        "record_type": "MEMORY_RETRY",
        "operation": operation,
        "decision_id": payload.get("decision_id"),
        "paper_signal_id": payload.get("paper_signal_id"),
        "symbol": payload.get("symbol"),
        "date": payload.get("date"),
        "error": error,
        "payload": payload,
    })


def _send_memory(operation: str, payload: Dict[str, Any]) -> str | None:
    bridge = _memory_bridge_url()
    if not bridge:
        _queue_memory_retry(operation, payload, "OBSIDIAN_BRIDGE_UNAVAILABLE")
        return None
    request = Request(
        f"{bridge}/memory",
        data=json.dumps({"operation": operation, **payload}, ensure_ascii=False, default=str).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except OSError as exc:
        _queue_memory_retry(operation, payload, repr(exc))
        return None
    return str(payload.get("path") or "")


def _markdown(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value if value not in (None, "") else "UNKNOWN")


def write_trade_memory(record: Dict[str, Any]) -> str | None:
    """Send one decision note through the Obsidian memory adapter."""
    state = str(record.get("decision") or "WATCH").upper()
    if state not in RECORDABLE_DECISIONS | {PAPER_OBSERVATION_STATUS}:
        return ""
    features = record.get("features_used") or {}
    alpha = features.get("core_alpha") or {}
    research = features.get("research_context") or {}
    thesis = record.get("thesis") or alpha.get("thesis") or {}
    note = f"""---
decision_id: {record.get('id') or record.get('decision_id') or 'UNKNOWN'}
paper_signal_id: {record.get('paper_signal_id') or 'NONE'}
symbol: {_memory_symbol(record.get('symbol'))}
decision: {state}
date: {record.get('date')}
signal_time: {record.get('signal_time') or record.get('asof_time')}
reference_price: {record.get('reference_price')}
alpha_version: {record.get('alpha_version') or alpha.get('alpha_version')}
feature_version: {record.get('feature_version') or alpha.get('feature_version')}
---

# XIAOGU {state}

## Decision
- Decision ID: `{record.get('id') or record.get('decision_id') or 'UNKNOWN'}`
- Reference price: `{record.get('reference_price') or 'UNKNOWN'}`
- Reason: {_markdown(record.get('decision_reason'))}
- Previous state: {_markdown(record.get('previous_state'))}
- Paper observation state: `{record.get('paper_observation_state') or 'N/A'}`
- Paper position state: `{record.get('paper_position_state') or 'PAPER_FLAT'}`
- Live order: `DISABLED`

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
- Repricing evidence score (diagnostic only): {_markdown(alpha.get('repricing_evidence_score'))}
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
    return _send_memory("UPSERT_NOTE", {
        "path": path,
        "decision_id": record.get("id") or record.get("decision_id"),
        "paper_signal_id": record.get("paper_signal_id"),
        "symbol": _memory_symbol(record.get("symbol")),
        "date": record.get("date"),
        "content": note,
    })


def update_trade_memory(result: Dict[str, Any]) -> str | None:
    """Send outcome/review facts through the Obsidian memory adapter."""
    symbol = _memory_symbol(result.get("symbol"))
    date = result.get("date")
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
    return _send_memory("UPDATE_OUTCOME", {
        "path": f"xiaogu_memory/decisions/{date}_{symbol}.md",
        "decision_id": result.get("decision_id"),
        "paper_signal_id": result.get("paper_signal_id"),
        "symbol": symbol,
        "date": date,
        "outcome": block,
        "post_trade_review": result.get("post_trade_review") if result.get("outcome_complete") else None,
    })


def write_daily_paper_memory(
    trade_date: str,
    signals: list[Dict[str, Any]],
    *,
    scan_status: str = "NO_SIGNAL",
    scan_reason: str = "",
    canonical_count: int = 0,
    alpha_count: int = 0,
) -> str | None:
    """Send one compact daily observation note through the memory adapter."""
    rows = []
    for signal in signals:
        paper = signal.get("paper_observation") if isinstance(signal.get("paper_observation"), dict) else signal
        rows.append(
            f"- `{paper.get('paper_signal_id')}` decision=`{paper.get('decision_id')}` "
            f"`{signal.get('symbol')}` reference={paper.get('reference_price')} "
            f"price_strength={paper.get('price_strength')} "
            f"state={paper.get('paper_observation_state') or 'OBSERVED'} "
            f"reason={paper.get('signal_reason') or 'CURRENT_PRODUCTION_DECISION'}"
        )
    content = (
        f"# {trade_date}\n\n"
        "## Xiaogu Paper Production\n\n"
        f"- Scan status: `{scan_status}`\n"
        f"- Scan reason: `{scan_reason or 'NONE'}`\n"
        f"- Canonical count: `{canonical_count}`\n"
        f"- Alpha count: `{alpha_count}`\n"
        f"- Paper observation count: `{len(signals)}`\n"
        "- Status: `PAPER_OBSERVATION_ONLY`\n"
        "- Production alpha: `price_strength`\n"
        "- Capital alpha: `RESEARCH_ONLY`\n"
        "- Live trading: `DISABLED`\n"
        "- Production BUY: `BLOCKED`\n\n"
        "## Signals\n"
        + ("\n".join(rows) if rows else "- `NO PAPER OBSERVATION`")
        + "\n\n## Outcome\n- T+1..T+5 are filled from PostgreSQL future-price facts.\n",
    )
    return _send_memory("UPSERT_DAILY", {
        "path": f"xiaogu_memory/daily/{trade_date}.md",
        "date": trade_date,
        "content": content,
    })


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


def validate_paper_observation(decision: Dict[str, Any], rule: Dict[str, Any]) -> None:
    observation = decision.get("paper_observation") if isinstance(decision.get("paper_observation"), dict) else {}
    if observation.get("status") != PAPER_OBSERVATION_STATUS:
        raise ValueError("PAPER_OBSERVATION_CONTRACT_REQUIRED")
    if not str(observation.get("paper_signal_id") or "").strip():
        raise ValueError("PAPER_OBSERVATION_ID_REQUIRED")
    if not str(observation.get("decision_id") or "").strip():
        raise ValueError("DECISION_ID_REQUIRED")
    canonical = decision.get("canonical_snapshot") or {}
    if canonical.get("trusted_snapshot") is not True:
        raise ValueError("TRUSTED_CANONICAL_REQUIRED")
    if observation.get("alpha_name") != "price_strength":
        raise ValueError("PAPER_OBSERVATION_ALPHA_CONTRACT_INVALID")
    if observation.get("live_order") is not False or observation.get("paper_only") is not True:
        raise ValueError("PAPER_OBSERVATION_LIVE_EXECUTION_DISABLED")
    if observation.get("paper_observation_state") not in PAPER_OBSERVATION_STATES:
        raise ValueError("PAPER_OBSERVATION_STATE_REQUIRED")
    if observation.get("paper_position_state") not in PAPER_POSITION_STATES:
        raise ValueError("PAPER_POSITION_STATE_REQUIRED")
    if observation.get("paper_position_state") != "PAPER_FLAT":
        raise ValueError("PAPER_ENTRY_OWNER_UNAVAILABLE")
    if not rule.get("paper_only") or not rule.get("no_trade") or rule.get("production_ready"):
        raise ValueError("PAPER_OBSERVATION_SAFETY_FLAGS_INVALID")
    if rule.get("auto_order") or rule.get("broker_connected"):
        raise ValueError("PAPER_OBSERVATION_LIVE_EXECUTION_DISABLED")


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
        'paper_signal_id': (features.get('paper_observation') or {}).get('paper_signal_id'),
        'signal_time': features.get('signal_time') or asof_time,
        'entry_time': features.get('entry_time') or asof_time,
        'reference_price': features.get('reference_price') or (
            features.get('canonical_snapshot', {}).get('price')
            if isinstance(features.get('canonical_snapshot'), dict)
            else None
        ),
        'reference_price_source': features.get('reference_price_source') or 'canonical_snapshot.price',
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
        'manual_paper_execution_allowed': False,
        'auto_order': False,
        'broker_connected': False,
        'paper_observation_status': PAPER_OBSERVATION_STATUS if decision == PAPER_OBSERVATION_STATUS else None,
        'paper_observation_state': (features.get('paper_observation') or {}).get('paper_observation_state'),
        'paper_position_state': (features.get('paper_observation') or {}).get('paper_position_state'),
        'signal_reason': (features.get('paper_observation') or {}).get('signal_reason') or decision_reason,
        'paper_observation_contract_version': (features.get('paper_observation') or {}).get(
            'paper_observation_contract_version'
        ),
        'note': 'T-day visible snapshot only; T+1..T+5 window outcomes are appended after the decision.',
    }
    canonical = features.get('canonical_snapshot') if isinstance(features.get('canonical_snapshot'), dict) else {}
    observation_contract = {
        "signal_time": features.get("signal_time") or asof_time,
        "reference_price": features.get("reference_price") or canonical.get("price"),
        "price_basis": "UNADJUSTED",
    }
    entry_contract = None
    if decision in RECORDABLE_DECISIONS:
        entry_contract = historical_entry_contract({
            'signal_time': asof_time if 'T' in asof_time else f'{date}T{asof_time}+08:00',
            'execution_price': features.get('canonical_snapshot', {}).get('price')
            if isinstance(features.get('canonical_snapshot'), dict)
            else None,
        })
        snapshot['entry_contract'] = entry_contract
    else:
        snapshot["observation_contract"] = observation_contract
    snapshot_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    snapshot['snapshot_sha256'] = snapshot_hash
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
        'observation_contract': observation_contract if decision == PAPER_OBSERVATION_STATUS else None,
        'raw_data_snapshot_path': str(snap),
        'raw_data_snapshot_sha256': snapshot_hash,
        'decision_reason': decision_reason,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_forward_paper': True,
        'manual_paper_execution_allowed': False,
        'auto_order': False,
        'broker_connected': False,
        'paper_observation_status': PAPER_OBSERVATION_STATUS if decision == PAPER_OBSERVATION_STATUS else None,
        'paper_observation_state': (features.get('paper_observation') or {}).get('paper_observation_state'),
        'paper_position_state': (features.get('paper_observation') or {}).get('paper_position_state'),
        'signal_reason': (features.get('paper_observation') or {}).get('signal_reason') or decision_reason,
        'paper_observation_contract_version': (features.get('paper_observation') or {}).get(
            'paper_observation_contract_version'
        ),
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
        'snapshot_id': features.get('snapshot_id') or canonical.get('snapshot_id'),
        'lineage_id': features.get('lineage_id') or canonical.get('lineage_id'),
        'reference_price': features.get('reference_price') or canonical.get('price'),
        'reference_price_source': features.get('reference_price_source') or 'canonical_snapshot.price',
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
        'reference': features.get('reference_price') or canonical.get('price'),
        'exit': features.get('exit_price'),
        'pnl': features.get('realized_pnl'),
        'costs': (features.get('core_alpha') or {}).get('execution_constraints', {}).get('cost_rate'),
        'exit_reason': features.get('reason') if decision in {'REDUCE', 'SELL'} else None,
    }
    return snap, snapshot, record


def append_production_decision(decision: Dict[str, Any]) -> Tuple[Path, Dict[str, Any]]:
    """Persist DB truth, then append audit JSONL, then write memory."""
    rule = load_json(RULE_FREEZE)
    state = str(decision.get("state") or "")
    if state not in PRODUCTION_DECISION_STATES:
        raise ValueError("RECORDER_ACCEPTS_PRODUCTION_EVENTS_ONLY")
    if state in RECORDABLE_DECISIONS:
        validate_generation(state, rule)
    canonical = decision.get('canonical_snapshot')
    if not isinstance(canonical, dict):
        raise ValueError("CANONICAL_SNAPSHOT_REQUIRED")
    snap, snapshot, record = _snapshot_and_record(
        date=str(canonical['trade_date']),
        asof_time=str(canonical.get('source_time') or '000000'),
        symbol=str(canonical['symbol']),
        decision=state,
        features=decision,
        decision_reason=str(decision['reason']),
        generated_at=now_iso(),
        rule_version=str(rule['rule_version']),
        production_run_id=str(decision.get('production_run_id') or ''),
        source='xiaogu_forward_runner',
    )
    from xiaogu_db import record_snapshot_and_decision
    try:
        record_snapshot_and_decision(canonical, decision)
        record['database_persistence'] = {'status': 'PASS'}
    except Exception as exc:
        record['database_persistence'] = {'status': 'FAILED', 'error': repr(exc)}
        raise
    if state not in RECORDABLE_DECISIONS:
        record['audit_persistence'] = {'status': 'SKIPPED'}
        record['memory_status'] = 'SKIPPED'
        return Path(""), record
    dump_json(snap, snapshot)
    record['audit_persistence'] = {'status': 'PASS'}
    append_jsonl(FORWARD_LEDGER, record)
    memory_path = write_trade_memory(record)
    record['memory_path'] = memory_path
    record['memory_status'] = 'SYNCED' if memory_path else 'RETRY_QUEUED'
    return snap, record


def append_paper_observation(decision: Dict[str, Any]) -> Tuple[Path, Dict[str, Any]]:
    """Persist the observation wrapper without creating a second decision."""
    rule = load_json(RULE_FREEZE)
    validate_paper_observation(decision, rule)
    canonical = decision["canonical_snapshot"]
    observation = decision["paper_observation"]
    paper_record = {
        **observation,
        "canonical_snapshot": canonical,
        "trade_date": canonical["trade_date"],
        "generated_at": now_iso(),
    }
    from xiaogu_db import paper_observation_exists, record_paper_observation
    if paper_observation_exists(str(observation["paper_signal_id"])):
        return Path(""), {
            "paper_signal_id": observation["paper_signal_id"],
            "decision_id": observation["decision_id"],
            "database_persistence": {"status": "ALREADY_EXISTS"},
            "audit_persistence": {"status": "SKIPPED"},
        }
    record_paper_observation(paper_record)
    snap, snapshot, record = _snapshot_and_record(
        date=str(canonical["trade_date"]),
        asof_time=str(canonical.get("source_time") or "000000"),
        symbol=str(canonical["symbol"]),
        decision=PAPER_OBSERVATION_STATUS,
        features=decision,
        decision_reason="CURRENT_PRODUCTION_DECISION",
        generated_at=paper_record["generated_at"],
        rule_version=str(rule["rule_version"]),
        production_run_id=str(decision.get("production_run_id") or ""),
        source="xiaogu_forward_runner",
    )
    record["database_persistence"] = {"status": "PASS"}
    dump_json(snap, snapshot)
    record["audit_persistence"] = {"status": "PASS"}
    append_jsonl(FORWARD_LEDGER, record)
    record["memory_path"] = write_trade_memory(record)
    record["memory_status"] = "SYNCED" if record["memory_path"] else "RETRY_QUEUED"
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

    canonical = features.get('canonical_snapshot')
    if not isinstance(canonical, dict):
        raise ValueError('CANONICAL_SNAPSHOT_REQUIRED')
    persisted_decision = {
        **features,
        'state': args.decision,
        'action': args.decision,
        'decision_id': features.get('decision_id') or rec.get('id'),
        'reason': args.decision_reason,
        'canonical_snapshot': canonical,
        'xiaochan_gate_status': args.xiaochan_gate_status,
        'xiaoshuju_data_gate_status': args.xiaoshuju_data_gate_status,
    }
    _snap_path, rec = append_production_decision(persisted_decision)
    print(json.dumps({'appended': True, 'ledger': str(FORWARD_LEDGER), 'snapshot': str(snap), 'record': rec}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
