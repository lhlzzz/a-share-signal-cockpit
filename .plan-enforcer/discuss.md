# Whole-Repository Redundancy Reduction

## Source Ask
> 仅作为开发期瘦身工具，不得改动行情、评分、门 禁、DB 或 PAPER_PICK。  你应该把这个仓库接入全部代码 因为这是一个已经成型仓库 有很多处代码都是冗余的 或者说无用的配合understand 一起把多余的清理掉

## Normalized Goal
Apply Ponytail's deletion-first discipline across all tracked code: production,
research, backtest, maintenance, and tests. Use Understand-Anything as an
architecture view and codebase-memory plus source/tests as the authority for
candidate evidence. Remove redundancy and dead code incrementally without
changing strategy behavior.

## Non-Negotiables
- NN1: Cover all tracked Python, shell, and test code in the audit inventory;
  "all code" does not mean every file must be modified.
- NN2: Preserve the one production chain: scanner -> runner -> recorder/DB ->
  T+1 filler -> scoreboard/API.
- NN3: A candidate may be deleted only with inbound/outbound call evidence,
  source confirmation, and validation that covers its public or dynamic
  contracts.
- NN4: Preserve paper-only safety and the current `PAPER_PICK`/`NO_PICK`
  contract. Semantics changes require a separate explicitly approved task.
- NN5: No mass deletion, parallel implementations, compatibility copies, or
  weakened validation.

## Hidden Contract Candidates
- HC1: Host-bound modules (`xiaogu_forward_eligibility.py`,
  `xiaogu_forward_bundle_io.py`, and `xiaogu_forward_persistence.py`) obtain
  runner symbols dynamically.
- HC2: CLI scripts, scheduler jobs, retry commands, and tests can be
  indirect callers even when codebase-memory reports no ordinary CALLS edge.
- HC3: Research and shadow modules are isolated from official decisions but
  remain required for replay, diagnostics, or stored evidence.
- HC4: Repeated strategy predicates can be consolidated only when boundary
  behavior is locked by a regression test.

## Plausible Interpretations
- PI1: Audit every code area and delete only behavior-proven redundancy and
  dead code in small validated batches.
- PI2: Delete all research, shadow, backtest, or large modules to minimize
  line count.

## Chosen Interpretation
Use PI1. Ponytail applies to all code ownership areas, including production
strategy modules, but it supplies a disciplined implementation choice rather
than a license to alter business behavior or delete active diagnostic paths.

## Rejected / Forbidden Narrowings
- FN1: Do not limit the audit to `xiaogu_forward_runner.py`.
- FN2: Do not treat an unused same-file reference, graph gap, or line count as
  sufficient deletion evidence.
- FN3: Do not remove quarantine, persistence, replay, lifecycle closure, or
  validation to reduce line count.
- FN4: Do not replace explainable scoring logic with terse opaque expressions.

## In Scope
- Refresh Understand-Anything's file/architecture scan and codebase-memory's
  graph before each affected ownership area.
- Build and maintain a deletion manifest with qualified name, ownership,
  callers, dynamic-contract check, replacement, and test proof.
- Consolidate exact duplicate behavior into one existing owner and delete
  proven dead paths from every tracked code area.
- Apply Ponytail to production, research, backtest, maintenance, and test
  code without adding dependencies or parallel implementations.

## Out of Scope
- Changing scoring policy, gates, database schema, or lifecycle semantics.
- Removing a module solely because it is large, research-facing, or has no
  direct static caller.
- Committing, pushing, or modifying unrelated worktree state.

## Success Signals
- The audit inventory covers every tracked code area and labels each
  candidate as retained, consolidated, or deleted with evidence.
- Every removed behavior has an explicit replacement or proof that no active
  contract reaches it.
- The production chain remains single-owner and closed.
- The complete test suite, compile check, and production CLI contract pass
  after each deletion batch.

## Proof Requirements
- Understand-Anything architecture summary plus codebase-memory call paths.
- Per-candidate source, graph, dynamic-binding, and test evidence.
- Targeted tests for consolidated predicates and affected modules, followed by
  `pytest tests/ -x -q`, `compileall`, and `git diff --check`.
- A final scan for duplicate implementations and stale references.

## Draft Handoff
- Start with the largest active ownership areas, but delete only candidates
  whose evidence is complete.
- Keep the existing runner decomposition plan as the single execution plan;
  update it rather than creating versioned plan files.
