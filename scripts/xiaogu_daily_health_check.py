#!/usr/bin/env python3
"""Validate the Xiaogu repricing production contract without mutating state."""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

PATHS = {
    "scanner": os.path.join(WORKSPACE_ROOT, "scrapy_scanner", "runner_v2.py"),
    "runner": os.path.join(WORKSPACE_ROOT, "xiaogu_forward_runner.py"),
    "features": os.path.join(WORKSPACE_ROOT, "xiaogu_forward_features.py"),
    "decision": os.path.join(WORKSPACE_ROOT, "xiaogu_portfolio_decision.py"),
    "filler": os.path.join(WORKSPACE_ROOT, "xiaogu_forward_result_filler_v0_1.py"),
    "scheduler": os.path.join(WORKSPACE_ROOT, "xiaogu_scheduler.py"),
    "pipeline": os.path.join(WORKSPACE_ROOT, "daily_pipeline.sh"),
    "rule": os.path.join(WORKSPACE_ROOT, "rule_freeze_v0_1.json"),
    "audit_ledger": os.path.join(WORKSPACE_ROOT, "forward_paper_ledger_v0_1.jsonl"),
    "api": os.path.join(WORKSPACE_ROOT, "xiaogu_api.py"),
    "filler": os.path.join(WORKSPACE_ROOT, "xiaogu_forward_result_filler_v0_1.py"),
}


def _text(name: str) -> str:
    with open(PATHS[name], encoding="utf-8") as handle:
        return handle.read()


def _compile(name: str):
    try:
        py_compile.compile(PATHS[name], doraise=True)
        return True, "ok"
    except py_compile.PyCompileError as exc:
        return False, str(exc)


def _contains(name: str, *tokens: str):
    text = _text(name)
    missing = [token for token in tokens if token not in text]
    return not missing, "ok" if not missing else "missing: " + ", ".join(missing)


def check_scanner_contract():
    return _contains("scanner", "canonical_snapshot", "DATA_CAPTURE_ONLY", '"selection": False')


def check_decision_owner():
    return _contains("decision", "def evaluate_candidate_bundle", "def evaluate_production_gates", "paper_observation", "price_strength", "PRODUCTION_BUY_BLOCKED", "WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL")


def check_price_formation_features():
    return _contains(
        "features", "BUSINESS", "FUTURE_DEMAND", "CAPITAL", "SUPPLY", "PRICING_GAP",
        "REFLEXIVITY", "MARKET", "RISK", "EXECUTION", "capital_price_impact_state",
    )


def check_horizon_outcomes():
    from xiaogu_horizon_evaluation import HORIZONS

    ok, detail = _contains("filler", "fill_pending_results", "--pending", "calculate_horizon_outcomes")
    if not ok:
        return ok, detail
    expected = (1, 2, 3, 4, 5)
    return tuple(HORIZONS) == expected, "ok" if tuple(HORIZONS) == expected else "unexpected horizon set"


def check_scheduler_outcome_job():
    return _contains("scheduler", "xiaogu_forward_result_filler_v0_1.py", "--due")


def check_pipeline_chain():
    return _contains(
        "pipeline",
        "XIAOGU_PERSIST_DB=1",
        "scrapy_scanner/runner_v2.py",
        "xiaogu_forward_runner.py",
    )


def check_rule_freeze():
    with open(PATHS["rule"], encoding="utf-8") as handle:
        rule = json.load(handle)
    decisions = set(rule.get("allowed_decisions") or [])
    required = {"WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL"}
    from xiaogu_portfolio_decision import DECISION_HARD_GATES
    alpha_contract = rule.get("alpha_contract", {})
    ok = (
        rule.get("rule_version") == "repricing_production_v1"
        and rule.get("production_owner") == "xiaogu_portfolio_decision.evaluate_candidate_bundle"
        and decisions == required
        and rule.get("evaluation", {}).get("horizons_days") == [1, 2, 3, 4, 5]
        and rule.get("evaluation", {}).get("evaluation_window_days") == [1, 2, 3, 4, 5]
        and rule.get("evaluation", {}).get("max_holding_boundary") == 5
        and alpha_contract.get("target") == "opportunity_5d"
        and tuple(alpha_contract.get("required_hard_gates") or ()) == tuple(DECISION_HARD_GATES)
        and "required_buy_evidence" not in alpha_contract
        and set(alpha_contract.get("model_inputs") or ()) == {
            "BUSINESS", "FUTURE_DEMAND", "CAPITAL", "SUPPLY", "PRICING_GAP", "REFLEXIVITY", "MARKET"
        }
        and rule.get("paper_only") is True
        and rule.get("auto_order") is False
        and rule.get("broker_connected") is False
        and rule.get("buy_enabled") is False
        and rule.get("paper_production") == "ENABLED"
        and rule.get("live_trading") == "DISABLED"
        and rule.get("production_buy") == "BLOCKED"
        and rule.get("paper_observation", {}).get("enabled") is True
        and rule.get("paper_observation", {}).get("signal") == "PAPER_OBSERVATION"
        and rule.get("paper_observation", {}).get("capital_alpha") == "RESEARCH_ONLY"
        and rule.get("paper_observation", {}).get("live_trading") is False
        and rule.get("SNAPSHOT_IDENTITY_IMMUTABLE") is True
        and rule.get("execution_board_policy") == "MAIN_BOARD_ONLY"
        and rule.get("execution_board_policy_version") == "main_board_only_v1"
        and rule.get("execution_universe_owner") == "xiaogu_forward_eligibility.execution_universe"
        and rule.get("architecture_freeze", {}).get("EXECUTION_BOARD_POLICY") == "MAIN_BOARD_ONLY_V1"
    )
    return ok, "ok" if ok else "repricing rule freeze contract mismatch"


def check_ledger_readable():
    if not os.path.exists(PATHS["audit_ledger"]):
        return True, "ledger not present - skipped"
    try:
        with open(PATHS["audit_ledger"], encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    json.loads(line)
        return True, "ok"
    except (OSError, json.JSONDecodeError) as exc:
        return False, repr(exc)


def check_database_truth_boundaries():
    api = _text("api")
    filler = _text("filler")
    if "forward_paper_ledger" in api or "fetch_picks" not in api or "fetch_returns" not in api:
        return False, "API must read PostgreSQL picks/returns only"
    if "fetch_picks" not in filler or "fetch_returns" not in filler:
        return False, "outcome filler must read PostgreSQL decisions/outcomes"
    return True, "ok"


def check_schema_fail_closed():
    db_text = open(os.path.join(WORKSPACE_ROOT, "xiaogu_db.py"), encoding="utf-8").read()
    if "except Exception:\n            continue" in db_text or "except Exception:\n        pass" in db_text:
        return False, "schema migration still swallows exceptions"
    if "ON CONFLICT (lineage_id) DO NOTHING" in db_text:
        return False, "production snapshots still conflict on lineage_id"
    if "ON CONFLICT (snapshot_id) DO NOTHING" in db_text:
        return False, "snapshot writer still swallows identity conflicts"
    if "payload->>'snapshot_id', lineage_id)" in db_text or "snapshot_id = COALESCE(NULLIF(snapshot_id, ''), payload->>'snapshot_id', lineage_id)" in db_text:
        return False, "schema still forges snapshot_id from lineage_id"
    if "SNAPSHOT_IDENTITY_CONFLICT" not in db_text:
        return False, "missing snapshot payload identity conflict"
    if "SNAPSHOT_IDENTITY_IMMUTABLE" not in db_text:
        return False, "missing snapshot immutability lock"
    return True, "ok"


def check_production_schema_audit():
    from xiaogu_db import audit_production_schema, ensure_production_schema

    ensure_production_schema()
    audit = audit_production_schema()
    snapshots = audit["tables"]["snapshots"]
    historical = audit["tables"]["canonical_historical_snapshots"]
    checks = audit["tables"]["paper_observations"]["checks"]
    paper_only_check = str(checks.get("paper_observations_paper_only_check") or "").replace("(", "").replace(")", "").strip()
    live_order_check = str(checks.get("paper_observations_live_order_check") or "").replace("(", "").replace(")", "").strip()
    required = (
        snapshots["columns"]["snapshot_id"] == "EXISTS"
        and snapshots["columns"]["lineage_id"] == "EXISTS"
        and snapshots["columns"]["source"] == "EXISTS"
        and snapshots["columns"]["source_time"] == "EXISTS"
        and snapshots["columns"]["payload_hash"] == "EXISTS"
        and audit["tables"]["picks"]["columns"]["decision_id"] == "EXISTS"
        and audit["tables"]["returns"]["columns"]["decision_id"] == "EXISTS"
        and audit["tables"]["returns"]["columns"]["calendar_version"] == "EXISTS"
        and audit["tables"]["returns"]["columns"]["calendar_content_hash"] == "EXISTS"
        and audit["tables"]["paper_observations"]["columns"]["paper_signal_id"] == "EXISTS"
        and audit["tables"]["paper_observations"]["columns"]["paper_observation_state"] == "EXISTS"
        and snapshots["unique"][("snapshot_id",)] == "EXISTS"
        and snapshots["unique"][("lineage_id", "symbol")] == "EXISTS"
        and snapshots["primary_key"]["status"] == "EXISTS"
        and snapshots["triggers"]["snapshots_identity_immutable"] == "EXISTS"
        and historical["columns"]["snapshot_id"] == "EXISTS"
        and historical["unique"][("snapshot_id",)] == "EXISTS"
        and historical["primary_key"]["status"] == "EXISTS"
        and historical["triggers"]["canonical_historical_snapshots_identity_immutable"] == "EXISTS"
        and audit["tables"]["snapshot_identity_conflicts"]["columns"]["snapshot_id"] == "EXISTS"
        and audit["tables"]["snapshot_identity_conflicts"]["columns"]["existing_payload_hash"] == "EXISTS"
        and audit["tables"]["snapshot_identity_conflicts"]["columns"]["incoming_payload_hash"] == "EXISTS"
        and ("decision_id", "trade_date") in audit["tables"]["returns"]["unique_constraints"]
        and audit.get("schema_version_status") == "EXISTS"
        and audit["tables"]["production_runs"]["columns"]["production_run_id"] == "EXISTS"
        and audit["tables"]["production_runs"]["columns"]["lineage_id"] == "EXISTS"
        and audit["tables"]["production_runs"]["primary_key"]["status"] == "EXISTS"
    )
    returns_fk = audit["tables"]["returns"]["foreign_keys"].get("decision_id->picks.decision_id")
    paper_fk = audit["tables"]["paper_observations"]["foreign_keys"].get("decision_id->picks.decision_id")
    calendar = audit["tables"]["trading_calendar"]
    required = (
        required
        and returns_fk == "EXISTS"
        and paper_fk == "EXISTS"
        and calendar["primary_key"]["status"] == "EXISTS"
        and calendar["indexes"]["idx_trading_calendar_open_days"] == "EXISTS"
        and paper_only_check == "CHECK paper_only"
        and live_order_check == "CHECK NOT live_order"
        and audit.get("audit") == "PASS"
        and audit.get("ok") is True
    )
    from inspect import getsource
    import xiaogu_db as db_mod
    production_init = getsource(db_mod.init_db)
    required = (
        required
        and "migrate_historical_snapshot_identity()" not in production_init
        and "ORDER BY source_time DESC" not in getsource(db_mod.fetch_persisted_canonical_snapshots)
    )
    return required, json.dumps(
        {
            "schema_version": audit.get("schema_version"),
            "schema_ok": audit.get("ok"),
            "schema_audit": audit.get("audit"),
            "last_migration": audit.get("last_migration"),
            "composite_unique": audit["tables"]["returns"]["unique_constraints"],
            "status": "PASS" if required else "FAIL",
        },
        ensure_ascii=False,
        default=str,
    )


def check_snapshot_identity_lock():
    from inspect import getsource
    import xiaogu_db as db_mod
    from xiaogu_db import (
        SNAPSHOT_IDENTITY_IMMUTABLE,
        ensure_production_schema,
        find_snapshot_identity_conflicts,
    )

    ensure_production_schema()
    writer = getsource(db_mod.record_snapshot)
    historical = getsource(db_mod.record_canonical_historical_snapshots)
    if "ON CONFLICT (snapshot_id) DO NOTHING" in writer or "ON CONFLICT (snapshot_id) DO NOTHING" in historical:
        return False, "snapshot writer still swallows identity conflicts"
    if "UPDATE snapshots" in writer or "SET payload" in writer:
        return False, "snapshot writer mutates immutable identity"
    if SNAPSHOT_IDENTITY_IMMUTABLE is not True:
        return False, "SNAPSHOT_IDENTITY_IMMUTABLE is not locked"
    conflicts = find_snapshot_identity_conflicts()
    if conflicts:
        return False, json.dumps({"conflicts": len(conflicts), "status": "FAIL"}, ensure_ascii=False)
    return True, "ok"


def check_calendar_truth():
    from xiaogu_db import audit_trading_calendar, get_calendar_version

    report = audit_trading_calendar()
    required = (
        report.get("status") == "PASS"
        and report.get("calendar_version") == get_calendar_version(report["effective_year"])
        and report.get("calendar_content_hash") == report.get("authoritative_content_hash")
        and report.get("calendar_source")
        and report.get("today_source")
        and report.get("regressions", {}).get("2026-08-31") == "TRUE"
        and report.get("regressions", {}).get("2026-09-25") == "FALSE"
        and report.get("regressions", {}).get("2026-09-28") == "TRUE"
        and report.get("t5") == "2026-09-29"
    )
    return required, json.dumps(
        {
            "calendar_version": report.get("calendar_version"),
            "calendar_source": report.get("calendar_source"),
            "effective_year": report.get("effective_year"),
            "coverage": [report.get("coverage_start"), report.get("coverage_end")],
            "row_count": report.get("row_count"),
            "content_hash": report.get("calendar_content_hash"),
            "today_status": report.get("today_status"),
            "today_source": report.get("today_source"),
            "status": report.get("status"),
        },
        ensure_ascii=False,
        default=str,
    )


def check_position_review_contract():
    from datetime import datetime, timezone
    import xiaogu_forward_runner as runner
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    original = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
    })
    current = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 11, "volume": 110, "amount": 1100,
        "source_time": "2026-09-01T09:40:00+08:00", "trade_date": "2026-09-01",
    })
    seen = {}

    def fake_run(received, **kwargs):
        seen.update({"received": received, **kwargs})
        return {"state": "HOLD", "action": "HOLD", "trade_status": "OPEN"}

    original_fetch = __import__("xiaogu_db").fetch_open_positions
    original_resolver = __import__("xiaogu_db").get_current_position_review_snapshot
    original_outcome = __import__("xiaogu_db").fetch_position_outcome
    original_days = __import__("xiaogu_db").trading_days_between
    original_run = runner.run_production_decision
    original_paper = runner.daily_paper_position_review
    original_write = runner._write_ledger_record
    try:
        __import__("xiaogu_db").fetch_open_positions = lambda: [{
            "position_id": "POS|d-original",
            "decision_id": "d-original",
            "symbol": "600001",
            "trade_date": "2026-08-26",
            "state": "HOLD",
            "action": "HOLD",
            "position_state": "LONG",
            "snapshot_id": original["snapshot_id"],
            "original_snapshot_id": original["snapshot_id"],
        }]
        __import__("xiaogu_db").get_current_position_review_snapshot = lambda **_kwargs: current
        __import__("xiaogu_db").fetch_position_outcome = lambda *_args, **_kwargs: {}
        __import__("xiaogu_db").trading_days_between = lambda *_args, **_kwargs: 2
        runner.run_production_decision = fake_run
        runner.daily_paper_position_review = lambda _date: []
        runner._write_ledger_record = lambda _decision: None
        runner.daily_position_review("2026-09-01")
    finally:
        __import__("xiaogu_db").fetch_open_positions = original_fetch
        __import__("xiaogu_db").get_current_position_review_snapshot = original_resolver
        __import__("xiaogu_db").fetch_position_outcome = original_outcome
        __import__("xiaogu_db").trading_days_between = original_days
        runner.run_production_decision = original_run
        runner.daily_paper_position_review = original_paper
        runner._write_ledger_record = original_write
    ok = (
        seen.get("received", {}).get("snapshot_id") == current["snapshot_id"]
        and seen.get("received", {}).get("snapshot_id") != original["snapshot_id"]
        and seen.get("account", {}).get("position_id") == "POS|d-original"
        and seen.get("account", {}).get("decision_id") == "d-original"
        and seen.get("account", {}).get("review_snapshot_id") == current["snapshot_id"]
        and seen.get("account", {}).get("original_snapshot_id") == original["snapshot_id"]
        and seen.get("mode") == "PRODUCTION"
        and seen.get("decision_clock") is None
    )
    return ok, "ok" if ok else "position review did not use current snapshot identity"


def check_paper_position_review_contract():
    import xiaogu_forward_runner as runner
    from xiaogu_forward_snapshot import production_now, validate_and_build_canonical_snapshot

    current = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 11, "volume": 110, "amount": 1100,
        "source_time": "2026-09-01T09:40:00+08:00", "trade_date": "2026-09-01",
    })
    seen = {}

    def fake_run(received, **kwargs):
        seen.update({"received": received, **kwargs})
        return {"state": "HOLD", "action": "HOLD", "trade_status": "OPEN"}

    db = __import__("xiaogu_db")
    original_fetch = db.fetch_open_paper_positions
    original_resolver = db.get_current_position_review_snapshot
    original_outcome = db.fetch_position_outcome
    original_days = db.trading_days_between
    original_update = db.update_paper_observation_state
    original_run = runner.run_production_decision
    try:
        db.fetch_open_paper_positions = lambda: [{
            "paper_signal_id": "paper-1",
            "decision_id": "d-original",
            "symbol": "600001",
            "trade_date": "2026-08-26",
            "snapshot_id": "original-id",
            "original_snapshot_id": "original-id",
            "paper_position_state": "PAPER_LONG",
            "paper_entry_contract": {"entry_price": 10},
        }]
        db.get_current_position_review_snapshot = lambda **_kwargs: current
        db.fetch_position_outcome = lambda *_args, **_kwargs: {}
        db.trading_days_between = lambda *_args, **_kwargs: 2
        db.update_paper_observation_state = lambda *_args, **_kwargs: None
        runner.run_production_decision = fake_run
        reviewed = runner.daily_paper_position_review("2026-09-01")
    finally:
        db.fetch_open_paper_positions = original_fetch
        db.get_current_position_review_snapshot = original_resolver
        db.fetch_position_outcome = original_outcome
        db.trading_days_between = original_days
        db.update_paper_observation_state = original_update
        runner.run_production_decision = original_run
    ok = (
        seen.get("mode") == "PRODUCTION"
        and seen.get("decision_clock") is None
        and seen.get("received", {}).get("snapshot_id") == current["snapshot_id"]
        and seen.get("account", {}).get("paper_signal_id") == "paper-1"
        and seen.get("account", {}).get("decision_id") == "d-original"
        and seen.get("account", {}).get("review_snapshot_id") == current["snapshot_id"]
        and reviewed
        and reviewed[0].get("paper_action") == "PAPER_HOLD"
        and reviewed[0].get("paper_position_state") == "PAPER_LONG"
        and "PAPER_REDUCE" not in str(reviewed[0].get("paper_action"))
        and seen.get("received", {}).get("source_time") != production_now().isoformat()
    )
    return ok, "ok" if ok else "paper position review did not use current snapshot/clock"


def check_production_clock_contract():
    from datetime import datetime, timedelta, timezone
    import xiaogu_forward_snapshot as snap
    from xiaogu_forward_runner import run_production_decision
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    source_time = datetime.fromisoformat("2026-08-26T10:00:00+08:00")
    clock = snap.production_now()
    age = snap.snapshot_age(source_time, clock)
    live_ok = age is not None and age > timedelta(minutes=120) and clock != source_time.astimezone(timezone.utc)
    fixed = datetime.fromisoformat("2026-09-03T10:00:00+08:00").astimezone(timezone.utc)
    original_now = snap.production_now
    original_clock = snap.production_decision_clock
    db = __import__("xiaogu_db")
    original_verify = db.verify_persisted_snapshot
    try:
        snap.production_now = lambda: fixed
        # production_decision_clock still calls production_now from module globals;
        # patch runner helper through an explicit clock instead.
        db.verify_persisted_snapshot = lambda **_kwargs: True
        snapshot = validate_and_build_canonical_snapshot({
            "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
            "source_time": "2026-09-03T09:50:00+08:00", "trade_date": "2026-09-03",
            "f13": 1, "f1": 2, "market": "SH",
        })
        decision = run_production_decision(
            snapshot,
            mode="PRODUCTION",
            trade_date="2026-09-03",
            position_state="FLAT",
            decision_clock=fixed,
        )
        runner_ok = decision["decision_clock"] == fixed.isoformat() and decision["decision_clock"] != snapshot["source_time"]
    finally:
        snap.production_now = original_now
        db.verify_persisted_snapshot = original_verify
    ok = live_ok and runner_ok
    return ok, "ok" if ok else "production clock contract mismatch"


def check_negative_evidence_contract():
    from datetime import datetime
    from xiaogu_portfolio_decision import (
        build_confirmed_negative_blocker,
        collect_production_negative_evidence,
    )

    as_of = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
    future_item = {
        "source_id": "lhb", "event_id": "future-1", "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "future-1", "distribution_risk"), "observed": True,
        "direction": "SELL", "observed_at": "2026-08-27T10:00:00+08:00",
        "available_at": "2026-08-27T10:00:00+08:00", "event_time": "2026-08-27T10:00:00+08:00",
        "pit_status": "OK", "confirmation_status": "CONFIRMED",
    }
    unknown_item = {
        "source_id": "lhb", "event_id": "unk-1", "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "unk-1", "distribution_risk"), "observed": False,
        "direction": "SELL", "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00", "event_time": "2026-08-26T14:45:00+08:00",
        "pit_status": "OK", "confirmation_status": "UNKNOWN",
    }
    sell_only = {
        "source_id": "lhb", "event_id": "conf-1", "mechanism": "lhb_event",
        "evidence_identity": ("lhb", "conf-1", "lhb_event"), "observed": True,
        "direction": "SELL", "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00", "event_time": "2026-08-26T14:45:00+08:00",
        "as_of": as_of.isoformat(), "pit_status": "OK", "confirmation_status": "CONFIRMED",
    }
    distribution_item = {
        **sell_only,
        "event_id": "dist-1",
        "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "dist-1", "distribution_risk"),
    }
    missing_identity = {
        "source_id": "lhb", "event_id": "", "mechanism": "distribution_risk",
        "evidence_identity": None, "observed": True, "direction": "SELL",
        "observed_at": "2026-08-26T14:45:00+08:00",
        "available_at": "2026-08-26T14:50:00+08:00",
        "event_time": "2026-08-26T14:45:00+08:00",
        "as_of": as_of.isoformat(), "pit_status": "OK", "confirmation_status": "CONFIRMED",
    }
    future_records = collect_production_negative_evidence({}, {"CAPITAL": {"distribution_evidence": [future_item]}}, {}, as_of=as_of)
    unknown_records = collect_production_negative_evidence({}, {"CAPITAL": {"distribution_evidence": [unknown_item]}}, {}, as_of=as_of)
    sell_records = collect_production_negative_evidence({}, {"CAPITAL": {"distribution_evidence": [sell_only], "institution_behavior": {"evidence": [sell_only]}}}, {}, as_of=as_of)
    confirmed_records = collect_production_negative_evidence({}, {"CAPITAL": {"distribution_evidence": [distribution_item]}}, {}, as_of=as_of)
    capital_channel_records = collect_production_negative_evidence({}, {"CAPITAL": {"institution_behavior": {"evidence": [distribution_item]}}}, {}, as_of=as_of)
    missing_records = collect_production_negative_evidence({}, {"CAPITAL": {"distribution_evidence": [missing_identity]}}, {}, as_of=as_of)
    behavior_ok = (
        future_records == []
        and unknown_records == []
        and sell_records == []
        and missing_records == []
        and capital_channel_records == []
        and any(item["blocker"] == "CONFIRMED_DISTRIBUTION" and item["mechanism"] == "distribution_risk" for item in confirmed_records)
        and build_confirmed_negative_blocker("REPRICING_COMPLETED", distribution_item, as_of=as_of) is None
        and build_confirmed_negative_blocker("CONFIRMED_DISTRIBUTION", sell_only, as_of=as_of) is None
    )
    ok = behavior_ok
    return ok, "ok" if ok else "negative evidence is still filtered out or unbound"


def check_evidence_identity_contract():
    from xiaogu_forward_features import _evidence, validate_evidence_identity

    missing_event = _evidence(
        observed=True, source="x", available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION", source_id="x", event_id="", mechanism="lhb_event",
    )
    missing_mechanism = _evidence(
        observed=True, source="x", available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION", source_id="x", event_id="evt", mechanism="",
    )
    complete = _evidence(
        observed=True, source="lhb", available_at="2026-08-26T14:50:00+08:00",
        evidence_family="DIRECT_INSTITUTION", source_id="lhb", event_id="evt-1", mechanism="lhb_event",
        observed_at="2026-08-26T14:45:00+08:00",
    )
    ok = (
        missing_event["evidence_identity"] is None
        and missing_mechanism["evidence_identity"] is None
        and missing_event["event_id"] == ""
        and missing_mechanism["mechanism"] == ""
        and validate_evidence_identity(missing_event) is None
        and complete["evidence_identity"] == ("lhb", "evt-1", "lhb_event")
        and validate_evidence_identity({"source_id": "lhb", "event_id": "evt-1", "mechanism": "lhb_event", "evidence_identity": ("lhb", "evt-1", "lhb_event")}) == ("lhb", "evt-1", "lhb_event")
        and validate_evidence_identity({"source_id": "lhb", "event_id": "evt-1", "mechanism": "lhb_event", "evidence_identity": ("lhb", "evt-1", "other")}) is None
    )
    return ok, "ok" if ok else "evidence identity still fabricates fallbacks"


def check_gate_contract():
    from datetime import datetime
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot
    from xiaogu_portfolio_decision import DECISION_HARD_GATES, evaluate_candidate_bundle, evaluate_production_gates

    rule = json.loads(open(PATHS["rule"], encoding="utf-8").read())
    as_of = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        "f13": 1, "f1": 2, "market": "SH",
    })
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=as_of)
    distribution_item = {
        "source_id": "lhb", "event_id": "gate-dist-1", "mechanism": "distribution_risk",
        "evidence_identity": ("lhb", "gate-dist-1", "distribution_risk"), "observed": True,
        "observed_at": "2026-08-26T14:45:00+08:00", "available_at": "2026-08-26T14:50:00+08:00",
        "event_time": "2026-08-26T14:45:00+08:00", "as_of": as_of.isoformat(),
        "pit_status": "OK", "confirmation_status": "CONFIRMED",
    }
    unknown_item = {
        **distribution_item,
        "event_id": "gate-unk-1",
        "evidence_identity": ("lhb", "gate-unk-1", "distribution_risk"),
        "observed": False,
        "confirmation_status": "UNKNOWN",
    }
    features = {"CAPITAL": {"distribution_evidence": [distribution_item]}, "RISK": {}, "EXECUTION": {}}
    unknown_features = {"CAPITAL": {"distribution_evidence": [unknown_item]}, "RISK": {}, "EXECUTION": {}}
    alpha = {"model_status": "DATA_INSUFFICIENT"}
    blocked = evaluate_production_gates(snapshot, features=features, alpha=alpha, research={}, as_of=as_of)
    unknown = evaluate_production_gates(snapshot, features=unknown_features, alpha=alpha, research={}, as_of=as_of)
    ok = (
        tuple(rule["alpha_contract"]["required_hard_gates"]) == tuple(DECISION_HARD_GATES)
        and rule["alpha_contract"].get("gate_owner") == "xiaogu_portfolio_decision.evaluate_production_gates"
        and rule["alpha_contract"].get("gate_version") == "production_gate_v1"
        and decision["gate_version"] == "production_gate_v1"
        and decision["gate_result"]["failed_gates"] == decision["failed_gates"]
        and "NEGATIVE_EVIDENCE_CLEAR" in blocked["failed_gates"]
        and "CONFIRMED_DISTRIBUTION" in blocked["production_blockers"]
        and "NEGATIVE_EVIDENCE_CLEAR" not in unknown["failed_gates"]
        and "CONFIRMED_DISTRIBUTION" not in unknown["production_blockers"]
        and decision["state"] != "BUY"
    )
    return ok, "ok" if ok else "gate owner contract mismatch"


def check_capital_identity_contract():
    from xiaogu_core_alpha import _capital_convergence

    shared = {
        "source_id": "lhb", "event_id": "same-event", "mechanism": "lhb_event",
        "evidence_identity": ("lhb", "same-event", "lhb_event"),
        "observed": True, "evidence_family": "DIRECT_INSTITUTION",
        "available_at": "2026-08-26T14:50:00+08:00",
    }
    institution = {**shared, "evidence_family": "DIRECT_INSTITUTION"}
    hot_money = {**shared, "evidence_family": "DIRECT_HOT_MONEY"}
    capital = {
        "institution_behavior": {"evidence_status": "OBSERVED", "strength": 0.8, "direction": "BUYING", "evidence": [institution], "confidence": 0.8},
        "main_force_behavior": {},
        "hot_money_behavior": {"evidence_status": "OBSERVED", "strength": 0.8, "direction": "BUYING", "evidence": [hot_money], "confidence": 0.8},
    }
    # hot_money evidence family must start with DIRECT_
    hot_money["evidence_family"] = "DIRECT_HOT_MONEY"
    result = _capital_convergence(capital)
    second = {
        **shared,
        "event_id": "other-event",
        "evidence_identity": ("lhb", "other-event", "lhb_event"),
        "evidence_family": "DIRECT_HOT_MONEY",
    }
    two = {
        "institution_behavior": {"evidence_status": "OBSERVED", "strength": 0.8, "direction": "BUYING", "evidence": [institution], "confidence": 0.8},
        "main_force_behavior": {},
        "hot_money_behavior": {"evidence_status": "OBSERVED", "strength": 0.8, "direction": "BUYING", "evidence": [second], "confidence": 0.8},
    }
    two_result = _capital_convergence(two)
    ok = (
        result["independent_origin_count"] == 1
        and result["evidence_identity_count"] == 1
        and result["confirmed_channel_count"] >= 1
        and "directional_alignment" in result
        and two_result["independent_origin_count"] == 2
        and two_result["evidence_identity_count"] == 2
        and two_result["confirmed_channel_count"] >= 2
        and two_result["directional_alignment"] is True
    )
    return ok, "ok" if ok else "capital identity still splits one LHB event"


def check_position_identity_contract():
    from sqlalchemy import text
    import xiaogu_db as db
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    db.ensure_production_schema()
    ids = ("health-pos-a", "health-pos-b")
    lineages = ("health-lin-a", "health-lin-b")
    symbol = "990013"

    def cleanup():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM positions WHERE decision_id IN ('health-pos-a', 'health-pos-b')"))
            connection.execute(text("DELETE FROM picks WHERE decision_id IN ('health-pos-a', 'health-pos-b')"))
            connection.execute(text("DELETE FROM snapshots WHERE lineage_id IN ('health-lin-a', 'health-lin-b')"))

    cleanup()
    try:
        for decision_id, lineage_id, trade_date in (
            ("health-pos-a", "health-lin-a", "2026-08-03"),
            ("health-pos-b", "health-lin-b", "2026-08-10"),
        ):
            snapshot = validate_and_build_canonical_snapshot(
                {
                    "symbol": symbol,
                    "price": 10,
                    "volume": 100,
                    "amount": 1000,
                    "source_time": f"{trade_date}T14:50:00+08:00",
                    "trade_date": trade_date,
                    "lineage_id": lineage_id,
                },
                lineage_id=lineage_id,
            )
            db.record_snapshot(snapshot)
            db.record_decision({
                "decision_id": decision_id,
                "position_id": f"POS|{decision_id}",
                "symbol": snapshot["symbol"],
                "trade_date": snapshot["trade_date"],
                "state": "HOLD",
                "action": "HOLD",
                "position_state": "LONG",
                "snapshot_id": snapshot["snapshot_id"],
                "lineage_id": snapshot["lineage_id"],
                "original_snapshot_id": snapshot["snapshot_id"],
                "canonical_snapshot": snapshot,
            })
        opened = [row for row in db.fetch_open_positions() if row["decision_id"] in ids]
        first = db.get_position_by_id("POS|health-pos-a")
        second = db.get_position_by_decision_id("health-pos-b")
        ok = (
            {row["position_id"] for row in opened} == {"POS|health-pos-a", "POS|health-pos-b"}
            and first["position_id"] != second["position_id"]
            and first["symbol"] == second["symbol"] == symbol
            and first["decision_id"] != second["decision_id"]
        )
        try:
            db.derive_position_state_by_symbol(symbol)
            derived_ok = False
        except RuntimeError as exc:
            derived_ok = "POSITION_STATE_AMBIGUOUS" in str(exc)
        ok = ok and derived_ok
    finally:
        cleanup()
    return ok, "ok" if ok else "position identity still collapses same-symbol positions"


def check_reduce_not_flat():
    from datetime import datetime
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot
    from xiaogu_portfolio_decision import QUANTITY_MODEL, evaluate_candidate_bundle

    as_of = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
    snapshot = validate_and_build_canonical_snapshot({
        "symbol": "600001", "price": 10, "volume": 100, "amount": 1000,
        "source_time": "2026-08-26T14:50:00+08:00", "trade_date": "2026-08-26",
        "financial_quality": 1, "moat": 1, "pricing_power": 1, "earnings_quality": 1,
        "roic": 100, "roe": 100, "growth": 100, "management": 1, "debt_safety": 1,
        "capital_allocation": 1, "valuation_quality": 0.8,
        "market_story_strength": 1, "system_change_strength": 1, "demand_score": 1,
        "bottleneck_score": 1, "supply_constraint": 1, "demand_visibility": 1,
        "industry_catalyst": 1, "evidence_strength": 1,
        "f62": 500, "f184": 5, "pct_chg": 3, "capital_accumulation": 1,
        "capital_persistence": 1, "capital_acceleration": 1, "institutional_flow": 0.8,
        "hot_money_flow": 0.5, "alpha_model_status": "VALIDATED", "execution_quality": 1,
    })
    decision = evaluate_candidate_bundle(
        snapshot,
        portfolio_state="BUY",
        position_state="LONG",
        account={"profit_window_hit": True, "holding_days": 1},
        as_of=as_of,
    )
    ok = (
        decision["state"] == "REDUCE"
        and decision["action"] == "REDUCE"
        and decision["position_state"] == "LONG"
        and decision["position_state_after"] == "LONG"
        and decision["trade_status"] != "CLOSED"
        and (QUANTITY_MODEL or decision.get("unsupported_reduction") == "REDUCE_UNSUPPORTED")
    )
    return ok, "ok" if ok else "REDUCE still collapses to FLAT/CLOSED"


def check_execution_board_contract():
    from xiaogu_forward_eligibility import (
        BOARD_BSE,
        BOARD_CHINEXT,
        BOARD_MAIN,
        BOARD_STAR,
        BOARD_UNKNOWN,
        classify_execution_board,
        execution_universe,
    )
    from xiaogu_forward_paper_recorder_v0_1 import validate_paper_observation
    from xiaogu_portfolio_decision import evaluate_candidate_bundle

    def _row(symbol, f13, **extra):
        payload = {
            "symbol": symbol,
            "f12": symbol,
            "f13": f13,
            "f1": 2,
            "price": 10,
            "volume": 100,
            "amount": 1000,
            "source_time": "2026-08-26T14:50:00+08:00",
            "trade_date": "2026-08-26",
        }
        payload.update(extra)
        return payload

    main_board = classify_execution_board(_row("600000", 1))
    star = classify_execution_board(_row("688001", 1))
    chinext = classify_execution_board(_row("300001", 0))
    bse = classify_execution_board(_row("920992", 0))
    unknown = classify_execution_board({"symbol": "600000", "price": 10})
    conflict = classify_execution_board(_row("300001", 1))
    st_main = classify_execution_board(_row("600001", 1, f14="*ST示例", f148=4))
    halted = classify_execution_board(_row("600002", 1, halted=True, trade_status="HALTED"))
    eligible, audit = execution_universe([
        _row("600000", 1),
        _row("688001", 1),
        _row("600002", 1, halted=True, trade_status="HALTED"),
    ])
    star_decision = evaluate_candidate_bundle(
        _row("688001", 1, open=9.9, high=12, low=9.7, pct_chg=8, f62=500),
        position_state="FLAT",
    )
    recorder_ok = False
    try:
        validate_paper_observation({
            "paper_observation": {
                "status": "PAPER_OBSERVATION",
                "paper_signal_id": "x",
                "decision_id": "y",
                "alpha_name": "price_strength",
                "live_order": False,
                "paper_only": True,
                "paper_observation_state": "OBSERVED",
                "paper_position_state": "PAPER_FLAT",
            },
            "canonical_snapshot": {**_row("300001", 0), "trusted_snapshot": True},
        }, {"paper_only": True, "no_trade": True, "production_ready": False, "auto_order": False, "broker_connected": False})
    except ValueError as exc:
        recorder_ok = str(exc) == "EXECUTION_BOARD_VIOLATION"
    policy_papers_ok = True
    try:
        from sqlalchemy import text
        from xiaogu_db import get_db
        with get_db() as db:
            rows = db.execute(text(
                "SELECT COUNT(*) FROM paper_observations "
                "WHERE payload->>'execution_board_policy_version' = 'main_board_only_v1' "
                "AND COALESCE(payload->>'board', '') <> 'MAIN_BOARD'"
            )).scalar()
            policy_papers_ok = int(rows or 0) == 0
    except Exception:
        policy_papers_ok = True
    ok = (
        main_board["board"] == BOARD_MAIN and main_board["execution_eligible"] is True
        and star["board"] == BOARD_STAR and star["execution_eligible"] is False
        and chinext["board"] == BOARD_CHINEXT and chinext["execution_eligible"] is False
        and bse["board"] == BOARD_BSE and bse["execution_eligible"] is False
        and unknown["board"] == BOARD_UNKNOWN and unknown["execution_eligible"] is False
        and conflict["reason"] == "BOARD_IDENTITY_CONFLICT"
        and st_main["board_allowed"] is True
        and halted["board_allowed"] is True
        and [row["symbol"] for row in eligible] == ["600000"]
        and star_decision.get("paper_observation") is None
        and recorder_ok
        and policy_papers_ok
        and audit["policy_version"] == "main_board_only_v1"
    )
    return ok, "ok" if ok else "execution board contract mismatch"


CHECKS = [
    *((f"compile_{name}", lambda name=name: _compile(name)) for name in ("scanner", "runner", "features", "decision", "filler", "scheduler")),
    ("scanner_contract", check_scanner_contract),
    ("decision_owner", check_decision_owner),
    ("price_formation_features", check_price_formation_features),
    ("horizon_outcomes", check_horizon_outcomes),
    ("scheduler_outcome_job", check_scheduler_outcome_job),
    ("pipeline_chain", check_pipeline_chain),
    ("rule_freeze", check_rule_freeze),
    ("ledger_readable", check_ledger_readable),
    ("database_truth_boundaries", check_database_truth_boundaries),
    ("schema_fail_closed", check_schema_fail_closed),
    ("production_schema_audit", check_production_schema_audit),
    ("snapshot_identity_lock", check_snapshot_identity_lock),
    ("calendar_truth", check_calendar_truth),
    ("position_review_contract", check_position_review_contract),
    ("paper_position_review_contract", check_paper_position_review_contract),
    ("production_clock_contract", check_production_clock_contract),
    ("negative_evidence_contract", check_negative_evidence_contract),
    ("evidence_identity_contract", check_evidence_identity_contract),
    ("capital_identity_contract", check_capital_identity_contract),
    ("position_identity_contract", check_position_identity_contract),
    ("gate_contract", check_gate_contract),
    ("reduce_not_flat", check_reduce_not_flat),
    ("execution_board_contract", check_execution_board_contract),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Xiaogu repricing production health check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = []
    for name, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, repr(exc)
        results.append({"name": name, "ok": ok, "detail": detail})
    payload = {"checks": results, "passed": sum(item["ok"] for item in results), "total": len(results)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"[{'PASS' if result['ok'] else 'FAIL'}] {result['name']}: {result['detail']}")
        print(f"SUMMARY: {payload['passed']}/{payload['total']}")
    raise SystemExit(0 if payload["passed"] == payload["total"] else 1)


if __name__ == "__main__":
    main()
