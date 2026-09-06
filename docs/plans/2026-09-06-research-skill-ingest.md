# Research Skill Into Existing Production Chain

**Goal:** 用户的系统图是：Xiaogu 负责扫全 A 市场数据；仓库里已有的投研 Skill 负责真正研究（Serenity 看产业链卡点，UZI/Buffett 看公司基本面，UZI/龙虎榜看资金和情绪）；研究完再判断未来 5 个交易日为什么可能赚钱；最后仍由现有唯一 5 日模型给出 Top3/Top1，并用真实 T+1..T+5 验证。缺口不是缺 Skill，而是生产链里的 Research 没有调用这些 Skill，只是把行情特征换了个 Serenity/Buffett/UZI 的名字。用户不需要、也不想面对 L1/L2 术语；那些只是内部实现细节。
**Constraints:** 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。Skill 不得 RANK / PICK / BUY。65 评委不得接入。前端、数据库、第二大脑、HTML 研报资产保留。完整 Skill 只解释已做深度抓取的票；其余可交易主板仍进唯一 5 日模型。生产解释已捕获的同日观察，不在决策时打实时网。
**Out of scope:** 重写 Scanner / Alpha / Decision / Selection；打开 BUY；今日全 A 实跑；把评委打分、Obsidian 笔记或历史对照写成第二套选股。

## Must-Haves

- MH1: 被深度研究的票不再只是把行情特征换个 Serenity/Buffett/UZI 名字；研究结论含 5 日 why / falsify，并带来源完整的 evidence。 A:I26 A:I27 A:I28
- MH2: 全市场仍被扫描和评估；完整 Skill 只解释值得花时间、已有深度观察的票。 A:I28
- MH3: Top3/Top1 仍由唯一 5 日模型拍板；Skill 和 Buffett 检查清单不得发出买卖或排名。 A:I26 A:I16
- MH4: 65 评委不进入生产 Research / Alpha / 出票。 A:I30
- MH5: 出票后能对照 PostgreSQL 历史出票和 Obsidian 笔记列出缺漏；对照不改 Top1。前端、库、第二大脑保留。 A:I29
- MH6: 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。改动落在现有 `xiaogu_research_context.py` 和现有 skills 目录。 A:I16

### Task 1: Record the owner map A:I25 A:I27
- [ ] Owner 对照表：用户步骤 ↔ 现有函数。扫描=`scrapy_scanner.runner_v2`；硬过滤=`cheap_eligibility_blockers`/`execution_universe`；宽路由=`detect_capital_candidates`；研究=`build_integrated_research_context`；5 日判断=`build_core_alpha`；Top3=`attach_top_paper_observations`；验证=`xiaogu_forward_result_filler_v0_1`
- [ ] 断言这些 owner 文件仍是唯一实现，没有新建 scanner/alpha/decision/selection 模块
- Verification: Owner 对照表：用户步骤 ↔ 现有函数。新测试读取这些函数的 `__module__`，确认不存在第二套同名 owner

### Task 2: Vendor Buffett skill methodology A:I28
- [ ] 把 `https://github.com/agi-now/buffett-skills` 的 `skills/buffett` 放到 `.agents/skills/buffett/`
- [ ] 生产只使用 8 问检查清单、护城河/财务/估值参考，丢弃买入/不买/持有/卖出和建议买入价
- Verification: `.agents/skills/buffett/SKILL.md` 存在；生产代码不把 Conclusion 买卖段写成 action

### Task 3: Make Serenity context interpret captured chain evidence A:I26 A:I27
- [ ] 修改 `xiaogu_research_context.build_serenity_context`：用已捕获的行业报告/行业资金/需求观察写卡点、催化、5 日 why / falsify
- [ ] 有深度观察时 `skill_ran=True`；无深度观察时 `skill_ran=False`，不得假装已做 Skill 研究
- [ ] 失败走现有 `_safe_context` 降级
- Verification: 带行业报告的 snapshot 上 Serenity `skill_ran=True` 且 evidence 有 identity；无行业观察时 `skill_ran=False`

### Task 4: Make Buffett context fill the 8-question checklist A:I28
- [ ] 修改 `build_buffett_context`：用已捕获的财报预告/公司研报/财务字段填 8 问检查清单与财务快照
- [ ] `buy_sell=None`，不输出建议买入价
- [ ] 有深度观察时 `skill_ran=True`；无则 `skill_ran=False`
- Verification: 带 earnings_preview 的 snapshot 上 Buffett checklist 有条目且 `buy_sell is None`；买卖字段变化不能进入 Decision

### Task 5: Make UZI context interpret captured capital and LHB A:I27 A:I30
- [ ] 修改 `build_uzi_context`：用已捕获龙虎榜/资金流/公告解释机构 vs 游资、分布风险、情绪
- [ ] 可本地匹配席位；不 import `investor_evaluator` / panel / 65 评委
- [ ] 有深度观察时 `skill_ran=True`；无则 `skill_ran=False`
- Verification: 带 LHB 的 snapshot 上 UZI `skill_ran=True`；`xiaogu_research_context.py` 源码不含评委 import

### Task 6: Attach a 5-day thesis without creating a second score A:I26 A:I25
- [ ] 在 `build_integrated_research_context` 增加 `opportunity_5d_thesis`：`why_5d`、`falsify`、资金/供给/需求/估值/催化/反证
- [ ] Alpha 可读取该 thesis 作为 research 消费，但 `_selection_score` 不改为 thesis 分数
- [ ] Paper observation 的 research overlay 引用 why/falsify，不新增 RANK 字段
- Verification: thesis 存在且不含 BUY/SELL/PICK；改变 thesis 文案不单独改变 Top1；`selection_score` 仍来自唯一 Alpha

### Task 7: Compare historical tickets and Obsidian notes after research A:I29
- [ ] 用已有 `fetch_historical_research_cases` 和 `fetch_memory_research_notes` 生成 `research_gap_audit`：缺历史对照、缺笔记、历史失败模式未进入当前 falsify
- [ ] 对照结果只进 Research Context，不进 Decision / Selection
- Verification: 人为制造历史失败模式时 audit 能列出缺漏；audit 变化不改变 `selection_score` 和 Top1

### Task 8: Prove the unique chain and keep assets A:I16 A:I30
- [ ] 测试：`xiaogu_*.py` 不 import 65 评委 / `investor_evaluator` / `panel.json`
- [ ] 测试：Skill 证据删除后不能靠 adapter 假装真实 Skill evidence
- [ ] 现有 data-contract / single-system / production-contract 相关断言仍绿
- [ ] 不删除前端、数据库、Obsidian adapter、UZI HTML 研报资产
- Verification: `pytest tests/test_data_contract.py tests/test_single_system_convergence.py tests/test_production_contract.py tests/test_research_skill_ingest.py -q` 通过
