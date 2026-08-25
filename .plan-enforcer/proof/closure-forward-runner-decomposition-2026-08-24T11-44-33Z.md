# Closure Receipt -- forward-runner-decomposition

**Plan source:** docs/plans/2026-08-21-forward-runner-decomposition.md
**Closed at (UTC):** 2026-08-24T11:44:33.483Z
**Tier:** structural

## Prior closure
- none (first close of this plan)

## Status
```
30/30 verified  |  0 blocked  |  0 skipped  |  0 superseded  |  0 remaining
drift: 0
```

## Task ledger
| ID | Task | Status | Evidence |
|----|------|--------|----------|
| T1 | Build the August date and snapshot inven | verified | prior replay/tests |
| T2 | Prove owner and historical chain provena | verified | pytest tests/ -x -q |
| T3 | Reconcile full candidate-pool persistenc | verified | pytest tests/ -x -q |
| T4 | Settle all available candidate rows | verified | pytest tests/ -x -q |
| T5 | Enforce replay leakage protection | verified | pytest tests/ -x -q |
| T6 | Classify T-day decision outcomes | verified | pytest tests/ -x -q |
| T7 | Record candidate snapshot evidence | verified | pytest tests/ -x -q |
| T8 | Explain settled candidate outcomes | verified | pytest tests/ -x -q |
| T9 | Aggregate settled candidate outcomes | verified | pytest tests/ -x -q |
| T10 | Replay the current main-force baseline | verified | shadow replay JSON |
| T11 | Benchmark baseline by full pool and emit | verified | L1 replay JSON |
| T12 | Select one repeated T-day failure class | verified | failure tests |
| T13 | Implement one main-force correction | verified | pytest tests/ -x -q |
| T14 | Run baseline-versus-change promotion rep | verified | replay output |
| T15 | Complete repository and knowledge valida | verified | 521 passed + indexes |
| T16 | Preserve the completed runner decomposit | verified | source + tests |
| T17 | Build settled August limit-up and high-r | verified | replay output |
| T18 | Select one repeated, T-day-observable fa | verified | failure tests |
| T19 | Modify the existing main-force factor ow | verified | pytest tests/ -x -q |
| T20 | Replay the sole main-force chain against | verified | L1 replay JSON |
| T21 | Validate and record the factor decision | verified | AgentMemory + Obsidian |
| T22 | Restore L1 replay resource safety withou | verified | ambiguity gate test |
| T23 | Calibrate the existing weak-regime chase | verified | regime shadow JSON |
| T24 | Demote Intraday Limit-Up Capture Extensi | verified | reversal-risk tests |
| T25 | Freeze the single-chain alpha contract | verified | pytest tests/ -x -q |
| T26 | Replace formal ranking with the five-mod | verified | 521 passed |
| T27 | Collapse admission into four primary alp | verified | path/gate tests |
| T28 | Complete settlement labels through exist | verified | backfill tests |
| T29 | Replay and promote only the sole chain | verified | L1 + shadow replay |
| T30 | Refresh architecture and knowledge closu | verified | graph/index/memory close |

## Decision Log summary
_(no decision log entries)_

## Reconciliation history
_(no reconciliation rounds recorded)_

## Files changed
```
HEAD: f675dd1

.plan-enforcer/.user-messages.jsonl                |   2 +
 .plan-enforcer/awareness.md                        |   4 +
 .plan-enforcer/combobulate.md                      |  12 +-
 .plan-enforcer/discuss.md                          |  82 ++-
 .plan-enforcer/ledger.md                           |  50 +-
 .plan-enforcer/statusline-state.json               |   8 +-
 .understand-anything/config.json                   |   2 +-
 .understand-anything/mcp-scan-result.json          |  98 +--
 docs/architecture.md                               | 129 +++-
 .../2026-08-21-forward-runner-decomposition.md     | 245 ++++++-
 scrapy_scanner/runner_v2.py                        |  36 +
 scripts/xiaogu_return_backfill.py                  |  58 +-
 tests/test_db_backfill.py                          | 127 +++-
 ...st_formal_rank_alignment_and_defensive_shell.py | 285 +++++++-
 tests/test_regime_policy.py                        |  27 +
 tests/test_xiaogu_a_share_forward_runner.py        | 204 +++++-
 .../test_xiaogu_runtime_payload_evidence_vector.py |   5 +-
 xiaogu_backtest_v0_1.py                            | 351 +++++++++-
 xiaogu_case_vector_store.py                        |  21 +-
 xiaogu_db.py                                       |  45 +-
 xiaogu_forward_bundle_io.py                        |   4 +
 xiaogu_forward_eligibility.py                      |  32 +-
 xiaogu_forward_features.py                         |   5 +
 xiaogu_forward_ranking.py                          | 742 +++++++++++++++++----
 xiaogu_forward_result_filler_v0_1.py               | 360 +++++++++-
 xiaogu_forward_runner.py                           | 164 +++++
 xiaogu_forward_snapshot.py                         |   7 +-
 xiaogu_regime_policy.py                            | 102 +++
 28 files changed, 2885 insertions(+), 322 deletions(-)
```

## Blocked / open coordination
_(nothing blocked)_

## Proof artifacts
_(no prior proof artifacts)_

## Plan-specific extras
### Must-Haves (from plan)

- MH1: Every available August snapshot has immutable identity, exact candidate
  count, provenance, and retry/non-comparable status. A:I5 A:I6 A:I7
- MH8: 唯一生产链保持 scanner -> runner -> recorder/DB -> T+1 settlement -> scoreboard/API。
- MH2: Every candidate row has a T-day outcome, exact admission/exclusion/rank
  reason, and settlement status; settled rows have source-evidenced labels.
  A:I5 A:I7
- MH3: Full-pool profit/loss attribution covers all rows, not only PAPER_PICK or
  Top10, and never feeds future fields into T-day decisions. A:I5 A:I7
- MH4: The current main-force chain has a deterministic baseline replay over
  the exact same snapshot cohort. A:I6 A:I7
- MH5: At most one surgical main-force behavior change is promoted only when
  baseline replay improves return/risk/coverage metrics without hard-block
  regression. A:I5 A:I6 A:I7
- MH6: The only production decision chain remains scanner -> runner ->
  recorder/DB -> T+1 settlement -> scoreboard/API, with no second selector.
  A:I2 A:I4 A:I6 A:I7
- MH7: The settled August limit-up and high-return labels improve only an
  existing main-force factor after exact-snapshot replay proves no leakage,
  hard-block regression, or deterioration in the promotion metrics. A:I6 A:I8

### Task 1: Build the August date and snapshot inventory A:I5 A:I6 A:I7
- [x] Query recorded scanner sessions, candidate snapshots, production runs, and
  available scan paths for 2026-08-01 through 2026-08-21.
- [x] Record one row per date/snapshot with snapshot ID, candidate count, symbol
  content hash, active/retry status, and missing-source reason.
- [x] Verification: read-only inventory artifact reconciles every selected date
  to an explicit snapshot or explicit missing/non-comparable reason.

### Task 2: Prove owner and historical chain provenance A:I2 A:I6 A:I7
- [x] Use codebase-memory `get_architecture`, `search_graph`, `trace_path`, and
  `get_code_snippet` to map scanner, runner, recorder, settlement, scoreboard,
  ranking, eligibility, and feature owners.
- [x] For each snapshot, extract only persisted provenance fields such as
  production run, runner mode, ranking view, scanner/source version, and
  scoring-config hash; do not infer provenance from T+1 outcomes.
- [x] Verification: codebase-memory call paths plus source/test confirmation for
  each owner prove the sole production chain and separate replay owner; the
  provenance table is reproducible.

### Task 3: Reconcile full candidate-pool persistence A:I5 A:I7
- [x] Load every candidate row in each selected immutable snapshot, including
  T+1 admission rejects and final-gate rejects.
- [x] Preserve exact row counts, snapshot/run isolation, source layer, stamped
  T-day admission, formal rank, score, and exclusion reason.
- [x] Verification: `expected_rows == persisted_rows` per selected snapshot, or
  an explicit provider/data-loss reason is recorded; no pool is padded or merged.

### Task 4: Settle all available candidate rows A:I5 A:I7
- [x] Use the existing settlement owner to join returns by production-run and
  candidate-snapshot lineage where available, falling back only to the existing
  immutable legacy key contract.
- [x] Record T+1 close/high/VWAP/limit-up labels, pending rows, failed fetches,
  and duplicate attempts with explicit reason codes.
- [x] Verification: per-date coverage reports contain settled, pending, failed,
  and duplicate counts; duplicate settlement leaves formal score/eligibility
  unchanged.

### Task 5: Enforce replay leakage protection A:I5 A:I7
- [x] Sanitize decision rows so future fields are removed except the designated
  post-decision placeholder.
- [x] Run injected-future fixtures against ranking, eligibility, replay, and
  shadow diagnostics.
- [x] Verification: injected T+1 values cannot change T-day rank, admission,
  exclusion, selected symbol, or replay identity.

### Task 6: Classify T-day decision outcomes A:I5 A:I7
- [x] Assign one primary T-day decision outcome: PAPER_PICK, historical emitted
  ticket, admitted-not-selected, admission rejection, or a
  regulatory/buyability/data hard block.
- [x] Verification: attribution artifact has exactly one T-day decision outcome
  for every selected snapshot candidate.

### Task 7: Record candidate snapshot evidence A:I5 A:I7
- [x] Attach exact T-day formal rank/score, admission state, gate reasons,
  provenance, and settlement state to every classified candidate.
- [x] Verification: no settled row remains unclassified and pending/source
  failures are explicit rather than synthesized.

### Task 8: Explain settled candidate outcomes A:I5 A:I7
- [x] For every settled positive or limit-up row, compare its T-day capital,
  theme, continuation, buyability, data-quality, timing, and formal-rank
  evidence with the selected ticket.
- [x] For every settled loss, record the T-day risk and evidence conditions that
  explain the loss, separating legitimate hard rejection from ranking miss.
- [x] Verification: each settled winner and loss has T-day evidence separated
  from its post-decision return label in the full-pool analysis artifact.

### Task 9: Aggregate settled candidate outcomes A:I5 A:I7
- [x] Aggregate results by date, provenance, rank cohort, admission state, and
  primary success/failure class.
- [x] Verification: full-pool report contains counts, examples, and metrics for
  every status class; no conclusion uses an unsettled row.

### Task 10: Replay the current main-force baseline A:I5 A:I6 A:I7
- [x] Re-run the current `main_force_behavior_chain` from each exact snapshot
  using T-day fields only, without activation, PAPER_PICK replacement, or
  production settlement writes.
- [x] Emit replay identity containing snapshot hash, runner mode, scoring-config
  hash, ranking implementation hash, and each candidate's rank/admission result.
- [x] Verification: repeated replay of one immutable snapshot is byte-stable;
  failed activation attempts and future-field injections are rejected.

### Task 11: Benchmark baseline by full pool and emitted ticket A:I5 A:I6 A:I7
- [x] Compute settled coverage, positive rate, mean/median T+1, limit-up rate,
  worst loss, downside/drawdown, and rank-to-outcome gaps for all candidates.
- [x] Compute the same metrics for PAPER_PICK, historical emitted tickets,
  admitted-not-selected rows, and provenance cohorts.
- [x] Verification: baseline artifact uses one fixed snapshot cohort and reports
  pending/provider failures separately from completed performance.

### Task 12: Select one repeated T-day failure class A:I5 A:I6 A:I7
- [x] Select only a repeated false-positive or missed-winner shape supported by
  counts and examples across multiple dates or provenance cohorts.
- [x] Reject isolated ex-post winners, intentionally sealed/unbuyable rows,
  missing-source rows, and future-only distinctions as change candidates.
- [x] Define one affected existing owner, expected direction, unchanged hard
  blocks, and regression cases before editing production code.
- [x] Verification: a decision record links the selected failure class to
  T-day-only features and the baseline artifact.

### Task 13: Implement one main-force correction A:I5 A:I6 A:I7
- [x] Modify exactly one existing ranking, feature, or eligibility owner; do not
  add a scorer, selector, runner, or parallel production path.
- [x] Preserve runner public imports/call contracts and all regulatory,
  data-quality, price, and buyability hard blocks.
- [x] Add focused regression cases for the selected false-positive/missed-winner
  shapes and unchanged hard-block cases.
- [x] Verification: focused tests pass and codebase-memory/source inspection
  confirms one owner for the changed responsibility.

### Task 14: Run baseline-versus-change promotion replay A:I5 A:I6 A:I7
- [x] Replay the changed chain on the exact same August snapshot and candidate
  cohort with the same settlement labels and no future-field access.
- [x] Require non-decreasing settled coverage and pick win rate, higher mean T+1,
  no worse median return or worst-loss magnitude, and no hard-block regression.
- [x] If any gate fails or coverage is incomplete, revert the behavior change and
  record the blocking evidence instead of promoting it.
- [x] Verification: baseline/change artifact and promotion gate are reproducible
  from immutable inputs.

### Task 15: Complete repository and knowledge validation A:I2 A:I4 A:I5 A:I6 A:I7
- [x] Run targeted tests, `pytest tests/ -x -q`, compile checks, `git diff
  --check`, and affected CLI/import checks.
- [x] Confirm production entry identity, replay separation, database consistency,
  and no new dead/parallel implementation.
- [x] Save the decision and failure lesson to AgentMemory and update the existing
  Obsidian owner note with date, evidence, affected paths, and gate result.
- [x] Verification: final receipt lists code diff, tests, replay artifact,
  candidate coverage, and any unresolved provider/pending rows.

### Task 16: Preserve the completed runner decomposition A:I2 A:I4 A:I6
- [x] Keep `xiaogu_forward_runner.py` as orchestration/CLI with `main()` as the
  only production entry.
- [x] Keep ranking, eligibility, snapshot, diagnostics, and feature assembly in
  their existing owners; replay remains in `xiaogu_backtest_v0_1.py`.
- [x] Verification already completed: runner `9154 -> 4730` lines, `499 passed`,
  health check `17/17`, import identity, and single CLI entry.

### Task 17: Build settled August limit-up and high-return factor evidence A:I6 A:I8
- [x] Load every exact August candidate snapshot and retain only rows with a
  source-evidenced T+1 close label; separately label T+1 limit-up and
  high-return rows using an explicit threshold derived from the settled cohort.
- [x] Extract only fields available before the T-day decision from existing
  main-force ranking and feature evidence, including capital confirmation,
  direct catalyst, continuation, close position, signal extension, sector
  evidence, risk profile, and market regime.
- [x] Compare each label cohort to same-day settled non-winner controls and
  report coverage, sample count, effect direction, and missingness.
- [x] Verification: artifact excludes pending rows and confirms no forbidden
  future field appears in a feature column.

### Task 18: Select one repeated, T-day-observable factor candidate A:I6 A:I8
- [x] Require the proposed factor shape to recur across at least three August
  trade dates and have complete T-day input coverage in the selected rows.
- [x] Reject candidate changes driven only by one symbol, one provenance retry,
  a hard-blocked row, or a future-only distinction.
- [x] Map the chosen signal to one existing owner and state the expected
  ranking direction plus unchanged gates.
- [x] Verification: the candidate was high-quality catalyst momentum in
  `xiaogu_forward_ranking.py`; it is rejected in Task 20 after an exact L3
  risk-gate failure, so it is not promoted.
  statistics and concrete same-day examples.

### Task 19: Modify the existing main-force factor owner A:I6 A:I8
- [ ] Change only `xiaogu_forward_ranking.py` or the existing T-day feature
  owner that supplies the selected factor; do not add a selector, runner,
  score implementation, or production entry.
- [ ] Keep regulatory, data-quality, price, and buyability hard blocks
  unchanged.
- [ ] Add regression tests for the winning feature shape, its negative control,
  and a no-future-label assertion.
- [x] Verification: skipped by Decision D1. The exact snapshot gate rejects
  the candidate before any production ranking edit.

### Task 20: Replay the sole main-force chain against the fixed August cohort A:I6 A:I8
- [x] Re-run the decisive exact L3 snapshot with the current sole
  `main_force_behavior_chain`, preserving the same candidate rows and
  settlement labels used by the baseline.
- [x] Require non-decreasing settled coverage, pick win rate, and median T+1;
  require improved mean T+1 or limit-up selection rate; reject a worse maximum
  loss or hard-block regression.
- [x] Verify the replay's decision input has no T+1 return, limit-up outcome,
  or other future-field access and has no database writes.
- [x] Verification: on `2026-08-18` snapshot
  `3f155e00-87be-4b50-8682-846c2758b02c`, the T-day-only factor changes
  `603110` (`-5.8294%`) to `603067` (`-9.8866%`); the maximum-loss gate fails.
  The candidate is rejected without a production edit, so no remaining
  snapshots are needed for promotion.

### Task 21: Validate and record the factor decision A:I6 A:I8
- [x] Run focused tests, `pytest tests/ -x -q`, compile checks, CLI help, health
  check, and `git diff --check`.
- [x] Update AgentMemory and the existing Obsidian decision/lesson records with
  the factor evidence, promotion result, affected owner, and residual limits.
- [x] Verification: `501 passed`; compile and runner CLI help passed; health
  check `17/17`; AgentMemory and the existing decision/failure notes updated.
  No production code was changed because the exact L3 maximum-loss gate failed.

### Task 22: Restore L1 replay resource safety without changing semantics A:I5 A:I6
- [x] Keep `fetch_daily_candidates` as the single database owner and preserve
  its default projection for production and general analysis callers.
- [x] Allow the existing L1 structural replay to request only its persisted
  pre-decision fields, avoiding unnecessary JSON deserialization while
  preserving its sanitizer/hydrator contract. A date with multiple production
  snapshots must require the existing production-run identity rather than
  merging retries.
- [x] Verification: focused replay test, full test suite, and the exact
  `2026-08-18` structural replay complete without a memory kill; compare the
  replay result against the same immutable DB snapshot. The snapshot
  `3f155e00-87be-4b50-8682-846c2758b02c` completed in 16.78 seconds at
  215,976 KiB RSS, selected `603067`, and retained T+1 `-9.8866%`.

### Task 23: Calibrate the existing weak-regime chase factor A:I6 A:I8
- [x] Reuse the settled August exact snapshots and existing failure records to
  scan only the current `weak_regime_chase` weight; do not add a factor,
  selector, gate, or production entry.
- [x] Select the smallest weight that changes the repeated weak-regime
  `603067` proxy-chase result while keeping candidate eligibility and all hard
  blocks unchanged.
- [x] Change `xiaogu_forward_ranking.py:formal_candidate_sort_key` from `6.0`
  to `12.0`, add a material-penalty regression test, and prove the change on
  the same immutable 2026-08-18 production run.
- [x] Verification: the exact L3 replay over 17 settled redecisions improves
  mean T+1 from `-3.1623%` to `-2.4463%`, preserves the `41.18%` win rate and
  `-2.0095%` median, and improves worst loss from `-9.8866%` to `-9.2074%`.
  The L1 replay of run `3f155e00-87be-4b50-8682-846c2758b02c` selects
  `603110` (`-5.8294%`) ahead of `603067` (`-9.8866%`). Full tests: `516
  passed`; decision inputs remain T-day only and there are no DB writes.

### Task 24: Demote Intraday Limit-Up Capture Extension A:I6 A:I8
- [x] Use settled August T+1 limit-up labels only to screen existing T-day
  fields, excluding near-limit T-day rows from executable-factor conclusions.
- [x] Reuse the existing `limitup_capture_score` in
  `xiaogu_forward_ranking.py:formal_candidate_sort_key` as a bounded soft
  extension penalty; do not add a selector, gate, configuration owner, or
  production entry.
- [x] Verify the exact L3 production runner over 2026-08-10 through
  2026-08-20: only 2026-08-13 changes from `002589` (`-4.8518%`) to
  `002357` (`+0.3850%`), with eligibility and exclusions unchanged. Across
  eight settled redecisions, mean improves `-1.7374% -> -1.0828%`, median
  `-3.4993% -> -1.4910%`, win rate `37.5% -> 50.0%`, and max drawdown
  `-19.4684% -> -17.0618%`; limit-up rate and worst loss do not worsen.
- [x] Verification: focused tests `9 passed`; full `pytest tests/ -x -q`
  `517 passed, 2 warnings`; L1 exact replay and compile check passed. T+1
  labels are absent from the T-day decision input and replay performs no DB
  writes.
