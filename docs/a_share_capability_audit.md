# A股分析系统能力审计

## 审计日期: 2026-07-03

---

## 一、行情数据

| 数据项 | 状态 | 数据源 | 缺失字段 | 优先级 | 补齐方案 |
|--------|------|--------|----------|--------|----------|
| 上证指数行情 | done | baostock | - | P0 | - |
| 深证成指行情 | done | baostock | - | P0 | - |
| 创业板指行情 | done | baostock | - | P0 | - |
| 指数 OHLCV | done | baostock | - | P0 | - |
| 沪市成交额 | done | baostock | - | P0 | - |
| 深市成交额 | done | baostock | - | P0 | - |
| 两市总成交额 | done | 计算得出 | - | P0 | - |
| 近5日成交额 | done | baostock | - | P0 | - |
| 近10日成交额 | done | baostock | - | P0 | - |
| 全市场上涨家数 | partial | daily_candidates | 需从 scanner 补充 | P0 | scanner 输出 |
| 全市场下跌家数 | partial | daily_candidates | 需从 scanner 补充 | P0 | scanner 输出 |
| 涨停数量 | done | limit pool API | - | P0 | - |
| 跌停数量 | done | limit pool API | - | P0 | - |
| 炸板数量 | done | limit pool API | - | P1 | - |
| 连板高度 | done | limit pool API | - | P1 | - |

---

## 二、板块数据

| 数据项 | 状态 | 数据源 | 缺失字段 | 优先级 | 补齐方案 |
|--------|------|--------|----------|--------|----------|
| 行业板块涨跌幅 | partial | signals 表 | 需实时数据 | P0 | 东财 API |
| 概念板块涨跌幅 | partial | signals 表 | 需实时数据 | P0 | 东财 API |
| 行业板块主力净流入 | partial | signals 表 | 需实时数据 | P0 | 东财 API |
| 概念板块主力净流入 | partial | signals 表 | 需实时数据 | P0 | 东财 API |
| 板块成交额 | missing | - | 需新增 | P1 | 东财 API |
| 板块内领涨股 | missing | - | 需新增 | P1 | 东财 API |
| 板块内涨停家数 | missing | - | 需新增 | P1 | 东财 API |

---

## 三、个股数据

| 数据项 | 状态 | 数据源 | 缺失字段 | 优先级 | 补齐方案 |
|--------|------|--------|----------|--------|----------|
| 个股涨跌幅 | done | scanner | - | P0 | - |
| 成交额 | done | scanner | - | P0 | - |
| 成交量 | done | scanner | - | P0 | - |
| 换手率 | done | scanner | - | P0 | - |
| 量比 | missing | - | 需新增 | P1 | 计算 |
| 前5日均量 | missing | - | 需新增 | P1 | baostock |
| 前10日均量 | missing | - | 需新增 | P1 | baostock |
| 分时数据 | partial | intraday_replay | 需结构化 | P1 | CDP |
| 尾盘30分钟数据 | missing | - | 需新增 | P0 | CDP |
| 大单/超大单资金 | partial | fund_flow | 需结构化 | P1 | 东财 API |
| 涨停/炸板/跌停状态 | done | limit pool | - | P0 | - |

---

## 四、扩展数据

| 数据项 | 状态 | 数据源 | 缺失字段 | 优先级 | 补齐方案 |
|--------|------|--------|----------|--------|----------|
| 龙虎榜 | done | lhb API | - | P1 | - |
| 北向资金 | missing | - | 需新增 | P1 | 东财 API |
| 两融数据 | partial | margin tab | 需结构化 | P2 | 东财 API |
| 公司公告 | partial | announcements tab | 需结构化 | P2 | 东财 API |
| 减持/质押 | partial | risk tabs | 需结构化 | P2 | 东财 API |
| 问询函/监管处罚 | partial | risk tabs | 需结构化 | P2 | 东财 API |
| 政策资讯 | missing | - | 需新增 | P1 | 东财快讯 |
| 行业新闻 | missing | - | 需新增 | P1 | 东财快讯 |

---

## 五、已实现模块

| 模块 | 文件 | 功能 | 状态 |
|------|------|------|------|
| 市场大局分析 | `xiaogu_market_overview_analyzer.py` | 指数、涨跌家数、情绪判断 | ✅ |
| 量能分析 | `xiaogu_volume_analyzer.py` | 成交额、量价组合、交易节奏 | ✅ |
| 指数价位分析 | `xiaogu_index_level_analyzer.py` | 支撑压力、运行区间 | ✅ |
| 板块资金分析 | `xiaogu_sector_flow_analyzer.py` | 资金流入流出 | ✅ |
| 短线策略生成 | `xiaogu_short_term_strategy.py` | 交易节奏、仓位建议 | ✅ |
| 日报生成器 | `xiaogu_daily_report_generator.py` | 整合所有模块 | ✅ |

---

## 六、待实现模块（P1）

| 模块 | 功能 | 优先级 |
|------|------|--------|
| `abnormal_stock_scanner.py` | 盘口异动筛查 | P1 |
| `tail_session_analyzer.py` | 尾盘异动分析 | P1 |
| `minute_pattern_analyzer.py` | 分时异常识别 | P1 |
| `theme_analyzer.py` | 题材判断 | P1 |
| `news_policy_analyzer.py` | 政策资讯分析 | P1 |

---

## 七、P2 Backlog（暂不实现）

- 龙虎榜深度分析
- 游资席位风格识别
- 连板梯队分析
- 首板筛选
- 北向资金 + 两融分析
- 机构持仓分析
- 基本面体检
- 暴雷预警
- 中线波段
- 长线价值

---

## 八、执行顺序

1. ✅ 能力审计（本文档）
2. ✅ P0 模块实现
3. ✅ P0 日报生成测试
4. 提交 P0
5. 实现 P1 模块
6. 提交 P1
7. P2 暂放 backlog
