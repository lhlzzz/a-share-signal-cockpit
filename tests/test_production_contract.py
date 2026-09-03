from datetime import datetime, timedelta, timezone
from inspect import getsource
import json

import pytest

from xiaogu_forward_snapshot import (
    MAX_STALENESS,
    assert_production_provenance,
    production_decision_clock,
    production_now,
    snapshot_age,
    validate_and_build_canonical_snapshot,
)
from xiaogu_forward_features import _evidence, validate_evidence_identity
from xiaogu_portfolio_decision import (
    DECISION_HARD_GATES,
    PRODUCTION_GATE_VERSION,
    _evidence_confirmed,
    build_confirmed_negative_blocker,
    collect_production_negative_evidence,
    evaluate_candidate_bundle,
    evaluate_production_gates,
)


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")

def _lhb_row(event_id: str, explain: str, **extra):
    event_time = extra.pop("event_time", "2026-08-26T14:45:00+08:00")
    available_at = extra.pop("available_at", "2026-08-26T14:50:00+08:00")
    row = {
        "event_id": event_id,
        "source_id": extra.pop("source_id", "lhb"),
        "mechanism": extra.pop("mechanism", "lhb_event"),
        "EXPLAIN": explain,
        "event_time": event_time,
        "available_at": available_at,
        "observed_at": extra.pop("observed_at", event_time),
    }
    row.update(extra)
    return row


def _open_position(**extra):
    decision_id = extra.get("decision_id", "d-original")
    payload = {
        "position_id": extra.get("position_id", f"POS|{decision_id}"),
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "state": "HOLD",
        "action": "HOLD",
        "decision_id": decision_id,
        "position_state": "LONG",
        "snapshot_id": extra.get("snapshot_id", "original-snapshot"),
        "original_snapshot_id": extra.get("original_snapshot_id", extra.get("snapshot_id", "original-snapshot")),
    }
    payload.update(extra)
    payload.setdefault("position_id", f"POS|{payload['decision_id']}")
    return payload


def _cleanup_seeded_positions(decision_ids, lineage_ids):
    from sqlalchemy import text
    from xiaogu_db import engine

    decision_ids = [str(item) for item in decision_ids]
    lineage_ids = [str(item) for item in lineage_ids]
    with engine.begin() as db:
        if decision_ids:
            wanted = ", ".join(f":d{i}" for i in range(len(decision_ids)))
            params = {f"d{i}": value for i, value in enumerate(decision_ids)}
            db.execute(text(f"DELETE FROM positions WHERE decision_id IN ({wanted})"), params)
            db.execute(text(f"DELETE FROM picks WHERE decision_id IN ({wanted})"), params)
        if lineage_ids:
            wanted = ", ".join(f":l{i}" for i in range(len(lineage_ids)))
            params = {f"l{i}": value for i, value in enumerate(lineage_ids)}
            db.execute(text(f"DELETE FROM snapshots WHERE lineage_id IN ({wanted})"), params)


def _seed_isolated_positions(rows):
    """Persist positions through production writers. Never forges identity."""
    from xiaogu_db import ensure_production_schema, record_decision, record_snapshot

    ensure_production_schema()
    decision_ids = [row["decision_id"] for row in rows]
    lineage_ids = [row["lineage_id"] for row in rows]
    _cleanup_seeded_positions(decision_ids, lineage_ids)
    seeded = []
    for row in rows:
        snapshot = validate_and_build_canonical_snapshot(
            _base_snapshot(
                symbol=row["symbol"],
                trade_date=row["trade_date"],
                source_time=f"{row['trade_date']}T14:50:00+08:00",
                lineage_id=row["lineage_id"],
            ),
            lineage_id=row["lineage_id"],
        )
        record_snapshot(snapshot)
        decision = {
            "decision_id": row["decision_id"],
            "position_id": f"POS|{row['decision_id']}",
            "symbol": snapshot["symbol"],
            "trade_date": snapshot["trade_date"],
            "state": "HOLD",
            "action": "HOLD",
            "position_state": "LONG",
            "snapshot_id": snapshot["snapshot_id"],
            "lineage_id": snapshot["lineage_id"],
            "original_snapshot_id": snapshot["snapshot_id"],
            "canonical_snapshot": snapshot,
        }
        record_decision(decision)
        if row.get("position_state") == "FLAT":
            record_decision({
                **decision,
                "state": "SELL",
                "action": "SELL",
                "position_state": "FLAT",
                "review_trade_date": snapshot["trade_date"],
            })
        seeded.append({**row, "snapshot_id": snapshot["snapshot_id"], "position_id": decision["position_id"]})
    return seeded

REVIEW_CLOCK = datetime.fromisoformat("2026-09-01T10:00:00+08:00")


def _base_snapshot(**extra):
    payload = {
        "symbol": "600001",
        "price": 10,
        "volume": 100,
        "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "trade_date": "2026-08-26",
        "f12": "600001",
        "f13": 1,
        "f1": 2,
        "market": "SH",
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
        "f12": "600001", "f13": 1, "f1": 2, "market": "SH",
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


def test_freshness_boundary_keeps_119_fresh_and_rejects_120_plus():
    from xiaogu_forward_eligibility import eligibility_blockers

    source = datetime.fromisoformat("2026-09-03T02:11:41+08:00")
    snapshot = validate_and_build_canonical_snapshot(
        _base_snapshot(source_time=source.isoformat(), trade_date="2026-09-03", as_of=source.isoformat())
    )
    fresh_clock = source + timedelta(minutes=119, seconds=59)
    exact_clock = source + timedelta(minutes=120)
    stale_clock = source + timedelta(minutes=120, milliseconds=1)

    assert snapshot_age(source, fresh_clock) < MAX_STALENESS
    assert snapshot_age(source, exact_clock) <= MAX_STALENESS
    assert snapshot_age(source, stale_clock) > MAX_STALENESS

    assert_production_provenance(
        snapshot, trade_date="2026-09-03", decision_time=fresh_clock, persisted=True
    )
    assert_production_provenance(
        snapshot, trade_date="2026-09-03", decision_time=exact_clock, persisted=True
    )
    with pytest.raises(ValueError, match="STALE_DATA"):
        assert_production_provenance(
            snapshot, trade_date="2026-09-03", decision_time=stale_clock, persisted=True
        )

    assert "STALE_DATA" not in eligibility_blockers(snapshot, as_of=fresh_clock)
    assert "STALE_DATA" not in eligibility_blockers(snapshot, as_of=exact_clock)
    assert "STALE_DATA" in eligibility_blockers(snapshot, as_of=stale_clock)


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
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [_open_position(snapshot_id=original["snapshot_id"], original_snapshot_id=original["snapshot_id"])])
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
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [_open_position(snapshot_id=original["snapshot_id"], original_snapshot_id=original["snapshot_id"])])
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
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [_open_position(snapshot_id=original["snapshot_id"], original_snapshot_id=original["snapshot_id"])])
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


def test_feature_engine_emits_distribution_specific_evidence():
    snapshot = validate_and_build_canonical_snapshot(_ready_snapshot(
        f62=-1_000,
        pct_chg=-2,
        lhb=[_lhb_row("lhb-dist-feat", "1家机构卖出", institution=True, NET_BS_AMT=-120, mechanism="distribution_risk")],
    ))
    from xiaogu_forward_features import build_feature_vector
    features = build_feature_vector(snapshot)
    items = features["CAPITAL"]["distribution_evidence"]
    assert items
    assert items[0]["mechanism"] == "distribution_risk"
    assert items[0]["evidence_identity"] == ("lhb", "lhb-dist-feat", "distribution_risk")


def test_confirmed_distribution_is_a_production_blocker():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(
            f62=-1_000,
            pct_chg=-2,
            lhb=[_lhb_row("lhb-dist-1", "1家机构卖出", institution=True, NET_BS_AMT=-120, mechanism="distribution_risk")],
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
                _lhb_row("lhb-inst-1", "1家机构买入", institution=True, NET_BS_AMT=100),
                _lhb_row("lhb-hot-1", "游资买入", hot_money=True, **{"游资": True}, NET_BS_AMT=80, event_time="2026-08-26T14:46:00+08:00"),
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
        _ready_snapshot(lhb=[_lhb_row(
            "lhb-same-1",
            "1家机构买入 游资",
            institution=True,
            hot_money=True,
            **{"游资": True},
            NET_BS_AMT=100,
        )]),
        as_of=AS_OF,
    )
    convergence = decision["core_alpha"]["capital_convergence"]
    assert convergence["independent_origin_count"] == 1
    assert convergence["evidence_identity_count"] == 1
    assert convergence["status"] == "PARTIAL"


def test_two_independent_events_count_as_two_origins():
    decision = evaluate_candidate_bundle(
        _ready_snapshot(lhb=[
            _lhb_row("lhb-inst-2", "1家机构买入", institution=True, NET_BS_AMT=100),
            _lhb_row("lhb-hot-2", "游资买入", hot_money=True, **{"游资": True}, NET_BS_AMT=80, event_time="2026-08-26T14:46:00+08:00"),
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



def test_paper_review_uses_current_snapshot(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_base_snapshot(trade_date="2026-08-26", source_time="2026-08-26T14:50:00+08:00"))
    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    assert original["snapshot_id"] != current["snapshot_id"]
    seen = {}
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": original["snapshot_id"],
        "original_snapshot_id": original["snapshot_id"],
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    def fake_resolver(**kwargs):
        seen["resolver"] = kwargs
        return current
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", fake_resolver)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"received": received, **kwargs}) or {
            "state": "HOLD", "action": "HOLD", "trade_status": "OPEN", "paper_signal_id": "should-not-keep",
        },
    )
    reviewed = runner.daily_paper_position_review("2026-09-01")
    assert seen["received"]["snapshot_id"] == current["snapshot_id"]
    assert seen["received"]["snapshot_id"] != original["snapshot_id"]
    assert seen["mode"] == "PRODUCTION"
    assert seen["account"]["paper_signal_id"] == "paper-1"
    assert seen["account"]["decision_id"] == "d-original"
    assert seen["account"]["original_snapshot_id"] == original["snapshot_id"]
    assert seen["account"]["review_snapshot_id"] == current["snapshot_id"]
    assert seen["account"]["review_trade_date"] == "2026-09-01"
    assert seen["account"]["position_review"] is True
    assert seen["resolver"]["symbol"] == "600001"
    assert seen["resolver"]["review_trade_date"] == "2026-09-01"
    assert reviewed[0]["paper_signal_id"] == "paper-1"
    assert reviewed[0]["decision_id"] == "d-original"
    assert reviewed[0]["original_snapshot_id"] == original["snapshot_id"]
    assert reviewed[0]["review_snapshot_id"] == current["snapshot_id"]
    assert reviewed[0]["review_trade_date"] == "2026-09-01"
    assert reviewed[0]["paper_action"] == "PAPER_HOLD"
    assert reviewed[0]["paper_observation"] is None


def test_paper_review_uses_production_clock(monkeypatch):
    import xiaogu_forward_runner as runner
    from xiaogu_forward_snapshot import production_now

    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    seen = {}
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"received": received, **kwargs}) or {"state": "HOLD", "action": "HOLD"},
    )
    runner.daily_paper_position_review("2026-09-01")
    assert seen["mode"] == "PRODUCTION"
    assert seen.get("decision_clock") is None
    assert seen["received"]["source_time"] != production_now().isoformat()


def test_paper_review_missing_current_snapshot_blocks(monkeypatch):
    import xiaogu_forward_runner as runner

    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr(
        "xiaogu_db.get_current_position_review_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="PAPER_POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND"):
        runner.daily_paper_position_review("2026-09-01")


def test_old_review_snapshot_is_stale_against_production_clock(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    monkeypatch.setattr(
        "xiaogu_db.get_position_by_id",
        lambda _position_id: {"position_id": "POS|d-original", "position_state": "LONG", "decision_id": "d-original"},
    )
    monkeypatch.setattr(
        "xiaogu_db.get_position_by_decision_id",
        lambda _decision_id: {"position_id": "POS|d-original", "position_state": "LONG", "decision_id": "d-original"},
    )
    snapshot = validate_and_build_canonical_snapshot(_base_snapshot())
    with pytest.raises(ValueError, match="STALE_DATA"):
        run_production_decision(
            snapshot,
            mode="PRODUCTION",
            trade_date="2026-08-26",
            position_state="LONG",
            account={"position_id": "POS|d-original", "decision_id": "d-original", "position_review": True},
        )


def test_production_clock_is_not_snapshot_source_time(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    fixed = datetime.fromisoformat("2026-09-03T10:00:00+08:00").astimezone(timezone.utc)
    monkeypatch.setattr("xiaogu_forward_snapshot.production_now", lambda: fixed)
    monkeypatch.setattr("xiaogu_forward_runner.production_decision_clock", lambda decision_time=None: decision_time or fixed)
    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    snapshot = validate_and_build_canonical_snapshot(_base_snapshot(source_time="2026-09-03T09:50:00+08:00", trade_date="2026-09-03"))
    decision = run_production_decision(snapshot, mode="PRODUCTION", trade_date="2026-09-03", position_state="FLAT")
    assert decision["decision_clock"] == fixed.isoformat()
    assert decision["decision_clock"] != snapshot["source_time"]


def test_future_available_at_is_excluded_from_production():
    from xiaogu_forward_snapshot import pit_record_audit

    audit = pit_record_audit(
        {
            "source_id": "lhb",
            "event_id": "future-1",
            "mechanism": "lhb_event",
            "event_time": "2026-08-27T10:00:00+08:00",
            "available_at": "2026-08-27T10:00:00+08:00",
            "observed_at": "2026-08-27T10:00:00+08:00",
        },
        AS_OF,
    )
    assert audit["pit_status"] == "EXCLUDED_FROM_PRODUCTION"
    assert audit["exclusion_reason"] == "FUTURE_TIMESTAMP"


def test_future_negative_evidence_is_not_a_production_blocker():
    future_item = {
        "source_id": "lhb",
        "event_id": "future-1",
        "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "future-1", "distribution_risk"),
        "observed": True,
        "direction": "SELL",
        "observed_at": "2026-08-27T10:00:00+08:00",
        "available_at": "2026-08-27T10:00:00+08:00",
        "as_of": "2026-08-27T10:00:00+08:00",
        "event_time": "2026-08-27T10:00:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    features = {"CAPITAL": {"distribution_evidence": [future_item]}}
    records = collect_production_negative_evidence({}, features, {}, as_of=AS_OF)
    assert records == []
    assert build_confirmed_negative_blocker("CONFIRMED_DISTRIBUTION", future_item, as_of=AS_OF) is None


def test_missing_event_id_is_not_confirmed():
    item = _evidence(
        observed=True,
        source="lhb",
        available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION",
        source_id="lhb",
        event_id="",
        mechanism="lhb_event",
        observed_at="2026-08-26T14:45:00+08:00",
        as_of=AS_OF.isoformat(),
    )
    assert item["evidence_identity"] is None
    assert validate_evidence_identity(item) is None
    assert _evidence_confirmed(item, as_of=AS_OF) is False


def test_missing_mechanism_is_not_confirmed():
    item = _evidence(
        observed=True,
        source="lhb",
        available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION",
        source_id="lhb",
        event_id="evt-1",
        mechanism="",
        observed_at="2026-08-26T14:45:00+08:00",
        as_of=AS_OF.isoformat(),
    )
    assert item["evidence_identity"] is None
    assert validate_evidence_identity({"source_id": "lhb", "event_id": "evt-1", "mechanism": ""}) is None
    assert _evidence_confirmed(item, as_of=AS_OF) is False


def test_evidence_identity_does_not_fabricate_fallbacks():
    item = _evidence(
        observed=True,
        source="x",
        available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION",
        source_id="x",
        event_id="",
        mechanism="",
        observed_at="2026-08-26T14:45:00+08:00",
        as_of=AS_OF.isoformat(),
    )
    assert item["event_id"] == ""
    assert item["mechanism"] == ""
    assert item["evidence_identity"] is None
    assert item["evidence_identity"] != ("x", "x", "DIRECT_INSTITUTION")
    assert validate_evidence_identity(item) is None


def test_repricing_blocker_uses_repricing_evidence_not_capital():
    capital_item = {
        "source_id": "lhb",
        "event_id": "capital-a",
        "mechanism": "lhb_event",
        "evidence_identity": ("lhb", "capital-a", "lhb_event"),
        "observed": True,
        "direction": "SELL",
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    repricing_item = {
        "source_id": "repricing",
        "event_id": "repricing-b",
        "mechanism": "completed_repricing",
        "evidence_identity": ("repricing", "repricing-b", "completed_repricing"),
        "observed": True,
        "observed_at": "2026-08-26T14:40:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "observed_at_event": "2026-08-26T14:40:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
        "completed": True,
    }
    features = {"CAPITAL": {"institution_behavior": {"evidence": [capital_item]}}}
    alpha = {"repricing_completion": repricing_item}
    records = collect_production_negative_evidence(alpha, features, {}, as_of=AS_OF)
    repricing = [item for item in records if item["blocker"] == "REPRICING_COMPLETED"]
    assert repricing
    assert tuple(repricing[0]["evidence_identity"]) == ("repricing", "repricing-b", "completed_repricing")
    assert tuple(repricing[0]["evidence_identity"]) != ("lhb", "capital-a", "lhb_event")
    distribution = [item for item in records if item["blocker"] == "CONFIRMED_DISTRIBUTION"]
    assert distribution == []


def test_unknown_evidence_identity_is_not_a_blocker():
    item = {
        "source_id": "lhb",
        "event_id": "unk-1",
        "mechanism": "lhb_event",
        "evidence_identity": ("lhb", "unk-1", "lhb_event"),
        "observed": False,
        "direction": "SELL",
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "UNKNOWN",
    }
    records = collect_production_negative_evidence(
        {},
        {"CAPITAL": {"institution_behavior": {"evidence": [item]}}},
        {},
        as_of=AS_OF,
    )
    assert records == []


def test_open_positions_are_isolated_by_decision_identity():
    from xiaogu_db import (
        derive_position_state_by_symbol,
        fetch_open_positions,
        get_position_by_decision_id,
        get_position_by_id,
    )

    rows = [
        {"decision_id": "iso-pos-d1", "lineage_id": "iso-lin-d1", "symbol": "990011", "trade_date": "2026-08-03", "position_state": "LONG"},
        {"decision_id": "iso-pos-d2", "lineage_id": "iso-lin-d2", "symbol": "990011", "trade_date": "2026-08-10", "position_state": "FLAT"},
    ]
    try:
        _seed_isolated_positions(rows)
        opened = fetch_open_positions()
        ids = {row["decision_id"] for row in opened}
        assert "iso-pos-d1" in ids
        assert "iso-pos-d2" not in ids
        first = get_position_by_decision_id("iso-pos-d1")
        second = get_position_by_id("POS|iso-pos-d2")
        assert first["position_id"] == "POS|iso-pos-d1"
        assert first["position_state"] == "LONG"
        assert second["position_state"] == "FLAT"
        assert first["decision_id"] != second["decision_id"]
        assert first["position_id"] != second["position_id"]
        assert first["symbol"] == second["symbol"] == "990011"
        assert derive_position_state_by_symbol("990011") == "LONG"
    finally:
        _cleanup_seeded_positions(["iso-pos-d1", "iso-pos-d2"], ["iso-lin-d1", "iso-lin-d2"])


def test_same_symbol_long_positions_stay_isolated():
    from xiaogu_db import derive_position_state_by_symbol, fetch_open_positions

    rows = [
        {"decision_id": "iso-pos-a", "lineage_id": "iso-lin-a", "symbol": "990012", "trade_date": "2026-08-03", "position_state": "LONG"},
        {"decision_id": "iso-pos-b", "lineage_id": "iso-lin-b", "symbol": "990012", "trade_date": "2026-08-10", "position_state": "LONG"},
    ]
    try:
        _seed_isolated_positions(rows)
        opened = [row for row in fetch_open_positions() if row["decision_id"] in {"iso-pos-a", "iso-pos-b"}]
        assert {row["decision_id"] for row in opened} == {"iso-pos-a", "iso-pos-b"}
        assert {row["position_id"] for row in opened} == {"POS|iso-pos-a", "POS|iso-pos-b"}
        with pytest.raises(RuntimeError, match="POSITION_STATE_AMBIGUOUS"):
            derive_position_state_by_symbol("990012")
    finally:
        _cleanup_seeded_positions(["iso-pos-a", "iso-pos-b"], ["iso-lin-a", "iso-lin-b"])


def test_entry_to_review_keeps_original_and_current_snapshot_identity(monkeypatch):
    import xiaogu_forward_runner as runner

    entry = validate_and_build_canonical_snapshot(_base_snapshot(trade_date="2026-08-26", source_time="2026-08-26T14:50:00+08:00"))
    review = validate_and_build_canonical_snapshot(_base_snapshot(price=12, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    seen = {}
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [_open_position(
        decision_id="D1",
        snapshot_id=entry["snapshot_id"],
        original_snapshot_id=entry["snapshot_id"],
    )])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: review)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"received": received, **kwargs}) or {
            "state": "SELL", "action": "SELL", "trade_status": "CLOSED", "paper_signal_id": "new-should-not-exist",
        },
    )
    monkeypatch.setattr(runner, "_write_ledger_record", lambda _decision: None)
    monkeypatch.setattr(runner, "daily_paper_position_review", lambda _date: [])
    reviewed = runner.daily_position_review("2026-09-01")
    assert seen["account"]["decision_id"] == "D1"
    assert seen["account"]["original_snapshot_id"] == entry["snapshot_id"]
    assert seen["account"]["review_snapshot_id"] == review["snapshot_id"]
    assert seen["account"]["original_snapshot_id"] != seen["account"]["review_snapshot_id"]
    assert seen["mode"] == "PRODUCTION"
    assert reviewed[0]["paper_observation"] is None
    assert "paper_signal_id" not in reviewed[0]


def test_position_identity_not_symbol(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    snapshot = validate_and_build_canonical_snapshot(_base_snapshot(source_time="2026-09-03T09:50:00+08:00", trade_date="2026-09-03"))
    decision = run_production_decision(
        snapshot,
        mode="PRODUCTION",
        trade_date="2026-09-03",
        position_state="FLAT",
        decision_clock=datetime.fromisoformat("2026-09-03T10:00:00+08:00"),
    )
    assert decision["position_state_before"] == "FLAT"
    assert decision["symbol"] == "600001"


def test_same_symbol_two_positions_isolated():
    test_same_symbol_long_positions_stay_isolated()


def test_open_positions_no_distinct_on_symbol():
    from inspect import getsource
    from xiaogu_db import _load_positions, fetch_open_positions

    for fn in (fetch_open_positions, _load_positions):
        source = getsource(fn)
        assert "DISTINCT ON" not in source
        assert "ORDER BY id DESC" not in source
    test_same_symbol_long_positions_stay_isolated()


def test_position_by_decision_id():
    test_open_positions_are_isolated_by_decision_identity()


def test_position_by_id():
    from xiaogu_db import get_position_by_id
    test_open_positions_are_isolated_by_decision_identity()
    assert callable(get_position_by_id)


def test_paper_reduce_rejected(monkeypatch):
    import xiaogu_forward_runner as runner

    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "run_production_decision", lambda *_args, **_kwargs: {"state": "REDUCE", "action": "REDUCE"})
    with pytest.raises(RuntimeError, match="PAPER_REDUCE_UNSUPPORTED"):
        runner.daily_paper_position_review("2026-09-01")


def test_position_review_reduce_is_unsupported(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_base_snapshot())
    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [_open_position(snapshot_id=original["snapshot_id"], original_snapshot_id=original["snapshot_id"])])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(runner, "run_production_decision", lambda *_args, **_kwargs: {"state": "REDUCE", "action": "REDUCE"})
    monkeypatch.setattr(runner, "daily_paper_position_review", lambda _date: [])
    with pytest.raises(RuntimeError, match="REDUCE_UNSUPPORTED"):
        runner.daily_position_review("2026-09-01")


def test_paper_hold_keeps_long(monkeypatch):
    import xiaogu_forward_runner as runner

    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "run_production_decision", lambda *_args, **_kwargs: {"state": "HOLD", "action": "HOLD"})
    reviewed = runner.daily_paper_position_review("2026-09-01")
    assert reviewed[0]["paper_action"] == "PAPER_HOLD"
    assert reviewed[0]["paper_position_state"] == "PAPER_LONG"
    assert reviewed[0]["paper_observation_state"] == "OBSERVED"


def test_paper_sell_closes(monkeypatch):
    import xiaogu_forward_runner as runner

    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    closed = {}
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(
        "xiaogu_db.update_paper_observation_state",
        lambda paper_signal_id, **kwargs: closed.update({"paper_signal_id": paper_signal_id, **kwargs}),
    )
    monkeypatch.setattr(runner, "run_production_decision", lambda *_args, **_kwargs: {"state": "SELL", "action": "SELL", "reason": "THESIS_BROKEN"})
    reviewed = runner.daily_paper_position_review("2026-09-01")
    assert reviewed[0]["paper_action"] == "PAPER_SELL"
    assert reviewed[0]["paper_position_state"] == "PAPER_FLAT"
    assert reviewed[0]["paper_observation_state"] == "CLOSED"
    assert closed["state"] == "CLOSED"
    assert closed["paper_position_state"] == "PAPER_FLAT"


def test_paper_t5_closes(monkeypatch):
    import xiaogu_forward_runner as runner

    current = validate_and_build_canonical_snapshot(_base_snapshot(price=11, trade_date="2026-09-01", source_time="2026-09-01T09:40:00+08:00"))
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "d-original",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": "original-id",
        "original_snapshot_id": "original-id",
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "run_production_decision", lambda *_args, **_kwargs: {"state": "HOLD", "action": "HOLD"})
    reviewed = runner.daily_paper_position_review("2026-09-01")
    assert reviewed[0]["paper_action"] == "PAPER_SELL"
    assert reviewed[0]["paper_position_state"] == "PAPER_FLAT"
    assert reviewed[0]["paper_observation_state"] == "CLOSED"
    assert reviewed[0]["paper_exit_reason"] == "T5_EXPIRY"


def test_paper_current_snapshot(monkeypatch):
    test_paper_review_uses_current_snapshot(monkeypatch)


def test_paper_current_clock(monkeypatch):
    test_paper_review_uses_production_clock(monkeypatch)


def test_negative_future_evidence_excluded():
    test_future_negative_evidence_is_not_a_production_blocker()


def test_negative_missing_identity_excluded():
    test_missing_event_id_is_not_confirmed()


def test_distribution_requires_distribution_mechanism():
    item = {
        "source_id": "lhb",
        "event_id": "dist-1",
        "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "dist-1", "distribution_risk"),
        "observed": True,
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    records = collect_production_negative_evidence(
        {},
        {"CAPITAL": {"distribution_evidence": [item]}},
        {},
        as_of=AS_OF,
    )
    assert records and records[0]["blocker"] == "CONFIRMED_DISTRIBUTION"
    assert records[0]["mechanism"] == "distribution_risk"


def test_capital_evidence_is_not_a_distribution_blocker():
    item = {
        "source_id": "lhb",
        "event_id": "dist-cap-1",
        "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "dist-cap-1", "distribution_risk"),
        "observed": True,
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    records = collect_production_negative_evidence(
        {},
        {"CAPITAL": {"institution_behavior": {"evidence": [item]}}},
        {},
        as_of=AS_OF,
    )
    assert records == []


def test_sell_direction_not_distribution_by_itself():
    item = {
        "source_id": "lhb",
        "event_id": "sell-1",
        "mechanism": "lhb_event",
        "evidence_identity": ("lhb", "sell-1", "lhb_event"),
        "observed": True,
        "direction": "SELL",
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    records = collect_production_negative_evidence(
        {},
        {"CAPITAL": {"institution_behavior": {"evidence": [item]}}},
        {},
        as_of=AS_OF,
    )
    assert records == []
    assert build_confirmed_negative_blocker("CONFIRMED_DISTRIBUTION", item, as_of=AS_OF) is None


def test_blocker_own_evidence():
    supply_item = {
        "source_id": "supply",
        "event_id": "sup-1",
        "mechanism": "supply_reversal",
        "evidence_identity": ("supply", "sup-1", "supply_reversal"),
        "observed": True,
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "as_of": AS_OF.isoformat(),
        "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK",
        "confirmation_status": "CONFIRMED",
    }
    assert build_confirmed_negative_blocker("CONFIRMED_SUPPLY_REVERSAL", supply_item, as_of=AS_OF)
    assert build_confirmed_negative_blocker("CONFIRMED_DISTRIBUTION", supply_item, as_of=AS_OF) is None
    assert build_confirmed_negative_blocker("REPRICING_COMPLETED", supply_item, as_of=AS_OF) is None


def test_capital_lhb_one_origin():
    test_same_lhb_event_counts_as_one_independent_origin()


def test_production_clock_now():
    test_production_clock_is_not_source_time()


def test_replay_clock_allowed_only_replay(monkeypatch):
    test_replay_clock_may_use_historical_source_time(monkeypatch)


def test_health_runtime_position_contract():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "xiaogu_daily_health_check",
        "scripts/xiaogu_daily_health_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, detail = mod.check_position_review_contract()
    assert ok, detail
    ok, detail = mod.check_position_identity_contract()
    assert ok, detail


def test_health_runtime_evidence_contract():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "xiaogu_daily_health_check",
        "scripts/xiaogu_daily_health_check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, detail = mod.check_evidence_identity_contract()
    assert ok, detail
    ok, detail = mod.check_negative_evidence_contract()
    assert ok, detail
    ok, detail = mod.check_gate_contract()
    assert ok, detail
    ok, detail = mod.check_capital_identity_contract()
    assert ok, detail
    ok, detail = mod.check_reduce_not_flat()
    assert ok, detail
