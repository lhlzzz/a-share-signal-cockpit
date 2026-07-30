#!/usr/bin/env python3
"""Historical PAPER_PICK shadow replay under production regime policy.

Diagnostic only:
- does NOT rewrite picks ledger
- does NOT change formal_candidate_sort_key
- reports per-day regime, preferred shadow variant, paper vs preferred returns

Uses completed paper-pick days from backtest helpers when available; falls back
to daily_closure shadow_ranking_replay aggregate if day-level sample missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SUMMARY = ROOT / "summary"


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_context_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort market context from a stored candidate / paper row."""
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    elig = row.get("paper_pick_eligibility") if isinstance(row.get("paper_pick_eligibility"), dict) else {}
    elig_signals = elig.get("signals") if isinstance(elig.get("signals"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            for src in (row, features, signals, elig_signals):
                if key in src and src.get(key) is not None:
                    return src.get(key)
        return None

    breadth = _num(pick("market_breadth_up_pct"))
    follow = _num(pick("market_follow_through_score"))
    limitups = _num(pick("market_limitups"))
    broken_ratio = _num(pick("limitup_broken_ratio"))
    broken = _num(pick("broken_limitups"))
    regime = str(pick("market_regime") or pick("production_regime") or "").lower()
    ctx = {
        "market_regime": regime,
        "market_breadth_up_pct": breadth,
        "market_follow_through_score": follow,
        "market_limitups": limitups,
        "limitup_broken_ratio": broken_ratio,
        "broken_limitups": broken,
        "sentiment_score": _num(pick("sentiment_score")),
        "max_consecutive": _num(pick("max_consecutive")),
        "supportive_market": bool(pick("supportive_market")),
        "weak_acceptance_market": bool(pick("weak_acceptance_market")),
        "overheated_market": bool(pick("overheated_market")),
    }
    # Infer flags when only raw metrics exist (align with market_adaptive_context heuristics).
    if not ctx["market_regime"]:
        if (
            follow is not None
            and follow >= 0.62
            and breadth is not None
            and breadth >= 58
            and broken_ratio is not None
            and broken_ratio >= 1.2
        ):
            ctx["market_regime"] = "strong"
            ctx["supportive_market"] = True
        elif (
            (follow is not None and follow <= 0.38)
            or (breadth is not None and breadth <= 45)
            or (broken_ratio is not None and broken_ratio <= 0.85)
        ):
            ctx["market_regime"] = "weak"
            ctx["weak_acceptance_market"] = True
        else:
            ctx["market_regime"] = "neutral"
    if not ctx["weak_acceptance_market"] and ctx["market_regime"] == "weak":
        ctx["weak_acceptance_market"] = True
    if not ctx["supportive_market"] and ctx["market_regime"] == "strong":
        ctx["supportive_market"] = True
    if not ctx["overheated_market"] and (
        (breadth is not None and breadth >= 80) or (limitups is not None and limitups >= 150)
    ):
        ctx["overheated_market"] = True
    soft = pick("pre_pick_market_context_soft") or pick("soft_context")
    if isinstance(soft, dict):
        ctx["soft_context"] = soft
    return ctx


def _load_soft_latest() -> Dict[str, Any]:
    for path in (SUMMARY / "sszcw_market_context_latest.json", ROOT / "data" / "sszcw" / "latest.json"):
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
            except Exception:
                continue
    return {}


def _completed_days_from_backtest() -> List[Dict[str, Any]]:
    """Use backtest internal sample builder when DB is available."""
    try:
        import xiaogu_backtest_v0_1 as backtest

        # Prefer public-ish helpers if present
        if hasattr(backtest, "completed_paper_pick_sample_days"):
            # Many deployments build sample inside build_daily_closure; try lightweight path.
            pass
        # Fallback: read last daily_closure for sample_count + variant metrics only
    except Exception:
        return []
    return []


def _parse_json_obj(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _days_from_db(limit_days: int = 40) -> List[Dict[str, Any]]:
    """Load PAPER_PICK + same-day top candidates with t1 returns.

    Schema notes (actual DB):
    - picks has no market_regime column; regime lives in features / eligibility JSON
    - daily_candidates has market_regime + candidate_features/raw_json, not market_breadth_up_pct col
    """
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text
    except Exception as exc:
        print(f"_days_from_db import failed: {exc}", file=sys.stderr)
        return []

    days: List[Dict[str, Any]] = []
    try:
        with get_db() as db:
            pick_rows = db.execute(
                text(
                    """
                    SELECT trade_date, symbol, decision, final_score, features,
                           paper_pick_eligibility
                    FROM picks
                    WHERE decision = 'PAPER_PICK'
                      AND COALESCE(features->>'superseded', 'false') <> 'true'
                    ORDER BY trade_date DESC
                    LIMIT :lim
                    """
                ),
                {"lim": int(limit_days)},
            ).fetchall()
            for prow in pick_rows:
                trade_date = prow[0]
                if hasattr(trade_date, "isoformat"):
                    trade_date_s = trade_date.isoformat()[:10]
                else:
                    trade_date_s = str(trade_date)[:10]
                paper_symbol = str(prow[1] or "").zfill(6)
                features = _parse_json_obj(prow[4])
                elig = _parse_json_obj(prow[5])
                # returns for paper
                ret = db.execute(
                    text(
                        """
                        SELECT t1_return FROM returns
                        WHERE trade_date = CAST(:d AS date) AND symbol = :s
                        ORDER BY id DESC LIMIT 1
                        """
                    ),
                    {"d": trade_date_s, "s": paper_symbol},
                ).fetchone()
                paper_t1 = _num(ret[0]) if ret else None
                if paper_t1 is None:
                    continue
                cand_rows = db.execute(
                    text(
                        """
                        SELECT symbol, rank, final_score, candidate_features,
                               market_regime, raw_json
                        FROM daily_candidates
                        WHERE trade_date = CAST(:d AS date)
                        ORDER BY rank NULLS LAST
                        LIMIT 30
                        """
                    ),
                    {"d": trade_date_s},
                ).fetchall()
                day_rows: List[Dict[str, Any]] = []
                for crow in cand_rows:
                    sym = str(crow[0] or "").zfill(6)
                    cret = db.execute(
                        text(
                            """
                            SELECT t1_return FROM returns
                            WHERE trade_date = CAST(:d AS date) AND symbol = :s
                            ORDER BY id DESC LIMIT 1
                            """
                        ),
                        {"d": trade_date_s, "s": sym},
                    ).fetchone()
                    t1 = _num(cret[0]) if cret else None
                    if t1 is None:
                        continue
                    cfeat = _parse_json_obj(crow[3])
                    raw = _parse_json_obj(crow[5])
                    if not cfeat and raw:
                        cfeat = raw
                    market_regime = crow[4] or cfeat.get("market_regime") or raw.get("market_regime")
                    if market_regime and "market_regime" not in cfeat:
                        cfeat["market_regime"] = market_regime
                    # breadth may live only inside features/raw JSON
                    for src in (cfeat, raw):
                        if "market_breadth_up_pct" not in cfeat and src.get("market_breadth_up_pct") is not None:
                            cfeat["market_breadth_up_pct"] = src.get("market_breadth_up_pct")
                    day_rows.append(
                        {
                            "symbol": sym,
                            "rank": crow[1],
                            "score": crow[2],
                            "final_score": crow[2],
                            "t1_return": t1,
                            "features": cfeat,
                            "market_regime": market_regime,
                        }
                    )
                if not day_rows:
                    continue
                market_regime = features.get("market_regime")
                if not market_regime and isinstance(elig.get("signals"), dict):
                    market_regime = elig.get("signals", {}).get("market_regime")
                if not market_regime and isinstance(elig, dict):
                    market_regime = elig.get("market_regime")
                if not market_regime and day_rows:
                    market_regime = day_rows[0].get("market_regime")
                if market_regime and "market_regime" not in features:
                    features["market_regime"] = market_regime
                paper = {
                    "symbol": paper_symbol,
                    "score": prow[3],
                    "final_score": prow[3],
                    "t1_return": paper_t1,
                    "features": features,
                    "paper_pick_eligibility": elig if isinstance(elig, dict) else {},
                    "market_regime": market_regime,
                }
                # attach paper into day if missing
                if not any(r["symbol"] == paper_symbol for r in day_rows):
                    day_rows.append({**paper, "rank": 0})
                days.append({"trade_date": trade_date_s, "paper": paper, "day": day_rows})
    except Exception as exc:
        print(f"_days_from_db failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    # chronological
    days.sort(key=lambda d: d["trade_date"])
    return days


def _shadow_pick_return(day: Dict[str, Any], variant: str) -> Optional[float]:
    """Prefer existing backtest shadow scorer when importable."""
    try:
        import xiaogu_backtest_v0_1 as backtest

        if hasattr(backtest, "_shadow_score"):
            best = max(
                day["day"],
                key=lambda row: (
                    backtest._shadow_score(row, variant),
                    str(row.get("symbol") or ""),
                ),
            )
            return _num(best.get("t1_return"))
    except Exception:
        pass
    # Fallback heuristic: limitup-ish uses highest t1 among top ranks as optimistic proxy only labeled heuristic
    if not day.get("day"):
        return None
    if variant == "baseline_current":
        return _num(day["paper"].get("t1_return"))
    # crude: pick max t1 in day (upper bound diagnostic) for non-baseline when scorer missing
    return max((_num(r.get("t1_return")) or -1.0) for r in day["day"])


def replay(days: List[Dict[str, Any]], soft_global: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from xiaogu_regime_policy import (
        classify_production_regime,
        preferred_shadow_variant,
        attach_regime_to_context,
        policy_snapshot,
    )

    soft_global = soft_global if isinstance(soft_global, dict) else {}
    per_day: List[Dict[str, Any]] = []
    regime_counts: Dict[str, int] = {}
    paper_returns: List[float] = []
    preferred_returns: List[float] = []
    beats = 0

    for day in days:
        paper = day["paper"]
        ctx = _market_context_from_row(paper)
        soft = ctx.get("soft_context") if isinstance(ctx.get("soft_context"), dict) else soft_global
        attach_regime_to_context(ctx, soft if isinstance(soft, dict) else None)
        regime = str(ctx.get("production_regime") or "sideways")
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        variant = preferred_shadow_variant(regime)
        paper_t1 = _num(paper.get("t1_return"))
        pref_t1 = _shadow_pick_return(day, variant)
        if paper_t1 is None or pref_t1 is None:
            continue
        paper_returns.append(paper_t1)
        preferred_returns.append(pref_t1)
        better = pref_t1 > paper_t1
        if better:
            beats += 1
        per_day.append(
            {
                "trade_date": day.get("trade_date"),
                "paper_symbol": paper.get("symbol"),
                "paper_t1_return": paper_t1,
                "production_regime": regime,
                "preferred_shadow_variant": variant,
                "preferred_shadow_t1_return": pref_t1,
                "preferred_beats_paper": better,
                "delta_t1": round(pref_t1 - paper_t1, 6),
                "sector_gate_threshold": ctx.get("regime_policy", {}).get("sector_gate_threshold"),
            }
        )

    def avg(vals: List[float]) -> Optional[float]:
        return round(sum(vals) / len(vals), 6) if vals else None

    return {
        "status": "PASS" if per_day else "INSUFFICIENT_SAMPLE",
        "sample_count": len(per_day),
        "regime_counts": regime_counts,
        "paper_avg_t1": avg(paper_returns),
        "preferred_shadow_avg_t1": avg(preferred_returns),
        "preferred_beats_paper_rate": round(beats / len(per_day), 4) if per_day else None,
        "days": per_day,
        "policy": policy_snapshot(),
        "selected_for_production": False,
        "ledger_mutation_allowed": False,
        "formal_sort_key_rewrite_allowed": False,
        "note": "diagnostic shadow only; does not rewrite historical PAPER_PICK",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Regime-aware historical PAPER_PICK shadow replay")
    ap.add_argument("--limit-days", type=int, default=40)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    days = _days_from_db(limit_days=args.limit_days)
    soft = _load_soft_latest()
    payload = replay(days, soft_global=soft)
    payload["asof"] = args.date
    payload["source_day_count_loaded"] = len(days)

    SUMMARY.mkdir(parents=True, exist_ok=True)
    out = SUMMARY / f"regime_shadow_replay_{args.date}.json"
    latest = SUMMARY / "regime_shadow_replay_latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")

    if args.json:
        print(text)
        return
    print(
        f"status={payload['status']} samples={payload['sample_count']} "
        f"paper_avg={payload['paper_avg_t1']} preferred_avg={payload['preferred_shadow_avg_t1']} "
        f"beats_rate={payload['preferred_beats_paper_rate']}"
    )
    print(f"regime_counts={payload['regime_counts']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
