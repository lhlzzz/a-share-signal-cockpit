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

    hold = evaluate_candidate_bundle(ready, portfolio_state="BUY", position_state="LONG", as_of=AS_OF)
    assert hold["state"] == "HOLD"

    blocked = evaluate_candidate_bundle(
        ready | {"tradingagents": {"contradiction_status": "BEARISH", "veto": True}},
        as_of=AS_OF,
    )
    assert blocked["state"] == "READY"
    assert "TRADINGAGENTS_CONTRADICTION" in blocked["repricing_risk"]["blockers"]

    reduce = evaluate_candidate_bundle(
        ready | {"f62": -1_000, "pct_chg": 1}, portfolio_state="BUY", position_state="LONG", as_of=AS_OF,
    )
    assert reduce["state"] == "REDUCE"
    assert reduce["reason"] == "CAPITAL_EXIT"

    sell = evaluate_candidate_bundle(
        ready | {"thesis_invalidated": True}, portfolio_state="BUY", position_state="LONG", as_of=AS_OF,
    )
    assert sell["state"] == "SELL"
    assert sell["reason"] == "BUSINESS_OR_INDUSTRY_THESIS_BROKEN"


def test_unverified_profit_window_cannot_enable_buy():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {"alpha_model_status": "UNVERIFIED"}, as_of=AS_OF,
    )
    assert decision["state"] == "READY"
    assert decision["core_alpha"]["expected_return_status"] in {"EXPERIMENTAL", "DATA_INSUFFICIENT"}
    assert decision["core_alpha"]["model_status"] != "VALIDATED"
    assert "ALPHA_NOT_VALIDATED" in decision["repricing_risk"]["blockers"]
    assert decision["core_alpha"]["expected_net_profit_window"] is None
    assert not any(key.startswith("expected_") and key.endswith("_return") for key in decision["core_alpha"])
    assert decision["core_alpha"]["capital_convergence"]["state"] == "UNKNOWN"


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
    assert decision["core_alpha"]["research_probability"] == 0.5
    assert decision["core_alpha"]["profit_window_probability"] is None
    assert decision["core_alpha"]["profit_window_calibration"]["status"] == "CALIBRATED"
    assert decision["state"] == "READY"
    assert "ALPHA_NOT_VALIDATED" in decision["repricing_risk"]["blockers"]


def test_validated_label_without_all_production_gates_stays_experimental(tmp_path, monkeypatch):
    import xiaogu_core_alpha as alpha

    calibration_path = tmp_path / "profit_window_calibration.json"
    calibration_path.write_text(json.dumps({
        "model_id": "profit_window_alpha_5d_v2",
        "model_version": "v2",
        "feature_version": "capital_behavior_measurements_v2",
        "dataset_hash": "test",
        "dataset_version": "test",
        "train_window": {"count": 10},
        "validation_window": {"count": 5},
        "oos_window": {"count": 5},
        "cost_model_version": "cost_model_v1",
        "target_version": "PROFIT_WINDOW_5D",
        "horizon": 5,
        "schema_version": "alpha_artifact_v1",
        "target": "PROFIT_WINDOW_5D",
        "status": "VALIDATED",
        "intercept": 0.0,
        "coefficients": [0.0] * 11,
        "feature_names": [
            "capital_convergence", "capital_persistence", "capital_acceleration",
            "supply_absorption", "pricing_gap", "repricing_state",
            "future_buyer_evidence", "reflexivity", "market_state",
            "execution_quality", "risk",
        ],
        "oos": {"passed": True, "mean_profit": 0.03},
    }), encoding="utf-8")
    monkeypatch.setattr(alpha, "CALIBRATION_PATH", calibration_path)

    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    assert decision["core_alpha"]["model_status"] == "EXPERIMENTAL"
    assert decision["core_alpha"]["expected_net_profit_window"] is None
    assert decision["state"] == "READY"


def test_capital_convergence_exposes_formal_levels_and_conflict():
    ready = _repricing_ready_snapshot()
    decision = evaluate_candidate_bundle(ready, as_of=AS_OF)
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["status"] == "UNKNOWN"
    assert set(convergence["levels"]) == {"institution", "main_force", "hot_money"}
    assert decision["core_alpha"]["capital_convergence_level"] == "UNKNOWN"
    assert decision["core_alpha"]["real_pricing_gap"] == decision["core_alpha"]["pricing_gap"]
    assert decision["core_alpha"]["low_price"] is False

    one_event = evaluate_candidate_bundle(
        ready | {"lhb": [{"EXPLAIN": "1家机构买入", "institution": True, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"}]},
        as_of=AS_OF,
    )
    assert one_event["core_alpha"]["capital_convergence"]["status"] == "PARTIAL"
    assert one_event["core_alpha"]["capital_convergence"]["independent_channel_count"] == 1

    evidenced = ready | {
        "lhb": [
            {"EXPLAIN": "1家机构买入", "institution": True, "NET_BS_AMT": 100, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
            {"EXPLAIN": "游资买入", "hot_money": True, "游资": True, "NET_BS_AMT": 80, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
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
        portfolio_state="BUY", position_state="LONG",
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


def test_five_day_boundary_closes_trade():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot(),
        portfolio_state="HOLD", position_state="LONG",
        account={"holding_days": 5},
        as_of=AS_OF,
    )
    assert decision["state"] == "SELL"
    assert decision["position_state"] == "FLAT"
    assert decision["trade_status"] == "CLOSED"


def test_five_day_boundary_closes_even_after_profit_window_hit():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot(),
        portfolio_state="HOLD", position_state="LONG",
        account={"holding_days": 5, "profit_window_hit": True},
        as_of=AS_OF,
    )
    assert decision["state"] == "SELL"
    assert decision["reason"] == "MAX_HOLDING_BOUNDARY_CLOSED"
    assert decision["trade_status"] == "CLOSED"


def test_same_lhb_event_is_one_independent_origin():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {
            "lhb": [
                {"EXPLAIN": "1家机构买入 游资", "institution": True, "hot_money": True, "游资": True, "NET_BS_AMT": 100, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
            ],
        },
        as_of=AS_OF,
    )
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["independent_channel_count"] == 1
    assert convergence["status"] == "PARTIAL"


def test_inflow_is_capital_flow_not_main_force():
    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    capital = decision["feature_vector"]["CAPITAL"]
    assert capital["capital_flow_state"] == "CAPITAL_FLOW_POSITIVE"
    assert capital["main_force_behavior"]["direction"] == "UNKNOWN"
    assert capital["main_force_behavior"]["evidence_status"] == "UNKNOWN"


def test_duplicate_evidence_family_does_not_inflate_independent_channels():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {
            "lhb": [
                {"EXPLAIN": "1家机构买入", "institution": True, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
                {"EXPLAIN": "游资买入", "hot_money": True, "游资": True, "TRADE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
            ],
        },
        as_of=AS_OF,
    )
    convergence = decision["core_alpha"]["capital_convergence"]
    assert "lhb" in convergence["independent_sources"]
    assert convergence["independent_channel_count"] == 2
    assert convergence["status"] in {"CONVERGENCE", "PARTIAL"}


def test_five_day_boundary_uses_sell_action_and_closed_trade():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot(),
        portfolio_state="HOLD", position_state="LONG",
        account={"holding_days": 5},
        as_of=AS_OF,
    )
    assert decision["action"] == "SELL"
    assert decision["position_state"] == "FLAT"
    assert decision["trade_status"] == "CLOSED"
    assert decision["state"] == "SELL"



def test_position_state_is_not_previous_action():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot(),
        portfolio_state="BUY",
        position_state="LONG",
        previous_action="BUY",
        as_of=AS_OF,
    )
    assert decision["position_state"] == "LONG"
    assert decision["action"] == "HOLD"
    assert decision["state"] == "HOLD"
    assert decision["previous_action"] == "BUY"


def test_reduce_is_action_not_position_state():
    decision = evaluate_candidate_bundle(
        _repricing_ready_snapshot() | {"f62": -1_000, "pct_chg": 1},
        position_state="LONG",
        previous_action="HOLD",
        as_of=AS_OF,
    )
    assert decision["action"] == "REDUCE"
    assert decision["position_state"] == "LONG"
    assert decision["state"] == "REDUCE"


def test_collapsed_and_unvalidated_families_have_no_production_alpha_permission():
    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    permissions = decision["core_alpha"]["production_alpha_permissions"]
    assert permissions
    assert all(value == "RESEARCH_ONLY" for value in permissions.values())
    assert decision["state"] != "BUY"
    assert decision["core_alpha"]["model_status"] != "VALIDATED"


def test_probability_collapse_and_full_coverage_stay_fail_closed(tmp_path, monkeypatch):
    import xiaogu_core_alpha as alpha

    calibration_path = tmp_path / "profit_window_calibration.json"
    calibration_path.write_text(json.dumps({
        "model_id": "profit_window_alpha_5d_v2",
        "model_version": "v2",
        "feature_version": "capital_behavior_measurements_v2",
        "dataset_hash": "test",
        "dataset_version": "test",
        "train_window": {"count": 10},
        "validation_window": {"count": 5},
        "oos_window": {"count": 5},
        "cost_model_version": "cost_model_v1",
        "target_version": "PROFIT_WINDOW_5D",
        "horizon": 5,
        "schema_version": "alpha_artifact_v1",
        "target": "PROFIT_WINDOW_5D",
        "status": "VALIDATED",
        "intercept": 0.0,
        "coefficients": [0.0] * 11,
        "feature_names": [
            "capital_convergence", "capital_persistence", "capital_acceleration",
            "supply_absorption", "pricing_gap", "repricing_state",
            "future_buyer_evidence", "reflexivity", "market_state",
            "execution_quality", "risk",
        ],
        "oos": {
            "passed": True,
            "probability_std": 0.001,
            "buy_coverage": 1.0,
        },
        "production_gates": {
            "data_quality": True,
            "oos_pass": True,
            "monotonicity": True,
            "probability_separation": True,
            "full_alpha_baseline_increment": True,
            "capital_supply_repricing_increment": True,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(alpha, "CALIBRATION_PATH", calibration_path)
    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    assert decision["core_alpha"]["model_status"] == "MODEL_NOT_DISCRIMINATIVE"
    assert decision["state"] != "BUY"


def test_missing_evidence_does_not_invent_main_force_accumulation():
    decision = evaluate_candidate_bundle(_repricing_ready_snapshot(), as_of=AS_OF)
    capital = decision["feature_vector"]["CAPITAL"]
    assert capital["capital_flow_state"] == "CAPITAL_FLOW_POSITIVE"
    assert capital["capital_price_impact_state"] == "DEMAND_RESPONSE_OBSERVATION"
    assert capital["main_force_behavior"]["direction"] == "UNKNOWN"
    assert "MAIN_FORCE_ACCUMULATING" not in str(capital["main_force_behavior"]["direction"])
    assert "CAPITAL_CONVERGENCE_INCOMPLETE" not in decision["repricing_risk"]["blockers"]
    assert "FUTURE_BUYER_EVIDENCE_MISSING" not in decision["repricing_risk"]["blockers"]
    assert decision["state"] != "BUY"
