from datetime import datetime
import json

from xiaogu_portfolio_decision import evaluate_candidate_bundle


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")


def _repricing_ready_snapshot():
    return {
        "symbol": "600001", "price": 10, "open": 9.9, "high": 10.3, "low": 9.7,
        "amount": 1_000, "volume": 100, "turnover": 5,
        "source_time": "2026-08-26T14:50:00+08:00",
        "financial_quality": 1, "moat": 1, "pricing_power": 1, "earnings_quality": 1,
        "roic": 100, "roe": 100, "growth": 100, "management": 1, "debt_safety": 1,
        "capital_allocation": 1, "valuation_quality": 0.8,
        "market_story_strength": 1, "system_change_strength": 1, "demand_score": 1,
        "bottleneck_score": 1, "supply_constraint": 1, "demand_visibility": 1,
        "industry_catalyst": 1, "evidence_strength": 1,
        "f62": 500, "f184": 5, "pct_chg": 3, "capital_accumulation": 1,
        "capital_persistence": 1, "capital_acceleration": 1, "institutional_flow": 0.8,
        "hot_money_flow": 0.5, "lhb_quality": 1, "seat_behavior_score": 1,
        "order_pressure": 1, "volume_accumulation": 1, "price_volume_confirmation": 1,
        "fund_flow_percentile": 1, "fundamental_gap": 1, "industry_gap": 1,
        "capital_gap": 1, "attention_gap": 1, "price_reflection": 0.1,
        "market_breadth_up_pct": 100, "sector_breadth": 1, "leader_strength": 1,
        "market_follow_through_score": 1, "market_alignment": 1,
        "attention_score": 0.2, "attention_growth": 0.5, "reflexivity_break": 0,
        "alpha_model_status": "VALIDATED", "execution_quality": 1,
    }


def test_only_portfolio_owner_emits_state():
    decision = evaluate_candidate_bundle(
        {
            "symbol": "600001", "price": 10, "source_time": "2026-08-26T14:50:00+00:00",
            "financial_quality": 1, "valuation_quality": 1, "bottleneck_score": 1,
            "demand_score": 1, "capital_accumulation": 1, "capital_persistence": 1,
            "capital_acceleration": 1, "revaluation_probability": 1, "market_alignment": 1,
            "relative_strength": 1,
        }
    )
    assert decision["state"] in {"WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL"}
    assert decision["decision_owner"].endswith("evaluate_candidate_bundle")


def test_watch_ready_buy_hold_reduce_sell_follow_one_owner_contract():
    ready = _repricing_ready_snapshot()
    buy = evaluate_candidate_bundle(ready, as_of=AS_OF)
    assert buy["state"] == "READY"
    assert buy["future_buyer_map"]["potential_next_buyer"] == []
    assert buy["thesis"]["why_future_buyers"]

    hold = evaluate_candidate_bundle(ready, portfolio_state="BUY", as_of=AS_OF)
    assert hold["state"] == "HOLD"

    blocked = evaluate_candidate_bundle(
        ready | {"tradingagents": {"contradiction_status": "BEARISH", "veto": True}},
        as_of=AS_OF,
    )
    assert blocked["state"] == "READY"
    assert "TRADINGAGENTS_CONTRADICTION" in blocked["repricing_risk"]["blockers"]

    reduce = evaluate_candidate_bundle(
        ready | {"f62": -1_000, "pct_chg": 1}, portfolio_state="BUY", as_of=AS_OF,
    )
    assert reduce["state"] == "REDUCE"
    assert reduce["reason"] == "CAPITAL_EXIT"

    sell = evaluate_candidate_bundle(
        ready | {"thesis_invalidated": True}, portfolio_state="BUY", as_of=AS_OF,
    )
    assert sell["state"] == "SELL"
    assert sell["reason"] == "BUSINESS_OR_INDUSTRY_THESIS_BROKEN"


def test_unverified_profit_window_cannot_enable_buy():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {"alpha_model_status": "UNVERIFIED"}, as_of=AS_OF,
    )
    assert decision["state"] == "READY"
    assert decision["core_alpha"]["expected_return_status"] == "DATA_INSUFFICIENT"
    assert "ALPHA_CALIBRATION_UNAVAILABLE" in decision["repricing_risk"]["blockers"]
    assert decision["core_alpha"]["expected_net_profit_window"] is None
    assert not any(key.startswith("expected_") and key.endswith("_return") for key in decision["core_alpha"])
    assert decision["core_alpha"]["capital_convergence"]["state"] == "PARTIAL"


def test_calibrated_probability_is_used_but_oos_failure_stays_fail_closed(tmp_path, monkeypatch):
    import xiaogu_core_alpha as alpha

    calibration_path = tmp_path / "profit_window_calibration.json"
    calibration_path.write_text(json.dumps({
        "model_id": "test-calibration",
        "target": "PROFIT_WINDOW_5D",
        "status": "CALIBRATED",
        "intercept": 0.0,
        "coefficients": [0.0] * 11,
        "feature_names": [
            "capital_convergence", "capital_persistence", "capital_acceleration",
            "supply_absorption", "pricing_gap", "repricing_state",
            "future_buyer_evidence", "reflexivity", "market_state",
            "execution_quality", "risk",
        ],
        "oos": {"passed": False},
    }), encoding="utf-8")
    monkeypatch.setattr(alpha, "CALIBRATION_PATH", calibration_path)

    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    assert decision["core_alpha"]["profit_window_probability"] == 0.5
    assert decision["core_alpha"]["profit_window_calibration"]["status"] == "CALIBRATED"
    assert decision["state"] == "READY"
    assert "ALPHA_CALIBRATION_UNAVAILABLE" in decision["repricing_risk"]["blockers"]


def test_capital_convergence_exposes_formal_levels_and_conflict():
    ready = _repricing_ready_snapshot()
    decision = evaluate_candidate_bundle(ready, as_of=AS_OF)
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["status"] == "PARTIAL"
    assert set(convergence["levels"]) == {"institution", "main_force", "hot_money"}
    assert decision["core_alpha"]["capital_convergence_level"] == "UNKNOWN"
    assert decision["core_alpha"]["real_pricing_gap"] == decision["core_alpha"]["pricing_gap"]
    assert decision["core_alpha"]["low_price"] is False

    evidenced = ready | {
        "lhb": [
            {"EXPLAIN": "1家机构买入", "institution": True},
        ],
    }
    evidenced_decision = evaluate_candidate_bundle(evidenced, as_of=AS_OF)
    assert evidenced_decision["core_alpha"]["capital_convergence"]["status"] == "CONVERGENCE"
    assert evidenced_decision["core_alpha"]["capital_convergence"]["independent_channel_count"] >= 2

    conflict = evaluate_candidate_bundle(
        ready | {"f62": -1_000, "pct_chg": -1}, as_of=AS_OF,
    )
    assert conflict["core_alpha"]["capital_convergence"]["status"] == "CONFLICT"


def test_profit_window_hit_reduces_held_position():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot(),
        portfolio_state="BUY",
        account={"profit_window_hit": True},
        as_of=AS_OF,
    )
    assert decision["state"] == "REDUCE"
    assert decision["reason"] == "PROFIT_WINDOW_HIT"


def test_climax_or_buyer_exhaustion_blocks_new_buy():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {"market_stage": "CLIMAX"}, as_of=AS_OF,
    )
    assert decision["state"] == "READY"
    assert "BUYER_EXHAUSTION_OR_CLIMAX" in decision["repricing_risk"]["blockers"]
