# Xiaogu 5D Alpha Evidence Lock

## Scope

Diagnose and, only where evidence requires it, correct the existing 5D
production Alpha. The target remains `PROFIT_WINDOW_5D`; the existing Feature
Engine, Research Context, Core Alpha, Portfolio Decision, ledger, returns table,
replay, and API remain the owners. No parallel selector, Alpha, database, or
decision path is introduced.

## Contract

- Maximum holding boundary: five trading days.
- T+1 through T+5 are evaluation/window-capture days, not a T+1 strategy.
- Core Alpha emits only the approved 5D outputs and explanations; legacy T+1 and
  longer-horizon Alpha fields are forbidden.
- `evaluate_candidate_bundle()` is the only portfolio decision owner.
- Research adapters remain context-only and cannot emit BUY.
- Historical replay calls the production Feature, Research, Alpha, and Decision
  owners before attaching future OHLC bars.
- Calibration is not validation. `CALIBRATED` or an existing artifact cannot
  enable BUY when OOS, monotonicity, separation, or baseline-increment gates
  fail.
- Daily OHLC highs are `DAILY_BAR_APPROXIMATION` unless execution evidence
  exists; the canonical cost model is marked `RESEARCH_APPROXIMATION`.
- Historical source rows are read-only and no sample is deleted to improve a
  result.

## Tasks

1. Audit the current Alpha, Decision Owner, replay inputs, calibration artifact,
   and existing OOS metrics without changing production behavior.
2. Run a read-only historical data and feature diagnostic for every approved
   production feature: distribution, missingness, uniqueness, collinearity,
   clipping/collapse, class balance, score variance, and probability separation.
3. Extend the existing evaluation owner with deterministic, same-dataset,
   same-time-split, same-target, same-cost feature-group ablation reporting.
4. Run the required cumulative and single-family ablations, including top 10/20/30
   percent, coverage, ranking, calibration, return, MAE, drawdown, and factor
   increment evidence.
5. Correct only evidenced feature/model defects in the existing Core Alpha and
   calibration path; otherwise demote non-incremental factors to diagnostics or
   remove them after caller/test proof. Keep BUY fail-closed.
6. Add or update focused tests for diagnostics, ablation reproducibility,
   probability separation, no leakage, replay, recorder, and production gates.
7. Run full validation: tests, compile, diff check, health check, production
   smoke, historical replay, and OOS evaluation; record actual status and limits.
8. Refresh codebase-memory, update AgentMemory and only relevant Obsidian notes,
   then create one clear production commit and push `main` after the worktree is
   clean.

## Success Criteria

- The report contains canonical sample counts (`Canonical Samples`, `Partial`,
  `Conflict`, `Invalid`) and per-feature distribution/missingness statistics.
- The report contains chronological Train/Validation/OOS probability summaries,
  ROC-AUC, PR-AUC, Brier, calibration error, trading metrics, ablation tables,
  top-decile tables, BUY coverage, baseline delta, monotonicity, and explicit
  `DATA_INSUFFICIENT`, `EXPERIMENTAL`, or `VALIDATED` status.
- OOS PASS is true only when all production gates pass: validated status,
  monotonicity, probability separation, full-Alpha baseline increment, real
  capital/supply/repricing increment, and minimum expected net return.
- `evaluate_candidate_bundle()` remains the only function that can emit BUY,
  SELL, or PICK; a failed or non-discriminative model blocks BUY.
- No future label reaches T-day Feature, Research, Alpha, or Decision inputs;
  historical records remain unchanged and no rows are removed.
- `pytest tests/ -q`, compile, `git diff --check`, health check, production
  smoke, historical replay, and OOS evaluation have actual recorded outcomes.
