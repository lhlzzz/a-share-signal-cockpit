"""Point-in-time five-day profit-window evaluation and calibration.

This module owns research-time labels and validation only. It never creates
future features and it never emits a production portfolio state.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from itertools import combinations
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Sequence

from xiaogu_core_alpha import CANONICAL_COST_MODEL, COST_MODEL_VERSION, DEFAULT_COST_RATE

HORIZONS = (1, 2, 3, 4, 5)
HISTORICAL_VALIDATION_HORIZONS = HORIZONS
MIN_TARGET_COVERAGE = 0.95
PROFIT_WINDOW_TARGET = 0.02
MIN_CALIBRATION_SAMPLES = 30
MIN_SPLIT_SAMPLES = 5
MIN_PROBABILITY_STD = 0.01
MIN_PROBABILITY_RANGE = 0.05
MIN_TOP_DECILE_DELTA = 0.02


def resolve_horizon_dates(trade_date: Any, horizons: Iterable[int] = HORIZONS) -> Dict[int, str]:
    """Resolve research T+N dates through the production Calendar owner."""
    from xiaogu_db import resolve_t_plus_n

    validated = validate_horizons(horizons)
    return {day: resolve_t_plus_n(trade_date, day).isoformat() for day in validated}

MINIMAL_ALPHA_FEATURES = ("price_strength",)
CORE_ALPHA_FEATURES = MINIMAL_ALPHA_FEATURES

PRODUCTION_FEATURES = (
    "price_strength", "turnover", "capital_flow_ratio", "capital_price_impact",
    "capital_convergence", "capital_persistence", "capital_acceleration",
    "supply_absorption", "pricing_gap", "real_pricing_gap", "repricing_state",
    "future_buyer_evidence", "future_buyer_capacity", "reflexivity", "market_state",
    "business_quality", "future_demand", "risk", "execution_quality",
)

PRICE_FEATURES = ("price_strength",)
VOLUME_FEATURES = ("turnover",)
CAPITAL_FEATURES = (
    "capital_flow_ratio", "capital_persistence", "capital_acceleration",
    "capital_inflection", "capital_price_efficiency", "capital_price_divergence",
)
FEATURE_FAMILIES = {
    "CAPITAL": CAPITAL_FEATURES,
    "CAPITAL_CONVERGENCE": ("capital_convergence",),
    "SUPPLY": ("supply_absorption",),
    "PRICING_GAP": ("pricing_gap",),
    "FUTURE_BUYER": ("future_buyer_evidence",),
    "REPRICING": ("repricing_state",),
    "REFLEXIVITY": ("reflexivity",),
}
CUMULATIVE_ABLATION_FEATURES = {
    "BASELINE": (),
    "PRICE": ("price_strength",),
    "PRICE + VOLUME": ("price_strength", "turnover"),
    "PRICE + CAPITAL": PRICE_FEATURES + CAPITAL_FEATURES,
    "PRICE + CAPITAL + SUPPLY": PRICE_FEATURES + CAPITAL_FEATURES + ("supply_absorption",),
    "PRICE + CAPITAL + REPRICING": PRICE_FEATURES + CAPITAL_FEATURES + ("repricing_state",),
    "PRICE + CAPITAL + PRICING GAP": PRICE_FEATURES + CAPITAL_FEATURES + ("real_pricing_gap",),
    "PRICE + CAPITAL + FUTURE BUYER": PRICE_FEATURES + CAPITAL_FEATURES + ("future_buyer_evidence",),
    "PRICE + FLOW": PRICE_FEATURES + ("capital_flow_ratio",),
    "PRICE + PERSISTENCE": PRICE_FEATURES + ("capital_persistence",),
    "PRICE + ACCELERATION": PRICE_FEATURES + ("capital_acceleration",),
    "PRICE + DIVERGENCE": PRICE_FEATURES + ("capital_price_divergence",),
    "PRICE + FLOW + PERSISTENCE": PRICE_FEATURES + ("capital_flow_ratio", "capital_persistence"),
    "PRICE + FLOW + ACCELERATION": PRICE_FEATURES + ("capital_flow_ratio", "capital_acceleration"),
    "PRICE + FLOW + DIVERGENCE": PRICE_FEATURES + ("capital_flow_ratio", "capital_price_divergence"),
    "PRICE + PERSISTENCE + ACCELERATION": PRICE_FEATURES + ("capital_persistence", "capital_acceleration"),
    "CAPITAL_FULL": PRICE_FEATURES + CAPITAL_FEATURES + ("institution", "hot_money", "capital_convergence"),
    "FULL": PRICE_FEATURES + VOLUME_FEATURES + CAPITAL_FEATURES + (
        "supply_absorption", "real_pricing_gap", "repricing_state", "future_buyer_evidence",
    ),
}
SINGLE_FAMILY_ABLATION_FEATURES = {
    "CAPITAL ONLY": CAPITAL_FEATURES,
    "SUPPLY ONLY": ("supply_absorption",),
    "PRICING_GAP ONLY": ("real_pricing_gap",),
    "REPRICING ONLY": ("repricing_state",),
    "FUTURE BUYER ONLY": ("future_buyer_evidence",),
    "INSTITUTION ONLY": ("institution",),
    "HOT MONEY ONLY": ("hot_money",),
    "CAPITAL CONVERGENCE ONLY": ("capital_convergence",),
}

FEATURE_SOURCE_MATRIX = {
    "price_strength": {
        "raw_source": "L0 OHLCV/pct_change",
        "scanner_level": "L0",
        "snapshot_field": "price, raw.pct_chg",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "turnover": {
        "raw_source": "L0 turnover",
        "scanner_level": "L0",
        "snapshot_field": "turnover",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_convergence": {
        "raw_source": "L3 direct capital evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.stock_capital_flow/raw.lhb",
        "feature_function": "xiaogu_core_alpha._capital_convergence",
    },
    "capital_flow_ratio": {
        "raw_source": "L0 basic capital flow / traded amount",
        "scanner_level": "L0",
        "snapshot_field": "raw.net_inflow_main/raw.signal_amount",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_persistence": {
        "raw_source": "PIT-proven historical capital observations only",
        "scanner_level": "L2/L3",
        "snapshot_field": "raw.fund_flow_persistence",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_acceleration": {
        "raw_source": "PIT-proven historical capital observations only",
        "scanner_level": "L2/L3",
        "snapshot_field": "raw.fund_flow_acceleration",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_inflection": {
        "raw_source": "PIT-proven historical capital observations only",
        "scanner_level": "L2/L3",
        "snapshot_field": "raw.capital_history",
        "historical_source": "PostgreSQL.canonical_historical_snapshots",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_price_efficiency": {
        "raw_source": "L2/L3 capital history plus T-day price change",
        "scanner_level": "L2/L3",
        "snapshot_field": "raw.capital_history",
        "historical_source": "PostgreSQL.canonical_historical_snapshots",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_price_divergence": {
        "raw_source": "L2/L3 capital history plus T-day price change",
        "scanner_level": "L2/L3",
        "snapshot_field": "raw.capital_history",
        "historical_source": "PostgreSQL.canonical_historical_snapshots",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "supply_absorption": {
        "raw_source": "L3 supply + capital + turnover + price response",
        "scanner_level": "L3",
        "snapshot_field": "raw supply fields + CAPITAL/SUPPLY",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "pricing_gap": {
        "raw_source": "L3 evidence-backed valuation/pricing fields",
        "scanner_level": "L3",
        "snapshot_field": "raw fundamental_gap/industry_gap/earnings_gap",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "repricing_state": {
        "raw_source": "L3 structural market/capital/supply state",
        "scanner_level": "L3",
        "snapshot_field": "raw.repricing_state or derived market stage",
        "feature_function": "xiaogu_core_alpha._repricing_state",
    },
    "future_buyer_evidence": {
        "raw_source": "L3 evidence-backed buyer record",
        "scanner_level": "L3",
        "snapshot_field": "raw.future_buyers",
        "feature_function": "xiaogu_core_alpha._first_buyer_capacity",
    },
    "capital_flow": {
        "raw_source": "L0 basic capital flow",
        "scanner_level": "L0",
        "snapshot_field": "raw.net_inflow_main/raw.f62",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "capital_price_response": {
        "raw_source": "L0 capital flow plus price response",
        "scanner_level": "L0",
        "snapshot_field": "raw.net_inflow_main/raw.pct_chg",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "institution": {
        "raw_source": "L3 direct institution evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.lhb/raw.institution_position_change",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "main_force": {
        "raw_source": "L3 direct main-force identity evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.main_force_identity/raw.large_order_structure",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "hot_money": {
        "raw_source": "L3 direct hot-money evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.lhb/raw.hot_money_evidence",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "supply": {
        "raw_source": "L3 supply evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.overhead_supply/raw.shareholder_changes",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "real_pricing_gap": {
        "raw_source": "L3 evidence-backed pricing gap",
        "scanner_level": "L3",
        "snapshot_field": "raw.fundamental_gap/raw.price_reflection",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "repricing_evidence_score": {
        "raw_source": "L3 repricing diagnostic",
        "scanner_level": "L3",
        "snapshot_field": "raw.repricing_state + capital/supply evidence",
        "feature_function": "xiaogu_core_alpha._repricing_state",
    },
    "future_buyer_capacity": {
        "raw_source": "L3 evidence-backed buyer capacity",
        "scanner_level": "L3",
        "snapshot_field": "raw.future_buyers[].capacity",
        "feature_function": "xiaogu_core_alpha._first_buyer_capacity",
    },
    "business_quality": {
        "raw_source": "L3 company and earnings evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.business_quality/raw.earnings_preview",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "future_demand": {
        "raw_source": "L3 demand and industry evidence",
        "scanner_level": "L3",
        "snapshot_field": "raw.demand_strength/raw.industry_reports",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "risk": {
        "raw_source": "L0/L3 risk evidence",
        "scanner_level": "L0/L3",
        "snapshot_field": "raw.downside_risk/raw.risk_notice_penalty",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
    "execution_quality": {
        "raw_source": "L0 buyability and execution evidence",
        "scanner_level": "L0",
        "snapshot_field": "raw.buyable/raw.slippage/raw.spread",
        "feature_function": "xiaogu_forward_features.build_feature_vector",
    },
}

REPRICING_ENCODING = {
    "ACCUMULATION": 0.2,
    "IGNITION": 0.4,
    "EXPANSION": 0.6,
    "CLIMAX": 0.8,
    "DISTRIBUTION": 1.0,
}
MARKET_ENCODING = {
    "WEAK": 0.25,
    "SIDEWAYS": 0.5,
    "NEUTRAL": 0.5,
    "STRONG": 0.75,
    "BULL": 1.0,
}
CAPITAL_DIVERGENCE_ENCODING = {
    "CAPITAL_UP_PRICE_UP": 1.0,
    "CAPITAL_UP_PRICE_FLAT": 0.75,
    "CAPITAL_UP_PRICE_DOWN": 0.90,
    "CAPITAL_DOWN_PRICE_UP": 0.10,
    "CAPITAL_DOWN_PRICE_FLAT": 0.25,
    "CAPITAL_DOWN_PRICE_DOWN": 0.0,
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


def _clip(value: Any) -> float | None:
    numeric = _number(value)
    return None if numeric is None else max(0.0, min(1.0, numeric))


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


def _day_fact_present(row: Dict[str, Any], day: int) -> bool:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else row
    days = labels.get("days")
    if not isinstance(days, dict):
        return False
    values = days.get(str(day), days.get(day))
    if not isinstance(values, dict):
        return False
    return (
        all(_number(values.get(field)) is not None for field in ("open", "high", "low", "close", "volume", "amount"))
        and all(values.get(field) not in (None, "") for field in ("source", "source_timestamp", "price_basis"))
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
    value = _label(row, "mfe_5d")
    if value is None:
        value = _label(row, "future_5d_mfe")
    return _number(value)


def _mae(row: Dict[str, Any]) -> float | None:
    value = _label(row, "max_mae_5d")
    if value is None:
        value = _label(row, "mae_5d")
    return _number(value)


def _time_to_profit(row: Dict[str, Any]) -> float | None:
    return _number(_label(row, "time_to_profit"))


def evaluate_5d(entry_price: float, prices: Dict[int, Any]) -> Dict[str, Any]:
    """Evaluate a close-path approximation when OHLC bars are unavailable."""
    if entry_price <= 0:
        return {"horizon_days": 5, "data_status": "INVALID", "profit_window": False}
    path = [prices.get(day) for day in HORIZONS]
    if any(value is None for value in path):
        return {"horizon_days": 5, "data_status": "PARTIAL", "profit_window": None}
    net_values = [(float(value) - entry_price) / entry_price - CANONICAL_COST_MODEL["all_in_transaction_cost"] for value in path]
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
        "mean_net_profit": sum(values) / len(values) if values else None,
        "median_net_profit": median(values) if values else None,
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
    fact_coverage = {}
    for day in HORIZONS:
        return_coverage[str(day)] = sum(
            _day_return(row, day) is not None
            for row in rows
        ) / total if total else 0.0
        ohlc_coverage[str(day)] = sum(
            _day_ohlc_present(row, day)
            for row in rows
        ) / total if total else 0.0
        fact_coverage[str(day)] = sum(
            _day_fact_present(row, day)
            for row in rows
        ) / total if total else 0.0
    complete = sum(
        _canonical_entry_present(row)
        and all(_day_fact_present(row, day) for day in HORIZONS)
        for row in rows
    ) / total if total else 0.0
    checks = {
        **{f"T+{day}_OHLC": ohlc_coverage[str(day)] >= min_coverage for day in HORIZONS},
        **{f"T+{day}_FACT": fact_coverage[str(day)] >= min_coverage for day in HORIZONS},
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
                "fact": fact_coverage[str(day)],
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


def _feature_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("current_decision_payload")
    if isinstance(payload, dict) and isinstance(payload.get("feature_vector"), dict):
        return payload["feature_vector"]
    value = row.get("feature_vector")
    return value if isinstance(value, dict) else {}


FEATURE_ALIASES = {
    "capital_flow": "capital_flow_ratio",
    "capital_price_response": "capital_price_impact",
    "supply": "supply_absorption",
    "business_quality": "business_quality",
    "future_demand": "future_demand",
    "institution": "institution",
    "main_force": "main_force",
    "hot_money": "hot_money",
}


def _feature_name(name: str) -> str:
    return FEATURE_ALIASES.get(name, name)


SIGNED_FEATURES = {
    "capital_flow_ratio", "capital_acceleration", "capital_price_efficiency",
}


def _feature_numeric(name: str, value: float) -> float:
    if name in SIGNED_FEATURES:
        return max(-1.0, min(1.0, value))
    return max(0.0, min(1.0, value))


def _feature_value(row: Dict[str, Any], name: str) -> float:
    name = _feature_name(name)
    alpha = _alpha_payload(row)
    values = alpha.get("profit_window_feature_values")
    if not isinstance(values, dict):
        values = row.get("profit_window_feature_values")
    if isinstance(values, dict) and name in values:
        value = values[name]
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get("score") if value.get("score") is not None else value.get("value")
        numeric = _number(value)
        if numeric is not None:
            return _feature_numeric(name, numeric)
        if name == "capital_price_divergence":
            return CAPITAL_DIVERGENCE_ENCODING.get(str(value).upper())
    direct = row.get(name, alpha.get(name))
    if isinstance(direct, dict):
        if direct.get("score") is not None:
            direct = direct["score"]
        elif direct.get("state") is not None:
            direct = direct["state"]
        else:
            direct = direct.get("value")
    numeric = _number(direct)
    if numeric is not None:
        return _feature_numeric(name, numeric)
    if name == "capital_price_divergence" and direct not in (None, ""):
        return CAPITAL_DIVERGENCE_ENCODING.get(str(direct).upper())
    vector = _feature_payload(row)
    group_by_name = {
        "capital_flow_ratio": ("CAPITAL", "capital_flow_ratio"),
        "capital_price_impact": ("CAPITAL", "capital_price_impact"),
        "capital_persistence": ("CAPITAL", "capital_persistence"),
        "capital_acceleration": ("CAPITAL", "capital_acceleration"),
        "capital_inflection": ("CAPITAL", "capital_inflection"),
        "capital_price_efficiency": ("CAPITAL", "capital_price_efficiency"),
        "capital_price_divergence": ("CAPITAL", "capital_price_divergence_state"),
        "real_pricing_gap": ("PRICING_GAP", "real_pricing_gap"),
    }
    group_name, field_name = group_by_name.get(name, (None, None))
    if group_name:
        group = vector.get(group_name) if isinstance(vector, dict) else None
        numeric = _number(group.get(field_name)) if isinstance(group, dict) else None
        if numeric is not None:
            return _feature_numeric(name, numeric)
        if name == "capital_price_divergence" and isinstance(group, dict):
            return CAPITAL_DIVERGENCE_ENCODING.get(str(group.get(field_name) or "").upper())
    if name == "repricing_state":
        raw = row.get(name) if row.get(name) not in (None, "", "UNKNOWN") else alpha.get(name)
        if raw in (None, "", "UNKNOWN"):
            return None
        return REPRICING_ENCODING.get(str(raw).upper())
    if name == "market_state":
        raw = row.get(name)
        if raw in (None, ""):
            raw = alpha.get(name)
        if raw in (None, ""):
            market_payload = vector.get("MARKET") if isinstance(vector, dict) else None
            raw = market_payload.get("score") if isinstance(market_payload, dict) else None
        if raw in (None, ""):
            raw = row.get("market_regime")
        if isinstance(raw, dict):
            if raw.get("regime") is not None:
                raw = raw["regime"]
            elif raw.get("state") is not None:
                raw = raw["state"]
            else:
                raw = raw.get("score")
        numeric = _number(raw)
        if numeric is not None:
            return max(0.0, min(1.0, numeric))
        if raw in (None, "", "UNKNOWN"):
            return None
        return MARKET_ENCODING.get(str(raw).upper())
    if name in {"institution", "main_force", "hot_money"}:
        convergence = alpha.get("capital_convergence")
        if not isinstance(convergence, dict):
            return None
        return _number(convergence.get(name))
    if name in {"business_quality", "future_demand"}:
        direct = alpha.get(name)
        if direct is None:
            direct = row.get(name)
        return _clip(direct)
    if name in {"risk", "execution_quality"}:
        group = "RISK" if name == "risk" else "EXECUTION"
        group_payload = vector.get(group) if isinstance(vector, dict) else None
        if isinstance(group_payload, dict):
            direct = group_payload.get("score")
            if direct is None and name == "execution_quality":
                direct = group_payload.get("execution_feasibility")
            return _clip(direct)
    if name in {"repricing_probability", "repricing_evidence_score"}:
        direct = row.get("repricing_evidence_score", alpha.get("repricing_evidence_score"))
        numeric = _number(direct)
        if numeric is not None:
            return max(0.0, min(1.0, numeric))
        return _feature_value(row, "repricing_state")
    return None


def _extra_feature_value(row: Dict[str, Any], name: str) -> float:
    alpha = _alpha_payload(row)
    vector = _feature_payload(row)
    market = vector.get("MARKET") or {}
    if name == "price_strength":
        numeric = _number(row.get(name) if row.get(name) is not None else market.get("price_strength"))
        return None if numeric is None else max(0.0, min(1.0, numeric))
    if name == "market_score":
        axes = alpha.get("axes") if isinstance(alpha.get("axes"), dict) else {}
        numeric = _number(row.get(name) if row.get(name) is not None else market.get("score", axes.get("MARKET")))
        return None if numeric is None else max(0.0, min(1.0, numeric))
    if name == "turnover":
        vector = _feature_payload(row)
        if isinstance(vector, dict):
            supply = vector.get("SUPPLY") or {}
            numeric = _number(supply.get("turnover_velocity") if supply.get("turnover_velocity") is not None else supply.get("turnover"))
            return None if numeric is None else max(0.0, min(1.0, numeric))
        numeric = _number(row.get(name))
        return None if numeric is None else max(0.0, min(1.0, numeric))
    return None


def _raw_feature_value(row: Dict[str, Any], name: str) -> float | None:
    name = _feature_name(name)
    if name in {"price_strength", "market_score", "turnover"}:
        return _extra_feature_value(row, name)
    return _feature_value(row, name)


def _imputer_for(rows: Sequence[Dict[str, Any]], names: Sequence[str]) -> Dict[str, float]:
    imputer: Dict[str, float] = {}
    for name in names:
        observed = [value for row in rows if (value := _raw_feature_value(row, name)) is not None]
        if observed:
            imputer[name] = float(median(observed))
    return imputer


def _feature_vector(
    row: Dict[str, Any],
    names: Sequence[str],
    imputer: Mapping[str, float] | None = None,
) -> list[float]:
    filled = imputer or {}
    values = []
    for name in names:
        value = _raw_feature_value(row, name)
        if value is None:
            value = filled.get(name)
        if value is None:
            raise ValueError(f"FEATURE_MISSING_AFTER_TRAIN_IMPUTATION:{name}")
        values.append(float(value))
    return values


def _feature_value_present(row: Dict[str, Any], name: str) -> tuple[float, bool]:
    """Return the model value and whether it was present, without hiding fallbacks."""
    name = _feature_name(name)
    if name in {"price_strength", "market_score", "turnover"}:
        if name == "turnover":
            vector = _feature_payload(row)
            supply = vector.get("SUPPLY") if isinstance(vector, dict) else None
            if isinstance(supply, dict) and any(key in supply for key in ("turnover_velocity", "turnover")):
                return _extra_feature_value(row, name), True
            return _extra_feature_value(row, name), name in row
        alpha = _alpha_payload(row)
        axes = alpha.get("axes") if isinstance(alpha.get("axes"), dict) else {}
        if name == "price_strength":
            return _extra_feature_value(row, name), "MARKET" in axes or name in row
        return _extra_feature_value(row, name), "MARKET" in axes or name in row
    if name in {"capital_price_impact", "real_pricing_gap"}:
        vector = _feature_payload(row)
        group_name, field_name = {
            "capital_price_impact": ("CAPITAL", "capital_price_impact"),
            "real_pricing_gap": ("PRICING_GAP", "real_pricing_gap"),
        }[name]
        group = vector.get(group_name) if isinstance(vector, dict) else None
        return _feature_value(row, name), isinstance(group, dict) and field_name in group
    alpha = _alpha_payload(row)
    values = alpha.get("profit_window_feature_values")
    if isinstance(values, dict) and name in values:
        return _feature_value(row, name), True
    if name in row or name in alpha:
        return _feature_value(row, name), True
    return _feature_value(row, name), False


def _snapshot_raw(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("current_decision_payload")
    snapshot = payload.get("canonical_snapshot") if isinstance(payload, dict) else None
    raw = snapshot.get("raw") if isinstance(snapshot, dict) else None
    return raw if isinstance(raw, dict) else {}


def _feature_observed(row: Dict[str, Any], name: str, value: float) -> bool:
    """Identify source evidence separately from derived zero-filled values."""
    name = _feature_name(name)
    raw = _snapshot_raw(row)
    alpha = _alpha_payload(row)
    vector = _feature_payload(row)
    capital = vector.get("CAPITAL") if isinstance(vector, dict) else {}
    supply = vector.get("SUPPLY") if isinstance(vector, dict) else {}
    if name == "capital_convergence":
        convergence = alpha.get("capital_convergence")
        return isinstance(convergence, dict) and convergence.get("status") not in {None, "UNKNOWN"}
    if name == "capital_price_impact":
        return (
            isinstance(capital, dict)
            and capital.get("capital_price_impact_state") not in {None, "UNKNOWN"}
            and any(key in raw for key in ("main_net_inflow", "f62", "amount", "signal_amount"))
        )
    if name in {"capital_persistence", "capital_acceleration"}:
        history_audit = (
            (capital.get("capital_history_audit") if isinstance(capital, dict) else None)
            or row.get("capital_history_audit")
            or {}
        )
        measured = capital.get(name) if isinstance(capital, dict) else row.get(name)
        return (
            history_audit.get("observed_days", 0) >= 6
            and measured is not None
        )
    if name in {"capital_inflection", "capital_price_efficiency", "capital_price_divergence"}:
        history_audit = (
            (capital.get("capital_history_audit") if isinstance(capital, dict) else None)
            or row.get("capital_history_audit")
            or {}
        )
        measured = capital.get(name) if isinstance(capital, dict) else row.get(name)
        if name == "capital_price_divergence" and measured is None and isinstance(capital, dict):
            measured = capital.get("capital_price_divergence_state")
        return (
            history_audit.get("observed_days", 0) >= 6
            and measured is not None
        )
    if name == "supply_absorption":
        return (
            isinstance(supply, dict)
            and supply.get("supply_absorption_state") not in {None, "UNKNOWN"}
        ) or any(key in raw for key in (
            "supply_absorption", "overhead_supply", "trapped_chip_ratio", "sell_pressure",
        ))
    if name == "pricing_gap":
        return any(key in raw for key in (
            "fundamental_gap", "industry_gap", "capital_gap", "earnings_gap", "demand_gap",
            "attention_gap", "institutional_positioning", "price_reflection",
        ))
    if name == "real_pricing_gap":
        return _feature_observed(row, "pricing_gap", value)
    if name == "future_buyer_evidence":
        payload = row.get("current_decision_payload") or {}
        buyer_map = payload.get("future_buyer_map") if isinstance(payload, dict) else None
        if not isinstance(buyer_map, dict):
            buyers = payload.get("research_context", {}) if isinstance(payload, dict) else {}
            buyer_map = buyers.get("future_buyer_map") if isinstance(buyers, dict) else None
        return any(
            isinstance(item, dict)
            and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
            and item.get("evidence") and item.get("source") and item.get("observed_at")
            for item in (buyer_map or {}).get("potential_next_buyer", [])
        ) if isinstance(buyer_map, dict) else False
    if name == "future_buyer_capacity":
        return _feature_observed(row, "future_buyer_evidence", value)
    if name == "repricing_state":
        return str(alpha.get("repricing_state") or "UNKNOWN").upper() != "UNKNOWN"
    if name in {"repricing_probability", "repricing_evidence_score"}:
        return _feature_observed(row, "repricing_state", value)
    if name == "reflexivity":
        market = (vector.get("MARKET") or {}) if isinstance(vector, dict) else {}
        return any(key in raw for key in ("crowding_risk", "crowding", "reflexivity_break")) or bool(
            market.get("price_strength")
        )
    if name == "market_state":
        market = vector.get("MARKET") if isinstance(vector, dict) else {}
        return isinstance(market, dict) and any(market.get(key) not in (None, "") for key in (
            "breadth", "sector_breadth", "price_strength", "follow_through",
        ))
    if name in {"institution", "main_force", "hot_money"}:
        convergence = alpha.get("capital_convergence")
        behavior = (convergence or {}).get("behaviors", {}).get(name) if isinstance(convergence, dict) else None
        return isinstance(behavior, dict) and any(
            item.get("observed") and str(item.get("evidence_family") or "").startswith("DIRECT_")
            for item in behavior.get("evidence") or []
            if isinstance(item, dict)
        )
    if name in {"business_quality", "future_demand"}:
        return name in alpha or name in row
    if name == "execution_quality":
        return any(key in raw for key in (
            "execution_quality", "buyable", "slippage", "spread", "market_impact",
        ))
    if name == "risk":
        return any(key in raw for key in (
            "downside_risk", "event_risk", "risk_notice_penalty", "regulatory_hard_block",
            "risk_hard_block", "thesis_invalidated", "halted", "is_suspended",
        ))
    return value is not None


def diagnose_features(
    rows: Iterable[Dict[str, Any]],
    *,
    feature_names: Sequence[str] = PRODUCTION_FEATURES,
) -> Dict[str, Any]:
    """Report distributions and source missingness without changing any row."""
    rows = list(rows)
    labels = [_label_value(row) for row in rows]
    report = {}
    for name in feature_names:
        raw_values = [_raw_feature_value(row, name) for row in rows]
        observed_flags = [
            value is not None and _feature_observed(row, name, value)
            for row, value in zip(rows, raw_values)
        ]
        values = [value for value in raw_values if value is not None]
        observed = observed_flags
        positive = [value for value, label in zip(raw_values, labels) if label == 1 and value is not None]
        negative = [value for value, label in zip(raw_values, labels) if label == 0 and value is not None]
        report[name] = {
            "count": len(raw_values),
            "mean": (sum(values) / len(values)) if values else None,
            "std": (
                math.sqrt(sum((value - (sum(values) / len(values))) ** 2 for value in values) / len(values))
                if values else None
            ),
            "min": min(values) if values else None,
            "p10": _percentile(values, 0.10),
            "p25": _percentile(values, 0.25),
            "p50": _percentile(values, 0.50),
            "median": _percentile(values, 0.50),
            "p75": _percentile(values, 0.75),
            "p90": _percentile(values, 0.90),
            "max": max(values) if values else None,
            "missing_rate": 1.0 - (sum(observed_flags) / len(observed_flags) if observed_flags else 0.0),
            "observed_count": sum(observed_flags),
            "available_count": len(observed_flags),
            "valid_rate": sum(observed_flags) / len(observed_flags) if observed_flags else None,
            "zero_rate": sum(value == 0.0 for value in raw_values if value is not None) / len(raw_values) if raw_values else None,
            "variance": (
                sum((value - (sum(values) / len(values))) ** 2 for value in values) / len(values)
                if values else None
            ),
            "boundary_rate": (
                sum(value in {0.0, 1.0} for value in raw_values if value is not None) / len(raw_values)
                if raw_values else None
            ),
            "unique_count": len(set(round(value, 12) for value in values)),
            "positive_mean": sum(positive) / len(positive) if positive else None,
            "negative_mean": sum(negative) / len(negative) if negative else None,
            "label_correlation": _correlation(
                [value for value, label in zip(raw_values, labels) if value is not None and label is not None],
                [label for value, label in zip(raw_values, labels) if value is not None and label is not None],
            ),
        }
        std = report[name]["std"]
        missing_rate = report[name]["missing_rate"]
        missing_rate = 0.0 if missing_rate is None else missing_rate
        unique_count = report[name]["unique_count"]
        collapsed = (
            unique_count <= 2
            or (std is not None and std < 1e-12)
            or missing_rate >= 0.95
        )
        report[name]["status"] = "FEATURE_COLLAPSED" if collapsed else "OK"
    collinearity = []
    for left, right in combinations(feature_names, 2):
        paired = [
            (_raw_feature_value(row, left), _raw_feature_value(row, right))
            for row in rows
        ]
        paired = [(left_value, right_value) for left_value, right_value in paired if left_value is not None and right_value is not None]
        correlation = _correlation(
            [left_value for left_value, _ in paired],
            [right_value for _, right_value in paired],
        )
        if correlation is not None and abs(correlation) >= 0.95:
            collinearity.append({"left": left, "right": right, "correlation": correlation})
    return {
        "samples": len(rows),
        "label_counts": {
            "positive": sum(label == 1 for label in labels),
            "negative": sum(label == 0 for label in labels),
            "missing": sum(label is None for label in labels),
        },
        "features": report,
        "constant_features": [name for name, item in report.items() if item["unique_count"] <= 2],
        "high_missing_features": [
            name for name, item in report.items()
            if (0.0 if item["missing_rate"] is None else item["missing_rate"]) >= 0.95
        ],
        "high_collinearity_pairs": collinearity,
    }


def build_feature_source_matrix(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Join source ownership to observed/missing feature evidence."""
    rows = list(rows)
    diagnostics = diagnose_features(rows, feature_names=tuple(FEATURE_SOURCE_MATRIX))
    result = {}
    for name, source in FEATURE_SOURCE_MATRIX.items():
        item = diagnostics["features"].get(name, {})
        result[name] = {
            **source,
            "observed_count": item.get("observed_count", 0),
            "available_count": item.get("available_count", len(rows)),
            "coverage": item.get("valid_rate"),
            "missing_rate": item.get("missing_rate"),
            "valid_rate": item.get("valid_rate"),
            "unique_count": item.get("unique_count", 0),
            "std": item.get("std"),
            "mean": item.get("mean"),
            "p10": item.get("p10"),
            "p25": item.get("p25"),
            "p50": item.get("p50"),
            "p75": item.get("p75"),
            "p90": item.get("p90"),
            "status": item.get("status", "FEATURE_COLLAPSED"),
            "production_permission": "RESEARCH_ONLY" if item.get("status") == "OK" else "NONE",
            "production_allowed": item.get("status") == "OK",
        }
    return result


def _correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    if not left_var or not right_var:
        return None
    return sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right)) / math.sqrt(left_var * right_var)


def _probability_separation(rows: Sequence[Dict[str, Any]], predictions: Sequence[float]) -> Dict[str, Any]:
    if not predictions:
        return {"status": "DATA_INSUFFICIENT", "reason": "NO_PREDICTIONS"}
    minimum = min(predictions)
    maximum = max(predictions)
    mean = sum(predictions) / len(predictions)
    std = math.sqrt(sum((value - mean) ** 2 for value in predictions) / len(predictions))
    base_rate = sum(_label_value(row) for row in rows) / len(rows)
    cutoff_index = max(0, math.ceil(len(predictions) * 0.10) - 1)
    top10_cutoff = sorted(predictions, reverse=True)[cutoff_index]
    high_probability = [
        _label_value(row)
        for row, prediction in zip(rows, predictions)
        if prediction >= top10_cutoff
    ]
    high_rate = sum(high_probability) / len(high_probability) if high_probability else None
    delta = high_rate - base_rate if high_rate is not None else None
    passed = bool(
        len(set(predictions)) > 1
        and std >= MIN_PROBABILITY_STD
        and maximum - minimum >= MIN_PROBABILITY_RANGE
        and delta is not None
        and delta >= MIN_TOP_DECILE_DELTA
    )
    return {
        "status": "PASS" if passed else "MODEL_NOT_DISCRIMINATIVE",
        "probability_mean": mean,
        "probability_std": std,
        "probability_min": minimum,
        "probability_max": maximum,
        "probability_range": maximum - minimum,
        "thresholds": {
            "minimum_std": MIN_PROBABILITY_STD,
            "minimum_range": MIN_PROBABILITY_RANGE,
            "minimum_top10_delta": MIN_TOP_DECILE_DELTA,
        },
        "high_probability_delta_vs_base_rate": delta,
    }


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
    usable_names = [
        name for name in names
        if any(_raw_feature_value(row, name) is not None for row in rows)
    ]
    if names and not usable_names:
        return None
    imputer = _imputer_for(rows, usable_names)
    try:
        x = [_feature_vector(row, usable_names, imputer) for row in rows]
    except ValueError:
        return None
    y = [float(_label_value(row)) for row in rows]
    rate = sum(y) / len(y)
    intercept = math.log(max(1e-6, min(1.0 - 1e-6, rate)) / max(1e-6, 1.0 - rate))
    weights = [0.0] * len(usable_names)
    for _ in range(800):
        gradient_b = 0.0
        gradient_w = [0.0] * len(usable_names)
        for values, target in zip(x, y):
            error = _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, values))) - target
            gradient_b += error
            for index, value in enumerate(values):
                gradient_w[index] += error * value
        scale = 1.0 / len(rows)
        intercept -= 0.18 * gradient_b * scale
        for index in range(len(weights)):
            weights[index] -= 0.18 * (gradient_w[index] * scale + 0.01 * weights[index])
    return {
        "intercept": intercept,
        "coefficients": weights,
        "feature_names": list(usable_names),
        "imputer": imputer,
    }


def _predict(model: Mapping[str, Any], rows: Sequence[Dict[str, Any]]) -> list[float]:
    names = list(model.get("feature_names") or [])
    intercept = float(model["intercept"])
    return [
        _sigmoid(intercept + sum(
            float(weight) * value for weight, value in zip(
                model.get("coefficients") or [],
                _feature_vector(row, names, model.get("imputer") if isinstance(model.get("imputer"), dict) else None),
            )
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
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and predictions[order[end]] == predictions[order[index]]:
            end += 1
        found += sum(labels[item] for item in order[index:end])
        recall = found / positives
        precision = found / end
        area += (recall - previous_recall) * precision
        previous_recall = recall
        index = end
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
    maes = [value for row in rows if (value := _mae(row)) is not None]
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
        "mean_net_profit": sum(profit for profit in profits if profit is not None) / len([profit for profit in profits if profit is not None])
        if any(profit is not None for profit in profits) else None,
        "median_net_profit": median([profit for profit in profits if profit is not None]) if any(profit is not None for profit in profits) else None,
        "mean_selected_profit": sum(selected) / len(selected) if selected else None,
        "mean_mae": sum(value for value in maes if value is not None) / len([value for value in maes if value is not None])
        if any(value is not None for value in maes) else None,
        "mae": min(maes) if maes else None,
        "mfe": max(value for value in (_mfe(row) for row in rows) if value is not None)
        if any(_mfe(row) is not None for row in rows) else None,
        "mean_mfe": sum(value for value in (_mfe(row) for row in rows) if value is not None)
        / len([value for value in (_mfe(row) for row in rows) if value is not None])
        if any(_mfe(row) is not None for row in rows) else None,
        "profit_factor": portfolio["profit_factor"],
        "max_drawdown": portfolio["max_drawdown"],
        "average_time_to_profit": sum(value for value in times if value is not None) / len([value for value in times if value is not None])
        if any(value is not None for value in times) else None,
        "probability_mean": sum(predictions) / len(predictions) if predictions else None,
        "probability_std": math.sqrt(sum((value - (sum(predictions) / len(predictions))) ** 2 for value in predictions) / len(predictions)) if predictions else None,
        "probability_p10": _percentile(predictions, 0.10),
        "probability_p25": _percentile(predictions, 0.25),
        "probability_p50": _percentile(predictions, 0.50),
        "probability_p75": _percentile(predictions, 0.75),
        "probability_p90": _percentile(predictions, 0.90),
        "buy_threshold": 0.5,
        "buy_coverage": predicted_positive / len(predictions) if predictions else None,
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    values = sorted(float(value) for value in values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _split_rows(rows: Sequence[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[Dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: str(row.get("signal_date") or row.get("trade_date") or ""))
    dates = sorted({str(row.get("signal_date") or row.get("trade_date") or "") for row in ordered})
    dates = [value for value in dates if value]
    if len(dates) < 3:
        return [], [], []
    train_end = max(1, int(len(dates) * 0.6))
    validation_end = max(train_end + 1, int(len(dates) * 0.8))
    validation_end = min(validation_end, len(dates) - 1)
    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])
    oos_dates = set(dates[validation_end:])
    return (
        [row for row in ordered if str(row.get("signal_date") or row.get("trade_date") or "") in train_dates],
        [row for row in ordered if str(row.get("signal_date") or row.get("trade_date") or "") in validation_dates],
        [row for row in ordered if str(row.get("signal_date") or row.get("trade_date") or "") in oos_dates],
    )


def _random_predictions(rows: Sequence[Dict[str, Any]], seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in rows]


def _baseline_features(name: str) -> tuple[str, ...]:
    return {
        **CUMULATIVE_ABLATION_FEATURES,
        **SINGLE_FAMILY_ABLATION_FEATURES,
    }.get(name, ())


def _fit_baseline(rows: Sequence[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    names = _baseline_features(name)
    return _fit_logistic(rows, names)


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
        "mean_mae": (
            sum(values) / len(values)
            if (values := [_mae(row) for _, row in selected if _mae(row) is not None])
            else None
        ),
        })
    rates = [item["profit_window_rate"] for item in bins]
    passed = bool(rates) and all(left <= right + 0.02 for left, right in zip(rates, rates[1:]))
    return {"status": "PASS" if passed else "FAIL", "bins": bins}


def _fit_set_report(
    train: Sequence[Dict[str, Any]],
    validation: Sequence[Dict[str, Any]],
    oos: Sequence[Dict[str, Any]],
    names: Sequence[str],
) -> Dict[str, Any]:
    model = _fit_logistic(train, names)
    if model is None:
        return {
            "feature_names": list(names),
            "status": "DATA_INSUFFICIENT",
            "train": {"samples": 0},
            "validation": {"samples": 0},
            "oos": {"samples": 0},
        }
    predictions = {
        "train": _predict(model, train),
        "validation": _predict(model, validation),
        "oos": _predict(model, oos),
    }
    oos_metrics = _prediction_metrics(oos, predictions["oos"])
    return {
        "feature_names": list(names),
        "status": "FITTED",
        "coefficients": model.get("coefficients"),
        "intercept": model.get("intercept"),
        "train": _prediction_metrics(train, predictions["train"]),
        "validation": _prediction_metrics(validation, predictions["validation"]),
        "oos": oos_metrics,
        "monotonicity": _monotonicity(oos, predictions["oos"]),
        "probability_separation": _probability_separation(oos, predictions["oos"]),
        "selectivity": _selectivity(oos, predictions["oos"]),
    }


def _ablation_report(
    train: Sequence[Dict[str, Any]],
    validation: Sequence[Dict[str, Any]],
    oos: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    results = {
        name: _fit_set_report(train, validation, oos, names)
        for name, names in {
            **CUMULATIVE_ABLATION_FEATURES,
            **SINGLE_FAMILY_ABLATION_FEATURES,
        }.items()
    }
    baseline = results.get("BASELINE", {})
    baseline_oos = baseline.get("oos") or {}
    price_oos = (results.get("PRICE") or {}).get("oos") or {}
    for result in results.values():
        oos = result.get("oos") or {}
        def delta(name: str) -> float | None:
            current = oos.get(name)
            base = baseline_oos.get(name)
            return current - base if current is not None and base is not None else None
        result["baseline_delta"] = {
            name: delta(name) for name in (
                "roc_auc", "pr_auc", "brier_score", "calibration_error", "profit_window_rate",
                "mean_net_profit", "median_net_profit", "mean_mae", "mean_mfe", "profit_factor",
                "max_drawdown",
            )
        }
        result["price_baseline_delta"] = {
            name: (
                oos.get(name) - price_oos.get(name)
                if oos.get(name) is not None and price_oos.get(name) is not None else None
            )
            for name in (
                "roc_auc", "pr_auc", "brier_score", "calibration_error", "probability_std",
                "profit_window_rate", "mean_net_profit", "median_net_profit", "mean_mae",
                "mfe", "profit_factor", "max_drawdown",
            )
        }
    return {
        "same_rows": True,
        "same_time_split": True,
        "same_target": "PROFIT_WINDOW_5D",
        "same_cost_model": dict(CANONICAL_COST_MODEL),
        "cumulative": {name: results[name] for name in CUMULATIVE_ABLATION_FEATURES},
        "single_family": {name: results[name] for name in SINGLE_FAMILY_ABLATION_FEATURES},
    }


def _selectivity(rows: Sequence[Dict[str, Any]], predictions: Sequence[float]) -> Dict[str, Any]:
    if not rows or not predictions:
        return {"status": "DATA_INSUFFICIENT"}
    ordered = sorted(zip(predictions, rows), key=lambda item: item[0], reverse=True)
    base_rate = sum(int(_label_value(row)) for row in rows if _label_value(row) is not None) / len(rows)

    def band(fraction: float | None) -> Dict[str, Any]:
        selected = ordered if fraction is None else ordered[: max(1, math.ceil(len(ordered) * fraction))]
        labels = [int(_label_value(row)) for _, row in selected if _label_value(row) is not None]
        profits = [_outcome_profit(row) for _, row in selected if _outcome_profit(row) is not None]
        maes = [_mae(row) for _, row in selected if _mae(row) is not None]
        mfes = [_mfe(row) for _, row in selected if _mfe(row) is not None]
        portfolio = portfolio_metrics(profits)
        rate = sum(labels) / len(labels) if labels else None
        return {
            "samples": len(selected),
            "label_samples": len(labels),
            "profit_window_rate": rate,
            "mean_net_profit": sum(profits) / len(profits) if profits else None,
            "median_net_profit": median(profits) if profits else None,
            "mae": sum(maes) / len(maes) if maes else None,
            "mfe": sum(mfes) / len(mfes) if mfes else None,
            "profit_factor": portfolio.get("profit_factor"),
            "drawdown": portfolio.get("max_drawdown"),
        }

    top5 = band(0.05)
    top10 = band(0.10)
    selective = bool(top10.get("profit_window_rate") is not None and top10["profit_window_rate"] > base_rate)
    return {
        "status": "PASS" if selective else "MODEL_NOT_SELECTIVE",
        "base_rate": base_rate,
        "TOP_5_PERCENT": top5,
        "TOP_10_PERCENT": top10,
        "TOP_20_PERCENT": band(0.20),
        "TOP_30_PERCENT": band(0.30),
        "All": band(None),
        "top_10_percent": top10,
        "top_20_percent": band(0.20),
        "top_30_percent": band(0.30),
        "all": band(None),
    }


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
    feature_audit = diagnose_features(complete, feature_names=CORE_ALPHA_FEATURES)
    collapsed = [
        name for name, item in (feature_audit.get("features") or {}).items()
        if item.get("status") == "FEATURE_COLLAPSED"
    ]
    usable = [name for name in CORE_ALPHA_FEATURES if name not in collapsed]
    model = _fit_logistic(train, usable)
    if model is None:
        return {
            "status": "EXPERIMENTAL",
            "reason": "TRAINING_LABEL_HAS_ONE_CLASS",
            "samples": len(complete),
            "train_samples": len(train),
            "validation_samples": len(validation),
            "oos_samples": len(oos),
            "feature_names": list(CORE_ALPHA_FEATURES),
            "collapsed_features": collapsed,
        }
    train_predictions = _predict(model, train)
    validation_predictions = _predict(model, validation)
    oos_predictions = _predict(model, oos)
    train_metrics = _prediction_metrics(train, train_predictions)
    validation_metrics = _prediction_metrics(validation, validation_predictions)
    oos_metrics = _prediction_metrics(oos, oos_predictions)
    monotonicity = _monotonicity(oos, oos_predictions)
    probability_separation = _probability_separation(oos, oos_predictions)
    selectivity = _selectivity(oos, oos_predictions)
    baseline_model = _fit_baseline(train, "PRICE")
    baseline_metrics = _prediction_metrics(oos, _baseline_predictions(baseline_model, oos)) if baseline_model else {}
    baseline_delta = {
        "roc_auc": oos_metrics.get("roc_auc") - baseline_metrics.get("roc_auc")
        if oos_metrics.get("roc_auc") is not None and baseline_metrics.get("roc_auc") is not None else None,
        "pr_auc": oos_metrics.get("pr_auc") - baseline_metrics.get("pr_auc")
        if oos_metrics.get("pr_auc") is not None and baseline_metrics.get("pr_auc") is not None else None,
        "brier_score": baseline_metrics.get("brier_score") - oos_metrics.get("brier_score")
        if oos_metrics.get("brier_score") is not None and baseline_metrics.get("brier_score") is not None else None,
    }
    passed = bool(
        oos_metrics.get("roc_auc") is not None
        and oos_metrics.get("pr_auc") is not None
        and oos_metrics.get("calibration_error") is not None
        and oos_metrics["calibration_error"] <= 0.15
        and monotonicity["status"] == "PASS"
        and probability_separation["status"] == "PASS"
        and selectivity["status"] == "PASS"
        and baseline_delta["pr_auc"] is not None
        and baseline_delta["pr_auc"] > 0.0
    )
    from xiaogu_core_alpha import FEATURE_VERSION, MODEL_ID, MODEL_VERSION, SCHEMA_VERSION, TARGET_VERSION
    dataset_identity = [
        {
            "decision_id": row.get("decision_id"),
            "snapshot_id": row.get("snapshot_id"),
            "decision_identity_key": row.get("decision_identity_key"),
            "trade_date": row.get("trade_date") or row.get("signal_date"),
            "target": _label_value(row),
            "profit": _outcome_profit(row),
        }
        for row in complete
    ]
    dataset_hash = hashlib.sha256(
        json.dumps(dataset_identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    result = {
        **model,
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "dataset_hash": dataset_hash,
        "dataset_version": "historical_profit_window_v4",
        "train_window": {
            "count": len(train),
            "start": str(train[0].get("trade_date") or train[0].get("signal_date") or "") if train else "",
            "end": str(train[-1].get("trade_date") or train[-1].get("signal_date") or "") if train else "",
        },
        "validation_window": {
            "count": len(validation),
            "start": str(validation[0].get("trade_date") or validation[0].get("signal_date") or "") if validation else "",
            "end": str(validation[-1].get("trade_date") or validation[-1].get("signal_date") or "") if validation else "",
        },
        "oos_window": {
            "count": len(oos),
            "start": str(oos[0].get("trade_date") or oos[0].get("signal_date") or "") if oos else "",
            "end": str(oos[-1].get("trade_date") or oos[-1].get("signal_date") or "") if oos else "",
        },
        "cost_model_version": COST_MODEL_VERSION,
        "target_version": TARGET_VERSION,
        "horizon": 5,
        "schema_version": SCHEMA_VERSION,
        "target": "PROFIT_WINDOW_5D",
        "status": "VALIDATED" if passed else "EXPERIMENTAL",
        "production_permission": "NONE",
        "calibration_status": "CALIBRATED",
        "samples": len(complete),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "oos_samples": len(oos),
        "train": train_metrics,
        "validation": validation_metrics,
        "oos": {**oos_metrics, "passed": passed},
        "monotonicity": monotonicity,
        "probability_separation": probability_separation,
        "selectivity": selectivity,
        "baseline_price_market_oos": baseline_metrics,
        "baseline_delta": baseline_delta,
        "cost_model": dict(CANONICAL_COST_MODEL),
        "collapsed_features": collapsed,
        "feature_information_audit": feature_audit,
    }
    return result


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


def evaluate_feature_groups(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)

    def value(row: Dict[str, Any], key: str, default: str = "UNKNOWN") -> str:
        payload = row.get("current_decision_payload") if isinstance(row.get("current_decision_payload"), dict) else {}
        alpha = payload.get("core_alpha") if isinstance(payload.get("core_alpha"), dict) else {}
        vector = payload.get("feature_vector") if isinstance(payload.get("feature_vector"), dict) else {}
        if key == "capital_convergence":
            raw = alpha.get("capital_convergence") or row.get("capital_convergence")
        elif key == "supply_absorption_state":
            raw = (vector.get("SUPPLY") or {}).get(key) or row.get(key)
        else:
            raw = alpha.get(key) or row.get(key)
        if raw is None and key == "capital_convergence":
            raw = row.get("capital_convergence_level")
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
    complete = _complete_training_rows(rows)
    train, validation, oos = _split_rows(complete)
    baseline_ladder: Dict[str, Any] = {}
    for name, names in {"RANDOM": (), **CUMULATIVE_ABLATION_FEATURES}.items():
        if name == "RANDOM":
            predictions = _random_predictions(oos)
            baseline_ladder[name] = {"PROFIT_WINDOW_5D": _prediction_metrics(oos, predictions)}
            continue
        baseline_ladder[name] = {
            "PROFIT_WINDOW_5D": _fit_set_report(train, validation, oos, names)
        }
    ablation = _ablation_report(train, validation, oos)
    feature_groups = evaluate_feature_groups(rows)
    feature_source_matrix = build_feature_source_matrix(rows)
    full_alpha = ablation["cumulative"]["FULL"]
    full_oos = full_alpha.get("oos") or {}
    baseline_oos = ablation["cumulative"]["PRICE"].get("oos") or {}
    full_passed = bool(
        full_alpha.get("status") == "FITTED"
        and full_alpha.get("monotonicity", {}).get("status") == "PASS"
        and full_alpha.get("probability_separation", {}).get("status") == "PASS"
        and full_oos.get("pr_auc") is not None
        and baseline_oos.get("pr_auc") is not None
        and full_oos["pr_auc"] > baseline_oos["pr_auc"]
    )
    validation_status = (
        "DATA_INSUFFICIENT"
        if gate.get("status") != "PASS" or len(complete) < MIN_CALIBRATION_SAMPLES
        else "VALIDATED" if calibration.get("status") == "VALIDATED" and full_passed
        else "EXPERIMENTAL"
    )
    collapsed = set(calibration.get("collapsed_features") or [])
    def _family_increment(model_name: str) -> bool:
        current = ((ablation["cumulative"].get(model_name) or {}).get("oos") or {}).get("pr_auc")
        price = baseline_oos.get("pr_auc")
        return current is not None and price is not None and current > price

    family_features = {
        "PRICE": ("price_strength",),
        "CAPITAL": CAPITAL_FEATURES,
        "SUPPLY": ("supply_absorption",),
        "PRICING_GAP": ("real_pricing_gap",),
        "REPRICING": ("repricing_state",),
        "FUTURE_BUYER": ("future_buyer_evidence",),
    }
    family_models = {
        "PRICE": "PRICE",
        "CAPITAL": "PRICE + CAPITAL",
        "SUPPLY": "PRICE + CAPITAL + SUPPLY",
        "PRICING_GAP": "PRICE + CAPITAL + PRICING GAP",
        "REPRICING": "PRICE + CAPITAL + REPRICING",
        "FUTURE_BUYER": "PRICE + CAPITAL + FUTURE BUYER",
    }
    production_alpha_permissions = {}
    for family, names in family_features.items():
        collapsed_family = any(name in collapsed for name in names)
        incremented = family == "PRICE" or _family_increment(family_models[family])
        if collapsed_family or not incremented:
            production_alpha_permissions[family] = "NONE"
        elif validation_status == "VALIDATED":
            production_alpha_permissions[family] = "PRODUCTION"
        else:
            production_alpha_permissions[family] = "RESEARCH_ONLY"
    family_oos_increment = {
        family: family == "PRICE" or _family_increment(family_models[family])
        for family in family_features
    }
    feature_permission = {
        "price_strength": family_oos_increment["PRICE"],
        "turnover": _family_increment("PRICE + VOLUME"),
        "capital_flow_ratio": family_oos_increment["CAPITAL"],
        "capital_persistence": family_oos_increment["CAPITAL"],
        "capital_acceleration": family_oos_increment["CAPITAL"],
        "capital_inflection": family_oos_increment["CAPITAL"],
        "capital_price_efficiency": family_oos_increment["CAPITAL"],
        "capital_price_divergence": family_oos_increment["CAPITAL"],
        "capital_price_impact": False,
        "supply_absorption": family_oos_increment["SUPPLY"],
        "real_pricing_gap": family_oos_increment["PRICING_GAP"],
        "repricing_state": family_oos_increment["REPRICING"],
        "future_buyer_evidence": family_oos_increment["FUTURE_BUYER"],
    }
    production_permission = (
        "PRODUCTION"
        if validation_status == "VALIDATED" and all(family_oos_increment.values())
        else "NONE"
    )
    calibration["production_permission"] = production_permission
    calibration["status"] = validation_status
    calibration["production_gates"] = {
        "data_quality": gate.get("status") == "PASS",
        "oos_pass": bool((calibration.get("oos") or {}).get("passed")),
        "monotonicity": calibration.get("monotonicity", {}).get("status") == "PASS",
        "probability_separation": calibration.get("probability_separation", {}).get("status") == "PASS",
        "full_alpha_baseline_increment": full_passed,
        "capital_supply_repricing_increment": any(
            family_oos_increment[name] for name in ("CAPITAL", "SUPPLY", "REPRICING")
        ),
    }
    production_feature_names = {
        name for name in (calibration.get("feature_names") or MINIMAL_ALPHA_FEATURES)
        if name not in collapsed and feature_permission.get(name, False)
    }
    # PRICE remains the frozen comparator and current minimal production
    # candidate even while BUY is blocked by incomplete truth or OOS gates.
    if "price_strength" not in collapsed:
        production_feature_names.add("price_strength")
    for name, item in feature_source_matrix.items():
        item["production_permission"] = (
            "PRODUCTION"
            if validation_status == "VALIDATED" and name in production_feature_names
            else "RESEARCH_ONLY"
            if item.get("status") == "OK"
            else "NONE"
        )
    return {
        "data_status": "READY" if gate.get("status") == "PASS" else "BLOCKED",
        "status": validation_status,
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
        "feature_source_matrix": feature_source_matrix,
        "production_alpha_features": sorted(production_feature_names),
        "removed_features": sorted(set(PRODUCTION_FEATURES) - production_feature_names),
        "feature_coverage": {
            name: {
                "observed_count": item.get("observed_count"),
                "available_count": item.get("available_count"),
                "missing_rate": item.get("missing_rate"),
                "valid_rate": item.get("valid_rate"),
                "status": item.get("status"),
            }
            for name, item in feature_source_matrix.items()
        },
        "feature_groups": feature_groups,
        "feature_diagnostics": diagnose_features(rows),
        "ablation": ablation,
        "probability_separation": full_alpha.get("probability_separation", {}),
        "production_gates": {
            "data_quality": gate.get("status") == "PASS",
            "oos_pass": bool((calibration.get("oos") or {}).get("passed")),
            "monotonicity": calibration.get("monotonicity", {}).get("status") == "PASS",
            "probability_separation": calibration.get("probability_separation", {}).get("status") == "PASS",
            "full_alpha_baseline_increment": full_passed,
            "capital_supply_repricing_increment": any(
                family_oos_increment[name]
                for name in ("CAPITAL", "SUPPLY", "REPRICING")
            ),
        },
        "production_alpha_permissions": production_alpha_permissions,
        "family_oos_increment": family_oos_increment,
        "production_permission": production_permission,
        "artifact": {
            "model_id": calibration.get("model_id", "profit_window_alpha_5d_v4"),
            "model_version": calibration.get("model_version", "v4"),
            "dataset_hash": calibration.get("dataset_hash"),
            "dataset_version": calibration.get("dataset_version", "historical_profit_window_v4"),
            "feature_version": calibration.get("feature_version", "minimal_price_alpha_v1"),
            "schema_version": calibration.get("schema_version", "alpha_artifact_v1"),
            "target_version": calibration.get("target_version", "PROFIT_WINDOW_5D"),
            "horizon": 5,
            "cost_model_version": COST_MODEL_VERSION,
            "train_window": calibration.get("train_window", {"count": len(train)}),
            "validation_window": calibration.get("validation_window", {"count": len(validation)}),
            "oos_window": calibration.get("oos_window", {"count": len(oos)}),
            "production_permission": production_permission,
            "status": validation_status,
        },
        "calibration": calibration,
        "core_alpha_status": validation_status,
    }
