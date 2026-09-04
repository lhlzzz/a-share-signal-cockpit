# FINAL_TRUTH_HARDENING_REPORT

Date: 2026-09-04  
Baseline: `main = 9ac06ed1c3eb6bd75b9868af085bcba104280c66`

This is in-place hardening on the existing Xiaogu owners. It is not Xiaogu 4.0, not a parallel rebuild, and not a second selector / ranker / alpha / decision / memory / database.

## 1. Modified files

Prior truth hardening plus this micro-hardening pass:

- `xiaogu_forward_runner.py`
- `xiaogu_core_alpha.py`
- `xiaogu_research_context.py`
- `xiaogu_forward_paper_recorder_v0_1.py`
- `xiaogu_forward_result_filler_v0_1.py`
- `xiaogu_db.py`
- `xiaogu_portfolio_decision.py`
- `xiaogu_horizon_evaluation.py`
- `tests/test_single_system_convergence.py`
- `tests/test_data_contract.py`
- `tests/test_execution_board.py`
- `STATE.md`
- `FINAL_SYSTEM_AUDIT.md`
- `FINAL_TRUTH_HARDENING_REPORT.md`

## 2. Deleted files

None. No new owner files were created (`selector.py`, `ranker.py`, `topk.py`, `alpha_v5.py`, `decision_engine_v2.py`, `second_memory.py` remain absent).

## 3. Deleted live paths

- Production Alpha `_signal_qualification` 0.5%–9.5% `PRICE_STRENGTH_OUT_OF_WINDOW` elimination
- Partial official selection after a permanent worker failure
- `execution_assumptions.slippage_included = false` while `cost_model_v1` includes slippage
- Historical / Obsidian outcome leakage keyed only by `signal_time` / note date
- Per-decision PostgreSQL persist loops that could leave a production run half-written

Live ownership remains on the original files. Compat / diagnostic fields (`expected_net_profit_window`, `PRODUCTION_TRADE_MODE = PAPER_PROFIT_WINDOW_5D`, `repricing_evidence_score` as DIAGNOSTIC_ONLY) were not promoted to a second production standard.

## 4. Schema / migration

Schema stays `xiaogu_production_schema_v6`. No v7 migration.

Outcome facts remain in the existing `returns` table as one nested JSON payload. There is no per-horizon row table.

`fetch_horizon_outcomes(decision_id)` returns nested `returns.payload.days` under schema v6. There is no `returns_horizons` table.

```
{
  "decision_id": decision_id,
  "outcome_id": decision_id,
  "days": {
    "1": {status: SETTLED|MISSING, horizon: 1, horizon_outcome_id: "decision_id:1", horizon_trade_date: ...},
    "2": ...,
    "3": ...,
    "4": ...,
    "5": ...
  }
}
```

Aggregate Outcome: `outcome_id = decision_id`.  
Horizon Outcome: `horizon_outcome_id = decision_id:horizon`.  
T+1..T+5 are always present. Missing days stay `MISSING` and are never omitted.

## 5. PIT changes

Historical PostgreSQL:

- Decision-era visibility uses `knowledge_available_at` (not `signal_time`).
- Outcome / `opportunity_5d` / `post_trade_review` / `failure_pattern` require `outcome_settled_at <= as_of`.

Obsidian:

- Decision notes: `knowledge_type=DECISION`, `knowledge_available_at`.
- Outcome updates: `knowledge_type=OUTCOME`, `outcome_available_at` / `settled_at`, same `paper_signal_id`.
- Reads at as_of=T hide outcome / review / attribution when the outcome stamp is after T.

## 6. Selection changes

Any unrecoverable candidate after `WORKER_RETRY_LIMIT + 1` attempts:

- `system_fault = True`
- `selection_status = ABSTAIN`
- `publishable = False`
- `top1 = None`
- `top3 = []`
- all `paper_observation = None`

Transient retry → recover still publishes. There is no PARTIAL_OBSERVATION / PARTIAL_SELECTION / PARTIAL_TOP1 / PARTIAL_TOP3.

0.5%–9.5% is L2 routing / research ablation only. Production qualification no longer re-applies it.

Selection score remains unique Alpha `selection_score` (`profit_window_alpha_5d_v4`). Tie-breakers: `decision_clock`, `snapshot_id`, `symbol`.

## 7. Outcome changes

`calculate_horizon_outcomes()` still owns T+1..T+5. Each day is `SETTLED` or `MISSING`. Horizon fields include `horizon` and `horizon_trade_date`. Cost semantics are `DAILY_BAR_APPROXIMATION`.

## 8. Memory changes

- Adapter write includes `knowledge_available_at` / `knowledge_type`.
- Adapter read filters by `knowledge_available_at` and hides unsettled outcomes.
- `rebuild_memory_from_postgresql(limit=None)` default is FULL rebuild. `limit=N` is an explicit bounded/diagnostic rebuild.
- Rebuild does not invent PIT availability from `signal_time` or `created_at`. Missing or empty `knowledge_available_at` skips that DECISION note (`skipped_missing_knowledge_available_at_count`).
- Rebuild diagnostics distinguish `rebuilt` / `skipped` / `failed`.
- Daily note remains a summary view. Identity is `paper_signal_id` / `decision_id`.

## 9. Atomic transaction changes

Production persist path:

BUILD → VALIDATE → `persist_production_facts()` BEGIN → snapshots / decisions / paper → COMMIT.

Any key PostgreSQL failure ROLLBACKs, sets `production_run.status = FAILED`, `publishable = false`, and ABSTAINS official Top1/Top3. JSONL audit may still append after a successful DB commit; it is not the fact owner.

## 10. OOS changes

Unchanged contract, documented honestly:

- chronological split
- purge
- embargo ≥ 5 trading days
- daily grouped Top1 / Top3
- target = `opportunity_5d`
- `evaluate_price_gate_ablation()` RESEARCH_ONLY, `production_frozen = False`

`random` in `xiaogu_horizon_evaluation.py` is only the RANDOM baseline diagnostic, not the walk-forward split.

## 11. Tests count

`tests/test_single_system_convergence.py`: 32 tests.

Full suite: 362 tests.

## 12. pytest result

Command captured 2026-09-04:

```
python -m pytest tests/ -x -q --tb=line
362 passed in 94.06s (0:01:34)
exit=0

python -m pytest tests/test_single_system_convergence.py -q --tb=line
32 passed

python -m compileall -q .
COMPILE_EXIT:0
```

Do not keep the earlier `355 passed in 83.45s` / `93.20s` timings. Those numbers are superseded by this run.

Live-owner static scan (Python owners only; tests/docs may keep historical tokens):

- `research_consumed` / `PARTIAL_OBSERVATION`: none in `xiaogu_*.py`
- `PRICE_STRENGTH_OUT_OF_WINDOW` / `SIGNAL_PCT_MIN` / `SIGNAL_PCT_MAX`: none in `xiaogu_*.py`
- `knowledge_available_at` fabricated from `signal_time` / `created_at`: none in `xiaogu_*.py`
- `used_downstream` auto-set from `provider_succeeded`: none in `xiaogu_*.py`
- aggregate `outcome_id = decision_id:horizon`: none in `xiaogu_*.py`

## 13. git commit

Previous truth-hardening code: `a760f9e44cf131d27310b91fe5de3207c25a50ce`

`fix: harden Xiaogu PIT, selection, outcome, and atomic persistence`

There is no `ace319d` hardening SHA in this tree.

Baseline: `9ac06ed1c3eb6bd75b9868af085bcba104280c66`

This micro-hardening commit SHA is recorded after git commit. It does not change production owners.

## 14. git status

Working tree is dirty until the micro-hardening commit. After that commit, `main` is ahead of `origin/main`. Not pushed unless requested.

## 15. Remaining risks

- `returns` is still one nested payload, not a `(decision_id, horizon)` row table. Queryability of a single day depends on JSON, not a unique DB constraint.
- Historical rows without `knowledge_available_at` are fail-closed (hidden / skipped on rebuild) rather than inferred from `signal_time` or `created_at`.
- JSONL / Obsidian audit writes after DB commit can still fail independently; PostgreSQL remains authoritative.
- Alpha remains `DATA_INSUFFICIENT` / `EXPERIMENTAL` until OOS gates pass. BUY stays BLOCKED.
- Schema stayed v6; no new calendar or snapshot rewrite.
- `usable_evidence_count` only counts explicit evidence dicts with identity + PIT stamps. Provider field bags without evidence lists correctly stay at 0 usable, even when the builder succeeded.

---

Xiaogu remains ONE production system.

ONE TARGET: `opportunity_5d`  
ONE ALPHA: `profit_window_alpha_5d_v4`  
ONE SELECTION: `attach_top_paper_observations`  
ONE DECISION: `evaluate_candidate_bundle`  
ONE GATE: `evaluate_production_gates`  
ONE FACT DATABASE: PostgreSQL  
ONE SEMANTIC MEMORY: Obsidian  

BUY: BLOCKED  
LIVE: DISABLED
