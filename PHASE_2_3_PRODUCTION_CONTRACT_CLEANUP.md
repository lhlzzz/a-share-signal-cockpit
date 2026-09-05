# Phase 2.3 Production Contract Cleanup

This is not Xiaogu 4.0. Production Alpha, Target, Selection, Decision, Paper,
Outcome, PostgreSQL schema v6, and Obsidian memory were not replaced.

Production ticket = T-day investment research observation, not a same-day
short-term buy print. Outcome = whether `opportunity_5d` appears on T+1..T+5.

2026-09-04 remains **NO_OFFICIAL_PRODUCTION_TICKET**. It was not backfilled.
The 6797 unranked dump is still not an official ticket.

## 1. Time definitions removed

Removed as live production clocks / strategy windows:

- `eastmoney_scan_afternoon` as the daily pipeline directory
- `eastmoney_scan_morning` as a production scan identity
- `job_morning_scan` (Cron 09:25)
- `job_afternoon_scan_and_pick` (Cron 14:30 wrapping `daily_pipeline.sh`)
- morning / afternoon / 14:30 wording as current production rules in
  `STATE.md`, `FINAL_SYSTEM_AUDIT.md`, `PHASE_2_TRUTH_REPORT.md`,
  `PHASE_2_1_FIRST_OBSERVATION_AUDIT.md`, `PHASE_2_2_FIRST_REAL_PRODUCTION_TICKET.md`,
  `README.md`, `AGENTS.md`
- Plan-enforcer leftover HC4: “same symbol 09:25 and 14:30 are two observations”

Never present as Python constants, so not deleted as code:

- `MORNING_WINDOW`
- `AFTERNOON_WINDOW`
- `OFFICIAL_14_30`
- `PREMARKET_PRODUCTION`
- `MIDDAY_PRODUCTION`
- `CLOSE_PRODUCTION`
- `SECOND_SCAN_WINDOW`
- `RETRY_SCAN_WINDOW`
- `INTRADAY_SIGNAL_WINDOW`

Kept as the only production time rules:

1. `xiaogu_db.is_trading_date()` must be `TRUE`
2. `source_time` age versus `production_now()` / batch `decision_clock` ≤ `MAX_STALENESS` (120 minutes)

## 2. Dead branches removed

- `xiaogu_scheduler.py` no longer schedules a morning capture or an afternoon
  ticket. Production capture is `bash daily_pipeline.sh`. The scheduler keeps
  only post-close `job_horizon_evaluation` at 20:00 (calendar-gated filler +
  position review).
- `daily_pipeline.sh` no longer names the capture directory as an afternoon slot.
- Same-day dual official persist is now blocked at `persist_production_facts`.
- SCAN ATTEMPT vs official observation is explicit on runner JSON
  (`observation_kind`).

## 3. Obsolete states

No live `MORNING_*`, `AFTERNOON_*`, `INTRADAY_*`, `PREMARKET_*`,
`SECOND_PASS`, `RESCAN`, or `MIDDAY_READY` production states existed in source.
Nothing to delete there.

Live statuses remain:

- `SCAN_BLOCKED` — SCAN ATTEMPT, not an official ticket
- `STALE_DATA` — SCAN ATTEMPT / candidate-level freshness fail
- `SNAPSHOT_CAPTURED` — capture identity only
- `DECISIONS_PERSISTED` — official production run status
- `NO_SIGNAL`
- `FAILED`

`DECISION_READY` is still not a stored production-run status. Decisions use
`READY` as a candidate state when BUY is blocked.

## 4. daily_pipeline new semantics

```bash
bash daily_pipeline.sh
```

means: run today’s one Production pipeline.

`SCAN_DIR=data/live_scan/${DATE}/eastmoney_scan`

Directory name is capture identity, not a clock. No `SLOT`, `SESSION`,
`MORNING`, or `AFTERNOON` was added.

## 5. Scanner new semantics

`scrapy_scanner/runner_v2.py` is REAL_MARKET_CAPTURE.

- live capture uses the actual source timestamp
- `--date` still cannot forge a historical source timestamp
- it is not a morning scanner or an afternoon scanner
- `_session_quote_complete()` / `_critical_row_gaps()` / `_incomplete_audit()` /
  `_collect(..., critical=True)` remain fail-closed
- 09:25 `CRITICAL_SOURCE_INCOMPLETE:stock_all_a` remains the correct result
  when ACTIVE_REQUIRED session fields (`f5` / `f6` / `f62`) are missing

A blocked early capture is a SCAN ATTEMPT. The later valid scan on the same
trading day is the official production observation. Retry does not mint a
second official ticket.

## 6. Production Ticket definition

Production Ticket = today’s T-day investment research observation:

based on a real complete market scan and research evidence, this company
enters a five-day opportunity observation.

It is not “today’s short-term buy print”.

Provenance stays separate:

- `lineage_id` = capture identity
- `production_run_id` = decision batch identity
- `decision_id` = candidate decision identity
- `paper_signal_id` = paper observation identity

Official observation requires `DECISIONS_PERSISTED` plus official Top1/Top3
provenance. Rank-only rows, unranked dumps, `SNAPSHOT_CAPTURED`,
`SCAN_BLOCKED`, and `STALE_DATA` are not official.

## 7. Freshness contract

`MAX_STALENESS = 120 minutes`.

Age = `decision_clock - source_time`. Production clock is
`xiaogu_forward_snapshot.production_now()`. There is no fixed 13:00 / 14:00 /
14:30 / 14:45 strategy clock.

## 8. Calendar contract

Owner remains `xiaogu_db.py`. `is_trading_date()` returns `TRUE` / `FALSE` /
`UNKNOWN`. Missing data is `CALENDAR_DATA_UNAVAILABLE` and fail-closed.
Future prices, scanner row availability, snapshots, paper outcomes, and
weekday arithmetic cannot determine a trading date.

## 9. Alpha unmodified

`xiaogu_core_alpha.py` diff = 0.

`profit_window_alpha_5d_v4` and `opportunity_5d` are unchanged.
When the model is not `VALIDATED`, `selection_score` may still fall back to
`price_strength`. `signal_qualified = true` does not require
`model_status = VALIDATED`. `ALPHA_NOT_VALIDATED` still blocks BUY only.

## 10. Selection unmodified

`xiaogu_portfolio_decision.attach_top_paper_observations` diff = 0.

Still ranks `paper_observation != None` and `core_alpha.signal_qualified == true`.
Limit remains 3. Rank 1 is Top1. No time-of-day / morning / afternoon /
intraday / close score was added.

## 11. BUY still BLOCKED

Production BUY remains `BUY_BLOCKED`. Tests assert `buy_status == BUY_BLOCKED`
and `state != BUY`.

## 12. LIVE still DISABLED

`paper_only = true`, `live_order = false`. No broker path was opened.

## 13. Tests

Command: `python -m pytest tests/ -q --tb=line`

Result: **378 passed**.

New file: `tests/test_phase23_production_contract.py`

1. `test_production_has_no_fixed_clock_contract`
2. `test_one_daily_scan_one_production_observation`
3. `test_stale_scan_attempt_is_not_official_ticket`
4. `test_afternoon_name_is_not_production_contract`
5. `test_source_completeness_remains_fail_closed`
6. `test_alpha_not_validated_can_still_emit_paper_signal`

Also retargeted:

- pipeline asserts `SCAN_DIR=.../eastmoney_scan` and forbids afternoon/morning names
- persist uniqueness: one official `production_run_id` per `trade_date`; same-run reentry allowed
- stale-only same-day lineage remains not current production input

## 14. compileall

```
python -m compileall -q .
COMPILE_EXIT:0
```

`git diff --check` clean on the committed sources.

## 15. grep audit

Command:

```
git grep -n -E 'morning|afternoon|14:30|pre_market|midday|intraday|second_scan|rescan|official_window|production_window|afternoon_scan|morning_scan|schedule|scheduler'
```

Goal was not zero matches. Remaining live hits:

| Hit | Why kept |
|-----|----------|
| `xiaogu_scheduler.py` + `apscheduler` + 20:00 Cron | post-close horizon fill / position review only |
| `scripts/xiaogu_daily_health_check.py` `check_scheduler_outcome_job` | health check that filler still runs from the scheduler |
| tests asserting `eastmoney_scan_afternoon` / `job_morning_scan` are **absent** | contract tests |
| docs saying there is **no** morning/afternoon/14:30 clock | current rule |
| PHASE_2_1 / PHASE_2_2 historical directory names | historical event record, labeled as past artifacts |
| scanner argparse “Not a morning or afternoon scanner” | negation of the old contract |
| `.understand-anything/*` fingerprints still listing `job_morning_scan` | navigation graph stale vs source; source wins |

No remaining Python production path defines morning/afternoon/14:30 as the
official ticket clock.

## 16. git HEAD

Contract commits on `main`:

- `53d28de` fix: simplify Xiaogu production contract
- `0bd2b67` test: harden one daily scan one observation
- `3e36ac3` docs: remove obsolete production time definitions

Tip SHA is `git rev-parse HEAD` after this report commit. `main == origin/main`.

## 17. worktree

Clean after push. Unrelated `.plan-enforcer/statusline-state.json` was restored
and not committed.

XIAOGU_PRODUCTION_CONTRACT = ONE DAILY REAL MARKET SCAN → ONE T-DAY INVESTMENT OBSERVATION → ONE T+1..T+5 OOS VALIDATION
