# Xiaogu forward ticket review: 2026-05-19 to 2026-06-06

Scope: canonical `forward_paper_ledger_v0_1.jsonl` active decisions from 2026-05-19 onward. PAPER_ONLY / NO_TRADE; this report is observation and review only.

## Summary

- Active rows: 20
- By decision: `{"NO_PICK": 5, "PAPER_PICK": 12, "RESEARCH_CANDIDATE": 3}`
- Review tag counts: `{"CHASE_HIGH_RISK": 7, "LOSS": 2, "LOW_RETURN": 3, "NO_LIMIT_UP_EVIDENCE": 10, "NO_PICK_OBSERVATION": 5, "RESEARCH_ONLY": 3, "RESULT_PENDING": 2}`
- PAPER_PICK filled T+ return summary: trades=10, wins=8, win_rate=80.0%, avg_return=2.595%, profit_factor=5.761
- Key issue: filled PAPER_PICK samples show positive hit rate, but limit-up evidence is missing/weak and average return is low for the stated goal of increasing 涨停率/收益率.
- Data quality fix landed in code: weekend `2026-06-06` local live scan is rejected as a T+1 exit source.

## All rows

| Date | Decision | Symbol | Name | Entry | D1 return | Tags | Evidence |
|---|---|---:|---|---:|---:|---|---|
| 2026-05-19 | PAPER_PICK | 300603 | 立昂技术 | 11.38 | 2.02% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | `/root/hermes/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-19/001337_historical_backtest_rule_v0_1_NOPICK.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/tencent_fqkline/2026-05-19/300603_2026-05-18_2026-05-31.json` |
| 2026-05-20 | PAPER_PICK | 300603 | 立昂技术 | 11.38 | -4.04% | CHASE_HIGH_RISK, LOSS, NO_LIMIT_UP_EVIDENCE | `/root/hermes/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-20/001337_historical_backtest_rule_v0_1_300603.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/tencent_fqkline/2026-05-20/300603_2026-05-19_2026-06-01.json` |
| 2026-05-21 | RESEARCH_CANDIDATE | 002709 | 天赐材料 | 58.0 | 4.14% | RESEARCH_ONLY | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-21/145000_historical_backtest_rule_v0_2_002709.json; workspaces/xiaogu/data/forward_result_evidence/web/2026-05-21/002709_stockevents_2026-05-22.json; workspaces/xiaogu/data/forward_result_evidence/web/2026-05-21/002709_stockevents_websearch_2026-05-22.json` |
| 2026-05-22 | NO_PICK |  |  |  |  | NO_PICK_OBSERVATION | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-22/145000_historical_backtest_rule_v0_2_NO_PICK.json` |
| 2026-05-23 | NO_PICK |  |  |  |  | NO_PICK_OBSERVATION | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-23/145000_historical_backtest_rule_v0_2_NO_PICK.json` |
| 2026-05-23 | PAPER_PICK | 603601 | 再升科技 | 19.29 | 7.67% | NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-23/145000_historical_backtest_rule_v0_3_603601.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/eastmoney/2026-05-23/603601_2026-05-22_2026-06-04.json` |
| 2026-05-25 | PAPER_PICK | 002436 | 兴森科技 | 38.12 | 3.62% | NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-25/155900_historical_backtest_rule_v0_3_002436.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/eastmoney/2026-05-25/002436_2026-05-24_2026-06-06.json` |
| 2026-05-26 | PAPER_PICK | 601615 | 明阳智能 | 18.81 | 4.52% | NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-26/145019_historical_backtest_rule_v0_3_601615.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/eastmoney/2026-05-26/601615_2026-05-25_2026-06-07.json` |
| 2026-05-27 | PAPER_PICK | 002273 | 水晶光电 | 42.05 | 5.35% | NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-27/150142_historical_backtest_rule_v0_3_002273.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/eastmoney/2026-05-27/002273_2026-05-26_2026-06-08.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/live_scan/2026-05-28/eastmoney_web_tabs_scan_v0_1/eastmoney_web_tabs_raw.jsonl` |
| 2026-05-28 | PAPER_PICK | 600237 | 铜峰电子 | 10.7 | 3.83% | NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-28/145000_historical_backtest_rule_v0_3_600237.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/tencent_fqkline/2026-05-28/600237_2026-05-27_2026-06-09.json` |
| 2026-05-29 | PAPER_PICK | 000002 | 万  科Ａ | 3.55 | -1.41% | CHASE_HIGH_RISK, LOSS, NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-05-29/145103_historical_backtest_rule_v0_3_000002.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/live_scan/2026-06-01/eastmoney_web_tabs_scan_v0_1/eastmoney_web_tabs_raw.jsonl` |
| 2026-06-01 | PAPER_PICK | 601898 | 中煤能源 | 16.89 | 2.31% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-01/164218_historical_backtest_rule_v0_3_601898.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/tencent_fqkline/2026-06-01/601898_2026-05-31_2026-06-13.json` |
| 2026-06-02 | PAPER_PICK | 603993 | 洛阳钼业 | 19.74 | 2.08% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-02/143628_historical_backtest_rule_v0_3_603993.json; /root/hermes/company-ai-system/workspaces/xiaogu/data/forward_result_evidence/eastmoney/2026-06-02/603993_2026-06-01_2026-06-14.json` |
| 2026-06-05 | NO_PICK | 002624 | 完美世界 | 13.92 |  | NO_PICK_OBSERVATION | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-05/145000_historical_backtest_rule_v0_3_NO_PICK.json` |
| 2026-06-05 | NO_PICK | 000070 | 特发信息 | 19.92 |  | NO_PICK_OBSERVATION | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-05/150000_historical_backtest_rule_v0_3_NO_PICK.json` |
| 2026-06-03 | RESEARCH_CANDIDATE | 600522 | 中天科技 | 45.53 |  | RESEARCH_ONLY | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-03/141233_historical_backtest_rule_v0_3_600522.json` |
| 2026-06-03 | PAPER_PICK | 002171 | 楚江新材 | 12.89 |  | CHASE_HIGH_RISK, RESULT_PENDING | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-03/143624_historical_backtest_rule_v0_3_002171.json` |
| 2026-06-04 | PAPER_PICK | 000700 | 模塑科技 | 17.83 |  | CHASE_HIGH_RISK, RESULT_PENDING | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-04/143823_historical_backtest_rule_v0_3_000700.json` |
| 2026-06-05 | RESEARCH_CANDIDATE | 000070 | 特发信息 | 19.92 |  | RESEARCH_ONLY | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-05/150000_historical_backtest_rule_v0_3_000070.json` |
| 2026-06-06 | NO_PICK | 000070 | 特发信息 | 19.92 |  | NO_PICK_OBSERVATION | `/root/hermes/company-ai-system/workspaces/xiaogu/data/forward_snapshots/2026-06-06/145000_historical_backtest_rule_v0_3_NO_PICK.json` |

## Problem rows

| Date | Symbol | Name | D1 return | Tags | Reasons |
|---|---:|---|---:|---|---|
| 2026-05-19 | 300603 | 立昂技术 | 2.02% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | forward return 2.02% < 3.00%; no explicit limit-up evidence and forward high return 2.02% < 9.00%; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-05-20 | 300603 | 立昂技术 | -4.04% | CHASE_HIGH_RISK, LOSS, NO_LIMIT_UP_EVIDENCE | forward return -4.04% < 0; no explicit limit-up evidence and forward high return -4.04% < 9.00%; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-05-23 | 603601 | 再升科技 | 7.67% | NO_LIMIT_UP_EVIDENCE | no explicit limit-up evidence and forward high return 7.67% < 9.00% |
| 2026-05-25 | 002436 | 兴森科技 | 3.62% | NO_LIMIT_UP_EVIDENCE | no explicit limit-up evidence and forward high return 3.62% < 9.00% |
| 2026-05-26 | 601615 | 明阳智能 | 4.52% | NO_LIMIT_UP_EVIDENCE | no explicit limit-up evidence and forward high return 4.52% < 9.00% |
| 2026-05-27 | 002273 | 水晶光电 | 5.35% | NO_LIMIT_UP_EVIDENCE | no explicit limit-up evidence and forward high return 5.35% < 9.00% |
| 2026-05-28 | 600237 | 铜峰电子 | 3.83% | NO_LIMIT_UP_EVIDENCE | no explicit limit-up evidence and forward high return 3.83% < 9.00% |
| 2026-05-29 | 000002 | 万  科Ａ | -1.41% | CHASE_HIGH_RISK, LOSS, NO_LIMIT_UP_EVIDENCE | forward return -1.41% < 0; no explicit limit-up evidence and forward high return -1.41% < 9.00%; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-06-01 | 601898 | 中煤能源 | 2.31% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | forward return 2.31% < 3.00%; no explicit limit-up evidence and forward high return 2.31% < 9.00%; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-06-02 | 603993 | 洛阳钼业 | 2.08% | CHASE_HIGH_RISK, LOW_RETURN, NO_LIMIT_UP_EVIDENCE | forward return 2.08% < 3.00%; no explicit limit-up evidence and forward high return 2.08% < 9.00%; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-06-03 | 002171 | 楚江新材 |  | CHASE_HIGH_RISK, RESULT_PENDING | PAPER_PICK has no filled forward return yet; entry candidate was already high/near close high while result is pending, low, or loss |
| 2026-06-04 | 000700 | 模塑科技 |  | CHASE_HIGH_RISK, RESULT_PENDING | PAPER_PICK has no filled forward return yet; entry candidate was already high/near close high while result is pending, low, or loss |

## Pending rows

- 2026-06-03 002171 楚江新材: pending legal T+ result; weekend live scan rejected.
- 2026-06-04 000700 模塑科技: pending legal T+ result; weekend live scan rejected.

## NO_PICK / research observations

- NO_PICK 2026-05-22  
- NO_PICK 2026-05-23  
- NO_PICK 2026-06-05 002624 完美世界
- NO_PICK 2026-06-05 000070 特发信息
- NO_PICK 2026-06-06 000070 特发信息
- RESEARCH 2026-05-21 002709 天赐材料: kept out of PAPER_PICK return summary.
- RESEARCH 2026-06-03 600522 中天科技: kept out of PAPER_PICK return summary.
- RESEARCH 2026-06-05 000070 特发信息: kept out of PAPER_PICK return summary.

## Diagnosis toward higher limit-up rate and return

1. The main recurring issue is `CHASE_HIGH_RISK`: candidates often enter after ~7%+ T-day move or near the close high, then fail to deliver a T+ limit-up/large return.
2. `NO_LIMIT_UP_EVIDENCE` dominates filled PAPER_PICK rows. This should not be treated as absolute proof of no intraday touch, but it is enough to block rule promotion until limit-up evidence is explicitly captured.
3. Loss rows are concentrated in high-entry contexts: 300603 on 2026-05-20 and 000002 on 2026-05-29.
4. Low-return rows below 3% are 300603 on 2026-05-19, 601898 on 2026-06-01, and 603993 on 2026-06-02.
5. For the next upgrade, promote observation tags only after repeated evidence: add stronger buyability/封单/炸板/追高 features before changing hard gates.

## Upgrade boundary

- Implemented now: derived review tags in scoreboard + non-trading-day result-source guard.
- Not implemented now: automatic trading, broker integration, or hard rule promotion from this single review.

## Implemented follow-up gate for higher limit-up / return target

After the first review, the runner now applies an observation-backed opportunity hard block when a candidate is already high/near close high but lacks structured limit-up confirmation:

- Trigger: `signal_pct >= 7.0` or `close_position_score >= 0.70`.
- Required confirmation: at least one of `limitup_reason_strength`, `seal_order_strength`, or `order_book_pressure` is >= 0.60 when those structured components are available.
- Block reason: `CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`.
- Purpose: reduce high-entry low-return / loss samples and force the chain to prefer candidates with explicit limit-up / seal / order-book support.
- Scope: still PAPER_ONLY / NO_TRADE; this is a runner opportunity gate, not broker execution.

Verification on 2026-06-06 dry-run: `000070 特发信息` is now blocked with `OPPORTUNITY_HARD_BLOCK_CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION` in addition to the stale scan blocker.
