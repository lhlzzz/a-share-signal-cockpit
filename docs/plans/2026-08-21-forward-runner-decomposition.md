# Whole-Repository Redundancy Reduction

**Goal:** Apply Ponytail's deletion-first discipline across all tracked Python,
shell, and test code. Use Understand-Anything as an architecture view and
codebase-memory plus source/tests as the authority for candidate evidence;
remove only code proven redundant or unreachable while preserving strategy
semantics.

**Normalized Goal:** Apply Ponytail's deletion-first discipline across all
tracked code: production, research, backtest, maintenance, and tests. Use
Understand-Anything as an architecture view and codebase-memory plus
source/tests as the authority for candidate evidence. Remove redundancy and
dead code incrementally without changing strategy behavior.

**Constraints:** Codebase-memory and source/tests are the authority for
definitions, callers, and behavior. Understand-Anything supplies the
architecture view. Preserve direct Eastmoney inputs, paper-only safety,
`PAPER_PICK`/`NO_PICK`, dynamic host bindings, database lifecycle closure, and
public import contracts.

**Out of scope:** Scoring-policy changes, database schema changes, blind mass
deletion, removal based only on file size or missing same-file callers, and
unrelated workspace state.

## Must-Haves

- MH1: NN1 — Cover all tracked Python, shell, and test code in the audit
  inventory; every file is assigned to an evidence-backed audit batch, not
  just the runner. A:I4
- MH2: Every deletion or consolidation names its owner, callers,
  dynamic-contract check, replacement, and verification. A:I4
- MH3: Production behavior remains scanner -> runner -> recorder/DB -> filler
  -> scoreboard/API, with unchanged `PAPER_PICK`/`NO_PICK` semantics. A:I1 A:I4
- MH4: The repository ends each cleanup batch with fewer lines or files and no
  validation regression. A:I3 A:I4

### Task 1: Refresh the whole-repository architecture inventory A:I4

- [ ] Refresh codebase-memory for the worktree and run Understand-Anything's
  project scan; record architecture layers and the size-ranked tracked source
  inventory.
- [ ] Classify every tracked `*.py`, `*.sh`, and `tests/*.py` file into one
  owner batch: scanner, runner/eligibility/persistence, database/API,
  research/backtest, maintenance, or tests.
- [ ] Verification (PR1): retain the Understand-Anything architecture summary
  plus codebase-memory call paths, then confirm each tracked Python, shell,
  and test file belongs to exactly one audit batch.

### Task 2: Build a deletion manifest from code and contract evidence A:I4

- [ ] For each candidate, record qualified name, file, inbound/outbound graph
  paths, Understand-Anything architecture layer, string/dynamic-import
  references, public CLI/API contract, and test coverage.
- [ ] Mark the candidate `DELETE` only when all contract checks are empty;
  mark duplicate behavior `CONSOLIDATE` only when one existing owner can retain
  the exact behavior.
- [ ] Verification: inspect every manifest entry against source and a
  codebase-memory `trace_path`; reject entries supported only by a missing
  same-file caller, graph omission, or architecture classification.

### Task 3: Consolidate exact duplicate strategy predicates A:I3 A:I4

- [ ] Keep `_count_leader_conditions` as the sole owner of the five climax
  leader predicates and make `is_strong_leader_candidate` delegate to it.
- [ ] Add a boundary regression test for one, two, and five satisfied
  predicates.
- [ ] Verification: run the targeted test and compare the `PAPER_PICK`/`NO_PICK`
  runner suite before and after the consolidation.

### Task 4: Delete only manifest-approved code in ownership batches A:I4

- [ ] Apply each approved `DELETE` or `CONSOLIDATE` entry in one owner batch at
  a time; update all direct callers rather than retaining compatibility copies.
- [ ] After each batch, re-query codebase-memory paths and source references
  for the deleted symbol, then run the named targeted tests.
- [ ] Verification: no stale references remain; a failed test or contract check
  rejects the deletion and keeps the original implementation.

### Task 5: Validate the closed production chain and repository health A:I1 A:I4

- [ ] Run `pytest tests/ -x -q`, `python3 -m compileall -q scrapy_scanner
  scripts xiaogu_*.py`, `python3 xiaogu_forward_runner.py --help`, and
  `git diff --check`.
- [ ] Recheck `daily_pipeline.sh` and scheduler entry points against the
  scanner -> runner -> recorder/DB -> filler -> scoreboard/API chain.
- [ ] Report files and line counts before/after, deleted symbols, retained
  high-complexity owners, and any candidates rejected for insufficient proof.
