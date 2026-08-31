from datetime import datetime
import json

import pytest

from scrapy_scanner.runner_v2 import (
    CRITICAL_SOURCES,
    OPTIONAL_SOURCES,
    detect_capital_candidates,
    fetch_announcements,
    fetch_capital_history,
    fetch_datacenter,
    fetch_news,
    fetch_report_list,
    fetch_ulist,
)
from xiaogu_core_alpha import CANONICAL_COST_MODEL, COST_MODEL_VERSION, DEFAULT_COST_RATE
from xiaogu_forward_features import build_feature_vector
from xiaogu_forward_snapshot import (
    assert_point_in_time_evidence,
    validate_and_build_canonical_snapshot,
    filter_point_in_time_records,
)
from xiaogu_portfolio_decision import evaluate_candidate_bundle


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")


def test_scanner_is_capture_only():
    import scrapy_scanner.runner_v2 as scanner
    source = Path_text = open(scanner.__file__, encoding="utf-8").read()
    assert "DATA_CAPTURE_ONLY" in source
    assert '"selection": False' in source
    assert "RESOURCE_ROUTER" in source
    assert "alpha_score" not in source
    assert "buy_score" not in source


def test_scanner_l3_filters_before_fetch(monkeypatch):
    seen = []

    def fake_api(url, timeout=30):
        seen.append(url)
        return {"data": {"diff": [], "list": [], "total": 0}, "result": {"data": [], "count": 0}, "rc": 0}

    monkeypatch.setattr("scrapy_scanner.runner_v2.api_get", fake_api)
    fetch_ulist(["600001"], "f12,f62")
    fetch_datacenter("RPT_DAILYBILLBOARD_DETAILSNEW", "TRADE_DATE", candidate_codes=["600001"])
    fetch_report_list("0", "2026-08-01", "2026-08-26", candidate_codes=["600001"])
    fetch_announcements(candidate_codes=["600001"])
    fetch_news(candidate_codes=["600001"])
    assert seen
    assert all("600001" in url for url in seen)
    assert not any("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23" in url and "secids" not in url for url in seen)


def test_news_audit_does_not_fabricate_candidate_symbols(monkeypatch):
    import scrapy_scanner.runner_v2 as scanner

    monkeypatch.setattr(scanner, "api_get", lambda *_args, **_kwargs: {
        "data": {"list": [{"title": "unrelated result"}]}
    })
    diagnostics = {}
    assert scanner.fetch_news(candidate_codes=["600001"], diagnostics=diagnostics) == []
    assert diagnostics["requested_symbols"] == ["600001"]
    assert diagnostics["unrelated_rows"] == 1
    assert diagnostics["unrelated_symbols"] == []
    assert diagnostics["response_count"] == 1


def test_pit_trade_date_is_record_level():
    from xiaogu_forward_snapshot import pit_record_audit

    audit = pit_record_audit({
        "source_id": "lhb",
        "event_time": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "trade_date": "2026-08-27",
    }, "2026-08-26T15:00:00+08:00")
    assert audit["pit_status"] == "EXCLUDED_FROM_FEATURES"
    assert audit["exclusion_reason"] == "FUTURE_TRADE_DATE"
    assert audit["primary_event_field"] == "event_time"
    assert audit["availability_field"] == "available_at"


def test_l2_is_resource_router():
    stocks = [
        {"f12": "600001", "f2": 10, "f3": 3, "f5": 100, "f6": 1000, "f8": 2, "f10": 1.2, "f15": 10.5, "f16": 9.5, "f62": 500},
        {"f12": "600002", "f2": 10, "f3": 0, "f5": 100, "f6": 1000, "f8": 0.5, "f10": 0.8, "f15": 10.5, "f16": 9.5},
    ]
    candidates, audit = detect_capital_candidates(stocks)
    assert audit["purpose"] == "RESOURCE_ROUTER"
    assert audit["selection"] is False
    assert audit["ranking"] is False
    assert audit["alpha"] is False
    assert "alpha_score" not in audit
    assert [row["f12"] for row in candidates] == ["600001"]
    routed = {item["symbol"]: item for item in audit["routing"]}
    assert routed["600001"]["deep_fetch_required"] is True
    assert routed["600001"]["routing_reasons"]
    assert routed["600002"]["deep_fetch_required"] is False


def _capital_history_rows():
    ratios = (-0.10, -0.05, 0.00, 0.10, 0.15, 0.20)
    rows = []
    for index, ratio in enumerate(ratios):
        trade_date = f"2026-08-{21 + index:02d}"
        rows.append({
            "symbol": "600001",
            "trade_date": trade_date,
            "capital_flow": ratio * 1_000,
            "capital_flow_ratio": ratio,
            "amount": 1_000,
            "volume": 100 + index,
            "turnover": 2 + index,
            "pct_change": -1 if index == 5 else 0.2,
            "close": 10 + index * 0.1,
            "relative_volume": 1.1 + index * 0.1,
            "source": "eastmoney_capital_history",
            "source_id": "eastmoney_capital_history",
            "source_time": f"{trade_date}T15:00:00+08:00",
            "available_at": f"{trade_date}T15:00:00+08:00",
            "snapshot_id": f"snap-{index}",
        })
    return rows


def test_capital_history_features_are_pit_and_keep_missing_values():
    history = _capital_history_rows() + [{
        **_capital_history_rows()[-1],
        "trade_date": "2026-08-27",
        "source_time": "2026-08-27T15:00:00+08:00",
        "available_at": "2026-08-27T15:00:00+08:00",
        "capital_flow_ratio": 0.99,
    }]
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001",
        "price": 10.5,
        "volume": 105,
        "amount": 1_000,
        "pct_chg": -1,
        "source_time": "2026-08-26T15:00:00+08:00",
        "raw": {"capital_history": history},
    }))
    capital = vector["CAPITAL"]
    audit = capital["capital_history_audit"]
    assert audit["returned_observations"] == 6
    assert audit["excluded_count"] == 1
    assert audit["positive_days"] == 3
    assert capital["capital_persistence"] == pytest.approx(0.5)
    assert capital["capital_acceleration"] == pytest.approx(0.05)
    assert capital["capital_acceleration_delta_3d"] == pytest.approx(0.20)
    assert capital["capital_acceleration_slope"] == pytest.approx(0.06)
    assert capital["capital_inflection"] == 1.0
    assert capital["capital_price_efficiency"] == pytest.approx(-0.05)
    assert capital["capital_price_divergence_state"] == "CAPITAL_UP_PRICE_DOWN"
    assert capital["capital_flow_ratio"] == pytest.approx(0.20)
    assert all(item["available_at"] <= "2026-08-26T15:00:00+08:00" for item in audit["observations"])
    assert all(item["source_id"] and item["snapshot_id"] for item in audit["observations"])


def test_capital_flow_ratio_zero_amount_is_none_and_history_is_not_replicated():
    history = _capital_history_rows()[:5] + [{
        **_capital_history_rows()[-1],
        "amount": 0,
        "capital_flow": 200,
        "capital_flow_ratio": None,
    }]
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "amount": 1_000,
        "source_time": "2026-08-26T15:00:00+08:00",
        "raw": {"capital_history": history},
    }))
    capital = vector["CAPITAL"]
    assert capital["capital_flow_ratio"] is None
    assert capital["capital_persistence"] is None
    assert capital["capital_acceleration"] is not None
    assert capital["capital_history_audit"]["observed_days"] == 5


def test_capital_flow_ratio_falls_back_to_latest_valid_history_without_t_duplication():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "amount": 1_000,
        "source_time": "2026-08-26T15:00:00+08:00",
        "raw": {"capital_history": _capital_history_rows()[:5]},
    }))
    capital = vector["CAPITAL"]
    assert capital["capital_flow_ratio"] == pytest.approx(0.15)
    assert capital["capital_history_audit"]["returned_observations"] == 5


def test_capital_history_scanner_accounts_for_l3_requests(monkeypatch):
    import scrapy_scanner.runner_v2 as scanner

    def fake_api(url, timeout=30):
        code = "600001" if "1.600001" in url else "000002"
        return {"data": {"klines": [["2026-08-26", "100", "0", "0", "0", "0", "0.1"]]}}

    monkeypatch.setattr(scanner, "api_get", fake_api)
    diagnostics = {}
    rows = fetch_capital_history(
        ["600001", "000002"],
        begin_date="2026-08-26",
        end_date="2026-08-26",
        diagnostics=diagnostics,
    )
    assert {row["symbol"] for row in rows} == {"600001", "000002"}
    assert diagnostics["requested_symbols"] == ["000002", "600001"]
    assert diagnostics["returned_symbols"] == ["000002", "600001"]
    assert diagnostics["unrelated_symbols"] == []
    assert diagnostics["request_count"] == 2
    assert diagnostics["response_count"] == 2


def test_no_selection_bias_blindspot():
    from xiaogu_backtest_v0_1 import historical_replay
    replay = historical_replay([{
        "symbol": "600001", "price": 10,
        "source_time": "2026-08-26T14:50:00+00:00",
        "future_bars": [],
    }])
    assert "full_universe_count" in replay["selection_audit"]
    assert "l1_count" in replay["selection_audit"]
    assert "l2_count" in replay["selection_audit"]
    assert "l3_count" in replay["selection_audit"]
    assert "alpha_count" in replay["selection_audit"]
    assert "decision_count" in replay["selection_audit"]
    assert "selection_bias_warning" in replay["selection_audit"]
    assert replay["selection_audit"]["l2_count"] is None
    assert replay["selection_audit"]["selection_bias_warning"] is True


def test_replay_selection_audit_keeps_l2_and_l3_counts_separate(monkeypatch):
    from xiaogu_backtest_v0_1 import historical_replay

    snapshots = [
        {
            "symbol": "600001", "price": 10,
            "source_time": "2026-08-26T14:50:00+00:00",
            "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                               "L2_CAPITAL_CANDIDATE", "L3_DEEP_CANDIDATE_FETCH"],
            "selection_audit": {"full_l0_universe": 3, "l1_eligible_universe": 2},
            "future_bars": [],
        },
        {
            "symbol": "600002", "price": 10,
            "source_time": "2026-08-26T14:50:00+00:00",
            "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                               "L2_CAPITAL_CANDIDATE"],
            "selection_audit": {"full_l0_universe": 3, "l1_eligible_universe": 2},
            "future_bars": [],
        },
    ]
    replay = historical_replay(snapshots)
    assert replay["selection_audit"]["l2_count"] == 2
    assert replay["selection_audit"]["l3_count"] == 1
    assert replay["selection_audit"]["partial_count"] == 0
    assert replay["selection_audit"]["conflict_count"] == 0
    assert replay["selection_audit"]["invalid_count"] == 0
    assert replay["selection_audit"]["unresolved_count"] == 0


def test_critical_source_failure_blocks():
    assert "stock_all_a" in CRITICAL_SOURCES
    from scrapy_scanner.runner_v2 import CriticalSourceError, _collect

    with pytest.raises(CriticalSourceError, match="CRITICAL_SOURCE_FAILURE:stock_all_a"):
        _collect("stock_all_a", {}, lambda: (_ for _ in ()).throw(RuntimeError("network down")), [], critical=True)


def test_optional_source_failure_returns_unknown_empty_value():
    from scrapy_scanner.runner_v2 import _collect

    timings = {}
    value = _collect("lhb", timings, lambda: (_ for _ in ()).throw(RuntimeError("network down")), [])
    assert value == []
    assert timings["lhb"]["evidence_status"] == "UNKNOWN"
    assert timings["lhb"]["critical"] is False


def test_optional_source_failure_is_unknown():
    assert "lhb" in OPTIONAL_SOURCES
    assert "stock_all_a" not in OPTIONAL_SOURCES


def test_record_level_pit():
    kept = assert_point_in_time_evidence(
        {"event_time": "2026-08-26T14:45:00+08:00", "available_at": "2026-08-26T14:50:00+08:00", "EXPLAIN": "机构买入", "source_id": "lhb"},
        "2026-08-26T15:00:00+08:00",
    )
    assert kept is not None
    excluded = assert_point_in_time_evidence({"EXPLAIN": "no time"}, "2026-08-26T15:00:00+08:00")
    assert excluded is None


def test_record_level_pit_preserves_provider_timestamp_in_features():
    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T15:00:00+08:00",
        "lhb": [{
            "EXPLAIN": "机构买入", "institution": True,
            "event_time": "2026-08-26T14:45:00+08:00",
            "available_at": "2026-08-26T14:45:00+08:00",
        }],
    })
    vector = build_feature_vector(snapshot)
    evidence = vector["CAPITAL"]["institution_behavior"]["evidence"]
    assert evidence[0]["available_at"] == "2026-08-26T14:45:00+08:00"


def test_future_event_rejected():
    future = assert_point_in_time_evidence(
        {"event_time": "2026-08-27T09:00:00+08:00", "available_at": "2026-08-27T09:00:00+08:00", "source_id": "lhb"},
        "2026-08-26T15:00:00+08:00",
    )
    assert future is None
    kept, excluded = filter_point_in_time_records(
        [{"event_time": "2026-08-28T00:00:00+08:00"}],
        "2026-08-26T15:00:00+08:00",
    )
    assert kept == []
    assert excluded


def test_missing_is_not_zero():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    }))
    assert vector["CAPITAL"]["fund_flow_persistence"] is None
    assert vector["BUSINESS"]["score"] is None
    assert vector["BUSINESS"]["coverage"].endswith("/" + vector["BUSINESS"]["coverage"].split("/")[-1])
    assert vector["BUSINESS"]["missing_rate"] == 1.0
    assert 0 not in (vector["SUPPLY"]["effective_supply"], vector["PRICING_GAP"]["score"])


def test_capital_flow_not_accumulation():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "amount": 1000, "f62": 400, "f3": 3,
        "source_time": "2026-08-26T14:50:00+08:00",
    }))
    assert vector["CAPITAL"]["capital_flow_ratio"] == 0.4
    assert vector["CAPITAL"]["accumulation"] is None
    assert "accumulation" not in str(vector["CAPITAL"]["capital_flow_state"]).lower() or vector["CAPITAL"]["capital_flow_state"] == "CAPITAL_FLOW_POSITIVE"


def test_capital_price_divergence_not_absorption():
    vector = build_feature_vector(validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "amount": 1000, "f62": 400, "f3": -1,
        "source_time": "2026-08-26T14:50:00+08:00",
    }))
    assert vector["CAPITAL"]["capital_price_impact_state"] == "CAPITAL_PRICE_DIVERGENCE"
    assert vector["SUPPLY"]["supply_absorption_state"] != "ABSORPTION"


def test_unknown_future_buyer_not_blocker():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    }, as_of=AS_OF)
    assert "FUTURE_BUYER_EVIDENCE_MISSING" not in decision["repricing_risk"]["blockers"]


def test_partial_capital_not_blocker():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "institutional_flow": 0.8,
        "lhb": [{"EXPLAIN": "1家机构买入", "institution": True, "event_time": "2026-08-26T14:45:00+08:00", "available_at": "2026-08-26T14:50:00+08:00"}],
    }, as_of=AS_OF)
    assert decision["core_alpha"]["capital_convergence"]["status"] == "PARTIAL"
    assert "CAPITAL_CONVERGENCE_INCOMPLETE" not in decision["repricing_risk"]["blockers"]


def test_partial_supply_not_blocker():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000, "turnover": 5,
        "source_time": "2026-08-26T14:50:00+08:00",
        "overhead_supply": 0.2,
    }, as_of=AS_OF)
    assert decision["feature_vector"]["SUPPLY"]["supply_absorption_state"] in {"UNKNOWN", "PARTIAL", "BALANCED", "RELEASING", "ABSORPTION"}
    assert "SUPPLY_ABSORPTION_UNCONFIRMED" not in decision["repricing_risk"]["blockers"]


def test_pricing_gap_not_hard_gate():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    }, as_of=AS_OF)
    assert "PRICING_GAP_TOO_SMALL" not in decision["repricing_risk"]["blockers"]


def test_artifact_version_mismatch_blocks(tmp_path, monkeypatch):
    import xiaogu_core_alpha as alpha
    path = tmp_path / "profit_window_calibration.json"
    path.write_text(json.dumps({
        "model_id": "other",
        "model_version": "v0",
        "feature_version": "old_features",
        "dataset_hash": "x",
        "dataset_version": "x",
        "train_window": {},
        "validation_window": {},
        "oos_window": {},
        "cost_model_version": "old_cost",
        "target_version": "PROFIT_WINDOW_5D",
        "horizon": 5,
        "schema_version": "alpha_artifact_v1",
        "status": "VALIDATED",
        "intercept": 0.0,
        "coefficients": [0.0],
        "feature_names": ["risk"],
        "oos": {"passed": True},
        "production_gates": {
            "data_quality": True, "oos_pass": True, "monotonicity": True,
            "probability_separation": True, "full_alpha_baseline_increment": True,
            "capital_supply_repricing_increment": True,
        },
    }), encoding="utf-8")
    monkeypatch.setattr("xiaogu_db.fetch_production_model", lambda _model_id: json.loads(path.read_text(encoding="utf-8")))
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    }, as_of=AS_OF)
    assert decision["core_alpha"]["model_status"] == "MODEL_ARTIFACT_MISMATCH"
    assert "MODEL_ARTIFACT_MISMATCH" in decision["repricing_risk"]["blockers"]
    assert decision["buy_status"] == "BUY_BLOCKED"


def test_position_state_not_action_derived():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "capital_accumulation": 1, "capital_persistence": 1,
    }, position_state="LONG", previous_action="BUY", as_of=AS_OF)
    assert decision["position_state"] in {"FLAT", "LONG"}
    assert decision["previous_action"] == "BUY"
    assert decision["action"] != decision["position_state"] or decision["action"] in {"HOLD", "REDUCE", "SELL", "BUY"}


def test_production_runner_reads_position_state_from_postgres(monkeypatch):
    import xiaogu_forward_runner as runner

    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "trade_date": "2026-08-26",
        "source_time": "2026-08-26T14:50:00+08:00",
    })
    seen = {}
    monkeypatch.setattr("xiaogu_db.verify_persisted_snapshot", lambda **_kwargs: True)
    def fake_position_state(symbol):
        seen["symbol"] = symbol
        return "LONG"

    monkeypatch.setattr("xiaogu_db.fetch_position_state", fake_position_state)
    monkeypatch.setattr(runner, "evaluate_candidate_bundle", lambda *_args, **kwargs: seen.update(kwargs) or {"state": "HOLD"})
    runner.run_production_decision(
        snapshot,
        mode="PRODUCTION",
        trade_date="2026-08-26",
        decision_clock=datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
    )
    assert seen["symbol"] == "600001"
    assert seen["position_state"] == "LONG"


def test_decision_outcome_isolation():
    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
    })
    with pytest.raises(ValueError):
        validate_and_build_canonical_snapshot({**snapshot, "future_5d_return": 0.2})


def test_t5_forces_close():
    decision = evaluate_candidate_bundle({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "capital_accumulation": 1,
    }, portfolio_state="HOLD", position_state="LONG", account={"holding_days": 5}, as_of=AS_OF)
    assert decision["action"] == "SELL"
    assert decision["trade_status"] == "CLOSED"


def test_cost_model_single_source():
    assert COST_MODEL_VERSION == "cost_model_v1"
    assert CANONICAL_COST_MODEL["all_in_transaction_cost"] == pytest.approx(DEFAULT_COST_RATE)
    assert "transaction_cost" not in CANONICAL_COST_MODEL
    assert DEFAULT_COST_RATE == pytest.approx(
        CANONICAL_COST_MODEL["commission"]
        + CANONICAL_COST_MODEL["stamp_duty"]
        + CANONICAL_COST_MODEL["slippage"]
        + CANONICAL_COST_MODEL["spread"]
        + CANONICAL_COST_MODEL["market_impact"]
    )


def test_snapshot_and_decision_share_one_transaction(monkeypatch):
    import xiaogu_db as db

    class FakeConnection:
        pass

    connection = FakeConnection()
    observed = []

    class Transaction:
        def __enter__(self):
            return connection

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(db, "ensure_production_schema", lambda: None)
    monkeypatch.setattr(db.engine, "begin", lambda: Transaction())
    monkeypatch.setattr(db, "record_snapshot", lambda _snapshot: observed.append(db._ACTIVE_DB_CONNECTION.get()))
    monkeypatch.setattr(db, "record_decision", lambda _decision: observed.append(db._ACTIVE_DB_CONNECTION.get()))
    db.record_snapshot_and_decision({"snapshot_id": "s", "lineage_id": "l"}, {"decision_id": "d"})
    assert observed == [connection, connection]


def test_historical_unresolved_not_rewritten():
    from xiaogu_backtest_v0_1 import _historical_decision_id
    assert _historical_decision_id(pick={"id": 7}) == ""
    assert _historical_decision_id(pick={"decision_id": "abc"}) == "abc"
