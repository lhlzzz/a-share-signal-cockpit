# Xiaogu Final Single-System Convergence

## Source Ask
> 这是一次最终收敛级重构，不是 patch 集合。每个交易日，从完整 A 股市场中，严格限定 MAIN_BOARD，输出最多 3 个正式 Paper Observation，其中 1 个 Top1。ONE SYSTEM / ONE PIPELINE / ONE ALPHA / ONE DECISION OWNER / ONE SELECTION OWNER。Formal Signal ≠ Live BUY。PostgreSQL = authoritative；Obsidian = rebuildable semantic asset。Memory 永远没有最终决策权。

## Normalized Goal
今天的生产链已经有唯一 Owner 文件，但行为上仍是多套真相：Selection 用 `repricing_evidence_score` 当隐藏第二 Alpha；Research 用 `research_consumed=any(...)` 假装消费；每个 worker 自己打 `production_now()`；worker 失败后仍用剩余股票出 Top1；Obsidian 按 `date_symbol.md` 覆盖；Outcome / Memory / OOS 无法按统一 identity 回答“为什么选、五天后是否错、过去为什么错”。要收敛成一条可解释、失败诚实、可重建记忆的单一生产真相链。

## Non-Negotiables
- NN1: 不新建第二套 Scanner / Alpha / Decision / Selection / Paper / Memory / DB。现有 Owner 原地收敛；重复路径删除或标 `RESEARCH_ARTIFACT_ONLY`。
- NN2: `MODEL_ID = profit_window_alpha_5d_v4` 直到正式 OOS 升级。`evaluate_candidate_bundle()` 仍是唯一 Decision Owner。Selection 不得再定义 Alpha。
- NN3: BUY 保持 BLOCKED；Live Trading 保持 DISABLED。Paper Observation 可以产生。
- NN4: Execution Universe = MAIN_BOARD ∩ L1。STAR / CHINEXT / BSE / UNKNOWN / OTHER 不得进入生产 Paper。
- NN5: 一个 production run 只有一个 `decision_clock` / `as_of`。Calendar Owner 仍是 `xiaogu_db.py`。
- NN6: PostgreSQL 是权威事实；Obsidian 可重建、无决策权。Memory 失败不得污染 production fact，必须进 retry。
- NN7: JSONL = audit only。Understand / codebase-memory 不得进入 Alpha / Decision。
- NN8: 完整市场覆盖是生产契约。正常日必须出一套正式 Top3+Top1。单个候选失败必须重试/恢复，不得让整天失败。只有系统级故障才 ABSTAIN：`publishable=false`，`top1=null`，`top3=[]`。禁止 PARTIAL_OBSERVATION，禁止用部分候选生成正式 Top1/Top3。
- NN9: 唯一生产目标是 `opportunity_5d`：未来 5 个有效交易日中任意一日 daily high 相对 T 日基准价达到净 +2%（扣除统一成本）。Alpha / Paper / Selection / OOS 全部只用这一个目标。不保留第二套 5D 生产标准。
- NN10: Research providers 是增强层。单个 provider 失败、超时、Obsidian 不可用、Historical 失败都不阻断正式 Selection。只有市场数据 / PIT / Canonical / 核心计算 / 数据库一致性失败才 ABSTAIN。

## Hidden Contract Candidates
- HC1: `selection_score` 必须可追溯到唯一 Production Alpha + 合法 tie-breaker（validated probability / confidence / risk / execution / timestamp / symbol）。禁止 `mean(capital, supply, repricing...)`。
- HC2: Alpha 未 VALIDATED 时 Top1 仍可 Paper，但必须标记 diagnostic / unvalidated，不能装成已校准概率。
- HC3: Research 每个 provider 要有 requested / available / succeeded / failed / evidence_count / usable_evidence_count / used_by_*。`invoked=true` 不是消费证明。
- HC4: 同一 symbol 同一日 09:25 与 14:30 是两个 observation；Memory / Daily note 不得覆盖。
- HC5: Outcome 按 `paper_signal_id → decision_id → snapshot_id → production_run_id` 绑定。禁止 `symbol+date` 作为唯一身份。
- HC6: Historical / Memory 只进 Research Context 与 OOS；不能直接 BUY 或直接 Top1。
- HC7: `opportunity_5d` 是唯一生产命中定义。不得再把 close-path / net_close / realizable_trade_return 当作第二生产标准或兼容模式。诊断字段若保留，不得进入 Alpha、Selection、Paper validation、OOS。
- HC8: ABSTAIN 是故障保护，不是降级模式。覆盖失败不得设计成正常分支。可靠性靠重试、超时恢复和结果一致性，不靠部分出票。

## Plausible Interpretations
- PI1: 在现有 Owner 上做最终收敛：删隐藏第二 Alpha、升 Research 消费、统一 clock / identity / Memory / Outcome / OOS，并清理重复路径。
- PI2: 当成 Xiaogu 3.0 重定价重写，再扩一套 Feature / Alpha / Decision 契约。
- PI3: 用新 selector / cost_model_v2 文件 / 新 ranker 完成需求，旧文件留 compat。

## Chosen Interpretation
PI1。这是收敛，不是平行重建。修改现有 Owner；Selection 留在 `attach_top_paper_observations`（或同文件内唯一函数），不新建 selector。`cost_model_v1` 原地升级为唯一 cost model，不另开生产费用路径。L2 的 0.5%–9.5% 先当未证明 routing，必须走 OOS ablation 后再决定保留或删除。仓库清理只删能证明不在唯一生产链上的重复物。

## Rejected / Forbidden Narrowings
- FN1: 把 `repricing_evidence_score` 改名为 `selection_score` 继续当 ranker。
- FN2: 保留 `research_consumed=True` 作为 Research 真实性证明。
- FN3: 用剩余成功股票伪装完整市场 Top1，或把覆盖失败设计成 PARTIAL_OBSERVATION 正常分支。
- FN9: 因单个 Research provider 失败而让正常交易日没有 Top1/Top3。
- FN10: 同时维护 opportunity 与 execution 两套生产目标 / 标签 / 验证 / 选择逻辑。
- FN4: 新建 `legacy_` / `v2_` / `compat_` / `shadow_` 生产路径。
- FN5: 用 Obsidian 或 Historical 直接决定 Top1 / BUY。
- FN6: 把 5 日最高价 +2% 继续叫 realizable_trade_return。
- FN7: 把本次降成文档/审计，不改生产行为。
- FN8: 用 weekday / future price / scanner 有数据来当 Calendar。

## In Scope
- 删除隐藏第二 Alpha；正式 `selection_score` 契约；Top1==1 且 Top3<=3；未校准 Alpha 诚实 Paper。
- Research provider 真实消费与失败状态；失败只记账/重试/降级，不阻断 Selection。共享 `decision_clock`；完整覆盖是契约；系统级故障才 ABSTAIN。
- Production run manifest 与 ID 事实层：run / lineage / snapshot / decision / paper / outcome / review / memory。
- 唯一 `opportunity_5d` 生产目标；删除第二套 5D target 路径；唯一 cost model；T+1..T+5 按 identity 落库，缺则 MISSING。
- Obsidian second brain：identity 路径、daily 不覆盖、可查询回忆、可从 PostgreSQL rebuild、sync retry。
- Daily cross-sectional OOS + rolling walk-forward + embargo；L2 价格门 ablation。
- 系统性质测试、mutation tests、仓库重复路径清理、`FINAL_SYSTEM_AUDIT.md`。

## Out of Scope
- 打开 Production BUY 或 Live Trading。
- 训练并宣称新 Alpha 已 VALIDATED。
- 真实券商、第二数据库、UI 重写。
- 把 Understand-Anything / AgentMemory 当市场证据。
- 改写历史 UNKNOWN 行的语义。

## Constraints
- 现有 Owner 文件保持唯一职责。文件名带 `_v0_1` 的 Recorder / Filler 是当前 Owner，不借机再造一套。
- Paper-only。复杂度不得用新层解决。
- Calendar / Snapshot immutability / Position identity / REDUCE≠FLAT 现有契约保持。

## Success Signals
- 架构审计能对 Scanner / Snapshot / Eligibility / Feature / Research / Alpha / Decision / Selection / Paper / Outcome / Calendar / Memory 各给出一个答案。
- Top1/Top3 不依赖 `repricing_evidence_score`；改变唯一 Alpha 输入会改变 Top1；Research 不可用不能再 `research_consumed=True`。
- 共享 clock；单个候选失败可恢复；系统级故障 ABSTAIN 且 top1/top3 为空；MAIN_BOARD-only Paper。
- PostgreSQL 能按 `decision_id` 查 T+1..T+5；Obsidian note 含 `production_run_id` 且同日双观察不覆盖；删除 Obsidian 后可 rebuild。
- `pytest tests/ -q`、`compileall`、`xiaogu_daily_health_check.py`、dry-run / replay / PIT / dual-observation / provider+worker failure / memory rebuild / OOS rolling 有证据。
- BUY BLOCKED，LIVE DISABLED。

## Drift Risks
- DR1: draft 把 Selection 抽成新文件，变成第二 Owner。
- DR2: 为 Memory / OOS / cost 各新建平行模块。
- DR3: ablation 未完成却把 0.5% 写成永久生产策略。
- DR4: 把 diagnostic `price_strength` 继续包装成 validated probability。
- DR5: 清理阶段误删唯一生产链或只改文档。
- DR6: 把 close/net_close 再次写成生产目标，或留下双目标兼容字段。
- DR7: 把 Research provider 失败重新设计成阻断条件。
- DR8: 把 ABSTAIN / PARTIAL 写成日常降级，而不是修并行、重试和覆盖。

## Proof Requirements
- PR1: Owner 调用链（codebase-memory + source）证明单一路径。
- PR2: 删除/降级重复模块清单写入 `FINAL_SYSTEM_AUDIT.md`。
- PR3: mutation tests：Research 删除/修改、Obsidian 删除、PG history 删除、worker fail、Top1 输入变化。
- PR4: 同一 `production_run_id` 下所有候选同一 `decision_clock`。
- PR5: 系统级 ABSTAIN 时 JSON 中 `publishable=false` 且 top1/top3 为空；正常覆盖完整时必须有正式 Top3+Top1。
- PR7: 证明不存在第二套 5D production target，也不存在 PARTIAL_OBSERVATION 出票路径。
- PR8: 证明单个 Research provider 失败不会阻断正式 Selection。
- PR6: Memory rebuild 与 Outcome identity 绑定的运行证据。

## Draft Handoff
- phase shape hint: 先锁 Owner 与删除隐藏第二 Alpha / 共享 clock / fail-closed coverage；再升 Research 消费与 run identity；再 Fact+Memory+Outcome；再 Target/Cost/OOS/ablation；最后清理、系统测试、审计。
- planning red lines: 不新建第二 Alpha/Selection/Decision；不打开 BUY；不把 ablation 结果预设为保留 0.5%；不把 Memory 写成决策权；不保留双 5D 目标；不设计 PARTIAL_OBSERVATION；不因 Research 层失败停掉正常出票。

## Resolved Forks
1. Production target = `opportunity_5d` only. Net +2% on any valid T+1..T+5 daily high vs T-day reference after the single cost model. Delete the second 5D production target and its labels, calculators, validators, selectors, and schema paths. No dual-target compatibility mode.
2. Coverage: complete MAIN_BOARD evaluation is the production contract. Normal days emit one formal Top3+Top1. Single-candidate failures retry/recover. Only system-level faults (incomplete market data, DB corruption, core component down, PIT/consistency failure) ABSTAIN with empty top1/top3. Delete PARTIAL_OBSERVATION.
3. Research providers never block Selection by themselves. Retry/degrade and continue. Only market data / PIT / Canonical / core compute / DB consistency failures ABSTAIN.
