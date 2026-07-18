# xiaogu 生命闭环完善 — Compose 任务包

**日期**: 2026-06-26  
**模型**: mimoauto (权限全开)  
**设计者**: Claude (claude-opus-4-8)  
**目标**: 高收益高涨停率，每天稳定出票，权重自动纠偏

---

## 系统现状

```
Scanner(CDP) → Runner(14:50) → Recorder(ledger) → Filler(T+1 return)
                                                         ↓
                                               signal_effectiveness(20:00)
                                                         ↓
                                               [断点] scoring_config 无人写回
```

**6个断点按优先级排列：**

| ID | 断点 | 影响 |
|----|------|------|
| LC-01 | weight_suggestion 不写回 scoring_config | 每日复盘无法自动纠偏权重 |
| LC-02 | social_sentiment 无采集job | runner读DB得null，social信号空转 |
| LC-03 | 无滚动涨停率门控 | 表现恶化无主动告警 |
| LC-04 | 节假日误跑 | 非交易日job执行浪费+报错 |
| LC-05 | 权重变更前无回测验证 | 改参数可能适得其反 |
| LC-06 | 外部社交源已收敛为东方财富股吧 | 降低噪声，保留诊断-only 舆情侧车 |

---

## TASK LC-01: 权重反馈自动写回

### 目标
`signal_effectiveness` 分析完后，把 `weight_suggestion=INCREASE` 的信号按步长 +0.1 写入 `scoring_config`，`DECREASE` 的 -0.1，`MAINTAIN` 不变。有下限保护(0.1)和上限(3.0)。

### 修改文件
- **主改**: `xiaogu_signal_effectiveness_v0_1.py`
  - 新增函数 `apply_weight_suggestions(analysis_result, db_url, dry_run=True)`
  - 从 `scoring_config` 表读当前权重，计算新值，写回
  - dry_run=True 只打印不写
- **主改**: `xiaogu_scheduler.py`
  - `job_signal_effectiveness` 调用后追加调用 apply_weight_suggestions
  - 环境变量 `XIAOGU_WEIGHT_AUTO_TUNE=1` 控制开关，默认关闭

### scoring_config 表结构（已存在）
```sql
-- signal_key, config_value(float), updated_at
-- 已有 UNIQUE(signal_key)
```

### 禁止修改
- runner 核心决策逻辑
- LOCKED_SAFETY 区块
- ledger 格式

### 验收标准
1. `python xiaogu_signal_effectiveness_v0_1.py --apply-weights --dry-run` 输出哪些key会涨哪些跌
2. `XIAOGU_WEIGHT_AUTO_TUNE=1` 时 scheduler 在20:00后确实写库
3. 权重有下限(0.1)上限(3.0)保护，不会写出界外值
4. 所有写操作有 `updated_at` 时间戳

### 测试
- `tests/test_xiaogu_a_share_forward_runner.py` 里加 `test_apply_weight_suggestions_dry_run`

---

## TASK LC-02: 社会情绪采集入调度

### 目标
每天14:20（盘中末段）用 CDP 采集当日候选股的股吧情绪，写入 DB signals 表 signal_key='social_sentiment'，让runner在14:50能读到。

### 修改文件
- **主改**: `xiaogu_social_sentiment.py`
  - 新增 `collect_and_store(symbols: list[str], db_url: str) -> dict`
  - 调用已有 `scrape_eastmoney_guba`，把 sentiment_score 写入 DB
  - 接受 `--symbols` 和 `--from-candidates` 两种输入模式
  - `--from-candidates` 从最新 scan bundle 读候选股列表
- **主改**: `xiaogu_scheduler.py`
  - 新增 `job_sentiment_collect` 在 14:20 触发
  - 环境变量 `XIAOGU_SENTIMENT_ENABLED=1` 控制开关

### DB 写入格式
```python
# signals 表
{
  "trade_date": today,
  "symbol": "300059",
  "signal_key": "social_sentiment",
  "signal_value": 0.63,   # sentiment_score
  "metadata": {"post_count": 28, "pos": 8, "neg": 3}
}
```

### 禁止修改
- CDP tab 复用逻辑（不能另开新tab，用现有 page tabs）
- runner 决策路径

### 验收标准
1. `python xiaogu_social_sentiment.py --symbols 300059,000001 --store` 能写入DB
2. scheduler 14:20 job 能从候选bundle自动读symbol列表
3. runner 在 scoring_config 有 social_sentiment 权重时能读到非null值

### 测试
- mock CDP eval 返回 → 验证 sentiment_score 计算正确
- DB 写入后可读出

---

## TASK LC-03: 滚动涨停率门控告警

### 目标
每天20:00信号分析后，计算最近7个交易日的滚动涨停率。低于20%触发 WARNING 日志和 state 文件写出，让运维可感知。

### 修改文件
- **主改**: `xiaogu_signal_effectiveness_v0_1.py`
  - 新增函数 `rolling_limit_up_check(ledger_path, window=7, threshold=0.20) -> dict`
  - 返回 `{rolling_lu_rate, window, alert: bool, reason}`
  - 把结果追加写入 `state/performance_gate.json`（覆盖更新）
- **主改**: `xiaogu_scheduler.py`
  - `job_signal_effectiveness` 结束前调用此检查
  - alert=True 时 logger.warning 红色输出

### state/performance_gate.json 格式
```json
{
  "checked_at": "2026-06-26T20:01:32",
  "rolling_window": 7,
  "rolling_lu_rate": 0.14,
  "alert": true,
  "reason": "7日滚动涨停率0.14 < 阈值0.20",
  "filled_count": 7
}
```

### 禁止修改
- 任何决策路径（只做观测，不干预出票）

### 验收标准
1. 有超过7条FILLED记录时能正确计算
2. 少于3条时返回 `alert=false, reason="INSUFFICIENT_DATA"`
3. `state/performance_gate.json` 每次运行后更新

---

## TASK LC-04: A股交易日历集成

### 目标
替换 scheduler 里 `weekday < 5` 的粗糙判断，接入 exchange_calendars 或内置节假日列表，避免法定节假日误跑。

### 修改文件
- **主改**: `xiaogu_scheduler.py`
  - 优先用 `exchange_calendars` 包（已在环境里）查 `XSHG` 日历
  - fallback: 内置 `CHINA_HOLIDAYS_2026` 硬编码列表（至少到2026年底）
  - `is_trading_day()` 函数对外接口不变，内部实现升级
  - 只在 `exchange_calendars` 不可用时才用 hardcode

### hardcode fallback（最小集）
```python
# 2026年A股休市（法定假日+调休）
CHINA_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-26","2026-01-27","2026-01-28","2026-01-29","2026-01-30",
    "2026-02-02","2026-02-03",  # 春节
    "2026-04-06",  # 清明
    "2026-05-01","2026-05-04","2026-05-05",  # 劳动节
    "2026-06-22",  # 端午
    "2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07","2026-10-08","2026-10-09",  # 国庆
}
```

### 验收标准
1. `is_trading_day("2026-10-01")` 返回 False（国庆）
2. `is_trading_day("2026-06-26")` 返回 True（普通周五）
3. `exchange_calendars` 不可用时 fallback 正常工作
4. 不引入新的必选依赖（exchange_calendars 是可选增强）

---

## TASK LC-05: 权重变更前回测验证

### 目标
在 `apply_weight_suggestions` 写回前，用最近30天 factor_store 数据快速回测新旧权重，只有新权重下涨停率 >= 旧权重才执行写回。

### 修改文件
- **主改**: `xiaogu_signal_effectiveness_v0_1.py`
  - 新增 `backtest_weight_set(new_weights, old_weights, factor_dir, window=30) -> dict`
  - 从 `xiaogu_factor_store.read_all_factors(last 30 days)` 读数据
  - 用新旧权重分别对 factor 打分，用 `returns` 表的实际t1_return验证
  - 返回 `{new_lu_rate, old_lu_rate, verdict: "BETTER"|"WORSE"|"NEUTRAL"}`
- `apply_weight_suggestions` 里：verdict != "BETTER" 时跳过写回，打印原因

### 禁止修改
- factor_store 格式
- returns 表结构

### 验收标准
1. factor 数据不足10天时 verdict="NEUTRAL"（跳过验证，直接写回）
2. 新权重在历史数据上涨停率更高才写回
3. 回测结果写入日志，可审计

---

## TASK LC-06: 东方财富股吧社交证据收敛

### 目标
将社交侧车收敛为东方财富股吧单一来源。外部社交平台不再作为 active 采集、DB 写入或 pipeline 默认源。

### 实施边界（2026-07-17）

- 采集 owner 继续是既有 `xiaogu_social_sentiment.py`，不新增 `xiaogu_theme_collector.py`。
- active 信号只保留 `social_sentiment_eastmoney_guba`、`social_catalyst_score`、`social_noise_risk`、`social_sentiment_score` 和 `social_collection_status`。
- `theme_strength_last30d` 不再由 social collector 写入；runner 兼容字段固定为 `0.0`。
- `production_ranking_change_gate=LOCKED`：社交信号保持 shadow/diagnostic-only，禁止写入正式排序或 `PAPER_PICK` 硬门禁。
- 东方财富股吧不可用时 pipeline 仅记录 WARN，不能阻断 runner。

### 修改文件
- **主改**: `xiaogu_social_sentiment.py`
  - 删除 active 外部社交平台 collector。
  - `collect_and_store()` 只调用 `scrape_eastmoney_guba()`。
  - `_store_social_payload()` 停止写入外部平台和主题热度 signal。
- **主改**: `daily_pipeline.sh`
  - 默认 `XIAOGU_SOCIAL_SOURCES=eastmoney_guba`。
  - 不再传递外部社交平台环境变量。

### 验收标准
1. `python xiaogu_social_sentiment.py --symbols 300059 --sources eastmoney_guba` 只访问东方财富股吧。
2. DB 写入不包含外部平台和主题热度 signal。
3. runner 仍能读取 shadow diagnostics，official `PAPER_PICK` 行为不变。

---

## 执行顺序建议

```
LC-04 (交易日历)  ← 最快，先做，防止节假日误跑
    ↓
LC-01 (权重写回)  ← 闭环最核心
    ↓
LC-03 (滚动门控)  ← 依赖 LC-01 的分析结果
    ↓
LC-02 (情绪采集)  ← 独立，可并行
LC-06 (社交证据收敛)  ← 独立，可并行
    ↓
LC-05 (回测验证)  ← 依赖 LC-01 完成后再加验证层
```

---

## 全局约束（所有TASK共享）

1. **PAPER_ONLY / NO_TRADE = True 不得修改**
2. **LOCKED_SAFETY 区块不得修改**
3. **ledger 格式只能追加，不能改历史记录**
4. **每个新功能默认关闭**，用环境变量开关控制
5. **优先修改已有文件**，社交采集继续由 `xiaogu_social_sentiment.py` 单一 owner 承担，禁止新增 parallel collector
6. **每个TASK完成后跑 `tests/test_xiaogu_a_share_forward_runner.py`**，不能引入新失败
