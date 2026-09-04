# FINAL_SYSTEM_AUDIT

Date: 2026-09-04

This audit records the in-place single-system convergence of Xiaogu plus final truth hardening. No second production system was added. This is not Xiaogu 4.0.

## Production permission

- BUY = BLOCKED
- LIVE TRADE = DISABLED
- paper_only = true
- live_order = false
- auto_order = false
- broker_connected = false
- Alpha production_permission remains fail-closed until OOS gates pass. Current live alpha status stays DATA_INSUFFICIENT / EXPERIMENTAL unless a validated artifact exists.

## Unique production target

- `opportunity_5d`
- Definition: future 5 valid trading days, any daily high versus T-day reference reaches net +2% after `cost_model_v1`
- Alpha / Paper / Selection / OOS use this target only
- Realized return / MAE / MFE remain auxiliary evaluation metrics, not a second selection chain

## Unique owners after convergence

| Responsibility | File / function |
|----------------|-----------------|
| Scanner capture | `scrapy_scanner/runner_v2.py` |
| Canonical snapshot | `xiaogu_forward_snapshot.py` |
| Eligibility / MAIN_BOARD | `xiaogu_forward_eligibility.py` |
| Features | `xiaogu_forward_features.py` |
| Research enhancement | `xiaogu_research_context.py` |
| Production Alpha | `xiaogu_core_alpha.build_core_alpha` (`profit_window_alpha_5d_v4`) |
| Decision / gates | `xiaogu_portfolio_decision.evaluate_candidate_bundle` / `evaluate_production_gates` |
| Selection Top3/Top1 | `xiaogu_portfolio_decision.attach_top_paper_observations` |
| Runner / batch clock | `xiaogu_forward_runner.py` |
| Paper | `xiaogu_forward_paper_recorder_v0_1.py` |
| Outcome T+1..T+5 | `xiaogu_forward_result_filler_v0_1.py` + `xiaogu_db.fetch_horizon_outcomes` |
| OOS walk-forward | `xiaogu_horizon_evaluation.py` |
| Calendar / DB | `xiaogu_db.py` |

## Hidden second alpha removed

- `repricing_evidence_score` is diagnostic only (`repricing_evidence_score_role = DIAGNOSTIC_ONLY`)
- `_signal_sort_key()` ranks by unique Alpha `selection_score`, then deterministic non-model fields: `decision_clock`, `snapshot_id`, `symbol`
- Confidence / execution_feasibility / repricing score are not ranking axes
- Top3 / Top1 reasons: `TOP1_OPPORTUNITY_5D` / `TOP3_OPPORTUNITY_5D`

## Research semantics

Research Adapter / Context Adapter fields:

- provider_requested
- provider_available
- provider_succeeded
- provider_failed
- evidence_count
- usable_evidence_count
- pit_valid
- used_downstream
- knowledge_available_at
- reason

`invoked` means called only. It is not success, evidence, or downstream use.

`research_consumed` is deleted. These adapters are not external live providers; they are in-process context builders.

Serenity / Buffett / UZI / Contradiction / Historical PostgreSQL / Obsidian are enhancement-only. A research-adapter failure retries, degrades, and does not block Selection. Market-data / canonical / PIT / core-calculation / DB-consistency failures ABSTAIN. Obsidian never emits BUY / RANK / PICK.

Historical PostgreSQL PIT: cases are visible when `knowledge_available_at <= as_of`. Outcome / review / failure_pattern are hidden until `outcome_settled_at <= as_of`. `signal_time` is not a substitute for `knowledge_available_at`.

Obsidian PIT: decision notes use `knowledge_type=DECISION`; outcome updates use `knowledge_type=OUTCOME` on the same `paper_signal_id`. Reading as_of=T hides outcome / review / attribution when `outcome_update_at > T`.

## Coverage contract

Normal trading days must complete:

scan → MAIN_BOARD execution universe → L0/L1/L2/L3 → Research → Alpha → Decision → Top3 → Top1 → Paper

Single-ticket worker errors retry (`WORKER_RETRY_LIMIT = 2`) and recover. Any production candidate that remains unrecoverable after three attempts sets `system_fault=True` and ABSTAINS (`top1 = null`, `top3 = []`, `publishable = false`, all `paper_observation = None`). PARTIAL_OBSERVATION / PARTIAL_SELECTION are deleted. Production PostgreSQL facts for one run are written in one transaction (`persist_production_facts`); a key persist failure rolls back and marks the run FAILED.

One batch `decision_clock` is generated in `main()`. Workers receive it; they do not call `production_now()`. Eligibility no longer invents a clock when `as_of` is missing.

## Paper / Memory / Outcome

- `paper_signal_id` ≠ `decision_id`
- paper_only = true, live_order = false
- Memory path: `xiaogu_memory/decisions/{section}/{date}/{symbol}/{paper_signal_id|decision_id}.md`
- Lineage fields: production_run_id, lineage_id, snapshot_id, decision_id, paper_signal_id, outcome_id, review_id, memory_id
- Date + symbol is not a unique memory identity
- T+1..T+5 facts live in the `returns` payload nested map `days["1".."5"]`. This is not a per-horizon row table. Missing days stay `MISSING`.
- Horizon identity is `decision_id:horizon`. Aggregate `outcome_id` is `decision_id:horizon`.
- `fetch_horizon_outcomes(decision_id)` reads that nested payload.
- `cost_model_v1` is unique. Commission/stamp_duty are modeled; slippage/spread/market_impact are proxy. Execution realism is `DAILY_BAR_APPROXIMATION`. There is no `slippage_included=false` contradiction.
- Memory can be rebuilt from PostgreSQL via `rebuild_memory_from_postgresql()`. Daily notes are a summary view; identity is `paper_signal_id`.

## Price gate

0.5%–9.5% remains L2 routing / research ablation only. Production Alpha `_signal_qualification` no longer eliminates on that window.

Research evaluation:

- WITH_GATE
- WITHOUT_GATE

`evaluate_price_gate_ablation()` is RESEARCH_ONLY and `production_frozen = false`.

## OOS

`xiaogu_horizon_evaluation._split_rows`:

- chronological 60/20/20
- embargo ≥ 5 trading days
- purge / no random split
- daily grouped Top1 / Top3 hit rate, opportunity rate, coverage
- MAE / MFE / realized return / market baseline / regime / stability remain on the same target

## Deleted or retired redundant paths

Source files `xiaogu_forward_ranking.py` and `xiaogu_scanner_scoring.py` are already absent. Remaining bytecode under `__pycache__/` is not a live owner.

Removed or retired from live owners:

- PARTIAL_OBSERVATION and any partial-ticket publish path
- fake `research_consumed`
- repricing_evidence_score → `_signal_sort_key` ranking chain
- per-worker `production_now()`
- eligibility fallback `as_of or production_now()`
- dual production target `PROFIT_WINDOW_5D` on Alpha / Paper / Selection / OOS / rule freeze
- backtest `net_profit_window` as a second production label
- horizon `same_target = PROFIT_WINDOW_5D`
- memory identity keyed only by date + symbol
- empty-research fail-closed WATCH / INSUFFICIENT_5D_EVIDENCE on selection

No new selector / ranker / top-k / decision-bucket files were created.

## Tests

Command: `pytest tests/ -x -q`

Result: **355 passed** in 93.20s.

`tests/test_single_system_convergence.py` (25 tests) covers:

- single-system ownership / single alpha / single target / single selection
- permanent worker failure => ABSTAIN
- transient worker failure => retry => recover
- no PARTIAL_OBSERVATION
- MAIN_BOARD_ONLY
- 0.5–9.5 not reapplied as production strategy gate
- unique Alpha ranking; repricing_evidence_score cannot change ranking
- shared decision_clock; workers do not call `production_now()`
- Historical PIT; outcome hidden before settlement
- Obsidian PIT; outcome hidden before settlement
- paper_signal_id ≠ decision_id; same-day multiple observations do not overwrite
- T+1..T+5 SETTLED/MISSING + horizon identity
- cost_model_v1 daily-bar approximation
- atomic persistence
- production run coverage contract
- OOS chronological + embargo ≥ 5 + daily grouped Top1/Top3
- price gate ablation RESEARCH_ONLY
- memory rebuild from PostgreSQL

Existing tests were migrated onto `opportunity_5d` and `research_used_downstream`.

## Confirmation

The system is still one Xiaogu:

- one Alpha
- one Target
- one Selection
- one Decision
- one Paper
- one Outcome
- one Memory lineage
- BUY = BLOCKED
- LIVE TRADE = DISABLED
