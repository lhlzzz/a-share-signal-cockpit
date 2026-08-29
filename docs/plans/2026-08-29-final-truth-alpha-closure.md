# Final Truth + Alpha Closure

**Goal:** Fail-close schema, snapshot identity, decision-outcome binding, evidence independence, and keep BUY blocked unless real OOS validates a smaller alpha.
**Constraints:** Modify existing owners only. No second alpha, decision, ranking, replay, or database. Do not rewrite historical values. Do not weaken BUY gates.
**Out of scope:** New models, longer horizons, opening BUY without OOS PASS.

## Must-Haves

- MH1: Schema migration failures raise; startup audit prints COLUMN/INDEX/UNIQUE/FK
- MH2: snapshot_id is unique snapshot identity; lineage_id is scan identity
- MH3: Production clock, DB-verified persistence, unique snapshot selection
- MH4: Outcomes bind by decision_id; same-symbol trades stay isolated
- MH5: OOS evidence decides production-alpha permission; BUY stays blocked

### Task 1: Schema fail-closed A:I1
- [ ] Remove except-continue/pass from ensure_production_schema
- [ ] Audit columns/indexes/uniques/FKs; historical FK conflicts are counted not rewritten
- Verification: migration-failure test raises; health prints schema audit

### Task 2: Snapshot identity A:I1
- [ ] lineage_id is one scan; snapshot_id is one symbol snapshot
- [ ] Persist ON CONFLICT (snapshot_id); unique (lineage_id, symbol)
- Verification: two symbols share lineage, different snapshot_id

### Task 3: Persistence, clock, unique selection A:I2
- [ ] Production uses DB-verified snapshots and now() clock
- [ ] Position review selects unique trusted snapshot for symbol+date
- Verification: stale, unpersisted, duplicate-symbol selection tests

### Task 4: Decision-outcome isolation A:I2
- [ ] fetch_position_outcome(decision_id)
- [ ] Missing decision_id is OUTCOME_NOT_BOUND
- Verification: same-symbol two-trade isolation test

### Task 5: Evidence and supply states A:I3
- [ ] Observation/Evidence/Interpretation stay separated
- [ ] Supply states carry evidence/count/confidence
- Verification: existing independence tests plus supply state fields

### Task 6: Feature audit, ablation, OOS permissions A:I4
- [ ] Feature audit has mean/std/percentiles/missing/unique
- [ ] Collapsed or no-increment families lose production-alpha permission
- [ ] BUY remains blocked on EXPERIMENTAL/OOS fail
- Verification: collapse, 100% coverage, OOS-fail tests

### Task 7: Docs, health, compile, tests, indexes, git A:I5
- [ ] AGENTS/README/rule_freeze match current chain
- [ ] compileall, pytest, health, understand, codebase-memory, commit, push
- Verification: 0 compile errors, pytest pass, HEAD == origin/main
