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
        from xiaogu_db import verify_persisted_snapshot
        db_verified = verify_persisted_snapshot(
            snapshot_id=str(trusted.get("snapshot_id") or ""),
            lineage_id=str(trusted.get("lineage_id") or ""),
            trade_date=trade_date or str(trusted.get("trade_date") or ""),
            source=str(trusted.get("source") or ""),
            source_time=str(trusted.get("source_time") or ""),
        )
        clock = production_decision_clock(decision_clock)
        assert_production_provenance(
            trusted,
            trade_date=trade_date or str(trusted.get("trade_date") or ""),
            decision_time=clock,
            persisted=bool(db_verified),
        )
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


def daily_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Re-evaluate active positions through the sole Decision Owner."""
    from xiaogu_db import fetch_open_positions, fetch_persisted_canonical_snapshots, fetch_position_outcome

    positions = fetch_open_positions()
    db_rows = fetch_persisted_canonical_snapshots(trade_date)
    rows = list(db_rows or [])
    reviewed = []
    for prior in positions:
        symbol = str(prior.get("symbol") or "").zfill(6)
        if str(prior.get("trade_date") or prior.get("date") or "") >= str(trade_date):
            continue
        snapshot = select_canonical_snapshot(rows, symbol=symbol, trade_date=trade_date)
        if snapshot is None:
            continue
        prior_id = str(prior.get("decision_id") or prior.get("id") or "")
        result = fetch_position_outcome(prior_id, symbol=symbol)
        try:
            start = date.fromisoformat(str(prior.get("trade_date") or prior.get("date")))
            end = date.fromisoformat(str(trade_date))
            holding_days = sum(
                (start.fromordinal(day).weekday() < 5)
                for day in range(start.toordinal() + 1, end.toordinal() + 1)
            )
        except (TypeError, ValueError):
            holding_days = 0
        previous_action = str(prior.get("action") or prior.get("previous_action") or prior.get("decision") or "HOLD")
        position_state = str(prior.get("position_state") or ("LONG" if previous_action in RECORDABLE_ACTIONS and previous_action != "SELL" else "FLAT"))
        account = {
            "decision_id": prior_id,
            "position_state": position_state,
            "previous_action": previous_action,
            "holding_days": holding_days,
            "profit_window_hit": result.get("status") != "OUTCOME_NOT_BOUND" and result.get("profit_window") is True,
            "max_profit": result.get("max_daily_bar_profit_opportunity_5d") or result.get("max_profit"),
            "mae": result.get("max_mae_5d") or result.get("mae_5d"),
            "mfe": result.get("mfe_5d"),
        }
        decision = run_production_decision(
            snapshot,
            portfolio_state=previous_action if previous_action in {"WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL"} else "HOLD",
            account=account,
            mode="PRODUCTION",
            trade_date=trade_date,
            position_state=position_state,
            previous_action=previous_action,
        )
        decision["holding_days"] = holding_days
        decision["prior_decision_id"] = prior_id
        new_action = decision.get("action") or decision.get("state")
        if new_action != previous_action or holding_days >= 5 or decision.get("trade_status") == "CLOSED":
            _write_ledger_record(decision)
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
    if args.position_review:
        print(json.dumps({"date": args.date, "reviewed": daily_position_review(args.date)}, ensure_ascii=False, default=str))
        return
    if args.snapshot_json:
        if mode == "PRODUCTION":
            print(json.dumps({"state": "WATCH", "reason": "NO_PRODUCTION_SNAPSHOT", "date": args.date}))
            return
        payload = json.loads(Path(args.snapshot_json).read_text(encoding="utf-8"))
        rows = payload.get("canonical_snapshots") or [payload]
    elif mode == "PRODUCTION":
        from xiaogu_db import fetch_persisted_canonical_snapshots
        rows = fetch_persisted_canonical_snapshots(args.date)
        if not rows:
            print(json.dumps({"state": "WATCH", "reason": "NO_PRODUCTION_SNAPSHOT", "date": args.date}))
            return
    else:
        payload = load_latest_snapshot_bundle(args.date)
        rows = payload.get("canonical_snapshots") or []
    if not rows:
        print(json.dumps({"state": "WATCH", "reason": "SNAPSHOT_NOT_FOUND", "date": args.date}))
        return
    trusted = []
    for row in rows:
        try:
            trusted.append(validate_and_build_canonical_snapshot(row, target_trade_date=args.date))
        except (TypeError, ValueError):
            continue
    rows = select_unique_canonical_snapshots(trusted, trade_date=args.date)
    if args.symbol:
        selected = select_canonical_snapshot(rows, symbol=args.symbol, trade_date=args.date)
        rows = [selected] if selected is not None else []
    rows, universe = candidate_universe(rows)
    decisions = []
    recorded = 0
    for row in rows:
        row = dict(row)
        row.setdefault("trade_date", args.date)
        decision = run_production_decision(
            row,
            portfolio_state=args.portfolio_state,
            mode=mode,
            trade_date=args.date,
        )
        if mode == "PRODUCTION" and not args.dry_run and decision["state"] in RECORDABLE_ACTIONS:
            decision["ledger_path"] = str(_write_ledger_record(decision))
            recorded += 1
        decisions.append(decision)
    print(json.dumps({
        "date": args.date,
        "mode": mode,
        "count": len(decisions),
        "recorded": recorded,
        "candidate_universe": universe,
        "decisions": decisions,
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
