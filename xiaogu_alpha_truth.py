#!/usr/bin/env python3
"""Observation-only Alpha truth report.

This is not a second Alpha, selector, ranker, decision, paper, outcome, or
database. It reads PostgreSQL facts through existing owners and never feeds
results back into scoring, selection, gates, BUY, or SELL.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable

from xiaogu_core_alpha import MODEL_ID, TARGET_VERSION
from xiaogu_horizon_evaluation import (
    evaluate_official_observations,
    load_official_observation_rows,
)
from xiaogu_utils import PRODUCTION_TARGET

OBSERVATION_MODE = "PAPER_OBSERVATION"
LIVE_TRADING = "DISABLED"
PRODUCTION_BUY = "BLOCKED"


def build_observation_truth_report(
    rows: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Dashboard payload. Observation layer only."""
    observations = list(rows) if rows is not None else load_official_observation_rows()
    stats = evaluate_official_observations(observations)
    status = stats.get("status") or "DATA_INSUFFICIENT"
    sample_count = int(stats.get("sample_count") or 0)
    return {
        "production_alpha": MODEL_ID,
        "target": PRODUCTION_TARGET or TARGET_VERSION,
        "observation_status": status,
        "sample_size": sample_count,
        "top1": stats.get("top1"),
        "top3": stats.get("top3"),
        "horizon": stats.get("horizon"),
        "risk": stats.get("risk"),
        "baseline": stats.get("baseline"),
        "regime": stats.get("regime"),
        "stability": stats.get("stability"),
        "evidence": stats.get("evidence") or "NO_REAL_OOS_EVIDENCE_YET",
        "no_real_oos_evidence_yet": sample_count == 0,
        "mode": OBSERVATION_MODE,
        "buy": PRODUCTION_BUY,
        "live": LIVE_TRADING,
        "influences_selection": False,
        "influences_alpha": False,
        "influences_decision": False,
        "influences_gate": False,
        "influences_buy": False,
        "source_of_truth": "PostgreSQL",
        "oos_owner": "xiaogu_horizon_evaluation.py",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print observation-only Alpha truth.")
    parser.parse_args()
    print(json.dumps(build_observation_truth_report(), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
