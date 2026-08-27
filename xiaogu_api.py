"""Production query API with no retired strategy surfaces."""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from xiaogu_horizon_evaluation import HORIZONS
from xiaogu_utils import decision_record_id, has_decision_payload

BASE = Path(__file__).resolve().parent
LEDGER = BASE / "forward_paper_ledger_v0_1.jsonl"
app = FastAPI(title="Xiaogu")


def _records() -> List[Dict[str, Any]]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def _decision_id(record: Dict[str, Any]) -> str:
    return str(record.get("id") or record.get("decision_id") or decision_record_id(record))


def _decision_records() -> List[Dict[str, Any]]:
    return [record for record in _records() if has_decision_payload(record)]


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


@app.get("/dashboard")
def dashboard() -> Dict[str, Any]:
    records = _decision_records()
    latest = {}
    for record in records:
        latest[str(record.get("symbol") or "")] = record
    today = __import__("datetime").date.today().isoformat()
    return {
        "date": today,
        "records": len(records),
        "market_state": "UNKNOWN",
        "capital_convergence": [
            _production_view((record.get("features_used") or {}).get("core_alpha", {}).get("capital_convergence"))
            for record in latest.values()
        ],
        "repricing_candidates": [_production_view(record) for record in latest.values() if record.get("decision") in {"BUY", "READY", "WATCH"}],
        "buy": [_production_view(record) for record in latest.values() if record.get("decision") == "BUY"],
        "ready": [_production_view(record) for record in latest.values() if record.get("decision") == "READY"],
        "watch": [_production_view(record) for record in latest.values() if record.get("decision") == "WATCH"],
        "portfolio": [_production_view(record) for record in latest.values() if record.get("decision") in {"BUY", "HOLD", "REDUCE"}],
        "latest": _production_view(records[-1]) if records else None,
    }


@app.get("/picks")
def picks() -> List[Dict[str, Any]]:
    return [_production_view(record) for record in _decision_records()]


@app.get("/portfolio")
def portfolio() -> List[Dict[str, Any]]:
    latest = {}
    for record in picks():
        latest[str(record.get("symbol") or "")] = record
    return [_production_view(record) for record in latest.values()]


@app.get("/returns")
def returns() -> List[Dict[str, Any]]:
    latest = {}
    for record in _records():
        if record.get("record_type") == "RESULT":
            latest[str(record.get("decision_id") or "")] = _production_view(record)
    return list(latest.values())


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
    vault = Path("/mnt/d/obisidian/Obsidian/Project/A股")
    return vault / "xiaogu_memory" if vault.exists() else BASE / "xiaogu_memory"


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


@app.get("/repricing-state")
def repricing_state() -> List[Dict[str, Any]]:
    return [
        {
            "date": record.get("date"),
            "symbol": record.get("symbol"),
            "state": record.get("decision"),
            "repricing_readiness": (record.get("features_used") or {}).get("repricing_readiness"),
            "repricing_risk": (record.get("features_used") or {}).get("repricing_risk"),
            "future_buyer_map": (record.get("features_used") or {}).get("future_buyer_map"),
            "core_alpha": (record.get("features_used") or {}).get("core_alpha"),
        }
        for record in portfolio()
    ]


@app.get("/alpha")
def alpha() -> List[Dict[str, Any]]:
    """Expose current five-day alpha without exposing retired targets."""
    return [
        {
            "date": record.get("date"),
            "symbol": record.get("symbol"),
            "decision": record.get("decision"),
            "core_alpha": (record.get("features_used") or {}).get("core_alpha", {}),
        }
        for record in portfolio()
    ]


@app.get("/research-context")
def research_context() -> List[Dict[str, Any]]:
    return [
        record["features_used"].get("research_context", {})
        for record in picks()
        if isinstance(record.get("features_used"), dict)
    ]
