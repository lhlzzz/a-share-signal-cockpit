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
| Runner | `xiaogu_forward_runner.py` | Pick decision engine |
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
| returns | T+1..T+5 profit-window outcomes (OHLC/realizable profit/MAE) |
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
8. **Ponytail decision ladder.** Apply it to every tracked code path: prefer existing code, then the standard library or native platform feature, then installed dependencies, and add the smallest implementation only when required. Cleanup must preserve behavior by default; market inputs, scoring, gates, database state, and `PAPER_PICK` semantics may change only in a separately approved strategy task with replay and benchmark evidence.

---

## Definition of Done

A task is complete only if:

- [ ] **Karpathy constraints loaded at task start** (CLAUDE.md / `.skills/karpathy-daily.md`)
- [ ] Code compiles / imports without error
- [ ] Tests pass (`pytest tests/ -x -q`)
- [ ] API endpoints return correct responses
- [ ] DB state is consistent
- [ ] No new dead code introduced
- [ ] Architecture preserved
- [ ] Research reproducible (if applicable)
- [ ] Documentation updated (if applicable)
- [ ] **AgentMemory updated** (`agentmemory__memory_save` for decisions/bugs/workflow; local `scripts/agentmemory_daily.sh` when relevant)
- [ ] **Obsidian updated when knowledge changed** — project evidence → `Project/A股`; cross-domain → 神临（想法池/总索引/项目接口）

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
5. **20:00** Filler backfills T+1..T+5 profit-window outcomes
6. **20:00** Signal effectiveness analysis

### Pipeline Command

```bash
# v2 Scanner (default, API direct)
python3 scrapy_scanner/runner_v2.py

# Runner
python3 xiaogu_forward_runner.py --date $(date +%Y-%m-%d) --force
```

### Development Cycle（任务启动强制顺序 — 不可跳过）

> **Global (all Grok CLI projects):** `~/.grok/AGENTS.md`. Below is xiaogu domain elaboration; do not skip global steps.

> 用户硬要求：启动先 Karpathy；定位用 **codebase-memory 主索引**，并用已维护的 Understand-Anything 图谱做架构交叉校验；有歧义先 **plan-discuss**；编码后必须更新 **AgentMemory**（及知识有变时的 Obsidian）。

0. **KARPATHY（启动闸门）** — 读 `CLAUDE.md` + `.skills/karpathy-daily.md`；陈述假设、成功标准、不做清单。未加载不得写业务代码。
1. **UNDERSTAND（codebase-memory 主索引）**
   - **主**：`codebase-memory-mcp` — `search_graph` / `trace_path` / `get_code_snippet` / `query_graph` / `search_code` 定位符号与调用链
   - **冲突以 source code / tests / git 为准**；codebase-memory 不可用时回退 `rg` / `read_file`
   - **Understand-Anything**：已启用并维护 `.understand-anything/`；用于架构、历史关系和高层解释交叉校验，不替代 source code、tests、runtime 或 Git diff
2. **PLAN** — 有实现歧义/多方案时：Plan Enforcer **plan-enforcer-discuss → draft → review**；机械小改（单点 bug、明确一行修复）可跳过 discuss，但仍要成功标准
3. **IMPLEMENT** — 只改必须改的；modify-before-create；不平行实现
4. **VALIDATE** — `pytest tests/ -x -q` + 受影响路径验证
5. **COMPLETE（自动落盘）**
   - `agentmemory__memory_save`（decision / bug / workflow / pattern）
   - 可选 `scripts/agentmemory_daily.sh`
   - 执行下方 **Obsidian Knowledge Closure**；知识有变时才写入 `ashare` 或 `shenlin` vault
   - 成功标准全部打勾才算完成；主链绿但记忆/笔记漏写 = **未完成**

### Obsidian Knowledge Closure

完成每项任务前必须依序判断：

1. **新知识**：本任务是否产生可复用的架构结论、策略/研究证据、验证结论、运行规则或 Bug 根因？
2. **Obsidian 更新**：有新知识时，是否需要更新既有笔记或新增一条可检索的项目记录？没有新知识时，明确不写，避免制造噪声。
3. **Decisions**：重要架构决定写入 `ashare:decisions/`；只有跨项目的接口、模式或知识地图才同步摘要到 `shenlin:项目接口/` 或 `shenlin:想法池/`。
4. **Lessons**：可复用的 Bug 根因、失效模式、修复验证和防回归措施写入 `ashare:失败案例/`（Lessons owner，首次需要时创建）；已验证的 PAPER_PICK/NO_PICK、T+1..T+5 盈利窗口结果、亏损归因和逻辑变化也是知识，分别写入 `决策日志/`、`跟踪记录/`、`失败案例/`；不要把行情原始明细、密钥或未验证猜测写入知识库。

通过项目配置的 `obsidian` MCP 管理 vault。默认 vault 是 `ashare`；删除操作保持禁用。知识记录必须包含日期、结论、证据/验证和受影响路径，且不得替代 source code、测试、运行日志或 Git diff 作为事实依据。

### 浏览器验证规则

- 浏览器不属于生产链，也不是日常验证前提。
- 默认使用 source code、数据库、API、测试和服务日志验证。
- 任何任务不得因浏览器不可用而绕过生产链、直接改库或把 dry-run 结果当正式事实。

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
- 2026-06-26: Return methodology: close is a historical compatibility field; realizable high is the primary 5D outcome
- 2026-06-26: Score cap (95) for win rate improvement
- 2026-06-26: Weekday blocklist (Mon/Fri blocked) for win rate improvement
- 2026-06-26: Historical social-sentiment experiment retired; it is not part of the production chain.
- 2026-06-29: Architecture overhaul — sector prediction first (full market API fund flow → predict hot sector → select stocks).
- 2026-06-29: Main force/hot money perspective is PRIMARY scoring dimension. `hm*0.6 + int*0.4` for sector-matched, `hm*0.2 + int*0.8` for unmatched.

## Codebase Memory MCP
- 代码结构、符号、调用链、影响面优先使用 codebase-memory-mcp（`index_repository`、`search_graph`、`trace_path`、`get_code_snippet`、`query_graph`、`search_code`）；未索引时先索引当前 workspace，工具不可用时再回退 `rg`。

## Code Discovery Order（xiaogu）

xiaogu 代码发现以 **codebase-memory-mcp 主索引** 为准，Understand-Anything 作为已维护的架构交叉校验：

### codebase-memory-mcp（唯一结构索引）
- **用途**：精确符号发现、调用链追踪、影响面分析、死代码检测、架构 cluster 视图
- **优先级**：始终优先使用
- **适用场景**：修改代码、重构、bug定位、依赖分析、架构理解
- **工具**：`search_graph`、`trace_path`、`get_code_snippet`、`query_graph`、`get_architecture`、`search_code`

### 使用规则
0. **任务启动时**：先 Karpathy，再 codebase-memory UNDERSTAND，再 plan-discuss（歧义时），最后才写代码；收口写 AgentMemory
1. **代码修改**：始终用 codebase-memory-mcp 做发现；冲突以 source code / tests / git 为准
2. **不可用时**：回退 `rg` / `read_file`，不得发明结构
3. **Understand-Anything**：用于当前图谱的架构交叉校验和高层解释；图谱路径漂移或提交不一致时先更新图谱，再以 source code、tests、runtime 和 Git diff 复核
