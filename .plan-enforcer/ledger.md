# Plan Enforcer Ledger

Plan: `docs/plans/2026-08-29-production-truth-lock.md`
Tier: structural

| ID | Task | Status | Evidence | Chain | Notes |
|---|---|---|---|---|---|
| T1 | Production clock and provenance | verified | stale-clock tests, compile, health 16/16 | D3 | production uses UTC decision clock and source age |
| T2 | DB persistence verification | verified | DB persistence/conflict tests, hash verification, health 16/16 | D3 | exact snapshot identity and payload hash are required |
| T3 | Decision-outcome linkage | verified | fetch by decision_id | D3 | same-symbol isolation covered |
| T4 | Position state and snapshot selection | verified | T+5 and PostgreSQL position-review tests | D3 | FLAT/LONG remains separate from action |
| T5 | Capital evidence independence | verified | origin tests pass | D3 | main force remains direct-only |
| T6 | Supply, repricing, alpha gates | verified | missing-value, OOS fail-closed, and alpha gate tests | D4 | current artifact remains DATA_INSUFFICIENT |
| T7 | Recorder ownership | verified | DB-first test passes | D3 | Obsidian is audit memory only |
| T8 | Historical dataset and scanner layering | verified | historical builder tests plus Level 0-3 scanner tests | D3 | canonical historical set remains UNRESOLVED where decision_id is absent |
| T9 | Validation, indexes, git | verified | 78 tests, compileall, health 16/16, graph refresh, pushed main | D3 | HEAD matches origin/main |

## Decision Log

| ID | Type | Scope | Reason | Evidence |
|---|---|---|---|---|
| D1 | pivot | plan | New closure task after production-truth-lock | user package |
| D2 | unplanned | xiaogu_db.py | Drop lineage_id PK because scanner is 1 lineage to N snapshots | live schema + runner_v2 |
| D3 | pivot | plan | Re-open closure for historical snapshot_id PK and missing-as-zero | user 7c5b163 package |
| D4 | unplanned | xiaogu_core_alpha.py | Missing model must emit None probability, not 0.0 | spec 35 |
| D5 | pivot | scanner | Existing scanner still fetches expensive domains for full universe | user final repair package |
| D6 | unplanned | xiaogu_forward_runner.py | Production verification must compare stored payload identity | source audit |

## Verification Records

| ID | Evidence |
|---|---|
| V1 | pytest 76 passed; compileall 0; health 16/16 |
| V2 | historical PK snapshot_id; 0 unresolved snapshot_ids |
| V3 | dataset 0 CANONICAL / 3598 UNRESOLVED |

## Reconciliation History

| Sweep | Result |
|---|---|
| R1 | T1-T5 verified; T6 open for understand/git |
| R2 | T1-T6 verified; BUY remains BLOCKED |
| R3 | T1-T8 verified; T9 open only for commit/push and final HEAD check |
| R4 | T1-T9 verified; BUY remains BLOCKED because calibration is DATA_INSUFFICIENT |
