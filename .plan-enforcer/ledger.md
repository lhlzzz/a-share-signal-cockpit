# Plan Enforcer Ledger

Plan: `docs/plans/2026-08-26-final-repricing-rebuild.md`
Tier: structural

| ID | Task | Status | Evidence | Chain | Notes |
|---|---|---|---|---|---|
| T1 | Audit Alpha, owner, replay, artifact, OOS | verified | source/index audit; owner confirmed | D2,V2 |  |
| T2 | Diagnose historical feature and probability data | verified | 3,598 rows; zero/variance stats | V2 |  |
| T3 | Add deterministic feature-group ablation report | verified | fixed split; 9 cumulative groups | V2 |  |
| T4 | Run cumulative and single-family ablations | verified | all requested groups; OOS fail | V2 |  |
| T5 | Apply only evidenced Alpha/gate correction | verified | calibrated != validated; BUY blocked | D3,V2 |  |
| T6 | Update focused regression tests | verified | pytest 51 passed | V3 |  |
| T7 | Run full validation and actual OOS/replay | verified | replay; health; smoke; OOS fail-closed | V4 |  |
| T8 | Refresh indexes, knowledge, commit, push | verified | index refreshed; commit pushed | C:FINAL,V5 |  |

## Decision Log

| ID | Type | Scope | Reason | Evidence |
|---|---|---|---|---|
| D1 | pivot | strategy target | User superseded 10D target with PROFIT_WINDOW_5D | latest task package |
| D2 | pivot | alpha validation | Current 5D chain is calibrated but OOS fail; diagnose before promotion | current task package |
| D3 | deviation | model status | Treat fitted/calibrated but OOS-failed artifact as EXPERIMENTAL | OOS separation and baseline evidence |

## Verification Records

| ID | Evidence |
|---|---|
| V1 | prior 5D contract validation at baseline |
| V2 | 3,598 canonical replay rows; OOS ROC-AUC 0.4313; BUY gates false |
| V3 | pytest 51 passed; compileall and diff check clean |
| V4 | DB replay PASS; health 14/14; direct /health 200; smoke no BUY |

## Reconciliation History

| Sweep | Result |
|---|---|
| Current | T1-T8 verified; production BUY remains blocked |
