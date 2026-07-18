# xiaogu Research Methodology

## Principles

1. Every signal must be explainable
2. Every factor must be measurable
3. Every improvement must be benchmarked
4. Never trust a single backtest
5. Research is reproducible

## Signal Analysis

### Regime Awareness

| Market | High Signal | Low Signal | Strategy |
|--------|------------|------------|----------|
| Strong | +4.79% | +0.04% | Momentum |
| Weak | -3.74% | +0.85% | Contrarian |
| Sideways | +0.34% | +3.77% | Balanced |

### Key Findings

- `close_position_score` negatively correlated with returns (high position = chasing)
- `fund_flow_momentum` negatively correlated (high flow = main force exiting)
- `sector_catalyst_score` negatively correlated (hot sector = late entry)
- `early_opportunity_score` positively correlated (early entry = better returns)

## Experiment Design

1. State hypothesis before analysis
2. Use frozen dataset (no live data during backtest)
3. Record data version, config version, random seed
4. Compare against baseline
5. Document in `docs/research.md`
