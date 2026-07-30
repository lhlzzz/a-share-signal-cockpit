# PAPER_PICK 结构排序全量修复

**Goal:** 把 12 日可比样本诊断暴露的结构缺口全部落到生产主链：在现有 `ranking_basis_adjustment_components` / `formal_candidate_sort_key` / risk hard gate 上做可解释提权与风险加强，使 production-path replay 朝 `limitup_gene_shadow_plus` + `low_position_catalyst_shadow_plus` + `risk_penalty_shadow_plus` 的改善方向收敛，并保留单测 + 12 日对照证据。

**Constraints:**
- 修改现有实现，禁止新建 `formal_candidate_sort_key_v2` 或平行 scoring engine
- 禁止为追涨停放宽 hard gate；OUTFLOW / failed-limitup / high-popularity / large-loss 只能加强或保持
- 禁止无验证的 `--apply-weights` 自动写生产权重；禁止 performance FAIL 时 freeze
- 禁止用 T+1 已实现涨停/收益作当日入选信号（防泄漏）
- 保持 DB-first / scanner-first / runner-consume-only
- `production_ranking_change_gate.forbidden_actions` 是门禁未过时的自动运营禁令；本次用户显式授权受控工程修复，仍不得绕过测试与风险不恶化约束

**Out of scope:**
- 真实下单 / 券商接入
- 重写 28 域评分框架或更换数据源体系
- freeze paper pick / 自动 apply-weights
- 非主板扩展
- 仅诊断报告、仅 shadow 文案、仅 UI 文案
- 把 3 个 `NO_ACTION_INSUFFICIENT_EVIDENCE` 日强行当可调参证据

**Baseline (from `summary/daily_closure_latest.json`, n=12):**
| metric | production | limitup_gene_shadow_plus | low_position_shadow_plus | risk_penalty_shadow_plus |
|---|---:|---:|---:|---:|
| avg_t1 | -2.6237% | -0.3121% | -1.0576% | -1.6477% |
| win_rate | 33.33% | 50.00% | 33.33% | 33.33% |
| limitup_rate | 0% | 8.33% | 8.33% | 8.33% |
| large_loss_rate | 16.67% | 8.33% (gene replay) | n/a in shadow table | n/a |
| max_dd | -29.82% | -13.26% | -23.82% | -29.30% |

**Actionable miss days (not NO_ACTION):** 07-01 SECTOR_HEAT; 07-06 SECTOR_HEAT+RANK4-6; 07-10 LOW_POS+RANK4-6; 07-13 LOW_POS+GENE (large loss -9.95%); 07-14 LOW_POS+GENE; 07-15 LOW_POS+SECTOR+GENE; 07-16 LOW_POS+GENE; 07-17 OUTFLOW; 07-20 LOW_POS+GENE.

## Must-Haves

- MH1: 生产 formal sort 提高低位催化、涨停基因/连板 proxy、板块热度的有效权重（有界、可解释），且不新建平行排序引擎 A:I6
- MH2: 生产风控加强 failed-limitup / main_buy_outflow / high-popularity / large-loss 惩罚与硬排除，覆盖 07-13 与 07-17 模式 A:I6
- MH3: 12 日 production-path replay 相对改前 baseline 改善 avg/win/limitup；large_loss_rate 与 max_drawdown 不恶化 A:I6
- MH4: LOW_POS + LIMITUP_GENE miss 合计下降 ≥50% 或等价 gap 收窄；诊断/case book/readiness 与生产新行为对齐且不假绿 A:I6
- MH5: 相关 pytest 全绿；有改前/改后对照表与 shadow 方向收敛证明；未达 performance PASS 的项诚实列出 A:I6

### Task 1: 固化 12 日基线与改前系数快照 A:I6
- [ ] 从 `summary/daily_closure_latest.json` 提取并写入本计划已记录的 production / shadow 指标表（完成）
- [ ] 记录改前生产系数源码位置：
  - `xiaogu_forward_d1_1450_runner_v0_1.py` `ranking_basis_adjustment_components` (~5031-5084)
  - `formal_candidate_sort_key` (~5087-5146)
  - `paper_pick_risk_explanation_gate` (~5004-5028)
  - `candidate_capital_risk_profile` (~4685-4788)
  - `official_target_exclusion_reasons` (~6144-6219)
- [ ] 记录当前 boosts: low_pos*0.50, gene_proxy*0.35, limitup_proxy*0.30, news*0.50, announcement*0.45
- [ ] 记录当前 penalties: failed_limitup*0.85, outflow*0.75, popularity*0.45, high_trap*0.85, combo_fail=1.25
- [ ] 记录 formal sort 中 `continuation_gene*0.40 + limitup_proxy*0.20` 与 `ranking_adjustment*0.15` 主维
- [ ] Verification: 打开上述函数确认行号与系数与本文一致；不修改代码

### Task 2: 生产 ranking_basis 提权（low-pos / gene / sector heat）A:I6
- [ ] 在 `ranking_basis_adjustment_components` 提高可解释 boost（有上界，避免单因子主导）：
  - `low_position_catalyst_score`: 0.50 → **0.90**
  - `sector_yesterday_limitup_gene_proxy`: 0.35 → **0.75**
  - `limitup_probability_proxy`: 0.30 → **0.55**
  - 新增/加强 sector heat 吸收：对 `structured_signal_profile` 的 `sector_opportunity_score` / `main_theme_alignment_score` 增加 bounded boost（建议各 *0.25，cap 后计入 boosts，key 名可解释）
- [ ] 在 `formal_candidate_sort_key` 第二维加强 gene 吸收：
  - `continuation_gene * 0.40` → **0.70**
  - `limitup_proxy * 0.20` → **0.35**
  - 保持 eligibility/hard block 仍优先于 sort
- [ ] 不改变“无证据不 boost”语义：缺字段 / MISSING 证据不得凭空加分
- [ ] Verification: 扩展 `test_ranking_basis_penalizes_high_risk_and_rewards_confirmed_low_position_catalyst`；新增 gene/sector heat 排序单测；`pytest -q tests/test_xiaogu_a_share_forward_runner.py -k "ranking_basis or formal_sort or limitup_proxy or gene"` 通过

### Task 3: 生产 risk 惩罚与硬门加强（07-13 / 07-17）A:I6
- [ ] 在 `ranking_basis_adjustment_components` 加强 penalties：
  - `failed_limitup_risk`: 0.85 → **1.20**
  - `main_buy_outflow_pressure`: 0.75 → **1.15**
  - `popularity_crowding_risk`: 0.45 → **0.70**
  - `high_popularity_trap_risk`: 0.85 → **1.10**
  - `high_popularity_trap_combo_penalty`: 1.25 → **1.60**（仅 risk_gate FAIL）
- [ ] 在 `formal_candidate_sort_key` 中放大 capital risk 的排序抑制：第二维 `-capital_risk_penalty` 前对 penalty 乘 **1.25**（或等价显式项），不削弱其它 hard gate
- [ ] 在 `paper_pick_risk_explanation_gate` / `official_target_exclusion_reasons` 对齐：
  - 保持 triple-risk 无强反证 → FAIL → 排除
  - 新增/加强：`failed_limitup + main_buy_outflow` 且无强催化/gene 反证时，加入可解释 exclusion 或更高 soft→hard 路径（覆盖 07-13 类大亏模式；不得用次日收益）
  - 07-17 outflow：确保 `main_buy_outflow_pressure` 在无催化时足以压低 formal sort，不被弱主题虚高覆盖
- [ ] 禁止放宽任何现有 hard exclusion 条件
- [ ] Verification: 现有 `test_limitup_proxy_and_risk_gate_do_not_promote_unexplained_triple_risk` 仍绿；新增 07-13 模式构造样例（炸板+流出+高人气无反证 → 被降权/排除）；outflow 惩罚单测；`pytest -q ... -k "risk_gate or capital_risk or exclusion or high_popularity"` 通过

### Task 4: 诊断 / case book / readiness 与生产对齐 A:I6
- [ ] 核对 miss 类型命名一致性：case book 使用 `LOW_POSITION_CATALYST_UNDERWEIGHTED`，`_ranking_miss_types` 使用 `LOW_POSITION_SETUP_UNDERWEIGHTED` 等；统一到生产诊断可读集合，避免“诊断说一套、代码另一套”
- [ ] `_primary_fix_direction` 在生产已吸收 shadow 方向后，应反映 production fix 方向（例如 `increase_production_weight_for_low_position_catalyst` / gene / risk），而不是永远 `increase_shadow_weight_*`
- [ ] `strategy_readiness` / performance gate 阈值不调低；若仍 FAIL，状态必须诚实列出剩余缺口
- [ ] 更新相关 backtest 诊断单测（loss attribution / case book / readiness）以匹配新文案与行为
- [ ] Verification: `pytest -q tests/test_xiaogu_a_share_forward_runner.py -k "case_book or loss_attribution or readiness or production_ranking or miss"` 通过

### Task 5: 全量验证与证据包 A:I6
- [ ] 跑定向单测集合，再跑与 ranking/risk/shadow/performance 相关的 forward runner 子集；失败则修到绿
- [ ] 用现有 backtest/closure 路径对 12 日可比样本做 production-path 对照（改后 formal sort 选谁 vs 改前 baseline / vs limitup_gene_shadow_plus）
  - 指标：avg_t1, win_rate, limitup_rate, large_loss_rate, max_drawdown
  - miss 分布：LOW_POS / LIMITUP_GENE 次数与 return_gap
- [ ] 若环境无法连真实 DB 重刷 full daily_closure，则：
  1. 用 fixture/ledger 或 `runner_chain_replay_*` / cohort 数据做 offline production sort replay
  2. 明确写出限制与已验证范围
- [ ] 产出对照摘要写入 `summary/` 仅当已有同类产物习惯且有运行价值；否则在本会话最终回复给出完整对照表（优先不爆炸新文档）
- [ ] 明确：7/21 官方 603115 T+1 pending，不阻塞本轮代码完成；7/22 回填后滚动重刷
- [ ] Verification:
  - 相关 pytest 全绿
  - large_loss_rate 与 max_drawdown 相对 baseline 不恶化
  - avg/win/limitup 至少一项实质改善且综合不劣于“只加强 risk 却失去弹性”
  - LOW_POS+GENE miss 合计下降目标 ≥50% 或 gap 明显收窄
  - 无 parallel sort v2 / 无放宽 hard gate / 无 freeze / 无自动 apply-weights

## Decision Log
| decision | choice | why |
|---|---|---|
| Scope | Full production structural fix, not diagnose-only | User A:I6 explicit |
| Sort approach | Retune existing ranking_basis + formal sort key coefficients | NN5 / FN5 |
| Shadow use | Directional evidence only, translate into formal coefficients | HC2 |
| Gate policy | Strengthen risk, never relax triple-risk | NN3 |
| Freeze/apply-weights | Still forbidden | NN4 |
| NO_ACTION days | Do not force-fit | FN7 |
| Authorization vs production_ranking_change_gate | User-authorized engineering change under tests; not silent ops unlock | HC3 |

## Proof map
| Proof | Task |
|---|---|
| PR1 改前/改后 12 日 production replay 对照 | Task 1 + 5 |
| PR2 生产 vs limitup_gene_shadow 方向收敛 | Task 2 + 5 |
| PR3 07-13 large-loss 拦截/降权单测 | Task 3 |
| PR4 pytest 清单 | Task 2-5 |
| PR5 未达 PASS 剩余项诚实列出 | Task 4-5 |
