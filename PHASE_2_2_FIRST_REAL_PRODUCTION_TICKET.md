# Phase 2.2 First Real Production Ticket

This is not Xiaogu 4.0. Production Alpha, Target, Selection, Decision, Paper,
Outcome, PostgreSQL schema v6, and Obsidian memory were not replaced.

## 1. Execution

- command: `bash daily_pipeline.sh`
- timestamp: `2026-09-04T18:30:47Z` start, scanner `2026-09-05T02:30:50+08:00`
- git HEAD at inspection: `39c11cdc9cb87b178b251c0e6f7179d57c9c390a`
- worktree at inspection: `main` aligned with `origin/main`; only unrelated `.plan-enforcer/statusline-state.json` was dirty and was restored
- production clock: `xiaogu_forward_snapshot.production_now()` = `datetime.now(timezone.utc)`
- local/CN date at run: `2026-09-05` Saturday
- pipeline exit: `0` (calendar fail-closed, not a fabricated ticket)

`daily_pipeline.sh` used `DATE=$(date +%F)` = `2026-09-05`, not `2026-09-04`.
That is the current production command. It was not rewritten.

Scanner output:

```
production_scan = BLOCKED
block_reason = NON_TRADING_DAY
database_persistence = SKIPPED / PRODUCTION_SCAN_BLOCKED
run_id = none
```

Runner output:

```
date = 2026-09-05
mode = PRODUCTION
scan_status = SCAN_BLOCKED
scan_reason = NON_TRADING_DAY
count = 0
paper_observations = []
```

Filler output:

```
as_of = 2026-09-05
exit_reason = NON_TRADING_DAY
filled = 0
t1_t5_persisted = false
```

## 2. Data Freshness

- source_time (this run): `2026-09-05T02:30:50+08:00` (calendar blocked before quotes)
- production_now at post-run check: `2026-09-04T18:31:45.982084+00:00` (`2026-09-05T02:31:45+08:00`)
- freshness_age: not computed; there is no trusted T-day quote `source_time`
- MAX_STALENESS: 120 minutes
- freshness result: **NOT_APPLICABLE** — calendar blocked before a production observation

2026-09-04 morning scan `source_time = 2026-09-04T09:25:00+08:00`. Against the current clock that age is far beyond 120 minutes. Reusing it would be `STALE_DATA`. It was not reused.

## 3. Coverage

| Field | 2026-09-04 morning | 2026-09-04 afternoon | 2026-09-05 pipeline |
|-------|--------------------|----------------------|---------------------|
| universe | stock_all_a observed 5909 | empty directory | not captured |
| production_scan | BLOCKED | no `scan_summary.json` | BLOCKED |
| block_reason | `CRITICAL_SOURCE_INCOMPLETE:stock_all_a:5548` | missing scan | `NON_TRADING_DAY` |
| MAIN_BOARD count | 0 (scan never passed L0 completeness) | 0 | 0 |
| eligible count | 0 | 0 | 0 |
| L3 count | 0 | 0 | 0 |
| fresh count | 0 | 0 | 0 |
| stale count | 0 (never reached workers) | 0 | 0 |
| run_id | none (persist SKIPPED) | none | none |

`CRITICAL_SOURCE_INCOMPLETE:stock_all_a:5548` is still the 2026-09-04 morning fact.
Fetch itself reported 5909 rows / 60 pages / `status=PASS`, but every ACTIVE_REQUIRED
row (5548) failed completeness: volume/amount/main_net_inflow were `"-"` at 09:25,
before the continuous session. Scanner fail-closed. That blocker was **not**
repaired by weakening completeness.

## 4. Decision

- decision count: 0
- decision status: none — runner never entered `evaluate_candidate_bundle`
- production gates: not evaluated on live 2026-09-04 candidates
- BUY remains `BLOCKED` (`PRODUCTION_BUY_BLOCKED:PAPER_OBSERVATION_ONLY`)
- LIVE remains `DISABLED` (`paper_only=true`, `live_order=false`, `auto_order=false`, `broker_connected=false`)

## 5. Selection

- Top3 count: 0
- Top1 count: 0
- selected ids: none
- `attach_top_paper_observations()` was not reached on the live 2026-09-04 path

NO OFFICIAL TICKET. Top1 was not invented.

## 6. Official Observation

- paper_signal_id: none
- decision_id: none
- production_run_id: none
- snapshot_id: none
- lineage_id: none (2026-09-04 morning lineage `2632e16a...` is a blocked scan, not a production observation)
- alpha: `profit_window_alpha_5d_v4` (unchanged owner; no live observation used it)
- target: `opportunity_5d` (unchanged)
- rank: none
- top1_flag: none
- top3_flag: none

`fetch_official_paper_observations()` live count: **0**.

The 6797 2026-09-03 paper rows remain `alpha_name=price_strength`, rank/top flags
absent, and are not official.

## 7. Persistence

- production_run status for 2026-09-04: **no row**
- `DECISIONS_PERSISTED` count: **0**
- snapshots for 2026-09-04: **0**
- PostgreSQL verification: connected; schema v6; calendar TRUE for 2026-09-04 and 2026-09-07, FALSE for 2026-09-05
- official fetch verification: empty
- `persist_production_facts()` was not called on the live path
- coverage JSONB merge owner remains `_write_production_run_coverage()`; no live 2026-09-04 scoring snapshot was overwritten because no run existed

## 8. T+1 Plan

T = 2026-09-04  
T+1 = 2026-09-07  
T+2 = 2026-09-08  
T+3 = 2026-09-09  
T+4 = 2026-09-10  
T+5 = 2026-09-11  

Resolved by `xiaogu_db.resolve_t_plus_n()`, not weekday arithmetic.

9/7 T+1 **cannot** be verified yet: there is no official 2026-09-04 paper observation.
Filler `--due` persists only when horizon == 5 because `returns` identity is immutable.
Even after a real 9/4 ticket, 9/7 would still be observation-time T+1 status, not a
final 5D result.

## 9. Truth

REAL_PRODUCTION_TICKET = NO

REAL_OOS_EVIDENCE = NO

NO_OFFICIAL_PRODUCTION_TICKET

LIVE_PRODUCTION_EXECUTED_ON_CURRENT_COMMAND = YES  
LIVE_2026-09-04_PRODUCTION_OBSERVATION = NO

The first paper observation, if it had existed, would still not validate Alpha.
OOS still needs chronological accumulation after official Top1/Top3 settle through T+5.

## Blockers (honest)

1. **2026-09-04 morning scan incomplete.** `CRITICAL_SOURCE_INCOMPLETE:stock_all_a:5548` at 09:25+08, before continuous quotes. Persist skipped. No `production_run_id`.
2. **2026-09-04 afternoon scan missing.** `data/live_scan/2026-09-04/eastmoney_scan_afternoon/` is empty. No `scan_summary.json`.
3. **Current clock is Saturday 2026-09-05.** Calendar owner returns FALSE. `daily_pipeline.sh` therefore blocks with `NON_TRADING_DAY`.
4. **Freshness.** Any reuse of the 09:25 9/4 source against `production_now()` is `STALE_DATA`. Freshness policy was not loosened.

Code path itself, given a complete trusted T-day scan inside 120 minutes, still can:

scan PASS → canonical persist → `evaluate_candidate_bundle` → `attach_top_paper_observations` → `persist_production_facts` → `DECISIONS_PERSISTED` → official fetch.

That path was proven in tests, not in live 2026-09-04 data.

## Tests

```
python -m pytest tests/ -q --tb=line
372 passed

python -m compileall -q .
COMPILE_EXIT:0
```

New tests (do not insert a live rank=1 row):

- `test_candidate_level_stale_does_not_swallow_fresh_selection`
- `test_phase22_official_observation_production_path_provenance`

They prove Decision → Selection → `persist_production_facts` → `DECISIONS_PERSISTED` → official fetch, and that rank-only rows are not official. They are not a 2026-09-04 live ticket.

## Frozen owners (unchanged)

| Role | Owner |
|------|--------|
| Target | `opportunity_5d` |
| Alpha | `profit_window_alpha_5d_v4` / `xiaogu_core_alpha.build_core_alpha` |
| Decision | `xiaogu_portfolio_decision.evaluate_candidate_bundle` |
| Gate | `xiaogu_portfolio_decision.evaluate_production_gates` |
| Selection | `xiaogu_portfolio_decision.attach_top_paper_observations` |
| Persist | `xiaogu_db.persist_production_facts` |
| Clock | `xiaogu_forward_runner.main()` batch `decision_clock` via `production_now()` |
| Calendar | `xiaogu_db.py` |
| BUY | BLOCKED |
| LIVE | DISABLED |
