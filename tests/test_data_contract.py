from pathlib import Path
import json
import sys
import calendar as calendar_module
from datetime import date, timedelta
import pytest

from xiaogu_forward_eligibility import candidate_universe
from xiaogu_forward_features import FEATURE_GROUPS, build_feature_vector
from xiaogu_forward_snapshot import attach_research_observations, validate_and_build_canonical_snapshot
from xiaogu_portfolio_decision import evaluate_candidate_bundle
from scrapy_scanner.runner_v2 import build_canonical_snapshots, detect_capital_candidates


def test_snapshot_has_no_future_fields_and_features_are_measurements():
    with pytest.raises(ValueError):
            validate_and_build_canonical_snapshot({"symbol": "600001", "future_5d_return": 0.1})
    with pytest.raises(ValueError):
        validate_and_build_canonical_snapshot({
            "lineage_id": "already-canonical",
            "raw": {"symbol": "600001", "future_5d_return": 0.1},
        })
    assert validate_and_build_canonical_snapshot({"symbol": "600001", "source_time": "2026-08-25 14:39:45+08"})["source_time"].endswith("+08:00")
    vector = build_feature_vector(validate_and_build_canonical_snapshot({"symbol": "600001", "price": 10, "source_time": "2026-08-26T14:50:00+00:00"}))
    assert tuple(key for key in FEATURE_GROUPS if key in vector) == FEATURE_GROUPS


def test_nested_future_outcomes_are_rejected_before_feature_measurement():
    with pytest.raises(ValueError, match=r"raw\.research\.labels"):
        validate_and_build_canonical_snapshot({
            "symbol": "600001",
            "raw": {"research": {"labels": {"future_5d_return": 0.1}}},
        })
    with pytest.raises(ValueError, match=r"\$\.lhb\[0\]\.post_result"):
        attach_research_observations(
            {"symbol": "600001"},
            lhb=[{"post_result": {"return": 0.1}}],
        )


def test_eastmoney_quote_fields_feed_existing_measurements():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "f12": "600001", "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
        "f7": 3, "f8": 2, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 400,
    }))
    assert vector["capital"]["capital_flow_ratio"] == 0.4
    assert vector["capital"]["accumulation"] is None
    assert vector["position"]["relative_strength"] == 0.4
    assert vector["execution"]["short_term_overheat"] == 0.4


def test_nested_capital_aliases_feed_ratio_without_inventing_persistence():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "raw": {"signal_amount": 1_000, "net_inflow_main": 400},
        "source_time": "2026-08-26T14:50:00+08:00",
    }))
    assert vector["CAPITAL"]["capital_flow_ratio"] == 0.4
    assert vector["CAPITAL"]["fund_flow_persistence"] is None
    assert vector["CAPITAL"]["fund_flow_acceleration"] is None


def test_eastmoney_capital_flow_reaches_core_alpha():
    decision = evaluate_candidate_bundle({
        "f12": "600001", "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
        "f7": 3, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 400,
        "source_time": "2026-08-26T14:50:00+08:00",
    })
    assert decision["research_context"]["capital"]["capital_flow_ratio"] == 0.4
    assert decision["research_context"]["capital"]["accumulation"] is None
    assert decision["core_alpha"]["axes"]["CAPITAL"] > 0


@pytest.mark.parametrize(("flow", "change", "expected"), [
    (400, 3, "DEMAND_RESPONSE_OBSERVATION"),
    (400, 0, "CAPITAL_PRICE_DIVERGENCE"),
    (400, -3, "CAPITAL_PRICE_DIVERGENCE"),
    (-400, 3, "CAPITAL_PRICE_DIVERGENCE"),
    (-400, -3, "DISTRIBUTION_RISK"),
])
def test_capital_price_impact_distinguishes_flow_and_price_response(flow, change, expected):
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "amount": 1_000, "f62": flow, "f3": change, "f184": 2,
    }))
    assert vector["CAPITAL"]["capital_price_impact_state"] == expected


def test_same_day_research_observations_feed_existing_contexts():
    snapshot = validate_and_build_canonical_snapshot(attach_research_observations(
        {
            "f12": "600001", "f14": "示例公司", "f100": "示例行业",
            "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
            "f7": 3, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 100,
            "source_time": "2026-08-26T14:50:00+08:00",
        },
            stock_capital_flow={"f62": 400, "observed_at": "2026-08-26T14:49:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
            earnings_preview={"WEIGHTAVG_ROE": 20, "publication_time": "2026-08-26T14:40:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
            industry_flow={"f3": 5, "observed_at": "2026-08-26T14:49:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"},
            stock_reports=[{"title": "公司研究", "publication_time": "2026-08-26T14:40:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"}],
            industry_reports=[{"title": "行业研究", "publication_time": "2026-08-26T14:40:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"}],
        lhb=[{
            "EXPLAIN": "1家机构买入", "NET_BS_AMT": -100,
            "ACCUM_AMOUNT": 100, "event_time": "2026-08-26T14:45:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
        }],
    ))
    decision = evaluate_candidate_bundle(snapshot)
    assert decision["feature_vector"]["company"]["financial_quality"] == 0.2
    assert decision["feature_vector"]["market"]["alignment"] == 0.5
    assert decision["research_context"]["company"]["earnings_preview"]["WEIGHTAVG_ROE"] == 20
    assert decision["research_context"]["industry"]["reports"][0]["title"] == "行业研究"
    assert decision["research_context"]["capital"]["institution_vs_hot_money"] == "institution"
    assert decision["research_context"]["capital"]["capital_divergence"] == 1.0


def test_scanner_fixture_only_emits_canonical_market_observations():
    snapshots = build_canonical_snapshots({
        "stock_all_a": [{"f12": "600001", "f14": "示例公司", "f2": 10, "f3": 1, "f6": 1_000, "f100": "示例行业"}],
        "stock_capital_flow": [], "earnings_preview": [], "stock_reports": [],
        "lhb": [], "announcements": [], "flow_industry": [], "industry_reports": [],
    }, "2026-08-26 14:50:00")
    assert len(snapshots) == 1
    assert snapshots[0]["source_version"] == "canonical_snapshot_v2"
    for forbidden in ("candidate_rank", "strategy_score", "recommendation", "setup_type"):
        assert forbidden not in snapshots[0]


def test_scanner_levels_keep_light_universe_and_deep_fetch_only_candidates():
    stocks = [
        {
            "f12": "600001", "f14": "候选", "f2": 10, "f3": 3, "f5": 100,
            "f6": 1_000, "f8": 2, "f10": 1.2, "f15": 10.5, "f16": 9.5, "f62": 500,
            "f100": "示例行业",
        },
        {
            "f12": "600002", "f14": "轻量", "f2": 10, "f3": 0, "f5": 100,
            "f6": 1_000, "f8": 0.5, "f10": 0.8, "f15": 10.5, "f16": 9.5,
            "f100": "示例行业",
        },
    ]
    candidates, audit = detect_capital_candidates(stocks)
    assert [row["f12"] for row in candidates] == ["600001"]
    assert audit["selection"] is False
    assert audit["ranking"] is False
    assert audit["alpha"] is False

    snapshots = build_canonical_snapshots(
        {
            "stock_all_a": stocks,
            "stock_capital_flow": [{"f12": "600001", "f62": 500}, {"f12": "600002", "f62": 900}],
            "earnings_preview": [], "stock_reports": [], "lhb": [], "announcements": [],
            "org_survey": [{"SECURITY_CODE": "600001", "survey": "observed"}],
            "news_kuaixun": [{"SECURITY_CODE": "600001", "title": "observed"}],
            "flow_industry": [], "industry_reports": [],
        },
        "2026-08-26T14:50:00+08:00",
        symbols=["600001"],
    )
    assert {row["symbol"] for row in snapshots} == {"600001", "600002"}
    by_symbol = {row["symbol"]: row for row in snapshots}
    assert by_symbol["600001"]["raw"]["stock_capital_flow"]["f62"] == 500
    assert by_symbol["600001"]["raw"]["org_surveys"][0]["survey"] == "observed"
    assert by_symbol["600001"]["raw"]["news"][0]["title"] == "observed"
    assert "stock_capital_flow" not in by_symbol["600002"]["raw"]
    assert "L3_DEEP_CANDIDATE_FETCH" in by_symbol["600001"]["source_layers"]
    assert "L3_DEEP_CANDIDATE_FETCH" not in by_symbol["600002"]["source_layers"]


def test_l2_does_not_route_without_price_window():
    stocks = [
        {
            "f12": "600001", "f14": "无价格窗口", "f2": 10, "f3": 0.1, "f5": 100,
            "f6": 1_000, "f8": 2, "f10": 1.2, "f15": 10.5, "f16": 9.5, "f62": 500,
            "f100": "示例行业",
        },
        {
            "f12": "600002", "f14": "有价格窗口", "f2": 10, "f3": 3, "f5": 100,
            "f6": 1_000, "f8": 2, "f10": 1.2, "f15": 10.5, "f16": 9.5, "f62": 500,
            "f100": "示例行业",
        },
    ]
    candidates, audit = detect_capital_candidates(stocks)
    assert [row["f12"] for row in candidates] == ["600002"]
    routed = {item["symbol"]: item for item in audit["routing"]}
    assert routed["600001"]["deep_fetch_required"] is False
    assert "price_response" not in routed["600001"]["routing_reasons"]
    assert routed["600002"]["deep_fetch_required"] is True
    assert "price_response" in routed["600002"]["routing_reasons"]
    assert audit["selection"] is False


def test_missing_measurements_remain_unknown_instead_of_zero_fill():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1_000,
        "source_time": "2026-08-26T14:50:00+08:00",
    }))
    assert vector["CAPITAL"]["fund_flow_acceleration"] is None
    assert vector["SUPPLY"]["effective_supply"] is None
    assert vector["PRICING_GAP"]["score"] is None
    assert vector["MARKET"]["leader_strength"] is None
    assert vector["RISK"]["event_risk"] is None
    assert vector["EXECUTION"]["execution_feasibility"] is None


def test_candidate_universe_is_cheap_and_has_no_alpha_fields():
    eligible, audit = candidate_universe([
        {"symbol": "600001", "price": 10, "volume": 100, "amount": 1000},
        {"symbol": "600002", "price": 10, "volume": 0, "amount": 1000, "score": 1},
    ])
    assert [row["symbol"] for row in eligible] == ["600001"]
    assert audit["selection"] is False
    assert audit["ranking"] is False
    assert audit["alpha"] is False


def _paper_observation_snapshot():
    return {
        "symbol": "600001", "price": 10, "open": 9.9, "high": 10.3, "low": 9.7,
        "amount": 1_000, "volume": 100, "pct_chg": 3,
        "buyable": True, "liquidity_score": 1, "execution_quality": 1,
        "gap_risk": 0, "slippage": 0, "spread": 0, "market_impact": 0,
        "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
        "f12": "600001", "f13": 1, "f1": 2, "market": "SH",
    }


def test_paper_observation_identity():
    decision = evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
        as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
    )
    paper = decision["paper_observation"]
    assert paper["status"] == "PAPER_OBSERVATION"
    assert paper["paper_signal_id"]
    assert paper["decision_id"] == decision["decision_id"]
    assert paper["paper_signal_id"] != paper["decision_id"]
    assert paper["signal_reason"] == "FORMAL_5D_PROFIT_WINDOW_SIGNAL"
    assert paper["alpha_name"] == "price_strength"
    assert paper["alpha_status"] == "DATA_INSUFFICIENT"
    assert decision["state"] != "BUY"
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert paper["paper_only"] is True
    assert paper["live_order"] is False
    assert paper["cost_model_version"] == "cost_model_v1"


def test_paper_observation_defaults_flat():
    paper = evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
    )["paper_observation"]
    assert paper["paper_observation_state"] == "OBSERVED"
    assert paper["paper_position_state"] == "PAPER_FLAT"


def test_out_of_window_price_is_not_a_formal_paper_signal():
    decision = evaluate_candidate_bundle(
        _paper_observation_snapshot() | {"pct_chg": 0.1},
        position_state="FLAT",
    )
    assert decision["core_alpha"]["signal_qualified"] is False
    assert decision["core_alpha"]["signal_reason"] == "PRICE_STRENGTH_OUT_OF_WINDOW"
    assert decision["paper_observation"] is None
    assert decision["buy_status"] == "BUY_BLOCKED"


def test_research_only_capital_does_not_change_production_decision():
    baseline = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    research_only = evaluate_candidate_bundle(
        _paper_observation_snapshot() | {
            "f62": -1_000,
            "pct_chg": -1,
            "lhb": [
                {
                    "EXPLAIN": "institution and hot money conflict",
                    "institution": True,
                    "hot_money": True,
                    "游资": True,
                    "NET_BS_AMT": -100,
                    "event_time": "2026-08-31T14:45:00+08:00",
                    "available_at": "2026-08-31T14:50:00+08:00",
                }
            ],
        },
        position_state="FLAT",
    )
    assert research_only["state"] == baseline["state"]
    assert research_only["action"] == baseline["action"]
    assert research_only["buy_status"] == "BUY_BLOCKED"
    overlay = (research_only["paper_observation"] or {}).get("research_overlay") or {"research_only": True}
    assert overlay["research_only"] is True


def test_research_cannot_grant_production_permission(monkeypatch):
    import xiaogu_portfolio_decision as decision_module

    baseline = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    original = decision_module.build_integrated_research_context

    def elevated_research(snapshot, features):
        context = original(snapshot, features)
        return {
            **context,
            "integrated": {"contradiction_status": "BULLISH", "veto": False},
            "future_buyer_map": {
                "potential_next_buyer": [{
                    "buyer": "institution",
                    "evidence_status": "EVIDENCE_BACKED",
                    "evidence": "research-only",
                    "source": "research",
                    "observed_at": "2026-08-26T14:50:00+08:00",
                }],
            },
        }

    monkeypatch.setattr(decision_module, "build_integrated_research_context", elevated_research)
    candidate = decision_module.evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
    )
    assert candidate["state"] == baseline["state"]
    assert candidate["buy_status"] == "BUY_BLOCKED"
    assert candidate["core_alpha"]["profit_window_feature_values"] == {"price_strength": baseline["core_alpha"]["profit_window_feature_values"]["price_strength"]}


def test_production_position_state_unavailable_blocks_before_decision(monkeypatch):
    from datetime import datetime
    import xiaogu_db
    from xiaogu_forward_runner import run_production_decision

    snapshot = validate_and_build_canonical_snapshot({
        **_paper_observation_snapshot(),
        "trade_date": "2026-08-26",
    })
    monkeypatch.setattr(xiaogu_db, "verify_persisted_snapshot", lambda **_kwargs: True)
    with pytest.raises(RuntimeError, match="POSITION_STATE_UNAVAILABLE"):
        run_production_decision(
            snapshot,
            mode="PRODUCTION",
            trade_date="2026-08-26",
            decision_clock=datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
        )


def test_direct_evaluation_does_not_expose_missing_position_as_flat():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot())
    assert decision["position_state_status"] == "UNAVAILABLE"
    assert decision["position_state_before"] is None
    assert decision["position_state"] is None
    assert decision["state"] == "WATCH"
    assert decision["reason"] == "POSITION_STATE_UNAVAILABLE"
    assert decision["paper_observation"] is None


def test_paper_observation_daily_freeze_uses_snapshot_identity_not_runner_clock():
    first = evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
        as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
    )
    second = evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
        as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:30:00+08:00"),
    )
    assert first["decision_id"] == second["decision_id"]
    assert first["paper_observation"]["paper_signal_id"] == second["paper_observation"]["paper_signal_id"]
    assert first["paper_observation"]["model_version"] == "v4"


def test_paper_observation_no_live_order(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    decision = evaluate_candidate_bundle(
        _paper_observation_snapshot(), position_state="FLAT",
        as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
    )
    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "audit.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
    stored = []
    monkeypatch.setattr(xiaogu_db, "record_paper_observation", lambda item: stored.append(item))
    _path, record = recorder.append_paper_observation(decision)
    assert len(stored) == 1
    assert stored[0]["status"] == "PAPER_OBSERVATION"
    assert stored[0]["paper_observation_state"] == "OBSERVED"
    assert stored[0]["paper_position_state"] == "PAPER_FLAT"
    assert record["decision"] == "PAPER_OBSERVATION"
    assert record["manual_paper_execution_allowed"] is False
    assert record["auto_order"] is False
    assert record["broker_connected"] is False
    assert record["future_5d_return"] is None


def test_observation_does_not_create_production_decision(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    calls = []
    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "audit.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(xiaogu_db, "paper_observation_exists", lambda _value: False)
    monkeypatch.setattr(xiaogu_db, "record_decision", lambda _value: calls.append("decision"))
    monkeypatch.setattr(xiaogu_db, "record_paper_observation", lambda _value: calls.append("observation"))
    recorder.append_paper_observation(decision)
    assert calls == ["observation"]


def test_paper_requires_existing_decision():
    import xiaogu_db

    paper = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")["paper_observation"]
    with pytest.raises(ValueError, match="DECISION_ID_NOT_FOUND"):
        xiaogu_db.record_paper_observation(paper)


def test_paper_fk_to_decision():
    import xiaogu_db

    xiaogu_db.ensure_production_schema()
    audit = xiaogu_db.audit_production_schema()
    assert audit["tables"]["paper_observations"]["foreign_keys"]["decision_id->picks.decision_id"] == "EXISTS"


def test_paper_observation_not_open_position(monkeypatch):
    import xiaogu_db

    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [{
        "paper_signal_id": "paper-1", "decision_id": "decision-1",
        "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_FLAT",
        "payload": {"paper_signal_id": "paper-1", "decision_id": "decision-1",
                    "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_FLAT"},
    }])
    assert xiaogu_db.fetch_open_paper_positions() == []


def test_real_paper_entry_only_is_open(monkeypatch):
    import xiaogu_db

    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [
        {
            "paper_signal_id": "observation", "decision_id": "decision-1",
            "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_FLAT",
            "payload": {"paper_signal_id": "observation", "decision_id": "decision-1",
                        "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_FLAT"},
        },
        {
            "paper_signal_id": "entry", "decision_id": "decision-2",
            "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_LONG",
            "payload": {
                "paper_signal_id": "entry", "decision_id": "decision-2",
                "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_LONG",
                "paper_entry_contract": {"entry_price": 10},
            },
        },
    ])
    assert [row["paper_signal_id"] for row in xiaogu_db.fetch_open_paper_positions()] == ["entry"]


def test_paper_observation_db_truth(monkeypatch):
    import xiaogu_api
    import xiaogu_db

    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [{
        "paper_signal_id": "paper-signal-1", "decision_id": "decision-1",
        "symbol": "600001", "reference_price": 10,
        "signal_time": "2026-08-26T14:50:00+08:00",
        "paper_observation_state": "OBSERVED", "paper_position_state": "PAPER_FLAT",
        "payload": {"paper_signal_id": "paper-signal-1", "decision_id": "decision-1",
                    "status": "PAPER_OBSERVATION", "price_strength": 0.6,
                    "signal_reason": "CURRENT_PRODUCTION_DECISION"},
    }])
    monkeypatch.setattr(xiaogu_db, "fetch_returns", lambda: [])
    payload = xiaogu_api.paper_signals()
    assert payload["status"] == "PAPER_OBSERVATION_ONLY"
    assert payload["count"] == 1
    assert payload["signals"][0]["paper_signal_id"] == "paper-signal-1"
    assert payload["signals"][0]["paper_observation_state"] == "OBSERVED"
    assert xiaogu_api.paper_open()["count"] == 0


def test_paper_no_future_leakage():
    with pytest.raises(ValueError, match="FUTURE_LEAKAGE"):
        evaluate_candidate_bundle(_paper_observation_snapshot() | {"future_5d_return": 0.2})


def test_paper_model_version():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    assert decision["paper_observation"]["model_version"] == "v4"
    assert decision["paper_observation"]["feature_version"] == "minimal_price_alpha_v1"
    assert decision["paper_observation"]["cost_model_version"] == "cost_model_v1"


def test_paper_daily_freeze():
    first = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT", as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:00:00+08:00"))
    second = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT", as_of=__import__("datetime").datetime.fromisoformat("2026-08-26T15:30:00+08:00"))
    assert first["paper_observation"]["paper_signal_id"] == second["paper_observation"]["paper_signal_id"]


def test_paper_performance(monkeypatch):
    import xiaogu_api
    import xiaogu_db

    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [{
        "paper_signal_id": "paper-signal-2", "decision_id": "paper-2",
        "symbol": "600002", "reference_price": 10,
        "signal_time": "2026-08-26T14:50:00+08:00",
        "payload": {"paper_signal_id": "paper-signal-2", "decision_id": "paper-2",
                    "status": "PAPER_OBSERVATION", "price_strength": 0.7},
    }])
    monkeypatch.setattr(xiaogu_db, "fetch_returns", lambda: [{
        "decision_id": "paper-2", "payload": {"decision_id": "paper-2", "outcome_complete": True,
            "profit_window": True, "future_5d_net_return": 0.03, "max_mae_5d": -0.01,
            "future_5d_mfe": 0.04, "first_profit_day": 2},
    }])
    payload = xiaogu_api.paper_performance()
    assert payload["status"] == "PAPER_OBSERVATION_ONLY"
    assert payload["performance"]["profit_window_rate"] == 1
    assert payload["performance"]["horizon_metrics"]["T+5"]["mean_net_return"] == 0.03


def test_paper_performance_is_read_only(monkeypatch):
    import xiaogu_api
    import xiaogu_db

    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [])
    monkeypatch.setattr(xiaogu_db, "fetch_returns", lambda: [])
    monkeypatch.setattr(xiaogu_db, "record_decision", lambda _value: pytest.fail("unexpected decision write"))
    monkeypatch.setattr(xiaogu_db, "record_paper_observation", lambda _value: pytest.fail("unexpected paper write"))
    assert xiaogu_api.paper_performance()["performance"]["count"] == 0


def test_paper_research_overlay():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    overlay = decision["paper_observation"]["research_overlay"]
    assert overlay["research_only"] is True
    assert "capital_flow_ratio" in overlay
    assert "supply" in overlay
    assert "repricing" in overlay


def test_shadow_not_production():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    assert decision["state"] != "BUY"
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert decision["paper_observation"].get("shadow") is None


def test_research_context_is_consumed_by_alpha():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    alpha = decision["core_alpha"]
    research = decision["research_context"]
    assert alpha["research_consumed"] is True
    assert research["status"] == "RESEARCH_ONLY"
    providers = {item["provider"]: item for item in research["research_provenance"]}
    assert providers["Serenity"]["invoked"] is True
    assert providers["Buffett"]["invoked"] is True
    assert providers["UZI"]["invoked"] is True
    assert providers["Contradiction"]["invoked"] is True
    assert providers["Serenity"]["role"] == "evidence"
    assert research["status"] != "BUY"
    assert "PICK" not in str(research.get("status"))


def test_signal_qualified_paper_stays_buy_blocked():
    decision = evaluate_candidate_bundle(_paper_observation_snapshot(), position_state="FLAT")
    assert decision["core_alpha"]["signal_qualified"] is True
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert decision["state"] != "BUY"
    paper = decision["paper_observation"]
    assert paper["status"] == "PAPER_OBSERVATION"
    assert paper["production_buy"] == "BLOCKED"
    assert paper["signal_reason"] == "FORMAL_5D_PROFIT_WINDOW_SIGNAL"


def test_batch_keeps_at_most_three_papers_and_one_top1():
    from xiaogu_forward_runner import evaluate_candidate_rows

    rows = []
    for index, pct in enumerate((1.0, 2.0, 3.0, 4.0, 5.0, 0.1)):
        symbol = f"60000{index + 1}"
        rows.append(validate_and_build_canonical_snapshot(_paper_observation_snapshot() | {
            "symbol": symbol, "f12": symbol, "pct_chg": pct,
        }))
    decisions, _accounting = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    papers = [item["paper_observation"] for item in decisions if item.get("paper_observation")]
    assert len(papers) <= 3
    assert len(papers) == 3
    assert sum(1 for paper in papers if paper.get("top1_flag")) == 1
    assert all(paper.get("top3_flag") for paper in papers)
    assert sorted(paper["rank"] for paper in papers) == [1, 2, 3]
    assert all(item["buy_status"] == "BUY_BLOCKED" for item in decisions)
    assert len(papers) < len(rows)


def test_dry_run_report_exposes_top_papers_and_research_summary():
    from xiaogu_forward_runner import (
        _compact_paper_observation,
        _public_decision,
        _research_summary,
        evaluate_candidate_rows,
    )

    rows = [
        validate_and_build_canonical_snapshot(_paper_observation_snapshot() | {
            "symbol": f"60000{index + 1}", "f12": f"60000{index + 1}", "pct_chg": pct,
        })
        for index, pct in enumerate((1.0, 2.0, 3.0, 4.0, 0.1))
    ]
    decisions, _accounting = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    summary = _research_summary(decisions)
    papers = [item for item in (_compact_paper_observation(decision) for decision in decisions) if item]
    public = [_public_decision(decision) for decision in decisions]
    assert summary["research_consumed_count"] == len(decisions)
    providers = {item["provider"]: item for item in summary["research_provenance"]}
    assert providers["Serenity"]["invoked_count"] == len(decisions)
    assert providers["Buffett"]["invoked_count"] == len(decisions)
    assert providers["UZI"]["invoked_count"] == len(decisions)
    assert providers["Contradiction"]["invoked_count"] == len(decisions)
    assert len(papers) == 3
    assert sum(1 for paper in papers if paper["top1_flag"]) == 1
    assert all(item["buy_status"] == "BUY_BLOCKED" for item in public)
    assert all("research_consumed" in item for item in public)


def test_no_signal_when_formal_signals_are_zero():
    from xiaogu_forward_runner import _scan_status_from_run

    status, reason = _scan_status_from_run(
        paper_count=0, decision_count=8, freshness_blocked=0, buy_allowed=0, qualified_signal_count=0,
    )
    assert status == "NO_SIGNAL"
    assert reason == "NO_FORMAL_SIGNAL"


def test_buy_blocked_does_not_delete_paper():
    from xiaogu_forward_runner import _scan_status_from_run

    status, reason = _scan_status_from_run(
        paper_count=3, decision_count=10, freshness_blocked=0, buy_allowed=0, qualified_signal_count=8,
    )
    assert status == "BUY_BLOCKED"
    assert reason == "PAPER_OBSERVATION_RECORDED"


def test_recorder_requires_canonical_snapshot_for_production_decision(tmp_path, monkeypatch):
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
    with pytest.raises(ValueError, match="CANONICAL_SNAPSHOT_REQUIRED"):
        recorder.append_production_decision({"state": "WATCH"})
    assert not list((tmp_path / "memory").glob("**/*.md"))


def test_recorder_persists_buy_and_queues_memory_without_bridge(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(recorder, "MEMORY_RETRY_QUEUE", tmp_path / "memory_retry.jsonl")
    monkeypatch.setattr(xiaogu_db, "record_snapshot", lambda _snapshot: None)
    monkeypatch.setattr(xiaogu_db, "record_decision", lambda _decision: None)
    decision = {
        "state": "BUY", "reason": "REPRICING_READY",
        "canonical_snapshot": {
            "lineage_id": "lineage", "trade_date": "2026-08-26",
            "source_time": "2026-08-26T14:50:00+08:00", "symbol": "600001", "price": 10,
        },
    }
    path, record = recorder.append_production_decision(decision)
    assert path.exists()
    assert record["rule_version"] == "repricing_production_v1"
    assert record["database_persistence"]["status"] == "PASS"
    assert record["future_5d_return"] is None
    assert record["max_daily_bar_profit_opportunity_5d"] is None
    assert record["future_1d_return"] is None
    assert record["auto_order"] is False
    assert record["memory_path"] is None
    assert record["memory_status"] == "RETRY_QUEUED"
    assert (tmp_path / "memory_retry.jsonl").exists()


def test_post_trade_review_queues_memory_without_bridge(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder
    from xiaogu_forward_result_filler_v0_1 import append_result

    monkeypatch.setattr(recorder, "MEMORY_RETRY_QUEUE", tmp_path / "memory_retry.jsonl")
    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setattr(xiaogu_db, "record_snapshot", lambda _snapshot: None)
    monkeypatch.setattr(xiaogu_db, "record_decision", lambda _decision: None)
    decision = {
        "state": "BUY", "reason": "READY", "decision_id": "decision-1",
        "canonical_snapshot": {
            "lineage_id": "lineage", "trade_date": "2026-08-26",
            "source_time": "2026-08-26T14:50:00+08:00", "symbol": "600001", "price": 10,
        },
        "core_alpha": {"capital_convergence": {"status": "CONVERGENCE", "levels": {"institution": "HIGH"}}, "supply_absorption": 0.8, "future_buyer_capacity": 0.8, "pricing_gap": 0.8},
        "research_context": {}, "thesis": {"invalidation": ["capital exit"]},
    }
    recorder.append_production_decision(decision)
    record = {"date": "2026-08-26", "symbol": "600001", "id": "decision-1", "rule_version": "repricing_production_v1", "features_used": decision, "entry_contract": {"signal_time": "2026-08-26T14:50:00+00:00", "execution_time": "2026-08-26T14:50:00+00:00", "execution_price": 10, "entry_price": 10, "price_basis": "UNADJUSTED"}}
    bars = [
        {"date": f"2026-09-{day:02d}", "open": 10, "high": 10.5, "low": 9, "close": 10.2, "volume": 100}
        for day in range(1, 6)
    ]
    result = append_result(record, future_bars=bars)
    assert result["result_status"] == "SETTLED"
    assert result["post_trade_review"]["status"] == "SUCCESS"
    path = recorder.update_trade_memory(result)
    assert path is None
    assert (tmp_path / "memory_retry.jsonl").exists()


def test_memory_api_uses_bridge_query_not_vault_scan(monkeypatch):
    import xiaogu_api

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"notes":[{"decision_id":"decision-1"}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setenv("XIAOGU_OBSIDIAN_BRIDGE_URL", "http://bridge.local")
    monkeypatch.setattr(xiaogu_api, "urlopen", fake_urlopen)
    payload = xiaogu_api.memory(
        date="2026-08-26",
        decision_id="decision-1",
        paper_signal_id="paper-signal-1",
        limit=5,
    )
    assert payload == {"status": "OK", "notes": [{"decision_id": "decision-1"}]}
    assert "date=2026-08-26" in captured["url"]
    assert "decision_id=decision-1" in captured["url"]
    assert "paper_signal_id=paper-signal-1" in captured["url"]


def test_position_review_keeps_previous_state_and_action_separate(monkeypatch):
    import xiaogu_forward_runner as runner

    seen = {}
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "position_id": "POS|d1",
        "symbol": "600001",
        "trade_date": "2026-08-25",
        "state": "HOLD",
        "action": "BUY",
        "decision_id": "d1",
        "position_state": "LONG",
        "snapshot_id": "orig-1",
        "original_snapshot_id": "orig-1",
    }])
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 1)
    original = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-25T14:50:00+08:00", "trade_date": "2026-08-25",
    })
    current = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10.2, "volume": 110, "amount": 1100,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
    })
    monkeypatch.setattr("xiaogu_db.fetch_decision_snapshot", lambda *_args, **_kwargs: original)
    monkeypatch.setattr(
        "xiaogu_db.get_current_position_review_snapshot",
        lambda **_kwargs: current,
    )
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"snapshot_id": received["snapshot_id"], **kwargs}) or {
            "state": "HOLD", "action": "HOLD", "trade_status": "NOT_OPEN",
        },
    )
    monkeypatch.setattr(runner, "_write_ledger_record", lambda _decision: Path("/tmp/unused"))
    runner.daily_position_review("2026-08-26")
    assert seen["portfolio_state"] == "HOLD"
    assert seen["previous_action"] == "BUY"
    assert seen["snapshot_id"] == current["snapshot_id"]
    assert seen["snapshot_id"] != original["snapshot_id"]
    assert seen["trade_date"] == "2026-08-26"
    assert "decision_clock" not in seen or seen.get("decision_clock") is None or seen.get("decision_clock") != original.get("source_time")


def test_position_review_exact_snapshot_identity(monkeypatch):
    import xiaogu_forward_runner as runner

    original = validate_and_build_canonical_snapshot(_paper_observation_snapshot())
    current = validate_and_build_canonical_snapshot({
        **_paper_observation_snapshot(),
        "price": 11,
        "trade_date": "2026-08-27",
        "source_time": "2026-08-27T14:50:00+08:00",
    })
    seen = {}
    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "decision-1",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "snapshot_id": original["snapshot_id"],
        "original_snapshot_id": original["snapshot_id"],
        "paper_position_state": "PAPER_LONG",
        "paper_entry_contract": {"entry_price": 10},
    }])
    monkeypatch.setattr("xiaogu_db.get_current_position_review_snapshot", lambda **_kwargs: current)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda _decision_id: {})
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        runner,
        "run_production_decision",
        lambda received, **kwargs: seen.update({"snapshot_id": received["snapshot_id"], **kwargs}) or {
            "state": "HOLD", "action": "HOLD", "reason": "THESIS_INTACT",
        },
    )
    reviewed = runner.daily_paper_position_review("2026-08-27")
    assert len(reviewed) == 1
    assert seen["snapshot_id"] == current["snapshot_id"]
    assert seen["snapshot_id"] != original["snapshot_id"]
    assert seen["mode"] == "PRODUCTION"
    assert reviewed[0]["paper_signal_id"] == "paper-1"
    assert reviewed[0]["original_snapshot_id"] == original["snapshot_id"]
    assert reviewed[0]["review_snapshot_id"] == current["snapshot_id"]


def test_missing_snapshot_id_blocks_review(monkeypatch):
    import xiaogu_forward_runner as runner

    monkeypatch.setattr("xiaogu_db.update_paper_observation_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("xiaogu_db.fetch_open_paper_positions", lambda: [{
        "paper_signal_id": "paper-1", "decision_id": "decision-1", "trade_date": "2026-08-26", "symbol": "600001",
    }])
    with pytest.raises(RuntimeError, match="PAPER_POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE"):
        runner.daily_paper_position_review("2026-08-27")


def test_position_review_blocks_when_calendar_input_is_unavailable(monkeypatch):
    import xiaogu_forward_runner as runner

    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "position_id": "POS|decision-1",
        "symbol": "600001",
        "trade_date": "",
        "state": "HOLD",
        "action": "HOLD",
        "decision_id": "decision-1",
        "position_state": "LONG",
        "snapshot_id": "orig-1",
        "original_snapshot_id": "orig-1",
    }])
    monkeypatch.setattr(
        "xiaogu_db.fetch_decision_snapshot",
        lambda _decision_id: validate_and_build_canonical_snapshot({
            "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
            "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        }),
    )
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda _decision_id: {})
    with pytest.raises(RuntimeError, match="TRADING_CALENDAR_UNAVAILABLE"):
        runner.daily_position_review("2026-08-27")


def test_runner_bundle_loader_reads_scanner_canonical_jsonl(tmp_path, monkeypatch):
    import xiaogu_forward_bundle_io as bundles

    scan_dir = tmp_path / "2026-08-26" / "eastmoney_scan"
    scan_dir.mkdir(parents=True)
    canonical_path = scan_dir / "canonical_snapshots.jsonl"
    canonical_path.write_text(
        '{"symbol":"600001","price":10,"trade_date":"2026-08-26","source_time":"2026-08-26T14:50:00+08:00"}\n',
        encoding="utf-8",
    )
    (scan_dir / "xiaogu_scan_summary.json").write_text(
        '{"files":{"canonical_snapshots":"' + str(canonical_path) + '"}}', encoding="utf-8",
    )
    monkeypatch.setattr(bundles, "LIVE_SCAN_ROOT", tmp_path)
    bundle = bundles.load_latest_snapshot_bundle("2026-08-26")
    assert bundle["available"] is True
    assert bundle["canonical_snapshot_path"] == str(canonical_path)
    assert bundle["canonical_snapshots"][0]["symbol"] == "600001"


def test_api_exposes_current_state_and_trade_views(tmp_path, monkeypatch):
    import xiaogu_api

    monkeypatch.setattr("xiaogu_db.fetch_picks", lambda: [{
        "decision_id": "decision-1", "symbol": "600001", "trade_date": "2026-08-26",
        "state": "BUY", "position_state": "LONG", "payload": {
            "decision_id": "decision-1", "decision": "BUY", "features_used": {},
        },
    }])
    monkeypatch.setattr("xiaogu_db.fetch_returns", lambda: [{
        "record_type": "RESULT", "decision_id": "decision-1", "payload": {
            "decision_id": "decision-1", "future_5d_return": 0.1,
        },
    }])
    monkeypatch.setattr("xiaogu_db.fetch_open_positions", lambda: [{
        "position_id": "POS|decision-1",
        "decision_id": "decision-1", "symbol": "600001", "state": "BUY",
        "position_state": "LONG", "original_snapshot_id": "snap-1",
        "payload": {},
    }])
    monkeypatch.setattr("xiaogu_db.fetch_paper_observations", lambda: [{
        "paper_signal_id": "paper-1",
        "decision_id": "decision-1",
        "original_snapshot_id": "snap-1",
        "review_snapshot_id": "snap-2",
        "paper_position_state": "PAPER_LONG",
        "paper_observation_state": "OBSERVED",
        "payload": {
            "paper_signal_id": "paper-1",
            "decision_id": "decision-1",
            "original_snapshot_id": "snap-1",
            "review_snapshot_id": "snap-2",
            "paper_position_state": "PAPER_LONG",
            "paper_observation_state": "OBSERVED",
        },
    }])
    position = xiaogu_api.current_state()["positions"][0]
    assert position["decision"] == "BUY"
    assert position["position_id"] == "POS|decision-1"
    assert position["decision_id"] == "decision-1"
    assert position["original_snapshot_id"] == "snap-1"
    assert position["position_state"] == "LONG"
    listed = xiaogu_api.positions()["positions"][0]
    assert listed["position_id"] == "POS|decision-1"
    assert xiaogu_api.current_decision()["decision"] == "BUY"
    assert xiaogu_api.trades()[0]["new_state"] == "BUY"
    decision_id = xiaogu_api.trades()[0]["decision_id"]
    assert xiaogu_api.trade(decision_id)["decision_id"] == decision_id
    assert xiaogu_api.trade("missing-decision")["found"] is False
    paper = xiaogu_api.paper_signals()["signals"][0]
    assert paper["paper_signal_id"] == "paper-1"
    assert paper["decision_id"] == "decision-1"
    assert paper["original_snapshot_id"] == "snap-1"
    assert paper["review_snapshot_id"] == "snap-2"
    assert paper["paper_position_state"] == "PAPER_LONG"
    assert paper["paper_action"] == "PAPER_HOLD"


def test_api_ignores_unresolved_pick_without_explicit_decision_id(monkeypatch):
    import xiaogu_api

    monkeypatch.setattr("xiaogu_db.fetch_picks", lambda: [{
        "id": 99, "symbol": "600001", "state": "BUY", "payload": {},
    }])
    monkeypatch.setattr("xiaogu_db.fetch_returns", lambda: [])
    assert xiaogu_api.current_decision() == {"found": False}
    assert xiaogu_api.trades() == []


def test_runner_sample_accounting_preserves_each_pipeline_layer(tmp_path, monkeypatch, capsys):
    import xiaogu_forward_runner as runner

    snapshots = [
        {"symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
         "f13": 1, "f1": 2,
         "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                            "L2_CAPITAL_CANDIDATE", "L3_DEEP_CANDIDATE_FETCH"]},
        {"symbol": "600002", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
         "f13": 1, "f1": 2,
         "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                            "L2_CAPITAL_CANDIDATE"]},
        {"symbol": "600003", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
         "f13": 1, "f1": 2,
         "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY"]},
        {"symbol": "600004", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "future_5d_return": 0.1},
    ]
    snapshot_path = tmp_path / "snapshots.json"
    snapshot_path.write_text(json.dumps({"canonical_snapshots": snapshots}), encoding="utf-8")
    monkeypatch.setattr(runner, "run_production_decision", lambda *args, **kwargs: {
        "state": "WATCH", "action": "WATCH", "symbol": args[0]["symbol"],
    })
    monkeypatch.setattr(sys, "argv", [
        "xiaogu_forward_runner.py", "--date", "2026-08-26", "--mode", "REPLAY",
        "--snapshot-json", str(snapshot_path),
    ])

    runner.main()
    output = json.loads(capsys.readouterr().out)
    assert output["sample_accounting"] == {
        "full_universe_count": 4, "l1_count": 3, "l2_count": 2, "l3_count": 1,
        "alpha_count": 1, "decision_count": 1, "canonical_count": 3,
        "partial_count": 0, "conflict_count": 0, "invalid_count": 1,
        "unresolved_count": 0,
    }


def test_fake_canonical_snapshot_is_blocked_from_feature_engine():
    from xiaogu_forward_features import build_feature_vector

    forged = {
        "symbol": "600001",
        "trusted_snapshot": True,
        "lineage_id": "forged",
        "raw": {"symbol": "600001", "price": 10},
    }
    with pytest.raises(TypeError, match="FEATURE_ENGINE_REQUIRES_CANONICAL_SNAPSHOT"):
        build_feature_vector(forged)


def test_production_mode_rejects_unpersisted_snapshot_json():
    from xiaogu_forward_runner import run_production_decision

    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
    })
    with pytest.raises(RuntimeError, match="SNAPSHOT_PERSISTENCE_FAILED"):
        run_production_decision(snapshot, mode="PRODUCTION", persisted=False, trade_date="2026-08-26")


def test_position_review_reads_postgres_not_jsonl(monkeypatch):
    import xiaogu_forward_runner as runner

    calls = {"jsonl": 0}

    def fake_positions():
        return [{
            "position_id": "POS|d1",
            "symbol": "600001",
            "trade_date": "2026-08-25",
            "state": "HOLD",
            "decision": "HOLD",
            "decision_id": "d1",
            "position_state": "LONG",
            "snapshot_id": "orig-1",
            "original_snapshot_id": "orig-1",
        }]

    monkeypatch.setattr("xiaogu_db.fetch_open_positions", fake_positions)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {"status": "OUTCOME_NOT_BOUND"})
    monkeypatch.setattr(
        "xiaogu_db.fetch_decision_snapshot",
        lambda *_args, **_kwargs: validate_and_build_canonical_snapshot({
            "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
            "source_time": "2026-08-25T14:50:00+08:00", "trade_date": "2026-08-25",
        }),
    )
    monkeypatch.setattr(
        "xiaogu_db.get_current_position_review_snapshot",
        lambda **_kwargs: validate_and_build_canonical_snapshot({
            "symbol": "600001", "price": 10.4, "volume": 120, "amount": 1200,
            "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        }),
    )
    monkeypatch.setattr(
        runner,
        "load_latest_snapshot_bundle",
        lambda _date: (_ for _ in ()).throw(AssertionError("LOCAL_BUNDLE_IS_NOT_POSITION_STATE")),
    )
    monkeypatch.setattr(runner, "run_production_decision", lambda *args, **kwargs: {
        "state": "SELL", "action": "SELL", "position_state": "FLAT",
        "reason": "MAX_HOLDING_BOUNDARY_CLOSED", "trade_status": "CLOSED",
        "canonical_snapshot": {"trade_date": "2026-08-26", "symbol": "600001", "source_time": "2026-08-26T14:50:00+08:00"},
    })
    monkeypatch.setattr("xiaogu_db.trading_days_between", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(runner, "_write_ledger_record", lambda decision: Path("/tmp/unused"))
    monkeypatch.setattr("xiaogu_utils.load_jsonl", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("JSONL_IS_NOT_POSITION_STATE")))
    reviewed = runner.daily_position_review("2026-08-26")
    assert reviewed[0]["state"] == "SELL"




def test_production_clock_rejects_stale_snapshot(monkeypatch):
    from xiaogu_forward_runner import run_production_decision

    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
    })
    with pytest.raises(ValueError, match="STALE_DATA"):
        run_production_decision(snapshot, mode="PRODUCTION", trade_date="2026-08-26")


def test_persisted_flag_is_not_enough_without_db_row():
    from xiaogu_forward_runner import run_production_decision

    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
    })
    with pytest.raises(RuntimeError, match="SNAPSHOT_PERSISTENCE_FAILED"):
        run_production_decision(snapshot, mode="PRODUCTION", persisted=True, trade_date="2026-08-26")


def test_recorder_db_failure_does_not_write_jsonl(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
    monkeypatch.setattr(xiaogu_db, "record_snapshot", lambda _snapshot: (_ for _ in ()).throw(RuntimeError("db down")))
    decision = {
        "state": "BUY", "reason": "REPRICING_READY",
        "canonical_snapshot": {
            "lineage_id": "lineage", "trade_date": "2026-08-26",
            "source_time": "2026-08-26T14:50:00+08:00", "symbol": "600001", "price": 10,
        },
    }
    with pytest.raises(RuntimeError, match="db down"):
        recorder.append_production_decision(decision)
    assert not (tmp_path / "ledger.jsonl").exists()


def test_same_symbol_outcomes_are_bound_to_decision_id():
    import json
    from sqlalchemy import text
    from xiaogu_db import engine, ensure_production_schema, fetch_position_outcome

    ensure_production_schema()
    with engine.begin() as db:
        db.execute(text("DELETE FROM returns WHERE decision_id IN ('test-trade-a', 'test-trade-b')"))
        db.execute(text("DELETE FROM picks WHERE decision_id IN ('test-trade-a', 'test-trade-b')"))
        from xiaogu_db import _table_columns
        pick_fields = ["trade_date", "symbol", "state", "decision_id", "payload"]
        if "decision" in _table_columns("picks"):
            pick_fields = ["trade_date", "symbol", "decision", "state", "decision_id", "payload"]
            values = (
                "('2026-08-01', '600001', 'BUY', 'BUY', 'test-trade-a', CAST(:payload AS jsonb)), "
                "('2026-08-08', '600001', 'BUY', 'BUY', 'test-trade-b', CAST(:payload AS jsonb))"
            )
        else:
            values = (
                "('2026-08-01', '600001', 'BUY', 'test-trade-a', CAST(:payload AS jsonb)), "
                "('2026-08-08', '600001', 'BUY', 'test-trade-b', CAST(:payload AS jsonb))"
            )
        db.execute(
            text(f"INSERT INTO picks ({', '.join(pick_fields)}) VALUES {values}"),
            {"payload": json.dumps({"decision_id": "seed"})},
        )
        db.execute(
            text("INSERT INTO returns (trade_date, symbol, decision_id, payload) VALUES ('2026-08-01', '600001', 'test-trade-a', CAST(:payload AS jsonb))"),
            {"payload": json.dumps({"profit_window": True, "decision_id": "test-trade-a", "max_profit": 0.08})},
        )
        db.execute(
            text("INSERT INTO returns (trade_date, symbol, decision_id, payload) VALUES ('2026-08-08', '600001', 'test-trade-b', CAST(:payload AS jsonb))"),
            {"payload": json.dumps({"profit_window": False, "decision_id": "test-trade-b", "max_profit": -0.04})},
        )
    try:
        trade_a = fetch_position_outcome("test-trade-a", symbol="600001")
        trade_b = fetch_position_outcome("test-trade-b", symbol="600001")
        missing = fetch_position_outcome("", symbol="600001")
        unknown = fetch_position_outcome("test-trade-missing", symbol="600001")
        assert trade_a["status"] == "BOUND"
        assert trade_a["decision_id"] == "test-trade-a"
        assert trade_a.get("profit_window") is True
        assert trade_b["decision_id"] == "test-trade-b"
        assert trade_b.get("profit_window") is False
        assert missing["status"] == "OUTCOME_NOT_BOUND"
        assert unknown["status"] == "OUTCOME_NOT_BOUND"
    finally:
        with engine.begin() as db:
            db.execute(text("DELETE FROM returns WHERE decision_id IN ('test-trade-a', 'test-trade-b')"))
            db.execute(text("DELETE FROM picks WHERE decision_id IN ('test-trade-a', 'test-trade-b')"))


def test_schema_migration_failure_is_fail_closed(monkeypatch):
    import xiaogu_db as db

    class Boom:
        def __enter__(self):
            raise RuntimeError("ALTER TABLE failed")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(db.engine, "begin", lambda: Boom())
    with pytest.raises(RuntimeError, match="ALTER TABLE failed"):
        db.ensure_production_schema()


def test_returns_fk_conflict_fails_schema(monkeypatch):
    import xiaogu_db as db

    monkeypatch.setattr(db, "_count_returns_decision_fk_conflicts", lambda: 1)
    audit = db.audit_production_schema()
    assert audit["ok"] is False
    assert audit["tables"]["returns"]["foreign_keys"]["decision_id->picks.decision_id"] == "CONFLICT"


def test_schema_migration_final_contract():
    import xiaogu_db as db

    db.ensure_production_schema()
    assert db.audit_production_schema()["ok"] is True


def test_missing_authoritative_calendar_fails_closed(monkeypatch):
    import xiaogu_db as db

    recorded = []
    monkeypatch.setattr(db, "record_trading_calendar", lambda rows: recorded.extend(rows))
    with pytest.raises(RuntimeError, match="CALENDAR_DATA_UNAVAILABLE"):
        db.refresh_a_share_trading_calendar("2024-10-01", "2024-10-12")
    assert recorded == []


def test_2026_authoritative_calendar_regressions():
    from xiaogu_db import authoritative_calendar_records

    rows = {
        row["trade_date"]: row["is_trading_day"]
        for row in authoritative_calendar_records("2026-08-28", "2026-09-28")
    }
    assert rows["2026-08-28"] is True
    assert rows["2026-08-31"] is True
    assert rows["2026-09-01"] is True
    assert rows["2026-09-25"] is False
    assert rows["2026-09-26"] is False
    assert rows["2026-09-27"] is False
    assert rows["2026-09-28"] is True


def test_calendar_unknown_fails_closed(monkeypatch):
    import xiaogu_db as db

    class BrokenEngine:
        def connect(self):
            raise RuntimeError("calendar database unavailable")

    monkeypatch.setattr(db, "engine", BrokenEngine())
    assert db.is_trading_date("2026-08-31") == db.CALENDAR_UNKNOWN


def test_authoritative_calendar_records_include_provenance():
    from xiaogu_db import authoritative_calendar_records

    row = authoritative_calendar_records("2026-08-31", "2026-08-31")[0]
    assert row["market"] == "ASHARE"
    assert row["source"] == "sse_official_2026_trading_calendar"
    assert row["calendar_version"] == "CN_A_SHARE_2026_V1"
    assert row["source_timestamp"] == "2025-12-22T00:00:00+08:00"


def _annual_calendar_fixture(year: int) -> dict:
    """TEST_FIXTURE_ONLY synthetic calendar. Not the official ASHARE dataset."""
    return {
        "source": f"official_{year}",
        "source_timestamp": f"{year - 1}-12-22T00:00:00+08:00",
        "calendar_version": f"CN_A_SHARE_{year}_V1",
        "rows": [
            {
                "trade_date": (date(year, 1, 1) + timedelta(days=offset)).isoformat(),
                "market": "ASHARE",
                "is_trading_day": True,
            }
            for offset in range(366 if calendar_module.isleap(year) else 365)
        ],
    }


def test_calendar_loader_is_year_bound_and_supports_leap_year(tmp_path, monkeypatch):
    import xiaogu_db as db

    fixture = _annual_calendar_fixture(2028)
    (tmp_path / "ashare_2028.json").write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(db, "CALENDAR_DATASET_DIR", tmp_path)
    loaded = db.load_trading_calendar(2028)
    assert len(loaded["rows"]) == 366
    assert db.get_calendar_version(2028) == "CN_A_SHARE_2028_V1"
    assert db.calendar_content_hash(loaded["rows"]) == loaded["content_hash"]


def test_calendar_hash_changes_when_one_fact_changes():
    import xiaogu_db as db

    rows = [
        {
            "trade_date": "2026-01-01",
            "market": "ASHARE",
            "is_trading_day": False,
            "source": "official",
            "calendar_version": "CN_A_SHARE_2026_V1",
        }
    ]
    changed = [dict(rows[0], is_trading_day=True)]
    assert db.calendar_content_hash(rows) == db.calendar_content_hash(list(reversed(rows)))
    assert db.calendar_content_hash(rows) != db.calendar_content_hash(changed)


def test_calendar_loader_rejects_wrong_year_and_missing_date(tmp_path, monkeypatch):
    import xiaogu_db as db

    fixture = _annual_calendar_fixture(2026)
    fixture["rows"][0]["trade_date"] = "2027-01-01"
    (tmp_path / "ashare_2026.json").write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(db, "CALENDAR_DATASET_PATH", tmp_path / "ashare_2026.json")
    with pytest.raises(RuntimeError, match="INVALID_CALENDAR_YEAR"):
        db.load_trading_calendar(2026)

    fixture = _annual_calendar_fixture(2026)
    fixture["rows"].pop()
    (tmp_path / "ashare_2026.json").write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(RuntimeError, match="CALENDAR_INCOMPLETE"):
        db.load_trading_calendar(2026)


def test_calendar_audit_uses_requested_current_date():
    from xiaogu_db import audit_trading_calendar

    report = audit_trading_calendar(today="2026-08-31")
    assert report["today"] == "2026-08-31"
    assert report["today_status"] == "TRUE"
    assert report["today_available"] is True


def test_calendar_missing_row_is_unknown_not_non_trading(monkeypatch):
    import xiaogu_db as db

    class Result:
        def first(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return Result()

    class MissingEngine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(db, "engine", MissingEngine())
    assert db.is_trading_date("2026-08-31") == db.CALENDAR_UNKNOWN


def test_t5_resolver_skips_mid_autumn_closure():
    from xiaogu_db import resolve_t_plus_n

    assert [resolve_t_plus_n("2026-09-21", offset).isoformat() for offset in range(1, 6)] == [
        "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28", "2026-09-29",
    ]


def test_calendar_dataset_rejects_duplicate_or_unidentified_rows(tmp_path, monkeypatch):
    import json
    import xiaogu_db as db

    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(json.dumps({
        "source": "official",
        "source_timestamp": "2025-12-22T00:00:00+08:00",
        "calendar_version": "test-v1",
        "rows": [
            {"trade_date": "2026-08-31", "market": "ASHARE", "is_trading_day": True},
            {"trade_date": "2026-08-31", "market": "ASHARE", "is_trading_day": True},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(db, "CALENDAR_DATASET_PATH", calendar_path)
    with pytest.raises(RuntimeError, match="CALENDAR_DATA_UNAVAILABLE"):
        db.authoritative_calendar_records("2026-08-31", "2026-08-31")


def test_scheduler_blocks_unknown_calendar_and_skips_closure(monkeypatch):
    import xiaogu_scheduler as scheduler

    monkeypatch.setattr("xiaogu_db.is_trading_date", lambda _value: "FALSE")
    assert scheduler.is_trading_day("2026-09-25") is False
    monkeypatch.setattr("xiaogu_db.is_trading_date", lambda _value: "UNKNOWN")
    with pytest.raises(RuntimeError, match="CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE"):
        scheduler.is_trading_day("2026-09-01")


def test_production_calendar_consumers_have_no_weekday_or_price_calendar_logic():
    from pathlib import Path

    root = Path(__file__).parents[1]
    for relative in (
        "xiaogu_scheduler.py",
        "xiaogu_forward_runner.py",
        "xiaogu_forward_result_filler_v0_1.py",
        "xiaogu_horizon_evaluation.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "week" + "day()" not in source
        assert "canonical_" + "future_prices" + ".*" + "trading" not in source
        assert "trading" + ".*" + "canonical_" + "future_prices" not in source


def test_scheduler_uses_single_calendar_owner(monkeypatch):
    import xiaogu_scheduler as scheduler

    calls = []
    monkeypatch.setattr("xiaogu_db.is_trading_date", lambda value: calls.append(value) or True)
    assert scheduler.is_trading_day(__import__("datetime").date(2024, 10, 8)) is True
    assert calls == [__import__("datetime").date(2024, 10, 8)]


def test_daily_pipeline_persists_canonical_snapshots_before_production_runner():
    pipeline = (__import__("pathlib").Path(__file__).parents[1] / "daily_pipeline.sh").read_text(
        encoding="utf-8"
    )
    assert "export XIAOGU_PERSIST_DB=1" in pipeline
    assert pipeline.index("scrapy_scanner/runner_v2.py") < pipeline.index(
        "xiaogu_forward_runner.py"
    )
    assert "--scan-dir" in pipeline
    assert pipeline.index("scrapy_scanner/runner_v2.py") < pipeline.index("--scan-dir")
    assert "--due" in pipeline
    assert "fill_pending" not in pipeline


def test_canonical_future_prices_are_immutable_facts():
    from sqlalchemy import text
    from xiaogu_db import canonical_future_price_fact, engine, ensure_production_schema, record_canonical_future_prices

    ensure_production_schema()
    fact = {
        "symbol": "699991", "date": "2026-08-26", "open": 10.0, "high": 10.5,
        "low": 9.8, "close": 10.2, "volume": 100.0, "amount": 1000.0,
        "source": "eastmoney_daily_kline", "price_basis": "UNADJUSTED",
    }
    with engine.begin() as db:
        db.execute(text("DELETE FROM canonical_future_prices WHERE symbol = '699991' AND date = '2026-08-26'"))
    try:
        record_canonical_future_prices([fact])
        record_canonical_future_prices([dict(fact)])
        with engine.connect() as db:
            stored = dict(db.execute(text("SELECT * FROM canonical_future_prices WHERE symbol = '699991' AND date = '2026-08-26'")).mappings().one())
        assert stored["close"] == pytest.approx(10.2)
        assert stored["price_fact_hash"] == canonical_future_price_fact(fact)["price_fact_hash"]
        with pytest.raises(ValueError, match="PRICE_FACT_CONFLICT"):
            record_canonical_future_prices([{**fact, "close": 10.3}])
        with pytest.raises(ValueError, match="PRICE_FACT_CONFLICT"):
            record_canonical_future_prices([{**fact, "source": "baostock_daily_kline"}])
        with engine.connect() as db:
            unchanged = dict(db.execute(text("SELECT close, source FROM canonical_future_prices WHERE symbol = '699991' AND date = '2026-08-26'")).mappings().one())
        assert unchanged == {"close": 10.2, "source": "eastmoney_daily_kline"}
    finally:
        with engine.begin() as db:
            db.execute(text("DELETE FROM canonical_future_prices WHERE symbol = '699991' AND date = '2026-08-26'"))


def test_paper_observation_db_checks_enforce_paper_only_and_no_live_order():
    from xiaogu_db import audit_production_schema, ensure_production_schema

    ensure_production_schema()
    checks = audit_production_schema()["tables"]["paper_observations"]["checks"]
    assert checks["paper_observations_paper_only_check"] == "CHECK (paper_only)"
    assert "NOT live_order" in checks["paper_observations_live_order_check"]


def test_rule_freeze_hard_gates_match_decision_owner():
    from xiaogu_portfolio_decision import DECISION_HARD_GATES

    rule = json.loads(open("rule_freeze_v0_1.json", encoding="utf-8").read())
    assert tuple(rule["alpha_contract"]["required_hard_gates"]) == DECISION_HARD_GATES
    assert "required_buy_evidence" not in rule["alpha_contract"]


def test_scan_lineage_is_shared_and_snapshot_id_is_unique():
    from xiaogu_forward_snapshot import select_unique_canonical_snapshots

    snapshots = build_canonical_snapshots({
        "stock_all_a": [
            {"f12": "600001", "f14": "A", "f2": 10, "f3": 1, "f6": 1_000, "f5": 100, "f100": "示例行业"},
            {"f12": "600002", "f14": "B", "f2": 11, "f3": 1, "f6": 1_000, "f5": 100, "f100": "示例行业"},
        ],
        "stock_capital_flow": [], "earnings_preview": [], "stock_reports": [],
        "lhb": [], "announcements": [], "flow_industry": [], "industry_reports": [],
    }, "2026-08-26 14:50:00")
    assert len(snapshots) == 2
    assert snapshots[0]["lineage_id"] == snapshots[1]["lineage_id"]
    assert snapshots[0]["snapshot_id"] != snapshots[1]["snapshot_id"]
    selected = select_unique_canonical_snapshots(snapshots + snapshots, trade_date="2026-08-26")
    assert {row["symbol"] for row in selected} == {"600001", "600002"}
    assert len(selected) == 2


def test_record_snapshot_conflicts_on_snapshot_id_not_lineage():
    from xiaogu_db import engine, ensure_production_schema, record_snapshot
    from sqlalchemy import text

    first, second = build_canonical_snapshots({
        "stock_all_a": [
            {"f12": "600001", "f14": "A", "f2": 10, "f3": 1, "f6": 1_000, "f5": 100},
            {"f12": "600002", "f14": "B", "f2": 11, "f3": 1, "f6": 1_000, "f5": 100},
        ],
        "stock_capital_flow": [], "earnings_preview": [], "stock_reports": [],
        "lhb": [], "announcements": [], "flow_industry": [], "industry_reports": [],
    }, "2026-08-26 14:50:00")
    ensure_production_schema()
    ids = (first["snapshot_id"], second["snapshot_id"])
    with engine.begin() as db:
        db.execute(
            text("DELETE FROM snapshots WHERE snapshot_id = :left OR snapshot_id = :right"),
            {"left": ids[0], "right": ids[1]},
        )
        db.execute(
            text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :left OR snapshot_id = :right"),
            {"left": ids[0], "right": ids[1]},
        )
    try:
        record_snapshot(first)
        record_snapshot(second)
        record_snapshot(first)
        with engine.connect() as db:
            rows = list(db.execute(
                text("SELECT snapshot_id, lineage_id, symbol FROM snapshots WHERE snapshot_id = :left OR snapshot_id = :right"),
                {"left": ids[0], "right": ids[1]},
            ).mappings())
        assert len(rows) == 2
        assert {row["lineage_id"] for row in rows} == {first["lineage_id"]}
        assert {row["symbol"] for row in rows} == {"600001", "600002"}
    finally:
        with engine.begin() as db:
            db.execute(
                text("DELETE FROM snapshots WHERE snapshot_id = :left OR snapshot_id = :right"),
                {"left": ids[0], "right": ids[1]},
            )
            db.execute(
                text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :left OR snapshot_id = :right"),
                {"left": ids[0], "right": ids[1]},
            )


def test_record_snapshot_rejects_payload_identity_conflict():
    from xiaogu_db import engine, ensure_production_schema, record_snapshot
    from sqlalchemy import text

    first, = build_canonical_snapshots({
        "stock_all_a": [
            {"f12": "600009", "f14": "Z", "f2": 10, "f3": 1, "f6": 1_000, "f5": 100},
        ],
        "stock_capital_flow": [], "earnings_preview": [], "stock_reports": [],
        "lhb": [], "announcements": [], "flow_industry": [], "industry_reports": [],
    }, "2026-08-26 14:50:00")
    ensure_production_schema()
    with engine.begin() as db:
        db.execute(text("DELETE FROM snapshots WHERE snapshot_id = :sid"), {"sid": first["snapshot_id"]})
        db.execute(
            text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :sid"),
            {"sid": first["snapshot_id"]},
        )
    try:
        record_snapshot(first)
        conflicted = dict(first)
        conflicted["price"] = 99
        import pytest
        with pytest.raises(ValueError, match="SNAPSHOT_IDENTITY_CONFLICT"):
            record_snapshot(conflicted)
    finally:
        with engine.begin() as db:
            db.execute(text("DELETE FROM snapshots WHERE snapshot_id = :sid"), {"sid": first["snapshot_id"]})
            db.execute(
                text("DELETE FROM snapshot_identity_conflicts WHERE snapshot_id = :sid"),
                {"sid": first["snapshot_id"]},
            )


def test_supply_state_exposes_evidence_and_confidence():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "amount": 1_000, "volume": 100, "turnover": 5, "f62": 400, "f3": 1,
        "overhead_supply": 0.2,
    }))
    supply = vector["SUPPLY"]
    assert supply["supply_absorption_state"] in {"UNKNOWN", "PARTIAL", "RELEASING", "BALANCED", "ABSORPTION"}
    assert "evidence" in supply
    assert "evidence_count" in supply
    assert "confidence" in supply

def test_unknown_capital_roles_are_not_hard_buy_blockers():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    })
    blockers = decision["repricing_risk"]["blockers"]
    assert "CAPITAL_CONVERGENCE_INCOMPLETE" not in blockers
    assert "SUPPLY_ABSORPTION_UNCONFIRMED" not in blockers
    assert "FUTURE_BUYER_EVIDENCE_MISSING" not in blockers
    assert "PRICING_GAP_TOO_SMALL" not in blockers
    assert decision["core_alpha"]["capital_convergence"]["status"] == "UNKNOWN"
    assert decision["feature_vector"]["SUPPLY"]["supply_absorption_state"] == "UNKNOWN"
    assert decision["future_buyer_map"]["future_buyer_capacity"] is None
    assert decision["state"] != "BUY"
    assert decision["buy_status"] == "BUY_BLOCKED"


def test_level_1_blocks_severe_liquidity_without_scoring():
    eligible, audit = candidate_universe([
        {"symbol": "600001", "price": 10, "volume": 100, "amount": 1000, "liquidity_score": 0},
        {"symbol": "600002", "price": 10, "volume": 100, "amount": 1000},
    ])
    assert [row["symbol"] for row in eligible] == ["600002"]
    assert audit["rejected"][0]["blockers"] == ["SEVERE_LIQUIDITY_ISSUE"]
    assert audit["selection"] is False


def test_scanner_attaches_level0_market_inputs_without_deep_research():
    snapshots = build_canonical_snapshots({
        "stock_all_a": [{"f12": "600001", "f14": "示例公司", "f2": 10, "f3": 1, "f5": 100, "f6": 1_000, "f13": 1, "f100": "示例行业"}],
        "stock_capital_flow": [], "earnings_preview": [], "stock_reports": [],
        "lhb": [], "announcements": [], "flow_industry": [], "industry_reports": [],
    }, "2026-08-26 14:50:00", symbols=[], market={"breadth_up_pct": 61.2, "up_count": 3, "down_count": 1, "flat_count": 0})
    raw = snapshots[0]["raw"]
    assert raw["trade_status"] == "TRADING"
    assert raw["market"] == "SH"
    assert raw["industry"] == "示例行业"
    assert raw["market_breadth"] == 61.2
    assert "up_count" in raw["market_regime_inputs"]
    assert "L3_DEEP_CANDIDATE_FETCH" not in snapshots[0]["source_layers"]


def test_scanner_daily_task_is_idempotent(tmp_path, monkeypatch):
    summary = {
        "production_scan": "PASS",
        "lineage": {"lineage_id": "lin-daily-task"},
        "database_persistence": {"status": "PASS", "run_id": "run-daily-task"},
        "scan_reason": "SCANNER_SUCCESS_AWAITING_DECISION",
    }
    (tmp_path / "scan_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runner_v2.py", "--output-dir", str(tmp_path)])
    from scrapy_scanner.runner_v2 import main as scanner_main
    result = scanner_main()
    assert result["daily_task_status"] == "ALREADY_CAPTURED"
    assert result["scan_reason"] == "DAILY_TASK_IDEMPOTENT"
    assert result["lineage"]["lineage_id"] == "lin-daily-task"
    assert result["database_persistence"]["run_id"] == "run-daily-task"
    assert "stock_all_a" not in result


def test_historical_research_cases_exclude_future_evidence(monkeypatch):
    from xiaogu_research_context import fetch_historical_research_cases

    class _Rows:
        def mappings(self):
            return [
                {
                    "paper_signal_id": "past",
                    "decision_id": "d-past",
                    "symbol": "600001",
                    "signal_time": "2026-08-25T14:50:00+08:00",
                    "payload": {"signal_reason": "FORMAL_5D_PROFIT_WINDOW_SIGNAL", "rank": 1},
                },
                {
                    "paper_signal_id": "future",
                    "decision_id": "d-future",
                    "symbol": "600001",
                    "signal_time": "2026-08-27T14:50:00+08:00",
                    "payload": {"signal_reason": "FORMAL_5D_PROFIT_WINDOW_SIGNAL", "rank": 1},
                },
            ]

    class _Connection:
        def execute(self, _sql, _params):
            return _Rows()
        def __enter__(self):
            return self
        def __exit__(self, *_exc):
            return False

    class _Engine:
        def connect(self):
            return _Connection()

    monkeypatch.setattr("xiaogu_db.engine", _Engine(), raising=False)
    import xiaogu_db
    monkeypatch.setattr(xiaogu_db, "engine", _Engine())
    payload = fetch_historical_research_cases("600001", "2026-08-26T15:00:00+08:00")
    ids = [item["paper_signal_id"] for item in payload["historical_cases"]]
    assert "past" in ids
    assert "future" not in ids
    assert payload["status"] == "RESEARCH_ONLY"
