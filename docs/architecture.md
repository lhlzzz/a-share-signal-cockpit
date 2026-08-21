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

## Pipeline Rules

- Stable chain: Runner → Recorder → Filler → Scoreboard
- V3 production boundary: Native Evidence + validated VEI + validated Qlib only
- Research repos output RESEARCH_SIGNAL only, never affect PAPER_PICK directly
- Production has no browser transport, alternate provider, or candidate
  promotion fallback. Missing required source data fails closed.
- A formal `PAPER_PICK` is limited to one 100-share board lot with a price at
  or below 70 CNY.
