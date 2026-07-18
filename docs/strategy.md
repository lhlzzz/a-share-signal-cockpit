# xiaogu Strategy

## Pick Selection Logic

### Hard Gates (blocking)

1. **Regulatory hard block** — stock on exchange risk list
2. **Data gate** — insufficient market data
3. **Chase high** — with sector/fund flow exception
4. **One lot cost** — exceeds available capital (6000 CNY)

### Scoring (regime-aware)

- **Strong market**: reward momentum (high signal, high flow, hot sector)
- **Weak market**: reward contrarian (low signal, low position, cold sector)
- **Sideways market**: balanced approach

### Candidate Selection

1. Load all scored candidates from scanner
2. Apply contrarian re-score (regime-aware)
3. Select top-scoring candidate that passes all hard gates
4. If no candidate passes → NO_PICK

## Return Methodology

- **Primary**: T+1 close (realistic exit)
- **Reference**: T+1 VWAP (volume-weighted)
- **Upper bound**: T+1 high (optimistic)

## Historical Performance

See `/stats/overview` API endpoint for current metrics.

## Tuning

Scoring thresholds stored in `scoring_config` table.
Regime detection thresholds adjustable via config.
