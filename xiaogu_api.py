"""Production query API backed exclusively by PostgreSQL facts."""
from __future__ import annotations

import json
import re
import os
from statistics import median
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI

RECORDABLE_DECISIONS = {"BUY", "HOLD", "REDUCE", "SELL"}
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
            "paper_observation_state": "CLOSED" if closed else record.get("paper_observation_state") or "OBSERVED",
            "paper_position_state": "PAPER_FLAT" if closed else record.get("paper_position_state") or "PAPER_FLAT",
            "signal_reason": record.get("signal_reason"),
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


def _paper_metric(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        values = [float((row.get("outcome") or {}).get(field)) for row in settled if (row.get("outcome") or {}).get(field) is not None]
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
    }


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


@app.get("/state")
def current_state() -> Dict[str, Any]:
    from xiaogu_db import fetch_open_positions

    records = _decision_records()
    positions = []
    for row in fetch_open_positions():
        position = _payload(row)
        position.setdefault("decision", position.get("action") or position.get("state"))
        position["position_id"] = position.get("position_id")
        position["decision_id"] = position.get("decision_id")
        position["original_snapshot_id"] = position.get("original_snapshot_id")
        position["review_snapshot_id"] = position.get("review_snapshot_id")
        position["current_snapshot_id"] = position.get("review_snapshot_id")
        position["position_state"] = position.get("position_state")
        position["review_trade_date"] = position.get("review_trade_date")
        position["decision_clock"] = position.get("decision_clock")
        positions.append(_production_view(position))
    return {
        "market_state": "UNKNOWN",
        "positions": positions,
        "latest": _production_view(records[0]) if records else None,
    }


@app.get("/decision")
def current_decision() -> Dict[str, Any]:
    records = _decision_records()
    if not records:
        return {"found": False}
    record = dict(records[0])
    record["decision_id"] = record.get("decision_id")
    record["original_snapshot_id"] = record.get("original_snapshot_id")
    record["review_snapshot_id"] = record.get("review_snapshot_id")
    record["review_trade_date"] = record.get("review_trade_date")
    record["decision_clock"] = record.get("decision_clock")
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
