# Plan Enforcer Ledger

Plan: `docs/plans/2026-08-29-final-truth-alpha-closure.md`
Tier: structural

| ID | Task | Status | Evidence | Chain | Notes |
|---|---|---|---|---|---|
| T1 | Schema identity | verified | snapshot_id PK live | D2,D3 | no lineage fallback |
| T2 | Persistence identity | verified | conflict test added | D3 | payload hash conflict |
| T3 | Decision linkage | verified | fetch by decision_id | D3 | UNRESOLVED stays |
| T4 | Missing evidence collapse | verified | missing stays None | D3,D4 | None != 0 |
| T5 | Ground truth + OOS | verified | 0 CANONICAL labels | D3 | PRE_REPAIR preserved |
| T6 | Docs, health, tests, git | in_progress | pytest 76 health 16/16 | D3,D4 | commit+push remaining |

## Decision Log

| ID | Type | Scope | Reason | Evidence |
|---|---|---|---|---|
| D1 | pivot | plan | New closure task after production-truth-lock | user package |
| D2 | unplanned | xiaogu_db.py | Drop lineage_id PK because scanner is 1 lineage to N snapshots | live schema + runner_v2 |
| D3 | pivot | plan | Re-open closure for historical snapshot_id PK and missing-as-zero | user 7c5b163 package |
| D4 | unplanned | xiaogu_core_alpha.py | Missing model must emit None probability, not 0.0 | spec 35 |

## Verification Records

| ID | Evidence |
|---|---|

## Reconciliation History

| Sweep | Result |
|---|---|
| R1 | T1-T5 verified; T6 open for understand/git |
