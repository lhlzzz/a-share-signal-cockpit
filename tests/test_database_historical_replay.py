import pytest

from xiaogu_backtest_v0_1 import (
    _entry_audit,
    _return_targets,
    build_historical_5d_profit_window_dataset,
    persist_historical_replay,
)
from xiaogu_horizon_evaluation import target_quality_gate
from xiaogu_horizon_evaluation import evaluate_replay


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
        "max_realizable_profit_5d": 0.077,
    }])["horizon_metrics"]["PROFIT_WINDOW_5D"]
    assert metrics["mean_mfe"] == pytest.approx(0.08)
    assert metrics["mean_mae"] == pytest.approx(-0.04)


def test_profit_window_uses_cost_adjusted_high_not_close():
    row = _return_row(t1_mfe=0.022, t1_mae=-0.01, t2_return=-0.01, t3_return=-0.01, t5_return=-0.01)
    targets = _return_targets([row], 10.0)
    assert targets["max_realizable_profit_5d"] == pytest.approx(0.019)
    assert targets["profit_window_flag"] is False


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
    assert gate["checks"]["T+4"] is False


def test_database_builder_never_uses_symbol_date_fallback_or_old_rank_snapshot():
    result = build_historical_5d_profit_window_dataset({
        "picks": [{
            "id": 7, "trade_date": "2026-08-01", "symbol": "600001",
            "decision": "BUY", "formal_rank_snapshot_id": "wrong-snapshot",
        }],
        "returns": [_return_row(pick_id=None, candidate_snapshot_id="different-snapshot")],
        "daily_candidates": [],
    })
    assert result["counts"]["invalid"] == 1
    assert result["audit"]["unresolved_decisions"] == ["pick:7"]


def test_persist_historical_replay_writes_only_research_artifact(tmp_path):
    result = persist_historical_replay({"rows": [], "research_artifact_path": tmp_path / "replay.json"})
    assert result["database_persistence"]["owner"] == "independent_research_artifact"
    assert (tmp_path / "replay.json").exists()
