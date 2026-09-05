# Xiaogu STATE

Date: 2026-09-05

Phase 2.3 locked the production contract: one trading day has one real-market scan, one Production Decision, and at most one official Top1/Top3 paper observation. There is no fixed clock. Time rules are trading calendar plus `source_time` freshness ≤ 120 minutes versus `production_now()`. 2026-09-04 remains **NO_OFFICIAL_PRODUCTION_TICKET** and was not backfilled. Saturday 2026-09-05 is `NON_TRADING_DAY`. See `PHASE_2_3_PRODUCTION_CONTRACT_CLEANUP.md`. Alpha status remains DATA_INSUFFICIENT. **NO_REAL_OOS_EVIDENCE_YET**.

## Production permission

- BUY = BLOCKED
- LIVE TRADE = DISABLED
- paper_only = true
- live_order = false
- auto_order = false
- broker_connected = false

## Unique owners

| Role | Owner |
|------|--------|
| Production target | `opportunity_5d` |
| Production alpha | `xiaogu_core_alpha.build_core_alpha` / `profit_window_alpha_5d_v4` |
| Selection | `xiaogu_portfolio_decision.attach_top_paper_observations` |
| Decision | `xiaogu_portfolio_decision.evaluate_candidate_bundle` |
| Gates | `xiaogu_portfolio_decision.evaluate_production_gates` |
| Clock | one batch `decision_clock` from `xiaogu_forward_runner.main()` via `production_now()`; workers do not call `production_now()`. No morning/afternoon/14:30 production clock. |
| Calendar | `xiaogu_db.py` |
| Paper | `xiaogu_forward_paper_recorder_v0_1.py` |
| Outcome | `xiaogu_forward_result_filler_v0_1.py` |
| Database | `xiaogu_db.py` / PostgreSQL schema v6 |
| Memory | PostgreSQL facts + Obsidian notes keyed by `paper_signal_id` / `decision_id` |
| Execution universe | MAIN_BOARD_ONLY |

## Target

Opportunity Target = any of T+1..T+5 daily high versus the T-day reference reaches net +2% after `cost_model_v1`.

This is a daily-bar approximation, not a fill simulator.

0.5%–9.5% remains L2 routing / research ablation (`WITH_GATE` vs `WITHOUT_GATE`). Production Alpha no longer re-applies it as a strategy gate.

## Outcome schema (actual)

`returns` remains the fact owner under schema v6. T+1..T+5 are nested `payload.days["1".."5"]` with `SETTLED` or `MISSING`. There is no per-horizon row table. Aggregate `outcome_id = decision_id`. Horizon identity is `horizon_outcome_id = decision_id:horizon`. `fetch_horizon_outcomes(decision_id)` always returns all five days. Memory rebuild default is FULL; missing `knowledge_available_at` is fail-closed.

## Tests

`python -m pytest tests/ -q --tb=line` → 378 passed.

Official settled Top1/Top3 `sample_count` = 0. Alpha status remains DATA_INSUFFICIENT. Live 2026-09-04 ticket: **NO_OFFICIAL_PRODUCTION_TICKET**. See `PHASE_2_3_PRODUCTION_CONTRACT_CLEANUP.md`.

Production ticket = T-day investment research observation, not a same-day short-term buy print. Outcome = whether `opportunity_5d` appears on T+1..T+5.
