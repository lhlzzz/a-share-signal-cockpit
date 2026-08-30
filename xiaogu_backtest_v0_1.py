"""Point-in-time historical replay and label validation for Xiaogu 3.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from xiaogu_forward_bundle_io import load_snapshot as _load_snapshot
from xiaogu_forward_runner import run_production_decision
from xiaogu_forward_result_filler_v0_1 import (
    PRICE_BASIS,
    canonical_future_prices,
    calculate_horizon_outcomes,
    historical_entry_contract,
)
from xiaogu_forward_snapshot import build_scan_lineage_id, build_snapshot_id
from xiaogu_core_alpha import (
    CANONICAL_COST_MODEL,
    FEATURE_VERSION as CURRENT_FEATURE_VERSION,
    MODEL_ID as CURRENT_ALPHA_VERSION,
    MODEL_VERSION as CURRENT_ALPHA_MODEL_VERSION,
)
from xiaogu_horizon_evaluation import (
    HORIZONS,
    HISTORICAL_VALIDATION_HORIZONS,
    build_alpha_report,
    evaluate_feature_groups,
    evaluate_replay,
    portfolio_metrics,
    target_quality_gate,
)

HISTORICAL_SNAPSHOT_VERSION = "canonical_historical_snapshot_v1"
HISTORICAL_TARGET_VERSION = "profit_window_5d_labels_v1"
MODEL_VERSION = CURRENT_ALPHA_MODEL_VERSION
TARGET_QUALITY = ("CANONICAL", "PARTIAL", "CONFLICT", "INVALID", "UNRESOLVED")
NONCANONICAL_CATEGORIES = (
    "MISSING_ENTRY", "MISSING_T1", "MISSING_T2", "MISSING_T3", "MISSING_T4", "MISSING_T5",
    "CONFLICT", "INVALID", "SOURCE_UNAVAILABLE", "AMBIGUOUS_IDENTITY", "UNRESOLVED",
)
NON_INPUT_KEY_MARKERS = (
    "rank", "score", "bonus", "penalty", "selection", "recommendation",
    "priority", "outcome", "decision",
)
DEFAULT_HISTORICAL_ROOT = Path(__file__).resolve().parent / "data" / "historical_replay_snapshots"
CONFIG_PATH = Path(__file__).resolve().parent / "rule_freeze_v0_1.json"
CONFIG_HASH = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
DEFAULT_FUTURE_BAR_CACHE = Path("/tmp/xiaogu-historical-future-bars.json")
DEFAULT_CALIBRATION_ARTIFACT = Path(__file__).resolve().parent / "data" / "research" / "profit_window_calibration.json"


def load_snapshot(path: str | Path) -> Dict[str, Any]:
    return _load_snapshot(Path(path))


def calculate_portfolio_metrics(returns: Iterable[float]) -> Dict[str, Any]:
    return portfolio_metrics(returns)


def _strip_non_input_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _strip_non_input_fields(value)
            for key, value in payload.items()
            if key not in {"future_prices", "future_bars", "outcomes", "labels"}
            and not str(key).lower().startswith(("future_", "actual_"))
            and not re.match(r"^t\d+[_-]", str(key), re.I)
            and not any(marker in str(key).lower() for marker in NON_INPUT_KEY_MARKERS)
        }
    if isinstance(payload, list):
        return [_strip_non_input_fields(value) for value in payload]
    return payload


def _compact_snapshot_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep T-day facts needed by the current owner without duplicating blobs."""
    drop = {
        "news_evidence", "sector_related_news", "macro_only_news_count",
        "market_news", "all_news", "raw_payload", "future_return_fields_placeholder",
        "postmortem_snapshot",
    }
    compact = {}
    for key, value in payload.items():
        if key in drop:
            continue
        if isinstance(value, list) and len(value) > 500:
            continue
        compact[key] = value
    return compact


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "", "-")), None)


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _same_number(values: Sequence[Any], tolerance: float = 1e-8) -> bool:
    numbers = [_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return bool(numbers) and max(numbers) - min(numbers) <= tolerance


def _json_row(row: Dict[str, Any], key: str) -> Dict[str, Any]:
    return _as_dict(row.get(key))


def _historical_decision_id(*, pick: Dict[str, Any] | None = None, candidate: Dict[str, Any] | None = None) -> str:
    pick = pick or {}
    candidate = candidate or {}
    for row in (pick, candidate):
        value = str(row.get("decision_id") or "").strip()
        if value:
            return value
    return ""


def _identity_signal_time(row: Dict[str, Any], companion: Dict[str, Any] | None = None) -> str:
    """Return a proven signal timestamp without using a row id as identity."""
    companion = companion or {}
    evidence = _json_row(row, "settlement_evidence")
    contract = _json_row(evidence, "execution_contract")
    row_raw = _json_row(row, "raw_json")
    companion_raw = _json_row(companion, "raw_json")
    value = _first_value(
        row.get("signal_time"),
        contract.get("signal_time"),
        row_raw.get("signal_time"),
        row_raw.get("source_time"),
        companion.get("signal_time"),
        companion_raw.get("signal_time"),
        companion.get("source_time"),
        companion_raw.get("source_time"),
    )
    if value in (None, ""):
        return ""
    value = str(value).strip()
    if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
        trade_date = str(row.get("trade_date") or companion.get("trade_date") or "")
        if trade_date:
            value = f"{trade_date}T{value}"
    return value


def _historical_decision_identity(
    *,
    pick: Dict[str, Any] | None = None,
    candidate: Dict[str, Any] | None = None,
    return_rows: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Resolve only explicit identities; never use a database row id as one."""
    pick = pick or {}
    candidate = candidate or {}
    explicit = _historical_decision_id(pick=pick, candidate=candidate)
    if not explicit:
        explicit = next(
            str(row.get("decision_id") or (row.get("payload") or {}).get("decision_id") or "").strip()
            for row in return_rows
            if str(row.get("decision_id") or (row.get("payload") or {}).get("decision_id") or "").strip()
        ) if any(
            str(row.get("decision_id") or (row.get("payload") or {}).get("decision_id") or "").strip()
            for row in return_rows
        ) else ""
    if explicit:
        return {
            "decision_id": explicit,
            "status": "EXPLICIT",
            "source": "EXPLICIT_DECISION_ID",
            "original_decision_id": explicit,
            "evidence": {"decision_id": explicit},
        }

    rows = list(return_rows)
    if not candidate or not rows:
        return {"decision_id": None, "status": "UNRESOLVED", "source": "NO_EXPLICIT_RELATION", "evidence": {}}
    run_id = str(candidate.get("production_run_id") or "").strip()
    snapshot_id = str(candidate.get("candidate_snapshot_id") or "").strip()
    symbol = str(candidate.get("symbol") or "").zfill(6)
    trade_date = str(candidate.get("trade_date") or "").strip()
    candidate_time = _identity_signal_time(candidate)
    return_times = {_identity_signal_time(row, candidate) for row in rows}
    return_times.discard("")
    if not run_id or not snapshot_id or not symbol or not trade_date:
        return {
            "decision_id": None,
            "status": "UNRESOLVED",
            "source": "INCOMPLETE_EXPLICIT_RELATION",
            "evidence": {
                "production_run_id": run_id,
                "candidate_snapshot_id": snapshot_id,
                "symbol": symbol,
                "trade_date": trade_date,
                "candidate_signal_time": candidate_time,
                "return_signal_times": sorted(return_times),
            },
        }
    if len(return_times) > 1 or (candidate_time and return_times and return_times != {candidate_time}):
        return {
            "decision_id": None,
            "status": "CONFLICT",
            "source": "EXPLICIT_RELATION_SIGNAL_TIME_CONFLICT",
            "evidence": {
                "production_run_id": run_id,
                "candidate_snapshot_id": snapshot_id,
                "symbol": symbol,
                "trade_date": trade_date,
                "candidate_signal_time": candidate_time,
                "return_signal_times": sorted(return_times),
            },
        }
    signal_time = candidate_time or next(iter(return_times), "")
    if not signal_time:
        return {
            "decision_id": None,
            "status": "UNRESOLVED",
            "source": "MISSING_EXPLICIT_RELATION_SIGNAL_TIME",
            "evidence": {
                "production_run_id": run_id,
                "candidate_snapshot_id": snapshot_id,
                "symbol": symbol,
                "trade_date": trade_date,
                "return_signal_times": sorted(return_times),
            },
        }
    # Keep the proven relation as evidence, but never promote it to a fake
    # decision_id. The production decision id is absent in this legacy data.
    return {
        "decision_id": None,
        "status": "RECOVERED_EXPLICIT_RELATION",
        "source": "PRODUCTION_RUN_CANDIDATE_SIGNAL_RELATION",
        "original_decision_id": None,
        "evidence": {
            "production_run_id": run_id,
            "candidate_snapshot_id": snapshot_id,
            "symbol": symbol,
            "trade_date": trade_date,
            "signal_time": signal_time,
            "return_count": len(rows),
        },
        "identity_key": {
            "production_run_id": run_id,
            "candidate_snapshot_id": snapshot_id,
            "symbol": symbol,
            "trade_date": trade_date,
            "signal_time": signal_time,
        },
    }


def _relation_key(row: Dict[str, Any]) -> tuple[Any, ...] | None:
    run = row.get("production_run_id")
    snapshot = row.get("candidate_snapshot_id")
    if run and snapshot:
        return (
            str(run), str(snapshot), str(row.get("symbol") or "").zfill(6),
            str(row.get("trade_date") or ""),
        )
    return None


def _pick_snapshot(pick: Dict[str, Any], candidate: Dict[str, Any] | None) -> Dict[str, Any]:
    features = _json_row(pick, "features")
    candidate = candidate or {}
    candidate_features = _json_row(candidate, "candidate_features")
    raw_json = _json_row(candidate, "raw_json")
    # Candidate facts are preferred; pick payload supplies the persisted decision
    # evidence when no exact candidate snapshot is available.
    source = {
        **_compact_snapshot_source(_strip_non_input_fields(raw_json)),
        **_compact_snapshot_source(_strip_non_input_fields(candidate_features)),
        **_compact_snapshot_source(_strip_non_input_fields(features)),
        "symbol": _first_value(candidate.get("symbol"), pick.get("symbol"), features.get("symbol")),
        "stock_name": _first_value(candidate.get("stock_name"), pick.get("stock_name")),
        "trade_date": _first_value(candidate.get("trade_date"), pick.get("trade_date")),
        "source_time": _first_value(
            candidate.get("source_time"), raw_json.get("source_time"),
            candidate.get("created_at"), pick.get("created_at"),
        ),
        "price": _first_value(
            candidate.get("close_price"), candidate.get("open_price"),
            candidate_features.get("price"), features.get("price"),
        ),
        "sector": _first_value(candidate.get("sector"), candidate_features.get("sector"), features.get("sector")),
    }
    source.update({
        key: _strip_non_input_fields(value)
        for key, value in {
            "candidate_features": _compact_snapshot_source(candidate_features),
            "eligibility_snapshot": _json_row(candidate, "eligibility_snapshot") or _json_row(features, "eligibility_snapshot"),
            "factor_snapshot": _compact_snapshot_source(_json_row(candidate, "factor_snapshot")),
            "auxiliary_evidence_snapshot": _compact_snapshot_source(_json_row(candidate, "auxiliary_evidence_snapshot")),
            "source_layers": candidate.get("source_layers") or pick.get("source_layers") or [],
        }.items()
        if value not in (None, "", {}, [])
    })
    # Normalize persisted T-day evidence into names consumed by the current
    # feature owner. These are raw measurements, never decisions or labels.
    if source.get("main_net_inflow") in (None, "", "-"):
        source["main_net_inflow"] = _first_value(
            source.get("main_net_inflow"), source.get("net_inflow_main"),
            source.get("replay_main_force_net_inflow"), source.get("fund_inflow_positive"),
        )
    if source.get("institutional_flow") in (None, "", "-"):
        source["institutional_flow"] = _first_value(
            source.get("institutional_flow"), source.get("hsgt_institutional_flow"),
        )
    if source.get("amount_percentile") in (None, "", "-"):
        source["amount_percentile"] = _first_value(
            source.get("amount_percentile"), source.get("full_universe_amount_pctile"),
        )
    source_time = source.get("source_time")
    if source_time:
        source_time = str(source_time)
        if source_time[-1:] != "Z" and not re.search(r"[+-]\d{2}:?\d{2}$", source_time):
            source["source_time"] = source_time.replace(" ", "T", 1) + "+08:00"
    return source


def _entry_audit(
    return_rows: Sequence[Dict[str, Any]],
    *,
    source_rows: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    candidates: list[tuple[str, Any]] = []
    derived_candidates: list[tuple[str, Any]] = []
    records = [*source_rows, *return_rows]
    for row in records:
        evidence = _json_row(row, "settlement_evidence")
        contract = _json_row(evidence, "execution_contract")
        payload = _as_dict(row.get("payload"))
        candidates.extend((name, value) for name, value in (
            ("returns.entry_price", row.get("entry_price")),
            ("settlement_evidence.entry_price", evidence.get("entry_price")),
            ("execution_contract.execution_price", contract.get("execution_price")),
            ("execution_contract.signal_price", contract.get("signal_price")),
            ("payload.entry_price", payload.get("entry_price")),
        ) if _number(value) is not None and _number(value) > 0)
        execution_model = _json_row(evidence, "execution_model")
        derived_candidates.extend((name, value) for name, value in (
            ("execution_model.entry_reference_price", execution_model.get("entry_reference_price")),
            ("execution_model.entry_execution_price", execution_model.get("entry_execution_price")),
        ) if _number(value) is not None and _number(value) > 0)
    values = [value for _, value in candidates]
    first = return_rows[0] if return_rows else (source_rows[0] if source_rows else {})
    metadata = {
        "decision_id": _first_value(*(row.get("decision_id") for row in records)),
        "snapshot_id": _first_value(*(row.get("candidate_snapshot_id") for row in records)),
        "signal_date": first.get("trade_date"),
        "signal_time": None,
        "entry_time": None,
        "execution_time": None,
        "symbol": first.get("symbol"),
        "entry_price": None,
        "execution_price": None,
        "price_basis": None,
        "source": None,
        "production_run_id": first.get("production_run_id"),
        "feature_version": None,
        "alpha_version": None,
        "decision_version": None,
        "issues": [],
        "candidates": candidates,
        "derived_candidates": derived_candidates,
        "t1_open": first.get("t1_open_price"),
        "t1_high": first.get("t1_high_price"),
        "t1_low": first.get("t1_low_price"),
    }
    if not candidates:
        metadata["issues"].append("MISSING_ENTRY_METADATA")
        return metadata
    if not _same_number(values):
        metadata["issues"].append("ENTRY_PRICE_CONFLICT")
        return metadata
    # The first available field follows the production contract priority.
    selected = next(
        _number(value) for source in (
            "returns.entry_price",
            "settlement_evidence.entry_price",
            "execution_contract.execution_price",
            "execution_contract.signal_price",
            "payload.entry_price",
        ) for name, value in candidates if name == source
    ) if candidates else None
    evidence = _json_row(records[0], "settlement_evidence") if records else {}
    contract = _json_row(evidence, "execution_contract")
    execution_price = next(
        (_number(value) for name, value in candidates if name == "execution_contract.execution_price"),
        selected,
    )
    metadata.update({
        "entry_price": selected,
        "execution_price": execution_price,
        "price_basis": _first_value(first.get("entry_price_basis"), evidence.get("price_basis")),
        "source": _first_value(first.get("entry_price_source"), evidence.get("price_source")),
        "signal_time": _first_value(contract.get("signal_time"), first.get("entry_time")),
        "entry_time": _first_value(
            first.get("entry_time"),
            contract.get("execution_time"),
        ),
        "execution_time": _first_value(
            contract.get("execution_time"),
            first.get("execution_time"),
            first.get("entry_time"),
        ),
    })
    required = (metadata["signal_date"], metadata["signal_time"], metadata["price_basis"], metadata["source"])
    if any(value in (None, "") for value in required):
        metadata["issues"].append("MISSING_ENTRY_METADATA")
    return metadata


def _return_targets(return_rows: Sequence[Dict[str, Any]], entry_price: float | None) -> Dict[str, Any]:
    """Build the 5D ground-truth contract from persisted source evidence.

    A close return is an outcome field.  A daily high is a bar opportunity
    proxy, never an execution fill.  Missing OHLC remains missing and prevents
    a row from becoming a canonical training label.
    """
    cost_rate = CANONICAL_COST_MODEL["all_in_transaction_cost"]
    days: Dict[str, Dict[str, Any]] = {str(day): {} for day in range(1, 6)}
    conflicts: list[str] = []

    def evidence_day(row: Dict[str, Any], day: int) -> Dict[str, Any]:
        evidence = _json_row(row, "settlement_evidence")
        values = evidence.get("days") or evidence.get("daily_outcomes") or evidence.get("future_bars")
        if isinstance(values, dict):
            value = values.get(str(day), values.get(day))
            return _as_dict(value)
        if isinstance(values, list) and len(values) >= day:
            return _as_dict(values[day - 1])
        return {}

    def agreed(row_key: str, day: int, *fallbacks: Any) -> float | None:
        values = [
            row.get(row_key),
            *fallbacks,
        ]
        numbers = [_number(value) for value in values if _number(value) is not None]
        if numbers and max(numbers) - min(numbers) > 1e-8:
            conflicts.append(f"T{day}_{row_key.upper()}_CONFLICT")
        return numbers[0] if numbers else None

    for day in range(1, 6):
        for row in return_rows:
            source = evidence_day(row, day)
            item = days[str(day)]
            return_value = agreed(
                f"t{day}_return", day,
                row.get(f"t{day}_return_close"),
                source.get("return"),
                source.get("close_return"),
            )
            if return_value is not None and "return" in item and item["return"] != return_value:
                conflicts.append(f"T{day}_RETURN_CONFLICT")
            if return_value is not None:
                item["return"] = return_value
            for field, keys in {
                "date": (f"future_{day}d_date", "date", "trade_date"),
                "open": (f"future_{day}d_open", "open", "open_price"),
                "high": (f"future_{day}d_high", "high", "high_price"),
                "low": (f"future_{day}d_low", "low", "low_price"),
                "close": (f"future_{day}d_close", "close", "close_price"),
                "volume": (f"future_{day}d_volume", "volume"),
                "amount": (f"future_{day}d_amount", "amount"),
                "source": ("source",),
                "source_timestamp": ("source_timestamp",),
                "price_basis": ("price_basis",),
            }.items():
                value = _first_value(*(row.get(key) for key in keys), *(source.get(key) for key in keys))
                if value not in (None, "") and field not in item:
                    item[field] = value

        if entry_price and entry_price > 0:
            high = _number(days[str(day)].get("high"))
            low = _number(days[str(day)].get("low"))
            close = _number(days[str(day)].get("close"))
            if high is not None:
                days[str(day)]["mfe"] = (high - entry_price) / entry_price
                days[str(day)]["daily_bar_profit_opportunity"] = (
                    days[str(day)]["mfe"] - cost_rate
                )
            if low is not None:
                days[str(day)]["mae"] = (low - entry_price) / entry_price
            if close is not None and "return" not in days[str(day)]:
                days[str(day)]["return"] = (close - entry_price) / entry_price

    complete_days = [
        day for day in range(1, 6)
        if all(_number(days[str(day)].get(field)) is not None for field in ("open", "high", "low", "close", "volume", "amount"))
        and all(days[str(day)].get(field) not in (None, "") for field in ("source", "source_timestamp", "price_basis"))
    ]
    opportunity_values = [
        days[str(day)]["daily_bar_profit_opportunity"]
        for day in range(1, 6)
        if days[str(day)].get("daily_bar_profit_opportunity") is not None
    ]
    mae_values = [
        days[str(day)]["mae"]
        for day in range(1, 6)
        if days[str(day)].get("mae") is not None
    ]
    complete_5d = len(complete_days) == 5
    profitable = [
        day for day in range(1, 6)
        if days[str(day)].get("daily_bar_profit_opportunity", -1) >= 0.02
    ] if complete_5d else []
    first_profit = profitable[0] if profitable else None
    status = "COMPLETE" if complete_5d else "PARTIAL" if any(days.values()) else "INVALID"
    targets: Dict[str, Any] = {
        "days": days,
        "entry_price": _number(entry_price),
        "profit_window_target": 0.02,
        "execution_cost_rate": cost_rate,
        "daily_bar_profit_opportunity": [
            days[str(day)].get("daily_bar_profit_opportunity") for day in range(1, 6)
        ],
        "max_daily_bar_profit_opportunity_5d": max(opportunity_values, default=None),
        "net_profit_window": max(0.0, max(opportunity_values)) if opportunity_values else None,
        "max_mae_5d": min(mae_values, default=None),
        "mfe_5d": max((days[str(day)].get("mfe") for day in complete_days), default=None),
        "profit_window": bool(first_profit) if complete_5d else None,
        "profit_window_flag": bool(first_profit) if complete_5d else None,
        "first_profit_day": first_profit,
        "time_to_profit": first_profit,
        "data_status": status,
        "realizability_level": "DAILY_BAR_APPROXIMATION",
        "complete_5d": complete_5d,
        "available_days": len(complete_days),
        "missing_days": [day for day in range(1, 6) if day not in complete_days],
        "conflicts": sorted(set(conflicts)),
    }
    for day in range(1, 6):
        value = days[str(day)].get("return")
        targets[f"t{day}_return"] = value
    return targets


def _quality(entry: Dict[str, Any], targets: Dict[str, Any], *, relation_conflict: bool = False) -> tuple[str, list[str]]:
    issues = list(entry.get("issues") or [])
    if relation_conflict:
        issues.append("EXPLICIT_RELATION_CONFLICT")
    issues.extend(targets.get("conflicts") or [])
    if "ENTRY_PRICE_CONFLICT" in issues or relation_conflict or targets.get("conflicts"):
        return "CONFLICT", sorted(set(issues))
    if "MISSING_ENTRY_METADATA" in issues or entry.get("entry_price") is None:
        return "INVALID", sorted(set(issues))
    if targets.get("complete_5d"):
        return "CANONICAL", sorted(set(issues))
    return "PARTIAL", sorted(set(issues))


def _quality_categories(
    *,
    entry: Dict[str, Any] | None,
    targets: Dict[str, Any] | None,
    identity_status: str,
    linked: Sequence[Dict[str, Any]],
    quality: str,
    replay_error: str | None = None,
) -> list[str]:
    entry = entry or {}
    targets = targets or {}
    categories: list[str] = []
    if quality == "CONFLICT" or "ENTRY_PRICE_CONFLICT" in (entry.get("issues") or []) or targets.get("conflicts"):
        categories.append("CONFLICT")
    if quality == "INVALID" and replay_error:
        categories.append("INVALID")
    if "MISSING_ENTRY_METADATA" in (entry.get("issues") or []) or entry.get("entry_price") is None:
        categories.append("MISSING_ENTRY")
    for day in targets.get("missing_days") or []:
        categories.append(f"MISSING_T{int(day)}")
    if not linked:
        categories.append("SOURCE_UNAVAILABLE")
    if identity_status == "CONFLICT":
        categories.append("AMBIGUOUS_IDENTITY")
    elif identity_status == "UNRESOLVED":
        categories.append("UNRESOLVED")
    if quality == "INVALID" and not categories:
        categories.append("INVALID")
    return [category for category in NONCANONICAL_CATEGORIES if category in set(categories)]


def _primary_quality_category(categories: Sequence[str]) -> str | None:
    priority = (
        "CONFLICT", "SOURCE_UNAVAILABLE", "INVALID", "MISSING_ENTRY", "MISSING_T1", "MISSING_T2",
        "MISSING_T3", "MISSING_T4", "MISSING_T5", "AMBIGUOUS_IDENTITY", "UNRESOLVED",
    )
    return next((category for category in priority if category in categories), None)


def _merge_missing_future_targets(
    targets: Dict[str, Any],
    future_bars: Sequence[Dict[str, Any]],
    entry_price: float | None,
) -> Dict[str, Any]:
    """Fill only missing target fields from canonical future-price evidence."""
    days = targets.setdefault("days", {str(day): {} for day in range(1, 6)})
    seen_dates: set[str] = set()
    bars = []
    for bar in sorted(
        future_bars,
        key=lambda value: str(value.get("trade_date") or value.get("date") or ""),
    ):
        bar_date = str(bar.get("trade_date") or bar.get("date") or "")
        if not bar_date or bar_date in seen_dates:
            continue
        if any(_number(bar.get(field)) is None for field in ("open", "high", "low", "close")):
            continue
        seen_dates.add(bar_date)
        bars.append(bar)
        if len(bars) == 5:
            break

    for day, bar in enumerate(bars, 1):
        item = days[str(day)]
        for target_key, source_key in (
            ("date", "trade_date"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("amount", "amount"),
            ("source", "source"),
            ("source_timestamp", "source_timestamp"),
            ("price_basis", "price_basis"),
        ):
            if item.get(target_key) in (None, ""):
                value = bar.get(source_key) if source_key in bar else bar.get("date")
                if value not in (None, ""):
                    item[target_key] = value
        if item.get("date") in (None, ""):
            item["date"] = bar.get("date")
        if (
            entry_price
            and entry_price > 0
            and item.get("return") not in (None, "")
            and str(item.get("date") or "") == str(bar.get("trade_date") or bar.get("date") or "")
        ):
            external_return = (
                _number(item.get("close")) - entry_price
            ) / entry_price
            if abs(float(item["return"]) - external_return) > 1e-8:
                targets.setdefault("conflicts", []).append(
                    f"T{day}_RETURN_EXTERNAL_CONFLICT"
                )

    cost_rate_value = targets.get("execution_cost_rate")
    cost_rate = float(0.003 if cost_rate_value is None else cost_rate_value)
    complete_days = []
    for day in range(1, 6):
        item = days[str(day)]
        if not all(_number(item.get(field)) is not None for field in ("open", "high", "low", "close", "volume", "amount")):
            continue
        if not all(item.get(field) not in (None, "") for field in ("source", "source_timestamp", "price_basis")):
            continue
        complete_days.append(day)
        if entry_price and entry_price > 0:
            high = _number(item["high"])
            low = _number(item["low"])
            close = _number(item["close"])
            if "mfe" not in item:
                item["mfe"] = (high - entry_price) / entry_price
            if "mae" not in item:
                item["mae"] = (low - entry_price) / entry_price
            if "return" not in item:
                item["return"] = (close - entry_price) / entry_price
            if "daily_bar_profit_opportunity" not in item:
                item["daily_bar_profit_opportunity"] = item["mfe"] - cost_rate

    opportunities = [
        days[str(day)]["daily_bar_profit_opportunity"]
        for day in complete_days
        if days[str(day)].get("daily_bar_profit_opportunity") is not None
    ]
    maes = [days[str(day)]["mae"] for day in complete_days if days[str(day)].get("mae") is not None]
    mfes = [days[str(day)]["mfe"] for day in complete_days if days[str(day)].get("mfe") is not None]
    complete = len(complete_days) == 5
    profitable = [
        day for day in complete_days
        if days[str(day)].get("daily_bar_profit_opportunity", -1) >= float(
            0.02 if targets.get("profit_window_target") is None else targets["profit_window_target"]
        )
    ] if complete else []
    first_profit = profitable[0] if profitable else None
    targets.update({
        "max_daily_bar_profit_opportunity_5d": max(opportunities, default=None),
        "net_profit_window": max(0.0, max(opportunities)) if opportunities else None,
        "max_mae_5d": min(maes, default=None),
        "mfe_5d": max(mfes, default=None),
        "profit_window": bool(first_profit) if complete else None,
        "profit_window_flag": bool(first_profit) if complete else None,
        "first_profit_day": first_profit,
        "time_to_profit": first_profit,
        "data_status": "COMPLETE" if complete else "PARTIAL" if complete_days else "INVALID",
        "complete_5d": complete,
        "available_days": len(complete_days),
        "missing_days": [day for day in range(1, 6) if day not in complete_days],
        "future_5d_ohlc_coverage": complete,
        "future_5d_volume_coverage": complete and all(
            days[str(day)].get("volume") not in (None, "") for day in range(1, 6)
        ),
    })
    for day in range(1, 6):
        targets[f"t{day}_return"] = days[str(day)].get("return")
    return targets


def _materialized_asset_rows(rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        {**_as_dict(row.get("payload")), **row}
        for row in rows or []
        if isinstance(row, dict)
    ]


def _database_linked_decision_ranges(
    assets: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, str]:
    """Return one earliest T date per symbol for decisions linked to returns."""
    picks = _materialized_asset_rows(assets.get("picks") or [])
    candidates = _materialized_asset_rows(assets.get("daily_candidates") or [])
    returns = _materialized_asset_rows(assets.get("returns") or [])
    pick_ids = {row.get("pick_id") for row in returns if row.get("pick_id") is not None}
    relations = {
        _relation_key(row)
        for row in returns
        if _relation_key(row) is not None
    }
    ranges: Dict[str, str] = {}
    linked_rows = [
        row for row in picks
        if row.get("id") in pick_ids or _relation_key(row) in relations
    ] + [
        row for row in candidates
        if _relation_key(row) in relations
    ]
    for row in linked_rows:
        relation = _relation_key(row)
        symbol = str(row.get("symbol") or "").zfill(6)
        trade_date = row.get("trade_date")
        if trade_date is None:
            continue
        trade_date = str(trade_date).strip()
        if not symbol or symbol == "000000" or not trade_date:
            continue
        ranges[symbol] = min(trade_date, ranges.get(symbol, trade_date))
    return ranges


def _database_linked_decision_windows(
    assets: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, tuple[str, str]]:
    """Return the full decision date span required for each symbol."""
    picks = _materialized_asset_rows(assets.get("picks") or [])
    candidates = _materialized_asset_rows(assets.get("daily_candidates") or [])
    returns = _materialized_asset_rows(assets.get("returns") or [])
    pick_ids = {row.get("pick_id") for row in returns if row.get("pick_id") is not None}
    relations = {
        _relation_key(row)
        for row in returns
        if _relation_key(row) is not None
    }
    linked_rows = [
        row for row in picks
        if row.get("id") in pick_ids or _relation_key(row) in relations
    ] + [
        row for row in candidates
        if _relation_key(row) in relations
    ]
    windows: Dict[str, tuple[str, str]] = {}
    for row in linked_rows:
        symbol = str(row.get("symbol") or "").zfill(6)
        trade_date = str(row.get("trade_date") or "").strip()
        if not symbol or symbol == "000000" or not trade_date:
            continue
        earliest, latest = windows.get(symbol, (trade_date, trade_date))
        windows[symbol] = (min(earliest, trade_date), max(latest, trade_date))
    return windows


def _cache_read(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {"version": 1, "symbols": {}}
    return value if isinstance(value, dict) and isinstance(value.get("symbols"), dict) else {
        "version": 1,
        "symbols": {},
    }


def _cache_write(path: Path, cache: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _bars_cover_five_days(
    bars: Sequence[Dict[str, Any]],
    trade_date: str,
) -> bool:
    dates = {
        str(bar.get("date") or bar.get("trade_date") or "")
        for bar in bars
        if all(_number(bar.get(field)) is not None for field in ("open", "high", "low", "close"))
    }
    return len([value for value in dates if value > trade_date]) >= 5


def _future_request_end(trade_date: str, requested_end: str) -> str:
    """Bound a provider query to the first five post-signal sessions."""
    try:
        bounded = date.fromisoformat(str(trade_date)) + timedelta(days=14)
        return min(str(requested_end), bounded.isoformat())
    except ValueError:
        return str(requested_end)


def supplement_database_future_prices(
    assets: Dict[str, List[Dict[str, Any]]],
    *,
    cache_path: str | Path = DEFAULT_FUTURE_BAR_CACHE,
    end_date: str | None = None,
    symbol_offset: int = 0,
    max_symbols: int | None = None,
    checkpoint_every: int = 25,
    max_retries: int = 1,
    retry_delay: float = 0.25,
    request_timeout: int = 10,
    eastmoney_timeout: int | None = None,
    baostock_timeout: int | None = None,
    max_errors: int = 3,
) -> Dict[str, Any]:
    """Fetch only missing future OHLC for DB-linked decisions.

    Database canonical bars are loaded first. External bars are a bounded
    fallback for missing future evidence and are never used as T-day inputs.
    """
    from xiaogu_forward_result_filler_v0_1 import (
        fetch_baostock_daily_bars,
        fetch_eastmoney_daily_bars,
    )

    path = Path(cache_path)
    cache = _cache_read(path)
    existing = list(assets.get("canonical_future_prices") or [])
    existing_by_symbol: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for bar in existing:
        if isinstance(bar, dict):
            existing_by_symbol[str(bar.get("symbol") or "").zfill(6)].append(bar)

    ranges = _database_linked_decision_ranges(assets)
    windows = _database_linked_decision_windows(assets)
    symbols = sorted(ranges)
    selected_symbols = symbols[max(0, symbol_offset):]
    if max_symbols is not None:
        selected_symbols = selected_symbols[:max(0, max_symbols)]
    requested_end = str(end_date or date.today().isoformat())
    eastmoney_timeout = request_timeout if eastmoney_timeout is None else eastmoney_timeout
    baostock_timeout = request_timeout if baostock_timeout is None else baostock_timeout
    fetched_bars: list[Dict[str, Any]] = []
    pending_persistence: list[Dict[str, Any]] = []
    errors = []
    persistence_errors = []
    cache_hits = 0
    fetched_symbols = 0
    provider_counts: Dict[str, int] = defaultdict(int)
    schema_ready = False
    persisted_keys = {
        (str(bar.get("symbol") or "").zfill(6), str(bar.get("date") or bar.get("trade_date") or ""))
        for bar in existing
        if bar.get("symbol") and (bar.get("date") or bar.get("trade_date"))
    }

    def checkpoint() -> None:
        nonlocal pending_persistence, schema_ready
        _cache_write(path, cache)
        if not pending_persistence:
            return
        try:
            from xiaogu_db import init_db, record_canonical_future_prices
            if not schema_ready:
                init_db()
                schema_ready = True
            record_canonical_future_prices(pending_persistence)
            persisted_keys.update(
                (str(bar.get("symbol") or "").zfill(6), str(bar.get("date") or ""))
                for bar in pending_persistence
            )
            pending_persistence = []
        except Exception as exc:
            persistence_errors.append(f"{type(exc).__name__}:{exc}")

    for processed, symbol in enumerate(selected_symbols, 1):
        earliest = ranges[symbol]
        latest = windows[symbol][1]
        if (
            _bars_cover_five_days(existing_by_symbol.get(symbol, []), earliest)
            and _bars_cover_five_days(existing_by_symbol.get(symbol, []), latest)
        ):
            continue
        cached = cache["symbols"].get(symbol) or {}
        cached_bars = cached.get("bars") if isinstance(cached, dict) else None
        symbol_end = _future_request_end(windows[symbol][1], requested_end)
        if (
            isinstance(cached_bars, list)
            and str(cached.get("start_date") or "") <= earliest
            and str(cached.get("end_date") or "") >= symbol_end
            and _bars_cover_five_days(cached_bars, earliest)
        ):
            fetched_bars.extend(cached_bars)
            for bar in cached_bars:
                key = (str(bar.get("symbol") or symbol).zfill(6), str(bar.get("date") or bar.get("trade_date") or ""))
                if key not in persisted_keys:
                    pending_persistence.append(bar)
            cache_hits += 1
            if processed % max(1, checkpoint_every) == 0:
                checkpoint()
            continue

        last_error = None
        provider = ""
        bars: list[Dict[str, Any]] = []
        for fetcher, fetcher_name in (
            (fetch_eastmoney_daily_bars, "eastmoney_api_daily_kline"),
            (fetch_baostock_daily_bars, "baostock_daily_kline"),
        ):
            for attempt in range(max(1, max_retries)):
                try:
                    bars = fetcher(
                        symbol,
                        start_date=earliest,
                        end_date=symbol_end,
                        timeout=(
                            eastmoney_timeout
                            if fetcher_name == "eastmoney_api_daily_kline"
                            else baostock_timeout
                        ),
                    )
                    if bars:
                        last_error = None
                        provider = fetcher_name
                        break
                    last_error = f"{fetcher_name}:EMPTY"
                except Exception as exc:
                    last_error = f"{fetcher_name}:{type(exc).__name__}:{exc}"
                    if attempt + 1 < max(1, max_retries):
                        time.sleep(retry_delay * (attempt + 1))
            if provider:
                break
        fetched_symbols += 1
        if last_error:
            errors.append({"symbol": symbol, "start_date": earliest, "error": last_error})
            if len(errors) >= max(1, max_errors):
                break
            continue
        normalized = canonical_future_prices(
            [
                {**bar, "source": bar.get("source") or provider}
                for bar in bars
                if isinstance(bar, dict)
            ],
            symbol=symbol,
            source_timestamp=datetime.now(timezone.utc).isoformat(),
            price_basis=PRICE_BASIS,
        )
        cached_bars = normalized
        provider_counts[provider] += 1
        cache["symbols"][symbol] = {
            "start_date": earliest,
            "end_date": symbol_end,
            "bars": normalized,
            "source": provider,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        fetched_bars.extend(normalized)
        for bar in normalized:
            key = (str(bar.get("symbol") or symbol).zfill(6), str(bar.get("date") or ""))
            if key not in persisted_keys:
                pending_persistence.append(bar)
        if processed % max(1, checkpoint_every) == 0:
            checkpoint()

    if fetched_bars or pending_persistence or cache.get("symbols"):
        checkpoint()

    existing_keys = {
        (str(bar.get("symbol") or "").zfill(6), str(bar.get("date") or bar.get("trade_date") or ""))
        for bar in existing
        if bar.get("symbol") and (bar.get("date") or bar.get("trade_date"))
    }
    new_bars = [
        bar for bar in fetched_bars
        if (
            str(bar.get("symbol") or "").zfill(6),
            str(bar.get("date") or bar.get("trade_date") or ""),
        ) not in existing_keys
    ]
    if fetched_bars:
        deduped = {
            (str(bar.get("symbol") or "").zfill(6), str(bar.get("date") or "")): bar
            for bar in existing + new_bars
            if bar.get("symbol") and bar.get("date")
        }
        assets["canonical_future_prices"] = list(deduped.values())
        persistence = {
            "status": "PASS" if not persistence_errors else "PARTIAL",
            "rows": len(new_bars),
            "errors": persistence_errors[:20],
            "error_count": len(persistence_errors),
        }
    else:
        persistence = {"status": "SKIPPED", "rows": 0}
        if cache.get("symbols"):
            _cache_write(path, cache)
    return {
        "status": "PASS" if not errors else "PARTIAL",
        "linked_symbols": len(ranges),
        "selected_symbols": len(selected_symbols),
        "symbol_offset": max(0, symbol_offset),
        "max_symbols": max_symbols,
        "fetched_symbols": fetched_symbols,
        "cache_hits": cache_hits,
        "provider_counts": dict(provider_counts),
        "fetched_rows": len(fetched_bars),
        "new_rows": len(new_bars),
        "errors": errors[:20],
        "error_count": len(errors),
        "cache_path": str(path),
        "requested_end_date": requested_end,
        "persistence": persistence,
    }


def _compact_current_decision(decision: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not decision:
        return None
    alpha = decision.get("core_alpha") or {}
    convergence = alpha.get("capital_convergence")
    return {
        "state": decision.get("state"),
        "reason": decision.get("reason"),
        "decision_owner": decision.get("decision_owner"),
        "signal_time": decision.get("signal_time"),
        "entry_price": decision.get("entry_price"),
        "entry_price_source": decision.get("entry_price_source"),
        "feature_vector": decision.get("feature_vector"),
        "canonical_snapshot": decision.get("canonical_snapshot"),
        "future_buyer_map": decision.get("future_buyer_map"),
        "core_alpha": {
            key: alpha.get(key)
            for key in (
                "profit_window_probability", "expected_net_profit_window",
                "expected_time_to_profit", "expected_mae_5d", "repricing_state",
                "accumulation_phase", "capital_convergence",
                "profit_window_feature_values", "axes", "supply_absorption",
                "capital_price_impact", "real_pricing_gap", "repricing_evidence_score",
                "future_buyer_capacity", "future_buyer_evidence", "pricing_gap", "execution_feasibility",
                "downside_risk", "alpha_version", "feature_version",
            )
        },
        "capital_convergence": convergence,
        "repricing_state": alpha.get("repricing_state"),
        "accumulation_phase": alpha.get("accumulation_phase"),
    }


def _capital_convergence_level(value: Any) -> str:
    if not isinstance(value, dict):
        return "UNOBSERVED"
    score = _number(value.get("score"))
    if score is None:
        return "UNOBSERVED"
    return "LOW" if score < 1 / 3 else "MEDIUM" if score < 2 / 3 else "HIGH"


def build_historical_5d_profit_window_dataset(
    assets: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build a read-only replay dataset from explicit database lineage."""
    def materialize(row: Dict[str, Any]) -> Dict[str, Any]:
        payload = _as_dict(row.get("payload"))
        return {**payload, **row}

    picks = [materialize(row) for row in assets.get("picks") or []]
    candidates = [materialize(row) for row in assets.get("daily_candidates") or []]
    returns = [materialize(row) for row in assets.get("returns") or []]
    canonical_future = [materialize(row) for row in assets.get("canonical_future_prices") or []]
    production_runs = {
        str(row.get("id") or row.get("production_run_id")): row
        for row in (materialize(row) for row in assets.get("production_runs") or [])
        if row.get("id") is not None or row.get("production_run_id")
    }
    candidate_by_relation = {_relation_key(row): row for row in candidates if _relation_key(row)}
    returns_by_pick: Dict[Any, list[Dict[str, Any]]] = {}
    returns_by_decision_id: Dict[str, list[Dict[str, Any]]] = {}
    returns_by_relation: Dict[tuple[Any, ...], list[Dict[str, Any]]] = {}
    for row in returns:
        decision_id = str(row.get("decision_id") or (row.get("payload") or {}).get("decision_id") or "")
        if decision_id:
            returns_by_decision_id.setdefault(decision_id, []).append(row)
        if row.get("pick_id") is not None:
            returns_by_pick.setdefault(row["pick_id"], []).append(row)
        key = _relation_key(row)
        if key:
            returns_by_relation.setdefault(key, []).append(row)
    future_by_symbol: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for bar in canonical_future:
        symbol = str(bar.get("symbol") or "").zfill(6)
        if symbol:
            future_by_symbol[symbol].append({
                "trade_date": bar.get("date") or bar.get("trade_date"),
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "volume": bar.get("volume"),
                "amount": bar.get("amount"),
                "source": bar.get("source") or "canonical_future_prices",
                "source_timestamp": bar.get("source_timestamp") or "",
                "price_basis": bar.get("price_basis") or PRICE_BASIS,
            })

    source_decisions: list[tuple[Dict[str, Any] | None, Dict[str, Any], list[Dict[str, Any]]]] = []
    resolved_return_ids: set[Any] = set()
    for pick in picks:
        pick_decision_id = str(pick.get("decision_id") or (pick.get("payload") or {}).get("decision_id") or "")
        linked = returns_by_decision_id.get(pick_decision_id, []) if pick_decision_id else []
        decision_bound = bool(linked)
        if not linked:
            linked = returns_by_pick.get(pick.get("id"), [])
        if not linked:
            audit_relation = _relation_key(pick)
            if audit_relation is None:
                source_decisions.append((pick, {}, []))
                continue
            linked = returns_by_relation.get(audit_relation, [])
        if not linked:
            source_decisions.append((pick, {}, []))
            continue
        pick["_decision_id_bound"] = decision_bound
        resolved_return_ids.update(row.get("id") for row in linked)
        relation_keys = {_relation_key(row) for row in linked if _relation_key(row)}
        candidate_matches = [candidate_by_relation[key] for key in relation_keys if key in candidate_by_relation]
        candidate_ids = {candidate.get("id") for candidate in candidate_matches}
        candidate = candidate_matches[0] if len(candidate_ids) == 1 else {}
        source_decisions.append((pick, candidate, linked))
    linked_candidate_ids: set[Any] = set()
    for key, linked in returns_by_relation.items():
        candidate = candidate_by_relation.get(key)
        remaining = [row for row in linked if row.get("id") not in resolved_return_ids]
        if candidate and remaining and candidate.get("id") not in linked_candidate_ids:
            source_decisions.append((None, candidate, remaining))
            resolved_return_ids.update(row.get("id") for row in remaining)
            linked_candidate_ids.add(candidate.get("id"))

    dataset = []
    canonical_snapshots: list[Dict[str, Any]] = []
    audit = {
        "unresolved_returns": [row.get("id") for row in returns if row.get("id") not in resolved_return_ids],
        "unresolved_decisions": [],
        "entry_audits": [],
        "identity_recovery": {
            "explicit_decision_id": 0,
            "recovered_explicit_relation": 0,
            "unresolved": 0,
            "conflict": 0,
        },
        "relationship_graph": {
            "production_run": {"rows": len(production_runs), "linked": 0},
            "lineage": {"rows": len({str(row.get("production_run_id") or "") for row in returns if row.get("production_run_id")}), "linked": 0},
            "snapshot": {"rows": len({str(row.get("candidate_snapshot_id") or "") for row in returns if row.get("candidate_snapshot_id")}), "linked": 0},
            "decision": {"rows": 0, "explicit": 0, "recovered": 0, "unresolved": 0, "conflict": 0},
            "outcome": {"rows": len(returns), "linked": 0, "unresolved": 0},
        },
    }
    for pick, candidate, linked in source_decisions:
        source_rows = [row for row in (pick, candidate) if row]
        identity = _historical_decision_identity(
            pick=pick,
            candidate=candidate,
            return_rows=linked,
        )
        decision_id = identity.get("decision_id")
        identity_status = str(identity.get("status") or "UNRESOLVED")
        identity_bucket = identity_status.lower().replace("-", "_")
        if identity_bucket not in audit["identity_recovery"]:
            identity_bucket = "unresolved"
        audit["identity_recovery"][identity_bucket] += 1
        audit["relationship_graph"]["decision"]["rows"] += 1
        if identity_status == "EXPLICIT":
            audit["relationship_graph"]["decision"]["explicit"] += 1
        elif identity_status == "RECOVERED_EXPLICIT_RELATION":
            audit["relationship_graph"]["decision"]["recovered"] += 1
        elif identity_status == "CONFLICT":
            audit["relationship_graph"]["decision"]["conflict"] += 1
        else:
            audit["relationship_graph"]["decision"]["unresolved"] += 1
        if linked:
            audit["relationship_graph"]["outcome"]["linked"] += len(linked)
        else:
            audit["relationship_graph"]["outcome"]["unresolved"] += 1
        if not linked:
            entry = _entry_audit([], source_rows=source_rows)
            categories = _quality_categories(
                entry=entry,
                targets=None,
                identity_status=identity_status,
                linked=linked,
                quality="INVALID",
            )
            dataset.append({
                "historical_decision_id": None,
                "symbol": str((pick or candidate).get("symbol") or "").zfill(6),
                "signal_date": str((pick or candidate).get("trade_date") or ""),
                "trade_date": str((pick or candidate).get("trade_date") or ""),
                "decision_id": None,
                "decision_identity_status": "UNRESOLVED",
                "decision_identity_source": identity.get("source"),
                "decision_identity_evidence": identity.get("evidence") or {},
                "decision_identity_key": identity.get("identity_key"),
                "historical_original_decision": pick.get("decision") if pick else candidate.get("selection_outcome"),
                "current_decision": None,
                "canonical_entry_price": None,
                "t1_open": None,
                "t1_high": None,
                "t1_low": None,
                "t1_return": None,
                "t2_return": None,
                "t3_return": None,
                "t4_return": None,
                "t5_return": None,
                "mfe_5d": None,
                "mae_5d": None,
                "days": {str(day): {} for day in range(1, 6)},
                "max_daily_bar_profit_opportunity_5d": None,
                "profit_window": None,
                "profit_window_flag": None,
                "max_mae_5d": None,
                "first_profit_day": None,
                "time_to_profit": None,
                "capital_convergence": None,
                "capital_convergence_level": "UNOBSERVED",
                "repricing_state": None,
                "accumulation_phase": None,
                "target_quality": "INVALID",
                "quality_issues": ["MISSING_HISTORICAL_RETURN_RELATION"],
                "quality_categories": categories,
                "quality_category": _primary_quality_category(categories),
                "entry_audit": entry,
                "feature_version": CURRENT_FEATURE_VERSION,
                "alpha_version": CURRENT_ALPHA_VERSION,
                "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
                "target_version": HISTORICAL_TARGET_VERSION,
                "production_run_id": (pick or candidate).get("production_run_id"),
                "pick_id": pick.get("id") if pick else None,
                "candidate_id": candidate.get("id"),
                "replay_error": None,
            })
            audit["entry_audits"].append({"historical_decision_id": None, **entry, "quality": "INVALID", "issues": categories})
            audit["unresolved_decisions"].append(identity.get("source") or "UNRESOLVED")
            continue
        entry = _entry_audit(linked, source_rows=source_rows)
        symbol = str((pick or candidate).get("symbol") or "").zfill(6)
        trade_date = str((pick or candidate).get("trade_date") or "")
        persisted_future = [
            bar for bar in future_by_symbol.get(symbol, [])
            if str(bar.get("trade_date") or "") > trade_date
        ]
        run = production_runs.get(str(entry.get("production_run_id")), {})
        run_payload = _as_dict(run.get("payload"))
        targets = _return_targets(linked, entry.get("entry_price"))
        if persisted_future:
            targets = _merge_missing_future_targets(targets, persisted_future, entry.get("entry_price"))
        relation_keys = {_relation_key(item) for item in linked if _relation_key(item)}
        quality, issues = _quality(entry, targets, relation_conflict=len(relation_keys) > 1)
        if identity_status == "UNRESOLVED":
            issues.append("MISSING_DECISION_ID")
            if quality == "CANONICAL":
                quality = "UNRESOLVED"
        elif identity_status == "CONFLICT":
            issues.append("DECISION_IDENTITY_CONFLICT")
            quality = "CONFLICT"
        snapshot_source = _pick_snapshot(pick or {}, candidate)
        if not snapshot_source.get("source_time"):
            snapshot_source["source_time"] = f"{snapshot_source.get('trade_date')}T15:00:00+08:00"
        snapshot = None
        current = None
        replay_error = None
        try:
            if snapshot_source.get("price") in (None, ""):
                raise ValueError("HISTORICAL_SNAPSHOT_PRICE_REQUIRED")
            snapshot = canonical_historical_snapshot(
                snapshot_source,
                trade_date=str(snapshot_source.get("trade_date") or ""),
                signal_time=str(snapshot_source.get("source_time") or ""),
                source="database_historical_snapshot",
                source_timestamp=str(snapshot_source.get("source_time") or ""),
                lineage_id=str((pick or {}).get("production_run_id") or candidate.get("production_run_id") or ""),
            )
            canonical_snapshots.append(snapshot)
            current = run_production_decision(snapshot, mode="REPLAY", persisted=True)
        except Exception as exc:
            replay_error = f"{type(exc).__name__}:{exc}"
            if quality == "CANONICAL":
                quality = "INVALID"
                issues.append("CURRENT_REPLAY_FAILED")
        categories = _quality_categories(
            entry=entry,
            targets=targets,
            identity_status=identity_status,
            linked=linked,
            quality=quality,
            replay_error=replay_error,
        )
        alpha = (current or {}).get("core_alpha") or {}
        compact_current = _compact_current_decision(current)
        snapshot_payload = snapshot or {}
        dataset.append({
            "historical_decision_id": decision_id,
            "decision_id": decision_id,
            "decision_identity_status": identity_status,
            "decision_identity_source": identity.get("source"),
            "decision_identity_evidence": identity.get("evidence") or {},
            "decision_identity_key": identity.get("identity_key"),
            "symbol": symbol,
            "signal_date": trade_date,
            "trade_date": trade_date,
            "signal_time": snapshot_payload.get("signal_time") or identity.get("evidence", {}).get("signal_time") or entry.get("signal_time"),
            "entry_time": entry.get("entry_time") or entry.get("signal_time"),
            "historical_original_decision": (pick or candidate).get("decision") if pick else candidate.get("selection_outcome"),
            "current_decision": (current or {}).get("state"),
            "current_decision_payload": compact_current,
            "canonical_entry_price": entry.get("entry_price"),
            "entry_price": entry.get("entry_price"),
            "entry_price_source": entry.get("source"),
            "price_basis": entry.get("price_basis"),
            **targets,
            "capital_convergence": alpha.get("capital_convergence"),
            "capital_convergence_level": _capital_convergence_level(alpha.get("capital_convergence")),
            "repricing_state": alpha.get("repricing_state"),
            "accumulation_phase": ((current or {}).get("portfolio_state") or {}).get("accumulation_status") or alpha.get("accumulation_phase"),
            "target_quality": quality,
            "quality_issues": sorted(set(issues)),
            "quality_categories": categories,
            "quality_category": _primary_quality_category(categories),
            "entry_audit": entry,
            "historical_feature_version": (pick or candidate).get("data_version"),
            "historical_alpha_version": (pick or candidate).get("rule_version"),
            "historical_decision_version": (
                run.get("runner_version")
                or run_payload.get("runner_version")
                or (pick or candidate).get("rule_version")
            ),
            "feature_version": CURRENT_FEATURE_VERSION,
            "alpha_version": CURRENT_ALPHA_VERSION,
            "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
            "target_version": HISTORICAL_TARGET_VERSION,
            "production_run_id": entry.get("production_run_id"),
            "pick_id": (pick or {}).get("id"),
            "candidate_id": candidate.get("id"),
            "replay_error": replay_error,
            "cost_model_version": CANONICAL_COST_MODEL["version"],
            "cost_model": dict(CANONICAL_COST_MODEL),
            "profit_window_semantics": "DAILY_BAR_PROFIT_OPPORTUNITY",
            "snapshot_id": snapshot_payload.get("snapshot_id") if snapshot else None,
        })
        if snapshot:
            audit["relationship_graph"]["production_run"]["linked"] += bool(snapshot.get("lineage_id"))
            audit["relationship_graph"]["lineage"]["linked"] += bool(snapshot.get("lineage_id"))
            audit["relationship_graph"]["snapshot"]["linked"] += bool(snapshot.get("snapshot_id"))
        audit["entry_audits"].append({"historical_decision_id": decision_id, **entry, "quality": quality, "issues": issues})

    canonical = [row for row in dataset if row["target_quality"] == "CANONICAL"]
    partial = [row for row in dataset if row["target_quality"] == "PARTIAL"]
    conflict = [row for row in dataset if row["target_quality"] == "CONFLICT"]
    invalid = [row for row in dataset if row["target_quality"] == "INVALID"]
    unresolved = [row for row in dataset if row["target_quality"] == "UNRESOLVED"]
    category_counts = {category: sum(category in (row.get("quality_categories") or []) for row in dataset) for category in NONCANONICAL_CATEGORIES}
    primary_category_counts = {
        category: sum(row.get("quality_category") == category for row in dataset)
        for category in NONCANONICAL_CATEGORIES
    }
    gate = target_quality_gate(dataset, min_coverage=0.95, horizons=HISTORICAL_VALIDATION_HORIZONS)
    report = build_alpha_report(canonical, quality_gate=gate, horizons=HISTORICAL_VALIDATION_HORIZONS)
    diagnostic = [row for row in dataset if row["target_quality"] in {"CANONICAL", "PARTIAL"}]
    report["diagnostic_sample_count"] = len(diagnostic)
    report["diagnostic_feature_groups"] = evaluate_feature_groups(diagnostic)
    def source_layers(row: Dict[str, Any]) -> list[str]:
        value = row.get("source_layers")
        if not isinstance(value, list):
            raw = _as_dict(row.get("raw_json"))
            value = raw.get("source_layers")
        return [str(item) for item in value or [] if str(item)]

    layer_counts = {
        "L0": sum(any(layer.startswith("L0") for layer in source_layers(row)) for row in candidates),
        "L1": sum(any(layer.startswith("L1") for layer in source_layers(row)) for row in candidates),
        "L2": sum(any(layer.startswith("L2") for layer in source_layers(row)) for row in candidates),
        "L3": sum(any(layer.startswith("L3") for layer in source_layers(row)) for row in candidates),
    }
    candidate_layers_by_id = {
        candidate.get("id"): source_layers(candidate)
        for candidate in candidates
        if candidate.get("id") is not None
    }
    layer_profit_rates = {}
    for layer in ("L0", "L1", "L2", "L3"):
        layer_rows = [
            row for row in dataset
            if layer in {
                item.split("_", 1)[0]
                for item in candidate_layers_by_id.get(row.get("candidate_id"), [])
            }
        ]
        labels = [row.get("profit_window") for row in layer_rows if row.get("profit_window") is not None]
        layer_profit_rates[layer] = {
            "samples": len(layer_rows),
            "labeled_samples": len(labels),
            "profit_window_rate": sum(bool(label) for label in labels) / len(labels) if labels else None,
        }
    def selection_metrics(layer_rows: list[Dict[str, Any]]) -> Dict[str, Any]:
        profit = [row.get("profit_window") for row in layer_rows if row.get("profit_window") is not None]
        net = [row.get("net_profit_window") for row in layer_rows if row.get("net_profit_window") is not None]
        mae = [row.get("max_mae_5d") for row in layer_rows if row.get("max_mae_5d") is not None]
        return {
            "count": len(layer_rows),
            "profit_window_rate": sum(bool(value) for value in profit) / len(profit) if profit else None,
            "mean_net_profit": sum(net) / len(net) if net else None,
            "median_net_profit": median(net) if net else None,
            "MAE": sum(mae) / len(mae) if mae else None,
        }
    rows_for_layer = {
        "ALL": dataset,
        **{
            layer: [
                row for row in dataset
                if any(item.split("_", 1)[0] == layer for item in candidate_layers_by_id.get(row.get("candidate_id"), []))
            ]
            for layer in ("L1", "L2", "L3")
        },
    }
    selection_bias_report = {layer: selection_metrics(layer_rows) for layer, layer_rows in rows_for_layer.items()}
    selection_bias_report["L2 changes sample distribution"] = True
    selection_audit = {
        "full_market_count": None,
        "full_l0_count": None,
        "l0_count": layer_counts["L0"],
        "l1_eligible_count": layer_counts["L1"],
        "l1_count": layer_counts["L1"],
        "l2_count": layer_counts["L2"],
        "l3_count": layer_counts["L3"],
        "alpha_evaluated_count": sum(row.get("current_decision") is not None for row in dataset),
        "alpha_count": sum(row.get("current_decision") is not None for row in dataset),
        "decision_count": len(dataset),
        "profit_window_rate_by_layer": layer_profit_rates,
        "selection_audit_status": "PARTIAL_OBSERVED",
        "selection_bias_warning": True,
        "selection_distribution_shift": True,
        "selection_bias_reason": "DATABASE_ASSETS_START_AT_CANDIDATE_LAYER;_FULL_MARKET_AND_L3_ACCOUNTING_NOT_PROVEN",
        "selection_bias_report": selection_bias_report,
    }
    audit["quality_categories"] = category_counts
    audit["primary_quality_categories"] = primary_category_counts
    report["selection_bias_report"] = selection_bias_report
    report["selection_audit"] = selection_audit
    return {
        "dataset_name": "historical_5d_profit_window_dataset",
        "read_only": True,
        "rows": dataset,
        "canonical_historical_snapshots": canonical_snapshots,
        "database_asset_report": None,
        "audit": audit,
        "selection_audit": selection_audit,
        "counts": {"historical_decisions": len(source_decisions), "dataset": len(dataset), "canonical": len(canonical), "partial": len(partial), "conflict": len(conflict), "invalid": len(invalid), "unresolved": len(unresolved), "quality_categories": category_counts, "primary_quality_categories": primary_category_counts},
        "target_quality_gate": gate,
        "alpha_report": report,
        "core_alpha_status": "VALIDATED" if gate.get("status") == "PASS" else ("DATA_INSUFFICIENT" if len(canonical) < 1 or gate.get("status") != "PASS" else "EXPERIMENTAL"),
        "outcome_boundary": "CURRENT_DECISION_FROZEN_BEFORE_HISTORICAL_RETURN_READ",
    }


def canonical_historical_snapshot(
    row: Dict[str, Any],
    *,
    trade_date: str = "",
    signal_time: str = "",
    source: str = "historical_replay_input",
    source_timestamp: str = "",
    snapshot_version: str = HISTORICAL_SNAPSHOT_VERSION,
    lineage_id: str = "",
) -> Dict[str, Any]:
    """Create a PIT row from T-day facts, never from old score payloads."""
    if not isinstance(row, dict):
        raise TypeError("HISTORICAL_SNAPSHOT_MUST_BE_OBJECT")
    clean = _strip_non_input_fields(row)
    clean.pop("_trade_date", None)
    clean.pop("_path", None)
    signal = signal_time or str(clean.get("signal_time") or clean.get("source_time") or "")
    if not signal:
        raise ValueError("HISTORICAL_SIGNAL_TIME_REQUIRED")
    signal_dt = datetime.fromisoformat(signal.replace("Z", "+00:00"))
    if signal_dt.tzinfo is None:
        signal_dt = signal_dt.replace(tzinfo=timezone.utc)
    available_at = str(clean.get("available_at") or signal)
    available_dt = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
    if available_dt.tzinfo is None:
        available_dt = available_dt.replace(tzinfo=timezone.utc)
    if available_dt > signal_dt:
        raise ValueError("PIT_AVAILABLE_AT_AFTER_SIGNAL_TIME")
    def number(*keys: str) -> float | None:
        raw = clean.get("raw") if isinstance(clean.get("raw"), dict) else {}
        for key in keys:
            value = _first_value(clean.get(key), raw.get(key))
            if value not in (None, "", "-"):
                try:
                    return float(str(value).replace(",", "").replace("%", ""))
                except ValueError:
                    continue
        return None

    snapshot = {
        **clean,
        "symbol": str(clean.get("symbol") or clean.get("code") or "").zfill(6),
        "price": number("price", "close", "close_price", "f2"),
        "open": number("open", "open_price", "f17"),
        "high": number("high", "high_price", "f15"),
        "low": number("low", "low_price", "f16"),
        "volume": number("volume", "f5"),
        "amount": number("amount", "f6"),
        "trade_date": trade_date or str(clean.get("trade_date") or clean.get("date") or signal[:10]),
        "signal_time": signal_dt.astimezone(timezone.utc).isoformat(),
        "source": source,
        "source_timestamp": source_timestamp or signal,
        "snapshot_version": snapshot_version,
        "point_in_time": True,
        "available_at": available_dt.astimezone(timezone.utc).isoformat(),
        "price_basis": PRICE_BASIS,
    }
    if not snapshot["symbol"] or snapshot.get("price") in (None, ""):
        raise ValueError("HISTORICAL_SNAPSHOT_SYMBOL_AND_PRICE_REQUIRED")
    snapshot["lineage_id"] = str(
        lineage_id
        or clean.get("lineage_id")
        or clean.get("production_run_id")
        or build_scan_lineage_id(
            source=snapshot["source"],
            source_time=snapshot["signal_time"],
            producer="canonical_historical_snapshot",
            trade_date=snapshot["trade_date"],
        )
    )
    snapshot["snapshot_id"] = str(clean.get("snapshot_id") or "") or build_snapshot_id(
        lineage_id=snapshot["lineage_id"],
        symbol=snapshot["symbol"],
        trade_date=snapshot["trade_date"],
        source=snapshot["source"],
        source_time=snapshot["signal_time"],
        producer="canonical_historical_snapshot",
    )
    return snapshot


def _source_rows(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*/stock_all_a.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append({"_trade_date": path.parent.name, "_path": str(path), **json.loads(line)})
    return rows


def build_canonical_historical_snapshots(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [canonical_historical_snapshot(
        row,
        trade_date=str(row.get("_trade_date") or row.get("trade_date") or ""),
        signal_time=str(row.get("signal_time") or row.get("source_time") or ""),
        source_timestamp=str(row.get("source_timestamp") or row.get("source_time") or ""),
    ) for row in rows]


def _replay_entry(entry: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], Dict[int, Any]]:
    if not isinstance(entry, dict):
        raise TypeError("REPLAY_ENTRY_MUST_BE_OBJECT")
    source = dict(entry.get("snapshot") if isinstance(entry.get("snapshot"), dict) else {
        key: value for key, value in entry.items()
        if key not in {"future_prices", "future_bars", "outcomes", "labels"}
    })
    future_bars = entry.get("future_bars")
    return source, future_bars if isinstance(future_bars, list) else [], {}


def _decision_record(snapshot: Dict[str, Any], decision: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    alpha = decision.get("core_alpha") or {}
    trade_date = snapshot.get("trade_date") or snapshot.get("date") or ""
    signal_time = snapshot.get("signal_time") or snapshot.get("source_time") or ""
    return {
        "trade_date": trade_date, "symbol": snapshot.get("symbol") or decision.get("symbol"),
        "signal_time": signal_time, "decision": decision.get("state"),
        "decision_owner": decision.get("decision_owner"),
        "reason": decision.get("reason"),
        "blockers": (decision.get("repricing_risk") or {}).get("blockers", []),
        "repricing_state": alpha.get("repricing_state"),
        "repricing_readiness": alpha.get("repricing_readiness"),
        "future_demand": alpha.get("future_demand"),
        "capital_accumulation": alpha.get("capital_accumulation"),
        "supply_absorption": alpha.get("supply_absorption"),
        "pricing_gap": alpha.get("pricing_gap"),
        "capital_price_impact": alpha.get("capital_price_impact"),
        "future_buyer_capacity": alpha.get("future_buyer_capacity"),
        "reflexivity_strength": alpha.get("reflexivity_strength"),
        "reflexivity_break_risk": alpha.get("reflexivity_break_risk"),
        "profit_window_probability": alpha.get("profit_window_probability"),
        "expected_max_profit_5d": alpha.get("expected_max_profit_5d"),
        "expected_time_to_profit": alpha.get("expected_time_to_profit"),
        "expected_mae_5d": alpha.get("expected_mae_5d"),
        "expected_net_profit_window": alpha.get("expected_net_profit_window"),
        "capital_convergence": alpha.get("capital_convergence"),
        "entry_contract": entry,
        "model_registry": {
            "model_version": MODEL_VERSION, "feature_version": CURRENT_FEATURE_VERSION,
            "target_version": HISTORICAL_TARGET_VERSION, "data_version": snapshot.get("snapshot_version", "historical_fixture"),
            "config_hash": CONFIG_HASH,
            "training_window": None, "test_window": trade_date,
        },
    }


def historical_replay(
    snapshots: Iterable[Dict[str, Any]] = (),
    *,
    min_coverage: float = 0.95,
    persist_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Freeze T-day decisions, then attach independent future labels."""
    decisions: List[Dict[str, Any]] = []
    decision_records: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    for item in snapshots:
        source, future_bars, _ignored_future_prices = _replay_entry(item)
        snapshot = canonical_historical_snapshot(source) if source.get("signal_time") or source.get("source_time") else source
        decision = run_production_decision(snapshot, mode="REPLAY", persisted=True)
        try:
            entry = historical_entry_contract(snapshot)
        except ValueError:
            raise
        record = _decision_record(snapshot, decision, entry)
        decision_summary = {
            "state": decision.get("state"), "symbol": decision.get("symbol"),
            "reason": decision.get("reason"), "decision_owner": decision.get("decision_owner"),
            "core_alpha": decision.get("core_alpha") or {},
        }
        decisions.append(decision_summary)
        decision_records.append(record)
        bars = canonical_future_prices(
            future_bars,
            symbol=snapshot["symbol"],
            price_basis=entry["price_basis"],
            require_volume=True,
        )
        outcomes = calculate_horizon_outcomes(
            entry["entry_price"], bars, horizons=HISTORICAL_VALIDATION_HORIZONS,
        )
        rows.append({
            "snapshot": snapshot, "decision": decision_summary, "decision_record": record,
            "entry_contract": entry, "future_bars": bars, "labels": outcomes,
                "forward_window": {
                    "profit_window": outcomes.get("profit_window"),
                    "max_daily_bar_profit_opportunity_5d": outcomes.get("max_daily_bar_profit_opportunity_5d"),
                    "first_profit_day": outcomes.get("first_profit_day"),
                    "time_to_profit": outcomes.get("time_to_profit"),
                    "max_mae_5d": outcomes.get("max_mae_5d"),
                "net_profit_window": outcomes.get("net_profit_window"),
            },
        })
    gate = target_quality_gate(rows, min_coverage=min_coverage, horizons=HISTORICAL_VALIDATION_HORIZONS)
    report = evaluate_replay(rows, quality_gate=gate, horizons=HISTORICAL_VALIDATION_HORIZONS)
    supplied_universe_audits = [
        (row.get("snapshot") or {}).get("selection_audit")
        for row in rows
        if isinstance((row.get("snapshot") or {}).get("selection_audit"), dict)
    ]
    full_universe = next(
        (audit.get("full_l0_universe") for audit in supplied_universe_audits
         if audit.get("full_l0_universe") is not None),
        None,
    )
    source_layers_present = any(
        isinstance((row.get("snapshot") or {}).get("source_layers"), list)
        for row in rows
    )
    l2_count = sum(
        any(layer.startswith("L2_") for layer in ((row.get("snapshot") or {}).get("source_layers") or []))
        for row in rows
    ) if source_layers_present else None
    l3_count = sum(
        "L3_DEEP_CANDIDATE_FETCH" in ((row.get("snapshot") or {}).get("source_layers") or [])
        for row in rows
    ) if source_layers_present else None
    quality_counts = {
        quality.lower() + "_count": sum(1 for row in rows if row.get("target_quality") == quality)
        for quality in ("PARTIAL", "CONFLICT", "INVALID", "UNRESOLVED")
    }
    def selection_metrics(layer_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        labels = [row.get("forward_window") or row.get("labels") or {} for row in layer_rows]
        profit = [item.get("profit_window") for item in labels if item.get("profit_window") is not None]
        net = [item.get("net_profit_window") for item in labels if item.get("net_profit_window") is not None]
        mae = [item.get("max_mae_5d") for item in labels if item.get("max_mae_5d") is not None]
        return {
            "count": len(layer_rows),
            "profit_window_rate": sum(bool(value) for value in profit) / len(profit) if profit else None,
            "mean_net_profit": sum(net) / len(net) if net else None,
            "median_net_profit": median(net) if net else None,
            "MAE": sum(mae) / len(mae) if mae else None,
        }

    selection_bias_report = {
        "ALL": selection_metrics(rows),
        "L1": selection_metrics([row for row in rows if any(layer.startswith("L1_") for layer in (row["snapshot"].get("source_layers") or []))]),
        "L2": selection_metrics([row for row in rows if any(layer.startswith("L2_") for layer in (row["snapshot"].get("source_layers") or []))]),
        "L3": selection_metrics([row for row in rows if "L3_DEEP_CANDIDATE_FETCH" in (row["snapshot"].get("source_layers") or [])]),
        "ALPHA": selection_metrics(rows),
        "L2 changes sample distribution": True,
    }
    selection_audit = {
        "full_universe_count": full_universe,
        "full_l0_count": full_universe,
        "l1_count": next(
            (audit.get("l1_eligible_universe") for audit in supplied_universe_audits
             if audit.get("l1_eligible_universe") is not None),
            None,
        ),
        "l2_count": l2_count,
        "l3_count": l3_count,
        "l3_requested_count": l3_count,
        "l3_returned_count": l3_count,
        "l3_unrelated_count": 0 if l3_count is not None else None,
        "alpha_evaluated_count": len(decisions),
        "alpha_count": len(decisions),
        "decision_count": len(decisions),
        "canonical_count": sum(1 for row in rows if (row.get("snapshot") or {}).get("trusted_snapshot") is True),
        **quality_counts,
        "selection_bias_warning": bool(
            full_universe is None
            or l3_count is None
            or l3_count not in {0, full_universe}
            or len(decisions) < full_universe
        ),
        "selection_audit_status": "OBSERVED" if full_universe is not None else "UNOBSERVED",
        "selection_distribution_shift": True,
        "selection_bias_report": selection_bias_report,
    }
    result = {
        "decisions": decisions, "decision_records": decision_records, "rows": rows,
        "metrics": report["horizon_metrics"], "horizon_metrics": report["horizon_metrics"],
        "horizons": HISTORICAL_VALIDATION_HORIZONS, "target_quality_gate": gate,
        "alpha_validation": "BLOCKED" if gate["status"] != "PASS" else "ELIGIBLE",
        "outcome_boundary": "OUTCOMES_ENTER_AFTER_PRODUCTION_DECISION",
        "selection_audit": selection_audit,
        "selection_bias_report": selection_bias_report,
        **selection_audit,
    }
    result["alpha_report"] = build_alpha_report(rows, quality_gate=gate, horizons=HISTORICAL_VALIDATION_HORIZONS)
    if persist_path:
        path = Path(persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def persist_historical_replay(result: Dict[str, Any]) -> Dict[str, Any]:
    """Write only an independent research artifact; production tables stay read-only."""
    path = result.get("research_artifact_path")
    if not path:
        result["database_persistence"] = {
            "status": "SKIPPED",
            "reason": "READ_ONLY_HISTORICAL_SOURCE",
        }
        return result
    artifact = Path(path)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact_result = dict(result)
    artifact_result.pop("canonical_historical_snapshots", None)
    artifact.write_text(
        json.dumps(artifact_result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    result["database_persistence"] = {
        "status": "PASS",
        "owner": "independent_research_artifact",
        "path": str(artifact),
    }
    return result


def replay_database_history(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    symbol_offset: int = 0,
    max_symbols: int | None = None,
    research_artifact_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Build the database-only five-day replay without altering source rows."""
    from xiaogu_db import (
        database_asset_report,
        fetch_historical_replay_assets,
        migrate_historical_snapshot_identity,
        record_canonical_historical_snapshots,
    )

    assets = fetch_historical_replay_assets(start_date=start_date, end_date=end_date)
    supplementation = supplement_database_future_prices(
        assets,
        end_date=end_date,
        symbol_offset=symbol_offset,
        max_symbols=max_symbols,
    )
    result = build_historical_5d_profit_window_dataset(assets)
    snapshot_rows = []
    snapshot_errors = []
    snapshot_rows = [
        snapshot for snapshot in result.get("canonical_historical_snapshots") or []
        if isinstance(snapshot, dict)
    ]
    try:
        result["historical_snapshot_identity_migration"] = migrate_historical_snapshot_identity()
        record_canonical_historical_snapshots(snapshot_rows)
    except Exception as exc:
        snapshot_errors.append({
            "error": f"{type(exc).__name__}:{exc}",
        })
    result["canonical_historical_snapshot_persistence"] = {
        "status": "PASS" if not snapshot_errors else "PARTIAL",
        "attempted": len(snapshot_rows),
        "errors": snapshot_errors[:20],
        "error_count": len(snapshot_errors),
    }
    result["future_price_supplementation"] = supplementation
    result["database_asset_report"] = database_asset_report()
    if research_artifact_path is not None:
        result["research_artifact_path"] = str(research_artifact_path)
    return persist_historical_replay(result)


def replay_historical_root(root: str | Path = DEFAULT_HISTORICAL_ROOT, **kwargs: Any) -> Dict[str, Any]:
    source_rows = _source_rows(Path(root))
    valid, rejected = [], []
    for row in source_rows:
        try:
            valid.append(canonical_historical_snapshot(
                row,
                trade_date=str(row.get("_trade_date") or ""),
                signal_time=str(row.get("signal_time") or row.get("source_time") or ""),
                source_timestamp=str(row.get("source_timestamp") or row.get("source_time") or ""),
            ))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            rejected.append({"trade_date": row.get("_trade_date"), "path": row.get("_path"), "reason": str(exc)})
    future_loader = kwargs.pop("future_loader", None)
    future_load_errors = []
    if future_loader:
        entries = []
        for snapshot in valid:
            try:
                entries.append({"snapshot": snapshot, "future_bars": future_loader(snapshot)})
            except Exception as exc:
                future_load_errors.append({"symbol": snapshot["symbol"], "trade_date": snapshot["trade_date"], "error": repr(exc)})
                entries.append({"snapshot": snapshot, "future_bars": []})
    else:
        # Fetch each symbol once from its earliest replay date, then slice by
        # T date. This keeps the historical source canonical and resumable.
        from collections import defaultdict
        from xiaogu_forward_result_filler_v0_1 import eastmoney_future_bars
        by_symbol = defaultdict(list)
        for snapshot in valid:
            by_symbol[snapshot["symbol"]].append(snapshot)
        bars_by_symbol = {}
        for symbol, symbol_snapshots in by_symbol.items():
            earliest = min(str(item["trade_date"]) for item in symbol_snapshots)
            try:
                bars_by_symbol[symbol] = eastmoney_future_bars(symbol, entry_date=earliest)
            except Exception as exc:
                future_load_errors.append({"symbol": symbol, "trade_date": earliest, "error": repr(exc)})
                bars_by_symbol[symbol] = []
        entries = [
            {
                "snapshot": snapshot,
                "future_bars": [
                    bar for bar in bars_by_symbol.get(snapshot["symbol"], [])
                    if str(bar.get("trade_date") or bar.get("date") or "") > str(snapshot["trade_date"])
                ],
            }
            for snapshot in valid
        ]
    result = historical_replay(entries, **kwargs) if entries else historical_replay([], **kwargs)
    result["historical_input_audit"] = {
        "source_rows": len(source_rows), "accepted_rows": len(valid), "rejected_rows": len(rejected),
        "rejected_examples": rejected[:20],
        "non_input_field_filter": list(NON_INPUT_KEY_MARKERS),
        "future_price_load_errors": future_load_errors[:20],
        "future_price_load_error_count": len(future_load_errors),
    }
    result["alpha_validation"] = "BLOCKED" if rejected or result["target_quality_gate"]["status"] != "PASS" else "ELIGIBLE"
    if kwargs.get("persist_path"):
        compact = dict(result)
        compact.pop("rows", None)
        compact.pop("decisions", None)
        Path(kwargs["persist_path"]).write_text(
            json.dumps(compact, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return result


def eastmoney_future_loader(snapshot: Dict[str, Any], *, end_date: str | None = None) -> List[Dict[str, Any]]:
    """Load future bars through the existing filler source owner."""
    from xiaogu_forward_result_filler_v0_1 import eastmoney_future_bars
    return eastmoney_future_bars(
        str(snapshot["symbol"]), entry_date=str(snapshot["trade_date"]), end_date=end_date,
    )


def main() -> int:
    """Run the database-first replay and emit research artifacts."""
    parser = argparse.ArgumentParser(
        description="Build the database-first five-day profit-window replay."
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--symbol-offset", type=int, default=0)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument(
        "--dataset-path",
        default=str(Path("data/research/historical_5d_profit_window_dataset.json")),
    )
    parser.add_argument(
        "--report-path",
        default=str(Path("data/research/historical_5d_profit_window_report.json")),
    )
    args = parser.parse_args()
    try:
        result = replay_database_history(
            start_date=args.start_date,
            end_date=args.end_date,
            symbol_offset=args.symbol_offset,
            max_symbols=args.max_symbols,
            research_artifact_path=args.dataset_path,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "DATABASE_UNAVAILABLE",
            "error": repr(exc),
            "dataset_path": args.dataset_path,
            "report_path": args.report_path,
        }, ensure_ascii=False))
        return 2

    report = dict(result.get("alpha_report") or {})
    report.update({
        "dataset_name": "historical_5d_profit_window_dataset",
        "read_only": True,
        "database_asset_report": result.get("database_asset_report"),
        "counts": result.get("counts"),
        "audit": result.get("audit"),
        "target_quality_gate": result.get("target_quality_gate"),
        "core_alpha_status": result.get("core_alpha_status"),
    })
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    DEFAULT_CALIBRATION_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    calibration = dict(report.get("calibration") or {})
    calibration["production_gates"] = report.get("production_gates") or {}
    calibration["production_alpha_permissions"] = report.get("production_alpha_permissions") or {}
    calibration["family_oos_increment"] = report.get("family_oos_increment") or {}
    DEFAULT_CALIBRATION_ARTIFACT.write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "dataset_path": args.dataset_path,
        "report_path": args.report_path,
        "calibration_path": str(DEFAULT_CALIBRATION_ARTIFACT),
        "counts": result.get("counts"),
        "target_quality_gate": result.get("target_quality_gate"),
        "core_alpha_status": result.get("core_alpha_status"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
