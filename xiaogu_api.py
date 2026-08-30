"""Production query API backed exclusively by PostgreSQL facts."""
from __future__ import annotations

import json
import re
import os
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
