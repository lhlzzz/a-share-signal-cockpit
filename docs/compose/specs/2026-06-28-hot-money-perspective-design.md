# Hot Money Perspective Scoring Design

## [S1] Problem

xiaogu 当前从散户视角选股——评估风险、安全性、基本面。导致选出的票"安全但不涨"。主力/游资拉升的票才有涨停潜力，需要从主力视角评估"这只票值不值得用资金去拉升"。

## [S2] Solution Overview

在现有 `integrated_score` 旁新增 `hot_money_score()`，作为独立评分维度。两个分数加权合成最终决策。

**核心转变：**

| 散户视角 | 主力视角 |
|----------|----------|
| 风险高不高 | 拉升空间有多大 |
| 收盘位置好不好 | 筹码是否集中 |
| 有没有资金流入 | 资金在吸筹还是出货 |
| 板块有没有催化 | 板块有没有持续性 |
| PE合不合理 | 流通盘大小、控盘难度 |
| 技术面是否健康 | 拉升后有没有抛压 |

## [S3] Hot Money Score 维度

### 3.1 吸筹信号 (accumulation_signal) — 权重 25%

主力吸筹特征：
- **低位缩量上涨**：price < 20 且 volume_ratio < 1.5 且 pct_chg > 0
- **尾盘拉升**：close_position_score > 0.7 且 signal_pct > 3%
- **连续小阳线**：close_position_score > 0.6 且 amplitude < 5%
- **量价背离**：价格下跌但资金净流入（主力低位承接）

数据源：candidate_quote_recheck, candidate_fund_recheck, net_inflow_main, close_position_score

### 3.2 板块热度 (sector_heat) — 权重 25%

主力喜欢在热门板块里找标的：
- **板块资金流入**：sector_fund_flow 正值且排名靠前
- **概念板块领涨**：concept_capital_flow 中该板块排前10
- **板块连板效应**：同板块有涨停股（接力预期）
- **板块轮动早期**：板块刚启动，不是已经涨了一周的

数据源：sector_fund_flow, concept_capital_flow, industry_board, sector_opportunity_score

### 3.3 控盘难度 (control_difficulty) — 权重 20%

主力偏好容易控盘的票：
- **小盘股**：float_market_cap < 100亿
- **低价股**：price < 20（拉升成本低）
- **换手率适中**：turnover_rate 5%-15%（不是无人问津也不是过度换手）
- **筹码集中**：量比 1.0-2.5（温和放量，非异常放量）
- **非ST/非退市**：排除风险股

数据源：market_cap, float_market_cap, price, turnover_rate, volume_ratio

### 3.4 拉升空间 (upside_potential) — 权重 20%

主力需要足够的拉升空间才能获利：
- **距涨停空间**：(limit - signal_pct) / limit > 5%
- **前期没有套牢盘**：close_position_score > 0.5（不是高位接盘）
- **均线多头排列**：price > MA5 > MA10 > MA20（技术面支撑）
- **突破关键位**：signal_pct > 5% 且 close_position_score > 0.7

数据源：signal_pct, close_position_score, price, limit_th

### 3.5 出货条件 (exit_conditions) — 权重 10%

主力拉升后能否顺利出货：
- **市场情绪配合**：market_breadth > 30%（不是极端弱市）
- **涨停板效应**：当天有涨停板打开后回封（赚钱效应）
- **资金接力**：net_inflow_main > 0（有资金愿意接盘）
- **换手充分**：turnover_rate > 5%（流动性足够出货）

数据源：market_breadth, net_inflow_main, turnover_rate, market_limitups

## [S4] Scoring Formula

```
hot_money_score = (
    accumulation_signal * 0.25 +
    sector_heat * 0.25 +
    (1 - control_difficulty) * 0.20 +
    upside_potential * 0.20 +
    exit_conditions * 0.10
) * 100
```

每个子维度归一化到 0-1，最终分数 0-100。

**出票条件：**
- hot_money_score >= 60（主力愿意拉升的门槛）
- integrated_score >= 40（基础风控门槛，可降低）
- 两个分数加权：final = hot_money * 0.6 + integrated * 0.4

## [S5] Data Sources (28 domains)

已有数据直接可用：
- **吸筹信号**：candidate_quote_recheck, candidate_fund_recheck, net_inflow_main
- **板块热度**：sector_fund_flow, concept_capital_flow, industry_board
- **控盘难度**：market_cap, float_market_cap, price, turnover_rate
- **拉升空间**：signal_pct, close_position_score
- **出货条件**：market_breadth, turnover_rate

无需新增 CDP 采集，所有数据已在 28 域 evidence pack 中。

## [S6] Architecture

```
scanner → evidence_pack (28 domains)
                ↓
        hot_money_features(c)  ← 新增
                ↓
        hot_money_score(c)     ← 新增
                ↓
        integrated_score(c)    ← 现有
                ↓
        final_score = 0.6 * hot_money + 0.4 * integrated
                ↓
        paper_pick_eligibility  ← 现有（门槛降低）
                ↓
        出票决策
```

## [S7] Implementation Scope

Phase 1（本次）：
- 实现 `hot_money_features(c)` 提取主力视角特征
- 实现 `hot_money_score(c)` 计算主力评分
- 修改 `score_candidates()` 合成双评分
- 回测验证：对比纯 integrated_score vs 双评分

Phase 2（后续）：
- 积累主力评分数据，优化权重
- 加入历史 pattern 匹配（类似 setup 但主力视角）
- 加入 LHB/大宗交易数据的主力行为分析
