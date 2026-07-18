# xiaogu File Manifest

## Canonical 状态 / 交接文档

这些文件是 xiaogu workspace 的状态、交接、规则和工具入口，优先作为恢复上下文与继续执行的 canonical 文档：

- `NEXT_ACTION.md`：下一步行动入口，只保留可直接继续执行的短清单。
- `STATE.md`：当前阶段状态、完成项、阻塞项和当前位置。
- `SESSION.md`：本轮会话目标、范围、阻塞和阶段性结论。
- `TASK.md`：当前任务拆解、执行记录和待办上下文。
- `RULES.md`：项目执行规则、边界和不可破坏约束。
- `DECISIONS.md`：已确认的关键决策与取舍依据。
- `PIPELINE.md`：A股执行链、验证链和输出流的兼容关系说明。
- `TOOLING.md`：项目专用工具、外部工具和验证入口说明。
- `CLAUDE.md`：Claude 在该 workspace 内工作的本地指令。
- `HANDOFF.md`：跨轮交接信息，包括完成、下一步和风险。
- `LOG.md`：按时间追加的执行日志和结果记录。

## A股稳定执行链

这些文件构成当前 A股核心链路，属于优先保留和验证的稳定执行入口：

- `xiaogu_eastmoney_web_tabs_scan_v0_1.py`：东财网页 tabs 扫描入口。
- `xiaogu_forward_d1_1450_runner_v0_1.py`：D1 14:50 forward runner。
- `xiaogu_forward_paper_recorder_v0_1.py`：forward paper ledger 记录器。
- `xiaogu_forward_result_filler_v0_1.py`：forward 结果补录入口。
- `xiaogu_forward_judge_scoreboard_v0_1.py`：forward judge scoreboard 生成/评估入口。
- `start_xiaogu_cdp_9333.sh`：项目隔离 CDP/browser 启动入口。

## six-repo / native 验证链

这些文件构成 six-repo、native runtime 和 research gate 的验证链，不属于本地噪音：

- `six_repo_integration_real_v2_1.py`：four-repo real integration 验证入口，保留旧名作兼容壳。
- `xiaogu_native_repo_runtime_v0_1.py`：native repo runtime 验证入口。
- `xiaogu_v2_1_six_repo_real_integrated.py`：v2.1 four-repo real integrated 主验证脚本，保留旧名作兼容壳。
- `xiaogu_v2_1_six_repo_one_year_topk_replay.py`：历史 one-year top-k replay，归档/删除优先。
- `xiaogu_quantdinger_capability_v0_1.py`：QuantDinger 能力探测/验证入口。
- `xiaogu_quantdinger_research_gate_v0_1.py`：QuantDinger research gate 入口。
- `runtime_foundation_v1_0/scripts/rollback_replay.sh`：runtime foundation rollback replay 脚本，必须保留。
- `runtime_foundation_v1_0/adapters/unified_runtime_adapter.py`：runtime foundation unified adapter，必须保留。

## 必须保留的业务证据

这些文件和目录是业务证据、验证输出或可复核资料，不是本地噪音；不要删除、不要粗暴 ignore：

- `data/live_scan/`
- `data/forward_*/`
- `data/native_repo_runtime/`
- `forward_paper_ledger_v0_1.jsonl`
- `a_share_universe_v0_4.jsonl`
- `forward_scoreboard/`
- `topn_candidate_*.jsonl`
- `v2_1_six_repo_*`
- 截图文件
- `stock_runtime_recheck.json`
- historical / alpha / research / backtest 输出

## 非 A股研究资产边界

非 A 股研究资产归属 `xiaomei`；`xiaogu` active 分类只展开 A 股核心资产，不在此处继续展开跨市场清单。

## Governance classification registry

| Class | Scope | Default handling |
|---|---|---|
| `ACTIVE_A_SHARE` | 当前 A股稳定 forward chain、状态入口和实盘决策辅助 evidence。 | 保持可见；读用无需审批，改动需按任务审批。 |
| `PROTECTED_LEDGER_EVIDENCE` | ledger、journal、scoreboard、snapshot、live scan、rollback proof 和 pinned/current inventory。 | 路径稳定保留；任何移动、归档、删除都必须逐项批准。 |
| `VALIDATION_HISTORICAL` | historical replay、alpha validation、top-n datasets、旧 v2.1 validation outputs。 | 先保留；路径依赖检查和用户批准后才可归档。 |
| `LOCAL_RUNTIME_CACHE` | `.codegraph/`、`.gitnexus/`、`.rtk/`、`__pycache__/`、runtime env、浏览器/runtime 本地状态。 | 只通过 ignore 隔离；删除必须基于 dry-run 候选并逐批批准。 |
| `REPORTS_AND_GOVERNANCE` | summary、manifest、governance docs、chain policies。 | 保留；执行索引保持简洁，重写需按任务确认。 |
| `UNKNOWN_OR_MIXED` | capability adapters 或混合 research scripts。 | 先分类；第一阶段不移动、不删除、不归档。 |

## XIAOGU_REPO_INTEGRATION_V3 registry

V3 仓库治理口径直接收敛在现有 canonical 文件中，不再为同一口径扩散新增 `summary/repo_*.md` 治理文件。

| Tier | Repos | Default handling |
|---|---|---|
| `CORE_REPOS` | `VEI`, `Qlib` | 可作为生产候选的外部核心能力，但必须先完成 evidence、no-leakage、forward、attribution 验证；不得绕过稳定 runner/ledger。 |
| `RESEARCH_REPOS` | `QuantDinger`, `tradingagent_a` | research-only / diagnosis-only / promotion-candidate；未经明确晋级审批不得影响 `candidate_score`、`ranking_score` 或 `production_pick`。 |
| `RETIRED_REPOS` | `TradingAgents`, `ai-hedge-fund`, `ZBS` | source-only；默认不读、不执行、不加载，不参与生产链路。 |

V3 生产打分边界固定为：`Xiaogu Native Evidence + validated VEI + validated Qlib`。任何研究仓库输出都必须先进入 promotion 证据包，而不是直接进入出票、排名或 ledger。

## 本地 / generated 噪音

这些内容只通过 ignore 隔离，不作为交付物；不要用它们替代业务证据整理：

- `.claude/`
- `.codegraph/`
- `.gitnexus/`
- `.rtk/`
- `__pycache__/`
- `runtime_foundation_v1_0/runtime_env/`
- browser profile
- playwright/chrome 临时输出
- cache
- dependency env
- test-results / playwright-report

## Context exclusion / token budget

以下历史文件仍作为档案证据保留，但正常会话不主动读入主上下文：

- `archive/legacy_root_2026-05-23/` 下所有旧链路脚本及输出。
- `PIPELINE.md` Removed 小节列出的旧 v0/v1/v2.0 replay 入口脚本。
- `forward_paper_ledger_v0_1.jsonl.bak_*` 备份文件；只在 rollback、审计或用户明确要求时读取。
- `v2_1_six_repo_one_year_top{1,2}_*` 等历史汇总输出；复盘用，不是日常执行入口。

正常会话只主动读取短状态入口、stable chain 脚本、当前规则和必要决策；长历史和大清单先用摘要或写入报告文件，避免把历史噪音塞进主上下文。

## 整理边界

第一阶段只建立分类入口和 ignore 边界；不迁移 evidence，不改脚本路径，不批量移动路径。

如果后续要真正搬目录，必须单独定义 taxonomy，小批量迁移，逐组更新 runner / recorder / filler / validator 路径，并完成对应验证后再继续。
