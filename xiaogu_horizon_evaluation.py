"""Point-in-time five-day profit-window evaluation and calibration.

This module owns research-time labels and validation only. It never creates
future features and it never emits a production portfolio state.
"""
from __future__ import annotations

import math
import random
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Sequence

HORIZONS = (1, 2, 3, 4, 5)
HISTORICAL_VALIDATION_HORIZONS = HORIZONS
MIN_TARGET_COVERAGE = 0.95
PROFIT_WINDOW_TARGET = 0.02
MIN_CALIBRATION_SAMPLES = 30
MIN_SPLIT_SAMPLES = 5

CORE_ALPHA_FEATURES = (
    "capital_convergence",
    "capital_persistence",
    "capital_acceleration",
    "supply_absorption",
    "pricing_gap",
    "repricing_state",
    "future_buyer_evidence",
    "reflexivity",
    "market_state",
    "execution_quality",
    "risk",
)

REPRICING_ENCODING = {
    "UNKNOWN": 0.0,
    "ACCUMULATION": 0.2,
    "IGNITION": 0.4,
    "EXPANSION": 0.6,
    "CLIMAX": 0.8,
    "DISTRIBUTION": 1.0,
}
MARKET_ENCODING = {
    "UNKNOWN": 0.0,
    "WEAK": 0.25,
    "SIDEWAYS": 0.5,
    "NEUTRAL": 0.5,
    "STRONG": 0.75,
    "BULL": 1.0,
}


def validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(horizon) for horizon in horizons)
    if values != HORIZONS:
        raise ValueError(f"UNSUPPORTED_HORIZON:{values}")
    return values


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _label(row: Dict[str, Any], field: str) -> Any:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else row
    return labels.get(field)


def _day_return(row: Dict[str, Any], day: int) -> Any:
    value = _label(row, f"t{day}_return")
    return value if value is not None else _label(row, f"future_{day}d_return")


def _day_ohlc_present(row: Dict[str, Any], day: int) -> bool:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else row
    days = labels.get("days")
    if not isinstance(days, dict):
        return False
    values = days.get(str(day), days.get(day))
    return isinstance(values, dict) and all(
        _number(values.get(field)) is not None
        for field in ("open", "high", "low", "close")
    )


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


def _outcome_profit(row: Dict[str, Any]) -> float | None:
    value = _label(row, "max_daily_bar_profit_opportunity_5d")
    if value is None:
        value = _label(row, "net_profit_window")
    return _number(value)


def _mfe(row: Dict[str, Any]) -> float | None:
    return _number(_label(row, "mfe_5d") or _label(row, "future_5d_mfe"))


def _mae(row: Dict[str, Any]) -> float | None:
    return _number(_label(row, "max_mae_5d") or _label(row, "mae_5d"))


def _time_to_profit(row: Dict[str, Any]) -> float | None:
    return _number(_label(row, "time_to_profit"))


def evaluate_5d(entry_price: float, prices: Dict[int, Any]) -> Dict[str, Any]:
    """Evaluate a close-path approximation when OHLC bars are unavailable."""
    if entry_price <= 0:
        return {"horizon_days": 5, "data_status": "INVALID", "profit_window": False}
    path = [prices.get(day) for day in HORIZONS]
    if any(value is None for value in path):
        return {"horizon_days": 5, "data_status": "PARTIAL", "profit_window": None}
    net_values = [(float(value) - entry_price) / entry_price - 0.003 for value in path]
    first = next((index for index, value in enumerate(net_values, 1) if value >= PROFIT_WINDOW_TARGET), None)
    return {
        "horizon_days": 5,
        "data_status": "COMPLETE",
        "profit_window": first is not None,
        "daily_bar_profit_opportunity": list(net_values),
        "max_daily_bar_profit_opportunity_5d": max(net_values),
        "first_profit_day": first,
        "time_to_profit": first,
        "max_mae_5d": min((float(value) - entry_price) / entry_price for value in path),
        "net_profit_window": max(0.0, max(net_values)),
        "realizability_level": "DAILY_BAR_APPROXIMATION",
    }


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
        "mean_bar_profit_opportunity": sum(values) / len(values) if values else None,
        "median_bar_profit_opportunity": median(values) if values else None,
        "mae": None,
        "mean_mae": None,
        "profit_factor": sum(positives) / sum(negatives) if negatives else None,
        "max_drawdown": max_drawdown if values else None,
        "average_time_to_profit": None,
    }


def target_quality_gate(
    rows: Iterable[Dict[str, Any]],
    *,
    min_coverage: float = MIN_TARGET_COVERAGE,
    horizons: Iterable[int] = HORIZONS,
) -> Dict[str, Any]:
    """Fail closed unless every training row has a canonical entry and 5D OHLC."""
    rows = list(rows)
    horizons = validate_horizons(horizons)
    total = len(rows)
    entry = sum(_canonical_entry_present(row) for row in rows) / total if total else 0.0
    return_coverage = {}
    ohlc_coverage = {}
    for day in HORIZONS:
        return_coverage[str(day)] = sum(
            _day_return(row, day) is not None
            for row in rows
        ) / total if total else 0.0
        ohlc_coverage[str(day)] = sum(
            _day_ohlc_present(row, day)
            for row in rows
        ) / total if total else 0.0
    complete = sum(
        _canonical_entry_present(row)
        and all(_day_ohlc_present(row, day) for day in HORIZONS)
        for row in rows
    ) / total if total else 0.0
    checks = {
        **{f"T+{day}_OHLC": ohlc_coverage[str(day)] >= min_coverage for day in HORIZONS},
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
            str(day): {
                "return": return_coverage[str(day)],
                "ohlc": ohlc_coverage[str(day)],
            }
            for day in HORIZONS
        },
        "complete_5d_coverage": complete,
        "checks": checks,
    }


def bootstrap_confidence_interval(
    values: Iterable[float],
    *,
    seed: int = 0,
    samples: int = 2000,
    confidence: float = 0.95,
) -> Dict[str, Any]:
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


def _alpha_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("current_decision_payload")
    if isinstance(payload, dict) and isinstance(payload.get("core_alpha"), dict):
        return payload["core_alpha"]
    for parent in ("decision_record", "decision", "core_alpha"):
        value = row.get(parent)
        if isinstance(value, dict):
            alpha = value.get("core_alpha") if parent != "core_alpha" else value
            if isinstance(alpha, dict):
                return alpha
    return {}


def _feature_value(row: Dict[str, Any], name: str) -> float:
    alpha = _alpha_payload(row)
    values = alpha.get("profit_window_feature_values")
    if not isinstance(values, dict):
        values = row.get("profit_window_feature_values")
    if isinstance(values, dict) and name in values:
        value = values[name]
        if isinstance(value, dict):
            value = value.get("score") or value.get("value")
        numeric = _number(value)
        if numeric is not None:
            return max(0.0, min(1.0, numeric))
    direct = row.get(name, alpha.get(name))
    if isinstance(direct, dict):
        direct = direct.get("score") or direct.get("state") or direct.get("value")
    numeric = _number(direct)
    if numeric is not None:
        return max(0.0, min(1.0, numeric))
    if name == "repricing_state":
        return REPRICING_ENCODING.get(str(row.get(name) or alpha.get(name) or "UNKNOWN").upper(), 0.0)
    if name == "market_state":
        raw = row.get(name) or alpha.get(name) or row.get("market_regime")
        if isinstance(raw, dict):
            raw = raw.get("regime") or raw.get("state") or raw.get("score")
        numeric = _number(raw)
        return max(0.0, min(1.0, numeric)) if numeric is not None else MARKET_ENCODING.get(str(raw or "UNKNOWN").upper(), 0.0)
    return 0.0


def _extra_feature_value(row: Dict[str, Any], name: str) -> float:
    alpha = _alpha_payload(row)
    if name == "price_strength":
        axes = alpha.get("axes") if isinstance(alpha.get("axes"), dict) else {}
        return max(0.0, min(1.0, _number(row.get(name), _number(axes.get("MARKET"), 0.0) or 0.0) or 0.0))
    if name == "market_score":
        axes = alpha.get("axes") if isinstance(alpha.get("axes"), dict) else {}
        return max(0.0, min(1.0, _number(row.get(name), _number(axes.get("MARKET"), 0.0) or 0.0) or 0.0))
    if name == "turnover":
        vector = row.get("feature_vector")
        if isinstance(vector, dict):
            supply = vector.get("SUPPLY") or {}
            return max(0.0, min(1.0, _number(supply.get("turnover_velocity") or supply.get("turnover"), 0.0) or 0.0))
        return max(0.0, min(1.0, _number(row.get(name), 0.0) or 0.0))
    return 0.0


def _feature_vector(row: Dict[str, Any], names: Sequence[str]) -> list[float]:
    return [
        _extra_feature_value(row, name) if name in {"price_strength", "market_score", "turnover"}
        else _feature_value(row, name)
        for name in names
    ]


def _label_value(row: Dict[str, Any]) -> int | None:
    value = _label(row, "profit_window")
    return None if value is None else int(bool(value))


def _complete_training_rows(rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        row for row in rows
        if _label_value(row) is not None
        and _outcome_profit(row) is not None
        and row.get("target_quality", "CANONICAL") == "CANONICAL"
    ]


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def _fit_logistic(rows: Sequence[Dict[str, Any]], names: Sequence[str]) -> Dict[str, Any] | None:
    if not rows or len({_label_value(row) for row in rows}) < 2:
        return None
    x = [_feature_vector(row, names) for row in rows]
    y = [float(_label_value(row)) for row in rows]
    rate = sum(y) / len(y)
    intercept = math.log(max(1e-6, min(1.0 - 1e-6, rate)) / max(1e-6, 1.0 - rate))
    weights = [0.0] * len(names)
    for _ in range(800):
        gradient_b = 0.0
        gradient_w = [0.0] * len(names)
        for values, target in zip(x, y):
            error = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, values))) - target
            gradient_b += error
            for index, value in enumerate(values):
                gradient_w[index] += error * value
        scale = 1.0 / len(rows)
        intercept -= 0.18 * gradient_b * scale
        for index in range(len(weights)):
            weights[index] -= 0.18 * (gradient_w[index] * scale + 0.01 * weights[index])
    return {"intercept": intercept, "coefficients": weights, "feature_names": list(names)}


def _predict(model: Mapping[str, Any], rows: Sequence[Dict[str, Any]]) -> list[float]:
    names = list(model.get("feature_names") or [])
    return [
        _sigmoid(float(model.get("intercept") or 0.0) + sum(
            float(weight) * value for weight, value in zip(model.get("coefficients") or [], _feature_vector(row, names))
        ))
        for row in rows
    ]


def _roc_auc(labels: Sequence[int], predictions: Sequence[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    order = sorted(range(len(labels)), key=lambda index: predictions[index])
    rank_sum = 0.0
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and predictions[order[end]] == predictions[order[index]]:
            end += 1
        rank = (index + 1 + end) / 2.0
        rank_sum += sum(labels[order[item]] for item in range(index, end)) * rank
        index = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _pr_auc(labels: Sequence[int], predictions: Sequence[float]) -> float | None:
    positives = sum(labels)
    if not positives:
        return None
    order = sorted(range(len(labels)), key=lambda index: predictions[index], reverse=True)
    found = area = previous_recall = 0.0
    for rank, index in enumerate(order, 1):
        if labels[index]:
            found += 1
            recall = found / positives
            area += (recall - previous_recall) * (found / rank)
            previous_recall = recall
    return area


def _calibration_table(labels: Sequence[int], predictions: Sequence[float], bins: int = 5) -> Dict[str, Any]:
    table = []
    total_error = 0.0
    for bucket in range(bins):
        lower, upper = bucket / bins, (bucket + 1) / bins
        selected = [
            index for index, prediction in enumerate(predictions)
            if lower <= prediction < upper or (bucket == bins - 1 and prediction == upper)
        ]
        if not selected:
            continue
        predicted = sum(predictions[index] for index in selected) / len(selected)
        actual = sum(labels[index] for index in selected) / len(selected)
        total_error += abs(predicted - actual) * len(selected)
        table.append({"lower": lower, "upper": upper, "samples": len(selected), "predicted": predicted, "actual": actual})
    return {"bins": table, "expected_calibration_error": total_error / len(labels) if labels else None}


def _prediction_metrics(rows: Sequence[Dict[str, Any]], predictions: Sequence[float]) -> Dict[str, Any]:
    labels = [int(_label_value(row)) for row in rows]
    profits = [_outcome_profit(row) for row in rows]
    maes = [_mae(row) for row in rows]
    times = [_time_to_profit(row) for row in rows]
    selected = [profit for profit, prediction in zip(profits, predictions) if prediction >= 0.5 and profit is not None]
    portfolio = portfolio_metrics([profit for profit in profits if profit is not None])
    calibration = _calibration_table(labels, predictions)
    true_positive = sum(label == 1 and prediction >= 0.5 for label, prediction in zip(labels, predictions))
    predicted_positive = sum(prediction >= 0.5 for prediction in predictions)
    actual_positive = sum(labels)
    return {
        "samples": len(rows),
        "profit_window_rate": actual_positive / len(labels) if labels else None,
        "precision": true_positive / predicted_positive if predicted_positive else 0.0,
        "recall": true_positive / actual_positive if actual_positive else None,
        "roc_auc": _roc_auc(labels, predictions),
        "pr_auc": _pr_auc(labels, predictions),
        "brier_score": sum((prediction - label) ** 2 for label, prediction in zip(labels, predictions)) / len(labels) if labels else None,
        "calibration_error": calibration["expected_calibration_error"],
        "calibration": calibration["bins"],
        "mean_profit": sum(profit for profit in profits if profit is not None) / len([profit for profit in profits if profit is not None])
        if any(profit is not None for profit in profits) else None,
        "median_profit": median([profit for profit in profits if profit is not None]) if any(profit is not None for profit in profits) else None,
        "mean_selected_profit": sum(selected) / len(selected) if selected else None,
        "mean_mae": sum(value for value in maes if value is not None) / len([value for value in maes if value is not None])
        if any(value is not None for value in maes) else None,
        "profit_factor": portfolio["profit_factor"],
        "max_drawdown": portfolio["max_drawdown"],
        "average_time_to_profit": sum(value for value in times if value is not None) / len([value for value in times if value is not None])
        if any(value is not None for value in times) else None,
    }


def _split_rows(rows: Sequence[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: str(row.get("signal_date") or row.get("trade_date") or ""))
    if len(ordered) < 3:
        return [], [], []
    train_end = max(1, int(len(ordered) * 0.6))
    validation_end = max(train_end + 1, int(len(ordered) * 0.8))
    validation_end = min(validation_end, len(ordered) - 1)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def _random_predictions(rows: Sequence[Dict[str, Any]], seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in rows]


def _baseline_features(name: str) -> tuple[str, ...]:
    return {
        "PRICE / MARKET": ("price_strength", "market_score"),
        "PRICE + VOLUME": ("price_strength", "market_score", "turnover"),
        "PRICE + CAPITAL": ("price_strength", "market_score", "capital_convergence", "capital_persistence", "capital_acceleration"),
        "PRICE + CAPITAL + SUPPLY": ("price_strength", "market_score", "capital_convergence", "capital_persistence", "capital_acceleration", "supply_absorption"),
        "PRICE + CAPITAL + SUPPLY + REPRICING": ("price_strength", "market_score", "capital_convergence", "capital_persistence", "capital_acceleration", "supply_absorption", "repricing_state"),
        "FULL CORE ALPHA": CORE_ALPHA_FEATURES,
    }.get(name, ())


def _fit_baseline(rows: Sequence[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    names = _baseline_features(name)
    return _fit_logistic(rows, names) if names else None


def _baseline_predictions(model: Mapping[str, Any], rows: Sequence[Dict[str, Any]]) -> list[float]:
    return _predict(model, rows)


def _monotonicity(rows: Sequence[Dict[str, Any]], predictions: Sequence[float]) -> Dict[str, Any]:
    if not rows:
        return {"status": "BLOCKED", "bins": []}
    ordered = sorted(zip(predictions, rows), key=lambda item: item[0])
    bins = []
    for index in range(5):
        selected = ordered[index * len(ordered) // 5:(index + 1) * len(ordered) // 5]
        if not selected:
            continue
        labels = [int(_label_value(row)) for _, row in selected]
        profits = [_outcome_profit(row) for _, row in selected if _outcome_profit(row) is not None]
        bins.append({
            "bin": f"P{index + 1}",
            "samples": len(selected),
            "predicted": sum(prediction for prediction, _ in selected) / len(selected),
            "profit_window_rate": sum(labels) / len(labels),
            "mean_profit": sum(profits) / len(profits) if profits else None,
            "mean_mae": sum(_mae(row) or 0.0 for _, row in selected) / len(selected),
        })
    rates = [item["profit_window_rate"] for item in bins]
    passed = bool(rates) and all(left <= right + 0.02 for left, right in zip(rates, rates[1:]))
    return {"status": "PASS" if passed else "FAIL", "bins": bins}


def calibrate_profit_window_probability(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Fit transparent chronological logistic regression and evaluate OOS."""
    complete = _complete_training_rows(rows)
    if len(complete) < MIN_CALIBRATION_SAMPLES:
        return {
            "status": "DATA_INSUFFICIENT",
            "reason": "INSUFFICIENT_COMPLETE_HISTORICAL_LABELS",
            "samples": len(complete),
            "train_samples": 0,
            "validation_samples": 0,
            "oos_samples": 0,
            "feature_names": list(CORE_ALPHA_FEATURES),
        }
    train, validation, oos = _split_rows(complete)
    if min(len(train), len(validation), len(oos)) < MIN_SPLIT_SAMPLES:
        return {
            "status": "DATA_INSUFFICIENT",
            "reason": "CHRONOLOGICAL_SPLIT_TOO_SMALL",
            "samples": len(complete),
            "train_samples": len(train),
            "validation_samples": len(validation),
            "oos_samples": len(oos),
            "feature_names": list(CORE_ALPHA_FEATURES),
        }
    model = _fit_logistic(train, CORE_ALPHA_FEATURES)
    if model is None:
        return {
            "status": "EXPERIMENTAL",
            "reason": "TRAINING_LABEL_HAS_ONE_CLASS",
            "samples": len(complete),
            "train_samples": len(train),
            "validation_samples": len(validation),
            "oos_samples": len(oos),
            "feature_names": list(CORE_ALPHA_FEATURES),
        }
    train_predictions = _predict(model, train)
    validation_predictions = _predict(model, validation)
    oos_predictions = _predict(model, oos)
    train_metrics = _prediction_metrics(train, train_predictions)
    validation_metrics = _prediction_metrics(validation, validation_predictions)
    oos_metrics = _prediction_metrics(oos, oos_predictions)
    monotonicity = _monotonicity(oos, oos_predictions)
    baseline_model = _fit_baseline(train, "PRICE / MARKET")
    baseline_metrics = _prediction_metrics(oos, _baseline_predictions(baseline_model, oos)) if baseline_model else {}
    passed = bool(
        oos_metrics.get("roc_auc") is not None
        and oos_metrics.get("pr_auc") is not None
        and oos_metrics.get("calibration_error") is not None
        and oos_metrics["calibration_error"] <= 0.15
        and monotonicity["status"] == "PASS"
        and (baseline_metrics.get("pr_auc") is None or oos_metrics["pr_auc"] >= baseline_metrics["pr_auc"])
    )
    return {
        **model,
        "model_id": "profit_window_logistic_5d_v1",
        "target": "PROFIT_WINDOW_5D",
        "status": "VALIDATED" if passed else "CALIBRATED",
        "samples": len(complete),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "oos_samples": len(oos),
        "train": train_metrics,
        "validation": validation_metrics,
        "oos": {**oos_metrics, "passed": passed},
        "monotonicity": monotonicity,
        "baseline_price_market_oos": baseline_metrics,
        "cost_model": {"transaction_cost": 0.003, "slippage": 0.0, "spread": 0.0},
    }


def evaluate_replay(
    rows: Iterable[Dict[str, Any]],
    *,
    quality_gate: Dict[str, Any] | None = None,
    horizons: Iterable[int] = HORIZONS,
) -> Dict[str, Any]:
    rows = list(rows)
    horizons = validate_horizons(horizons)
    gate = quality_gate or target_quality_gate(rows, horizons=horizons)
    values = [_outcome_profit(row) for row in rows]
    values = [float(value) for value in values if value is not None]
    metrics = portfolio_metrics(values)
    mfe_values = [value for row in rows if (value := _mfe(row)) is not None]
    mae_values = [value for row in rows if (value := _mae(row)) is not None]
    time_values = [value for row in rows if (value := _time_to_profit(row)) is not None]
    metrics.update({
        "mfe": max(mfe_values) if mfe_values else None,
        "mean_mfe": sum(mfe_values) / len(mfe_values) if mfe_values else None,
        "mae": min(mae_values) if mae_values else None,
        "mean_mae": sum(mae_values) / len(mae_values) if mae_values else None,
        "average_time_to_profit": sum(time_values) / len(time_values) if time_values else None,
        "bootstrap_ci": bootstrap_confidence_interval(values, seed=5),
    })
    return {
        "status": "PASS" if gate.get("status") == "PASS" else "BLOCKED",
        "target_quality_gate": gate,
        "horizon_metrics": {"PROFIT_WINDOW_5D": metrics},
        "main_table": [{"Target": "PROFIT_WINDOW_5D", **metrics}],
    }


def evaluate_decision_buckets(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    buckets = {
        "OLD BUY": lambda row: str(row.get("historical_original_decision") or "").upper() in {"BUY", "PAPER_PICK"},
        "CURRENT BUY": lambda row: str(row.get("current_decision") or "").upper() == "BUY",
        "WATCH": lambda row: str(row.get("current_decision") or "").upper() == "WATCH",
        "READY": lambda row: str(row.get("current_decision") or "").upper() == "READY",
    }
    return {
        name: evaluate_replay([row for row in rows if predicate(row)])["horizon_metrics"]["PROFIT_WINDOW_5D"]
        for name, predicate in buckets.items()
    }


def evaluate_feature_groups(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)

    def value(row: Dict[str, Any], key: str, default: str = "UNKNOWN") -> str:
        raw = row.get(key)
        if raw is None and key == "capital_convergence":
            raw = row.get("capital_convergence_level") or row.get("capital_convergence")
        if raw is None and isinstance(row.get("current_decision_payload"), dict):
            raw = (row["current_decision_payload"].get("core_alpha") or {}).get(key)
        if isinstance(raw, dict):
            raw = raw.get("status") or raw.get("state") or raw.get("value")
        return str(raw or default).upper()

    def grouped(key: str, names: Iterable[str]) -> Dict[str, Any]:
        return {
            name: evaluate_replay([row for row in rows if value(row, key) == name])[
                "horizon_metrics"
            ]["PROFIT_WINDOW_5D"]
            for name in names
        }

    return {
        "capital_convergence": grouped("capital_convergence", ("CONVERGENCE", "PARTIAL", "CONFLICT", "UNKNOWN")),
        "supply_absorption": grouped("supply_absorption_state", ("ABSORPTION", "BALANCED", "RELEASING", "UNKNOWN")),
        "repricing_state": grouped("repricing_state", ("ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION", "UNKNOWN")),
    }


def evaluate_group(rows: Iterable[Dict[str, Any]], predicate, *, horizons: Iterable[int] = HORIZONS) -> Dict[str, Any]:
    validate_horizons(horizons)
    selected = [row for row in rows if predicate(row)]
    return {"PROFIT_WINDOW_5D": evaluate_replay(selected)["horizon_metrics"]["PROFIT_WINDOW_5D"]}


def evaluate_top_k(
    rows: Iterable[Dict[str, Any]],
    *,
    score_key: str = "thesis_score",
    ks: Iterable[int] = (1, 5, 10),
    horizons: Iterable[int] = HORIZONS,
) -> Dict[str, Any]:
    validate_horizons(horizons)
    ordered = sorted(
        rows,
        key=lambda row: float((row.get("decision", {}).get("core_alpha") or {}).get(score_key) or row.get(score_key) or 0.0),
        reverse=True,
    )
    return {
        f"Top{k}": evaluate_replay(ordered[:k], horizons=horizons)["horizon_metrics"]
        for k in ks
    }


def build_alpha_report(
    rows: Iterable[Dict[str, Any]],
    *,
    quality_gate: Dict[str, Any] | None = None,
    horizons: Iterable[int] = HORIZONS,
) -> Dict[str, Any]:
    rows = list(rows)
    horizons = validate_horizons(horizons)
    gate = quality_gate or target_quality_gate(rows, horizons=horizons)
    evaluated = evaluate_replay(rows, quality_gate=gate, horizons=horizons)
    calibration = calibrate_profit_window_probability(rows)
    baseline_names = (
        "RANDOM",
        "PRICE / MARKET",
        "PRICE + VOLUME",
        "PRICE + CAPITAL",
        "PRICE + CAPITAL + SUPPLY",
        "PRICE + CAPITAL + SUPPLY + REPRICING",
        "FULL CORE ALPHA",
    )
    complete = _complete_training_rows(rows)
    train, validation, oos = _split_rows(complete)
    baseline_ladder: Dict[str, Any] = {}
    for name in baseline_names:
        if name == "RANDOM":
            predictions = _random_predictions(oos)
        else:
            model = _fit_baseline(train, name)
            predictions = _baseline_predictions(model, oos) if model else []
        baseline_ladder[name] = {
            "PROFIT_WINDOW_5D": _prediction_metrics(oos, predictions) if predictions else {"samples": 0}
        }
    feature_groups = evaluate_feature_groups(rows)
    return {
        "data_status": "READY" if gate.get("status") == "PASS" else "BLOCKED",
        "target_coverage": gate,
        "replay_sample_count": len(rows),
        "main_table": evaluated["main_table"],
        "train_samples": len(train),
        "validation_samples": len(validation),
        "oos_samples": len(oos),
        "baseline_ladder": baseline_ladder,
        "capital_convergence": feature_groups["capital_convergence"],
        "supply_absorption": feature_groups["supply_absorption"],
        "repricing_state": feature_groups["repricing_state"],
        "decision_buckets": evaluate_decision_buckets(rows),
        "feature_groups": feature_groups,
        "calibration": calibration,
        "core_alpha_status": calibration.get("status", "DATA_INSUFFICIENT"),
    }
