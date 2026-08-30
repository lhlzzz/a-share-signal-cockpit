"""Context-only research boundaries for demand, business, capital, and contradiction."""
from __future__ import annotations

from typing import Any, Dict


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _context(kind: str, values: Dict[str, Any], lineage_id: str, provider: str) -> Dict[str, Any]:
    return {
        "context_type": kind,
        "status": "RESEARCH_ONLY",
        "provider": provider,
        "lineage_id": lineage_id,
        "provenance": {"provider": provider, "lineage_id": lineage_id, "as_of": values.pop("as_of", "")},
        **values,
    }

def build_serenity_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    demand = features["FUTURE_DEMAND"]
    return _context("FutureDemandContext", {
        "as_of": features.get("available_at", ""),
        "market_story": demand["market_story"],
        "system_change": demand["system_change"],
        "industry": snapshot.get("sector", ""),
        "required_components": list(snapshot.get("raw", {}).get("required_components") or []),
        "bottleneck": demand["bottleneck_strength"],
        "supply_constraint": demand["supply_constraint"],
        "demand": demand["demand_strength"],
        "demand_visibility": demand["demand_visibility"],
        "industry_cycle": demand["industry_cycle"],
        "industry_catalyst": demand["industry_catalyst"],
        "evidence_strength": demand["evidence_strength"],
        "invalidation": list(demand["invalidation_condition"]),
        "reports": list(snapshot.get("raw", {}).get("industry_reports") or []),
    }, features["lineage_id"], "Serenity")


def build_buffett_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    business = features["BUSINESS"]
    return _context("CompanyContext", {
        "as_of": features.get("available_at", ""),
        "business_quality": business["score"],
        "ability_circle": snapshot.get("raw", {}).get("ability_circle", "UNKNOWN"),
        "moat": business["moat"],
        "pricing_power": business["pricing_power"],
        "earnings_quality": business["earnings_quality"],
        "cash_flow": _number(snapshot.get("raw", {}).get("cash_flow_quality")),
        "roic": business["roic"],
        "roe": business["roe"],
        "growth": business["growth"],
        "management": business["management"],
        "debt_safety": business["debt_safety"],
        "capital_allocation": business["capital_allocation"],
        "valuation": business["valuation"],
        "margin_of_safety": _number(snapshot.get("raw", {}).get("margin_of_safety")),
        "reports": list(snapshot.get("raw", {}).get("stock_reports") or []),
        "earnings_preview": dict(snapshot.get("raw", {}).get("earnings_preview") or {}),
    }, features["lineage_id"], "Buffett")


def build_uzi_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    capital = features["CAPITAL"]
    raw = snapshot.get("raw", {})
    lhb_rows = list(raw.get("lhb") or [])
    institution_signal = any(
        "机构" in str(row.get("EXPLAIN") or "")
        for row in lhb_rows
        if isinstance(row, dict)
    )
    return _context("CapitalContext", {
        "as_of": features.get("available_at", ""),
        "institution_vs_hot_money": raw.get(
            "institution_vs_hot_money",
            "institution" if institution_signal else "UNKNOWN",
        ),
        "fund_flow": capital["fund_flow"],
        "fund_flow_acceleration": capital["fund_flow_acceleration"],
        "fund_flow_persistence": capital["fund_flow_persistence"],
        "capital_persistence": capital["capital_persistence"],
        "capital_acceleration": capital["capital_acceleration"],
        "main_force_flow": capital["main_force_flow"],
        "institutional_flow": capital["institutional_flow"],
        "hot_money_flow": capital["hot_money_flow"],
        "lhb_quality": capital["lhb_quality"],
        "seat_behavior": raw.get("seat_behavior", "UNKNOWN"),
        "accumulation": capital["accumulation"],
        "capital_flow_ratio": capital.get("capital_flow_ratio"),
        "distribution": capital["distribution_risk"],
        "capital_price_impact": capital["capital_price_impact"],
        "capital_divergence": capital["capital_price_divergence"],
        "lhb_events": lhb_rows,
        "institution_behavior": capital.get("institution_behavior") or {},
        "main_force_behavior": capital.get("main_force_behavior") or {},
        "hot_money_behavior": capital.get("hot_money_behavior") or {},
        "capital_flow_observation": capital.get("capital_flow_observation") or [],
        "capital_flow_state": capital.get("capital_flow_state") or "UNKNOWN",
        "observation": {
            "main_net_inflow": capital.get("fund_flow"),
            "lhb": lhb_rows,
            "capital_flow": capital.get("capital_flow_observation") or [],
        },
        "interpretation": {
            "institution": (capital.get("institution_behavior") or {}).get("direction") or "UNKNOWN",
            "main_force": (capital.get("main_force_behavior") or {}).get("direction") or "UNKNOWN",
            "hot_money": (capital.get("hot_money_behavior") or {}).get("direction") or "UNKNOWN",
        },
    }, features["lineage_id"], "UZI")


def build_supply_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    return _context("SupplyContext", {
        "as_of": features.get("available_at", ""),
        **features["SUPPLY"],
        "source_rows": {
            "shareholder_changes": list(snapshot.get("raw", {}).get("shareholder_changes") or []),
            "lockup": list(snapshot.get("raw", {}).get("lockup_expiry") or []),
        },
    }, features["lineage_id"], "XiaoguFeatureEngine")


def build_pricing_gap_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    return _context("PricingGapContext", {
        "as_of": features.get("available_at", ""),
        **features["PRICING_GAP"],
        "price": snapshot.get("price"),
        "attention": features["REFLEXIVITY"].get("attention_growth"),
    }, features["lineage_id"], "XiaoguFeatureEngine")


def build_future_buyer_map(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    raw = snapshot.get("raw", {})
    capital = features["CAPITAL"]
    # Future buyers are never inferred from current flow, demand, attention,
    # breadth, or supply. Only an explicitly supplied same-day evidence row
    # may enter this list.
    buyers = []
    for source in raw.get("future_buyers") or []:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        status = str(item.get("evidence_status") or item.get("status") or "UNKNOWN").upper()
        if status not in {"OBSERVED", "EVIDENCE_BACKED"}:
            status = "UNKNOWN"
        evidence = item.get("evidence")
        if not evidence or not item.get("source") or not item.get("observed_at"):
            status = "UNKNOWN"
        item["evidence_status"] = status
        item["capacity"] = _number(item.get("capacity")) if status != "UNKNOWN" else None
        buyers.append(item)
    current_buyer = raw.get("current_buyer")
    if not current_buyer:
        current_buyer = [
            name for name, behavior in (
                ("institution", capital.get("institution_behavior", {})),
                ("main_force", capital.get("main_force_behavior", {})),
                ("hot_money", capital.get("hot_money_behavior", {})),
            )
            if behavior.get("evidence_count", 0) > 0
        ] or ["UNKNOWN"]
    elif isinstance(current_buyer, str):
        current_buyer = [current_buyer]
    next_buyers = [
        item for item in buyers
        if item.get("capacity") is not None and float(item["capacity"]) > 0.50
        and item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
    ]
    observed_buyers = [item for item in buyers if item.get("evidence_status") == "OBSERVED"]
    evidence_map = {
        category: next(
            (item.get("evidence_status") for item in buyers if item.get("buyer") == category),
            "UNKNOWN",
        )
        for category in ("institutions", "mutual_funds", "ETF/index", "quant", "hot_money", "retail", "industry_capital")
    }
    observed_values = [item.get("capacity") for item in observed_buyers if item.get("capacity") is not None]
    observed_capacity = max(observed_values) if observed_values else None
    return _context("FutureBuyerMap", {
        "as_of": features.get("available_at", ""),
        "buyer_categories": ["institutions", "mutual_funds", "ETF/index", "quant", "hot_money", "retail", "industry_capital"],
        "buyer_evidence": evidence_map,
        "observed_buyers": observed_buyers,
        "current_buyer": current_buyer,
        "next_buyer": next_buyers,
        "potential_next_buyer": buyers,
        "buyer_capacity": observed_capacity,
        "observed_buyer_capacity": observed_capacity,
        # UNKNOWN buyers remain visible to research but cannot add alpha.
        "future_buyer_capacity": (
            max(values) if (values := [
                item.get("capacity") for item in buyers
                if item.get("evidence_status") in {"OBSERVED", "EVIDENCE_BACKED"}
                and item.get("capacity") is not None
            ]) else None
        ),
        "buyer_trigger": [item.get("trigger", "") for item in buyers if isinstance(item, dict)],
    }, features["lineage_id"], "XiaoguFeatureEngine")


def build_contradiction_context(
    industry: Dict[str, Any], company: Dict[str, Any], capital: Dict[str, Any], lineage_id: str
) -> Dict[str, Any]:
    from integrations.contradiction_adapter import integrate_research_context
    return integrate_research_context(industry, company, capital, lineage_id=lineage_id)


def build_integrated_research_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    industry = build_serenity_context(snapshot, features)
    company = build_buffett_context(snapshot, features)
    capital = build_uzi_context(snapshot, features)
    supply = build_supply_context(snapshot, features)
    pricing_gap = build_pricing_gap_context(snapshot, features)
    future_buyer_map = build_future_buyer_map(snapshot, features)
    integrated = build_contradiction_context(industry, company, capital, features["lineage_id"])
    raw_tradingagents = snapshot.get("raw", {}).get("tradingagents")
    if isinstance(raw_tradingagents, dict):
        for key in ("bull_thesis", "bear_thesis", "strongest_counterargument", "missing_evidence", "thesis_invalidation", "contradiction_status", "veto", "key_conflicts"):
            if key in raw_tradingagents:
                integrated[key] = raw_tradingagents[key]
    return {
        "context_type": "ResearchContext",
        "status": "RESEARCH_ONLY",
        "lineage_id": features["lineage_id"],
        "as_of": features.get("available_at", ""),
        "industry": industry,
        "company": company,
        "capital": capital,
        "supply": supply,
        "pricing_gap": pricing_gap,
        "future_buyer_map": future_buyer_map,
        "integrated": integrated,
        "contradiction": integrated,
    }
