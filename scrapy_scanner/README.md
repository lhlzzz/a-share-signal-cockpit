# Scrapy Scanner for xiaogu

Official scanner entrypoint: `scrapy_scanner/runner_v2.py`. Legacy direct-scanner entrypoints have been removed after validating the official ticket chain, database readiness, and DB-backed backtest smoke path.

## Quick Start

```bash
cd /workspace/hermes-workspaces/xiaogu

# Run the production v2 scanner
python3 scrapy_scanner/runner_v2.py

# Run with custom output directory
python3 scrapy_scanner/runner_v2.py --output-dir data/live_scan/2026-07-05/eastmoney_scan_morning
```

## Output Files

| File | Description |
|------|-------------|
| `scrapy_stock_list.jsonl` | A-share stock quotes (5870+ stocks) |
| `scrapy_fund_flow.jsonl` | Market index fund flow |
| `scrapy_lhb.jsonl` | Dragon-Tiger board records |
| `scrapy_sector_flow_industry.jsonl` | Industry sector fund flow |
| `scrapy_sector_flow_concept.jsonl` | Concept sector fund flow |
| `scrapy_summary.json` | Summary with market statistics |

## Data Fields

### Stock List
- `code`: Stock code (6 digits)
- `name`: Stock name
- `price`: Current price
- `pct_chg`: Percent change
- `volume`: Trading volume
- `amount`: Trading amount
- `turnover_rate`: Turnover rate
- `pe_dynamic`: Dynamic PE ratio
- `pb`: Price-to-book ratio
- `net_inflow_main`: Main force net inflow

### Fund Flow
- `secid`: Index code
- `name`: Index name
- `price`: Current price
- `pct_chg`: Percent change
- `net_inflow`: Net inflow amount

## Limitations

1. **Endpoint coverage**: An unavailable endpoint is recorded in `source_status`; it does not trigger a browser fallback.
2. **LHB data**: The API may return empty results outside applicable trading windows.

## Failure Handling

The v2 scanner is API-only. Inspect `scrapy_summary.json` and its `source_status` entries for endpoint failures or partial coverage.

## API Endpoints Used

| Domain | Endpoint |
|--------|----------|
| Stock list | `push2delay.eastmoney.com/api/qt/clist/get` |
| Fund flow | `push2delay.eastmoney.com/api/qt/ulist.np/get` |
| LHB | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Sector flow | `push2delay.eastmoney.com/api/qt/clist/get` |
