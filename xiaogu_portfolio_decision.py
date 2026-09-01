"""The sole production owner for six-state portfolio decisions."""
from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Dict

from xiaogu_core_alpha import COST_MODEL_VERSION, build_core_alpha
from xiaogu_forward_eligibility import eligibility_blockers
from xiaogu_forward_features import build_feature_vector, validate_evidence_identity
from xiaogu_forward_snapshot import CanonicalSnapshot, pit_record_audit, validate_and_build_canonical_snapshot
from xiaogu_research_context import build_integrated_research_context

PORTFOLIO_STATES = ("WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL")
TRADE_ACTIONS = ("BUY", "HOLD", "REDUCE", "SELL")
POSITION_STATES = ("FLAT", "LONG")
HELD_ACTIONS = {"BUY", "HOLD", "REDUCE"}
MAX_HOLDING_DAYS = 5
PAPER_OBSERVATION_CONTRACT_VERSION = "paper_observation_v1"
PAPER_OBSERVATION_STATE = "OBSERVED"
PAPER_POSITION_STATES = ("PAPER_FLAT", "PAPER_LONG")
PAPER_REVIEW_ACTIONS = ("PAPER_HOLD", "PAPER_SELL")
QUANTITY_MODEL = False
REDUCE_UNSUPPORTED = "REDUCE_UNSUPPORTED"
PAPER_REDUCE_UNSUPPORTED = "PAPER_REDUCE_UNSUPPORTED"
PRODUCTION_GATE_VERSION = "production_gate_v1"
RESEARCH_ONLY_POSITIVE_SIGNALS = {
    "CAPITAL_ACCUMULATION",
    "CAPITAL_CONVERGENCE",
    "HOT_MONEY",
    "INSTITUTION_PRESENCE",
    "FUTURE_BUYER",
}
DECISION_HARD_GATES = (
    "TRUSTED_CANONICAL",
    "DB_VERIFIED",
    "FRESH_DATA",
    "DATA_VALID",
    "TRADABLE",
    "RISK_PASS",
    "EXECUTION_PASS",
    "ALPHA_VALIDATED",
    "PROFIT_WINDOW_MODEL",
    "OOS_PASS",
    "PROBABILITY_SEPARATION_PASS",
    "MONOTONICITY_PASS",
    "BASELINE_INCREMENT_PASS",
    "NEGATIVE_EVIDENCE_CLEAR",
)
DATA_VALID_REASONS = {"INVALID_SYMBOL", "INVALID_PRICE", "INCOMPLETE_MARKET_DATA", "MISSING_DATA"}
TRADABLE_REASONS = {"HALTED", "UNBUYABLE", "REGULATORY_HARD_RISK", "ACCOUNT_CONSTRAINT"}


def _decision_id(snapshot: Dict[str, Any], state: str, as_of: datetime | None) -> str:
    signal_time = snapshot.get("signal_time") or snapshot.get("source_time") or ""
    identity = f"{snapshot.get('snapshot_id', '')}|{snapshot.get('trade_date', '')}|{signal_time}|{snapshot['symbol']}|{state}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _paper_signal_id(decision_id: str) -> str:
    identity = f"{decision_id}|{PAPER_OBSERVATION_CONTRACT_VERSION}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _at_least(value: Any, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _evidence_identity(item: Dict[str, Any]) -> tuple[str, str, str] | None:
    return validate_evidence_identity(item)


def _confirmation_status(item: Dict[str, Any]) -> str:
    status = str(
        item.get("confirmation_status")
        or item.get("evidence_status")
        or item.get("status")
        or ""
    ).upper()
    if status in {"UNKNOWN", "UNCONFIRMED", "MISSING"}:
        return status or "UNKNOWN"
    if item.get("observed") is False:
        return "UNCONFIRMED"
    if status in {"CONFIRMED", "OBSERVED", "EVIDENCE_BACKED"}:
        return "CONFIRMED" if status == "CONFIRMED" else status
    if item.get("observed") is True:
        return "OBSERVED"
    return "UNKNOWN"


def _evidence_confirmed(item: Any, as_of: datetime | None = None) -> bool:
    """Strict confirmation + strict PIT + strict (source_id, event_id, mechanism) identity."""
    if not isinstance(item, dict):
        return False
    identity = validate_evidence_identity(item)
    if identity is None or item.get("evidence_identity") is None:
        return False
    source_id, event_id, mechanism = identity
    if not (source_id and event_id and mechanism):
        return False
    observed_at = str(item.get("observed_at") or "").strip()
    available_at = str(item.get("available_at") or "").strip()
    if not observed_at or not available_at:
        return False
    as_of_value = as_of if as_of is not None else item.get("as_of")
    if as_of_value in (None, ""):
        return False
    audit = pit_record_audit(item, as_of_value)
    if audit.get("pit_status") != "OK":
        return False
    if str(audit.get("exclusion_reason") or "").strip():
        return False
    status = _confirmation_status(item)
    if status not in {"CONFIRMED", "OBSERVED", "EVIDENCE_BACKED"}:
        return False
    return True


def _nested_evidence_items(container: Any) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    if isinstance(container, dict):
        evidence = container.get("evidence")
        if isinstance(evidence, list):
            items.extend(item for item in evidence if isinstance(item, dict))
        for value in container.values():
            if value is evidence:
                continue
            if isinstance(value, dict):
                items.extend(_nested_evidence_items(value))
            elif isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict) and "source_id" in item)
    elif isinstance(container, list):
        items.extend(item for item in container if isinstance(item, dict) and "source_id" in item)
    return items


def _capital_evidence_items(features: Dict[str, Any]) -> list[Dict[str, Any]]:
    capital = features.get("CAPITAL") or {}
    items = []
    for key in ("institution_behavior", "main_force_behavior", "hot_money_behavior"):
        behavior = capital.get(key) or {}
        for item in behavior.get("evidence") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


DISTRIBUTION_MECHANISMS = frozenset({"distribution_risk"})
BLOCKER_REQUIRED_MECHANISMS = {
    "CONFIRMED_DISTRIBUTION": DISTRIBUTION_MECHANISMS,
    "CONFIRMED_SUPPLY_REVERSAL": {"supply_reversal"},
    "BUYER_EXHAUSTION_OR_CLIMAX": {"buyer_exhaustion"},
    "REPRICING_COMPLETED": {"completed_repricing", "repricing_completion"},
    "TRADINGAGENTS_CONTRADICTION": {"critical_contradiction", "contradiction"},
}


def build_confirmed_negative_blocker(
    blocker: str,
    item: Any,
    *,
    as_of: datetime | None = None,
    required_mechanisms: set[str] | None = None,
) -> Dict[str, Any] | None:
    """Confirm one blocker only against its own evidence identity, PIT, and mechanism."""
    if not isinstance(item, dict):
        return None
    identity = validate_evidence_identity(item)
    if identity is None or item.get("evidence_identity") is None:
        return None
    source_id, event_id, mechanism = identity
    allowed = required_mechanisms if required_mechanisms is not None else BLOCKER_REQUIRED_MECHANISMS.get(blocker)
    if not allowed or mechanism not in allowed:
        return None
    if not _evidence_confirmed(item, as_of=as_of):
        return None
    as_of_value = as_of if as_of is not None else item.get("as_of")
    audit = pit_record_audit(item, as_of_value)
    if audit.get("pit_status") != "OK":
        return None
    return {
        "blocker": blocker,
        "kind": "CONFIRMED_NEGATIVE_PRODUCTION_BLOCKER",
        "status": "CONFIRMED",
        "evidence_identity": list(identity),
        "source_id": source_id,
        "event_id": event_id,
        "mechanism": mechanism,
        "observed_at": item.get("observed_at") or "",
        "available_at": item.get("available_at") or "",
        "as_of": audit.get("as_of") or str(as_of_value or ""),
        "confidence": item.get("confidence"),
        "pit_status": audit.get("pit_status"),
        "time_basis": audit.get("time_basis"),
        "source_time": audit.get("source_time"),
        "primary_event_field": audit.get("primary_event_field"),
        "availability_field": audit.get("availability_field"),
    }


def collect_production_negative_evidence(
    alpha: Dict[str, Any],
    features: Dict[str, Any],
    research: Dict[str, Any],
    as_of: datetime | None = None,
) -> list[Dict[str, Any]]:
    """Confirmed negative evidence only. UNKNOWN/None/MISSING never become blockers."""
    records = []
    capital_items = _capital_evidence_items(features)
    distribution_items = [
        item for item in capital_items
        if str(item.get("mechanism") or "").strip() in DISTRIBUTION_MECHANISMS
    ]
    for item in distribution_items:
        record = build_confirmed_negative_blocker("CONFIRMED_DISTRIBUTION", item, as_of=as_of)
        if record:
            records.append(record)
    supply_items = _nested_evidence_items(features.get("SUPPLY") or {})
    for item in supply_items:
        record = build_confirmed_negative_blocker("CONFIRMED_SUPPLY_REVERSAL", item, as_of=as_of)
        if record:
            records.append(record)
    exhaustion_items = [
        item for item in (_nested_evidence_items(features.get("REFLEXIVITY") or {}) + capital_items)
        if str(item.get("mechanism") or "").strip() == "buyer_exhaustion"
    ]
    buyer_exhaustion = alpha.get("buyer_exhaustion")
    if isinstance(buyer_exhaustion, dict):
        exhaustion_items.append(buyer_exhaustion)
    for item in exhaustion_items:
        record = build_confirmed_negative_blocker("BUYER_EXHAUSTION_OR_CLIMAX", item, as_of=as_of)
        if record:
            records.append(record)
    completion = alpha.get("repricing_completion") or {}
    completion_items = _nested_evidence_items(completion)
    if isinstance(completion, dict) and completion.get("source_id"):
        completion_items.append(completion)
    for item in completion_items:
        record = build_confirmed_negative_blocker("REPRICING_COMPLETED", item, as_of=as_of)
        if record:
            records.append(record)
    contradiction = (research or {}).get("contradiction") or (research or {}).get("integrated") or {}
    contradiction_items = _nested_evidence_items(contradiction)
    if isinstance(contradiction, dict):
        contradiction_items.append(contradiction)
    for item in contradiction_items:
        record = build_confirmed_negative_blocker("TRADINGAGENTS_CONTRADICTION", item, as_of=as_of)
        if record:
            records.append(record)
    unique = []
    seen = set()
    for record in records:
        key = (record["blocker"], tuple(record.get("evidence_identity") or ()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def evaluate_production_gates(
    snapshot: Dict[str, Any],
    *,
    features: Dict[str, Any],
    alpha: Dict[str, Any],
    research: Dict[str, Any],
    account: Dict[str, Any] | None = None,
    as_of: datetime | None = None,
    minimum_required_return: float = 0.0,
) -> Dict[str, Any]:
    """Sole production hard-gate evaluator. Decision must not reimplement these checks."""
    failed_gates: list[str] = []
    hard_blockers: list[str] = []
    production_blockers: list[str] = []
    warnings: list[str] = []
    operational = eligibility_blockers(snapshot, account=account, as_of=as_of)
    if not isinstance(snapshot, CanonicalSnapshot) or snapshot.get("trusted_snapshot") is not True:
        failed_gates.append("TRUSTED_CANONICAL")
        hard_blockers.append("NO_PRODUCTION_SNAPSHOT")
    if not snapshot.get("decision_clock"):
        failed_gates.append("DB_VERIFIED")
        production_blockers.append("DB_NOT_VERIFIED")
    if "STALE_DATA" in operational:
        failed_gates.append("FRESH_DATA")
        hard_blockers.append("STALE_DATA")
    data_valid_hits = [reason for reason in operational if reason in DATA_VALID_REASONS]
    if data_valid_hits:
        failed_gates.append("DATA_VALID")
        hard_blockers.extend(data_valid_hits)
    tradable_hits = [reason for reason in operational if reason in TRADABLE_REASONS]
    if tradable_hits:
        failed_gates.append("TRADABLE")
        hard_blockers.extend(tradable_hits)
    risk = features.get("RISK") or {}
    if risk.get("thesis_invalidated") is True:
        failed_gates.append("RISK_PASS")
        production_blockers.append("THESIS_INVALIDATED")
        hard_blockers.append("THESIS_INVALIDATED")
    if risk.get("regulatory_hard_risk") is True or (
        risk.get("event_risk") is not None and float(risk["event_risk"]) >= 0.80
    ):
        if "RISK_PASS" not in failed_gates:
            failed_gates.append("RISK_PASS")
        production_blockers.append("RISK_BLOCKED")
        hard_blockers.append("RISK_BLOCKED")
    execution = features.get("EXECUTION") or {}
    if execution.get("buyable") is False:
        failed_gates.append("EXECUTION_PASS")
        production_blockers.append("EXECUTION_IMPOSSIBLE")
        hard_blockers.append("EXECUTION_IMPOSSIBLE")
    elif execution.get("execution_feasibility") is not None and execution["execution_feasibility"] <= 0:
        failed_gates.append("EXECUTION_PASS")
        production_blockers.append("SEVERE_EXECUTION_RISK")
        hard_blockers.append("SEVERE_EXECUTION_RISK")
    elif "SEVERE_LIQUIDITY_ISSUE" in operational:
        failed_gates.append("EXECUTION_PASS")
        hard_blockers.append("SEVERE_LIQUIDITY_ISSUE")
    calibration = alpha.get("profit_window_calibration") or alpha.get("model_validation") or {}
    gates = calibration.get("production_gates") if isinstance(calibration.get("production_gates"), dict) else {}
    model_status = alpha.get("model_status")
    if model_status != "VALIDATED":
        failed_gates.append("ALPHA_VALIDATED")
        production_blockers.append("ALPHA_NOT_VALIDATED")
    if model_status == "MODEL_ARTIFACT_MISMATCH" or str(calibration.get("reason") or "").startswith("MODEL_ARTIFACT_MISMATCH"):
        if "ALPHA_VALIDATED" not in failed_gates:
            failed_gates.append("ALPHA_VALIDATED")
        production_blockers.append("MODEL_ARTIFACT_MISMATCH")
    expected_net_profit = alpha.get("expected_net_profit_window")
    if model_status != "VALIDATED" or expected_net_profit is None:
        failed_gates.append("PROFIT_WINDOW_MODEL")
        production_blockers.append("PROFIT_WINDOW_MODEL_UNVALIDATED")
    if minimum_required_return > 0 and (expected_net_profit is None or expected_net_profit < minimum_required_return):
        if "PROFIT_WINDOW_MODEL" not in failed_gates:
            failed_gates.append("PROFIT_WINDOW_MODEL")
        production_blockers.append("PROFIT_WINDOW_BELOW_MINIMUM")
    if gates.get("oos_pass") is False:
        failed_gates.append("OOS_PASS")
        production_blockers.append("OOS_FAIL")
    if gates.get("probability_separation") is False:
        failed_gates.append("PROBABILITY_SEPARATION_PASS")
        production_blockers.append("PROBABILITY_SEPARATION_FAIL")
    if gates.get("monotonicity") is False:
        failed_gates.append("MONOTONICITY_PASS")
        production_blockers.append("MONOTONICITY_FAIL")
    if gates.get("full_alpha_baseline_increment") is False:
        failed_gates.append("BASELINE_INCREMENT_PASS")
        production_blockers.append("BASELINE_INCREMENT_FAIL")
    negative_evidence = collect_production_negative_evidence(alpha, features, research, as_of=as_of)
    if negative_evidence:
        failed_gates.append("NEGATIVE_EVIDENCE_CLEAR")
        production_blockers.extend(record["blocker"] for record in negative_evidence)
    if alpha.get("reflexivity_break") is not None and alpha["reflexivity_break"] >= 0.70:
        production_blockers.append("REFLEXIVITY_BREAK")
    remaining_operational = [
        reason for reason in operational
        if reason not in hard_blockers and reason not in production_blockers
    ]
    hard_blockers.extend(remaining_operational)
    hard_blockers = list(dict.fromkeys(reason for reason in hard_blockers if reason))
    production_blockers = list(dict.fromkeys(reason for reason in production_blockers if reason))
    failed_gates = [gate for gate in DECISION_HARD_GATES if gate in failed_gates]
    blockers = list(dict.fromkeys(hard_blockers + production_blockers))
    passed = not failed_gates and not blockers
    return {
        "passed": passed,
        "failed_gates": failed_gates,
        "blockers": blockers,
        "hard_blockers": hard_blockers,
        "production_blockers": production_blockers,
        "warnings": warnings,
        "gate_version": PRODUCTION_GATE_VERSION,
        "negative_evidence": negative_evidence,
    }


def _exit_reason(
    alpha: Dict[str, Any],
    features: Dict[str, Any],
    snapshot: Dict[str, Any],
    account: Dict[str, Any] | None = None,
) -> str:
    if (account or {}).get("profit_window_hit"):
        return "PROFIT_WINDOW_HIT"
    risk = features["RISK"]
    if risk["thesis_invalidated"] or snapshot["raw"].get("thesis_invalidated"):
        return "BUSINESS_OR_INDUSTRY_THESIS_BROKEN"
    if risk["regulatory_hard_risk"] or _at_least(risk.get("event_risk"), 0.80):
        return "RISK_EVENT"
    if (
        _at_least(features["MARKET"].get("price_strength"), 0.80)
        and _at_least(features["MARKET"].get("attention"), 0.85)
    ):
        return "REPRICING_COMPLETED"
    if features["PRICING_GAP"]["score"] is not None and features["PRICING_GAP"]["score"] <= 0.10:
        return "PRICING_GAP_CLOSED"
    if _at_least(features["BUSINESS"].get("valuation"), 0.90):
        return "VALUATION_EXCESS"
    return "THESIS_INTACT"


def _paper_observation(
    snapshot: CanonicalSnapshot,
    features: Dict[str, Any],
    alpha: Dict[str, Any],
    *,
    decision_id: str,
    decision_state: str,
    position_state: str,
) -> Dict[str, Any]:
    """Wrap the current production decision as a paper-only observation."""
    market = features.get("MARKET") or {}
    price_strength = market.get("price_strength")
    capital = features.get("CAPITAL") or {}
    research_capital = alpha.get("capital_convergence") or {}
    research = {
        "research_only": True,
        "capital_flow_ratio": capital.get("capital_flow_ratio"),
        "capital_persistence": capital.get("fund_flow_persistence"),
        "capital_acceleration": capital.get("fund_flow_acceleration"),
        "institution": research_capital.get("institution"),
        "main_force": research_capital.get("main_force"),
        "hot_money": research_capital.get("hot_money"),
        "supply": alpha.get("supply_absorption"),
        "repricing": alpha.get("repricing_state"),
        "future_buyer": alpha.get("future_buyer_capacity"),
    }
    return {
        "status": "PAPER_OBSERVATION",
        "paper_signal_id": _paper_signal_id(decision_id),
        "decision_id": decision_id,
        "snapshot_id": snapshot.get("snapshot_id"),
        "original_snapshot_id": snapshot.get("snapshot_id"),
        "lineage_id": snapshot.get("lineage_id"),
        "symbol": snapshot.get("symbol"),
        "signal_time": snapshot.get("signal_time") or snapshot.get("source_time"),
        "reference_price": snapshot.get("price"),
        "paper_observation_state": PAPER_OBSERVATION_STATE,
        "signal_reason": "CURRENT_PRODUCTION_DECISION",
        "alpha_name": "price_strength",
        "alpha_version": alpha.get("alpha_version"),
        "alpha_status": alpha.get("model_status") or "DATA_INSUFFICIENT",
        "price_strength": price_strength,
        "validated_probability": alpha.get("profit_window_probability"),
        "production_decision_state": decision_state,
        "production_buy": "BLOCKED",
        "position_state": position_state,
        "paper_position_state": "PAPER_FLAT",
        "research_overlay": research,
        "paper_only": True,
        "live_order": False,
        "model_version": "v4",
        "feature_version": alpha.get("feature_version"),
        "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
        "cost_model_version": COST_MODEL_VERSION,
        "paper_observation_contract_version": PAPER_OBSERVATION_CONTRACT_VERSION,
    }


def evaluate_candidate_bundle(
    canonical: Dict[str, Any],
    *,
    portfolio_state: str = "WATCH",
    account: Dict[str, Any] | None = None,
    minimum_required_return: float = 0.0,
    as_of: datetime | None = None,
    position_state: str | None = None,
    previous_action: str | None = None,
) -> Dict[str, Any]:
    """Evaluate one T-day snapshot; this is the only function allowed to emit states."""
    if portfolio_state not in PORTFOLIO_STATES:
        raise ValueError(f"INVALID_PORTFOLIO_STATE:{portfolio_state}")
    if position_state is None:
        position_state_unavailable = True
        evaluation_position_state = None
    else:
        position_state_unavailable = False
        evaluation_position_state = position_state
    if evaluation_position_state is not None and evaluation_position_state not in POSITION_STATES:
        raise ValueError(f"INVALID_POSITION_STATE:{position_state}")
    snapshot = (
        canonical
        if isinstance(canonical, CanonicalSnapshot)
        else validate_and_build_canonical_snapshot(
            canonical,
            trade_date=str(canonical.get("trade_date") or ""),
            source=str(canonical.get("source") or "eastmoney_api_scan_v2"),
            source_time=str(canonical.get("source_time") or canonical.get("as_of") or ""),
            decision_time=as_of,
        )
    )
    features = build_feature_vector(snapshot)
    research = build_integrated_research_context(snapshot, features)
    alpha = build_core_alpha(
        features,
        industry={},
        company={},
        capital={},
        integrated={},
        future_buyer_map=None,
    )
    gate_result = evaluate_production_gates(
        snapshot,
        features=features,
        alpha=alpha,
        research=research,
        account=account,
        as_of=as_of,
        minimum_required_return=minimum_required_return,
    )
    hard_blockers = list(gate_result["hard_blockers"])
    production_blockers = list(gate_result["production_blockers"])
    all_blockers = list(gate_result["blockers"])
    held = evaluation_position_state == "LONG"
    position_review = bool((account or {}).get("position_review"))

    if position_state_unavailable:
        state, reason = "WATCH", "POSITION_STATE_UNAVAILABLE"
    elif held:
        holding_days = (account or {}).get("holding_days")
        if int(0 if holding_days is None else holding_days) >= MAX_HOLDING_DAYS:
            state, reason = "SELL", "MAX_HOLDING_BOUNDARY_CLOSED"
        else:
            exit_reason = _exit_reason(alpha, features, snapshot, account)
            if exit_reason != "THESIS_INTACT":
                state = "SELL" if exit_reason in {"BUSINESS_OR_INDUSTRY_THESIS_BROKEN", "RISK_EVENT", "REPRICING_COMPLETED", "PRICING_GAP_CLOSED", "VALUATION_EXCESS"} else "REDUCE"
                reason = exit_reason
            elif (
                (alpha["downside_risk"] is not None and alpha["confidence"] is not None and alpha["downside_risk"] >= alpha["confidence"])
                or any(
                blocker in production_blockers
                for blocker in (
                    "REFLEXIVITY_BREAK",
                    "RISK_BLOCKED",
                    "EXECUTION_IMPOSSIBLE",
                    "SEVERE_EXECUTION_RISK",
                )
                )
                or gate_result["negative_evidence"]
            ):
                state, reason = "REDUCE", "REPRICING_RISK_OR_CONFIRMATION_DETERIORATED"
            else:
                state, reason = "HOLD", "REPRICING_THESIS_STILL_VALID"
    elif hard_blockers:
        state, reason = "WATCH", "HARD_CONSTRAINT:" + ";".join(hard_blockers)
    elif not production_blockers:
        state, reason = "BUY", "REPRICING_READINESS_CONFIRMED"
    else:
        # Diagnostic research measurements may explain a candidate, but they
        # never decide readiness or stand in for model probability.
        state, reason = "READY", "BUY_BLOCKED_PENDING_HARD_GATE:" + ";".join(production_blockers)

    if state == "BUY":
        state, reason = "READY", "PRODUCTION_BUY_BLOCKED:PAPER_OBSERVATION_ONLY"

    if position_review and state == "BUY":
        state, reason = "HOLD", "PRODUCTION_BUY_BLOCKED:POSITION_REVIEW"
    decision_id = str((account or {}).get("decision_id") or "") if position_review and (account or {}).get("decision_id") else _decision_id(snapshot, state, as_of)
    observation = None
    if (not position_review) and state in {"WATCH", "READY"} and evaluation_position_state == "FLAT" and not position_state_unavailable:
        observation = _paper_observation(
            snapshot,
            features,
            alpha,
            decision_id=decision_id,
            decision_state=state,
            position_state=evaluation_position_state,
        )
    position_state_after = (
        None
        if position_state_unavailable
        else "FLAT" if state in {"WATCH", "READY", "SELL"} else "LONG"
    )
    # REDUCE is an abstract action only. Without a quantity model it never closes the position.
    return {
        "decision_id": decision_id,
        "position_id": (account or {}).get("position_id"),
        "state": state,
        "action": state if state in TRADE_ACTIONS else None,
        "position_state": position_state_after,
        "position_state_before": position_state,
        "position_state_after": position_state_after,
        "previous_action": previous_action,
        "original_snapshot_id": (account or {}).get("original_snapshot_id") or (None if position_review else snapshot["snapshot_id"]),
        "review_snapshot_id": (account or {}).get("review_snapshot_id"),
        "review_trade_date": (account or {}).get("review_trade_date"),
        "decision_clock": as_of.isoformat() if as_of else None,
        "gate_version": gate_result["gate_version"],
        "gate_result": {
            "passed": gate_result["passed"],
            "failed_gates": list(gate_result["failed_gates"]),
            "blockers": list(gate_result["blockers"]),
            "warnings": list(gate_result["warnings"]),
        },
        "failed_gates": list(gate_result["failed_gates"]),
        "production_blockers": production_blockers,
        "production_negative_evidence": list(gate_result["negative_evidence"]),
        "position_state_status": "UNAVAILABLE" if position_state_unavailable else "AVAILABLE",
        "holding_days": int(0 if (account or {}).get("holding_days") is None else (account or {}).get("holding_days")),
        "trade_status": "CLOSED" if state == "SELL" else "OPEN" if state in TRADE_ACTIONS else "NOT_OPEN",
        "buy_status": "BUY_ALLOWED" if state == "BUY" else "BUY_BLOCKED",
        "symbol": snapshot["symbol"],
        "snapshot_id": snapshot["snapshot_id"],
        "lineage_id": snapshot["lineage_id"],
        "trade_date": snapshot["trade_date"],
        "reason": reason,
        "decision_owner": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
        "allowed_states": list(PORTFOLIO_STATES),
        "portfolio_state_before": portfolio_state,
        "canonical_snapshot": snapshot,
        "feature_vector": features,
        "research_context": research,
        "core_alpha": alpha,
        "repricing_readiness": alpha["readiness"],
        "repricing_risk": {
            "downside": alpha["downside_risk"],
            "reflexivity_break": alpha["reflexivity_break"],
            "completion": alpha["repricing_completion"],
            "blockers": all_blockers,
        },
        "thesis": alpha["thesis"],
        "future_buyer_map": research["future_buyer_map"],
        "supply": research["supply"],
        "pricing_gap_context": research["pricing_gap"],
        "minimum_required_return": minimum_required_return,
        "signal_time": snapshot.get("signal_time") or snapshot.get("source_time") or "",
        "reference_price": snapshot.get("price"),
        "reference_price_source": "canonical_snapshot.price",
        "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
        "alpha_version": alpha.get("alpha_version"),
        "feature_version": alpha.get("feature_version"),
        "model_version": alpha.get("model_id"),
        "cost_model_version": COST_MODEL_VERSION,
        "paper_observation": observation,
        "capital_convergence": alpha.get("capital_convergence"),
        "future_demand": alpha.get("future_demand"),
        "business_quality": alpha.get("business_quality"),
        "pricing_gap": alpha.get("pricing_gap"),
        "future_buyer_capacity": alpha.get("future_buyer_capacity"),
        "repricing_state": alpha.get("repricing_state"),
        "repricing_evidence_score": alpha.get("repricing_evidence_score"),
        "profit_window_probability": alpha.get("profit_window_probability"),
        "expected_max_profit_5d": alpha.get("expected_max_profit_5d"),
        "expected_mae_5d": alpha.get("expected_mae_5d"),
        "downside_risk": alpha.get("downside_risk"),
        "confidence": alpha.get("confidence"),
        "invalidation": (alpha.get("thesis") or {}).get("invalidation", []),
        "state_transition_timestamp": (as_of.isoformat() if as_of else snapshot.get("source_time") or ""),
        "state_transition": {
            "previous_state": portfolio_state,
            "new_state": state,
            "reason": reason,
            "timestamp": as_of.isoformat() if as_of else snapshot.get("source_time") or "",
        },
        "portfolio_state": {
            "thesis_status": "BROKEN" if features["RISK"]["thesis_invalidated"] else "INTACT",
            "accumulation_status": features["CAPITAL"].get("accumulation_phase", "UNOBSERVED"),
            "repricing_status": alpha.get("repricing_state", "UNKNOWN"),
            "average_cost": (account or {}).get("average_cost"),
            "unrealized_return": (account or {}).get("unrealized_return"),
            "realized_return": (account or {}).get("realized_return"),
            "max_drawdown": (account or {}).get("max_drawdown"),
        },
    }
