"""Point-in-time historical replay and label validation for Xiaogu 3.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from xiaogu_forward_bundle_io import load_snapshot as _load_snapshot
from xiaogu_forward_result_filler_v0_1 import (
    PRICE_BASIS,
    canonical_future_prices,
    calculate_horizon_outcomes,
    historical_entry_contract,
)
from xiaogu_horizon_evaluation import (
    HORIZONS,
    HISTORICAL_VALIDATION_HORIZONS,
    build_alpha_report,
    evaluate_decision_buckets,
    evaluate_feature_groups,
    evaluate_replay,
    portfolio_metrics,
    target_quality_gate,
)
from xiaogu_portfolio_decision import evaluate_candidate_bundle

HISTORICAL_SNAPSHOT_VERSION = "canonical_historical_snapshot_v1"
HISTORICAL_TARGET_VERSION = "profit_window_5d_labels_v1"
MODEL_VERSION = "profit_window_alpha_5d_v1"
TARGET_QUALITY = ("CANONICAL", "PARTIAL", "CONFLICT", "INVALID")
LEGACY_FIELDS = {
    "legacy_final_score", "final_score", "formal_primary_score", "production_score",
    "score", "structured_score", "structured_priority_score", "expected_edge_score",
    "fund_flow_score", "theme_strength_score", "small_account_score",
    "multi_agent_consensus_score", "source_health_score", "kline_language_score",
    "rank", "formal_rank", "pool_rank", "scanner_rank", "candidate_rank",
    "candidate_priority", "expected_profit_score", "t1_return", "t1_close_return",
    "expected_t1_profit_score", "capital_bonus", "flow_bonus", "news_bonus",
    "market_bonus", "sentiment_bonus", "fundamental_bonus", "risk_penalty",
    "topic_heat_bonus", "sector_rotation_bonus", "evidence_contribution",
    "candidate_evaluation_decision", "paper_pick_eligibility", "selection_outcome",
    "candidate_evaluation_reason", "selection_outcome_reason", "selection_reason",
}
RAW_MARKET_FIELDS = {
    "symbol", "code", "name", "stock_name", "sector", "industry", "sector_name",
    "trade_date", "date", "source_time", "signal_time", "available_at", "source_timestamp",
    "price", "close", "close_price", "open", "open_price", "high", "high_price",
    "low", "low_price", "prev_close", "volume", "amount", "turnover", "turnover_rate",
    "pct_chg", "signal_pct", "f2", "f3", "f5", "f6", "f7", "f8", "f12", "f14",
    "f15", "f16", "f17", "f18", "f43", "f44", "f45", "f46", "f47", "f48", "f62",
    "f100", "f168", "f184", "halted", "in_halted", "is_suspended", "regulatory_hard_block",
    "risk_hard_block",
}
DEFAULT_HISTORICAL_ROOT = Path(__file__).resolve().parent / "data" / "historical_replay_snapshots"
CONFIG_PATH = Path(__file__).resolve().parent / "rule_freeze_v0_1.json"
CONFIG_HASH = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
DEFAULT_FUTURE_BAR_CACHE = Path("/tmp/xiaogu-historical-future-bars.json")


def load_snapshot(path: str | Path) -> Dict[str, Any]:
    return _load_snapshot(Path(path))


def run_production_decision(snapshot: Dict[str, Any], portfolio_state: str = "WATCH") -> Dict[str, Any]:
    source_time = str(snapshot.get("signal_time") or snapshot.get("source_time") or snapshot.get("as_of") or "")
    try:
        as_of = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"INVALID_SIGNAL_TIME:{source_time}") from exc
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return evaluate_candidate_bundle(snapshot, portfolio_state=portfolio_state, as_of=as_of)


def calculate_portfolio_metrics(returns: Iterable[float]) -> Dict[str, Any]:
    return portfolio_metrics(returns)


def _strip_legacy(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _strip_legacy(value)
            for key, value in payload.items()
            if key not in {"future_prices", "future_bars", "outcomes", "labels"}
            and key not in LEGACY_FIELDS
            and not str(key).lower().startswith(("future_", "actual_"))
            and not re.match(r"^t\d+[_-]", str(key), re.I)
        }
    if isinstance(payload, list):
        return [_strip_legacy(value) for value in payload]
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
    if pick.get("id") is not None:
        return f"pick:{pick['id']}"
    if candidate.get("id") is not None:
        return f"candidate:{candidate['id']}"
    run = candidate.get("production_run_id") or candidate.get("candidate_snapshot_id")
    if run:
        return f"snapshot:{run}"
    raise ValueError("HISTORICAL_DECISION_ID_REQUIRED")


def _relation_key(row: Dict[str, Any]) -> tuple[Any, ...] | None:
    run = row.get("production_run_id")
    snapshot = row.get("candidate_snapshot_id")
    if run and snapshot:
        return (str(run), str(snapshot), str(row.get("symbol") or "").zfill(6))
    return None


def _pick_snapshot(pick: Dict[str, Any], candidate: Dict[str, Any] | None) -> Dict[str, Any]:
    features = _json_row(pick, "features")
    candidate = candidate or {}
    candidate_features = _json_row(candidate, "candidate_features")
    raw_json = _json_row(candidate, "raw_json")
    # Candidate facts are preferred; pick payload supplies the persisted decision
    # evidence when no exact candidate snapshot is available.
    source = {
        **_compact_snapshot_source(_strip_legacy(raw_json)),
        **_compact_snapshot_source(_strip_legacy(candidate_features)),
        **_compact_snapshot_source(_strip_legacy(features)),
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
        key: _strip_legacy(value)
        for key, value in {
            "candidate_features": _compact_snapshot_source(candidate_features),
            "eligibility_snapshot": _json_row(candidate, "eligibility_snapshot") or _json_row(features, "eligibility_snapshot"),
            "factor_snapshot": _compact_snapshot_source(_json_row(candidate, "factor_snapshot")),
            "auxiliary_evidence_snapshot": _compact_snapshot_source(_json_row(candidate, "auxiliary_evidence_snapshot")),
            "ranking_basis": _json_row(candidate, "ranking_basis") or _json_row(pick, "ranking_basis"),
            "source_layers": candidate.get("source_layers") or pick.get("source_layers") or [],
        }.items()
        if value not in (None, "", {}, [])
    })
    # Normalize persisted T-day evidence into names consumed by the current
    # feature owner. These are raw measurements, never legacy scores/labels.
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


def _entry_audit(return_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    candidates: list[tuple[str, Any]] = []
    derived_candidates: list[tuple[str, Any]] = []
    for row in return_rows:
        evidence = _json_row(row, "settlement_evidence")
        contract = _json_row(evidence, "execution_contract")
        candidates.extend((name, value) for name, value in (
            ("returns.entry_price", row.get("entry_price")),
            ("settlement_evidence.entry_price", evidence.get("entry_price")),
            ("execution_contract.execution_price", contract.get("execution_price")),
            ("execution_contract.signal_price", contract.get("signal_price")),
        ) if _number(value) is not None and _number(value) > 0)
        execution_model = _json_row(evidence, "execution_model")
        derived_candidates.extend((name, value) for name, value in (
            ("execution_model.entry_reference_price", execution_model.get("entry_reference_price")),
            ("execution_model.entry_execution_price", execution_model.get("entry_execution_price")),
        ) if _number(value) is not None and _number(value) > 0)
    values = [value for _, value in candidates]
    metadata = {
        "signal_date": return_rows[0].get("trade_date") if return_rows else None,
        "signal_time": None,
        "symbol": return_rows[0].get("symbol") if return_rows else None,
        "entry_price": None,
        "execution_price": None,
        "price_basis": None,
        "source": None,
        "production_run_id": return_rows[0].get("production_run_id") if return_rows else None,
        "feature_version": None,
        "alpha_version": None,
        "decision_version": None,
        "issues": [],
        "candidates": candidates,
        "derived_candidates": derived_candidates,
        "t1_open": return_rows[0].get("t1_open_price") if return_rows else None,
        "t1_high": return_rows[0].get("t1_high_price") if return_rows else None,
        "t1_low": return_rows[0].get("t1_low_price") if return_rows else None,
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
        ) for name, value in candidates if name == source
    )
    evidence = _json_row(return_rows[0], "settlement_evidence")
    contract = _json_row(evidence, "execution_contract")
    execution_price = next(
        (_number(value) for name, value in candidates if name == "execution_contract.execution_price"),
        selected,
    )
    metadata.update({
        "entry_price": selected,
        "execution_price": execution_price,
        "price_basis": _first_value(return_rows[0].get("entry_price_basis"), evidence.get("price_basis")),
        "source": _first_value(return_rows[0].get("entry_price_source"), evidence.get("price_source")),
        "signal_time": _first_value(contract.get("signal_time"), return_rows[0].get("entry_time")),
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
    cost_rate = 0.003
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
        if all(days[str(day)].get(field) not in (None, "") for field in ("open", "high", "low", "close"))
    ]
    opportunity_values = [
        days[str(day)]["daily_bar_profit_opportunity"]
        for day in complete_days
        if "daily_bar_profit_opportunity" in days[str(day)]
    ]
    mae_values = [
        days[str(day)]["mae"] for day in complete_days if "mae" in days[str(day)]
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

    cost_rate = float(targets.get("execution_cost_rate") or 0.003)
    complete_days = []
    for day in range(1, 6):
        item = days[str(day)]
        if not all(_number(item.get(field)) is not None for field in ("open", "high", "low", "close")):
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
        if days[str(day)].get("daily_bar_profit_opportunity", -1) >= float(targets.get("profit_window_target") or 0.02)
    ] if complete else []
    first_profit = profitable[0] if profitable else None
    targets.update({
        "max_daily_bar_profit_opportunity_5d": max(opportunities, default=None),
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


def supplement_database_future_prices(
    assets: Dict[str, List[Dict[str, Any]]],
    *,
    cache_path: str | Path = DEFAULT_FUTURE_BAR_CACHE,
    end_date: str | None = None,
    max_retries: int = 1,
    retry_delay: float = 0.25,
    request_timeout: int = 10,
    max_errors: int = 3,
) -> Dict[str, Any]:
    """Fetch only missing future OHLC for DB-linked decisions.

    Database canonical bars are loaded first. External bars are a bounded
    fallback for missing future evidence and are never used as T-day inputs.
    """
    from xiaogu_forward_result_filler_v0_1 import fetch_eastmoney_daily_bars

    path = Path(cache_path)
    cache = _cache_read(path)
    existing = list(assets.get("canonical_future_prices") or [])
    existing_by_symbol: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for bar in existing:
        if isinstance(bar, dict):
            existing_by_symbol[str(bar.get("symbol") or "").zfill(6)].append(bar)

    ranges = _database_linked_decision_ranges(assets)
    requested_end = str(end_date or date.today().isoformat())
    fetched_bars: list[Dict[str, Any]] = []
    errors = []
    cache_hits = 0
    fetched_symbols = 0
    for symbol, earliest in sorted(ranges.items()):
        if _bars_cover_five_days(existing_by_symbol.get(symbol, []), earliest):
            continue
        cached = cache["symbols"].get(symbol) or {}
        cached_bars = cached.get("bars") if isinstance(cached, dict) else None
        if (
            isinstance(cached_bars, list)
            and str(cached.get("start_date") or "") <= earliest
            and str(cached.get("end_date") or "") >= requested_end
            and _bars_cover_five_days(cached_bars, earliest)
        ):
            fetched_bars.extend(cached_bars)
            cache_hits += 1
            continue

        last_error = None
        bars: list[Dict[str, Any]] = []
        for attempt in range(max(1, max_retries)):
            try:
                bars = fetch_eastmoney_daily_bars(
                    symbol,
                    start_date=earliest,
                    end_date=requested_end,
                    timeout=request_timeout,
                )
                last_error = None
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{exc}"
                if attempt + 1 < max(1, max_retries):
                    time.sleep(retry_delay * (attempt + 1))
        fetched_symbols += 1
        if last_error:
            errors.append({"symbol": symbol, "start_date": earliest, "error": last_error})
            if len(errors) >= max(1, max_errors):
                break
            continue
        normalized = canonical_future_prices(
            bars,
            symbol=symbol,
            source_timestamp=datetime.now(timezone.utc).isoformat(),
            price_basis=PRICE_BASIS,
        )
        cached_bars = normalized
        cache["symbols"][symbol] = {
            "start_date": earliest,
            "end_date": requested_end,
            "bars": normalized,
            "source": "eastmoney_api_daily_kline",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        fetched_bars.extend(normalized)

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
        _cache_write(path, cache)
        try:
            from xiaogu_db import init_db, record_canonical_future_prices
            init_db()
            record_canonical_future_prices(new_bars)
            persistence = {"status": "PASS", "rows": len(new_bars)}
        except Exception as exc:
            persistence = {"status": "FAILED", "error": f"{type(exc).__name__}:{exc}"}
    else:
        persistence = {"status": "SKIPPED", "rows": 0}
        if cache.get("symbols"):
            _cache_write(path, cache)
    return {
        "status": "PASS" if not errors else "PARTIAL",
        "linked_symbols": len(ranges),
        "fetched_symbols": fetched_symbols,
        "cache_hits": cache_hits,
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
        "core_alpha": {
            key: alpha.get(key)
            for key in (
                "thesis_score", "profit_window_probability", "expected_net_profit_window",
                "expected_time_to_profit", "expected_mae_5d", "repricing_state",
                "accumulation_phase", "capital_convergence",
                "profit_window_feature_values", "axes", "supply_absorption",
                "future_buyer_capacity", "pricing_gap", "execution_feasibility",
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
    returns_by_relation: Dict[tuple[Any, ...], list[Dict[str, Any]]] = {}
    for row in returns:
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
    audit = {
        "unresolved_returns": [row.get("id") for row in returns if row.get("id") not in resolved_return_ids],
        "unresolved_decisions": [],
        "entry_audits": [],
    }
    for pick, candidate, linked in source_decisions:
        decision_id = _historical_decision_id(pick=pick, candidate=candidate)
        if not linked:
            dataset.append({
                "historical_decision_id": decision_id,
                "symbol": str((pick or candidate).get("symbol") or "").zfill(6),
                "signal_date": str((pick or candidate).get("trade_date") or ""),
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
                "entry_audit": None,
                "feature_version": "price_formation_measurements_v1",
                "alpha_version": MODEL_VERSION,
                "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
                "target_version": HISTORICAL_TARGET_VERSION,
                "production_run_id": (pick or candidate).get("production_run_id"),
                "pick_id": pick.get("id") if pick else None,
                "candidate_id": candidate.get("id"),
                "replay_error": None,
            })
            audit["unresolved_decisions"].append(decision_id)
            continue
        entry = _entry_audit(linked)
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
            )
            current = run_production_decision(snapshot)
        except Exception as exc:
            replay_error = f"{type(exc).__name__}:{exc}"
            if quality == "CANONICAL":
                quality = "INVALID"
                issues.append("CURRENT_REPLAY_FAILED")
        alpha = (current or {}).get("core_alpha") or {}
        compact_current = _compact_current_decision(current)
        dataset.append({
            "historical_decision_id": decision_id,
            "decision_id": decision_id,
            "symbol": symbol,
            "signal_date": trade_date,
            "signal_time": entry.get("signal_time"),
            "historical_original_decision": (pick or candidate).get("decision") if pick else candidate.get("selection_outcome"),
            "current_decision": (current or {}).get("state"),
            "current_decision_payload": compact_current,
            "canonical_entry_price": entry.get("entry_price"),
            "entry_price": entry.get("entry_price"),
            "entry_price_source": entry.get("source"),
            **targets,
            "capital_convergence": alpha.get("capital_convergence"),
            "capital_convergence_level": _capital_convergence_level(alpha.get("capital_convergence")),
            "repricing_state": alpha.get("repricing_state"),
            "accumulation_phase": ((current or {}).get("portfolio_state") or {}).get("accumulation_status") or alpha.get("accumulation_phase"),
            "target_quality": quality,
            "quality_issues": sorted(set(issues)),
            "entry_audit": entry,
            "historical_feature_version": (pick or candidate).get("data_version"),
            "historical_alpha_version": (pick or candidate).get("rule_version"),
            "historical_decision_version": (
                run.get("runner_version")
                or run_payload.get("runner_version")
                or (pick or candidate).get("rule_version")
            ),
            "feature_version": "price_formation_measurements_v1",
            "alpha_version": MODEL_VERSION,
            "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
            "target_version": HISTORICAL_TARGET_VERSION,
            "production_run_id": entry.get("production_run_id"),
            "pick_id": (pick or {}).get("id"),
            "candidate_id": candidate.get("id"),
            "replay_error": replay_error,
        })
        audit["entry_audits"].append({"historical_decision_id": decision_id, **entry, "quality": quality, "issues": issues})

    canonical = [row for row in dataset if row["target_quality"] == "CANONICAL"]
    partial = [row for row in dataset if row["target_quality"] == "PARTIAL"]
    conflict = [row for row in dataset if row["target_quality"] == "CONFLICT"]
    invalid = [row for row in dataset if row["target_quality"] == "INVALID"]
    gate = target_quality_gate(dataset, min_coverage=0.95, horizons=HISTORICAL_VALIDATION_HORIZONS)
    report = build_alpha_report(canonical, quality_gate=gate, horizons=HISTORICAL_VALIDATION_HORIZONS)
    report["decision_buckets"] = evaluate_decision_buckets(canonical)
    diagnostic = [row for row in dataset if row["target_quality"] in {"CANONICAL", "PARTIAL"}]
    report["diagnostic_sample_count"] = len(diagnostic)
    report["diagnostic_decision_buckets"] = evaluate_decision_buckets(diagnostic)
    report["diagnostic_feature_groups"] = evaluate_feature_groups(diagnostic)
    return {
        "dataset_name": "historical_5d_profit_window_dataset",
        "read_only": True,
        "rows": dataset,
        "database_asset_report": None,
        "audit": audit,
        "counts": {"historical_decisions": len(source_decisions), "dataset": len(dataset), "canonical": len(canonical), "partial": len(partial), "conflict": len(conflict), "invalid": len(invalid)},
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
) -> Dict[str, Any]:
    """Create a PIT row from T-day facts, never from old score payloads."""
    if not isinstance(row, dict):
        raise TypeError("HISTORICAL_SNAPSHOT_MUST_BE_OBJECT")
    clean = _strip_legacy(row)
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
        for key in keys:
            value = clean.get(key)
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
    snapshot["lineage_id"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
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
        "thesis_score": alpha.get("thesis_score"),
        "profit_window_probability": alpha.get("profit_window_probability"),
        "expected_max_profit_5d": alpha.get("expected_max_profit_5d"),
        "expected_time_to_profit": alpha.get("expected_time_to_profit"),
        "expected_mae_5d": alpha.get("expected_mae_5d"),
        "expected_net_profit_window": alpha.get("expected_net_profit_window"),
        "capital_convergence": alpha.get("capital_convergence"),
        "entry_contract": entry,
        "model_registry": {
            "model_version": MODEL_VERSION, "feature_version": "price_formation_measurements_v1",
            "target_version": HISTORICAL_TARGET_VERSION, "data_version": snapshot.get("snapshot_version", "legacy_fixture"),
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
        decision = run_production_decision(snapshot)
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
    result = {
        "decisions": decisions, "decision_records": decision_records, "rows": rows,
        "metrics": report["horizon_metrics"], "horizon_metrics": report["horizon_metrics"],
        "horizons": HISTORICAL_VALIDATION_HORIZONS, "target_quality_gate": gate,
        "alpha_validation": "BLOCKED" if gate["status"] != "PASS" else "ELIGIBLE",
        "outcome_boundary": "OUTCOMES_ENTER_AFTER_PRODUCTION_DECISION",
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
    artifact.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
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
    research_artifact_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Build the database-only five-day replay without altering source rows."""
    from xiaogu_db import database_asset_report, fetch_historical_replay_assets

    assets = fetch_historical_replay_assets(start_date=start_date, end_date=end_date)
    supplementation = supplement_database_future_prices(assets, end_date=end_date)
    result = build_historical_5d_profit_window_dataset(assets)
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
        "legacy_fields_ignored": sorted(LEGACY_FIELDS),
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
    print(json.dumps({
        "status": "PASS",
        "dataset_path": args.dataset_path,
        "report_path": args.report_path,
        "counts": result.get("counts"),
        "target_quality_gate": result.get("target_quality_gate"),
        "core_alpha_status": result.get("core_alpha_status"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
