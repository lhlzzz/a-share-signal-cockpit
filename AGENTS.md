# XIAOGU PROJECT CONSTITUTION

> A-share autonomous research & paper trading platform.
> This document is the single source of truth for what this project is,
> how it works, and what rules govern it.

---

## Mission

Build a production-grade autonomous A-share research and paper trading system.

**Primary objective**: Produce reliable daily stock picks (PAPER_PICK) with explainable selection logic.

**Secondary objective**: Produce maintainable, well-tested, well-archived software.

**Never optimize for speed over correctness.**

### Always optimize for

- Reproducibility — every result must be reproducible from recorded state
- Data Quality — garbage in, garbage out; validate before using
- Architecture — clean boundaries, single responsibility, minimal coupling
- Research Quality — every signal must be explainable, every factor measurable
- Reliability — the system must work every trading day without manual intervention

---

## Engineering Principles

1. **Preserve architecture.** Every change must improve or maintain the existing architecture. Never sacrifice architecture for short-term convenience.
2. **No shortcuts.** Never skip tests, never bypass quality gates, never weaken validation.
3. **Readability over cleverness.** Code should be obvious to a reader seeing it for the first time.
4. **Every change improves the repository.** If you touch a file, leave it better than you found it. Remove dead code, fix inconsistencies, improve clarity.
5. **Small coherent commits.** One logical change per commit. No mixed concerns.
6. **No duplicated logic.** Extract shared utilities. One function, one implementation.
7. **Explicit over implicit.** Named parameters > positional. Named constants > magic numbers. Documented interfaces > undocumented behavior.
8. **Single source of truth.** Data lives in the DB. Logic lives in code. Config lives in `scoring_config`. No parallel copies.

---

## Architecture Principles

### System Components

| Component | File | Role |
|-----------|------|------|
| Scanner | `scrapy_scanner/runner_v2.py` | Eastmoney API v2 market data collection |
| Runner | `xiaogu_forward_d1_1450_runner_v0_1.py` | Pick decision engine |
| Recorder | `xiaogu_forward_paper_recorder_v0_1.py` | Ledger writer |
| Filler | `xiaogu_forward_result_filler_v0_1.py` | Return backfill |
| Scheduler | `xiaogu_scheduler.py` | Job orchestration |
| API | `xiaogu_api.py` | Query interface |
| DB | `xiaogu_db.py` | Data persistence |
| Utils | `xiaogu_utils.py` | Shared utilities |

### Data Flow

```
Scanner (Eastmoney API v2) → Runner (gate + score) → Recorder (ledger) → Filler (returns)
                                                                    ↓
                                                              PostgreSQL
                                                                    ↓
                                                              API (query)
```

### Database (8 tables)

| Table | Purpose |
|-------|---------|
| picks | Daily pick decisions |
| returns | T+1 returns (high/vwap/close) |
| scan_sessions | Scanner run metadata |
| signal_effectiveness | Signal validity analysis |
| signals | Raw signal snapshots |
| research_runs | Run version tracking |
| daily_candidates | Candidate analysis + selection rationale |
| scoring_config | Tunable scoring thresholds |

### Pipeline Rules

- Stable chain: Runner → Recorder → Filler → Scoreboard
- Validation chain: six_repo_integration → xiaogu_v2_1
- Rollback chain: CORRECTION records in ledger
- V3 production boundary: Native Evidence + validated VEI + validated Qlib only
- Research repos output RESEARCH_SIGNAL only, never affect PAPER_PICK directly

---

## Research Principles

1. **Every signal must be explainable.** If you can't explain why a signal works, it doesn't belong in production.
2. **Every factor must be measurable.** Track hit rate, contribution, and degradation for each factor.
3. **Every improvement must be benchmarked.** Compare against baseline before merging.
4. **Never trust a single backtest.** Validate across multiple time periods and market regimes.
5. **Research is reproducible.** Record data version, config version, and random seed for every experiment.
6. **Research before implementation.** Understand the problem before writing code.
7. **Dynamic market awareness.** Scoring must adapt to market regime (strong/weak/sideways), not use one-size-fits-all weights.

---

## Risk Principles

1. **Never modify production database directly.** All changes through code.
2. **Never skip replay.** Historical validation before production.
3. **Never skip benchmark.** Compare against baseline before merging.
4. **Never remove tests to pass CI.** Fix the test or fix the code.
5. **Never bypass quality gates.** Gates exist for a reason.
6. **Never weaken validation.** Validation is a one-way ratchet.
7. **Paper only.** No real trades, no broker connections, no API keys in code.

---

## Coding Principles

1. **Karpathy guidelines.** Before writing code: state assumptions, prefer simplicity, surgical changes, define success criteria.
2. **Modify before create.** Prefer editing existing files over creating new ones.
3. **Replace before add.** Replace old logic, don't add parallel logic.
4. **Remove before expand.** Clean up before adding features.
5. **Test after big tasks.** Major changes (runner/scanner/filler logic) require immediate test verification. Small tasks can batch-then-test.
6. **DB over JSONL.** Data belongs in PostgreSQL, not scattered JSONL files.
7. **CAST not ::.** SQLAlchemy `text()` requires `CAST(:param AS type)` not `:param::type` for PostgreSQL.

---

## Definition of Done

A task is complete only if:

- [ ] Code compiles / imports without error
- [ ] Tests pass (`pytest tests/ -x -q`)
- [ ] API endpoints return correct responses
- [ ] DB state is consistent
- [ ] No new dead code introduced
- [ ] Architecture preserved
- [ ] Research reproducible (if applicable)
- [ ] Documentation updated (if applicable)

---

## Never Rules

1. **Never fake results.** Report actual performance, not cherry-picked best cases.
2. **Never hide failures.** Log errors, surface them, fix them.
3. **Never invent data.** Use real market data or clearly mark as synthetic.
4. **Never silently ignore errors.** Every exception must be logged or handled.
5. **Never reduce validation.** Validation is a one-way ratchet — only increase.
6. **Never bypass gates.** Quality gates are mandatory, not optional.
7. **Never commit secrets.** No API keys, tokens, or credentials in code.
8. **Never modify production DB directly.** All changes through code.
9. **Never trust high scores blindly.** Score ≠ quality; validate against returns.
10. **Never chase blindly.** High signal + hot sector can be a trap; use regime-aware scoring.

---

## Workflow

### Daily Cycle

1. **09:25** v2 Scanner runs (API direct, < 1s)
2. **14:30** v2 Scanner runs again (afternoon data)
3. **14:50** Runner evaluates candidates → PAPER_PICK or NO_PICK
4. **14:50** Recorder writes to ledger
5. **15:30** Filler backfills T+1 returns
6. **20:00** Signal effectiveness analysis

### Pipeline Command

```bash
# v2 Scanner (default, API direct)
python3 scrapy_scanner/runner_v2.py

# Runner
python3 xiaogu_forward_d1_1450_runner_v0_1.py --date $(date +%Y-%m-%d) --force
```

### Development Cycle

1. **INTAKE** — Load `/karpathy-guidelines`, create task
2. **UNDERSTAND** — `codebase-memory-mcp` (符号/调用链) + `understand-anything` (架构图谱) 交叉验证
3. **PLAN** — Plan Enforcer discuss→draft
4. **IMPLEMENT** — Code changes (遵循 karpathy 4 原则)
5. **VALIDATE** — pytest + API test
6. **COMPLETE** — Verify success criteria, commit, `agentmemory__memory_save` 记录决策

---

## Historical Decisions

- 2026-05-21: Rule v0.2 — fresh source + cooldown after consecutive losses
- 2026-05-23: Eastmoney web as primary data source
- 2026-05-25: Climax regime detection added
- 2026-05-25: Low-price crowding penalty
- 2026-05-26: Logged-in Eastmoney pages as primary chain
- 2026-06-03: All A-share boards eligible (not just main board)
- 2026-06-03: Regulatory hard blocks mandatory
- 2026-06-25: Chase_high gate softened (sector/fund flow exemption)
- 2026-06-26: Contrarian re-scoring with regime awareness
- 2026-06-26: DB schema finalized (8 tables)
- 2026-06-26: Return methodology: T+1 close as primary, high/vwap as reference
- 2026-06-26: Score cap (95) for win rate improvement
- 2026-06-26: Weekday blocklist (Mon/Fri blocked) for win rate improvement
- 2026-06-26: Social sentiment integration via CloakChrome CDP (东财股吧 + X/Twitter)
- 2026-06-29: Architecture overhaul — sector prediction first (full market API fund flow → predict hot sector → select stocks). `fetch_all_sector_fund_flow()` replaces CDP concept_capital_flow. 创新药 = 36.96亿 #1 net inflow on 6/26.
- 2026-06-29: Main force/hot money perspective is PRIMARY scoring dimension. `hm*0.6 + int*0.4` for sector-matched, `hm*0.2 + int*0.8` for unmatched.

## Codebase Memory MCP
- 代码结构、符号、调用链、影响面优先使用 codebase-memory-mcp（`index_repository`、`search_graph`、`trace_path`、`get_code_snippet`、`query_graph`、`search_code`）；未索引时先索引当前 workspace，工具不可用时再回退 `rg`。

## Dual-Index Discovery Order

xiaogu 代码发现使用两个互补索引，各有明确角色：

### 1. codebase-memory-mcp（主索引）
- **用途**：精确符号发现、调用链追踪、影响面分析、死代码检测
- **优先级**：始终优先使用
- **适用场景**：修改代码、重构、bug定位、依赖分析
- **工具**：`search_graph`、`trace_path`、`get_code_snippet`、`query_graph`

### 2. Understand-Anything（辅助索引）
- **用途**：架构层可视化、交互式探索、新成员onboarding
- **优先级**：仅在codebase-memory-mcp结果需要交叉验证时使用
- **适用场景**：架构理解、模块边界确认、大范围重构前的全局视图
- **工具**：`/understand`、`/understand-dashboard`、`/understand-chat`
- **限制**：不作为代码修改的唯一依据，不覆盖source code/truth

### 使用规则
1. **代码修改**：始终用 codebase-memory-mcp 做发现，Understand-Anything 仅做架构参考
2. **架构理解**：两者互补，codebase-memory-mcp 给精确调用链，Understand-Anything 给全局视图
3. **冲突处理**：以 source code 为准，Understand-Anything 的图是近似值
4. **不可用时**：codebase-memory-mcp 不可用则回退 `rg`；Understand-Anything 不可用不影响代码修改
