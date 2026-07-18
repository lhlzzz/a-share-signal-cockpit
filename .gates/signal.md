# Signal Gate

## Before merge

- [ ] No lookahead bias (no future data in signal computation)
- [ ] No leakage (no T+1 data at T-day decision time)
- [ ] Universe verified (all stocks in universe are tradeable)
- [ ] Factor documented (what it measures, why it works)
- [ ] Reason recorded (why this signal was selected)
- [ ] Confidence calculated (hit rate, sample size)

## Regime Awareness

Signals must adapt to market regime:
- **Strong market**: momentum signals rewarded
- **Weak market**: contrarian signals rewarded
- **Sideways market**: balanced approach

## Check

```bash
python3 -c "
from xiaogu_db import engine
from sqlalchemy import text
with engine.connect() as c:
    rows = c.execute(text('SELECT signal_key, present_count, limit_up_rate, avg_t1_return FROM signal_effectiveness ORDER BY limit_up_rate DESC')).fetchall()
    for r in rows:
        print(f'{r[0]:<40} n={r[1]:<3} LU={r[2]:.0%} avg={r[3]:+.2%}')
"
```
