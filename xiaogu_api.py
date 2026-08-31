"""Production query API backed exclusively by PostgreSQL facts."""
from __future__ import annotations

import json
import re
import os
from statistics import median
from pathlib import Path
from typing import Any, Dict, List

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


def _paper_signal_records() -> List[Dict[str, Any]]:
    """Read observational signals from PostgreSQL picks only."""
    from xiaogu_db import fetch_picks

    records = []
    for row in fetch_picks():
        record = _payload(row)
        signal = record.get("paper_signal") if isinstance(record.get("paper_signal"), dict) else {}
        if record.get("paper_signal_status") != "PAPER_SIGNAL" and signal.get("status") != "PAPER_SIGNAL":
            continue
        decision_id = str(record.get("decision_id") or "").strip()
        if not decision_id:
            continue
        record["decision_id"] = decision_id
        record["paper_signal"] = "PAPER_SIGNAL"
        record["signal_reason"] = record.get("signal_reason") or record.get("decision_reason") or signal.get("signal_reason")
        record["price_strength"] = record.get("price_strength") if record.get("price_strength") is not None else signal.get("price_strength")
        record["alpha_status"] = record.get("alpha_status") or signal.get("alpha_status") or "DATA_INSUFFICIENT"
        record["risk_state"] = record.get("risk_state") or signal.get("risk_state")
        record["execution_state"] = record.get("execution_state") or signal.get("execution_state")
        record["research_overlay"] = record.get("research_overlay") or signal.get("research_overlay") or {}
        record["sort_value"] = signal.get("sort_value") if signal.get("sort_value") is not None else record.get("price_strength")
        records.append(record)
    return records


def _paper_views() -> List[Dict[str, Any]]:
    outcomes = {}
    for result in _result_records():
        decision_id = str(result.get("decision_id") or "").strip()
        if decision_id:
            outcomes[decision_id] = result
    views = []
    for record in _paper_signal_records():
        outcome = outcomes.get(record["decision_id"]) or {}
        closed = outcome.get("outcome_complete") is True or outcome.get("paper_signal_state") == "PAPER_CLOSED"
        view = {
            "decision_id": record["decision_id"],
            "symbol": record.get("symbol"),
            "signal_time": record.get("signal_time") or record.get("asof_time"),
            "entry_reference_price": record.get("entry_price") or record.get("entry_reference_price"),
            "entry_price_source": record.get("entry_price_source"),
            "price_strength": record.get("price_strength"),
            "alpha_status": record.get("alpha_status"),
            "paper_signal": "PAPER_SIGNAL",
            "paper_signal_state": "PAPER_CLOSED" if closed else "PAPER_OPEN",
            "paper_position_state": "PAPER_FLAT" if closed else "PAPER_LONG",
            "risk_state": record.get("risk_state"),
            "execution_state": record.get("execution_state"),
            "signal_reason": record.get("signal_reason"),
            "research_overlay": record.get("research_overlay") or {},
            "sort_value": record.get("sort_value"),
            "model_version": record.get("model_version"),
            "feature_version": record.get("feature_version"),
            "decision_version": record.get("decision_version"),
            "cost_model_version": record.get("cost_model_version") or "cost_model_v1",
            "snapshot_id": record.get("snapshot_id") or (record.get("canonical_snapshot") or {}).get("snapshot_id"),
            "lineage_id": record.get("lineage_id") or (record.get("canonical_snapshot") or {}).get("lineage_id"),
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
        "signal_count": len(rows),
        "closed_count": len(settled),
        "open_count": len(rows) - len(settled),
        "profit_window_rate": sum(bool((row.get("outcome") or {}).get("profit_window")) for row in settled) / len(settled) if settled else None,
        "mean_net_profit": sum(nets) / len(nets) if nets else None,
        "median_net_profit": median(nets) if nets else None,
        "mean_mae": sum(maes) / len(maes) if maes else None,
        "mean_mfe": sum(mfes) / len(mfes) if mfes else None,
        "first_profit_day_distribution": first_profit_days,
        "horizon_metrics": horizon_metrics,
        "profit_factor": positive / negative if negative else (None if not positive else "INF"),
        "drawdown": drawdown,
        "paper_status": "PAPER_OBSERVATION_ONLY",
    }


def _paper_grouped_performance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(str(row.get("signal_time") or "")[:10], []).append(row)
    selected = {"All": rows, "Top5": [], "Top10": []}
    for date_rows in by_date.values():
        ordered = sorted(date_rows, key=lambda row: float(row.get("sort_value") or -1), reverse=True)
        selected["Top5"].extend(ordered[:5])
        selected["Top10"].extend(ordered[:10])
    return {name: _paper_metric(group) for name, group in selected.items()}


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
        positions.append(_production_view(position))
    return {
        "market_state": "UNKNOWN",
        "positions": positions,
        "latest": _production_view(records[0]) if records else None,
    }


@app.get("/decision")
def current_decision() -> Dict[str, Any]:
    records = _decision_records()
    return _production_view(records[0]) if records else {"found": False}


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


def _memory_root() -> Path:
    configured = os.environ.get("XIAOGU_MEMORY_ROOT")
    if configured:
        return Path(configured)
    return Path("/mnt/d/obisidian/Obsidian/Project/A股") / "xiaogu_memory"


@app.get("/memory")
def memory() -> List[Dict[str, Any]]:
    root = _memory_root()
    if not root.exists():
        return []
    return [{
        "path": str(path),
        "category": path.parent.name,
        "symbol": path.stem.rsplit("_", 1)[-1],
        "content": path.read_text(encoding="utf-8"),
    } for path in sorted(root.glob("**/*.md"))]


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


@app.get("/paper/signal/{decision_id}")
def paper_signal(decision_id: str) -> Dict[str, Any]:
    for row in _paper_views():
        if row["decision_id"] == decision_id:
            return row
    return {"decision_id": decision_id, "found": False, "status": "PAPER_OBSERVATION_ONLY"}


@app.get("/paper/performance")
def paper_performance() -> Dict[str, Any]:
    rows = _paper_views()
    return {
        "status": "PAPER_OBSERVATION_ONLY",
        "performance": _paper_metric(rows),
        "groups": _paper_grouped_performance(rows),
    }


@app.get("/paper/open")
def paper_open() -> Dict[str, Any]:
    rows = [row for row in _paper_views() if row["paper_signal_state"] == "PAPER_OPEN"]
    return {"status": "PAPER_OBSERVATION_ONLY", "signals": rows, "count": len(rows)}


@app.get("/paper/history")
def paper_history() -> Dict[str, Any]:
    rows = _paper_views()
    return {"status": "PAPER_OBSERVATION_ONLY", "signals": rows, "count": len(rows)}
