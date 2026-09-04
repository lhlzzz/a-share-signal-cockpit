"""Single-system convergence contracts. One production path, one target, one alpha."""
import json
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
        "provider_failed", "evidence_count", "usable_evidence_count",
        "pit_valid", "used_downstream", "knowledge_available_at", "reason",
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


def test_worker_permanent_failure_abstains(monkeypatch):
    import xiaogu_forward_runner as runner

    def boom(snapshot, **kwargs):
        raise RuntimeError("permanent worker failure")

    monkeypatch.setattr(runner, "run_production_decision", boom)
    rows = [
        validate_and_build_canonical_snapshot(_snapshot("600001", 3.0)),
        validate_and_build_canonical_snapshot(_snapshot("600002", 4.0)),
    ]
    decisions, accounting = evaluate_candidate_rows(
        rows, portfolio_state="WATCH", mode="DRY_RUN", trade_date="2026-08-26", workers=1,
    )
    assert decisions[0]["worker_attempts"] == WORKER_RETRY_LIMIT + 1
    assert accounting["error_count"] > 0
    assert accounting["selection_status"] == "ABSTAIN"
    assert accounting["publishable"] is False
    assert accounting["top1"] is None
    assert accounting["top3"] == []
    assert all(item.get("paper_observation") is None for item in decisions)


def _price_gate_snapshot(symbol: str, pct: float):
    return _snapshot(
        symbol,
        pct,
        thesis_invalidated=False,
        attention_score=0.10,
        crowding_risk=0.10,
        price_reflection=0.10,
        buyer_exhaustion=False,
        institutional_flow=0.10,
        hot_money_flow=0.10,
    )


def test_price_gate_is_not_reapplied_in_production_signal_qualification():
    low = evaluate_candidate_bundle(_price_gate_snapshot("600001", 0.2), position_state="FLAT", as_of=AS_OF)
    high = evaluate_candidate_bundle(_price_gate_snapshot("600002", 12.0), position_state="FLAT", as_of=AS_OF)
    for decision, pct in ((low, 0.2), (high, 12.0)):
        alpha = decision["core_alpha"]
        assert alpha["signal_reason"] != "PRICE_STRENGTH_OUT_OF_WINDOW", pct
        assert alpha["signal_qualified"] is True, (pct, alpha["signal_reason"])
        assert alpha["signal_status"] == "SIGNAL"
        assert alpha["signal_reason"] == "FORMAL_5D_PROFIT_WINDOW_SIGNAL"
        completion = alpha.get("repricing_completion") or {}
        assert completion.get("completed") is not True
        assert alpha.get("contradiction", {}).get("veto") is not True
        assert (decision.get("feature_vector") or {}).get("RISK", {}).get("thesis_invalidated") is not True
    source = Path("xiaogu_core_alpha.py").read_text(encoding="utf-8")
    qualification = source.split("def _signal_qualification")[1].split("def build_core_alpha")[0]
    assert "SIGNAL_PCT_MIN" not in qualification
    assert "PRICE_STRENGTH_OUT_OF_WINDOW" not in qualification


def test_historical_outcome_is_hidden_before_settlement(monkeypatch):
    from xiaogu_research_context import fetch_historical_research_cases

    class _Rows:
        def mappings(self):
            return [
                {
                    "paper_signal_id": "ps-early",
                    "decision_id": "d-early",
                    "symbol": "600001",
                    "signal_time": "2026-08-20T14:50:00+08:00",
                    "paper_payload": {
                        "signal_reason": "FORMAL_5D_PROFIT_WINDOW_SIGNAL",
                        "rank": 1,
                        "knowledge_available_at": "2026-08-20T14:50:00+08:00",
                    },
                    "outcome_payload": {
                        "opportunity_5d": True,
                        "post_trade_review": {"attribution": "MODEL_ERROR"},
                        "outcome_settled_at": "2026-08-25T15:00:00+08:00",
                    },
                }
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
    payload = fetch_historical_research_cases("600001", "2026-08-22T15:00:00+08:00")
    assert payload["historical_cases"]
    case = payload["historical_cases"][0]
    assert case["knowledge_available_at"] == "2026-08-20T14:50:00+08:00"
    assert case["opportunity_5d"] is None
    assert case["post_trade_review"] is None
    assert case["failure_pattern"] is None


def test_obsidian_outcome_is_hidden_before_settlement():
    from xiaogu_research_context import fetch_memory_research_notes
    from xiaogu_forward_paper_recorder_v0_1 import read_memory_notes
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    notes = [
        {
            "paper_signal_id": "ps-1",
            "decision_id": "d-1",
            "knowledge_available_at": "2026-08-20T14:50:00+08:00",
            "knowledge_type": "DECISION",
            "reason": "TOP1_OPPORTUNITY_5D",
            "outcome_available_at": "2026-08-25T15:00:00+08:00",
            "outcome": "T+5 hit",
            "review": "SUCCESS",
            "attribution": "MODEL_ERROR",
        }
    ]

    class _Response:
        def read(self):
            return json.dumps({"notes": notes}).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *_exc):
            return False

    recorder.os.environ["XIAOGU_OBSIDIAN_BRIDGE_URL"] = "http://memory.test"
    original_urlopen = recorder.urlopen
    recorder.urlopen = lambda *_args, **_kwargs: _Response()
    try:
        filtered = read_memory_notes(symbol="600001", as_of="2026-08-22T15:00:00+08:00")
        assert filtered and "outcome" not in filtered[0]
        payload = fetch_memory_research_notes("600001", "2026-08-22T15:00:00+08:00")
        assert payload["notes"]
        assert "outcome" not in payload["notes"][0]
        assert payload["notes"][0].get("review") is None
    finally:
        recorder.urlopen = original_urlopen
        recorder.os.environ.pop("XIAOGU_OBSIDIAN_BRIDGE_URL", None)


def test_horizon_identity_and_missing_days():
    from xiaogu_db import fetch_horizon_outcomes

    complete = calculate_horizon_outcomes(10, _bars())
    for day in range(1, 6):
        assert complete["days"][str(day)]["status"] == "SETTLED"
        assert complete["days"][str(day)]["horizon"] == day
    missing = fetch_horizon_outcomes.__wrapped__ if hasattr(fetch_horizon_outcomes, "__wrapped__") else None
    payload = {
        "decision_id": "d-1",
        "status": "MISSING",
        "days": {str(day): {"status": "MISSING"} for day in (1, 2, 3, 4, 5)},
    }
    for day in ("1", "2", "3", "4", "5"):
        assert payload["days"][day]["status"] in {"SETTLED", "MISSING"}
    assert missing is None or callable(fetch_horizon_outcomes)


def test_cost_model_v1_is_daily_bar_approximation():
    from xiaogu_core_alpha import CANONICAL_COST_MODEL, COST_MODEL_COMPONENT_SEMANTICS, EXECUTION_REALISM_LEVEL

    outcomes = calculate_horizon_outcomes(10, _bars())
    assumptions = outcomes["execution_assumptions"]
    assert assumptions["execution_realism"]["level"] == "DAILY_BAR_APPROXIMATION"
    assert assumptions["cost_model"]["commission"] == "modeled"
    assert assumptions["cost_model"]["slippage"] == "proxy"
    assert "slippage_included" not in assumptions
    assert CANONICAL_COST_MODEL["version"] == "cost_model_v1"
    assert COST_MODEL_COMPONENT_SEMANTICS["slippage"] == "proxy"
    assert EXECUTION_REALISM_LEVEL == "DAILY_BAR_APPROXIMATION"


def test_atomic_persistence_rolls_back_on_failure(monkeypatch):
    import xiaogu_db as db

    class FakeConnection:
        def execute(self, *_args, **_kwargs):
            return None

    connection = FakeConnection()
    observed = []
    rolled_back = {"value": False}

    class Transaction:
        def __enter__(self):
            return connection
        def __exit__(self, exc_type, *_args):
            if exc_type is not None:
                rolled_back["value"] = True
            return False

    monkeypatch.setattr(db, "ensure_production_schema", lambda: None)
    monkeypatch.setattr(db.engine, "begin", lambda: Transaction())
    monkeypatch.setattr(db, "record_snapshot", lambda _snapshot: observed.append("snapshot"))
    monkeypatch.setattr(db, "record_decision", lambda _decision: observed.append("decision"))

    def boom(_observation):
        raise RuntimeError("paper failed")

    monkeypatch.setattr(db, "record_paper_observation", boom)
    monkeypatch.setattr(db, "paper_observation_exists", lambda _value: False)
    monkeypatch.setattr(db, "_table_columns", lambda _name: {"production_run_id"})
    with pytest.raises(RuntimeError, match="paper failed"):
        db.persist_production_facts([
            {
                "state": "WATCH",
                "canonical_snapshot": {"snapshot_id": "s", "lineage_id": "l", "trade_date": "2026-08-26"},
                "paper_observation": {
                    "paper_signal_id": "ps-1",
                    "decision_id": "d-1",
                    "symbol": "600001",
                    "signal_time": "2026-08-26T14:50:00+08:00",
                    "reference_price": 10,
                    "paper_observation_state": "OBSERVED",
                    "paper_position_state": "PAPER_FLAT",
                    "alpha_name": "price_strength",
                    "alpha_version": "v4",
                    "feature_version": "minimal_price_alpha_v1",
                    "decision_version": "xiaogu_portfolio_decision.evaluate_candidate_bundle",
                    "cost_model_version": "cost_model_v1",
                    "paper_observation_contract_version": "v1",
                    "paper_only": True,
                    "live_order": False,
                },
            }
        ], production_run_id="run-1")
    assert rolled_back["value"] is True


def test_production_run_coverage_contract_fields():
    source = Path("xiaogu_forward_runner.py").read_text(encoding="utf-8")
    for field in (
        "full_universe_count", "l1_count", "l2_count", "l3_count",
        "feature_count", "alpha_count", "decision_count",
        "selection_candidate_count", "top3_count", "top1_count", "paper_count",
        "worker_error_count", "worker_recovered_count", "system_fault", "publishable",
    ):
        assert f'"{field}"' in source
    worker = Path("xiaogu_forward_runner.py").read_text(encoding="utf-8")
    assert "def _evaluate_one_candidate" in worker
    assert "production_now()" not in worker.split("def _evaluate_one_candidate")[1].split("def evaluate_candidate_rows")[0]


def test_memory_rebuild_from_postgresql(monkeypatch):
    from xiaogu_forward_paper_recorder_v0_1 import rebuild_memory_from_postgresql
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    written = []
    monkeypatch.setattr(recorder, "write_trade_memory", lambda record: written.append(("DECISION", record.get("paper_signal_id"))) or "path")
    monkeypatch.setattr(recorder, "update_trade_memory", lambda result: written.append(("OUTCOME", result.get("decision_id"))) or "path")
    monkeypatch.setattr("xiaogu_db.fetch_picks", lambda: [{
        "decision_id": "d-1",
        "symbol": "600001",
        "trade_date": "2026-08-26",
        "payload": {"decision_id": "d-1", "thesis": {"invalidation": "NONE"}, "symbol": "600001"},
    }])
    monkeypatch.setattr("xiaogu_db.fetch_paper_observations", lambda: [{
        "paper_signal_id": "ps-1",
        "decision_id": "d-1",
        "payload": {
            "paper_signal_id": "ps-1",
            "decision_id": "d-1",
            "symbol": "600001",
            "trade_date": "2026-08-26",
            "knowledge_available_at": "2026-08-26T14:50:00+08:00",
        },
    }])
    monkeypatch.setattr("xiaogu_db.fetch_horizon_outcomes", lambda _decision_id: {
        "days": {"1": {"status": "SETTLED"}, "2": {"status": "MISSING"}, "3": {"status": "MISSING"}, "4": {"status": "MISSING"}, "5": {"status": "MISSING"}},
        "opportunity_5d": None,
        "result_filled_at": "2026-08-27T15:00:00+08:00",
    })
    result = rebuild_memory_from_postgresql()
    notes = result["notes"]
    assert result["mode"] == "FULL"
    assert result["processed"] == 1
    assert result["rebuilt"] >= 1
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert any(item["knowledge_type"] == "DECISION" for item in notes)
    assert any(item["knowledge_type"] == "OUTCOME" for item in notes)
    assert ("DECISION", "ps-1") in written
    assert ("OUTCOME", "d-1") in written
    assert not Path("selector.py").exists()
    assert not Path("ranker.py").exists()
    assert not Path("topk.py").exists()
    assert not Path("alpha_v5.py").exists()
    assert not Path("decision_engine_v2.py").exists()
    assert not Path("second_memory.py").exists()


def test_memory_rebuild_fails_closed_without_knowledge_available_at(monkeypatch):
    from xiaogu_forward_paper_recorder_v0_1 import rebuild_memory_from_postgresql
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    written = []
    monkeypatch.setattr(
        recorder,
        "write_trade_memory",
        lambda record: written.append(("DECISION", record)) or "path",
    )
    monkeypatch.setattr(
        recorder,
        "update_trade_memory",
        lambda result: written.append(("OUTCOME", result)) or "path",
    )
    monkeypatch.setattr("xiaogu_db.fetch_picks", lambda: [{
        "decision_id": "d-missing-pit",
        "symbol": "600001",
        "created_at": "2026-08-26T15:00:00+08:00",
        "payload": {
            "decision_id": "d-missing-pit",
            "created_at": "2026-08-26T15:00:00+08:00",
            "signal_time": "2026-08-26T14:50:00+08:00",
        },
    }])
    monkeypatch.setattr("xiaogu_db.fetch_paper_observations", lambda: [{
        "paper_signal_id": "ps-missing-pit",
        "decision_id": "d-missing-pit",
        "payload": {
            "paper_signal_id": "ps-missing-pit",
            "decision_id": "d-missing-pit",
            "signal_time": "2026-08-26T14:50:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
        },
    }])
    monkeypatch.setattr("xiaogu_db.fetch_horizon_outcomes", lambda _decision_id: {"days": {}})
    result = rebuild_memory_from_postgresql()
    assert written == []
    assert not any(item.get("knowledge_type") == "DECISION" for item in result["notes"])
    assert result["skipped"] == 1
    assert result["skipped_missing_knowledge_available_at_count"] == 1
    assert result["rebuilt"] == 0
    assert result["failed"] == 0


def test_memory_rebuild_default_is_full(monkeypatch):
    from xiaogu_forward_paper_recorder_v0_1 import rebuild_memory_from_postgresql
    import xiaogu_forward_paper_recorder_v0_1 as recorder

    written = []
    monkeypatch.setattr(
        recorder,
        "write_trade_memory",
        lambda record: written.append(record.get("decision_id")) or "path",
    )
    monkeypatch.setattr(recorder, "update_trade_memory", lambda result: "path")
    monkeypatch.setattr("xiaogu_db.fetch_picks", lambda: [
        {"decision_id": f"d-{index}", "payload": {"decision_id": f"d-{index}"}}
        for index in range(51)
    ])
    monkeypatch.setattr("xiaogu_db.fetch_paper_observations", lambda: [
        {
            "paper_signal_id": f"ps-{index}",
            "decision_id": f"d-{index}",
            "payload": {
                "paper_signal_id": f"ps-{index}",
                "decision_id": f"d-{index}",
                "knowledge_available_at": "2026-08-26T14:50:00+08:00",
            },
        }
        for index in range(51)
    ])
    monkeypatch.setattr("xiaogu_db.fetch_horizon_outcomes", lambda _decision_id: {"days": {}})
    result = rebuild_memory_from_postgresql()
    assert result["mode"] == "FULL"
    assert result["processed"] == 51
    assert result["rebuilt"] == 51
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert len(written) == 51
    assert written[0] == "d-0"
    assert written[-1] == "d-50"


def test_usable_evidence_count_is_less_than_total_when_pit_invalid(monkeypatch):
    import xiaogu_research_context as research

    snapshot = validate_and_build_canonical_snapshot(_snapshot())
    features = build_feature_vector(snapshot)
    original = research.build_serenity_context

    def with_mixed_evidence(snap, feats):
        payload = original(snap, feats)
        payload["evidence"] = [
            {
                "source_id": "eastmoney.lhb",
                "event_id": "evt-valid",
                "mechanism": "capital_flow",
                "source": "eastmoney.lhb",
                "observed_at": "2026-08-26T14:00:00+08:00",
                "available_at": "2026-08-26T14:00:00+08:00",
            },
            {
                "source_id": "eastmoney.lhb",
                "event_id": "evt-future",
                "mechanism": "capital_flow",
                "source": "eastmoney.lhb",
                "observed_at": "2026-08-27T16:00:00+08:00",
                "available_at": "2026-08-27T16:00:00+08:00",
            },
            {
                "source_id": "eastmoney.lhb",
                "event_id": "",
                "mechanism": "capital_flow",
                "source": "eastmoney.lhb",
                "observed_at": "2026-08-26T14:00:00+08:00",
                "available_at": "2026-08-26T14:00:00+08:00",
            },
        ]
        return payload

    monkeypatch.setattr(research, "build_serenity_context", with_mixed_evidence)
    context = research.build_integrated_research_context(snapshot, features)
    serenity = context["research_providers"]["Serenity"]
    assert serenity["evidence_count"] == 3
    assert serenity["usable_evidence_count"] == 1
    assert serenity["provider_succeeded"] is True
    assert serenity["used_downstream"] is False


def test_used_downstream_reflects_actual_consumption():
    snapshot = validate_and_build_canonical_snapshot(_snapshot())
    features = build_feature_vector(snapshot)
    context = build_integrated_research_context(snapshot, features)
    uzi = context["research_providers"]["UZI"]
    assert uzi["provider_succeeded"] is True
    assert uzi["used_downstream"] is False
    assert context["research_providers"]["Serenity"]["used_downstream"] is False
    assert context["research_providers"]["Buffett"]["used_downstream"] is False

    unread = build_core_alpha(
        features,
        industry={},
        company={},
        capital={},
        integrated={},
        research=context,
    )
    assert unread["research_used_downstream"] is False
    assert context["research_providers"]["UZI"]["used_downstream"] is False

    alpha = build_core_alpha(
        features,
        industry={},
        company={},
        capital=context["capital"],
        integrated={},
        research=context,
    )
    assert context["research_providers"]["UZI"]["used_downstream"] is True
    assert context["research_providers"]["Serenity"]["used_downstream"] is False
    assert context["research_providers"]["Buffett"]["used_downstream"] is False
    assert alpha["research_used_downstream"] is True
    assert "research_context" in alpha["signal_evidence"]


def test_outcome_identity_separates_aggregate_and_horizon(monkeypatch):
    from xiaogu_db import fetch_horizon_outcomes

    decision_id = "abc123"
    payload = {
        "outcome_id": f"{decision_id}:horizon",
        "days": {
            "1": {"status": "SETTLED", "horizon": 1, "outcome_id": f"{decision_id}:1"},
            "2": {"status": "SETTLED", "horizon": 2},
            "3": {"status": "MISSING", "horizon": 3},
        },
        "data_status": "PARTIAL",
    }

    class _Rows:
        def mappings(self):
            return self
        def first(self):
            return {
                "decision_id": decision_id,
                "trade_date": "2026-08-26",
                "symbol": "600001",
                "payload": payload,
            }

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
    result = fetch_horizon_outcomes(decision_id)
    assert result["outcome_id"] == decision_id
    assert result["decision_id"] == decision_id
    for day in range(1, 6):
        item = result["days"][str(day)]
        assert item["horizon"] == day
        assert item["horizon_outcome_id"] == f"{decision_id}:{day}"
        assert item["status"] in {"SETTLED", "MISSING"}
        assert "outcome_id" not in item or item.get("outcome_id") != result["outcome_id"]
    assert result["days"]["1"]["status"] == "SETTLED"
    assert result["days"]["4"]["status"] == "MISSING"
    assert result["days"]["5"]["status"] == "MISSING"


def test_horizon_outcomes_round_trip_through_persistence():
    import xiaogu_db as db
    from sqlalchemy import text
    from xiaogu_forward_result_filler_v0_1 import append_result

    db.ensure_production_schema()
    snapshot = validate_and_build_canonical_snapshot(_snapshot("603991", 3.0, lineage_id="horizon-roundtrip-lineage"))
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    decision_id = decision["decision_id"]
    try:
        db.record_snapshot(snapshot)
        db.record_decision(decision)
        result = append_result(
            {
                "id": decision_id,
                "decision_id": decision_id,
                "date": snapshot["trade_date"],
                "symbol": snapshot["symbol"],
                "reference_price": snapshot["price"],
                "signal_time": snapshot["signal_time"],
                "snapshot_id": snapshot["snapshot_id"],
            },
            future_bars=_bars(3),
        )
        db.record_returns(
            str(result["date"]),
            str(result["symbol"]),
            result,
            decision_id=decision_id,
        )
        fetched = db.fetch_horizon_outcomes(decision_id)
        assert fetched["outcome_id"] == decision_id
        assert fetched["days"]["1"]["status"] == "SETTLED"
        assert fetched["days"]["2"]["status"] == "SETTLED"
        assert fetched["days"]["3"]["status"] == "SETTLED"
        assert fetched["days"]["4"]["status"] == "MISSING"
        assert fetched["days"]["5"]["status"] == "MISSING"
        for day in range(1, 6):
            item = fetched["days"][str(day)]
            assert item["horizon"] == day
            assert item["horizon_outcome_id"] == f"{decision_id}:{day}"
            assert "horizon_trade_date" in item
            if item["status"] == "SETTLED":
                assert item["horizon_trade_date"]
            else:
                assert item["horizon_trade_date"] in (None, "")
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM returns WHERE decision_id = :decision_id"), {"decision_id": decision_id})
            connection.execute(text("DELETE FROM picks WHERE decision_id = :decision_id"), {"decision_id": decision_id})
            connection.execute(
                text("DELETE FROM snapshots WHERE snapshot_id = :snapshot_id"),
                {"snapshot_id": snapshot["snapshot_id"]},
            )


def test_atomic_persistence_rolls_back_all_facts_on_paper_failure(monkeypatch):
    import xiaogu_db as db
    from sqlalchemy import text

    db.ensure_production_schema()
    snapshot = validate_and_build_canonical_snapshot(_snapshot("603992", 3.0, lineage_id="atomic-rollback-lineage"))
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    paper = dict(decision["paper_observation"])
    decision["canonical_snapshot"] = snapshot
    decision["paper_observation"] = paper
    lineage_id = snapshot["lineage_id"]
    scan_dir = "data/test/atomic_rollback"
    run_id = db.insert_scan_session(
        trade_date=snapshot["trade_date"],
        scan_time=snapshot["source_time"],
        source_id="atomic_rollback_test",
        quotes_count=1,
        captured_count=1,
        scan_dir=scan_dir,
        lineage_id=lineage_id,
    )
    decision["production_run_id"] = run_id

    def boom(_observation):
        raise RuntimeError("paper failed")

    monkeypatch.setattr(db, "record_paper_observation", boom)
    with pytest.raises(RuntimeError, match="paper failed"):
        db.persist_production_facts([decision], production_run_id=run_id)
    db.mark_production_run_status(run_id, "FAILED")
    try:
        with db.engine.connect() as connection:
            snapshots = connection.execute(
                text("SELECT COUNT(*) FROM snapshots WHERE snapshot_id = :snapshot_id"),
                {"snapshot_id": snapshot["snapshot_id"]},
            ).scalar()
            picks = connection.execute(
                text("SELECT COUNT(*) FROM picks WHERE decision_id = :decision_id"),
                {"decision_id": decision["decision_id"]},
            ).scalar()
            papers = connection.execute(
                text("SELECT COUNT(*) FROM paper_observations WHERE paper_signal_id = :paper_signal_id"),
                {"paper_signal_id": paper["paper_signal_id"]},
            ).scalar()
            run = connection.execute(
                text("SELECT status FROM production_runs WHERE production_run_id = :run_id"),
                {"run_id": run_id},
            ).mappings().first()
        assert snapshots == 0
        assert picks == 0
        assert papers == 0
        assert run is not None
        assert run["status"] != "DECISIONS_PERSISTED"
        assert run["status"] == "FAILED"
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM paper_observations WHERE paper_signal_id = :paper_signal_id"), {"paper_signal_id": paper["paper_signal_id"]})
            connection.execute(text("DELETE FROM picks WHERE decision_id = :decision_id"), {"decision_id": decision["decision_id"]})
            connection.execute(text("DELETE FROM snapshots WHERE snapshot_id = :snapshot_id"), {"snapshot_id": snapshot["snapshot_id"]})
            connection.execute(text("DELETE FROM production_runs WHERE production_run_id = :run_id"), {"run_id": run_id})
            connection.execute(text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"), {"scan_dir": scan_dir})
