"""Price-formation measurements derived from one canonical snapshot."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable

from xiaogu_forward_snapshot import CanonicalSnapshot, assert_point_in_time_evidence, filter_point_in_time_records

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
DIRECT_EVIDENCE_FAMILIES = (
    "DIRECT_INSTITUTION",
    "DIRECT_MAIN_FORCE",
    "DIRECT_HOT_MONEY",
)


def _event_id(source_id: str, payload: Any, index: int = 0) -> str:
    return hashlib.sha256(
        json.dumps({"source": source_id, "payload": payload, "index": index}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _evidence(
    *,
    observed: bool,
    source: str,
    available_at: str,
    evidence_family: str,
    source_id: str = "",
    event_id: str = "",
    mechanism: str = "",
    observed_at: str = "",
    lineage_id: str = "",
    interpretation: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    source_id = str(source_id or source or "")
    event_id = str(event_id or source_id)
    mechanism = str(mechanism or evidence_family)
    observed_at = str(observed_at or available_at or "")
    identity = f"{source_id}|{event_id}|{mechanism}"
    return {
        "observed": bool(observed),
        "source": source,
        "source_id": source_id,
        "event_id": event_id,
        "mechanism": mechanism,
        "evidence_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        "evidence_family": evidence_family,
        "observed_at": observed_at,
        "available_at": available_at,
        "lineage_id": lineage_id,
        "interpretation": interpretation,
        **extra,
    }



def _optional_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _number(value: Any, default: float | None = None) -> float | None:
    number = _optional_number(value)
    return default if number is None else number


def _source_present(payload: Dict[str, Any], *keys: str) -> bool:
    return any(payload.get(key) not in (None, "", "-") for key in keys)


def _optional_clip(value: Any, low: float = 0.0, high: float = 1.0) -> float | None:
    number = _optional_number(value)
    return None if number is None else max(low, min(high, number))


def _optional_rate(value: Any, high: float = 0.02) -> float | None:
    number = _optional_number(value)
    return None if number is None else _clip(number, high=high) / high


def _first(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if payload.get(key) not in (None, "", "-"):
            return payload[key]
    return default


def _clip(value: Any, low: float = 0.0, high: float = 1.0) -> float | None:
    number = _optional_number(value)
    return None if number is None else max(low, min(high, number))


def _mean(*values: Any) -> float | None:
    numbers = [number for number in (_clip(value) for value in values if value is not None) if number is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _observed_mean(weights_and_values: Iterable[tuple[float, Any]]) -> float | None:
    items = [(weight, _optional_number(value)) for weight, value in weights_and_values]
    present = [(weight, value) for weight, value in items if value is not None]
    if not present:
        return None
    total_weight = sum(weight for weight, _ in present)
    if total_weight <= 0:
        return None
    return _clip(sum(weight * value for weight, value in present) / total_weight)


def _ratio(numerator: Any, denominator: Any) -> float | None:
    denominator_value = _optional_number(denominator)
    numerator_value = _optional_number(numerator)
    if denominator_value is None or numerator_value is None or denominator_value <= 0:
        return None
    return _clip(numerator_value / denominator_value)


def _signed_strength(value: Any, scale: float = 10.0) -> float | None:
    number = _optional_number(value)
    return None if number is None else _clip((number / scale + 1.0) / 2.0)


def _percentile(values: Iterable[Any], current: Any) -> float | None:
    numbers = sorted(number for number in (_optional_number(value) for value in values) if number is not None)
    current_value = _optional_number(current)
    if not numbers or current_value is None:
        return None
    return sum(value <= current_value for value in numbers) / len(numbers)


def _raw(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    value = snapshot.get("raw")
    return value if isinstance(value, dict) else {}


def _record_available_at(record: Dict[str, Any], fallback: str) -> str:
    """Keep the provider timestamp attached to one evidence record."""
    return str(
        record.get("available_at")
        or record.get("observed_at")
        or record.get("publication_time")
        or record.get("event_time")
        or fallback
        or ""
    )


_COVERAGE_SKIP = {
    "lineage_id", "source", "available_at", "evidence", "evidence_count", "score",
    "coverage", "observed_count", "available_count", "missing_rate", "valid_rate",
    "industry_cycle", "invalidation_condition", "accumulation_phase",
    "capital_flow_state", "capital_price_impact_state", "supply_absorption_state",
    "regime", "stage", "buyable", "low_price", "drawdown_is_not_gap",
    "SUPPLY_OBSERVED", "DEMAND_OBSERVED", "ABSORPTION_OBSERVED", "PRICE_RESPONSE_OBSERVED",
    "halted", "regulatory_hard_risk", "thesis_invalidated", "buyer_exhaustion",
    "market_regime",
}


def _attach_coverage(group: Dict[str, Any]) -> Dict[str, Any]:
    fields = []
    for key, value in group.items():
        if key in _COVERAGE_SKIP or key.endswith(("_behavior", "_evidence", "_observation", "_components")):
            continue
        if isinstance(value, (dict, list)):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            continue
        fields.append(value)
    available = len(fields)
    observed = sum(value is not None for value in fields)
    group["observed_count"] = observed
    group["available_count"] = available
    group["missing_rate"] = None if not available else round(1.0 - observed / available, 8)
    group["valid_rate"] = None if not available else round(observed / available, 8)
    group["coverage"] = f"{observed}/{available}"
    return group


def _market_stage(raw: Dict[str, Any], capital: Dict[str, Any], supply: Dict[str, Any], market: Dict[str, Any]) -> str:
    explicit = str(_first(raw, "repricing_state", "market_stage", default="") or "").upper()
    if explicit in {"ACCUMULATION", "IGNITION", "EXPANSION", "CLIMAX", "DISTRIBUTION"}:
        return explicit
    if (market["attention"] or 0.0) >= 0.85 and (market["price_strength"] or 0.0) >= 0.80:
        return "CLIMAX"
    if (capital["capital_price_divergence"] or 0.0) >= 0.70 and (market["price_strength"] or 0.0) >= 0.55:
        return "DISTRIBUTION"
    if (
        capital.get("fund_flow_acceleration") is not None
        and capital["fund_flow_acceleration"] >= 0.65
        and (market["price_strength"] or 0.0) >= 0.50
    ):
        return "IGNITION"
    if (market["breadth"] or 0.0) >= 0.60 and (market["leader_strength"] or 0.0) >= 0.60:
        return "EXPANSION"
    if (
        capital.get("fund_flow_persistence") is not None
        and capital["fund_flow_persistence"] >= 0.55
        and supply["supply_absorption_state"] == "ABSORPTION"
        and (market["price_strength"] or 0.0) < 0.55
    ):
        return "ACCUMULATION"
    return "UNKNOWN"


def build_feature_vector(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Build bounded measurements; missing evidence remains explicitly missing."""
    if not isinstance(snapshot, CanonicalSnapshot) or snapshot.get("trusted_snapshot") is not True:
        raise TypeError("FEATURE_ENGINE_REQUIRES_CANONICAL_SNAPSHOT")
    snap = snapshot
    raw = _raw(snap)
    as_of = snap.get("as_of") or snap.get("source_time") or snap.get("available_at")
    flow_raw = raw.get("stock_capital_flow") if isinstance(raw.get("stock_capital_flow"), dict) else {}
    flow = flow_raw if assert_point_in_time_evidence(flow_raw, as_of) else {}
    industry_raw = raw.get("industry_flow") if isinstance(raw.get("industry_flow"), dict) else {}
    industry_flow = industry_raw if assert_point_in_time_evidence(industry_raw, as_of) else {}
    earnings_raw = raw.get("earnings_preview") if isinstance(raw.get("earnings_preview"), dict) else {}
    earnings = earnings_raw if assert_point_in_time_evidence(earnings_raw, as_of) else {}
    shareholder, shareholder_excluded = filter_point_in_time_records(raw.get("shareholder_changes") if isinstance(raw.get("shareholder_changes"), list) else [], as_of)
    lhb_kept, lhb_excluded = filter_point_in_time_records(raw.get("lhb") if isinstance(raw.get("lhb"), list) else [], as_of)
    announcements_kept, announcements_excluded = filter_point_in_time_records(raw.get("announcements") if isinstance(raw.get("announcements"), list) else [], as_of)
    news_kept, news_excluded = filter_point_in_time_records(raw.get("news") if isinstance(raw.get("news"), list) else [], as_of)
    org_surveys_kept, org_surveys_excluded = filter_point_in_time_records(raw.get("org_surveys") if isinstance(raw.get("org_surveys"), list) else [], as_of)
    reports_kept, reports_excluded = filter_point_in_time_records(raw.get("stock_reports") if isinstance(raw.get("stock_reports"), list) else [], as_of)
    lockup_kept, lockup_excluded = filter_point_in_time_records(raw.get("lockup_expiry") if isinstance(raw.get("lockup_expiry"), list) else [], as_of)
    industry_reports_kept, industry_reports_excluded = filter_point_in_time_records(raw.get("industry_reports") if isinstance(raw.get("industry_reports"), list) else [], as_of)
    future_buyers_kept, future_buyers_excluded = filter_point_in_time_records(raw.get("future_buyers") if isinstance(raw.get("future_buyers"), list) else [], as_of)
    raw = dict(raw)
    raw["lhb"] = lhb_kept
    raw["announcements"] = announcements_kept
    raw["news"] = news_kept
    raw["org_surveys"] = org_surveys_kept
    raw["stock_reports"] = reports_kept
    raw["lockup_expiry"] = lockup_kept
    raw["industry_reports"] = industry_reports_kept
    raw["shareholder_changes"] = shareholder
    raw["future_buyers"] = future_buyers_kept
    if flow:
        raw["stock_capital_flow"] = flow
    elif "stock_capital_flow" in raw:
        raw["stock_capital_flow"] = {}
    if industry_flow:
        raw["industry_flow"] = industry_flow
    elif "industry_flow" in raw:
        raw["industry_flow"] = {}
    if earnings:
        raw["earnings_preview"] = earnings
    elif "earnings_preview" in raw:
        raw["earnings_preview"] = {}
    price = _optional_number(snap.get("price"))
    high = _optional_number(snap.get("high"))
    low = _optional_number(snap.get("low"))
    amount = _optional_number(snap.get("amount"))
    turnover = _optional_number(snap.get("turnover"))
    pct_change = _optional_number(_first(raw, "pct_chg", "signal_pct", "f3"))
    main_flow = _optional_number(_first(flow, "main_net_inflow", "f62", default=_first(raw, "main_net_inflow", "f62")))
    main_flow_pct = _optional_number(_first(flow, "main_net_inflow_pct", "f184", "f18"))
    amount_value = amount or 0.0
    turnover_value = turnover or 0.0
    main_flow_value = main_flow or 0.0
    pct_change_value = pct_change or 0.0
    close_position = _ratio(price - low, high - low) if price is not None and high is not None and low is not None and high > low else None
    turnover_velocity = _optional_clip(turnover / 20.0) if turnover is not None else None
    price_strength = _optional_clip((pct_change + 5.0) / 15.0) if pct_change is not None else None
    amount_pctile = _optional_clip(_first(raw, "amount_percentile", "full_universe_amount_pctile"))
    attention = _optional_clip(_first(raw, "attention_score", "popularity_score", "popularity_percentile"))
    breadth = _optional_clip(_optional_number(_first(raw, "market_breadth_up_pct")) / 100.0 if _optional_number(_first(raw, "market_breadth_up_pct")) is not None else None)
    sector_source = _first(raw, "sector_breadth", "sector_strength")
    sector_breadth = _optional_clip(sector_source if sector_source is not None else (_optional_number(_first(industry_flow, "f3")) / 10.0 if _optional_number(_first(industry_flow, "f3")) is not None else None))
    # A flow amount without traded amount has no comparable denominator and
    # must remain unobserved rather than becoming a saturated signal.
    flow_comparable = amount is not None and amount > 0 and main_flow is not None and abs(main_flow) <= amount * 5.0
    positive_flow = _clip(max(main_flow, 0.0) / amount) if flow_comparable else None
    negative_flow = _clip(max(-main_flow, 0.0) / amount) if flow_comparable else None
    price_impact = _clip(abs(pct_change) / max(abs(main_flow_pct) * 2.0, 1.0)) if pct_change is not None and main_flow_pct is not None else None
    price_response = _optional_clip(max(pct_change, 0.0) / 10.0) if pct_change is not None else None
    capital_divergence = _optional_clip(
        _first(raw, "capital_price_divergence")
        if raw.get("capital_price_divergence") not in (None, "")
        else (
            1.0
            if main_flow is not None and main_flow > 0 and any(
                (net := _optional_number(row.get("NET_BS_AMT"))) is not None and net < 0
                for row in (raw.get("lhb") or [])
                if isinstance(row, dict)
            )
            else (0.75 if main_flow is not None and main_flow > 0 and pct_change is not None and pct_change <= 0 else None)
        )
    )
    if amount is None or main_flow is None or pct_change is None:
        capital_price_impact_state = "UNKNOWN"
    elif main_flow > 0 and pct_change > 0:
        capital_price_impact_state = "DEMAND_RESPONSE_OBSERVATION"
    elif main_flow > 0 and pct_change <= 0:
        capital_price_impact_state = "CAPITAL_PRICE_DIVERGENCE"
    elif main_flow < 0 and pct_change > 0:
        capital_price_impact_state = "CAPITAL_PRICE_DIVERGENCE"
    elif main_flow < 0:
        capital_price_impact_state = "DISTRIBUTION_RISK"
    else:
        capital_price_impact_state = "NEUTRAL"

    business = {
        "business_quality": _optional_clip(_first(raw, "business_quality", "financial_quality", default=_optional_number(_first(earnings, "WEIGHTAVG_ROE")) / 100.0 if _optional_number(_first(earnings, "WEIGHTAVG_ROE")) is not None else None)),
        "moat": _optional_clip(_first(raw, "moat", "moat_quality")),
        "pricing_power": _optional_clip(_first(raw, "pricing_power")),
        "earnings_quality": _optional_clip(_first(raw, "earnings_quality", "cash_flow_quality")),
        "roic": _optional_clip(_optional_number(_first(raw, "roic")) / 100.0 if _optional_number(_first(raw, "roic")) is not None else None),
        "roe": _optional_clip(_optional_number(_first(raw, "roe", default=_first(earnings, "WEIGHTAVG_ROE"))) / 100.0 if _optional_number(_first(raw, "roe", default=_first(earnings, "WEIGHTAVG_ROE"))) is not None else None),
        "growth": _optional_clip(_optional_number(_first(raw, "growth", "earnings_growth")) / 100.0 if _optional_number(_first(raw, "growth", "earnings_growth")) is not None else None),
        "management": _optional_clip(_first(raw, "management", "management_quality")),
        "debt_safety": _optional_clip(_first(raw, "debt_safety")),
        "capital_allocation": _optional_clip(_first(raw, "capital_allocation")),
        "valuation": _optional_clip(_first(raw, "valuation", "valuation_quality", "valuation_score")),
    }
    business["score"] = _observed_mean((1.0, value) for key, value in business.items() if key != "score")
    # Accept the source synonym while keeping BUSINESS as the production axis.
    business["financial_quality"] = business["business_quality"]

    future_demand = {
        "market_story": _optional_clip(_first(raw, "market_story_strength")),
        "system_change": _optional_clip(_first(raw, "system_change_strength")),
        "demand_strength": _optional_clip(_first(raw, "demand_strength", "demand_score")),
        "bottleneck_strength": _optional_clip(_first(raw, "bottleneck_strength", "bottleneck_score")),
        "supply_constraint": _optional_clip(_first(raw, "supply_constraint")),
        "demand_visibility": _optional_clip(_first(raw, "demand_visibility")),
        "industry_cycle": str(_first(raw, "industry_cycle", default="UNKNOWN") or "UNKNOWN").upper(),
        "industry_catalyst": _optional_clip(_first(raw, "industry_catalyst", "catalyst_strength")),
        "evidence_strength": _optional_clip(_first(raw, "evidence_strength")),
        "invalidation_condition": list(raw.get("industry_invalidation_conditions") or []),
    }
    demand_numeric = [value for key, value in future_demand.items() if key not in {"industry_cycle", "invalidation_condition"}]
    future_demand["score"] = _observed_mean((1.0, value) for value in demand_numeric)

    capital = {
        "fund_flow": positive_flow,
        "fund_flow_acceleration": _optional_clip(_first(raw, "fund_flow_acceleration", "capital_acceleration")),
        "fund_flow_persistence": _optional_clip(_first(raw, "fund_flow_persistence", "capital_persistence")),
            "fund_flow_percentile": _optional_clip(_first(raw, "fund_flow_percentile", default=amount_pctile if main_flow is not None and main_flow > 0 else None)),
        "institutional_flow": _optional_clip(_first(raw, "institutional_flow", "institution_confirmation")),
        "hot_money_flow": _optional_clip(_first(raw, "hot_money_flow", "hot_money_confirmation")),
        "lhb_quality": _optional_clip(_first(raw, "lhb_quality")),
        "seat_behavior": _optional_clip(_first(raw, "seat_behavior_score")),
        "order_pressure": _optional_clip(_first(raw, "order_pressure", "order_book_pressure")),
        "capital_flow_ratio": positive_flow,
        "volume_accumulation": _optional_clip(_first(raw, "volume_accumulation")),
        "price_volume_confirmation": _optional_clip(_first(raw, "price_volume_confirmation", default=price_response)),
        "capital_price_divergence": capital_divergence,
        "capital_price_impact": price_impact,
        "capital_price_impact_state": capital_price_impact_state,
        "distribution_risk": negative_flow,
    }
    # Flow/amount is a ratio, not accumulation. Accumulation needs identity
    # evidence plus persistence that has actually been observed.
    capital["accumulation"] = _optional_clip(_first(raw, "capital_accumulation"))
    capital["main_force_flow"] = None
    capital["capital_persistence"] = capital["fund_flow_persistence"]
    capital["capital_acceleration"] = capital["fund_flow_acceleration"]
    available_at = str(snap.get("as_of") or snap.get("source_time") or "")
    flow_available_at = _record_available_at(flow, available_at)
    industry_available_at = _record_available_at(industry_flow, available_at)
    earnings_available_at = _record_available_at(earnings, available_at)
    lhb_rows = [row for row in raw.get("lhb") or [] if isinstance(row, dict)]
    flow_source = "stock_capital_flow" if isinstance(raw.get("stock_capital_flow"), dict) and raw.get("stock_capital_flow") else "quote_flow"
    industry_source = "industry_flow" if industry_flow else ""

    def _institution_row(row: Dict[str, Any]) -> bool:
        return (
            "机构" in str(row.get("EXPLAIN") or "")
            or row.get("institution") is True
            or row.get("institution_type") is True
        )

    def _hot_money_row(row: Dict[str, Any]) -> bool:
        return bool(
            row.get("hot_money") is True
            or row.get("hot_money_type") is True
            or row.get("游资") is True
            or "游资" in str(row.get("EXPLAIN") or "")
        )

    def _lhb_direction(rows: list[Dict[str, Any]]) -> str:
        text = " ".join(str(row.get("EXPLAIN") or "") for row in rows)
        nets = [value for value in (_optional_number(row.get("NET_BS_AMT")) for row in rows) if value is not None]
        net = sum(nets) if nets else None
        if "卖出" in text or (net is not None and net < 0):
            return "SELL"
        if "买入" in text or (net is not None and net > 0):
            return "BUY"
        return ""

    lineage_id = str(snap.get("lineage_id") or "")
    institution_rows = [row for row in lhb_rows if _institution_row(row)]
    hot_money_rows = [row for row in lhb_rows if _hot_money_row(row)]
    institution_direct = []
    hot_money_direct = []
    for index, row in enumerate(lhb_rows):
        event_id = _event_id("lhb", row, index)
        record_available_at = _record_available_at(row, available_at)
        if _institution_row(row):
            institution_direct.append(_evidence(
                observed=True, source="lhb", available_at=record_available_at,
                evidence_family="DIRECT_INSTITUTION", detail="lhb_institution",
                source_id="lhb", event_id=event_id, mechanism="lhb_event",
                observed_at=record_available_at, lineage_id=lineage_id,
                interpretation="INSTITUTION",
            ))
        if _hot_money_row(row):
            hot_money_direct.append(_evidence(
                observed=True, source="lhb", available_at=record_available_at,
                evidence_family="DIRECT_HOT_MONEY", detail="lhb_hot_money",
                source_id="lhb", event_id=event_id, mechanism="lhb_event",
                observed_at=record_available_at, lineage_id=lineage_id,
                interpretation="HOT_MONEY",
            ))
    for key in ("institution_position_change", "institution_holding_change", "institution_flow_evidence", "institution_trade_evidence"):
        if raw.get(key) not in (None, "", False, 0, 0.0):
            institution_direct.append(_evidence(
                observed=True, source=key, available_at=available_at,
                evidence_family="DIRECT_INSTITUTION", detail=key,
                source_id=key, event_id=_event_id(key, raw.get(key)), mechanism="institution_identity",
                observed_at=available_at, lineage_id=lineage_id, interpretation="INSTITUTION",
            ))
    for key in ("hot_money_evidence", "hot_money_trade_evidence"):
        if raw.get(key) not in (None, "", False, 0, 0.0):
            hot_money_direct.append(_evidence(
                observed=True, source=key, available_at=available_at,
                evidence_family="DIRECT_HOT_MONEY", detail=key,
                source_id=key, event_id=_event_id(key, raw.get(key)), mechanism="hot_money_identity",
                observed_at=available_at, lineage_id=lineage_id, interpretation="HOT_MONEY",
            ))
    capital_flow_observation = []
    if amount is not None and amount > 0 and main_flow is not None:
        capital_flow_observation.append(_evidence(
            observed=True, source=flow_source, available_at=flow_available_at,
            evidence_family="DIRECT_CAPITAL_FLOW", detail="main_net_inflow",
            source_id=flow_source, event_id=_event_id(flow_source, {"main_net_inflow": main_flow}),
            mechanism="capital_flow", observed_at=flow_available_at, lineage_id=lineage_id,
            interpretation="CAPITAL_FLOW_POSITIVE" if main_flow > 0 else "CAPITAL_FLOW_NEGATIVE",
        ))
    main_force_direct = []
    for key in ("main_force_identity", "direct_main_force", "large_order_structure", "main_force_seat", "主力席位"):
        if raw.get(key) not in (None, "", False, 0, 0.0):
            main_force_direct.append(_evidence(
                observed=True, source=key, available_at=available_at,
                evidence_family="DIRECT_MAIN_FORCE", detail=key,
                source_id=key, event_id=_event_id(key, raw.get(key)), mechanism="main_force_identity",
                observed_at=available_at, lineage_id=lineage_id, interpretation="MAIN_FORCE",
            ))
    price_volume_evidence = []
    if turnover:
        price_volume_evidence.append(_evidence(
            observed=True, source="quote_turnover", available_at=available_at,
            evidence_family="PRICE_VOLUME", detail="turnover",
            source_id="quote_turnover", event_id=_event_id("quote_turnover", turnover),
            mechanism="price_volume", observed_at=available_at, lineage_id=lineage_id,
        ))
    if snap.get("volume"):
        price_volume_evidence.append(_evidence(
            observed=True, source="quote_volume", available_at=available_at,
            evidence_family="PRICE_VOLUME", detail="volume",
            source_id="quote_volume", event_id=_event_id("quote_volume", snap.get("volume")),
            mechanism="price_volume", observed_at=available_at, lineage_id=lineage_id,
        ))
    persistence_evidence = []
    if raw.get("fund_flow_persistence") not in (None, ""):
        persistence_evidence.append(_evidence(
            observed=True, source="fund_flow_persistence", available_at=available_at,
            evidence_family="FLOW_PERSISTENCE", detail="fund_flow_persistence",
            source_id="fund_flow_persistence", event_id=_event_id("fund_flow_persistence", raw.get("fund_flow_persistence")),
            mechanism="flow_persistence", observed_at=available_at, lineage_id=lineage_id,
        ))
    industry_capital_evidence = []
    if industry_source:
        industry_capital_evidence.append(_evidence(
            observed=True, source=industry_source, available_at=available_at,
            evidence_family="INDUSTRY_CAPITAL", detail="industry_flow",
            source_id=industry_source, event_id=_event_id(industry_source, industry_flow),
            mechanism="industry_capital", observed_at=available_at, lineage_id=lineage_id,
        ))

    institution_direction_hint = _lhb_direction(institution_rows)
    if not institution_direct:
        institution_direction = "UNKNOWN"
    elif institution_direction_hint == "SELL":
        institution_direction = "DISTRIBUTING"
    elif (
        institution_direction_hint == "BUY"
        and capital["fund_flow_persistence"] is not None
        and capital["fund_flow_persistence"] >= 0.55
    ):
        institution_direction = "ACCUMULATING"
    elif institution_direction_hint == "BUY":
        institution_direction = "BUYING"
    elif (capital["institutional_flow"] or 0.0) > 0 and institution_direction_hint == "BUY":
        institution_direction = "BUYING"
    elif (capital["institutional_flow"] or 0.0) < 0:
        institution_direction = "DISTRIBUTING"
    elif capital["institutional_flow"] == 0:
        institution_direction = "NEUTRAL"
    else:
        institution_direction = "PRESENT"

    hot_money_direction_hint = _lhb_direction(hot_money_rows)
    if not hot_money_direct:
        hot_money_direction = "UNKNOWN"
    elif hot_money_direction_hint == "SELL" or (capital["hot_money_flow"] or 0.0) < 0:
        hot_money_direction = "EXITING"
    elif (
        capital["fund_flow_acceleration"] is not None
        and capital["fund_flow_acceleration"] >= 0.60
        and hot_money_direction_hint == "BUY"
    ):
        hot_money_direction = "ACCELERATING"
    elif hot_money_direction_hint == "BUY" or (capital["hot_money_flow"] or 0.0) > 0:
        hot_money_direction = "BUYING"
    else:
        hot_money_direction = "PRESENT"

    if not main_force_direct:
        main_force_direction = "UNKNOWN"
    elif main_flow is not None and main_flow < 0:
        main_force_direction = "DISTRIBUTING"
    elif (
        capital["fund_flow_persistence"] is not None
        and capital["fund_flow_persistence"] >= 0.55
        and main_flow is not None
        and main_flow > 0
    ):
        main_force_direction = "ACCUMULATING"
    else:
        main_force_direction = "PRESENT"

    def behavior(direction: str, strength: float, persistence: float, acceleration: float, evidence: list[Dict[str, Any]]) -> Dict[str, Any]:
        observed = [item for item in evidence if item.get("observed") and item.get("evidence_family") in DIRECT_EVIDENCE_FAMILIES]
        count = len(observed)
        source_trust = 0.85 if any(item.get("source") in {"lhb", "stock_capital_flow"} for item in observed) else 0.40 if observed else 0.0
        freshness = 1.0 if available_at else 0.5
        return {
            "direction": direction,
            "strength": None if not count or strength is None else round(_clip(strength), 8),
            "persistence": None if persistence is None or not count else round(_clip(persistence), 8),
            "acceleration": None if acceleration is None or not count else round(_clip(acceleration), 8),
            "evidence": evidence,
            "evidence_count": int(count),
            "evidence_family": next((item.get("evidence_family") for item in observed), "UNKNOWN"),
            "source": next((item.get("source") for item in observed), ""),
            "available_at": available_at,
            "confidence": round(_clip(min(1.0, count / 2.0) * source_trust * freshness), 8),
            "evidence_status": "OBSERVED" if count else "UNKNOWN",
            "observation": [item for item in evidence if item.get("observed")],
            "interpretation": direction,
        }

    capital["institution_behavior"] = behavior(
        institution_direction, capital["institutional_flow"], capital["fund_flow_persistence"],
        capital["fund_flow_acceleration"], institution_direct,
    )
    if main_force_direct:
        capital["main_force_flow"] = _observed_mean((
            (0.35, capital["accumulation"]),
            (0.25, capital["fund_flow_persistence"]),
            (0.20, capital["volume_accumulation"]),
            (0.20, capital["price_volume_confirmation"]),
        ))
    capital["main_force_behavior"] = behavior(
        main_force_direction, capital["accumulation"] if main_force_direct else None,
        capital["fund_flow_persistence"], capital["fund_flow_acceleration"],
        main_force_direct,
    )
    capital["capital_flow_observation"] = capital_flow_observation
    capital["capital_flow_state"] = (
        "CAPITAL_FLOW_POSITIVE" if main_flow_value > 0 and amount_value > 0
        else "CAPITAL_FLOW_NEGATIVE" if main_flow_value < 0 and amount_value > 0
        else "UNKNOWN"
    )
    capital["hot_money_behavior"] = behavior(
        hot_money_direction, capital["hot_money_flow"], capital["fund_flow_persistence"],
        capital["fund_flow_acceleration"], hot_money_direct,
    )
    capital["price_volume_evidence"] = price_volume_evidence
    capital["flow_persistence_evidence"] = persistence_evidence
    capital["industry_capital_evidence"] = industry_capital_evidence
    capital["accumulation_quality"] = _observed_mean((
        (0.30, capital["fund_flow_persistence"]),
        (0.25, capital["volume_accumulation"]),
        (0.20, capital["institutional_flow"]),
        (0.15, capital["price_volume_confirmation"]),
        (0.10, capital["fund_flow"]),
    ))
    if (capital["distribution_risk"] or 0.0) >= 0.70:
        capital["accumulation_phase"] = "DISTRIBUTION"
    elif capital["accumulation"] is None:
        capital["accumulation_phase"] = "UNOBSERVED"
    elif (
        capital["accumulation"] >= 0.60
        and capital["fund_flow_acceleration"] is not None
        and capital["fund_flow_acceleration"] >= 0.60
    ):
        capital["accumulation_phase"] = "IGNITION"
    elif (capital["accumulation_quality"] or 0.0) >= 0.45:
        capital["accumulation_phase"] = "ACCUMULATION"
    elif capital["accumulation"] > 0:
        capital["accumulation_phase"] = "ORDINARY_TRADING"
    else:
        capital["accumulation_phase"] = "UNOBSERVED"

    supply = {
        "free_float": _optional_clip(_first(raw, "free_float_ratio", "free_float")),
        "turnover": _optional_clip(turnover / 20.0) if turnover is not None else None,
        "turnover_velocity": turnover_velocity,
        "historical_volume_nodes": _optional_clip(_first(raw, "historical_volume_nodes")),
        "overhead_supply": _optional_clip(_first(raw, "overhead_supply")),
        "profit_chip_ratio": _optional_clip(_first(raw, "profit_chip_ratio")),
        "trapped_chip_ratio": _optional_clip(_first(raw, "trapped_chip_ratio")),
        "shareholder_reduction": _optional_clip(_first(raw, "shareholder_reduction")),
        "pledge_pressure": _optional_clip(_first(raw, "pledge_pressure")),
        "unlocking_pressure": _optional_clip(_first(raw, "unlocking_pressure", "lockup_pressure")),
        "large_holder_supply": _optional_clip(_first(raw, "large_holder_supply")),
        "recent_distribution": _optional_clip(_first(raw, "recent_distribution")),
        "sell_pressure": _optional_clip(_first(raw, "sell_pressure")),
    }
    if supply["shareholder_reduction"] is None and shareholder:
        reductions = [
            1 for row in shareholder
            if isinstance(row, dict) and (_optional_number(row.get("change_num")) is not None) and _optional_number(row.get("change_num")) < 0
        ]
        supply["shareholder_reduction"] = _clip(sum(reductions) / 3.0) if reductions else None
    supply["effective_supply"] = _observed_mean((
        (0.35, supply["overhead_supply"]),
        (0.20, supply["trapped_chip_ratio"]),
        (0.15, supply["shareholder_reduction"]),
        (0.10, supply["pledge_pressure"]),
        (0.10, supply["unlocking_pressure"]),
        (0.10, supply["sell_pressure"]),
    ))
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
            (capital["capital_flow_ratio"] or 0.0) > 0
            and capital["fund_flow_persistence"] is not None
            and capital["fund_flow_persistence"] > 0
            and main_flow is not None
            and main_flow > 0
            and amount is not None
            and amount > 0
        ),
        "turnover": turnover is not None and turnover >= 1.0,
        "price_response": (capital["price_volume_confirmation"] or 0.0) > 0 and amount_value > 0,
        "supply": supply["supply_evidence_count"] > 0,
        "stability": close_position is not None and close_position >= 0.45,
        "continuation": capital["fund_flow_acceleration"] is not None and capital["fund_flow_acceleration"] > 0,
    }
    supply["SUPPLY_OBSERVED"] = bool(absorption_components["supply"])
    supply["DEMAND_OBSERVED"] = bool(absorption_components["funds"])
    supply["ABSORPTION_OBSERVED"] = bool(
        absorption_components["funds"]
        and absorption_components["turnover"]
        and absorption_components["price_response"]
        and absorption_components["supply"]
    )
    supply["PRICE_RESPONSE_OBSERVED"] = bool(absorption_components["price_response"])
    supply["absorption_evidence_count"] = sum(absorption_components.values())
    supply["absorption_confidence"] = (
        round(_clip(supply["absorption_evidence_count"] / len(absorption_components)), 8)
        if supply["absorption_evidence_count"] else None
    )
    supply_support = _observed_mean((
        (1.0, capital["accumulation"] if capital["accumulation"] is not None else capital["capital_flow_ratio"]),
        (1.0, capital["fund_flow_persistence"]),
        (1.0, capital["fund_flow_acceleration"]),
        (1.0, capital["price_volume_confirmation"]),
        (1.0, supply["turnover_velocity"]),
        (1.0, close_position),
    ))
    supply_pressure = _observed_mean((
        (1.0, supply["effective_supply"]),
        (1.0, supply["sell_pressure"]),
        (1.0, supply["overhead_supply"]),
        (1.0, supply["recent_distribution"]),
    ))
    supply["unlock_pressure"] = supply["unlocking_pressure"]
    supply["supply_pressure"] = supply["effective_supply"]
    supply["distribution_pressure"] = supply["sell_pressure"]
    minimum_evidence = (
        amount_value > 0
        and turnover_value >= 1.0
        and supply["absorption_evidence_count"] >= 4
        and all(absorption_components[key] for key in ("funds", "turnover", "price_response", "supply"))
        and supply["PRICE_RESPONSE_OBSERVED"]
    )
    supply["supply_absorption"] = round(
        _clip(supply_support - 0.60 * supply_pressure), 8
    ) if minimum_evidence and supply_support is not None and supply_pressure is not None else None
    supply["supply_absorption_state"] = (
        "UNKNOWN" if not supply["absorption_evidence_count"]
        else "PARTIAL" if not minimum_evidence
        else "RELEASING" if supply_pressure is not None and supply_support is not None and supply_pressure > supply_support + 0.15
        else "ABSORPTION" if (supply["supply_absorption"] or 0.0) >= 0.35
        else "BALANCED"
    )
    supply["evidence"] = [name for name, present in absorption_components.items() if present]
    supply["evidence_count"] = int(supply["absorption_evidence_count"])
    supply["confidence"] = supply["absorption_confidence"]
    supply["score"] = supply["supply_absorption"]
    # Keep capital/price as an observation. True SUPPLY_ABSORPTION lives on the
    # supply engine, not on this two-variable pair.
    capital["capital_price_impact_state"] = capital_price_impact_state

    pricing_gap = {
        "fundamental_gap": _optional_clip(_first(raw, "fundamental_gap")),
        "industry_gap": _optional_clip(_first(raw, "industry_gap")),
        "capital_gap": _optional_clip(_first(raw, "capital_gap")),
        "earnings_gap": _optional_clip(_first(raw, "earnings_gap")),
        "demand_gap": _optional_clip(_first(raw, "demand_gap")),
        "attention_gap": _optional_clip(_first(raw, "attention_gap")),
        "institutional_positioning": _optional_clip(_first(raw, "institutional_positioning", default=capital["institutional_flow"])),
        "institutional_gap": (
            None if (positioning := _optional_clip(_first(raw, "institutional_positioning", default=capital["institutional_flow"]))) is None
            else _optional_clip(1.0 - positioning)
        ),
        "price_reflection": _optional_clip(_first(raw, "price_reflection")),
    }
    pricing_gap["score"] = _observed_mean((
        (0.20, pricing_gap["fundamental_gap"]),
        (0.15, pricing_gap["industry_gap"]),
        (0.15, pricing_gap["earnings_gap"]),
        (0.15, pricing_gap["demand_gap"]),
        (0.15, pricing_gap["capital_gap"]),
        (0.10, pricing_gap["attention_gap"]),
        (0.10, pricing_gap["institutional_gap"] if _source_present(raw, "institutional_positioning") else None),
    ))
    # Low price and a prior drawdown are not a pricing gap.
    pricing_gap["low_price"] = False
    pricing_gap["drawdown_is_not_gap"] = True
    pricing_gap["real_pricing_gap"] = pricing_gap["score"]

    market = {
        "regime": str(_first(raw, "market_regime", default="UNKNOWN") or "UNKNOWN").upper(),
        "breadth": breadth,
        "sector_breadth": sector_breadth,
        "leader_strength": _optional_clip(_first(raw, "leader_strength", "leader_position")),
        "price_strength": price_strength,
        "attention": attention,
        "follow_through": _optional_clip(_first(raw, "market_follow_through_score", "follow_through")),
        "revaluation": _optional_clip(_first(raw, "revaluation_probability")),
    }
    market["stage"] = _market_stage(raw, capital, supply, market)
    market["alignment"] = _optional_clip(_first(raw, "market_alignment", default=sector_breadth))
    market_values = [market[key] for key in ("breadth", "sector_breadth", "follow_through", "price_strength") if market[key] is not None]
    market["score"] = _observed_mean((1.0, value) for value in market_values)

    reflexivity = {
        "price_strength": market["price_strength"],
        "sector_breadth": market["sector_breadth"],
        "leader_strength": market["leader_strength"],
        "attention_growth": _optional_clip(_first(raw, "attention_growth")),
        "capital_acceleration": capital["fund_flow_acceleration"],
        "market_regime": market["regime"],
        "crowding": _optional_clip(_first(raw, "crowding_risk", "crowding"))
        if _source_present(raw, "crowding_risk", "crowding")
        else (_optional_clip((market["attention"] - 0.75)) if market["attention"] is not None else None),
        "buyer_exhaustion": None if raw.get("buyer_exhaustion") is None else bool(raw.get("buyer_exhaustion")),
    }
    reflexivity["score"] = _observed_mean((1.0, reflexivity[key]) for key in ("price_strength", "sector_breadth", "leader_strength", "attention_growth", "capital_acceleration"))
    reflexivity["break"] = _optional_clip(
        _first(raw, "reflexivity_break")
        if raw.get("reflexivity_break") not in (None, "")
        else max(value for value in (capital["distribution_risk"], (market["attention"] - 0.75) if market["attention"] is not None else None) if value is not None)
        if capital["distribution_risk"] is not None or market["attention"] is not None
        else None
    )
    reflexivity["reflexivity_strength"] = reflexivity["score"]
    reflexivity["reflexivity_break_risk"] = reflexivity["break"]

    halted_source = _first(raw, "halted", "is_suspended")
    regulatory_source = _first(raw, "regulatory_hard_block", "risk_hard_block")
    thesis_invalidated_source = raw.get("thesis_invalidated")
    risk = {
        "halted": None if halted_source is None else bool(halted_source),
        "regulatory_hard_risk": None if regulatory_source is None else bool(regulatory_source),
        "liquidity": _optional_clip(_first(raw, "liquidity_score")),
        "downside": _optional_clip(_first(raw, "downside_risk")),
        "event_risk": _optional_clip(_first(raw, "event_risk", "risk_notice_penalty")),
        "thesis_invalidated": None if thesis_invalidated_source is None else bool(thesis_invalidated_source),
    }
    risk_penalties = (
        (0.35, risk["downside"]),
        (0.25, risk["event_risk"]),
        (0.20, None if risk["regulatory_hard_risk"] is None else float(risk["regulatory_hard_risk"])),
        (0.20, None if risk["halted"] is None else float(risk["halted"])),
    )
    observed_penalty = _observed_mean(risk_penalties)
    risk["score"] = None if observed_penalty is None else _clip(1.0 - observed_penalty)

    execution = {
        "entry_price": price,
        "buyable": raw.get("buyable"),
        "execution_quality": _optional_clip(_first(raw, "execution_quality")),
        "short_term_overheat": _optional_clip(_first(raw, "short_term_overheat")) if _source_present(raw, "short_term_overheat") else _optional_clip(max(value for value in (price_response, market["attention"]) if value is not None)) if price_response is not None or market["attention"] is not None else None,
        "gap_risk": _optional_clip(_first(raw, "gap_risk", "next_day_risk")),
        "close_position": close_position,
        "slippage": _optional_rate(_first(raw, "slippage", "slippage_rate")),
        "spread": _optional_rate(_first(raw, "spread", "spread_rate")),
        "market_impact": _optional_rate(_first(raw, "market_impact", "market_impact_rate")),
    }
    execution["cost_rate"] = _optional_clip(_first(raw, "execution_cost_rate"), high=0.02)
    execution_inputs = (execution["execution_quality"], execution["gap_risk"], execution["slippage"], execution["spread"], execution["market_impact"])
    execution["execution_feasibility"] = None if any(value is None for value in execution_inputs) else _clip(
        execution["execution_quality"]
        * (1.0 - execution["gap_risk"])
        * (1.0 - 0.50 * execution["slippage"] - 0.25 * execution["spread"] - 0.25 * execution["market_impact"])
    )
    execution["score"] = execution["execution_feasibility"]

    for group in (business, future_demand, capital, supply, pricing_gap, market, reflexivity, risk, execution):
        _attach_coverage(group)

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
        "pit_audit": {
            "as_of": as_of,
            "kept": {
                "shareholder_changes": len(shareholder),
                "lhb": len(lhb_kept),
                "announcements": len(announcements_kept),
                "news": len(news_kept),
                "org_surveys": len(org_surveys_kept),
                "stock_reports": len(reports_kept),
                "lockup_expiry": len(lockup_kept),
                "industry_reports": len(industry_reports_kept),
                "future_buyers": len(future_buyers_kept),
            },
            "excluded_from_features": {
                "shareholder_changes": len(shareholder_excluded),
                "lhb": len(lhb_excluded),
                "announcements": len(announcements_excluded),
                "news": len(news_excluded),
                "org_surveys": len(org_surveys_excluded),
                "stock_reports": len(reports_excluded),
                "lockup_expiry": len(lockup_excluded),
                "industry_reports": len(industry_reports_excluded),
                "future_buyers": len(future_buyers_excluded),
            },
        },
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
