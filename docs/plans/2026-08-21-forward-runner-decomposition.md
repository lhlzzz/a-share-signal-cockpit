# August Full-Pool Main-Force Chain Upgrade

**Goal:** Use the current single `main_force_behavior_chain` as the production
reference while reconstructing every available August 2026 trading day from its
recorded candidate snapshot. Analyze every persisted candidate row, not only
PAPER_PICK or Top10, with historical provenance, T-day admission/ranking
outcome, and T+1 result when settled. Use repeated T-day-observable profit and
loss patterns to upgrade the existing main-force production chain.

**Constraints:** Preserve the only production decision chain:
scanner -> runner -> recorder/DB -> T+1 settlement -> scoreboard/API. Use exact
immutable snapshots and exact recorded candidate counts. Historical chain
identity is provenance, not a second production selector. T+1 fields are
post-decision labels only. Preserve regulatory, data-quality, price, and
buyability hard blocks. No direct production DB edits, live trading, broker
connections, or new selection path.

**Out of scope:** Forcing ex-post winners into PAPER_PICK, limiting analysis to
PAPER_PICK or Top10, merging retries with different pools, changing the scanner
universe without evidence, or promoting shadow/research output into production.

## Assumptions

- Study window: 2026-08-01 through 2026-08-21, limited to dates with recorded
  scanner/candidate evidence.
- A date with fewer than 400 rows is analyzed at its exact persisted count.
- Missing T+1 settlement is pending/provider failure, never a synthetic result.
- Production entry remains `xiaogu_forward_runner.main`; replay remains owned by
  `xiaogu_backtest_v0_1.py`.

## Must-Haves

- MH1: Every available August snapshot has immutable identity, exact candidate
  count, provenance, and retry/non-comparable status. A:I5 A:I6 A:I7
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

### Task 6: Classify every candidate row A:I5 A:I7
- [x] Assign one primary T-day outcome: PAPER_PICK, historical emitted ticket,
  admitted-not-selected, admission rejection, regulatory/buyability/data hard
  block, lower formal rank, pending settlement, or source failure.
- [x] Attach exact T-day formal rank/score, admission state, gate reasons,
  provenance, and settlement state to every row.
- [x] Verification: attribution artifact has exactly one classification row for
  every selected snapshot candidate and no unclassified settled rows.

- [x] For every settled positive or limit-up row, compare its T-day capital,
  theme, continuation, buyability, data-quality, timing, and formal-rank
  evidence with the selected ticket.
- [x] For every settled loss, record the T-day risk and evidence conditions that
  explain the loss, separating legitimate hard rejection from ranking miss.
- [x] Aggregate results by date, provenance, rank cohort, admission state, and
  primary success/failure class.
- [x] Verification: full-pool report contains counts, examples, and metrics for
  every status class; no conclusion uses an unsettled row.

### Task 8: Replay the current main-force baseline A:I5 A:I6 A:I7
- [x] Re-run the current `main_force_behavior_chain` from each exact snapshot
  using T-day fields only, without activation, PAPER_PICK replacement, or
  production settlement writes.
- [x] Emit replay identity containing snapshot hash, runner mode, scoring-config
  hash, ranking implementation hash, and each candidate's rank/admission result.
- [x] Verification: repeated replay of one immutable snapshot is byte-stable;
  failed activation attempts and future-field injections are rejected.

### Task 9: Benchmark baseline by full pool and emitted ticket A:I5 A:I6 A:I7
- [x] Compute settled coverage, positive rate, mean/median T+1, limit-up rate,
  worst loss, downside/drawdown, and rank-to-outcome gaps for all candidates.
- [x] Compute the same metrics for PAPER_PICK, historical emitted tickets,
  admitted-not-selected rows, and provenance cohorts.
- [x] Verification: baseline artifact uses one fixed snapshot cohort and reports
  pending/provider failures separately from completed performance.

### Task 10: Select one repeated T-day failure class A:I5 A:I6 A:I7
- [x] Select only a repeated false-positive or missed-winner shape supported by
  counts and examples across multiple dates or provenance cohorts.
- [x] Reject isolated ex-post winners, intentionally sealed/unbuyable rows,
  missing-source rows, and future-only distinctions as change candidates.
- [x] Define one affected existing owner, expected direction, unchanged hard
  blocks, and regression cases before editing production code.
- [x] Verification: a decision record links the selected failure class to
  T-day-only features and the baseline artifact.

### Task 11: Implement one main-force correction A:I5 A:I6 A:I7
- [x] Modify exactly one existing ranking, feature, or eligibility owner; do not
  add a scorer, selector, runner, or parallel production path.
- [x] Preserve runner public imports/call contracts and all regulatory,
  data-quality, price, and buyability hard blocks.
- [x] Add focused regression cases for the selected false-positive/missed-winner
  shapes and unchanged hard-block cases.
- [x] Verification: focused tests pass and codebase-memory/source inspection
  confirms one owner for the changed responsibility.

### Task 12: Run baseline-versus-change promotion replay A:I5 A:I6 A:I7
- [x] Replay the changed chain on the exact same August snapshot and candidate
  cohort with the same settlement labels and no future-field access.
- [x] Require non-decreasing settled coverage and pick win rate, higher mean T+1,
  no worse median return or worst-loss magnitude, and no hard-block regression.
- [x] If any gate fails or coverage is incomplete, revert the behavior change and
  record the blocking evidence instead of promoting it.
- [x] Verification: baseline/change artifact and promotion gate are reproducible
  from immutable inputs.

### Task 13: Complete repository and knowledge validation A:I2 A:I4 A:I5 A:I6 A:I7
- [x] Run targeted tests, `pytest tests/ -x -q`, compile checks, `git diff
  --check`, and affected CLI/import checks.
- [x] Confirm production entry identity, replay separation, database consistency,
  and no new dead/parallel implementation.
- [x] Save the decision and failure lesson to AgentMemory and update the existing
  Obsidian owner note with date, evidence, affected paths, and gate result.
- [x] Verification: final receipt lists code diff, tests, replay artifact,
  candidate coverage, and any unresolved provider/pending rows.

### Task 14: Preserve the completed runner decomposition A:I2 A:I4 A:I6
- [x] Keep `xiaogu_forward_runner.py` as orchestration/CLI with `main()` as the
  only production entry.
- [x] Keep ranking, eligibility, snapshot, diagnostics, and feature assembly in
  their existing owners; replay remains in `xiaogu_backtest_v0_1.py`.
- [x] Verification already completed: runner `9154 -> 4730` lines, `499 passed`,
  health check `17/17`, import identity, and single CLI entry.

## T8 Execution Boundary

The completed decomposition is a structural prerequisite for the full-pool
analysis and possible strategy correction. It does not authorize a strategy
change by itself; promotion remains gated by Tasks 8-12.
