"""Five-day profit-window alpha; it measures evidence and never emits states."""
from __future__ import annotations

from typing import Any, Dict

from xiaogu_forward_features import FEATURE_GROUPS

MODEL_ID = "profit_window_alpha_5d_v1"
FEATURE_VERSION = "price_formation_measurements_v1"
PROFIT_WINDOW_DAYS = 5
DEFAULT_COST_RATE = 0.003
DEFAULT_PROFIT_TARGET = 0.02


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
    capital: Dict[str, Any],
    demand: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
) -> float:
    if isinstance(future_buyer_map, dict) and future_buyer_map.get("future_buyer_capacity") is not None:
        return _clip(future_buyer_map.get("future_buyer_capacity"))
    explicit = raw.get("future_buyer_capacity")
    if explicit not in (None, ""):
        return _clip(explicit)
    return _mean(capital["institutional_flow"], capital["hot_money_flow"], demand["demand_visibility"])


def _capital_convergence(capital: Dict[str, Any]) -> Dict[str, Any]:
    institution = _clip(capital.get("institutional_flow"))
    main_force = _clip(capital.get("main_force_flow"))
    hot_money = _mean(capital.get("hot_money_flow"), capital.get("lhb_quality"), capital.get("seat_behavior"))
    channels = {
        "institution": institution,
        "main_force": main_force,
        "hot_money": hot_money,
    }
    levels = {
        key: "HIGH" if value >= 0.70 else "MEDIUM" if value >= 0.40 else "LOW" if value > 0 else "UNKNOWN"
        for key, value in channels.items()
    }
    confirmed = sum(value >= 0.50 for value in channels.values())
    observed = sum(value > 0 for value in channels.values())
    # Convergence rewards the weakest participating channel. This prevents a
    # single strong flow source from disguising absent confirmation elsewhere.
    ordered = sorted(channels.values(), reverse=True)
    score = _clip(0.45 * ordered[0] + 0.35 * ordered[1] + 0.20 * ordered[2])
    distribution = _clip(capital.get("distribution_risk"))
    conflict = distribution >= 0.70 or (
        ordered[0] >= 0.70 and ordered[-1] < 0.20 and observed >= 2
    )
    status = "UNKNOWN" if observed == 0 else "CONFLICT" if conflict else "CONVERGENCE" if confirmed >= 2 else "UNKNOWN"
    return {
        "score": round(score, 8),
        "institution": round(institution, 8),
        "main_force": round(main_force, 8),
        "hot_money": round(hot_money, 8),
        "levels": levels,
        "institution_level": levels["institution"],
        "main_force_level": levels["main_force"],
        "hot_money_level": levels["hot_money"],
        "confirmed_channels": confirmed,
        "observed_channels": observed,
        "status": status,
        # Read compatibility for callers written before the formal status.
        "state": "CONVERGENT" if status == "CONVERGENCE" else "PARTIAL" if observed else "UNOBSERVED",
    }


def build_core_alpha(
    features: Dict[str, Any],
    industry: Dict[str, Any],
    company: Dict[str, Any],
    capital: Dict[str, Any],
    integrated: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build T-day repricing evidence and a five-day realizable-window estimate."""
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
    buyer_capacity = _first_buyer_capacity(raw, capital_measure, demand, future_buyer_map)

    axes = {
        "BUSINESS": _mean(business["score"], _research_score(company, "business_quality", "valuation")),
        "FUTURE_DEMAND": _mean(demand["score"], _research_score(industry, "demand", "bottleneck")),
        "CAPITAL": _mean(capital_measure["accumulation"], convergence["score"], capital_measure["capital_price_impact"]),
        "SUPPLY": _mean(supply["supply_absorption"], 1.0 - supply["effective_supply"]),
        "PRICING_GAP": gap["score"],
        "REFLEXIVITY": _mean(reflexivity["score"], 1.0 - reflexivity["break"]),
        "MARKET": market["score"],
    }
    stage = market["stage"]
    repricing_state = stage if stage != "UNKNOWN" else (
        "DISTRIBUTION" if capital_measure["distribution_risk"] >= 0.70
        else "IGNITION" if capital_measure["fund_flow_acceleration"] >= 0.60 and market["price_strength"] >= 0.50
        else "EXPANSION" if market["sector_breadth"] >= 0.60 and market["leader_strength"] >= 0.60
        else "ACCUMULATION" if capital_measure["accumulation_quality"] >= 0.45
        else "UNKNOWN"
    )
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

    repricing_probability = _clip(
        0.18 * axes["BUSINESS"]
        + 0.18 * axes["FUTURE_DEMAND"]
        + 0.22 * convergence["score"]
        + 0.16 * axes["SUPPLY"]
        + 0.14 * axes["PRICING_GAP"]
        + 0.12 * axes["MARKET"]
        - 0.15 * risk["downside"]
        - 0.10 * reflexivity["break"]
    )
    execution_feasibility = _clip(execution["execution_feasibility"] * (1.0 - execution["short_term_overheat"] * 0.35))
    profit_window_probability = _clip(
        0.60 * repricing_probability
        + 0.20 * execution_feasibility
        + 0.10 * convergence["score"]
        + 0.10 * buyer_capacity
        - 0.15 * risk["downside"]
        - 0.10 * reflexivity["break"]
    )
    expected_max_profit_5d = round(
        _clip(0.018 + 0.045 * profit_window_probability + 0.015 * gap["score"] - 0.020 * risk["downside"]),
        8,
    )
    expected_mae_5d = round(_clip(0.010 + 0.060 * risk["downside"] + 0.020 * reflexivity["break"]), 8)
    expected_time_to_profit = round(
        max(1.0, min(5.0, 5.0 - 3.0 * profit_window_probability - 0.5 * convergence["score"])),
        8,
    )
    raw_cost_rate = execution.get("cost_rate", raw.get("execution_cost_rate", DEFAULT_COST_RATE))
    try:
        cost_rate = max(0.0, min(0.02, float(raw_cost_rate)))
    except (TypeError, ValueError):
        cost_rate = DEFAULT_COST_RATE
    expected_net_profit_window = round(max(0.0, expected_max_profit_5d - cost_rate), 8)

    readiness = {
        "BUSINESS_READY": axes["BUSINESS"] >= 0.50,
        "FUTURE_DEMAND_READY": axes["FUTURE_DEMAND"] >= 0.50,
        "CAPITAL_CONVERGENCE_READY": convergence["state"] == "CONVERGENT",
        "SUPPLY_ABSORPTION_READY": supply["supply_absorption"] >= 0.35,
        "PRICING_GAP_READY": gap["score"] >= 0.35,
        "FUTURE_BUYERS_READY": buyer_capacity >= 0.35,
        "REFLEXIVITY_READY": reflexivity["score"] >= 0.35 and reflexivity["break"] < 0.70,
        "EXECUTION_FEASIBLE": execution_feasibility >= 0.35,
        "RISK_READY": risk["score"] >= 0.50,
        "MARKET_READY": market["score"] >= 0.35,
        "COMPLETION_CLEAR": not completion["completed"],
        "PROFIT_WINDOW_READY": profit_window_probability >= 0.45 and expected_net_profit_window > 0,
    }
    thesis_score = _mean(*axes.values(), risk["score"], execution_feasibility)
    model_status = str(raw.get("alpha_model_status") or "UNVERIFIED").upper()
    if model_status not in {"CALIBRATED", "VALIDATED"}:
        model_status = "UNVERIFIED"
    core_alpha_status = str(raw.get("core_alpha_status") or "").upper()
    if core_alpha_status not in {"DATA_INSUFFICIENT", "EXPERIMENTAL", "VALIDATED"}:
        core_alpha_status = "VALIDATED" if model_status == "VALIDATED" else "EXPERIMENTAL"
    contradiction = integrated.get("contradiction") if isinstance(integrated.get("contradiction"), dict) else integrated
    contradiction_status = str(contradiction.get("contradiction_status") or contradiction.get("status") or "UNKNOWN").upper()
    contradiction_veto = contradiction_status in {"BEARISH", "VETO"} or bool(contradiction.get("veto"))
    buyers = [item for item in ((future_buyer_map or {}).get("potential_next_buyer") or []) if isinstance(item, dict)]
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
        "output_status": "RESEARCH_ONLY" if model_status == "UNVERIFIED" else "VALIDATED_RESEARCH",
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
        "expected_max_profit_5d": expected_max_profit_5d,
        "expected_time_to_profit": expected_time_to_profit,
        "expected_mae_5d": expected_mae_5d,
        "expected_net_profit_window": expected_net_profit_window,
        "profit_window_target": DEFAULT_PROFIT_TARGET,
        "execution_feasibility": round(execution_feasibility, 8),
        "execution_constraints": {
            "slippage": execution["slippage"],
            "spread": execution["spread"],
            "market_impact": execution["market_impact"],
            "cost_rate": execution["cost_rate"],
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
        "expected_return_status": "UNVERIFIED" if model_status == "UNVERIFIED" else "VALIDATED",
        "model_validation": "EXPERIMENTAL" if model_status == "UNVERIFIED" else "DECLARED_VALIDATED_INPUT",
    }
