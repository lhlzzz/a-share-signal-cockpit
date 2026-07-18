# Social Sentiment Integration Implementation Plan

> **For agentic workers:** Use compose:execute to implement this plan task-by-task.

**Goal:** Add social media sentiment signals from Eastmoney stock forums to improve stock selection.

**Architecture:** Scanner collects stock forum data → New signal dimension → Runner uses in scoring.

**Tech Stack:** Python, SQLAlchemy, pytest

## Global Constraints

- Free data sources only (Eastmoney forums, no paid APIs)
- Rate limit: max 100 forum pages per scan run
- Cache forum data for 4 hours to avoid repeated fetches
- All new signals must be explainable

---

### Task 1: Add social_sentiment signal dimension to DB

**Covers:** [S4]

**Files:**
- N/A (SQL only)

- [ ] **Step 1: Verify signals table can store new signal type**

Run: `python3 -c "from xiaogu_db import engine; from sqlalchemy import text; print(engine.connect().execute(text(\"SELECT DISTINCT signal_key FROM signals\")).fetchall())"`
Expected: existing signal keys listed

---

### Task 2: Create forum data collector

**Covers:** [S4]

**Files:**
- Create: `xiaogu_social_sentiment.py`
- Test: `tests/test_social_sentiment.py`

**Interfaces:**
- Consumes: stock codes from scanner output
- Produces: sentiment scores per stock

- [ ] **Step 1: Write failing test**

```python
def test_fetch_stock_forum_returns_sentiment():
    from xiaogu_social_sentiment import fetch_stock_forum_sentiment
    result = fetch_stock_forum_sentiment('300059')  # 东方财富
    assert isinstance(result, dict)
    assert 'sentiment_score' in result
    assert 'post_count' in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_social_sentiment.py -v`
Expected: FAIL

- [ ] **Step 3: Implement forum data collector**

Create `xiaogu_social_sentiment.py` with:
- `fetch_stock_forum_sentiment(code)` — fetch from Eastmoney stock forum
- Parse post titles and content for sentiment
- Return sentiment_score (0-1), post_count, positive_ratio

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_social_sentiment.py -v`
Expected: PASS

---

### Task 3: Integrate sentiment into runner scoring

**Covers:** [S4]

**Files:**
- Modify: `xiaogu_forward_d1_1450_runner_v0_1.py` (contrarian_re_score)
- Test: `tests/test_xiaogu_a_share_forward_runner.py`

**Interfaces:**
- Consumes: social_sentiment signal from DB
- Produces: adjusted candidate score

- [ ] **Step 1: Add sentiment to contrarian_re_score**

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -x -q`
Expected: 82+ passed

---

### Task 4: Add sentiment to daily_candidates

**Covers:** [S4]

**Files:**
- Modify: runner's daily_candidates insertion logic

- [ ] **Step 1: Store sentiment in daily_candidates.raw_json**

- [ ] **Step 2: Verify via API**

Run: `curl http://localhost:8000/daily-candidates/2026-06-25`
Expected: sentiment data in response
