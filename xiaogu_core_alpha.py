"""Five-day profit-window alpha; it measures evidence and never emits states."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from xiaogu_forward_features import FEATURE_GROUPS

MODEL_ID = "profit_window_alpha_5d_v2"
FEATURE_VERSION = "capital_behavior_measurements_v2"
PROFIT_WINDOW_DAYS = 5
CANONICAL_COST_MODEL = {"transaction_cost": 0.003, "slippage": 0.0, "spread": 0.0}
DEFAULT_COST_RATE = CANONICAL_COST_MODEL["transaction_cost"]
DEFAULT_PROFIT_TARGET = 0.02
CALIBRATION_PATH = Path(__file__).resolve().parent / "data" / "research" / "profit_window_calibration.json"


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _mean(*values: Any) -> float:
    numbers = [_clip(value) for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _research_score(payload: Dict[str, Any], *keys: str) -> float:
    return _mean(*(payload.get(key) for key in keys))


def _first_buyer_capacity(
    raw: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
) -> float:
    del raw
    if not isinstance(future_buyer_map, dict):
        return 0.0
    eligible = [
        _clip(item.get("capacity"))
        for item in future_buyer_map.get("potential_next_buyer") or []
        if isinstance(item, dict)
        and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
        and item.get("evidence")
        and item.get("source")
        and item.get("observed_at")
    ]
    return max(eligible, default=0.0)


def _capital_convergence(capital: Dict[str, Any]) -> Dict[str, Any]:
    behaviors = {
        "institution": dict(capital.get("institution_behavior") or {}),
        "main_force": dict(capital.get("main_force_behavior") or {}),
        "hot_money": dict(capital.get("hot_money_behavior") or {}),
    }
    channels = {
        name: _clip(behavior.get("strength"))
        if behavior.get("evidence_status") == "OBSERVED" and int(behavior.get("evidence_count") or 0) > 0
        else 0.0
        for name, behavior in behaviors.items()
    }
    levels = {
        key: "HIGH" if value >= 0.70 else "MEDIUM" if value >= 0.40 else "LOW" if value > 0 else "UNKNOWN"
        for key, value in channels.items()
    }
    confirmed = sum(value >= 0.50 for value in channels.values())
    observed = sum(value > 0 for value in channels.values())
    evidence_count = sum(int(behavior.get("evidence_count") or 0) for behavior in behaviors.values())
    independent_channel_count = sum(
        behavior.get("evidence_status") == "OBSERVED" and int(behavior.get("evidence_count") or 0) > 0
        for behavior in behaviors.values()
    )
    score = _mean(*[value for value in channels.values() if value > 0])
    distribution = _clip(capital.get("distribution_risk"))
    conflict = distribution >= 0.70 or capital.get("capital_price_impact_state") in {
        "DISTRIBUTION_RISK",
        "PRICE_SUPPORTED_DIVERGENCE",
    }
    status = "UNKNOWN" if observed == 0 else "CONFLICT" if conflict else "CONVERGENCE" if confirmed >= 2 and independent_channel_count >= 2 else "PARTIAL"
    return {
        "score": round(score, 8),
        "institution": round(channels["institution"], 8),
        "main_force": round(channels["main_force"], 8),
        "hot_money": round(channels["hot_money"], 8),
        "levels": levels,
        "institution_level": levels["institution"],
        "main_force_level": levels["main_force"],
        "hot_money_level": levels["hot_money"],
        "confirmed_channels": confirmed,
        "observed_channels": observed,
        "evidence_count": evidence_count,
        "independent_channel_count": independent_channel_count,
        "confidence": round(_mean(*(behavior.get("confidence") for behavior in behaviors.values())), 8),
        "behaviors": behaviors,
        "status": status,
        "state": status,
    }


def _repricing_state(capital: Dict[str, Any], supply: Dict[str, Any], market: Dict[str, Any], convergence: Dict[str, Any]) -> str:
    if capital.get("distribution_risk", 0) >= 0.70 or capital.get("capital_price_impact_state") == "DISTRIBUTION_RISK":
        return "DISTRIBUTION"
    if market.get("stage") in {"CLIMAX", "DISTRIBUTION"}:
        return market["stage"]
    if market.get("attention", 0) >= 0.85 and market.get("price_strength", 0) >= 0.80:
        return "CLIMAX"
    if convergence["status"] == "CONVERGENCE" and capital.get("fund_flow_acceleration", 0) >= 0.60 and market.get("price_strength", 0) >= 0.50:
        return "IGNITION"
    if convergence["status"] == "CONVERGENCE" and market.get("sector_breadth", 0) >= 0.60 and market.get("leader_strength", 0) >= 0.60:
        return "EXPANSION"
    if (
        capital.get("fund_flow_persistence", 0) >= 0.55
        and supply.get("supply_absorption_state") == "ABSORPTION"
        and market.get("price_strength", 0) < 0.55
    ):
        return "ACCUMULATION"
    return "UNKNOWN"


def _calibrated_probability(values: Dict[str, float]) -> tuple[float, Dict[str, Any]]:
    if not CALIBRATION_PATH.exists():
        return 0.0, {"status": "DATA_INSUFFICIENT", "reason": "CALIBRATION_ARTIFACT_MISSING"}
    try:
        model = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return 0.0, {"status": "DATA_INSUFFICIENT", "reason": f"CALIBRATION_ARTIFACT_INVALID:{type(exc).__name__}"}
    status = str(model.get("status") or "").upper()
    if status not in {"CALIBRATED", "VALIDATED"}:
        return 0.0, {"status": "DATA_INSUFFICIENT", "reason": "CALIBRATION_STATUS_INVALID"}
    names = model.get("feature_names") or []
    coefficients = model.get("coefficients") or []
    if len(names) != len(coefficients):
        return 0.0, {"status": "DATA_INSUFFICIENT", "reason": "CALIBRATION_CONTRACT_INVALID"}
    logit = float(model.get("intercept") or 0.0) + sum(float(weight) * values.get(name, 0.0) for name, weight in zip(names, coefficients))
    probability = 1.0 / (1.0 + pow(2.718281828459045, -max(-30.0, min(30.0, logit))))
    return _clip(probability), {
        "status": status,
        "model_id": model.get("model_id"),
        "oos": model.get("oos"),
    }


def build_core_alpha(
    features: Dict[str, Any],
    industry: Dict[str, Any],
    company: Dict[str, Any],
    capital: Dict[str, Any],
    integrated: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build T-day repricing evidence and the calibrated five-day window output."""
    business = features["BUSINESS"]
    demand = features["FUTURE_DEMAND"]
    capital_measure = features["CAPITAL"]
    supply = features["SUPPLY"]
    gap = features["PRICING_GAP"]
    reflexivity = features["REFLEXIVITY"]
    market = features["MARKET"]
    risk = features["RISK"]
    execution = features["EXECUTION"]
    raw = features["snapshot"].get("raw") if isinstance(features.get("snapshot"), dict) else {}
    convergence = _capital_convergence(capital_measure)
    buyer_capacity = _first_buyer_capacity(raw, future_buyer_map)

    axes = {
        "BUSINESS": _mean(business["score"], _research_score(company, "business_quality", "valuation")),
        "FUTURE_DEMAND": _mean(demand["score"], _research_score(industry, "demand", "bottleneck")),
        "CAPITAL": _mean(capital_measure["accumulation"], convergence["score"], capital_measure["capital_price_impact"]),
        "SUPPLY": _mean(supply["supply_absorption"], 1.0 - supply["effective_supply"]),
        "PRICING_GAP": gap["score"],
        "REFLEXIVITY": _mean(reflexivity["score"], 1.0 - reflexivity["break"]),
        "MARKET": market["score"],
    }
    repricing_state = _repricing_state(capital_measure, supply, market, convergence)
    completion = {
        "price_expanded": market["price_strength"] >= 0.80,
        "valuation_expanded": gap["price_reflection"] >= 0.80,
        "attention_extreme": market["attention"] >= 0.85,
        "institutional_saturated": _clip(capital_measure["institutional_flow"]) >= 0.90,
        "hot_money_crowded": capital_measure["hot_money_flow"] >= 0.90,
    }
    completion["completed"] = bool(
        sum(bool(value) for key, value in completion.items() if key != "completed") >= 2
        or repricing_state == "CLIMAX"
    )

    execution_feasibility = _clip(execution["execution_feasibility"] * (1.0 - execution["short_term_overheat"] * 0.35))
    probability_features = {
        "capital_convergence": convergence["score"],
        "capital_persistence": capital_measure["fund_flow_persistence"],
        "capital_acceleration": capital_measure["fund_flow_acceleration"],
        "supply_absorption": supply["supply_absorption"],
        "pricing_gap": gap["score"],
        "repricing_state": {"ACCUMULATION": 0.35, "IGNITION": 0.65, "EXPANSION": 0.55}.get(repricing_state, 0.0),
        "future_buyer_evidence": buyer_capacity,
        "reflexivity": reflexivity["score"],
        "market_state": market["score"],
        "execution_quality": execution_feasibility,
        "risk": risk["downside"],
    }
    profit_window_probability, calibration = _calibrated_probability(probability_features)
    repricing_probability = _mean(
        convergence["score"], supply["supply_absorption"], gap["score"], probability_features["repricing_state"]
    )
    # Expected outcome fields are model outputs, not deterministic transforms
    # of the same-day evidence.  Keep them absent until an OOS-validated
    # calibration artifact supplies them; this prevents an unvalidated alpha
    # from manufacturing a positive expected return.
    expected_max_profit_5d = None
    expected_mae_5d = None
    expected_time_to_profit = None
    expected_net_profit_window = None

    readiness = {
        "BUSINESS_READY": axes["BUSINESS"] >= 0.50,
        "FUTURE_DEMAND_READY": axes["FUTURE_DEMAND"] >= 0.50,
        "CAPITAL_CONVERGENCE_READY": convergence["status"] == "CONVERGENCE",
        "SUPPLY_ABSORPTION_READY": supply["supply_absorption_state"] == "ABSORPTION",
        "PRICING_GAP_READY": gap["score"] >= 0.35,
        "FUTURE_BUYERS_READY": buyer_capacity >= 0.35,
        "REFLEXIVITY_READY": reflexivity["score"] >= 0.35 and reflexivity["break"] < 0.70,
        "EXECUTION_FEASIBLE": execution_feasibility >= 0.35,
        "RISK_READY": risk["score"] >= 0.50,
        "MARKET_READY": market["score"] >= 0.35,
        "COMPLETION_CLEAR": not completion["completed"],
        "PROFIT_WINDOW_READY": (
            calibration["status"] == "VALIDATED"
            and profit_window_probability >= 0.45
            and expected_net_profit_window is not None
            and expected_net_profit_window > 0
        ),
    }
    thesis_score = _mean(*axes.values(), risk["score"], execution_feasibility)
    model_status = calibration["status"]
    core_alpha_status = calibration["status"]
    contradiction = integrated.get("contradiction") if isinstance(integrated.get("contradiction"), dict) else integrated
    contradiction_status = str(contradiction.get("contradiction_status") or contradiction.get("status") or "UNKNOWN").upper()
    contradiction_veto = contradiction_status in {"BEARISH", "VETO"} or bool(contradiction.get("veto"))
    buyers = [
        item for item in ((future_buyer_map or {}).get("potential_next_buyer") or [])
        if isinstance(item, dict) and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
    ]
    thesis = {
        "why_future_buyers": [
            f"{item.get('buyer', 'unknown')}: {item.get('trigger', 'trigger not supplied')}"
            for item in buyers
        ] or ["future demand evidence and buyer triggers remain incomplete"],
        "who_is_buying": [
            key for key, value in (("institution", convergence["institution"]), ("main_force", convergence["main_force"]), ("hot_money", convergence["hot_money"]))
            if value >= 0.50
        ],
        "who_may_sell": [
            key for key, value in (("overhead_supply", supply["overhead_supply"]), ("shareholder_reduction", supply["shareholder_reduction"]), ("unlocking", supply["unlocking_pressure"]))
            if value > 0.40
        ],
        "supply_absorption": supply["supply_absorption"],
        "pricing_gap": gap["score"],
        "repricing_trigger": ["future demand", "capital convergence", "supply absorption"],
        "invalidation": list(demand.get("invalidation_condition") or []) + (["capital exit or supply reversal"] if not risk["thesis_invalidated"] else ["thesis_invalidated"]),
    }
    return {
        "model_id": MODEL_ID,
        "model_status": model_status,
        "core_alpha_status": core_alpha_status,
        "output_status": "PAPER_PRODUCTION" if model_status == "VALIDATED" else "DATA_INSUFFICIENT",
        "alpha_version": MODEL_ID,
        "feature_version": FEATURE_VERSION,
        "target": "PROFIT_WINDOW_5D",
        "feature_families": list(FEATURE_GROUPS),
        "axes": axes,
        "readiness": readiness,
        "repricing_readiness": readiness,
        "REPRICING_READINESS": readiness,
        "repricing_readiness_score": round(_mean(axes["FUTURE_DEMAND"], convergence["score"], axes["SUPPLY"], gap["score"], buyer_capacity, axes["REFLEXIVITY"]), 8),
        "business_quality": round(axes["BUSINESS"], 8),
        "future_demand": round(axes["FUTURE_DEMAND"], 8),
        "capital_accumulation": round(capital_measure["accumulation"], 8),
        "capital_convergence": convergence,
        "capital_convergence_status": convergence["status"],
        "capital_convergence_level": (
            "HIGH" if convergence["status"] == "CONVERGENCE" and convergence["score"] >= 0.70
            else "MEDIUM" if convergence["status"] == "CONVERGENCE"
            else "LOW" if convergence["status"] == "CONFLICT"
            else "UNKNOWN"
        ),
        "capital_price_impact": round(capital_measure["capital_price_impact"], 8),
        "capital_price_impact_state": capital_measure["capital_price_impact_state"],
        "supply_absorption": round(supply["supply_absorption"], 8),
        "supply_pressure": round(supply["supply_pressure"], 8),
        "pricing_gap": round(gap["score"], 8),
        "real_pricing_gap": round(gap["real_pricing_gap"], 8),
        "low_price": bool(gap["low_price"]),
        "future_buyer_capacity": round(buyer_capacity, 8),
        "future_buyer_evidence": (future_buyer_map or {}).get("buyer_evidence", {}),
        "reflexivity": round(reflexivity["score"], 8),
        "reflexivity_strength": round(reflexivity["score"], 8),
        "reflexivity_break_risk": round(reflexivity["break"], 8),
        "crowding_risk": round(reflexivity.get("crowding", 0.0), 8),
        "buyer_exhaustion": reflexivity.get("buyer_exhaustion", False),
        "repricing_state": repricing_state,
        "REPRICING_STATE": repricing_state,
        "repricing_probability": round(repricing_probability, 8),
        "profit_window_probability": round(profit_window_probability, 8),
        "profit_window_calibration": calibration,
        "profit_window_feature_values": probability_features,
        "expected_max_profit_5d": expected_max_profit_5d,
        "expected_time_to_profit": expected_time_to_profit,
        "expected_mae_5d": expected_mae_5d,
        "expected_net_profit_window": expected_net_profit_window,
        "profit_window_target": DEFAULT_PROFIT_TARGET,
        "execution_feasibility": round(execution_feasibility, 8),
        "execution_constraints": {
            **CANONICAL_COST_MODEL,
            "market_impact": execution["market_impact"],
            "cost_rate": DEFAULT_COST_RATE,
        },
        "thesis_score": round(thesis_score, 8),
        "downside_risk": round(_clip(risk["downside"]), 8),
        "confidence": round(_mean(thesis_score, demand["evidence_strength"], execution_feasibility, convergence["score"]), 8),
        "market_stage": market["stage"],
        "reflexivity_break": reflexivity["break"],
        "repricing_completion": completion,
        "REPRICING_COMPLETION": completion,
        "contradiction": {"status": contradiction_status, "veto": contradiction_veto},
        "thesis": thesis,
        "lineage_id": features["lineage_id"],
        "expected_return_status": "VALIDATED" if model_status == "VALIDATED" else "DATA_INSUFFICIENT",
        "model_validation": calibration,
    }
