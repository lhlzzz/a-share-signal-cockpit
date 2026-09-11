# Full Skill Research As Ticket Precondition

## Source Ask
> 那么这就是问题所在了 既然东财的数据源是可以完整的获取的 并且拒绝了k线 那么就需要跑满skill 不然出票的质量难以保证 再一个需要做成出票前置啊 不然skill的作用是什么呢 本身就是用来选5个窗口内最容易获利的票

## Normalized Goal
当前官方票是 Alpha 先选出 Top3，再贴一层扫描证据 overlay。09-04/07 的完整报告是事后人工补的；09-08 UZI 没跑；09-10 Buffett 没跑；即便标了 `full_skill_workflow=True` 也只是行业资金 / ROE / 公告改写，不是 Serenity 卡点表、Buffett 八问、UZI 席位百科。用户要改的是出票顺序：东财扫描已经够用、日 K 已拒绝，所以正式票必须先跑满三技能深研，否则质量没有保证。Skill 的作用是在 5 日窗口里筛出值得观察、最可能获利的票，而不是出票之后再解释。

## Non-Negotiables
- NN1: 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。
- NN2: 唯一 Production Alpha 仍是 `profit_window_alpha_5d_v4`；唯一目标仍是 `opportunity_5d`。
- NN3: BUY 保持 BLOCKED；Live Trading 保持 DISABLED。Paper 只记 `OBSERVED + PAPER_FLAT`。
- NN4: Skill 不得发出 BUY、SELL、RANK、PICK，也不得成为第二套 Alpha。Skill 的出票权是门槛，不是排名。
- NN5: 三技能规定深研没跑满，该股不能成为官方 Paper Observation。这是出票前置，不是票面装饰。
- NN6: 跑满是必要非充分。不能只因为三技能写完就进官方票。官方 Top3 必须是跑满集合里公司业绩 / 5 日获利率最强的名字。名次仍由唯一 Alpha `selection_score` 产出，排序键不得再是研究覆盖度或 `skill_ran`。
- NN7: 回填价格只读官方东财扫描已落库 OHLC。不得把日 K HTTP 接回来。
- NN8: PostgreSQL 是权威事实。Memory / Obsidian / Graph 不得决定 Top1。
- NN9: 不重盖已锁定的历史官方票，除非用户另外明确要求。

## Hidden Contract Candidates
- HC1: “跑满”不是 `full_skill_workflow=True` 这面旗。规定产出是：Serenity 卡点表、Buffett 八问、UZI 机构 vs 游资 / 席位百科，并且每份都有 PIT 证据身份 `(source_id, event_id, mechanism)`。
- HC2: 东财源本身被当作完整。所谓“缺龙虎榜 / 缺财报”不得解释成东财没有这些接口。09-10 官方扫描里 `financials.jsonl` / `lhb.jsonl` / `earnings_preview.jsonl` 是空文件，这是扫描挂载失败，不是市场没有财报。当天没上龙虎榜是合法市场事实，UZI 必须用已落库资金流 / 公告 / 席位百科给出机构 vs 游资判断，不能把空榜写成 `not_run` 后照样出票。
- HC2b: 证据只用来自东财官方扫描应落库字段（龙虎榜、公告、业绩预告、研报、行业/个股资金流、F10 财务）。扫描没采到应采字段 = 扫描失败，挡的是当日官方出票，直到扫描补全。不得另起第二数据源，不得接日 K。
- HC3: Buffett 的买入 / 不买 / 建议买入价必须丢掉。八问回答的是 5 日 why / falsify，不是十年持有建议。
- HC4: Skill 跑满 = 进票池资格。进官方 Top3 还要在合格集合里比公司业绩和 5 日获利率。现有 `_selection_score` 的 `0.60 * coverage + 0.40 * thesis_ready` 会把“写得全”当成名次，这正是用户否掉的。VALIDATED 后用 `opportunity_5d` 概率；未 VALIDATED 时用已落库东财 F10 / 业绩预告 / 财务质量作为唯一 Alpha 的排序证据，不另起业绩分。
- HC5: 旧契约 “Research 失败不得阻断正式 Selection” 被本轮用户意图废止，仅针对官方 Paper Observation。全市场 Feature / Alpha 评估仍可跑；未跑满的股不得 `attach_top_paper_observations`。
- HC6: 浅层 interpreter / 扫描 overlay 不再算跑满，即使三个 `ran=True`。
- HC7: 09-04/07 人工贴进票面的 `Full Skill Research` 是诊断资产，不是生产自动前置。本轮要的是生产路径自动跑满，不是再补一份 markdown。
- HC8: 日频不能对全 A 每只跑完整深研。前置发生在宽候选之后、正式出票之前。

## Plausible Interpretations
- PI1: 硬门槛 + 业绩/5 日获利率排序。宽候选先跑满三技能；没跑满不能成官方票；跑满之后仍要比公司业绩和 5 日获利，不能只凭跑满进 Top3。
- PI2: Skill 直接决定 Top3。改宪法，让研究结论自己挑 3 张票，唯一 Alpha 不再排序。
- PI3: Skill 质量混进现有 `selection_score`，没跑满仍可出票。
- PI4: 先 Alpha Top3，再对 3 张补深研。这是现在 09-04/07 的做法。
- PI5: 执行宇宙每只主板都跑满三技能。

## Chosen Interpretation
PI1，并按用户补充收紧：跑满是资格，不是入场券。用户原话“就算跑满了 那也得这家公司的业绩 或者说获利率最高才行啊 不能说单独跑满了就能进啊”。PI2 仍禁止：Skill 不自己写 1/2/3。PI3/PI4 仍禁止。PI5 仍不在本轮。

## Rejected / Forbidden Narrowings
- FN1: 继续 Alpha 先出 Top3，事后补报告。
- FN2: 把 overlay 的 `full_skill_workflow=True` 改个名字，假装已经跑满。
- FN2b: 把研究覆盖度 / `skill_ran` 当官方名次，让“写完报告的票”压过业绩更好、5 日获利更强的票。
- FN3: 让 Serenity / Buffett / UZI 直接 RANK / PICK / BUY。
- FN4: 把日 K HTTP 接回来当研究或回填数据。
- FN5: 打开 Production BUY 或 Live Trading。
- FN6: 新建 skill_ranker / thesis_selector / engine_v2，或另起一套研究/选股/数据链。
- FN7: 用 LLM agent 现场“扮演”三技能，而不是用已落库东财字段生成规定产出。
- FN8: 因为快照里没有业绩预告就把 Buffett 标 `not_run` 却仍出票。东财 F10 财务 / 研报 / 预告应被扫描挂上；没挂上是扫描失败。有财务字段时 Buffett 必须跑八问，不能停。
- FN9: 重盖 09-04/07/08/09/10 已锁定官方票。
- FN10: 用 Memory / Obsidian 笔记当 5 日赚钱理由或直接出票。

## In Scope
- 把三技能规定深研做成官方出票前置：没跑满不能进入 Top3 Paper Observation。
- 跑满定义钉死为规定产出，而不是 interpreter overlay。
- 只用现有东财官方扫描应采字段生成这些产出。扫描空文件必须当失败修，不得另起数据链。
- 当天未上龙虎榜仍须跑满 UZI：用资金流 / 公告判断，空榜是观察结果不是缺源。
- 跑满集合里，官方 Top3 必须按公司业绩 / 5 日获利率排序；覆盖度不得当名次。仍走唯一 `selection_score`，不另起业绩分。
- 研究结论必须回答 `opportunity_5d`：未来 5 个交易日为什么可能赚钱，以及什么情况证明它错。
- 生产记账必须能证明：requested / succeeded / 规定产出齐全 / 因不齐全被挡票。
- 回填继续只读官方扫描 OHLC。

## Out of Scope
- 让 Skill 自己写官方排名。
- 对全 A / 全部执行宇宙逐只跑完整深研。
- 重写 Scanner / Alpha 公式 / Calendar / Snapshot 不变性。
- 训练并宣称新 Alpha 已 VALIDATED。
- 今日无确认地重跑历史官方票。
- 打开 BUY / Live Trading。
- 把 65 评委接回生产。
- 把日 K 接回回填。

## Constraints
- 现有 Owner 文件保持唯一职责。Research 产出门槛；Decision 出状态；Alpha 出唯一排序分；Recorder 只记官方 Top3。
- Calendar / Snapshot immutability / Position identity 不变。
- 真实 Skill 方法论可以贵，但本轮生产路径必须是可复现脚本 + 已落库东财字段，不能依赖交互式 agent。
- 日频出票仍要在现有 pipeline 里完成，不能把扫描卡住等人工深研。

## Success Signals
- 没有三份规定产出的股票，`paper_observation` 必须是空，不能进官方 Top3。
- 官方票票面不再出现 interpreter overlay 冒充跑满；Serenity 有卡点表，Buffett 有八问，UZI 有机构 vs 游资 / 席位判断。
- 两只都跑满时，业绩 / 5 日获利更强的才是 Top1；只改研究报告文案不得改名次。
- 现有 coverage 公式不得再作为官方 `selection_score`。
- 扫描应采的东财财务 / 预告 / 研报没挂上时，当日官方出票被挡住，直到现有 Scanner 修好。
- 当天没上龙虎榜不再写成 UZI `not_run`；有资金流或公告就必须给出机构 vs 游资判断。
- Paper 仍是 Top3<=3 / Top1==1；BUY BLOCKED；T+1..T+5 仍按 `paper_signal_id` 验证。
- 回填路径源码里仍没有 `kline/get`。

## Drift Risks
- DR1: 把 Skill 研究做成第二套 ranking。
- DR2: 把“跑满”实现成再贴一层 markdown。
- DR3: 只对已经选出的 3 张票跑深研，宣称已经前置。
- DR4: 把扫描空文件解释成“东财没有财报/龙虎榜”，然后另起数据源。
- DR4b: 空龙虎榜当成缺源而 `not_run`，有资金流却仍出票。
- DR5: 为了前置改 Alpha 公式或打开 BUY。
- DR6: 讨论后又把 Research 失败改回“不阻断出票”。
- DR7: 用 LLM 现场深研冒充可复现生产路径。
- DR8: 重盖已锁定历史官方票。

## Proof Requirements
- PR1: 突变测试：拿掉任一份规定产出后，该股不得成为官方票。
- PR2: 三份规定产出字段存在且含 PIT 证据身份；overlay 句子不够。
- PR3: Top3 仍来自 `attach_top_paper_observations` + 唯一 Alpha `selection_score`。
- PR4: 源码断言回填不再调用日 K。
- PR5: 研究结论能回答 `opportunity_5d` why / falsify。
- PR6: 生产路径仍无 BUY / LIVE。

## Resolved Forks
1. 本轮目标 = PI1：Skill 深研是官方出票硬门槛；跑满之后按公司业绩 / 5 日获利率选 Top3。覆盖度不是名次。
9. 用户补充：就算跑满了，也得这家公司业绩或 5 日获利率最高才能进；不能单独跑满就进。
2. 旧 NN8（Research 失败不阻断正式 Selection）对官方 Paper Observation 作废。未跑满 = 不能出官方票。全市场评估仍可进行。
3. “用来选 5 日窗口最容易获利的票”= 前置过滤进票池，不是 Skill 输出 1/2/3。
4. 跑满对象 = 宽候选，不是全 A，也不是先 Top3 再补报告。
5. 跑满材料 = 现有东财官方扫描应采字段。不接日 K，不另起第二数据源 / 第二研究链 / 第二选股 owner。
6. 规定产出 = Serenity 卡点表 + Buffett 八问 + UZI 机构 vs 游资 / 席位百科。扫描没挂上应采字段 = 挡当日官方出票。空龙虎榜不是缺源。
7. 不重盖已锁定历史官方票。
8. 用户补充：东财数据源是全的；不能另外起。09-10 空 jsonl 证明问题在现有扫描挂载，不在东财缺接口。

## Open Forks
用户未回答分叉题。本包按 PI1 锁定。若用户改口要 Skill 直接挑 Top3，或要对全部执行宇宙跑满，必须重开 discuss。

## Draft Handoff
- phase shape hint: 先钉跑满契约和挡票点，再让现有 Research 用已落库东财字段生成三份规定产出，再让现有 qualification 挡未跑满，最后让现有 `_selection_score` 在跑满集合里按业绩 / 5 日获利排序。不要新选股 owner，也不要用覆盖度当名次。
- planning red lines: 不另起数据/研究/选股链；不让 Skill 写排名；不把 overlay 或跑满本身当名次；不接日 K；不打开 BUY；不重盖历史官方票；不把空龙虎榜或扫描空文件解释成东财没数据。
