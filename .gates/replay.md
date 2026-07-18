# Replay Gate

## Before merge

- [ ] Historical replay completed on representative dataset
- [ ] Checksum matches expected values
- [ ] Frozen dataset used (no live data during replay)
- [ ] Deterministic output (same input → same output)
- [ ] No hidden randomness

## Check

```bash
python3 scripts/xiaogu_ledger_migrate.py --dry-run
python3 -m pytest tests/ -x -q
```

## Metrics

Record for each replay:
- Total picks evaluated
- PAPER_PICK count
- NO_PICK count
- Win rate (T+1 close)
- Average return
- Max drawdown
