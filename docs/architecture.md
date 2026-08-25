# xiaogu Architecture

## System Overview

A-share autonomous research & paper trading platform.

```
Direct Eastmoney API Scanner → Runner (gate + score) → Recorder (ledger) → Eastmoney T+1 Filler (returns)
                                                                    ↓
                                                              PostgreSQL
                                                                    ↓
                                                              API (query)
```

## Components

| Component | File | Role |
|-----------|------|------|
| Scanner | `scrapy_scanner/runner_v2.py` | Eastmoney API v2 market data collection |
| Runner | `xiaogu_forward_runner.py` | Pick decision engine |
| Recorder | `xiaogu_forward_paper_recorder_v0_1.py` | Ledger writer |
| Filler | `xiaogu_forward_result_filler_v0_1.py` | Return backfill |
| Scheduler | `xiaogu_scheduler.py` | Job orchestration |
| API | `xiaogu_api.py` | Query interface |
| DB | `xiaogu_db.py` | Data persistence |
| Utils | `xiaogu_utils.py` | Shared utilities |

## Database Schema

8 tables: picks, returns, scan_sessions, signal_effectiveness, signals, research_runs, daily_candidates, scoring_config

## Data Flow

1. **09:25** API v2 scanner runs
2. **14:30** API v2 scanner runs again
3. **14:50** Runner evaluates candidates → PAPER_PICK or NO_PICK
4. **14:50** Recorder writes to ledger
5. **15:30** Filler backfills T+1 returns
6. **20:00** Signal effectiveness analysis

## Official Ticket Strategy

Xiaogu has one formal T-day decision path. It consumes the same-day scanner
snapshot only and produces either one `PAPER_PICK` or `NO_PICK`; research,
shadow, and replay outputs can explain or challenge the result but cannot
promote a second ticket.

```
same-day scan snapshot
  -> production-chain and source-health validation
  -> freeze one full-pool formal rank
  -> eligibility and buyability gates
  -> single formal sort key among eligible, unheld candidates
  -> PAPER_PICK or NO_PICK
  -> immutable decision snapshot and ledger record
  -> T+1 return backfill and effectiveness analysis
```

### 1. Decision Inputs and Time Boundary

- The runner consumes a same-day Eastmoney API v2 scan snapshot.
- Source freshness, source timestamps, as-of validity, evidence coverage, rule
  version, and scoring configuration are checked before a candidate can become
  a formal ticket.
- The formal score is built from T-day evidence. Legacy scanner scores remain
  audit fields and do not decide the production rank.
- The final full-pool rank is frozen before selection so the selected ticket and
  persisted snapshot use the same ordering.

### 2. Eligibility Is a Gate, Not a Second Ranker

`xiaogu_forward_eligibility.py` determines whether a candidate is allowed to
compete for a new paper buy. It enforces the current-day tradability and
buyability contract, including production evidence completeness, regulatory
blocks, price and one-board-lot affordability constraints, sealed-limit
buyability, structural risk, and the minimum T+1-profit confirmation profile.

The gate keeps explicit continuation and direct-catalyst exceptions where
evidence is verified. In a risk-off external market it blocks high-extension
chases unless direct catalyst or verified continuation evidence supports the
candidate. A held symbol is retained for position-management observation but
cannot produce a new-buy `PAPER_PICK`.

If no candidate passes the production and eligibility contracts, the correct
output is `NO_PICK`; the system does not substitute a lower-quality fallback.

### 3. Single Formal Ranking

`xiaogu_forward_ranking.formal_candidate_sort_key` is the only formal ranking
owner. Its first tuple element is the production score; remaining tuple
elements are deterministic tie-break and diagnostic evidence. The current
positive contribution is:

| Evidence | Formal weight |
|---|---:|
| Direct capital behavior confirmation | 20 |
| T+1 room plus entry quality | 20 |
| Sector attack strength | 15 |
| Credible catalyst | 10 |
| Theme-cycle phase | 10 |
| Continuation / profit edge | 10 |
| Expected T+1 profit confirmation | 5 |
| Market environment | 5 |
| Main-force attack supplement | 5 |

Capital flow is confirmation, not sufficient evidence by itself: without direct
capital fields, the capital component is zeroed. Catalyst credit is scaled by
evidence quality, with policy, industry, company, and unconfirmed/noise tiers.
Entry quality favors a 2% to 5% same-day move and demotes extended chases.
Theme-cycle and market-environment fields are explicit T-day inputs.

The score subtracts distribution risk, broken-limit risk, hot-fund shell risk,
near-limit extension risk, auxiliary risk notices, and weak/climax or
external-risk-off chase pressure. `similar_cases_boost` is only a bounded soft
increment; it is not a second selector.

### 4. Selection and Persistence

Among candidates that pass the gate and are not already held, the runner selects
the maximum value of the single formal sort key. It records the candidate
features, gate result, risk flags, selection trace, formal rank, snapshot
identity, rule/config state, and reason in the paper ledger. Corrections are
append-only; historical decisions are not overwritten in place.

### 5. Result Measurement

The current result filler waits for a final T+1 daily bar and records the gross
close-to-close reference return:

```text
(T+1 close - T-day entry reference price) / T-day entry reference price
```

It preserves source, final-bar status, entry/exit dates and prices, K-line
adjustment mode, and evidence hash. The same result record also carries the
single shadow execution model: `FILLED`, `PARTIAL`, `NOT_FILLABLE`, or
`UNKNOWN`; entry/exit execution state; unadjusted price-basis consistency;
slippage, impact, commission, stamp duty, transfer fee, net return, and
worst-case return. `NOT_FILLABLE` is not treated as an ordinary loss and
`UNKNOWN` is not silently removed from the denominator. The gross T+1 close
return remains the historical reference metric; costed and conservative
metrics are used for execution validation and promotion gates.

### 6. Provenance, Regime, and Validation

Every formal T-day feature is stamped with source, source time, as-of time,
producer version, rule version, and label status. The production snapshot uses
an allowlist and quarantines rows that fail lineage validation. Similar-case
evidence must be marked `MATURED`; historical cases are restricted to dates
before the decision date and require a settled T+1 label.

The market context is frozen once per candidate snapshot and shared by
eligibility and ranking through `market_context_hash`. It exposes an explicit
`UNKNOWN` observation status, plus a five-session shadow smoothing/hysteresis
diagnostic; legacy `production_regime` labels remain compatible until replay
evidence authorizes a behavior change.

Full-pool diagnostics retain role buckets for rank 1, ranks 2 to 6, ranks 7 to
10, eligible-but-not-selected, rejected-with-return, outside-target, and
execution failures. They report gate/ranking descriptive deltas, missing-return
concentration by regime/rank/price/turnover/limit state/provider, and tail-risk
metrics including p10, p25, conditional expected shortfall, loss streak, and
drawdown. These diagnostics do not create a second selector.

## Pipeline Rules

- Stable chain: Runner → Recorder → Filler → Scoreboard
- V3 production boundary: Native Evidence + validated VEI + validated Qlib only
- Research repos output RESEARCH_SIGNAL only, never affect PAPER_PICK directly
- Production has no browser transport, alternate selector, or candidate
  promotion fallback. Missing required source data fails closed.
- A formal `PAPER_PICK` is limited to one 100-share board lot with a price at
  or below 70 CNY.
- `PAPER_ONLY` is a hard boundary: the system has no broker execution path.
- A ranking, a shadow study, or a realized T+1 return does not override a
  regulatory, buyability, source-health, or `NO_PICK` decision.
