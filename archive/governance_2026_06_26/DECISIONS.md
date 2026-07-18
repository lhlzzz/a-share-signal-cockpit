# DECISIONS

Long-lived workspace decisions.

- 2026-05-21：复盘 2026-05-20 单票时，归档 live scan/forward ledger 优先于当前代码重算；若两者分数不同，先保留差异并以是否过 gate 作为结论基础。
- 2026-05-21：出票规则升级为 historical_backtest_rule_v0_2；连续未获利后不直接降低主板硬门槛，而是触发 fresh source + cooldown， blocked 候选只能作为 RESEARCH_CANDIDATE。
- 2026-05-23：forward live scan 主源改为 Eastmoney DevTools 公开延迟行情页；Sina/Tencent 指数快照只保留为 fallback，不再作为主 candidate gate。
- 2026-05-26：上述 2026-05-23 数据源决策升级为“已登录东财网页集合源”主链路：行情中心、资金流向、自选股、公告大全、龙虎榜、风险警示/风险提示等均优先从东财结构化采集；Sina/Tencent/web search 只可兜底核验，不进入主出票链路。
- 2026-05-23：candidate basket 必须顺序遍历，返回第一个满足硬门槛的 `PAPER_PICK`，不能只看 top-score 第一名。
- 2026-05-23：`xiaogu_forward_result_filler_v0_1.py` 回填时优先读取本地 web 证据和本地 kline 缓存，网络抓取只作兜底。
- 2026-05-25：市场面判断新增 `climax` 过热 regime；高 breadth + 极端涨停/大涨数不能继续按纯强市加分，必须叠加个股强收盘与资金/冲板确认。
- 2026-05-25：最终排序新增中价位/拥挤度再平衡；降低 8 元以下低价拥挤票和成交额最前排票的排序优势，以修复 top2 胜率高于 top1 的排序偏差。
- 2026-05-25：用户明确总资金为 7000，出票硬门槛应以一手成本和板块权限为准；取消单纯 `PRICE_GT_30`，主板/中小板一手成本 <=7000 可作为 PAPER_ONLY 观察票，创业板/科创/北交等权限板块继续拦截。此规则已被 2026-06-03 新资金/板块规则取代。
- 2026-06-03：A 股稳定总链路买入范围不再限制主板/中小板；创业板、科创、北交等所有 A 股板块均可进入候选，出票硬门槛只按总资金 6000 的一手成本上限与既有监管/风险/数据 gate 判断。
- 2026-05-25：监管异动、异常波动、严重异常波动、交易所风险提示或近期异常交易重点监控名单必须作为出票 hard block；模型分数再高也不能作为 PAPER_PICK，需剔除后顺延。
- 2026-05-27：非 A 股研究能力归属 `xiaomei`；`xiaogu` 决策只保留 A 股/东财/实盘跟踪，不再承载跨市场研究决策。
- 2026-06-03：`forward_paper_ledger_v0_1.jsonl.bak_20260525_ledger_split_repair` 是 A股 system-of-record ledger 的 rollback proof，不是普通 archive candidate；任何自动化 lifecycle/cleanup 命中该文件时必须暂停并等待用户明确审批，不得自动移动、删除、归档、压缩或作为 scoreboard 输入。
