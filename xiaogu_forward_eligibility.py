"""Hard operational constraints only; it contains no alpha or ranking logic."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


EXECUTION_BOARD_POLICY = "MAIN_BOARD_ONLY"
EXECUTION_BOARD_POLICY_VERSION = "main_board_only_v1"
BOARD_MAIN = "MAIN_BOARD"
BOARD_STAR = "STAR"
BOARD_CHINEXT = "CHINEXT"
BOARD_BSE = "BSE"
BOARD_OTHER = "OTHER"
BOARD_UNKNOWN = "UNKNOWN"
EXECUTION_BOARDS = (
    BOARD_MAIN,
    BOARD_STAR,
    BOARD_CHINEXT,
    BOARD_BSE,
    BOARD_OTHER,
    BOARD_UNKNOWN,
)
_F13_MARKETS = {0: "SZ", 1: "SH", 2: "BJ"}
_MARKET_ALIASES = {
    "SH": "SH",
    "SZ": "SZ",
    "BJ": "BJ",
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
    "1": "SH",
    "0": "SZ",
    "2": "BJ",
}


def _merged_source(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("raw")
    merged = dict(raw) if isinstance(raw, dict) else {}
    for key, value in row.items():
        if key == "raw" or value in (None, "", "-"):
            continue
        if value == {} and key == "market":
            continue
        merged[key] = value
    return merged


def _normalized_symbol(row: Dict[str, Any]) -> str:
    merged = _merged_source(row)
    raw = str(merged.get("symbol") or merged.get("code") or merged.get("f12") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def official_range_board_hint(symbol: str) -> str | None:
    """SSE/SZSE/BSE published code ranges. Diagnostic only, never sole production truth."""
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if not digits:
        return None
    code = digits.zfill(6)[-6:]
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("688", "689")):
        return BOARD_STAR
    if code.startswith(("300", "301", "302")):
        return BOARD_CHINEXT
    if code.startswith("92") or code[0] in {"4", "8"}:
        return BOARD_BSE
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return BOARD_MAIN
    return None


def _source_market(row: Dict[str, Any]) -> str | None:
    merged = _merged_source(row)
    if merged.get("f13") not in (None, "", "-"):
        try:
            return _F13_MARKETS.get(int(float(str(merged["f13"]))))
        except (TypeError, ValueError):
            pass
    market = merged.get("market")
    if isinstance(market, str) and market.strip():
        return _MARKET_ALIASES.get(market.strip().upper())
    return None


def _expected_markets(symbol: str, hint: str | None) -> set[str]:
    if hint == BOARD_STAR:
        return {"SH"}
    if hint == BOARD_CHINEXT:
        return {"SZ"}
    if hint == BOARD_BSE:
        return {"SZ", "BJ"}
    if hint == BOARD_MAIN:
        if symbol.startswith(("600", "601", "603", "605")):
            return {"SH"}
        if symbol.startswith(("000", "001", "002", "003")):
            return {"SZ"}
        return {"SH", "SZ"}
    return set()


def classify_execution_board(row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify board from Eastmoney source metadata, with code-range sanity only."""
    merged = _merged_source(row)
    symbol = _normalized_symbol(row)
    source_market = _source_market(row)
    hint = official_range_board_hint(symbol)
    security_class = None
    for key in ("security_class", "f1"):
        if merged.get(key) not in (None, "", "-"):
            try:
                security_class = int(float(str(merged.get(key))))
            except (TypeError, ValueError):
                security_class = None
            break
    conflict = False
    board = BOARD_UNKNOWN
    reason = "NOT_EXECUTION_ELIGIBLE"
    if not symbol:
        board = BOARD_UNKNOWN
        reason = "INVALID_SYMBOL"
    elif security_class is not None and security_class != 2:
        board = BOARD_OTHER
        reason = "NOT_EXECUTION_ELIGIBLE"
    elif source_market is None:
        board = BOARD_UNKNOWN
        reason = "NOT_EXECUTION_ELIGIBLE"
    elif hint is None:
        board = BOARD_OTHER
        reason = "NOT_EXECUTION_ELIGIBLE"
    else:
        expected = _expected_markets(symbol, hint)
        if expected and source_market not in expected:
            conflict = True
            board = BOARD_UNKNOWN
            reason = "BOARD_IDENTITY_CONFLICT"
        else:
            board = hint
            reason = "" if board == BOARD_MAIN else "NOT_EXECUTION_ELIGIBLE"
    board_allowed = board == BOARD_MAIN and not conflict
    return {
        "symbol": symbol,
        "board": board,
        "source_market": source_market,
        "range_hint": hint,
        "security_class": security_class,
        "conflict": conflict,
        "board_allowed": board_allowed,
        "execution_eligible": board_allowed,
        "reason": reason,
        "policy": EXECUTION_BOARD_POLICY,
        "policy_version": EXECUTION_BOARD_POLICY_VERSION,
    }


def execution_board_blocker(row: Dict[str, Any]) -> str:
    info = classify_execution_board(row)
    if info["execution_eligible"]:
        return ""
    return str(info.get("reason") or "NOT_EXECUTION_ELIGIBLE")


def cheap_eligibility_blockers(row: Dict[str, Any]) -> List[str]:
    """Check only observable market and operational prerequisites."""
    symbol = str(row.get("symbol") or row.get("code") or row.get("f12") or "").strip()
    def _num(*keys):
        for key in keys:
            value = row.get(key)
            if value in (None, "", "-"):
                continue
            try:
                return float(str(value).replace(",", "").replace("%", ""))
            except (TypeError, ValueError):
                return None
        return None
    price = _num("price", "close", "f2")
    volume = _num("volume", "f5")
    amount = _num("amount", "f6")
    blockers = []
    if not symbol:
        blockers.append("INVALID_SYMBOL")
    if price is None or price <= 0:
        blockers.append("INVALID_PRICE")
    if volume is None or amount is None or volume <= 0 or amount <= 0:
        blockers.append("INCOMPLETE_MARKET_DATA")
    if row.get("halted") or row.get("is_suspended") or row.get("in_halted"):
        blockers.append("HALTED")
    if row.get("trade_status") == "HALTED" or row.get("universe_state") in {"HALTED", "DELISTED"}:
        blockers.append("HALTED")
    if row.get("regulatory_hard_block") or row.get("risk_hard_block"):
        blockers.append("REGULATORY_HARD_RISK")
    if row.get("buyable") is False or row.get("sealed_limit_up"):
        blockers.append("UNBUYABLE")
    try:
        liquidity = row.get("liquidity_score")
        if liquidity is not None and float(liquidity) <= 0:
            blockers.append("SEVERE_LIQUIDITY_ISSUE")
    except (TypeError, ValueError):
        blockers.append("SEVERE_LIQUIDITY_ISSUE")
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


def execution_universe(snapshots: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """MAIN_BOARD intersected with existing L1 operational eligibility."""
    eligible = []
    rejected = []
    board_counts = {board: 0 for board in EXECUTION_BOARDS}
    for snapshot in snapshots:
        info = classify_execution_board(snapshot)
        board_counts[info["board"]] = board_counts.get(info["board"], 0) + 1
        blockers = list(cheap_eligibility_blockers(snapshot))
        board_blocker = execution_board_blocker(snapshot)
        if board_blocker:
            blockers.append(board_blocker)
        blockers = list(dict.fromkeys(reason for reason in blockers if reason))
        if blockers:
            rejected.append({
                "symbol": snapshot.get("symbol") or info["symbol"],
                "board": info["board"],
                "board_allowed": info["board_allowed"],
                "blockers": blockers,
                "reason": blockers[0],
            })
        else:
            eligible.append(snapshot)
    return eligible, {
        "input_count": len(snapshots),
        "eligible_count": len(eligible),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "board_counts": board_counts,
        "policy": EXECUTION_BOARD_POLICY,
        "policy_version": EXECUTION_BOARD_POLICY_VERSION,
        "selection": False,
        "ranking": False,
        "alpha": False,
    }


def execution_block_reason(row: Dict[str, Any], account: Dict[str, Any] | None = None) -> str:
    account = account or {}
    symbol = str(row.get("symbol") or row.get("code") or row.get("f12") or "").strip()
    price = None
    for key in ("price", "close"):
        if row.get(key) not in (None, "", "-"):
            try:
                price = float(str(row.get(key)).replace(",", "").replace("%", ""))
                break
            except (TypeError, ValueError):
                return "INVALID_PRICE"
    if not symbol:
        return "INVALID_SYMBOL"
    if price is None or price <= 0:
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
    blockers = list(cheap_eligibility_blockers(snapshot))
    blockers.append(execution_block_reason(snapshot, account))
    blockers.append(execution_board_blocker(snapshot))
    source_time = str(snapshot.get("source_time") or "")
    if not source_time:
        blockers.append("MISSING_DATA")
    else:
        try:
            asof = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if asof.tzinfo is None:
                asof = asof.replace(tzinfo=timezone.utc)
            if as_of is not None:
                clock = as_of
                if clock.tzinfo is None:
                    clock = clock.replace(tzinfo=timezone.utc)
                age = (clock.astimezone(timezone.utc) - asof.astimezone(timezone.utc)).total_seconds() / 60
                if age > max_staleness_minutes:
                    blockers.append("STALE_DATA")
        except ValueError:
            blockers.append("STALE_DATA")
    return list(dict.fromkeys(reason for reason in blockers if reason))
