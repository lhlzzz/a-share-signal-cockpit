# A-share Signal Cockpit

Research-only A-share signal system for data collection, multi-factor scoring, candidate selection, forward validation, and operator-facing APIs. It does not place trades and is not investment advice.

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

`direct Eastmoney API scanner -> runner -> paper recorder -> Eastmoney T+1 filler -> PostgreSQL -> FastAPI`

- Scanner: `scrapy_scanner/runner_v2.py`
- Decision runner: `xiaogu_forward_d1_1450_runner_v0_1.py`
- Scheduler: `xiaogu_scheduler.py`
- API: `xiaogu_api.py`
- Persistence: `xiaogu_db.py`

Production has one chain and one transport: direct Eastmoney API data only.
Legacy browser scanners, alternate market-data providers, and
candidate promotion fallbacks are not production inputs.

Optional research adapters are intentionally not vendored. A missing adapter
is recorded as unavailable and cannot create a `PAPER_PICK`.

## API

- `GET /health`
- `GET /daily-candidates/{trade_date}`
- `GET /picks/{trade_date}/summary`
- `GET /scan-sessions`
- `GET /stats/overview`
- `GET /explain/{trade_date}/{symbol}`
- `GET /demo/cockpit`

## Validation

```bash
pytest tests/ -x -q
python -m compileall -q scrapy_scanner scripts xiaogu_*.py
```

## Evidence and Limits

Forward validation and backtest data are stored in PostgreSQL and Obsidian; they are not a performance guarantee.
