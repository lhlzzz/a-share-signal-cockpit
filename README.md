# Xiaogu Capital-Behavior Repricing System

Paper-only A-share capture, measurement, 5D profit-window alpha, and portfolio-decision system. It does not place trades and is not investment advice.

## Run

```bash
pip install -r requirements.txt
```

Xiaogu uses the PostgreSQL service configured by `DATABASE_URL` (default:
`postgresql://xiaogu:xiaogu@127.0.0.1:5432/xiaogu`). Database lifecycle is
managed outside this repository.

Start the query API with `bash start_api.sh`.

```bash
python3 scrapy_scanner/runner_v2.py
python3 xiaogu_forward_runner.py --date $(date +%Y-%m-%d) --mode PRODUCTION
python3 xiaogu_scheduler.py
```

`--snapshot-json` is blocked in PRODUCTION. Production reads DB-verified trusted canonical snapshots and uses the actual production decision clock, not `source_time`. Replay may use a historical clock. `persisted` means PostgreSQL verification, not a local file flag. JSONL is audit only.

## Architecture

```
Eastmoney → Canonical Snapshot → Cheap Eligibility → Candidate Universe
→ Feature Engine → Research Context → Core Alpha → Portfolio Decision
→ Recorder → PostgreSQL → 5D Outcome → Position Review → Obsidian Memory
```

- Scanner: `scrapy_scanner/runner_v2.py` captures raw market reality only.
- Canonical: `xiaogu_forward_snapshot.validate_and_build_canonical_snapshot()` is the only trusted-snapshot builder.
- Eligibility: `xiaogu_forward_eligibility.py` checks operational constraints only. It does not score, rank, or form a capital thesis.
- Features: `xiaogu_forward_features.py` measures BUSINESS, FUTURE_DEMAND, CAPITAL, SUPPLY, PRICING_GAP, REFLEXIVITY, MARKET, RISK, and EXECUTION.
- Research: Serenity, Buffett, UZI, and Contradiction supply context only.
- Alpha owner: `xiaogu_core_alpha.build_core_alpha()`.
- Decision owner: `xiaogu_portfolio_decision.evaluate_candidate_bundle()`.
- Production runner: `xiaogu_forward_runner.run_production_decision()`.
- Recorder: `xiaogu_forward_paper_recorder_v0_1.py` writes PostgreSQL first, then JSONL audit. Obsidian is memory only.
- Outcomes: `xiaogu_forward_result_filler_v0_1.py --pending` appends T+1..T+5 daily-bar approximations, not executable fills.
- Position state lives in PostgreSQL. JSONL is an audit artifact. Obsidian is memory only.

The sole alpha target is `PROFIT_WINDOW_5D`. Maximum holding is 5 trading days. T+5 closes the trade. `WATCH` and `READY` are analysis states. `PAPER_SIGNAL` is a separate observational signal persisted in the existing `picks` table with `PAPER_OPEN/PAPER_CLOSED` and `PAPER_FLAT/PAPER_LONG`; it never becomes a real `BUY` or real `LONG`. Production BUY is hard-blocked in the current paper-observation mode.

`snapshot_id` is the immutable canonical snapshot identity. `lineage_id` is one scan/lineage and may cover many symbols. Schema migration failure raises, health fails, and production is blocked. Historical rows with missing `decision_id` stay `UNRESOLVED` and are not rewritten. Current alpha status is `DATA_INSUFFICIENT`; the rebuilt 5D ground truth covers 92.61% of historical decisions, so production BUY stays blocked. The only remaining production-alpha candidate is `price_strength`; other tested features are research-only or have no stable OOS increment.

## API

- `GET /health`
- `GET /state`
- `GET /decision`
- `GET /trades`
- `GET /trade/{decision_id}`
- `GET /memory`
- `GET /patterns`
- `GET /paper/signals`
- `GET /paper/signal/{decision_id}`
- `GET /paper/performance`
- `GET /paper/open`
- `GET /paper/history`

All paper endpoints read PostgreSQL. `data/research/paper_production_5d_dataset.json` is a research artifact and is not a validation dataset. The frozen production alpha is `price_strength`; capital, supply, repricing, and future-buyer fields remain `RESEARCH_ONLY` overlay data.

## Validation

```bash
pytest tests/ -x -q
python -m compileall -q .
python scripts/xiaogu_daily_health_check.py
```
