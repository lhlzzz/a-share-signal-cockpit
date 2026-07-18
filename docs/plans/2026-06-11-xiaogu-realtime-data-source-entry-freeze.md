# Xiaogu Realtime Data-Source Entry Freeze

**Goal:** 确认 xiaogu 当前实时链路能否稳定、只读地读取实时数据源，并把可复用的运行流程与入口固定下来，避免后续开盘/复盘时临时找脚本或走错链路。重点是读数据和固化入口，不是改交易策略或扩大系统。

**Constraints:**
- 严禁任何自动交易、下单、broker/API key/order endpoint 或交易写操作；只允许读取行情、自选、持仓、资金、成本、盈亏并生成参考/复盘证据。
- 开发治理链路和实时运行链路不得混用；实时热路径不能依赖 CodeGraph/GitNexus/UA/AgentMemory/Plan 参与决策。
- 优先复用和固定现有入口、配置、脚本和文档；不新增并行 `realtime_v2`、`runner_new` 或替代链路。
- 若账号、登录态、网页可见性、反爬或数据缺失阻塞读取，必须明确标为 `BLOCKED`，不得伪造实时可读结论。
- 任何代码/配置修改必须先遵守 xiaogu 工具链：CodeGraph 定位，GitNexus upstream impact，必要时 Karpathy guidelines，修改后验证。

**Out of scope:**
- 自动交易、下单、券商写接口、API key/order endpoint 接入。
- 策略大改、评分模型重写、并行新版链路。
- 大规模清理、归档、治理文件扩张或跨 workspace 工作。

## Must-Haves

- MH1: 明确当前实时链路的唯一稳定入口和人工运行流程，下一次开盘/复盘可按同一流程复现。 A:I1
- MH2: 用只读 smoke/recheck 证明行情、自选、持仓、资金、成本、盈亏各数据项分别是可读、部分可读还是 blocked。 A:I1
- MH3: 运行证据必须包含只读命令/脚本的运行结果或失败输出、入口、输入配置、数据源响应/输出快照、失败原因，且不泄露敏感凭据。 A:I1
- MH4: NN1 preserved — 严禁任何自动交易、下单、broker/API key/order endpoint 或写交易动作；系统只读行情、自选、持仓、资金、成本、盈亏并生成参考/复盘证据。 A:I1
- MH5: 固化结果写回现有 xiaogu 状态/操作说明位置，避免新增并行链路或新治理体系。 A:I1

### Task 1: Restore xiaogu context and execution boundaries A:I1
- [ ] 读取最小上下文：`NEXT_ACTION.md`、`STATE.md`、`SESSION.md`；只有本轮需要追溯任务/规则时再读取 `TASK.md`、`HANDOFF.md`、`DECISIONS.md`、`RULES.md` 的相关段落。
- [ ] 确认本轮只处理 xiaogu 实时读取链路，不切换其他 workspace，不处理非 A 股资产。
- [ ] 在执行记录中写明边界：本轮只能读取行情/自选/持仓/资金/成本/盈亏，禁止 broker、order endpoint、自动下单、交易写操作。
- [ ] Verification: 能在会话记录或状态更新中看到本轮 scope、NO_AUTO_TRADE / NO_ORDER_EXECUTION 边界，以及需要验证的数据项清单。

### Task 2: Locate the existing realtime entry and data-source owners A:I1
- [ ] 使用 CodeGraph 精确入口词定位现有链路：`xiaogu_forward_d1_1450_runner_v0_1`、`evaluate_candidate_bundle`、`decision_for_candidate`、`run_recorder`、`xiaogu_eastmoney_web_tabs_scan_v0_1`。
- [ ] 使用 GitNexus query/context 补充确认 realtime/scan/ticket 执行流、数据源配置位置、输出 ledger/summary/runner 关系。
- [ ] 选定一个当前主入口；若发现多个候选入口，按“现有 active/stable chain + 当前配置 + 最近验证证据”选择唯一主入口，并把其余入口标为非本轮入口而不是新增或复制。
- [ ] Verification: 产出一份短清单，包含主入口命令/脚本、读取的数据源、输出位置、关联配置、为何不是其他候选入口。

### Task 3: Prepare the read-only smoke command A:I1
- [ ] 将 Task 2 选定的现有主入口转换成一条本轮要执行的 smoke/recheck 命令，命令参数必须只启用读取和输出证据。
- [ ] 在命令或执行说明旁标注 `NO_AUTO_TRADE=1` / `NO_ORDER_EXECUTION=1` 边界；不得提供 broker、order endpoint、API key 或交易写参数。
- [ ] Verification: 记录待执行命令、工作目录、输入配置和输出目标；命令文本中没有 broker/order/write/trade execution 参数。

### Task 4: Execute the read-only smoke and capture raw proof A:I1
- [ ] 执行 Task 3 的只读 smoke/recheck 命令。
- [ ] 记录退出码、关键 stdout/stderr、输出文件路径或失败输出；账号/页面/浏览器相关读取只记录必要字段可见性与状态，不保存敏感凭据、cookie、token、完整账号截图或可复用登录材料。
- [ ] Evidence: 相关只读命令/脚本的运行结果或失败输出。
- [ ] Verification: PR1 preserved — 已保存或引用实际命令、退出码、关键输出或失败输出；没有任何交易写动作日志。

### Task 5: Build the six-source read-status matrix A:I1
- [ ] 从 Task 4 输出中分别判定行情、自选、持仓、资金、成本、盈亏六类数据状态。
- [ ] 每类数据只允许标为 `READ_OK`、`PARTIAL` 或 `BLOCKED`，并附一行基于输出证据的原因。
- [ ] Verification: 状态矩阵正好覆盖六类数据；每个 `PARTIAL` / `BLOCKED` 项都有具体错误、缺失字段或阻塞原因；没有凭历史 ledger 或源码推断替代本轮输出证据。

### Task 6: Classify smoke outcomes without code changes A:I1
- [ ] 如果六类数据全部 `READ_OK`，记录“无需代码/配置修复”，跳过 remediation，直接进入入口固化。
- [ ] 如果数据项因登录态、账号权限、页面不可见、反爬、外部服务不可达或用户人工步骤缺失失败，标记为 `BLOCKED: NEED_BROWSER_OR_ACCOUNT_ACCESS`、`BLOCKED: EXTERNAL_SERVICE_UNAVAILABLE` 或更具体的 blocked 原因。
- [ ] 如果失败看起来来自解析、配置、路径或现有入口参数错误，列出可疑 owner 符号/文件，但本任务不修改代码。
- [ ] Verification: 每个 `PARTIAL` / `BLOCKED` 项都有一条分类原因；外部/账号类失败没有被代码绕过；可疑代码类失败只进入下一任务评估。

### Task 7: Apply minimal remediation only for deterministic local owner failures A:I1
- [ ] 仅当 Task 6 明确失败来自现有入口的确定性解析/配置/路径错误时，使用 CodeGraph 定位 owner，并对待改符号运行 GitNexus upstream impact。
- [ ] 若 GitNexus impact 返回 HIGH 或 CRITICAL，先报告风险并暂停修改；若 LOW/MEDIUM 且修复必要，只修改现有 owner 文件或现有配置。
- [ ] 禁止新增并行 runner、替代 scanner、重复配置体系或 `*_v2/new/final` 文件；禁止接入 broker、order endpoint、自动下单或凭据落盘。
- [ ] Verification: 若无本地 owner 故障，记录“no remediation needed/skipped”；若有改动，保存 impact 结果、变更摘要、针对性 smoke 或测试结果，并证明无法修复的项仍保持 blocked 而不是宣称成功。

### Task 8: Freeze the stable flow in existing project state files A:I1
- [ ] 在现有 xiaogu 状态/操作说明位置记录稳定入口：命令、工作目录、前置条件、只读边界、输入配置、输出位置、常见 blocked 原因。
- [ ] 更新 `NEXT_ACTION.md` 为最多 3 条下一步，围绕下一次实时读取/开盘验证继续，不重新规划。
- [ ] 更新 `STATE.md` / `SESSION.md` / `LOG.md` 中的完成、阻塞、当前位置和本轮证据；需要任务源同步时更新 `TASK.md` 的相关现有条目。
- [ ] Verification: 状态文件能让下一轮从固定入口继续；文档没有新增并行链路；没有创建新的治理/report 文件替代现有状态源。

### Task 9: Final validation and scope check A:I1
- [ ] 运行相关验证：只读 smoke/recheck；若改代码则运行相关 `pytest` 或 runner 测试；运行根级 `scripts/scheduler.sh` 做系统层校验。
- [ ] 若存在代码/配置变更，运行 `gitnexus_detect_changes(scope="all")`，确认影响范围只覆盖预期实时读取链路和状态更新。
- [ ] 检查 git diff，确认没有 broker/order endpoint、自动下单、凭据落盘、并行 runner、新治理文件或跨 workspace 无关改动。
- [ ] Verification: 最终回复列出验证命令与结果、相关只读命令/脚本的运行结果或失败输出、六类数据读取状态矩阵、固定入口、blocked 项、NO_AUTO_TRADE / NO_ORDER_EXECUTION 证明、未完成限制；明确复述“严禁任何自动交易、下单、broker/API key/order endpoint 或写交易动作；系统只读行情、自选、持仓、资金、成本、盈亏并生成参考/复盘证据”。
