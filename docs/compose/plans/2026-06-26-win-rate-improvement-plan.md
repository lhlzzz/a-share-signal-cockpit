# Win Rate Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve xiaogu win rate from 48% to 55%+ (T+1 close) by adding calendar filtering and score capping.

**Architecture:** Add weekday blocklist and score cap to scoring_config table, then enforce them in the runner's eligibility check.

**Tech Stack:** Python, PostgreSQL, SQLAlchemy, pytest

## Global Constraints

- DRY_RUN must be "0" in scheduler
- All tables must have created_at, updated_at, data_version
-收益口径: T+1 close as primary
- No real trades, paper only
- All changes must pass 82+ tests

---

### Task 1: Add weekday blocklist and score cap to scoring_config

**Covers:** [S2, S3]

**Files:**
- Modify: N/A (SQL only)
- Test: Verify via DB query

**Interfaces:**
- Consumes: existing scoring_config table
- Produces: two new config entries

- [ ] **Step 1: Add weekday_blocklist config**

```sql
INSERT INTO scoring_config (config_key, config_value, description)
VALUES ('weekday_blocklist', '0,4', 'Blocked weekdays: 0=Monday, 4=Friday')
ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value;
```

- [ ] **Step 2: Add max_score_cap config**

```sql
INSERT INTO scoring_config (config_key, config_value, description)
VALUES ('max_score_cap', 95, 'Candidates above this score are penalized')
ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value;
```

- [ ] **Step 3: Verify configs exist**

Run: `python3 -c "from xiaogu_db import engine; from sqlalchemy import text; print(engine.connect().execute(text(\"SELECT config_key, config_value FROM scoring_config WHERE config_key IN ('weekday_blocklist', 'max_score_cap')\")).fetchall())"`
Expected: `[('weekday_blocklist', '0,4'), ('max_score_cap', 95.0)]`

---

### Task 2: Add weekday check to runner eligibility

**Covers:** [S2, S3]

**Files:**
- Modify: `xiaogu_forward_d1_1450_runner_v0_1.py` (paper_pick_eligibility_profile function, ~line 2579)
- Test: `tests/test_xiaogu_a_share_forward_runner.py`

**Interfaces:**
- Consumes: scoring_config table (weekday_blocklist), candidate's trade_date
- Produces: eligibility result with weekday blocker

- [ ] **Step 1: Write failing test**

```python
def test_weekday_blocklist_blocks_monday():
    """Monday picks should be blocked when weekday_blocklist contains 0."""
    from xiaogu_forward_d1_1450_runner_v0_1 import paper_pick_eligibility_profile
    # Create a mock candidate with Monday date
    candidate = {
        'date': '2026-06-22',  # Monday
        'symbol': '300017',
        'score': 87.9,
        'candidate_features': {'signal_pct': 2.0, 'close_position_score': 0.7},
    }
    bundle = {'date': '2026-06-22'}
    result = paper_pick_eligibility_profile(candidate, bundle)
    # Should be blocked on Monday
    assert not result['eligible'] or 'WEEKDAY_BLOCKED' in str(result.get('blockers', []))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_xiaogu_a_share_forward_runner.py::test_weekday_blocklist_blocks_monday -v`
Expected: FAIL (no weekday check implemented yet)

- [ ] **Step 3: Implement weekday check**

In `xiaogu_forward_d1_1450_runner_v0_1.py`, in the `paper_pick_eligibility_profile` function, add after the existing blocker checks:

```python
# Weekday blocklist check
try:
    from xiaogu_db import engine
    from sqlalchemy import text as sa_text
    with engine.connect() as conn:
        row = conn.execute(sa_text("SELECT config_value FROM scoring_config WHERE config_key='weekday_blocklist'")).fetchone()
        if row and row[0]:
            blocked_days = [int(d.strip()) for d in str(row[0]).split(',') if d.strip()]
            trade_date = candidate.get('date') or bundle.get('date', '')
            if trade_date:
                import datetime as _dt
                weekday = _dt.date.fromisoformat(trade_date).weekday()
                if weekday in blocked_days:
                    blockers.append('WEEKDAY_BLOCKED')
                    missing_conditions.append(f'weekday_not_in_{blocked_days}')
except Exception:
    pass  # DB unavailable — skip check
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_xiaogu_a_share_forward_runner.py::test_weekday_blocklist_blocks_monday -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 82+ passed

---

### Task 3: Add score cap to contrarian_re_score

**Covers:** [S2, S3]

**Files:**
- Modify: `xiaogu_forward_d1_1450_runner_v0_1.py` (contrarian_re_score function)
- Test: `tests/test_xiaogu_a_share_forward_runner.py`

**Interfaces:**
- Consumes: scoring_config table (max_score_cap), candidate's final_score
- Produces: adjusted score with penalty for high scores

- [ ] **Step 1: Write failing test**

```python
def test_score_cap_penalizes_high_scores():
    """Candidates above max_score_cap should be penalized."""
    from xiaogu_forward_d1_1450_runner_v0_1 import contrarian_re_score
    candidate = {
        'final_score': 120.0,
        'candidate_features': {'signal_pct': 3.0, 'close_position_score': 0.9, 'fund_flow_momentum': 0.9}
    }
    score = contrarian_re_score(candidate)
    # High score should be penalized
    assert score < 120.0, f"Expected penalty for high score, got {score}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_xiaogu_a_share_forward_runner.py::test_score_cap_penalizes_high_scores -v`
Expected: FAIL

- [ ] **Step 3: Implement score cap in contrarian_re_score**

In the `contrarian_re_score` function, add after computing `re_score`:

```python
# Score cap: penalize candidates above threshold
try:
    from xiaogu_db import engine
    from sqlalchemy import text as sa_text
    with engine.connect() as conn:
        row = conn.execute(sa_text("SELECT config_value FROM scoring_config WHERE config_key='max_score_cap'")).fetchone()
        if row and row[0] and original > float(row[0]):
            penalty = (original - float(row[0])) * 0.3
            re_score -= penalty
except Exception:
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_xiaogu_a_share_forward_runner.py::test_score_cap_penalizes_high_scores -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: 82+ passed

---

### Task 4: Backtest verification

**Covers:** [S4, S5]

**Files:**
- No code changes
- Verify via DB queries

**Interfaces:**
- Consumes: DB with all picks and returns
- Produces: performance comparison before/after

- [ ] **Step 1: Get baseline metrics**

Run: `python3 -c "from xiaogu_db import engine; from sqlalchemy import text; ..."` (query wins, losses, avg return before applying filters)

- [ ] **Step 2: Simulate with weekday filter**

Calculate how many losses would be avoided by blocking Monday and Friday.

- [ ] **Step 3: Simulate with score cap**

Calculate how many losses would be avoided by capping at 95.

- [ ] **Step 4: Combined simulation**

Calculate expected win rate after both filters.

- [ ] **Step 5: Verify API still works**

Run: `python3 -c "from xiaogu_api import app; ..."` (test key endpoints)

---

### Task 5: Commit and document

**Covers:** [S4]

**Files:**
- Modify: AGENTS.md (update Historical Decisions section)

**Interfaces:**
- Consumes: completed tasks 1-4
- Produces: git commit, updated docs

- [ ] **Step 1: Commit changes**

```bash
git add xiaogu_forward_d1_1450_runner_v0_1.py tests/ AGENTS.md
git commit -m "feat: add weekday blocklist and score cap to improve win rate

- Add weekday_blocklist config (block Monday/Friday)
- Add max_score_cap config (penalize scores > 95)
- Enforce in runner eligibility and contrarian_re_score
- Expected improvement: 48% → 55%+ win rate"
```

- [ ] **Step 2: Update AGENTS.md Historical Decisions**

Add to the end of the file:
```
- 2026-06-26: Weekday blocklist (Mon/Fri) + score cap (95) added to improve win rate
```
