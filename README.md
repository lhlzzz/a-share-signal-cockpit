# Xiaogu Repricing Research System

Research-only A-share market-data, repricing-readiness, and paper-ledger system. It does not place trades and is not investment advice.

## Demo

```bash
pip install -r requirements.txt
```

Xiaogu uses the local PostgreSQL service configured by `DATABASE_URL`
(default: `postgresql://xiaogu:xiaogu@127.0.0.1:5432/xiaogu`). Database
lifecycle is managed outside this repository.

Start the read API with `bash start_api.sh`.
Open `http://localhost:8000/dashboard/` for the operator frontend.
The frontend reads `http://localhost:8000/api/os/front-data`.

Run the scheduler separately when the daily paper-trading workflow is required:

```bash
python3 xiaogu_scheduler.py
```

## Architecture

`Eastmoney -> canonical snapshot -> price-formation features -> research context -> Core Alpha -> portfolio decision -> paper ledger -> T+1..T+5 profit window`

- Scanner: `scrapy_scanner/runner_v2.py` captures raw market reality only.
- Decision runner: `xiaogu_forward_runner.py` calls the sole decision owner, `xiaogu_portfolio_decision.evaluate_candidate_bundle()`.
- Features: `xiaogu_forward_features.py` measures BUSINESS, FUTURE_DEMAND, CAPITAL, SUPPLY, PRICING_GAP, REFLEXIVITY, MARKET, RISK, and EXECUTION.
- Outcomes: `xiaogu_forward_result_filler_v0_1.py --pending` appends post-decision T+1..T+5 OHLC and execution-aware profit-window outcomes.
- Scheduler: `xiaogu_scheduler.py`
- API: `xiaogu_api.py`
- Persistence: `xiaogu_db.py`

Production decisions are only `WATCH`, `READY`, `BUY`, `HOLD`, `REDUCE`, or `SELL`.
The sole formal alpha target is `PROFIT_WINDOW_5D`; `T+1` is an evaluation day inside the five-day window, not a separate alpha target.

Optional research adapters are intentionally not vendored. A missing adapter
is recorded as unavailable and blocks a new `BUY` decision.

## API

- `GET /health`
- `GET /dashboard`
- `GET /picks`
- `GET /portfolio`
- `GET /repricing-state`
- `GET /alpha`
- `GET /returns`

## Validation

```bash
pytest tests/ -x -q
python -m compileall -q scrapy_scanner scripts xiaogu_*.py
```

## Evidence and Limits

Forward validation and backtest data are stored in PostgreSQL and Obsidian; they are not a performance guarantee.
