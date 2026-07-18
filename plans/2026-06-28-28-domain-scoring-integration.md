# 28域东财证据包接入出票打分链路 — 改造方案

## 背景

**目标**: 将东财行情中心的完整28域数据全部接入xiaogu出票打分链路，使每个域的信号都能影响最终评分。

**现状**: `integrated_score()` 仅读取 candidate row 中的预计算字段（signal_pct, price, close_position_score 等），不读取28域证据包。Scanner 的 `evidence_flags()` 已提取17类标志但未被 runner 消费。

## 架构设计

### 改造原则

1. **不破坏现有打分逻辑** — 新增维度以 additive bonus/penalty 形式叠加
2. **渐进式集成** — 按域的信号强度和可靠性分批接入
3. **可回滚** — 每个新维度有独立开关，可通过 scoring_config 关闭
4. **向后兼容** — 不改变 candidate row 现有字段

### 数据流改造

```
当前:
  Scanner → candidate row (预计算字段) → integrated_score()

改造后:
  Scanner → candidate row + evidence_flags → asof_features() 提取新维度 → integrated_score() 使用全部维度
```

## 域分类与接入策略

### 第一批: 高价值信号（7域）— 直接影响分数

| 域 | 信号类型 | 接入方式 | 权重建议 |
|---|---------|---------|---------|
| limitup_strength | 涨停强度 | 封单额/封板比 → bonus | +2~5分 |
| broken_limit_risk | 炸板风险 | 有炸板记录 → penalty | -3~8分 |
| consecutive_limit_strength | 连板强度 | 连板数 → bonus | +1~3分 |
| sector_fund_flow | 板块资金流 | 板块净流入方向 → bonus/penalty | ±2~4分 |
| concept_capital_flow | 概念资金流 | 概念板块资金 → bonus/penalty | ±1~3分 |
| popularity_heat | 人气热度 | 人气排名 → 适度bonus | +0~2分 |
| industry_board | 行业板块 | 行业涨幅排名 → bonus | +0~2分 |

### 第二批: 风险信号（5域）— 主要用于 penalty

| 域 | 信号类型 | 接入方式 | 权重建议 |
|---|---------|---------|---------|
| margin_trading | 融资融券 | 融资余额变化 → 融券风险penalty | -1~3分 |
| block_trades | 大宗交易 | 折价交易 → penalty | -1~4分 |
| lockup_expiry | 限售解禁 | 近期解禁 → penalty | -2~5分 |
| shareholder_changes | 股东变化 | 减持 → penalty, 增持 → bonus | ±1~3分 |
| trading_halts | 停复牌 | 停牌中 → hard block | -999分 |

### 第三批: 催化剂信号（4域）— 用于 bonus

| 域 | 信号类型 | 接入方式 | 权重建议 |
|---|---------|---------|---------|
| research_reports | 研报评级 | 买入/增持评级 → bonus | +0~2分 |
| earnings_preview | 业绩预告 | 预增 → bonus, 预减/预亏 → penalty | ±1~3分 |
| ipo_calendar | 新股日历 | 市场情绪参考 → 调节系数 | ±0~1分 |
| yesterday_limit_strength | 昨日涨停 | 连板延续性 → bonus | +0~2分 |

### 第四批: 候选人复核（5域）— 用于 gate/penalty

| 域 | 信号类型 | 接入方式 | 权重建议 |
|---|---------|---------|---------|
| candidate_quote_recheck | 盘口复核 | 买盘压力 → bonus | +0~2分 |
| candidate_fund_recheck | 资金复核 | 主力净流入 → bonus | +0~2分 |
| candidate_lhb_recheck | 龙虎榜复核 | 机构买入 → bonus | +0~2分 |
| candidate_announcement_recheck | 公告复核 | 新催化 → bonus | +0~2分 |
| candidate_intraday_replay | 盘中回放 | 盘中异动 → bonus | +0~1分 |

### 第五批: 已间接接入（2域）

| 域 | 状态 | 说明 |
|---|------|------|
| data_directory_content | 已间接接入 | 通过 research_signals → catalyst_quality 影响 research_panel |
| historical_risk_notes | 已接入 | 作为 historical_risk_notes 桶参与 risk review |

## 实现步骤

### Step 1: 扩展 asof_features() — 提取证据标志

**文件**: `xiaogu_v2_1_six_repo_real_integrated.py`

在 `asof_features()` 函数中新增证据标志提取：

```python
def asof_features(c):
    # 现有字段保持不变...
    features = {
        'signal_pct': fl(c.get('signal_pct'), 0) or 0,
        # ... existing fields ...
    }
    
    # === 新增: 28域证据标志提取 ===
    # 从 scored record 中读取 evidence_flags
    evidence_flags = c if isinstance(c, dict) else {}
    
    # 第一批: 高价值信号
    features['limitup_strength_count'] = len(evidence_flags.get('limitup_strength_tags', []))
    features['broken_limit_risk_count'] = len(evidence_flags.get('broken_limit_risk_flags', []))
    features['sector_fund_flow_count'] = len(evidence_flags.get('board_strength_tags', []))
    features['concept_capital_flow_present'] = 1 if evidence_flags.get('concept_capital_flow') else 0
    features['popularity_rank'] = fl(evidence_flags.get('popularity_rank'), 0) or 0
    
    # 第二批: 风险信号
    features['margin_risk_count'] = len(evidence_flags.get('margin_risk_flags', []))
    features['block_trade_count'] = len(evidence_flags.get('block_trade_flags', []))
    features['lockup_risk_count'] = len(evidence_flags.get('lockup_risk_flags', []))
    features['shareholder_change_count'] = len(evidence_flags.get('shareholder_change_flags', []))
    features['trading_halt_count'] = len(evidence_flags.get('trading_halt_flags', []))
    
    # 第三批: 催化剂信号
    features['research_rating_count'] = len(evidence_flags.get('research_rating_tags', []))
    features['earnings_preview_count'] = len(evidence_flags.get('earnings_preview_flags', []))
    features['ipo_calendar_count'] = len(evidence_flags.get('ipo_calendar_tags', []))
    
    # 第四批: 候选人复核
    features['quote_recheck_count'] = len(evidence_flags.get('candidate_quote_recheck_tags', []))
    features['fund_recheck_count'] = len(evidence_flags.get('candidate_fund_recheck_tags', []))
    features['lhb_recheck_count'] = len(evidence_flags.get('candidate_lhb_recheck_tags', []))
    
    return features
```

### Step 2: 在 integrated_score() 中使用新维度

**文件**: `xiaogu_v2_1_six_repo_real_integrated.py`

在 `integrated_score()` 的 `opp` 计算中加入新维度：

```python
def integrated_score(c):
    # ... existing code ...
    f = asof_features(c)
    
    # ... existing opp calculation ...
    
    # === 新增: 28域证据维度加分/减分 ===
    
    # 第一批: 高价值信号 bonus
    if f['limitup_strength_count'] > 0:
        opp += min(5.0, f['limitup_strength_count'] * 1.5)
    if f['sector_fund_flow_count'] > 0:
        opp += min(4.0, f['sector_fund_flow_count'] * 1.0)
    if f['concept_capital_flow_present']:
        opp += 2.0
    if 0 < f['popularity_rank'] <= 50:
        opp += min(2.0, (50 - f['popularity_rank']) / 25)
    
    # 第一批: 高价值风险 penalty
    if f['broken_limit_risk_count'] > 0:
        opp -= min(8.0, f['broken_limit_risk_count'] * 3.0)
    
    # 第二批: 风险信号 penalty
    if f['margin_risk_count'] > 0:
        opp -= min(3.0, f['margin_risk_count'] * 1.5)
    if f['block_trade_count'] > 0:
        opp -= min(4.0, f['block_trade_count'] * 2.0)
    if f['lockup_risk_count'] > 0:
        opp -= min(5.0, f['lockup_risk_count'] * 2.5)
    if f['shareholder_change_count'] > 0:
        opp -= min(3.0, f['shareholder_change_count'] * 1.5)
    if f['trading_halt_count'] > 0:
        return None, ['trading_halt_block'], market_regime
    
    # 第三批: 催化剂 bonus
    if f['research_rating_count'] > 0:
        opp += min(2.0, f['research_rating_count'] * 0.8)
    if f['earnings_preview_count'] > 0:
        opp += min(3.0, f['earnings_preview_count'] * 1.0)
    
    # 第四批: 候选人复核 bonus
    if f['quote_recheck_count'] > 0:
        opp += min(2.0, f['quote_recheck_count'] * 0.8)
    if f['fund_recheck_count'] > 0:
        opp += min(2.0, f['fund_recheck_count'] * 0.8)
    
    # ... rest of existing code (opp_threshold check, ranking_adjustment, final_score) ...
```

### Step 3: 支持 flag 文本方向判断

**文件**: `xiaogu_v2_1_six_repo_real_integrated.py`

部分 flag 需要区分正向/负向（如 shareholder_changes: 增持 vs 减持）：

```python
def flag_direction_score(flags, positive_keywords, negative_keywords, bonus_per=1.0, penalty_per=1.5, max_bonus=3.0, max_penalty=4.0):
    """从文本 flags 中提取方向性分数"""
    if not flags:
        return 0.0
    text = ' '.join(str(f) for f in flags)
    pos_count = sum(1 for kw in positive_keywords if kw in text)
    neg_count = sum(1 for kw in negative_keywords if kw in text)
    if pos_count > neg_count:
        return min(max_bonus, pos_count * bonus_per)
    elif neg_count > pos_count:
        return -min(max_penalty, neg_count * penalty_per)
    return 0.0

# 在 integrated_score() 中使用:
shareholder_score = flag_direction_score(
    evidence_flags.get('shareholder_change_flags', []),
    positive_keywords=['增持', '回购', '加仓'],
    negative_keywords=['减持', '减持计划', '股份减持'],
    bonus_per=1.5, penalty_per=2.0
)
opp += shareholder_score

earnings_score = flag_direction_score(
    evidence_flags.get('earnings_preview_flags', []),
    positive_keywords=['预增', '扭亏', '大幅增长'],
    negative_keywords=['预减', '预亏', '大幅下降'],
    bonus_per=1.5, penalty_per=2.0
)
opp += earnings_score
```

### Step 4: 添加 scoring_config 开关

**文件**: `xiaogu_db.py` + `xiaogu_forward_d1_1450_runner_v0_1.py`

每个新维度有独立开关，可通过 scoring_config 表关闭：

```sql
INSERT INTO scoring_config (config_key, config_value) VALUES
('domain_limitup_strength_weight', '1.5'),
('domain_broken_limit_risk_weight', '3.0'),
('domain_sector_fund_flow_weight', '1.0'),
('domain_concept_capital_flow_weight', '2.0'),
('domain_margin_risk_weight', '1.5'),
('domain_block_trade_weight', '2.0'),
('domain_lockup_risk_weight', '2.5'),
('domain_shareholder_change_weight', '1.5'),
('domain_research_rating_weight', '0.8'),
('domain_earnings_preview_weight', '1.0'),
('domain_candidate_recheck_weight', '0.8'),
('domain_enabled', 'true');
```

### Step 5: 更新 runner 的 gate 检查

**文件**: `xiaogu_forward_d1_1450_runner_v0_1.py`

将 `concept_capital_flow` 加入 `REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS`：

```python
REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS = (
    'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength', 'yesterday_limit_strength',
    'popularity_heat', 'industry_board', 'sector_fund_flow', 'concept_capital_flow',  # 新增
    'candidate_quote_recheck', 'candidate_fund_recheck', 'candidate_lhb_recheck',
    'candidate_announcement_recheck', 'candidate_intraday_replay',
)
```

### Step 6: 回测验证

**文件**: `xiaogu_v2_1_six_repo_real_integrated.py`

在 `main()` 中分别回测：
1. 当前 baseline（5域打分）
2. 第一批接入后（+7域）
3. 全量接入后（+23域）

对比出票率、T+1胜率、平均收益、最大回撤。

## 权重调优策略

### 初始权重来源

1. **历史数据分析**: 从 `data/live_scan/` 的 evidence pack 中统计各域的 flags 命中率和对应 T+1 收益
2. **用户经验判断**: 基于用户对各信号的理解设定初始值
3. **回测网格搜索**: 对每个权重在 [-5, +5] 范围内网格搜索最优值

### 约束条件

- 总 bonus 不超过 +15 分（避免单一域主导）
- 总 penalty 不超过 -20 分（避免多重惩罚过度）
- 每个域独立开关，可通过 scoring_config 实时调整
- 新增维度的总影响不超过原 opp 的 30%

## 测试计划

1. **单元测试**: 每个 flag_direction_score 函数的边界条件
2. **集成测试**: 从 evidence pack 到 final_score 的完整链路
3. **回归测试**: 对比改造前后 30 天回测结果
4. **门控测试**: 确保 scoring_config 开关正常工作

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 新维度过度影响打分 | 权重上限 + 独立开关 |
| Flag 文本解析错误 | 关键词白名单 + 回退到默认值 |
| 回测过拟合 | 多时间段验证 + 用户经验校验 |
| 现有胜率下降 | 渐进式接入，每批独立回测 |
