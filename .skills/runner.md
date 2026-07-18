# Runner Skill

## Trigger

When pick decisions need to be made from scanned candidates.

## Workflow

1. Load candidate bundle from latest scan
2. Apply hard gates (regulatory, data, chase_high with exceptions)
3. Score candidates using regime-aware scoring
4. Select PAPER_PICK or NO_PICK
5. Write to ledger via recorder

## Key Files

- `xiaogu_forward_d1_1450_runner_v0_1.py` — main runner
- `xiaogu_forward_paper_recorder_v0_1.py` — ledger writer
- `scoring_config` table — tunable thresholds

## Verification

- Decision is PAPER_PICK or NO_PICK (never undefined)
- All hard gates evaluated
- Score recorded in daily_candidates
- Selection reason documented

## Regime-Aware Scoring

- Strong market: reward momentum (high signal, high flow)
- Weak market: reward contrarian (low signal, low position)
- Sideways: balanced approach
