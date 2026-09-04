# Xiaogu STATE

Date: 2026-09-04

Phase 2 observation / OOS tracking is in place on the existing Xiaogu owners. This is not Xiaogu 4.0 and not a parallel rebuild. Official forward OOS has not started: **NO_REAL_OOS_EVIDENCE_YET**.

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
| Clock | one batch `decision_clock` from `xiaogu_forward_runner.main()` via `production_now()`; workers do not call `production_now()` |
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

`python -m pytest tests/ -x -q --tb=line` → 367 passed.

Official settled Top1/Top3 `sample_count` = 0. Alpha status remains DATA_INSUFFICIENT. See `PHASE_2_TRUTH_REPORT.md`.
