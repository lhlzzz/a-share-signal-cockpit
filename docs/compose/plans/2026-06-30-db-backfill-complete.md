# DB Backfill Complete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill all missing data in the xiaogu database from May 18 to present, using every available data source (ledger JSONL, forward_candidate_bundles, live_scan scored JSONL, factors parquet).

**Architecture:** Single backfill script (`scripts/xiaogu_db_backfill.py`) that reads all sources, deduplicates, and upserts into `daily_candidates`, `picks`, `returns`, and `signals` tables. Sources are prioritized: forward_candidate_bundles > live_scan scored > ledger > factors parquet.

**Tech Stack:** Python 3, SQLAlchemy, json, pathlib, pandas (for parquet)

## Global Constraints

- All DB operations through `xiaogu_db.py` helpers (insert_pick, upsert_daily_candidate, upsert_return, upsert_signal)
- Never modify production DB directly — all changes through code
- Idempotent: re-running the script produces the same result (uses ON CONFLICT DO UPDATE)
- Data source priority: forward_candidate_bundles (richest) > live_scan scored > ledger > factors parquet

---

## Data Source Inventory

| Source | Path | Dates | Content |
|--------|------|-------|---------|
| Ledger | `forward_paper_ledger_v0_1.jsonl` | 5/12-7/03 (40 dates) | Pick decisions + embedded candidates |
| Candidate Bundles | `data/forward_candidate_bundles/{date}/` | 6/06-7/03 (22 dates) | Full scored candidate lists |
| Live Scan Scored | `data/live_scan/{date}/eastmoney_web_tabs_scan_v0_1/eastmoney_web_tabs_scored.jsonl` | 6/06-7/03 (22 dates) | Scored candidates per scan |
| Factors Parquet | `data/factors/{YYYYMMDD}.parquet` | 6/10-7/03 (6 dates) | Scored candidates with features |

---

### Task 1: Create Backfill Script Skeleton

**Covers:** Core backfill infrastructure

**Files:**
- Create: `scripts/xiaogu_db_backfill.py`
- Test: `tests/test_db_backfill.py`

**Interfaces:**
- Consumes: `xiaogu_db.py` (insert_pick, upsert_daily_candidate, upsert_return, upsert_signal, get_db)
- Produces: `backfill_from_ledger()`, `backfill_from_bundles()`, `backfill_from_live_scan()`, `backfill_from_factors()`, `main()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_backfill.py
def test_backfill_script_imports():
    """Verify backfill script can be imported."""
    import importlib
    mod = importlib.import_module('scripts.xiaogu_db_backfill')
    assert hasattr(mod, 'backfill_from_ledger')
    assert hasattr(mod, 'backfill_from_bundles')
    assert hasattr(mod, 'backfill_from_live_scan')
    assert hasattr(mod, 'backfill_from_factors')
    assert hasattr(mod, 'main')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/xiaogu_db_backfill.py
"""Backfill missing data in xiaogu database from all available sources."""
import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xiaogu_db import (
    insert_pick, upsert_daily_candidate, upsert_return, upsert_signal,
    get_db, engine
)
from sqlalchemy import text


def backfill_from_ledger() -> int:
    """Extract picks + candidates from forward_paper_ledger_v0_1.jsonl."""
    # TODO: implement
    return 0


def backfill_from_bundles() -> int:
    """Extract candidates from data/forward_candidate_bundles/."""
    # TODO: implement
    return 0


def backfill_from_live_scan() -> int:
    """Extract scored candidates from data/live_scan/."""
    # TODO: implement
    return 0


def backfill_from_factors() -> int:
    """Extract candidates from data/factors/*.parquet."""
    # TODO: implement
    return 0


def main():
    """Run all backfill sources."""
    print("=== xiaogu DB Backfill ===")
    n1 = backfill_from_ledger()
    print(f"Ledger: {n1} records inserted")
    n2 = backfill_from_bundles()
    print(f"Bundles: {n2} records inserted")
    n3 = backfill_from_live_scan()
    print(f"Live scan: {n3} records inserted")
    n4 = backfill_from_factors()
    print(f"Factors: {n4} records inserted")
    print(f"Total: {n1 + n2 + n3 + n4} records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: add backfill script skeleton with source placeholders"
```

---

### Task 2: Implement Ledger Backfill

**Covers:** Extract picks + candidates from the primary paper ledger

**Files:**
- Modify: `scripts/xiaogu_db_backfill.py` (backfill_from_ledger function)
- Modify: `tests/test_db_backfill.py`

**Interfaces:**
- Consumes: `forward_paper_ledger_v0_1.jsonl` (234 records, 40 dates)
- Produces: Inserts into `picks` and `daily_candidates` tables

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_backfill.py - add after test_backfill_script_imports
def test_ledger_backfill_dry_run(tmp_path):
    """Verify ledger parser extracts records correctly."""
    import json
    # Create a mini ledger
    ledger = tmp_path / "test_ledger.jsonl"
    records = [
        {
            "date": "2026-05-20", "symbol": "300603", "decision": "PAPER_PICK",
            "rule_version": "rule_v0_2", "features_used": {
                "candidate_bundle_status": {
                    "available": True,
                    "paper_scoring_candidates": [
                        {"code": "300603", "name": "立昂微", "rank": 1, "final_score": 75.0,
                         "score": 75.0, "structured_score": 50.0, "signal_pct": 3.5,
                         "close_position_score": 0.8, "fund_flow_momentum": 0.5,
                         "sector_catalyst_score": 0.3, "early_opportunity_score": 0.7,
                         "topic_propagation_score": 0.2, "market_regime": "neutral",
                         "setup_type": "FIRST_BOARD_PRE_SIGNAL"}
                    ]
                },
                "candidate_features": {"code": "300603", "name": "立昂微"},
                "risk_flags": [],
            },
            "t1_return": 0.05, "t2_return": None, "t3_return": None,
        },
        {
            "date": "2026-05-20", "symbol": "300603", "decision": "",
            "rule_version": "rule_v0_2", "features_used": {
                "candidate_bundle_status": {"available": None},
                "candidate_features": {},
                "risk_flags": [],
            },
        },
    ]
    with open(ledger, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')

    # Parse without DB
    from scripts.xiaogu_db_backfill import _parse_ledger
    parsed = _parse_ledger(ledger)
    assert len(parsed) >= 1
    assert parsed[0]['date'] == '2026-05-20'
    assert parsed[0]['symbol'] == '300603'
    assert parsed[0]['decision'] == 'PAPER_PICK'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py::test_ledger_backfill_dry_run -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement ledger parser**

Add to `scripts/xiaogu_db_backfill.py`:

```python
def _parse_ledger(ledger_path: Path) -> list:
    """Parse ledger JSONL into structured records for DB insertion."""
    records = []
    with open(ledger_path) as f:
        for line in f:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue

            trade_date_str = raw.get('date', '')
            if not trade_date_str:
                continue

            decision = raw.get('decision', '')
            symbol = (raw.get('symbol') or '').strip()
            rule_version = raw.get('rule_version', '')
            features = raw.get('features_used', {})
            if not isinstance(features, dict):
                features = {}

            # Extract candidate bundle
            cbs = features.get('candidate_bundle_status', {})
            if not isinstance(cbs, dict):
                cbs = {}
            psc_list = cbs.get('paper_scoring_candidates', [])
            if not isinstance(psc_list, list):
                psc_list = []

            # Extract candidate features
            cf = features.get('candidate_features', {})
            if not isinstance(cf, dict):
                cf = {}

            # Extract risk flags
            risk_flags = features.get('risk_flags', [])
            if not isinstance(risk_flags, list):
                risk_flags = []

            # Build picks record
            final_score = None
            if psc_list:
                # Use the picked candidate's score
                for c in psc_list:
                    if isinstance(c, dict) and (c.get('code') == symbol or c.get('symbol') == symbol):
                        final_score = c.get('final_score') or c.get('score')
                        break
                if final_score is None and psc_list:
                    final_score = psc_list[0].get('final_score') or psc_list[0].get('score')

            records.append({
                'trade_date': trade_date_str,
                'symbol': symbol,
                'decision': decision or 'NO_PICK',
                'final_score': final_score,
                'blockers': risk_flags,
                'features': cf,
                'source_layers': [],
                'rule_version': rule_version,
                'scan_dir': '',
                'dry_run': True,
                't1_return': raw.get('t1_return'),
                't2_return': raw.get('t2_return'),
                't3_return': raw.get('t3_return'),
                'candidates': psc_list,
            })

    return records


def backfill_from_ledger() -> int:
    """Extract picks + candidates from forward_paper_ledger_v0_1.jsonl."""
    ledger_path = ROOT / 'forward_paper_ledger_v0_1.jsonl'
    if not ledger_path.exists():
        print(f"Ledger not found: {ledger_path}")
        return 0

    records = _parse_ledger(ledger_path)
    count = 0

    for rec in records:
        trade_date = date.fromisoformat(rec['trade_date'])

        # Insert pick record
        if rec['symbol'] or rec['decision'] in ('PAPER_PICK', 'NO_PICK'):
            try:
                insert_pick(
                    trade_date=trade_date,
                    symbol=rec['symbol'],
                    decision=rec['decision'],
                    final_score=rec['final_score'],
                    blockers=rec['blockers'],
                    features=rec['features'],
                    source_layers=rec['source_layers'],
                    rule_version=rec['rule_version'],
                    scan_dir=rec['scan_dir'],
                    dry_run=rec['dry_run'],
                )
                count += 1
            except Exception as e:
                print(f"  Error inserting pick {trade_date} {rec['symbol']}: {e}")

        # Insert candidates from bundle
        for i, cand in enumerate(rec['candidates']):
            if not isinstance(cand, dict):
                continue
            code = cand.get('code') or cand.get('symbol', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=(code == rec['symbol'] and rec['decision'] == 'PAPER_PICK'),
                    decision=rec['decision'] if code == rec['symbol'] else 'CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=cand.get('market_regime', ''),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=cand,
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting candidate {trade_date} {code}: {e}")

        # Insert return if available
        if rec['t1_return'] is not None and rec['symbol']:
            try:
                upsert_return(
                    trade_date=trade_date,
                    symbol=rec['symbol'],
                    pick_id=None,
                    t1_return=rec['t1_return'],
                    t2_return=rec['t2_return'],
                    t3_return=rec['t3_return'],
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting return {trade_date} {rec['symbol']}: {e}")

    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: implement ledger backfill parser and inserter"
```

---

### Task 3: Implement Candidate Bundles Backfill

**Covers:** Extract full scored candidate lists from forward_candidate_bundles

**Files:**
- Modify: `scripts/xiaogu_db_backfill.py` (backfill_from_bundles function)
- Modify: `tests/test_db_backfill.py`

**Interfaces:**
- Consumes: `data/forward_candidate_bundles/{date}/*.json` (22 dates, 8-24 candidates each)
- Produces: Inserts into `daily_candidates` table

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_backfill.py - add
def test_bundles_backfill_dry_run(tmp_path):
    """Verify bundle parser extracts candidates correctly."""
    import json
    bundle_dir = tmp_path / "bundles" / "2026-06-10"
    bundle_dir.mkdir(parents=True)
    bundle_file = bundle_dir / "2026-06-10_eastmoney_web_tabs_v0_1_research_basket_candidate.json"
    bundle_data = {
        "date": "2026-06-10",
        "paper_scoring_candidates": [
            {"code": "601816", "name": "京沪高铁", "rank": 1, "final_score": 83.8,
             "score": 67.96, "structured_score": 46.8, "signal_pct": 1.77,
             "close_position_score": 0.9, "fund_flow_momentum": 0.415,
             "sector_catalyst_score": 0.0, "early_opportunity_score": 0.96,
             "topic_propagation_score": 0.0, "market_regime": "neutral",
             "setup_type": "INTRADAY_ALERT_REVERSAL"},
            {"code": "600210", "name": "紫江企业", "rank": 2, "final_score": 82.0,
             "score": 82.0, "signal_pct": 2.1, "market_regime": "neutral"},
        ],
    }
    with open(bundle_file, 'w') as f:
        json.dump(bundle_data, f)

    from scripts.xiaogu_db_backfill import _parse_bundles
    parsed = _parse_bundles(tmp_path / "bundles")
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2
    assert parsed[0]['date'] == '2026-06-10'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py::test_bundles_backfill_dry_run -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement bundles parser**

Add to `scripts/xiaogu_db_backfill.py`:

```python
def _parse_bundles(bundles_dir: Path) -> list:
    """Parse forward_candidate_bundles/ into structured records."""
    results = []
    if not bundles_dir.exists():
        return results

    for date_dir in sorted(bundles_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        json_files = list(date_dir.glob('*_research_basket_candidate.json'))
        if not json_files:
            continue

        with open(json_files[0]) as f:
            bundle = json.load(f)

        trade_date_str = bundle.get('date', date_dir.name)
        candidates = bundle.get('paper_scoring_candidates', [])
        if not isinstance(candidates, list):
            candidates = []

        results.append({
            'date': trade_date_str,
            'candidates': candidates,
            'decision_reason': bundle.get('decision_reason', ''),
        })

    return results


def backfill_from_bundles() -> int:
    """Extract candidates from data/forward_candidate_bundles/."""
    bundles_dir = ROOT / 'data' / 'forward_candidate_bundles'
    if not bundles_dir.exists():
        print(f"Bundles dir not found: {bundles_dir}")
        return 0

    parsed = _parse_bundles(bundles_dir)
    count = 0

    for day in parsed:
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            if not isinstance(cand, dict):
                continue
            code = cand.get('code') or cand.get('symbol', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=False,
                    decision='CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=cand.get('market_regime', ''),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=cand,
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting bundle candidate {trade_date} {code}: {e}")

    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: implement candidate bundles backfill"
```

---

### Task 4: Implement Live Scan Scored Backfill

**Covers:** Extract scored candidates from live_scan JSONL files

**Files:**
- Modify: `scripts/xiaogu_db_backfill.py` (backfill_from_live_scan function)
- Modify: `tests/test_db_backfill.py`

**Interfaces:**
- Consumes: `data/live_scan/{date}/eastmoney_web_tabs_scan_v0_1/eastmoney_web_tabs_scored.jsonl`
- Produces: Inserts into `daily_candidates` table

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_backfill.py - add
def test_live_scan_backfill_dry_run(tmp_path):
    """Verify live scan parser extracts scored candidates."""
    import json
    scan_dir = tmp_path / "scan" / "2026-06-10" / "eastmoney_web_tabs_scan_v0_1"
    scan_dir.mkdir(parents=True)
    scored_file = scan_dir / "eastmoney_web_tabs_scored.jsonl"
    records = [
        {"code": "601816", "name": "京沪高铁", "rank": 1, "score": 67.96,
         "final_score": 67.96, "signal_pct": 1.77, "setup_type": "INTRADAY_ALERT_REVERSAL"},
        {"code": "600210", "name": "紫江企业", "rank": 2, "score": 82.0,
         "final_score": 82.0, "signal_pct": 2.1},
    ]
    with open(scored_file, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')

    from scripts.xiaogu_db_backfill import _parse_live_scan
    parsed = _parse_live_scan(tmp_path / "scan")
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py::test_live_scan_backfill_dry_run -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement live scan parser**

Add to `scripts/xiaogu_db_backfill.py`:

```python
def _parse_live_scan(scan_dir: Path) -> list:
    """Parse data/live_scan/ scored JSONL files."""
    results = []
    if not scan_dir.exists():
        return results

    for date_dir in sorted(scan_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        # Find the base scan dir (not the cloak variants)
        base_scan = date_dir / 'eastmoney_web_tabs_scan_v0_1'
        if not base_scan.exists():
            continue
        scored_file = base_scan / 'eastmoney_web_tabs_scored.jsonl'
        if not scored_file.exists():
            continue

        candidates = []
        with open(scored_file) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        candidates.append(rec)
                except json.JSONDecodeError:
                    continue

        results.append({
            'date': date_dir.name,
            'candidates': candidates,
        })

    return results


def backfill_from_live_scan() -> int:
    """Extract scored candidates from data/live_scan/."""
    scan_dir = ROOT / 'data' / 'live_scan'
    if not scan_dir.exists():
        print(f"Live scan dir not found: {scan_dir}")
        return 0

    parsed = _parse_live_scan(scan_dir)
    count = 0

    for day in parsed:
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            code = cand.get('code', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=False,
                    decision='CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=cand.get('market_regime', ''),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=cand,
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting scan candidate {trade_date} {code}: {e}")

    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: implement live scan scored backfill"
```

---

### Task 5: Implement Factors Parquet Backfill

**Covers:** Extract candidates from factors parquet files

**Files:**
- Modify: `scripts/xiaogu_db_backfill.py` (backfill_from_factors function)
- Modify: `tests/test_db_backfill.py`

**Interfaces:**
- Consumes: `data/factors/{YYYYMMDD}.parquet` (6 dates)
- Produces: Inserts into `daily_candidates` table

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_backfill.py - add
def test_factors_backfill_dry_run(tmp_path):
    """Verify factors parquet parser extracts candidates."""
    import pandas as pd
    factors_dir = tmp_path / "factors"
    factors_dir.mkdir()
    df = pd.DataFrame({
        'trade_date': ['2026-06-10', '2026-06-10'],
        'code': ['601816', '600210'],
        'symbol': ['601816', '600210'],
        'name': ['京沪高铁', '紫江企业'],
        'score': [67.96, 82.0],
        'final_score': [67.96, 82.0],
        'pct_chg': [1.77, 2.1],
        'close': [4.61, 8.5],
        'amount': [1e9, 5e8],
        'volume_ratio': [1.26, 1.5],
        'net_inflow_main': [2.3e7, 1e7],
        'close_position_score': [0.9, 0.7],
        'sector_opportunity_score': [0.0, 0.5],
        'decision': ['CANDIDATE', 'CANDIDATE'],
    })
    df.to_parquet(factors_dir / "20260610.parquet")

    from scripts.xiaogu_db_backfill import _parse_factors
    parsed = _parse_factors(factors_dir)
    assert len(parsed) == 1
    assert len(parsed[0]['candidates']) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py::test_factors_backfill_dry_run -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement factors parser**

Add to `scripts/xiaogu_db_backfill.py`:

```python
def _parse_factors(factors_dir: Path) -> list:
    """Parse data/factors/*.parquet files."""
    results = []
    if not factors_dir.exists():
        return results

    try:
        import pandas as pd
    except ImportError:
        print("pandas not available, skipping factors")
        return results

    for pq_file in sorted(factors_dir.glob('*.parquet')):
        try:
            df = pd.read_parquet(pq_file)
        except Exception as e:
            print(f"  Error reading {pq_file}: {e}")
            continue

        # Extract date from filename (YYYYMMDD.parquet)
        date_str = pq_file.stem
        if len(date_str) == 8:
            trade_date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        else:
            continue

        candidates = []
        for _, row in df.iterrows():
            candidates.append({
                'code': str(row.get('code', '')),
                'symbol': str(row.get('symbol', row.get('code', ''))),
                'name': str(row.get('name', '')),
                'rank': None,
                'final_score': row.get('final_score') or row.get('score'),
                'score': row.get('score'),
                'signal_pct': row.get('pct_chg'),
                'close_position_score': row.get('close_position_score'),
                'fund_flow_momentum': row.get('net_inflow_main'),
                'sector_catalyst_score': row.get('sector_opportunity_score'),
                'early_opportunity_score': row.get('kline_language_score'),
                'topic_propagation_score': row.get('theme_strength_score'),
                'market_regime': '',
                'pct_chg': row.get('pct_chg'),
                'close': row.get('close'),
                'amount': row.get('amount'),
                'volume_ratio': row.get('volume_ratio'),
                'decision': row.get('decision', 'CANDIDATE'),
            })

        results.append({
            'date': trade_date_str,
            'candidates': candidates,
        })

    return results


def backfill_from_factors() -> int:
    """Extract candidates from data/factors/*.parquet."""
    factors_dir = ROOT / 'data' / 'factors'
    if not factors_dir.exists():
        print(f"Factors dir not found: {factors_dir}")
        return 0

    parsed = _parse_factors(factors_dir)
    count = 0

    for day in parsed:
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            code = cand.get('code', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score'),
                    is_official_pick=False,
                    decision=cand.get('decision', 'CANDIDATE'),
                    open_price=None,
                    close_price=cand.get('close'),
                    high_price=None,
                    low_price=None,
                    volume=None,
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=None,
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=cand.get('market_regime', ''),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=cand,
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting factor candidate {trade_date} {code}: {e}")

    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: implement factors parquet backfill"
```

---

### Task 6: Run Full Backfill and Verify

**Covers:** Execute the backfill against the real database and verify completeness

**Files:**
- Modify: `scripts/xiaogu_db_backfill.py` (if any bugs found)

**Interfaces:**
- Consumes: All data sources listed above
- Produces: Complete database with all historical data

- [ ] **Step 1: Run the backfill script**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 scripts/xiaogu_db_backfill.py`
Expected: All sources report record counts, no errors

- [ ] **Step 2: Verify database completeness**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -c "
from xiaogu_db import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Check date range in daily_candidates
    result = conn.execute(text('''
        SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date, 
               COUNT(DISTINCT trade_date) as date_count, COUNT(*) as total
        FROM daily_candidates
    '''))
    row = result.fetchone()
    print(f'daily_candidates: {row[0]} to {row[1]}, {row[2]} dates, {row[3]} records')
    
    # Check date range in picks
    result = conn.execute(text('''
        SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date,
               COUNT(DISTINCT trade_date) as date_count, COUNT(*) as total
        FROM picks
    '''))
    row = result.fetchone()
    print(f'picks: {row[0]} to {row[1]}, {row[2]} dates, {row[3]} records')
    
    # Check date range in returns
    result = conn.execute(text('''
        SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date,
               COUNT(DISTINCT trade_date) as date_count, COUNT(*) as total
        FROM returns
    '''))
    row = result.fetchone()
    print(f'returns: {row[0]} to {row[1]}, {row[2]} dates, {row[3]} records')
    
    # Check top 10 per day
    result = conn.execute(text('''
        SELECT trade_date, COUNT(*) as total,
               COUNT(CASE WHEN rank IS NOT NULL AND rank <= 10 THEN 1 END) as top_10
        FROM daily_candidates
        GROUP BY trade_date
        ORDER BY trade_date
    '''))
    print('\\nCandidates per day:')
    for row in result:
        print(f'  {row[0]}: {row[1]} total, {row[2]} in top 10')
"
```
Expected: daily_candidates covers 5/18-7/03 with 10+ candidates per day

- [ ] **Step 3: Verify no duplicate issues**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -c "
from xiaogu_db import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Check for duplicates
    result = conn.execute(text('''
        SELECT trade_date, symbol, COUNT(*) as cnt
        FROM daily_candidates
        GROUP BY trade_date, symbol
        HAVING COUNT(*) > 1
        LIMIT 10
    '''))
    rows = result.fetchall()
    if rows:
        print('Duplicates found:')
        for row in rows:
            print(f'  {row[0]} {row[1]}: {row[2]} records')
    else:
        print('No duplicates found')
"
```
Expected: No duplicates

- [ ] **Step 4: Run all tests**

Run: `cd /workspace/hermes-workspaces/xiaogu && python3 -m pytest tests/test_db_backfill.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit final state**

```bash
git add scripts/xiaogu_db_backfill.py tests/test_db_backfill.py
git commit -m "feat: complete DB backfill from all historical data sources"
```
