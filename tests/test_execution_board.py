from datetime import datetime
from inspect import getsource

import pytest

from xiaogu_forward_eligibility import (
    BOARD_BSE,
    BOARD_CHINEXT,
    BOARD_MAIN,
    BOARD_STAR,
    BOARD_UNKNOWN,
    cheap_eligibility_blockers,
    classify_execution_board,
    execution_universe,
)
from xiaogu_forward_paper_recorder_v0_1 import validate_paper_observation
from xiaogu_forward_runner import evaluate_candidate_rows
from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot
from xiaogu_portfolio_decision import evaluate_candidate_bundle


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")


def _row(symbol, f13, **extra):
    payload = {
        "symbol": symbol,
        "f12": symbol,
        "f13": f13,
        "f1": 2,
        "f148": 65,
        "price": 10,
        "open": 9.9,
        "high": 10.3,
        "low": 9.7,
        "volume": 100,
        "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00",
        "trade_date": "2026-08-26",
        "buyable": True,
        "liquidity_score": 1,
    }
    payload.update(extra)
    return payload


def test_main_board_is_execution_eligible():
    shanghai = classify_execution_board(_row("600000", 1))
    shenzhen = classify_execution_board(_row("000001", 0))
    sme = classify_execution_board(_row("002001", 0))
    new_sme = classify_execution_board(_row("003001", 0))
    assert shanghai["board"] == BOARD_MAIN and shanghai["execution_eligible"] is True
    assert shenzhen["board"] == BOARD_MAIN and shenzhen["execution_eligible"] is True
    assert sme["board"] == BOARD_MAIN and sme["execution_eligible"] is True
    assert new_sme["board"] == BOARD_MAIN and new_sme["execution_eligible"] is True


def test_star_is_not_execution_eligible():
    info = classify_execution_board(_row("688001", 1))
    assert info["board"] == BOARD_STAR
    assert info["execution_eligible"] is False
    assert info["reason"] == "NOT_EXECUTION_ELIGIBLE"


def test_chinext_is_not_execution_eligible():
    chi_next = classify_execution_board(_row("300001", 0))
    registered = classify_execution_board(_row("301001", 0))
    assert chi_next["board"] == BOARD_CHINEXT
    assert registered["board"] == BOARD_CHINEXT
    assert chi_next["execution_eligible"] is False
    assert registered["execution_eligible"] is False


def test_bse_is_not_execution_eligible():
    bj_new = classify_execution_board(_row("920992", 0))
    bj_old = classify_execution_board(_row("830001", 0))
    neeq = classify_execution_board(_row("430001", 0))
    assert bj_new["board"] == BOARD_BSE
    assert bj_old["board"] == BOARD_BSE
    assert neeq["board"] == BOARD_BSE
    assert bj_new["execution_eligible"] is False


def test_unknown_board_is_blocked():
    missing_market = classify_execution_board({"symbol": "600000", "price": 10, "f1": 2})
    assert missing_market["board"] == BOARD_UNKNOWN
    assert missing_market["execution_eligible"] is False
    eligible, audit = execution_universe([{"symbol": "600000", "price": 10, "volume": 100, "amount": 1000, "f1": 2}])
    assert eligible == []
    assert audit["rejected"][0]["reason"] == "NOT_EXECUTION_ELIGIBLE"


def test_st_main_board_not_rejected_by_board_policy():
    info = classify_execution_board(_row("600001", 1, f14="*ST示例", name="*ST示例", f148=4))
    assert info["board"] == BOARD_MAIN
    assert info["board_allowed"] is True
    assert info["execution_eligible"] is True


def test_star_still_blocked():
    info = classify_execution_board(_row("688001", 1, f14="*ST科创", name="*ST科创", f148=4))
    assert info["board"] == BOARD_STAR
    assert info["execution_eligible"] is False


def test_main_board_halted_is_board_allowed_but_not_tradeable():
    row = _row("600002", 1, halted=True, trade_status="HALTED")
    info = classify_execution_board(row)
    assert info["board_allowed"] is True
    assert "HALTED" in cheap_eligibility_blockers(row)
    eligible, audit = execution_universe([row])
    assert eligible == []
    assert "HALTED" in audit["rejected"][0]["blockers"]
    assert "NOT_EXECUTION_ELIGIBLE" not in audit["rejected"][0]["blockers"]


def test_board_metadata_conflict_fails_closed():
    info = classify_execution_board(_row("300001", 1))
    assert info["reason"] == "BOARD_IDENTITY_CONFLICT"
    assert info["board"] == BOARD_UNKNOWN
    assert info["execution_eligible"] is False
    eligible, audit = execution_universe([_row("300001", 1, volume=100, amount=1000)])
    assert eligible == []
    assert audit["rejected"][0]["reason"] == "BOARD_IDENTITY_CONFLICT"


def test_name_is_not_board_truth():
    named_star = classify_execution_board(_row("600000", 1, f14="科创示例", name="科创示例"))
    named_main = classify_execution_board(_row("688001", 1, f14="主板示例", name="主板示例"))
    assert named_star["board"] == BOARD_MAIN
    assert named_main["board"] == BOARD_STAR


def test_star_production_decision_blocked_even_with_high_price_strength():
    decision = evaluate_candidate_bundle(
        _row("688001", 1, high=20, pct_chg=9, f62=900, f184=8),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert decision["execution_board"] == BOARD_STAR
    assert decision["execution_eligible"] is False
    assert decision["paper_observation"] is None
    assert decision["state"] != "BUY"
    blockers = list(decision.get("gate_result", {}).get("blockers") or []) + [str(decision.get("reason") or "")]
    assert any("NOT_EXECUTION_ELIGIBLE" in str(item) for item in blockers)


def test_chinext_paper_recorder_rejects():
    snapshot = validate_and_build_canonical_snapshot(_row("300001", 0))
    with pytest.raises(ValueError, match="EXECUTION_BOARD_VIOLATION"):
        validate_paper_observation(
            {
                "paper_observation": {
                    "status": "PAPER_OBSERVATION",
                    "paper_signal_id": "paper-chinext",
                    "decision_id": "decision-chinext",
                    "alpha_name": "price_strength",
                    "live_order": False,
                    "paper_only": True,
                    "paper_observation_state": "OBSERVED",
                    "paper_position_state": "PAPER_FLAT",
                },
                "canonical_snapshot": snapshot,
            },
            {
                "paper_only": True,
                "no_trade": True,
                "production_ready": False,
                "auto_order": False,
                "broker_connected": False,
            },
        )


def test_paper_signals_api_does_not_hide_db_truth():
    from inspect import getsource
    from xiaogu_api import _paper_observation_records, paper_signals
    source = getsource(_paper_observation_records) + getsource(paper_signals)
    assert "MAIN_BOARD" not in source
    assert "board !=" not in source
    assert "execution_eligible" not in source


def test_parallel_decision_keeps_input_order_and_isolates_rows(monkeypatch):
    import xiaogu_forward_runner as runner

    seen = []

    def fake_run(snapshot, **kwargs):
        payload = dict(snapshot)
        payload["_mutated"] = True
        seen.append(payload["symbol"])
        if payload["symbol"] == "000002":
            raise RuntimeError("boom")
        return {
            "symbol": payload["symbol"],
            "snapshot_id": payload.get("snapshot_id"),
            "state": "WATCH",
            "reason": "HARD_CONSTRAINT:TEST",
            "paper_observation": None,
            "failed_gates": ["DATA_VALID"],
            "production_blockers": [],
            "decision_id": f"id-{payload['symbol']}",
        }

    monkeypatch.setattr(runner, "run_production_decision", fake_run)
    rows = [
        _row("600001", 1, snapshot_id="s1"),
        _row("000002", 0, snapshot_id="s2"),
        _row("601398", 1, snapshot_id="s3"),
    ]
    originals = [dict(row) for row in rows]
    decisions, accounting = evaluate_candidate_rows(
        rows,
        portfolio_state="WATCH",
        mode="DRY_RUN",
        trade_date="2026-08-26",
        workers=3,
    )
    assert [item["symbol"] for item in decisions] == ["600001", "000002", "601398"]
    assert decisions[1]["reason"].startswith("WORKER_ERROR")
    assert accounting["error_count"] == 1
    assert accounting["success_count"] == 2
    assert accounting["selection_status"] == "ABSTAIN"
    assert accounting["publishable"] is False
    assert accounting["top1"] is None
    assert accounting["top3"] == []
    assert all(item.get("paper_observation") is None for item in decisions)
    assert rows[0] == originals[0]
    assert "_mutated" not in rows[0]


def test_parallel_matches_serial_decision_identity():
    rows = [
        validate_and_build_canonical_snapshot(_row("600000", 1)),
        validate_and_build_canonical_snapshot(_row("000001", 0)),
        validate_and_build_canonical_snapshot(_row("601398", 1)),
    ]
    serial, _serial_acc = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    parallel, _parallel_acc = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=3,
    )
    assert [item["decision_id"] for item in serial] == [item["decision_id"] for item in parallel]
    assert [item["state"] for item in serial] == [item["state"] for item in parallel]
    assert [item["symbol"] for item in serial] == [item["symbol"] for item in parallel]
