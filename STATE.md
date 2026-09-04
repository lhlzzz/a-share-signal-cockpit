# Xiaogu STATE

Date: 2026-09-04

Single-system convergence is complete on the existing Xiaogu owners. This is not a parallel rebuild.

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
| Memory | PostgreSQL facts + Obsidian notes keyed by `paper_signal_id` / `decision_id` |
| Execution universe | MAIN_BOARD_ONLY |

## Target

Opportunity Target = any of T+1..T+5 daily high versus the T-day reference reaches net +2% after `cost_model_v1`.

0.5%–9.5% price window remains L2 routing / research ablation (`WITH_GATE` vs `WITHOUT_GATE`). It is not a frozen Alpha rule.

## Tests

`pytest tests/ -x -q` → 346 passed.
