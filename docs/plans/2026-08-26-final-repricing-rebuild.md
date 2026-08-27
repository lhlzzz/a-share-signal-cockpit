# Xiaogu 3.0 Profit Window 5D Production Lock

## Scope

Replace the incorrect longer-horizon repricing target with one production target:
`PROFIT_WINDOW_5D`. The existing Feature Engine, Research Context, Core Alpha,
Portfolio Decision, ledger, returns table, replay, and API remain the owners;
no parallel selector, alpha, database, or decision path is introduced.

## Contract

- Maximum holding boundary: five trading days.
- T+1 through T+5 are evaluation/window-capture days, not a T+1 strategy.
- Core Alpha emits `profit_window_probability`, `expected_max_profit_5d`,
  `expected_time_to_profit`, `expected_mae_5d`, and
  `expected_net_profit_window`, plus repricing/risk/convergence explanations.
- `evaluate_candidate_bundle()` is the only portfolio decision owner.
- Research adapters remain context-only and cannot emit BUY.
- Historical replay calls the production Feature, Research, Alpha, and Decision
  owners before attaching future OHLC bars.
- Returns persistence remains one `returns` table and only stores 5D window
  outcomes; old records remain readable through API filtering.

## Tasks

1. Freeze the corrected 5D strategy contract and production rule metadata.
2. Replace the prior horizon estimate with capital convergence and 5D profit-window outputs.
3. Update Portfolio Decision BUY/hold/exit gates to use the 5D window contract.
4. Replace horizon evaluation and filler outputs with T+1..T+5 realizable-window outcomes.
5. Update replay, recorder, database/API compatibility, health checks, and active docs.
6. Update the existing focused tests to assert the 5D contract and no legacy target.
7. Run full tests, compile, diff checks, production health checks, and a real replay attempt.
8. Refresh the code index and record the durable architecture/validation decision.

## Verification

- `pytest tests/ -q`
- `python -m compileall -q scrapy_scanner xiaogu_*.py integrations`
- `git diff --check`
- production source has no retired expected-return field or longer-horizon target semantics
- historical replay returns real samples or explicit `DATA_INSUFFICIENT`, never fabricated labels
