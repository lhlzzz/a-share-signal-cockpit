# 出票主链去 CDP + 职责拆分 + 生命周期闭环

**Status:** READY FOR USER APPROVAL（未执行）  
**Date:** 2026-07-23  
**Intent:** A:I9  
**Chosen interpretation:** PI3  
**Discuss:** [`.plan-enforcer/discuss.md`](../../.plan-enforcer/discuss.md)  
**Architecture truth:** `summary/2026-07-23_architecture_live_vs_cdp_truth.json`

## Normalized Goal

生产出票名义是 `runner_v2`「API 直连」，但 **`api_get` 强制走 CloakChrome CDP 导航**，因此全市场分页/数据中心/外盘索引实际都在拖浏览器；同时评分库与决策引擎仍以 `web_tabs` 命名和 1 万行巨型文件承载，职责混杂、死代码/旧门禁并存。目标是：**一条最新、最完整、最高性能的 live 出票主链 + 完整生命周期闭环**，旧 CDP 链不再误当生产默认；可复用逻辑迁入新职责面并拆分，禁止继续堆在单文件里。

## Non-Negotiables

- NN1: **生产默认 transport = 直接 HTTP**（push2 / datacenter），CDP 仅可选 fallback（env 显式开启），不得再把 CDP 写成「唯一路径」。
- NN2: **出票生命周期闭环不可破**：scanner → sszcw soft → runner → slim recorder → picks/DB → filler T+1 → scoreboard → **pgvector upsert/search** → daily_pipeline。
- NN3: **修改优先于新建**；禁止 `runner_v3` / `api_get_v2` 平行系统；升级现有入口。
- NN4: **不破 formal 门 / 不 freeze / 不静默改 THEME_LEXICON 或 hard gate**。
- NN5: 巨型文件拆分必须 **行为保持 + 测试/抽检出票路径绿**；禁止「大挪移无验证」。
- NN6: paper only；无真实下单；secrets 不进库。
- NN7: codebase-memory 以 `workspace-hermes-workspaces-xiaogu` 为准；结构事实改完后刷新索引。

## Proof Requirements

- PR1: 直连 `api_get` 前后对比（是否启动 CDP / 耗时）
- PR2: pytest 相关 + 可选 `runner_v2` 短跑日志无 CDP_BROWSER
- PR3: 模块边界清单（谁负责 scan / score / gate / formal / persist / vector / fill）
- PR4: 生命周期一次抽检或引用既有 7/23 闭环证据
- PR5: discuss→draft→实现收据；AgentMemory architecture/workflow

## Must-Haves

- MH1: **生产默认 transport = 直接 HTTP**（push2 / datacenter），CDP 仅可选 fallback（env 显式开启），不得再把 CDP 写成「唯一路径」。
- MH2: v2 身份与实现一致；runner 对 v2 不因假 CDP tabs 硬挡
- MH3: 稳定面 `xiaogu_scanner_scoring` 为 scoring import 主入口；web_tabs 非 live
- MH4: 至少抽出 `xiaogu_forward_gates.py` 一刀
- MH5: Social 硬预算 soft-skip
- MH6: pytest + 证据 + reindex + AgentMemory + Obsidian

## Out of scope

- 真实交易 / 券商
- 12 日全量 wall-clock 重爬作为必达
- 一次性物理删除全部 web_tabs 实现
- 电池进 THEME_LEXICON
- force 改 7/23 华银 600744 正式票
- UA 作为改码权威

## 默认选择

1. Social = 有界 sidecar  
2. Extract 至少 T-gates 一刀；bundle_io 可 defer  
3. Transport 本轮必达  
4. 顺序 T1→T22  

---

### Task 1: Transport env 默认 direct

- [ ] 在 `scrapy_scanner/runner_v2.py` 定义 `DEFAULT_SCANNER_TRANSPORT = 'direct'`
- [ ] 读取 `XIAOGU_SCANNER_TRANSPORT`，合法值仅 `direct` `cdp` `auto`
- [ ] 缺省或非法值回退 `direct` 并 print 警告
- [ ] **Verification:** 模块常量默认值为 `direct`；非法 env 回退 `direct`

---

### Task 2: 实现 _api_get_direct

- [ ] 新增 `_api_get_direct(url)`：`LOCAL_OPENER` + `HEADERS`；timeout 20s；最多 3 次尝试
- [ ] 成功走 `_loads_json_or_jsonp`；三次失败 raise；**从不**调用 `_cdp_navigate_text`
- [ ] **Verification:** mock open 成功可 parse；失败路径无 CDP 调用

---

### Task 3: api_get 三 mode 接线

- [ ] `api_get`：`direct` 仅 `_api_get_direct`；`cdp` 仅 `_cdp_navigate_text`；`auto` 先 direct 失败再 cdp 并 print `transport=cdp_fallback`
- [ ] 首次成功 print `SCANNER_TRANSPORT=direct|cdp|cdp_fallback`
- [ ] 改写旧注释，删除「CDP is the scanner's only Eastmoney network path」
- [ ] **Verification:** monkeypatch 下 `direct` 不调用 `_cdp_navigate_text`；无 `api_get_v2` 新文件

---

### Task 4: 保持 fetch 签名

- [ ] 确认 `fetch_paginated` / `fetch_datacenter` 签名未改，仍调用 `api_get`
- [ ] **Verification:** 两函数 def 行与调用 `api_get` 仍在；`git diff` 无签名破坏

---

### Task 5: PR1 直连证据落盘

- [ ] 写 `summary/2026-07-23_scanner_transport_direct_verify.json`
- [ ] 字段：`default_mode` `direct_clist_ok` `direct_datacenter_ok` `direct_clist_ms` `direct_datacenter_ms` `cdp_process_started` `comment_no_longer_cdp_only`
- [ ] `XIAOGU_SCANNER_TRANSPORT=direct` 下真实请求 1 次 clist + 1 次 datacenter
- [ ] **Verification:** 完成 **直连 `api_get` 前后对比（是否启动 CDP / 耗时）**；`cdp_process_started=false` 且两 ok=true

---

### Task 6: Identity helper 判定 v2 源

- [ ] 在 forward runner 增加局部 helper（或 gates 内联）：识别 v2 源
- [ ] 条件（任一）：`candidate_source` 含 `v2_scanner_api`；含 `eastmoney_api_scan_v2`；`pipeline_version` 含 `v2_scanner_api`；`required_cdp_tabs.mode == api_direct`
- [ ] **Verification:** 单元/临时代码对 7/23 字段返回 True

---

### Task 7: v2 跳过三类 CDP tabs flags

- [ ] 改 `web_tabs_evidence_missing_flags`：v2 源跳过前缀  
  `EASTMONEY_REQUIRED_CDP_TABS_MISSING_`  
  `EASTMONEY_ENHANCED_CDP_TABS_MISSING_`  
  `EASTMONEY_DEFAULT_ENHANCED_CDP_TABS_MISSING_`
- [ ] 保留 FULL_UNIVERSE 与证据域 PASS/PARTIAL；保留真 `eastmoney_web_tabs_scan_v0_1` tabs 检查
- [ ] **Verification:** v2 bundle 调用结果不含上述三类前缀

---

### Task 8: Identity pytest 对齐

- [ ] 跑 `pytest tests/test_xiaogu_a_share_forward_runner.py -k "web_tabs_evidence or runner_v2 or db_completeness" -q`
- [ ] 若旧 CDP-only 断言失败：只改测试对齐 v2，不改 formal 分
- [ ] **Verification:** pytest 退出码 0

---

### Task 9: 稳定面六符号

- [ ] 锁定 `xiaogu_scanner_scoring.py` 导出：  
  `build_information_coverage_audit`  
  `build_research_signals`  
  `build_structured_bundle`  
  `build_structured_scores`  
  `enrich_candidates_with_v2_data`  
  `load_v2_scanner_data`
- [ ] forward runner 与 runner_v2 主路径优先该模块
- [ ] **Verification:** `python3 -c "from xiaogu_scanner_scoring import load_v2_scanner_data, enrich_candidates_with_v2_data, build_structured_scores, build_structured_bundle, build_research_signals, build_information_coverage_audit; print('ok')"` 输出 ok

---

### Task 10: web_tabs quarantine 文案

- [ ] `xiaogu_eastmoney_web_tabs_scan_v0_1.py` docstring 三句：  
  NOT production live scanner；  
  production live = scrapy_scanner/runner_v2.py；  
  scoring library + legacy offline/CDP tools
- [ ] 不删实现；不建平行评分引擎
- [ ] **Verification:** 文件头可见三句；`import xiaogu_eastmoney_web_tabs_scan_v0_1` 成功

---

### Task 11: Social 预算 env

- [ ] runner_v2 social 段读 `XIAOGU_SOCIAL_COLLECT_BUDGET_SEC` 默认 60
- [ ] 非法或负数回退 60
- [ ] **Verification:** 源码可见 env 名与默认 60

---

### Task 12: Social collect soft-skip

- [ ] `collect_and_store` 外包 wall-clock 预算；超时 print `Social collect budget exceeded`
- [ ] 超时或异常不 raise 杀主扫；走 optional skip
- [ ] **Verification:** `BUDGET_SEC=0` 或 mock 超时后主路径可继续写 summary

---

### Task 13: Extract 清单冻结

- [ ] 冻结迁入 `xiaogu_forward_gates.py` 仅四符号：  
  `missing_coverage_items`  
  `web_tabs_evidence_missing_flags`  
  `soft_no_pick_flag`  
  `candidate_evidence_missing_flags`
- [ ] 兼容：`from xiaogu_forward_d1_1450_runner_v0_1 import web_tabs_evidence_missing_flags` 仍可用
- [ ] 禁止迁 formal sort 与权重
- [ ] **Verification:** 清单本任务勾选即冻结

---

### Task 14: 新建 xiaogu_forward_gates 迁入四符号

- [ ] 新建 `xiaogu_forward_gates.py`，迁入 Task 13 四符号 + 最小常量闭包
- [ ] 避免与 runner 环依赖
- [ ] **Verification:** `python3 -c "import xiaogu_forward_gates; print('ok')"` 输出 ok

---

### Task 15: runner re-export gates

- [ ] `xiaogu_forward_d1_1450_runner_v0_1.py`：`from xiaogu_forward_gates import missing_coverage_items, web_tabs_evidence_missing_flags, soft_no_pick_flag, candidate_evidence_missing_flags`
- [ ] 模块级保留同名绑定
- [ ] 禁止改 formal 公式、权重、escape
- [ ] **Verification:** `from xiaogu_forward_d1_1450_runner_v0_1 import web_tabs_evidence_missing_flags` 成功

---

### Task 16: Extract 回归 pytest

- [ ] 跑 `pytest tests/test_xiaogu_a_share_forward_runner.py -k "web_tabs_evidence or load_candidate or runner_v2 or db_completeness" -q`
- [ ] `wc -l`：runner 变短且 `xiaogu_forward_gates.py` 存在
- [ ] **Verification:** pytest 退出码 0

---

### Task 17: Extract2 defer 或 bundle_io

- [ ] 二选一：  
  A 抽出 `load_latest_eastmoney_scan` 与 `load_candidate_bundle` 到 `xiaogu_forward_bundle_io.py` 并 re-export；  
  B 在 verify json 写 `extract2_deferred=true` 与 reason
- [ ] **Verification:** A 则相关 pytest 绿；B 则 json 含 `extract2_deferred=true`（B 不算失败）

---

### Task 18: 单测 transport mock

- [ ] 新增 `tests/test_scanner_transport_direct.py`
- [ ] mock HTTP；禁止真起浏览器；`direct` 不调用 `_cdp_navigate_text`
- [ ] **Verification:** `pytest tests/test_scanner_transport_direct.py -q` 退出码 0

---

### Task 19: runtime 与生命周期引用

- [ ] `pytest tests/test_xiaogu_runtime_payload_evidence_vector.py -q`
- [ ] verify json 写 `lifecycle_ref` 引用 7/23 紫金 T+1、pgvector、华银票（不 force 改票）
- [ ] **Verification:** pytest 0；json 含 lifecycle_ref（PR4）

---

### Task 20: codebase-memory reindex

- [ ] moderate reindex `workspace-hermes-workspaces-xiaogu`
- [ ] search 命中 `xiaogu_forward_gates` 或 transport 相关符号
- [ ] **Verification:** index ready；至少一次 search 命中

---

### Task 21: AgentMemory 收口

- [ ] `memory_save` architecture：CDP 不再默认；gates 边界
- [ ] `memory_save` workflow：`XIAOGU_SCANNER_TRANSPORT` 与生命周期
- [ ] **Verification:** 可检索本轮 architecture/workflow 记录（PR5）

---

### Task 22: Obsidian inbox 笔记

- [ ] 写 `Project/A股/inbox/2026-07-23-主链去CDP与职责拆分.md`（含 PR3 模块边界清单）
- [ ] **Verification:** 文件存在且含本计划结论

---

### Task 23: Obsidian 状态与任务

- [ ] 更新 `Project/A股/状态.md`
- [ ] 更新 `Project/A股/任务.md`
- [ ] **Verification:** 两文件含 2026-07-23 主链去 CDP 条目

---

### Task 24: 神临摘要一行

- [ ] 神临每日想法或项目接口写一行摘要（跨域接口）
- [ ] **Verification:** 对应笔记可见该行

---

## 任务包总览（评审用）

| ID | 内容 | 文件 | 风险 | defer |
|----|------|------|------|-------|
| T1 | env 默认 direct | runner_v2 | 低 | 否 |
| T2 | `_api_get_direct` | runner_v2 | 中 | 否 |
| T3 | `api_get` 三 mode | runner_v2 | 中 | 否 |
| T4 | fetch 签名保持 | runner_v2 | 低 | 否 |
| T5 | PR1 证据 json | summary/ | 低 | 否 |
| T6 | v2 源判定 helper | forward runner | 低 | 否 |
| T7 | 跳过假 CDP flags | forward runner | 中 | 否 |
| T8 | identity pytest | tests | 低 | 否 |
| T9 | 六符号稳定面 | scanner_scoring | 低 | 否 |
| T10 | web_tabs quarantine | web_tabs | 低 | 否 |
| T11 | social budget env | runner_v2 | 低 | 否 |
| T12 | social soft-skip | runner_v2 | 中 | 否 |
| T13 | extract 清单 | 契约 | 低 | 否 |
| T14 | 新建 gates 模块 | xiaogu_forward_gates.py | 中高 | 否 |
| T15 | runner re-export | forward runner | 中 | 否 |
| T16 | extract pytest | tests | 中 | 否 |
| T17 | extract2 或 defer | optional | 高 | **是** |
| T18 | transport 单测 | tests/ | 低 | 否 |
| T19 | runtime + lifecycle | tests + summary | 低 | 否 |
| T20 | reindex | codebase-memory | 低 | 否 |
| T21 | AgentMemory | memory | 低 | 否 |
| T22 | Obsidian inbox | Project/A股 | 低 | 否 |
| T23 | Obsidian 状态任务 | Project/A股 | 低 | 否 |
| T24 | 神临一行 | 神临 | 低 | 否 |

## 整包 Done（验收清单，非 Task checklist）

1. MH1/NN1 默认 direct HTTP；CDP 非唯一路径
2. PR1：`cdp_process_started=false` + 双请求 ok + 耗时字段
3. v2 无假 CDP tabs 硬挡
4. 六符号稳定面
5. `xiaogu_forward_gates` + pytest 绿
6. Social 预算 soft-skip
7. T18–T24 完成
8. 无 runner_v3；无 formal 漂移

## 红线

- 新建平行出票引擎
- 改 formal 权重或 hard gate「顺手优化」
- 伪造非今日 T+1
- force 改华银票
- 只改文档不改 `api_get`
- 把 CDP 重新标成唯一生产路径

## Proof mapping

| PR | Tasks |
|----|-------|
| PR1 直连 `api_get` 前后对比（是否启动 CDP / 耗时） | T2 T3 T5 |
| PR2 pytest / 无 CDP_BROWSER | T8 T16 T18 T19 |
| PR3 模块边界 | T9 T10 T13 T14 T22 |
| PR4 生命周期 | T19 |
| PR5 记忆 | T21 T22 T23 T24 |

## 生命周期（保持）

```
runner_v2 (direct HTTP)
  -> sszcw soft
  -> forward runner (gates / formal)
  -> recorder / picks DB
  -> filler T+1
  -> scoreboard
  -> pgvector
  -> daily_pipeline
```

已通不重做：紫金 601899 T+1；pgvector 真库；华银 600744。

## 批准后

1. plan-enforcer ledger 导入本 plan  
2. 严格 T1→T24  
3. 每任务验证后勾选  
4. T21–T24 收口  

*Atomic tasks (≤4 checkboxes), discuss-literal goal/NN/PR. Awaiting user approval — no execution yet.*
