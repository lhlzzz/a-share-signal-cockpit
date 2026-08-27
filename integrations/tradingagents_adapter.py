"""Pinned TradingAgents boundary. It returns analysis context, never decisions."""
from __future__ import annotations

from typing import Any, Dict

TRADINGAGENTS_COMMIT = "a33fd4c0f134485a43553a2c23a63cb14adbd88f"


def integrate_research_context(
    industry: Dict[str, Any],
    company: Dict[str, Any],
    capital: Dict[str, Any],
    *,
    lineage_id: str,
) -> Dict[str, Any]:
    demand = float(industry.get("demand") or 0)
    risk = float(capital.get("distribution") or 0)
    return {
        "context_type": "IntegratedResearchContext",
        "status": "RESEARCH_ONLY",
        "provider": "TradingAgents",
        "provider_commit": TRADINGAGENTS_COMMIT,
        "lineage_id": lineage_id,
        "bull_case": industry.get("catalyst") or "",
        "bear_case": "capital distribution risk" if risk else "",
        "strongest_catalyst": industry.get("catalyst") or "",
        "strongest_risk": "capital distribution" if risk else "",
        "strongest_counterargument": "capital distribution risk" if risk else "insufficient demand evidence",
        "missing_evidence": list(industry.get("invalidation") or []),
        "thesis_invalidation": list(industry.get("invalidation") or []),
        "contradiction_status": "BEARISH" if risk and demand >= 0.50 else "UNRESOLVED",
        "veto": bool(risk >= 0.80 and demand < 0.50),
        "key_conflicts": ["demand_vs_distribution"] if demand and risk else [],
        "confidence": max(0.0, min(1.0, (demand + float(company.get("business_quality") or 0)) / 2)),
        "invalidation": industry.get("invalidation") or [],
    }
