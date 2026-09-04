import pytest

from xiaogu_forward_result_filler_v0_1 import (
    append_result,
    calculate_horizon_outcomes,
    calculate_horizon_returns,
    eastmoney_future_close_prices,
)


def _bars(count=5):
    return [
        {"date": f"2026-09-{day:02d}", "open": 10, "high": 10.5, "low": 9, "close": 10.2, "volume": 100}
        for day in range(1, count + 1)
    ]


def test_five_day_profit_window_uses_daily_path_and_costs():
    outcomes = calculate_horizon_outcomes(10, _bars())
    assert outcomes["future_5d_return"] == pytest.approx(0.02)
    assert outcomes["max_daily_bar_profit_opportunity_5d"] == pytest.approx(0.047)
    assert outcomes["first_profit_day"] == 1
    assert outcomes["time_to_profit"] == 1
    assert outcomes["max_mae_5d"] == -0.1
    assert outcomes["profit_window"] is True
    assert outcomes["opportunity_5d"] is True
    assert len(outcomes["daily_outcomes"]) == 5
    assert all(outcomes["days"][str(day)]["status"] == "SETTLED" for day in range(1, 6))


def test_missing_five_day_data_is_explicitly_insufficient():
    outcomes = calculate_horizon_outcomes(10, _bars(4))
    assert outcomes["data_status"] == "PARTIAL"
    assert outcomes["max_daily_bar_profit_opportunity_5d"] is None
    assert outcomes["profit_window"] is False
    assert outcomes["opportunity_5d"] is None
    assert outcomes["available_days"] == 4
    assert outcomes["days"]["5"]["status"] == "MISSING"
    assert outcomes["partial_status"] == "PARTIAL"
    assert outcomes["realizability_level"] == "DAILY_BAR_APPROXIMATION"


def test_eastmoney_loader_counts_only_five_future_trading_days(monkeypatch):
    rows = [f"2026-08-{day:02d},10,{day},10,10,0,0,0,0,0,0" for day in range(1, 12)]
    monkeypatch.setattr(
        "xiaogu_forward_result_filler_v0_1.api_get",
        lambda *_args, **_kwargs: {"rc": 0, "data": {"klines": rows}},
    )
    monkeypatch.setattr(
        "xiaogu_forward_result_filler_v0_1.calendar_future_bars",
        lambda entry_date, bars: [bar for bar in bars if bar["trade_date"] > entry_date][:5],
    )
    assert eastmoney_future_close_prices("600001", entry_date="2026-08-01", end_date="2026-10-30") == {5: 6.0}
    assert calculate_horizon_returns(10, {5: 11}) == {"future_5d_return": 0.1}


def test_baostock_loader_normalizes_unadjusted_ohlcv(monkeypatch):
    import sys
    import types

    class Login:
        error_code = "0"
        error_msg = "success"

    class Result:
        error_code = "0"
        error_msg = "success"
        data = [["2026-08-17", "10", "10.5", "9.8", "10.2", "100", "1000"]]

    fake = types.SimpleNamespace(
        login=lambda: Login(),
        query_history_k_data_plus=lambda *args, **kwargs: Result(),
        logout=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    from xiaogu_forward_result_filler_v0_1 import fetch_baostock_daily_bars

    bars = fetch_baostock_daily_bars(
        "600001", start_date="2026-08-17", end_date="2026-08-17", timeout=1,
    )
    assert bars == [{
        "trade_date": "2026-08-17",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 100.0,
        "amount": 1000.0,
        "price_basis": "UNADJUSTED",
        "source": "baostock_daily_kline",
    }]


def test_append_result_exposes_only_profit_window_target():
    result = append_result({
        "id": "decision-1", "date": "2026-08-26", "symbol": "600001", "rule_version": "repricing_production_v1",
        "features_used": {"canonical_snapshot": {"price": 10, "source_time": "2026-08-26T14:50:00+08:00"}},
        "entry_contract": {
            "signal_time": "2026-08-26T14:50:00+00:00", "execution_time": "2026-08-26T14:50:00+00:00",
            "execution_mode": "SIGNAL_TIME_LAST_PRICE", "execution_price": 10, "entry_price": 10,
            "price_basis": "UNADJUSTED", "entry_price_source": "canonical_snapshot.price",
        },
    }, future_bars=_bars())
    assert result["profit_window"] is True
    assert result["opportunity_5d"] is True
    assert result["max_daily_bar_profit_opportunity_5d"] == pytest.approx(0.047)
    assert not any(key.startswith("expected_") and key.endswith("_return") for key in result)
    assert result["future_1d_net_return"] == pytest.approx(0.017)
    assert all(result["days"][str(day)].get("status") == "SETTLED" for day in range(1, 6))


def test_reference_price_not_execution_price():
    result = append_result({
        "id": "paper-1", "date": "2026-08-26", "symbol": "600001",
        "paper_observation_status": "PAPER_OBSERVATION", "paper_observation_state": "OBSERVED",
        "paper_position_state": "PAPER_FLAT",
        "features_used": {"canonical_snapshot": {"price": 10, "source_time": "2026-08-26T14:50:00+08:00"}},
        "reference_price": 10,
        "signal_time": "2026-08-26T14:50:00+00:00",
    }, future_bars=_bars())
    assert result["decision_id"] == "paper-1"
    assert result["paper_observation_state"] == "OBSERVED"
    assert result["paper_position_state"] == "PAPER_FLAT"
    assert result["paper_exit_reason"] is None
    assert result["reference_price"] == 10
    assert result["entry_price"] is None
    assert result["entry_contract"] is None
    assert result["days"]["5"]["close"] == 10.2
    assert result["profit_window"] is True


def test_paper_t5_close():
    result = append_result({
        "id": "paper-t5", "date": "2026-08-26", "symbol": "600001",
        "paper_observation_status": "PAPER_OBSERVATION", "paper_observation_state": "OBSERVED",
        "paper_position_state": "PAPER_LONG",
        "features_used": {"canonical_snapshot": {"price": 10, "source_time": "2026-08-26T14:50:00+08:00"}},
        "entry_contract": {
            "signal_time": "2026-08-26T14:50:00+00:00", "execution_time": "2026-08-26T14:50:00+00:00",
            "execution_mode": "SIGNAL_TIME_LAST_PRICE", "execution_price": 10, "entry_price": 10,
            "price_basis": "UNADJUSTED", "entry_price_source": "canonical_snapshot.price",
        },
    }, future_bars=_bars())
    assert result["paper_observation_state"] == "CLOSED"
    assert result["paper_position_state"] == "PAPER_FLAT"
    assert result["paper_exit_reason"] == "T5_EXPIRY"


def test_pending_filler_appends_only_newly_available_outcomes(tmp_path, monkeypatch):
    import xiaogu_db
    import xiaogu_forward_result_filler_v0_1 as filler

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"record_type":"DECISION","date":"2026-08-01","asof_time":"14:50:00","symbol":"600001","decision":"BUY","features_used":{"canonical_snapshot":{"price":10,"source_time":"2026-08-01T14:50:00+08:00"}},"entry_contract":{"signal_time":"2026-08-01T06:50:00+00:00","execution_time":"2026-08-01T06:50:00+00:00","execution_mode":"SIGNAL_TIME_LAST_PRICE","execution_price":10,"entry_price":10,"price_basis":"UNADJUSTED","entry_price_source":"canonical_snapshot.price"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(filler, "FORWARD_LEDGER", ledger)
    monkeypatch.setattr(filler, "eastmoney_future_bars", lambda *_args, **_kwargs: _bars())
    monkeypatch.setattr(filler, "calendar_future_bars", lambda _entry_date, bars: bars[:5])
    monkeypatch.setattr(xiaogu_db, "fetch_canonical_future_bars", lambda *_args, **_kwargs: _bars())
    monkeypatch.setattr(xiaogu_db, "record_canonical_future_prices", lambda _bars: None)
    stored_returns = []
    monkeypatch.setattr(
        xiaogu_db,
        "record_returns",
        lambda _date, _symbol, payload, decision_id="": stored_returns.append({
            "decision_id": decision_id, "payload": payload,
        }),
    )
    monkeypatch.setattr(xiaogu_db, "fetch_picks", lambda: [{
        "decision_id": "2026-08-01_14:50:00_DECISION_600001",
        "trade_date": "2026-08-01", "symbol": "600001", "state": "BUY",
        "payload": {
            "decision_id": "2026-08-01_14:50:00_DECISION_600001",
            "date": "2026-08-01", "asof_time": "14:50:00", "symbol": "600001",
            "decision": "BUY", "features_used": {
                "canonical_snapshot": {"price": 10, "source_time": "2026-08-01T14:50:00+08:00"}
            },
            "entry_contract": {
                "signal_time": "2026-08-01T06:50:00+00:00",
                "execution_time": "2026-08-01T06:50:00+00:00",
                "execution_mode": "SIGNAL_TIME_LAST_PRICE", "execution_price": 10,
                "entry_price": 10, "price_basis": "UNADJUSTED",
                "entry_price_source": "canonical_snapshot.price",
            },
        },
    }])
    monkeypatch.setattr(xiaogu_db, "fetch_returns", lambda: list(stored_returns))
    monkeypatch.setattr(xiaogu_db, "fetch_paper_observations", lambda: [])
    assert filler.fill_pending_results(end_date="2026-08-10")["filled"] == 1
    assert filler.fill_pending_results(end_date="2026-08-10")["filled"] == 0


def test_outcome_persistence_orders_database_audit_then_memory(monkeypatch):
    import xiaogu_forward_result_filler_v0_1 as filler

    events = []
    monkeypatch.setattr(filler, "FORWARD_LEDGER", __import__("pathlib").Path("/tmp/xiaogu-order-test.jsonl"))
    monkeypatch.setattr(filler, "append_jsonl", lambda *_args, **_kwargs: events.append("audit"))
    monkeypatch.setattr("xiaogu_db.record_returns", lambda *_args, **_kwargs: events.append("database"))
    monkeypatch.setattr(
        "xiaogu_forward_paper_recorder_v0_1.update_trade_memory",
        lambda _result: events.append("memory") or "/tmp/memory.md",
    )

    result = filler._persist_and_append_result({
        "date": "2026-08-26", "symbol": "600001", "decision_id": "decision-1",
    })
    assert events == ["database", "audit", "memory"]
    assert result["audit_persistence"] == {"status": "PASS"}


def test_settled_paper_observation_updates_postgres_lifecycle(monkeypatch):
    import xiaogu_db
    import xiaogu_forward_result_filler_v0_1 as filler

    updates = []
    monkeypatch.setattr(xiaogu_db, "record_returns", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        xiaogu_db,
        "update_paper_observation_state",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    monkeypatch.setattr(filler, "FORWARD_LEDGER", __import__("pathlib").Path("/tmp/xiaogu-paper-lifecycle-test.jsonl"))
    monkeypatch.setattr(filler, "append_jsonl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "xiaogu_forward_paper_recorder_v0_1.update_trade_memory",
        lambda _result: "/tmp/memory.md",
    )

    result = filler._persist_and_append_result({
        "date": "2026-08-26",
        "symbol": "600001",
        "decision_id": "decision-1",
            "paper_signal_id": "paper-signal-1",
            "paper_observation_state": "CLOSED",
            "paper_position_state": "PAPER_FLAT",
            "paper_exit_reason": "T5_EXPIRY",
    })
    assert result["database_persistence"] == {"status": "PASS"}
    assert updates == [
        (
            ("paper-signal-1",),
            {
                "state": "CLOSED",
                "paper_position_state": "PAPER_FLAT",
                "exit_reason": "T5_EXPIRY",
            },
        )
    ]
