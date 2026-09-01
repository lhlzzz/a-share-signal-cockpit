"""Canonical, decision-free market snapshots with a strict time boundary."""
from __future__ import annotations

import hashlib
import json
import re
import math
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
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
SOURCE_TIMESTAMP_CONTRACTS = {
    "lhb": {"PRIMARY_TIME_FIELD": "event_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "event_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "news": {"PRIMARY_TIME_FIELD": "publication_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "publication_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "announcements": {"PRIMARY_TIME_FIELD": "publication_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "publication_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "research_report": {"PRIMARY_TIME_FIELD": "publication_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "publication_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "stock_reports": {"PRIMARY_TIME_FIELD": "publication_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "publication_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "industry_reports": {"PRIMARY_TIME_FIELD": "publication_time", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "publication_time", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
    "default": {"PRIMARY_TIME_FIELD": "observed_at", "AVAILABILITY_TIME_FIELD": "available_at", "primary_event_field": "observed_at", "availability_field": "available_at", "timezone": "Asia/Shanghai"},
}
REQUIRED_CANONICAL_FIELDS = (
    "symbol",
    "trade_date",
    "signal_time",
    "source",
    "source_version",
    "source_time",
    "available_at",
    "as_of",
    "lineage_id",
    "snapshot_id",
    "payload_hash",
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


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = _parse_timestamp(text)
    if parsed is not None:
        return parsed.date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _source_contract(record: Mapping[str, Any]) -> tuple[str, Dict[str, str]]:
    source_id = str(record.get("source_id") or record.get("source") or "default").lower()
    contract = SOURCE_TIMESTAMP_CONTRACTS.get(source_id, SOURCE_TIMESTAMP_CONTRACTS["default"])
    return source_id, contract


def pit_record_audit(record: Any, as_of: str | datetime | None) -> Dict[str, Any]:
    """Audit one evidence row under its declared source timestamp contract."""
    if not isinstance(record, dict):
        return {"pit_status": "EXCLUDED_FROM_FEATURES", "time_basis": "invalid_record", "source_time": None,
                "available_at": None, "as_of": str(as_of or ""), "exclusion_reason": "INVALID_RECORD"}
    as_of_ts = as_of if isinstance(as_of, datetime) else _parse_timestamp(as_of)
    if as_of_ts is None:
        return {"pit_status": "EXCLUDED_FROM_FEATURES", "time_basis": "invalid_as_of", "source_time": None,
                "available_at": None, "as_of": str(as_of or ""), "exclusion_reason": "INVALID_AS_OF"}
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.replace(tzinfo=timezone.utc)
    source_id, contract = _source_contract(record)
    primary_field = contract["PRIMARY_TIME_FIELD"]
    available_field = contract["AVAILABILITY_TIME_FIELD"]
    source_value = record.get(primary_field)
    available_value = record.get(available_field)
    audit = {
        "pit_status": "OK", "time_basis": f"{source_id}:{primary_field}/{available_field}",
        "source_time": source_value, "available_at": available_value, "as_of": as_of_ts.isoformat(),
        "trade_date": record.get("trade_date"),
        "primary_event_field": primary_field,
        "availability_field": available_field,
        "timezone": contract.get("timezone", "UTC"),
        "exclusion_reason": None,
    }
    if not source_value or not available_value:
        audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="TIMESTAMP_CONTRACT_INCOMPLETE")
        return audit
    source_ts = _parse_timestamp(source_value)
    available_ts = _parse_timestamp(available_value)
    if source_ts is None or available_ts is None:
        audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="TIMESTAMP_INVALID")
        return audit
    if source_ts.tzinfo is None:
        source_ts = source_ts.replace(tzinfo=timezone.utc)
    if available_ts.tzinfo is None:
        available_ts = available_ts.replace(tzinfo=timezone.utc)
    explicit_times = {
        key: record.get(key)
        for key in ("event_time", "publication_time", "observed_at", "available_at")
        if record.get(key) not in (None, "")
    }
    for value in explicit_times.values():
        parsed = _parse_timestamp(value)
        if parsed is None:
            audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="TIMESTAMP_INVALID")
            return audit
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > as_of_ts:
            audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="FUTURE_TIMESTAMP")
            return audit
    if source_ts > as_of_ts or available_ts > as_of_ts:
        audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="FUTURE_TIMESTAMP")
    elif available_ts < source_ts:
        audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="AVAILABILITY_BEFORE_EVENT")
    trade_date_value = record.get("trade_date")
    if audit["pit_status"] == "OK" and trade_date_value not in (None, ""):
        trade_date = _parse_date(trade_date_value)
        if trade_date is None:
            audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="TRADE_DATE_INVALID")
        elif trade_date > as_of_ts.date():
            audit.update(pit_status="EXCLUDED_FROM_FEATURES", exclusion_reason="FUTURE_TRADE_DATE")
    return audit


def pit_record_status(record: Any, as_of: str | datetime | None) -> str:
    return pit_record_audit(record, as_of)["pit_status"]


def assert_point_in_time_evidence(
    record: Any,
    as_of: str | datetime | None,
    *,
    location: str = "EVIDENCE",
) -> Dict[str, Any] | None:
    """Keep a record only when event/publication/observation time is proven PIT."""
    audit = pit_record_audit(record, as_of)
    if audit["pit_status"] != "OK":
        return None
    enriched = dict(record)
    enriched["pit_audit"] = {**audit, "location": location}
    return enriched


def filter_point_in_time_records(
    records: Iterable[Any] | None,
    as_of: str | datetime | None,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    kept: list[Dict[str, Any]] = []
    excluded: list[Dict[str, Any]] = []
    for record in records or []:
        if assert_point_in_time_evidence(record, as_of) is None:
            if isinstance(record, dict):
                excluded.append(record)
            continue
        kept.append(record)
    return kept, excluded


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
) -> CanonicalSnapshot:
    """Resolve the unique trusted snapshot for one symbol and trade date.

    Production never selects max(source_time). Zero matches fail closed.
    Multiple distinct snapshot identities fail closed.
    """
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
        raise ValueError("CANONICAL_SNAPSHOT_NOT_FOUND")
    identities = {
        str(snapshot.get("snapshot_id") or "").strip()
        for snapshot in matched
    }
    if len(identities) == 1 and next(iter(identities)):
        return matched[0]
    raise ValueError("CANONICAL_SNAPSHOT_AMBIGUOUS")


def select_unique_canonical_snapshots(
    rows: Iterable[Mapping[str, Any]],
    *,
    trade_date: str,
) -> list[CanonicalSnapshot]:
    """Keep exactly one trusted snapshot per symbol for a production day."""
    grouped: dict[str, list[CanonicalSnapshot]] = {}
    for row in rows:
        if not isinstance(row, CanonicalSnapshot) or row.get("trusted_snapshot") is not True:
            continue
        if str(row.get("trade_date") or "") != str(trade_date):
            continue
        symbol = str(row.get("symbol") or "").zfill(6)[-6:]
        if not symbol or symbol == "000000":
            continue
        grouped.setdefault(symbol, []).append(row)
    return [
        select_canonical_snapshot(items, symbol=symbol, trade_date=trade_date)
        for symbol, items in grouped.items()
    ]


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def snapshot_payload_hash(payload: Mapping[str, Any]) -> str:
    """Hash immutable snapshot facts, excluding write-time provenance fields."""
    stable = {
        key: value
        for key, value in dict(payload).items()
        if key not in {"payload_hash", "trusted_snapshot", "decision_clock", "source_age_seconds"}
    }
    return _sha256(_canonicalize_for_hash(stable))


_HASH_INSTANT_KEYS = {
    "source_time",
    "created_at",
    "as_of",
    "signal_time",
    "available_at",
    "source_timestamp",
}
_HASH_DATE_KEYS = {"trade_date"}


def _canonical_instant(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_number(value: int | float | Decimal) -> int | float:
    if isinstance(value, bool):
        raise TypeError("boolean is not a hash number")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(format(float(value), ".15g"))
    if math.isfinite(value) and value.is_integer():
        return int(value)
    return float(format(value, ".15g"))


def _canonicalize_for_hash(value: Any, key: str = "") -> Any:
    """Stable hash input: sorted keys, UTC instants, integral floats as ints."""
    if isinstance(value, Mapping):
        return {
            str(item_key): _canonicalize_for_hash(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_for_hash(item, key) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _canonical_number(value)
    if isinstance(value, datetime):
        return _canonical_instant(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        if key in _HASH_DATE_KEYS:
            return value[:10] if len(value) >= 10 else value
        if key in _HASH_INSTANT_KEYS:
            parsed = _parse_timestamp(value)
            if parsed is not None:
                return _canonical_instant(parsed)
        return value
    return value


def build_scan_lineage_id(
    *,
    source: str,
    source_time: str,
    producer: str,
    trade_date: str,
    scan_nonce: str = "",
) -> str:
    """Identity of one data-production/scan lineage. Never a per-symbol snapshot id."""
    return _sha256(
        {
            "source": source,
            "source_time": source_time,
            "producer": producer,
            "trade_date": trade_date,
            "scan_nonce": scan_nonce,
        }
    )


def build_snapshot_id(
    *,
    lineage_id: str,
    symbol: str,
    trade_date: str,
    source: str,
    source_time: str,
    producer: str = "",
) -> str:
    """Identity of one symbol snapshot inside a scan lineage."""
    return _sha256(
        {
            "lineage_id": lineage_id,
            "symbol": symbol,
            "trade_date": trade_date,
            "source": source,
            "source_time": source_time,
            "producer": producer,
        }
    )


def _serialized_canonical(row: Mapping[str, Any]) -> bool:
    return all(row.get(field) not in (None, "") for field in ("symbol", "trade_date", "source", "source_time", "as_of", "raw", "lineage_id"))


def attach_research_observations(
    row: Dict[str, Any],
    *,
    stock_capital_flow: Dict[str, Any] | None = None,
    capital_history: list[Dict[str, Any]] | None = None,
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
        "capital_history": list(capital_history or []),
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
    lineage_id: str = "",
) -> Dict[str, Any]:
    visible_at = _normalize_timestamp(
        source_time or _first(row, "source_time", "as_of", "timestamp", "scan_time") or ""
    )
    as_of = _normalize_timestamp(row.get("as_of") or visible_at)
    signal_time = _normalize_timestamp(row.get("signal_time") or visible_at)
    available_at = _normalize_timestamp(row.get("available_at") or as_of or visible_at)
    payload = {
        "symbol": str(_first(row, "symbol", "code", "f12") or "").strip().zfill(6)[-6:],
        "name": str(_first(row, "name", "stock_name", "f14") or ""),
        "trade_date": trade_date or str(_first(row, "trade_date", "date") or ""),
        "source": source or str(row.get("source") or "eastmoney_api_scan_v2"),
        "source_version": str(row.get("source_version") or SOURCE_VERSION),
        "source_time": visible_at,
        "signal_time": signal_time,
        "available_at": available_at,
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
        "source_layers": list(row.get("source_layers") or []),
        "raw": dict(row.get("raw") or row),
    }
    if payload["symbol"] == "000000":
        payload["symbol"] = ""
    if not payload["trade_date"] and payload["source_time"]:
        payload["trade_date"] = payload["source_time"][:10]
    payload["lineage_id"] = lineage_id or build_scan_lineage_id(
        source=payload["source"],
        source_time=payload["source_time"],
        producer=payload["producer"],
        trade_date=payload["trade_date"],
    )
    payload["snapshot_id"] = build_snapshot_id(
        lineage_id=payload["lineage_id"],
        symbol=payload["symbol"],
        trade_date=payload["trade_date"],
        source=payload["source"],
        source_time=payload["source_time"],
        producer=payload["producer"],
    )
    payload["trusted_snapshot"] = True
    payload["payload_hash"] = snapshot_payload_hash(payload)
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
    lineage_id: str = "",
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
    supplied_payload_hash = str(raw_row.get("payload_hash") or "")
    visible_at = _normalize_timestamp(
        source_time or timestamp or source_timestamp or _first(raw_row, "source_time", "timestamp", "scan_time") or ""
    )
    payload = _build_payload(
        raw_row,
        trade_date=trade_date or str(_first(raw_row, "trade_date", "date") or ""),
        source=source or str(raw_row.get("source") or "eastmoney_api_scan_v2"),
        source_time=visible_at,
        producer=str(raw_row.get("producer") or producer),
        lineage_id=str(lineage_id or raw_row.get("lineage_id") or ""),
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
        payload["signal_time"] = _normalize_timestamp(raw_row.get("signal_time") or payload["signal_time"])
        payload["available_at"] = _normalize_timestamp(raw_row.get("available_at") or payload["available_at"])
    _validate_required(payload)
    payload["payload_hash"] = snapshot_payload_hash(payload)
    if supplied_payload_hash and supplied_payload_hash != payload["payload_hash"]:
        raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")
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
    enriched = dict(snapshot)
    enriched["decision_clock"] = clock.isoformat()
    enriched["source_age_seconds"] = age.total_seconds()
    return CanonicalSnapshot(enriched, _token=_TRUST_TOKEN)


validate_production_provenance = assert_production_provenance
