# Xiaogu Capital-Behavior Repricing System

Paper-only A-share market-data, capital-behavior, repricing, and outcome system. It does not place trades and is not investment advice.

## Run

```bash
pip install -r requirements.txt
```

Xiaogu uses the PostgreSQL service configured by `DATABASE_URL` (default:
`postgresql://xiaogu:xiaogu@127.0.0.1:5432/xiaogu`). Database lifecycle is
managed outside this repository.

Start the query API with `bash start_api.sh`.

Run the scheduler separately when the daily paper-trading workflow is required:

```bash
python3 xiaogu_scheduler.py
```

## Architecture

`Eastmoney -> canonical snapshot -> cheap eligibility -> candidate universe -> features -> research context -> Core Alpha -> portfolio decision -> recorder -> T+1..T+5 outcome -> position review -> memory`

- Scanner: `scrapy_scanner/runner_v2.py` captures raw market reality only.
- Decision runner: `xiaogu_forward_runner.py` calls the sole decision owner, `xiaogu_portfolio_decision.evaluate_candidate_bundle()`.
- Features: `xiaogu_forward_features.py` measures BUSINESS, FUTURE_DEMAND, CAPITAL, SUPPLY, PRICING_GAP, REFLEXIVITY, MARKET, RISK, and EXECUTION.
- Eligibility: `xiaogu_forward_eligibility.py` checks only objective market and operational prerequisites.
- Outcomes: `xiaogu_forward_result_filler_v0_1.py --pending` appends post-decision T+1..T+5 OHLC and execution-aware profit-window outcomes.
- Scheduler: `xiaogu_scheduler.py`
- API: `xiaogu_api.py`
- Persistence: `xiaogu_db.py`

The sole alpha target is `PROFIT_WINDOW_5D`; `T+1` is an evaluation day inside the five-day window, not a separate alpha target. `WATCH` and `READY` are analysis states. The recorder persists only `BUY`, `HOLD`, `REDUCE`, and `SELL`.

Optional research adapters are intentionally not vendored. A missing adapter
is recorded as unavailable and blocks a new `BUY` decision.

## API

- `GET /health`
- `GET /state`
- `GET /decision`
- `GET /trades`
- `GET /trade/{decision_id}`
- `GET /memory`
- `GET /patterns`

## Validation

```bash
pytest tests/ -x -q
python -m compileall -q .
python scripts/xiaogu_daily_health_check.py
```

## Evidence and Limits

Forward validation and backtest data are stored in PostgreSQL and Obsidian; they are not a performance guarantee.
