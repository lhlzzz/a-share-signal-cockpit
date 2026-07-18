# xiaogu 数据管道重构设计

## [S1] 问题

xiaogu 当前数据管道存在三个核心问题导致无法出票：

1. **候选证据全 MISSING** — `collect_candidate_detail_evidence` 中的 API 调用返回空数据，导致 `candidate_evidence_status = MISSING`，runner 门控直接 block
2. **增强域覆盖 PARTIAL** — `candidate_fund_recheck`, `candidate_lhb_recheck`, `candidate_announcement_recheck`, `candidate_intraday_replay` 四个增强域缺失
3. **数据组织混乱** — 28 个域散落在 scanner 中，没有按层级（行情层/信号层/证据层）组织

## [S2] 目标

构建完整闭环的 T+1 高收益预测系统：
- 每天都能出票（NO_PICK 也要有明确原因）
- 三层数据源全部跑通
- 可迭代：未涨停时自动分析原因

## [S3] 三层数据架构设计

### 第一层：行情数据层（Market Data Layer）
**文件：`xiaogu_market_data.py`**

所有实时行情相关的域归入此文件：
- 5 个基础域：announcements, risk_alerts, lhb, concept_industry, financials
- 7 个行情增强域：limitup_strength, broken_limit_risk, consecutive_limit_strength, yesterday_limit_strength, popularity_heat, industry_board, sector_fund_flow
- 4 个候选增强域：candidate_quote_recheck, candidate_fund_recheck, candidate_lhb_recheck, candidate_announcement_recheck

**职责：** 从 CDP/HTTP API 采集原始数据，输出标准化 evidence rows

### 第二层：信号层（Signal Layer）
**文件：`xiaogu_signal_layer.py`**

所有信号计算和评分归入此文件：
- concept_capital_flow（板块资金流）
- sector_fund_flow（行业资金流）
- research_signals（研究信号）
- social_sentiment（社交情绪）
- 28 域评分逻辑

**职责：** 基于行情层数据计算信号，输出评分和标签

### 第三层：决策层（Decision Layer）
**文件：`xiaogu_decision_layer.py`**

所有门控和出票逻辑归入此文件：
- paper_pick_eligibility_profile
- sector_gate
- limitup_capture_confirmation
- underwater_reversal_confirmation

**职责：** 综合信号层输出，做出出票/不出票决策

## [S4] 候选证据修复方案

### 问题根因
`rows_from_announcement_api` 等函数可能因为：
1. API 端点变更或限流
2. 候选代码格式不匹配
3. 数据源返回空结果但未触发 fallback

### 修复步骤
1. 在 `collect_candidate_detail_evidence` 中增加 API 响应日志
2. 为每个候选的 API 调用添加 timeout 和 retry
3. 当 API 返回空时，尝试从 CDP 页面 DOM 直接解析
4. 增加 `candidate_evidence_status` 的 PARTIAL 降级逻辑

## [S5] CDP 页面复用优化

当前问题：候选详情页导航超时导致证据采集失败

### 方案
1. 复用 tab 不再导航到新页面，而是通过 `fetch` API 在后台获取数据
2. 对于必须导航的页面（如分时图），设置更长的 timeout
3. 增加 tab 健康检查，失败时自动重连

## [S6] 可迭代闭环

### 未涨停分析
当 PAPER_PICK 未涨停时：
1. 从 returns 表获取 T+1 实际数据
2. 对比信号层预测 vs 实际表现
3. 生成归因报告（哪些信号失效）
4. 自动调整 scoring_config 权重

### 数据流
```
Scanner → Runner → Recorder → Filler → Scoreboard → 归因分析 → 调整配置 → 下一轮
```

## [S7] 实施优先级

1. **P0: 修复候选证据采集** — 让 API 调用返回有效数据
2. **P1: 组织三层架构** — 从 scanner 中拆分出 market_data / signal_layer / decision_layer
3. **P2: CDP 超时修复** — 页面复用和 timeout 优化
4. **P3: 闭环迭代** — 归因分析和自动调参
