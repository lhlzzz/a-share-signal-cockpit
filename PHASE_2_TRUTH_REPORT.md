# PHASE 2 TRUTH REPORT

Date: 2026-09-04

This is not Xiaogu 4.0. Production owners were not replaced. Phase 2 adds an
observation / OOS query layer on the existing PostgreSQL schema v6.

## Honest sample status

**NO_REAL_OOS_EVIDENCE_YET**

PostgreSQL currently contains:

| Fact | Count | Meaning |
|------|-------|---------|
| `production_runs` | 27 | Scan metadata exists |
| `production_runs` with `DECISIONS_PERSISTED` | 0 | No official production decision persist |
| `paper_observations` | 6797 | Same-day dump on 2026-09-03 |
| Official Top1 / Top3 (`rank` / `top1_flag` / `top3_flag`) | 0 | Unranked dump is not OOS evidence |
| `returns.payload.days` nested T+1..T+5 | 0 | Horizon facts not yet written in v6 nested form |

The 6797 paper rows have `rank = null`, no `top1_flag`, no `top3_flag`, and
`alpha_name = price_strength`. They are not official Selection observations.
Latest production runs on 2026-09-03 are `SNAPSHOT_CAPTURED` only. The
2026-09-03 operator summary is `STALE_DATA` / `NO_PAPER_OBSERVATION`.

Do not treat historical replay datasets, the unranked dump, or backtest
artifacts as forward OOS.

## 20 answers

1. Unique Production Alpha: `profit_window_alpha_5d_v4` (`xiaogu_core_alpha.build_core_alpha`).
2. Unique Target: `opportunity_5d`.
3. Selection owner: `xiaogu_portfolio_decision.attach_top_paper_observations`.
4. Decision owner: `xiaogu_portfolio_decision.evaluate_candidate_bundle`.
5. Paper owner: `xiaogu_forward_paper_recorder_v0_1.py`.
6. Outcome owner: `xiaogu_forward_result_filler_v0_1.py` + `xiaogu_db.fetch_horizon_outcomes`.
7. Fact DB: PostgreSQL, schema `xiaogu_production_schema_v6`.
8. Semantic Memory: Obsidian via Memory Adapter. PostgreSQL remains fact owner.
9. First real official Observation in DB: **no**. Unranked 2026-09-03 dump is not official Top1/Top3.
10. T+1..T+5 complete tracking: **code path yes**, **live nested facts no**. `fetch_horizon_outcomes(decision_id)` always returns days 1..5 as `SETTLED` or `MISSING`. Current live `returns` rows have no nested `payload.days`.
11. OOS accumulation: **not started** for official observations. Chronological 60/20/20 + embargo ≥ 5 remain the only OOS split.
12. `sample_count`: **0** official settled Top1/Top3 observations.
13. Top1 hit rate: **None** (`NO_REAL_OOS_EVIDENCE_YET`).
14. Top3 hit rate: **None** (`NO_REAL_OOS_EVIDENCE_YET`).
15. Alpha status: **DATA_INSUFFICIENT** / reserved **EXPERIMENTAL**. Not QUALIFIED. Not REJECTED.
16. Future function: none added. Outcome remains after decision. PIT still uses `knowledge_available_at`, not `signal_time` / `created_at` as availability substitutes.
17. Second production owner: **no**. `xiaogu_alpha_truth.py` is observation-only CLI. OOS owner remains `xiaogu_horizon_evaluation.py`.
18. BUY: **BLOCKED**.
19. LIVE: **DISABLED**.
20. Largest unknown risk: the live daily path has not yet persisted one official Top1/Top3 paper observation with nested T+1..T+5. Until that happens, Alpha cannot be judged. Secondary risks: 2026-09-03 `STALE_DATA`; unranked paper dump must not leak into OOS; nested JSON outcomes remain queryable only through `fetch_horizon_outcomes`, not a per-horizon unique constraint.

## What Phase 2 added without changing owners

- Observation coverage is stored on existing `production_runs.scoring_config_snapshot` JSONB. No new table. No schema v7.
- Query functions: `fetch_production_run_coverage`, `fetch_paper_observation_ledger`, `fetch_official_paper_observations`.
- Official-only OOS: `evaluate_official_observations` in `xiaogu_horizon_evaluation.py`.
- Dashboard CLI: `xiaogu_alpha_truth.py`. It does not score, select, gate, BUY, or SELL.

Coverage fields now retrievable for a `production_run_id` after persist:

`scan_count`, `execution_universe_count`, `research_count`, `alpha_count`,
`decision_count`, `top3_count`, `top1_count`, `paper_count`, `system_fault`,
`publishable`.

A `paper_signal_id` ledger returns T date, symbol, decision_id, alpha/model,
selection_score, target, T+1..T+5, outcome status, settled_at, hit, MAE, MFE,
realized return. Missing days stay `MISSING`. `paper_signal_id != decision_id`.

## Frozen production contract

- ONE TARGET: `opportunity_5d`
- ONE ALPHA: `profit_window_alpha_5d_v4`
- ONE DECISION: `evaluate_candidate_bundle`
- ONE GATE: `evaluate_production_gates`
- ONE SELECTION: `attach_top_paper_observations`
- ONE PAPER / ONE OUTCOME / ONE PostgreSQL / ONE Obsidian memory
- ONE CLOCK: `main()` batch `decision_clock`
- EXECUTION UNIVERSE: MAIN_BOARD_ONLY
- BUY BLOCKED / LIVE DISABLED
- Mode remains PAPER_OBSERVATION, not LIVE_TRADING
- Permanent worker failure: ABSTAIN, no PARTIAL_OBSERVATION / PARTIAL_SELECTION
- Completed outcomes remain immutable (`OUTCOME_IDENTITY_CONFLICT`)
- Same `paper_signal_id` cannot overwrite another identity (`PAPER_OBSERVATION_IDENTITY_CONFLICT`)

## Tests / scan

```
python -m pytest tests/ -x -q --tb=line
367 passed

python -m pytest tests/test_single_system_convergence.py -q --tb=line
37 passed

python -m compileall -q .
COMPILE_EXIT:0
```

Live-owner static scan (`xiaogu_*.py` + scanner):

- `PARTIAL_OBSERVATION` / `PARTIAL_SELECTION` / `research_consumed`: none
- `PRICE_STRENGTH_OUT_OF_WINDOW` / `SIGNAL_PCT_MIN` / `SIGNAL_PCT_MAX`: none
- second selector / ranker / alpha / decision files: absent
- random split is not the production OOS split
- embargo ≥ 5 trading days remains

## Next real evidence, not next module

Wait for a normal trading day that:

1. Captures a trusted scan
2. Persists `DECISIONS_PERSISTED` with official Top1/Top3
3. Fills nested T+1..T+5
4. Settles T+5
5. Enters chronological OOS

Until then the only honest Alpha statement is:

**NO_REAL_OOS_EVIDENCE_YET**
