# 全库出票链路瘦身：DELETE + EXTRACT

**Goal:** 以「出票链路 + 生命闭环」为唯一标准，对 xiaogu 全库做作用审计；对闭环无用的删除（git 留史），有用但堆在万行文件里的拆到单一职责模块，降低体量与冗余。  
**Constraints:** 不破 formal / THEME_LEXICON / hard gate；不平行 `runner_v3`；删前 pipeline+import+tests 交叉验证；每批有 pytest/import 证据。  
**Out of scope:** 改出票算法；实盘；`external_research/` 大清；`archive/`；tests 万行重写；运维 T+1 回填（并行债）。  
**Intent packet:** `.plan-enforcer/discuss.md`（全库出票链路瘦身）  
**Prior subset (done, not sufficient alone):** `docs/plans/2026-07-23-live-chain-de-cdp-responsibility-split.md` T1–T24

---

## Must-Haves

- MH1: 可复现的全库分类 inventory（文件级 + 巨型函数级），证据绑定 pipeline/import/test。 A:I10
- MH2: 至少一批 **DEAD 删除**落地且测试绿（硬死代码，无闭环引用）。 A:I10
- MH3: forward **再拆 ≥1 职责面**（优先 eligibility 或 io_bundle），行为保持。 A:I10
- MH4: web_tabs 生产评分面与 CDP live 死路径分离（实现抽出或删 CDP 块）。 A:I10
- MH5: 前后 LOC 对比 + reindex + AgentMemory/Obsidian 收口。 A:I10

---

## Baseline Inventory（审计快照 2026-07-23）

### 生命闭环（唯一标准）

```
ensure_database
  → scrapy_scanner/runner_v2.py          # live scan (HTTP default)
  → xiaogu_social_sentiment.py           # soft
  → scripts/xiaogu_sszcw_market_context  # soft
  → xiaogu_forward_d1_1450_runner_v0_1   # PAPER_PICK / NO_PICK
  → recorder / ledger / picks DB
  → scripts/xiaogu_return_backfill + filler
  → scripts/xiaogu_data_retention
  → scripts/xiaogu_safe_self_evolve
scheduler 另挂: filler, signal_effectiveness, ensure_database, runner_v2
T1_VALIDATION/closure: xiaogu_backtest_v0_1 (daily_pipeline 内嵌)
```

入口证据：`daily_pipeline.sh`、`xiaogu_scheduler.py`。

### 体量（python，含 tests/external 全树 ~105k LOC / 261 files）

| LOC | Path | 分类 | 证据 |
|----:|------|------|------|
| 10691 | `xiaogu_forward_d1_1450_runner_v0_1.py` | CORE_LIFECYCLE（须 EXTRACT） | pipeline + tests |
| 10501 | `tests/test_xiaogu_a_share_forward_runner.py` | TEST（随拆分改 import） | pytest |
| 7789 | `xiaogu_eastmoney_web_tabs_scan_v0_1.py` | SCORING_LIB + DEAD_CDP | runner_v2 仅 4 符号；scoring re-export 6 符号 |
| 4543 | `scrapy_scanner/runner_v2.py` | CORE_LIFECYCLE | pipeline/scheduler |
| 4458 | `xiaogu_backtest_v0_1.py` | OPS_LIFECYCLE | daily_pipeline closure |
| 2508 | `xiaogu_signal_effectiveness_v0_1.py` | OPS_LIFECYCLE | scheduler |
| 1871 | `xiaogu_v2_1_six_repo_real_integrated.py` | RESEARCH_AUX | 经 six_repo 集成 |
| 1708 | `xiaogu_native_repo_runtime_v0_1.py` | RESEARCH_AUX | six_repo 用 |
| 1665 | `xiaogu_db.py` | CORE_LIFECYCLE | 广泛 import |
| 1050 | `scripts/xiaogu_return_backfill.py` | CORE_LIFECYCLE | pipeline |
| 931 | `xiaogu_forward_judge_scoreboard_v0_1.py` | OPS_AUX | web_tabs 引用；非 daily 硬路径 |
| 891 | `xiaogu_forward_result_filler_v0_1.py` | CORE_LIFECYCLE | pipeline/scheduler |
| 554 | `xiaogu_social_sentiment.py` | CORE_SOFT | pipeline |
| 227 | `xiaogu_forward_gates.py` | CORE（已 EXTRACT） | forward + tests |
| 35 | `xiaogu_scanner_scoring.py` | 薄 re-export（须变实） | runner/forward |

### DEAD / ORPHAN 候选（删前再核一次）

| LOC | Path | 建议 | 证据摘要 |
|----:|------|------|----------|
| ~530 | `scrapy_scanner/spiders/*.py` + items/settings | **DELETE** | runner_v2 无 `import scrapy` / 无 CrawlerProcess；旧 `runner.py` 已删 |
| 154 | `scrapy_scanner/score_with_new_logic.py` | **DELETE** | 0 引用 |
| 78 | `scrapy_scanner/compare.py` | QUARANTINE/DELETE | CDP vs scrapy 对比工具，非闭环 |
| 330 | root `xiaogu_db_backfill.py` | **DELETE** | 0 引用；真身在 `scripts/xiaogu_db_backfill.py` |
| 266 | `xiaogu_backtest_prediction.py` | **DELETE/QUARANTINE** | 0 pipeline/import |
| 150+1300 | `xiaogu_daily_report_generator` + market/index/volume/sector/short_term analyzers | **整簇 QUARANTINE 或 DELETE** | 仅互引，不进 pipeline |
| 182 | `scripts/clean_ledger_and_archive.py` | DELETE/QUARANTINE | 0 引用 |
| 303 | `scripts/xiaogu_db_review_report.py` | DELETE/QUARANTINE | 0 引用 |
| ~984+ | web_tabs 内 CDP 函数族（~40 函数） | **DELETE 块** 或移 archive | live 已 HTTP；生产只吃 scoring 符号 |

### CORE 但须拆（EXTRACT 地图）

**forward ~183 funcs / 10.7k** 分桶（名启发式，LOC 约值）：

| 桶 | ~LOC | 动作 |
|----|-----:|------|
| select_score（含 main、decision、formal） | ~2177 | 保留在 runner 编排核或后置 |
| gates_eligibility（含 eligibility_profile 1116） | ~1740 | **EXTRACT →** 扩展 `xiaogu_forward_gates` 或 `xiaogu_forward_eligibility` |
| io_bundle_ledger | ~1485 | **EXTRACT →** `xiaogu_forward_bundle_io`（T17 曾 defer） |
| diagnostics_cards | ~563 | EXTRACT 候选 |
| lifecycle_history / position / news / repo_vei / utils | 其余 | 分批 |
| other（含大块 research basket 等） | ~3138 | 逐函数标注 CORE vs RESEARCH |

**web_tabs ~195 funcs / 7.8k：**

| 桶 | ~LOC | 动作 |
|----|-----:|------|
| scoring | ~1493 | **真实现迁入** scoring 模块；runner_v2 改 import |
| cdp_live | ~984 | DELETE（闭环不需要） |
| other/io/utils | 大量 | 随 scoring 依赖保留或删 |

生产稳定符号（当前）：  
`build_information_coverage_audit`, `build_research_signals`, `build_structured_bundle`, `build_structured_scores`, (+ `load_v2_scanner_data`, `enrich_candidates_with_v2_data`).

### 不可误删

- `six_repo_integration_real_v2_1` / native / six_repo integrated：forward `aggregate_four_repo_native_signals`
- `xiaogu_backtest_v0_1`：pipeline closure
- `xiaogu_runtime_payload` / `evidence_card` / `case_vector_store` / `pick_validator`：forward 热路径
- `xiaogu_forward_paper_recorder_v0_1`：shell/subprocess 调用

---

## Phase map

| Phase | 主题 | 用户价值 |
|-------|------|----------|
| 0 | Inventory 落盘可复现脚本/表 | MH1 |
| A | DELETE 硬死代码 | MH2 |
| B | EXTRACT forward | MH3 |
| C | EXTRACT scoring + 剥 CDP | MH4 |
| D | ORPHAN 簇裁决（删或 `archive/ops_orphan/`） | MH2 扩展 |
| E | 验证、LOC、reindex、记忆 | MH5 |

---

### Task 1: 落盘可复现 inventory 工件 A:I10 MH1
- [ ] 新增或更新 `summary/2026-07-23_lifecycle_code_inventory.json`（或 `.md` 表）：每个 root/scripts/scrapy 文件 → LOC、class、evidence
- [ ] 记录 forward top 函数与 web_tabs CDP 函数列表（可脚本生成，禁止手填估计作为唯一证据）
- [ ] Verification: 文件存在且含 `CORE_LIFECYCLE` / `DEAD` / `ORPHAN` 字段；与 `daily_pipeline.sh` 入口列表一致

### Task 2: DELETE scrapy 死栈 A:I10 MH2
- [ ] 再次 `rg` 确认无 `CrawlerProcess` / `scrapy_scanner.spiders` 生产引用
- [ ] 删除 `scrapy_scanner/spiders/*.py`、`items.py`、`settings.py`（若仅蜘蛛用）、`score_with_new_logic.py`；`compare.py` 一并删或移 `archive/`
- [ ] 清理空包与 README 中 multi-spider 入口描述（仅改相关句）
- [ ] Verification: `python3 -c "import scrapy_scanner.runner_v2"`；`pytest tests/test_scanner_transport_direct.py -q` 绿

### Task 3: DELETE root 重复/零引用模块 A:I10 MH2
- [ ] 删除 root `xiaogu_db_backfill.py`（保留 `scripts/xiaogu_db_backfill.py`）
- [ ] 删除或 archive `xiaogu_backtest_prediction.py`
- [ ] 删除或 archive `scripts/clean_ledger_and_archive.py`、`scripts/xiaogu_db_review_report.py`（删前 `rg` 零引用）
- [ ] Verification: `rg` 无破 import；`pytest tests/test_db_backfill.py -q`（若涉及）

### Task 4: 裁决 daily_report ORPHAN 簇 A:I10 MH2
- [ ] 用户默认：**整簇移出主树** → `archive/ops_orphan_daily_report/` 或直接 DELETE（git 有史）；本 plan 默认 **DELETE**（与「旧版可删」一致），若需保留 CLI 则改为 archive 单提交
- [ ] 覆盖：`xiaogu_daily_report_generator.py` + `xiaogu_market_overview_analyzer.py` + `xiaogu_index_level_analyzer.py` + `xiaogu_volume_analyzer.py` + `xiaogu_short_term_strategy.py` + `xiaogu_sector_flow_analyzer.py`
- [ ] Verification: 上述路径不在 root；无 pipeline 引用；全量 `pytest tests/ -x -q` 不因缺模块 fail（修仅测这些模块的测试）

### Task 5: EXTRACT forward eligibility A:I10 MH3
- [ ] 将 `paper_pick_eligibility_profile` / `attach_paper_pick_eligibility` 及相关纯 eligibility 辅助（不改语义）迁入 `xiaogu_forward_eligibility.py` **或** 扩写现有 `xiaogu_forward_gates.py`（二选一：优先 **新文件 eligibility** 以免 gates 变第二个万行）
- [ ] forward 改为 import + 薄 re-export（保持 `from xiaogu_forward_d1_1450_runner_v0_1 import paper_pick_eligibility_profile` 兼容若测试依赖）
- [ ] Verification: `pytest tests/test_xiaogu_a_share_forward_runner.py -q -k "eligibility or paper_pick or decision" --maxfail=5`；forward LOC 下降 ≥1000

### Task 6: EXTRACT forward bundle IO A:I10 MH3
- [ ] 迁出 load/persist/scan_summary/ledger preload 相关（`_bundle_from_scan_summary`、`load_candidate_bundle`、`persist_daily_candidate_snapshot`、ledger 缓存族）→ `xiaogu_forward_bundle_io.py`
- [ ] runner `main` 只编排调用
- [ ] Verification: 同上 pytest 子集 + `test_main_uses_runtime_scan_summary` 类用例绿；forward 再降 LOC

### Task 7: 生产评分实现与 web_tabs 脱钩 A:I10 MH4
- [ ] 将 runner_v2/scoring 所需符号的**实现**迁入单一模块（升级 `xiaogu_scanner_scoring.py` 为真实现宿主，或 `xiaogu_scanner_scoring_impl` 后由 scoring 导出——**禁止**长期双实现）
- [ ] `runner_v2` / forward 优先 `from xiaogu_scanner_scoring import ...`
- [ ] Verification: `python3 scrapy_scanner/runner_v2.py --help`；structured score 单测或现有 scanner 测；import 不再强制加载 CDP 导航主路径

### Task 8: DELETE web_tabs CDP live 块 A:I10 MH4
- [ ] 在评分实现迁出后，删除 web_tabs 内 CDP tab 打开/导航/snapshot 函数族与 `main` live CDP 入口（或整文件删除并更新所有 import）
- [ ] 保留路径名兼容仅当产物文件名仍叫 `eastmoney_web_tabs_*`（数据契约，不强制保留 7k 源）
- [ ] Verification: `rg "open_cdp_tabs|DEFAULT_CDP_URL" --glob '*.py'` 仅剩文档/archive/tests 夹具或零；pytest 绿

### Task 9: RESEARCH_AUX 边界文档（不删） A:I10 MH1
- [ ] 在 inventory 中标注 six_repo/native/scoreboard：**保留**原因与调用点
- [ ] 可选：从 forward 热路径延迟 import（已有多处 lazy）审计是否还有顶层重 import 可缩
- [ ] Verification: inventory 有 `RESEARCH_AUX` 行；无删除这三文件

### Task 10: 总验证 + LOC + 索引 + 记忆 A:I10 MH5
- [ ] `pytest tests/ -x -q`（或项目约定子集若全量过重：至少 transport + forward 核心 + db_backfill）
- [ ] 写 `summary/2026-07-23_lifecycle_slim_before_after.json`：关键文件 before/after LOC、删除文件列表
- [ ] codebase-memory `index_repository` 刷新 `workspace-hermes-workspaces-xiaogu`
- [ ] AgentMemory `memory_save`（decision：闭环标准瘦身）
- [ ] Obsidian `Project/A股/inbox` 一条 + 状态（若 mount）
- [ ] Verification: summary 存在；ledger/plan checkbox 全勾

---

## Done when

1. MH1–MH5 均有对应 Task 验证证据  
2. 用户原先「扫每一行：无用删、有用拆」在 **root+scripts+scrapy** 范围落地（external_research 除外已声明）  
3. 生产闭环入口文件仍存在且可 import；无 `runner_v3`  
4. forward / web_tabs 体量相对 baseline **显著下降**（目标：forward 编排核明显小于 10k；web_tabs CDP 块消失）

## Assumptions

- 用户同意 DEAD 删除以 git 为版本控制，不在树内保留平行旧实现  
- daily_report 簇默认 DELETE；若用户要保留报告 CLI，执行前改为 archive  
- 不在本 plan 改 formal 分数公式  

## Risks

- eligibility/io 抽取触发循环 import → 用延迟 import 或单向依赖  
- 测试绑定旧模块路径 → re-export 过渡一层后下一批再删 re-export  
- 误删 six_repo → Task 9 红线  

---

**执行策略：** 本文件为任务包。**先用户审阅批准，再按 Task 1→10 执行。** 未批准不开删。
