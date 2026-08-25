# T+1 Net Return Production Rewrite

**Goal:** Upgrade the existing sole production chain into an auditable
T+1-tradable-edge decision chain. Its only production objective is verified
`T1_NET_RETURN`; until a production-accepted prediction is available, the sole
valid decision is `NO_PICK`.

**Constraints:** Retain one runner, selector, execution contract, ledger, and
return backfill. Only T-day-available inputs may enter a prediction. Do not
create a second selector or write to the production database during tests.

**Out of scope:** Real trading, broker integration, model training on an
unsettled or incomplete data set, and tuning for any named historical winner.
Research-only fitting is allowed once canonical labels exist, but it cannot
promote a production model or create a `PAPER_PICK`.

## Normalized Goal

Remove every legacy production admission and ranking mechanism that selects
same-day strength rather than a model-predicted, cost-adjusted `T1_NET_RETURN`.
The existing sole chain must output `PAPER_PICK` only when a production-accepted
T-day prediction proves sufficient next-day edge; otherwise it must output
`NO_PICK`.

## Must-Haves

- MH1: `PAPER_PICK` is impossible without a production-accepted prediction of
  canonical `t1_net_return`. A:I12
- MH2: Legacy five-module scores, `PATH_*`, main-force scores, sector/news
  composites, and `expected_t1_profit_score` cannot filter, rank, or promote a
  production candidate. A:I12
- MH3: The sole selector ranks candidates only by predicted net return,
  cross-sectional edge, win probability, downside, uncertainty, and execution
  cost, after narrow tradability and regulatory gates. A:I12
- MH4: Missing, stale, leaked, or unaccepted model evidence produces an
  explainable `NO_PICK`, never a fallback to the old scorer. A:I12
- MH5: Regression tests prove the production path ignores old-score changes and
  rejects unaccepted prediction evidence. A:I12
- MH6: The only chain remains scanner -> runner -> recorder/DB -> T+1
  settlement -> scoreboard/API, with one selector owner. A:I12

### Task 1: Define the T+1 production-prediction contract in existing ranking owner A:I12
- [x] Replace the formal rank tuple with only canonical forward-return prediction
  fields and reject missing/non-finite values.
- [x] Keep the prediction as an explicit input record with model id, status,
  feature timestamp, available-at timestamp, costs, and uncertainty.
- [x] Verification: focused ranking tests prove legacy score fields cannot
  change the tuple.

### Task 2: Reduce production eligibility to hard tradability and evidence gates A:I12
- [x] Remove `t1_profit_candidate_profile`, PATH, setup, main-force, theme,
  news, and manual-score admission from the production candidate filter.
- [x] Retain only data freshness/provenance, time-bound availability,
  regulatory, buyability, liquidity/capacity, and severe risk blocks.
- [x] Verification: focused eligibility tests retain a weak-looking but valid
  candidate and reject untradable or leaked evidence.

- [x] Stop calling the legacy T+1-profit candidate gate before selection.
- [x] Require a `PRODUCTION` model prediction and minimum cost-covered edge for
  every `PAPER_PICK`; otherwise emit `NO_PICK` with a deterministic reason.
- [x] Verification: runner tests prove no old-score fallback and one official
  selection owner.

### Task 4: Remove legacy production-decision influence and update test fixtures A:I12
- [x] Delete obsolete formal five-module/PATH priority use from the active
  production path while leaving raw snapshot fields available for research.
- [x] Update tests that encoded the retired manual scoring contract.
- [x] Verification: affected test modules pass with old-score perturbation
  invariance.

- [x] Run focused tests, full `pytest tests/ -x -q`, compile checks, and
  `git diff --check`.
- [x] Verification: all commands complete without failures.

- [x] Re-run the current-day chain dry-run and record whether it correctly
  yields `PAPER_PICK` or `NO_PICK` under the accepted-model contract.
- [ ] Re-index the codebase-memory graph and trace the runner-to-selector path
  to prove one decision owner remains; cross-check the maintained
  Understand-Anything graph.
- [x] Verify the production chain remains scanner -> runner -> recorder/DB ->
  T+1 settlement -> scoreboard/API and that current source, tests, and
  codebase-memory call tracing prove one selection owner.
- [ ] Verification: dry-run and architecture evidence agree with the contract.

### Task 7: Close the architectural change record A:I12
- [x] Refresh AgentMemory and record the resulting architecture decision in the
  A-share Obsidian vault.
- [x] Verification: durable records cite the code path and completed tests.

### Task 8: Complete canonical net-return label recovery A:I12
- [x] Recover missing `t1_net_return` and cost fields from persisted
  `settlement_evidence.execution_model` through the existing return-backfill
  owner.
- [x] Preserve existing canonical labels and refuse rows without persisted
  execution evidence.
- [x] Add CLI dry-run mode and regression coverage.
- [x] Verification: 3,630 August canonical rows have `t1_net_return`; no
  repairable rows remain. Three August 18 rows remain unresolved because
  their execution evidence is explicitly `NOT_FILLABLE`, not because a zero
  return was substituted.

### Task 9: Generate auditable research predictions without promotion A:I12
- [x] Freeze the existing price/volume baseline parameters using only settled
  rows strictly before the signal date.
- [x] Emit `expected_t1_net_return`, `p_win`, downside, uncertainty, cost, and
  tradable edge with explicit timestamps.
- [x] Attach one `RESEARCH` prediction to the Runner candidate snapshot through
  the existing single decision owner.
- [x] Verification: prediction and Runner attachment tests pass; production
  selector rejects `RESEARCH` status and cannot create a second pick.
- [ ] Production acceptance remains blocked until target history has at least
  the required Walk-Forward trading days and OOS/regime gates pass.
