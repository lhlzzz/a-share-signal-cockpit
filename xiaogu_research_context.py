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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _seat_name(row: Dict[str, Any]) -> str:
    for key in ("BUY_SEAT", "SEAT", "OPERATESEATNAME", "EXPLAIN"):
        text = _text(row.get(key))
        if text:
            return text
    return ""


def _path_b_quality_from_answers(answers: list[str]) -> float:
    scored = []
    for answer in answers:
        text = str(answer or "").upper()
        if text == "YES":
            scored.append(1.0)
        elif text in {"BOUNDARY", "BOUNDARY AREA"}:
            scored.append(0.5)
        elif text == "NO":
            scored.append(0.0)
    if not scored:
        return 0.0
    return round(sum(scored) / len(scored), 8)


def _serenity_judgment(
    snapshot: Dict[str, Any],
    reports: list[Dict[str, Any]],
    industry_flow: list[Dict[str, Any]],
    demand: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    industry = _text(snapshot.get("sector") or snapshot.get("industry")) or "UNKNOWN"
    flow = industry_flow[0] if industry_flow else {}
    flow_change = _first_number(flow.get("f3"), flow.get("pct_change"))
    report_title = _text((reports[0] or {}).get("title")) if reports else ""
    demand = demand if isinstance(demand, dict) else {}
    bottleneck = _first_number(demand.get("bottleneck_strength"), demand.get("supply_constraint"))
    if bottleneck is not None and bottleneck >= 0.50:
        scarce_layer = f"{industry} 供给约束可见，接近卡点层"
        evidence_grade = "STRONG"
        chokepoint_role = "CONTROLS"
        path_b_quality = 1.0
        judgment = f"{industry} 有可观察的扩产约束或供给瓶颈，Serenity 把它排在卡点层。"
    elif reports:
        scarce_layer = f"{industry} 有行业报告，但报告未证明扩产约束"
        evidence_grade = "MEDIUM"
        chokepoint_role = "REPORT_UNPROVEN"
        path_b_quality = 0.5
        judgment = f"{industry} 有 T 日行业报告，仍看不到谁卡住供给。"
    elif flow_change is not None and flow_change >= 1:
        scarce_layer = f"{industry} 资金流入可见，卡点未证实"
        evidence_grade = "WEAK"
        chokepoint_role = "FLOW_ONLY"
        path_b_quality = 0.25
        judgment = f"{industry} T 日行业资金流入 {flow_change}%，这是资金线索，不是产业链卡点。"
    elif flow_change is not None and flow_change <= -1:
        scarce_layer = f"{industry} 资金流出，没有供给约束证据"
        evidence_grade = "WEAK"
        chokepoint_role = "FLOW_ONLY"
        path_b_quality = 0.25
        judgment = f"{industry} T 日行业资金流出 {flow_change}%，需求可见度下降。"
    elif industry_flow:
        scarce_layer = f"{industry} 行业资金可见，卡点未知"
        evidence_grade = "WEAK"
        chokepoint_role = "FLOW_ONLY"
        path_b_quality = 0.25
        judgment = f"{industry} 只有行业资金快照，没有扩产约束或客户认证。"
    else:
        scarce_layer = "NO_CAPTURED_CHAIN_EVIDENCE"
        evidence_grade = "UNVERIFIED"
        chokepoint_role = "NONE"
        path_b_quality = 0.0
        judgment = "没有行业报告或行业资金，Serenity 不能假装找到卡点。"
    if report_title:
        judgment = f"{judgment} 报告标题：{report_title}。"
    bottleneck_table = [
        {"layer": "下游需求", "position": industry, "is_bottleneck": chokepoint_role == "CONTROLS"},
        {"layer": "制造/供给", "position": "扩产约束" if chokepoint_role == "CONTROLS" else "未见扩产约束", "is_bottleneck": chokepoint_role == "CONTROLS"},
        {"layer": "材料/认证", "position": "客户认证或材料约束" if evidence_grade == "STRONG" else "未证实", "is_bottleneck": False},
        {"layer": "资金线索", "position": "行业资金" if industry_flow else "无行业资金", "is_bottleneck": False},
    ]
    return {
        "scarce_layer": scarce_layer,
        "evidence_grade": evidence_grade,
        "chokepoint_role": chokepoint_role,
        "path_b_quality": path_b_quality,
        "judgment": judgment,
        "wrong_if": "行业资金反转，或后续公告证明供给并不紧。",
        "bottleneck_table": bottleneck_table,
    }


def _roe_percent(preview: Dict[str, Any], business: Dict[str, Any]) -> float | None:
    preview_roe = _first_number(preview.get("WEIGHTAVG_ROE"))
    if preview_roe is not None:
        return preview_roe
    roe = _first_number(business.get("roe"))
    if roe is None:
        return None
    return roe * 100.0 if roe <= 1.5 else roe


def _buffett_path_b_checklist(
    snapshot: Dict[str, Any],
    preview: Dict[str, Any],
    reports: list[Dict[str, Any]],
    business: Dict[str, Any],
) -> list[Dict[str, Any]]:
    financials = _dict_rows((snapshot.get("raw") or {}).get("financials") if isinstance(snapshot.get("raw"), dict) else [])
    visible = bool(preview or reports or business.get("score") is not None or financials)
    sector = _text(snapshot.get("sector")) or "UNKNOWN"
    roe = _roe_percent(preview, business)
    gross = _first_number(business.get("gross_margin"))
    if gross is not None and gross > 1.5:
        gross = gross / 100.0
    moat_value = _first_number(business.get("moat"))
    pricing_value = _first_number(business.get("pricing_power"))
    earnings_value = _first_number(business.get("earnings_quality"))
    debt_value = _first_number(business.get("debt_safety"))
    q1 = "NO" if not visible else ("YES" if sector != "UNKNOWN" else "BOUNDARY")
    if moat_value is not None:
        q3 = "YES" if moat_value >= 0.50 else "NO"
    elif gross is not None:
        q3 = "YES" if gross >= 0.40 else "NO"
    else:
        q3 = "NO" if visible else "UNKNOWN"
    if pricing_value is not None:
        q4 = "YES" if pricing_value >= 0.50 else "NO"
    elif gross is not None:
        q4 = "YES" if gross >= 0.40 else "NO"
    else:
        q4 = "NO" if visible else "UNKNOWN"
    if earnings_value is not None:
        q5 = "YES" if earnings_value >= 0.50 else ("BOUNDARY" if earnings_value > 0 else "NO")
    elif preview or roe is not None:
        q5 = "YES"
    else:
        q5 = "UNKNOWN"
    if q5 == "YES" and roe is not None and roe < 15:
        q5 = "BOUNDARY"
    if debt_value is not None:
        q6 = "YES" if debt_value >= 0.50 else "NO"
    else:
        q6 = "UNKNOWN"
    q8 = "YES" if _first_number(business.get("valuation"), snapshot.get("raw", {}).get("margin_of_safety") if isinstance(snapshot.get("raw"), dict) else None) else "UNKNOWN"
    durability = "UNKNOWN"
    history = business.get("roe_history") if isinstance(business.get("roe_history"), list) else []
    history_values = []
    for item in history:
        value = _number(item)
        if value is None:
            continue
        history_values.append(value / 100.0 if value > 1.5 else value)
    if len(history_values) >= 3:
        durability = "YES" if min(history_values) >= 0.08 else "NO"
    return [
        {"id": 1, "dimension": "Circle of Competence", "answer": q1},
        {"id": 2, "dimension": "Durability", "answer": durability},
        {"id": 3, "dimension": "Moat", "answer": q3},
        {"id": 4, "dimension": "Pricing Power", "answer": q4},
        {"id": 5, "dimension": "Earnings Quality", "answer": q5},
        {"id": 6, "dimension": "Debt Safety", "answer": q6},
        {"id": 7, "dimension": "Management Integrity", "answer": "UNKNOWN"},
        {"id": 8, "dimension": "Reasonable Price", "answer": q8},
    ]


def _buffett_judgment(snapshot: Dict[str, Any], preview: Dict[str, Any], reports: list[Dict[str, Any]], business: Dict[str, Any]) -> Dict[str, Any]:
    name = _text(snapshot.get("name") or snapshot.get("f14")) or _text(snapshot.get("symbol"))
    sector = _text(snapshot.get("sector")) or "UNKNOWN"
    checklist = _buffett_path_b_checklist(snapshot, preview, reports, business)
    answers = [str(item.get("answer") or "UNKNOWN") for item in checklist]
    no_count = sum(1 for answer in answers if answer == "NO")
    roe = _roe_percent(preview, business)
    if answers[0] == "NO":
        circle = "outside circle"
        judgment = f"{name} 八问看不到可解释的生意，能力圈外。"
    elif answers[2] == "NO" or answers[3] == "NO":
        circle = "boundary area"
        judgment = f"{name} 属于{sector}。能解释怎么赚钱，但 Path B 没有看到护城河或定价权。"
    else:
        circle = "boundary area"
        judgment = f"{name} 有公司观察，但仍在能力圈边界。"
    if roe is not None:
        judgment += f" 可见 ROE {roe}%。"
        if roe < 8:
            judgment += " 长期平庸，不是 franchise。"
        elif roe < 15:
            judgment += " 未达到 15% 的质量门槛。"
        else:
            judgment += " ROE 过线，但仍缺护城河趋势。"
    if no_count >= 4:
        judgment += " Path B 八问四项为否，按快筛应过掉。"
    return {
        "circle_of_competence": circle,
        "checklist": checklist,
        "path_b_no_count": no_count,
        "path_b_quality": _path_b_quality_from_answers(answers),
        "judgment": judgment,
        "wrong_if": "后续财报证明 ROE 质量或护城河被高估。",
        "buy_sell": None,
    }


def _uzi_judgment(
    lhb_rows: list[Dict[str, Any]],
    announcements: list[Dict[str, Any]],
    capital: Dict[str, Any],
    captured_flow: bool = False,
) -> Dict[str, Any]:
    institution = any("机构" in _text(row.get("EXPLAIN")) for row in lhb_rows)
    seats = [_seat_name(row) for row in lhb_rows if _seat_name(row)]
    title = _text((announcements[0] or {}).get("title")) if announcements else ""
    if institution:
        vs = "institution"
        path_b_quality = 1.0
        judgment = "龙虎榜出现机构字样，按机构主导观察，不引入 panel scoring。"
    elif lhb_rows:
        vs = "hot_money"
        path_b_quality = 0.6
        judgment = "龙虎榜可见，未出现机构字样，按游资/短线资金观察。"
        if seats:
            judgment += f" 席位线索：{'；'.join(seats[:3])}。"
    elif announcements or captured_flow:
        vs = "unknown_no_board"
        path_b_quality = 0.25
        judgment = "T 日未上龙虎榜。用资金流/公告观察机构 vs 游资，空榜是市场事实。"
    else:
        vs = "UNKNOWN"
        path_b_quality = 0.0
        judgment = "没有龙虎榜、资金流或公告，不能判断机构还是游资。"
    if title:
        judgment += f" 公告：{title}。"
    if capital.get("distribution_risk"):
        judgment += " 资金分布风险已经可见。"
        path_b_quality = min(path_b_quality, 0.25)
    return {
        "institution_vs_hot_money": vs,
        "path_b_quality": path_b_quality,
        "judgment": judgment,
        "wrong_if": "机构资金撤出，或游资次日砸盘。",
    }


def _skill_verdict(
    provider: str,
    ran: bool,
    judgment: Dict[str, Any],
    evidence: list[Dict[str, Any]],
    *,
    complete: bool | None = None,
) -> Dict[str, Any]:
    complete = bool(ran) if complete is None else bool(complete)
    return {
        "provider": provider,
        "skill_file": {
            "Serenity": ".agents/skills/serenity-skill/SKILL.md",
            "Buffett": ".agents/skills/buffett/SKILL.md",
            "UZI": ".agents/skills/uzi/lhb-analyzer/SKILL.md",
        }.get(provider),
        "ran": bool(ran),
        "mode": "captured_path_b" if ran else "not_run",
        "full_skill_workflow": complete,
        "path_b_quality": judgment.get("path_b_quality") if ran else 0.0,
        "judgment": judgment.get("judgment") if ran else f"{provider} 没有深度观察，未执行。",
        "wrong_if": judgment.get("wrong_if") if ran else None,
        "evidence_ids": [
            {
                "source_id": item.get("source_id"),
                "event_id": item.get("event_id"),
                "mechanism": item.get("mechanism"),
            }
            for item in evidence
            if isinstance(item, dict)
        ],
        "buy_sell": None,
    }


def _dict_rows(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and value:
        return [value]
    return []


def _evidence_item(
    item: Dict[str, Any],
    *,
    default_source: str,
    default_mechanism: str,
    as_of: str,
    event_keys: tuple[str, ...] = ("event_id", "title", "EXPLAIN"),
) -> Dict[str, Any] | None:
    source_id = str(item.get("source_id") or default_source).strip()
    event_id = ""
    for key in event_keys:
        event_id = str(item.get(key) or "").strip()
        if event_id:
            break
    mechanism = str(item.get("mechanism") or default_mechanism).strip()
    if not (source_id and event_id and mechanism):
        return None
    observed = str(
        item.get("observed_at") or item.get("event_time") or item.get("publication_time") or ""
    ).strip()
    available = str(item.get("available_at") or item.get("knowledge_available_at") or as_of or "").strip()
    return {
        "source": item.get("source") or source_id,
        "source_id": source_id,
        "event_id": event_id,
        "mechanism": mechanism,
        "observed_at": observed,
        "available_at": available,
        "knowledge_available_at": available,
        "title": item.get("title") or item.get("EXPLAIN") or "",
    }


def _collect_evidence(rows: list[Dict[str, Any]], **kwargs) -> list[Dict[str, Any]]:
    items = []
    for row in rows:
        item = _evidence_item(row, **kwargs)
        if item is not None:
            items.append(item)
    return items


def build_serenity_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    demand = features["FUTURE_DEMAND"]
    raw = snapshot.get("raw", {})
    reports = _dict_rows(raw.get("industry_reports"))
    industry_flow = _dict_rows(raw.get("industry_flow"))
    if not industry_flow and isinstance(raw.get("industry_flow"), dict) and raw.get("industry_flow"):
        industry_flow = [raw["industry_flow"]]
    as_of = str(features.get("available_at") or "")
    evidence = _collect_evidence(
        reports,
        default_source="eastmoney.industry_report",
        default_mechanism="DEMAND",
        as_of=as_of,
    ) + _collect_evidence(
        industry_flow,
        default_source="eastmoney.industry_flow",
        default_mechanism="DEMAND",
        as_of=as_of,
        event_keys=("event_id", "f14", "industry"),
    )
    skill_ran = bool(reports or industry_flow)
    why_5d = []
    falsify = list(demand.get("invalidation_condition") or [])
    if reports:
        why_5d.append("T-day industry report may reprice demand inside five trading days")
        falsify.append("industry report follow-through disappears")
    if industry_flow:
        why_5d.append("T-day industry capital flow is visible")
        falsify.append("industry capital flow reverses")
    judgment = _serenity_judgment(snapshot, reports, industry_flow, demand)
    bottleneck_table = list(judgment.get("bottleneck_table") or [])
    complete = bool(skill_ran and len(bottleneck_table) >= 3 and judgment.get("scarce_layer"))
    return _context("FutureDemandContext", {
        "as_of": as_of,
        "market_story": demand["market_story"],
        "system_change": demand["system_change"],
        "industry": snapshot.get("sector", ""),
        "required_components": list(raw.get("required_components") or []),
        "bottleneck": demand["bottleneck_strength"],
        "supply_constraint": demand["supply_constraint"],
        "demand": demand["demand_strength"],
        "demand_visibility": demand["demand_visibility"],
        "industry_cycle": demand["industry_cycle"],
        "industry_catalyst": demand["industry_catalyst"],
        "evidence_strength": demand["evidence_strength"],
        "invalidation": list(demand["invalidation_condition"]),
        "reports": reports,
        "skill_ran": skill_ran,
        "skill_complete": complete,
        "bottleneck_table": bottleneck_table,
        "scarce_layer": judgment["scarce_layer"],
        "evidence_grade": judgment["evidence_grade"],
        "chokepoint_role": judgment["chokepoint_role"],
        "path_b_quality": judgment["path_b_quality"] if skill_ran else 0.0,
        "judgment": judgment["judgment"],
        "skill_verdict": _skill_verdict("Serenity", skill_ran, judgment, evidence, complete=complete),
        "evidence": evidence,
        "why_5d": why_5d,
        "falsify": falsify,
    }, features["lineage_id"], "Serenity")


def build_buffett_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    business = features["BUSINESS"]
    raw = snapshot.get("raw", {})
    reports = _dict_rows(raw.get("stock_reports"))
    preview = dict(raw.get("earnings_preview") or {})
    financials = _dict_rows(raw.get("financials"))
    as_of = str(features.get("available_at") or "")
    evidence = _collect_evidence(
        reports,
        default_source="eastmoney.stock_report",
        default_mechanism="VALUATION",
        as_of=as_of,
    ) + _collect_evidence(
        [preview] if preview else [],
        default_source="eastmoney.earnings_preview",
        default_mechanism="VALUATION",
        as_of=as_of,
        event_keys=("event_id", "WEIGHTAVG_ROE"),
    ) + _collect_evidence(
        financials,
        default_source="eastmoney.financials",
        default_mechanism="VALUATION",
        as_of=as_of,
        event_keys=("event_id", "REPORT_DATE"),
    )
    skill_ran = bool(reports or preview or financials)
    why_5d = []
    falsify = []
    if preview:
        why_5d.append("T-day earnings preview may reprice the company inside five trading days")
        falsify.append("earnings preview is revised down")
    if reports:
        why_5d.append("T-day company report is visible")
        falsify.append("company quality evidence is withdrawn")
    if financials:
        why_5d.append("T-day financials may reprice business quality inside five trading days")
        falsify.append("financial quality deteriorates")
    judgment = _buffett_judgment(snapshot, preview, reports, business)
    checklist = list(judgment.get("checklist") or [])
    complete = bool(skill_ran and len(checklist) == 8)
    return _context("CompanyContext", {
        "as_of": as_of,
        "business_quality": business["score"],
        "ability_circle": raw.get("ability_circle") or judgment["circle_of_competence"],
        "moat": business["moat"],
        "pricing_power": business["pricing_power"],
        "earnings_quality": business["earnings_quality"],
        "cash_flow": _number(raw.get("cash_flow_quality")),
        "roic": business["roic"],
        "roe": business["roe"],
        "growth": business["growth"],
        "management": business["management"],
        "debt_safety": business["debt_safety"],
        "capital_allocation": business["capital_allocation"],
        "valuation": business["valuation"],
        "margin_of_safety": _number(raw.get("margin_of_safety")),
        "reports": reports,
        "earnings_preview": preview,
        "financials": financials,
        "skill_ran": skill_ran,
        "skill_complete": complete,
        "checklist": checklist,
        "path_b_no_count": judgment.get("path_b_no_count") or 0,
        "path_b_quality": judgment.get("path_b_quality") if skill_ran else 0.0,
        "judgment": judgment["judgment"],
        "skill_verdict": _skill_verdict("Buffett", skill_ran, judgment, evidence, complete=complete),
        "buy_sell": None,
        "recommended_buy_price": None,
        "evidence": evidence,
        "why_5d": why_5d,
        "falsify": falsify,
    }, features["lineage_id"], "Buffett")


def build_uzi_context(snapshot: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    capital = features["CAPITAL"]
    raw = snapshot.get("raw", {})
    lhb_rows = _dict_rows(raw.get("lhb"))
    announcements = _dict_rows(raw.get("announcements"))
    as_of = str(features.get("available_at") or "")
    institution_signal = any(
        "机构" in str(row.get("EXPLAIN") or "")
        for row in lhb_rows
    )
    evidence = _collect_evidence(
        lhb_rows,
        default_source="eastmoney.lhb",
        default_mechanism="CAPITAL",
        as_of=as_of,
        event_keys=("event_id", "EXPLAIN"),
    ) + _collect_evidence(
        announcements,
        default_source="eastmoney.announcement",
        default_mechanism="CATALYST",
        as_of=as_of,
    )
    captured_flow_rows = _dict_rows(raw.get("stock_capital_flow"))
    if not captured_flow_rows and isinstance(raw.get("stock_capital_flow"), dict) and raw.get("stock_capital_flow"):
        captured_flow_rows = [raw["stock_capital_flow"]]
    captured_flow = bool(
        captured_flow_rows
        and any(
            row.get("source_id") or row.get("event_id")
            for row in captured_flow_rows
            if isinstance(row, dict)
        )
    )
    skill_ran = bool(lhb_rows or announcements or captured_flow)
    why_5d = []
    falsify = []
    if institution_signal:
        why_5d.append("T-day LHB shows institution activity")
        falsify.append("institution activity reverses")
    elif lhb_rows:
        why_5d.append("T-day LHB is visible")
        falsify.append("hot-money activity fades")
    elif skill_ran:
        why_5d.append("T-day capital flow or announcement is visible without an LHB board")
        falsify.append("capital flow reverses with no board confirmation")
    if announcements:
        why_5d.append("T-day announcement may act as a five-day catalyst")
        falsify.append("announcement is clarified away")
    if capital.get("distribution_risk"):
        falsify.append("capital distribution continues")
    judgment = _uzi_judgment(lhb_rows, announcements, capital, captured_flow=captured_flow)
    complete = bool(skill_ran and judgment.get("institution_vs_hot_money") in {"institution", "hot_money", "unknown_no_board"})
    return _context("CapitalContext", {
        "as_of": as_of,
        "institution_vs_hot_money": raw.get("institution_vs_hot_money") or judgment["institution_vs_hot_money"],
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
        "skill_ran": skill_ran,
        "skill_complete": complete,
        "path_b_quality": judgment.get("path_b_quality") if skill_ran else 0.0,
        "judgment": judgment["judgment"],
        "skill_verdict": _skill_verdict("UZI", skill_ran, judgment, evidence, complete=complete),
        "evidence": evidence,
        "why_5d": why_5d,
        "falsify": falsify,
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


def _research_evidence_items(payload: Any) -> list[Dict[str, Any]]:
    """Collect raw observed evidence dicts. Nested field values are not evidence."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    items: list[Dict[str, Any]] = []
    for key in ("evidence", "historical_cases", "notes"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _is_usable_research_evidence(item: Any, as_of: str) -> bool:
    """True only when evidence exists, is PIT-valid, identified, and contract-complete."""
    if not isinstance(item, dict) or not item:
        return False
    from xiaogu_forward_features import validate_evidence_identity

    identity = validate_evidence_identity(item)
    if identity is None:
        return False
    source_id, event_id, mechanism = identity
    if not (source_id and event_id and mechanism):
        return False
    source = str(item.get("source") or source_id).strip()
    observed = str(
        item.get("observed_at")
        or item.get("event_time")
        or item.get("publication_time")
        or ""
    ).strip()
    available = str(
        item.get("available_at")
        or item.get("knowledge_available_at")
        or ""
    ).strip()
    if not source or not observed or not available:
        return False
    if not as_of or not _visible_before(as_of, observed) or not _visible_before(as_of, available):
        return False
    return True


def _research_evidence_counts(payload: Any, as_of: str) -> tuple[int, int]:
    items = _research_evidence_items(payload)
    usable = sum(1 for item in items if _is_usable_research_evidence(item, as_of))
    return len(items), usable


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
    usable = 0 if usable_evidence_count is None else int(usable_evidence_count or 0)
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


_RESEARCH_PROVIDER_KEYS = {
    "industry": "Serenity",
    "serenity_context": "Serenity",
    "company": "Buffett",
    "buffett_context": "Buffett",
    "capital": "UZI",
    "uzi_context": "UZI",
    "capital_context": "UZI",
    "integrated": "Contradiction",
    "contradiction": "Contradiction",
    "contradiction_context": "Contradiction",
    "historical": "postgresql.paper_observations",
    "memory": "obsidian_memory_adapter",
}


def mark_research_used_downstream(
    research: Dict[str, Any] | None,
    *providers: str,
) -> Dict[str, Any] | None:
    """Record that Alpha/Decision actually read these providers. Not a second research system."""
    if not isinstance(research, dict) or not providers:
        return research
    slots = research.get("research_providers")
    if not isinstance(slots, dict):
        return research
    for name in providers:
        record = slots.get(name)
        if isinstance(record, dict):
            record["used_downstream"] = True
    research["research_provenance"] = list(slots.values())
    return research


def read_research_provider(research: Dict[str, Any] | None, key: str) -> Any:
    """Read one research field and mark that provider as used downstream."""
    if not isinstance(research, dict):
        return None
    value = research.get(key)
    provider = _RESEARCH_PROVIDER_KEYS.get(key)
    if provider:
        mark_research_used_downstream(research, provider)
    return value


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

    def _load() -> Dict[str, Any]:
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
            observed = 0
            usable = 0
            for row in rows:
                paper = _json_payload(row.get("paper_payload"))
                outcome = _json_payload(row.get("outcome_payload"))
                event_time = _knowledge_stamp(paper.get("event_time"), row.get("signal_time"))
                knowledge_available_at = _knowledge_stamp(
                    paper.get("knowledge_available_at"),
                    paper.get("available_at"),
                )
                observed += 1
                identity_item = {
                    **paper,
                    "source": paper.get("source") or "postgresql.paper_observations",
                    "source_id": paper.get("source_id"),
                    "event_id": paper.get("event_id"),
                    "mechanism": paper.get("mechanism"),
                    "observed_at": paper.get("observed_at") or event_time,
                    "available_at": knowledge_available_at,
                    "knowledge_available_at": knowledge_available_at,
                }
                if _is_usable_research_evidence(identity_item, as_of):
                    usable += 1
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
        return {
            "cases": loaded,
            "observed_count": observed,
            "usable_count": usable,
        }

    payload, error = _run_provider(_load)
    if error is not None:
        return {
            "status": "UNAVAILABLE",
            "reason": type(error).__name__,
            "historical_cases": [],
            "historical_success_rate": None,
            "historical_failure_patterns": [],
            "case_count": 0,
            "usable_evidence_count": 0,
            "provider_available": False,
            "provider_succeeded": False,
        }
    payload = payload or {}
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        cases = []
    observed_count = int(payload.get("observed_count") or 0) if isinstance(payload, dict) else len(cases)
    usable_count = int(payload.get("usable_count") or 0) if isinstance(payload, dict) else 0
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
        "case_count": observed_count,
        "usable_evidence_count": usable_count,
        "provider_available": True,
        "provider_succeeded": True,
        "pit_valid": usable_count > 0 if observed_count else None,
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
    observed = 0
    usable = 0
    visible = []
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        observed += 1
        knowledge_available_at = _knowledge_stamp(
            note.get("knowledge_available_at"),
            note.get("available_at"),
        )
        identity_item = {
            **note,
            "source": note.get("source") or "obsidian_memory_adapter",
            "source_id": note.get("source_id"),
            "event_id": note.get("event_id"),
            "mechanism": note.get("mechanism"),
            "observed_at": note.get("observed_at") or note.get("event_time") or note.get("signal_time"),
            "available_at": knowledge_available_at,
            "knowledge_available_at": knowledge_available_at,
        }
        if _is_usable_research_evidence(identity_item, as_of):
            usable += 1
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
        "note_count": observed,
        "usable_evidence_count": usable,
        "provider_available": True,
        "provider_succeeded": True,
        "pit_valid": usable > 0 if observed else None,
    }


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
    serenity_count, serenity_usable = _research_evidence_counts(industry, as_of)
    buffett_count, buffett_usable = _research_evidence_counts(company, as_of)
    uzi_count, uzi_usable = _research_evidence_counts(capital, as_of)
    contradiction_count, contradiction_usable = _research_evidence_counts(integrated, as_of)
    historical_count = int(historical.get("case_count") or 0)
    historical_usable = int(historical.get("usable_evidence_count") or 0)
    memory_count = int(memory.get("note_count") or 0)
    memory_usable = int(memory.get("usable_evidence_count") or 0)
    providers = {
        "Serenity": _provider_record(
            "Serenity",
            role="evidence",
            available=serenity_error is None,
            succeeded=serenity_error is None and not industry.get("degraded"),
            failed=serenity_error is not None or bool(industry.get("degraded")),
            evidence_count=serenity_count,
            usable_evidence_count=serenity_usable,
            pit_valid=True if serenity_usable else (False if serenity_count else None),
            knowledge_available_at=as_of,
            reason="" if serenity_error is None else type(serenity_error).__name__,
        ),
        "Buffett": _provider_record(
            "Buffett",
            role="evidence",
            available=buffett_error is None,
            succeeded=buffett_error is None and not company.get("degraded"),
            failed=buffett_error is not None or bool(company.get("degraded")),
            evidence_count=buffett_count,
            usable_evidence_count=buffett_usable,
            pit_valid=True if buffett_usable else (False if buffett_count else None),
            knowledge_available_at=as_of,
            reason="" if buffett_error is None else type(buffett_error).__name__,
        ),
        "UZI": _provider_record(
            "UZI",
            role="evidence",
            available=uzi_error is None,
            succeeded=uzi_error is None and not capital.get("degraded"),
            failed=uzi_error is not None or bool(capital.get("degraded")),
            evidence_count=uzi_count,
            usable_evidence_count=uzi_usable,
            pit_valid=True if uzi_usable else (False if uzi_count else None),
            knowledge_available_at=as_of,
            reason="" if uzi_error is None else type(uzi_error).__name__,
        ),
        "Contradiction": _provider_record(
            "Contradiction",
            role="contradiction",
            available=contradiction_error is None,
            succeeded=contradiction_error is None and not integrated.get("degraded"),
            failed=contradiction_error is not None or bool(integrated.get("degraded")),
            evidence_count=contradiction_count,
            usable_evidence_count=contradiction_usable,
            pit_valid=True if contradiction_usable else (False if contradiction_count else None),
            knowledge_available_at=as_of,
            reason="" if contradiction_error is None else type(contradiction_error).__name__,
        ),
        "postgresql.paper_observations": _provider_record(
            "postgresql.paper_observations",
            role="historical_cases",
            available=bool(historical.get("provider_available")),
            succeeded=bool(historical.get("provider_succeeded")),
            failed=historical.get("status") == "UNAVAILABLE",
            evidence_count=historical_count,
            usable_evidence_count=historical_usable,
            pit_valid=historical.get("pit_valid"),
            knowledge_available_at=as_of,
            reason=str(historical.get("reason") or ""),
        ),
        "obsidian_memory_adapter": _provider_record(
            "obsidian_memory_adapter",
            role="historical_reasons",
            available=bool(memory.get("provider_available") or memory.get("connected")),
            succeeded=bool(memory.get("provider_succeeded")),
            failed=memory.get("status") == "UNAVAILABLE",
            evidence_count=memory_count,
            usable_evidence_count=memory_usable,
            pit_valid=memory.get("pit_valid"),
            knowledge_available_at=as_of,
            reason=str(memory.get("reason") or ""),
        ),
    }
    provenance = list(providers.values())
    why_5d = []
    falsify = []
    for payload in (industry, company, capital, supply, pricing_gap):
        if isinstance(payload, dict):
            why_5d.extend(str(item) for item in (payload.get("why_5d") or []) if item)
            falsify.extend(str(item) for item in (payload.get("falsify") or []) if item)
    if not why_5d:
        why_5d = ["captured T-day observations remain incomplete for a five-day profit window"]
    if not falsify:
        falsify = ["capital exit or supply reversal"]
    seen_why: list[str] = []
    seen_falsify: list[str] = []
    for item in why_5d:
        if item not in seen_why:
            seen_why.append(item)
    for item in falsify:
        if item not in seen_falsify:
            seen_falsify.append(item)
    historical_failures = [
        str(item)
        for item in (historical.get("historical_failure_patterns") or [])
        if item
    ]
    gaps = []
    if not historical.get("historical_cases"):
        gaps.append("MISSING_HISTORICAL_TICKETS")
    if not memory.get("notes"):
        gaps.append("MISSING_OBSIDIAN_NOTES")
    for pattern in historical_failures:
        if pattern not in seen_falsify:
            gaps.append(pattern)
            seen_falsify.append(pattern)
    skill_verdicts = {
        "Serenity": industry.get("skill_verdict") or _skill_verdict("Serenity", False, {}, []),
        "Buffett": company.get("skill_verdict") or _skill_verdict("Buffett", False, {}, []),
        "UZI": capital.get("skill_verdict") or _skill_verdict("UZI", False, {}, []),
    }
    skill_complete = bool(
        industry.get("skill_complete")
        and company.get("skill_complete")
        and capital.get("skill_complete")
    )
    thesis = {
        "status": "RESEARCH_ONLY",
        "why_5d": seen_why,
        "falsify": seen_falsify,
        "capital": capital.get("why_5d") or [],
        "supply": list((supply.get("source_rows") or {}).keys()) if isinstance(supply, dict) else [],
        "demand": industry.get("why_5d") or [],
        "valuation": company.get("why_5d") or [],
        "catalyst": capital.get("why_5d") or industry.get("why_5d") or [],
        "contradiction": integrated.get("contradiction_status") if isinstance(integrated, dict) else "UNKNOWN",
        "path_b_quality": {
            "serenity": industry.get("path_b_quality"),
            "buffett": company.get("path_b_quality"),
            "uzi": capital.get("path_b_quality"),
        },
    }
    research_gap_audit = {
        "status": "RESEARCH_ONLY",
        "gaps": gaps,
        "historical_failure_patterns": historical_failures,
        "note_count": int(memory.get("note_count") or 0),
        "case_count": int(historical.get("case_count") or 0),
        "selects_top1": False,
    }
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
        "opportunity_5d_thesis": thesis,
        "skill_verdicts": skill_verdicts,
        "skill_complete": skill_complete,
        "research_gap_audit": research_gap_audit,
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
