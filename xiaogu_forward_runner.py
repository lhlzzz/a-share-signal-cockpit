#!/usr/bin/env python3
"""Production orchestration: canonical snapshot to one portfolio decision."""
from __future__ import annotations

import argparse
import json
import copy
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict

from xiaogu_forward_bundle_io import load_latest_snapshot_bundle
from xiaogu_forward_eligibility import candidate_universe, execution_universe
from xiaogu_forward_snapshot import (
    assert_production_provenance,
    production_decision_clock,
    production_now,
    select_canonical_snapshot,
    select_production_observation_snapshots,
    snapshot_age,
    validate_and_build_canonical_snapshot,
)
from xiaogu_portfolio_decision import attach_top_paper_observations, evaluate_candidate_bundle

BASE = Path(__file__).resolve().parent
RECORDABLE_ACTIONS = {"BUY", "HOLD", "REDUCE", "SELL"}


PRODUCTION_MODES = ("PRODUCTION", "REPLAY", "DRY_RUN", "RESEARCH")
DEFAULT_DECISION_WORKERS = 8
MAX_DECISION_WORKERS = 32


def _parse_clock(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def run_production_decision(
    snapshot: Dict[str, Any],
    *,
    portfolio_state: str = "WATCH",
    account: Dict[str, Any] | None = None,
    mode: str = "PRODUCTION",
    persisted: bool = False,
    trade_date: str = "",
    decision_clock: datetime | None = None,
    position_state: str | None = None,
    previous_action: str | None = None,
) -> Dict[str, Any]:
    mode = str(mode or "PRODUCTION").upper()
    if mode not in PRODUCTION_MODES:
        raise ValueError(f"INVALID_RUNNER_MODE:{mode}")
    trusted = validate_and_build_canonical_snapshot(
        snapshot,
        trade_date=str(snapshot.get("trade_date") or trade_date or ""),
        source=str(snapshot.get("source") or "eastmoney_api_scan_v2"),
        source_time=str(snapshot.get("source_time") or snapshot.get("as_of") or ""),
        target_trade_date=trade_date,
    )
    account = account or {}
    if mode == "PRODUCTION":
        from xiaogu_db import get_position_by_decision_id, get_position_by_id, verify_persisted_snapshot
        db_verified = verify_persisted_snapshot(
            snapshot_id=str(trusted.get("snapshot_id") or ""),
            lineage_id=str(trusted.get("lineage_id") or ""),
            trade_date=trade_date or str(trusted.get("trade_date") or ""),
            source=str(trusted.get("source") or ""),
            source_time=str(trusted.get("source_time") or ""),
            symbol=str(trusted.get("symbol") or ""),
            payload_hash=str(trusted.get("payload_hash") or ""),
        )
        if not db_verified:
            raise RuntimeError("SNAPSHOT_PERSISTENCE_FAILED")
        clock = production_decision_clock(decision_clock)
        trusted = assert_production_provenance(
            trusted,
            trade_date=trade_date or str(trusted.get("trade_date") or ""),
            decision_time=clock,
            persisted=True,
        )
        if account.get("paper_review"):
            if not str(account.get("paper_signal_id") or "") or not str(account.get("decision_id") or ""):
                raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:POSITION_IDENTITY_UNAVAILABLE")
            if position_state not in {"FLAT", "LONG"}:
                raise RuntimeError("POSITION_STATE_UNAVAILABLE")
        elif account.get("position_review"):
            position_id = str(account.get("position_id") or "").strip()
            review_decision_id = str(account.get("decision_id") or "").strip()
            if not position_id or not review_decision_id:
                raise RuntimeError("POSITION_REVIEW_BLOCKED:POSITION_IDENTITY_UNAVAILABLE")
            db_position = get_position_by_id(position_id)
            if db_position is None:
                raise RuntimeError("POSITION_STATE_UNAVAILABLE")
            if str(db_position.get("decision_id") or "").strip() != review_decision_id:
                raise RuntimeError("POSITION_IDENTITY_CONFLICT")
            by_decision = get_position_by_decision_id(review_decision_id)
            if by_decision is None or str(by_decision.get("position_id") or "").strip() != position_id:
                raise RuntimeError("POSITION_IDENTITY_CONFLICT")
            db_position_state = db_position.get("position_state")
            if db_position_state is None:
                raise RuntimeError("POSITION_STATE_UNAVAILABLE")
            if position_state is not None and position_state != db_position_state:
                raise RuntimeError("POSITION_STATE_CONFLICT")
            position_state = db_position_state
        elif position_state is None:
            raise RuntimeError("POSITION_STATE_UNAVAILABLE")
        elif position_state not in {"FLAT", "LONG"}:
            raise RuntimeError(f"INVALID_POSITION_STATE:{position_state}")
    elif mode == "REPLAY":
        clock = production_decision_clock(
            decision_clock
            or _parse_clock(str(trusted.get("source_time") or trusted.get("as_of") or ""))
        )
    else:
        clock = decision_clock or _parse_clock(str(trusted.get("source_time") or trusted.get("as_of") or ""))
    return evaluate_candidate_bundle(
        trusted,
        portfolio_state=portfolio_state,
        account=account,
        as_of=clock,
        position_state=position_state,
        previous_action=previous_action,
    )


def decision_worker_count(requested: int | None = None) -> int:
    if requested is None:
        raw = os.environ.get("XIAOGU_DECISION_WORKERS", str(DEFAULT_DECISION_WORKERS))
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            requested = DEFAULT_DECISION_WORKERS
    return max(1, min(int(requested), MAX_DECISION_WORKERS))


def _stale_decision(row: Dict[str, Any], reason: str = "STALE_DATA") -> Dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "snapshot_id": row.get("snapshot_id"),
        "lineage_id": row.get("lineage_id"),
        "trade_date": row.get("trade_date"),
        "state": "WATCH",
        "action": None,
        "reason": reason,
        "paper_observation": None,
        "failed_gates": ["FRESH_DATA"],
        "production_blockers": [],
        "error": None,
        "decision_owner": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
    }


def _error_decision(row: Dict[str, Any], exc: BaseException) -> Dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "snapshot_id": row.get("snapshot_id"),
        "lineage_id": row.get("lineage_id"),
        "trade_date": row.get("trade_date"),
        "state": "WATCH",
        "action": None,
        "reason": f"WORKER_ERROR:{type(exc).__name__}",
        "paper_observation": None,
        "failed_gates": [],
        "production_blockers": [],
        "error": repr(exc),
        "decision_owner": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
    }


def _evaluate_one_candidate(
    row: Dict[str, Any],
    *,
    portfolio_state: str,
    mode: str,
    trade_date: str,
) -> Dict[str, Any]:
    item = copy.deepcopy(dict(row))
    item.setdefault("trade_date", trade_date)
    if mode == "PRODUCTION":
        from xiaogu_forward_snapshot import MAX_STALENESS, production_now, snapshot_age
        age = snapshot_age(str(item.get("source_time") or ""), production_now())
        if age is None or age > MAX_STALENESS:
            result = _stale_decision(item)
            result["source_age_seconds"] = None if age is None else age.total_seconds()
            return result
    try:
        return run_production_decision(
            item,
            portfolio_state=portfolio_state,
            mode=mode,
            trade_date=trade_date,
            position_state="FLAT",
        )
    except ValueError as exc:
        if str(exc) == "STALE_DATA" or str(exc).startswith("STALE_DATA"):
            return _stale_decision(item)
        return _error_decision(item, exc)
    except Exception as exc:
        return _error_decision(item, exc)


def evaluate_candidate_rows(
    rows: list[Dict[str, Any]],
    *,
    portfolio_state: str,
    mode: str,
    trade_date: str,
    workers: int | None = None,
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Evaluate candidates independently. Output order follows input order, not completion."""
    worker_count = decision_worker_count(workers)
    results: list[Dict[str, Any] | None] = [None] * len(rows)

    def _run(index: int, row: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return index, _evaluate_one_candidate(
            row,
            portfolio_state=portfolio_state,
            mode=mode,
            trade_date=trade_date,
        )

    if worker_count == 1 or len(rows) <= 1:
        for index, row in enumerate(rows):
            results[index] = _evaluate_one_candidate(
                row,
                portfolio_state=portfolio_state,
                mode=mode,
                trade_date=trade_date,
            )
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(_run, index, row) for index, row in enumerate(rows)]
            for future in as_completed(futures):
                try:
                    index, decision = future.result()
                except Exception as exc:
                    raise RuntimeError(f"WORKER_RESULT_MISSING:{exc!r}") from exc
                results[index] = decision
    decisions = []
    success_count = 0
    blocked_count = 0
    stale_count = 0
    error_count = 0
    for index, decision in enumerate(results):
        if decision is None:
            decision = _error_decision(rows[index], RuntimeError("WORKER_RESULT_MISSING"))
        reason = str(decision.get("reason") or "")
        if decision.get("error") or reason.startswith("WORKER_ERROR"):
            error_count += 1
        elif reason == "STALE_DATA" or "STALE_DATA" in reason or "STALE_DATA" in (decision.get("failed_gates") or []):
            stale_count += 1
        else:
            success_count += 1
            blockers = list(decision.get("failed_gates") or []) + list(decision.get("production_blockers") or [])
            if decision.get("state") in {"WATCH", "READY"} or blockers:
                blocked_count += 1
        decisions.append(decision)
    attach_top_paper_observations(decisions)
    return decisions, {
        "workers": worker_count,
        "success_count": success_count,
        "blocked_count": blocked_count,
        "stale_count": stale_count,
        "error_count": error_count,
    }


def _write_ledger_record(decision: Dict[str, Any]) -> Path:
    from xiaogu_forward_paper_recorder_v0_1 import append_production_decision
    path, _record = append_production_decision(decision)
    return path


def _write_paper_observation(decision: Dict[str, Any]) -> Dict[str, Any]:
    from xiaogu_forward_paper_recorder_v0_1 import append_paper_observation
    _path, record = append_paper_observation(decision)
    return {
        "paper_signal_id": record.get("paper_signal_id") or (decision.get("paper_observation") or {}).get("paper_signal_id"),
        "decision_id": record.get("decision_id") or (decision.get("paper_observation") or {}).get("decision_id"),
        "symbol": record.get("symbol") or decision.get("symbol"),
        "rank": (decision.get("paper_observation") or {}).get("rank"),
        "top1_flag": (decision.get("paper_observation") or {}).get("top1_flag"),
        "top3_flag": (decision.get("paper_observation") or {}).get("top3_flag"),
        "selection_reason": (decision.get("paper_observation") or {}).get("selection_reason"),
        "signal_reason": (decision.get("paper_observation") or {}).get("signal_reason"),
        "database_persistence": record.get("database_persistence"),
        "audit_persistence": record.get("audit_persistence"),
        "memory_status": record.get("memory_status"),
    }


def _scan_observation_from_dir(scan_dir: str) -> Dict[str, Any]:
    """Read this scan's production observation identity. It is not latest-wins."""
    summary_path = Path(scan_dir) / "scan_summary.json"
    if not summary_path.exists():
        return {"status": "SCAN_BLOCKED", "reason": "SCAN_SUMMARY_NOT_FOUND", "lineage_id": "", "source_time": ""}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    lineage_id = str((summary.get("lineage") or {}).get("lineage_id") or "").strip()
    source_time = str(summary.get("source_time") or "")
    persist = summary.get("database_persistence") or {}
    if str(summary.get("production_scan") or "") != "PASS":
        return {
            "status": "SCAN_BLOCKED",
            "reason": str(summary.get("block_reason") or "PRODUCTION_SCAN_BLOCKED"),
            "lineage_id": lineage_id,
            "source_time": source_time,
            "run_id": str(persist.get("run_id") or ""),
        }
    if not lineage_id:
        return {"status": "SCAN_BLOCKED", "reason": "LINEAGE_ID_REQUIRED", "lineage_id": "", "source_time": source_time}
    return {
        "status": "PASS",
        "reason": "",
        "lineage_id": lineage_id,
        "source_time": source_time,
        "run_id": str(persist.get("run_id") or ""),
    }


def _load_scan_dir_snapshots(scan_dir: str, trade_date: str) -> list[Dict[str, Any]]:
    snapshot_path = Path(scan_dir) / "canonical_snapshots.jsonl"
    if not snapshot_path.exists():
        return []
    rows = []
    with snapshot_path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _block_funnel(decisions: list[Dict[str, Any]], *, execution_rejected: int = 0) -> Dict[str, int]:
    freshness_blocked = 0
    alpha_blocked = 0
    gate_blocked = 0
    strategy_no_signal = 0
    strategy_signal = 0
    for decision in decisions:
        reason = str(decision.get("reason") or "")
        failed = list(decision.get("failed_gates") or [])
        blockers = list(decision.get("production_blockers") or []) + list(decision.get("failed_gates") or [])
        alpha = decision.get("core_alpha") or {}
        if reason == "STALE_DATA" or "FRESH_DATA" in failed or "STALE_DATA" in blockers:
            freshness_blocked += 1
            continue
        if "ALPHA_VALIDATED" in failed or "ALPHA_NOT_VALIDATED" in blockers:
            alpha_blocked += 1
        if failed or decision.get("production_blockers") or str(decision.get("buy_status") or "") == "BUY_BLOCKED":
            gate_blocked += 1
        if alpha.get("signal_qualified") is True or decision.get("paper_observation"):
            strategy_signal += 1
        else:
            strategy_no_signal += 1
    return {
        "execution_eligibility_blocked": int(execution_rejected),
        "freshness_blocked": freshness_blocked,
        "alpha_blocked": alpha_blocked,
        "gate_blocked": gate_blocked,
        "strategy_no_signal": strategy_no_signal,
        "strategy_signal": strategy_signal,
    }


def _scan_status_from_run(
    *,
    paper_count: int,
    decision_count: int,
    freshness_blocked: int,
    buy_allowed: int,
    qualified_signal_count: int = 0,
) -> tuple[str, str]:
    if decision_count > 0 and freshness_blocked == decision_count and paper_count == 0:
        return "STALE_DATA", "STALE_DATA"
    if paper_count > 0 and buy_allowed > 0:
        return "SIGNAL_AVAILABLE", "PAPER_OBSERVATION_RECORDED"
    if paper_count > 0:
        return "BUY_BLOCKED", "PAPER_OBSERVATION_RECORDED"
    if qualified_signal_count == 0:
        return "NO_SIGNAL", "NO_FORMAL_SIGNAL"
    return "NO_SIGNAL", "NO_PAPER_OBSERVATION"


def _public_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    observation = decision.get("paper_observation")
    paper = None
    if isinstance(observation, dict):
        paper = {
            "status": observation.get("status"),
            "paper_signal_id": observation.get("paper_signal_id"),
            "decision_id": observation.get("decision_id"),
            "symbol": observation.get("symbol"),
            "rank": observation.get("rank"),
            "top1_flag": observation.get("top1_flag"),
            "top3_flag": observation.get("top3_flag"),
            "selection_reason": observation.get("selection_reason"),
            "signal_reason": observation.get("signal_reason"),
            "alpha_score": observation.get("alpha_score"),
            "production_buy": observation.get("production_buy"),
        }
    return {
        "decision_id": decision.get("decision_id"),
        "symbol": decision.get("symbol"),
        "snapshot_id": decision.get("snapshot_id"),
        "lineage_id": decision.get("lineage_id"),
        "trade_date": decision.get("trade_date"),
        "state": decision.get("state"),
        "action": decision.get("action"),
        "reason": decision.get("reason"),
        "buy_status": decision.get("buy_status"),
        "execution_eligible": decision.get("execution_eligible"),
        "signal_status": (decision.get("core_alpha") or {}).get("signal_status"),
        "signal_qualified": (decision.get("core_alpha") or {}).get("signal_qualified"),
        "signal_reason": (decision.get("core_alpha") or {}).get("signal_reason"),
        "research_consumed": (decision.get("core_alpha") or {}).get("research_consumed"),
        "paper_observation": paper,
    }


def _research_summary(decisions: list[Dict[str, Any]]) -> Dict[str, Any]:
    consumed = 0
    provenance: Dict[str, Dict[str, Any]] = {}
    for decision in decisions:
        alpha = decision.get("core_alpha") or {}
        if alpha.get("research_consumed") is True:
            consumed += 1
        for item in ((decision.get("research_context") or {}).get("research_provenance") or []):
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or "").strip()
            if not provider:
                continue
            slot = provenance.setdefault(provider, {
                "provider": provider,
                "role": item.get("role"),
                "invoked_count": 0,
            })
            if item.get("invoked"):
                slot["invoked_count"] += 1
    return {
        "research_consumed_count": consumed,
        "research_provenance": list(provenance.values()),
    }


def _compact_paper_observation(decision: Dict[str, Any]) -> Dict[str, Any] | None:
    observation = decision.get("paper_observation")
    if not isinstance(observation, dict):
        return None
    return {
        "paper_signal_id": observation.get("paper_signal_id"),
        "decision_id": observation.get("decision_id"),
        "symbol": observation.get("symbol") or decision.get("symbol"),
        "rank": observation.get("rank"),
        "top1_flag": observation.get("top1_flag"),
        "top3_flag": observation.get("top3_flag"),
        "selection_reason": observation.get("selection_reason"),
        "signal_reason": observation.get("signal_reason"),
        "alpha_score": observation.get("alpha_score"),
        "production_buy": observation.get("production_buy"),
    }


def _emit_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _empty_observation_output(trade_date: str, reason: str, *, scan_status: str = "SCAN_BLOCKED") -> Dict[str, Any]:
    output = {"date": trade_date, "mode": "PRODUCTION", "count": 0, "recorded": 0,
              "scan_status": scan_status, "scan_reason": reason,
              "l0_count": None, "l1_count": None, "l2_count": None, "l3_count": None,
              "canonical_count": None, "feature_count": None, "alpha_count": None,
              "decision_count": None, "paper_observation_count": None, "paper_observations": [],
              "state": "WATCH", "reason": reason}
    from xiaogu_forward_paper_recorder_v0_1 import write_daily_paper_memory
    output["daily_memory_path"] = write_daily_paper_memory(
        trade_date, [], scan_status=scan_status, scan_reason=reason,
        paper_observation_count=None,
    )
    try:
        from xiaogu_forward_result_filler_v0_1 import refresh_paper_dataset
        output["paper_dataset"] = refresh_paper_dataset()
    except Exception as exc:
        output["paper_dataset"] = {"status": "FAILED", "error": repr(exc)}
    return output


def _holding_days_since(opened_on: Any, reviewed_on: str) -> int:
    """Count the review horizon from the sole persisted trading calendar."""
    try:
        start = date.fromisoformat(str(opened_on))
        end = date.fromisoformat(str(reviewed_on))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("POSITION_REVIEW_BLOCKED:TRADING_CALENDAR_UNAVAILABLE") from exc
    try:
        from xiaogu_db import trading_days_between
        return trading_days_between(start, end)
    except RuntimeError as exc:
        raise RuntimeError(
            f"POSITION_REVIEW_BLOCKED:TRADING_CALENDAR_UNAVAILABLE:{exc}"
        ) from exc


def daily_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Re-evaluate active positions through the sole Decision Owner."""
    from xiaogu_db import (
        fetch_open_positions,
        fetch_position_outcome,
        get_current_position_review_snapshot,
    )

    positions = fetch_open_positions()
    reviewed = []
    for prior in positions:
        symbol = str(prior.get("symbol") or "").zfill(6)
        if str(prior.get("trade_date") or prior.get("opened_trade_date") or prior.get("date") or "") >= str(trade_date):
            continue
        position_id = str(prior.get("position_id") or "").strip()
        prior_id = str(prior.get("decision_id") or "").strip()
        if not position_id or not prior_id:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:POSITION_IDENTITY_UNAVAILABLE")
        original_snapshot_id = str(prior.get("original_snapshot_id") or prior.get("snapshot_id") or "")
        if not original_snapshot_id:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
        result = fetch_position_outcome(prior_id)
        holding_days = _holding_days_since(
            prior.get("trade_date") or prior.get("date"),
            trade_date,
        )
        previous_action = prior.get("action")
        previous_state = prior.get("state") or prior.get("previous_state")
        position_state = prior.get("position_state")
        if previous_state not in {"WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL"}:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:DECISION_STATE_UNAVAILABLE")
        if position_state not in {"FLAT", "LONG"}:
            raise RuntimeError("POSITION_STATE_UNAVAILABLE")
        review_snapshot = get_current_position_review_snapshot(
            symbol=symbol,
            review_trade_date=str(trade_date),
        )
        review_snapshot_id = str(review_snapshot.get("snapshot_id") or "")
        if not review_snapshot_id:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")
        account = {
            "position_id": position_id,
            "decision_id": prior_id,
            "position_review": True,
            "original_snapshot_id": original_snapshot_id,
            "review_snapshot_id": review_snapshot_id,
            "review_trade_date": str(trade_date),
            "position_state": position_state,
            "previous_action": previous_action,
            "holding_days": holding_days,
            "profit_window_hit": result.get("status") != "OUTCOME_NOT_BOUND" and result.get("profit_window") is True,
            "max_profit": result.get("max_daily_bar_profit_opportunity_5d") or result.get("max_profit"),
            "mae": result.get("max_mae_5d") or result.get("mae_5d"),
            "mfe": result.get("mfe_5d"),
        }
        decision = run_production_decision(
            review_snapshot,
            portfolio_state=previous_state,
            account=account,
            mode="PRODUCTION",
            trade_date=str(trade_date),
            position_state=position_state,
            previous_action=previous_action,
        )
        if decision.get("action") == "BUY" or decision.get("state") == "BUY":
            raise RuntimeError("POSITION_REVIEW_BLOCKED:BUY_NOT_ALLOWED")
        decision["position_id"] = position_id
        decision["decision_id"] = prior_id
        decision["original_snapshot_id"] = original_snapshot_id
        decision["review_snapshot_id"] = review_snapshot_id
        decision["review_trade_date"] = str(trade_date)
        decision["paper_observation"] = None
        decision.pop("paper_signal_id", None)
        decision["holding_days"] = holding_days
        decision["prior_decision_id"] = prior_id
        new_action = decision.get("action") or decision.get("state")
        if holding_days >= 5:
            new_action = "SELL"
            decision["action"] = "SELL"
            decision["state"] = "SELL"
            decision["position_state"] = "FLAT"
            decision["position_state_after"] = "FLAT"
            decision["trade_status"] = "CLOSED"
            decision["reason"] = "MAX_HOLDING_BOUNDARY_CLOSED"
        if new_action == "REDUCE":
            raise RuntimeError("POSITION_REVIEW_BLOCKED:REDUCE_UNSUPPORTED")
        if new_action not in {"HOLD", "SELL"}:
            raise RuntimeError(f"POSITION_REVIEW_BLOCKED:INVALID_REVIEW_ACTION:{new_action}")
        if new_action != previous_action or holding_days >= 5 or decision.get("trade_status") == "CLOSED":
            _write_ledger_record(decision)
            reviewed.append(decision)
    reviewed.extend(daily_paper_position_review(trade_date))
    return reviewed


def daily_paper_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Review explicit PAPER_LONG positions against the current trusted snapshot."""
    from xiaogu_db import (
        fetch_open_paper_positions,
        fetch_position_outcome,
        get_current_position_review_snapshot,
    )

    positions = fetch_open_paper_positions()
    reviewed = []
    for prior in positions:
        paper_signal_id = str(prior.get("paper_signal_id") or "").strip()
        prior_id = str(prior.get("decision_id") or "").strip()
        if not prior_id:
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:DECISION_ID_UNAVAILABLE")
        if not paper_signal_id:
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:PAPER_SIGNAL_ID_UNAVAILABLE")
        original_snapshot_id = str(prior.get("original_snapshot_id") or prior.get("snapshot_id") or "").strip()
        if not original_snapshot_id:
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
        symbol = str(prior.get("symbol") or "").zfill(6)
        try:
            review_snapshot = get_current_position_review_snapshot(
                symbol=symbol,
                review_trade_date=str(trade_date),
            )
        except RuntimeError as exc:
            reason = str(exc)
            if reason.startswith("PAPER_POSITION_REVIEW_BLOCKED:"):
                raise
            suffix = reason.split(":", 1)[-1] if ":" in reason else reason
            raise RuntimeError(f"PAPER_POSITION_REVIEW_BLOCKED:{suffix}") from exc
        review_snapshot_id = str(review_snapshot.get("snapshot_id") or "").strip()
        if not review_snapshot_id:
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")
        result = fetch_position_outcome(prior_id)
        holding_days = _holding_days_since(
            prior.get("trade_date") or prior.get("date"),
            trade_date,
        )
        decision = run_production_decision(
            review_snapshot,
            portfolio_state="HOLD",
            account={
                "decision_id": prior_id,
                "position_review": True,
                "paper_review": True,
                "paper_signal_id": paper_signal_id,
                "original_snapshot_id": original_snapshot_id,
                "review_snapshot_id": review_snapshot_id,
                "review_trade_date": str(trade_date),
                "holding_days": holding_days,
                "profit_window_hit": result.get("profit_window") is True,
                "max_profit": result.get("max_daily_bar_profit_opportunity_5d"),
                "mae": result.get("max_mae_5d"),
                "mfe": result.get("future_5d_mfe"),
            },
            mode="PRODUCTION",
            trade_date=str(trade_date),
            position_state="LONG",
            previous_action="HOLD",
        )
        if decision.get("action") == "BUY" or decision.get("state") == "BUY":
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:BUY_NOT_ALLOWED")
        action = decision.get("action") or "HOLD"
        if holding_days >= 5:
            action = "SELL"
        if action == "REDUCE":
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:PAPER_REDUCE_UNSUPPORTED")
        if action not in {"HOLD", "SELL"}:
            raise RuntimeError("PAPER_POSITION_REVIEW_BLOCKED:INVALID_REVIEW_ACTION")
        paper_action = "PAPER_SELL" if action == "SELL" else "PAPER_HOLD"
        closed = paper_action == "PAPER_SELL"
        decision.update({
            "paper_review": True,
            "paper_signal_id": paper_signal_id,
            "decision_id": prior_id,
            "original_snapshot_id": original_snapshot_id,
            "review_snapshot_id": review_snapshot_id,
            "review_trade_date": str(trade_date),
            "paper_observation": None,
            "paper_observation_state": "CLOSED" if closed else "OBSERVED",
            "paper_position_state": "PAPER_FLAT" if closed else "PAPER_LONG",
            "paper_action": paper_action,
            "paper_exit_reason": "T5_EXPIRY" if holding_days >= 5 else decision.get("reason") if closed else None,
            "holding_days": holding_days,
        })
        if closed:
            from xiaogu_db import update_paper_observation_state
            update_paper_observation_state(
                paper_signal_id,
                state="CLOSED",
                paper_position_state="PAPER_FLAT",
                exit_reason=str(decision.get("paper_exit_reason") or "PAPER_SELL"),
            )
        reviewed.append(decision)
    return reviewed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--snapshot-json", default="")
    parser.add_argument("--scan-dir", default="", help="This scan's observation directory; production still loads DB-verified snapshots")
    parser.add_argument("--lineage-id", default="", help="Explicit production observation identity")
    parser.add_argument("--production-run-id", default="", help="Explicit production_run_id for this observation")
    parser.add_argument("--portfolio-state", default="WATCH")
    parser.add_argument("--symbol", default="", help="Only evaluate one symbol; default evaluates every canonical snapshot")
    parser.add_argument("--mode", default="PRODUCTION", choices=list(PRODUCTION_MODES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--position-review", action="store_true")
    parser.add_argument("--decision-workers", type=int, default=None)
    args = parser.parse_args()
    mode = "DRY_RUN" if args.dry_run and args.mode == "PRODUCTION" else args.mode
    if mode == "PRODUCTION":
        from xiaogu_db import CALENDAR_UNKNOWN, TRADING_DAY, is_trading_date
        calendar_status = is_trading_date(args.date)
        if calendar_status == CALENDAR_UNKNOWN:
            _emit_json(_empty_observation_output(
                args.date, "CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE", scan_status="SCAN_BLOCKED"
            ))
            return
        if calendar_status != TRADING_DAY:
            _emit_json(_empty_observation_output(
                args.date, "NON_TRADING_DAY", scan_status="SCAN_BLOCKED"
            ))
            return
    if args.position_review:
        _emit_json({"date": args.date, "reviewed": daily_position_review(args.date)})
        return
    observation_lineage_id = str(args.lineage_id or "").strip()
    observation_run_id = str(args.production_run_id or "").strip()
    observation_source_time = ""
    if args.scan_dir:
        scan_observation = _scan_observation_from_dir(args.scan_dir)
        if scan_observation["status"] != "PASS":
            _emit_json(_empty_observation_output(
                args.date, scan_observation["reason"], scan_status="SCAN_BLOCKED"
            ))
            return
        observation_lineage_id = observation_lineage_id or str(scan_observation.get("lineage_id") or "")
        observation_run_id = observation_run_id or str(scan_observation.get("run_id") or "")
        observation_source_time = str(scan_observation.get("source_time") or "")
    if args.snapshot_json:
        if mode == "PRODUCTION":
            _emit_json(_empty_observation_output(args.date, "NO_PRODUCTION_SNAPSHOT"))
            return
        payload = json.loads(Path(args.snapshot_json).read_text(encoding="utf-8"))
        rows = payload.get("canonical_snapshots") or [payload]
    elif mode == "PRODUCTION":
        from xiaogu_db import fetch_persisted_canonical_snapshots
        try:
            rows = fetch_persisted_canonical_snapshots(
                args.date,
                lineage_id=observation_lineage_id,
                production_run_id=observation_run_id,
                decision_clock=production_now(),
                require_fresh=True,
            )
        except ValueError as exc:
            reason = str(exc)
            status = "STALE_DATA" if reason == "STALE_DATA" or reason.startswith("STALE_DATA") else "SCAN_BLOCKED"
            if (
                "CANONICAL_SNAPSHOT_AMBIGUOUS" not in reason
                and "CANONICAL_SNAPSHOT_NOT_FOUND" not in reason
                and reason != "STALE_DATA"
                and not reason.startswith("STALE_DATA")
            ):
                raise
            _emit_json(_empty_observation_output(args.date, reason, scan_status=status))
            return
        if not rows:
            _emit_json(_empty_observation_output(
                args.date, "CANONICAL_SNAPSHOT_NOT_FOUND", scan_status="SCAN_BLOCKED"
            ))
            return
    elif args.scan_dir:
        rows = _load_scan_dir_snapshots(args.scan_dir, args.date)
    else:
        payload = load_latest_snapshot_bundle(args.date)
        rows = payload.get("canonical_snapshots") or []
    if not rows:
        _emit_json(_empty_observation_output(args.date, "SNAPSHOT_NOT_FOUND"))
        return
    input_count = len(rows)
    trusted = []
    for row in rows:
        try:
            trusted.append(validate_and_build_canonical_snapshot(row, target_trade_date=args.date))
        except (TypeError, ValueError):
            continue
    try:
        canonical_rows = select_production_observation_snapshots(
            trusted,
            trade_date=args.date,
            lineage_id=observation_lineage_id,
            decision_clock=production_now() if mode == "PRODUCTION" else None,
            require_fresh=mode == "PRODUCTION",
        )
    except ValueError as exc:
        reason = str(exc)
        status = "STALE_DATA" if reason == "STALE_DATA" or reason.startswith("STALE_DATA") else "SCAN_BLOCKED"
        if "CANONICAL_SNAPSHOT_AMBIGUOUS" in reason and mode != "PRODUCTION":
            reason = "RESEARCH_AMBIGUOUS"
            status = "SCAN_BLOCKED"
        elif (
            "CANONICAL_SNAPSHOT_AMBIGUOUS" not in reason
            and "CANONICAL_SNAPSHOT_NOT_FOUND" not in reason
            and reason != "STALE_DATA"
            and not reason.startswith("STALE_DATA")
        ):
            raise
        _emit_json(_empty_observation_output(args.date, reason, scan_status=status))
        return
    canonical_count = len(canonical_rows)
    if canonical_count == 0:
        _emit_json(_empty_observation_output(args.date, "CANONICAL_SNAPSHOT_NOT_FOUND"))
        return
    if args.symbol:
        try:
            selected = select_canonical_snapshot(canonical_rows, symbol=args.symbol, trade_date=args.date)
        except ValueError as exc:
            reason = str(exc)
            if "CANONICAL_SNAPSHOT_AMBIGUOUS" in reason and mode != "PRODUCTION":
                reason = "RESEARCH_AMBIGUOUS"
            _emit_json(_empty_observation_output(args.date, reason))
            return
        canonical_rows = [selected]
    eligible_rows, universe = candidate_universe(canonical_rows)
    execution_rows, execution_audit = execution_universe(eligible_rows)
    routed_rows = [
        row for row in execution_rows
        if "L3_DEEP_CANDIDATE_FETCH" in (row.get("source_layers") or [])
    ]
    # Replay fixtures may predate source_layers; only live scanner output is
    # required to prove the L3 route before Alpha evaluation.
    routing_metadata_present = any(row.get("source_layers") for row in execution_rows)
    if routing_metadata_present:
        rows = routed_rows
    else:
        rows = execution_rows
    universe.update({
        "l2_routed_count": (
            sum(any(layer.startswith("L2_") for layer in (row.get("source_layers") or [])) for row in execution_rows)
            if routing_metadata_present else None
        ),
        "l3_count": len(rows),
        "execution_universe_count": execution_audit.get("eligible_count", 0),
        "execution_board_counts": execution_audit.get("board_counts", {}),
        "execution_rejected_count": execution_audit.get("rejected_count", 0),
        "execution_board_policy": execution_audit.get("policy"),
        "execution_board_policy_version": execution_audit.get("policy_version"),
        "execution_rejected": execution_audit.get("rejected", []),
    })
    decisions, decision_accounting = evaluate_candidate_rows(
        rows,
        portfolio_state=args.portfolio_state,
        mode=mode,
        trade_date=args.date,
        workers=args.decision_workers,
    )
    recorded = 0
    paper_observations = []
    persist_failures = []
    persist_paper = mode == "PRODUCTION" and not args.dry_run
    if persist_paper and not observation_run_id:
        _emit_json(_empty_observation_output(
            args.date, "PRODUCTION_RUN_ID_REQUIRED", scan_status="SCAN_BLOCKED"
        ))
        return
    if observation_run_id:
        for decision in decisions:
            decision["production_run_id"] = observation_run_id
    for decision in decisions:
        if persist_paper and (
            decision.get("state") in RECORDABLE_ACTIONS or decision.get("paper_observation")
        ):
            try:
                decision["ledger_path"] = str(_write_ledger_record(decision))
                recorded += 1
            except Exception as exc:
                persist_failures.append({"decision_id": decision.get("decision_id"), "error": repr(exc)})
        if persist_paper and decision.get("paper_observation"):
            try:
                paper_observations.append(_write_paper_observation(decision))
            except Exception as persist_exc:
                persist_failures.append({
                    "paper_signal_id": (decision.get("paper_observation") or {}).get("paper_signal_id"),
                    "error": repr(persist_exc),
                })
    funnel = _block_funnel(
        decisions,
        execution_rejected=int(universe.get("execution_rejected_count") or 0),
    )
    buy_allowed = sum(1 for decision in decisions if str(decision.get("buy_status") or "") == "BUY_ALLOWED")
    qualified_signal_count = sum(
        1 for decision in decisions if (decision.get("core_alpha") or {}).get("signal_qualified") is True
    )
    research_summary = _research_summary(decisions)
    if not persist_paper:
        paper_observations = [
            record
            for record in (_compact_paper_observation(decision) for decision in decisions)
            if record is not None
        ]
    paper_count = len(paper_observations)
    scan_status, scan_reason = _scan_status_from_run(
        paper_count=paper_count,
        decision_count=len(decisions),
        freshness_blocked=funnel["freshness_blocked"],
        buy_allowed=buy_allowed,
        qualified_signal_count=qualified_signal_count,
    )
    decision_clock = production_now() if mode == "PRODUCTION" else None
    source_times = sorted({str(row.get("source_time") or "") for row in canonical_rows if row.get("source_time")})
    source_time = observation_source_time or (source_times[0] if len(source_times) == 1 else "")
    source_age = snapshot_age(source_time, decision_clock) if source_time and decision_clock is not None else None
    output = {
        "date": args.date,
        "mode": mode,
        "count": len(decisions),
        "recorded": recorded,
        "scan_status": scan_status,
        "scan_reason": scan_reason,
        "production_run_id": observation_run_id or None,
        "lineage_id": observation_lineage_id or (canonical_rows[0].get("lineage_id") if canonical_rows else None),
        "source_time": source_time or None,
        "decision_time": None if decision_clock is None else decision_clock.isoformat(),
        "source_age_seconds": None if source_age is None else source_age.total_seconds(),
        "freshness_blocked": funnel["freshness_blocked"],
        "alpha_blocked": funnel["alpha_blocked"],
        "gate_blocked": funnel["gate_blocked"],
        "strategy_no_signal": funnel["strategy_no_signal"],
        "strategy_signal": funnel["strategy_signal"],
        "execution_eligibility_blocked": funnel["execution_eligibility_blocked"],
        "buy_allowed_count": buy_allowed,
        "main_board_count": (universe.get("execution_board_counts") or {}).get("MAIN_BOARD"),
        "non_main_board_blocked_count": funnel["execution_eligibility_blocked"],
        "paper_from_decisions": sum(1 for decision in decisions if decision.get("paper_observation")),
        "qualified_signal_count": qualified_signal_count,
        "research_consumed_count": research_summary["research_consumed_count"],
        "research_provenance": research_summary["research_provenance"],
        "top3_count": sum(1 for decision in decisions if (decision.get("paper_observation") or {}).get("top3_flag")),
        "top1_count": sum(1 for decision in decisions if (decision.get("paper_observation") or {}).get("top1_flag")),
        "l0_count": input_count,
        "l1_count": universe.get("eligible_count", 0),
        "l2_count": universe.get("l2_routed_count", 0),
        "l3_count": universe.get("l3_count", 0),
        "canonical_count": canonical_count,
        "feature_count": len(decisions),
        "alpha_count": len(decisions),
        "decision_count": len(decisions),
        "paper_observation_count": paper_count,
        "paper_observations": paper_observations,
        "candidate_universe": universe,
        "execution_universe_count": universe.get("execution_universe_count", 0),
        "execution_board_counts": universe.get("execution_board_counts", {}),
        "execution_board_policy": universe.get("execution_board_policy"),
        "execution_board_policy_version": universe.get("execution_board_policy_version"),
        "decision_workers": decision_accounting.get("workers"),
        "success_count": decision_accounting.get("success_count", 0),
        "blocked_count": decision_accounting.get("blocked_count", 0),
        "stale_count": decision_accounting.get("stale_count", 0),
        "error_count": decision_accounting.get("error_count", 0),
        "sample_accounting": {
            "full_universe_count": input_count,
            "l1_count": universe.get("eligible_count", 0),
            "l2_count": universe.get("l2_routed_count", 0),
            "l3_count": universe.get("l3_count", 0),
            "alpha_count": len(decisions),
            "decision_count": len(decisions),
            "canonical_count": canonical_count,
            "partial_count": 0,
            "conflict_count": sum(
                1 for item in universe.get("execution_rejected") or []
                if "BOARD_IDENTITY_CONFLICT" in (item.get("blockers") or [])
            ),
            "invalid_count": input_count - len(trusted),
            "unresolved_count": 0,
        },
        "decisions": [_public_decision(decision) for decision in decisions],
        "persist_failures": persist_failures,
    }
    if persist_failures:
        output["scan_status"] = "SCAN_BLOCKED"
        output["scan_reason"] = "PAPER_PERSISTENCE_FAILED"
    if mode == "PRODUCTION" and not args.dry_run:
        from xiaogu_forward_paper_recorder_v0_1 import write_daily_paper_memory
        output["daily_memory_path"] = write_daily_paper_memory(
            args.date, paper_observations,
            scan_status=output["scan_status"],
            scan_reason=output["scan_reason"],
            canonical_count=canonical_count,
            alpha_count=len(decisions),
            paper_observation_count=paper_count,
        )
        try:
            from xiaogu_forward_result_filler_v0_1 import refresh_paper_dataset
            output["paper_dataset"] = refresh_paper_dataset()
        except Exception as exc:
            output["paper_dataset"] = {"status": "FAILED", "error": repr(exc)}
    try:
        _emit_json(output)
    except (TypeError, ValueError) as exc:
        output["scan_status"] = "SCAN_BLOCKED"
        output["scan_reason"] = f"STDOUT_SERIALIZE_FAILED:{type(exc).__name__}"
        output["decisions"] = []
        output["paper_observations"] = [
            {
                "paper_signal_id": item.get("paper_signal_id"),
                "decision_id": item.get("decision_id"),
                "symbol": item.get("symbol"),
                "rank": item.get("rank"),
            }
            for item in paper_observations
            if isinstance(item, dict)
        ]
        _emit_json(output)
        raise SystemExit(1)
    if persist_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
