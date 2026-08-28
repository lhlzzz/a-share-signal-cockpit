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
from xiaogu_portfolio_decision import evaluate_candidate_bundle

BASE = Path(__file__).resolve().parent
RECORDABLE_ACTIONS = {"BUY", "HOLD", "REDUCE", "SELL"}


def run_production_decision(
    snapshot: Dict[str, Any],
    *,
    portfolio_state: str = "WATCH",
    account: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    source_time = str(snapshot.get("source_time") or snapshot.get("as_of") or "")
    as_of = None
    if source_time:
        try:
            as_of = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        except ValueError:
            as_of = None
    return evaluate_candidate_bundle(
        snapshot, portfolio_state=portfolio_state, account=account, as_of=as_of,
    )


def _write_ledger_record(decision: Dict[str, Any]) -> Path:
    from xiaogu_forward_paper_recorder_v0_1 import append_production_decision
    path, _record = append_production_decision(decision)
    return path


def daily_position_review(trade_date: str) -> list[Dict[str, Any]]:
    """Re-evaluate active positions through the sole Decision Owner."""
    from xiaogu_forward_paper_recorder_v0_1 import FORWARD_LEDGER, append_production_decision
    from xiaogu_utils import decision_record_id, load_jsonl

    records = load_jsonl(FORWARD_LEDGER)
    latest = {}
    results = {}
    for record in records:
        if record.get("record_type") in {"DECISION", "CORRECTION"} and record.get("symbol"):
            latest[str(record["symbol"])] = record
        if record.get("record_type") == "RESULT":
            results[str(record.get("decision_id") or "")] = record
    bundle = load_latest_snapshot_bundle(trade_date)
    snapshots = {str(row.get("symbol")): row for row in bundle.get("canonical_snapshots") or []}
    reviewed = []
    for symbol, prior in latest.items():
        if prior.get("decision") not in {"BUY", "HOLD"} or symbol not in snapshots:
            continue
        if str(prior.get("date") or "") >= str(trade_date):
            continue
        if int(prior.get("renewal_count") or 0) >= 1:
            continue
        prior_id = str(prior.get("id") or prior.get("decision_id") or decision_record_id(prior))
        result = results.get(prior_id, {})
        try:
            start = date.fromisoformat(str(prior.get("date")))
            end = date.fromisoformat(str(trade_date))
            holding_days = sum(
                (start.fromordinal(day).weekday() < 5)
                for day in range(start.toordinal() + 1, end.toordinal() + 1)
            )
        except (TypeError, ValueError):
            holding_days = 0
        account = {
            "profit_window_hit": result.get("profit_window") is True,
            "holding_days": holding_days,
        }
        decision = run_production_decision(
            snapshots[symbol], portfolio_state=str(prior["decision"]), account=account,
        )
        decision["holding_days"] = holding_days
        decision["renewal_count"] = int(prior.get("renewal_count") or 0) + (1 if holding_days >= 5 else 0)
        if decision["state"] != prior.get("decision") or holding_days >= 5:
            _write_ledger_record(decision)
            reviewed.append(decision)
    return reviewed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--snapshot-json", default="")
    parser.add_argument("--portfolio-state", default="WATCH")
    parser.add_argument("--symbol", default="", help="Only evaluate one symbol; default evaluates every canonical snapshot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--position-review", action="store_true")
    args = parser.parse_args()
    if args.position_review:
        print(json.dumps({"date": args.date, "reviewed": daily_position_review(args.date)}, ensure_ascii=False, default=str))
        return
    if args.snapshot_json:
        payload = json.loads(Path(args.snapshot_json).read_text(encoding="utf-8"))
        rows = payload.get("canonical_snapshots") or [payload]
    else:
        payload = load_latest_snapshot_bundle(args.date)
        rows = payload.get("canonical_snapshots") or []
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
        decision = run_production_decision(row, portfolio_state=args.portfolio_state)
        if not args.dry_run and decision["state"] in RECORDABLE_ACTIONS:
            decision["ledger_path"] = str(_write_ledger_record(decision))
            recorded += 1
        decisions.append(decision)
    print(json.dumps({
        "date": args.date,
        "count": len(decisions),
        "recorded": recorded,
        "candidate_universe": universe,
        "decisions": decisions,
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
