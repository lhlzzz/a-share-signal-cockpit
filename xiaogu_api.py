"""Production query API with no retired strategy surfaces."""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from xiaogu_utils import decision_record_id, has_decision_payload

BASE = Path(__file__).resolve().parent
LEDGER = BASE / "forward_paper_ledger_v0_1.jsonl"
RECORDABLE_DECISIONS = {"BUY", "HOLD", "REDUCE", "SELL"}
app = FastAPI(title="Xiaogu")


def _records() -> List[Dict[str, Any]]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def _decision_id(record: Dict[str, Any]) -> str:
    return str(record.get("id") or record.get("decision_id") or decision_record_id(record))


def _decision_records() -> List[Dict[str, Any]]:
    return [
        record for record in _records()
        if has_decision_payload(record)
        and str(record.get("decision") or record.get("new_state") or "").upper()
        in RECORDABLE_DECISIONS
    ]


def _result_records() -> List[Dict[str, Any]]:
    return [record for record in _records() if record.get("record_type") == "RESULT"]


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
    return {"status": "ok", "paper_only": True, "ledger_available": LEDGER.exists()}


@app.get("/state")
def current_state() -> Dict[str, Any]:
    records = _decision_records()
    latest = {}
    for record in records:
        latest[str(record.get("symbol") or "")] = record
    return {
        "market_state": "UNKNOWN",
        "positions": [
            _production_view(record)
            for record in latest.values()
            if record.get("decision") in {"BUY", "HOLD", "REDUCE"}
        ],
        "latest": _production_view(records[-1]) if records else None,
    }


@app.get("/decision")
def current_decision() -> Dict[str, Any]:
    records = _decision_records()
    return _production_view(records[-1]) if records else {"found": False}


@app.get("/trades")
def trades() -> List[Dict[str, Any]]:
    """Return one traceable trade view per production decision."""
    results = {}
    for result in _result_records():
        results.setdefault(_decision_id(result), []).append(result)
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
    }) for record in _decision_records()]


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
            "decision_id": _decision_id(result),
            "first_profit_day": review.get("profit_window_day"),
        })
    return {"success": successful, "failure": failed, "research_only": True}
