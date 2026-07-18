# 候选选择策略修复设计

## [S1] 问题

Scanner 的 `build_candidates` 选出了 20 只候选，但 0 只是涨停票。市场有 147 只涨停票（price<=70），全部被排除。

根因：
1. `L1_HOT_MOMENTUM` 池要求 `2.0 <= pct <= 9.0`，涨停票 pct >= 9.5 被排除
2. `L2_LIMIT_STRENGTH` 池虽然包含涨停票（pct >= 8.0），但池子容量只有 5
3. `selected` 是 dict，同一 code 多池子时后面的覆盖前面的
4. `L7_INTRADAY_ALERT` 和 `L8_LIMITUP_REASON_PROPAGATION` 池的候选（pct=0）挤掉了涨停票

## [S2] 目标

Scanner 必须能选到当天涨停或接近涨停的票，让 Runner 有机会评估它们。

## [S3] 修复方案

### 方案 A：扩大候选池（推荐）
- `per_pool_cap` 从 `max(5, 20//9)` 改为 `max(10, max_candidates//5)`
- `LIMIT_STRENGTH` 池单独给更大容量（涨停票是核心候选来源）
- 增加 `--max-candidates` 默认值到 40

### 方案 B：放宽 HOT_MOMENTUM 范围
- `max_pct` 从 9.0 改为 15.0 或 20.0
- 让涨停票也能进入 HOT_MOMENTUM 池

### 方案 C：增加涨停专用池
- 新增 `L1B_LIMITUP_CANDIDATE` 池，专门收集 pct >= 9.0 的票
- 独立容量，不与其他池子竞争

### 推荐：方案 A + C 组合
1. 新增涨停专用池，确保涨停票不被挤掉
2. 扩大 overall pool cap
3. Runner 门控逻辑保持不变（涨停票需要确认信号才能出票）

## [S4] 实施步骤

1. 在 `candidate_setup` 中新增 `L1B_LIMITUP_CANDIDATE` 层
2. 在 `build_candidates` 中新增 `LIMITUP_CANDIDATE` 池
3. 增加 `per_pool_cap` 和涨停池独立容量
4. 测试：确认涨停票能进入候选池
