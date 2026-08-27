"""Canonical, decision-free market snapshots with a strict time boundary."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict

FUTURE_FIELD_PATTERNS = (
    re.compile(r"^t\d+_", re.I),
    re.compile(r"^future_(?:\d+d_|return|price|close|open|high|low|volume)", re.I),
    re.compile(r"^(?:future_prices|outcomes|labels)$", re.I),
    re.compile(r"^max_(?:favorable|adverse)_excursion$", re.I),
    re.compile(r"^realized_", re.I),
    re.compile(r"^post_result", re.I),
)


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
        raise ValueError(f"FUTURE_FIELDS_IN_{location}:" + ",".join(leaked))


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
    enriched = dict(row or {})
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


def canonical_snapshot(
    row: Dict[str, Any],
    *,
    trade_date: str = "",
    source: str = "eastmoney_api_scan_v2",
    source_time: str = "",
    timestamp: str = "",
    source_timestamp: str = "",
) -> Dict[str, Any]:
    row = dict(row or {})
    _assert_visible(row, "SNAPSHOT")
    if isinstance(row.get("raw"), dict) and row.get("lineage_id"):
        _assert_visible(row["raw"], "RAW_SNAPSHOT")
        return row

    visible_at = _normalize_timestamp(
        source_time or timestamp or source_timestamp or _first(row, "source_time", "timestamp", "scan_time")
    )
    snapshot = {
        "symbol": str(_first(row, "symbol", "code", "f12") or "").strip().zfill(6),
        "name": str(_first(row, "name", "stock_name", "f14") or ""),
        "trade_date": trade_date or str(_first(row, "trade_date", "date") or ""),
        "source": source,
        "source_version": "canonical_snapshot_v2",
        "source_time": visible_at,
        "as_of": visible_at,
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
        "raw": row,
    }
    snapshot["lineage_id"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return snapshot


normalize_snapshot = canonical_snapshot
