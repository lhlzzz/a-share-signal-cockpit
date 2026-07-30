# 四缺口全主攻：live re-decision + residual miss + T+1 roll + performance

**Normalized Goal:** 结构排序全量修复已落生产主链，但验收仍主要依赖 offline formal sort；同时 07-14/07-16 miss 未收敛、7/21 603115 T+1 未进滚动成绩单、performance 仍 FAIL（limitup 8.3%<10% 等）。本轮 **四个缺口全部主攻**：抬升验证到 production-path re-decision、啃残 miss、打通 T+1 滚动、并在不放水阈值/不放宽 hard gate 的前提下推进 performance 朝 PASS。

**Goal:** 四缺口全主攻且收口不丢项——re-decision 对照、07-14/07-16 根因修补、603115 T+1 滚动、performance 朝 PASS（诚实 FAIL 可接受）。

**Constraints:**
- **四个缺口都是主攻**，不得在执行中静默丢掉任一项。
- 验证/修复顺序可按依赖编排（通常 re-decision 与 miss 根因先于盲目调参；T+1 数据未到前 performance 用可得样本推进并诚实 pending），但收口时四项都要有证据。
- gap1 **不降阈值、不假绿**；禁止为冲 PASS 放宽 hard gate 或泄漏 T+1。
- 禁止 freeze paper pick；禁止无验证自动 `--apply-weights`。
- 禁止新建平行 replay 引擎 / sort v2 / 平行 pipeline；**修改/扩展现有入口**。
- 禁止用 T+1 已实现收益/涨停反推当日入选（防泄漏）。
- 证据必须诚实标注验证层级（offline sort / historical live replay / production-path re-decision / full wall-clock live），禁止把 offline 说成 full live。
- 任何生产排序/硬门再改动，必须以可解释失败原因为前提，且 large_loss / max_dd 不恶化。
- 明确 full wall-clock 12 日全爬仍非必达

**Out of scope:**
- freeze / READY_FOR_PAPER_PICK_FREEZE
- 自动 apply-weights
- 真实下单 / 券商接入
- 重写 28 域评分或换数据源体系
- 非主板扩展
- 仅 UI/文案
- HISTORICAL_LIVE_REPLAY 冒充 re-decision

**Baseline:**
| 项 | 状态 |
|---|---|
| offline 12 日 formal sort | avg +0.63%、win 50%、limitup 8.3%、large_loss 8.3%、max_dd -8.0% |
| gap2 | 07-14 002056 miss 001388；07-16 002558 miss 001258 |
| gap4 | 603115 PAPER_PICK；T+1 pending |
| gap1 | performance FAIL；阈值不动 |

## Must-Haves

- MH0: **四个缺口都是主攻**，不得在执行中静默丢掉任一项。收口 `all_four_gaps_have_evidence=true` A:I8
- MH1: re-decision 对照产物 + 验证层级声明 A:I8
- MH2: 07-14 / 07-16 根因 + 若改码则前后对照 A:I8
- MH3: T+1 backfill / 滚动前后差异或 pending 证据 A:I8
- MH4: performance gate 滚动结果（PASS 或诚实 FAIL） A:I8
- MH5: pytest 与风险不恶化；无 freeze/auto-weights/平行 v2 A:I8

## Decision Log

| decision | choice | why |
|---|---|---|
| Scope | 四缺口全主攻 | I8 |
| Re-decision | eligibility + exclusion + formal sort on DB snapshot | 可复现；非全爬 |
| L2 replay | 不冒充 re-decision | 不重算选票 |
| Thresholds | 不改 PERFORMANCE_GATE_THRESHOLDS | 防假绿 |

## Proof Requirements (mirrored)

- PR1: re-decision 对照产物 + 验证层级声明
- PR2: 07-14 / 07-16 根因 + 若改码则前后对照
- PR3: T+1 backfill / 滚动前后差异或 pending 证据
- PR4: performance gate 滚动结果（PASS 或诚实 FAIL）
- PR5: pytest 与风险不恶化
- PR6: 明确 full wall-clock 12 日全爬仍非必达

### Task 1: 四层验证定义表 A:I8

- [ ] 写明 L1 offline formal sort / L2 HISTORICAL_LIVE_REPLAY / L3 production-path re-decision / L4 full wall-clock live 的入口与是否重算选票
- [ ] 标明 L2 不关闭 gap3；L4 全爬非必达
- [ ] Verification: 打开 `build_historical_live_replay_closure` 确认不调用 formal 重选官方

### Task 2: 固定 12 日样本主键 A:I8

- [ ] 从 `summary/2026-07-21_structural_ranking_full_fix_replay.json` 的 `daily[]` 提取全部 `date`
- [ ] 记录 offline limits 原文含 `not full live pipeline re-decision`
- [ ] Verification: 得到 12 个日期列表，可打印为 `sample_dates`

### Task 3: 新增 redecision 函数骨架 A:I8

- [ ] 在 `xiaogu_backtest_v0_1.py` 新增 `production_path_redecision_for_day(candidate_rows, bundle_context=None)`
- [ ] 对每行先 `_attribution_candidate`，再 import runner 的 eligibility/exclusion/sort（不复制逻辑正文）
- [ ] Verification: `python -c "from xiaogu_backtest_v0_1 import production_path_redecision_for_day"` 成功

### Task 4: redecision 选股语义 A:I8

- [ ] formal-eligible = `paper_pick_eligibility_profile` 通过且 `official_target_exclusion_reasons` 为空
- [ ] 非空则 `max(eligible, key=formal_candidate_sort_key)`；空则 `notes=NO_FORMAL_ELIGIBLE`
- [ ] 返回 `redecision_symbol, eligible_count, excluded_count, top3_formal_symbols, notes`
- [ ] Verification: 单测 `test_production_path_redecision_picks_formal_sort_winner_and_honors_exclusion` 通过

### Task 5: redecision 防泄漏 A:I8

- [ ] 输入经 `_sanitize_decision_snapshot_rows` 或等价剥离
- [ ] 禁止 `t1_return` / `t2_return` / `is_limit_up` 进入 eligibility 或 sort
- [ ] Verification: 单测 `test_production_path_redecision_strips_or_rejects_future_return_fields` 通过

### Task 6: redecision CLI 入口 A:I8

- [ ] 扩展 `xiaogu_backtest_v0_1.py` 或 `daily_pipeline.sh` 增加 `--production-path-redecision`
- [ ] 不得复用 `--historical-live-replay` 名称
- [ ] Verification: usage/help 可见新 flag；调用走 Task 3 函数

### Task 7: 生成 re-decision 对照产物 A:I8

- [ ] 对 `sample_dates` 逐日算 `official_old` / `offline_formal` / `redecision`
- [ ] 写 `summary/2026-07-21_production_path_redecision_compare.json`
- [ ] Verification: 文件含 PR1 所需：`validation_tier` 与 `validation_tier_declaration`（re-decision 对照产物 + 验证层级声明）

### Task 8: 填充对照 metrics 字段 A:I8

- [ ] `daily[]` 含 t1、day_best、`agree_offline_vs_redecision`、notes
- [ ] 写 `metrics_redecision` 与 `agree_rate_offline_vs_redecision` 与 `limits`
- [ ] Verification: JSON 可被 `python -m json.tool` 解析且 metrics 键齐全

### Task 9: 07-14 miss 根因 A:I8

- [ ] 对比 002056 与 day-best 001388 的 pool/eligibility/exclusion/sort/ranking_basis
- [ ] 写 `primary_root_cause` 四选一：`NOT_IN_DECISION_POOL` / `HARD_EXCLUDED_OR_INELIGIBLE` / `SORT_UNDERWEIGHTED` / `DATA_INCOMPLETE_PRE_DECISION`
- [ ] Verification: 07-14 根因字段落盘可读

### Task 10: 07-16 miss 根因 A:I8

- [ ] 对比 official/redecision 与 day-best 001258 的 pool/eligibility/exclusion/sort/ranking_basis
- [ ] 写 `primary_root_cause`（同上四选一）
- [ ] Verification: 07-16 根因字段落盘可读

### Task 11: 决定是否改生产码 A:I8

- [ ] 若两日均为 pool/data 缺口：`production_patch=NONE_DATA_OR_POOL_GAP` 且不改码
- [ ] 若存在 `SORT_UNDERWEIGHTED` 或可解释 exclusion：仅最小改 runner 现有 ranking/risk/exclusion
- [ ] Verification: 决策写入证据；禁止放宽 hard gate；禁止 t1 特征

### Task 12: 改码后回归对照 A:I8

- [ ] 若有改码则重跑 redecision compare，写 before/after metrics
- [ ] 检查 07-14/07-16 gap 不恶化，且 large_loss/max_dd 不恶化；07-13 与 07-20 不回退大亏
- [ ] 若无改码则记录 `after_patch=SKIPPED`
- [ ] Verification: 前后对照表或 SKIPPED 标记存在（PR2）

### Task 13: 执行 603115 回填命令 A:I8

- [ ] 确认官方票 603115
- [ ] 跑 `python3 scripts/xiaogu_return_backfill.py --trade-date 2026-07-21 --validate-on 2026-07-22`
- [ ] Verification: 命令退出后检查 `summary/return_backfill_results.json` 更新时间或输出

### Task 14: 标记 T+1 READY 或 PENDING A:I8

- [ ] READY：603115 非空 `t1_return`；PENDING：无数据则不写假收益并记录原因
- [ ] Verification: 状态二选一写入证据（PR3 的 pending 分支）

### Task 15: 滚动 refresh A:I8

- [ ] READY 时重跑 t1-validation/closure 与 redecision compare
- [ ] PENDING 时写 `rolling_refresh=SKIPPED_PENDING_T1`
- [ ] Verification: READY 样本含 7/21 或 PENDING skip 原因存在（PR3）

### Task 16: 三路径 performance 重算 A:I8

- [ ] 不修改 `PERFORMANCE_GATE_THRESHOLDS`
- [ ] 计算 official_rolling / offline_formal_path / redecision_path 的 gate 指标与 status
- [ ] Verification: 三路径结果落盘（PR4）

### Task 17: performance 最终判定 A:I8

- [ ] 已 PASS 则 `gap1_status=PASS_ON_<path>`
- [ ] 全 FAIL 则列失败阈值差距；仅证据支持时才允许一轮最小修补后再判
- [ ] Verification: 最终 `gap1_status=PASS` 或 `FAIL` + `blocking_reason`（PR4）

### Task 18: 定向 pytest A:I8

- [ ] 跑 `pytest -q tests/test_xiaogu_a_share_forward_runner.py -k "production_path_redecision or ranking_basis or formal_sort or risk_gate or historical_replay or leakage or exclusion or performance or case_book"`
- [ ] Verification: 退出码 0（PR5）

### Task 19: 红线 diff 检查 A:I8

- [ ] 确认阈值未降、无平行 v2 文件、无 freeze/auto-weights、无 hard gate 放宽、无 t1 泄漏入选
- [ ] Verification: `git diff` 人工核对通过并记入证据

### Task 20: 四缺口证据包收口 A:I8

- [ ] 写 `summary/2026-07-21_four_gap_status.json`，含 gap3/gap2/gap4/gap1/red_lines
- [ ] 仅当四 gap 字段均非空时 `all_four_gaps_have_evidence=true`
- [ ] Verification: `all_four_gaps_have_evidence==true` 证明 **四个缺口都是主攻**，不得在执行中静默丢掉任一项。

## Assumptions

- 12 日 snapshot 多数可得；缺失日进子集并写 limits
- T+1 可能 PENDING；状态机已覆盖
- redecision 最小忠实子集非 runner 主函数逐行等价；差异写 limits

## Risks

- offline 误标 live
- miss 过拟合
- 只写报告不交付：Task 20 硬卡四项证据
