# A Share Main Chain Runtime Review

**Goal:** Verify that the xiaogu A-share active chain is using VEI, Qlib, QuantDinger, and tradingagent_a as the active capability set; run the current main chain; then review live paper ticket returns and quality from 2026-05-18 onward.

**Constraints:** Keep `MANUAL_TRADE_ONLY`, `paper_only=true`, `no_trade=true`, `auto_order=false` in every runner/scoreboard path checked. Do not connect broker, account, order, API key, gateway, or execution endpoints. Use existing Eastmoney/CDP 9333 chain and local ledgers/evidence only. If the market is closed, CDP 9333 is unavailable, or fresh data cannot be generated, record the exact command/runtime condition and fall back only to the latest valid stable-chain evidence already on disk. Any recommendation to add or replace a capability must cite an observed gap from ledger, replay, scoreboard, or source flow evidence.

**Assumptions:** The active repo set named by the user maps to `VEI`, `Qlib`, `QuantDinger`, and `tradingagent_a`. `vie` is treated as `VEI`, and `quant agenttra` is treated as `QuantDinger + tradingagent_a` unless source evidence shows otherwise.

**Out of scope:** Do not delete, archive, or cleanup any file. Do not stage, commit, push, or edit other workspaces. Do not install or clone external repos. Do not change live trading status. Do not modify scoring code during Tasks 1-4; if a concrete code gap is found, record it as a next action instead of patching it in the same run.

## Must-Haves

- MH1: Confirm whether VEI, Qlib, QuantDinger, and tradingagent_a are active in the A-share main scoring path, and identify any obvious missing/better capability candidates from those repos. A:I2
- MH2: Run the A-share active chain or its freshest valid dry-run path and report whether the runtime function set is complete enough for current paper-ticket output. A:I2
- MH3: Review canonical live paper tickets from 2026-05-18 onward and report return/quality metrics from actual ledger/evidence outputs. A:I2
- MH4: Preserve all no-trade/manual-trade gates and avoid cleanup, stage, commit, or external account actions. A:I2
- MH5: Update xiaogu TASK/LOG/SESSION with commands, results, blockers, and next action. A:I2

### Task 1: Inspect active four-repo main-chain wiring A:I2
- [ ] Inspect source/config for active repo order and score contribution flow.
- [ ] Identify whether repo capabilities are active scoring, diagnosis-only, or research-only.
- [ ] Verification: source references plus GitNexus/CodeGraph context or direct command output.

### Task 2: Run current A-share main chain dry-run A:I2
- [ ] Check CDP 9333 availability and current scan/runtime prerequisites.
- [ ] Run the current stable A-share chain in dry-run/no-ledger mode.
- [ ] Verification: command output showing PAPER_PICK or NO_PICK, evidence status, and no-trade fields.

### Task 3: Review 2026-05-18 onward ticket return metrics A:I2
- [ ] Read canonical `forward_paper_ledger_v0_1.jsonl` and related scoreboard/fill outputs.
- [ ] Compute ticket count, filled/pending horizons, win rate, average return, profit factor where evidence supports it.
- [ ] Verification: generated summary/report from actual ledger rows.

### Task 4: Assess ticket quality and capability gaps A:I2
- [ ] Classify ticket quality using existing diagnosis fields, limit-up/fund/underwater/VEI/Qlib signals, and hard-block reasons.
- [ ] Compare observed misses against the four-repo active capability set and list only concrete replacement/addition candidates backed by evidence.
- [ ] Verification: report section citing actual counts and sample tickets.

### Task 5: Record results and next action A:I2
- [ ] Update `TASK.md`, `LOG.md`, and `SESSION.md` with this run's facts.
- [ ] Run governance/GitNexus checks if code changes were made; if no code changes, record that explicitly.
- [ ] Verification: updated files and final status summary.
