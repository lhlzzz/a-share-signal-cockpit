"""Five-day profit-window alpha; it measures evidence and never emits states."""
from __future__ import annotations

from typing import Any, Dict

from xiaogu_forward_features import FEATURE_GROUPS, validate_evidence_identity

MODEL_ID = "profit_window_alpha_5d_v4"
MODEL_VERSION = "v4"
FEATURE_VERSION = "minimal_price_alpha_v1"
TARGET_VERSION = "opportunity_5d"
SCHEMA_VERSION = "alpha_artifact_v1"
PROFIT_WINDOW_DAYS = 5
COST_MODEL_VERSION = "cost_model_v1"
CANONICAL_COST_MODEL = {
    "version": COST_MODEL_VERSION,
    "commission": 0.0005,
    "stamp_duty": 0.0005,
    "slippage": 0.001,
    "spread": 0.0005,
    "market_impact": 0.0005,
}
CANONICAL_COST_MODEL["all_in_transaction_cost"] = round(sum(
    CANONICAL_COST_MODEL[name] for name in ("commission", "stamp_duty", "slippage", "spread", "market_impact")
), 8)
DEFAULT_COST_RATE = CANONICAL_COST_MODEL["all_in_transaction_cost"]
DEFAULT_PROFIT_TARGET = 0.02
REQUIRED_ARTIFACT_FIELDS = (
    "model_id", "model_version", "feature_version", "dataset_hash", "dataset_version",
    "train_window", "validation_window", "oos_window", "cost_model_version",
    "target_version", "horizon", "schema_version", "status", "production_permission",
)


def _clip(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _round_or_none(value: Any, digits: int = 8) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _mean(*values: Any) -> float | None:
    numbers = [number for number in (_clip(value) for value in values if value is not None) if number is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _at_least(value: Any, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _below(value: Any, threshold: float) -> bool:
    return value is not None and float(value) < threshold


def _research_score(payload: Dict[str, Any], *keys: str) -> float | None:
    return _mean(*(payload.get(key) for key in keys))


SIGNAL_STATUS_SIGNAL = "SIGNAL"
SIGNAL_STATUS_WATCH = "WATCH"
SIGNAL_STATUS_ELIMINATED = "ELIMINATED"
COST_MODEL_COMPONENT_SEMANTICS = {
    "commission": "modeled",
    "stamp_duty": "modeled",
    "slippage": "proxy",
    "spread": "proxy",
    "market_impact": "proxy",
}
EXECUTION_REALISM_LEVEL = "DAILY_BAR_APPROXIMATION"


def _selection_score(
    *,
    model_status: Any,
    profit_window_probability: Any,
    price_strength: Any,
) -> float | None:
    """Sole production ranking score. Never averages diagnostic axes."""
    if model_status == "VALIDATED" and profit_window_probability is not None:
        return _round_or_none(profit_window_probability)
    return _round_or_none(price_strength)


def _signal_qualification(
    *,
    market: Dict[str, Any],
    capital_measure: Dict[str, Any],
    supply: Dict[str, Any],
    risk: Dict[str, Any],
    repricing_state: str,
    completion: Dict[str, Any],
    contradiction_veto: bool,
    selection_score: float | None,
    research_used_downstream: bool,
) -> Dict[str, Any]:
    """Qualify a formal 5D paper signal. This is not a second alpha and not BUY.

    The 0.5%–9.5% window is L2 routing / research ablation only. Production
    qualification must not re-apply it as a strategy gate.
    """
    price_strength = market.get("price_strength")
    evidence = []
    if research_used_downstream:
        evidence.append("research_context")
    if capital_measure.get("capital_flow_ratio") is not None or capital_measure.get("fund_flow") is not None:
        evidence.append("capital")
    if supply.get("supply_absorption") is not None:
        evidence.append("supply")
    if repricing_state not in {"UNKNOWN", ""}:
        evidence.append("repricing")
    score = selection_score
    if price_strength is None:
        return {
            "signal_status": SIGNAL_STATUS_ELIMINATED,
            "signal_qualified": False,
            "signal_reason": "PRICE_STRENGTH_UNOBSERVED",
            "signal_score": None,
            "selection_score": None,
            "signal_evidence": evidence,
        }
    if bool(completion.get("completed")) or repricing_state in {"CLIMAX", "DISTRIBUTION"}:
        return {
            "signal_status": SIGNAL_STATUS_ELIMINATED,
            "signal_qualified": False,
            "signal_reason": "REPRICING_COMPLETED_OR_DISTRIBUTION",
            "signal_score": score,
            "selection_score": score,
            "signal_evidence": evidence,
        }
    if contradiction_veto:
        return {
            "signal_status": SIGNAL_STATUS_ELIMINATED,
            "signal_qualified": False,
            "signal_reason": "CONTRADICTION_VETO",
            "signal_score": score,
            "selection_score": score,
            "signal_evidence": evidence,
        }
    if risk.get("thesis_invalidated") is True:
        return {
            "signal_status": SIGNAL_STATUS_ELIMINATED,
            "signal_qualified": False,
            "signal_reason": "THESIS_INVALIDATED",
            "signal_score": score,
            "selection_score": score,
            "signal_evidence": evidence,
        }
    return {
        "signal_status": SIGNAL_STATUS_SIGNAL,
        "signal_qualified": True,
        "signal_reason": "FORMAL_5D_PROFIT_WINDOW_SIGNAL",
        "signal_score": score,
        "selection_score": score,
        "signal_evidence": evidence,
    }


def _first_buyer_capacity(
    raw: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
) -> float | None:
    del raw
    if not isinstance(future_buyer_map, dict):
        return None
    eligible = [
        value for value in (
            _clip(item.get("capacity"))
            for item in future_buyer_map.get("potential_next_buyer") or []
            if isinstance(item, dict)
            and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
            and item.get("evidence")
            and item.get("source")
            and item.get("observed_at")
        ) if value is not None
    ]
    return max(eligible) if eligible else None


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
        if behavior.get("evidence_status") == "OBSERVED" and _direct_evidence_items(behavior) and behavior.get("strength") is not None
        else None
        for name, behavior in behaviors.items()
    }
    levels = {
        key: (
            "UNKNOWN" if value is None
            else "HIGH" if value >= 0.70
            else "MEDIUM" if value >= 0.40
            else "LOW"
        )
        for key, value in channels.items()
    }
    confirmed = sum(value >= 0.50 for value in channels.values() if value is not None)
    observed = sum(value is not None for value in channels.values())
    evidence_items = [item for behavior in behaviors.values() for item in _direct_evidence_items(behavior)]
    independent_sources = {str(item.get("source_id") or item.get("source") or "") for item in evidence_items if item.get("source_id") or item.get("source")}
    independent_families = {str(item.get("evidence_family") or "") for item in evidence_items if item.get("evidence_family")}
    evidence_identities = set()
    grouping_origins = set()
    independent_mechanisms = set()
    for item in evidence_items:
        identity = validate_evidence_identity(item)
        if identity is not None:
            evidence_identities.add(identity)
            independent_mechanisms.add(identity[2])
        origin = str(item.get("economic_origin_id") or "").strip()
        if origin:
            grouping_origins.add(origin)
    independent_origin_count = len(evidence_identities)
    independent_channel_count = independent_origin_count
    independent_origins = grouping_origins
    score = _mean(*[value for value in channels.values() if value is not None and value > 0])
    distribution = None if capital.get("distribution_risk") is None else _clip(capital.get("distribution_risk"))
    conflict = _at_least(distribution, 0.70) or capital.get("capital_price_impact_state") == "DISTRIBUTION_RISK"
    bullish_directions = {
        "BUYING", "ACCUMULATING", "ACCELERATING",
    }
    directional = [
        name for name, behavior in behaviors.items()
        if _at_least(channels[name], 0.50) and str(behavior.get("direction") or "") in bullish_directions
    ]
    aligned = len(directional) >= 2
    status = (
        "CONFLICT" if conflict
        else "UNKNOWN" if observed == 0
        else "CONVERGENCE" if confirmed >= 2 and independent_origin_count >= 2 and aligned
        else "PARTIAL"
    )
    qualities = [_clip(behavior.get("confidence")) for behavior in behaviors.values() if _direct_evidence_items(behavior)]
    freshness = _mean(*[
        1.0 if item.get("available_at") else 0.5
        for item in evidence_items
    ])
    quality_score = _mean(*qualities)
    confidence = _clip(
        (independent_channel_count / 3.0) * quality_score * freshness
    ) if evidence_items and quality_score is not None and freshness is not None else None
    return {
        "score": _round_or_none(score),
        "institution": _round_or_none(channels["institution"]),
        "main_force": _round_or_none(channels["main_force"]),
        "hot_money": _round_or_none(channels["hot_money"]),
        "levels": levels,
        "institution_level": levels["institution"],
        "main_force_level": levels["main_force"],
        "hot_money_level": levels["hot_money"],
        "confirmed_channels": confirmed,
        "confirmed_channel_count": confirmed,
        "observed_channels": observed,
        "evidence_count": len(evidence_items),
        "evidence_identity_count": independent_origin_count,
        "independent_origin_count": independent_origin_count,
        "independent_channel_count": independent_channel_count,
        "independent_evidence_count": independent_channel_count,
        "evidence_identities": [list(item) for item in sorted(evidence_identities)],
        "independent_origins": sorted(independent_origins),
        "independent_mechanisms": sorted(independent_mechanisms),
        "independent_sources": sorted(independent_sources),
        "independent_families": sorted(independent_families),
        "directional_alignment": aligned,
        "confidence": _round_or_none(confidence),
        "behaviors": behaviors,
        "status": status,
        "state": status,
    }


def _repricing_state(capital: Dict[str, Any], supply: Dict[str, Any], market: Dict[str, Any], convergence: Dict[str, Any]) -> Dict[str, Any]:
    evidence = []
    if _at_least(capital.get("distribution_risk"), 0.70) or capital.get("capital_price_impact_state") == "DISTRIBUTION_RISK":
        evidence.append("distribution_risk")
        state = "DISTRIBUTION"
    elif market.get("stage") in {"CLIMAX", "DISTRIBUTION"}:
        evidence.append("market_stage")
        state = market["stage"]
    elif _at_least(market.get("attention"), 0.85) and _at_least(market.get("price_strength"), 0.80):
        evidence.extend(["attention_extreme", "price_strength"])
        state = "CLIMAX"
    elif (
        convergence["status"] == "CONVERGENCE"
        and capital.get("fund_flow_acceleration") is not None
        and capital.get("fund_flow_acceleration") >= 0.60
        and _at_least(market.get("price_strength"), 0.50)
    ):
        evidence.extend(["capital_acceleration", "price_response", "capital_convergence"])
        state = "IGNITION"
    elif convergence["status"] == "CONVERGENCE" and _at_least(market.get("sector_breadth"), 0.60) and _at_least(market.get("leader_strength"), 0.60):
        evidence.extend(["capital_convergence", "breadth_expansion", "leader_strength"])
        state = "EXPANSION"
    elif (
        capital.get("fund_flow_persistence") is not None
        and capital.get("fund_flow_persistence") >= 0.55
        and supply.get("supply_absorption_state") == "ABSORPTION"
        and _below(market.get("price_strength"), 0.55)
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


def _artifact_identity(model: Dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [field for field in REQUIRED_ARTIFACT_FIELDS if model.get(field) in (None, "")]
    mismatches = []
    if model.get("model_id") not in (None, "", MODEL_ID):
        mismatches.append("model_id")
    if model.get("model_version") not in (None, "", MODEL_VERSION):
        mismatches.append("model_version")
    if model.get("feature_version") not in (None, "", FEATURE_VERSION):
        mismatches.append("feature_version")
    if model.get("cost_model_version") not in (None, "", COST_MODEL_VERSION):
        mismatches.append("cost_model_version")
    if model.get("target_version") not in (None, "", TARGET_VERSION, "opportunity_5d"):
        mismatches.append("target_version")
    if model.get("horizon") not in (None, "", PROFIT_WINDOW_DAYS, str(PROFIT_WINDOW_DAYS)):
        mismatches.append("horizon")
    if model.get("schema_version") not in (None, "", SCHEMA_VERSION):
        mismatches.append("schema_version")
    return not missing and not mismatches, missing + mismatches


def _calibrated_probability(values: Dict[str, float]) -> tuple[float | None, Dict[str, Any]]:
    default_permissions = {
        "PRICE": "NONE", "CAPITAL": "NONE", "SUPPLY": "NONE", "PRICING_GAP": "NONE",
        "REPRICING": "NONE", "FUTURE_BUYER": "NONE", "REFLEXIVITY": "NONE",
    }
    try:
        from xiaogu_db import fetch_production_model
        model = fetch_production_model(MODEL_ID)
    except Exception as exc:
        return None, {"status": "DATA_INSUFFICIENT", "reason": f"MODEL_REGISTRY_UNAVAILABLE:{type(exc).__name__}", "production_alpha_permissions": default_permissions, "collapsed_features": []}
    if not model:
        return None, {"status": "DATA_INSUFFICIENT", "reason": "MODEL_REGISTRY_MISSING", "production_alpha_permissions": default_permissions, "collapsed_features": []}
    artifact_status = str(model.get("status") or "").upper()
    identity_ok, identity_issues = _artifact_identity(model)
    oos = model.get("oos") if isinstance(model.get("oos"), dict) else {}
    production_gates = model.get("production_gates") if isinstance(model.get("production_gates"), dict) else {}
    permissions = model.get("production_alpha_permissions") or default_permissions
    # Non-validated artifacts may inform research diagnostics but cannot carry
    # family-level production permissions into a live decision.
    if str(model.get("status") or "").upper() != "VALIDATED" or model.get("production_permission") != "PRODUCTION":
        permissions = dict(default_permissions)
    collapsed = model.get("collapsed_features") or []
    if artifact_status == "VALIDATED" and (not identity_ok or collapsed or any(name in collapsed for name in model.get("feature_names") or [])):
        if collapsed:
            identity_issues = [*identity_issues, "collapsed_features"]
        return None, {
            "status": "MODEL_ARTIFACT_MISMATCH",
            "validation_status": "MODEL_ARTIFACT_MISMATCH",
            "reason": "MODEL_ARTIFACT_MISMATCH:" + ",".join(identity_issues),
            "oos": oos,
            "production_gates": production_gates,
            "production_alpha_permissions": permissions,
            "collapsed_features": collapsed,
        }
    if artifact_status not in {"CALIBRATED", "EXPERIMENTAL", "VALIDATED"}:
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_STATUS_INVALID" if artifact_status not in {"DATA_INSUFFICIENT"} else str(model.get("reason") or "DATA_INSUFFICIENT"),
            "validation_status": "DATA_INSUFFICIENT",
            "oos": oos,
            "production_gates": production_gates,
            "production_alpha_permissions": permissions,
            "collapsed_features": collapsed,
        }
    names = model.get("feature_names") or []
    coefficients = model.get("coefficients") or []
    if len(names) != len(coefficients):
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_CONTRACT_INVALID",
            "validation_status": "DATA_INSUFFICIENT",
            "production_alpha_permissions": permissions,
            "collapsed_features": collapsed,
        }
    required_gates = (
        "data_quality",
        "oos_pass",
        "monotonicity",
        "probability_separation",
        "full_alpha_baseline_increment",
        "capital_supply_repricing_increment",
    )
    gates_pass = all(production_gates.get(name) is True for name in required_gates)
    if artifact_status == "VALIDATED" and model.get("production_permission") == "PRODUCTION" and oos.get("passed") is True and gates_pass:
        validation_status = "VALIDATED"
    elif artifact_status == "CALIBRATED":
        validation_status = "CALIBRATED_ONLY"
    else:
        validation_status = "EXPERIMENTAL"
    imputer = model.get("imputer") if isinstance(model.get("imputer"), dict) else {}
    if model.get("intercept") in (None, ""):
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_CONTRACT_INVALID:intercept",
            "validation_status": "DATA_INSUFFICIENT",
            "production_alpha_permissions": permissions,
            "collapsed_features": collapsed,
        }
    try:
        logit = float(model["intercept"])
    except (TypeError, ValueError):
        return None, {
            "status": "DATA_INSUFFICIENT",
            "reason": "CALIBRATION_CONTRACT_INVALID:intercept",
            "validation_status": "DATA_INSUFFICIENT",
            "production_alpha_permissions": permissions,
            "collapsed_features": collapsed,
        }
    for name, weight in zip(names, coefficients):
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError):
            return None, {
                "status": "DATA_INSUFFICIENT",
                "reason": f"CALIBRATION_CONTRACT_INVALID:coefficient:{name}",
                "validation_status": "DATA_INSUFFICIENT",
                "production_alpha_permissions": permissions,
                "collapsed_features": collapsed,
            }
        value = values.get(name)
        if value is None:
            value = imputer.get(name)
        if value is None:
            if numeric_weight == 0.0:
                continue
            return None, {
                "status": "DATA_INSUFFICIENT",
                "reason": f"FEATURE_MISSING:{name}",
                "validation_status": "DATA_INSUFFICIENT",
                "production_alpha_permissions": permissions,
                "collapsed_features": collapsed,
            }
        logit += numeric_weight * float(value)
    probability = 1.0 / (1.0 + pow(2.718281828459045, -max(-30.0, min(30.0, logit))))
    clipped = _clip(probability)
    meta = {
        "status": artifact_status,
        "validation_status": validation_status,
        "model_id": model.get("model_id"),
        "model_version": model.get("model_version"),
        "feature_version": model.get("feature_version"),
        "dataset_hash": model.get("dataset_hash"),
        "dataset_version": model.get("dataset_version"),
        "target_version": model.get("target_version"),
        "horizon": model.get("horizon"),
        "schema_version": model.get("schema_version"),
        "production_permission": model.get("production_permission"),
        "cost_model_version": model.get("cost_model_version"),
        "oos": oos,
        "production_gates": production_gates,
        "production_alpha_permissions": permissions,
        "collapsed_features": collapsed,
        "imputer": imputer,
    }
    return clipped, meta


def _mark_research_arguments(
    research: Dict[str, Any] | None,
    *,
    industry: Any,
    company: Any,
    capital: Any,
    integrated: Any,
) -> None:
    if not isinstance(research, dict):
        return
    from xiaogu_research_context import mark_research_used_downstream

    if industry is research.get("industry"):
        mark_research_used_downstream(research, "Serenity")
    if company is research.get("company"):
        mark_research_used_downstream(research, "Buffett")
    if capital is research.get("capital"):
        mark_research_used_downstream(research, "UZI")
    if integrated is research.get("integrated") or integrated is research.get("contradiction"):
        mark_research_used_downstream(research, "Contradiction")


def _research_used_downstream(research: Dict[str, Any] | None) -> bool:
    """True only when a provider was actually read by Alpha or Decision."""
    if not isinstance(research, dict):
        return False
    providers = research.get("research_providers")
    if isinstance(providers, dict):
        return any(
            isinstance(item, dict) and item.get("used_downstream") is True
            for item in providers.values()
        )
    provenance = research.get("research_provenance")
    if isinstance(provenance, list):
        return any(
            isinstance(item, dict) and item.get("used_downstream") is True
            for item in provenance
        )
    return False


def build_core_alpha(
    features: Dict[str, Any],
    industry: Dict[str, Any],
    company: Dict[str, Any],
    capital: Dict[str, Any],
    integrated: Dict[str, Any],
    future_buyer_map: Dict[str, Any] | None = None,
    research: Dict[str, Any] | None = None,
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
    _mark_research_arguments(
        research,
        industry=industry,
        company=company,
        capital=capital,
        integrated=integrated,
    )
    convergence = _capital_convergence(capital_measure)
    buyer_capacity = _first_buyer_capacity(raw, future_buyer_map)
    buyer_observed = buyer_capacity is not None and buyer_capacity > 0

    # Diagnostic axes remain measurements. Production probability uses only the
    # calibrated artifact below, never a second handmade average of these axes.
    axes = {
        "BUSINESS": business["score"],
        "FUTURE_DEMAND": demand["score"],
        "CAPITAL": capital_measure["accumulation"] if capital_measure.get("accumulation") is not None else capital_measure.get("capital_flow_ratio"),
        "SUPPLY": supply["supply_absorption"],
        "PRICING_GAP": gap["real_pricing_gap"],
        "REFLEXIVITY": reflexivity["score"],
        "MARKET": market["score"],
    }
    repricing = _repricing_state(capital_measure, supply, market, convergence)
    repricing_state = repricing["state"]
    completion = {
        "price_expanded": _at_least(market["price_strength"], 0.80),
        "valuation_expanded": gap.get("price_reflection") is not None and gap["price_reflection"] >= 0.80,
        "attention_extreme": _at_least(market["attention"], 0.85),
        "institutional_saturated": _at_least(capital_measure["institutional_flow"], 0.90),
        "hot_money_crowded": _at_least(capital_measure["hot_money_flow"], 0.90),
    }
    completion["completed"] = bool(
        sum(bool(value) for key, value in completion.items() if key != "completed") >= 2
        or repricing_state == "CLIMAX"
    )

    execution_feasibility = (
        None if execution.get("execution_feasibility") is None
        else _clip(execution["execution_feasibility"] * (1.0 - (execution["short_term_overheat"] if execution.get("short_term_overheat") is not None else 0.0) * 0.35))
    )
    # Production alpha is intentionally minimal. Other measurements remain
    # diagnostic/research context and cannot enter the probability model.
    probability_features = {
        "price_strength": market.get("price_strength"),
    }
    research_probability, calibration = _calibrated_probability(probability_features)
    oos = calibration.get("oos") if isinstance(calibration.get("oos"), dict) else {}
    probability_std = oos.get("probability_std")
    if calibration.get("validation_status") == "VALIDATED" and (
        probability_std is not None and float(probability_std) < 0.02
    ):
        calibration["validation_status"] = "MODEL_NOT_DISCRIMINATIVE"
        calibration["reason"] = "PROBABILITY_COLLAPSE"
    model_status = calibration.get("validation_status", calibration.get("status"))
    profit_window_probability = research_probability if model_status == "VALIDATED" else None
    repricing_evidence_score = _mean(
        capital_measure.get("accumulation"),
        capital_measure.get("capital_flow_ratio"),
        supply["supply_absorption"],
        gap["real_pricing_gap"],
        {"ACCUMULATION": 0.35, "IGNITION": 0.65, "EXPANSION": 0.55, "CLIMAX": 0.80, "DISTRIBUTION": 0.10}.get(repricing_state),
    )
    # No conditional expected-profit model exists. Do not copy OOS averages
    # onto each candidate; keep these fields unset so BUY stays fail-closed.
    expected_max_profit_5d = None
    expected_mae_5d = None
    expected_time_to_profit = None
    expected_net_profit_window = None

    readiness = {
        "BUSINESS_READY": _at_least(axes["BUSINESS"], 0.50),
        "FUTURE_DEMAND_READY": _at_least(axes["FUTURE_DEMAND"], 0.50),
        "CAPITAL_CONVERGENCE_READY": convergence["status"] == "CONVERGENCE",
        "SUPPLY_ABSORPTION_READY": supply["supply_absorption_state"] == "ABSORPTION",
        "PRICING_GAP_READY": gap.get("score") is not None and gap["score"] >= 0.35,
        "FUTURE_BUYERS_READY": buyer_observed and buyer_capacity >= 0.35,
        "REFLEXIVITY_READY": _at_least(reflexivity["score"], 0.35) and _below(reflexivity["break"], 0.70),
        "EXECUTION_FEASIBLE": execution_feasibility is not None and execution_feasibility >= 0.35,
        "RISK_READY": risk["score"] is None or risk["score"] >= 0.50,
        "MARKET_READY": market["score"] is None or market["score"] >= 0.35,
        "COMPLETION_CLEAR": not completion["completed"],
        "PROFIT_WINDOW_READY": (
            model_status == "VALIDATED"
            and profit_window_probability is not None
            and profit_window_probability >= 0.45
            and expected_net_profit_window is not None
            and expected_net_profit_window > 0
        ),
    }
    core_alpha_status = model_status
    contradiction = integrated.get("contradiction") if isinstance(integrated.get("contradiction"), dict) else integrated
    contradiction_status = str(contradiction.get("contradiction_status") or contradiction.get("status") or "UNKNOWN").upper()
    contradiction_veto = contradiction_status in {"BEARISH", "VETO"} or bool(contradiction.get("veto"))
    research_used_downstream = _research_used_downstream(research)
    selection_score = _selection_score(
        model_status=model_status,
        profit_window_probability=profit_window_probability,
        price_strength=market.get("price_strength"),
    )
    qualification = _signal_qualification(
        market=market,
        capital_measure=capital_measure,
        supply=supply,
        risk=risk,
        repricing_state=repricing_state,
        completion=completion,
        contradiction_veto=contradiction_veto,
        selection_score=selection_score,
        research_used_downstream=research_used_downstream,
    )
    buyers = [
        item for item in ((future_buyer_map or {}).get("potential_next_buyer") or [])
        if isinstance(item, dict) and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
    ]
    thesis = {
        "why_future_buyers": [
            f"{item.get('buyer', 'UNKNOWN')}: {item.get('trigger', 'trigger not supplied')}"
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
        "target": TARGET_VERSION,
        "target_version": TARGET_VERSION,
        "feature_families": list(FEATURE_GROUPS),
        "axes": axes,
        "readiness": readiness,
        "repricing_readiness": readiness,
        "REPRICING_READINESS": readiness,
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
        "future_buyer_capacity": _round_or_none(buyer_capacity),
        "future_buyer_evidence": (future_buyer_map or {}).get("buyer_evidence", {}),
        "reflexivity": _round_or_none(reflexivity["score"]),
        "reflexivity_strength": _round_or_none(reflexivity["score"]),
        "reflexivity_break_risk": _round_or_none(reflexivity["break"]),
        "crowding_risk": _round_or_none(reflexivity.get("crowding")),
        "buyer_exhaustion": reflexivity.get("buyer_exhaustion"),
        "repricing_state": repricing_state,
        "REPRICING_STATE": repricing_state,
        "repricing_evidence_score": _round_or_none(repricing_evidence_score),
        "repricing_evidence_score_role": "DIAGNOSTIC_ONLY",
        "research_probability": _round_or_none(research_probability),
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
            "component_semantics": dict(COST_MODEL_COMPONENT_SEMANTICS),
            "execution_realism": {"level": EXECUTION_REALISM_LEVEL},
            "all_in_transaction_cost": DEFAULT_COST_RATE,
            "cost_rate": DEFAULT_COST_RATE,
            "cost_model_version": COST_MODEL_VERSION,
        },
        "downside_risk": _round_or_none(risk["downside"]),
        "confidence": _round_or_none(_mean(demand["evidence_strength"], execution_feasibility, convergence["score"])),
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
        "signal_status": qualification["signal_status"],
        "signal_qualified": qualification["signal_qualified"],
        "signal_reason": qualification["signal_reason"],
        "signal_score": qualification["selection_score"],
        "selection_score": qualification["selection_score"],
        "selection_score_source": (
            "profit_window_probability" if model_status == "VALIDATED" and profit_window_probability is not None
            else "price_strength"
        ),
        "signal_evidence": list(qualification["signal_evidence"]),
        "research_used_downstream": research_used_downstream,
    }
