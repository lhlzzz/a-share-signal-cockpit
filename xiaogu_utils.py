"""Shared utility functions for xiaogu pipeline."""
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Production contract: every official ticket is evaluated on the T-day close
# and sold on the next trading day at the final close. Historical database
# columns may remain for audit, but they are not production metrics.
PRODUCTION_TRADE_MODE = 'T_DAY_BUY_T1_CLOSE_SELL'
PRODUCTION_RETURN_FIELD = 't1_return'
PRODUCTION_RETURN_FORMULA = '(t1_close - t_day_entry_close) / t_day_entry_close'
PRODUCTION_DECISIONS = frozenset({'PAPER_PICK', 'NO_PICK'})
ACTIVE_DECISION_RECORD_TYPES = frozenset({'DECISION', 'CORRECTION'})


def eastmoney_quote_prices(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Normalize Eastmoney quote aliases across clist and stock endpoints.

    The paginated clist endpoint used by the scanner returns f2/f15/f16/f17,
    while some stock endpoints expose the same values as f43/f44/f45/f46.
    Prefer the canonical clist fields whenever they are present because the
    f43-f46 aliases can represent unrelated metrics in clist responses.
    """
    row = row if isinstance(row, dict) else {}

    def number(*keys: str) -> Optional[float]:
        for key in keys:
            value = row.get(key)
            if value in (None, '', '-'):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    return {
        'close': number('close', 'price', 'p', 'f2', 'f43'),
        'high': number('high', 'h', 'f15', 'f44'),
        'low': number('low', 'l', 'f16', 'f45'),
        'open': number('open', 'o', 'f17', 'f46'),
        'prev_close': number('prev_close', 'pre_close', 'f18'),
    }


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


def decision_symbol(decision: Dict[str, Any]) -> str:
    symbol = decision.get('symbol')
    if symbol and symbol != 'NO_PICK':
        return str(symbol).zfill(6)
    features = decision.get('features_used', {}).get('candidate_features', {})
    symbol = features.get('symbol') or features.get('code')
    return str(symbol).zfill(6) if symbol else ''


def decision_record_id(row: Dict[str, Any]) -> str:
    return '_'.join([
        str(row.get('date') or ''),
        str(row.get('asof_time') or ''),
        str(row.get('record_type', 'DECISION')),
        decision_symbol(row),
    ])


def has_decision_payload(row: Dict[str, Any]) -> bool:
    record_type = row.get('record_type', 'DECISION')
    return record_type == 'DECISION' or (record_type == 'CORRECTION' and bool(row.get('decision')))


def corrected_decision_ids(rows: List[Dict[str, Any]]) -> set[str]:
    return {
        str(row.get('correction_of'))
        for row in rows
        if row.get('record_type') == 'CORRECTION' and row.get('decision') and row.get('correction_of')
    }


def superseded_decision_keys(rows: List[Dict[str, Any]]) -> set[Tuple[str, str]]:
    keys = set()
    for row in rows:
        if not has_decision_payload(row):
            continue
        supersedes = (row.get('features_used') or {}).get('supersedes') or {}
        date = supersedes.get('date')
        symbol = supersedes.get('symbol')
        if date and symbol:
            keys.add((str(date), str(symbol).zfill(6)))
    return keys


def is_active_decision_record(
    row: Dict[str, Any],
    corrected_ids: set[str],
    superseded: set[Tuple[str, str]],
) -> bool:
    if row.get('record_type', 'DECISION') not in ACTIVE_DECISION_RECORD_TYPES or not has_decision_payload(row):
        return False
    symbol = decision_symbol(row)
    if decision_record_id(row) in corrected_ids:
        return False
    return (str(row.get('date')), symbol) not in superseded


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=False) + '\n')
