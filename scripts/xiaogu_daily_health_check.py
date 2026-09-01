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
    return _contains("scheduler", "xiaogu_forward_result_filler_v0_1.py", "--pending")


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
        and alpha_contract.get("target") == "PROFIT_WINDOW_5D"
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
    from inspect import getsource
    import xiaogu_db as db
    import xiaogu_forward_runner as runner

    review = getsource(runner.daily_position_review)
    resolver = getsource(db.get_current_position_review_snapshot)
    ok = (
        "get_current_position_review_snapshot" in review
        and "original_snapshot_id" in review
        and "review_snapshot_id" in review
        and "review_trade_date" in review
        and "decision_clock=_parse_clock" not in review
        and "ORDER BY source_time DESC" not in resolver
        and "CURRENT_REVIEW_SNAPSHOT_NOT_FOUND" in resolver
        and "CURRENT_REVIEW_SNAPSHOT_AMBIGUOUS" in resolver
    )
    return ok, "ok" if ok else "position review still uses original snapshot or latest-wins"


def check_production_clock_contract():
    from inspect import getsource
    import xiaogu_forward_runner as runner
    import xiaogu_forward_snapshot as snap

    ok = (
        "datetime.now(timezone.utc)" in getsource(snap.production_now)
        and "production_now()" in getsource(snap.production_decision_clock)
        and "production_decision_clock(decision_clock)" in getsource(runner.run_production_decision)
        and "decision_clock=_parse_clock" not in getsource(runner.daily_position_review)
    )
    return ok, "ok" if ok else "production clock contract mismatch"


def check_negative_evidence_contract():
    from inspect import getsource
    from xiaogu_portfolio_decision import (
        collect_production_negative_evidence,
        evaluate_candidate_bundle,
        evaluate_production_gates,
    )

    ok = (
        "collect_production_negative_evidence" in getsource(evaluate_production_gates)
        and "CONFIRMED_DISTRIBUTION" in getsource(collect_production_negative_evidence)
        and "UNKNOWN" in getsource(collect_production_negative_evidence)
        and "RESEARCH_ONLY_DECISION_BLOCKERS" not in getsource(evaluate_candidate_bundle)
        and "evaluate_production_gates(" in getsource(evaluate_candidate_bundle)
    )
    return ok, "ok" if ok else "negative evidence is still filtered out"


def check_gate_contract():
    from inspect import getsource
    from xiaogu_portfolio_decision import DECISION_HARD_GATES, evaluate_candidate_bundle, evaluate_production_gates

    rule = json.loads(open(PATHS["rule"], encoding="utf-8").read())
    ok = (
        tuple(rule["alpha_contract"]["required_hard_gates"]) == tuple(DECISION_HARD_GATES)
        and rule["alpha_contract"].get("gate_owner") == "xiaogu_portfolio_decision.evaluate_production_gates"
        and rule["alpha_contract"].get("gate_version") == "production_gate_v1"
        and "evaluate_production_gates(" in getsource(evaluate_candidate_bundle)
        and "oos_pass" not in getsource(evaluate_candidate_bundle)
        and "def evaluate_production_gates" in getsource(evaluate_production_gates)
    )
    return ok, "ok" if ok else "gate owner contract mismatch"


def check_capital_identity_contract():
    from inspect import getsource
    import xiaogu_core_alpha as alpha
    import xiaogu_forward_features as features

    conv = getsource(alpha._capital_convergence)
    evidence = getsource(features._evidence)
    ok = (
        "evidence_identity" in evidence
        and "independent_origin_count" in conv
        and "confirmed_channel_count" in conv
        and "directional_alignment" in conv
        and "evidence_identity_count" in conv
    )
    return ok, "ok" if ok else "capital identity contract mismatch"


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
    ("production_clock_contract", check_production_clock_contract),
    ("negative_evidence_contract", check_negative_evidence_contract),
    ("gate_contract", check_gate_contract),
    ("capital_identity_contract", check_capital_identity_contract),
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
