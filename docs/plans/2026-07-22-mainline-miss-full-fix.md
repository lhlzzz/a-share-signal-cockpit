# 主线 Miss 全量修复任务包（7/21 海星 / 矿能源 + @sszcw 方法对象化）

**Normalized Goal:** 修掉 7/21 暴露的 live 主线出票缺陷（M1–M6），并把 @sszcw 可工程化方法落成**只读诊断字段 + 日更 miss taxonomy**；生产改动只针对可解释 gate 泄漏与结构排序，**不跟博主改权重、不 T+1 反推入选**。

**Goal:** 官方 PAPER_PICK 不再由「pure proxy 涨停理由 + partial aux 例外 + 贴价边缘」成为唯一幸存者；同时系统能每日解释「领导链 / 阶段 / 指数 regime / 官方主线位 / clean TOP / miss 类型」，且 7/21 类 case 可被单测与 re-decision 复现。

**Evidence source of truth:**
- `summary/2026-07-21_mainline_miss_case.md`（§0–§11，含 §10.6 字段映射）
- runtime: `data/forward_raw_runtime/2026-07-21/144245/runtime_decision_context.json`
- scan: `data/live_scan/2026-07-21/eastmoney_scan_afternoon/flow_industry.jsonl`
- clean: `summary/2026-07-21_clean_factor_rerank.json`
- 既有主线诊断骨架: `xiaogu_backtest_v0_1.py` `_mainline_*`
- 既有生产门: `xiaogu_forward_d1_1450_runner_v0_1.py` `paper_pick_eligibility_profile` / `formal_candidate_sort_key` / `official_target_exclusion_reasons`
- proxy 来源: `scrapy_scanner/runner_v2.py` `limitup_pool_sector_proxy`

---

## 0. 问题清单 → 修复对象（全部必做，不得静默丢项）

| ID | Miss / 缺陷 | 现象（7/21） | 修复层级 | 生产是否改行为 |
|---|---|---|---|---|
| **M2** | `PROXY_LIMITUP_REASON_HARD_PASS` | 元件 sector proxy 把 `limitup_reason_strength` 抬到 ≥0.60 过闸 | **P0 生产 hard** | **是** |
| **M3** | `PARTIAL_AUX_EXCEPTION_LEAK` | `strong_sector_theme_partial_aux_exception` 在贴价/无个股催化时放行 PARTIAL aux | **P0 生产 hard** | **是** |
| **M1** | `GATE_SURVIVOR_NOT_MAINLINE_CORE` | 唯一 full gate 通过者 = 官方，非领导链核心 | **P1 生产排序 + 诊断** | **是（有界）** |
| **M4** | `CORE_OR_BETTER_STRUCTURE_BLOCKED_BY_QUALIFIED` | 兖矿/富联结构更好但 QUALIFIED/过热卡住 | **P1 诊断 + 有界 soft 吸收** | **有限**（不降 hard 安全阀） |
| **M5** | `HOLLOW_THEME_TAGS_POLLUTION` | clean tags 全局同套 → alignment 不可信 | **P1 生产/诊断** | **是（标签/对齐）** |
| **M6** | `MAINLINE_OBJECT_IS_LAYER_NOT_THEME` | L0/L7 当 theme，缺可交易领导链对象 | **P0 诊断对象** | **否**（对象先 shadow/report） |
| **S1–S5** | §10.6 博主方法字段 | 缺涨跌榜轮动/阶段/指数/鱼尾风险 | **P0 只读诊断** | **否** |
| **D1** | 日更 miss taxonomy | 7/21 手写 case，无自动日更 | **P0 报告脚本** | **否** |
| **M7** | `T1_FRAGILITY_OBSERVED` | T+1 验证脆弱性 | **观测 only** | **禁止**进 eligibility/sort |

**四缺口边界（不得混淆）：** formal/L1 re-decision 与 gene 主维已有计划；本包修的是 **live 主线位置 + gate 泄漏 + 领导链对象**，不替代四缺口收口。

---

## 1. Constraints（硬约束）

1. **禁止**把生产权重改成“跟 @sszcw / 跟油气 / 跟煤炭”。
2. **禁止**用 T+1 收益/涨停回写当日 eligibility / sort / exclusion。
3. **禁止**新建平行 sort engine / runner / pipeline（`formal_candidate_sort_key_v2` 等）。
4. **禁止**为冲 PASS 放宽现有 hard gate 总安全阀（OUTFLOW / triple-risk / regulatory hard 等只能保持或加强）。
5. **禁止** freeze PAPER_PICK / 自动 `--apply-weights`。
6. **修改现有文件优先**：runner / backtest / scanner / tests；新文件仅允许 1 个日更诊断 CLI（若无法挂入现有 CLI）+ 本计划 + 必要 summary JSON。
7. 生产改动必须 **可解释失败原因 → 最小补丁 → 单测 + 7/21 re-decision 对照**。
8. 诊断字段即使未来进生产，也必须先 **shadow 日更 ≥ N 日可复现**（本包 N 默认 5 个可比交易日；不足则诚实 PENDING）。
9. Performance 阈值不调低；诚实 FAIL 可接受。
10. 兖矿 T+1 好 **不**证明 7/21 应认煤炭为主线；修复目标是 **元件边缘+proxy+例外** 不再成为官方，以及 **结构更好的票可解释地参与 formal 竞争**，不是强行出兖矿。

---

## 2. Out of scope

- 真实下单 / 券商
- 重写 28 域评分体系 / 换数据源
- 非主板扩展
- 复制博主荐股/目标价
- 仅因 1 日样本上调资源/下调电子生产权重
- 把 clean factor 直接替换 production formal sort
- 删除 legacy scanner（另有删除条件记忆）

---

## 3. 成功标准（全部要证据）

### SC0 全项证据
- `all_miss_ids_have_evidence=true`：M1–M6 + S1–S5 + D1 每项有代码/报告/测试其一以上证据。

### SC1 生产硬泄漏关闭（M2/M3）
- pure `limitup_pool_sector_proxy` **不得单独**使 `limitup_reason_strength>=0.60` 充当 buy_confirmation / L2 exemption。
- `strong_sector_theme_partial_aux_exception` 在以下任一成立时 **禁用**：
  - `price >= 65`（贴 70 上限邻域；7/21 海星 69.79）
  - chase HIGH / `limitup_quality_block_reason` 非空路径
  - `limitup_reason_status == PROXY` 且无 direct 个股涨停理由
  - `main_theme_core_score==0` 且 `news_catalyst_strength==0` 且无 announcement
- 单测：海星型 fixture → **不可**因 proxy+partial exception 成为 eligible 官方。

### SC2 领导链对象存在（M6 + S1–S5）
- 日更产物含：
  - `sector_return_leaders` / `sector_return_laggards`（涨跌幅 TOP，不只净流入）
  - `leader_chain_top3`（可交易主题名，**禁止** L0_FULL_UNIVERSE / L7_INTRADAY_ALERT 当 theme）
  - `mainline_stage` ∈ {MAIN_UP, BOUNCE, CLIMAX, NO_MAIN, ROTATION, UNKNOWN}
  - `index_regime` ∈ {STABLE, FRAGILE, DOWNTREND, UNKNOWN}
  - `resource_futures_chain_heat`（有界 0–1 或等级）
  - `climax_chase_risk`（有界）
- 全部 `selected_for_production=false` 直至另开生产吸收计划。

### SC3 Miss taxonomy 日更（D1 + M1 标签）
- 每日可出：扫描领导链 / 官方主线位标签 / clean TOP3 / primary miss bucket（扩展 taxonomy）
- 扩展 primary/secondary 标签至少含：
  - `GATE_SURVIVOR_NOT_MAINLINE_CORE`
  - `PROXY_LIMITUP_REASON_HARD_PASS`
  - `PARTIAL_AUX_EXCEPTION_LEAK`
  - `CORE_OR_BETTER_STRUCTURE_BLOCKED_BY_QUALIFIED`
  - `HOLLOW_THEME_TAGS_POLLUTION`
  - `EDGE_FOLLOWER` / `PROXY_ONLY_LIMITUP` / `NEAR_PRICE_CAP`（位置质量标签）
- 7/21 回放标签与 miss case §4 一致（允许 secondary 多标签）。

### SC4 结构竞争（M1/M4 有界）
- formal 路径上：有 **direct catalyst / continuation_gene / 昨涨停基因** 的结构票，不得被 hollow 主题高分 + proxy 确认无条件碾压。
- 不要求生产必须选出兖矿；要求：
  - 海星型（proxy-only + partial aux + mt=0 + 贴价）正式路径 **出局或大幅降权**
  - 富联类（更贴 L1）与兖矿类（高 snc/cont）在 **eligibility 可解释**；若仍 NO_PICK，blockers 必须区分过热/分不足/缺 VEI，并进入 miss taxonomy，而不是 silent。
- re-decision 对照：7/21 官方不再是海星 **或** 若环境缺数据无法重算选票，则 fixture 单测证明 gate 关闭 + 报告层标 `STRUCTURE_MISS`。

### SC5 标签可信（M5）
- 候选 theme tags 不得再是「全池同一套全局标签」污染 alignment；缺失时标 `THEME_TAGS_HOLLOW` / alignment 降权，而不是假高对齐。

### SC6 回归与风险
- 相关 pytest 全绿。
- large_loss_rate / max_dd 相对当前 structural fix 基线 **不恶化**（可用 offline formal / redecision 样本；不足则诚实 LIMIT）。
- 无 T+1 字段进入 eligibility/sort。
- 无 parallel v2 / freeze / auto-weights。

---

## 4. 架构决策（Decision Log）

| decision | choice | why |
|---|---|---|
| 修复顺序 | **P0 gate 泄漏 → P0 诊断对象/日更 → P1 有界排序/标签 → 验收** | 先止血（海星型），再对象化，再谈主线竞争力 |
| M2 策略 | proxy 证据 **降级**：可进 soft/诊断，不可单独 hard-pass buy_confirm / L2 | 根因是证据质量，不是阈值数字 |
| M3 策略 | partial aux exception **加护栏**，不整段删除 | 强主题+真 PASS 研究仍可能需要；删光会伤合法路径 |
| M1 策略 | 出票仍 eligibility∩exclusion∩formal sort；增加 **edge/proxy 惩罚 + 领导链 alignment soft** | 不改成“主题第一”黑箱 |
| M4 策略 | **不**为兖矿放宽 score&lt;70 hard；加强 structure 在 formal 第二维可见性 + 诊断 | 安全阀方向可纠，但不能用 T+1 证明应放行 |
| M6/S* | 新对象只进 backtest/日报，**不进** runner eligibility | miss case 与博主映射明确 shadow first |
| 博主 | 学方法对象，不学结论权重 | §10.4/§11 |
| 文件策略 | 改 runner/backtest/scanner/tests；日报挂 `xiaogu_backtest_v0_1.py` CLI 优先 | NN 修改优先、禁平行系统 |
| 与四缺口 | 并行不替代；本包不宣称 performance PASS | 边界诚实 |

---

## 5. 代码落点（优先修改）

| 职责 | 文件 | 符号 |
|---|---|---|
| Proxy 证据生成 | `scrapy_scanner/runner_v2.py` | sector_limitup_proxy / `limitup_reason_status` |
| 强度是否把 proxy 当 hard | `xiaogu_forward_d1_1450_runner_v0_1.py` | `paper_pick_eligibility_profile`（buy_confirmation / L2 / positive_conditions） |
| Partial aux 例外 | 同上 | `strong_sector_theme_partial_aux_exception` ~5914；`official_target_exclusion_reasons` ~6224 |
| 涨停质量/追高 | 同上 | `limitup_quality_block_reason` |
| 有界排序 | 同上 | `ranking_basis_adjustment_components` / `formal_candidate_sort_key` |
| 官方选择 | 同上 | `attach_paper_pick_eligibility` / `official_pick_priority` |
| 领导链/日报/shadow | `xiaogu_backtest_v0_1.py` | `_daily_mainlines` / `_mainline_diagnostics` / `_mainline_case_book` / CLI |
| 单测 | `tests/test_xiaogu_a_share_forward_runner.py` | 扩 existing mainline / eligibility / ranking 测 |
| 可选薄 CLI | 仅当无法挂入 backtest CLI 时：`scripts/` 下 **一个** `mainline_miss_daily_report.py` | 调用 backtest 函数，不复制逻辑 |

---

## 6. 分阶段任务

### Phase P0-A — 关闭 proxy hard-pass（M2）

#### Task A1: 定义证据等级
- [ ] 在 runner（或 scanner 输出已有 status 上统一）固化：
  - `DIRECT`：个股 limitup_pool 非 proxy
  - `PROXY`：`limitup_pool_sector_proxy` / 仅板块理由
  - `GENE`：昨涨停/续涨基因代理
  - `MISSING`
- [ ] 字段：`limitup_reason_status` 已有；确保 eligibility 读取并写入 `signals.limitup_reason_evidence_class`
- [ ] Verification: 海星型 fixture status=PROXY；直连涨停票=DIRECT

#### Task A2: buy_confirmation / L2 不吃 pure proxy
- [ ] 在 `paper_pick_eligibility_profile`：
  - `limitup_reason_strength>=threshold` 计入 **buy_confirmation_hits / L2 exemption** 时，要求 evidence class ∈ {DIRECT} **或**（PROXY 且另有 seal/order_book/direct news/announcement 之一 ≥ 阈值）
  - pure PROXY：可保留 soft 分/诊断，但 **不得**单独 append `limitup_reason_strength>=0.60` 为硬确认
- [ ] Verification: 单测 `test_proxy_only_limitup_reason_cannot_hard_pass_buy_confirmation`；`test_direct_limitup_reason_still_confirms`

#### Task A3: scanner 输出不夸大
- [ ] 确认 `runner_v2` 在 only sector proxy 时 `limitup_reason_status='PROXY'`（已有则不改）
- [ ] 若 strength 在下游被当成真理由，补 `proxy: true` 传播到 structured profile
- [ ] Verification: 单元或集成断言 proxy 标记不丢

---

### Phase P0-B — 收紧 partial aux 例外（M3）

#### Task B1: exception 护栏
- [ ] 修改 `strong_sector_theme_partial_aux_exception` 条件，增加 **全部满足才允许** 的否决项：
  1. 非 pure PROXY 涨停理由（或有 direct catalyst ≥0.75）
  2. `price` 未贴上限邻域：默认 `price is None or price < 65`
  3. 非 chase HIGH：`limitup_quality_block_reason` 为空 **或** 不在 mid/high 贴价无催化
  4. 保持原有：main board + PARTIAL aux + research PARTIAL/PASS + sector/theme 强 + 无 near_limit/regulatory/opportunity/capital risk_codes
- [ ] Verification: 更新 `test_mainboard_auxiliary_partial_strong_theme_exception_can_be_official`：合法强主题仍可；新增海星型 **不可** exception

#### Task B2: exclusion 对齐
- [ ] `official_target_exclusion_reasons` 中 `partial_aux_exception` 跳过逻辑与 eligibility **同一护栏**（禁止 eligibility 放行、exclusion 仍跳过不一致）
- [ ] Verification: 单测 eligibility.signals 与 exclusion 列表一致

#### Task B3: 7/21 海星回归夹具
- [ ] 用 miss case 字段构造最小 row：mt=0, ma=0, proxy evidence×3, price≈69.8, PARTIAL aux, sector_opp 有, score 73–77
- [ ] 断言：`eligible is False` **或** exclusion 非空导致不可 official
- [ ] Verification: pytest 红→绿

---

### Phase P0-C — 领导链对象 + §10.6 字段（M6 + S1–S5）

#### Task C1: 从已落盘 scan 抽涨跌领导
- [ ] 在 `xiaogu_backtest_v0_1.py` 增加（或扩展 `_daily_mainlines`）：
  - 输入优先：`data/live_scan/<date>/**/flow_industry.jsonl`（及概念若有）
  - 输出 `sector_return_leaders` / `sector_return_laggards`（按涨跌幅，附净流入）
  - 无文件 → `status=MAINLINE_DATA_PARTIAL`，禁止编造
- [ ] Verification: 7/21 fixture 或真实路径：电子/半导体在 leaders；煤炭不在 leaders

#### Task C2: `leader_chain_top3` 可交易主题
- [ ] 合并：涨跌 leaders + 资金 leaders + 候选聚合；**过滤** layer 名 `L0_*` `L7_*` `FULL_UNIVERSE` 等
- [ ] 层级粗标：L1/L2/L3（诊断用）
- [ ] Verification: 输出主题为行业/概念中文名；单测过滤 layer

#### Task C3: `mainline_stage` 启发式（shadow）
- [ ] 规则粗版（可解释、有界，允许 UNKNOWN）：
  - 多日切换且涨跌对立 → ROTATION
  - 高涨幅+大流入+高拥挤 → CLIMAX
  - 低位修复特征 → BOUNCE
  - 资金涨幅同向持续 → MAIN_UP
  - 弱且分散 → NO_MAIN
- [ ] **禁止**用次日收益定义 stage
- [ ] Verification: 合成输入覆盖各枚举；7/20–7/21 可为 ROTATION/UNKNOWN 诚实值

#### Task D4: `index_regime`（shadow）
- [ ] 用已有 market_context / 指数字段若落盘则算；否则 UNKNOWN + 原因
- [ ] 不因 UNKNOWN 否决生产
- [ ] Verification: 缺数据 → UNKNOWN

#### Task C5: `resource_futures_chain_heat` + `climax_chase_risk`
- [ ] resource：有色/油气/煤炭/黄金等 leaders 热度聚合 0–1
- [ ] climax_chase：高 position + proxy-only + 无 gene + 贴价 → 高风险分
- [ ] Verification: 海星型 climax_chase_risk 高；资源 leaders 日 heat 高

#### Task C6: 挂入报告
- [ ] `mainline_diagnostic_gate` / 新 section `leader_chain_diagnostic` 输出上述字段
- [ ] `selected_for_production=false`
- [ ] Verification: db-cohort 或 offline 报告 JSON 含键

---

### Phase P0-D — Miss taxonomy 日更（D1）

#### Task D1: 扩展 bucket / 标签
- [ ] 在 `_mainline_diagnostics` 或并列函数输出：
  - primary miss（兼容旧四桶）
  - secondary tags：M1–M6 名称 + EDGE_FOLLOWER 等
- [ ] 规则示例（可机读）：
  - 官方 eligible 唯一 + alignment 低 + 非 top3 leader → `GATE_SURVIVOR_NOT_MAINLINE_CORE`
  - 官方 limitup PROXY only → `PROXY_LIMITUP_REASON_HARD_PASS`
  - 官方靠 partial aux exception → `PARTIAL_AUX_EXCEPTION_LEAK`
  - clean/formal 结构更强但 QUALIFIED_FALSE → `CORE_OR_BETTER_STRUCTURE_BLOCKED_BY_QUALIFIED`
  - tags 全局相同 → `HOLLOW_THEME_TAGS_POLLUTION`
- [ ] Verification: 7/21 回放 primary/secondary 与 miss case 对齐

#### Task D2: 日更入口
- [ ] 优先扩展：`python3 xiaogu_backtest_v0_1.py --mainline-miss-daily-report --date YYYY-MM-DD`
- [ ] 输出：`summary/mainline_miss_daily_YYYY-MM-DD.json`（或项目既有 summary 命名习惯）
- [ ] 内容最低集：leader_chain_top3、leaders/laggards、stage、index_regime、official 位标签、clean/formal top3（若可得）、miss tags、`production_mutation_allowed=false`
- [ ] Verification: 对 2026-07-21 跑通；无网可跑（只读本地 scan/runtime/db）

#### Task D3: 禁止进生产
- [ ] 报告与函数 docstring 写明 shadow/diagnostic only
- [ ] Verification: 无 runner 调用这些字段改 eligible

---

### Phase P1-E — 有界生产排序 / 标签（M1/M4/M5）

> 仅在 P0-A/B 单测绿后进行。仍禁止博主权重、禁止 T+1 特征。

#### Task E1: hollow theme 惩罚落地/加强
- [ ] 扩展 `ranking_basis_adjustment_components` 已有 `hollow_theme_core_without_catalyst`：
  - 当 tags 空洞或全局污染信号 → penalty
  - alignment/core 无 catalyst 时不可虚高 primary
- [ ] Verification: 单测 hollow 票 formal key 低于有 snc/cont 票

#### Task E2: edge follower + proxy-only 排序抑制
- [ ] 生产 soft（非必须 hard block）：
  - PROXY-only + mt/ma≈0 + 近 price cap → 增加 penalty 或 exclusion soft→hard 仅当同时 partial aux 试图 exception（已与 B1 协同）
- [ ] Verification: 海星型 formal 不胜有 direct catalyst 的电子链中军 fixture

#### Task E3: 结构票可见性（M4 有界）
- [ ] **不**降低 score&lt;70 全局 hard
- [ ] 确保 continuation_gene / sector_news_catalyst / 昨涨停 gene 在 formal 第二维已有系数下 **对 DIRECT 结构票生效**；缺字段不 boost
- [ ] 对 QUALIFIED_FALSE 结构票写清 `why_not_official_pick` 机读码
- [ ] Verification: 兖矿型 fixture：仍可能 NO_PICK，但 miss tag=M4；不可 silent

#### Task E4: theme tags 去污染（M5）
- [ ] 定位 clean/全局 tags 写入点；候选级 tags 优先个股/板块命中，禁止无差别广播全池同一套
- [ ] 若短期无法修数据源：alignment 计算时检测「全池 Jaccard 过高」→ 标记 hollow 并降权
- [ ] Verification: 单测全池同 tags → hollow 标记

#### Task E5: 7/21 re-decision 对照
- [ ] 跑 production-path redecision（若 DB/snapshot 可用）
- [ ] 写 `summary/2026-07-22_mainline_miss_redecision_smoke.json`（或追加既有 compare 的 notes）
- [ ] 期望：官方路径不再选出海星型 **或** 明确 `gate_closed_on_fixture_only` + 原因
- [ ] Verification: JSON 含 validation_tier 声明

---

### Phase P1-F — Shadow 规则记分（不拦截生产）

#### Task F1: shadow 规则
- [ ] `EDGE_FOLLOWER + proxy_only` → shadow 禁止官方分
- [ ] `partial_aux_exception` 在 price≥65 或 chase HIGH → shadow 禁用（应已与生产 B1 对齐；shadow 仍保留历史对照）
- [ ] clean TOP1 若 cont≥0.5 & snc≥0.5 且官方 mt=0 → `STRUCTURE_MISS`
- [ ] Verification: 报告字段存在；`selected_for_production=false`

#### Task F2: 与 mainline_shadow_replay 合流
- [ ] 不新建平行 replay 引擎；扩展现有 `_mainline_shadow_replay` 变体列表最多 +1（`leader_chain_quality_shadow`）
- [ ] Verification: 旧 variant 名仍在；新 variant 可选

---

### Phase P2-G — 验证收口

#### Task G1: 定向 pytest
```text
python3 -m pytest tests/test_xiaogu_a_share_forward_runner.py -q -k \
  "proxy or partial_aux or mainline or ranking_basis or formal_sort or eligibility or exclusion or limitup_reason"
```
- [ ] 全绿

#### Task G2: 7/21 诊断回放
- [ ] 日更报告 primary/secondary 匹配 miss case
- [ ] 领导链非煤炭；官方位 EDGE_FOLLOWER

#### Task G3: 风险不恶化声明
- [ ] 12 日或可得样本：large_loss / max_dd 不差于改前 structural baseline
- [ ] 不足样本 → `LIMITATION` 字段诚实写

#### Task G4: 完成清单
- [ ] SC0–SC6 逐条 PASS/PENDING 表
- [ ] 明确仍 **不** 跟博主改权重、**不** T+1 入选
- [ ] 明确 performance 是否仍 FAIL（诚实）

---

## 7. Must-Haves

- MH0: M1–M6 + S1–S5 + D1 全有证据，不得静默丢项
- MH1: M2/M3 生产硬泄漏关闭 + 海星型单测
- MH2: leader_chain + §10.6 五字段日更只读
- MH3: miss taxonomy 日更且 7/21 可复现
- MH4: M1/M4/M5 有界生产补丁或等价 formal 抑制 + 解释字段
- MH5: pytest 绿；无 T+1 泄漏；无平行 v2；无 freeze/auto-weights；不跟博主改权重

---

## 8. Proof map

| Proof | Tasks |
|---|---|
| PR-M2 proxy 不能 hard-pass | A1–A3 |
| PR-M3 partial aux 护栏 | B1–B3 |
| PR-M6/S 领导链与五字段 | C1–C6 |
| PR-D1 日更 taxonomy | D1–D3 |
| PR-M1/M4/M5 有界生产 | E1–E5 |
| PR-shadow | F1–F2 |
| PR-regression | G1–G4 |

---

## 9. 执行顺序（强制）

```text
A (M2) → B (M3) → C (对象) → D (日更) → E (有界排序/标签) → F (shadow 记分) → G (收口)
```

- A/B 未绿 **禁止** 开 E 的生产排序大改。
- C/D 可与 A/B 后半并行，但不得阻塞 A/B 止血。
- 任一项证据缺失 → 总状态 `INCOMPLETE`，不得宣称「主线 miss 已修完」。

---

## 10. 验收话术模板（完成后填写）

| 项 | 结果 |
|---|---|
| 海星型 proxy+partial 是否仍可 official | **否**（fixture：`eligible=False` 或 exclusion 含 `mainboard_auxiliary_evidence_status_not_PASS`；proxy soft-only） |
| 7/21 日更 miss tags | primary=`GATE_SURVIVOR_NOT_MAINLINE_CORE`；secondary 含 M2/M3/M1/M4/`STRUCTURE_MISS`；position=`EDGE_FOLLOWER/NEAR_PRICE_CAP/PROXY_ONLY_LIMITUP` |
| leader_chain_top3（禁 L0/L7） | `半导体/电子/科技风格`（非 L0/L7） |
| §10.6 五字段 | stage=`CLIMAX`，index=`FRAGILE`，resource_heat=`0.0`，climax_chase=`1.0`，leaders/laggards 已出 |
| 兖矿/富联解释（M4） | `why_not_official_pick_codes`：`STRUCTURE_CANDIDATE_PRESENT` + `SCORE_BELOW_70_HARD`；日更 `CORE_OR_BETTER_STRUCTURE_BLOCKED_BY_QUALIFIED` / `STRUCTURE_MISS` |
| hollow tags（M5） | 全池同 tags → `theme_tags_hollow` + penalty `hollow_theme_tags_pollution`；alignment boost 清零 |
| pytest | 定向 suite 33 passed（含 proxy/partial/haixing/mainline/hollow/edge/structure/shadow） |
| T+1 泄漏检查 | AST 扫描 eligibility/sort 路径 **无** `t1_return` 读取 |
| 是否跟博主改权重 | **否** |
| performance | **LIMITATION**（未重算 multi-day large_loss/max_dd；见 smoke JSON） |

SC0–SC6:

| SC | 结果 | 证据 |
|---|---|---|
| SC0 全项证据 | PASS | M1–M6 + S* + D1 均有代码/报告/测试 |
| SC1 M2/M3 hard | PASS | proxy hard-pass 关闭；partial aux 贴价/proxy/无催化护栏；海星 fixture |
| SC2 领导链对象 | PASS | `build_leader_chain_diagnostic` + 日更 JSON，`selected_for_production=false` |
| SC3 miss taxonomy 日更 | PASS | CLI `--mainline-miss-daily-report` + `summary/mainline_miss_daily_2026-07-21.json` |
| SC4 结构竞争有界 | PASS | edge/proxy soft suppress；structure 第二维 gene；M4 机读码；不降 score&lt;70 |
| SC5 hollow tags | PASS | pool hollow 检测 + ranking penalty |
| SC6 回归与风险 | PASS/LIMIT | pytest 绿；无 T+1/v2/freeze；performance **LIMITATION** |

---

## 11. 明确不做的“假修复”

1. 把官方改成兖矿只因 T+1 好  
2. 扫描主线改认煤炭  
3. 删除所有 partial aux 例外而不留合法强主题路径  
4. 新建 `mainline_engine_v2.py`  
5. 为 performance 假绿降阈值  

---

## 12. 实施时假设（若违例需停）

1. 7/21 scan/runtime 本地仍可读；若缺失，fixture 必须覆盖海星/proxy/partial。  
2. `limitup_reason_status` 已由 scanner 区分 PROXY；若线上历史票缺失，runner 需从 evidence `proxy:true` 推断。  
3. price cap 邻域阈值 65 来自 miss case 贴 70 场景；若项目已有统一 price-cap 常数则 **复用** 不双轨。  
4. index 成分若无落盘，index_regime=UNKNOWN 可接受，不阻塞 P0。  

---

**状态:** EXECUTED_WITH_EVIDENCE（2026-07-22）  
**产物:**
- 生产：`xiaogu_forward_d1_1450_runner_v0_1.py`（M2/M3 hard + M1/M4/M5 soft）
- 诊断：`xiaogu_backtest_v0_1.py`（leader_chain + miss taxonomy + shadow variant + CLI）
- 测试：`tests/test_xiaogu_a_share_forward_runner.py`
- 日更：`summary/mainline_miss_daily_2026-07-21.json`
- smoke：`summary/2026-07-22_mainline_miss_redecision_smoke.json`

**下一步（非本包阻塞）:** 多日 production-path redecision 重跑以填 performance 表；hollow tags 数据源去广播若仍污染。
