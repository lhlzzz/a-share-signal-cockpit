"""Canonical snapshot bundle I/O with no selection semantics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from xiaogu_forward_snapshot import canonical_snapshot

BASE = Path(__file__).resolve().parent
LIVE_SCAN_ROOT = BASE / "data" / "live_scan"


def load_snapshot(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def canonical_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("canonical_snapshots") or payload.get("stock_all_a") or payload.get("rows") or []
    return [canonical_snapshot(row, trade_date=str(payload.get("date") or ""), source_time=str(payload.get("source_time") or "")) for row in rows if isinstance(row, dict)]


def load_latest_snapshot_bundle(trade_date: str) -> Dict[str, Any]:
    paths = sorted(LIVE_SCAN_ROOT.glob(f"{trade_date}/**/xiaogu_scan_summary*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not paths:
        return {"available": False, "reason": "SNAPSHOT_NOT_FOUND", "date": trade_date}
    payload = load_snapshot(paths[0])
    canonical_path = Path(str((payload.get("files") or {}).get("canonical_snapshots") or ""))
    rows = _load_jsonl(canonical_path) if canonical_path.exists() else canonical_rows(payload)
    return {
        "available": bool(rows),
        "date": trade_date,
        "source_path": str(paths[0]),
        "canonical_snapshot_path": str(canonical_path) if canonical_path.exists() else None,
        "canonical_snapshots": canonical_rows({"canonical_snapshots": rows}),
    }
