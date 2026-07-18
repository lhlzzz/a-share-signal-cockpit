# A股分析系统能力审计

## 审计日期: 2026-07-03

---

## 一、数据源能力

| 能力 | 状态 | 当前实现 | 缺失字段 | 优先级 |
|------|------|----------|----------|--------|
| 上证指数行情 | ✅ 已实现 | `collect_index_snapshot()` | - | P0 |
| 深证成指行情 | ✅ 已实现 | `collect_index_snapshot()` | - | P0 |
| 创业板指行情 | ✅ 已实现 | `collect_index_snapshot()` | - | P0 |
| 两市总成交额 | ⚠️ 部分实现 | 需要从 scanner 提取 | 历史均量对比 | P0 |
| 沪市成交额 | ⚠️ 部分实现 | 需要从 scanner 提取 | 历史均量对比 | P0 |
| 深市成交额 | ⚠️ 部分实现 | 需要从 scanner 提取 | 历史均量对比 | P0 |
| 行业板块资金流向 | ✅ 已实现 | `fetch_all_sector_fund_flow()` | - | P0 |
| 概念板块资金流向 | ✅ 已实现 | `sector_fund_flow_stocks()` | - | P0 |
| 个股涨跌幅 | ✅ 已实现 | scanner quotes | - | P0 |
| 个股成交额/量 | ✅ 已实现 | scanner quotes | - | P0 |
| 换手率 | ✅ 已实现 | scanner quotes | - | P0 |
| 涨停池 | ✅ 已实现 | `rows_from_limitup_pool_api()` | - | P1 |
| 跌停池 | ✅ 已实现 | limit pool API | - | P1 |
| 炸板池 | ✅ 已实现 | limit pool API | - | P1 |
| 连板池 | ✅ 已实现 | limit pool API | - | P1 |
| 龙虎榜 | ✅ 已实现 | `rows_from_lhb_api()` | - | P1 |
| 北向资金 | ❌ 未实现 | - | 需要新增 | P1 |
| 两融数据 | ⚠️ 部分实现 | scanner margin tab | 需要结构化 | P1 |
| 分时数据 | ⚠️ 部分实现 | `intraday_replay_page_rows()` | 需要聚合 | P1 |
| 尾盘数据 | ❌ 未实现 | - | 需要新增 | P0 |
| 政策资讯 | ❌ 未实现 | - | 需要新增 | P1 |
| 公司公告 | ⚠️ 部分实现 | scanner announcements tab | 需要结构化 | P2 |
| 减持/质押 | ⚠️ 部分实现 | scanner risk tabs | 需要结构化 | P2 |

---

## 二、分析能力

| 能力 | 状态 | 当前实现 | 缺失字段 | 优先级 |
|------|------|----------|----------|--------|
| 市场大局分析 | ❌ 未实现 | - | 需要新增 | P0 |
| 量能分析 | ⚠️ 部分实现 | `classify_volume_level()` | 历史对比 | P0 |
| 指数价位分析 | ❌ 未实现 | - | 支撑压力计算 | P0 |
| 板块资金分析 | ✅ 已实现 | `sector_prediction()` | - | P0 |
| 个股异动筛查 | ⚠️ 部分实现 | scanner 异动检测 | 需要结构化输出 | P1 |
| 尾盘异动 | ❌ 未实现 | - | 需要新增 | P0 |
| 分时异常 | ⚠️ 部分实现 | `intraday_replay_page_rows()` | 需要模式识别 | P1 |
| 政策资讯分析 | ❌ 未实现 | - | 需要新增 | P1 |
| 题材判断 | ⚠️ 部分实现 | `extract_hot_sector_names_from_capital_flow()` | 需要一日游判断 | P1 |
| 短线策略 | ❌ 未实现 | - | 需要新增 | P0 |
| 龙虎榜分析 | ⚠️ 部分实现 | `rows_from_lhb_api()` | 需要游资风格识别 | P2 |
| 连板梯队 | ⚠️ 部分实现 | limit pool API | 需要梯队梳理 | P2 |
| 风控排雷 | ⚠️ 部分实现 | scanner risk tabs | 需要结构化 | P2 |

---

## 三、已实现的关键函数

### 数据采集
- `collect_index_snapshot()` - 指数快照
- `fetch_all_sector_fund_flow()` - 板块资金流向
- `sector_fund_flow_stocks()` - 板块资金个股
- `rows_from_limitup_pool_api()` - 涨停池
- `rows_from_lhb_api()` - 龙虎榜
- `rows_from_financial_api()` - 财务数据

### 分析逻辑
- `classify_volume_level()` - 量能分类
- `sector_prediction()` - 板块预测
- `sector_trend_score()` - 板块趋势评分
- `sector_enriched_scoring()` - 板块增强评分
- `extract_hot_sector_names_from_capital_flow()` - 热门板块提取

### 输出
- `get_overview()` - API 概览
- `xiaogu_api.py` - REST API 接口

---

## 四、缺失能力（需新增）

### P0: 必须先实现
1. `market_overview_analyzer.py` - 市场大局分析
2. `volume_analyzer.py` - 量能分析（增强现有）
3. `index_level_analyzer.py` - 指数价位分析
4. `sector_flow_analyzer.py` - 板块资金分析（增强现有）
5. `tail_auction_analyzer.py` - 尾盘异动分析
6. `short_term_strategy_generator.py` - 短线策略生成
7. `daily_report_generator.py` - 日报生成器

### P1: 第二阶段
1. `intraday_abnormal_stock_scanner.py` - 盘口异动筛查
2. `minute_pattern_analyzer.py` - 分时异常分析
3. `theme_emergence_analyzer.py` - 题材判断
4. `news_policy_analyzer.py` - 政策资讯分析

### P2: 第三阶段
1. `lhb_analyzer.py` - 龙虎榜分析
2. `hot_money_style_analyzer.py` - 游资风格识别
3. `limit_up_analyzer.py` - 连板梯队分析
4. `black_swan_warning_analyzer.py` - 风控排雷

---

## 五、数据源推荐补齐方式

| 缺失数据 | 推荐方式 | API/工具 |
|----------|----------|----------|
| 北向资金 | 东财 API | `datacenter-web.eastmoney.com` |
| 历史成交额 | baostock | `query_history_k_data_plus()` |
| 尾盘数据 | CDP 扫描 | 14:30-15:00 时间段数据 |
| 政策资讯 | 东财快讯 API | `push2.eastmoney.com/api/qt/` |
| 支撑压力 | 计算得出 | MA5/10/20 + ATR |

---

## 六、建议执行顺序

1. ✅ 能力审计（本文档）
2. 实现 `market_overview_analyzer.py`
3. 实现 `volume_analyzer.py`
4. 实现 `index_level_analyzer.py`
5. 实现 `sector_flow_analyzer.py`
6. 实现 `tail_auction_analyzer.py`
7. 实现 `short_term_strategy_generator.py`
8. 实现 `daily_report_generator.py`
9. 生成第一版日报
10. 用最近 5 个交易日回放测试
