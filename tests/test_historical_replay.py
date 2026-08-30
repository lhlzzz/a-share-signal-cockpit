from xiaogu_backtest_v0_1 import HISTORICAL_VALIDATION_HORIZONS, historical_replay


def test_replay_uses_production_decision():
    replay = historical_replay([{
        "symbol": "600001", "price": 10,
        "source_time": "2026-08-26T14:50:00+00:00",
        "future_bars": [],
    }])
    assert replay["decisions"][0]["decision_owner"].endswith("evaluate_candidate_bundle")
    assert replay["horizons"] == HISTORICAL_VALIDATION_HORIZONS


def test_replay_strips_future_labels_before_calling_production_owner(monkeypatch):
    seen = []

    def fake_production_owner(snapshot, **_kwargs):
        seen.append(snapshot)
        assert "future_prices" not in snapshot
        assert "outcomes" not in snapshot
        return {"canonical_snapshot": {"price": 10}, "decision_owner": "fake"}

    monkeypatch.setattr("xiaogu_backtest_v0_1.run_production_decision", fake_production_owner)
    replay = historical_replay([{
        "snapshot": {
            "symbol": "600001", "price": 10,
            "source_time": "2026-08-26T14:50:00+00:00",
        },
        "future_bars": [
        {"date": "2026-08-27", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100,
         "amount": 1000, "source": "test", "source_timestamp": "2026-08-27T15:00:00+08:00"}
            for _ in range(10)
        ],
        "outcomes": {"labels": {"future_5d_return": 0.3}},
    }])
    assert seen[0]["symbol"] == "600001"
    assert "future_bars" not in seen[0]
    assert "outcomes" not in seen[0]
    assert replay["rows"][0]["forward_window"]["profit_window"] is True
    assert replay["rows"][0]["forward_window"]["first_profit_day"] == 1


def test_historical_label_coverage_gate_requires_five_day_window():
    bars = [
        {"date": f"2026-09-{day:02d}", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100,
         "amount": 1000, "source": "test", "source_timestamp": f"2026-09-{day:02d}T15:00:00+08:00",
         "price_basis": "UNADJUSTED"}
        for day in range(1, 6)
    ]
    replay = historical_replay([{
        "snapshot": {
            "symbol": "600001", "price": 10, "trade_date": "2026-08-27",
            "signal_time": "2026-08-27T14:50:00+08:00",
            "source_timestamp": "2026-08-27T14:50:00+08:00",
            "available_at": "2026-08-27T14:50:00+08:00",
            "point_in_time": True, "snapshot_version": "test",
            "source": "test", "price_basis": "UNADJUSTED",
        },
        "future_bars": bars,
    }])
    assert replay["target_quality_gate"]["status"] == "PASS"
    assert replay["alpha_report"]["core_alpha_status"] == "DATA_INSUFFICIENT"
