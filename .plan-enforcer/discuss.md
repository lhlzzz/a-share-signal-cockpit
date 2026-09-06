# Research Skill Into Existing Production Chain

## Source Ask
> 全A市场现实扫描
>         ↓
> 硬过滤
>         ↓
> 宽候选路由
>         ↓
> 真正投研 Skill 深度研究
>         ↓
> 判断“未来5日为什么可能赚钱”
>         ↓
> 反证 / 风险 / 资金 / 供给 / 需求 / 估值 / 催化
>         ↓
> 形成候选研究结论
>         ↓
> Production Alpha
>         ↓
> Top3 / Top1 Paper Observation
>         ↓
> T+1 ~ T+5 真正验证

## Normalized Goal
用户的系统图是：Xiaogu 负责扫全 A 市场数据；仓库里已有的投研 Skill 负责真正研究（Serenity 看产业链卡点，UZI/Buffett 看公司基本面，UZI/龙虎榜看资金和情绪）；研究完再判断未来 5 个交易日为什么可能赚钱；最后仍由现有唯一 5 日模型给出 Top3/Top1，并用真实 T+1..T+5 验证。缺口不是缺 Skill，而是生产链里的 Research 没有调用这些 Skill，只是把行情特征换了个 Serenity/Buffett/UZI 的名字。用户不需要、也不想面对 L1/L2 术语；那些只是内部实现细节。

## Non-Negotiables
- NN1: 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。
- NN2: 唯一 Production Alpha 仍是 `profit_window_alpha_5d_v4`；唯一目标仍是 `opportunity_5d`。
- NN3: BUY 保持 BLOCKED；Live Trading 保持 DISABLED。Paper 只记 `OBSERVED + PAPER_FLAT`。
- NN4: Research / Skill 不得发出 BUY、SELL、RANK、PICK，也不得成为第二 Alpha。
- NN5: 硬过滤 = L1 operational + MAIN_BOARD execution universe。不得用涨幅/主题/评分当硬过滤。
- NN6: 宽候选路由 = L2 resource router（`detect_capital_candidates`）。0.5%–9.5% 仍是 ablation，不是冻结 Alpha 规则。
- NN7: PostgreSQL 是权威事实；Skill 输出只进 Research Context。Memory / Obsidian / Graph 不得决定 Top1。
- NN8: Research provider 失败不得阻断正式 Selection。完整市场覆盖仍是契约。

## Hidden Contract Candidates
- HC1: “真正投研 Skill”必须可证明被消费：requested / available / succeeded / failed / usable_evidence_count / used_by_alpha。适配器重打包不算深度研究。
- HC2: 研究结论回答的是 `opportunity_5d`：未来 5 个交易日任意一日净 +2%。不是长期基本面故事，也不是当天涨幅叙事。
- HC3: 反证 / 风险 / 资金 / 供给 / 需求 / 估值 / 催化 是 Research 维度，不是新的 ranking axes。
- HC4: 昂贵研究预算只决定“谁被深度研究”，不决定“谁进 Top3”。用户语言里不要再把这层叫成选股。
- HC5: 没被深度研究的可交易主板股票仍须进入唯一 5 日模型；不得用“只研究了几只”冒充全市场结论。
- HC6: 候选研究结论 ≠ Paper Observation。结论进入 Research Context 后，仍由 `build_core_alpha` + `attach_top_paper_observations` 出票。
- HC7: Buffett Skill 来源是 `https://github.com/agi-now/buffett-skills`，仓库尚未 vendoring。UZI deep-analysis 里的 Buffett persona / 65 评委不是这份 Skill。
- HC8: 65 评委对 Xiaogu 唯一生产链没有作用。`xiaogu_*.py` 没有任何调用。用户要求删除，不得接入 Research，不得参与出票。
- HC9: PostgreSQL、前端、Obsidian 第二大脑是已有资产，不是要删的东西。Skill 真正被吃进唯一链路并出票之后，用数据库历史出票和 Obsidian 笔记对照，找研究缺漏。对照是诊断，不能决定 Top1。

## Plausible Interpretations
- PI1: 对照诊断。把用户管线映射到现有 Owner，指出缺口，不改代码。
- PI2: 在现有 `xiaogu_research_context.py` 上接入真实 Skill，让 routed 候选先形成 5D 研究结论，再进唯一 Alpha / Top3 / Outcome。
- PI3: 今天就对全 A 跑这条链，产出当日 Top3/Top1。
- PI4: 用 Skill 研究替代 Alpha / Selection，让研究结论直接出票。

## Chosen Interpretation
PI2，并用用户自己的话说清：Xiaogu 扫市场；已有 Serenity / UZI（含 Buffett 基本面与资金情绪）做真正研究；唯一 5 日模型仍负责从研究结论里判断 Top3/Top1。用户不需要理解或操作 L1/L2。PI3 今日实跑不在本轮。PI4 禁止：Skill 研究不能自己“找出并排名”Top3。

## Rejected / Forbidden Narrowings
- FN1: 新建 research_v2 / skill_ranker / thesis_selector。
- FN2: 让 Serenity / UZI / deep-analysis 直接 RANK 或出 Top1。
- FN3: 把 0.5%–9.5% 写成永久生产硬门。
- FN4: 只对 routed 子集评估 Alpha，然后宣称这是全市场 Top1。
- FN5: 把 Feature 适配器改个名字，假装已经是“真正投研 Skill”。
- FN6: 打开 Production BUY 或把研究结论当成 LIVE 信号。
- FN7: 用 Memory / Obsidian 笔记当 5D 赚钱理由或直接出票。
- FN8: 把 65 评委、panel.json、评委打分嵌进 Xiaogu Research / Alpha / Top3。
- FN9: 因为不用 65 评委，就把前端、HTML 研报资产、数据库历史出票或 Obsidian 第二大脑一并删掉。

## In Scope
- 在现有 `xiaogu_research_context.py` 上接入真实投研 Skill。
- 研究结论必须回答 `opportunity_5d`：这只股票未来 5 个交易日为什么可能赚钱，以及什么情况证明它错。
- 反证 / 风险 / 资金 / 供给 / 需求 / 估值 / 催化 作为 Research 维度进入 Context，供唯一 Alpha 消费。
- 证明 Skill 被真实调用且可记账：requested / available / succeeded / failed / usable_evidence_count / used_by_alpha。
- 未研究或 Skill 失败的可交易主板仍走唯一 5 日模型；不得用“只研究了几只”冒充全市场结论。
- 从 Xiaogu 唯一链路中删除 65 评委：不调用、不消费、不作为研究证据。
- Skill 被吃进唯一链路并出票之后，对照 PostgreSQL 历史出票和 Obsidian 第二大脑资产，找出研究缺漏。对照结果只回 Research / 笔记，不改 Top1。

## Out of Scope
- 重写 Scanner / Alpha / Decision / Selection。
- 训练并宣称新 Alpha 已 VALIDATED。
- 今日无确认地全市场实跑（除非用户明确要 PI3）。
- 打开 BUY / Live Trading。
- 把 Understand-Anything / AgentMemory 当市场证据。
- 删除前端、数据库历史出票或 Obsidian 第二大脑。
- 把 UZI 的 HTML 研报能力从仓库资产里清掉。HTML 可以继续作为研究产物/前端资产；65 评委不能进生产出票。

## Constraints
- 现有 Owner 文件保持唯一职责。
- Calendar / Snapshot immutability / Position identity 不变。
- L2 价格窗 ablation 未完成前不得冻结为策略。
- 真实 Skill 调用昂贵；只能作为 routed 研究预算，不能替代全市场 Feature/Alpha。

## Success Signals
- 用户管线的每一步都能指到唯一 Owner，或被明确标成未接入。
- 被深度研究的票能产出 PIT 研究结论；Alpha 能标记 used_by_alpha；Skill 失败不阻断 Top3。
- Top1 仍随唯一 5 日模型变化，不随 Skill 文案或 65 评委分数直接改票。
- Paper 仍是 Top3<=3 / Top1==1；T+1..T+5 按 `paper_signal_id` 验证 `opportunity_5d`。
- 生产 Research 证据链里找不到 65 评委 / panel 打分。
- 出票后能用 PostgreSQL 历史出票和 Obsidian 笔记对照，列出缺漏；对照不改正式 Top1。

## Drift Risks
- DR1: 把 Skill 研究做成第二 ranking。
- DR2: 把宽路由做成第二候选池/选股器。
- DR3: 用适配器包装冒充深度研究。
- DR4: 只研究 routed 子集却对外声称全 A 扫描结论。
- DR5: discuss 后直接改 Alpha / Selection。
- DR6: 把 65 评委重新接回 Research。
- DR7: 用 PostgreSQL / Obsidian 对照结果直接改 Top1。
- DR8: 因为删除 65 评委而清掉前端、HTML 资产或第二大脑。

## Proof Requirements
- PR1: Owner 对照表：用户步骤 ↔ 现有函数。
- PR2: 接入后证明 Serenity/Buffett/UZI context 调用了 `.agents/skills` 下的可复现 Skill 产出，而不只是重打包 Feature。
- PR3: mutation 证明删除 Skill 证据不会让 `research_consumed=True`，也不会改第二套分数出票。
- PR4: Top1 仍来自 `attach_top_paper_observations` + 唯一 Alpha `selection_score`，除非用户明确批准本轮改 Alpha 排序。
- PR5: 研究结论字段能回答 `opportunity_5d` why/falsify；T+1..T+5 仍按 `paper_signal_id` 验证。
- PR6: 生产路径和 Research 消费证明不含 65 评委。
- PR7: 出票后对照 PostgreSQL 历史出票 + Obsidian 资产，产出缺漏清单；对照不得写入 Decision / Selection。

## Resolved Forks
1. 本轮目标 = PI2：让已有投研 Skill 真正进入唯一生产 Research。不对照-only，不今日全 A 实跑，不用 Skill 替代唯一 5 日模型。
2. 用户心智模型：Xiaogu = 扫全 A 市场数据；Skill = 产业链 / 公司基本面 / 资金情绪研究；输出 = 未来 5 日为什么可能赚钱。内部路由术语对用户不可见。
3. Buffett Skill 来源 = `https://github.com/agi-now/buffett-skills`。仓库当前未 vendoring。它是方法论 Skill（`SKILL.md` + 8 份 references），没有可复现脚本，标准输出含买入/不买/持有/卖出。生产不得执行其买卖结论。
4. 全市场扫描；完整 Skill 只研究值得花时间的票。其余可交易主板仍进唯一 5 日模型，不做深度研究，也不从候选里消失。
5. 生产接可复现研究脚本，产出“未来 5 日为什么可能赚钱 / 什么会证伪”。
6. Skill 只给研究结论。Top3 / Top1 仍由现有唯一 5 日模型拍板。Skill 不得自己挑 3 只。
7. 三个研究槽的生产接入：
   - Serenity：现有 `.agents/skills/serenity-skill` 的卡点/需求证据。
   - Buffett：vendor `agi-now/buffett-skills` 作为基本面方法论；用财报/护城河/估值证据填 8 问检查清单与财务快照，映射到现有 CompanyContext。丢弃买入/卖出/建议买入价。
   - UZI：龙虎榜 / 资金流 / 情绪 / 公告事件，映射到资金与情绪槽。
8. 65 评委删除。它对 Xiaogu 唯一链路没有作用，生产从未调用。不得接入 Research，不得作为出票证据。
9. 前端、数据库、Obsidian 第二大脑、HTML 研报资产保留。Skill 被唯一链路吃进并出票之后，对照 PostgreSQL 历史出票和 Obsidian 笔记找缺漏。对照是诊断，不是第二套选股。

## Open Forks
无。剩余实现细节交给 draft，不再改变本轮目标。

## Draft Handoff
- phase shape hint: vendor Buffett skill；只改 Research Context，让 Serenity/Buffett/UZI 可复现研究填进现有槽；去掉 65 评委；研究结论必须含 5 日 why / falsify；出票后再用 PostgreSQL + Obsidian 对照缺漏。
- planning red lines: 不教用户内部路由术语；不新建选股 owner；不让 Skill 直接排名或发出买卖；不把 65 评委嵌进生产；不删除前端 / 数据库 / 第二大脑；不对全市场每只股票跑完整深度研究；不把 Buffett 的买入价/买卖结论写进 Alpha；对照缺漏不得改正式 Top1。
