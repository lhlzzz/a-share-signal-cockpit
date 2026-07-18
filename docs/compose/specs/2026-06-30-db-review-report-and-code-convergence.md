# DB-First复盘报表 + 代码收敛 设计规格

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task.

## [S1] Problem

xiaogu系统存在三个核心问题：
1. **没有DB-first复盘报表** — 无法快速验证回填数据质量、评估策略表现
2. **Scanner输入发散** — 3层数据源、28个证据域、多fallback路径导致扫描成本高
3. **运行时IO频繁** — scanner/runner/db在实时链路中频繁打开/读取文件

## [S2] Solution Overview

### Part A: DB-First复盘报表
基于已回填的daily_candidates + returns数据，生成：
- **即时收益(t1)**: 按rank bucket(1-3/4-6/7-10)统计平均t1_return
- **滞后收益(t2/t3)**: 同上维度
- **setup_class分布**: 按setup_class统计命中率和收益；保留 scanner 原始 `setup_type` 到复盘层的映射，但报表统一用 `setup_class`
- **决策质量**: PAPER_PICK vs NO_PICK的收益对比

### Part B: Scanner收敛
- 统一实时数据源入口：主路径收敛到单一`data/live_scan/{date}/`入口；历史回填/回放允许受控 fallback，不影响实时链路
- 减少证据域加载：按需加载28域，而非全量加载
- 消除重复读取：同一文件在同一pipeline中只读一次

### Part C: Runner+DB收敛
- Runner: 合并重复的候选评分子函数
- DB: 统一连接池管理，减少session创建/销毁
- DB: 保留现有单一事实源语义，复盘/报表只读 DB，不再从文件侧重建同一指标

## [S3] 复盘报表设计

### 终端输出（快速看）
```
=== xiaogu 复盘报表 (2026-05-19 ~ 2026-06-30) ===

【Rank Bucket收益】
Rank 1-3:  t1=+2.3%  t2=-1.1%  t3=-0.5%  (n=45, win_rate=58%)
Rank 4-6:  t1=+1.1%  t2=-0.8%  t3=-0.2%  (n=52, win_rate=52%)
Rank 7-10: t1=+0.5%  t2=-1.5%  t3=-1.0%  (n=55, win_rate=45%)

【Setup Type收益】
INTRADAY_ALERT_REVERSAL: t1=+3.1% (n=12, win_rate=67%)
LIMIT_STRENGTH:          t1=+1.8% (n=8, win_rate=63%)
UNDERWATER_RECOVERY:     t1=+0.9% (n=15, win_rate=53%)

【决策质量】
PAPER_PICK: t1=+2.5% (n=15, win_rate=60%)
Top10 CANDIDATE: t1=+1.2% (n=199, win_rate=52%)

【数据完整性】
daily_candidates: 37天, 1330条, 825条有分数
returns: 29天, 151条, 151条有t1
```

### HTML报告（详细版）
- 日期维度收益曲线图
- Rank bucket箱线图
- Setup class热力图
- 每日top10候选明细表

## [S4] Scanner收敛设计

### 当前问题
```
scanner尝试路径:
1. data/live_scan/{date}/eastmoney_web_tabs_scan_v0_1/
2. data/live_scan/{date}/eastmoney_web_tabs_scan_v0_1_cloak_9333_*/
3. data/forward_raw_runtime/{date}/
4. data/forward_candidate_bundles/{date}/
```

### 收敛方案
- 统一为单一入口: `data/live_scan/{date}/eastmoney_web_tabs_scan_v0_1/`
- 消除cloak变体的fallback（已验证数据质量相同）
- 证据域按需加载：仅加载当前候选需要的域

## [S5] Runner+DB收敛设计

### Runner收敛
- 合并`make_candidate`和`make_bundle`中的重复评分逻辑
- 消除重复的`safe_float`/`symbol_for`调用
- 统一候选特征提取入口

### DB收敛
- 使用连接池复用，减少session创建
- 批量insert/upsert代替逐条操作
- 添加索引优化常用查询

## [S6] Success Criteria

- [ ] 终端复盘报表可在5秒内生成
- [ ] HTML报告包含完整图表
- [ ] Scanner文件读取次数减少50%+
- [ ] Runner执行时间减少20%+
- [ ] 报表与 API/CLI 使用同一份 DB 数据源，不存在双口径
- [ ] 所有测试通过
