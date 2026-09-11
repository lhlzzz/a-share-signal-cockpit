"""Production query API backed exclusively by PostgreSQL facts."""
from __future__ import annotations

import json
import re
import os
from statistics import median
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query

RECORDABLE_DECISIONS = {"BUY", "HOLD", "REDUCE", "SELL"}
FRONT_DATA_CONTRACT_VERSION = "2026-08-13"
app = FastAPI(title="Xiaogu")


def _payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    result = dict(payload) if isinstance(payload, dict) else {}
    result.update({key: value for key, value in row.items() if key != "payload"})
    return result


def _decision_id(record: Dict[str, Any]) -> str:
    return str(record.get("decision_id") or "").strip()


def _decision_records() -> List[Dict[str, Any]]:
    from xiaogu_db import fetch_picks

    records = []
    for row in fetch_picks():
        record = _payload(row)
        action = str(record.get("action") or record.get("state") or record.get("decision") or "").upper()
        if action not in RECORDABLE_DECISIONS or not _decision_id(record):
            continue
        record["decision_id"] = _decision_id(record)
        record["decision"] = action
        record["new_state"] = action
        records.append(record)
    return records


def _result_records() -> List[Dict[str, Any]]:
    from xiaogu_db import fetch_returns

    return [_payload(row) for row in fetch_returns()]


def _paper_observation_records() -> List[Dict[str, Any]]:
    """Read paper observations from PostgreSQL only."""
    from xiaogu_db import fetch_paper_observations

    records = []
    for row in fetch_paper_observations():
        record = _payload(row)
        if not str(record.get("paper_signal_id") or "").strip():
            continue
        records.append(record)
    return records


def _paper_views() -> List[Dict[str, Any]]:
    outcomes = {}
    for result in _result_records():
        key = str(result.get("paper_signal_id") or result.get("decision_id") or "").strip()
        if key:
            outcomes[key] = result
    views = []
    for record in _paper_observation_records():
        key = record["paper_signal_id"]
        outcome = outcomes.get(key) or outcomes.get(str(record.get("decision_id") or "")) or {}
        closed = outcome.get("outcome_complete") is True and record.get("paper_position_state") == "PAPER_LONG"
        paper_observation_state = "CLOSED" if closed else record.get("paper_observation_state") or "OBSERVED"
        paper_position_state = "PAPER_FLAT" if closed else record.get("paper_position_state") or "PAPER_FLAT"
        if paper_observation_state == "CLOSED" or (closed and paper_position_state == "PAPER_FLAT"):
            paper_action = record.get("paper_action") or "PAPER_SELL"
        elif paper_position_state == "PAPER_LONG":
            paper_action = record.get("paper_action") or "PAPER_HOLD"
        else:
            paper_action = record.get("paper_action")
        view = {
            "paper_signal_id": key,
            "decision_id": record["decision_id"],
            "snapshot_id": record.get("snapshot_id"),
            "original_snapshot_id": record.get("original_snapshot_id") or record.get("snapshot_id"),
            "review_snapshot_id": record.get("review_snapshot_id"),
            "review_trade_date": record.get("review_trade_date"),
            "decision_clock": record.get("decision_clock"),
            "lineage_id": record.get("lineage_id"),
            "symbol": record.get("symbol"),
            "signal_time": record.get("signal_time") or record.get("asof_time"),
            "reference_price": record.get("reference_price"),
            "price_strength": record.get("price_strength"),
            "alpha_status": record.get("alpha_status"),
            "paper_observation": "PAPER_OBSERVATION",
            "paper_observation_state": paper_observation_state,
            "paper_position_state": paper_position_state,
            "paper_action": paper_action,
            "signal_reason": record.get("signal_reason"),
            "rank": record.get("rank"),
            "top1_flag": bool(record.get("top1_flag")),
            "top3_flag": bool(record.get("top3_flag")),
            "selection_reason": record.get("selection_reason"),
            "alpha_score": record.get("alpha_score"),
            "research_overlay": record.get("research_overlay") or {},
            "model_version": record.get("model_version"),
            "feature_version": record.get("feature_version"),
            "decision_version": record.get("decision_version"),
            "cost_model_version": record.get("cost_model_version") or "cost_model_v1",
            "paper_observation_contract_version": record.get("paper_observation_contract_version"),
            "validated_probability": record.get("validated_probability"),
            "paper_only": True,
            "live_order": False,
            "production_buy": "BLOCKED",
            "outcome": outcome,
        }
        views.append(view)
    return views


def _rank_band(rows: List[Dict[str, Any]], rank: int | None = None, *, top3: bool = False) -> List[Dict[str, Any]]:
    if rank is not None:
        return [row for row in rows if row.get("rank") == rank or (rank == 1 and row.get("top1_flag") is True)]
    if top3:
        return [row for row in rows if row.get("top3_flag") is True or row.get("rank") in {1, 2, 3}]
    return list(rows)


def _paper_band_metric(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = sorted(rows, key=lambda row: str(row.get("signal_time") or ""))
    settled = [row for row in rows if (row.get("outcome") or {}).get("outcome_complete") is True]
    nets = [float((row.get("outcome") or {}).get("future_5d_net_return")) for row in settled if (row.get("outcome") or {}).get("future_5d_net_return") is not None]
    maes = [float((row.get("outcome") or {}).get("max_mae_5d")) for row in settled if (row.get("outcome") or {}).get("max_mae_5d") is not None]
    mfes = [float((row.get("outcome") or {}).get("future_5d_mfe")) for row in settled if (row.get("outcome") or {}).get("future_5d_mfe") is not None]
    positive = sum(value for value in nets if value > 0)
    negative = abs(sum(value for value in nets if value < 0))
    cumulative = 0.0
    drawdown = 0.0
    peak = 0.0
    for value in nets:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    first_profit_days = {}
    for row in settled:
        day = (row.get("outcome") or {}).get("first_profit_day")
        if day is not None:
            first_profit_days[str(day)] = first_profit_days.get(str(day), 0) + 1
    horizon_metrics = {}
    for day in range(1, 6):
        field = f"future_{day}d_net_return"
        values = []
        for row in rows:
            outcome = row.get("outcome") or {}
            day_item = (outcome.get("days") or {}).get(str(day)) or {}
            value = outcome.get(field)
            if value is None:
                continue
            if outcome.get("outcome_complete") is True or str(day_item.get("status") or "") == "SETTLED":
                values.append(float(value))
        horizon_metrics[f"T+{day}"] = {
            "count": len(values),
            "mean_net_return": sum(values) / len(values) if values else None,
        }
    return {
        "count": len(rows),
        "closed": len(settled),
        "open": len(rows) - len(settled),
        "signal_count": len(rows),
        "closed_count": len(settled),
        "open_count": len(rows) - len(settled),
        "profit_window_rate": sum(bool((row.get("outcome") or {}).get("profit_window")) for row in settled) / len(settled) if settled else None,
        "mean_net_profit": sum(nets) / len(nets) if nets else None,
        "median_net_profit": median(nets) if nets else None,
        "mean_mae": sum(maes) / len(maes) if maes else None,
        "mean_mfe": sum(mfes) / len(mfes) if mfes else None,
        "MAE": sum(maes) / len(maes) if maes else None,
        "MFE": sum(mfes) / len(mfes) if mfes else None,
        "first_profit_day_distribution": first_profit_days,
        "horizon_metrics": horizon_metrics,
        "profit_factor": positive / negative if negative else (None if not positive else "INF"),
        "drawdown": drawdown,
        "paper_status": "PAPER_OBSERVATION_ONLY",
        "failure_rate": (
            sum(not bool((row.get("outcome") or {}).get("profit_window")) for row in settled) / len(settled)
            if settled else None
        ),
    }


def _paper_metric(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    metric = _paper_band_metric(rows)
    metric["by_rank"] = {
        "top1": _paper_band_metric(_rank_band(rows, 1)),
        "top2": _paper_band_metric(_rank_band(rows, 2)),
        "top3": _paper_band_metric(_rank_band(rows, 3)),
        "top3_all": _paper_band_metric(_rank_band(rows, top3=True)),
    }
    return metric


def _production_view(value: Any) -> Any:
    """Hide retired outcome keys when reading append-only historical records."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key).lower()
            match = re.search(r"(?:expected|future|actual)_(\d+)d_", key_text)
            if match and int(match.group(1)) not in {1, 2, 3, 4, 5}:
                continue
            if re.match(r"^t\d+[_-]", key_text):
                continue
            result[key] = _production_view(item)
        return result
    if isinstance(value, list):
        return [_production_view(item) for item in value]
    return value


@app.get("/health")
def system_health() -> Dict[str, Any]:
    try:
        from xiaogu_db import audit_production_schema

        schema = audit_production_schema()
        return {"status": "ok" if schema.get("ok") else "blocked", "paper_only": True, "database": schema}
    except Exception as exc:
        return {"status": "blocked", "paper_only": True, "database": {"ok": False, "error": repr(exc)}}


def _position_view(row: Dict[str, Any]) -> Dict[str, Any]:
    position = _payload(row)
    action = position.get("action") or position.get("state") or position.get("decision")
    return _production_view({
        **position,
        "position_id": position.get("position_id"),
        "decision_id": position.get("decision_id"),
        "symbol": position.get("symbol"),
        "original_snapshot_id": position.get("original_snapshot_id"),
        "position_state": position.get("position_state"),
        "opened_trade_date": position.get("opened_trade_date") or position.get("trade_date"),
        "closed_trade_date": position.get("closed_trade_date"),
        "review_snapshot_id": position.get("review_snapshot_id"),
        "review_trade_date": position.get("review_trade_date"),
        "decision_clock": position.get("decision_clock"),
        "action": action,
        "decision": action,
        "current_snapshot_id": position.get("review_snapshot_id"),
    })


@app.get("/state")
def current_state() -> Dict[str, Any]:
    from xiaogu_db import fetch_open_positions

    records = _decision_records()
    positions = [_position_view(row) for row in fetch_open_positions()]
    return {
        "market_state": "UNKNOWN",
        "positions": positions,
        "latest": _production_view(records[0]) if records else None,
    }


@app.get("/positions")
def positions() -> Dict[str, Any]:
    from xiaogu_db import fetch_open_positions

    rows = [_position_view(row) for row in fetch_open_positions()]
    return {"positions": rows, "count": len(rows)}


@app.get("/decision")
def current_decision() -> Dict[str, Any]:
    records = _decision_records()
    if not records:
        return {"found": False}
    record = dict(records[0])
    record["position_id"] = record.get("position_id")
    record["decision_id"] = record.get("decision_id")
    record["original_snapshot_id"] = record.get("original_snapshot_id")
    record["review_snapshot_id"] = record.get("review_snapshot_id")
    record["review_trade_date"] = record.get("review_trade_date")
    record["decision_clock"] = record.get("decision_clock")
    record["action"] = record.get("action") or record.get("decision") or record.get("state")
    return _production_view(record)


@app.get("/trades")
def trades() -> List[Dict[str, Any]]:
    """Return one traceable trade view per production decision."""
    results = {}
    for result in _result_records():
        decision_id = str(result.get("decision_id") or "").strip()
        if decision_id:
            results.setdefault(decision_id, []).append(result)
    return [_production_view({
        "decision_id": _decision_id(record),
        "symbol": record.get("symbol"),
        "original_snapshot_id": record.get("original_snapshot_id"),
        "review_snapshot_id": record.get("review_snapshot_id"),
        "review_trade_date": record.get("review_trade_date"),
        "decision_clock": record.get("decision_clock"),
        "decision": record.get("decision"),
        "signal_time": record.get("signal_time") or record.get("asof_time"),
        "entry_price": record.get("entry_price"),
        "entry_price_source": record.get("entry_price_source"),
        "previous_state": record.get("previous_state"),
        "new_state": record.get("new_state") or record.get("decision"),
        "reason": record.get("decision_reason"),
        "exit_reason": record.get("exit_reason"),
        "versions": {
            "decision": record.get("decision_version"),
            "alpha": record.get("alpha_version"),
            "feature": record.get("feature_version"),
        },
        "memory_path": record.get("memory_path"),
        "outcomes": results.get(_decision_id(record), []),
    }) for record in _decision_records() if _decision_id(record)]


@app.get("/trade/{decision_id}")
def trade(decision_id: str) -> Dict[str, Any]:
    matches = [item for item in trades() if item.get("decision_id") == decision_id]
    return matches[0] if matches else {"decision_id": decision_id, "found": False}


def _memory_bridge_url() -> str:
    return str(os.environ.get("XIAOGU_OBSIDIAN_BRIDGE_URL") or "").rstrip("/")


@app.get("/memory")
def memory(
    date: str = "",
    decision_id: str = "",
    paper_signal_id: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """Query the Obsidian memory adapter without scanning the vault."""
    bridge = _memory_bridge_url()
    if not bridge:
        return {"status": "MEMORY_BRIDGE_UNAVAILABLE", "notes": []}
    query = urlencode({
        "date": date,
        "decision_id": decision_id,
        "paper_signal_id": paper_signal_id,
        "limit": max(1, min(int(limit), 200)),
    })
    try:
        with urlopen(Request(f"{bridge}/memory?{query}"), timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "MEMORY_BRIDGE_UNAVAILABLE", "notes": [], "error": repr(exc)}
    notes = payload.get("notes", payload) if isinstance(payload, dict) else payload
    return {
        "status": "OK",
        "notes": notes if isinstance(notes, list) else [],
    }


@app.get("/patterns")
def patterns() -> Dict[str, Any]:
    successful, failed = [], []
    for result in _result_records():
        review = result.get("post_trade_review") or {}
        attribution = review.get("attribution")
        if not attribution:
            continue
        (successful if review.get("status") == "SUCCESS" else failed).append({
            "attribution": attribution,
            "symbol": result.get("symbol"),
            "decision_id": str(result.get("decision_id") or ""),
            "first_profit_day": review.get("profit_window_day"),
        })
    return {"success": successful, "failure": failed, "research_only": True}


@app.get("/paper/signals")
def paper_signals() -> Dict[str, Any]:
    rows = _paper_views()
    return {"status": "PAPER_OBSERVATION_ONLY", "signals": rows, "count": len(rows)}


@app.get("/paper/signal/{paper_signal_id}")
def paper_signal(paper_signal_id: str, decision_id: str = "") -> Dict[str, Any]:
    for row in _paper_views():
        if row["paper_signal_id"] == paper_signal_id or (decision_id and row["decision_id"] == decision_id):
            return row
    return {"paper_signal_id": paper_signal_id, "found": False, "status": "PAPER_OBSERVATION_ONLY"}


@app.get("/paper/performance")
def paper_performance() -> Dict[str, Any]:
    rows = _paper_views()
    return {
        "status": "PAPER_OBSERVATION_ONLY",
        "performance": _paper_metric(rows),
    }


@app.get("/paper/open")
def paper_open() -> Dict[str, Any]:
    rows = [
        row for row in _paper_views()
        if row["paper_observation_state"] == "OBSERVED"
        and row["paper_position_state"] == "PAPER_LONG"
    ]
    return {"status": "PAPER_OBSERVATION_ONLY", "signals": rows, "count": len(rows)}


@app.get("/paper/history")
def paper_history() -> Dict[str, Any]:
    rows = _paper_views()
    return {"status": "PAPER_OBSERVATION_ONLY", "signals": rows, "count": len(rows)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _front_date(record: Dict[str, Any]) -> str:
    return _text(record.get("trade_date") or _text(record.get("signal_time"))[:10])


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scan_session(run: Dict[str, Any] | None) -> Dict[str, Any]:
    from sqlalchemy import text
    from xiaogu_db import engine

    session_id = (run or {}).get("scan_session_id")
    if not session_id:
        return {}
    with engine.connect() as db:
        row = db.execute(
            text("SELECT * FROM scan_sessions WHERE id = :id"),
            {"id": session_id},
        ).mappings().first()
    return dict(row) if row else {}


def _horizon_t1(decision_id: str) -> float | None:
    from xiaogu_db import fetch_horizon_outcomes

    if not decision_id:
        return None
    days = (fetch_horizon_outcomes(decision_id).get("days") or {}).get("1") or {}
    if _text(days.get("status")).upper() != "SETTLED":
        return None
    return _number(days.get("net_return"))


def _snapshot(record: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = record.get("canonical_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _evidence_report(record: Dict[str, Any]) -> Dict[str, Any]:
    overlay = record.get("research_overlay") if isinstance(record.get("research_overlay"), dict) else {}
    snapshot = _snapshot(record)
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    announcements = []
    for item in snapshot.get("announcements") or []:
        if isinstance(item, dict) and (item.get("title") or item.get("title_ch")):
            announcements.append({"title": item.get("title") or item.get("title_ch")})
    inflow = _number(raw.get("f62"))
    sector = snapshot.get("sector") or overlay.get("scarce_layer")
    return {
        "sectors": {
            "primary": sector,
            "market_theme_tags": [sector] if sector else [],
            "sector_news": [],
        },
        "catalyst": {"announcements": announcements[:3]},
        "capital_flow": {
            "main_force_net_inflow_yi": None if inflow is None else inflow / 1e8,
        },
        "risk": {"risk_flags": [], "missing_domains": []},
        "t1_space": {"score": record.get("price_strength")},
        "data_coverage": {
            "status": "READY" if overlay.get("skill_complete") else "PARTIAL",
            "missing_domains": [],
        },
        "skills": {
            "serenity": overlay.get("serenity"),
            "buffett": overlay.get("buffett"),
            "uzi": overlay.get("uzi"),
            "skill_complete": overlay.get("skill_complete"),
            "bottleneck_table": overlay.get("bottleneck_table"),
            "institution_vs_hot_money": overlay.get("institution_vs_hot_money"),
        },
    }


def _front_candidate(record: Dict[str, Any], t1_return: float | None) -> Dict[str, Any]:
    snapshot = _snapshot(record)
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    name = snapshot.get("name") or record.get("symbol")
    score = record.get("selection_score") or record.get("alpha_score")
    rank = record.get("rank")
    official = bool(record.get("top3_flag") or rank in {1, 2, 3})
    return {
        "symbol": record.get("symbol"),
        "name": name,
        "stock_name": name,
        "rank": rank,
        "formal_rank": rank,
        "top1_flag": bool(record.get("top1_flag") or rank == 1),
        "top3_flag": official,
        "is_official_pick": official,
        "decision": "PAPER_OBSERVATION",
        "score": score,
        "final_score": score,
        "production_score": score,
        "alpha_score": record.get("alpha_score"),
        "entry_price": record.get("reference_price") or snapshot.get("price"),
        "close_price": snapshot.get("price") or record.get("reference_price"),
        "price": snapshot.get("price") or record.get("reference_price"),
        "pct_chg": raw.get("f3"),
        "t1_return": t1_return,
        "selection_reason": record.get("selection_reason") or record.get("signal_reason"),
        "selectionReason": record.get("selection_reason") or record.get("signal_reason"),
        "paper_signal_id": record.get("paper_signal_id"),
        "decision_id": record.get("decision_id"),
        "production_run_id": record.get("production_run_id"),
        "snapshot_id": record.get("original_snapshot_id") or record.get("snapshot_id"),
        "production_buy": "BLOCKED",
        "paper_observation_state": record.get("paper_observation_state") or "OBSERVED",
        "paper_position_state": record.get("paper_position_state") or "PAPER_FLAT",
        "paper_only": True,
        "live_order": False,
        "evidenceReport": _evidence_report(record),
        "research_overlay": record.get("research_overlay") or {},
    }


def _obsidian_connection() -> Dict[str, Any]:
    from pathlib import Path
    from xiaogu_forward_paper_recorder_v0_1 import _obsidian_vault_root

    vault = _obsidian_vault_root()
    daily = vault / "xiaogu_memory" / "daily" if vault is not None else None
    shenlin = Path("/mnt/d/obisidian/Obsidian/神临")
    return {
        "database": "online",
        "obsidian": "online" if daily is not None and daily.exists() else "offline",
        "obsidianPath": str(vault) if vault is not None else "",
        "shenlin": "online" if shenlin.exists() else "offline",
        "shenlinPath": str(shenlin) if shenlin.exists() else "",
        "vectorRecords": 0,
    }


def _memory_entries(trade_date: str) -> list[Dict[str, Any]]:
    from pathlib import Path
    from xiaogu_forward_paper_recorder_v0_1 import BASE, _obsidian_vault_root

    entries: list[Dict[str, Any]] = []
    seen: set[str] = set()
    roots = []
    vault = _obsidian_vault_root()
    if vault is not None:
        roots.append(vault)
    roots.append(BASE / "data" / "obsidian_memory")
    for root in roots:
        daily = root / "xiaogu_memory" / "daily" / f"{trade_date}.md"
        if daily.exists() and "daily" not in seen:
            seen.add("daily")
            entries.append({
                "id": f"daily-{trade_date}",
                "title": f"{trade_date} 官方出票日笔记",
                "type": "daily",
                "path": str(daily),
                "content": daily.read_text(encoding="utf-8")[:1200],
            })
        decision_root = root / "xiaogu_memory" / "decisions" / "PAPER_OBSERVATION" / trade_date
        if not decision_root.exists():
            continue
        for path in sorted(decision_root.glob("*/*.md")):
            key = path.stem
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "id": key,
                "title": path.parent.name,
                "type": "paper_observation",
                "path": str(path),
                "content": path.read_text(encoding="utf-8")[:1200],
            })
    return entries


def load_front_data(trade_date: str = "") -> Dict[str, Any]:
    from datetime import datetime, timezone
    from xiaogu_db import fetch_production_run

    records = _paper_observation_records()
    dates = sorted({_front_date(row) for row in records if _front_date(row)}, reverse=True)
    requested = _text(trade_date)
    date = requested if requested in dates or (requested and not dates) else (dates[0] if dates else requested)
    day_rows = [row for row in records if _front_date(row) == date]
    day_rows.sort(key=lambda row: (row.get("rank") is None, row.get("rank") or 99, str(row.get("symbol") or "")))
    t1_by_decision = {str(row.get("decision_id") or ""): _horizon_t1(str(row.get("decision_id") or "")) for row in records}
    candidates = [_front_candidate(row, t1_by_decision.get(str(row.get("decision_id") or ""))) for row in day_rows]
    history = []
    for row in records:
        item = _front_candidate(row, t1_by_decision.get(str(row.get("decision_id") or "")))
        item["trade_date"] = _front_date(row)
        item["date"] = _front_date(row)
        history.append(item)
    history.sort(key=lambda row: (str(row.get("trade_date") or ""), int(row.get("rank") or 99), str(row.get("symbol") or "")))
    top1 = next((row for row in candidates if row.get("top1_flag")), candidates[0] if candidates else {})
    run_id = _text((day_rows[0] if day_rows else {}).get("production_run_id"))
    run = fetch_production_run(run_id) if run_id else None
    session = _scan_session(run)
    market = session.get("market_snapshot") if isinstance(session.get("market_snapshot"), dict) else {}
    settled = [row for row in history if row.get("t1_return") is not None]
    wins = [row for row in settled if float(row["t1_return"]) > 0]
    losses = [row for row in settled if float(row["t1_return"]) <= 0]
    avg_t1 = (sum(float(row["t1_return"]) for row in settled) / len(settled)) if settled else None
    memory_connection = _obsidian_connection()
    memory_entries = _memory_entries(date) if date else []
    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    coverage = ((run or {}).get("scoring_config_snapshot") or {})
    if isinstance(coverage, str):
        try:
            coverage = json.loads(coverage)
        except json.JSONDecodeError:
            coverage = {}
    observation_coverage = coverage.get("observation_coverage") if isinstance(coverage, dict) else {}
    lifecycle_state = _text((run or {}).get("status")) or ("PAPER_OBSERVATION_RECORDED" if candidates else "UNAVAILABLE")
    return {
        "contract_version": FRONT_DATA_CONTRACT_VERSION,
        "source": "postgresql",
        "workspace": "xiaogu",
        "date": date,
        "availableDates": dates,
        "databaseConnected": True,
        "generatedAt": generated,
        "paper_only": True,
        "production_buy": "BLOCKED",
        "live_trading": "DISABLED",
        "productionChain": {
            "name": "main_force_behavior_chain",
            "label": "5日获利窗口观察链",
            "rankSource": "formal_profit_first",
            "objective": "T日出票，T+1收盘获利",
            "returnField": "returns.t1_return",
        },
        "lifecycle": {
            "state": lifecycle_state,
            "production_run_id": run_id or None,
            "lineage_id": (run or {}).get("lineage_id") or (day_rows[0].get("lineage_id") if day_rows else None),
            "blockers": ["PRODUCTION_BUY_BLOCKED"],
            "source_freshness": {
                "run_updated_at": _text((run or {}).get("updated_at")),
                "scan_time": _text(session.get("scan_time") or market.get("timestamp")),
            },
        },
        "manualExecution": {
            "status": "BLOCKED",
            "symbol": top1.get("symbol"),
            "blockers": ["PRODUCTION_BUY_BLOCKED"],
            "risk_state": {
                "data_gate_status": "PASS" if candidates else "UNAVAILABLE",
                "market_regime": "UNKNOWN",
                "market_regime_risk": "OBSERVE_ONLY",
                "chase_high_risk": "UNKNOWN",
            },
            "position_boundary": {"max_risk_fraction": None, "account_snapshot_required": False},
            "execution_record": {"status": "UNCONFIRMED"},
            "replay_provenance": {
                "production_run_id": run_id or None,
                "candidate_snapshot_id": top1.get("snapshot_id"),
                "symbol": top1.get("symbol"),
            },
        },
        "decision": {
            "paper_pick": top1 or None,
            "status": "PAPER_OBSERVATION_ONLY",
        },
        "candidates": candidates,
        "tradeHistory": history,
        "latestChainHistory": history,
        "selectedDateTradeHistory": [_front_candidate(row, t1_by_decision.get(str(row.get("decision_id") or ""))) | {"trade_date": date, "date": date} for row in day_rows],
        "allOfficialTradeHistory": history,
        "candidateHistoryTop10": history,
        "marketState": {
            "regime": "WEAK" if _number(market.get("breadth_up_pct")) is not None and float(market.get("breadth_up_pct")) < 40 else "NEUTRAL",
            "quoteCount": market.get("quote_count") or session.get("quotes_count"),
            "limitUpCount": market.get("limit_up_observation_count"),
            "brokenLimitups": None,
            "upPercent": market.get("breadth_up_pct"),
            "advancing": market.get("up_count"),
            "declining": market.get("down_count"),
            "marketMainInflow": None,
            "marketMainInflowSource": "scan_sessions.market_snapshot",
        },
        "aShareMarket": {
            "snapshotScanTime": _text(session.get("scan_time") or market.get("timestamp")),
            "quoteCount": market.get("quote_count") or session.get("quotes_count"),
        },
        "systemStats": {
            "winningTrades": len(wins),
            "losingTrades": len(losses),
            "officialObservationCount": len(records),
        },
        "systemHealth": {
            "api": "online",
            "database": "online",
            "scanner": "online" if session else "offline",
            "model": "online",
            "memory": memory_connection.get("obsidian") or "offline",
            "lastUpdate": _text(session.get("scan_time") or market.get("timestamp") or generated),
        },
        "syncState": {
            "database": "online",
            "obsidian": memory_connection.get("obsidian"),
            "obsidianPath": memory_connection.get("obsidianPath"),
            "historyRecords": len(records),
            "reviewCases": 0,
            "generatedAt": generated,
            "latestUpdate": _text(session.get("updated_at") or session.get("scan_time") or generated),
        },
        "memory": {
            "connection": memory_connection,
            "entries": memory_entries,
        },
        "dataSources": [
            {"name": "PostgreSQL paper_observations", "status": "online"},
            {"name": "PostgreSQL production_runs / scan_sessions", "status": "online" if session else "offline"},
            {"name": "PostgreSQL returns T+1..T+5", "status": "online"},
            {"name": "Obsidian Project/A股", "status": memory_connection.get("obsidian")},
        ],
        "observability": {
            "scanTradeDate": _text(session.get("trade_date") or date),
            "scanTime": _text(session.get("scan_time") or market.get("timestamp")),
            "coverage": observation_coverage or {},
        },
        "review": {
            "cases": [],
            "fullHistoryAttribution": {
                "status": "MONITOR_ONLY",
                "data_range": {
                    "min_date": dates[-1] if dates else None,
                    "max_date": dates[0] if dates else None,
                },
                "strict_production": {
                    "status": "PASS" if settled else "UNAVAILABLE",
                    "sample_count": len(settled),
                    "minimum_settled_samples": 1,
                    "paper": {
                        "count": len(settled),
                        "avg_t1": avg_t1,
                        "win_rate": (len(wins) / len(settled)) if settled else None,
                        "max_drawdown": None,
                    },
                    "data_range": {
                        "min_date": dates[-1] if dates else None,
                        "max_date": dates[0] if dates else None,
                    },
                },
                "formal_top10_rescore": {
                    "count": len(settled),
                    "avg_t1": avg_t1,
                    "win_rate": (len(wins) / len(settled)) if settled else None,
                },
            },
        },
        "latestChainReplay": {
            "window": {"min_date": dates[-1] if dates else None, "max_date": dates[0] if dates else None},
            "settledSamples": [
                {
                    "trade_date": row.get("trade_date"),
                    "symbol": row.get("symbol"),
                    "stock_name": row.get("stock_name"),
                    "final_score": row.get("final_score"),
                    "entry_price": row.get("entry_price"),
                    "t1_return": row.get("t1_return"),
                    "reason_summary": row.get("selection_reason"),
                }
                for row in settled
            ],
            "max_drawdown_detail": None,
        },
    }


@app.get("/api/os/front-data")
def front_data(trade_date: str = Query(default=""), date: str = Query(default="")) -> Dict[str, Any]:
    """Operator dashboard contract. Query only; PostgreSQL is ticket truth, Obsidian is memory."""
    try:
        return load_front_data(trade_date or date)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": "Database unavailable", "detail": repr(exc)}) from exc
