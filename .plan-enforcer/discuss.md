# August T+1 Paper-Pick Attribution

## Source Ask
> 用17日采集的数据 主力行为链路出的票 需要为获利的票或者涨停的票，以此类推其他交易日也需要达成这样的情况

## Normalized Goal
Diagnose why the production `PAPER_PICK` selections for 2026-08-17 through
2026-08-20 lost money while higher-return T+1 candidates existed in the
recorded 400-stock pools. Use all completed August cohorts to measure which
production-chain layer excluded or under-ranked those candidates, then make
only evidence-backed improvements to the existing main-force behavior path.

## Non-Negotiables
- NN1: One production chain only: scanner -> runner -> recorder/DB -> T+1
  filler -> scoreboard/API.
- NN2: Modify existing owners; do not add a selection path, parallel runner,
  or a future-outcome feature.
- NN3: T+1 returns are labels for diagnosis/replay only and cannot be read by
  a T-day decision.
- NN4: Preserve paper-only operation, regulatory/buyability hard blocks, and
  full reproducibility from stored database state.
- NN5: Any strategy change needs August replay, a baseline comparison, and
  targeted regression tests before production use.

## Hidden Contract Candidates
- HC1: A profitable pool member may be non-tradable, outside the formal
  universe, or excluded by a legitimate hard safety gate.
- HC2: Current shadow/replay logic is diagnostic-only and must not become a
  second production decision path.
- HC3: `PAPER_PICK` may be intentionally absent (`NO_PICK`) rather than a
  selection-ranking failure.

## Plausible Interpretations
- PI1: Improve the main-force behavior ranking/gates only when an August
  out-of-sample-style replay proves better returns without worse risk.
- PI2: Force the ex-post best T+1 stock to become every day's pick.

## Chosen Interpretation
Use PI1. A deterministic "every day profits" guarantee is not a valid
quantitative acceptance criterion; the system must improve expected,
risk-adjusted T+1 outcomes using only T-day information.

## Rejected / Forbidden Narrowings
- FN1: Do not infer a scoring defect merely because an ex-post winner existed.
- FN2: Do not weaken regulatory, tradability, or data-quality hard blocks to
  increase retrospective hit rate.
- FN3: Do not make shadow/watch candidates official without a replay gate.

## In Scope
- Verify worktree cleanliness, active run identity, unique production call
  chain, and score/gate ownership.
- Build day-level and August-wide attribution from stored candidates, picks,
  returns, decision diagnostics, and A-share knowledge evidence.
- Identify and test the smallest explainable correction in the existing
  main-force production owner if and only if replay proves it.

## Out of Scope
- Live trading, direct production DB edits, external broker integration,
  replacing the production chain, or widening the selection universe without
  evidence.

## Success Signals
- Every profitable/limit-up candidate investigated is classified by exact
  T-day exclusion or ranking reason.
- The August replay is leakage-free and reports baseline versus candidate
  change: pick rate, win rate, mean/median T+1 return, limit-up rate, and
  downside metrics.
- A production change is promoted only when it improves the defined metrics
  without violating hard blocks.

## Proof Requirements
- Read-only database queries and immutable recorded candidate snapshots.
- Codebase-memory call paths plus source confirmation for each decision layer.
- Targeted tests, complete `pytest tests/ -x -q`, and replay evidence.

## Draft Handoff
- Phase shape: establish evidence -> attribute all August misses -> define
  acceptance thresholds -> make one surgical main-chain change -> replay and
  validate.
- Planning red lines: no new decision path; no use of T+1 fields in T-day
  scoring; retain historical diagnostics.
