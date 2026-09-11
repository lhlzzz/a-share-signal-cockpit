# Full Skill Research As Ticket Precondition

**Normalized Goal:** 当前官方票是 Alpha 先选出 Top3，再贴一层扫描证据 overlay。09-04/07 的完整报告是事后人工补的；09-08 UZI 没跑；09-10 Buffett 没跑；即便标了 `full_skill_workflow=True` 也只是行业资金 / ROE / 公告改写，不是 Serenity 卡点表、Buffett 八问、UZI 席位百科。用户要改的是出票顺序：东财扫描已经够用、日 K 已拒绝，所以正式票必须先跑满三技能深研，否则质量没有保证。Skill 的作用是在 5 日窗口里筛出值得观察、最可能获利的票，而不是出票之后再解释。跑满只是资格，进 Top3 还要在合格集合里比公司业绩和 5 日获利率，不能只因为研究报告写完就进。
**Goal:** 当前官方票是 Alpha 先选出 Top3，再贴一层扫描证据 overlay。用户要改的是出票顺序：东财扫描已经够用、日 K 已拒绝，所以正式票必须先跑满三技能深研；跑满只是资格，进 Top3 还要在合格集合里比公司业绩和 5 日获利率，不能只因为研究报告写完就进。Skill 用来筛 5 日窗口里值得观察的票，不是出票之后再解释。
**Constraints:** 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。Skill 不得 RANK / PICK / BUY。BUY 保持 BLOCKED。回填不接日 K。不重盖 09-04 到 09-10 已锁定官方票。空龙虎榜是市场事实，不是缺源。覆盖度不得当官方名次。
**Out of scope:** Skill 直接写 1/2/3；对全 A 或全部执行宇宙逐只深研；打开 BUY / LIVE；重跑历史官方票；把 65 评委接回生产；接日 K；用 LLM agent 现场扮演三技能；另起业绩分 / ranker。

## Must-Haves

- MH1: 没有三份规定产出的股票不能成为官方 Paper Observation。 A:I31
- MH2: 规定产出是 Serenity 卡点表、Buffett 八问、UZI 机构 vs 游资 / 席位判断，不是 overlay 句子或 `full_skill_workflow=True`。 A:I31 A:I27
- MH3: 跑满之后，官方 Top3 必须是公司业绩 / 5 日获利率最强的名字，不是研究报告写得最全的名字。名次仍由唯一 `selection_score` 产出。 A:I16 A:I31 A:I33
- MH4: 东财应采字段没挂上时，修现有 Scanner 并挡住当日官方出票；不另起数据源。 A:I32
- MH5: 当天没上龙虎榜仍须跑满 UZI；有资金流或公告就不能 `not_run`。 A:I32 A:I31
- MH6: 回填仍只读官方扫描 OHLC；已锁定 09-04 到 09-10 官方票不重盖。 A:I31
- MH7: 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。 A:I16 A:I32

### Task 1: Block official tickets when F10 financials capture is empty A:I32
- [ ] 在现有 `scrapy_scanner/runner_v2.py`：宽候选非空时，`financials` 空列表必须走现有 `CriticalSourceError` / `CRITICAL_SOURCE_EMPTY:financials`，生产扫描 `SCAN_BLOCKED`
- [ ] 不把 `financials` 继续当 OPTIONAL 空通过。不新增 fetcher 模块
- Verification: 单测宽候选非空、`fetch_financials_for_buffett` 返回 `[]` 时扫描 blocked。MH4 MH7

### Task 2: Block empty preview/report dumps; keep empty LHB as a market fact A:I32
- [ ] 同一 `runner_v2.py`：宽候选非空时，`earnings_preview.jsonl` 与 `stock_reports.jsonl` 整份全空 = 扫描挂载失败，阻断官方出票。单票没有预告或没有研报仍允许
- [ ] `lhb` 整日空榜记 `EMPTY_BOARD`，不阻断扫描
- [ ] `stock_capital_flow` 对宽候选为空则阻断
- Verification: `fetch_lhb_for_uzi` 返回 `[]` 且资金流非空时扫描不因空榜 blocked；预告+研报双空时 blocked。MH4 MH5 MH7

### Task 3: Serenity prescribed output is a bottleneck table A:I31 A:I27
- [ ] 只改 `xiaogu_research_context.build_serenity_context`：有 `industry_flow` 或 `industry_reports` 必须产出 `bottleneck_table`（层级 / 位置 / 是否卡点，至少 3 行）+ `scarce_layer` + `why_5d` + `falsify` + PIT evidence
- [ ] 不能只写“资金流入可见”。`full_skill_workflow=True` 仅当卡点表齐全
- Verification: 现有 `tests/test_research_skill_ingest.py` 夹具上，有行业资金必有卡点表。MH2

### Task 4: Buffett must run eight questions from captured F10 A:I31 A:I32
- [ ] 只改 `build_buffett_context`：有 `financials` 或 `earnings_preview` 或 `stock_reports` 就必须填 8 问，不能再走“没有财报预告…停止假装深度分析”
- [ ] `buy_sell is None`；`recommended_buy_price is None`
- Verification: 有 F10 财务时 Buffett 不是 `not_run`，checklist 长度 8。MH2 MH5

### Task 5: UZI must judge without a T-day LHB board A:I32 A:I31
- [ ] 只改 `build_uzi_context`：空 `lhb` 但有 `stock_capital_flow` 或 `announcements` 时 `skill_ran=True`，`mode` 不得为 `not_run`
- [ ] 产出 `institution_vs_hot_money`（`institution` / `hot_money` / `unknown_no_board`）+ `why_5d` + `falsify`
- Verification: 无龙虎榜但有资金流时 UZI 不是 `not_run`。MH5

### Task 6: Incomplete research cannot become an official ticket A:I31
- [ ] 改现有 `xiaogu_core_alpha._signal_qualification`：三技能规定产出不齐时 `signal_qualified=False`，reason=`RESEARCH_SKILL_INCOMPLETE`
- [ ] `attach_top_paper_observations` 保持现有 owner。未合格决策 `paper_observation` 必须是 `None`
- Verification: PR1 突变测试：拿掉任一份规定产出后，该股不得成为官方票。分别拿掉卡点表、拿掉八问、把 UZI 标 `not_run`，三次都不得进入 `attach_top_paper_observations` 的 Top3。MH1

### Task 7: Rank complete names by earnings / 5-day profit, not coverage A:I33 A:I16
- [ ] 改现有 `_selection_score`：官方名次不得再用 `0.60 * coverage + 0.40 * thesis_ready`
- [ ] VALIDATED 时仍用 `opportunity_5d` 概率；未 VALIDATED 时用已落库东财 F10 / 业绩预告 / 财务质量作为唯一 Alpha 的排序证据。不新建业绩分函数，不另起 ranker
- [ ] 两只都跑满时，财务更强的必须是 Top1。只改研究报告文案不得改名次
- [ ] Production BUY 仍 BLOCKED，Paper 仍 `OBSERVED + PAPER_FLAT`
- Verification: `_selection_score` 源码不再把 coverage 当官方排序键；两只都跑满、一只 ROE/财务明显更强时强的是 Top1。MH3 MH7

### Task 8: Persist prescribed outputs; do not restamp locked tickets or restore kline A:I31
- [ ] 改现有 `xiaogu_forward_paper_recorder_v0_1.py` memory 模板：票面写入卡点表 / 八问 / 机构 vs 游资，替换 overlay 冒充跑满
- [ ] `research_overlay` 带 `skill_complete` 和三份规定产出引用。现有 write gate 继续只收 `DECISIONS_PERSISTED` Top3
- [ ] 不调用 `--replace-official`，不重跑 09-04/07/08/09/10
- [ ] `xiaogu_forward_result_filler_v0_1.py` 仍无 `kline/get` / `push2his.eastmoney.com` / `EASTMONEY_KLINE_ENDPOINT`
- Verification: `tests/test_return_calculation.py::test_future_bars_are_eastmoney_only` 绿；Recorder 夹具含三份规定产出且无 BUY/LIVE；历史 15 张官方票身份不变。MH6

### Task 9: Prove one pipeline A:I16 A:I32
- [ ] 不新增 `research_v2.py` / `skill_ranker.py` / 第二 scanner
- [ ] Owner 仍是：扫描=`scrapy_scanner.runner_v2`；研究=`xiaogu_research_context.build_integrated_research_context`；Alpha=`xiaogu_core_alpha.build_core_alpha`；出票=`xiaogu_portfolio_decision.attach_top_paper_observations`；回填=`xiaogu_forward_result_filler_v0_1.fetch_eastmoney_snapshot_daily_bars`
- [ ] 生产模块不 import 65 评委 / `investor_evaluator` / panel
- [ ] AgentMemory 收口：前置 = 三技能规定产出；跑满不是入场券；空 jsonl = 扫描失败；空龙虎榜 = 市场事实
- Verification: `pytest tests/test_research_skill_ingest.py tests/test_return_calculation.py tests/test_single_system_convergence.py tests/test_data_contract.py -q` 通过；`git diff --stat` 只落在现有 owner 文件和现有测试。MH7
