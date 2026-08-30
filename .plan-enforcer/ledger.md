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
| T10 | Historical truth classification | verified | 327 categorized; 92.61% canonical | D7 | no synthetic decision IDs; unresolved identity preserved |
| T11 | Minimal alpha and OOS accounting | verified | v4 price-only; no incremental families | D7,D8 | FULL below PRICE; BUY remains blocked |
| T12 | Regression and artifact rebuild | verified | 118 tests; artifacts rebuilt; 17/17 health | V5 | research artifacts use database facts only |
| T13 | Graph, commit, and push | verified | af110db indexed; push pending | C:af110db,V6 | final graph refresh follows amend |

## Decision Log

| ID | Type | Scope | Reason | Evidence |
|---|---|---|---|---|
| D1 | pivot | plan | New closure task after production-truth-lock | user package |
| D2 | unplanned | xiaogu_db.py | Drop lineage_id PK because scanner is 1 lineage to N snapshots | live schema + runner_v2 |
| D3 | pivot | plan | Re-open closure for historical snapshot_id PK and missing-as-zero | user 7c5b163 package |
| D4 | unplanned | xiaogu_core_alpha.py | Missing model must emit None probability, not 0.0 | spec 35 |
| D5 | pivot | scanner | Existing scanner still fetches expensive domains for full universe | user final repair package |
| D6 | unplanned | xiaogu_forward_runner.py | Production verification must compare stored payload identity | source audit |
| D7 | pivot | truth and alpha audit | Direct execution requires strict historical identity and minimal OOS alpha | user alpha-truth package + DB audit |
| D8 | delete | production alpha | Capital and other families have no stable OOS increment; retain price only | rebuilt v4 ablation |

## Verification Records

| ID | Evidence |
|---|---|
| V1 | pytest 76 passed; compileall 0; health 16/16 |
| V2 | historical PK snapshot_id; 0 unresolved snapshot_ids |
| V3 | dataset 0 CANONICAL / 3598 UNRESOLVED |
| V5 | 118 tests; compileall; health 17/17; v4 artifacts rebuilt |
| V6 | moderate graph index: 1003 nodes / 2612 edges |

## Reconciliation History

| Sweep | Result |
|---|---|
| R1 | T1-T5 verified; T6 open for understand/git |
| R2 | T1-T6 verified; BUY remains BLOCKED |
| R3 | T1-T8 verified; T9 open only for commit/push and final HEAD check |
| R4 | T1-T9 verified; BUY remains BLOCKED because calibration is DATA_INSUFFICIENT |
