# xiaogu 数据扫描说明

## 扫描脚本

### 1. 快速扫描 (单次)
```bash
# 默认扫描
bash daily_scan.sh

# 指定日期
bash daily_scan.sh 2026-07-07

# 早盘扫描
bash daily_scan.sh morning

# 尾盘扫描
bash daily_scan.sh afternoon
```

### 2. 完整出票流程
```bash
# 扫描 + 出票
bash daily_pipeline.sh

# 指定日期
bash daily_pipeline.sh 2026-07-07
```

## 数据域清单 (31个)

### 行情中心 (12个)
| 数据域 | 说明 | 采集量 |
|--------|------|--------|
| stock_all_a | 沪深A股全市场 | ~5800 |
| sector_industry | 行业板块 | ~500 |
| sector_concept | 概念板块 | ~500 |
| sector_region | 地域板块 | ~30 |
| indexes | 沪深京指数 | 11 |
| limitup_pool | 涨停池 | ~20 |
| limitup_broken | 炸板池 | ~10 |
| limitup_consecutive | 连板池 | ~20 |
| limitup_yesterday | 昨日涨停 | ~30 |
| block_trades | 大宗交易 | ~1200 |
| trading_halts | 停牌信息 | ~10 |
| popularity_rank | 人气排名 | ~100 |

### 资金流 (5个)
| 数据域 | 说明 | 采集量 |
|--------|------|--------|
| stock_capital_flow | 个股资金流 | ~5500 |
| sector_capital_flow | 板块资金流 | ~1000 |
| market_capital_flow | 大盘资金流 | 3 |
| flow_industry | 行业资金流 | ~500 |
| flow_concept | 概念资金流 | ~500 |

### 数据中心 (10个)
| 数据域 | 说明 | 采集量 |
|--------|------|--------|
| lhb | 龙虎榜 | 500 |
| hsgt_summary | 北向资金汇总 | 1 |
| hsgt_deals | 北向资金明细 | 500 |
| hsgt_holdings | 北向持股明细 | ~500 |
| earnings_preview | 业绩预告 | 500 |
| lockup_expiry | 限售解禁 | 500 |
| org_survey | 机构调研 | 1000 |
| margin_trading | 融资融券 | 500 |
| shareholder_changes | 股东变动 | 500 |
| ipo_calendar | IPO日历 | 500 |

### 研报/公告/新闻 (4个)
| 数据域 | 说明 | 采集量 |
|--------|------|--------|
| stock_reports | 个股研报 | ~100 |
| industry_reports | 行业研报 | ~100 |
| announcements | 公告 | ~100 |
| news_kuaixun | 东财7x24快讯 | 200 |

## 输出目录结构
```
data/live_scan/{date}/
├── eastmoney_scan_morning/      # 早盘扫描
│   ├── scan_summary.json
│   ├── eastmoney_web_tabs_summary_runner.json
│   └── *.jsonl                  # 31个数据文件
├── eastmoney_scan_afternoon/    # 尾盘扫描
│   └── ...
└── eastmoney_scan -> eastmoney_scan_afternoon  # symlink
```

## 12个评分维度 (综合评分 0-95分)

| # | 维度 | 分数 | 数据源 |
|---|------|------|--------|
| 1 | 基础分 | 30-70 | 涨幅+位置+流动性 |
| 2 | 涨停潜力 | 0-15 | 蓄力+高换手+资金流入+低位反弹 |
| 3 | 市场信号 | 0-20 | 涨停池+连板+龙虎榜+炸板+昨日涨停 |
| 4 | 资金信号 | 0-15 | 主力净流入+超大单+融资余额+北向持股 |
| 5 | 基本面 | 0-15 | 业绩预告+机构调研+研报 |
| 6 | 板块轮动 | 0-8 | 行业资金流→龙头股 |
| 7 | 题材热度 | 0-8 | 概念资金流→龙头股 |
| 8 | 龙头动向 | 0-10 | 连板+涨停池+封单强度 |
| 9 | 资金流向 | 0-10 | 个股资金流+大盘资金流 |
| 10 | 市场情绪 | 0-10 | 市场宽度+涨停/炸板比+连板高度 |
| 11 | 新闻催化 | 0-8 | 关键词同义词→板块龙头+公告提及 |
| 12 | 风险扣分 | 0-20 | 停牌+限售解禁+大宗折价+股东减持 |

### 新闻催化关键词 (mimo判断)
- 银行: 降准、降息、LPR、金融改革
- AI: 大模型、算力、GPU、AI应用、人工智能
- 半导体: 芯片法案、国产替代、光刻机、封测
- 新能源: 光伏补贴、储能政策、锂电池、充电桩
- 创新药: CXO、医药、生物制品、疫苗
- 红利: 高股息、央企估值、破净修复、中特估
- 机器人: 人形机器人、特斯拉机器人、减速器
- 军工: 军费增长、装备订单、航天发射
- 煤炭: 能源安全、煤价、限产
- 华为海思: 海思、华为
- 中芯概念: 中芯、台积电
- 白酒: 茅台
- 券商: 证券
- 房地产: 地产、楼市
- 汽车: 新能源车、智能驾驶
- 5G: 通信、数据中心

## 定时任务
```bash
# 安装定时任务
crontab crontab_config.txt

# 查看定时任务
crontab -l
```

## 数据源
- API: 东方财富 push2/datacenter/reportapi
- Fallback: CloakBrowser (涨停池数据)
- 代码: `scrapy_scanner/runner_v2.py`
