# xiaogu 下一阶段：实时出票契约 + 主力视角数据域 + 因子/Rank 升级

> **For mimocode compose mode:** Use `codebase-memory-mcp` first for code discovery. If the graph is missing, run index/reindex first; only fall back to `rg` / direct reads when graph tools are unavailable. Use RTK-style filtered output for large command logs. Use `browser-cdp` with CloakChrome/CDP as the default browser validation path.

## Source Ask
> 实时出票的时候是scanner先扫描然后转化成runner可以读取的文件形式，然后消费出票，不然之前mimocode执行scanner的时候一直超时。还要保证数据源是完整的，行情中心里面那些板块、主板/创业板、主力资金、北向资金、公告、利好信息、主力视角这些。
>
> 下一步建议里，UNDERWATER_RED_FLAT_RECOVERY 应加分，INTRADAY_ALERT_REVERSAL 应减分；Rank 4-6 要重点考虑，但不能先硬编码成不可回退的死规则。

## Normalized Goal
把 xiaogu 的实时出票链路固定成“scanner 先采集并落盘，runner 再消费文件/DB 快照”，同时把主力/游资视角下真正有用的数据域补全、标清覆盖缺口，并把复盘结论转成可回放的因子与 Rank 实验，而不是直接硬编码成新的死规则。

## Non-Negotiables
- NN1: 实时出票必须是 `scanner -> file/DB snapshot -> runner`，runner 只消费产物，不做在线抓取补洞。
- NN2: 不放宽 hard block、paper_only/no_trade、source_time/asof、监管与证据约束。
- NN3: 不创建平行系统，不重写主链，只在现有 scanner / runner / db / report 上收敛。
- NN4: 因子升级和 Rank 策略必须先做实验与回放验证，不能直接硬编码成永久规则。
- NN5: 所有结论必须能从 DB / 运行产物 / 回放结果复现。

## Tooling Requirements
- Discovery first: `codebase-memory-mcp` (`search_graph`, `trace_path`, `get_code_snippet`, `query_graph`).
- If the repo is not indexed, run the index/reindex step first.
- Use `browser-cdp` + CloakChrome/CDP for browser validation and existing login reuse.
- Use RTK-style filtered command output for large logs or long command output.
- Prefer existing code paths over new files; create new files only if a capability cannot be placed into an existing owner cleanly.

## Observed Runtime Issues
- R1: scanner 曾经在实时执行里超时，说明“在线抓取 + 即时消费”耦合太深。
- R2: 历史样本的来源边界不一致，6/6 之后和 5 月零散 evidence 的口径不同。
- R3: 复盘如果只看官方出票，不看候选历史，会继续丢失样本和因子信号。
- R4: 文件重复读取和重模块导入会放大运行峰值，影响稳定性。

## Chosen Interpretation
- Factor upgrade and Rank policy are both in scope, but as **gated experiments**.
- `UNDERWATER_RED_FLAT_RECOVERY` 先作为加分候选因子，`INTRADAY_ALERT_REVERSAL` 先作为减分候选因子。
- Rank 4-6 先作为“优先区间/软偏置”验证，Rank 1-3 和 7-10 继续保留在分析桶里，不先做不可回退的硬过滤。

## In Scope
- 固定实时链路契约：scanner 落盘、runner 消费、DB 做单一事实源。
- 扩展/核对主力视角下的数据域覆盖。
- 把复盘结论转成因子权重候选和 Rank 候选策略。
- 把 DB-first 复盘报表输出成终端 + HTML。
- 补测试和验证，证明实时链路与复盘链路可复现。

## Out of Scope
- 持仓自动化、券商执行、下单接口。
- 新建独立采集系统。
- 直接把 Rank 4-6 写成永久硬过滤而不做回放验证。
- 直接把因子升级写死成不可配置的常量。

## Main-Force / Hot-Money Coverage Map
以下数据域必须在覆盖审计中被显式记录，且缺失原因不能静默：

- Universe: 沪深京A股、主板、创业板、北交所可交易个股基础行情。
- Board / rotation: 行业板块、概念板块、板块资金流、题材传导、板块轮动强弱。
- Hot-money / microstructure: 限涨池、炸板池、连板池、昨日涨停池、首板/二板梯队、封板强度、回封、炸板、盘口/五档、分时量价、换手、量比、涨速。
- Main-force flow: 主力资金、北向资金、机构/游资净流入、分时资金曲线、大单/大宗交易、融资融券。
- Event / catalyst: 公告、利好信息、风险提示、业绩预告、研报、股东变动、解禁、停复牌、IPO 日历、交易异常。
- Regime: 市场宽度、指数环境、赚钱效应、情绪温度、板块龙头梯队。
- Auxiliary: 主力视角 / 游资视角下可解释的社交或热度信息，只作为辅助，不可替代正式数据源。

## Implementation Tasks
- [ ] Task 1: 收敛实时出票契约
  - scanner 负责采集和落盘，不让 runner 反向抓取实时数据。
  - runner 只从 scanner 产物或 DB 快照消费。
  - 实时失败或超时只能通过拆分、缓存、惰性加载、分段写盘解决。

- [ ] Task 2: 补覆盖审计与缺失原因
  - 在 scanner / runner features / report 中记录每个候选的覆盖域、缺失域、缺失原因。
  - 对上面列出的主力/游资视角域做显式审计。
  - 对“只部分覆盖”的域，不可静默空值。

- [ ] Task 3: 因子升级实验
  - 基于 DB-first 复盘结果，把 `UNDERWATER_RED_FLAT_RECOVERY` 作为正向候选加分项。
  - 把 `INTRADAY_ALERT_REVERSAL` 作为负向候选减分项。
  - 先通过配置/实验权重生效，后根据跨窗口回放结果决定是否晋级。

- [ ] Task 4: Rank 政策实验
  - 先实现 Rank 4-6 的软偏置或候选优先区间。
  - 对比“软偏置”“硬过滤”“不处理”三种策略。
  - 只有在多窗口回放里稳定优于基线时，才允许配置化推进。

- [ ] Task 5: DB-first 复盘报表增强
  - 输出终端快速版 + HTML 详细版。
  - 报表至少包含：rank bucket、setup_class、即时收益(t1)、滞后收益(t2/t3)、top10 候选、决策质量、覆盖完整性。
  - 报表数据源必须与 API / CLI 共享同一份 DB。

- [ ] Task 6: browser / tool validation
  - 用 CloakChrome/CDP 验证实时扫描与网页证据加载。
  - 用 codebase-memory-mcp 做代码发现与调用链确认。
  - 用 RTK 风格输出压缩长日志，减少无效噪声。

## Files Likely to Touch
- `xiaogu_eastmoney_web_tabs_scan_v0_1.py`
- `xiaogu_forward_d1_1450_runner_v0_1.py`
- `xiaogu_signal_effectiveness_v0_1.py`
- `xiaogu_db.py`
- `xiaogu_api.py`
- `xiaogu_scheduler.py`
- `scripts/xiaogu_db_review_report.py`
- `tests/test_xiaogu_a_share_forward_runner.py`

## Success Criteria
- scanner 先落盘、runner 后消费的执行顺序被固定。
- 实时出票不再依赖 runner 反向在线抓取。
- 主力/游资视角的关键域覆盖可见、缺失可追踪。
- `UNDERWATER_RED_FLAT_RECOVERY` / `INTRADAY_ALERT_REVERSAL` 的因子调整有回放证据。
- Rank 4-6 优先策略有实验结果，但未被硬编码成不可回退规则。
- 终端 + HTML 复盘报表都可重复生成。

## Proof Requirements
- 至少一个测试证明：scanner 产物存在时，runner 只消费文件/DB 快照。
- 至少一个测试证明：覆盖域和缺失原因会进入 features 或报表。
- 至少一个测试证明：因子升级与 Rank 策略可以在回放里比较 before/after。
- 至少一个验证步骤证明：CloakChrome/CDP 可用于网页证据验证，不依赖新的浏览器栈。

## Suggested Execution Order
1. 先收敛实时出票契约，保证 scanner -> runner 的执行边界。
2. 再补覆盖审计，把主力/游资视角的数据域补齐并显式记录缺失。
3. 接着做因子升级和 Rank 实验，先软偏置，再决定是否晋级。
4. 最后把复盘报表和验证补齐，确保可重复生成和可审计。

## Red Lines
- 不要把 Rank 4-6 直接写成永久硬规则而不做跨窗口验证。
- 不要让 runner 重新承担实时采集职责。
- 不要把数据缺失藏进默认空值。
- 不要引入新的平行数据源或平行报表链路。
- 不要把持仓自动化混进这次任务。

