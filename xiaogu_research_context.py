"""Context-only research boundaries for demand, business, capital, and contradiction."""
from __future__ import annotations

from datetime import datetime
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


def _as_of_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _visible_before(as_of: str, observed: Any) -> bool:
    stamp = str(observed or "").strip()
    if not as_of or not stamp:
        return False
    return stamp <= as_of


def _json_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _provider_record(
    provider: str,
    *,
    role: str,
    requested: bool = True,
    available: bool = False,
    succeeded: bool = False,
    failed: bool = False,
    evidence_count: int = 0,
    usable_evidence_count: int | None = None,
    pit_valid: bool | None = None,
    used_downstream: bool = False,
    knowledge_available_at: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    count = int(evidence_count or 0)
    usable = count if usable_evidence_count is None else int(usable_evidence_count or 0)
    return {
        "provider": provider,
        "role": role,
        "provider_requested": requested,
        "provider_available": available,
        "provider_succeeded": succeeded,
        "provider_failed": failed,
        "evidence_count": count,
        "usable_evidence_count": usable,
        "pit_valid": pit_valid,
        "used_downstream": used_downstream,
        "knowledge_available_at": knowledge_available_at or "",
        "reason": reason or "",
        "invoked": requested,
    }


def _run_provider(fn, retries: int = 1):
    last_exc: BaseException | None = None
    for _ in range(max(0, retries) + 1):
        try:
            return fn(), None
        except Exception as exc:
            last_exc = exc
    return None, last_exc


def _knowledge_stamp(*values: Any) -> str:
    for value in values:
        stamp = str(value or "").strip()
        if stamp:
            return stamp
    return ""


def fetch_historical_research_cases(symbol: str, as_of: str) -> Dict[str, Any]:
    """PIT historical paper/outcome cases. Evidence only; never a BUY source."""
    symbol = str(symbol or "").zfill(6)[-6:]
    cases: list[Dict[str, Any]] = []

    def _load() -> list[Dict[str, Any]]:
        from xiaogu_db import engine
        from sqlalchemy import text
        loaded: list[Dict[str, Any]] = []
        with engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT
                        p.paper_signal_id,
                        p.decision_id,
                        p.symbol,
                        p.signal_time,
                        p.payload AS paper_payload,
                        r.payload AS outcome_payload
                    FROM paper_observations p
                    LEFT JOIN returns r ON r.decision_id = p.decision_id
                    WHERE p.symbol = :symbol
                    ORDER BY p.signal_time DESC
                    LIMIT 20
                    """
                ),
                {"symbol": symbol},
            ).mappings()
            for row in rows:
                paper = _json_payload(row.get("paper_payload"))
                outcome = _json_payload(row.get("outcome_payload"))
                event_time = _knowledge_stamp(paper.get("event_time"), row.get("signal_time"))
                knowledge_available_at = _knowledge_stamp(
                    paper.get("knowledge_available_at"),
                    paper.get("available_at"),
                )
                if not knowledge_available_at or not _visible_before(as_of, knowledge_available_at):
                    continue
                settled_at = _knowledge_stamp(
                    outcome.get("outcome_settled_at"),
                    outcome.get("settled_at"),
                    outcome.get("outcome_available_at"),
                    outcome.get("result_filled_at"),
                )
                outcome_visible = bool(settled_at) and _visible_before(as_of, settled_at)
                review = outcome.get("post_trade_review") if isinstance(outcome.get("post_trade_review"), dict) else {}
                opportunity = None
                failure_pattern = None
                first_profit_day = None
                max_mae_5d = None
                if outcome_visible:
                    opportunity = outcome.get("opportunity_5d")
                    if opportunity is None:
                        opportunity = outcome.get("profit_window")
                    first_profit_day = outcome.get("first_profit_day")
                    max_mae_5d = outcome.get("max_mae_5d")
                    failure_pattern = review.get("attribution") if opportunity is False else None
                loaded.append({
                    "paper_signal_id": row.get("paper_signal_id"),
                    "decision_id": row.get("decision_id"),
                    "symbol": row.get("symbol"),
                    "event_time": event_time,
                    "signal_time": event_time,
                    "available_at": knowledge_available_at,
                    "knowledge_available_at": knowledge_available_at,
                    "settled_at": settled_at if outcome_visible else None,
                    "outcome_settled_at": settled_at if outcome_visible else None,
                    "signal_reason": paper.get("signal_reason"),
                    "price_strength": paper.get("price_strength"),
                    "rank": paper.get("rank"),
                    "top1_flag": paper.get("top1_flag"),
                    "top3_flag": paper.get("top3_flag"),
                    "selection_reason": paper.get("selection_reason"),
                    "opportunity_5d": opportunity,
                    "profit_window": opportunity,
                    "first_profit_day": first_profit_day,
                    "max_mae_5d": max_mae_5d,
                    "failure_pattern": failure_pattern,
                    "post_trade_review": review if outcome_visible else None,
                    "source": "postgresql.paper_observations",
                })
        return loaded

    payload, error = _run_provider(_load)
    if error is not None:
        return {
            "status": "UNAVAILABLE",
            "reason": type(error).__name__,
            "historical_cases": [],
            "historical_success_rate": None,
            "historical_failure_patterns": [],
            "case_count": 0,
            "provider_available": False,
            "provider_succeeded": False,
        }
    cases = payload or []
    visible = cases[:8]
    settled = [item for item in visible if item.get("opportunity_5d") is not None]
    success_rate = (
        sum(1 for item in settled if item.get("opportunity_5d") is True) / len(settled)
        if settled else None
    )
    failure_patterns = sorted({
        str(item.get("failure_pattern"))
        for item in settled
        if item.get("opportunity_5d") is False and item.get("failure_pattern")
    })
    return {
        "status": "RESEARCH_ONLY",
        "historical_cases": visible,
        "historical_success_rate": success_rate,
        "historical_failure_patterns": failure_patterns,
        "case_count": len(visible),
        "provider_available": True,
        "provider_succeeded": True,
        "pit_valid": True,
    }


def fetch_memory_research_notes(symbol: str, as_of: str) -> Dict[str, Any]:
    """Read historical ticket reasons through the Memory Adapter. Never selects Top1."""
    def _load():
        from xiaogu_forward_paper_recorder_v0_1 import read_memory_notes
        return read_memory_notes(symbol=symbol, as_of=as_of, limit=8)

    notes, error = _run_provider(_load)
    if error is not None:
        return {
            "status": "UNAVAILABLE",
            "reason": type(error).__name__,
            "notes": [],
            "connected": False,
            "provider_available": False,
            "provider_succeeded": False,
            "note_count": 0,
        }
    if notes is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "OBSIDIAN_BRIDGE_UNAVAILABLE",
            "notes": [],
            "connected": False,
            "provider_available": False,
            "provider_succeeded": False,
            "note_count": 0,
        }
    visible = []
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        knowledge_available_at = _knowledge_stamp(
            note.get("knowledge_available_at"),
            note.get("available_at"),
        )
        if not knowledge_available_at or not _visible_before(as_of, knowledge_available_at):
            continue
        outcome_available_at = _knowledge_stamp(
            note.get("outcome_available_at"),
            note.get("outcome_update_at"),
            note.get("settled_at"),
        )
        outcome_visible = bool(outcome_available_at) and _visible_before(as_of, outcome_available_at)
        item = {
            "source": "obsidian_memory_adapter",
            "path": note.get("path"),
            "decision_id": note.get("decision_id"),
            "paper_signal_id": note.get("paper_signal_id"),
            "production_run_id": note.get("production_run_id"),
            "memory_id": note.get("memory_id"),
            "symbol": note.get("symbol") or symbol,
            "date": note.get("date"),
            "event_time": note.get("event_time") or note.get("signal_time"),
            "available_at": knowledge_available_at,
            "knowledge_available_at": knowledge_available_at,
            "knowledge_type": note.get("knowledge_type") or "DECISION",
            "reason": note.get("reason") or note.get("decision_reason"),
        }
        if outcome_visible:
            item["knowledge_type"] = note.get("knowledge_type") or "OUTCOME"
            item["settled_at"] = outcome_available_at
            item["outcome_available_at"] = outcome_available_at
            item["outcome"] = note.get("outcome")
            item["review"] = note.get("review") or note.get("post_trade_review")
            item["attribution"] = note.get("attribution")
        visible.append(item)
    return {
        "status": "OK",
        "connected": True,
        "notes": visible,
        "note_count": len(visible),
        "provider_available": True,
        "provider_succeeded": True,
        "pit_valid": True,
    }


def _context_evidence_count(payload: Any) -> int:
    if not isinstance(payload, dict) or not payload:
        return 0
    count = 0
    for value in payload.values():
        if isinstance(value, list):
            count += sum(1 for item in value if item not in (None, "", {}, []))
        elif value not in (None, "", {}, []):
            count += 1
    return count


def build_integrated_research_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    def _safe_context(builder, fallback_kind: str, provider: str) -> tuple[Dict[str, Any], BaseException | None]:
        payload, error = _run_provider(lambda: builder(snapshot, features))
        if error is None and isinstance(payload, dict):
            return payload, None
        return _context(fallback_kind, {
            "as_of": features.get("available_at", ""),
            "status": "UNAVAILABLE",
            "degraded": True,
        }, features["lineage_id"], provider), error

    industry, serenity_error = _safe_context(build_serenity_context, "FutureDemandContext", "Serenity")
    company, buffett_error = _safe_context(build_buffett_context, "CompanyContext", "Buffett")
    capital, uzi_error = _safe_context(build_uzi_context, "CapitalContext", "UZI")
    supply, _supply_error = _safe_context(build_supply_context, "SupplyContext", "XiaoguFeatureEngine")
    pricing_gap, _gap_error = _safe_context(build_pricing_gap_context, "PricingGapContext", "XiaoguFeatureEngine")
    future_buyer_map, _buyer_error = _safe_context(build_future_buyer_map, "FutureBuyerMap", "XiaoguFeatureEngine")
    integrated, contradiction_error = _run_provider(
        lambda: build_contradiction_context(industry, company, capital, features["lineage_id"])
    )
    if contradiction_error is not None or not isinstance(integrated, dict):
        integrated = {
            "context_type": "IntegratedResearchContext",
            "status": "UNAVAILABLE",
            "provider": "Contradiction",
            "lineage_id": features["lineage_id"],
            "degraded": True,
            "contradiction_status": "UNKNOWN",
            "veto": False,
        }
    raw_tradingagents = snapshot.get("raw", {}).get("tradingagents")
    if isinstance(raw_tradingagents, dict):
        for key in ("bull_thesis", "bear_thesis", "strongest_counterargument", "missing_evidence", "thesis_invalidation", "contradiction_status", "veto", "key_conflicts"):
            if key in raw_tradingagents:
                integrated[key] = raw_tradingagents[key]
    as_of = _as_of_text(features.get("available_at") or snapshot.get("source_time") or "")
    historical, historical_error = _run_provider(
        lambda: fetch_historical_research_cases(str(snapshot.get("symbol") or ""), as_of)
    )
    if historical_error is not None or not isinstance(historical, dict):
        historical = {
            "status": "UNAVAILABLE",
            "reason": type(historical_error).__name__ if historical_error else "UNAVAILABLE",
            "historical_cases": [],
            "historical_success_rate": None,
            "historical_failure_patterns": [],
            "case_count": 0,
            "provider_available": False,
            "provider_succeeded": False,
        }
    memory, memory_error = _run_provider(
        lambda: fetch_memory_research_notes(str(snapshot.get("symbol") or ""), as_of)
    )
    if memory_error is not None or not isinstance(memory, dict):
        memory = {
            "status": "UNAVAILABLE",
            "reason": type(memory_error).__name__ if memory_error else "UNAVAILABLE",
            "notes": [],
            "connected": False,
            "provider_available": False,
            "provider_succeeded": False,
            "note_count": 0,
        }
    providers = {
        "Serenity": _provider_record(
            "Serenity",
            role="evidence",
            available=serenity_error is None,
            succeeded=serenity_error is None and not industry.get("degraded"),
            failed=serenity_error is not None or bool(industry.get("degraded")),
            evidence_count=_context_evidence_count(industry),
            pit_valid=True if serenity_error is None else None,
            used_downstream=serenity_error is None and not industry.get("degraded"),
            knowledge_available_at=as_of,
            reason="" if serenity_error is None else type(serenity_error).__name__,
        ),
        "Buffett": _provider_record(
            "Buffett",
            role="evidence",
            available=buffett_error is None,
            succeeded=buffett_error is None and not company.get("degraded"),
            failed=buffett_error is not None or bool(company.get("degraded")),
            evidence_count=_context_evidence_count(company),
            pit_valid=True if buffett_error is None else None,
            used_downstream=buffett_error is None and not company.get("degraded"),
            knowledge_available_at=as_of,
            reason="" if buffett_error is None else type(buffett_error).__name__,
        ),
        "UZI": _provider_record(
            "UZI",
            role="evidence",
            available=uzi_error is None,
            succeeded=uzi_error is None and not capital.get("degraded"),
            failed=uzi_error is not None or bool(capital.get("degraded")),
            evidence_count=_context_evidence_count(capital),
            pit_valid=True if uzi_error is None else None,
            used_downstream=uzi_error is None and not capital.get("degraded"),
            knowledge_available_at=as_of,
            reason="" if uzi_error is None else type(uzi_error).__name__,
        ),
        "Contradiction": _provider_record(
            "Contradiction",
            role="contradiction",
            available=contradiction_error is None,
            succeeded=contradiction_error is None and not integrated.get("degraded"),
            failed=contradiction_error is not None or bool(integrated.get("degraded")),
            evidence_count=_context_evidence_count(integrated),
            pit_valid=True if contradiction_error is None else None,
            used_downstream=contradiction_error is None and not integrated.get("degraded"),
            knowledge_available_at=as_of,
            reason="" if contradiction_error is None else type(contradiction_error).__name__,
        ),
        "postgresql.paper_observations": _provider_record(
            "postgresql.paper_observations",
            role="historical_cases",
            available=bool(historical.get("provider_available")),
            succeeded=bool(historical.get("provider_succeeded")),
            failed=historical.get("status") == "UNAVAILABLE",
            evidence_count=int(historical.get("case_count") or 0),
            pit_valid=historical.get("pit_valid"),
            used_downstream=bool(historical.get("provider_succeeded")),
            knowledge_available_at=as_of,
            reason=str(historical.get("reason") or ""),
        ),
        "obsidian_memory_adapter": _provider_record(
            "obsidian_memory_adapter",
            role="historical_reasons",
            available=bool(memory.get("provider_available") or memory.get("connected")),
            succeeded=bool(memory.get("provider_succeeded")),
            failed=memory.get("status") == "UNAVAILABLE",
            evidence_count=int(memory.get("note_count") or 0),
            pit_valid=memory.get("pit_valid"),
            used_downstream=bool(memory.get("provider_succeeded")),
            knowledge_available_at=as_of,
            reason=str(memory.get("reason") or ""),
        ),
    }
    provenance = list(providers.values())
    return {
        "context_type": "ResearchContext",
        "status": "RESEARCH_ONLY",
        "lineage_id": features["lineage_id"],
        "as_of": as_of,
        "industry": industry,
        "company": company,
        "capital": capital,
        "supply": supply,
        "pricing_gap": pricing_gap,
        "future_buyer_map": future_buyer_map,
        "integrated": integrated,
        "contradiction": integrated,
        "historical": historical,
        "memory": memory,
        "research_providers": providers,
        "research_provenance": provenance,
        "serenity_context": industry,
        "buffett_context": company,
        "uzi_context": capital,
        "contradiction_context": integrated,
        "capital_context": capital,
        "supply_context": supply,
        "repricing_context": pricing_gap,
        "risk_context": features.get("RISK") or {},
        "pit_audit": features.get("pit_audit") or {},
    }
