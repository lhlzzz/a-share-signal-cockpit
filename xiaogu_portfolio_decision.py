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


def _decision_id(snapshot: Dict[str, Any], state: str, as_of: datetime | None) -> str:
    signal_time = snapshot.get("signal_time") or snapshot.get("source_time") or ""
    clock = as_of.isoformat() if as_of else ""
    identity = f"{snapshot.get('snapshot_id', '')}|{snapshot.get('trade_date', '')}|{signal_time}|{clock}|{snapshot['symbol']}|{state}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _blockers(alpha: Dict[str, Any], features: Dict[str, Any], research: Dict[str, Any]) -> list[str]:
    blockers = []
    readiness = alpha["readiness"]
    # Research unknowns remain observable context. Only operational, risk,
    # model-validation, and explicit negative evidence can block BUY here.
    blockers.extend(
        key for key in ("EXECUTION_FEASIBLE", "RISK_READY", "MARKET_READY")
        if not readiness.get(key, False)
    )
    if alpha["model_status"] != "VALIDATED":
        blockers.append("ALPHA_CALIBRATION_UNAVAILABLE")
    if alpha["repricing_completion"]["completed"]:
        blockers.append("REPRICING_COMPLETED")
    if alpha["contradiction"]["veto"]:
        blockers.append("TRADINGAGENTS_CONTRADICTION")
    if (alpha["reflexivity_break"] or 0.0) >= 0.70:
        blockers.append("REFLEXIVITY_BREAK")
    if alpha.get("repricing_state") == "CLIMAX" or (alpha.get("crowding_risk") or 0.0) >= 0.85 or alpha.get("buyer_exhaustion"):
        blockers.append("BUYER_EXHAUSTION_OR_CLIMAX")
    if alpha["capital_convergence"]["status"] == "CONFLICT":
        blockers.append("CAPITAL_CONVERGENCE_CONFLICT")
    if alpha["profit_window_probability"] is None or alpha["profit_window_probability"] < 0.45:
        blockers.append("PROFIT_WINDOW_PROBABILITY_LOW")
    expected_net_profit = alpha.get("expected_net_profit_window")
    if expected_net_profit is None:
        blockers.append("NET_PROFIT_WINDOW_UNAVAILABLE")
    elif expected_net_profit <= 0:
        blockers.append("NET_PROFIT_WINDOW_NOT_POSITIVE")
    if alpha["execution_feasibility"] is None or alpha["execution_feasibility"] < 0.35:
        blockers.append("EXECUTION_NOT_FEASIBLE")
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
    if risk["regulatory_hard_risk"] or (risk["event_risk"] or 0.0) >= 0.80:
        return "RISK_EVENT"
    if (features["CAPITAL"]["distribution_risk"] or 0.0) >= 0.70:
        return "CAPITAL_EXIT"
    if (features["SUPPLY"]["effective_supply"] or 0.0) >= 0.80:
        return "SUPPLY_REVERSAL"
    if completion["completed"]:
        return "REPRICING_COMPLETED"
    if features["PRICING_GAP"]["score"] is not None and features["PRICING_GAP"]["score"] <= 0.10:
        return "PRICING_GAP_CLOSED"
    if (features["BUSINESS"]["valuation"] or 0.0) >= 0.90:
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
    if portfolio_state in POSITION_STATES and position_state is None:
        position_state = portfolio_state
        portfolio_state = previous_action or "WATCH"
    if portfolio_state not in PORTFOLIO_STATES:
        raise ValueError(f"INVALID_PORTFOLIO_STATE:{portfolio_state}")
    previous_action = previous_action or (portfolio_state if portfolio_state in TRADE_ACTIONS else None)
    if position_state is None:
        position_state = "LONG" if portfolio_state in HELD_ACTIONS else "FLAT"
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
        if int((account or {}).get("holding_days", 0) or 0) >= MAX_HOLDING_DAYS:
            state, reason = "SELL", "MAX_HOLDING_BOUNDARY_CLOSED"
        else:
            exit_reason = _exit_reason(alpha, features, snapshot, account)
            if exit_reason != "THESIS_INTACT":
                state = "SELL" if exit_reason in {"BUSINESS_OR_INDUSTRY_THESIS_BROKEN", "RISK_EVENT", "REPRICING_COMPLETED", "PRICING_GAP_CLOSED", "VALUATION_EXCESS"} else "REDUCE"
                reason = exit_reason
            elif (
                (alpha["downside_risk"] is not None and alpha["downside_risk"] >= (alpha["confidence"] or 0.0))
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
    elif alpha["thesis_score"] >= 0.45 and alpha.get("repricing_evidence_score", alpha.get("repricing_readiness_score", 0)) >= 0.35:
        state, reason = "READY", "THESIS_VALID_BUT_CONFIRMATION_PENDING:" + ";".join(repricing_blockers)
    else:
        state, reason = "WATCH", "REPRICING_THESIS_INCOMPLETE:" + ";".join(repricing_blockers)

    return {
        "decision_id": _decision_id(snapshot, state, as_of),
        "state": state,
        "action": state if state in TRADE_ACTIONS else None,
        "position_state": "FLAT" if state in {"WATCH", "READY", "SELL"} else "LONG",
        "previous_action": previous_action,
        "holding_days": int((account or {}).get("holding_days", 0) or 0),
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
        "supply_absorption": research["supply"],
        "pricing_gap": research["pricing_gap"],
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
        "repricing_readiness_score": alpha.get("repricing_readiness_score"),
        "profit_window_probability": alpha.get("profit_window_probability"),
        "expected_max_profit_5d": alpha.get("expected_max_profit_5d"),
        "expected_mae_5d": alpha.get("expected_mae_5d"),
        "downside_risk": alpha.get("downside_risk"),
        "confidence": alpha.get("confidence"),
        "thesis": alpha.get("thesis"),
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
