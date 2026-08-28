# Xiaogu Production Core Cleanup

## Scope

Converge the repository on one capital-behavior-driven five-day paper
production chain. Preserve historical database rows and the current OOS
fail-closed alpha contract. Delete code and artifacts that have no current
production or development responsibility.

## Contract

- Eastmoney capture owns transport, raw observations, lineage, and canonical
  snapshots only.
- Cheap eligibility owns objective data and trading constraints only; it does
  not score, rank, or interpret candidates.
- `build_core_alpha()` is the only Alpha owner.
- `evaluate_candidate_bundle()` is the only Portfolio Decision owner.
- Research adapters provide context and contradiction evidence only.
- The runner evaluates only the candidate universe after cheap eligibility.
- The recorder persists only `BUY`, `HOLD`, `REDUCE`, and `SELL` events.
- Position state is `FLAT` or `LONG`; `REDUCE` is an action, not a state.
- Every trade is closed at the five-trading-day boundary.
- Outcomes cover only T+1 through T+5 and distinguish daily-bar opportunity
  from execution-realizable evidence.
- No live trading, direct database mutation, future leakage, or parallel owner
  is permitted.

## Validation

The completion gate is `pytest tests/ -x -q`, repository compile, the daily
health check, production smoke, historical replay checks, `git diff --check`,
the final forbidden-term scan, refreshed code indexes, and a clean pushed `main`.
