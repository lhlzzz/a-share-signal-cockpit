# xiaogu 投研交易部

## Agent skills

### Issue tracker

Issues and PRDs are tracked as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock five-label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context layout: root `CONTEXT.md` and `docs/adr/` when present. See `docs/agents/domain.md`.

## 固定工具链

- 进入本 workspace 后按根级 `/root/hermes/company-ai-system/CLAUDE.md` 的分层上下文规则恢复；本目录 `TOOLING.md` 和 `/root/hermes/company-ai-system/tools/external/TOOLING.md` 只在涉及项目专用工具、浏览器/CDP、外部工具或验证入口时读取。
- xiaogu 固定分两条链路执行：治理工具用于“改系统”，不用于“跑系统”。开发链路和实时行情/出票链路不得混用。
- 开发链路固定为：PM/task → Plan → CodeGraph → GitNexus → 修改 scanner/runner/gate/scoring → 验证 → Plan Enforcer → AgentMemory/LOG。只有当 Claude 准备改代码、改规则、改配置、做结构理解、做影响面评估或复盘沉淀时，才启用这些治理工具。
- 涉及 xiaogu 策略修复时固定采用 Claude 控制层 + Codex 执行层：Claude 负责设计任务包、禁止项、优先修改的现有文件、验收标准、最终审查和纠偏；Codex 负责按包实现和本地验证。不得让 Codex 自行扩大扫描或新建并行策略。PAPER_PICK 口径必须区分正式 ledger/final runtime 与盘中观察 runtime；浏览器数据源不完整或为 0 必须显式失败/降级。Cloak 浏览器启动可用于绕过网页阻塞，不需要额外验证该绕过能力。
- 实时运行链路固定为：启动 → 读取当前配置 → 读取当前数据 → 按当前规则和当前模型运行行情扫描/结构化提取/评分/排序/出票 → 输出结果 → 记录轻量证据。09:25 开盘实时扫描、实时行情链路和出票热路径禁止读取或调用 CodeGraph、GitNexus、UA、AgentMemory、PM/task、Plan/Plan Enforcer 参与决策。
- 运行证据层只在本轮扫描、出票或回测结束后记录：本轮输入/配置、输出结果、轻量快照、验证结果和是否写 ledger；需要复盘改动时再回到开发链路。
- 代码结构、符号、调用链、文件定位优先用 CodeGraph；索引过期时在本 workspace 运行 `codegraph sync`，不要重装工具。
- A 股/东财出票链路的 CodeGraph 查询入口固定用精确词：`xiaogu_forward_d1_1450_runner_v0_1`、`evaluate_candidate_bundle`、`decision_for_candidate`、`run_recorder`、`xiaogu_eastmoney_web_tabs_scan_v0_1`、`xiaogu_eastmoney_tail_scan_v0_2`。不要用泛 `ticket` 词判断 A 股链路；非 A 股资产归属 `xiaomei`，不得进入 `xiaogu` stable chain。可从 repo 根目录运行 `python3 scripts/xiaogu_codegraph_health_check.py --sync` 做出票链路健康检查。
- API、流程、跨模块影响面、当前 diff 风险用 GitNexus。
- 写代码、重构、review 前调用 `andrej-karpathy-skills:karpathy-guidelines`，保持最小改动和明确验证标准。
- 跨会话偏好、项目事实和非代码经验用 AgentMemory/现有记忆系统；不要把代码或 git 可推导信息写入长期记忆。
- 本项目专业工具固定使用 Backtrader、vectorbt、`quant-python`、项目隔离浏览器 MCP；A 股自 2026-05-28 起已进入用户手动下单的实盘跟踪阶段，Claude/自动化系统只允许读行情、自选、持仓、资金、成本和盈亏并生成出票/复盘建议，严禁接 broker/API key/order endpoint 或执行任何交易动作。

## Workspace 整理边界

- 开工默认只读：`NEXT_ACTION.md`、`STATE.md`、`SESSION.md`。需要追溯任务、交接、决策或规则来源时，再按需读取 `TASK.md`、`HANDOFF.md`、`DECISIONS.md`、`RULES.md`；涉及工具/验证时再读 `TOOLING.md`。上下文恢复和状态整理优先用 Opus 4.6 `claude -p --model claude-opus-4-6` 生成短摘要，不要在主会话全量读取长历史文件。
- 保留证据：策略脚本、ledger/jsonl、summary/manifest/json、scoreboard、research journal、截图、market read/recheck 结果、`data/` 下可复核研究资料。
- 历史清理、archive 和 lifecycle 操作必须分批处理：每批最多 10 个文件；manifest/report 写入文件，不在主会话打印大段分类表；禁止单次会话全量扫描 `workspaces/xiaogu/` 并把结果作为日常上下文。
- Phase 1 清理入口：在 repo 根目录运行 `python3 scripts/xiaogu_cleanup_candidates.py` 生成 `summary/cleanup_candidates_dry_run.jsonl`；再运行 `python3 scripts/xiaogu_governance_check.py` 校验 active chain、旧链路隔离、ignore 隔离、protected evidence 和 rollback proof。先审核候选清单并获得用户批准，再按候选清单批量执行删除/归档（每批 ≤10 文件）。rollback proof `forward_paper_ledger_v0_1.jsonl.bak_20260525_ledger_split_repair` 不得进入任何候选清单。
- 本地噪音：`.codegraph/`、`.gitnexus/`、`.rtk/`、`__pycache__/`、`runtime_foundation_v1_0/runtime_env/`、`external_research/`、浏览器 profile 和临时 runtime 只通过 ignore 隔离，不当作业务交付。
- 验证入口：优先跑相关 `pytest`、runner/recheck 脚本和根级 `scripts/scheduler.sh`；任何交易相关输出都只能作为用户手动交易参考和实盘跟踪记录，Claude/自动化系统保持 NO_AUTO_TRADE / NO_ORDER_EXECUTION。

## 最小安全收敛路线

本 workspace 按全局和根级路线执行：代码事实源只认源码、Git diff、GitNexus 和有效 CodeGraph 索引；任务源只认 PM 当前任务系统、现有 `PROJECTS.md` / `QUEUE.md` 和本 workspace `TASK.md`；执行账本只认 Plan Enforcer、现有 `execution_log.md` 和本 workspace `LOG.md`。AgentMemory 只保存长期偏好、背景约束和决策摘要；Understand-Anything / UA 只用于 onboarding、地图和阶段分析。不得新增治理文件；可主动清理的重复配置仅限本地重复 RTK hook。

## Claude 模型池落地

本项目按具体工作项定档，不按 Hermes 旧 agent 身份、xiaochan 或 light routing 配置定档；Claude/Codex 也不是固定分工，谁执行都先按工作项选模型。

- Opus 4.6（`claude-opus-4-6`）：普通、机械、例行任务默认使用：读 `TASK/STATE/SESSION/NEXT_ACTION/HANDOFF/LOG`、找路径、扫目录、提取事实、跑命令/测试、整理失败证据、收集公开网页/浏览器证据、按已确认目标做小而明确的改动、机械更新日志/状态。
- Opus 4.7（`claude-opus-4-7`）：只用于复杂或高风险任务：架构理解、方案选择、代码编写/重构、非显然 bug、复杂调试、跨文件一致性、风险审查、最终审查，以及任何外部可见或不可逆动作。

执行要求：主会话模型不会被 hook 自动切换；当建议模型不同于当前主会话时，必须用 `claude -p --model <model>`、Agent 或 Workflow 显式传入对应 model 来执行实质工作。

Agent 工具限制：需要精确模型 ID 时优先用 `claude -p --model claude-opus-4-6` 或 `claude -p --model claude-opus-4-7`；Agent/Workflow 若只能按档位传参，必须与普通/复杂两档 Opus 规则保持一致。

Context-fetch 瘦身要求：不要在主会话直接 Read/cat 全量状态文件、长日志或大段历史；先用 Opus 4.6 生成短摘要，再把摘要交回主会话。若必须读取原文，只读最小文件集合和明确行段。

升级规则：Opus 4.6 执行中发现不确定、冲突、跨项目/跨 workspace 影响，或涉及生产、安全、资金、支付、发布、法务、税务、交易/实盘风险时，先升 Opus 4.7 再判断或落地。代码落地后，纯状态整理可回到 Opus 4.6；最终风险判断仍用 Opus 4.7。

## Context Policy

Manage Claude Code context by usage level:

- 0–40%: Normal execution.
- 40–60%: Update state files.
- 60–70%: Generate handoff.
- 70%+: Prefer saving state, running `/clear`, and starting a new session.

Avoid repeated `/compact` unless absolutely necessary.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **xiaogu** (4342 symbols, 7626 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/xiaogu/context` | Codebase overview, check index freshness |
| `gitnexus://repo/xiaogu/clusters` | All functional areas |
| `gitnexus://repo/xiaogu/processes` | All execution flows |
| `gitnexus://repo/xiaogu/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
