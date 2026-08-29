"""Five-day profit-window alpha; it measures evidence and never emits states."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from xiaogu_forward_features import FEATURE_GROUPS

MODEL_ID = "profit_window_alpha_5d_v2"
FEATURE_VERSION = "capital_behavior_measurements_v2"
PROFIT_WINDOW_DAYS = 5
CANONICAL_COST_MODEL = {"commission": 0.0005, "stamp_duty": 0.0005, "slippage": 0.0, "spread": 0.0, "transaction_cost": 0.003}
DEFAULT_COST_RATE = CANONICAL_COST_MODEL["transaction_cost"]
DEFAULT_PROFIT_TARGET = 0.02
CALIBRATION_PATH = Path(__file__).resolve().parent / "data" / "research" / "profit_window_calibration.json"


def _clip(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _round_or_none(value: Any, digits: int = 8) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


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


def _direct_evidence_items(behavior: Dict[str, Any]) -> list[Dict[str, Any]]:
    items = [item for item in (behavior.get("evidence") or []) if isinstance(item, dict)]
    return [
        item for item in items
        if item.get("observed") and str(item.get("evidence_family") or "").startswith("DIRECT_")
    ]


def _capital_convergence(capital: Dict[str, Any]) -> Dict[str, Any]:
    behaviors = {
        "institution": dict(capital.get("institution_behavior") or {}),
        "main_force": dict(capital.get("main_force_behavior") or {}),
        "hot_money": dict(capital.get("hot_money_behavior") or {}),
    }
    channels = {
        name: _clip(behavior.get("strength"))
        if behavior.get("evidence_status") == "OBSERVED" and _direct_evidence_items(behavior)
        else 0.0
        for name, behavior in behaviors.items()
    }
    levels = {
        key: "HIGH" if value >= 0.70 else "MEDIUM" if value >= 0.40 else "LOW" if value > 0 else "UNKNOWN"
        for key, value in channels.items()
    }
    confirmed = sum(value >= 0.50 for value in channels.values())
    observed = sum(value > 0 for value in channels.values())
    evidence_items = [item for behavior in behaviors.values() for item in _direct_evidence_items(behavior)]
    independent_sources = {str(item.get("source_id") or item.get("source") or "") for item in evidence_items if item.get("source_id") or item.get("source")}
    independent_families = {str(item.get("evidence_family") or "") for item in evidence_items if item.get("evidence_family")}
    independent_origins = {
        (str(item.get("source_id") or item.get("source") or ""), str(item.get("event_id") or ""))
        for item in evidence_items
        if item.get("source_id") or item.get("source")
    }
    independent_mechanisms = {str(item.get("mechanism") or item.get("evidence_family") or "") for item in evidence_items if item.get("mechanism") or item.get("evidence_family")}
    independent_channel_count = len(independent_origins)
    score = _mean(*[value for value in channels.values() if value > 0])
    distribution = _clip(capital.get("distribution_risk"))
    conflict = distribution >= 0.70 or capital.get("capital_price_impact_state") in {
        "DISTRIBUTION_RISK",
        "PRICE_SUPPORTED_DIVERGENCE",
    }
    bullish_directions = {
        "INSTITUTION_BUYING", "INSTITUTION_ACCUMULATING",
        "MAIN_FORCE_LIKELY_ACCUMULATING",
        "HOT_MONEY_BUYING", "HOT_MONEY_ACCELERATING",
    }
    directional = [
        name for name, behavior in behaviors.items()
        if channels[name] >= 0.50 and str(behavior.get("direction") or "") in bullish_directions
    ]
    aligned = len(directional) >= 2
    status = (
        "CONFLICT" if conflict
        else "UNKNOWN" if observed == 0
        else "CONVERGENCE" if confirmed >= 2 and independent_channel_count >= 2 and aligned
        else "PARTIAL"
    )
    qualities = [_clip(behavior.get("confidence")) for behavior in behaviors.values() if _direct_evidence_items(behavior)]
    freshness = _mean(*[
        1.0 if item.get("available_at") else 0.5
        for item in evidence_items
    ])
    confidence = _clip(
        (independent_channel_count / 3.0) * _mean(*qualities) * freshness
    ) if evidence_items else 0.0
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
        "evidence_count": len(evidence_items),
        "independent_channel_count": independent_channel_count,
        "independent_evidence_count": independent_channel_count,
        "independent_origins": sorted(f"{source}|{event}" for source, event in independent_origins),
        "independent_mechanisms": sorted(independent_mechanisms),
        "independent_sources": sorted(independent_sources),
        "independent_families": sorted(independent_families),
        "confidence": round(confidence, 8),
        "behaviors": behaviors,
        "status": status,
        "state": status,
    }


def _repricing_state(capital: Dict[str, Any], supply: Dict[str, Any], market: Dict[str, Any], convergence: Dict[str, Any]) -> Dict[str, Any]:
    evidence = []
    if (capital.get("distribution_risk") or 0.0) >= 0.70 or capital.get("capital_price_impact_state") == "DISTRIBUTION_RISK":
        evidence.append("distribution_risk")
        state = "DISTRIBUTION"
    elif market.get("stage") in {"CLIMAX", "DISTRIBUTION"}:
        evidence.append("market_stage")
        state = market["stage"]
    elif (market.get("attention") or 0.0) >= 0.85 and (market.get("price_strength") or 0.0) >= 0.80:
        evidence.extend(["attention_extreme", "price_strength"])
        state = "CLIMAX"
    elif (
        convergence["status"] == "CONVERGENCE"
        and capital.get("fund_flow_acceleration") is not None
        and capital.get("fund_flow_acceleration") >= 0.60
        and (market.get("price_strength") or 0.0) >= 0.50
    ):
        evidence.extend(["capital_acceleration", "price_response", "capital_convergence"])
        state = "IGNITION"
    elif convergence["status"] == "CONVERGENCE" and (market.get("sector_breadth") or 0.0) >= 0.60 and (market.get("leader_strength") or 0.0) >= 0.60:
        evidence.extend(["capital_convergence", "breadth_expansion", "leader_strength"])
        state = "EXPANSION"
    elif (
        capital.get("fund_flow_persistence") is not None
        and capital.get("fund_flow_persistence") >= 0.55
        and supply.get("supply_absorption_state") == "ABSORPTION"
        and (market.get("price_strength") or 0.0) < 0.55
    ):
        evidence.extend(["capital_persistence", "supply_absorption", "price_contained"])
        state = "ACCUMULATION"
    else:
        state = "UNKNOWN"
    return {
        "state": state,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "confidence": round(_clip(len(evidence) / 3.0), 8),
    }


def _calibrated_probability(values: Dict[str, float]) -> tuple[float | None, Dict[str, Any]]:
    if not CALIBRATION_PATH.exists():
        return None, {"status": "DATA_INSUFFICIENT", "reason": "CALIBRATION_ARTIFACT_MISSING"}
    try:
        model = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, {"status": "DATA_INSUFFICIENT", "reason": f"CALIBRATION_ARTIFACT_INVALID:{type(exc).__name__}"}
    artifact_status = str(model.get("status") or "").upper()
    default_permissions = {
        "PRICE": "RESEARCH_ONLY",
        "CAPITAL": "RESEARCH_ONLY",
        "SUPPLY": "RESEARCH_ONLY",
        "PRICING_GAP": "RESEARCH_ONLY",
        "REPRICING": "RESEARCH_ONLY",
        "FUTURE_BUYER": "RESEARCH_ONLY",
        "REFLEXIVITY": "RESEARCH_ONLY",
    }
    if artifact_status not in {"CALIBRATED", "EXPERIMENTAL", "VALIDATED"}:
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_STATUS_INVALID" if artifact_status not in {"DATA_INSUFFICIENT"} else str(model.get("reason") or "DATA_INSUFFICIENT"),
            "validation_status": "DATA_INSUFFICIENT",
            "oos": model.get("oos") if isinstance(model.get("oos"), dict) else {},
            "production_gates": model.get("production_gates") if isinstance(model.get("production_gates"), dict) else {},
            "production_alpha_permissions": model.get("production_alpha_permissions") or default_permissions,
            "collapsed_features": model.get("collapsed_features") or [],
        }
    names = model.get("feature_names") or []
    coefficients = model.get("coefficients") or []
    if len(names) != len(coefficients):
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_CONTRACT_INVALID",
            "validation_status": "DATA_INSUFFICIENT",
            "production_alpha_permissions": model.get("production_alpha_permissions") or default_permissions,
            "collapsed_features": model.get("collapsed_features") or [],
        }
    oos = model.get("oos") if isinstance(model.get("oos"), dict) else {}
    production_gates = model.get("production_gates") if isinstance(model.get("production_gates"), dict) else {}
    required_gates = (
        "data_quality",
        "oos_pass",
        "monotonicity",
        "probability_separation",
        "full_alpha_baseline_increment",
        "capital_supply_repricing_increment",
    )
    gates_pass = all(production_gates.get(name) is True for name in required_gates)
    if artifact_status == "VALIDATED" and oos.get("passed") is True and gates_pass:
        validation_status = "VALIDATED"
    elif artifact_status == "CALIBRATED":
        validation_status = "CALIBRATED_ONLY"
    else:
        validation_status = "EXPERIMENTAL"
    imputer = model.get("imputer") if isinstance(model.get("imputer"), dict) else {}
    logit = float(model.get("intercept") or 0.0)
    for name, weight in zip(names, coefficients):
        value = values.get(name)
        if value is None:
            value = imputer.get(name)
        if value is None:
            if float(weight) == 0.0:
                continue
            return None, {
                "status": "DATA_INSUFFICIENT",
                "reason": f"FEATURE_MISSING:{name}",
                "validation_status": "DATA_INSUFFICIENT",
                "production_alpha_permissions": model.get("production_alpha_permissions") or default_permissions,
                "collapsed_features": model.get("collapsed_features") or [],
            }
        logit += float(weight) * float(value)
    probability = 1.0 / (1.0 + pow(2.718281828459045, -max(-30.0, min(30.0, logit))))
    return _clip(probability), {
        "status": artifact_status,
        "validation_status": validation_status,
        "model_id": model.get("model_id"),
        "oos": oos,
        "production_gates": production_gates,
        "production_alpha_permissions": model.get("production_alpha_permissions") or {
            "PRICE": "RESEARCH_ONLY",
            "CAPITAL": "RESEARCH_ONLY",
            "SUPPLY": "RESEARCH_ONLY",
            "PRICING_GAP": "RESEARCH_ONLY",
            "REPRICING": "RESEARCH_ONLY",
            "FUTURE_BUYER": "RESEARCH_ONLY",
            "REFLEXIVITY": "RESEARCH_ONLY",
        },
        "collapsed_features": model.get("collapsed_features") or [],
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
    buyer_observed = buyer_capacity > 0

    # Diagnostic axes remain measurements. Production probability uses only the
    # calibrated artifact below, never a second handmade average of these axes.
    axes = {
        "BUSINESS": business["score"],
        "FUTURE_DEMAND": demand["score"],
        "CAPITAL": capital_measure["accumulation"],
        "SUPPLY": supply["supply_absorption"],
        "PRICING_GAP": gap["real_pricing_gap"],
        "REFLEXIVITY": reflexivity["score"],
        "MARKET": market["score"],
    }
    repricing = _repricing_state(capital_measure, supply, market, convergence)
    repricing_state = repricing["state"]
    completion = {
        "price_expanded": (market["price_strength"] or 0.0) >= 0.80,
        "valuation_expanded": gap.get("price_reflection") is not None and gap["price_reflection"] >= 0.80,
        "attention_extreme": (market["attention"] or 0.0) >= 0.85,
        "institutional_saturated": _clip(capital_measure["institutional_flow"]) >= 0.90,
        "hot_money_crowded": (capital_measure["hot_money_flow"] or 0.0) >= 0.90,
    }
    completion["completed"] = bool(
        sum(bool(value) for key, value in completion.items() if key != "completed") >= 2
        or repricing_state == "CLIMAX"
    )

    execution_feasibility = (
        None if execution.get("execution_feasibility") is None
        else _clip(execution["execution_feasibility"] * (1.0 - (execution["short_term_overheat"] or 0.0) * 0.35))
    )
    probability_features = {
        "capital_convergence": convergence["score"],
        "capital_persistence": capital_measure["fund_flow_persistence"],
        "capital_acceleration": capital_measure["fund_flow_acceleration"],
        "supply_absorption": supply["supply_absorption"],
        "pricing_gap": gap["real_pricing_gap"],
        "repricing_state": None if repricing_state == "UNKNOWN" else {"ACCUMULATION": 0.35, "IGNITION": 0.65, "EXPANSION": 0.55, "CLIMAX": 0.80, "DISTRIBUTION": 0.10}.get(repricing_state),
        "future_buyer_evidence": buyer_capacity if buyer_observed else None,
        "reflexivity": reflexivity["score"],
        "market_state": market["score"],
        "execution_quality": execution_feasibility,
        "risk": risk["downside"],
    }
    profit_window_probability, calibration = _calibrated_probability(probability_features)
    oos = calibration.get("oos") if isinstance(calibration.get("oos"), dict) else {}
    probability_std = oos.get("probability_std")
    if calibration.get("validation_status") == "VALIDATED" and (
        probability_std is not None and float(probability_std) < 0.02
    ):
        calibration["validation_status"] = "MODEL_NOT_DISCRIMINATIVE"
        calibration["reason"] = "PROBABILITY_COLLAPSE"
    repricing_evidence_score = _mean(
        capital_measure["accumulation"],
        supply["supply_absorption"],
        gap["real_pricing_gap"],
        probability_features["repricing_state"],
    )
    # No conditional expected-profit model exists. Do not copy OOS averages
    # onto each candidate; keep these fields unset so BUY stays fail-closed.
    model_status = calibration.get("validation_status", calibration.get("status"))
    expected_max_profit_5d = None
    expected_mae_5d = None
    expected_time_to_profit = None
    expected_net_profit_window = None

    readiness = {
        "BUSINESS_READY": (axes["BUSINESS"] or 0.0) >= 0.50,
        "FUTURE_DEMAND_READY": (axes["FUTURE_DEMAND"] or 0.0) >= 0.50,
        "CAPITAL_CONVERGENCE_READY": convergence["status"] == "CONVERGENCE",
        "SUPPLY_ABSORPTION_READY": supply["supply_absorption_state"] == "ABSORPTION",
        "PRICING_GAP_READY": gap.get("score") is not None and gap["score"] >= 0.35,
        "FUTURE_BUYERS_READY": buyer_observed and buyer_capacity >= 0.35,
        "REFLEXIVITY_READY": (reflexivity["score"] or 0.0) >= 0.35 and (reflexivity["break"] or 0.0) < 0.70,
        "EXECUTION_FEASIBLE": execution_feasibility is not None and execution_feasibility >= 0.35,
        "RISK_READY": (risk["score"] or 0.0) >= 0.50,
        "MARKET_READY": (market["score"] or 0.0) >= 0.35,
        "COMPLETION_CLEAR": not completion["completed"],
        "PROFIT_WINDOW_READY": (
            model_status == "VALIDATED"
            and profit_window_probability is not None
            and profit_window_probability >= 0.45
            and expected_net_profit_window is not None
            and expected_net_profit_window > 0
        ),
    }
    thesis_inputs = list(axes.values()) + [risk["score"], execution_feasibility]
    if buyer_observed:
        thesis_inputs.append(buyer_capacity)
    thesis_score = _mean(*thesis_inputs)
    core_alpha_status = model_status
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
            if value is not None and value >= 0.50
        ],
        "who_may_sell": [
            key for key, value in (("overhead_supply", supply["overhead_supply"]), ("shareholder_reduction", supply["shareholder_reduction"]), ("unlocking", supply["unlocking_pressure"]))
            if value is not None and value > 0.40
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
        "output_status": (
            "PAPER_PRODUCTION" if model_status == "VALIDATED"
            else "EXPERIMENTAL" if model_status == "EXPERIMENTAL"
            else "DATA_INSUFFICIENT"
        ),
        "alpha_version": MODEL_ID,
        "feature_version": FEATURE_VERSION,
        "target": "PROFIT_WINDOW_5D",
        "feature_families": list(FEATURE_GROUPS),
        "axes": axes,
        "readiness": readiness,
        "repricing_readiness": readiness,
        "REPRICING_READINESS": readiness,
        "repricing_readiness_score": round(_mean(*([axes["FUTURE_DEMAND"], convergence["score"], axes["SUPPLY"], gap["real_pricing_gap"], axes["REFLEXIVITY"]] + ([buyer_capacity] if buyer_observed else []))), 8),
        "repricing_state_evidence": repricing,
        "business_quality": _round_or_none(axes["BUSINESS"]),
        "future_demand": _round_or_none(axes["FUTURE_DEMAND"]),
        "capital_accumulation": _round_or_none(capital_measure["accumulation"]),
        "capital_convergence": convergence,
        "capital_convergence_status": convergence["status"],
        "capital_convergence_level": (
            "HIGH" if convergence["status"] == "CONVERGENCE" and convergence["score"] >= 0.70
            else "MEDIUM" if convergence["status"] == "CONVERGENCE"
            else "LOW" if convergence["status"] == "CONFLICT"
            else "UNKNOWN"
        ),
        "capital_price_impact": _round_or_none(capital_measure["capital_price_impact"]),
        "capital_price_impact_state": capital_measure["capital_price_impact_state"],
        "supply_absorption": _round_or_none(supply["supply_absorption"]),
        "supply_pressure": _round_or_none(supply["supply_pressure"]),
        "pricing_gap": _round_or_none(gap["score"]),
        "real_pricing_gap": _round_or_none(gap["real_pricing_gap"]),
        "low_price": bool(gap["low_price"]),
        "future_buyer_capacity": round(buyer_capacity, 8),
        "future_buyer_evidence": (future_buyer_map or {}).get("buyer_evidence", {}),
        "reflexivity": _round_or_none(reflexivity["score"]),
        "reflexivity_strength": _round_or_none(reflexivity["score"]),
        "reflexivity_break_risk": _round_or_none(reflexivity["break"]),
        "crowding_risk": _round_or_none(reflexivity.get("crowding")),
        "buyer_exhaustion": reflexivity.get("buyer_exhaustion"),
        "repricing_state": repricing_state,
        "REPRICING_STATE": repricing_state,
        "repricing_evidence_score": round(repricing_evidence_score, 8),
        "profit_window_probability": _round_or_none(profit_window_probability),
        "profit_window_calibration": calibration,
        "profit_window_feature_values": probability_features,
        "production_alpha_permissions": calibration.get("production_alpha_permissions") or {},
        "collapsed_features": calibration.get("collapsed_features") or [],
        "expected_max_profit_5d": expected_max_profit_5d,
        "expected_time_to_profit": expected_time_to_profit,
        "expected_mae_5d": expected_mae_5d,
        "expected_net_profit_window": expected_net_profit_window,
        "profit_window_target": DEFAULT_PROFIT_TARGET,
        "execution_feasibility": _round_or_none(execution_feasibility),
        "execution_constraints": {
            **CANONICAL_COST_MODEL,
            "market_impact": execution["market_impact"],
            "cost_rate": DEFAULT_COST_RATE,
        },
        "thesis_score": round(thesis_score, 8),
        "downside_risk": _round_or_none(risk["downside"]),
        "confidence": round(_mean(thesis_score, demand["evidence_strength"], execution_feasibility, convergence["score"]), 8),
        "market_stage": market["stage"],
        "reflexivity_break": _round_or_none(reflexivity["break"]),
        "repricing_completion": completion,
        "REPRICING_COMPLETION": completion,
        "contradiction": {"status": contradiction_status, "veto": contradiction_veto},
        "thesis": thesis,
        "lineage_id": features["lineage_id"],
        "expected_return_status": (
            "VALIDATED" if model_status == "VALIDATED"
            else "EXPERIMENTAL" if model_status == "EXPERIMENTAL"
            else "DATA_INSUFFICIENT"
        ),
        "model_validation": calibration,
    }
