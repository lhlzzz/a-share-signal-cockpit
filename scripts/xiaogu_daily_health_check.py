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
    "ledger": os.path.join(WORKSPACE_ROOT, "forward_paper_ledger_v0_1.jsonl"),
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
    return _contains("decision", "def evaluate_candidate_bundle", "WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL")


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
    return tuple(HORIZONS) == (5,), "ok" if tuple(HORIZONS) == (5,) else "unexpected horizon set"


def check_scheduler_outcome_job():
    return _contains("scheduler", "xiaogu_forward_result_filler_v0_1.py", "--pending")


def check_pipeline_chain():
    return _contains("pipeline", "scrapy_scanner/runner_v2.py", "xiaogu_forward_runner.py")


def check_rule_freeze():
    with open(PATHS["rule"], encoding="utf-8") as handle:
        rule = json.load(handle)
    decisions = set(rule.get("allowed_decisions") or [])
    required = {"WATCH", "READY", "BUY", "HOLD", "REDUCE", "SELL"}
    ok = (
        rule.get("rule_version") == "repricing_production_v1"
        and rule.get("production_owner") == "xiaogu_portfolio_decision.evaluate_candidate_bundle"
        and decisions == required
        and rule.get("evaluation", {}).get("horizons_days") == [5]
        and rule.get("evaluation", {}).get("max_holding_boundary") == 5
        and rule.get("alpha_contract", {}).get("target") == "PROFIT_WINDOW_5D"
        and rule.get("paper_only") is True
        and rule.get("auto_order") is False
        and rule.get("broker_connected") is False
    )
    return ok, "ok" if ok else "repricing rule freeze contract mismatch"


def check_ledger_readable():
    if not os.path.exists(PATHS["ledger"]):
        return True, "ledger not present - skipped"
    try:
        with open(PATHS["ledger"], encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    json.loads(line)
        return True, "ok"
    except (OSError, json.JSONDecodeError) as exc:
        return False, repr(exc)


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
