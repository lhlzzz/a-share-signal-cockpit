# NO_PICK Fallback to Highest Score

**Goal:** when the runner would otherwise emit `NO_PICK`, the final public result should become the day's highest-scoring candidate, while preserving the original NO_PICK diagnostic trail and runtime evidence. In the same package, fix the scanner timeout path that prevents the daily bundle from completing so the final output is always complete, with full evidence domains retained.

**Constraints:** keep scanner-first / DB-first / runner-consume-only intact; do not add broker execution or a parallel decision path; preserve the existing observation ticket and diagnostic metadata for replay; scanner timeout fixes must end with a complete persisted bundle, not a partial one, and must not shrink evidence domains or candidate scope.

**Out of scope:** scanner refactors, new factors, live trading, or reworking the core ranking model.

## Must-Haves

- MH1: A run that would have ended as `NO_PICK` is promoted to the day's highest-score candidate in the final output.
- MH2: `daily_best_paper_watch` and the original NO_PICK diagnostics remain present for replay and audit.
- MH3: The runtime snapshot, JSON output, and DB-facing data stay internally consistent after the promotion.
- MH4: Regression tests assert the new output semantics instead of the old empty `NO_PICK` behavior.
- MH5: Scanner timeout remediation is included and must keep the final bundle complete, durable, and full-domain.
- MH6: Remaining cleanup items are listed clearly so later execution can finish them without guessing.

### Task 1: Confirm the fallback decision path in runner output
- [ ] Verify the runner promotes a NO_PICK case to the highest-scoring candidate, not to an empty observation-only result.
- [ ] Keep the original diagnostic block and fallback reason in the runtime features payload.
- [ ] Verification: a dry-run or test case shows `decision=PAPER_PICK` and a non-empty `symbol` for a previously NO_PICK scenario.

### Task 2: Preserve NO_PICK diagnostics and observation ticket metadata
- [ ] Ensure `daily_best_paper_watch` remains attached even when the decision is promoted.
- [ ] Keep `fallback_from_no_pick`, `fallback_source`, `fallback_original_decision`, and `fallback_original_reason` in the candidate features.
- [ ] Verification: the runtime snapshot still contains the original NO_PICK diagnostic trail after promotion.

### Task 3: Align runtime snapshot and DB-facing fields
- [ ] Confirm the JSON runtime decision context, top-level output, and persisted record all reflect the same promoted candidate.
- [ ] Ensure the promotion does not drop existing fields used by recorder, DB insert, or audit tooling.
- [ ] Verification: a promoted NO_PICK run produces consistent `decision`, `symbol`, and candidate feature fields across outputs.

### Task 4: Update regression tests for the new semantics
- [ ] Change the NO_PICK tests so they expect `PAPER_PICK` plus fallback metadata instead of an empty NO_PICK output.
- [ ] Keep tests asserting that the original diagnostic metadata is still present.
- [ ] Verification: targeted runner tests around NO_PICK promotion pass with the new expectations.

### Task 5: Capture any remaining repair items as an executable follow-up list
- [ ] List any remaining contract, compatibility, or output-shape gaps discovered during the change.
- [ ] Separate already-fixed issues from still-open items so mimocode can continue without re-discovery.
- [ ] Verification: the task package clearly states what is done and what is still pending.

### Task 6: Fix the scanner timeout path that blocks a complete daily bundle
- [ ] Trace the timeout hotspot in the scanner's candidate-detail evidence phase and identify the exact loop/batch that exhausts the pipeline budget.
- [ ] Reduce the timeout pressure without dropping required evidence from the final persisted summary, so the bundle still writes out completely.
- [ ] Prefer structural fixes such as bounded batching, phased persistence, early summary emission, or skipping only non-essential retry loops over simply increasing the timeout budget.
- [ ] Verification: a real-time run no longer dies before writing the summary/bundle, and the final output file is present and readable.

### Task 7: Reduce candidate-detail evidence fanout to fit the realtime budget
- [ ] Replace the one-shot full-fanout pattern with a two-phase pipeline: first persist a complete candidate summary, then continue detail evidence collection in bounded batches.
- [ ] Keep `candidate_detail_topn` aligned with the current input set, but do not use it to cut evidence domains or candidate completeness.
- [ ] Verification: a realtime run can emit a complete summary before deeper evidence finishes, and the final bundle is still full-domain when all phases complete.

### Task 8: Make retry and post-score evidence phases non-blocking for final bundle completion
- [ ] Separate the must-have evidence pass from retry-only passes so retry exhaustion does not prevent final file emission.
- [ ] Make `post-score sectors` and `extra_detail` steps best-effort after the minimum viable summary is already persisted, but keep the full-domain evidence slots in the final artifact.
- [ ] Preserve hard-block and candidate ranking data even when optional evidence steps are skipped, and continue the remaining evidence phases asynchronously or in a resumable pass.
- [ ] Verification: timeout on optional retry phases no longer prevents summary/bundle completion, and the final artifact can still be fully hydrated later.
