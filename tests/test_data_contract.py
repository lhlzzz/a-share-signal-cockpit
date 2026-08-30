from pathlib import Path
import json
import sys
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
        stock_capital_flow={"f62": 400, "available_at": "2026-08-26T14:50:00+08:00"},
        earnings_preview={"WEIGHTAVG_ROE": 20, "NOTICE_DATE": "2026-08-26", "available_at": "2026-08-26T14:50:00+08:00"},
        industry_flow={"f3": 5, "available_at": "2026-08-26T14:50:00+08:00"},
        stock_reports=[{"title": "公司研究"}],
        industry_reports=[{"title": "行业研究"}],
        lhb=[{
            "EXPLAIN": "1家机构买入", "NET_BS_AMT": -100,
            "ACCUM_AMOUNT": 100, "TRADE_DATE": "2026-08-26",
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


def test_recorder_accepts_trade_events_but_rejects_watch_memory(tmp_path, monkeypatch):
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
    with pytest.raises(ValueError, match="RECORDER_ACCEPTS_PRODUCTION_EVENTS_ONLY"):
        recorder.append_production_decision({"state": "WATCH"})
    assert not list((tmp_path / "memory").glob("**/*.md"))


def test_recorder_persists_buy_without_watch_memory(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    monkeypatch.setattr(recorder, "FORWARD_LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(recorder, "SNAPSHOT_ROOT", tmp_path / "snapshots")
    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
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
    assert record["memory_path"].endswith("BUY/2026-08-26_600001.md")
    assert (tmp_path / "memory" / "decisions" / "BUY" / "2026-08-26_600001.md").exists()


def test_post_trade_review_and_memory_are_written_for_complete_window(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_paper_recorder_v0_1 as recorder
    from xiaogu_forward_result_filler_v0_1 import append_result

    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
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
    assert path and (tmp_path / "memory" / "post_trade_review" / "2026-08-26_600001.md").exists()


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
        "decision_id": "decision-1", "symbol": "600001", "state": "BUY",
        "position_state": "LONG", "payload": {},
    }])
    assert xiaogu_api.current_state()["positions"][0]["decision"] == "BUY"
    assert xiaogu_api.current_decision()["decision"] == "BUY"
    assert xiaogu_api.trades()[0]["new_state"] == "BUY"
    decision_id = xiaogu_api.trades()[0]["decision_id"]
    assert xiaogu_api.trade(decision_id)["decision_id"] == decision_id
    assert xiaogu_api.trade("missing-decision")["found"] is False


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
         "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                            "L2_CAPITAL_CANDIDATE", "L3_DEEP_CANDIDATE_FETCH"]},
        {"symbol": "600002", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
         "source_layers": ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                            "L2_CAPITAL_CANDIDATE"]},
        {"symbol": "600003", "price": 10, "volume": 100, "amount": 1000,
         "trade_date": "2026-08-26", "source_time": "2026-08-26T14:50:00+08:00",
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
    with pytest.raises(ValueError, match="NO_PRODUCTION_SNAPSHOT"):
        run_production_decision(snapshot, mode="PRODUCTION", persisted=False, trade_date="2026-08-26")


def test_position_review_reads_postgres_not_jsonl(monkeypatch):
    import xiaogu_forward_runner as runner

    calls = {"jsonl": 0}

    def fake_positions():
        return [{
            "symbol": "600001",
            "trade_date": "2026-08-25",
            "state": "HOLD",
            "decision": "HOLD",
            "decision_id": "d1",
        }]

    monkeypatch.setattr("xiaogu_db.fetch_open_positions", fake_positions)
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {"status": "OUTCOME_NOT_BOUND"})
    monkeypatch.setattr(
        "xiaogu_db.fetch_persisted_canonical_snapshots",
        lambda *_args, **_kwargs: [validate_and_build_canonical_snapshot({
            "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
            "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        })],
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
    with pytest.raises(ValueError, match="NO_PRODUCTION_SNAPSHOT"):
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
        db.execute(
            text(
                "INSERT INTO picks (trade_date, symbol, decision, state, decision_id, payload) "
                "VALUES ('2026-08-01', '600001', 'BUY', 'BUY', 'test-trade-a', CAST(:payload AS jsonb)), "
                "       ('2026-08-08', '600001', 'BUY', 'BUY', 'test-trade-b', CAST(:payload AS jsonb))"
            ),
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
