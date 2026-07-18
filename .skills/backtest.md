# Backtest Skill

## Trigger

When historical validation of pick logic is needed.

## Workflow

1. Load historical scan data from `data/forward_raw_runtime/`
2. Re-run runner logic on historical data
3. Compare picks against actual returns
4. Calculate win rate, average return, max drawdown
5. Compare against baseline

## Key Files

- `xiaogu_forward_d1_1450_runner_v0_1.py` — runner logic
- `xiaogu_forward_result_filler_v0_1.py` — return computation
- `scripts/xiaogu_ledger_migrate.py` — data migration

## Return Methodology

- **T+1 close** (primary): exit at next day's closing price
- **T+1 VWAP** (reference): volume-weighted average price
- **T+1 high** (upper bound): optimistic sell at day's high

## Verification

- All picks have returns (no missing data)
- Returns computed using T+1 close (not high)
- Results match DB state
