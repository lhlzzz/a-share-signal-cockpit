import pytest

from xiaogu_backtest_v0_1 import (
    _database_linked_decision_ranges,
    _capital_history_index,
    _capital_history_window,
    _entry_audit,
    _merge_missing_future_targets,
    _return_targets,
    build_capital_behavior_research_dataset,
    build_historical_5d_profit_window_dataset,
    persist_historical_replay,
    supplement_database_future_prices,
)
from xiaogu_horizon_evaluation import (
    _probability_separation,
    _split_rows,
    diagnose_features,
    evaluate_replay,
    target_quality_gate,
)


def _return_row(**kwargs):
    row = {
        "id": kwargs.pop("id", 1),
        "trade_date": "2026-08-01",
        "symbol": "600001",
        "production_run_id": "run-1",
        "candidate_snapshot_id": "snap-1",
        "entry_price": 10.0,
        "entry_price_source": "BACKFILL_T_DAY_CLOSE",
        "entry_price_basis": "UNADJUSTED_DAILY_OHLC",
        "entry_time": "15:00:00",
        "t1_return": 0.01,
        "t2_return": 0.02,
        "t3_return": 0.03,
        "t5_return": 0.04,
        "t1_mfe": 0.03,
        "t1_mae": -0.02,
        "settlement_evidence": {
            "execution_contract": {
                "signal_price": 10.0,
                "execution_price": 10.0,
                "signal_time": "15:00:00",
            },
            "execution_model": {
                "entry_reference_price": 10.0,
                "entry_execution_price": 10.01,
            },
        },
    }
    row.update(kwargs)
    return row


def test_entry_priority_ignores_derived_execution_model_fill():
    audit = _entry_audit([_return_row()])
    assert audit["entry_price"] == pytest.approx(10.0)
    assert audit["execution_price"] == pytest.approx(10.0)
    assert audit["execution_time"] == "15:00:00"
    assert "ENTRY_PRICE_CONFLICT" not in audit["issues"]
    assert audit["derived_candidates"][-1] == ("execution_model.entry_execution_price", 10.01)


def test_entry_conflict_and_missing_metadata_are_explicit():
    conflict = _entry_audit([_return_row(entry_price=10.0), _return_row(id=2, entry_price=10.2)])
    assert "ENTRY_PRICE_CONFLICT" in conflict["issues"]
    missing = _entry_audit([_return_row(entry_price=None, settlement_evidence={})])
    assert "MISSING_ENTRY_METADATA" in missing["issues"]


def test_duplicate_targets_merge_when_equal_and_conflict_when_not():
    same = _return_targets([_return_row(), _return_row(id=2)], 10.0)
    assert same["conflicts"] == []
    conflict = _return_targets([_return_row(), _return_row(id=2, t2_return=0.09)], 10.0)
    assert "T2_RETURN_CONFLICT" in conflict["conflicts"]


def test_t1_net_return_is_not_used_as_gross_target():
    row = _return_row(t1_return=None, t1_return_close=None, t1_net_return=-0.20)
    assert _return_targets([row], 10.0)["t1_return"] is None


def test_evaluation_reports_mfe_and_mae_for_diagnostic_groups():
    metrics = evaluate_replay([{
        "canonical_entry_price": 10.0,
        "t1_return": 0.01, "t2_return": 0.01, "t3_return": 0.01,
        "t4_return": 0.01, "t5_return": 0.01,
        "mfe_5d": 0.08, "mae_5d": -0.04,
        "max_daily_bar_profit_opportunity_5d": 0.077,
    }])["horizon_metrics"]["PROFIT_WINDOW_5D"]
    assert metrics["mean_mfe"] == pytest.approx(0.08)
    assert metrics["mean_mae"] == pytest.approx(-0.04)


def test_feature_diagnostics_marks_zero_filled_production_inputs_missing():
    row = {
        "target_quality": "CANONICAL",
        "profit_window": True,
        "max_daily_bar_profit_opportunity_5d": 0.03,
        "current_decision_payload": {
            "core_alpha": {
                "profit_window_feature_values": {
                    "capital_persistence": 0.0,
                    "supply_absorption": 0.0,
                    "pricing_gap": 0.1,
                },
                "axes": {"MARKET": 0.5},
            },
            "feature_vector": {"MARKET": {"score": 0.5}},
            "canonical_snapshot": {"raw": {}},
        },
    }
    diagnostics = diagnose_features([row], feature_names=("capital_persistence", "supply_absorption", "pricing_gap"))
    assert diagnostics["features"]["capital_persistence"]["missing_rate"] == 1.0
    assert diagnostics["features"]["supply_absorption"]["missing_rate"] == 1.0
    assert diagnostics["features"]["pricing_gap"]["missing_rate"] == 1.0
    assert set(diagnostics["constant_features"]) == {"capital_persistence", "supply_absorption", "pricing_gap"}
    assert diagnostics["label_counts"] == {"positive": 1, "negative": 0, "missing": 0}


def test_probability_separation_blocks_constant_predictions():
    rows = [{"profit_window": True}, {"profit_window": False}]
    separation = _probability_separation(rows, [0.55, 0.55])
    assert separation["status"] == "MODEL_NOT_DISCRIMINATIVE"


def test_feature_diagnostics_accepts_only_complete_future_buyer_evidence():
    row = {
        "profit_window": True,
        "current_decision_payload": {
            "core_alpha": {"profit_window_feature_values": {"future_buyer_evidence": 0.8}},
            "future_buyer_map": {
                "potential_next_buyer": [{
                    "buyer": "institutions",
                    "capacity": 0.8,
                    "evidence_status": "OBSERVED",
                    "evidence": "allocation notice",
                    "source": "exchange_filing",
                    "observed_at": "2026-08-28T14:00:00+08:00",
                }],
            },
        },
    }
    diagnostics = diagnose_features([row], feature_names=("future_buyer_evidence",))
    assert diagnostics["features"]["future_buyer_evidence"]["missing_rate"] == 0.0


def test_feature_diagnostics_reads_capital_measurement_from_feature_vector():
    row = {
        "profit_window": True,
        "feature_vector": {"CAPITAL": {"capital_flow_ratio": 0.25}},
    }
    diagnostics = diagnose_features([row], feature_names=("capital_flow_ratio",))
    assert diagnostics["features"]["capital_flow_ratio"]["missing_rate"] == 0.0


def test_capital_history_window_enforces_available_at_pit_and_deduplicates_dates():
    history = []
    for day in range(21, 27):
        history.append({
            "symbol": "600001",
            "trade_date": f"2026-08-{day}",
            "capital_flow": 100.0,
            "amount": 1_000.0,
            "source": "canonical_historical_snapshots",
            "source_time": f"2026-08-{day}T15:00:00+08:00",
            "available_at": f"2026-08-{day}T15:00:00+08:00",
        })
    history.append({
        **history[-1],
        "source_time": "2026-08-26T15:01:00+08:00",
        "available_at": "2026-08-26T15:01:00+08:00",
        "capital_flow": 999.0,
    })
    assets = {"canonical_historical_snapshots": [{
        "snapshot_id": "snapshot-26",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "source_time": "2026-08-26T15:00:00+08:00",
        "available_at": "2026-08-26T15:00:00+08:00",
        "payload": {"raw": {"capital_history": history}},
    }]}
    indexed = _capital_history_index(assets)
    window = _capital_history_window(
        indexed,
        symbol="600001",
        trade_date="2026-08-26",
        as_of="2026-08-26T15:00:00+08:00",
    )
    assert [row["trade_date"] for row in window] == [f"2026-08-{day}" for day in range(21, 27)]
    assert window[-1]["capital_flow"] == 100.0


def test_capital_history_historical_pit_exclusion():
    history = [{
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "capital_flow": 100.0,
        "amount": 1_000.0,
        "source": "eastmoney_capital_history",
        "source_time": "2026-08-26T15:00:00+08:00",
        "available_at": "2026-08-31T15:00:00+08:00",
        "observation_class": "FORWARD_OBSERVATION_ONLY",
    }]
    assets = {"canonical_historical_snapshots": [{
        "snapshot_id": "snapshot-26",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "source_time": "2026-08-26T15:00:00+08:00",
        "available_at": "2026-08-26T15:00:00+08:00",
        "payload": {"raw": {"capital_history": history}},
    }]}
    indexed = _capital_history_index(assets)
    assert indexed.get("600001") == [{
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "capital_flow": None,
        "amount": None,
        "volume": None,
        "turnover": None,
        "pct_change": None,
        "close": None,
        "relative_volume": None,
        "source": "canonical_historical_snapshots",
        "source_id": "canonical_historical_snapshots",
        "source_time": "2026-08-26T15:00:00+08:00",
        "available_at": "2026-08-26T15:00:00+08:00",
        "snapshot_id": "snapshot-26",
    }]


def test_capital_research_dataset_is_explicitly_non_production():
    dataset = build_capital_behavior_research_dataset([{
        "decision_id": None,
        "snapshot_id": "snapshot-1",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "current_decision_payload": {
            "canonical_snapshot": {"as_of": "2026-08-26T15:00:00+08:00"},
            "feature_vector": {"CAPITAL": {
                "capital_flow_ratio": 0.2,
                "capital_persistence": 0.5,
                "capital_acceleration": 0.1,
                "capital_inflection": 1.0,
                "capital_price_efficiency": 0.2,
                "capital_price_divergence_state": "CAPITAL_UP_PRICE_FLAT",
                "capital_history_audit": {"returned_observations": 6},
                "main_force_behavior": {"direction": "UNKNOWN"},
                "institution_behavior": {"direction": "UNKNOWN"},
                "hot_money_behavior": {"direction": "UNKNOWN"},
            }},
            "core_alpha": {"capital_convergence": {"status": "UNKNOWN"}},
        },
    }])
    assert dataset["production_permission"] == "NONE"
    assert dataset["research_only"] is True
    assert dataset["rows"][0]["decision_id"] is None
    assert dataset["rows"][0]["capital_price_divergence"] == "CAPITAL_UP_PRICE_FLAT"


def test_capital_ablation_keeps_price_baseline_and_selectivity_metrics():
    from xiaogu_horizon_evaluation import _ablation_report, _split_rows

    rows = []
    for index in range(60):
        price = 0.8 if index % 4 in (0, 1) else 0.2
        flow = 0.8 if index % 3 else 0.1
        label = bool(price > 0.5 and flow > 0.2)
        rows.append({
            "trade_date": f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}",
            "price_strength": price,
            "capital_flow_ratio": flow,
            "capital_persistence": flow,
            "capital_acceleration": flow,
            "capital_inflection": float(flow > 0.5),
            "capital_price_efficiency": flow,
            "capital_price_divergence": "CAPITAL_UP_PRICE_UP" if label else "CAPITAL_DOWN_PRICE_DOWN",
            "profit_window": label,
            "max_daily_bar_profit_opportunity_5d": 0.03 if label else 0.01,
            "target_quality": "CANONICAL",
        })
    train, validation, oos = _split_rows(rows)
    report = _ablation_report(train, validation, oos)
    price = report["cumulative"]["PRICE"]
    capital = report["cumulative"]["PRICE + CAPITAL"]
    assert price["oos"]["samples"] > 0
    assert capital["price_baseline_delta"]["pr_auc"] is not None
    assert "TOP_5_PERCENT" in capital["selectivity"]
    assert "TOP_10_PERCENT" in capital["selectivity"]
    assert "probability_std" in capital["oos"]


def test_profit_window_uses_cost_adjusted_high_not_close():
    row = _return_row(
        t1_mfe=0.022,
        t1_mae=-0.01,
        future_1d_open=10.0,
        future_1d_high=10.22,
        future_1d_low=9.9,
        future_1d_close=9.95,
        t2_return=-0.01,
        t3_return=-0.01,
        t5_return=-0.01,
    )
    targets = _return_targets([row], 10.0)
    assert targets["max_daily_bar_profit_opportunity_5d"] == pytest.approx(0.019)
    assert targets["net_profit_window"] == pytest.approx(0.019)
    assert "max_realizable_profit_5d" not in targets
    assert "max_profit_5d" not in targets
    assert targets["profit_window_flag"] is None


def test_gate_does_not_trust_quality_label_or_fabricate_t4():
    gate = target_quality_gate([{
        "target_quality": "CANONICAL",
        "canonical_entry_price": 10.0,
        "t1_return": 0.01,
        "t2_return": 0.01,
        "t3_return": 0.01,
        "t5_return": 0.01,
    }])
    assert gate["status"] == "BLOCKED"
    assert gate["checks"]["T+4_OHLC"] is False


def test_gate_requires_day_by_day_ohlc_not_only_returns():
    gate = target_quality_gate([{
        "canonical_entry_price": 10.0,
        **{f"t{day}_return": 0.01 for day in range(1, 6)},
        "future_5d_ohlc_coverage": True,
    }])
    assert gate["status"] == "BLOCKED"
    assert gate["horizon_coverage"]["1"]["return"] == 1.0
    assert gate["horizon_coverage"]["1"]["ohlc"] == 0.0


def test_database_builder_requires_explicit_linked_snapshot():
    result = build_historical_5d_profit_window_dataset({
        "picks": [{
            "id": 7, "trade_date": "2026-08-01", "symbol": "600001",
            "decision": "BUY",
        }],
        "returns": [_return_row(pick_id=None, candidate_snapshot_id="different-snapshot")],
        "daily_candidates": [],
    })
    assert result["counts"]["invalid"] == 1
    assert "pick:" not in str(result["audit"]["unresolved_decisions"])
    assert result["audit"]["unresolved_decisions"]


def test_oos_split_keeps_each_trade_date_in_one_partition():
    rows = [
        {"trade_date": date, "profit_window": bool(index % 2), "max_daily_bar_profit_opportunity_5d": 0.03}
        for index, date in enumerate(("2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"))
        for _ in range(2)
    ]
    partitions = _split_rows(rows)
    partition_dates = [
        {row["trade_date"] for row in partition}
        for partition in partitions
    ]
    assert all(left.isdisjoint(right) for index, left in enumerate(partition_dates) for right in partition_dates[index + 1:])


def test_database_linked_ranges_ignore_rows_without_trade_date():
    assert _database_linked_decision_ranges({
        "picks": [{"id": 1, "symbol": "600001", "trade_date": None}],
        "returns": [{"pick_id": 1}],
        "daily_candidates": [],
    }) == {}


def test_persist_historical_replay_writes_only_research_artifact(tmp_path):
    result = persist_historical_replay({"rows": [], "research_artifact_path": tmp_path / "replay.json"})
    assert result["database_persistence"]["owner"] == "independent_research_artifact"
    assert (tmp_path / "replay.json").exists()


def test_missing_future_targets_are_filled_without_overwriting_returns():
    targets = _return_targets([
        _return_row(
            future_1d_open=10.0,
            future_1d_high=10.1,
            future_1d_low=9.9,
            future_1d_close=10.05,
        )
    ], 10.0)
    bars = [
        {
            "trade_date": f"2026-08-{day:02d}",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 100.0,
            "amount": 1000.0,
            "source": "external",
            "source_timestamp": f"2026-08-{day:02d}T15:00:00+08:00",
            "price_basis": "UNADJUSTED",
        }
        for day in range(2, 7)
    ]
    merged = _merge_missing_future_targets(targets, bars, 10.0)
    assert merged["days"]["1"]["close"] == pytest.approx(10.05)
    assert merged["days"]["2"]["close"] == pytest.approx(10.2)
    assert merged["complete_5d"] is True
    assert "T1_RETURN_EXTERNAL_CONFLICT" not in merged["conflicts"]


def test_future_target_merge_handles_complete_ohlc_without_existing_mfe():
    targets = _return_targets([], 10.0)
    bars = [{
        "trade_date": f"2026-08-{day:02d}",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 100.0,
        "amount": 1000.0,
        "source": "external",
        "source_timestamp": f"2026-08-{day:02d}T15:00:00+08:00",
        "price_basis": "UNADJUSTED",
    } for day in range(2, 7)]
    merged = _merge_missing_future_targets(targets, bars, 10.0)
    assert merged["mfe_5d"] == pytest.approx(0.05)


def test_future_supplementation_uses_cache_and_only_linked_decisions(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(symbol, *, start_date, end_date, timeout):
        calls.append((symbol, start_date, end_date))
        return [
            {
                "trade_date": f"2026-08-{day:02d}",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 100.0,
                "amount": 1000.0,
            }
            for day in range(2, 7)
        ]

    monkeypatch.setattr(
        "xiaogu_forward_result_filler_v0_1.fetch_eastmoney_daily_bars",
        fake_fetch,
    )
    monkeypatch.setattr(
        "xiaogu_db.init_db",
        lambda: None,
    )
    monkeypatch.setattr(
        "xiaogu_db.record_canonical_future_prices",
        lambda bars: None,
    )
    assets = {
        "picks": [{"id": 1, "symbol": "600001", "trade_date": "2026-08-01"}],
        "returns": [{"pick_id": 1}],
        "daily_candidates": [],
        "canonical_future_prices": [],
    }
    result = supplement_database_future_prices(
        assets,
        cache_path=tmp_path / "future-bars.json",
        end_date="2026-08-28",
    )
    assert result["fetched_symbols"] == 1
    assert calls == [("600001", "2026-08-01", "2026-08-15")]
    assert len(assets["canonical_future_prices"]) == 5


def test_future_supplementation_falls_back_to_baostock(tmp_path, monkeypatch):
    import xiaogu_forward_result_filler_v0_1 as filler

    calls = []

    def eastmoney(*_args, **_kwargs):
        raise RuntimeError("EASTMONEY_UNAVAILABLE")

    def baostock(symbol, *, start_date, end_date, timeout):
        calls.append((symbol, start_date, end_date, timeout))
        return [{
            "trade_date": f"2026-08-{day:02d}", "open": 10.0,
            "high": 10.5, "low": 9.8, "close": 10.2,
            "volume": 100.0, "amount": 1000.0,
        } for day in range(2, 7)]

    monkeypatch.setattr(filler, "fetch_eastmoney_daily_bars", eastmoney)
    monkeypatch.setattr(filler, "fetch_baostock_daily_bars", baostock)
    monkeypatch.setattr("xiaogu_db.init_db", lambda: None)
    monkeypatch.setattr("xiaogu_db.record_canonical_future_prices", lambda bars: None)
    assets = {
        "picks": [{"id": 1, "symbol": "600001", "trade_date": "2026-08-01"}],
        "returns": [{"pick_id": 1}], "daily_candidates": [],
        "canonical_future_prices": [],
    }
    result = supplement_database_future_prices(
        assets, cache_path=tmp_path / "future-bars.json", end_date="2026-08-28",
    )
    assert calls == [("600001", "2026-08-01", "2026-08-15", 10)]
    assert result["provider_counts"] == {"baostock_daily_kline": 1}
    assert all(bar["source"] == "baostock_daily_kline" for bar in assets["canonical_future_prices"])
