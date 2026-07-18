# xiaogu Pipeline

## Stable Total Chain
1. `xiaogu_forward_d1_1450_runner_v0_1.py`
2. `xiaogu_forward_paper_recorder_v0_1.py`
3. `xiaogu_forward_result_filler_v0_1.py`
4. `xiaogu_forward_judge_scoreboard_v0_1.py`

## Validation Chain
1. `six_repo_integration_real_v2_1.py`
2. `xiaogu_v2_1_six_repo_real_integrated.py`
3. historical `xiaogu_v2_1_six_repo_one_year_topk_replay.py` (archive-only)

### Active Four-Repo Integration Naming
- `six_repo_integration_real_v2_1.py` currently implements four active repos: `tradingagent_a`, `VEI`, `Qlib`, `QuantDinger`.
- The active scoring entrypoints should be read as four-repo/current-repo integration, even though the legacy filenames remain for compatibility.
- `xiaogu_v2_1_six_repo_one_year_topk_replay.py` is historical validation only and should not be treated as a live entrypoint.

## Rollback Chain
1. `runtime_foundation_v1_0/scripts/rollback_replay.sh`
2. `CORRECTION` records in `forward_paper_ledger_v0_1.jsonl`

## Evidence Contract
- Live scan and browser capture scripts write local JSON evidence once.
- `xiaogu_forward_result_filler_v0_1.py` reads local evidence first, then local kline cache, and only falls back to network when cached evidence is missing or unusable.
- Keep producer and consumer separated; do not make the filler depend on live browser access for the normal path.

## XIAOGU_REPO_INTEGRATION_V3 Production Boundary

V3 不改 Stable Total Chain。生产打分和正式 PAPER_PICK 只允许读取：

```text
Xiaogu Native Evidence
+ validated VEI features
+ validated Qlib feature views
```

Research repos (`QuantDinger`, `tradingagent_a`) 默认只输出 `RESEARCH_SIGNAL` / `DIAGNOSIS_ONLY` / `ATTRIBUTION_HINT` / `PROMOTION_CANDIDATE`，未经明确 promotion 审批不得影响 `candidate_score`、`ranking_score` 或 `production_pick`。

Retired repos (`TradingAgents`, `ai-hedge-fund`, `ZBS`) 保留 source-only，默认不读、不执行、不加载。

## XIAOGU_REPO_INTEGRATION_V3 Feature Flow

```text
Eastmoney Scan
↓
Evidence Engine
↓
VEI Features
↓
Qlib Feature Store
↓
Candidate Engine
↓
Diagnosis Engine
↓
Forward Tracking
↓
Attribution Engine
```

连接规则：

1. Eastmoney Scan 仍由现有东财网页/尾盘扫描产生 T-day 可见 evidence。
2. Evidence Engine 只做 evidence normalization / freshness / completeness / no-leakage 边界，不引入未来收益字段。
3. VEI 只在 evidence 之后生成 event/anomaly/pre-breakout/underwater/regime 特征；未验证前不得进生产打分。
4. Qlib 只作为 feature store、backtest、attribution 层；未通过 no-leakage + forward + attribution 证据前不得进生产打分。
5. Candidate Engine 仍由稳定 runner gate 决定 `NO_PICK` / `RESEARCH_CANDIDATE` / `PAPER_PICK`。
6. Forward Tracking 仍只通过现有 recorder/filler/ledger 合约写入或补录。
7. Attribution Engine 使用 Qlib/scoreboard/diagnosis 解释收益、亏损、炸板、未涨停和 regime mismatch；研究仓库只能提供假设。

## Removed
- `xiaogu_v2_1_ultimate.py`
- `xiaogu_v1_0_runtime_foundation_landing.py`
- `density / score / breadth / rank / small-combo` replay outputs
- `runtime_foundation_v1_0/scripts/*.py` stubs except rollback
- 2026-05-23 cleanup:
- `four_repo_integration_{lightweight_v2_1,real_v2_0}.py`
- `xiaogu_v0_*` / `xiaogu_v1_*` / `xiaogu_v2_0*` / `xiaogu_v3_0_conservative.py` historical top-level replay entry scripts
- old verifier / workflow / bridge entry scripts such as `xiaogu_realtime_*`, `xiaogu_market_context_v0_4d.py`, `xiaogu_workflow_graph_executor_v0_1.py`

## Historical Artifacts
- Historical `*.json`, `*.jsonl`, and `*.md` outputs are kept as evidence and may still mention removed scripts.
- Treat those references as archive metadata, not runnable current entrypoints.
- Root-level legacy outputs and old support directories were moved to `archive/legacy_root_2026-05-23/`.

## Rule
- Keep the stable chain small.
- Keep rollback append-only.
- Do not recreate removed experiments unless explicitly requested.

## Daily Rule Optimization Cycle

After `xiaogu_forward_result_filler_v0_1.py` fills a forward result:

1. Run `xiaogu_forward_judge_scoreboard_v0_1.py --dry-run` against the canonical `forward_paper_ledger_v0_1.jsonl`.
2. Inspect `horizons.d1.by_method` and judge the active `historical_backtest_rule_v0_3:PAPER_PICK` slice separately from older rule versions.
3. If v0.3 underperforms, diagnose the dominant blockers and propose one targeted rule change in `DECISIONS.md` before editing `rule_freeze_v0_1.json`.
4. Old v0/v1/v2.0 chains and archived replay outputs remain evidence only; they must not be used as current entrypoints or mixed into active-rule conclusions.
5. `forward_paper_ledger_v0_1.jsonl.bak_*` files are rollback proof only and must never be used as scoreboard source ledgers.
