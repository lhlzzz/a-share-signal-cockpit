# xiaogu 提升涨停捕捉率 / 出票率 / 胜率执行计划

## Goal
当前 xiaogu 的行情数据链路、CDP 固定入口、板块/资金流/沪深港通/龙虎榜映射已经打通，下一阶段目标不再是补数据，而是提升策略输出质量。核心是围绕三个直接结果指标推进：涨停捕捉率、出票率、胜率，并按先后优先级执行，而不是扩到持仓自动化。

## Assumptions
- 当前固定 CDP 9333 实时链路可持续复用，后续优化不需要再重做数据采集基础设施。
- 当前 ledger / runtime / live_scan 足以支撑三项指标基线量化和后续 before/after 对照。
- 当前 0% 涨停捕捉率、80% 出票率、84.6% 胜率是本轮计划的权威基线，后续执行都以此为对照。

## Constraints
- 必须保留当前固定入口：CDP `http://127.0.0.1:9333`。
- 必须保持 `paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。
- 不放宽监管 hard block、near_limit_up_risk、candidate evidence、source_time/asof、一手成本等安全边界。
- 只能在现有 scanner / structured_score / runner / replay / diagnosis 链路内修改，不引入新平行系统。
- 所有改动必须有 before/after replay 或 fixed-time dry-run 证据。
- 本轮只做 1/2/3：涨停捕捉率、出票率、胜率；不把持仓自动化混进本轮计划。

## Out of Scope
- 持仓自动化。
- 账户同步、真实持仓读取、成本/盈亏自动回填。
- 实盘执行、下单、券商接口。
- 社交热度替代东财正式数据源。
- 全量重写评分系统。

## Proof Requirements

- PR1: 计划正文必须引用当前三项指标基线。
- PR1-Evidence: 当前三项指标基线为——涨停捕捉率 0%（0/13）、出票率 80%（16/20）、胜率 84.6%（11/13）。
- PR2: 后续每个阶段都要提供 replay / fixed-time dry-run / ledger 统计证据。
- PR3: 任何规则升级都必须同时报告对涨停捕捉率、出票率、胜率三者的影响，不能只报单一指标。

## Must-Haves

- MH1: 计划正文必须引用当前三项指标基线。 A:I3 A:I4
- MH1-Evidence: 当前三项指标基线为——涨停捕捉率 0%（0/13）、出票率 80%（16/20）、胜率 84.6%（11/13），并在后续每个阶段继续以这三项指标为对照。 A:I3 A:I4
- MH2: 现在只做 1/2/3：涨停捕捉率、出票率、胜率；不把持仓自动化混进本轮计划。 A:I3 A:I4
- MH3: 形成一套先后顺序明确的优化路线，优先处理涨停捕捉率，再处理出票率，再处理胜率。 A:I3 A:I4
- MH4: 针对涨停捕捉率给出可执行的主链升级方案，并证明不会破坏既有 hard gate。 A:I3
- MH5: 针对无票日给出 blocker taxonomy 与可安全放行的优化边界，而不是简单减少 NO_PICK。 A:I3
- MH6: 针对亏损票给出共性归因与收口方案，确保胜率优化不会以牺牲涨停弹性为代价。 A:I3
- MH7: 最终计划必须要求每个阶段都有 replay / fixed-time dry-run / ledger 指标证据。 A:I4

### Task 1: 在计划正文中引用当前三项指标基线 A:I3 A:I4
- [ ] 在计划正文中原样写出 proof requirement 句子：`计划正文必须引用当前三项指标基线。`
- [ ] 在同一任务中列出当前三项指标基线：涨停捕捉率 0%（0/13）、出票率 80%（16/20）、胜率 84.6%（11/13）。
- [ ] Evidence: 在计划审查记录中逐字列出 proof requirement 句子 `计划正文必须引用当前三项指标基线。` 以及三项指标基线明细。
- [ ] Verification: 明确证明“计划正文必须引用当前三项指标基线。”，并能用 ledger 数据逐条回溯核对四项数字。

### Task 2: 固化本轮范围红线 A:I3 A:I4
- [ ] 显式记录并保留原始约束句："现在只做 1/2/3：涨停捕捉率、出票率、胜率；不把持仓自动化混进本轮计划。"
- [ ] Verification: 明确证明计划中已逐字覆盖“现在只做 1/2/3：涨停捕捉率、出票率、胜率；不把持仓自动化混进本轮计划。”，且任务清单中未包含持仓自动化、账户同步、执行自动化内容。

### Task 3: 样本分层清单构建 A:I3 A:I4
- [ ] 将历史票按五类分层：涨停票、近涨停票（>=7%）、普通盈利票、亏损票、无票日。
- [ ] 列出每一层的样本清单，作为后续各阶段验证集合。
- [ ] Verification: 生成一份样本分层清单，并能用 ledger / runtime 数据逐条回溯核对。

### Task 4: 涨停捕捉率专项诊断 A:I3
- [ ] 对当前 0% 涨停捕捉率做专项归因，找出“为什么没有一笔 T+1 >= 9.5%”。
- [ ] 对最接近涨停的样本（如 `300435 +9.36%`）做 before/after 证据审计，确认差距来自哪类信号缺失。
- [ ] 对现有第三层信号 `seal_order_strength`、`broken_limit_recovery`、`intraday_volume_price_confirm` 做历史样本命中统计，判断其是否具备重新晋级潜力。
- [ ] Verification: 给出涨停捕捉率归因报告，并附带每类信号的样本命中数与误杀风险说明。

### Task 3: 涨停捕捉率主链升级方案 A:I3
- [ ] 基于 Task 2 结论，设计最小主链升级方案，优先考虑封单强度、炸板回封、分时量价确认如何重新进入 scanner/ranking/runner。
- [ ] 明确哪些信号只能进入 ranking-assist，哪些信号可以作为 dynamic confirmation 的补充条件。
- [ ] 设计固定时点 replay / dry-run 验证集，覆盖至少 1 个近涨停正例、1 个误杀反例、1 个追高风险反例。
- [ ] Verification: 形成一份升级提案，逐项说明它如何提升涨停捕捉率且不破坏监管、证据、near-limit hard gate。

### Task 4: 出票率专项诊断 A:I3
- [ ] 统计当前无票日（NO_PICK）清单，并对每个无票日抽取 closest-to-pick / first rejected / highest score 候选。
- [ ] 汇总 blocker taxonomy：例如 `near_limit_up_risk`、`buy_confirmation_below_threshold`、`QUALIFIED_CANDIDATE_FALSE`、`candidate_fund_recheck missing` 等。
- [ ] 判断哪些 blocker 属于“应坚持的安全边界”，哪些属于“存在策略改进空间的过严门”。
- [ ] Verification: 输出无票日 blocker 分布表，并标出禁止动与可研究两类 blocker。

### Task 5: 出票率安全优化方案 A:I3
- [ ] 针对 Task 4 中可研究的 blocker，设计一组不削弱 `NO_PICK` 能力的安全优化方案。
- [ ] 明确哪些优化只影响 closest-to-pick 排序，哪些优化可能影响 official eligibility。
- [ ] 评估每项方案对错误出票风险的影响，避免“为了每天出票而弱化 hard gate”。
- [ ] Verification: 形成出票率优化提案，要求每项都附带 expected benefit 与 expected risk。

### Task 6: 胜率专项诊断 A:I3
- [ ] 对当前已回填的 2 笔亏损票与 11 笔盈利票做对照，提取亏损票的共性特征。
- [ ] 检查亏损票在 close_position、fund_flow、sector、repo_contributions、risk review、intraday pattern 上的差异。
- [ ] 判断当前高胜率是否来自“过度保守”，以及它是否与 0% 涨停捕捉率存在结构性冲突。
- [ ] Verification: 输出胜率归因报告，明确亏损票的主要 failure modes。

### Task 7: 胜率收口方案 A:I3
- [ ] 基于 Task 6，提出最小化亏损票的收口方案，例如更强的失败确认、冲高回落识别、风控再平衡。
- [ ] 明确这些收口规则不能误伤已经接近涨停的高弹性票。
- [ ] 设计至少 1 个亏损票反例、1 个盈利票保留样例的 before/after 验证。
- [ ] Verification: 形成胜率收口提案，说明为什么不会进一步压低涨停捕捉率。

### Task 8: 综合验证与阶段收口 A:I4
- [ ] 将 Task 3 / 5 / 7 中拟推进的改动组合成最终候选改动集。
- [ ] 对候选改动集执行 fixed-time dry-run、历史 replay、ledger 指标对照，逐项报告对涨停捕捉率、出票率、胜率三者的影响。
- [ ] 如果三项中出现 trade-off，必须明确记录“哪项上升、哪项下降、是否接受”，禁止只报改善的一项。
- [ ] Verification: 输出一份最终对照表，含 before / after 三指标、受影响样本、是否保留到主链的结论。

### Task 9: 计划执行红线与验收机制 A:I4
- [ ] 把以下红线写入执行规则：不做持仓自动化；不取消 hard gate；不为了每天有票放弃 NO_PICK；不做 symbol hardcode。
- [ ] 为每个阶段指定必跑验证：相关 pytest、fixed-time dry-run、ledger 指标复算、必要时 GitNexus detect_changes。
- [ ] 明确任何阶段未满足验证条件时不得宣称完成。
- [ ] Verification: 形成一份阶段验收表，确保后续执行不会偏离 plan intent。