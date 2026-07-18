# Research Skill

## Trigger

When new signals, factors, or strategies need evaluation.

## Workflow

1. State hypothesis clearly
2. Collect historical data
3. Compute factor values
4. Backtest against baseline
5. Document results in `docs/research.md`
6. Only promote to production after passing benchmark gate

## Principles

- Every signal must be explainable
- Every factor must be measurable
- Every improvement must be benchmarked
- Never trust a single backtest
- Research is reproducible (record data version, config, seed)

## Regime Awareness

- Strong market: momentum signals
- Weak market: contrarian signals
- Sideways: balanced
- Never use one-size-fits-all weights

## Verification

- Hypothesis stated before analysis
- Baseline comparison included
- Multiple time periods tested
- Results documented with confidence levels
