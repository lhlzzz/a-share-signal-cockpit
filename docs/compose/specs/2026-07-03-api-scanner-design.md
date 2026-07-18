# API-Based Scanner Design

## [S1] Problem

The current scanner (`xiaogu_eastmoney_web_tabs_scan_v0_1.py`, 7661 lines) relies on CloakChrome CDP to extract data from JavaScript-rendered Eastmoney pages. This approach is:

- **Slow**: Browser overhead, tab management, DOM evaluation
- **Fragile**: Anti-scraping detection, page structure changes
- **Complex**: 7661 lines of code, many edge cases
- **Resource-heavy**: Requires running Chrome browser

## [S2] Solution

Replace CDP-based DOM extraction with direct HTTP API calls to Eastmoney's backend APIs. Most data the browser displays is fetched from these APIs - we can call them directly.

## [S3] Data Sources Mapping

### Already Available via API

| Data | API Endpoint | Status |
|------|-------------|--------|
| LHB (龙虎榜) | `datacenter-web.eastmoney.com/api/data/v1/get` with `reportName=RPT_DAILYBILLBOARD_PROFILE` | ✅ Implemented |
| Financial reports | `datacenter-web.eastmoney.com/api/data/v1/get` with `reportName=RPT_LICO_FN_CPD` | ✅ Implemented |
| Limit-up pool | `push2ex.eastmoney.com/getTopicZTPool` | ✅ Implemented |
| Broken-limit pool | `push2ex.eastmoney.com/getTopicZTPool` with type=zbgc | Need to verify |
| Consecutive limit | `push2ex.eastmoney.com/getTopicZTPool` with type=ljb | Need to verify |
| Yesterday limit | `push2ex.eastmoney.com/getTopicZTPool` with type=zrzt | Need to verify |

### Requires New API Discovery

| Data | Current Method | API Pattern |
|------|---------------|-------------|
| Stock quotes (grid list) | CDP DOM extraction | `push2.eastmoney.com/api/qt/clist/get` |
| Fund flow (individual stocks) | CDP DOM extraction | `push2.eastmoney.com/api/qt/clist/get` with fund flow fields |
| Concept board list | Direct API ✅ | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Concept member stocks | Direct API ✅ | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Industry board list | CDP DOM extraction | `push2.eastmoney.com/api/qt/clist/get` |
| Sector fund flow | CDP DOM extraction | `push2.eastmoney.com/api/qt/clist/get` |
| Concept capital flow | CDP DOM extraction | `push2.eastmoney.com/api/qt/clist/get` |
| Popularity rank | CDP DOM extraction | `guba.eastmoney.com/api/` endpoints |
| Margin trading | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Block trades | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Lockup expiry | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Shareholder changes | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Research reports | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Earnings preview | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| IPO calendar | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |
| Trading halts | CDP DOM extraction | `datacenter-web.eastmoney.com/api/data/v1/get` |

## [S4] Architecture

### New Scanner Structure

```
xiaogu_api_scanner_v0_1.py
├── api_client.py          # HTTP client with retry, rate limiting
├── endpoints/
│   ├── quotes.py          # Stock quotes API
│   ├── fund_flow.py       # Fund flow API
│   ├── limit_pools.py     # Limit-up/broken-limit pools
│   ├── lhb.py             # 龙虎榜 API
│   ├── financials.py      # Financial reports API
│   ├── concepts.py        # Concept/industry boards
│   ├── risk_data.py       # Margin, block trades, lockup, etc.
│   └── popularity.py      # Popularity rank API
├── collectors/
│   ├── quote_collector.py
│   ├── evidence_collector.py
│   └── risk_collector.py
└── scanner.py             # Main scanner orchestration
```

### Key Design Decisions

1. **No Scrapy**: Direct `requests`/`urllib` calls are sufficient. Scrapy adds unnecessary framework overhead.

2. **Rate Limiting**: 100ms between requests to avoid triggering anti-scraping.

3. **Retry Logic**: Exponential backoff for transient failures.

4. **Fallback Strategy**: If an API fails, fall back to CDP for that specific data point.

5. **Output Compatibility**: Produce same output format as current scanner for Runner compatibility.

## [S5] Implementation Phases

### Phase 1: API Discovery (1-2 hours)
- Test each API endpoint to confirm availability
- Document request/response formats
- Identify authentication requirements (if any)

### Phase 2: Core APIs (2-4 hours)
- Implement quotes API
- Implement fund flow API
- Implement limit pool APIs
- Test against current CDP output

### Phase 3: Evidence APIs (2-4 hours)
- Implement LHB, financials, concepts APIs
- Implement risk data APIs (margin, block trades, etc.)
- Implement popularity API

### Phase 4: Integration (2-4 hours)
- Build scanner orchestration
- Output format compatibility
- Testing against current scanner

### Phase 5: CDP Fallback (1-2 hours)
- Keep CDP as fallback for any APIs that don't work
- Gradual migration

## [S6] Expected Benefits

- **Speed**: 10x faster (no browser overhead)
- **Reliability**: Direct API calls are more stable than DOM extraction
- **Maintainability**: Simpler code, easier to debug
- **Resource Usage**: No Chrome browser needed

## [S7] Risks

- **API Changes**: Eastmoney may change API endpoints or formats
- **Rate Limiting**: Aggressive requests may get IP blocked
- **Missing Data**: Some data may only be available via DOM extraction

## [S8] Testing Strategy

1. Run both scanners in parallel for 1 week
2. Compare output consistency
3. Measure performance improvement
4. Gradual rollout

## [S9] Success Criteria

- All required data points available via API
- Output format matches current scanner
- Performance improvement ≥ 5x
- No data loss compared to CDP approach
