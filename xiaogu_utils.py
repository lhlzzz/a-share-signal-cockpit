"""Shared utility functions for xiaogu pipeline."""
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec='seconds')


def read_json(path: Path) -> Dict[str, Any]:
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def dump_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    pending = ''
    pending_start = 0
    with path.open('r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            if pending and text.startswith('{'):
                print(json.dumps({'warning': 'SKIP_MALFORMED_JSONL_RECORD', 'path': str(path), 'line_start': pending_start, 'line_end': i - 1}, ensure_ascii=False), file=sys.stderr)
                pending = ''
                pending_start = 0
            candidate = pending + text if pending else text
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                pending = candidate
                if not pending_start:
                    pending_start = i
                continue
            obj['_line'] = pending_start or i
            if pending_start and pending_start != i:
                obj['_line_end'] = i
            rows.append(obj)
            pending = ''
            pending_start = 0
    if pending:
        print(json.dumps({'warning': 'SKIP_MALFORMED_JSONL_RECORD', 'path': str(path), 'line_start': pending_start, 'line_end': pending_start}, ensure_ascii=False), file=sys.stderr)
    return rows


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=False) + '\n')
