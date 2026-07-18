# Benchmark Gate

## Before merge

- [ ] Compare against baseline (previous rule version)
- [ ] Alpha: new - baseline
- [ ] Win Rate: new vs baseline
- [ ] Average Return: new vs baseline
- [ ] Drawdown: new vs baseline
- [ ] No metric degradation without explicit tradeoff documented

## Check

Run scoreboard and compare:
```bash
python3 xiaogu_forward_judge_scoreboard_v0_1.py --dry-run
```

## Acceptance

- Win rate must not decrease by >5%
- Average return must not decrease by >0.5%
- Any degradation must be documented in DECISIONS.md
