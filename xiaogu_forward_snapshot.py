"""Canonical, decision-free market snapshots with a strict time boundary."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping

FUTURE_FIELD_PATTERNS = (
    re.compile(r"^t\d+_", re.I),
    re.compile(r"^future_(?:\d+d_|return|price|close|open|high|low|volume)", re.I),
    re.compile(r"^(?:future_prices|outcomes|labels)$", re.I),
    re.compile(r"^max_(?:favorable|adverse)_excursion$", re.I),
    re.compile(r"^realized_", re.I),
    re.compile(r"^post_result", re.I),
)

REGISTERED_PRODUCTION_SOURCES = frozenset({"eastmoney_api_scan_v2"})
SCHEMA_VERSION = "canonical_snapshot_trusted_v1"
SOURCE_VERSION = "canonical_snapshot_v2"
MAX_STALENESS = timedelta(minutes=120)
STALE_DATA = "STALE_DATA"
REQUIRED_CANONICAL_FIELDS = (
    "symbol",
    "trade_date",
    "source",
    "source_version",
    "source_time",
    "as_of",
    "lineage_id",
    "snapshot_id",
    "producer",
    "schema_version",
)
_TRUST_TOKEN = object()


class RawSnapshot(dict):
    """Untrusted capture row. It is never a production decision input."""


class CanonicalSnapshot(dict):
    """Trusted snapshot produced only by validate_and_build_canonical_snapshot()."""

    def __init__(self, payload: Mapping[str, Any], *, _token: object | None = None):
        if _token is not _TRUST_TOKEN:
            raise TypeError("UNTRUSTED_INPUT")
        super().__init__(payload)


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _first(row: Dict[str, Any], *keys: str) -> Any:
    return next((row[key] for key in keys if row.get(key) not in (None, "", "-")), None)


def _normalize_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if re.search(r"[+-]\d{2}$", text):
        return text + ":00"
    return text


def _parse_timestamp(value: Any) -> datetime | None:
    text = _normalize_timestamp(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _future_fields(payload: Any, path: str = "$") -> list[str]:
    """Return paths whose names identify post-decision outcome data."""
    if isinstance(payload, dict):
        fields = []
        for key, value in payload.items():
            field_path = f"{path}.{key}"
            if any(pattern.search(str(key)) for pattern in FUTURE_FIELD_PATTERNS):
                fields.append(field_path)
            fields.extend(_future_fields(value, field_path))
        return fields
    if isinstance(payload, list):
        return [
            field
            for index, value in enumerate(payload)
            for field in _future_fields(value, f"{path}[{index}]")
        ]
    return []


def _assert_visible(payload: Dict[str, Any], location: str) -> None:
    leaked = _future_fields(payload)
    if leaked:
        raise ValueError(f"FUTURE_LEAKAGE:{location}:" + ",".join(leaked))


def _assert_time_order(
    source_time: str,
    as_of: str,
    decision_time: str | datetime | None = None,
) -> None:
    source_ts = _parse_timestamp(source_time)
    as_of_ts = _parse_timestamp(as_of)
    if source_ts and as_of_ts and source_ts > as_of_ts:
        raise ValueError("FUTURE_LEAKAGE:source_time>as_of")
    if decision_time is None:
        return
    decision_ts = decision_time if isinstance(decision_time, datetime) else _parse_timestamp(decision_time)
    if as_of_ts and decision_ts:
        if decision_ts.tzinfo is None:
            decision_ts = decision_ts.replace(tzinfo=timezone.utc)
        if as_of_ts > decision_ts:
            raise ValueError("FUTURE_LEAKAGE:as_of>decision_time")


def snapshot_age(source_time: str | datetime | None, decision_time: str | datetime | None) -> timedelta | None:
    """Return decision_clock - source_time. Production must not substitute as_of for the clock."""
    source_ts = source_time if isinstance(source_time, datetime) else _parse_timestamp(source_time)
    decision_ts = decision_time if isinstance(decision_time, datetime) else _parse_timestamp(decision_time)
    if source_ts is None or decision_ts is None:
        return None
    if source_ts.tzinfo is None:
        source_ts = source_ts.replace(tzinfo=timezone.utc)
    if decision_ts.tzinfo is None:
        decision_ts = decision_ts.replace(tzinfo=timezone.utc)
    return decision_ts.astimezone(timezone.utc) - source_ts.astimezone(timezone.utc)


def production_decision_clock(decision_time: str | datetime | None = None) -> datetime:
    """Return the actual production clock. Replay may pass a historical decision time."""
    if decision_time is None:
        return datetime.now(timezone.utc)
    parsed = decision_time if isinstance(decision_time, datetime) else _parse_timestamp(decision_time)
    if parsed is None:
        raise ValueError("INVALID_DECISION_CLOCK")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def select_canonical_snapshot(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    trade_date: str,
) -> CanonicalSnapshot | None:
    """Pick the unique trusted snapshot for one symbol and trade date."""
    wanted = str(symbol or "").zfill(6)[-6:]
    matched: list[CanonicalSnapshot] = []
    for row in rows:
        if not isinstance(row, CanonicalSnapshot) or row.get("trusted_snapshot") is not True:
            continue
        if str(row.get("symbol") or "").zfill(6)[-6:] != wanted:
            continue
        if str(row.get("trade_date") or "") != str(trade_date):
            continue
        matched.append(row)
    if not matched:
        return None

    def _key(snapshot: CanonicalSnapshot) -> tuple[datetime, str]:
        parsed = _parse_timestamp(snapshot.get("source_time"))
        stamp = parsed or datetime.min.replace(tzinfo=timezone.utc)
        return (stamp, str(snapshot.get("production_run_id") or snapshot.get("lineage_id") or ""))

    return max(matched, key=_key)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _serialized_canonical(row: Mapping[str, Any]) -> bool:
    return all(row.get(field) not in (None, "") for field in ("symbol", "trade_date", "source", "source_time", "as_of", "raw", "lineage_id"))


def attach_research_observations(
    row: Dict[str, Any],
    *,
    stock_capital_flow: Dict[str, Any] | None = None,
    earnings_preview: Dict[str, Any] | None = None,
    org_surveys: list[Dict[str, Any]] | None = None,
    stock_reports: list[Dict[str, Any]] | None = None,
    lhb: list[Dict[str, Any]] | None = None,
    announcements: list[Dict[str, Any]] | None = None,
    industry_flow: Dict[str, Any] | None = None,
    industry_reports: list[Dict[str, Any]] | None = None,
    news: list[Dict[str, Any]] | None = None,
    shareholder_changes: list[Dict[str, Any]] | None = None,
    lockup_expiry: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Attach same-snapshot raw observations without interpreting them."""
    enriched = RawSnapshot(row or {})
    enriched.update({
        "stock_capital_flow": dict(stock_capital_flow or {}),
        "earnings_preview": dict(earnings_preview or {}),
        "org_surveys": list(org_surveys or []),
        "stock_reports": list(stock_reports or []),
        "lhb": list(lhb or []),
        "announcements": list(announcements or []),
        "industry_flow": dict(industry_flow or {}),
        "industry_reports": list(industry_reports or []),
        "news": list(news or []),
        "shareholder_changes": list(shareholder_changes or []),
        "lockup_expiry": list(lockup_expiry or []),
    })
    _assert_visible(enriched, "RESEARCH_OBSERVATIONS")
    return enriched


def _build_payload(
    row: Dict[str, Any],
    *,
    trade_date: str,
    source: str,
    source_time: str,
    producer: str,
) -> Dict[str, Any]:
    visible_at = _normalize_timestamp(
        source_time or _first(row, "source_time", "as_of", "timestamp", "scan_time") or ""
    )
    as_of = _normalize_timestamp(row.get("as_of") or visible_at)
    payload = {
        "symbol": str(_first(row, "symbol", "code", "f12") or "").strip().zfill(6)[-6:],
        "name": str(_first(row, "name", "stock_name", "f14") or ""),
        "trade_date": trade_date or str(_first(row, "trade_date", "date") or ""),
        "source": source or str(row.get("source") or "eastmoney_api_scan_v2"),
        "source_version": str(row.get("source_version") or SOURCE_VERSION),
        "source_time": visible_at,
        "as_of": as_of,
        "producer": producer,
        "schema_version": SCHEMA_VERSION,
        "price": _number(_first(row, "price", "close", "f2", "f43")),
        "open": _number(_first(row, "open", "f17", "f46")),
        "high": _number(_first(row, "high", "f15", "f44")),
        "low": _number(_first(row, "low", "f16", "f45")),
        "volume": _number(_first(row, "volume", "f5", "f47")),
        "amount": _number(_first(row, "amount", "f6", "f48")),
        "turnover": _number(_first(row, "turnover", "turnover_rate", "f8", "f168")),
        "sector": str(_first(row, "sector", "industry", "sector_name", "f100") or ""),
        "fund_flow": row.get("fund_flow") or {"main_net_inflow": _number(row.get("f62"))},
        "capital_flow": row.get("capital_flow") or {},
        "news": row.get("news") or row.get("news_evidence") or [],
        "announcements": row.get("announcements") or row.get("announcement_evidence") or [],
        "lhb": row.get("lhb") or row.get("lhb_evidence") or [],
        "market": row.get("market") or row.get("market_state") or {},
        "risk": row.get("risk") or {},
        "raw": dict(row.get("raw") or row),
    }
    if payload["symbol"] == "000000":
        payload["symbol"] = ""
    payload["lineage_id"] = _sha256(payload)
    payload["snapshot_id"] = _sha256(
        {
            "lineage_id": payload["lineage_id"],
            "symbol": payload["symbol"],
            "trade_date": payload["trade_date"],
            "source": payload["source"],
            "source_time": payload["source_time"],
            "producer": payload["producer"],
        }
    )
    if not payload["trade_date"] and payload["source_time"]:
        payload["trade_date"] = payload["source_time"][:10]
    payload["trusted_snapshot"] = True
    return payload


def _validate_required(payload: Mapping[str, Any], *, strict: bool = False) -> None:
    _assert_visible(dict(payload), "SNAPSHOT")
    raw = payload.get("raw")
    if isinstance(raw, dict):
        _assert_visible(raw, "RAW_SNAPSHOT")
    _assert_time_order(str(payload.get("source_time") or ""), str(payload.get("as_of") or ""))
    if strict:
        missing = [field for field in REQUIRED_CANONICAL_FIELDS if payload.get(field) in (None, "")]
        if missing:
            raise ValueError("CANONICAL_FIELDS_MISSING:" + ",".join(missing))


def validate_and_build_canonical_snapshot(
    row: Mapping[str, Any] | None,
    *,
    trade_date: str = "",
    source: str = "eastmoney_api_scan_v2",
    source_time: str = "",
    timestamp: str = "",
    source_timestamp: str = "",
    producer: str = "xiaogu_forward_snapshot.validate_and_build_canonical_snapshot",
    decision_time: str | datetime | None = None,
    target_trade_date: str = "",
) -> CanonicalSnapshot:
    """Convert untrusted input into a trusted canonical snapshot."""
    if isinstance(row, CanonicalSnapshot):
        _validate_required(row)
        _assert_time_order(str(row.get("source_time") or ""), str(row.get("as_of") or ""), decision_time)
        if target_trade_date and str(row.get("trade_date") or "") != str(target_trade_date):
            raise ValueError("NO_PRODUCTION_SNAPSHOT:trade_date_mismatch")
        return row

    raw_row = RawSnapshot(row or {})
    _assert_visible(raw_row, "SNAPSHOT")
    visible_at = _normalize_timestamp(
        source_time or timestamp or source_timestamp or _first(raw_row, "source_time", "timestamp", "scan_time") or ""
    )
    payload = _build_payload(
        raw_row,
        trade_date=trade_date or str(_first(raw_row, "trade_date", "date") or ""),
        source=source or str(raw_row.get("source") or "eastmoney_api_scan_v2"),
        source_time=visible_at,
        producer=str(raw_row.get("producer") or producer),
    )
    if _serialized_canonical(raw_row):
        payload["raw"] = dict(raw_row.get("raw") or {})
        payload["lineage_id"] = str(raw_row.get("lineage_id") or payload["lineage_id"])
        payload["snapshot_id"] = str(raw_row.get("snapshot_id") or payload["snapshot_id"])
        payload["producer"] = str(raw_row.get("producer") or payload["producer"])
        payload["schema_version"] = str(raw_row.get("schema_version") or SCHEMA_VERSION)
        payload["source_version"] = str(raw_row.get("source_version") or SOURCE_VERSION)
        payload["as_of"] = _normalize_timestamp(raw_row.get("as_of") or payload["as_of"])
        payload["source_time"] = _normalize_timestamp(raw_row.get("source_time") or payload["source_time"])
    _validate_required(payload)
    _assert_time_order(payload["source_time"], payload["as_of"], decision_time)
    if target_trade_date and payload["trade_date"] != str(target_trade_date):
        raise ValueError("NO_PRODUCTION_SNAPSHOT:trade_date_mismatch")
    return CanonicalSnapshot(payload, _token=_TRUST_TOKEN)


def assert_production_provenance(
    snapshot: Mapping[str, Any],
    *,
    trade_date: str = "",
    decision_time: str | datetime | None = None,
    persisted: bool = False,
    registered_sources: Iterable[str] = REGISTERED_PRODUCTION_SOURCES,
    max_staleness: timedelta = MAX_STALENESS,
) -> CanonicalSnapshot:
    """Block production unless the snapshot is trusted, sourced, persisted, and fresh."""
    if not isinstance(snapshot, CanonicalSnapshot) or snapshot.get("trusted_snapshot") is not True:
        raise ValueError("NO_PRODUCTION_SNAPSHOT")
    if not persisted:
        raise ValueError("NO_PRODUCTION_SNAPSHOT")
    _validate_required(snapshot, strict=True)
    if snapshot.get("source") not in set(registered_sources):
        raise ValueError("NO_PRODUCTION_SNAPSHOT:unregistered_source")
    clock = production_decision_clock(decision_time)
    _assert_time_order(
        str(snapshot.get("source_time") or ""),
        str(snapshot.get("as_of") or ""),
        clock,
    )
    age = snapshot_age(str(snapshot.get("source_time") or ""), clock)
    if age is None:
        raise ValueError("STALE_DATA")
    if age > max_staleness:
        raise ValueError("STALE_DATA")
    if trade_date and str(snapshot.get("trade_date") or "") != str(trade_date):
        raise ValueError("NO_PRODUCTION_SNAPSHOT:trade_date_mismatch")
    snapshot["decision_clock"] = clock.isoformat()
    snapshot["source_age_seconds"] = age.total_seconds()
    return snapshot


validate_production_provenance = assert_production_provenance


def canonical_snapshot(
    row: Dict[str, Any],
    *,
    trade_date: str = "",
    source: str = "eastmoney_api_scan_v2",
    source_time: str = "",
    timestamp: str = "",
    source_timestamp: str = "",
    producer: str = "xiaogu_forward_snapshot.validate_and_build_canonical_snapshot",
) -> CanonicalSnapshot:
    return validate_and_build_canonical_snapshot(
        row,
        trade_date=trade_date,
        source=source,
        source_time=source_time,
        timestamp=timestamp,
        source_timestamp=source_timestamp,
        producer=producer,
    )


normalize_snapshot = canonical_snapshot
