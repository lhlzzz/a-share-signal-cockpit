"""Price-formation measurements derived from one canonical snapshot."""
from __future__ import annotations

from typing import Any, Dict, Iterable

from xiaogu_forward_snapshot import canonical_snapshot

FEATURE_GROUPS = (
    "BUSINESS",
    "FUTURE_DEMAND",
    "CAPITAL",
    "SUPPLY",
    "PRICING_GAP",
    "REFLEXIVITY",
    "MARKET",
    "RISK",
    "EXECUTION",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _first(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if payload.get(key) not in (None, "", "-"):
            return payload[key]
    return default


def _clip(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _number(value)))


def _mean(*values: Any) -> float:
    numbers = [_clip(value) for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _ratio(numerator: Any, denominator: Any) -> float:
    denominator_value = _number(denominator)
    return _clip(_number(numerator) / denominator_value) if denominator_value > 0 else 0.0


def _signed_strength(value: Any, scale: float = 10.0) -> float:
    return _clip((_number(value) / scale + 1.0) / 2.0)


def _percentile(values: Iterable[Any], current: Any) -> float:
    numbers = sorted(_number(value) for value in values)
    if not numbers:
        return 0.0
    return sum(value <= _number(current) for value in numbers) / len(numbers)


def _raw(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    value = snapshot.get("raw")
    return value if isinstance(value, dict) else {}


def _market_stage(raw: Dict[str, Any], capital: Dict[str, Any], supply: Dict[str, Any], market: Dict[str, Any]) -> str:
    explicit = str(_first(raw, "repricing_state", "market_stage", default="") or "").upper()
    if explicit in {"ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION"}:
        return explicit
    if market["attention"] >= 0.85 and market["price_strength"] >= 0.80:
        return "CLIMAX"
    if capital["capital_price_divergence"] >= 0.70 and market["price_strength"] >= 0.55:
        return "DISTRIBUTION"
    if capital["fund_flow_acceleration"] >= 0.65 and market["price_strength"] >= 0.50:
        return "IGNITION"
    if market["breadth"] >= 0.60 and market["leader_strength"] >= 0.60:
        return "EXPANSION"
    if capital["fund_flow_persistence"] >= 0.55 and supply["supply_absorption_state"] == "ABSORPTION" and market["price_strength"] < 0.55:
        return "ACCUMULATION"
    return "UNKNOWN"


def build_feature_vector(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Build bounded measurements; missing evidence remains explicitly missing."""
    # Re-validate pre-canonicalized payloads so a forged lineage cannot bypass
    # the snapshot's future-field boundary.
    snap = canonical_snapshot(snapshot)
    raw = _raw(snap)
    flow = raw.get("stock_capital_flow") if isinstance(raw.get("stock_capital_flow"), dict) else {}
    industry_flow = raw.get("industry_flow") if isinstance(raw.get("industry_flow"), dict) else {}
    earnings = raw.get("earnings_preview") if isinstance(raw.get("earnings_preview"), dict) else {}
    shareholder = raw.get("shareholder_changes") if isinstance(raw.get("shareholder_changes"), list) else []
    price = _number(snap.get("price"))
    high = _number(snap.get("high"))
    low = _number(snap.get("low"))
    amount = _number(snap.get("amount"))
    turnover = _number(snap.get("turnover"))
    pct_change = _number(_first(raw, "pct_chg", "signal_pct", "f3"))
    main_flow = _number(_first(flow, "main_net_inflow", "f62", default=_first(raw, "main_net_inflow", "f62")))
    main_flow_pct = _number(_first(flow, "main_net_inflow_pct", "f184", "f18"))
    close_position = _ratio(price - low, high - low) if high > low else 0.0
    turnover_velocity = _clip(turnover / 20.0)
    price_strength = _clip((pct_change + 5.0) / 15.0)
    amount_pctile = _clip(_first(raw, "amount_percentile", "full_universe_amount_pctile", default=0.0))
    attention = _clip(_first(raw, "attention_score", "popularity_score", "popularity_percentile", default=0.0))
    breadth = _clip(_number(_first(raw, "market_breadth_up_pct", default=0.0)) / 100.0)
    sector_breadth = _clip(_first(raw, "sector_breadth", "sector_strength", default=_number(_first(industry_flow, "f3", default=0.0)) / 10.0))
    # A flow amount without traded amount has no comparable denominator and
    # must remain unobserved rather than becoming a saturated signal.
    positive_flow = _clip(max(main_flow, 0.0) / amount) if amount > 0 else 0.0
    negative_flow = _clip(max(-main_flow, 0.0) / amount) if amount > 0 else 0.0
    price_impact = _clip(abs(pct_change) / max(abs(main_flow_pct) * 2.0, 1.0)) if main_flow or main_flow_pct else 0.0
    price_response = _clip(max(pct_change, 0.0) / 10.0)
    capital_divergence = _clip(
        _first(raw, "capital_price_divergence", default=0.0)
        if raw.get("capital_price_divergence") not in (None, "")
        else (
            1.0
            if main_flow > 0 and any(
                _number(row.get("NET_BS_AMT")) < 0
                for row in (raw.get("lhb") or [])
                if isinstance(row, dict)
            )
            else (0.75 if main_flow > 0 and pct_change <= 0 else 0.0)
        )
    )
    if not amount or not main_flow:
        capital_price_impact_state = "UNKNOWN"
    elif main_flow > 0 and pct_change > 0:
        capital_price_impact_state = "DEMAND_CONFIRMATION"
    elif main_flow > 0 and pct_change <= 0:
        capital_price_impact_state = "SUPPLY_ABSORPTION"
    elif main_flow < 0 and pct_change > 0:
        capital_price_impact_state = "PRICE_SUPPORTED_DIVERGENCE"
    elif main_flow < 0:
        capital_price_impact_state = "DISTRIBUTION_RISK"
    else:
        capital_price_impact_state = "NEUTRAL"

    business = {
        "business_quality": _clip(_first(raw, "business_quality", "financial_quality", default=_number(_first(earnings, "WEIGHTAVG_ROE")) / 100.0)),
        "moat": _clip(_first(raw, "moat", "moat_quality", default=0.0)),
        "pricing_power": _clip(_first(raw, "pricing_power", default=0.0)),
        "earnings_quality": _clip(_first(raw, "earnings_quality", "cash_flow_quality", default=0.0)),
        "roic": _clip(_number(_first(raw, "roic", default=0.0)) / 100.0),
        "roe": _clip(_number(_first(raw, "roe", default=_first(earnings, "WEIGHTAVG_ROE", default=0.0))) / 100.0),
        "growth": _clip(_number(_first(raw, "growth", "earnings_growth", default=0.0)) / 100.0),
        "management": _clip(_first(raw, "management", "management_quality", default=0.0)),
        "debt_safety": _clip(_first(raw, "debt_safety", default=0.0)),
        "capital_allocation": _clip(_first(raw, "capital_allocation", default=0.0)),
        "valuation": _clip(_first(raw, "valuation", "valuation_quality", "valuation_score", default=0.0)),
    }
    business["score"] = sum(business.values()) / len(business)
    # Accept the source synonym while keeping BUSINESS as the production axis.
    business["financial_quality"] = business["business_quality"]

    future_demand = {
        "market_story": _clip(_first(raw, "market_story_strength", default=0.0)),
        "system_change": _clip(_first(raw, "system_change_strength", default=0.0)),
        "demand_strength": _clip(_first(raw, "demand_strength", "demand_score", default=0.0)),
        "bottleneck_strength": _clip(_first(raw, "bottleneck_strength", "bottleneck_score", default=0.0)),
        "supply_constraint": _clip(_first(raw, "supply_constraint", default=0.0)),
        "demand_visibility": _clip(_first(raw, "demand_visibility", default=0.0)),
        "industry_cycle": str(_first(raw, "industry_cycle", default="UNKNOWN") or "UNKNOWN").upper(),
        "industry_catalyst": _clip(_first(raw, "industry_catalyst", "catalyst_strength", default=0.0)),
        "evidence_strength": _clip(_first(raw, "evidence_strength", default=0.0)),
        "invalidation_condition": list(raw.get("industry_invalidation_conditions") or []),
    }
    demand_numeric = [value for key, value in future_demand.items() if key not in {"industry_cycle", "invalidation_condition"}]
    future_demand["score"] = sum(demand_numeric) / len(demand_numeric) if demand_numeric else 0.0

    capital = {
        "fund_flow": positive_flow,
        "fund_flow_acceleration": _clip(_first(raw, "fund_flow_acceleration", "capital_acceleration", default=0.0)),
        "fund_flow_persistence": _clip(_first(raw, "fund_flow_persistence", "capital_persistence", default=0.0)),
        "fund_flow_percentile": _clip(_first(raw, "fund_flow_percentile", default=amount_pctile if main_flow > 0 else 0.0)),
        "institutional_flow": _clip(_first(raw, "institutional_flow", "institution_confirmation", default=0.0)),
        "hot_money_flow": _clip(_first(raw, "hot_money_flow", "hot_money_confirmation", default=0.0)),
        "lhb_quality": _clip(_first(raw, "lhb_quality", default=0.0)),
        "seat_behavior": _clip(_first(raw, "seat_behavior_score", default=0.0)),
        "order_pressure": _clip(_first(raw, "order_pressure", "order_book_pressure", default=0.0)),
        "volume_accumulation": _clip(_first(raw, "volume_accumulation", "capital_accumulation", default=positive_flow)),
        "price_volume_confirmation": _clip(_first(raw, "price_volume_confirmation", default=price_response)),
        "capital_price_divergence": capital_divergence,
        "capital_price_impact": price_impact,
        "capital_price_impact_state": capital_price_impact_state,
        "distribution_risk": negative_flow,
    }
    capital["accumulation"] = _clip(
        _first(raw, "capital_accumulation", default=positive_flow)
        if raw.get("capital_accumulation") not in (None, "")
        else positive_flow
    )
    capital["main_force_flow"] = _clip(
        0.35 * capital["accumulation"]
        + 0.25 * capital["fund_flow_persistence"]
        + 0.20 * capital["volume_accumulation"]
        + 0.20 * capital["price_volume_confirmation"]
    )
    capital["capital_persistence"] = capital["fund_flow_persistence"]
    capital["capital_acceleration"] = capital["fund_flow_acceleration"]
    lhb_rows = [row for row in raw.get("lhb") or [] if isinstance(row, dict)]
    institution_evidence = sum(
        (
            "机构" in str(row.get("EXPLAIN") or "")
            or row.get("institution") is True
            or row.get("institution_type") is True
        )
        for row in lhb_rows
    ) + sum(
        bool(raw.get(key))
        for key in (
            "institution_position_change",
            "institution_holding_change",
            "institution_flow_evidence",
            "institution_trade_evidence",
        )
    )
    hot_money_evidence = sum(
        bool(
            row.get("hot_money") is True
            or row.get("hot_money_type") is True
            or row.get("游资") is True
        )
        and "机构" not in str(row.get("EXPLAIN") or "")
        for row in lhb_rows
    ) + sum(bool(raw.get(key)) for key in ("hot_money_evidence", "hot_money_trade_evidence"))
    main_force_evidence = sum(
        value is not None
        for value in (main_flow if amount else None, turnover if turnover else None, capital["fund_flow_persistence"] if raw.get("fund_flow_persistence") is not None else None)
    )
    def behavior(direction: str, strength: float, persistence: float, acceleration: float, count: int) -> Dict[str, Any]:
        return {
            "direction": direction,
            "strength": round(_clip(strength), 8),
            "persistence": round(_clip(persistence), 8),
            "acceleration": round(_clip(acceleration), 8),
            "evidence_count": int(count),
            "confidence": round(_clip(min(1.0, count / 2.0) * _mean(strength, persistence)), 8),
            "evidence_status": "OBSERVED" if count else "UNKNOWN",
        }
    capital["institution_behavior"] = behavior(
        "ACCUMULATING" if institution_evidence and capital["institutional_flow"] > 0 else "UNKNOWN",
        capital["institutional_flow"], capital["fund_flow_persistence"], capital["fund_flow_acceleration"], institution_evidence,
    )
    capital["main_force_behavior"] = behavior(
        "ACCELERATING" if main_force_evidence and capital["fund_flow_acceleration"] >= 0.60 else "PRESENT" if main_force_evidence else "UNKNOWN",
        capital["main_force_flow"], capital["fund_flow_persistence"], capital["fund_flow_acceleration"], main_force_evidence,
    )
    capital["hot_money_behavior"] = behavior(
        "PRESENT" if hot_money_evidence and capital["hot_money_flow"] > 0 else "UNKNOWN",
        capital["hot_money_flow"], capital["fund_flow_persistence"], capital["fund_flow_acceleration"], hot_money_evidence,
    )
    capital["accumulation_quality"] = _clip(
        0.30 * capital["fund_flow_persistence"]
        + 0.25 * capital["volume_accumulation"]
        + 0.20 * capital["institutional_flow"]
        + 0.15 * capital["price_volume_confirmation"]
        + 0.10 * capital["fund_flow"]
    )
    if capital["distribution_risk"] >= 0.70:
        capital["accumulation_phase"] = "DISTRIBUTION"
    elif capital["accumulation"] >= 0.60 and capital["fund_flow_acceleration"] >= 0.60:
        capital["accumulation_phase"] = "IGNITION"
    elif capital["accumulation_quality"] >= 0.45:
        capital["accumulation_phase"] = "ACCUMULATION"
    elif capital["accumulation"] > 0:
        capital["accumulation_phase"] = "ORDINARY_TRADING"
    else:
        capital["accumulation_phase"] = "UNOBSERVED"

    supply = {
        "free_float": _clip(_first(raw, "free_float_ratio", "free_float", default=0.0)),
        "turnover": _clip(turnover / 20.0),
        "turnover_velocity": turnover_velocity,
        "historical_volume_nodes": _clip(_first(raw, "historical_volume_nodes", default=0.0)),
        "overhead_supply": _clip(_first(raw, "overhead_supply", default=0.0)),
        "profit_chip_ratio": _clip(_first(raw, "profit_chip_ratio", default=0.0)),
        "trapped_chip_ratio": _clip(_first(raw, "trapped_chip_ratio", default=0.0)),
        "shareholder_reduction": _clip(_first(raw, "shareholder_reduction", default=0.0)),
        "pledge_pressure": _clip(_first(raw, "pledge_pressure", default=0.0)),
        "unlocking_pressure": _clip(_first(raw, "unlocking_pressure", "lockup_pressure", default=0.0)),
        "large_holder_supply": _clip(_first(raw, "large_holder_supply", default=0.0)),
        "recent_distribution": _clip(_first(raw, "recent_distribution", default=0.0)),
        "sell_pressure": _clip(_first(raw, "sell_pressure", default=0.0)),
    }
    if not supply["shareholder_reduction"] and shareholder:
        supply["shareholder_reduction"] = _clip(sum(1 for row in shareholder if isinstance(row, dict) and _number(row.get("change_num")) < 0) / 3.0)
    supply["effective_supply"] = _clip(
        0.35 * supply["overhead_supply"]
        + 0.20 * supply["trapped_chip_ratio"]
        + 0.15 * supply["shareholder_reduction"]
        + 0.10 * supply["pledge_pressure"]
        + 0.10 * supply["unlocking_pressure"]
        + 0.10 * supply["sell_pressure"]
    )
    supply["supply_evidence_count"] = sum(
        raw.get(key) not in (None, "", "-")
        for key in (
            "overhead_supply",
            "trapped_chip_ratio",
            "shareholder_reduction",
            "pledge_pressure",
            "unlocking_pressure",
            "large_holder_supply",
            "recent_distribution",
            "sell_pressure",
        )
    ) + sum(
        isinstance(row, dict)
        and any(row.get(key) not in (None, "", "-") for key in ("change_num", "change_ratio", "direction"))
        for row in shareholder
    )
    absorption_components = {
        "funds": (
            capital["accumulation"] > 0
            and capital["fund_flow_persistence"] > 0
            and main_flow > 0
        ),
        "turnover": supply["turnover"] > 0,
        "price_response": capital["price_volume_confirmation"] > 0,
        "supply": supply["supply_evidence_count"] > 0,
        "stability": close_position >= 0.45,
        "continuation": capital["fund_flow_acceleration"] > 0,
    }
    supply["absorption_evidence_count"] = sum(absorption_components.values())
    supply["absorption_confidence"] = round(
        _clip(supply["absorption_evidence_count"] / len(absorption_components)), 8
    )
    supply_support = _mean(
        capital["accumulation"],
        capital["fund_flow_persistence"],
        capital["fund_flow_acceleration"],
        capital["price_volume_confirmation"],
        supply["turnover_velocity"],
        close_position,
    )
    supply_pressure = _mean(
        supply["effective_supply"],
        supply["sell_pressure"],
        supply["overhead_supply"],
        supply["recent_distribution"],
    )
    supply["supply_absorption"] = round(
        _clip(supply_support - 0.60 * supply_pressure)
        if (
            amount > 0
            and supply["absorption_evidence_count"] >= 4
            and all(absorption_components[key] for key in ("funds", "turnover", "price_response", "supply"))
        )
        else 0.0,
        8,
    )
    supply["unlock_pressure"] = supply["unlocking_pressure"]
    supply["supply_pressure"] = supply["effective_supply"]
    supply["distribution_pressure"] = supply["sell_pressure"]
    supply["supply_absorption_state"] = (
        "UNKNOWN"
        if (
            amount <= 0
            or supply["absorption_evidence_count"] < 4
            or not all(absorption_components[key] for key in ("funds", "turnover", "price_response", "supply"))
        )
        else "RELEASING" if supply_pressure > supply_support + 0.15
        else "ABSORPTION" if supply["supply_absorption"] >= 0.35
        else "BALANCED"
    )
    supply["score"] = supply["supply_absorption"]
    if capital_price_impact_state != "UNKNOWN":
        if main_flow > 0 and pct_change > 0 and supply["supply_absorption_state"] == "ABSORPTION":
            capital_price_impact_state = "DEMAND_CONFIRMATION"
        elif main_flow > 0 and supply["supply_absorption_state"] == "ABSORPTION":
            capital_price_impact_state = "SUPPLY_ABSORPTION"
        elif main_flow > 0 and pct_change < 0:
            capital_price_impact_state = "DISTRIBUTION_RISK"
        elif main_flow < 0 and pct_change > 0:
            capital_price_impact_state = "PRICE_SUPPORTED_DIVERGENCE"
        capital["capital_price_impact_state"] = capital_price_impact_state

    pricing_gap = {
        "fundamental_gap": _clip(_first(raw, "fundamental_gap", default=max(0.0, business["score"] - _clip(_first(raw, "valuation", "valuation_quality", default=0.0))))),
        "industry_gap": _clip(_first(raw, "industry_gap", default=future_demand["score"] * 0.5)),
        "capital_gap": _clip(_first(raw, "capital_gap", default=capital["accumulation"] * (1.0 - price_response))),
        "earnings_gap": _clip(_first(raw, "earnings_gap", default=business["growth"] * (1.0 - price_response))),
        "demand_gap": _clip(_first(raw, "demand_gap", default=future_demand["demand_strength"] * (1.0 - price_response))),
        "attention_gap": _clip(_first(raw, "attention_gap", default=max(0.0, future_demand["score"] - attention))),
        "institutional_positioning": _clip(_first(raw, "institutional_positioning", default=capital["institutional_flow"])),
        "institutional_gap": _clip(1.0 - _clip(_first(raw, "institutional_positioning", default=capital["institutional_flow"]))),
        "price_reflection": _clip(_first(raw, "price_reflection", default=price_response)),
    }
    pricing_gap["score"] = _clip(
        0.20 * pricing_gap["fundamental_gap"]
        + 0.15 * pricing_gap["industry_gap"]
        + 0.15 * pricing_gap["earnings_gap"]
        + 0.15 * pricing_gap["demand_gap"]
        + 0.15 * pricing_gap["capital_gap"]
        + 0.10 * pricing_gap["attention_gap"]
        + 0.10 * pricing_gap["institutional_gap"]
    )
    pricing_gap["low_price"] = price > 0 and price <= _number(_first(raw, "low_price_threshold", default=0.0)) if raw.get("low_price_threshold") not in (None, "") else False
    pricing_gap["real_pricing_gap"] = pricing_gap["score"]

    market = {
        "regime": str(_first(raw, "market_regime", default="UNKNOWN") or "UNKNOWN").upper(),
        "breadth": breadth,
        "sector_breadth": sector_breadth,
        "leader_strength": _clip(_first(raw, "leader_strength", "leader_position", default=0.0)),
        "price_strength": price_strength,
        "attention": attention,
        "follow_through": _clip(_first(raw, "market_follow_through_score", "follow_through", default=0.0)),
        "revaluation": _clip(_first(raw, "revaluation_probability", default=0.0)),
    }
    market["stage"] = _market_stage(raw, capital, supply, market)
    market["alignment"] = _clip(_first(raw, "market_alignment", default=sector_breadth))
    market["score"] = _clip(0.35 * market["breadth"] + 0.25 * market["sector_breadth"] + 0.20 * market["follow_through"] + 0.20 * market["price_strength"])

    reflexivity = {
        "price_strength": market["price_strength"],
        "sector_breadth": market["sector_breadth"],
        "leader_strength": market["leader_strength"],
        "attention_growth": _clip(_first(raw, "attention_growth", default=0.0)),
        "capital_acceleration": capital["fund_flow_acceleration"],
        "market_regime": market["regime"],
        "crowding": _clip(_first(raw, "crowding_risk", "crowding", default=max(market["attention"] - 0.75, 0.0))),
        "buyer_exhaustion": bool(raw.get("buyer_exhaustion")),
    }
    reflexivity["score"] = _clip(sum(reflexivity[key] for key in ("price_strength", "sector_breadth", "leader_strength", "attention_growth", "capital_acceleration")) / 5.0)
    reflexivity["break"] = _clip(
        _first(raw, "reflexivity_break", default=0.0)
        if raw.get("reflexivity_break") not in (None, "")
        else max(capital["distribution_risk"], market["attention"] - 0.75, 0.0)
    )
    reflexivity["reflexivity_strength"] = reflexivity["score"]
    reflexivity["reflexivity_break_risk"] = reflexivity["break"]

    risk = {
        "halted": bool(raw.get("halted") or raw.get("is_suspended")),
        "regulatory_hard_risk": bool(raw.get("regulatory_hard_block") or raw.get("risk_hard_block")),
        "liquidity": _clip(_first(raw, "liquidity_score", default=1.0 if amount > 0 else 0.0)),
        "downside": _clip(_first(raw, "downside_risk", default=0.0)),
        "event_risk": _clip(_first(raw, "event_risk", "risk_notice_penalty", default=0.0)),
        "thesis_invalidated": bool(raw.get("thesis_invalidated")),
    }
    risk["score"] = _clip(1.0 - (0.35 * risk["downside"] + 0.25 * risk["event_risk"] + 0.20 * float(risk["regulatory_hard_risk"]) + 0.20 * float(risk["halted"])))

    execution = {
        "entry_price": price,
        "buyable": raw.get("buyable"),
        "execution_quality": _clip(_first(raw, "execution_quality", default=1.0 if price > 0 and amount > 0 else 0.0)),
        "short_term_overheat": _clip(_first(raw, "short_term_overheat", default=max(price_response, market["attention"]))),
        "gap_risk": _clip(_first(raw, "gap_risk", "next_day_risk", default=0.0)),
        "close_position": close_position,
        "slippage": _clip(_first(raw, "slippage", "slippage_rate", default=0.0), high=0.02) / 0.02,
        "spread": _clip(_first(raw, "spread", "spread_rate", default=0.0), high=0.02) / 0.02,
        "market_impact": _clip(_first(raw, "market_impact", "market_impact_rate", default=0.0), high=0.02) / 0.02,
    }
    execution["cost_rate"] = _clip(_first(raw, "execution_cost_rate", default=0.003), high=0.02)
    execution["execution_feasibility"] = _clip(
        execution["execution_quality"]
        * (1.0 - execution["gap_risk"])
        * (1.0 - 0.50 * execution["slippage"] - 0.25 * execution["spread"] - 0.25 * execution["market_impact"])
    )
    execution["score"] = execution["execution_feasibility"]

    result = {
        "version": "price_formation_measurements_v1",
        "lineage_id": snap["lineage_id"],
        "available_at": snap.get("as_of") or snap.get("source_time") or "",
        "source": snap.get("source") or "unknown",
        "snapshot": snap,
        "feature_families": list(FEATURE_GROUPS),
        "BUSINESS": business,
        "FUTURE_DEMAND": future_demand,
        "CAPITAL": capital,
        "SUPPLY": supply,
        "PRICING_GAP": pricing_gap,
        "REFLEXIVITY": reflexivity,
        "MARKET": market,
        "RISK": risk,
        "EXECUTION": execution,
    }
    for family in FEATURE_GROUPS:
        result[family].update({
            "lineage_id": snap["lineage_id"],
            "source": snap.get("source") or "unknown",
            "available_at": snap.get("as_of") or snap.get("source_time") or "",
        })
    # Lowercase aliases preserve read compatibility while the uppercase keys are canonical.
    result.update({key.lower(): result[key] for key in FEATURE_GROUPS})
    # Compatibility aliases are read-only views for existing callers. The
    # canonical production contract remains the uppercase family keys above.
    result.update({
        "company": result["BUSINESS"],
        "industry": result["FUTURE_DEMAND"],
        "position": {
            "close_position": close_position,
            "relative_strength": price_response,
        },
        "catalyst": {"strength": future_demand["industry_catalyst"]},
        "supply_pressure": supply["effective_supply"],
        "capital_price_impact": capital["capital_price_impact"],
        "capital_price_impact_state": capital["capital_price_impact_state"],
    })
    return result
