# Xiaogu Repo Promotion Rules V3

Date: 2026-06-07
Task: `XIAOGU_REPO_INTEGRATION_V3`
Mode: governance only; no code, scoring, ledger, runner, or scoreboard changes.

## Promotion scope

Promotion means allowing a non-production repository output to affect one or more of:

- `candidate_score`
- `ranking_score`
- `production_pick`
- official PAPER_PICK decision evidence

Promotion is not required for research-only output that remains labeled and blocked from production scoring.

## Default rule

All `RESEARCH_REPO` outputs default to:

```text
BLOCKED_FROM_PRODUCTION_SCORING
```

A research output may be stored, reviewed, backtested, and diagnosed, but it must not change production score or production pick before approval.

## Required evidence before promotion

| Gate | Required evidence | Pass condition |
|---|---|---|
| Unique capability | Documented signal or diagnostic gap | Capability is not already covered by Xiaogu Native Evidence, VEI, Qlib, or existing approved tools. |
| Backtest evidence | Historical no-leakage replay | Improves target metrics without degrading hard safety gates. |
| Forward evidence | Forward paper observations | Improves pick quality/profit/limit-up diagnosis under current chain conditions. |
| Attribution evidence | Qlib/diagnosis attribution | Benefit is explainable by feature contribution and failure modes are known. |
| Evidence contract | T-day visible input lineage | No future fields, stale data, broker-only data, or unverifiable scraping dependency. |
| Safety contract | PAPER_ONLY / NO_TRADE / no broker endpoint | No order execution capability or credential dependency. |
| Context cost | Added context/tooling cost is justified | Net simplification or clear quality gain. |
| Approval | Explicit approval record | Human-approved promotion decision exists. |

## Promotion states

| State | Meaning | Production scoring allowed |
|---|---|---:|
| `RESEARCH_ONLY` | Idea/factor/strategy/diagnosis experiment only | no |
| `BACKTEST_READY` | Has historical replay path but no forward proof | no |
| `FORWARD_OBSERVATION` | Has forward evidence collection but no attribution approval | no |
| `ATTRIBUTION_REVIEW` | Attribution under Qlib/diagnosis review | no |
| `PROMOTION_CANDIDATE` | Evidence package complete and awaiting approval | no |
| `PROMOTED_CORE_FEATURE` | Approved feature/output; production-eligible under controls | yes |
| `REJECTED` | Insufficient evidence or bad tradeoff | no |
| `RETIRED_SOURCE_ONLY` | Source retained only | no |

## Promotion package contents

A promotion package must include:

1. Repo name and output name.
2. Existing Xiaogu gap addressed.
3. Unique capability statement.
4. Input fields and source lineage.
5. Explicit future-field exclusion statement.
6. Backtest method and metrics.
7. Forward evidence method and metrics.
8. Attribution report.
9. Failure mode report.
10. Context/maintenance cost assessment.
11. Safety assertion: `PAPER_ONLY`, `NO_TRADE`, `NO_BROKER_LOGIN`, `NO_ORDER_ENDPOINT`.
12. Approval decision.

## Metrics to evaluate

Promotion must improve one or more target quality metrics without weakening hard safety gates:

- ticket quality
- next-day-high profitability signal
- limit-up hit or limit-up-touch diagnosis
- fried-board risk avoidance
- weak-close/high-open-low-close avoidance
- market regime fit
- candidate evidence completeness
- attribution clarity
- false-positive reduction
- context/runtime simplicity

## Automatic rejection conditions

Reject promotion if any item is true:

- Uses future result fields during T-day decision.
- Requires broker credentials or order endpoint.
- Writes or mutates ledgers outside the stable recorder/filler path.
- Requires daily loading of retired or research repos into main context.
- Cannot show traceable source evidence.
- Cannot reproduce backtest or forward evidence.
- Improves average return only by increasing hard-gate risk.
- Degrades no-trade / paper-only controls.
- Duplicates VEI/Qlib/Xiaogu native capabilities without clear added value.
- Adds complexity without measurable quality gain.

## Retired repo re-admission

A retired repo cannot be re-enabled by changing labels. It must pass the full new repository admission policy:

```text
unique_capability
+ documented_gap
+ backtest_evidence
+ forward_evidence
+ attribution_evidence
+ approval
```

Without all required items, it remains `RETIRED_SOURCE_ONLY`.

## Research repo output handling

Research repo outputs may be written only as:

- research notes
- promotion candidate packages
- diagnosis hints
- attribution hypotheses
- offline backtest artifacts

They must not be written as:

- production score authority
- production pick authority
- official ledger mutation
- runner gate override
- hidden score delta

## Approval rule

Approval must be explicit. Absence of rejection is not approval.

Until approval exists, the safe interpretation is:

```text
research output = diagnostic evidence only
```

## Task boundary

This document defines rules only. It does not promote any repo, change any feature, modify scoring, or execute integration.
