# Research Upgrade and Factor Cleanup

**Goal:** use the stabilized DB-backed review stack to improve xiaogu's pick quality and explanatory power by identifying which ranks, setup classes, and main-force/hot-money signals should be boosted, softened, or left unchanged.

**Constraints:** keep scanner-first / DB-first / runner-consume-only intact; do not add broker execution or a parallel analysis chain; prefer existing review and effectiveness scripts; any new bias must be reversible and evidence-backed.

**Out of scope:** infrastructure cleanup, memory tuning, contract rewrites, or adding new data collection pipelines.

## Must-Haves

- MH1: The current DB review and signal-effectiveness outputs are used to produce concrete rank/setup/signal conclusions, not just descriptive summaries.
- MH2: Instant winners, delayed winners, and missed winners are compared so the plan can distinguish fast payoff vs delayed payoff behavior.
- MH3: Main-force / hot-money signals are evaluated for actual predictive value, not only coverage completeness.
- MH4: At least one validated soft bias or ranking adjustment is written back into the runtime path and can be replayed.
- MH5: The resulting changes preserve reversibility and can be verified against historical samples.

### Task 1: Re-run DB review with the stabilized sample set
- [ ] Generate the current DB review report and extract rank bucket, setup type, decision quality, and daily top10 outputs from the filled DB.
- [ ] Identify the rank buckets and setup types that are consistently stronger or weaker on T+1 and note where T+2/T+3 show delayed payoff.
- [ ] Verification: a short review note or terminal snapshot showing the strongest and weakest rank/setup bands from the current DB-backed sample set.

### Task 2: Compare instant winners, delayed winners, and missed winners
- [ ] Use `xiaogu_signal_effectiveness_v0_1.py` to compare instant winners vs delayed winners vs missed winners on the existing ledger/DB-backed samples.
- [ ] Break out the same comparison by `setup_class` so delayed setups and instant setups are not mixed together.
- [ ] Verification: a report or saved artifact showing the payoff split and the sample counts for each class.

### Task 3: Validate main-force / hot-money signal usefulness
- [ ] Check whether main-force net inflow, hot-money buy behavior, northbound flow, and related capital-flow signals actually improve return quality beyond coverage presence.
- [ ] Separate signals that are only explanatory from signals that are actually predictive.
- [ ] Verification: a signal effectiveness table or note that identifies which capital-flow signals are candidates for boost, neutral, or penalty.

### Task 4: Convert the strongest findings into reversible runtime biases
- [ ] Apply one or more soft biases to the runner/scanner path for the strongest validated findings, such as rank band preference or setup-class preference.
- [ ] Keep the change reversible and document the rollback point or config entry.
- [ ] Verification: a replay or targeted test demonstrates the adjusted bias is active and does not break the existing paper-only contract.

### Task 5: Backtest the updated bias against the historical sample set
- [ ] Run the updated logic against historical samples and compare before/after on hit rate, average T+1 return, and delayed-win capture.
- [ ] Confirm the change does not improve one metric by clearly degrading another hidden metric without an explanation.
- [ ] Verification: a before/after summary showing whether the new bias helps or hurts the historical sample set.

