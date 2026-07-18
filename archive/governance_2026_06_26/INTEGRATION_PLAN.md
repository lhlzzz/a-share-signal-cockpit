# xiaogu 仓库/Skill 真实集成计划

## 目标

将 TradingAgents、qlib、QuantDinger 的真实能力集成进 xiaogu，同时探查外部投资 Skills，提升获利率。

## 集成原则

- **不接 LLM API** — TradingAgents 的多 Agent 分析不集成
- **不用微软数据源/模型** — qlib 只用特征工程/回测框架
- **paper_only / no_trade** — 所有集成保持只读/纸面交易
- **V3 治理规则** — 研究仓库输出默认 research-only / diagnosis-only / promotion-candidate

---

## 一、TradingAgents 真实集成

### 当前状态

只用了 `a_share_common.py` 的 3 个工具函数（代码归一化、交易日历、小资金可买判断），`score_delta=0.0`。

### 网络限制

akshare 无法直接访问 eastmoney API（网络受限）。但 xiaogu 已通过 CloakBrowser CDP 9333 获取了丰富的东财数据。

### 集成策略

**使用 xiaogu 已有的东财数据**，实现 TradingAgents 的分析逻辑：

| 分析能力 | 数据来源 | 实现方式 |
|---------|---------|---------|
| 技术指标（RSI/MACD/Boll/ATR） | `eastmoney_web_tabs_raw.jsonl` 的 OHLCV | 本地计算 |
| 动量因子 | `signal_pct` / `price` 历史 | 本地计算 |
| 波动率因子 | `signal_pct` / `volume_ratio` | 本地计算 |
| 量价背离 | `signal_amount` vs `signal_pct` | 本地计算 |
| 新闻催化 | `structured_news.jsonl` | 已有 |
| 板块轮动 | `sector_propagation_edges.jsonl` | 已有 |
| 资金流向 | `fund_flow_ts.jsonl` | 已有 |

### 实现方案

在 `xiaogu_native_repo_runtime_v0_1.py` 中扩展 `tradingagent_a_native_adapter`：

```python
# 新增：技术面因子（使用 xiaogu 已有数据）
def compute_technical_features(candidate: dict) -> dict:
    """使用 xiaogu 东财数据计算技术指标"""
    features = {}
    
    # RSI 近似（基于 signal_pct 和 volume_ratio）
    signal_pct = candidate.get('signal_pct', 0)
    volume_ratio = candidate.get('volume_ratio', 1)
    features['rsi_proxy'] = min(100, max(0, 50 + signal_pct * 5 - (volume_ratio - 1) * 20))
    
    # 动量因子
    features['momentum'] = signal_pct
    
    # 波动率因子
    features['volatility'] = abs(signal_pct) * volume_ratio
    
    # 量价背离检测
    if signal_pct > 0 and volume_ratio < 1:
        features['price_volume_divergence'] = -1  # 价涨量缩
    elif signal_pct < 0 and volume_ratio > 1:
        features['price_volume_divergence'] = 1   # 价跌量增
    else:
        features['price_volume_divergence'] = 0
    
    return features
```

**输出位置**: `research_panel` + `repo_contributions.tradingagent_a`

**score_delta**: 保持 0.0（研究用途），但新增 `technical_features` 字段供 research_panel 使用。

---

## 二、qlib 特征工程集成

### 当前状态

只做了特征代理（rank_quality/liquidity_quality 等），没有调用 qlib 的特征工程能力。

### 网络限制

qlib 的完整管线需要数据源。但 xiaogu 已有丰富的东财数据，可以使用 qlib 的特征工程算子。

### 集成策略

**使用 xiaogu 已有的东财数据**，实现 qlib 的特征工程能力：

| 算子 | 能力 | 数据来源 |
|------|------|---------|
| `Slope` | 趋势强度 | `metric_delta_ts.jsonl` 的历史指标 |
| `Rsquare` | 趋势置信度 | `metric_delta_ts.jsonl` 的历史指标 |
| `Rank` | 截面排名 | `eastmoney_web_tabs_scored.jsonl` 的 rank |
| `Quantile` | 分位数 | `amount_pctile_rule` 等百分位字段 |
| `Corr` | 相关性 | `signal_amount` vs `signal_pct` |

### 实现方案

在 `xiaogu_native_repo_runtime_v0_1.py` 中扩展 `qlib_native_adapter`：

```python
# 新增：qlib 特征工程（使用 xiaogu 已有数据）
def compute_qlib_features(candidate: dict) -> dict:
    """使用 qlib 算子思想计算特征"""
    features = {}
    
    # 趋势强度（基于 signal_pct 的变化）
    signal_pct = candidate.get('signal_pct', 0)
    features['trend_strength'] = abs(signal_pct) / 10.0  # 归一化
    
    # 趋势置信度（基于 rank 的分位数）
    rank = candidate.get('rank', 0)
    universe_size = candidate.get('full_universe_quote_count', 5500)
    features['trend_confidence'] = 1.0 - (rank / max(1, universe_size))
    
    # 截面排名
    features['cross_sectional_rank'] = candidate.get('full_universe_rank', 0) / max(1, universe_size)
    
    # 量价相关性代理
    amount = candidate.get('signal_amount', 0)
    price_change = candidate.get('signal_pct', 0)
    if amount > 0 and price_change != 0:
        features['price_volume_corr_proxy'] = min(1.0, amount / (abs(price_change) * 1e8))
    else:
        features['price_volume_corr_proxy'] = 0.0
    
    return features
```

**输出位置**: `research_panel` + `repo_contributions.Qlib`

**score_delta**: 保持当前范围（-1.5~1.5），但新增 `qlib_features` 字段供 research_panel 使用。

---

## 三、QuantDinger 策略集成

### 当前状态

只做了数据健康检查（source_hash/evidence_path/source_time），没有使用策略能力。

### 集成策略

**使用 xiaogu 已有的东财数据**，实现 QuantDinger 的策略模板：

| 策略模板 | 能力 | 数据来源 |
|---------|------|---------|
| 动量+RSI 复合 | 截面打分排序 | `signal_pct` / `volume_ratio` |
| EMA 交叉信号 | 趋势跟踪 | `signal_pct` 历史变化 |
| 四路信号 | 开多/开空/平多/平空 | `signal_pct` / `volume_ratio` |
| 止损止盈参考 | 风险管理 | `signal_pct` / `price` |

### 实现方案

在 `xiaogu_native_repo_runtime_v0_1.py` 中扩展 `quantdinger_native_adapter`：

```python
# 新增：QuantDinger 策略因子（使用 xiaogu 已有数据）
def compute_strategy_features(candidate: dict) -> dict:
    """使用 QuantDinger 策略模板思想计算因子"""
    features = {}
    
    # 动量因子
    signal_pct = candidate.get('signal_pct', 0)
    features['momentum'] = signal_pct
    
    # RSI 代理（基于 signal_pct 和 volume_ratio）
    volume_ratio = candidate.get('volume_ratio', 1)
    rsi_proxy = 50 + signal_pct * 3 - (volume_ratio - 1) * 15
    features['rsi_proxy'] = min(100, max(0, rsi_proxy))
    
    # 动量+RSI 复合评分
    features['momentum_rsi_composite'] = features['momentum'] * 0.7 + (100 - features['rsi_proxy']) * 0.3
    
    # EMA 交叉信号代理
    # 如果 signal_pct > 0 且 volume_ratio > 1，表示上涨放量，EMA 可能金叉
    if signal_pct > 0 and volume_ratio > 1:
        features['ema_crossover_signal'] = 1  # 金叉信号
    elif signal_pct < 0 and volume_ratio > 1:
        features['ema_crossover_signal'] = -1  # 死叉信号
    else:
        features['ema_crossover_signal'] = 0  # 无信号
    
    # 四路信号
    if signal_pct > 2 and volume_ratio > 1.2:
        features['four_way_signal'] = 'open_long'
    elif signal_pct < -2 and volume_ratio > 1.2:
        features['four_way_signal'] = 'open_short'
    elif signal_pct < -2 and volume_ratio < 0.8:
        features['four_way_signal'] = 'close_long'
    elif signal_pct > 2 and volume_ratio < 0.8:
        features['four_way_signal'] = 'close_short'
    else:
        features['four_way_signal'] = 'hold'
    
    return features
```

**输出位置**: `research_panel` + `repo_contributions.QuantDinger`

**score_delta**: 保持当前范围（-2.0~0.0），但新增 `strategy_features` 字段供 research_panel 使用。

---

## 四、外部投资 Skills 探查

### 已确认缺失的 Skills

| Skill | 描述 | 对 xiaogu 的价值 | 来源 |
|-------|------|-----------------|------|
| UZI-Skill | A 股深度分析、龙虎榜、22 维数据、180 条量化规则 | **高** — 中文 A 股分析 | github.com/wbh604/UZI-Skill |
| Serenity Skill | 产业链研究、供应链瓶颈分析 | **中** — 产业链研究 | github.com/muxuuu/serenity |
| Buffett Skills | 质量审查（护城河、现金流、安全边际） | **中** — 长期投资审查 | github.com/agi-now/buffett |

### 集成建议

1. **UZI-Skill** — 优先集成。A 股量化规则和龙虎榜分析直接提升获利率。
2. **Serenity Skill** — 产业链研究可用于板块轮动分析。
3. **Buffett Skills** — 质量审查可用于候选风险排查。

### 实现方案

克隆仓库到 `external_research/`，提取可复用的分析逻辑（不依赖 LLM 的部分）：

```bash
# 克隆 skills
git clone https://github.com/wbh604/UZI-Skill external_research/uzi_skill
git clone https://github.com/muxuuu/serenity external_research/serenity_skill
git clone https://github.com/agi-now/buffett external_research/buffett_skill
```

然后提取其中的数据分析逻辑，集成到 xiaogu 的 research_panel。

---

## 五、集成架构

### 数据流

```
Eastmoney Scan (现有)
    ↓
TradingAgents 数据工具 (新增)
    ↓ akshare: 技术指标/基本面/新闻
qlib 特征工程 (新增)
    ↓ 算子: 斜率/R²/排名/相关性
QuantDinger 策略因子 (新增)
    ↓ 本地策略: 动量+RSI/EMA交叉
VEI 原生特征 (现有)
    ↓ 涨停前异动/弱转强/首板
Research Panel (现有 + 增强)
    ↓ 候选研究证据
Runner Gate (现有)
    ↓ hard gate: 监管/风险/数据
PAPER_PICK (现有)
```

### 输出结构

```json
{
  "candidate": "600519",
  "repo_contributions": {
    "tradingagent_a": {
      "status": "REAL_OUTPUT",
      "technical_features": {"rsi": 45.2, "macd": 0.15, ...},
      "fundamental_features": {"roe": 0.25, "debt_ratio": 0.35, ...},
      "score_delta": 0.0
    },
    "VEI": {
      "status": "REAL_OUTPUT_ACTIVE_VEI_ASOF_SCORING",
      "features": {"pre_limitup_anomaly": 0.8, ...},
      "score_delta": 1.2
    },
    "Qlib": {
      "status": "REAL_OUTPUT",
      "feature_view": {"rank_quality": 0.7, "slope_20": 0.3, ...},
      "score_delta": 0.5
    },
    "QuantDinger": {
      "status": "REAL_OUTPUT",
      "strategy_score": {"momentum_rsi": 65.0, "ema_signal": 1, ...},
      "score_delta": -0.2
    }
  },
  "research_panel": {
    "technical_analysis": "...",
    "fundamental_analysis": "...",
    "strategy_analysis": "...",
    "risk_assessment": "..."
  }
}
```

---

## 六、实施步骤

### Phase 1: TradingAgents + QuantDinger 策略集成 ✅ 完成

1. ✅ 新增 `compute_tradingagent_technical_features()` — RSI 代理、动量、波动率、趋势强度、趋势置信度、量价背离
2. ✅ 新增 `compute_quantdinger_strategy_features()` — 动量+RSI 复合、EMA 交叉信号、四路信号
3. ✅ 扩展 `tradingagent_a_native_adapter`，输出 `technical_features` 和 `strategy_features`
4. ✅ 更新 `repo_contribution_status`、`repo_contribution_candidate_signal`、`repo_contribution_explanation`
5. ✅ 测试 19 passed、py_compile PASS

### Phase 2: qlib 特征工程集成 ✅ 完成

1. ✅ 新增 `compute_qlib_enhanced_features()` — 趋势强度、趋势置信度、截面排名、量价相关性代理、动量质量、突破评分、反转评分、板块热度
2. ✅ 扩展 `qlib_native_adapter`，输出 `enhanced_features`
3. ✅ 更新 `repo_contribution_status`、`repo_contribution_candidate_signal`、`repo_contribution_explanation`
4. ✅ 测试 19 passed、py_compile PASS

### Phase 3: 外部 Skills 探查 ✅ 完成

1. ✅ 克隆 UZI-Skill（Serenity、Buffett Skills 仓库不存在）
2. ✅ 提取 UZI-Skill 22 维评分体系的简化版（K线阶段、估值、资金面、龙虎榜、舆情）
3. ✅ 新增 `compute_uzi_skill_features()` 和 `uzi_skill_native_adapter`
4. ✅ 测试 19 passed、py_compile PASS

### Phase 4: 验证与优化（下一步）

1. 用真实 scan 数据验证集成效果
2. 对比集成前后的 research_panel 输出
3. 优化特征权重和组合
4. 更新 TASK.md、STATE.md、PIPELINE.md

---

## 七、风险与约束

1. **akshare 依赖** — TradingAgents 数据工具依赖 akshare，需要确保网络可访问
2. **qlib 编译** — qlib 的 Cython 算子需要编译，确保环境正确
3. **性能影响** — 新增数据获取可能增加 scan 时间，需要优化并行/缓存
4. **V3 治理** — 所有新输出只能进入 research_panel，不直接影响 PAPER_PICK 排序（除非经过 promotion 审批）

---

## 八、成功标准

1. **技术指标** — RSI/MACD/Boll/ATR 等指标正常计算并写入 research_panel
2. **基本面** — ROE/负债率/现金流等指标正常计算并写入 research_panel
3. **策略因子** — 动量+RSI/EMA 交叉信号正常计算并写入 research_panel
4. **测试通过** — 所有新增测试通过，现有测试不回归
5. **research_panel 增强** — 候选的研究证据明显丰富，有助于人工判断
