# XIAOGU PROJECT CONSTITUTION

> Paper-only A-share research and 5D capital-behavior trading system.
> Source code is the logic fact. PostgreSQL is the production data fact.
> Canonical snapshots are the T-day input fact. Git is the version fact.

---

## Mission

Produce explainable five-day profit-window paper decisions from observed capital behavior.

Never optimize for speed over correctness. Never add a second alpha, decision owner, ranking path, or production state source.

### Always optimize for

- Reproducibility
- Data quality
- Single ownership
- Evidence independence
- Fail-closed BUY

---

## Source Of Truth

- Source code = logic
- PostgreSQL = production data and position state
- Canonical snapshot = T-day decision input
- Git = version
- Understand / codebase-memory = navigation only
- Obsidian = knowledge / memory layer only
- JSONL = audit artifact only

Forbidden:

- Obsidian → production logic
- Memory → Alpha
- Graph → automatic production edits
- JSONL → position state
- Research artifact → production BUY

---

## Production Chain

```
Eastmoney Capture
        ↓
Canonical Snapshot
        ↓
Cheap Eligibility
        ↓
Candidate Universe
        ↓
Feature Engine
        ↓
Research Context
        ↓
Core Alpha
        ↓
Portfolio Decision
        ↓
Recorder
        ↓
Outcome
        ↓
Position Review
        ↓
Memory
```

| Owner | File | Role |
|-----------|------|------|
| Scanner | `scrapy_scanner/runner_v2.py` | Capture only |
| Canonical | `xiaogu_forward_snapshot.py` | Trusted T-day snapshot |
| Eligibility | `xiaogu_forward_eligibility.py` | Operational constraints only |
| Features | `xiaogu_forward_features.py` | Measurements |
| Research | `xiaogu_research_context.py` | Context / evidence / contradiction |
| Alpha | `xiaogu_core_alpha.build_core_alpha` | Sole alpha owner |
| Decision | `xiaogu_portfolio_decision.evaluate_candidate_bundle` | Sole state/action owner |
| Runner | `xiaogu_forward_runner.run_production_decision` | Production entry |
| Recorder | `xiaogu_forward_paper_recorder_v0_1.py` | BUY/HOLD/REDUCE/SELL events |
| Outcome | `xiaogu_forward_result_filler_v0_1.py` | T+1..T+5 truth |
| DB | `xiaogu_db.py` | Production persistence |
| API | `xiaogu_api.py` | Query only |
| Memory | Memory Adapter → Obsidian | Trade notes only |

Trading Calendar owner: `xiaogu_db.py`. The sole truth is the versioned
`trading_calendar` table, populated from an authoritative dataset and audited
through `trading_calendar_migrations`. `is_trading_date()` returns `TRUE`,
`FALSE`, or `UNKNOWN`; missing data is `CALENDAR_DATA_UNAVAILABLE` and blocks
production. Future prices, scanner row availability, snapshots, paper
observations, and weekday arithmetic cannot determine a trading date.

The unified `ASHARE` Calendar covers SSE and SZSE. T+N resolution is owned by
`resolve_trading_date()` / `resolve_t_plus_n()` in `xiaogu_db.py`; Scheduler,
Runner, Outcome Filler, Horizon evaluation, and Position Review are consumers.

Sole alpha target: `PROFIT_WINDOW_5D`. Maximum holding: 5 trading days.

Scanner = capture. Cheap Eligibility = operational constraints. Feature = measurement. Research = evidence/context. Alpha = model. Decision = state/action. Recorder = production event. DB = truth. Outcome = T+1..T+5. Obsidian = memory.

---

## Engineering Principles

1. Preserve architecture. Never add a parallel owner.
2. Modify before create. Replace before add. Remove before expand.
3. Karpathy constraints: state assumptions, smallest correct change, verify.
4. Tests after production-path changes.
5. DB over JSONL for state.
6. CAST not `::` in SQLAlchemy `text()`.
7. Ponytail ladder: reuse existing owner, then stdlib, then installed dependency, then smallest new code.

---

## Contracts

Scanner captures market reality only. It does not score, rank, or pick.

Cheap eligibility and candidate universe check data validity, trading availability, and hard operational constraints. They do not score, rank, select themes, or form a capital thesis.

Feature engine accepts only `CanonicalSnapshot`. Feature is measurement. Alpha is the model.

Research context (Serenity, Buffett, UZI, Contradiction) may supply evidence, risk, and contradiction. It may not emit BUY, SELL, RANK, or PICK.

Production Decision accepts trusted snapshots only. Runner modes are PRODUCTION, REPLAY, DRY_RUN, RESEARCH. PRODUCTION reads DB-verified trusted snapshots, uses `datetime.now(timezone.utc)` or an explicit current clock, and computes age as `decision_clock - source_time`. `--snapshot-json` cannot enter paper production. `persisted` means PostgreSQL verification, not a local file flag.

`snapshot_id` is one immutable Canonical Snapshot. `lineage_id` is one scan lineage and may map to many symbol snapshots. `ensure_production_schema()` must raise on ALTER failure. Missing historical `decision_id` stays UNRESOLVED and is never rewritten.

Position state is `FLAT` or `LONG`. Action is `BUY`, `HOLD`, `REDUCE`, or `SELL`. T+5 is SELL / CLOSED. A still-valid thesis requires a new trade, not renewal.

Recorder persists only BUY/HOLD/REDUCE/SELL and position transitions.

BUY requires a validated alpha plus data, capital, supply, repricing, risk, execution, profit-window, OOS, probability-separation, monotonicity, and baseline-increment gates. Any failure is BUY BLOCKED.

---

## Risk Principles

1. Never modify production database directly.
2. Never skip replay or benchmark for strategy changes.
3. Never invent data or hide failures.
4. Never weaken validation.
5. Paper only. No broker. No secrets in code.

---

## Definition of Done

- [ ] Karpathy constraints loaded
- [ ] Code compiles
- [ ] `pytest tests/ -x -q`
- [ ] Affected production path verified
- [ ] AgentMemory updated
- [ ] Obsidian updated only when reusable knowledge changed

---

## Workflow

### Daily Cycle

1. Scanner captures Eastmoney snapshots
2. Runner evaluates DB-verified canonical snapshots
3. Recorder writes BUY/HOLD/REDUCE/SELL
4. Filler backfills T+1..T+5 outcomes
5. Position review reads PostgreSQL state
6. Obsidian records actual trade events

### Development Cycle

0. Karpathy
1. codebase-memory UNDERSTAND
2. Plan Enforcer discuss when ambiguous
3. Implement against existing owners
4. Validate
5. AgentMemory + Obsidian knowledge closure

---

## Code Discovery

Prefer codebase-memory-mcp (`search_graph`, `trace_path`, `get_code_snippet`, `query_graph`, `search_code`) over grep. Source, tests, runtime, and Git win conflicts.
