"""Single-system convergence contracts. One production path, one target, one alpha."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xiaogu_core_alpha import MODEL_ID, TARGET_VERSION, build_core_alpha
from xiaogu_forward_eligibility import BOARD_MAIN, classify_execution_board, eligibility_blockers
from xiaogu_forward_features import build_feature_vector
from xiaogu_forward_paper_recorder_v0_1 import _memory_identity, _memory_note_path
from xiaogu_forward_result_filler_v0_1 import calculate_horizon_outcomes
from xiaogu_forward_runner import (
    SYSTEM_FAULT_REASONS,
    WORKER_RETRY_LIMIT,
    evaluate_candidate_rows,
)
from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot
from xiaogu_horizon_evaluation import (
    OOS_EMBARGO_TRADING_DAYS,
    TARGET_VERSION as HORIZON_TARGET,
    _daily_grouped_hit_rates,
    _split_rows,
    evaluate_price_gate_ablation,
    passes_price_gate,
)
from xiaogu_portfolio_decision import (
    attach_top_paper_observations,
    evaluate_candidate_bundle,
)
from xiaogu_research_context import build_integrated_research_context
from xiaogu_utils import PRODUCTION_TARGET, PRODUCTION_RETURN_FIELD


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")


def _snapshot(symbol="600001", pct=3.0, **extra):
    payload = {
        "symbol": symbol,
        "f12": symbol,
        "f13": 1 if str(symbol).startswith(("6", "9")) else 0,
        "f1": 2,
        "price": 10,
        "open": 9.9,
        "high": 10.3,
        "low": 9.7,
        "amount": 1_000,
        "volume": 100,
        "pct_chg": pct,
        "buyable": True,
        "liquidity_score": 1,
        "execution_quality": 1,
        "gap_risk": 0,
        "slippage": 0,
        "spread": 0,
        "market_impact": 0,
        "trade_date": "2026-08-26",
        "source_time": "2026-08-26T14:50:00+08:00",
        "market": "SH" if str(symbol).startswith(("6", "9")) else "SZ",
    }
    payload.update(extra)
    return payload


def _bars(count=5):
    return [
        {"date": f"2026-09-{day:02d}", "open": 10, "high": 10.5, "low": 9, "close": 10.2, "volume": 100}
        for day in range(1, count + 1)
    ]


def test_single_production_target_and_alpha_owners():
    assert PRODUCTION_TARGET == "opportunity_5d"
    assert PRODUCTION_RETURN_FIELD == "opportunity_5d"
    assert TARGET_VERSION == "opportunity_5d"
    assert HORIZON_TARGET == "opportunity_5d"
    assert MODEL_ID == "profit_window_alpha_5d_v4"
    decision = evaluate_candidate_bundle(_snapshot(), position_state="FLAT", as_of=AS_OF)
    alpha = decision["core_alpha"]
    assert alpha["model_id"] == "profit_window_alpha_5d_v4"
    assert alpha["target"] == "opportunity_5d"
    assert alpha["target_version"] == "opportunity_5d"
    assert alpha.get("repricing_evidence_score_role") == "DIAGNOSTIC_ONLY"
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert decision["state"] != "BUY"


def test_selection_uses_unique_alpha_not_repricing_score():
    from xiaogu_portfolio_decision import _signal_sort_key

    low = evaluate_candidate_bundle(_snapshot("600001", 1.0), position_state="FLAT", as_of=AS_OF)
    high = evaluate_candidate_bundle(_snapshot("600002", 5.0), position_state="FLAT", as_of=AS_OF)
    low["core_alpha"]["repricing_evidence_score"] = 0.99
    high["core_alpha"]["repricing_evidence_score"] = 0.01
    ranked = sorted([low, high], key=_signal_sort_key)
    assert ranked[0]["symbol"] == "600002"
    assert ranked[0]["core_alpha"]["selection_score"] >= ranked[1]["core_alpha"]["selection_score"]
    source = Path("xiaogu_portfolio_decision.py").read_text(encoding="utf-8")
    assert "repricing_evidence_score" not in source.split("def _signal_sort_key")[1].split("def attach_top_paper_observations")[0]


def test_top3_top1_are_deterministic_and_owned_by_attach_top():
    rows = [
        validate_and_build_canonical_snapshot(_snapshot(f"60000{index}", pct))
        for index, pct in enumerate((1.0, 5.0, 3.0, 4.0, 2.0, 0.1), start=1)
    ]
    clock = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)
    first, _ = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26",
        workers=1, decision_clock=clock,
    )
    second, _ = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26",
        workers=3, decision_clock=clock,
    )
    def _selected(items):
        papers = [item["paper_observation"] for item in items if item.get("paper_observation")]
        return [(paper["symbol"], paper["rank"], paper["top1_flag"]) for paper in sorted(papers, key=lambda item: item["rank"])]
    assert _selected(first) == _selected(second)
    papers = [item["paper_observation"] for item in first if item.get("paper_observation")]
    assert len(papers) == 3
    assert sum(1 for paper in papers if paper["top1_flag"]) == 1
    top1 = next(paper for paper in papers if paper["top1_flag"])
    assert top1["selection_reason"] == "TOP1_OPPORTUNITY_5D"
    assert top1["symbol"] == "600002"
    assert attach_top_paper_observations.__name__ == "attach_top_paper_observations"


def test_batch_decision_clock_is_shared():
    clock = datetime(2026, 8, 26, 7, 5, tzinfo=timezone.utc)
    rows = [
        validate_and_build_canonical_snapshot(_snapshot("600001", 3.0)),
        validate_and_build_canonical_snapshot(_snapshot("600002", 4.0)),
    ]
    decisions, _ = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26",
        workers=2, decision_clock=clock,
    )
    stamps = {item["decision_clock"] for item in decisions}
    assert stamps == {clock.isoformat()}


def test_eligibility_does_not_invent_production_now():
    source = Path("xiaogu_forward_eligibility.py").read_text(encoding="utf-8")
    assert "production_now" not in source
    snapshot = validate_and_build_canonical_snapshot(_snapshot())
    assert "STALE_DATA" not in eligibility_blockers(snapshot, as_of=None)


def test_research_provider_semantics_and_non_blocking(monkeypatch):
    import xiaogu_research_context as research

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(research, "fetch_historical_research_cases", boom)
    monkeypatch.setattr(research, "fetch_memory_research_notes", boom)
    snapshot = validate_and_build_canonical_snapshot(_snapshot())
    features = build_feature_vector(snapshot)
    context = build_integrated_research_context(snapshot, features)
    providers = {item["provider"]: item for item in context["research_provenance"]}
    required = {
        "provider_requested", "provider_available", "provider_succeeded",
        "provider_failed", "evidence_count", "pit_valid", "used_downstream",
    }
    for item in providers.values():
        assert required.issubset(item)
    assert providers["postgresql.paper_observations"]["provider_failed"] is True
    assert providers["obsidian_memory_adapter"]["provider_failed"] is True
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    assert decision["paper_observation"] is not None or decision["core_alpha"]["signal_qualified"] is False
    assert decision["state"] != "BUY"
    assert "research_consumed" not in decision["core_alpha"]


def test_worker_retry_recovers_single_ticket(monkeypatch):
    import xiaogu_forward_runner as runner

    attempts = {"count": 0}

    def flaky(snapshot, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)

    monkeypatch.setattr(runner, "run_production_decision", flaky)
    rows = [validate_and_build_canonical_snapshot(_snapshot())]
    decisions, accounting = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    assert accounting["error_count"] == 0
    assert accounting["recovered_count"] == 1
    assert decisions[0].get("worker_attempts") == 2
    assert WORKER_RETRY_LIMIT >= 1
    assert accounting["selection_status"] != "ABSTAIN"
    assert accounting["publishable"] is True


def test_system_fault_fail_closed_abstains(monkeypatch):
    import xiaogu_forward_runner as runner

    def boom(snapshot, **kwargs):
        raise RuntimeError("SNAPSHOT_PERSISTENCE_FAILED")

    monkeypatch.setattr(runner, "run_production_decision", boom)
    rows = [
        validate_and_build_canonical_snapshot(_snapshot("600001", 3.0)),
        validate_and_build_canonical_snapshot(_snapshot("600002", 4.0)),
    ]
    decisions, accounting = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    assert accounting["selection_status"] == "ABSTAIN"
    assert accounting["top1"] is None
    assert accounting["top3"] == []
    assert accounting["publishable"] is False
    assert all(item.get("paper_observation") is None for item in decisions)
    assert "SNAPSHOT_PERSISTENCE_FAILED" in SYSTEM_FAULT_REASONS
    assert "PARTIAL_OBSERVATION" not in Path("xiaogu_forward_runner.py").read_text(encoding="utf-8")


def test_main_board_only_paper_and_chi_next_excluded():
    main = evaluate_candidate_bundle(_snapshot("600001", 3.0), position_state="FLAT", as_of=AS_OF)
    chi_next = evaluate_candidate_bundle(
        _snapshot("300001", 3.0, f13=0, market="SZ"),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert classify_execution_board(_snapshot("600001"))["board"] == BOARD_MAIN
    assert main["execution_eligible"] is True
    assert main["paper_observation"] is not None
    assert chi_next["execution_eligible"] is False
    assert chi_next["paper_observation"] is None


def test_paper_identity_separated_from_decision():
    decision = evaluate_candidate_bundle(_snapshot(), position_state="FLAT", as_of=AS_OF)
    paper = decision["paper_observation"]
    assert paper["paper_signal_id"] != paper["decision_id"]
    assert paper["paper_only"] is True
    assert paper["live_order"] is False
    assert paper["production_buy"] == "BLOCKED"
    assert paper["production_alpha"] == "profit_window_alpha_5d_v4"
    assert paper["production_target"] == "opportunity_5d"


def test_memory_identity_is_not_date_symbol_only():
    record = {
        "paper_signal_id": "ps-1",
        "decision_id": "d-1",
        "production_run_id": "run-1",
        "date": "2026-08-26",
        "symbol": "600001",
        "decision": "READY",
    }
    path = _memory_note_path("READY", record)
    assert "ps-1" in path
    assert path.endswith("ps-1.md")
    other = dict(record, paper_signal_id="ps-2", decision_id="d-2")
    assert _memory_note_path("READY", other) != path
    assert _memory_identity(record) == "ps-1"


def test_horizon_outcomes_persist_each_day_as_fact():
    complete = calculate_horizon_outcomes(10, _bars())
    assert complete["opportunity_5d"] is True
    assert all(complete["days"][str(day)]["status"] == "SETTLED" for day in range(1, 6))
    partial = calculate_horizon_outcomes(10, _bars(3))
    assert partial["days"]["1"]["status"] == "SETTLED"
    assert partial["days"]["5"]["status"] == "MISSING"
    assert partial["opportunity_5d"] is None


def test_price_gate_ablation_is_research_only():
    rows = [
        {"trade_date": "2026-08-01", "pct_chg": 3.0, "opportunity_5d": True, "price_strength": 0.53},
        {"trade_date": "2026-08-01", "pct_chg": 12.0, "opportunity_5d": False, "price_strength": 1.0},
        {"trade_date": "2026-08-02", "pct_chg": 0.2, "opportunity_5d": True, "price_strength": 0.35},
    ]
    report = evaluate_price_gate_ablation(rows)
    assert report["status"] == "RESEARCH_ONLY"
    assert report["production_frozen"] is False
    assert "WITH_GATE" in report and "WITHOUT_GATE" in report
    assert report["WITH_GATE"]["samples"] == 1
    assert report["WITHOUT_GATE"]["samples"] == 3
    assert passes_price_gate(rows[0]) is True
    assert passes_price_gate(rows[1]) is False


def test_walk_forward_oos_uses_embargo_and_daily_groups():
    assert OOS_EMBARGO_TRADING_DAYS >= 5
    rows = []
    for day in range(1, 21):
        rows.append({
            "trade_date": f"2026-08-{day:02d}",
            "symbol": "600001",
            "opportunity_5d": day % 2 == 0,
            "selection_score": 0.4 + day / 100,
            "price_strength": 0.5,
            "max_daily_bar_profit_opportunity_5d": 0.03,
            "target_quality": "CANONICAL",
        })
        rows.append({
            "trade_date": f"2026-08-{day:02d}",
            "symbol": "600002",
            "opportunity_5d": day % 3 == 0,
            "selection_score": 0.3,
            "price_strength": 0.4,
            "max_daily_bar_profit_opportunity_5d": 0.01,
            "target_quality": "CANONICAL",
        })
    train, validation, oos = _split_rows(rows)
    assert train and validation and oos
    train_dates = {row["trade_date"] for row in train}
    val_dates = {row["trade_date"] for row in validation}
    oos_dates = {row["trade_date"] for row in oos}
    assert not (train_dates & val_dates)
    assert not (val_dates & oos_dates)
    assert max(train_dates) < min(val_dates)
    assert max(val_dates) < min(oos_dates)
    daily = _daily_grouped_hit_rates(oos)
    assert "top1_hit_rate" in daily
    assert "top3_hit_rate" in daily
    assert "opportunity_rate" in daily
    assert "coverage" in daily


def test_no_second_selector_or_partial_observation_path():
    root = Path(".")
    forbidden = (
        "PARTIAL_OBSERVATION",
        "research_consumed",
    )
    for path in (
        "xiaogu_forward_runner.py",
        "xiaogu_portfolio_decision.py",
        "xiaogu_core_alpha.py",
        "xiaogu_research_context.py",
        "xiaogu_horizon_evaluation.py",
    ):
        text = (root / path).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} still in {path}"
    assert not Path("xiaogu_forward_ranking.py").exists()
    assert not Path("xiaogu_scanner_scoring.py").exists()


def test_production_permission_stays_paper_blocked():
    decision = evaluate_candidate_bundle(_snapshot(), position_state="FLAT", as_of=AS_OF)
    assert decision["buy_status"] == "BUY_BLOCKED"
    paper = decision["paper_observation"]
    assert paper["live_order"] is False
    assert paper["paper_only"] is True
    freeze = Path("rule_freeze_v0_1.json").read_text(encoding="utf-8")
    assert '"auto_order": false' in freeze or '"auto_order":false' in freeze
    assert '"paper_only": true' in freeze or '"paper_only":true' in freeze
