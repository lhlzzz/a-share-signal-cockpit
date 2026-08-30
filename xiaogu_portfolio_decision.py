"""The sole production owner for six-state portfolio decisions."""
from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Dict

from xiaogu_core_alpha import build_core_alpha
from xiaogu_forward_eligibility import eligibility_blockers
from xiaogu_forward_features import build_feature_vector
from xiaogu_forward_snapshot import CanonicalSnapshot, validate_and_build_canonical_snapshot
from xiaogu_research_context import build_integrated_research_context

PORTFOLIO_STATES = ("WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL")
TRADE_ACTIONS = ("BUY", "HOLD", "REDUCE", "SELL")
POSITION_STATES = ("FLAT", "LONG")
HELD_ACTIONS = {"BUY", "HOLD", "REDUCE"}
MAX_HOLDING_DAYS = 5
DECISION_HARD_GATES = (
    "TRUSTED_CANONICAL", "DB_VERIFIED", "FRESH_DATA", "DATA_VALID", "TRADABLE",
    "RISK_PASS", "EXECUTION_PASS", "ALPHA_VALIDATED", "OOS_PASS",
    "PROBABILITY_SEPARATION_PASS", "MONOTONICITY_PASS", "BASELINE_INCREMENT_PASS",
)


def _decision_id(snapshot: Dict[str, Any], state: str, as_of: datetime | None) -> str:
    signal_time = snapshot.get("signal_time") or snapshot.get("source_time") or ""
    clock = as_of.isoformat() if as_of else ""
    identity = f"{snapshot.get('snapshot_id', '')}|{snapshot.get('trade_date', '')}|{signal_time}|{clock}|{snapshot['symbol']}|{state}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _at_least(value: Any, threshold: float) -> bool:
    return value is not None and float(value) >= threshold


def _blockers(alpha: Dict[str, Any], features: Dict[str, Any], research: Dict[str, Any]) -> list[str]:
    blockers = []
    del research
    # Missing capital/supply/pricing-gap/future-buyer evidence is a model
    # input, not a BUY hard gate. Only operational failure, unvalidated
    # alpha, and confirmed negative evidence can block here.
    calibration = alpha.get("profit_window_calibration") or alpha.get("model_validation") or {}
    gates = calibration.get("production_gates") if isinstance(calibration.get("production_gates"), dict) else {}
    model_status = alpha.get("model_status")
    if model_status != "VALIDATED":
        blockers.append("ALPHA_NOT_VALIDATED")
    if model_status == "MODEL_ARTIFACT_MISMATCH" or calibration.get("reason", "").startswith("MODEL_ARTIFACT_MISMATCH"):
        blockers.append("MODEL_ARTIFACT_MISMATCH")
    if gates.get("oos_pass") is False:
        blockers.append("OOS_FAIL")
    if gates.get("probability_separation") is False:
        blockers.append("PROBABILITY_SEPARATION_FAIL")
    if gates.get("monotonicity") is False:
        blockers.append("MONOTONICITY_FAIL")
    if gates.get("full_alpha_baseline_increment") is False:
        blockers.append("BASELINE_INCREMENT_FAIL")
    risk = features.get("RISK") or {}
    if risk.get("thesis_invalidated") is True:
        blockers.append("THESIS_INVALIDATED")
    if alpha["repricing_completion"]["completed"]:
        blockers.append("REPRICING_COMPLETED")
    if alpha["contradiction"]["veto"]:
        blockers.append("TRADINGAGENTS_CONTRADICTION")
    if alpha.get("reflexivity_break") is not None and alpha["reflexivity_break"] >= 0.70:
        blockers.append("REFLEXIVITY_BREAK")
    if alpha.get("repricing_state") == "CLIMAX" or (
        alpha.get("crowding_risk") is not None and alpha["crowding_risk"] >= 0.85
    ) or alpha.get("buyer_exhaustion") is True:
        blockers.append("BUYER_EXHAUSTION_OR_CLIMAX")
    if alpha["capital_convergence"]["status"] == "CONFLICT":
        blockers.append("CAPITAL_CONVERGENCE_CONFLICT")
        blockers.append("CONFIRMED_DISTRIBUTION")
    if (features.get("SUPPLY") or {}).get("supply_absorption_state") == "RELEASING" and (features.get("CAPITAL") or {}).get("distribution_risk") not in (None, 0):
        if (features.get("CAPITAL") or {}).get("distribution_risk", 0) >= 0.70:
            blockers.append("CONFIRMED_SUPPLY_REVERSAL")
    execution = features.get("EXECUTION") or {}
    if execution.get("buyable") is False:
        blockers.append("EXECUTION_IMPOSSIBLE")
    elif execution.get("execution_feasibility") is not None and execution["execution_feasibility"] <= 0:
        blockers.append("SEVERE_EXECUTION_RISK")
    return list(dict.fromkeys(blockers))


def _exit_reason(
    alpha: Dict[str, Any],
    features: Dict[str, Any],
    snapshot: Dict[str, Any],
    account: Dict[str, Any] | None = None,
) -> str:
    if (account or {}).get("profit_window_hit"):
        return "PROFIT_WINDOW_HIT"
    risk = features["RISK"]
    completion = alpha["repricing_completion"]
    if risk["thesis_invalidated"] or snapshot["raw"].get("thesis_invalidated"):
        return "BUSINESS_OR_INDUSTRY_THESIS_BROKEN"
    if risk["regulatory_hard_risk"] or _at_least(risk.get("event_risk"), 0.80):
        return "RISK_EVENT"
    if _at_least(features["CAPITAL"].get("distribution_risk"), 0.70):
        return "CAPITAL_EXIT"
    if _at_least(features["SUPPLY"].get("effective_supply"), 0.80):
        return "SUPPLY_REVERSAL"
    if completion["completed"]:
        return "REPRICING_COMPLETED"
    if features["PRICING_GAP"]["score"] is not None and features["PRICING_GAP"]["score"] <= 0.10:
        return "PRICING_GAP_CLOSED"
    if _at_least(features["BUSINESS"].get("valuation"), 0.90):
        return "VALUATION_EXCESS"
    return "THESIS_INTACT"


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
    previous_action = previous_action or (portfolio_state if portfolio_state in TRADE_ACTIONS else None)
    if position_state is None:
        # The current holding is a PostgreSQL fact. Callers without that fact
        # are fail-closed as FLAT.
        position_state = "FLAT"
    if position_state not in POSITION_STATES:
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
        industry=research["industry"],
        company=research["company"],
        capital=research["capital"],
        integrated=research["integrated"],
        future_buyer_map=research["future_buyer_map"],
    )
    hard_blockers = eligibility_blockers(snapshot, account=account, as_of=as_of)
    repricing_blockers = _blockers(alpha, features, research)
    expected_net_profit = alpha.get("expected_net_profit_window")
    if (
        minimum_required_return > 0
        and expected_net_profit is not None
        and expected_net_profit < minimum_required_return
    ):
        repricing_blockers.append("PROFIT_WINDOW_BELOW_MINIMUM")
    elif minimum_required_return > 0 and expected_net_profit is None:
        repricing_blockers.append("PROFIT_WINDOW_BELOW_MINIMUM")
    all_blockers = hard_blockers + repricing_blockers
    held = position_state == "LONG"

    if held:
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
                blocker in repricing_blockers
                for blocker in (
                    "CAPITAL_CONVERGENCE_CONFLICT",
                    "REFLEXIVITY_BREAK",
                    "BUYER_EXHAUSTION_OR_CLIMAX",
                    "TRADINGAGENTS_CONTRADICTION",
                    "SUPPLY_REVERSAL",
                    "CAPITAL_EXIT",
                )
                )
            ):
                state, reason = "REDUCE", "REPRICING_RISK_OR_CONFIRMATION_DETERIORATED"
            else:
                state, reason = "HOLD", "REPRICING_THESIS_STILL_VALID"
    elif hard_blockers:
        state, reason = "WATCH", "HARD_CONSTRAINT:" + ";".join(hard_blockers)
    elif not repricing_blockers:
        state, reason = "BUY", "REPRICING_READINESS_CONFIRMED"
    else:
        # Diagnostic research measurements may explain a candidate, but they
        # never decide readiness or stand in for model probability.
        state, reason = "READY", "BUY_BLOCKED_PENDING_HARD_GATE:" + ";".join(repricing_blockers)

    position_state_after = "FLAT" if state in {"WATCH", "READY", "SELL"} else "LONG"
    return {
        "decision_id": _decision_id(snapshot, state, as_of),
        "state": state,
        "action": state if state in TRADE_ACTIONS else None,
        "position_state": position_state_after,
        "position_state_before": position_state,
        "position_state_after": position_state_after,
        "previous_action": previous_action,
        "holding_days": int(0 if (account or {}).get("holding_days") is None else (account or {}).get("holding_days")),
        "trade_status": "CLOSED" if state == "SELL" else "OPEN" if state in TRADE_ACTIONS else "NOT_OPEN",
        "buy_status": "BUY_ALLOWED" if state == "BUY" else "BUY_BLOCKED",
        "symbol": snapshot["symbol"],
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
        "entry_price": snapshot.get("price"),
        "entry_price_source": "canonical_snapshot.price",
        "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
        "alpha_version": alpha.get("alpha_version"),
        "feature_version": alpha.get("feature_version"),
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
