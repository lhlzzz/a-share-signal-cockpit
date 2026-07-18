#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小股统一前向裁判 scoreboard v0.1。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
RULE_VERSION = "xiaogu_forward_judge_scoreboard_v0_1"
HORIZON_FIELDS = {
    "d1": "d1_return",
    "d20": "d20_return",
    "d60": "d60_return",
    "d120": "d120_return",
    "d250": "d250_return",
}
LEDGER_HORIZON_MAP = {
    "d1_return": "t1_return",
    "d2_return": "t2_return",
    "d3_return": "t3_return",
}
ACTIVE_DECISION_RECORD_TYPES = {"DECISION", "CORRECTION"}
GUARDRAILS = [
    "PAPER_ONLY",
    "NO_TRADE",
    "NO_BROKER_LOGIN",
    "MODEL_SCORE_NOT_REAL_WIN_RATE",
    "FORWARD_RETURNS_ONLY_FOR_WIN_RATE",
    "TRACEABLE_EVIDENCE_REQUIRED_FOR_PROMOTION",
]
DIAGNOSIS_VERSION = "xiaogu_p0_p6_diagnosis_v0_1"
ATTRIBUTION_VERSION = "xiaogu_failure_attribution_v0_1"
ATTRIBUTION_CATEGORIES = {
    "DATA_MISS",
    "FACTOR_MISS",
    "TIMING_MISS",
    "SECTOR_MISS",
    "FLOW_MISS",
    "LIMITUP_MISS",
    "RISK_CONTROL_MISS",
}
ATTRIBUTION_PRIORITY = [
    "DATA_MISS",
    "RISK_CONTROL_MISS",
    "TIMING_MISS",
    "LIMITUP_MISS",
    "FLOW_MISS",
    "SECTOR_MISS",
    "FACTOR_MISS",
]
FAILURE_TAGS = {
    "LOSS",
    "LOW_RETURN",
    "CHASE_HIGH_RISK",
    "FRIED_BOARD_RISK",
    "FRIED_BOARD_UNVERIFIED",
}
MISS_LIMITUP_TAGS = {"NO_LIMIT_UP_EVIDENCE"}


def fnum(v: Any) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        x = float(str(v).replace("%", "").replace(",", ""))
        return None if math.isnan(x) else x
    except Exception:
        return None


def method_name(row: Dict[str, Any]) -> str:
    return str(
        row.get("method")
        or row.get("method_winner")
        or row.get("method_winner_after_d1")
        or row.get("method_winner_after_60d")
        or row.get("longline_method_winner_after_250d")
        or "UNKNOWN_METHOD"
    ).strip() or "UNKNOWN_METHOD"


def summarize_returns(values: Iterable[float]) -> Dict[str, Any]:
    vals = list(values)
    if not vals:
        return {"trades": 0, "wins": 0, "win_rate_pct": None, "avg_return_pct": None, "profit_factor": None}
    wins = [x for x in vals if x > 0]
    gains = [max(x, 0.0) for x in vals]
    losses = [abs(min(x, 0.0)) for x in vals]
    loss_sum = sum(losses)
    pf = (sum(gains) / loss_sum) if loss_sum else ("INF" if sum(gains) > 0 else None)
    return {
        "trades": len(vals),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(vals) * 100, 4),
        "avg_return_pct": round(sum(vals) / len(vals), 4),
        "profit_factor": round(pf, 4) if isinstance(pf, float) else pf,
    }


def first_available_return_pct(row: Dict[str, Any]) -> Optional[float]:
    for field in ("d1_return", "d2_return", "d3_return", "d20_return", "d60_return", "d120_return", "d250_return"):
        ret = fnum(row.get(field))
        if ret is not None:
            return ret
    return None


def has_limit_up_evidence(row: Dict[str, Any]) -> bool:
    for key in ("hit_limit_up", "hit_limit_up_t1", "sealed_limit_up", "limit_up_touched"):
        if row.get(key) is True:
            return True
    for key in ("limitup_strength_tags", "board_strength_tags", "review_tags"):
        values = row.get(key) or []
        if isinstance(values, str):
            values = [values]
        if any("limit" in str(v).lower() or "涨停" in str(v) for v in values):
            return True
    return False


def has_limitup_capture_evidence(row: Dict[str, Any]) -> bool:
    profile = str(row.get("limitup_capture_profile") or "")
    score = fnum(row.get("limitup_capture_score")) or 0.0
    return profile in {"STRONG_LIMITUP_CAPTURE", "MEDIUM_LIMITUP_CAPTURE"} or score >= 0.50 or bool(row.get("limitup_capture_confirmed"))


def listify(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def add_reason(reasons: List[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def limitup_feature_gap_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if has_limit_up_evidence(row):
        return reasons
    signal_pct = fnum(row.get("signal_pct"))
    close_position_score = fnum(row.get("close_position_score"))
    intraday_high_pct = fnum(row.get("intraday_high_pct"))
    limitup_reason_strength = fnum(row.get("limitup_reason_strength"))
    seal_order_strength = fnum(row.get("seal_order_strength"))
    order_book_pressure = fnum(row.get("order_book_pressure"))
    structured_confirmations = [x for x in (limitup_reason_strength, seal_order_strength, order_book_pressure) if x is not None]
    if structured_confirmations and max(structured_confirmations) >= 0.60:
        return reasons

    if intraday_high_pct is not None and intraday_high_pct >= 9.5:
        add_reason(reasons, "intraday_limit_touch_without_seal_confirmation")
    if (signal_pct is not None and signal_pct >= 7.0) or (close_position_score is not None and close_position_score >= 0.70):
        add_reason(reasons, "pre_limitup_anomaly_without_vei_confirmation")
    source_layers = {str(x) for x in listify(row.get("source_layers"))}
    setup_type = str(row.get("setup_type") or "")
    if "L4_UNDERWATER_RECOVERY" in source_layers or "UNDERWATER" in setup_type:
        add_reason(reasons, "weak_to_strong_without_limitup_mapping")
    if "L4_PRE_BREAKOUT" in source_layers or "PRE_BREAKOUT" in setup_type:
        add_reason(reasons, "first_board_pre_signal_without_limitup_mapping")
    if not reasons:
        add_reason(reasons, "limitup_confirmation_feature_gap")
    return reasons


def attribution_categories_for_row(
    row: Dict[str, Any],
    tags: Iterable[str],
    failure_reasons: Iterable[str],
    missed_limitup_reasons: Iterable[str],
) -> Tuple[List[str], Optional[str]]:
    tag_set = set(tags)
    reasons = set(failure_reasons) | set(missed_limitup_reasons)
    categories: List[str] = []

    def add_category(category: str) -> None:
        if category in ATTRIBUTION_CATEGORIES and category not in categories:
            categories.append(category)

    if reasons & {"negative_forward_return", "below_expected_return"}:
        add_category("FACTOR_MISS")
    if "late_entry" in reasons:
        add_category("TIMING_MISS")
    if "weak_sector_or_board" in reasons:
        add_category("SECTOR_MISS")
    if "capital_outflow" in reasons:
        add_category("FLOW_MISS")
    if reasons & {"no_limitup_confirmation", "non_pick_later_showed_limitup_or_large_return", "fried_board_risk"}:
        add_category("LIMITUP_MISS")

    risk_markers = " ".join(str(x) for x in listify(row.get("blocked_reasons")) + listify(row.get("broken_limit_risk_flags"))).upper()
    risk_penalty = fnum(row.get("risk_penalty"))
    if (
        row.get("weak_close_risk")
        or row.get("high_open_low_close_risk")
        or (risk_penalty is not None and risk_penalty > 0)
        or any(marker in risk_markers for marker in ("RISK", "REGULATORY", "ABNORMAL", "CONTROL", "监管", "风险", "异动"))
    ):
        add_category("RISK_CONTROL_MISS")

    missing_domains = (
        listify(row.get("candidate_evidence_missing_domains"))
        + listify(row.get("enhanced_evidence_missing_domains"))
        + listify(row.get("experimental_evidence_missing_domains"))
    )
    evidence_status = str(row.get("candidate_evidence_status") or "").upper()
    needs_attribution = bool(tag_set & ({"FAILURE", "FALSE_NEGATIVE", "MISSED_LIMITUP"} | FAILURE_TAGS | MISS_LIMITUP_TAGS))
    if needs_attribution and (missing_domains or evidence_status in {"MISSING", "DATA_MISSING", "FAIL"} or _evidence_count(row) == 0):
        add_category("DATA_MISS")
    if needs_attribution and not categories:
        add_category("DATA_MISS")

    ordered = [category for category in ATTRIBUTION_PRIORITY if category in categories]
    return ordered, (ordered[0] if ordered else None)


def infer_market_regime(row: Dict[str, Any]) -> str:
    explicit = str(row.get("market_regime") or "").strip()
    if explicit:
        return explicit
    breadth = fnum(row.get("market_breadth_up_pct") or row.get("market_breadth"))
    limitups = fnum(row.get("market_limitups"))
    bigups = fnum(row.get("market_bigups"))
    if breadth is None:
        return "UNKNOWN_MARKET_REGIME"
    if breadth < 15:
        return "panic"
    if breadth < 30:
        return "weak_trend"
    if limitups is not None and limitups >= 80 and breadth >= 45:
        return "limitup_expansion"
    if limitups is not None and limitups < 30 and breadth < 45:
        return "limitup_contraction"
    if breadth >= 55 and (bigups is None or bigups >= 80):
        return "strong_trend"
    if 30 <= breadth < 55:
        return "rotation"
    return "weak_trend"


def observation_reasons(row: Dict[str, Any], tags: Iterable[str], market_regime: str) -> Tuple[List[str], List[str], List[str], List[str], List[str]]:
    tag_set = set(tags)
    success_reasons: List[str] = []
    failure_reasons: List[str] = []
    missed_limitup_reasons: List[str] = []
    false_positive_reasons: List[str] = []
    false_negative_reasons: List[str] = []

    if "SUCCESS" in tag_set:
        add_reason(success_reasons, "positive_forward_return")
        if has_limit_up_evidence(row):
            add_reason(success_reasons, "limitup_confirmed")
        if fnum(row.get("net_inflow_main")) is not None and fnum(row.get("net_inflow_main")) > 0:
            add_reason(success_reasons, "capital_flow_positive")
        if market_regime in {"strong_trend", "limitup_expansion"}:
            add_reason(success_reasons, "supportive_market_regime")

    if tag_set & FAILURE_TAGS:
        if "LOSS" in tag_set:
            add_reason(failure_reasons, "negative_forward_return")
        if "LOW_RETURN" in tag_set:
            add_reason(failure_reasons, "below_expected_return")
        if "CHASE_HIGH_RISK" in tag_set:
            add_reason(failure_reasons, "late_entry")
        if tag_set & {"FRIED_BOARD_RISK", "FRIED_BOARD_UNVERIFIED"}:
            add_reason(failure_reasons, "fried_board_risk")
        net_flow = fnum(row.get("net_inflow_main"))
        if net_flow is not None and net_flow <= 0:
            add_reason(failure_reasons, "capital_outflow")
        if not row.get("concept_industry_tags") and not row.get("board_strength_tags"):
            add_reason(failure_reasons, "weak_sector_or_board")
        if market_regime in {"panic", "weak_trend", "limitup_contraction"}:
            add_reason(failure_reasons, "weak_market_regime")

    if tag_set & MISS_LIMITUP_TAGS:
        add_reason(missed_limitup_reasons, "no_limitup_confirmation")
        for reason in limitup_feature_gap_reasons(row):
            add_reason(missed_limitup_reasons, reason)
    if "MISSED_LIMITUP" in tag_set:
        add_reason(missed_limitup_reasons, "non_pick_later_showed_limitup_or_large_return")
        for reason in limitup_feature_gap_reasons(row):
            add_reason(missed_limitup_reasons, reason)
    if "FALSE_POSITIVE" in tag_set:
        false_positive_reasons.extend(failure_reasons or ["pick_failed_post_result"])
    if "FALSE_NEGATIVE" in tag_set:
        false_negative_reasons.extend(missed_limitup_reasons or ["non_pick_outperformed_post_result"])

    return success_reasons, failure_reasons, missed_limitup_reasons, false_positive_reasons, false_negative_reasons


def classify_forward_observation(row: Dict[str, Any], low_return_pct: float = 3.0, limit_up_return_pct: float = 9.0) -> Dict[str, Any]:
    tags: List[str] = []
    reasons: List[str] = []
    decision = row.get("decision")
    ret = first_available_return_pct(row)
    signal_pct = fnum(row.get("signal_pct"))
    close_position_score = fnum(row.get("close_position_score"))
    market_regime = infer_market_regime(row)

    if decision == "NO_PICK":
        tags.append("NO_PICK_OBSERVATION")
    if decision == "RESEARCH_CANDIDATE":
        tags.append("RESEARCH_ONLY")
    if decision == "PAPER_PICK" and ret is None:
        tags.append("RESULT_PENDING")
        reasons.append("PAPER_PICK has no filled forward return yet")

    if decision == "PAPER_PICK" and ret is not None:
        if ret < 0:
            tags.append("LOSS")
            reasons.append(f"forward return {ret:.2f}% < 0")
        elif ret < low_return_pct:
            tags.append("LOW_RETURN")
            reasons.append(f"forward return {ret:.2f}% < {low_return_pct:.2f}%")
        else:
            tags.append("SUCCESS")
            reasons.append(f"forward return {ret:.2f}% >= {low_return_pct:.2f}%")
        if not has_limit_up_evidence(row) and ret < limit_up_return_pct:
            tags.append("NO_LIMIT_UP_EVIDENCE")
            reasons.append(f"no explicit limit-up evidence and forward high return {ret:.2f}% < {limit_up_return_pct:.2f}%")

    if decision in {"NO_PICK", "RESEARCH_CANDIDATE"} and ret is not None and (ret >= limit_up_return_pct or has_limit_up_evidence(row)):
        tags.append("MISSED_LIMITUP")
        tags.append("FALSE_NEGATIVE")
        reasons.append("non-PAPER_PICK row later showed limit-up-like return or evidence")

    chase_high = (signal_pct is not None and signal_pct >= 7.0) or (close_position_score is not None and close_position_score >= 0.70)
    if decision == "PAPER_PICK" and chase_high and (ret is None or ret < low_return_pct):
        tags.append("CHASE_HIGH_RISK")
        reasons.append("entry candidate was already high/near close high while result is pending, low, or loss")

    if row.get("broken_limit_risk") or row.get("broken_limit_risk_flags"):
        tags.append("FRIED_BOARD_RISK")
        reasons.append("candidate carried broken-limit risk fields")
    elif ret is not None and row.get("limitup_strength_tags") and not has_limit_up_evidence(row):
        tags.append("FRIED_BOARD_UNVERIFIED")
        reasons.append("limit-up related tags exist but no confirmed seal/limit-up result evidence")

    tag_set = set(tags)
    if decision == "PAPER_PICK" and ret is not None and (tag_set & FAILURE_TAGS):
        tags.append("FAILURE")
        tags.append("FALSE_POSITIVE")
    success_reasons, failure_reasons, missed_limitup_reasons, false_positive_reasons, false_negative_reasons = observation_reasons(row, tags, market_regime)
    attribution_categories, primary_attribution_category = attribution_categories_for_row(row, tags, failure_reasons, missed_limitup_reasons)

    return {
        "review_tags": sorted(set(tags)),
        "review_reasons": reasons,
        "market_regime": market_regime,
        "success_reason": success_reasons,
        "failure_reason": failure_reasons,
        "missed_limitup_reason": missed_limitup_reasons,
        "false_positive_reason": false_positive_reasons,
        "false_negative_reason": false_negative_reasons,
        "attribution_categories": attribution_categories,
        "primary_attribution_category": primary_attribution_category,
    }


def compact_diagnosis_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": row.get("date"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "decision": row.get("decision"),
        "d1_return": row.get("d1_return"),
        "market_regime": row.get("market_regime") or "UNKNOWN_MARKET_REGIME",
        "setup_type": row.get("setup_type"),
        "source_layers": row.get("source_layers") or [],
        "signal_pct": row.get("signal_pct"),
        "close_position_score": row.get("close_position_score"),
        "intraday_high_pct": row.get("intraday_high_pct"),
        "pullback_from_high_pct": row.get("pullback_from_high_pct"),
        "limitup_reason_strength": row.get("limitup_reason_strength"),
        "seal_order_strength": row.get("seal_order_strength"),
        "order_book_pressure": row.get("order_book_pressure"),
        "limitup_capture_score": row.get("limitup_capture_score"),
        "limitup_capture_profile": row.get("limitup_capture_profile"),
        "limitup_capture_reasons": row.get("limitup_capture_reasons") or [],
        "vei_phase_d_tags": row.get("vei_phase_d_tags") or [],
        "review_tags": row.get("review_tags") or [],
        "review_reasons": row.get("review_reasons") or [],
        "success_reason": row.get("success_reason") or [],
        "failure_reason": row.get("failure_reason") or [],
        "missed_limitup_reason": row.get("missed_limitup_reason") or [],
        "false_positive_reason": row.get("false_positive_reason") or [],
        "false_negative_reason": row.get("false_negative_reason") or [],
        "attribution_categories": row.get("attribution_categories") or [],
        "primary_attribution_category": row.get("primary_attribution_category"),
        "decision_record_line": row.get("decision_record_line"),
    }


def count_reasons(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts = Counter()
    for row in rows:
        for reason in listify(row.get(key)):
            counts[str(reason)] += 1
    return dict(sorted(counts.items()))


def count_filtered_reasons(rows: Iterable[Dict[str, Any]], key: str, excluded: set[str]) -> Dict[str, int]:
    counts = Counter()
    for row in rows:
        for reason in listify(row.get(key)):
            reason_s = str(reason)
            if reason_s not in excluded:
                counts[reason_s] += 1
    return dict(sorted(counts.items()))


def count_primary_categories(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter()
    for row in rows:
        category = row.get("primary_attribution_category")
        if category:
            counts[str(category)] += 1
    return dict(sorted(counts.items()))


def build_market_regime_performance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_regime: Dict[str, List[float]] = {}
    decisions: Dict[str, Counter] = {}
    for row in rows or []:
        regime = str(row.get("market_regime") or "UNKNOWN_MARKET_REGIME")
        decisions.setdefault(regime, Counter())[str(row.get("decision") or "UNKNOWN")] += 1
        if row.get("decision") != "PAPER_PICK":
            continue
        ret = first_available_return_pct(row)
        if ret is not None:
            by_regime.setdefault(regime, []).append(ret)
    return {
        regime: {
            "decisions": dict(sorted(decisions.get(regime, Counter()).items())),
            "paper_pick_returns": summarize_returns(by_regime.get(regime, [])),
        }
        for regime in sorted(set(decisions) | set(by_regime))
    }


def build_diagnosis_libraries(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    libraries = {
        "failure_library": [],
        "success_library": [],
        "false_positive_library": [],
        "false_negative_library": [],
        "missed_limitup_library": [],
    }
    for row in rows or []:
        tags = set(row.get("review_tags") or [])
        compact = compact_diagnosis_row(row)
        if "FAILURE" in tags:
            libraries["failure_library"].append(compact)
        if "SUCCESS" in tags:
            libraries["success_library"].append(compact)
        if "FALSE_POSITIVE" in tags:
            libraries["false_positive_library"].append(compact)
        if "FALSE_NEGATIVE" in tags:
            libraries["false_negative_library"].append(compact)
        if "MISSED_LIMITUP" in tags:
            libraries["missed_limitup_library"].append(compact)
    return libraries


def build_diagnosis_engine(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    failures = [row for row in rows or [] if "FAILURE" in set(row.get("review_tags") or [])]
    attribution_rows = [row for row in rows or [] if row.get("attribution_categories")]
    unclassified = [row for row in failures if not row.get("failure_reason")]
    unclassified_attribution = [row for row in failures if not row.get("attribution_categories")]
    return {
        "enabled": True,
        "version": DIAGNOSIS_VERSION,
        "attribution_version": ATTRIBUTION_VERSION,
        "attribution_categories": sorted(ATTRIBUTION_CATEGORIES),
        "rows_diagnosed": len(rows or []),
        "unclassified_failure_count": len(unclassified),
        "unclassified_attribution_count": len(unclassified_attribution),
        "success_reason_counts": count_reasons(rows or [], "success_reason"),
        "failure_reason_counts": count_reasons(rows or [], "failure_reason"),
        "missed_limitup_reason_counts": count_reasons(rows or [], "missed_limitup_reason"),
        "limitup_feature_gap_reason_counts": count_filtered_reasons(rows or [], "missed_limitup_reason", {"no_limitup_confirmation", "non_pick_later_showed_limitup_or_large_return"}),
        "false_positive_reason_counts": count_reasons(rows or [], "false_positive_reason"),
        "false_negative_reason_counts": count_reasons(rows or [], "false_negative_reason"),
        "attribution_category_counts": count_reasons(attribution_rows, "attribution_categories"),
        "primary_attribution_category_counts": count_primary_categories(attribution_rows),
    }


def build_forward_review(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tag_counts = Counter()
    by_decision = Counter()
    problem_rows = []
    pending_rows = []
    no_pick_rows = []
    research_rows = []
    paper_pick_returns = []
    paper_pick_count = 0
    paper_pick_limitup_capture_evidence_count = 0
    paper_pick_strong_limitup_capture_count = 0
    missed_limitup_with_limitup_capture_evidence_count = 0

    for row in rows or []:
        decision = row.get("decision") or "UNKNOWN"
        by_decision[decision] += 1
        for tag in row.get("review_tags") or []:
            tag_counts[tag] += 1
        if decision == "PAPER_PICK":
            paper_pick_count += 1
            if has_limitup_capture_evidence(row):
                paper_pick_limitup_capture_evidence_count += 1
            if str(row.get("limitup_capture_profile") or "") == "STRONG_LIMITUP_CAPTURE":
                paper_pick_strong_limitup_capture_count += 1
            ret = first_available_return_pct(row)
            if ret is not None:
                paper_pick_returns.append(ret)
        compact = compact_diagnosis_row(row)
        tags = set(row.get("review_tags") or [])
        if tags & {"MISSED_LIMITUP", "FALSE_NEGATIVE"} and has_limitup_capture_evidence(row):
            missed_limitup_with_limitup_capture_evidence_count += 1
        if tags & {"LOSS", "LOW_RETURN", "NO_LIMIT_UP_EVIDENCE", "CHASE_HIGH_RISK", "FRIED_BOARD_RISK", "FRIED_BOARD_UNVERIFIED"}:
            problem_rows.append(compact)
        if "RESULT_PENDING" in tags:
            pending_rows.append(compact)
        if decision == "NO_PICK":
            no_pick_rows.append(compact)
        if decision == "RESEARCH_CANDIDATE":
            research_rows.append(compact)

    libraries = build_diagnosis_libraries(rows or [])
    return {
        "review_tags_are_observation_only": True,
        "classification_thresholds": {"low_return_pct": 3.0, "limit_up_return_pct": 9.0, "chase_signal_pct": 7.0, "chase_close_position_score": 0.70},
        "rows_total": len(rows or []),
        "by_decision": dict(sorted(by_decision.items())),
        "tag_counts": dict(sorted(tag_counts.items())),
        "paper_pick_return_summary": summarize_returns(paper_pick_returns),
        "paper_pick_limitup_capture_evidence_count": paper_pick_limitup_capture_evidence_count,
        "paper_pick_strong_limitup_capture_count": paper_pick_strong_limitup_capture_count,
        "paper_pick_limitup_capture_rate_pct": round(paper_pick_limitup_capture_evidence_count / paper_pick_count * 100, 4) if paper_pick_count else None,
        "missed_limitup_with_limitup_capture_evidence_count": missed_limitup_with_limitup_capture_evidence_count,
        "diagnosis_engine": build_diagnosis_engine(rows or []),
        "market_regime_performance": build_market_regime_performance(rows or []),
        "failure_library": libraries["failure_library"],
        "success_library": libraries["success_library"],
        "false_positive_library": libraries["false_positive_library"],
        "false_negative_library": libraries["false_negative_library"],
        "missed_limitup_library": libraries["missed_limitup_library"],
        "problem_rows": problem_rows,
        "pending_rows": pending_rows,
        "no_pick_rows": no_pick_rows,
        "research_rows": research_rows,
    }


def _evidence_count(row: Dict[str, Any]) -> int:
    ids = row.get("evidence_ids") or row.get("evidence_id") or row.get("evidence_paths") or []
    if isinstance(ids, list):
        return len([x for x in ids if x])
    return 1 if str(ids).strip() else 0


def load_rule_freeze_snapshot() -> Dict[str, Any]:
    path = BASE / "rule_freeze_v0_1.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "UNAVAILABLE", "path": str(path)}
    return payload if isinstance(payload, dict) else {"status": "INVALID", "path": str(path)}


def build_registry_snapshots(rule: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    experiment_snapshot = {
        "source": "rule_freeze_v0_1.json",
        "rule_version": rule.get("rule_version"),
        "previous_rule_version": rule.get("previous_rule_version"),
        "status": rule.get("status"),
        "thresholds": rule.get("thresholds", {}),
        "freshness_gates": rule.get("freshness_gates", {}),
        "paper_only": rule.get("paper_only"),
        "no_trade": rule.get("no_trade"),
        "production_ready": rule.get("production_ready"),
        "allow_trade": rule.get("allow_trade"),
    }
    signal_snapshot = {
        "source": "rule_freeze_v0_1.json",
        "allowed_features_on_T_day": rule.get("allowed_features_on_T_day", []),
        "excluded_future_fields": rule.get("excluded_future_fields", []),
        "no_pick_rules": rule.get("no_pick_rules", []),
        "paper_pick_rules": rule.get("paper_pick_rules", []),
        "research_candidate_rules": rule.get("research_candidate_rules", []),
        "regulatory_hard_blocks": sorted((rule.get("regulatory_hard_blocks") or {}).keys()),
        "opportunity_hard_blocks": sorted((rule.get("opportunity_hard_blocks") or {}).keys()),
        "lifecycle": {
            "PROPOSED": "not production scoring",
            "TESTING": "paper/review only",
            "ACTIVE": "eligible only when listed in current T-day rules",
            "RETIRED": "not production scoring",
        },
    }
    return experiment_snapshot, signal_snapshot


def build_a_share_chain_scorecard(rows: List[Dict[str, Any]], rows_with_evidence: int) -> Dict[str, Any]:
    paper_rows = [row for row in rows or [] if row.get("decision") == "PAPER_PICK"]
    returns = [ret for ret in (first_available_return_pct(row) for row in paper_rows) if ret is not None]
    summary = summarize_returns(returns)
    false_positive_count = sum(1 for row in paper_rows if "FALSE_POSITIVE" in set(row.get("review_tags") or []))
    limitup_capture_count = sum(1 for row in paper_rows if has_limitup_capture_evidence(row))
    strong_limitup_capture_count = sum(1 for row in paper_rows if str(row.get("limitup_capture_profile") or "") == "STRONG_LIMITUP_CAPTURE")
    non_pick_rows = [row for row in rows or [] if row.get("decision") in {"NO_PICK", "RESEARCH_CANDIDATE"}]
    false_negative_count = sum(1 for row in non_pick_rows if "FALSE_NEGATIVE" in set(row.get("review_tags") or []))
    total_rows = len(rows or [])
    evidence_health = rows_with_evidence / total_rows if total_rows else 0.0
    win_rate = (summary.get("win_rate_pct") or 0.0) / 100
    avg_return = summary.get("avg_return_pct") or 0.0
    profitability = max(0.0, min(1.0, win_rate * 0.65 + max(min(avg_return, 10.0), -10.0) / 20 + 0.35)) if returns else 0.0
    false_positive_rate = false_positive_count / len(paper_rows) if paper_rows else 0.0
    false_negative_rate = false_negative_count / len(non_pick_rows) if non_pick_rows else 0.0
    signal_quality = max(0.0, 1.0 - false_positive_rate * 0.7 - false_negative_rate * 0.3)
    stability = max(0.0, min(1.0, win_rate if returns else 0.0))
    maintenance_cost = max(0.0, min(1.0, 1.0 - evidence_health))
    score = round((profitability * 0.35 + stability * 0.25 + signal_quality * 0.25 + evidence_health * 0.15) * 100, 2)
    if score >= 85:
        status = "ACTIVE"
    elif score >= 70:
        status = "OBSERVE"
    else:
        status = "RETIRED_CANDIDATE" if returns else "OBSERVE"
    return {
        "A_SHARE_CHAIN": {
            "score": score,
            "status": status,
            "status_is_recommendation_only": True,
            "profitability": round(profitability, 4),
            "stability": round(stability, 4),
            "signal_quality": round(signal_quality, 4),
            "false_positive_rate": round(false_positive_rate, 4),
            "false_negative_rate": round(false_negative_rate, 4),
            "maintenance_cost": round(maintenance_cost, 4),
            "evidence_health": round(evidence_health, 4),
            "paper_pick_limitup_capture_evidence_count": limitup_capture_count,
            "paper_pick_strong_limitup_capture_count": strong_limitup_capture_count,
            "paper_pick_limitup_capture_rate_pct": round(limitup_capture_count / len(paper_rows) * 100, 4) if paper_rows else None,
            "paper_pick_summary": summary,
        },
        "US_CHAIN": {"status": "RESEARCH_ONLY"},
        "CRYPTO_CHAIN": {"status": "RESEARCH_ONLY"},
    }


def build_forward_judge_scoreboard(rows: List[Dict[str, Any]], asof_ts: Optional[str] = None) -> Dict[str, Any]:
    horizons: Dict[str, Any] = {}
    total_forward_points = 0
    rows_with_evidence = 0
    model_score_fields_seen = set()

    for row in rows or []:
        if _evidence_count(row) > 0:
            rows_with_evidence += 1
        for field in ("model_score", "risk_panel_score"):
            if fnum(row.get(field)) is not None:
                model_score_fields_seen.add(field)

    for horizon, field in HORIZON_FIELDS.items():
        values_by_method: Dict[str, List[float]] = {}
        all_values: List[float] = []
        for row in rows or []:
            ret = fnum(row.get(field) or row.get(f"{horizon}_return_pct") or row.get(f"{horizon}_ret_pct"))
            if ret is None:
                continue
            method = method_name(row)
            values_by_method.setdefault(method, []).append(ret)
            all_values.append(ret)
        total_forward_points += len(all_values)
        horizons[horizon] = {
            "return_field": field,
            "overall": summarize_returns(all_values),
            "by_method": {m: summarize_returns(v) for m, v in sorted(values_by_method.items())},
        }

    blockers: List[str] = []
    if total_forward_points == 0:
        blockers.append("NO_FORWARD_RETURN_ROWS")
    decision = "PAPER_FORWARD_SCOREBOARD_READY" if not blockers else "NO_SCOREBOARD"
    rule_snapshot = load_rule_freeze_snapshot()
    experiment_snapshot, signal_snapshot = build_registry_snapshots(rule_snapshot)
    return {
        "rule_version": RULE_VERSION,
        "diagnosis_version": DIAGNOSIS_VERSION,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "asof_ts": asof_ts or dt.datetime.now().isoformat(timespec="seconds"),
        "paper_only": True,
        "no_trade": True,
        "trade_action_allowed": False,
        "decision": decision,
        "blockers": blockers,
        "guardrails": GUARDRAILS,
        "horizons": horizons,
        "evidence_traceability": {
            "rows_total": len(rows or []),
            "rows_with_evidence": rows_with_evidence,
            "traceability_required_before_weight_promotion": True,
        },
        "forward_review": build_forward_review(rows or []),
        "a_share_chain_scorecard": build_a_share_chain_scorecard(rows or [], rows_with_evidence),
        "experiment_registry_snapshot": experiment_snapshot,
        "signal_registry_snapshot": signal_snapshot,
        "model_score_fields_seen": sorted(model_score_fields_seen),
        "model_scores_used_as_win_rate": False,
    }


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    pending = ''
    pending_start = 0
    with path.open('r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            if pending and text.startswith('{'):
                print(json.dumps({'warning': 'SKIP_MALFORMED_JSONL_RECORD', 'path': str(path), 'line_start': pending_start, 'line_end': i - 1}, ensure_ascii=False), file=sys.stderr)
                pending = ''
                pending_start = 0
            candidate = pending + text if pending else text
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                pending = candidate
                if not pending_start:
                    pending_start = i
                continue
            obj['_line'] = pending_start or i
            if pending_start and pending_start != i:
                obj['_line_end'] = i
            rows.append(obj)
            pending = ''
            pending_start = 0
    if pending:
        print(json.dumps({'warning': 'SKIP_MALFORMED_JSONL_RECORD', 'path': str(path), 'line_start': pending_start, 'line_end': pending_start}, ensure_ascii=False), file=sys.stderr)
    return rows


def decision_symbol(decision: Dict[str, Any]) -> str:
    symbol = decision.get('symbol')
    if symbol and symbol != 'NO_PICK':
        return str(symbol).zfill(6)
    features = decision.get('features_used', {}).get('candidate_features', {})
    symbol = features.get('symbol') or features.get('code')
    return str(symbol).zfill(6) if symbol else ''


def decision_record_id(row: Dict[str, Any]) -> str:
    return '_'.join([
        str(row.get('date') or ''),
        str(row.get('asof_time') or ''),
        str(row.get('record_type', 'DECISION')),
        decision_symbol(row),
    ])


def has_decision_payload(row: Dict[str, Any]) -> bool:
    record_type = row.get('record_type', 'DECISION')
    return record_type == 'DECISION' or (record_type == 'CORRECTION' and bool(row.get('decision')))


def corrected_decision_ids(rows: List[Dict[str, Any]]) -> set[str]:
    return {
        str(row.get('correction_of'))
        for row in rows
        if row.get('record_type') == 'CORRECTION' and row.get('decision') and row.get('correction_of')
    }


def superseded_decision_keys(rows: List[Dict[str, Any]]) -> set[Tuple[str, str]]:
    keys = set()
    for row in rows:
        if not has_decision_payload(row):
            continue
        supersedes = (row.get('features_used') or {}).get('supersedes') or {}
        date = supersedes.get('date')
        symbol = supersedes.get('symbol')
        if date and symbol:
            keys.add((str(date), str(symbol).zfill(6)))
    return keys


def is_active_decision_record(row: Dict[str, Any], corrected_ids: set[str], superseded: set[Tuple[str, str]]) -> bool:
    if row.get('record_type', 'DECISION') not in ACTIVE_DECISION_RECORD_TYPES or not has_decision_payload(row):
        return False
    symbol = decision_symbol(row)
    if decision_record_id(row) in corrected_ids:
        return False
    if (str(row.get('date')), symbol) in superseded:
        return False
    return True


def merge_forward_ledger(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    corrected_ids = corrected_decision_ids(rows)
    superseded = superseded_decision_keys(rows)
    decisions = [
        r for r in rows
        if is_active_decision_record(r, corrected_ids, superseded)
        and r.get('decision') in {'PAPER_PICK', 'RESEARCH_CANDIDATE', 'NO_PICK'}
    ]
    fills = [r for r in rows if r.get('record_type') == 'RESULT_FILL' or (r.get('record_type') == 'CORRECTION' and not r.get('decision'))]
    out = []
    for decision in decisions:
        symbol = decision_symbol(decision)
        features = decision.get('features_used', {}).get('candidate_features', {})
        row = {
            'method': f"{decision.get('rule_version')}:{decision.get('decision')}",
            'date': decision.get('date'),
            'asof_time': decision.get('asof_time'),
            'symbol': symbol,
            'name': features.get('name'),
            'decision': decision.get('decision'),
            'entry_price': features.get('entry_price') or features.get('price') or features.get('signal_close'),
            'signal_pct': features.get('signal_pct') or features.get('pct_chg') or features.get('change_pct'),
            'close_position_score': features.get('close_position_score'),
            'turnover_rate': features.get('turnover_rate'),
            'volume_ratio': features.get('volume_ratio'),
            'net_inflow_main': features.get('net_inflow_main'),
            'rank': features.get('rank'),
            'board': features.get('board'),
            'market_regime': features.get('market_regime'),
            'market_breadth_up_pct': features.get('market_breadth_up_pct'),
            'market_limitups': features.get('market_limitups'),
            'market_bigups': features.get('market_bigups'),
            'setup_type': features.get('setup_type'),
            'source_layers': features.get('source_layers') or [],
            'underwater_recovery_score': features.get('underwater_recovery_score'),
            'full_universe_rank': features.get('full_universe_rank'),
            'full_universe_quote_count': features.get('full_universe_quote_count'),
            'full_universe_tradable_count': features.get('full_universe_tradable_count'),
            'full_universe_amount_pctile': features.get('full_universe_amount_pctile'),
            'full_universe_fund_pctile': features.get('full_universe_fund_pctile'),
            'candidate_evidence_status': features.get('candidate_evidence_status'),
            'candidate_evidence_missing_domains': features.get('candidate_evidence_missing_domains') or [],
            'enhanced_evidence_missing_domains': features.get('enhanced_evidence_missing_domains') or [],
            'experimental_evidence_missing_domains': features.get('experimental_evidence_missing_domains') or [],
            'final_score': features.get('final_score') or features.get('score'),
            'structured_score': features.get('structured_score'),
            'structured_score_mode': features.get('structured_score_mode'),
            'limitup_reason_strength': features.get('limitup_reason_strength'),
            'seal_order_strength': features.get('seal_order_strength'),
            'order_book_pressure': features.get('order_book_pressure'),
            'intraday_high_pct': features.get('intraday_high_pct'),
            'pullback_from_high_pct': features.get('pullback_from_high_pct'),
            'vei_phase_d_tags': features.get('vei_phase_d_tags') or [],
            'blocked_reasons': features.get('blocked_reasons') or [],
            'risk_penalty': features.get('risk_penalty'),
            'broken_limit_risk': features.get('broken_limit_risk'),
            'broken_limit_risk_flags': features.get('broken_limit_risk_flags') or [],
            'broken_limit_risk_reason': features.get('broken_limit_risk_reason'),
            'limitup_strength_tags': features.get('limitup_strength_tags') or [],
            'board_strength_tags': features.get('board_strength_tags') or [],
            'sealed_limit_up': features.get('sealed_limit_up'),
            'weak_close_risk': features.get('weak_close_risk'),
            'high_open_low_close_risk': features.get('high_open_low_close_risk'),
            'candidate_source': features.get('candidate_source'),
            'candidate_bundle_path': features.get('candidate_bundle_path'),
            'decision_reason': decision.get('decision_reason'),
            'evidence_paths': [decision.get('raw_data_snapshot_path')],
            'paper_only': decision.get('paper_only'),
            'no_trade': decision.get('no_trade'),
            'production_ready': decision.get('production_ready'),
            'decision_record_line': decision.get('_line'),
        }
        relevant_fills = sorted(
            [f for f in fills if f.get('date') == decision.get('date') and decision_symbol(f) == symbol],
            key=lambda r: r.get('_line', 0),
        )
        for fill in relevant_fills:
            for d_field, t_field in LEDGER_HORIZON_MAP.items():
                value = fill.get(t_field)
                if value is not None:
                    row[d_field] = float(value) * 100
            evidence = fill.get('result_source_evidence') or {}
            if evidence.get('evidence_path'):
                row.setdefault('evidence_paths', []).append(evidence['evidence_path'])
            for key in ('exit_date', 'exit_high', 'exit_close', 'source', 'return_formula'):
                if evidence.get(key) is not None:
                    row[f'result_{key}'] = evidence.get(key)
        row.update(classify_forward_observation(row))
        out.append(row)
    return out


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=False) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ledger', default=str(BASE / 'forward_paper_ledger_v0_1.jsonl'))
    parser.add_argument('--output', default=str(BASE / 'forward_scoreboard' / 'stock_forward_observation_scoreboard.jsonl'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    ledger_rows = load_jsonl(Path(args.ledger))
    forward_rows = merge_forward_ledger(ledger_rows)
    board = build_forward_judge_scoreboard(forward_rows)
    board['source_ledger'] = args.ledger
    board['forward_rows'] = forward_rows
    board['append_only_scoreboard'] = True
    print(json.dumps(board, ensure_ascii=False, indent=2))
    if not args.dry_run:
        append_jsonl(Path(args.output), board)


if __name__ == '__main__':
    main()
