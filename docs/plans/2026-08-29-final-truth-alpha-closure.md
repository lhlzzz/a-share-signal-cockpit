# Final Truth + Alpha Closure

**Goal:** Repair snapshot/lineage identity, keep historical values unrewritten, rebuild 5D ground truth, and revalidate the existing five-day alpha without opening BUY unless real OOS increment exists.
**Constraints:** Modify existing owners only. No second alpha, decision, ranking, replay, or database. Do not forge snapshot_id from lineage_id. Do not fill missing evidence with 0. Do not weaken BUY gates.
**Out of scope:** New models, longer horizons, opening BUY without VALIDATED OOS.

## Must-Haves

- MH1: snapshot_id is the row identity; lineage_id is one scanner run; ALTER failure raises
- MH2: same snapshot_id + same payload hash is idempotent; different payload is SNAPSHOT_IDENTITY_CONFLICT
- MH3: missing T-day evidence stays missing; collapsed families leave production alpha
- MH4: rebuild historical_5d_profit_window_dataset after identity repair; old OOS stays PRE_REPAIR_RESULT
- MH5: BUY remains blocked unless Trusted+OOS+separation+monotonicity+increment all pass

### Task 1: Schema identity
- [x] canonical_historical_snapshots PRIMARY KEY (snapshot_id); INDEX lineage_id and (trade_date, symbol)
- [x] ensure_production_schema ADD/INDEX/CONSTRAINT only; no lineage_id fallback; ALTER failure raises
- [x] historical snapshot_id recovery is a separate migration; unrecoverable rows stay UNRESOLVED
- Verification: schema audit prints EXISTS/MISSING/CONFLICT; migration-failure test raises

### Task 2: Persistence identity
- [x] record_snapshot / record_canonical_historical_snapshots ON CONFLICT (snapshot_id)
- [x] payload identity match = idempotent; mismatch = SNAPSHOT_IDENTITY_CONFLICT
- Verification: two symbols share lineage; conflicting payload raises

### Task 3: Decision linkage
- [x] fetch_position_outcome(decision_id); same-symbol trades stay isolated
- [x] missing historical decision_id is UNRESOLVED and cannot enter canonical training
- Verification: existing same-symbol isolation test

### Task 4: Missing evidence and feature collapse
- [x] persistence/acceleration/gap/execution/risk do not default missing to 0
- [x] diagnose count/missing/unique/mean/std/percentiles; unique_count<=2 or std~0 or missing>=95% = FEATURE_COLLAPSED
- Verification: zero-fill diagnostics and collapse tests

### Task 5: Ground truth rebuild and OOS
- [x] rebuild dataset; train-only imputer; baseline/ablation/OOS
- [x] collapsed or no-increment families are RESEARCH_ONLY
- Verification: BUY blocked unless VALIDATED

### Task 6: Docs, health, compile, tests, indexes, git
- [x] compileall, pytest, health, understand, codebase-memory, commit, push
- Verification: 0 compile errors, pytest pass, HEAD == origin/main
