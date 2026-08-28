# Xiaogu 3.0 Repricing Production Rebuild

## Source Ask
> Xiaogu 不是寻找明天上涨股票，而是识别正在形成价格重新定价条件的股票。直接重构 Scanner、Feature Engine、Research Context、Core Alpha、Portfolio Decision、Replay、Ledger 和数据库，保留唯一决策 Owner `xiaogu_portfolio_decision.evaluate_candidate_bundle()`。

## Normalized Goal
把现有 T+1/候选筛选生产链替换为一条可解释、无未来泄漏的价格形成链：Eastmoney 只采集并规范化市场事实；Feature Engine 测量 Business、Future Demand、Capital、Supply、Pricing Gap、Reflexivity、Market、Risk、Execution；Serenity、Buffett、UZI 和 TradingAgents 只提供研究上下文；Core Alpha 输出中期重定价预期；唯一 Portfolio Decision Owner 输出六种状态。

## Non-Negotiables
- NN1: Scanner 只产生 raw acquisition、canonical snapshot、lineage、source validation 和 completeness，不候选筛选、排名、打分或推荐。
- NN2: `evaluate_candidate_bundle()` 是唯一生产决策 Owner，只输出 WATCH、READY、BUY、HOLD、REDUCE、SELL。
- NN3: Production Alpha 只使用五日窗口证据；短周期结果只能是 entry timing、risk 或 outcome compatibility。
- NN4: BUY 必须同时通过 Business、Future Demand、Capital Accumulation、Supply Absorption、Pricing Gap、Market、Risk 和 Repricing Completion 检查，并能解释买家、卖家、吸收、未来买家和失效条件。
- NN5: Serenity=Future Demand，Buffett=Business Quality，UZI=Capital，TradingAgents=Contradiction；任何 Skill 都不能直接 BUY。
- NN6: 保留 PostgreSQL、canonical snapshot、production run、paper ledger、execution contract、return evaluation 和 scheduler 单一边界；不连接真实交易。
- NN7: 历史回放与 live 使用相同 Feature、Research、Alpha、Decision Owner，只计算五日窗口，禁止未来字段进入决策。
- NN8: 删除前必须完成调用链证明；不创建第二套数据库、第二个决策 Owner 或平行实现。

## Hidden Contract Candidates
- HC1: 每个决策带 snapshot lineage、as-of、model/version、context provenance、thesis、blockers 和 completion/risk 状态。
- HC2: WATCH 和 READY 是正常生产结果，系统不强制每日 BUY。
- HC3: `persistent capital + supply absorption + pricing gap` 不能被单一大额净流入替代。
- HC4: 价格已扩张、估值/注意力/机构仓位拥挤时，REPRICING_COMPLETION 必须阻断 BUY。
- HC5: 数据不足或研究适配器不可用时 fail closed，返回上下文缺口或 WATCH，而不是猜测。

## Chosen Interpretation
执行一次垂直生产重构，优先修改现有 Owner 和入口，保留兼容字段只用于读取历史数据和结果结算；生产计算改用新重定价契约。未训练、未校准的概率/收益输出标记为 RESEARCH/UNVERIFIED，不伪造为可交易模型。

## Rejected / Forbidden Narrowings
- FN1: 不把旧 T+1 分数改名为 Core Alpha。
- FN2: 不只增加字段或文档而保留旧候选筛选进入生产。
- FN3: 不把 `net_inflow > 0`、强势上涨、涨停或热门板块直接当作 BUY。
- FN4: 不保留第二个候选选择器、研究选择器或主线选择器。
- FN5: 不为删除旧模块而绕过调用者、测试、数据库和回放验证。

## In Scope
- Scanner raw/canonical 边界与候选思维清理。
- 九类价格形成测量、研究上下文、资金价格影响、供给吸收、未来买家、反身性和重定价完成度。
- Core Alpha 的五个持有期输出、风险/置信度/状态/解释契约。
- 唯一六状态 Portfolio Decision、纸面执行、Ledger、DB 持久化和退出逻辑。
- 同链历史回放、无未来泄漏、T+1..T+5 结果评估和生产运行验证。
- 删除经证明专属的候选选择器、重复评分模块、社会情绪模块和无关测试/配置。

## Out of Scope
- 真实交易、券商连接、凭空训练或伪造历史收益。
- 第二套数据库、UI 重写、未经回放和基准证据的策略权重升级。

## Success Signals
- Scanner 源码和运行产物不包含候选排序/策略分/BUY 推荐 Owner。
- 新九类 Feature、`FutureBuyerMap`、`SupplyAbsorption`、`PricingGap`、`CapitalPriceImpact`、`RepricingState` 可从同一 canonical snapshot 生成。
- Core Alpha 明确输出五日窗口，并在未校准时 fail closed。
- `evaluate_candidate_bundle()` 单独产生六状态；BUY 具备完整价格发动机解释，缺证据时不能 BUY。
- Replay、live snapshot、paper ledger 和结果 filler 可运行，且未来字段注入不改变决策。
- 定向测试、`pytest tests/ -x -q`、compile、`git diff --check` 和至少一个无网络 fixture 回放通过。

## Proof Requirements
- codebase-memory 的 owner/caller trace 与源码确认唯一生产链。
- Scanner 禁止项、九类 Feature、资本/供给/价格差、六状态、completion block、研究只读边界和未来泄漏测试。
- 同一 fixture 通过 live/replay 得到相同决策输入与状态；五个 horizon 输出明确缺失值和日期边界。
- 数据库初始化和写入契约可重复执行，不直接改生产库。
- 收口更新 AgentMemory；只有产生可复用项目知识时更新既有 Obsidian 笔记。

## Draft Handoff
- Phase 1: 真实审计当前 dirty tree、调用链、删除候选和数据库边界。
- Phase 2: 原地替换 snapshot/features/research/alpha/decision 契约。
- Phase 3: 清理 Scanner 生产路径并迁移 runner、ledger、DB、replay、evaluation。
- Phase 4: 删除已证明专属的旧实现并补全测试。
- Phase 5: 全量验证、刷新索引、记录架构决策和失败教训。
