# 实时出票链路运行报告 (2026-07-01)

## 运行环境
- **时间**: 2026-07-01 02:51 - 03:25 (约34分钟)
- **CDP**: http://127.0.0.1:9333 (Chrome/146.0.7680.177)
- **日期**: 2026-07-01 (周二)
- **内存**: 6.6GB RAM + 6GB Swap

## 运行流程与结果

### Step 1: Scanner启动
```bash
python3 xiaogu_eastmoney_web_tabs_scan_v0_1.py \
  --cdp-url http://127.0.0.1:9333 \
  --open-required-cdp-tabs \
  --source-time "2026-07-01 10:30:00"
```
**结果**: ✅ 启动成功
- 采集全市场报价: 4578只
- 候选数: 43只
- 证据收集: 开始处理43个候选

### Step 2: Scanner证据收集（卡住）
**问题**: Scanner在收集43个候选的CDP证据时超时（约10分钟未完成）
- 每个候选需要收集: announcements, lhb, financials, concept_industry, risk_alerts
- CDP页面切换+数据提取: 每个候选约10-15秒
- 43候选 × 15秒 = 约10.5分钟（理论值）
- 实际: 超过10分钟仍未完成

**根因**: CDP证据收集是串行的，没有并行化，且每个候选需要多次CDP页面切换

### Step 3: Runner执行
```bash
python3 xiaogu_forward_d1_1450_runner_v0_1.py \
  --date 2026-07-01 \
  --force \
  --no-runtime-date-adjust
```
**结果**: ✅ 完成，但decision=NO_PICK
- 原因: `NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE`
- Runner没有找到当天的scanner产物（因为scanner超时未写入）
- Runner从DB查询lifecycle history: ✅ 成功（使用DB而非JSONL ledger）
- Runner写入picks记录: ✅ 成功（1条NO_PICK记录）

### Step 4: DB状态
```
picks: 1 record (NO_PICK)
daily_candidates: 0 records (scanner未完成)
```

## 遇到的问题与修复

### 问题1: Scanner超时（证据收集阶段）
**现象**: Scanner在收集43个候选的CDP证据时超时，10分钟内未完成
**影响**: 无法生成当天的扫描产物，runner无法消费
**修复建议**: 
- 并行化CDP证据收集（当前是串行）
- 减少每个候选的证据域数量
- 增加scanner超时时间（当前900秒可能不够）

### 问题2: Scanner未写入summary文件
**现象**: Scanner超时退出后，`eastmoney_web_tabs_summary.json`未生成
**影响**: Runner无法找到当天的扫描产物
**修复建议**: 
- Scanner在超时前应写入partial summary
- 或者scanner在证据收集前就写入summary（仅含候选列表）

### 问题3: Runner默认asof_time=15:10
**现象**: Runner在没有scanner产物时使用默认asof_time=15:10
**影响**: Runner使用了过时的时间戳
**修复建议**: 
- Runner应拒绝在没有scanner产物的情况下运行
- 或者使用当前时间作为asof_time

## 修改的文件

| 文件 | 修改内容 | 目的 |
|------|----------|------|
| `xiaogu_eastmoney_web_tabs_scan_v0_1.py` | 添加`_validate_trade_date()`, 文件缓存, runner summary生成 | 日期校验, IO优化, 内存优化 |
| `xiaogu_forward_d1_1450_runner_v0_1.py` | 添加`--trigger-scan`, DB lifecycle history, bundle contract修复 | 消除OOM, 保证契约完整性 |
| `xiaogu_v2_1_six_repo_real_integrated.py` | 添加setup_type加分/减分, Rank软偏置 | 因子升级实验 |
| `xiaogu_db.py` | 添加`upsert_daily_candidate()`, `_stable_directory_record_key()` | DB留痕 |
| `scripts/xiaogu_db_backfill.py` | 新建，全量回填脚本 | 历史数据补全 |
| `scripts/xiaogu_db_review_report.py` | 新建，DB-first复盘报表 | 终端+HTML报表 |
| `AGENTS.md` | 添加双索引发现规则 | 架构治理 |

## 性能数据

| 指标 | 值 | 说明 |
|------|-----|------|
| Scanner启动时间 | <5秒 | 正常 |
| 全市场报价采集 | 4578只 | 正常 |
| 候选生成 | 43只 | 正常 |
| 证据收集 | >10分钟 | **瓶颈** |
| Runner执行 | ~60秒 | 正常（含DB查询） |
| Runner内存峰值 | ~600MB | 正常（有6GB swap） |
| DB写入 | <1秒 | 正常 |

## 待解决问题

1. **Scanner证据收集超时**: 需要并行化或减少证据域
2. **Scanner partial summary**: 超时前应写入部分结果
3. **Runner拒绝无产物运行**: 不应在没有scanner产物时使用默认asof_time
4. **Understand-Anything集成**: 需要运行`/understand`建立第二个知识索引
