# PHASE 2.1 FIRST OBSERVATION AUDIT

Date: 2026-09-04

This is not Xiaogu 4.0. Production Alpha, Target, Selection, Decision, Paper,
Outcome, PostgreSQL schema v6, and Obsidian memory were not replaced.

## Honest sample status

**NO_REAL_OOS_EVIDENCE_YET**

**LIVE_PRODUCTION_NOT_EXECUTED**

Official settled Top1/Top3 `sample_count` remains **0**.

Do not treat the 2026-09-03 unranked 6797-row dump as official forward
observation. Those rows were not rewritten, not ranked, and not migrated into
OOS.

## Frozen owners (unchanged)

| Role | Owner |
|------|--------|
| Target | `opportunity_5d` |
| Alpha | `profit_window_alpha_5d_v4` / `xiaogu_core_alpha.build_core_alpha` |
| Decision | `xiaogu_portfolio_decision.evaluate_candidate_bundle` |
| Gate | `xiaogu_portfolio_decision.evaluate_production_gates` |
| Selection | `xiaogu_portfolio_decision.attach_top_paper_observations` |
| Paper | `xiaogu_forward_paper_recorder_v0_1.py` |
| Outcome | `xiaogu_forward_result_filler_v0_1.py` + `xiaogu_db.fetch_horizon_outcomes` |
| Fact DB | PostgreSQL schema `xiaogu_production_schema_v6` |
| Memory | Obsidian (semantic only) |
| Clock | `xiaogu_forward_runner.main()` batch `decision_clock` via `production_now()` |
| Execution universe | MAIN_BOARD_ONLY |
| BUY | BLOCKED |
| LIVE | DISABLED |
| Mode | PAPER_OBSERVATION |

## CURRENT_PRODUCTION_COMMAND

Recommended daily entry (`daily_pipeline.sh`):

```bash
bash daily_pipeline.sh
```

which runs:

```bash
python3 scripts/xiaogu_ensure_database.py
python3 scrapy_scanner/runner_v2.py --output-dir data/live_scan/${DATE}/eastmoney_scan
python3 xiaogu_forward_runner.py --date ${DATE} --scan-dir ${SCAN_DIR}
python3 xiaogu_forward_result_filler_v0_1.py --due --end-date ${DATE} --timeout-seconds 90
```

README also documents:

```bash
python3 xiaogu_forward_runner.py --date $(date +%Y-%m-%d) --mode PRODUCTION
```

Production mode is the runner default. `--snapshot-json` cannot enter
PRODUCTION. Production loads DB-verified trusted snapshots. `--scan-dir`
supplies `lineage_id` / `production_run_id` from `scan_summary.json`.
`XIAOGU_PERSIST_DB=1` is set by `daily_pipeline.sh`. Calendar owner is
`xiaogu_db.is_trading_date()`.

## Actual call chain

```
scrapy_scanner/runner_v2.py
        ↓ insert_scan_session()  status = SNAPSHOT_CAPTURED
canonical snapshots in PostgreSQL
        ↓
xiaogu_forward_runner.main()
        ↓ calendar TRUE/FALSE/UNKNOWN
        ↓ _scan_observation_from_dir()  (production_scan must be PASS)
        ↓ fetch_persisted_canonical_snapshots()  require_fresh=True
        ↓ select_production_observation_snapshots()
        ↓ candidate_universe / execution_universe MAIN_BOARD_ONLY
        ↓ L2 route / L3_DEEP_CANDIDATE_FETCH
        ↓ evaluate_candidate_rows()
            _evaluate_one_candidate()
                PRODUCTION freshness: snapshot_age <= MAX_STALENESS (120 min)
                else WATCH / STALE_DATA / paper_observation=None
                else run_production_decision()
                    verify_persisted_snapshot()
                    assert_production_provenance()
                    evaluate_candidate_bundle()  position_state=FLAT
        ↓ if any unrecoverable worker: ABSTAIN, no Top1/Top3
        ↓ else attach_top_paper_observations()
        ↓ persist_paper = PRODUCTION and not dry_run and publishable
        ↓ if persist_paper and missing production_run_id:
              SCAN_BLOCKED PRODUCTION_RUN_ID_REQUIRED  (no persist)
        ↓ persist_production_facts()
              unique writer of DECISIONS_PERSISTED
        ↓ JSONL / Obsidian audit after PostgreSQL
```

`DECISIONS_PERSISTED` has one writer: `xiaogu_db.persist_production_facts()`.
`insert_scan_session()` only writes `SNAPSHOT_CAPTURED`.

## production_runs facts (live PostgreSQL, 2026-09-04)

Total runs: **27**. `DECISIONS_PERSISTED`: **0**.

Status counts:

| status | count |
|--------|-------|
| FAILED_PERSISTENCE | 8 |
| PASS | 8 |
| FAIL | 4 |
| SNAPSHOT_CAPTURED | 3 |
| ACTIVE_PENDING_T1 | 2 |
| RUNNING | 1 |
| SETTLED_CLOSED | 1 |

Latest three ONE-SYSTEM scan runs:

| production_run_id | trade_date | run_mode | status | started_at |
|-------------------|------------|----------|--------|------------|
| `011bcfff-adb3-4b6d-92d8-d3c1db39e59d` | 2026-09-03 | PRODUCTION | SNAPSHOT_CAPTURED | 15:24:25+08 |
| `cb8c657f-6d2a-4f9c-b1f3-8869c0d4ffcc` | 2026-09-03 | PRODUCTION | SNAPSHOT_CAPTURED | 02:11:41+08 |
| `693f4141-9d89-49cc-a135-10a936e63a95` | 2026-09-02 | PRODUCTION | SNAPSHOT_CAPTURED | 21:21:24+08 |

Older `LIVE_DAILY_PIPELINE` rows (`PASS` / `FAIL` / `FAILED_PERSISTENCE`) are
pre-convergence statuses. They are not official Phase 2 observation runs.

Paper observations: 6797, all `signal_time` 2026-09-03, `rank` null, no
`top1_flag` / `top3_flag`, `alpha_name=price_strength`, no
`production_alpha` / `production_run_id` in payload. Nested
`returns.payload.days`: **0**. Picks: 7105. Snapshots exist for 2026-09-03
(11096) and 2026-09-02 (5547). Calendar: 2026-09-03 TRUE, 2026-09-04 TRUE.

No 2026-09-04 `production_runs` row.

## Why 2026-09-03 is SNAPSHOT_CAPTURED

1. Scanner persisted run `011bcfff-...` as `SNAPSHOT_CAPTURED`. That is the
   scan-session contract, not a decision persist.
2. Operator production summary: 3191 MAIN_BOARD L3 candidates, **3191
   STALE_DATA**, `paper_observation_count=0`, `recorded=0`, scan_status
   `NO_SIGNAL` / `NO_PAPER_OBSERVATION`.
3. Source time on the PASS 2026-09-03 scan is `2026-09-03T15:24:25+08:00`.
   Production freshness is `MAX_STALENESS = 120 minutes` against
   `production_now()`. A later operator run against that source is stale.
4. `DECISIONS_PERSISTED` did not exist as a live status writer until Phase 2
   (`persist_production_facts`). 2026-09-03 could not have received it.
5. The 6797 unranked papers are a dump (`price_strength`, no rank). They are
   not Selection output and must not enter official OOS.

STALE_DATA does **not** raise a system fault. Selection still runs, but every
stale worker returns `paper_observation=None`, so Top1/Top3 is empty.

WATCH on a fresh, MAIN_BOARD, `signal_qualified` candidate **does** still
create a paper observation. Alpha `DATA_INSUFFICIENT` still allows Paper
Observation; BUY remains BLOCKED. The 2026-09-03 miss is freshness, not WATCH
eating Selection.

## PRODUCTION_PERSIST_BLOCKER

Live 2026-09-03 persist did not reach official Top1/Top3 because of:

**STALE_DATA** (source older than 120 minutes at decision clock)

plus historical:

**DECISIONS_PERSISTED writer did not exist on 2026-09-03**

Current code path itself, given a fresh trusted scan, `production_run_id`,
PRODUCTION mode, and `publishable=true`, can write `DECISIONS_PERSISTED`.
That is not a second owner. Answers to the persist checklist:

1. Writer: `persist_production_facts()` only.
2. Condition: PRODUCTION, not dry-run, publishable, `production_run_id` present.
3. Unique writer: yes.
4. Early exits that skip persist: calendar block, scan not PASS,
   `CANONICAL_SNAPSHOT_NOT_FOUND` / `AMBIGUOUS`, `STALE_DATA` at load,
   `PRODUCTION_RUN_ID_REQUIRED`, system-fault ABSTAIN (`publishable=false`).
   All-stale workers still allow persist of empty papers if run_id exists;
   they do not create official Top1/Top3.
5. `persist_database=True` is not the production fact path. Runner now
   persists facts first via `persist_production_facts`, then JSONL with
   `persist_database=False`.
6. `production_run_id` comes from `--production-run-id` or scan_summary
   `database_persistence.run_id`. Missing run_id blocks persist.
7. Empty decisions / empty papers can still mark `DECISIONS_PERSISTED` after
   Phase 2; official OOS still requires ranked provenance.
8. PRODUCTION workers require `verify_persisted_snapshot()`.
9. `attach_top_paper_observations` runs before persist unless system fault.
10. Paper observation requires FLAT + MAIN_BOARD + `signal_qualified` + not
    position review. Rank/top flags are set only by Selection.

Code defects found and fixed this round (not Alpha, not Selection math):

- coverage JSONB replaced the whole `scoring_config_snapshot` → now merges.
- official OOS accepted rank/top flags without production provenance → now
  requires `paper_signal_id`, `decision_id`, `production_run_id`,
  `snapshot_id`, `lineage_id`, `profit_window_alpha_5d_v4`,
  `opportunity_5d`, and `production_runs.status=DECISIONS_PERSISTED`.
- `production_run_id` is copied onto paper observations before persist.

## SELECTION_DIAGNOSTIC (2026-09-03 operator)

| field | value |
|-------|--------|
| candidate_count | 3191 |
| qualified_count | 0 |
| paper_count | 0 |
| top1_count | 0 |
| top3_count | 0 |
| first_rejection_reason | STALE_DATA |
| top1_reason | none |

## PRODUCTION_RUNTIME_BLOCKER (2026-09-04)

**LIVE_PRODUCTION_NOT_EXECUTED**

Blockers:

- Historical 2026-09-04 09:25 capture under `eastmoney_scan_morning` (directory
  name is a past artifact, not a production clock): `production_scan=BLOCKED`,
  `CRITICAL_SOURCE_INCOMPLETE:stock_all_a:5548`, persist SKIPPED, no run_id.
  That is a SCAN ATTEMPT, not an official production observation.
- Historical `eastmoney_scan_afternoon` directory exists and is **empty**
  (no `scan_summary.json`). Current pipeline uses `eastmoney_scan`.
- Reusing 2026-09-03 snapshots against the current clock would be STALE.
- No missing PostgreSQL, no missing calendar year, no invented credential
  blocker. The missing piece is a complete, fresh, trusted T-day scan inside
  120 minutes of `production_now()`.

Running the live command now would not create an honest official observation.
It was not executed.

## Coverage JSONB

`_write_production_run_coverage()` now reads the existing
`scoring_config_snapshot`, merges `observation_coverage` /
`observation_layer` / `influences_*=false`, and keeps prior model / target /
provenance / scoring metadata. No schema v7. Test:
`test_coverage_merge_preserves_existing_scoring_snapshot`.

## Official observation provenance

`fetch_official_paper_observations()` no longer treats rank-only rows as
official. Unranked dump, test fixtures without `production_run_id`, and
non-`DECISIONS_PERSISTED` runs are excluded. 6797 dump rows stay historical
artifacts.

## Outcome / OOS

Unchanged. Nested `returns.payload.days["1".."5"]`. Chronological 60/20/20,
embargo ≥ 5. No gate change. Result filler still consumes paper
observations; first official paper can enter T+1..T+5 through the existing
owner.

## Tests

```
python -m pytest tests/ -x -q --tb=line
370 passed

python -m pytest tests/test_single_system_convergence.py -q --tb=line
40 passed

python -m compileall -q .
COMPILE_EXIT:0
```

New / strengthened:

- coverage merge preserves old snapshot fields
- unranked / fixture ≠ official; production provenance = official
- first official observation path uses runner decision + Selection +
  `persist_production_facts` (not a hand-ranked paper row)

Full `tests/` suite is recorded in STATE.md after the run completes.

## 20 answers

1. Production entry: `bash daily_pipeline.sh` /
   `python3 xiaogu_forward_runner.py --date DATE --scan-dir SCAN_DIR`
   (default mode PRODUCTION).
2. Past status is `SNAPSHOT_CAPTURED` because that is the scanner persist
   status; decision persist never succeeded on those runs.
3. No `DECISIONS_PERSISTED` because 2026-09-03 workers were all STALE_DATA
   and the status writer did not exist until Phase 2.
4. Yes: live freshness / incomplete 2026-09-04 scan. Coverage overwrite and
   weak official provenance were code defects and are fixed.
5. Live miss is **data/freshness**, not Alpha math. Coverage merge and
   provenance were **code**.
6. Unique Alpha: `profit_window_alpha_5d_v4`.
7. Unique Target: `opportunity_5d`.
8. Unique Selection: `attach_top_paper_observations`.
9. Unique Decision: `evaluate_candidate_bundle`.
10. Unique Fact DB: PostgreSQL schema v6.
11. Unique Memory: Obsidian semantic notes; PostgreSQL remains facts.
12. Coverage now merges; old scoring snapshot fields are kept.
13. Official provenance is now strict (run + alpha + target + rank +
    `DECISIONS_PERSISTED`).
14. Production-path integration test:
    `test_first_official_observation_production_path` passed.
15. Live official Top1: **no**.
16. Live official Top3: **no**.
17. Official `sample_count`: **0**.
18. **NO_REAL_OOS_EVIDENCE_YET**.
19. BUY: **BLOCKED**.
20. LIVE: **DISABLED**.

## Next real evidence

Wait for a normal trading-day real-market scan that:

1. Completes `stock_all_a` and other critical sources
2. Persists trusted snapshots with `production_scan=PASS`
3. Is evaluated inside 120 minutes of `source_time`
4. Carries `production_run_id`
5. Hits `persist_production_facts` → `DECISIONS_PERSISTED`
6. Writes official Top1/Top3 with provenance

Until then:

**NO_REAL_OOS_EVIDENCE_YET**
