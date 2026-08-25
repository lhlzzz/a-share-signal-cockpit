# Xiaogu T+1-Only Production Rewrite

## Source Ask
> 那你就全部删掉 只保留对t日出票 t+1可获利的标的 就行

## Normalized Goal
Remove every legacy production admission and ranking mechanism that selects
same-day strength rather than a model-predicted, cost-adjusted `T1_NET_RETURN`.
The existing sole chain must output `PAPER_PICK` only when a production-accepted
T-day prediction proves sufficient next-day edge; otherwise it must output
`NO_PICK`.

## Non-Negotiables
- NN1: The production chain remains scanner -> runner -> recorder/DB -> T+1
  settlement -> scoreboard/API, with `xiaogu_forward_runner.main` as its sole entry.
- NN2: Five-module ranking, `PATH_*`, main-force/theme/news composites,
  `expected_t1_profit_score`, legacy final scores, and manual bonuses cannot
  decide, admit, rank, or promote a `PAPER_PICK`.
- NN3: Every production prediction must be based only on fields available at or
  before its declared T-day signal time, with model and provenance evidence.
- NN4: Missing or unaccepted model evidence must result in explainable
  `NO_PICK`; it may not fall back to the old scorer.
- NN5: Keep only hard data quality, provenance/freshness, regulatory,
  buyability, liquidity/capacity, and severe-risk blocks before the model.
- NN6: No direct production DB edits, real trading, broker connection, second
  runner, selector, return filler, or execution chain.

## Hidden Contract Candidates
- HC1: `T1_NET_RETURN` uses the canonical execution contract and costs; a
  close-return, high-return, or manual score cannot substitute for it.
- HC2: A candidate with a high old score must be behaviorally indistinguishable
  from one with a low old score when their T+1 prediction is identical.
- HC3: Research results and post-T-day labels cannot become production inputs.

## Chosen Interpretation
Replace the active formal sorting and pre-sort profit gate in the existing
owners. Preserve raw market fields for research and diagnostics only. With no
production-accepted prediction present today, `NO_PICK` is the correct safe
production result rather than continuing to emit a stock selected by legacy
signals.

## Rejected / Forbidden Narrowings
- FN1: Do not retune legacy weights or rename an old composite as an alpha model.
- FN2: Do not keep old signals as hidden tie-breakers, admission gates, or
  fallback logic.
- FN3: Do not fabricate a model prediction from a named historical winner or
  leak T+1 labels into a T-day snapshot.
- FN4: Do not create a parallel production chain or a second `PAPER_PICK`.

## In Scope
- Replace legacy formal rank inputs with explicit accepted-model prediction
  fields only.
- Remove old-factor influence from the active candidate filter and selector.
- Require a cost-covered predicted edge and model acceptance for `PAPER_PICK`.
- Prove the new boundary through focused and full tests plus a current-date
  dry-run.

## Out of Scope
- Training or promoting an unproven model, changing the scanner universe,
  connecting a broker, or changing settled historical returns.

## Success Signals
- Old-score perturbations cannot affect the formal rank, candidate admission,
  or decision.
- An accepted valid prediction produces a rankable `PAPER_PICK` candidate; a
  missing, stale, leaked, or unaccepted prediction produces `NO_PICK`.
- The sole chain and its recorder/filler/scoreboard ownership remain intact.

## Proof Requirements
- Current source, tests, and codebase-memory call tracing prove one selection
  owner and no legacy path to `PAPER_PICK`.
- Focused tests, full `pytest tests/ -x -q`, compile checks, and
  `git diff --check` pass.
- AgentMemory and, because the decision architecture changes, the A-share
  Obsidian vault record the result.

## Draft Handoff
- Replace active ranking contract -> slim active eligibility -> bind runner
  selection -> remove legacy active-path logic -> validate ownership and replay.
