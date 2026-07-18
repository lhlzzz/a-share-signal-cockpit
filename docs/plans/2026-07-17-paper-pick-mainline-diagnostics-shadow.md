# PAPER_PICK Mainline Diagnostics and Shadow Replay Task Package

**Goal:** Build a complete diagnostic and shadow-replay package for the recent PAPER_PICK failure mode where official picks miss the market mainline. The package must identify whether failures come from mainline data coverage, candidate-pool membership, eligibility gates, ranking weights, or risk under-penalization, without changing production ranking or freezing PAPER_PICK.

**Constraints:**
- Do not change `formal_candidate_sort_key()`, `official_pick_priority()`, production ranking weights, `PAPER_PICK` eligibility semantics, or freeze/unfreeze policy in this phase.
- Do not turn `sector_follower` into a production-eligible layer in this phase.
- Use existing files before creating new files. Preferred owners are `xiaogu_backtest_v0_1.py`, `xiaogu_signal_effectiveness_v0_1.py`, `xiaogu_forward_d1_1450_runner_v0_1.py`, `xiaogu_eastmoney_web_tabs_scan_v0_1.py`, and existing tests.
- Treat current `production_ranking_change_gate=LOCKED` as binding until at least 10 comparable mainboard PAPER_PICK dates and non-degrading shadow evidence exist.
- Keep all work paper-only / no-trade.

**Out of scope:**
- Production ranking changes.
- Freezing or force-selecting a PAPER_PICK.
- Real trading, broker execution, or position automation.
- Blind deletion of old records.
- Creating a parallel scanner, runner, or scoring engine.

**Relevant current facts:**
- Current report: `data/backtest/db_cohort_report_20260717_103239.json`.
- Current strategy status: `INSUFFICIENT_COMPARABLE_SAMPLE`.
- Current production ranking gate: `LOCKED`.
- Current allowed actions: diagnose, shadow replay, case book.
- Current forbidden actions: change formal candidate sort key, change production ranking weights, freeze PAPER_PICK.
- Recent failure pattern: PAPER_PICK can miss live market mainline such as innovation medicine / electricity because mainline signals are inputs to candidate scoring, not the top-level decision axis.

## Must-Haves

- MH1: Produce a daily mainline-hit diagnostic that says whether each PAPER_PICK was inside the day's top market themes and whether better mainline candidates existed in the pool. A:I1 A:I5
- MH2: Separate each miss into one of four actionable buckets: `MAINLINE_NOT_IN_DATA`, `MAINLINE_NOT_IN_POOL`, `MAINLINE_BLOCKED_BY_GATE`, or `MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK`. A:I1 A:I5
- MH3: Add shadow-replay variants for mainline-first selection, sector-follower inclusion, low-position catalyst uplift, limit-up gene uplift, and failed-limit/high-popularity risk penalties without changing production selection. A:I1
- MH4: Extend case-book output so each comparable date shows the market mainline, PAPER_PICK sector alignment, best available mainline candidate, blockers, T+1 return, and recommended fix direction. A:I1
- MH5: Preserve strategy discipline: production ranking remains locked until sample and replay gates pass; reports must explicitly show why production is still locked or what evidence would unlock it. A:I1
- MH6: Provide targeted automated tests and one DB cohort validation command that prove the new diagnostics and shadow replays run on existing persisted data. A:I5

### Task 1: Establish baseline and guardrails A:I1 A:I5
- [ ] Record the current baseline from `data/backtest/db_cohort_report_20260717_103239.json`: `paper_pick_performance_gate`, `paper_pick_loss_attribution`, `shadow_ranking_replay`, `limitup_gene_shadow_replay`, `paper_pick_vs_pool_diagnostic`, `strategy_status`, and `production_ranking_change_gate`.
- [ ] Add or update a backtest report section named `mainline_diagnostic_gate` with initial status `DIAGNOSTIC_ONLY` and a field `production_mutation_allowed=false`.
- [ ] Ensure this section carries the current locked constraints: minimum comparable dates = 10, no production ranking change, no PAPER_PICK freeze.
- [ ] Verification: run `python3 xiaogu_backtest_v0_1.py --db-cohort-report --start 2026-06-20 --end 2026-07-16` and confirm the JSON report still contains `production_ranking_change_gate.status == "LOCKED"` when sample count is below 10.

### Task 2: Build daily market mainline extraction from existing persisted data A:I1 A:I5
- [ ] In the existing backtest/reporting owner, add a helper that derives daily top themes from already persisted scan/candidate data rather than live web calls.
- [ ] Source priority for mainline extraction:
  1. candidate/bundle fields: `predicted_sector`, `sector_prediction_boost`, `sector_opportunity_tags`, `sector_news_strength`, `sector_opportunity_score`, `main_theme_core_score`, `main_theme_alignment_score`;
  2. scan summary / evidence rows if present: concept board, industry board, concept capital flow, sector fund flow;
  3. daily candidate pool aggregation by sector tags when raw board rows are unavailable.
- [ ] Normalize aliases so common sector names that describe the same mainline are grouped, for example `创新药`, `医药`, `CRO`, `生物医药`; `电力`, `电网`, `绿色电力`, `特高压`.
- [ ] Bound the extraction to top 3 and top 5 daily themes, each with score components: breadth/count, fund flow, avg candidate score, limit-up/near-limit signal count, and evidence source.
- [ ] Do not fetch live data in this report path. If persisted evidence is missing, emit `MAINLINE_DATA_PARTIAL` instead of silently inventing a theme.
- [ ] Verification: add a test fixture with candidates tagged as innovation medicine / electricity and assert that the extractor returns the expected top theme with source marked as persisted candidate evidence.

### Task 3: Add PAPER_PICK mainline-hit and miss-bucket diagnostics A:I1 A:I5
- [ ] For every comparable PAPER_PICK date, compute `paper_pick_mainline_hit_top3`, `paper_pick_mainline_hit_top5`, `paper_pick_theme_tags`, and `paper_pick_mainline_alignment_score`.
- [ ] For the same date, scan top10/mainboard candidates and identify `best_mainline_candidate` using T+1 return only for diagnostic attribution, not for production selection.
- [ ] Classify each day into exactly one primary bucket:
  - `MAINLINE_NOT_IN_DATA`: persisted data cannot identify any reliable mainline.
  - `MAINLINE_NOT_IN_POOL`: a mainline exists, but no candidate in the pool maps to it.
  - `MAINLINE_BLOCKED_BY_GATE`: a mainline candidate exists but is blocked by eligibility/exclusion reasons.
  - `MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK`: a clean or near-clean mainline candidate exists but production picked a non-mainline candidate.
- [ ] Preserve secondary tags when useful: `SECTOR_FOLLOWER_DIAGNOSTIC_ONLY`, `LOW_POSITION_CATALYST_UNDERWEIGHTED`, `LIMITUP_GENE_UNDERWEIGHTED`, `FAILED_LIMITUP_RISK_UNDERPENALIZED`, `HIGH_POPULARITY_REVERSAL_RISK`, `RANK4_TO_6_UNDERVALUED`.
- [ ] Verification: add tests covering all four primary buckets with synthetic candidate rows and expected diagnostic labels.

### Task 4: Audit candidate-pool coverage and sector/member mapping A:I1 A:I5
- [ ] Add a report subsection `mainline_pool_coverage` listing, by date, whether top mainline sectors had any candidates in `paper_scoring_candidates`, `search_rows`, top10, and mainboard top10.
- [ ] For each top mainline, report counts by source layer: `L6_SECTOR_CATALYST`, `L8_LIMITUP_REASON_PROPAGATION`, `L3_FUND_FLOW`, `L2_LIMIT_STRENGTH`, `sector_follower`, `sector_contrarian`, `formal_high_score`.
- [ ] Report alias/mapping misses separately from true absence: `MAINLINE_ALIAS_UNMAPPED` vs `MAINLINE_NO_SYMBOL_IN_POOL`.
- [ ] Check recent case examples explicitly in the case book when data exists: innovation medicine day, electricity day, 横店东磁, 亿道信息/001314.
- [ ] Verification: run DB cohort report and confirm `mainline_pool_coverage` has one row per comparable date and does not require live network access.

### Task 5: Add mainline-first shadow replay variants only A:I1
- [ ] Add a `mainline_shadow_replay` section to the DB cohort report, separate from existing production ranking fields.
- [ ] Implement these diagnostic variants as pure report-time candidate selectors:
  - `mainline_first_shadow`: prioritize top3 mainline alignment before current final score.
  - `mainline_limitup_gene_shadow`: mainline-first plus existing limit-up gene uplift signals.
  - `mainline_low_position_catalyst_shadow`: mainline-first plus low-position catalyst uplift.
  - `mainline_risk_penalty_shadow`: mainline-first plus failed-limit/high-popularity reversal risk penalties.
  - `mainline_composite_shadow`: combines only the best non-degrading components after individual variants are measured.
- [ ] Each variant must output selected symbol, selected rank, mainline tag, T+1 return, limit-up hit, large-loss hit, and whether it beats baseline.
- [ ] Do not write these scores back into production candidate rows or scoring config.
- [ ] Verification: assert the report includes baseline and all variant names, and that `selected_for_production=false` while sample count is below gate.

### Task 6: Add sector-follower shadow without production eligibility changes A:I1
- [ ] Keep the existing production blocker `sector_follower_diagnostic_only` unchanged.
- [ ] Add a shadow-only variant `sector_follower_mainline_shadow` that can select a sector-follower candidate only when all conditions are met:
  - candidate maps to top3 daily mainline;
  - main force / fund-flow evidence is not heavy negative;
  - close position is below high-chase threshold;
  - no regulatory hard block;
  - no near-limit unconfirmed risk;
  - no `mainboard_auxiliary_evidence_hard_block` unless a documented shadow exception applies.
- [ ] Report how often sector-follower candidates were present, blocked, and would have outperformed PAPER_PICK.
- [ ] Verification: add a unit test where production still blocks `sector_follower_diagnostic_only`, while the shadow report records a hypothetical sector-follower selection.

### Task 7: Strengthen failed-limit and high-popularity reversal diagnostics in shadow A:I1
- [ ] In report-only logic, compute a `failed_limit_reversal_risk_score` using existing fields where available: broken limit risk, intraday pullback, weak close risk, high turnover, high popularity/heat without limit-up confirmation, main force sell, and prior failed limit-up signals.
- [ ] Add a `high_popularity_reversal_risk_score` using popularity/heat fields and weak confirmation fields already persisted in candidates or evidence domains.
- [ ] Use these scores only in `mainline_risk_penalty_shadow` and `mainline_composite_shadow`.
- [ ] Extend `paper_pick_loss_attribution` so days like 2026-07-16 can show whether risk under-penalization was the primary reason.
- [ ] Verification: add a regression test where a failed-limit/high-popularity candidate loses in shadow replay but production ranking output remains unchanged.

### Task 8: Extend case book for human review A:I1
- [ ] Extend `paper_pick_case_book` or add a sibling section `mainline_case_book` in the existing backtest report.
- [ ] Each case row must include: trade date, market mainline top3/top5, PAPER_PICK symbol/name/rank/sector tags, T+1 return, best mainline candidate, best pool candidate, miss bucket, blockers/exclusion reasons, shadow variant winners, and recommended next action.
- [ ] Recommended actions must be selected from a closed set: `WAIT_FOR_SAMPLE`, `FIX_ALIAS_MAPPING`, `FIX_POOL_COVERAGE`, `SHADOW_REPLAY_MORE_DATES`, `CONSIDER_MAINLINE_WEIGHT_AFTER_GATE`, `CONSIDER_SECTOR_FOLLOWER_AFTER_GATE`, `INCREASE_RISK_PENALTY_AFTER_GATE`.
- [ ] Avoid free-text-only conclusions; keep fields machine-readable for later review.
- [ ] Verification: run report and inspect that every comparable PAPER_PICK case has a `mainline_case_book` row with non-empty `recommended_next_action`.

### Task 9: Extend signal effectiveness persistence for mainline diagnostics A:I1 A:I5
- [ ] Add mainline-related signal buckets to `xiaogu_signal_effectiveness_v0_1.py` replay/persistence without changing selection logic:
  - `MAINLINE_HIT_TOP3`
  - `MAINLINE_HIT_TOP5`
  - `MAINLINE_AVAILABLE_BUT_NOT_SELECTED`
  - `SECTOR_FOLLOWER_SHADOW`
  - `MAINLINE_LIMITUP_GENE`
  - `FAILED_LIMIT_REVERSAL_RISK`
- [ ] Ensure non-trading-day filtering remains active for DB replay.
- [ ] Persist aggregate counts, T+1 average, limit-up rate, and large-loss rate for these diagnostic buckets.
- [ ] Verification: run `python3 xiaogu_signal_effectiveness_v0_1.py --ledger forward_paper_ledger_v0_1.jsonl --min-samples 20 --source db --persist --json` and confirm new buckets are present when matching rows exist.

### Task 10: Add targeted tests and fixtures A:I5
- [ ] Add focused tests in existing test files rather than creating a new broad test file unless necessary.
- [ ] Required test cases:
  - mainline extractor groups aliases and returns top themes from persisted candidate rows;
  - miss-bucket classifier emits each of the four primary buckets;
  - sector-follower remains blocked for production but appears in shadow replay;
  - mainline-first shadow beats baseline on a controlled fixture without changing production decision;
  - failed-limit/high-popularity risk penalty changes only shadow selection;
  - non-trading-day records remain excluded from signal effectiveness and backtest diagnostics.
- [ ] Verification command: `python3 -m pytest tests/test_xiaogu_a_share_forward_runner.py -q -k 'mainline or sector_follower or shadow_replay or non_trading_dates'`.

### Task 11: Run DB cohort validation and summarize gates A:I1 A:I5
- [ ] Run `python3 xiaogu_backtest_v0_1.py --db-cohort-report --start 2026-06-20 --end 2026-07-16` after implementation.
- [ ] Confirm these report sections exist: `mainline_diagnostic_gate`, `mainline_pool_coverage`, `mainline_shadow_replay`, `mainline_case_book`, existing `production_ranking_change_gate`.
- [ ] Confirm `production_ranking_change_gate.status` remains `LOCKED` until minimum comparable sample and non-degrading shadow requirements pass.
- [ ] Confirm report explicitly states allowed actions remain diagnostic/shadow/case-book while locked.
- [ ] Verification: capture the generated report path and include key metrics: baseline avg T+1, each shadow variant avg T+1, win rate, limit-up rate, large-loss rate, sample count, and lock reason.

### Task 12: Decide next phase only from evidence A:I1
- [ ] If sample count is still below 10, end with `WAIT_FOR_SAMPLE` and list which diagnostics are now ready for future dates.
- [ ] If sample count reaches 10 and one or more shadow variants beat baseline without worse large-loss or limit-up rates, prepare a separate future production-change plan; do not include that production change in this task package.
- [ ] If the dominant bucket is `MAINLINE_NOT_IN_DATA`, prepare a future data-coverage plan.
- [ ] If the dominant bucket is `MAINLINE_NOT_IN_POOL`, prepare a future candidate-generation/mapping plan.
- [ ] If the dominant bucket is `MAINLINE_BLOCKED_BY_GATE`, prepare a future eligibility-gate review plan.
- [ ] If the dominant bucket is `MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK`, prepare a future production ranking review plan after gates pass.
- [ ] Verification: final report summary contains one and only one `next_phase_recommendation` selected from the listed options.

## Acceptance Criteria

- The implementation produces diagnostics that explain whether mainline failure is data, pool, gate, ranking, or risk-related.
- The implementation adds only diagnostic and shadow replay outputs; production selection behavior is unchanged.
- Existing return coverage, full-chain completion, non-trading-day filtering, and production ranking lock gates continue to run.
- Targeted tests pass.
- DB cohort report can be generated from persisted data without live network fetches.

## Validation Commands

```bash
python3 -m pytest tests/test_xiaogu_a_share_forward_runner.py -q -k 'mainline or sector_follower or shadow_replay or non_trading_dates'
python3 xiaogu_backtest_v0_1.py --db-cohort-report --start 2026-06-20 --end 2026-07-16
python3 xiaogu_signal_effectiveness_v0_1.py --ledger forward_paper_ledger_v0_1.jsonl --min-samples 20 --source db --persist --json
```

## Execution Notes

- Prefer modifying existing report builders and test helpers.
- Do not add a new strategy engine.
- Do not use T+1 returns inside any production-like selector; T+1 is allowed only for backtest attribution and shadow evaluation.
- Keep all new diagnostic fields serializable JSON so reports can be compared across dates.
- Any future production-ranking change requires a separate plan after sample and shadow gates pass.
