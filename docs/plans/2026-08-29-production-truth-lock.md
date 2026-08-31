# Production Truth Lock

**Goal:** Keep one production chain and lock clock, DB persistence, evidence independence, decision-outcome binding, and fail-closed BUY.
**Constraints:** Modify existing owners only. No second alpha, decision, ranking, replay, or database. ADD-only schema. Do not weaken BUY gates.
**Out of scope:** New models, longer horizons, silent historical repair.

## Must-Haves

- MH1: Production uses a real decision clock and DB-verified snapshots
- MH2: Returns bind to decision_id; same-symbol trades cannot cross-read outcomes
- MH3: Capital evidence is independent by origin; main force / institution / hot money stay Observation → Evidence → Interpretation
- MH4: Repricing handmade score is not called probability; BUY stays blocked unless OOS-validated
- MH5: Recorder writes DB first; JSONL is audit; tests, compile, health, indexes, and git stay consistent

### Task 1: Production clock and provenance A:I1
- [ ] Production decision_clock is now/explicit current clock
- [ ] Stale snapshots emit STALE_DATA and cannot BUY
- Verification: stale and source_time-bypass tests

### Task 2: DB persistence verification A:I1
- [ ] verify_persisted_snapshot checks PostgreSQL
- [ ] persisted means DB_VERIFIED, not local files
- Verification: unpersisted PRODUCTION still blocked

### Task 3: Decision-outcome linkage A:I2
- [ ] ADD decision_id to picks/returns
- [ ] fetch_position_outcome requires decision_id
- Verification: same-symbol two-trade isolation test

### Task 4: Position state and snapshot selection A:I2
- [ ] FLAT/LONG isolated from BUY/HOLD/REDUCE/SELL
- [ ] Review selects unique trusted snapshot
- Verification: T+5 SELL/CLOSED and review tests

### Task 5: Capital evidence independence A:I3
- [ ] Independent identity is source_id + event_id
- [ ] Main force identity vs capital-flow observation
- Verification: duplicate LHB origin and CONVERGENCE tests

### Task 6: Supply, repricing naming, alpha gates A:I4
- [ ] Rename handmade probability to evidence score
- [ ] Keep BUY fail-closed without VALIDATED OOS
- Verification: existing BUY-block tests plus rename assertions

### Task 7: Recorder ownership A:I5
- [ ] DB transaction then JSONL; DB failure is not success
- [x] Obsidian failure logs a retry without a second memory
- Verification: recorder tests

### Task 8: Historical dataset and docs A:I5
- [ ] Bind historical rows by decision_id first; unbound stays UNRESOLVED
- [ ] AGENTS.md, README, rule_freeze match code
- Verification: historical builder tests and health check

### Task 9: Validation, indexes, git A:I5
- [ ] compileall, pytest, health, understand, codebase-memory, commit, push
- Verification: 0 compile errors, pytest pass, HEAD == origin/main
