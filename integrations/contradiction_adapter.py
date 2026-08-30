"""Contradiction / bear-case context only. It never emits BUY, SELL, RANK, or PICK."""
from __future__ import annotations

from typing import Any, Dict

CONTRADICTION_ADAPTER_COMMIT = "a33fd4c0f134485a43553a2c23a63cb14adbd88f"


def integrate_research_context(
    industry: Dict[str, Any],
    company: Dict[str, Any],
    capital: Dict[str, Any],
    *,
    lineage_id: str,
) -> Dict[str, Any]:
    def _num(value):
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    demand = _num(industry.get("demand"))
    risk = _num(capital.get("distribution"))
    quality = _num(company.get("business_quality"))
    return {
        "context_type": "IntegratedResearchContext",
        "status": "RESEARCH_ONLY",
        "provider": "Contradiction",
        "provider_commit": CONTRADICTION_ADAPTER_COMMIT,
        "lineage_id": lineage_id,
        "bull_case": industry.get("catalyst") or "",
        "bear_case": "capital distribution risk" if risk is not None and risk > 0 else "",
        "strongest_catalyst": industry.get("catalyst") or "",
        "strongest_risk": "capital distribution" if risk is not None and risk > 0 else "",
        "strongest_counterargument": "capital distribution risk" if risk is not None and risk > 0 else "insufficient demand evidence",
        "missing_evidence": list(industry.get("invalidation") or []),
        "thesis_invalidation": list(industry.get("invalidation") or []),
        "contradiction_status": "BEARISH" if risk is not None and risk > 0 and demand is not None and demand >= 0.50 else "UNRESOLVED",
        "veto": bool(risk is not None and risk >= 0.80 and demand is not None and demand < 0.50),
        "key_conflicts": ["demand_vs_distribution"] if demand not in (None, 0) and risk not in (None, 0) else [],
        "confidence": None if demand is None and quality is None else max(
            0.0,
            min(
                1.0,
                sum(value for value in (demand, quality) if value is not None)
                / (2 if demand is not None and quality is not None else 1),
            ),
        ),
        "invalidation": industry.get("invalidation") or [],
    }
