# August T+1 Candidate Attribution and Main-Force Repair

**Goal:** Diagnose why the production `PAPER_PICK` selections for
2026-08-17 through 2026-08-20 underperformed while better T+1 outcomes may
exist in their recorded 400-stock pools. First prove the canonical scanner
snapshot and same production ranking entry for every available August 2026
trading day through 2026-08-21, then restore a single reproducible settlement
path that records T+1 labels for every candidate in each immutable snapshot.
Use those labels to improve the existing main-force behavior ranking without
leakage; only after the behavior is locked, reduce runner complexity without
introducing another production path.

**Constraints:** Preserve scanner -> runner -> recorder/DB -> T+1 settlement ->
scoreboard/API ownership. Modify existing files only. Use `t1_return` strictly
as a post-decision label. Keep regulatory, data-quality, and buyability hard
blocks intact. No direct production DB changes.

**Out of scope:** Live trading, broker connections, a new selection engine,
shadow/watch promotion, changing the candidate universe, or an unproven score
weight change.

## Must-Haves

- MH1: Every available August 2026 trading day through 2026-08-21 has one
  canonical scanner snapshot identity or an explicit missing-source reason;
  historical comparison never mixes a retry snapshot with a different
  candidate pool. A:I5 A:I6
- MH2: Historical re-runs use the same main-force ranking, T-day gates, and
  candidate-universe semantics as production, but cannot create an active run,
  replace a `PAPER_PICK`, or read T+1 fields. A:I5 A:I6
- MH3: Every active production snapshot has a single settlement owner and a
  clear terminal settlement state; stale/failed runs remain auditable. A:I1 A:I6
- MH4: The only production chain remains scanner -> runner -> recorder/DB ->
  T+1 settlement -> scoreboard/API; shadow and research artifacts cannot
  select, settle, or activate `PAPER_PICK`. A:I1 A:I6
- MH5: Each completed active 400-stock candidate snapshot persists an
  immutable, source-evidenced T+1 label for all candidates, not only the
  official pick. A:I5
- MH6: The system can classify an ex-post profitable or limit-up candidate by
  its exact T-day rank, admission result, hard block, or lower formal score.
  A:I5
- MH7: Any main-force ranking change uses only T-day fields and improves the
  completed August replay against the recorded baseline without worse downside
  or a hard-block regression. A:I5 A:I6
- MH8: After ranking behavior is validated, `xiaogu_forward_runner.py`
  shrinks through behavior-preserving extraction into existing owners; no
  alternate runner or selection path is created. A:I2 A:I3 A:I4 A:I6

### Task 1: Audit canonical August scanner snapshots A:I5 A:I6

- [ ] For each available August 2026 trading day through 2026-08-21, query
  recorded `scan_sessions`, `scan_market_data`, raw summary path, candidate
  snapshot ID, candidate count, and a deterministic symbol/content hash.
- [ ] Treat same-day retries with unequal 400-candidate hashes as distinct
  snapshots; select the active production snapshot when present, otherwise
  retain each failed/retry snapshot as non-comparable audit evidence.
- [ ] Record only immutable identifiers and source completeness diagnostics;
  do not manufacture missing scanner data or merge candidates across sessions.
- [ ] Verification: a read-only fixture/query test proves one comparison
  cohort per date/run and explicitly reports any non-400 or missing snapshot.

### Task 2: Re-run canonical snapshots through the production ranking without activation A:I5 A:I6

- [ ] Locate the existing replay owner that can load the immutable scanner and
  candidate snapshot while invoking the current `formal_candidate_sort_key`,
  T-day admission, and per-candidate decision logic.
- [ ] Make historical replay explicit and non-active: no `production_run_active`
  write, recorder append, active-pick correction, or use of
  `future_return_fields_placeholder` in a decision field.
- [ ] Persist or emit a deterministic replay identity consisting of source
  snapshot ID, rule/scoring hash, formal-rank snapshot hash, runner mode, and
  per-candidate rank/admission outcome.
- [ ] Verification: replay fixtures show identical ranking for an immutable
  snapshot, a failed activation attempt, and leakage rejection when a future
  field is injected.

### Task 3: Reconcile production-run and settlement ownership A:I1 A:I6

- [ ] Trace scheduler, `daily_pipeline.sh`, legacy result filler, and
  `scripts/xiaogu_return_backfill.py` to identify every production settlement
  caller and the current source of `production_run_active`.
- [ ] Make `scripts/xiaogu_return_backfill.py` the only scheduled production
  T+1 settlement entry point; retain the legacy filler only as a non-scheduled
  compatibility CLI until its direct contracts are removed in a separate
  manifest-approved cleanup task.
- [ ] Ensure the chosen settlement owner closes or fails the required
  `t1_settlement` step with a deterministic retry command and never activates
  a failed run.
- [ ] Verification: targeted scheduler/pipeline tests prove no scheduled path
  invokes the legacy filler; database fixture tests prove a settled run has
  one active run and terminal settlement state; read-only diagnostic queries
  prove that failed snapshots never become active.

### Task 4: Persist candidate-level T+1 labels in the existing data model A:I5 A:I6

- [ ] Extend the existing return persistence owner so a settled
  `(production_run_id, candidate_snapshot_id, symbol)` writes its T+1 result
  and immutable settlement evidence to the matching `daily_candidates`
  `future_return_fields_placeholder` payload.
- [ ] Extend `scripts/xiaogu_return_backfill.py` from the existing Top10
  diagnostic scope to the complete immutable production candidate snapshot
  when invoked by the production T+1 workflow; preserve explicit Top10 scope
  only for bounded historical diagnostics.
- [ ] Preserve the replay leakage guard: decision snapshots must not consume
  `future_return_fields_placeholder`, and production re-decision must reject
  future fields outside that placeholder.
- [ ] Verification: fixtures cover all 400 candidates, duplicate settlement,
  production-run isolation, and no mutation of formal score/eligibility;
  existing leakage tests remain green.

### Task 5: Repair and verify historical August settlement evidence A:I5

- [ ] After Tasks 1 and 2 pass their targeted tests, use the existing backfill
  CLI with explicit immutable run IDs for 2026-08-17 through 2026-08-20; do
  not alter rows manually.
- [ ] Reconcile 2026-08-17 and 2026-08-18 terminal run state from recorded
  settlement evidence, retaining failed replay runs as non-active audit
  records.
- [ ] Record a coverage report by date and run: 400 labels expected,
  settled labels, failed fetches, and reason codes. Do not compare runs until
  coverage is 100% or a documented data-provider failure gate is reached.
- [ ] Verification: read-only DB checks show each analyzed run's snapshot ID,
  return coverage, and official pick return agree with the stored evidence;
  the report reads immutable recorded candidate snapshots and never writes
  scoring, eligibility, or `PAPER_PICK` fields.

### Task 6: Attribute August ranking misses with the existing research owners A:I5 A:I6

- [ ] Use `xiaogu_backtest_v0_1.py` and stored candidate snapshots to classify
  every positive-T+1 or limit-up candidate as: current-day untradable,
  regulatory/buyability/data hard block, T+1 admission rejection, eligible but
  lower formal rank, already-held exclusion, or winner.
- [ ] Report baseline versus pool and rank cohorts: official-pick win rate,
  mean/median T+1, limit-up rate, worst return, Top1/Top10/full-pool coverage,
  and reason distributions.
- [ ] Compare findings with the A-share knowledge records for the selected
  dates; use knowledge only as evidence context, never as a replacement for
  stored market/return data.
- [ ] Verification: the report uses only immutable snapshot IDs, passes the
  existing future-field leakage gate, and is reproducible from DB inputs.

### Task 7: Make one evidence-backed main-force correction only if replay passes A:I5 A:I6

- [ ] Define the smallest candidate change inside the existing
  `formal_candidate_sort_key`, admission profile, or hard-gate owner based on
  Task 4's dominant, repeated failure class.
- [ ] Add regression cases for the observed false-positive and missed-winner
  shapes, including unchanged regulatory and buyability blocks.
- [ ] Run a leakage-free August replay against the exact baseline snapshot and
  require: non-decreasing completed-pick win rate, higher mean T+1 return,
  no higher worst-loss magnitude, and no lower completed coverage.
- [ ] Verification: targeted tests, `pytest tests/ -x -q`, compile check,
  `git diff --check`, and baseline-versus-change replay artifact all pass
  before enabling the changed production score.

### Task 8: Reduce runner complexity only after strategy behavior is stable A:I2 A:I3 A:I4 A:I6

- [x] Inventory the post-Task-7 runner by responsibility, callers, test
  coverage, and line count; identify only behavior-preserving extraction
  candidates with a single owner in an existing module or an unavoidable
  narrowly scoped owner.
- [x] Apply the Ponytail decision ladder: reuse the standard library or
  existing `xiaogu_forward_eligibility.py`, `xiaogu_forward_persistence.py`,
  and `xiaogu_forward_bundle_io.py` before adding code; no parallel runner,
  selector, or score implementation.
- [x] Remove the moved duplicate from `xiaogu_forward_runner.py` in the same
  change and preserve its public import/call contracts until callers migrate.
- [x] Verification: import identity, focused runner tests, full test suite,
  behavior snapshot comparison, and files/lines-before-versus-after evidence
  show lower runner complexity with unchanged production semantics.

#### T8 execution boundary (2026-08-22)

The runner is decomposed into six responsibility owners while retaining one
production entry:

1. `xiaogu_forward_runner.py`: production orchestration and CLI.
2. `xiaogu_forward_ranking.py`: main-force formal ranking and
   `formal_candidate_sort_key`.
3. `xiaogu_forward_eligibility.py`: T+1 admission plus regulatory,
   tradability, and position-related eligibility gates.
4. `xiaogu_forward_snapshot.py`: formal snapshot freeze and rank validation.
5. `xiaogu_forward_diagnostics.py`: Top10, NO_PICK, rank-alignment, and
   candidate-consumption explanations.
6. `xiaogu_forward_features.py`: T-day feature assembly, including capital
   flow, theme/news, similar-case and research/native evidence composition.

Historical replay remains owned by `xiaogu_backtest_v0_1.py`; the extracted
feature owner only supplies T-day feature composition used by replay. No
replay or diagnostic function may activate or produce `PAPER_PICK`.

The extracted modules use the existing host-binding pattern and the runner
re-exports public symbols during migration. Acceptance requires unchanged
formal ranks, gates, snapshot IDs, decision semantics, and replay behavior.
