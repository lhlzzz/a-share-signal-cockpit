# A-share Signal Cockpit

Research-only A-share signal system for data collection, multi-factor scoring, candidate selection, forward validation, and operator-facing APIs. It does not place trades and is not investment advice.

## Demo

```bash
pip install -r requirements.txt
DEMO_MODE=1 uvicorn xiaogu_api:app --reload
```

Open `http://127.0.0.1:8000/demo/cockpit` for deterministic sample data, or open `public/index.html` as a static case study.

## Architecture

`scanner -> runner -> paper recorder -> return filler -> PostgreSQL -> FastAPI`

- Scanner: `scrapy_scanner/runner_v2.py`
- Decision runner: `xiaogu_forward_d1_1450_runner_v0_1.py`
- Scheduler: `xiaogu_scheduler.py`
- API: `xiaogu_api.py`
- Persistence: `xiaogu_db.py`

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
pytest tests/test_db_backfill.py -q
python -m py_compile xiaogu_api.py xiaogu_db.py
```

## Evidence and Limits

Forward validation and backtest files are research evidence, not a performance guarantee. The publication audit in the workspace root identifies large historical artifacts and local state that must be reviewed before any public push.

