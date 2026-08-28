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
from xiaogu_forward_snapshot import assert_production_provenance, validate_and_build_canonical_snapshot
from xiaogu_portfolio_decision import evaluate_candidate_bundle

BASE = Path(__file__).resolve().parent
RECORDABLE_ACTIONS = {"BUY", "HOLD", "REDUCE", "SELL"}


PRODUCTION_MODES = ("PRODUCTION", "REPLAY", "DRY_RUN", "RESEARCH")


def run_production_decision(
    snapshot: Dict[str, Any],
    *,
    portfolio_state: str = "WATCH",
    account: Dict[str, Any] | None = None,
    mode: str = "PRODUCTION",
    persisted: bool = False,
    trade_date: str = "",
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
    source_time = str(trusted.get("source_time") or trusted.get("as_of") or "")
    as_of = None
    if source_time:
        try:
            as_of = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        except ValueError:
            as_of = None
    if mode == "PRODUCTION":
        assert_production_provenance(
            trusted,
            trade_date=trade_date or str(trusted.get("trade_date") or ""),
            decision_time=as_of,
            persisted=persisted,
        )
    return evaluate_candidate_bundle(
        trusted, portfolio_state=portfolio_state, account=account, as_of=as_of,
    )


def _write_ledger_record(decision: Dict[str, Any]) -> Path:
    from xiaogu_forward_paper_recorder_v0_1 import append_production_decision
    path, _record = append_production_decision(decision)
    return path


def daily_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Re-evaluate active positions through the sole Decision Owner."""
    from xiaogu_db import fetch_open_positions, fetch_position_outcome

    positions = fetch_open_positions()
    bundle = load_latest_snapshot_bundle(trade_date)
    snapshots = {str(row.get("symbol")).zfill(6): row for row in bundle.get("canonical_snapshots") or []}
    reviewed = []
    for prior in positions:
        symbol = str(prior.get("symbol") or "").zfill(6)
        if symbol not in snapshots:
            continue
        if str(prior.get("trade_date") or prior.get("date") or "") >= str(trade_date):
            continue
        prior_id = str(prior.get("decision_id") or prior.get("id") or "")
        result = fetch_position_outcome(symbol, prior_id)
        try:
            start = date.fromisoformat(str(prior.get("trade_date") or prior.get("date")))
            end = date.fromisoformat(str(trade_date))
            holding_days = sum(
                (start.fromordinal(day).weekday() < 5)
                for day in range(start.toordinal() + 1, end.toordinal() + 1)
            )
        except (TypeError, ValueError):
            holding_days = 0
        previous_action = str(prior.get("action") or prior.get("state") or prior.get("decision") or "HOLD")
        account = {
            "profit_window_hit": result.get("profit_window") is True,
            "holding_days": holding_days,
        }
        decision = run_production_decision(
            snapshots[symbol],
            portfolio_state=previous_action,
            account=account,
            mode="PRODUCTION",
            persisted=True,
            trade_date=trade_date,
        )
        decision["holding_days"] = holding_days
        if decision["state"] != previous_action or holding_days >= 5:
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
    persisted = False
    if args.snapshot_json:
        if mode == "PRODUCTION":
            print(json.dumps({"state": "WATCH", "reason": "NO_PRODUCTION_SNAPSHOT", "date": args.date}))
            return
        payload = json.loads(Path(args.snapshot_json).read_text(encoding="utf-8"))
        rows = payload.get("canonical_snapshots") or [payload]
    else:
        payload = load_latest_snapshot_bundle(args.date)
        rows = payload.get("canonical_snapshots") or []
        persisted = bool(rows)
    if not rows:
        print(json.dumps({"state": "WATCH", "reason": "SNAPSHOT_NOT_FOUND", "date": args.date}))
        return
    if args.symbol:
        rows = [row for row in rows if str(row.get("symbol") or "").zfill(6) == args.symbol.zfill(6)]
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
            persisted=persisted,
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
