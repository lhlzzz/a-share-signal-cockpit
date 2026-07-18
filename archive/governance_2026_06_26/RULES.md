# RULES

- 永远 PAPER_ONLY / NO_TRADE，除非老板另行授权且完整生产 gate 通过。
- 历史回测、forward paper、真实交易必须分层，不允许把高回测胜率说成可交易。
- 外部工具使用以本 workspace 的 `TOOLING.md` 和 `/root/hermes/company-ai-system/tools/external/TOOLING.md` 为准；Backtrader 和 vectorbt 仅用于研究、回测框架、参数扫描和 PAPER_ONLY / NO_TRADE 策略验证。
- 使用 Backtrader 或 vectorbt 前必须从共享工具区运行 smoke/查官方源码，并把调研结果写入 `RESEARCH.md` 或 `SESSION.md` 明确小节。
- 任何出票、交易建议、真实交易、自动化真实账户或生产化动作必须先有 xiaochan gate。
- xiaogu 出票必须跑最新稳定总链路重新生成当次 scan/bundle；旧 scan、旧 candidate bundle 只能追溯复盘，不能正式 PAPER_PICK。
- 非 A 股资产研究链路已归属 `xiaomei`；`xiaogu` 当前 active runtime 只负责 A 股/东财/手动交易跟踪，禁止把非 A 股规则接入 A 股 stable runner。
- xiaogu A 股出票所需数据固定优先来自已登录东财网页集合源：行情中心、资金流向、自选股、公告大全、龙虎榜、风险警示/风险提示等；web search 只能作为东财不可用时的兜底核验，不能作为主链路。
- xiaogu A 股稳定总链路不再限制主板；所有 A 股板块均可进入候选，但一手成本必须 <=6000，且仍需通过既有监管/风险/数据 gate。
- xiaogu 出票必须把监管异动、近期异常波动、近期严重异常波动、交易所风险提示、近期异常交易重点监控或龙虎榜风险作为 hard block；命中后只能剔除顺延，不能作为 PAPER_PICK。
- XIAOGU_REPO_INTEGRATION_V3 固定仓库分层：CORE_REPOS=`VEI`,`Qlib`；RESEARCH_REPOS=`QuantDinger`,`tradingagent_a`；RETIRED_REPOS=`TradingAgents`,`ai-hedge-fund`,`ZBS`。
- V3 生产打分边界只允许 `Xiaogu Native Evidence + validated VEI + validated Qlib`；研究仓库输出默认 research-only / diagnosis-only / promotion-candidate，未经明确晋级审批不得影响 `candidate_score`、`ranking_score`、`production_pick` 或 PAPER_PICK reason authority。
- VEI 只允许作为 event/anomaly/pre-breakout/underwater/regime 特征源接入 evidence 之后、candidate gate 之前的验证层；Qlib 只允许作为 feature store/backtest/attribution 层；二者都不得直接写 ledger、调用 recorder、绕过 runner hard gate 或连接 broker/order endpoint。
- 退役仓库默认不读、不执行、不加载，只保留 source-only 审计价值；重新启用必须按新仓库准入重新证明 unique capability、documented gap、backtest evidence、forward evidence、attribution evidence 和 approval。
