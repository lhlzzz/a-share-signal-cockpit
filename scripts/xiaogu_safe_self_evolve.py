#!/usr/bin/env python3
"""Bounded factor/scoring self-evolution when production ranking gate is ready.

Does NOT:
- freeze PAPER_PICK
- rewrite formal_candidate_sort_key
- blindly --apply-weights on all signal keys

Does:
- read daily_closure production_ranking_change_gate
- if READY_FOR_PROPOSAL / READY_FOR_SMALL_STEP_CHANGE, apply small scoring_config nudges
  mapped from shadow/limitup-gene underweight evidence
- audit to summary/safe_self_evolve_{date}.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SUMMARY = ROOT / "summary"

# Real scoring_config knobs (not free-form signal names).
# Single owner: xiaogu_regime_policy.SELF_EVOLVE_BOUNDS (strategy table only).
try:
    from xiaogu_regime_policy import SELF_EVOLVE_BOUNDS as ALLOWED_KEYS
except Exception:  # pragma: no cover - bootstrap fallback
    ALLOWED_KEYS = {
        "evidence_limitup_momentum_weight": (0.4, 1.5, 0.10),
        "evidence_catalyst_boost_weight": (0.3, 1.2, 0.10),
        "evidence_broken_limit_penalty_weight": (1.0, 2.5, 0.10),
        "l2_limit_strength_bonus": (1.0, 150.0, 5.0),
        "sector_catalyst_penalty": (1.0, 150.0, 5.0),
        "delayed_setup_theme_min_score": (0.3, 0.8, 0.05),
        "instant_momentum_min_confirmations": (1.0, 3.0, 1.0),
    }


def _load_closure() -> Dict[str, Any]:
    path = SUMMARY / "daily_closure_latest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _gate(closure: Dict[str, Any]) -> Dict[str, Any]:
    cg = closure.get("cohort_gates") if isinstance(closure.get("cohort_gates"), dict) else {}
    gate = cg.get("production_ranking_change_gate") or closure.get("production_ranking_change_gate") or {}
    return gate if isinstance(gate, dict) else {}


def _limitup_gate(closure: Dict[str, Any]) -> Dict[str, Any]:
    cg = closure.get("cohort_gates") if isinstance(closure.get("cohort_gates"), dict) else {}
    gate = cg.get("limitup_capture_gate") or {}
    return gate if isinstance(gate, dict) else {}


def _shadow(closure: Dict[str, Any]) -> Dict[str, Any]:
    cg = closure.get("cohort_gates") if isinstance(closure.get("cohort_gates"), dict) else {}
    shadow = cg.get("shadow_ranking_replay") or {}
    return shadow if isinstance(shadow, dict) else {}


def _get_config_value(key: str) -> Optional[float]:
    from xiaogu_db import get_db
    from sqlalchemy import text

    with get_db() as db:
        row = db.execute(
            text("SELECT config_value FROM scoring_config WHERE config_key = :k"),
            {"k": key},
        ).fetchone()
    if not row:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def _set_config_value(key: str, value: float, dry_run: bool) -> None:
    if dry_run:
        return
    from xiaogu_db import get_db
    from sqlalchemy import text

    with get_db() as db:
        existing = db.execute(
            text("SELECT 1 FROM scoring_config WHERE config_key = :k"),
            {"k": key},
        ).fetchone()
        if existing:
            db.execute(
                text(
                    """
                    UPDATE scoring_config
                    SET config_value = :v, updated_at = NOW()
                    WHERE config_key = :k
                    """
                ),
                {"k": key, "v": str(value)},
            )
        else:
            db.execute(
                text(
                    """
                    INSERT INTO scoring_config (config_key, config_value, updated_at)
                    VALUES (:k, :v, NOW())
                    """
                ),
                {"k": key, "v": str(value)},
            )


def propose_nudges(closure: Dict[str, Any]) -> List[Dict[str, Any]]:
    gate = _gate(closure)
    limitup = _limitup_gate(closure)
    shadow = _shadow(closure)
    proposals: List[Dict[str, Any]] = []
    status = str(gate.get("status") or "LOCKED")
    if status not in ("READY_FOR_PROPOSAL", "READY_FOR_SMALL_STEP_CHANGE"):
        return proposals

    selected = str(shadow.get("selected_candidate_variant") or gate.get("selected_shadow_variant") or "")
    primary_blocker = str(limitup.get("primary_blocker") or "")
    # Optional regime hint from closure / sszcw soft (never free-form outside table).
    regime_hint = ""
    try:
        from xiaogu_regime_policy import classify_production_regime, preferred_shadow_variant

        soft = {}
        cg = closure.get("cohort_gates") if isinstance(closure.get("cohort_gates"), dict) else {}
        soft = cg.get("pre_pick_market_context_soft") if isinstance(cg.get("pre_pick_market_context_soft"), dict) else {}
        if not soft:
            soft = closure.get("pre_pick_market_context_soft") if isinstance(closure.get("pre_pick_market_context_soft"), dict) else {}
        regime_hint = classify_production_regime({}, soft if soft else None)
        # If gate has no shadow winner, prefer regime's shadow→key mapping.
        if not selected and regime_hint:
            selected = preferred_shadow_variant(regime_hint)
    except Exception:
        regime_hint = ""

    def add(key: str, direction: str, reason: str) -> None:
        if key not in ALLOWED_KEYS:
            return
        lo, hi, step = ALLOWED_KEYS[key]
        current = _get_config_value(key)
        if current is None:
            # defaults for missing keys
            defaults = {
                "evidence_limitup_momentum_weight": 0.7,
                "evidence_catalyst_boost_weight": 0.5,
                "evidence_broken_limit_penalty_weight": 1.5,
                "l2_limit_strength_bonus": 2.0,
                "sector_catalyst_penalty": 1.0,
                "delayed_setup_theme_min_score": 0.5,
                "instant_momentum_min_confirmations": 2.0,
            }
            current = float(defaults.get(key, lo))
        delta = step if direction == "INCREASE" else -step
        new_val = round(max(lo, min(hi, current + delta)), 4)
        if new_val == current:
            return
        proposals.append(
            {
                "config_key": key,
                "old": current,
                "new": new_val,
                "direction": direction,
                "reason": reason,
                "regime_hint": regime_hint or None,
                "within_regime_table_bounds": True,
            }
        )

    if primary_blocker == "LIMITUP_GENE_UNDERWEIGHTED" or selected == "limitup_gene_shadow_plus":
        add(
            "evidence_limitup_momentum_weight",
            "INCREASE",
            "shadow/limitup_capture points to LIMITUP_GENE_UNDERWEIGHTED",
        )
    if selected in ("risk_penalty_shadow_plus", "weak_market_defensive_shadow"):
        add(
            "evidence_broken_limit_penalty_weight",
            "INCREASE",
            f"selected shadow variant {selected} prefers stronger risk penalty",
        )
    if selected == "low_position_catalyst_shadow_plus":
        add(
            "evidence_catalyst_boost_weight",
            "INCREASE",
            "low_position_catalyst shadow beats baseline",
        )
    if selected == "social_catalyst_shadow":
        add(
            "evidence_catalyst_boost_weight",
            "INCREASE",
            "social_catalyst shadow selected (still soft evidence weight only)",
        )

    # Cap daily changes; never emit keys outside ALLOWED_KEYS / regime table.
    return proposals[:3]


def apply_proposals(proposals: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    applied = []
    for item in proposals:
        _set_config_value(item["config_key"], float(item["new"]), dry_run=dry_run)
        applied.append({**item, "dry_run": dry_run})
    return applied


def should_apply_if_ready(gate: Dict[str, Any]) -> bool:
    """True when production ranking gate has unlocked bounded factor apply."""
    status = str(gate.get("status") or "LOCKED")
    if status not in ("READY_FOR_PROPOSAL", "READY_FOR_SMALL_STEP_CHANGE"):
        return False
    self_evo = gate.get("self_evolution") if isinstance(gate.get("self_evolution"), dict) else {}
    if self_evo:
        return bool(self_evo.get("factor_weight_apply_allowed"))
    allowed = gate.get("allowed_actions") or []
    return "apply_bounded_factor_weights" in allowed


def main() -> None:
    ap = argparse.ArgumentParser(description="Bounded self-evolution for scoring_config")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--apply", action="store_true", help="Force write DB changes")
    ap.add_argument(
        "--apply-if-ready",
        action="store_true",
        help="Write DB only when production_ranking_change_gate is READY_* (chain default)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Force dry-run even if apply flags set")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    closure = _load_closure()
    gate = _gate(closure)

    # Chain default: --apply-if-ready auto-writes bounded knobs when gate READY.
    # Still never rewrites formal sort key / freeze.
    dry_run = True
    apply_mode = "dry_run"
    if args.dry_run:
        dry_run = True
        apply_mode = "forced_dry_run"
    elif args.apply:
        dry_run = False
        apply_mode = "force_apply"
    elif args.apply_if_ready and should_apply_if_ready(gate):
        dry_run = False
        apply_mode = "apply_if_ready"
    elif args.apply_if_ready:
        dry_run = True
        apply_mode = "gate_not_ready"
    elif os.environ.get("XIAOGU_SAFE_SELF_EVOLVE_APPLY") == "1":
        dry_run = False
        apply_mode = "env_apply"

    proposals = propose_nudges(closure)
    applied = apply_proposals(proposals, dry_run=dry_run) if proposals else []

    payload = {
        "asof": args.date,
        "gate_status": gate.get("status"),
        "gate_reason": gate.get("reason"),
        "allowed_actions": gate.get("allowed_actions"),
        "self_evolution": gate.get("self_evolution"),
        "proposals": proposals,
        "applied": applied,
        "dry_run": dry_run,
        "apply_mode": apply_mode,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    SUMMARY.mkdir(parents=True, exist_ok=True)
    out = SUMMARY / f"safe_self_evolve_{args.date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (SUMMARY / "safe_self_evolve_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"gate={payload['gate_status']} dry_run={dry_run} proposals={len(proposals)}")
    for item in applied:
        print(f"  {item['config_key']}: {item['old']} -> {item['new']} ({item['direction']})")
    if not applied:
        print("  (no changes)")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
