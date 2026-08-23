# August Full-Pool Main-Force Chain Upgrade

## Source Ask
> 用你现在的唯一链路、主力行为链路，分析整个 8 月的出票，不仅仅是 PAPER_PICK，还有当日的候选池子；当日有多少候选就分析多少候选。记录是哪条链路出的票、盈利还是亏损、为什么盈利或亏损，然后基于此直接修改主力行为链路。

## Normalized Goal
Use the current single `main_force_behavior_chain` as the production reference while
reconstructing every available August 2026 trading day from its recorded candidate
snapshot. Analyze every persisted candidate row, not only PAPER_PICK or Top10, with
its historical provenance, T-day admission/ranking outcome, and T+1 result when
settled. Use repeated, T-day-observable profit and loss patterns to make one
evidence-backed upgrade in the existing main-force chain.

## Non-Negotiables
- NN1: The only production decision chain remains scanner -> runner -> recorder/DB -> T+1 settlement -> scoreboard/API.
- NN2: Every available August date uses its exact recorded candidate count; do not pad, merge, or replace candidate pools.
- NN3: Historical chain identity is recorded as provenance for attribution, but all production behavior changes land only in the current main-force chain owners.
- NN4: T+1 returns, limit-up outcomes, and any other future fields are labels only; they cannot enter T-day ranking, admission, or gate decisions.
- NN5: Regulatory, data-quality, price, and buyability hard blocks remain intact unless a separately approved replay proves a safe change.
- NN6: No direct production DB mutation, live trading, broker connection, or second selection path.

## Hidden Contract Candidates
- HC1: A candidate in the pool is an analysis subject, not automatically a tradable PAPER_PICK; preserve the exact exclusion and admission reason.
- HC2: A historical row may have been emitted by an older chain or partial snapshot; provenance must not be mistaken for current production behavior.
- HC3: Missing T+1 settlement is an explicit pending/data-provider state, never a synthetic result.
- HC4: A profitable or limit-up candidate can still be correctly rejected at T time; post-decision profit alone does not authorize weakening a buyability gate.

## Plausible Interpretations
- PI1: Attribute the full August candidate population, then improve current main-force behavior only for repeated failure shapes that are observable at T time and pass a leakage-free replay.
- PI2: Treat every ex-post profitable candidate as a missed official pick and relax gates until the best pool member is selected.

## Chosen Interpretation
Use PI1. The objective is reproducible T-day selection for T+1 return, not retrospective perfect selection. Full-pool analysis supplies training evidence; it does not override tradability, safety, or replay gates.

## Rejected / Forbidden Narrowings
- FN1: Do not limit the study to PAPER_PICK, Top10, or only the losing dates.
- FN2: Do not compare candidates across different snapshots or silently collapse retries.
- FN3: Do not convert research/shadow/replay output into a PAPER_PICK path.
- FN4: Do not modify historical rows or manufacture missing returns.

## In Scope
- Complete August inventory of recorded candidate snapshots, candidate counts, historical provenance, admission state, formal rank/score, and T+1 settlement coverage.
- Candidate-level profit/loss and limit-up attribution for every available August row, grouped by the T-day reason that produced, admitted, ranked, or rejected it.
- Baseline replay of the current main-force chain against the exact recorded pools.
- One surgical modification to an existing main-force ranking, feature, admission, or diagnostic owner only when repeated evidence and benchmark gates justify it.

## Out of Scope
- Forcing ex-post winners into production, weakening hard safety rules from isolated cases, rewriting the scanner universe, or building a new strategy/runner.

## Success Signals
- Every available August candidate row has an auditable status: provenance, T-day decision path, settlement status, and reason.
- Settled rows have exact T+1 return labels and explainable profit/loss classifications; pending rows are explicitly excluded from performance claims.
- Baseline versus changed main-force replay reports candidate coverage, selected-pick outcomes, mean/median T+1, win rate, limit-up rate, downside, and hard-block regression.
- The changed production chain improves the required benchmark without future-field leakage; otherwise production behavior remains unchanged and the blocking evidence is recorded.

## Proof Requirements
- Codebase-memory call paths plus source/test confirmation for each owner.
- Read-only DB queries and immutable candidate snapshots; no manual DB edits.
- Candidate-level replay artifact, regression tests for observed shapes, full `pytest tests/ -x -q`, compile check, and diff validation.
- AgentMemory and Obsidian evidence closure after the decision.

## Draft Handoff
- Phase shape: inventory and lineage -> full-pool settlement/attribution -> baseline replay -> repeated failure-class selection -> one main-force change -> replay and validation.
- Planning red lines: do not silently narrow to PAPER_PICK/Top10, do not use future labels in T-day logic, and do not create a parallel chain.
