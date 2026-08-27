"""Outcome-only evaluation for the five-day realizable profit window."""
from __future__ import annotations

import random
from statistics import median
from typing import Any, Dict, Iterable, List

HORIZONS = (5,)
HISTORICAL_VALIDATION_HORIZONS = HORIZONS
MIN_TARGET_COVERAGE = 0.95
PROFIT_WINDOW_TARGET = 0.02


def validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(horizon) for horizon in horizons)
    if any(horizon != 5 for horizon in values):
        raise ValueError(f"UNSUPPORTED_HORIZON:{values}")
    return values


def evaluate_5d(entry_price: float, prices: Dict[int, Any]) -> Dict[str, Any]:
    """Evaluate a five-day close path when OHLC bars are not available."""
    if entry_price <= 0:
        return {"horizon_days": 5, "data_status": "DATA_INSUFFICIENT", "profit_window": False}
    path = [prices.get(day) for day in (1, 2, 3, 4, 5)]
    if any(value is None for value in path):
        return {"horizon_days": 5, "data_status": "DATA_INSUFFICIENT", "profit_window": False}
    net_values = [(float(value) - entry_price) / entry_price - 0.003 for value in path]
    first = next((index for index, value in enumerate(net_values, 1) if value >= 0.02), None)
    return {
        "horizon_days": 5,
        "data_status": "PASS",
        "profit_window": first is not None,
        "max_realizable_profit_5d": max(net_values),
        "first_profit_day": first,
        "time_to_profit": first,
        "max_mae_5d": min((float(value) - entry_price) / entry_price for value in path),
        "net_profit_window": max(0.0, max(net_values)),
    }


def _label(row: Dict[str, Any], field: str) -> Any:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else row
    return labels.get(field)


def _day_return(row: Dict[str, Any], day: int) -> Any:
    return _label(row, f"t{day}_return") if _label(row, f"t{day}_return") is not None else _label(row, f"future_{day}d_return")


def _canonical_entry_present(row: Dict[str, Any]) -> bool:
    if row.get("canonical_entry_price") not in (None, "", 0, 0.0):
        return True
    contract = row.get("entry_contract")
    if isinstance(contract, dict) and contract.get("entry_price") not in (None, "", 0, 0.0):
        return True
    decision_record = row.get("decision_record")
    return (
        isinstance(decision_record, dict)
        and isinstance(decision_record.get("entry_contract"), dict)
        and decision_record["entry_contract"].get("entry_price") not in (None, "", 0, 0.0)
    )


def _window_values(row: Dict[str, Any]) -> list[float]:
    values = _label(row, "daily_realizable_profit")
    return [float(value) for value in values or [] if value is not None]


def portfolio_metrics(values: Iterable[float]) -> Dict[str, Any]:
    values = [float(value) for value in values if value is not None]
    positives = [value for value in values if value > 0]
    negatives = [abs(value) for value in values if value < 0]
    equity = peak = max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "samples": len(values),
        "profit_window_rate": sum(value >= PROFIT_WINDOW_TARGET for value in values) / len(values) if values else None,
        "mean_realizable_profit": sum(values) / len(values) if values else None,
        "median_realizable_profit": median(values) if values else None,
        "mae": None,
        "mean_mae": None,
        "profit_factor": sum(positives) / sum(negatives) if negatives else None,
        "max_drawdown": max_drawdown if values else None,
        "average_time_to_profit": None,
    }


def target_quality_gate(rows: Iterable[Dict[str, Any]], *, min_coverage: float = MIN_TARGET_COVERAGE, horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    """Fail closed unless canonical entry and every source-backed target exist."""
    rows = list(rows)
    horizons = validate_horizons(horizons)
    total = len(rows)
    entry = sum(
        _canonical_entry_present(row)
        for row in rows
    ) / total if total else 0.0
    horizon_coverage = {}
    for day in range(1, 6):
        horizon_coverage[str(day)] = sum(
            _day_return(row, day) is not None
            or (day == 5 and bool(_label(row, "future_5d_ohlc_coverage")))
            for row in rows
        ) / total if total else 0.0
    complete = sum(
        _canonical_entry_present(row)
        and all(
            _day_return(row, day) is not None
            or (day == 5 and bool(_label(row, "future_5d_ohlc_coverage")))
            for day in range(1, 6)
        )
        for row in rows
    ) / total if total else 0.0
    checks = {
        "T+1": horizon_coverage["1"] >= min_coverage,
        "T+2": horizon_coverage["2"] >= min_coverage,
        "T+3": horizon_coverage["3"] >= min_coverage,
        "T+4": horizon_coverage["4"] >= min_coverage,
        "T+5": horizon_coverage["5"] >= min_coverage,
        "complete_5d": complete >= min_coverage,
        "entry": entry >= min_coverage,
    }
    status = "PASS" if total and all(checks.values()) else "BLOCKED"
    return {
        "status": status,
        "reason": None if status == "PASS" else "DATA_INSUFFICIENT",
        "threshold": min_coverage,
        "samples": total,
        "horizons": list(horizons),
        "entry_coverage": entry,
        "horizon_coverage": {
            str(day): {"return": horizon_coverage[str(day)]}
            for day in range(1, 6)
        },
        "complete_5d_coverage": complete,
        "checks": checks,
    }


def bootstrap_confidence_interval(values: Iterable[float], *, seed: int = 0, samples: int = 2000, confidence: float = 0.95) -> Dict[str, Any]:
    values = [float(value) for value in values if value is not None]
    if not values:
        return {"low": None, "high": None, "confidence": confidence, "samples": 0}
    rng = random.Random(seed)
    means = [sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples)]
    means.sort()
    return {
        "low": means[int((1.0 - confidence) / 2.0 * samples)],
        "high": means[min(samples - 1, int((1.0 + confidence) / 2.0 * samples))],
        "confidence": confidence,
        "samples": len(values),
    }


def evaluate_replay(rows: Iterable[Dict[str, Any]], *, quality_gate: Dict[str, Any] | None = None, horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    rows = list(rows)
    horizons = validate_horizons(horizons)
    gate = quality_gate or target_quality_gate(rows, horizons=horizons)
    values = [_label(row, "max_realizable_profit_5d") for row in rows]
    values = [float(value) for value in values if value is not None]
    metrics = portfolio_metrics(values)
    mfe_values = [
        float(value)
        for row in rows
        for value in [_label(row, "mfe_5d"), _label(row, "future_5d_mfe")]
        if value is not None
    ]
    metrics["mfe"] = max(mfe_values) if mfe_values else None
    metrics["mean_mfe"] = sum(mfe_values) / len(mfe_values) if mfe_values else None
    mae_values = [
        float(value)
        for row in rows
        for value in [_label(row, "max_mae_5d"), _label(row, "mae_5d")]
        if value is not None
    ]
    metrics["mae"] = min(mae_values) if mae_values else None
    metrics["mean_mae"] = sum(mae_values) / len(mae_values) if mae_values else None
    metrics["average_time_to_profit"] = (
        sum(float(value) for row in rows for value in [_label(row, "time_to_profit")] if value is not None)
        / sum(_label(row, "time_to_profit") is not None for row in rows)
        if any(_label(row, "time_to_profit") is not None for row in rows) else None
    )
    metrics["bootstrap_ci"] = bootstrap_confidence_interval(values, seed=5)
    return {
        "status": "PASS" if gate.get("status") == "PASS" else "BLOCKED",
        "target_quality_gate": gate,
        "horizon_metrics": {"PROFIT_WINDOW_5D": metrics},
        "main_table": [{"Target": "PROFIT_WINDOW_5D", **metrics}],
    }


def evaluate_decision_buckets(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare persisted decisions and the current replay without relabeling rows."""
    rows = list(rows)
    buckets = {
        "OLD BUY": lambda row: str(row.get("historical_original_decision") or "").upper() in {"BUY", "PAPER_PICK"},
        "CURRENT BUY": lambda row: str(row.get("current_decision") or "").upper() == "BUY",
        "WATCH": lambda row: str(row.get("current_decision") or "").upper() == "WATCH",
    }
    return {
        name: evaluate_replay([row for row in rows if predicate(row)])[
            "horizon_metrics"
        ]["PROFIT_WINDOW_5D"]
        for name, predicate in buckets.items()
    }


def evaluate_feature_groups(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce diagnostic groups from T-day fields only."""
    rows = list(rows)

    def value(row: Dict[str, Any], key: str, default: str = "UNOBSERVED") -> str:
        raw = row.get(f"{key}_level") if key == "capital_convergence" else row.get(key)
        if raw is None:
            raw = row.get(key)
        if raw is None and isinstance(row.get("current_decision_payload"), dict):
            alpha = row["current_decision_payload"].get("core_alpha") or {}
            raw = alpha.get(key)
        if isinstance(raw, dict):
            raw = raw.get("state") or raw.get("value")
        return str(raw or default).upper()

    def grouped(key: str, names: Iterable[str]) -> Dict[str, Any]:
        return {
            name: evaluate_replay([row for row in rows if value(row, key) == name])[
                "horizon_metrics"
            ]["PROFIT_WINDOW_5D"]
            for name in names
        }

    return {
        "capital_convergence": grouped("capital_convergence", ("LOW", "MEDIUM", "HIGH", "CONVERGENT", "PARTIAL", "UNOBSERVED")),
        "repricing_state": grouped("repricing_state", ("ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION")),
        "accumulation_phase": grouped("accumulation_phase", ("ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION", "ORDINARY_TRADING", "UNOBSERVED")),
    }


def _blocked_group() -> Dict[str, Any]:
    return {"status": "BLOCKED", "samples": 0}


def evaluate_group(rows: Iterable[Dict[str, Any]], predicate, *, horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    validate_horizons(horizons)
    selected = [row for row in rows if predicate(row)]
    return {"PROFIT_WINDOW_5D": evaluate_replay(selected)["horizon_metrics"]["PROFIT_WINDOW_5D"]}


def evaluate_top_k(rows: Iterable[Dict[str, Any]], *, score_key: str = "thesis_score", ks: Iterable[int] = (1, 5, 10), horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    validate_horizons(horizons)
    ordered = sorted(rows, key=lambda row: float((row.get("decision", {}).get("core_alpha") or {}).get(score_key) or row.get(score_key) or 0.0), reverse=True)
    return {
        f"Top{k}": evaluate_replay(ordered[:k], horizons=horizons)["horizon_metrics"]
        for k in ks
    }


def build_alpha_report(rows: Iterable[Dict[str, Any]], *, quality_gate: Dict[str, Any] | None = None, horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    rows = list(rows)
    horizons = validate_horizons(horizons)
    gate = quality_gate or target_quality_gate(rows, horizons=horizons)
    evaluated = evaluate_replay(rows, quality_gate=gate, horizons=horizons)
    blocked = gate.get("status") != "PASS"
    return {
        "data_status": "BLOCKED" if blocked else "READY",
        "target_coverage": gate,
        "replay_sample_count": len(rows),
        "main_table": evaluated["main_table"],
        "top_k": evaluate_top_k(rows, horizons=horizons),
        "baseline_ladder": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("RANDOM", "PRICE", "PRICE + VOLUME", "PRICE + CAPITAL", "FULL CORE ALPHA")},
        "capital_convergence": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("CONVERGENT", "PARTIAL", "UNOBSERVED")},
        "supply_absorption": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("ABSORBING", "BALANCED", "RELEASING")},
        "pricing_gap": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("HIGH", "LOW")},
        "repricing_state": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION")},
        "decision_state": {name: {"PROFIT_WINDOW_5D": _blocked_group()} for name in ("WATCH", "READY", "BUY")},
        "decision_buckets": evaluate_decision_buckets(rows),
        "feature_groups": evaluate_feature_groups(rows),
        "core_alpha_status": "DATA_INSUFFICIENT" if blocked else "VALIDATED",
    }
