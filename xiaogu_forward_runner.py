#!/usr/bin/env python3
"""Production orchestration: canonical snapshot to one portfolio decision."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict

from xiaogu_forward_bundle_io import load_latest_snapshot_bundle
from xiaogu_forward_eligibility import candidate_universe
from xiaogu_forward_snapshot import (
    assert_production_provenance,
    production_decision_clock,
    select_canonical_snapshot,
    select_unique_canonical_snapshots,
    validate_and_build_canonical_snapshot,
)
from xiaogu_portfolio_decision import evaluate_candidate_bundle

BASE = Path(__file__).resolve().parent
RECORDABLE_ACTIONS = {"BUY", "HOLD", "REDUCE", "SELL"}


PRODUCTION_MODES = ("PRODUCTION", "REPLAY", "DRY_RUN", "RESEARCH")


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
    if mode == "PRODUCTION":
        from xiaogu_db import fetch_position_state, verify_persisted_snapshot
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
        db_position_state = fetch_position_state(str(trusted.get("symbol") or ""))
        if db_position_state is None:
            raise RuntimeError("POSITION_STATE_UNAVAILABLE")
        if position_state is not None and position_state != db_position_state:
            raise RuntimeError("POSITION_STATE_CONFLICT")
        position_state = db_position_state
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


def _write_ledger_record(decision: Dict[str, Any]) -> Path:
    from xiaogu_forward_paper_recorder_v0_1 import append_production_decision
    path, _record = append_production_decision(decision)
    return path


def _write_paper_observation(decision: Dict[str, Any]) -> Dict[str, Any]:
    from xiaogu_forward_paper_recorder_v0_1 import append_paper_observation
    _path, record = append_paper_observation(decision)
    return record


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
        fetch_decision_snapshot,
        fetch_open_positions,
        fetch_position_outcome,
        get_current_position_review_snapshot,
    )

    positions = fetch_open_positions()
    reviewed = []
    for prior in positions:
        symbol = str(prior.get("symbol") or "").zfill(6)
        if str(prior.get("trade_date") or prior.get("date") or "") >= str(trade_date):
            continue
        prior_id = str(prior.get("decision_id") or "")
        if not prior_id:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:DECISION_ID_UNAVAILABLE")
        original_snapshot = fetch_decision_snapshot(prior_id)
        original_snapshot_id = str(original_snapshot.get("snapshot_id") or "")
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
        decision["decision_id"] = prior_id
        decision["original_snapshot_id"] = original_snapshot_id
        decision["review_snapshot_id"] = review_snapshot_id
        decision["review_trade_date"] = str(trade_date)
        decision["paper_observation"] = None
        decision.pop("paper_signal_id", None)
        decision["holding_days"] = holding_days
        decision["prior_decision_id"] = prior_id
        new_action = decision.get("action") or decision.get("state")
        if new_action != previous_action or holding_days >= 5 or decision.get("trade_status") == "CLOSED":
            _write_ledger_record(decision)
            reviewed.append(decision)
    reviewed.extend(daily_paper_position_review(trade_date))
    return reviewed


def daily_paper_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Review only explicit PAPER_LONG positions bound to their original snapshot."""
    from xiaogu_db import fetch_decision_snapshot, fetch_open_paper_positions, fetch_position_outcome

    positions = fetch_open_paper_positions()
    reviewed = []
    for prior in positions:
        prior_id = str(prior.get("decision_id") or "")
        if not prior_id:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
        snapshot = fetch_decision_snapshot(prior_id)
        result = fetch_position_outcome(prior_id)
        holding_days = _holding_days_since(
            prior.get("trade_date") or prior.get("date"),
            trade_date,
        )
        decision = run_production_decision(
            snapshot,
            portfolio_state="HOLD",
            account={
                "decision_id": prior_id,
                "holding_days": holding_days,
                "profit_window_hit": result.get("profit_window") is True,
                "max_profit": result.get("max_daily_bar_profit_opportunity_5d"),
                "mae": result.get("max_mae_5d"),
                "mfe": result.get("future_5d_mfe"),
            },
            mode="REPLAY",
            position_state="LONG",
            previous_action="HOLD",
        )
        action = decision.get("action") or "HOLD"
        decision.update({
            "paper_review": True,
            "paper_signal_id": prior.get("paper_signal_id"),
            "paper_observation_state": "CLOSED" if action in {"SELL", "REDUCE"} or holding_days >= 5 else "OBSERVED",
            "paper_position_state": "PAPER_FLAT" if action in {"SELL", "REDUCE"} or holding_days >= 5 else "PAPER_LONG",
            "paper_action": "PAPER_SELL" if action == "SELL" or holding_days >= 5 else "PAPER_REDUCE" if action == "REDUCE" else "PAPER_HOLD",
            "paper_exit_reason": "T5_EXPIRY" if holding_days >= 5 else decision.get("reason") if action in {"SELL", "REDUCE"} else None,
            "holding_days": holding_days,
        })
        reviewed.append(decision)
    return reviewed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--snapshot-json", default="")
    parser.add_argument("--portfolio-state", default="WATCH")
    parser.add_argument("--symbol", default="", help="Only evaluate one symbol; default evaluates every canonical snapshot")
    parser.add_argument("--mode", default="PRODUCTION", choices=list(PRODUCTION_MODES))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--position-review", action="store_true")
    args = parser.parse_args()
    mode = "DRY_RUN" if args.dry_run and args.mode == "PRODUCTION" else args.mode
    if mode == "PRODUCTION":
        from xiaogu_db import CALENDAR_UNKNOWN, TRADING_DAY, is_trading_date
        calendar_status = is_trading_date(args.date)
        if calendar_status == CALENDAR_UNKNOWN:
            print(json.dumps(_empty_observation_output(
                args.date, "CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE", scan_status="SCAN_BLOCKED"
            ), ensure_ascii=False, default=str))
            return
        if calendar_status != TRADING_DAY:
            print(json.dumps(_empty_observation_output(
                args.date, "NON_TRADING_DAY", scan_status="SCAN_BLOCKED"
            ), ensure_ascii=False, default=str))
            return
    if args.position_review:
        print(json.dumps({"date": args.date, "reviewed": daily_position_review(args.date)}, ensure_ascii=False, default=str))
        return
    if args.snapshot_json:
        if mode == "PRODUCTION":
            print(json.dumps(_empty_observation_output(args.date, "NO_PRODUCTION_SNAPSHOT"), ensure_ascii=False, default=str))
            return
        payload = json.loads(Path(args.snapshot_json).read_text(encoding="utf-8"))
        rows = payload.get("canonical_snapshots") or [payload]
    elif mode == "PRODUCTION":
        from xiaogu_db import fetch_persisted_canonical_snapshots
        try:
            rows = fetch_persisted_canonical_snapshots(args.date)
        except ValueError as exc:
            reason = str(exc)
            if "CANONICAL_SNAPSHOT_AMBIGUOUS" not in reason and "CANONICAL_SNAPSHOT_NOT_FOUND" not in reason:
                raise
            print(json.dumps(_empty_observation_output(args.date, reason), ensure_ascii=False, default=str))
            return
        if not rows:
            print(json.dumps(_empty_observation_output(args.date, "CANONICAL_SNAPSHOT_NOT_FOUND"), ensure_ascii=False, default=str))
            return
    else:
        payload = load_latest_snapshot_bundle(args.date)
        rows = payload.get("canonical_snapshots") or []
    if not rows:
        print(json.dumps(_empty_observation_output(args.date, "SNAPSHOT_NOT_FOUND"), ensure_ascii=False, default=str))
        return
    input_count = len(rows)
    trusted = []
    for row in rows:
        try:
            trusted.append(validate_and_build_canonical_snapshot(row, target_trade_date=args.date))
        except (TypeError, ValueError):
            continue
    try:
        canonical_rows = select_unique_canonical_snapshots(trusted, trade_date=args.date)
    except ValueError as exc:
        reason = str(exc)
        if "CANONICAL_SNAPSHOT_AMBIGUOUS" in reason and mode != "PRODUCTION":
            reason = "RESEARCH_AMBIGUOUS"
        elif "CANONICAL_SNAPSHOT_AMBIGUOUS" not in reason and "CANONICAL_SNAPSHOT_NOT_FOUND" not in reason:
            raise
        print(json.dumps(_empty_observation_output(args.date, reason), ensure_ascii=False, default=str))
        return
    canonical_count = len(canonical_rows)
    if canonical_count == 0:
        print(json.dumps(
            _empty_observation_output(args.date, "CANONICAL_SNAPSHOT_NOT_FOUND"),
            ensure_ascii=False,
            default=str,
        ))
        return
    if args.symbol:
        try:
            selected = select_canonical_snapshot(canonical_rows, symbol=args.symbol, trade_date=args.date)
        except ValueError as exc:
            reason = str(exc)
            if "CANONICAL_SNAPSHOT_AMBIGUOUS" in reason and mode != "PRODUCTION":
                reason = "RESEARCH_AMBIGUOUS"
            print(json.dumps(_empty_observation_output(args.date, reason), ensure_ascii=False, default=str))
            return
        canonical_rows = [selected]
    eligible_rows, universe = candidate_universe(canonical_rows)
    routed_rows = [
        row for row in eligible_rows
        if "L3_DEEP_CANDIDATE_FETCH" in (row.get("source_layers") or [])
    ]
    # Replay fixtures may predate source_layers; only live scanner output is
    # required to prove the L3 route before Alpha evaluation.
    routing_metadata_present = any(row.get("source_layers") for row in eligible_rows)
    if routing_metadata_present:
        rows = routed_rows
    else:
        rows = eligible_rows
    universe.update({
        "l2_routed_count": (
            sum(any(layer.startswith("L2_") for layer in (row.get("source_layers") or [])) for row in eligible_rows)
            if routing_metadata_present else None
        ),
        "l3_count": len(rows),
    })
    decisions = []
    recorded = 0
    paper_observations = []
    for row in rows:
        row = dict(row)
        row.setdefault("trade_date", args.date)
        decision = run_production_decision(
            row,
            portfolio_state=args.portfolio_state,
            mode=mode,
            trade_date=args.date,
        )
        if mode == "PRODUCTION" and not args.dry_run and (
            decision["state"] in RECORDABLE_ACTIONS or decision.get("paper_observation")
        ):
            decision["ledger_path"] = str(_write_ledger_record(decision))
            recorded += 1
        if mode == "PRODUCTION" and not args.dry_run and decision.get("paper_observation"):
            decision["paper_observation_record"] = _write_paper_observation(decision)
            paper_observations.append(decision["paper_observation_record"])
        decisions.append(decision)
    output = {
        "date": args.date,
        "mode": mode,
        "count": len(decisions),
        "recorded": recorded,
        "scan_status": "SIGNAL_AVAILABLE" if paper_observations else "NO_SIGNAL",
        "scan_reason": "PAPER_OBSERVATION_RECORDED" if paper_observations else "NO_PAPER_OBSERVATION",
        "l0_count": input_count,
        "l1_count": universe.get("eligible_count", 0),
        "l2_count": universe.get("l2_routed_count", 0),
        "l3_count": universe.get("l3_count", 0),
        "canonical_count": canonical_count,
        "feature_count": len(decisions),
        "alpha_count": len(decisions),
        "decision_count": len(decisions),
        "paper_observation_count": len(paper_observations),
        "paper_observations": paper_observations,
        "candidate_universe": universe,
        "sample_accounting": {
            "full_universe_count": input_count,
            "l1_count": universe.get("eligible_count", 0),
            "l2_count": universe.get("l2_routed_count", 0),
            "l3_count": universe.get("l3_count", 0),
            "alpha_count": len(decisions),
            "decision_count": len(decisions),
            "canonical_count": canonical_count,
            "partial_count": 0,
            "conflict_count": 0,
            "invalid_count": input_count - len(trusted),
            "unresolved_count": 0,
        },
        "decisions": decisions,
    }
    if mode == "PRODUCTION" and not args.dry_run:
        from xiaogu_forward_paper_recorder_v0_1 import write_daily_paper_memory
        output["daily_memory_path"] = write_daily_paper_memory(
            args.date, paper_observations,
            scan_status=output["scan_status"],
            scan_reason=output["scan_reason"],
            canonical_count=canonical_count,
            alpha_count=len(decisions),
            paper_observation_count=len(paper_observations),
        )
        try:
            from xiaogu_forward_result_filler_v0_1 import refresh_paper_dataset
            output["paper_dataset"] = refresh_paper_dataset()
        except Exception as exc:
            output["paper_dataset"] = {"status": "FAILED", "error": repr(exc)}
    print(json.dumps(output, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
