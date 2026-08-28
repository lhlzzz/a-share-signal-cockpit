from pathlib import Path
import pytest

from xiaogu_forward_eligibility import candidate_universe
from xiaogu_forward_features import FEATURE_GROUPS, build_feature_vector
from xiaogu_forward_snapshot import attach_research_observations, canonical_snapshot
from xiaogu_portfolio_decision import evaluate_candidate_bundle
from scrapy_scanner.runner_v2 import build_canonical_snapshots


def test_snapshot_has_no_future_fields_and_features_are_measurements():
    with pytest.raises(ValueError):
            canonical_snapshot({"symbol": "600001", "future_5d_return": 0.1})
    with pytest.raises(ValueError):
        canonical_snapshot({
            "lineage_id": "already-canonical",
            "raw": {"symbol": "600001", "future_5d_return": 0.1},
        })
    assert canonical_snapshot({"symbol": "600001", "source_time": "2026-08-25 14:39:45+08"})["source_time"].endswith("+08:00")
    vector = build_feature_vector(canonical_snapshot({"symbol": "600001", "price": 10, "source_time": "2026-08-26T14:50:00+00:00"}))
    assert tuple(key for key in FEATURE_GROUPS if key in vector) == FEATURE_GROUPS


def test_nested_future_outcomes_are_rejected_before_feature_measurement():
    with pytest.raises(ValueError, match=r"raw\.research\.labels"):
        canonical_snapshot({
            "symbol": "600001",
            "raw": {"research": {"labels": {"future_5d_return": 0.1}}},
        })
    with pytest.raises(ValueError, match=r"\$\.lhb\[0\]\.post_result"):
        attach_research_observations(
            {"symbol": "600001"},
            lhb=[{"post_result": {"return": 0.1}}],
        )


def test_eastmoney_quote_fields_feed_existing_measurements():
    vector = build_feature_vector(canonical_snapshot({
        "f12": "600001", "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
        "f7": 3, "f8": 2, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 400,
    }))
    assert vector["capital"]["accumulation"] == 0.4
    assert vector["position"]["relative_strength"] == 0.4
    assert vector["execution"]["short_term_overheat"] == 0.4


def test_eastmoney_capital_flow_reaches_core_alpha():
    decision = evaluate_candidate_bundle({
        "f12": "600001", "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
        "f7": 3, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 400,
        "source_time": "2026-08-26T14:50:00+08:00",
    })
    assert decision["research_context"]["capital"]["accumulation"] == 0.4
    assert decision["core_alpha"]["axes"]["CAPITAL"] > 0


@pytest.mark.parametrize(("flow", "change", "expected"), [
    (400, 3, "DEMAND_CONFIRMATION"),
    (400, 0, "SUPPLY_ABSORPTION"),
    (400, -3, "DISTRIBUTION_RISK"),
    (-400, 3, "PRICE_SUPPORTED_DIVERGENCE"),
])
def test_capital_price_impact_distinguishes_flow_and_price_response(flow, change, expected):
    vector = build_feature_vector(canonical_snapshot({
        "symbol": "600001", "price": 10, "high": 10.5, "low": 9.5,
        "amount": 1_000, "f62": flow, "f3": change, "f184": 2,
    }))
    assert vector["CAPITAL"]["capital_price_impact_state"] == expected


def test_same_day_research_observations_feed_existing_contexts():
    snapshot = canonical_snapshot(attach_research_observations(
        {
            "f12": "600001", "f14": "示例公司", "f100": "示例行业",
            "f2": 10, "f3": 4, "f5": 100, "f6": 1_000,
            "f7": 3, "f15": 10.5, "f16": 9.5, "f17": 9.8, "f62": 100,
        },
        stock_capital_flow={"f62": 400},
        earnings_preview={"WEIGHTAVG_ROE": 20, "NOTICE_DATE": "2026-08-26"},
        industry_flow={"f3": 5},
        stock_reports=[{"title": "公司研究"}],
        industry_reports=[{"title": "行业研究"}],
        lhb=[{
            "EXPLAIN": "1家机构买入", "NET_BS_AMT": -100,
            "ACCUM_AMOUNT": 100, "TRADE_DATE": "2026-08-26",
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
    import xiaogu_forward_paper_recorder_v0_1 as recorder
    from xiaogu_forward_result_filler_v0_1 import append_result

    monkeypatch.setenv("XIAOGU_MEMORY_ROOT", str(tmp_path / "memory"))
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

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join([
        '{"record_type":"DECISION","symbol":"600001","date":"2026-08-25","decision":"WATCH","features_used":{}}',
        '{"record_type":"DECISION","symbol":"600001","date":"2026-08-26","decision":"BUY","features_used":{"repricing_readiness":{"CAPITAL_READY":true},"repricing_risk":{"blockers":[]},"future_buyer_map":{"potential_next_buyer":[{"buyer":"Institution"}]}}}',
        '{"record_type":"RESULT","decision_id":"2026-08-26__DECISION_600001","future_5d_return":0.1}',
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(xiaogu_api, "LEDGER", ledger)
    assert xiaogu_api.current_state()["positions"][0]["decision"] == "BUY"
    assert xiaogu_api.current_decision()["decision"] == "BUY"
    assert xiaogu_api.trades()[0]["new_state"] == "BUY"
    decision_id = xiaogu_api.trades()[0]["decision_id"]
    assert xiaogu_api.trade(decision_id)["decision_id"] == decision_id
    assert xiaogu_api.trade("missing-decision")["found"] is False


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

    snapshot = canonical_snapshot({
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
    monkeypatch.setattr("xiaogu_db.fetch_position_outcome", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "load_latest_snapshot_bundle",
        lambda _date: {"available": True, "canonical_snapshots": [canonical_snapshot({
            "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
            "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        })]},
    )
    monkeypatch.setattr(runner, "run_production_decision", lambda *args, **kwargs: {
        "state": "SELL", "reason": "MAX_HOLDING_BOUNDARY_CLOSED",
        "canonical_snapshot": {"trade_date": "2026-08-26", "symbol": "600001", "source_time": "2026-08-26T14:50:00+08:00"},
    })
    monkeypatch.setattr(runner, "_write_ledger_record", lambda decision: Path("/tmp/unused"))
    monkeypatch.setattr("xiaogu_utils.load_jsonl", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("JSONL_IS_NOT_POSITION_STATE")))
    reviewed = runner.daily_position_review("2026-08-26")
    assert reviewed[0]["state"] == "SELL"

