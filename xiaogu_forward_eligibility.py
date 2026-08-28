"""Hard operational constraints only; it contains no alpha or ranking logic."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def cheap_eligibility_blockers(row: Dict[str, Any]) -> List[str]:
    """Check only observable market and operational prerequisites."""
    symbol = str(row.get("symbol") or row.get("code") or "").strip()
    try:
        price = float(row.get("price") or row.get("close") or row.get("f2") or 0)
    except (TypeError, ValueError):
        price = 0.0
    try:
        volume = float(row.get("volume") or row.get("f5") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    try:
        amount = float(row.get("amount") or row.get("f6") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    blockers = []
    if not symbol:
        blockers.append("INVALID_SYMBOL")
    if price <= 0:
        blockers.append("INVALID_PRICE")
    if volume <= 0 or amount <= 0:
        blockers.append("INCOMPLETE_MARKET_DATA")
    if row.get("halted") or row.get("is_suspended") or row.get("in_halted"):
        blockers.append("HALTED")
    if row.get("regulatory_hard_block") or row.get("risk_hard_block"):
        blockers.append("REGULATORY_HARD_RISK")
    if row.get("buyable") is False or row.get("sealed_limit_up"):
        blockers.append("UNBUYABLE")
    return list(dict.fromkeys(blockers))


def candidate_universe(snapshots: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return analyzable snapshots without measuring or ranking them."""
    eligible = []
    rejected = []
    for snapshot in snapshots:
        blockers = cheap_eligibility_blockers(snapshot)
        if blockers:
            rejected.append({"symbol": snapshot.get("symbol"), "blockers": blockers})
        else:
            eligible.append(snapshot)
    return eligible, {
        "input_count": len(snapshots),
        "eligible_count": len(eligible),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "selection": False,
        "ranking": False,
        "alpha": False,
    }


def paper_pick_buyability_block_reason(row: Dict[str, Any], account: Dict[str, Any] | None = None) -> str:
    account = account or {}
    symbol = str(row.get("symbol") or row.get("code") or "").strip()
    try:
        price = float(row.get("price") or row.get("close") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if not symbol:
        return "INVALID_SYMBOL"
    if price <= 0:
        return "INVALID_PRICE"
    if row.get("halted") or row.get("is_suspended"):
        return "HALTED"
    if row.get("regulatory_hard_block") or row.get("risk_hard_block"):
        return "REGULATORY_HARD_RISK"
    if row.get("buyable") is False or row.get("sealed_limit_up"):
        return "UNBUYABLE"
    if account.get("available_cash") is not None and float(account["available_cash"]) < price * 100:
        return "ACCOUNT_CONSTRAINT"
    if row.get("liquidity_score") is not None and float(row["liquidity_score"]) <= 0:
        return "SEVERE_LIQUIDITY_ISSUE"
    return ""


def eligibility_blockers(
    snapshot: Dict[str, Any],
    *,
    account: Dict[str, Any] | None = None,
    max_staleness_minutes: int = 120,
    as_of: datetime | None = None,
) -> List[str]:
    blockers = [paper_pick_buyability_block_reason(snapshot, account)]
    source_time = str(snapshot.get("source_time") or "")
    if not source_time:
        blockers.append("MISSING_DATA")
    else:
        try:
            asof = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if asof.tzinfo is None:
                asof = asof.replace(tzinfo=timezone.utc)
            clock = as_of or datetime.now(timezone.utc)
            if clock.tzinfo is None:
                clock = clock.replace(tzinfo=timezone.utc)
            age = (clock.astimezone(timezone.utc) - asof.astimezone(timezone.utc)).total_seconds() / 60
            if age > max_staleness_minutes:
                blockers.append("STALE_DATA")
        except ValueError:
            blockers.append("STALE_DATA")
    return [reason for reason in blockers if reason]
