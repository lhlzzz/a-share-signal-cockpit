from datetime import datetime, timedelta, timezone
from inspect import getsource
import json

import pytest

from xiaogu_forward_snapshot import (
    production_decision_clock,
    production_now,
    snapshot_age,
    validate_and_build_canonical_snapshot,
)
from xiaogu_portfolio_decision import (
    DECISION_HARD_GATES,
    PRODUCTION_GATE_VERSION,
    evaluate_candidate_bundle,
    evaluate_production_gates,
)


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
REVIEW_CLOCK = datetime.fromisoformat("2026-09-01T10:00:00+08:00")


def _base_snapshot(**extra):
    payload = {
        "symbol": "600001",
        "price": 10,
        "volume": 100,
        "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "trade_date": "2026-08-26",
    }
    payload.update(extra)
    return payload


def _ready_snapshot(**extra):
    payload = {
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
        "execution_quality": 1,
    }
    payload.update(extra)
    return payload


def test_production_clock_is_not_source_time():
    source_time = "2026-08-26T14:50:00+08:00"
    clock = production_now()
    age = snapshot_age(source_time, clock)
    assert age is not None
    assert age > timedelta(minutes=120)
    assert production_decision_clock() != datetime.fromisoformat(source_time)
    assert production_decision_clock.__code__.co_names and "production_now" in production_decision_clock.__code__.co_names


def test_production_clock_stale_data_uses_real_clock(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    snapshot = validate_and_build_canonical_snapshot(_base_snapshot())
    with pytest.raises(ValueError, match="STALE_DATA"):
        run_production_decision(snapshot, mode="PRODUCTION", trade_date="2026-08-26")


def test_replay_clock_may_use_historical_source_time(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    snapshot = validate_and_build_canonical_snapshot(_base_snapshot())
    historical = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
    decision = run_production_decision(
        snapshot,
        mode="REPLAY",
        trade_date="2026-08-26",
        decision_clock=historical,
        position_state="FLAT",
    )
    assert decision["decision_clock"] == historical.astimezone(timezone.utc).isoformat()
    assert decision["buy_status"] == "BUY_BLOCKED"


def test_position_review_uses_current_snapshot_not_original(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_base_snapshot(trade_date="2026-08-26", source_time="2026-08-26T14:50:00+08:00"))
    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    assert original["snapshot_id"] != current["snapshot_id"]
    seen = {}
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "symbol": "600001", "trade_date": "2026-08-26", "state": "HOLD",
        "action": "HOLD", "decision_id": "d-original", "position_state": "LONG",
    }])
    monkeypatch.setattr("xiaogu_db.fetch_decision_snapshot", lambda *_args, **_kwargs: original)
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"received": received, **kwargs}) or {
            "state": "HOLD", "action": "HOLD", "trade_status": "OPEN",
        },
    )
    monkeypatch.setattr(runner, "daily_paper_position_review", lambda _date: [])
    runner.daily_position_review("2026-09-01")
    assert seen["received"]["snapshot_id"] == current["snapshot_id"]
    assert seen["received"]["snapshot_id"] != original["snapshot_id"]
    assert seen["trade_date"] == "2026-09-01"
    assert seen.get("decision_clock") is None
    assert seen["account"]["original_snapshot_id"] == original["snapshot_id"]
    assert seen["account"]["review_snapshot_id"] == current["snapshot_id"]
    assert seen["account"]["review_trade_date"] == "2026-09-01"
    assert seen["account"]["position_review"] is True


def test_position_review_missing_current_snapshot_blocks(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_base_snapshot())
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "symbol": "600001", "trade_date": "2026-08-26", "state": "HOLD",
        "action": "HOLD", "decision_id": "d-original", "position_state": "LONG",
    }])
    monkeypatch.setattr("xiaogu_db.fetch_decision_snapshot", lambda *_args, **_kwargs: original)
    monkeypatch.setattr(
        "xiaogu_db.get_current_position_review_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")),
    )
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    with pytest.raises(RuntimeError, match="CURRENT_REVIEW_SNAPSHOT_NOT_FOUND"):
        runner.daily_position_review("2026-09-01")


def test_position_review_does_not_create_paper_observation(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_base_snapshot())
    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    captured = {}
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "symbol": "600001", "trade_date": "2026-08-26", "state": "HOLD",
        "action": "HOLD", "decision_id": "d-original", "position_state": "LONG",
    }])
    monkeypatch.setattr("xiaogu_db.fetch_decision_snapshot", lambda *_args, **_kwargs: original)
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda *_args, **_kwargs: {
            "state": "SELL", "action": "SELL", "trade_status": "CLOSED",
            "paper_observation": {"paper_signal_id": "should-not-keep"},
            "paper_signal_id": "should-not-keep",
        },
    )
    monkeypatch.setattr(runner, "_write_ledger_record", lambda decision: captured.setdefault("decision", decision))
    monkeypatch.setattr(runner, "daily_paper_position_review", lambda _date: [])
    reviewed = runner.daily_position_review("2026-09-01")
    assert reviewed[0]["paper_observation"] is None
    assert "paper_signal_id" not in reviewed[0]
    assert reviewed[0]["decision_id"] == "d-original"
    assert reviewed[0]["original_snapshot_id"] == original["snapshot_id"]
    assert reviewed[0]["review_snapshot_id"] == current["snapshot_id"]
    assert reviewed[0]["review_trade_date"] == "2026-09-01"


def test_unknown_evidence_is_not_a_production_blocker():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(market_stage="UNKNOWN", buyer_exhaustion=None),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert decision["production_negative_evidence"] == []
    assert "CONFIRMED_DISTRIBUTION" not in decision["production_blockers"]
    assert "BUYER_EXHAUSTION_OR_CLIMAX" not in decision["production_blockers"]
    assert "TRADINGAGENTS_CONTRADICTION" not in decision["production_blockers"]


def test_confirmed_distribution_is_a_production_blocker():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(
            f62=-1_000,
            pct_chg=-2,
            lhb=[{
                "EXPLAIN": "1家机构卖出",
                "institution": True,
                "NET_BS_AMT": -120,
                "event_time": "2026-08-26T14:45:00+08:00",
                "available_at": "2026-08-26T14:50:00+08:00",
            }],
        ),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert "CONFIRMED_DISTRIBUTION" in decision["production_blockers"]
    assert any(item["blocker"] == "CONFIRMED_DISTRIBUTION" and item["status"] == "CONFIRMED" for item in decision["production_negative_evidence"])
    assert decision["state"] != "BUY"
    assert decision["buy_status"] == "BUY_BLOCKED"


def test_research_positive_capital_cannot_buy():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(
            lhb=[
                {"EXPLAIN": "1家机构买入", "institution": True, "NET_BS_AMT": 100, "event_time": "2026-08-26T14:45:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
                {"EXPLAIN": "游资买入", "hot_money": True, "游资": True, "NET_BS_AMT": 80, "event_time": "2026-08-26T14:46:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
            ],
        ),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert decision["core_alpha"]["capital_convergence"]["status"] == "CONVERGENCE"
    assert decision["state"] != "BUY"
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert decision["paper_observation"]["research_overlay"]["research_only"] is True


def test_same_lhb_event_counts_as_one_independent_origin():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(lhb=[{
            "EXPLAIN": "1家机构买入 游资",
            "institution": True,
            "hot_money": True,
            "游资": True,
            "NET_BS_AMT": 100,
            "event_time": "2026-08-26T14:45:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
        }]),
        as_of=AS_OF,
    )
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["independent_origin_count"] == 1
    assert convergence["evidence_identity_count"] == 1
    assert convergence["status"] == "PARTIAL"


def test_two_independent_events_count_as_two_origins():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(lhb=[
            {"EXPLAIN": "1家机构买入", "institution": True, "NET_BS_AMT": 100, "event_time": "2026-08-26T14:45:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
            {"EXPLAIN": "游资买入", "hot_money": True, "游资": True, "NET_BS_AMT": 80, "event_time": "2026-08-26T14:46:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
        ]),
        as_of=AS_OF,
    )
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["independent_origin_count"] >= 2
    assert convergence["evidence_identity_count"] >= 2
    assert convergence["confirmed_channel_count"] >= 2
    assert convergence["directional_alignment"] is True
    assert convergence["status"] == "CONVERGENCE"


def test_evaluate_candidate_bundle_does_not_implement_gates():
    source = getsource(evaluate_candidate_bundle)
    assert "evaluate_production_gates(" in source
    for token in (
        "oos_pass",
        "monotonicity",
        "full_alpha_baseline_increment",
        "probability_separation",
        "STALE_DATA",
        "CONFIRMED_DISTRIBUTION",
        "RESEARCH_ONLY_DECISION_BLOCKERS",
    ):
        assert token not in source
    gate_source = getsource(evaluate_production_gates)
    for token in (
        "TRUSTED_CANONICAL",
        "DB_VERIFIED",
        "FRESH_DATA",
        "OOS_PASS",
        "MONOTONICITY_PASS",
        "BASELINE_INCREMENT_PASS",
        "NEGATIVE_EVIDENCE_CLEAR",
    ):
        assert token in gate_source


def test_rule_freeze_matches_python_gate_contract():
    rule = json.loads(open("rule_freeze_v0_1.json", encoding="utf-8").read())
    assert tuple(rule["alpha_contract"]["required_hard_gates"]) == DECISION_HARD_GATES
    assert rule["alpha_contract"]["gate_owner"] == "xiaogu_portfolio_decision.evaluate_production_gates"
    assert rule["alpha_contract"]["gate_version"] == PRODUCTION_GATE_VERSION


def test_gate_pass_and_fail_are_owned_by_evaluate_production_gates():
    snapshot = validate_and_build_canonical_snapshot(_ready_snapshot())
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    assert decision["gate_version"] == PRODUCTION_GATE_VERSION
    assert decision["gate_result"]["passed"] is False
    assert "ALPHA_VALIDATED" in decision["failed_gates"]
    assert decision["gate_result"]["failed_gates"] == decision["failed_gates"]
    assert decision["state"] != "BUY"


def test_paper_observation_stays_observed_and_flat():
    decision = evaluate_candidate_bundle(_ready_snapshot(), position_state="FLAT", as_of=AS_OF)
    observation = decision["paper_observation"]
    assert observation["paper_observation_state"] == "OBSERVED"
    assert observation["paper_position_state"] == "PAPER_FLAT"
    assert observation["production_buy"] == "BLOCKED"


def test_review_decision_contract_fields():
    snapshot = validate_and_build_canonical_snapshot(_ready_snapshot(trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    decision = evaluate_candidate_bundle(
        snapshot,
        portfolio_state="HOLD",
        position_state="LONG",
        previous_action="HOLD",
        as_of=REVIEW_CLOCK,
        account={
            "decision_id": "d-original",
            "position_review": True,
            "original_snapshot_id": "original-id",
            "review_snapshot_id": snapshot["snapshot_id"],
            "review_trade_date": "2026-09-01",
            "holding_days": 2,
        },
    )
    assert decision["decision_id"] == "d-original"
    assert decision["original_snapshot_id"] == "original-id"
    assert decision["review_snapshot_id"] == snapshot["snapshot_id"]
    assert decision["review_snapshot_id"] != decision["original_snapshot_id"]
    assert decision["review_trade_date"] == "2026-09-01"
    assert decision["position_state"] in {"FLAT", "LONG"}
    assert decision["previous_action"] == "HOLD"
    assert decision["decision_clock"] == REVIEW_CLOCK.isoformat()
    assert decision["paper_observation"] is None
    assert decision["action"] in {"HOLD", "REDUCE", "SELL"}
    assert decision["action"] != "BUY"


def test_current_review_snapshot_resolver_fail_closed(monkeypatch):
    from xiaogu_db import get_current_position_review_snapshot

    class Rows:
        def mappings(self):
            return []

    class DB:
        def execute(self, *_args, **_kwargs):
            return Rows()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Engine:
        def connect(self):
            return DB()

    import xiaogu_db as db
    monkeypatch.setattr(db, "ensure_production_schema", lambda: None)
    monkeypatch.setattr(db, "engine", Engine())
    with pytest.raises(RuntimeError, match="CURRENT_REVIEW_SNAPSHOT_NOT_FOUND"):
        get_current_position_review_snapshot(symbol="600001", review_trade_date="2026-09-01")


def test_current_review_snapshot_ambiguous(monkeypatch):
    from xiaogu_db import get_current_position_review_snapshot

    first = validate_and_build_canonical_snapshot(_base_snapshot(price=10, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    second = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:50:00+08:00"))
    assert first["snapshot_id"] != second["snapshot_id"]

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self._rows

    class DB:
        def execute(self, *_args, **_kwargs):
            return Rows([
                {"payload": dict(first), "snapshot_id": first["snapshot_id"], "symbol": "600001", "trade_date": "2026-09-01", "source": first["source"], "source_time": first["source_time"], "lineage_id": first["lineage_id"]},
                {"payload": dict(second), "snapshot_id": second["snapshot_id"], "symbol": "600001", "trade_date": "2026-09-01", "source": second["source"], "source_time": second["source_time"], "lineage_id": second["lineage_id"]},
            ])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Engine:
        def connect(self):
            return DB()

    import xiaogu_db as db
    monkeypatch.setattr(db, "ensure_production_schema", lambda: None)
    monkeypatch.setattr(db, "engine", Engine())
    with pytest.raises(RuntimeError, match="CURRENT_REVIEW_SNAPSHOT_AMBIGUOUS"):
        get_current_position_review_snapshot(symbol="600001", review_trade_date="2026-09-01")
