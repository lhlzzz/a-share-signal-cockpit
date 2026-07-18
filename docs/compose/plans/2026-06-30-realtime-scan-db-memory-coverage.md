# Realtime Scan Stability, Memory Fix, and DB Reuse Package

**Goal:** make the realtime xiaogu chain stable and replayable: scanner finishes first, persists a complete scan snapshot, runner consumes the persisted artifact only, and all scan inputs/decisions/evidence are stored for reuse and review.

**Constraints:** keep paper-only / no-trade / hard-block rules intact; do not create a parallel pipeline; use existing scanner/runner/DB files first; prefer codebase-memory-mcp for discovery, RTK for filtered command output, and CloakChrome / browser-cdp when browser-backed validation is required. Memory increase is allowed only as a guardrail after profiling, not as the root fix.

**Out of scope:** live trading, broker integration, adding a second scanner chain, or weakening current safety gates just to force a pick.

## Must-Haves

- MH1: Every scan run leaves a durable record of inputs, top candidates, score breakdown, blockers, source_time/asof, and final decision that can be queried later.
- MH2: The runner nominal path consumes only scanner-produced artifacts and does not online-fetch to patch missing data.
- MH3: The current memory-pressure / timeout failure mode is eliminated through profiling and structural fixes such as streaming, chunking, lazy loading, and cache cleanup.
- MH4: Daily scan data, including NO_PICK days, becomes reusable DB-backed research asset instead of throwaway terminal output.
- MH5: Coverage auditing includes the main-force / hot-money view and flags missing high-signal domains, not just the user-listed categories.
- MH6: The current realtime path can be replayed end-to-end and proves that the runner used the persisted scan artifact.

### Task 1: Audit the realtime scan-to-runner contract and failure points
- [ ] Trace the current scanner output, DB write, and runner load path to identify where scan artifacts are created, persisted, and consumed.
- [ ] Reproduce the memory / timeout failure mode on the current workload and capture the failing stage, peak memory point, and whether the failure happens before or after persistence.
- [ ] Record the current contract in code comments or the existing runtime log path so the boundary is explicit for future runs.
- [ ] Verification: a short run log that shows the scanner source_time, the persisted artifact location, and the runner consumption path with the observed failure or success point.

### Task 2: Persist scan provenance and decision evidence into DB-backed storage
- [ ] Extend the scan write path so each daily run stores the candidate list, top-N ranking, score breakdown, decision rationale, blockers, and source_time/asof in a queryable form.
- [ ] Ensure NO_PICK days are saved with the same provenance fields as pick days.
- [ ] Persist enough metadata to reconstruct “why this ticket was chosen / rejected” later without re-running the scanner.
- [ ] Verification: DB queries show one row set per run with candidate provenance and decision rationale, and a replay read can reconstruct the same top candidate set.

### Task 3: Remove the memory bottleneck instead of masking it
- [ ] Profile the heavy load points in scanner / runner / DB interaction and identify which reads can be streamed, chunked, or lazily loaded.
- [ ] Replace any all-at-once loading pattern with bounded reads and explicit cache cleanup.
- [ ] If the environment limit is still the true ceiling after profiling, raise the memory cap only as a guardrail and document why.
- [ ] Verification: rerun the previously failing workload without OOM or timeout, and capture the peak-memory improvement or failure elimination.

### Task 4: Expand the coverage audit from a main-force / hot-money perspective
- [ ] Audit the scan payload for full coverage of the market views needed to explain strong-money behavior, including sector heat, concept linkage, limit-up / limit-down quality, intraday strength, big-order / capital-flow signals, northbound flow, announcements, risk alerts, and board membership / follower effects.
- [ ] Add missing high-signal domains discovered during the audit, even if they were not in the user’s original checklist.
- [ ] Store the coverage result per run so gaps are visible later during factor upgrades.
- [ ] Verification: a coverage report or DB view lists covered vs missing domains for at least one real scan.

### Task 5: Replay the current realtime path end-to-end
- [ ] Run the scanner first, then run the runner against the produced artifact without online fallback.
- [ ] Confirm the runner output matches the persisted scan provenance and that the decision record is stored.
- [ ] Confirm the same path works for the current 14:33-style realtime input and not only for a delayed batch replay.
- [ ] Verification: logs / DB evidence show scanner-first execution, runner artifact consumption, and a reproducible decision record.

## Follow-up: Bundle Contract Cleanup

**Goal:** remove the remaining contract regressions so DB-backed bundles, scan-summary-backed bundles, and realtime bundles expose the same stable fields and the same replay semantics.

**Constraints:** keep the existing scanner-first contract; do not reintroduce online fetching into the nominal runner path; preserve the new DB-first memory fix; avoid widening scope into strategy changes.

**Out of scope:** new factors, new scoring rules, or a second runtime chain.

## Must-Haves

- MH7: `load_candidate_bundle()` returns a bundle with stable `candidate_source`, `paper_scoring_candidates`, `candidate`, and `_bundle_path` fields for both DB-backed and scan-summary-backed inputs.
- MH8: The returned `_bundle_path` points to a real, readable artifact, not a placeholder path.
- MH9: DB-backed bundles are preferred when available, and scan-summary-backed bundles are used only when they are the canonical live source for that replay case.
- MH10: The bundle contract is explicit enough that existing regression tests can be updated to the current behavior instead of failing on missing fields.

### Task 6: Unify bundle source precedence and field completeness
- [ ] Make `load_candidate_bundle()` select the canonical source deterministically instead of letting an incidental scan-summary file override a DB-backed bundle.
- [ ] Ensure DB-backed bundles and scan-summary-backed bundles both populate `candidate_source`, `paper_scoring_candidates`, `candidate`, and `available` consistently.
- [ ] Verification: a DB-backed replay and a scan-summary replay both expose the same required top-level fields in the returned bundle.

### Task 7: Persist the replay artifact path as a real file
- [ ] Ensure `_ensure_current_realtime_bundle_path()` and the DB bundle builder both write the returned bundle to disk so `_bundle_path` exists.
- [ ] Keep `scan_summary_path` and `_bundle_path` distinct when they point to different artifacts, but never return a nonexistent path.
- [ ] Verification: `Path(bundle['_bundle_path']).exists()` passes for both DB-derived and realtime-derived bundles.

### Task 8: Align tests with the stabilized bundle contract
- [ ] Update the regression tests that encode the old bundle precedence assumptions so they validate the current canonical-source behavior.
- [ ] Keep the tests for DB merge behavior, signal merge behavior, and runtime-date selection, but make them assert the new stable field contract instead of incidental path names.
- [ ] Verification: the targeted runner test subset passes after the contract cleanup.

## Follow-up: Dual-Index Discovery and Final Hygiene

**Goal:** make `codebase-memory-mcp` and Understand-Anything mutually corroborating tools for xiaogu work, while keeping source code and tests as the authority and finishing the remaining bundle-contract hygiene.

**Constraints:** do not treat Understand-Anything as a runtime dependency; do not let either index override source truth; keep the current scanner-first / DB-first execution contract unchanged.

**Out of scope:** reworking strategy logic, adding new scoring factors, or creating another knowledge graph pipeline.

## Must-Haves

- MH11: The repo documents a clear discovery order: codebase-memory-mcp first for exact symbols/calls, Understand-Anything second for architecture corroboration only.
- MH12: At least one real xiaogu subsystem has been cross-checked by both indexes, and any mismatch has been recorded or resolved.
- MH13: The remaining bundle contract regressions are eliminated so DB-backed, scan-summary-backed, and realtime bundles all expose stable required fields.
- MH14: `candidate_source` and `_bundle_path` are always explicit and non-placeholder in the returned bundle contract.

### Task 9: Codify dual-index discovery roles
- [ ] Update the relevant repo guidance so `codebase-memory-mcp` remains the primary discovery source and Understand-Anything is explicitly limited to architecture-level corroboration.
- [ ] Add a short operating note for xiaogu work that says when to trust source code, when to compare with the knowledge graph, and when to ignore graph-only differences.
- [ ] Verification: the repo guidance contains a single, unambiguous discovery order and the fallback behavior when one tool is unavailable.

### Task 10: Cross-check one xiaogu subsystem with both indexes
- [ ] Pick one hot path already touched in this turn, such as `load_candidate_bundle()` or the scanner-to-runner contract, and compare the symbol/call structure between codebase-memory and Understand-Anything.
- [ ] Record any discrepancy between the two views as either a real code issue or a graph limitation; do not silently merge them.
- [ ] Verification: a short audit note or task artifact lists the compared subsystem, the match/mismatch summary, and the final source-of-truth decision.

### Task 11: Finish bundle contract hygiene
- [ ] Make `load_candidate_bundle()` deterministic about source precedence so DB-backed bundles are not accidentally overridden by incidental scan-summary files.
- [ ] Ensure returned bundles always carry `candidate_source`, `paper_scoring_candidates`, `candidate`, `available`, `scan_summary_path` when applicable, and a real `_bundle_path`.
- [ ] Update the targeted regression tests so they assert the stabilized contract rather than old path-name assumptions.
- [ ] Verification: targeted runner tests pass and the bundle contract fields are present in both DB-backed and scan-summary-backed replay cases.
