# xiaogu 出票率专项诊断（2026-06-20）

## 基线
- 总交易日：20
- 有票日：16
- 无票日：4（按最终 official 视角）
- 当前出票率：80%

## 说明
历史 ledger 中存在个别 same-day `NO_PICK` 后又出现 `PAPER_PICK` 的情况，因此本诊断按“最终 official 是否有 PAPER_PICK”来认定有票日/无票日。

## 无票日清单（最终 official 视角）
- 2026-05-12
- 2026-05-21
- 2026-05-22
- 2026-06-05
- 2026-06-10
- 2026-06-12

> 其中 2026-05-12 / 2026-05-22 带有 `NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE` 或 source 缺失性质，更接近“数据/证据不足日”，不应简单当成策略错杀日。

## blocker taxonomy
### A. 不可动安全边界（禁止用来硬提出票率）
- `regulatory_hard_block`
- `near_limit_up_risk`
- `candidate_evidence_missing`
- `source_time/asof invalid`
- `one_lot_cost > cap`
- `candidate_fund_recheck missing`

这些 blocker 的作用是保护主链不误出票，不能为了“每天有票”而放宽。

### B. 可研究的过严门 / 策略空间
- `buy_confirmation_below_threshold`
- `QUALIFIED_CANDIDATE_FALSE`
- `dynamic_signal_confirmation_pass` 缺失
- `opp_too_low`
- `climax_close_position_unconfirmed`

这些 blocker 不是天然错误，但它们可能包含“高质量 continuation 票被压住”的机会成本，值得按票型做精细化研究。

## 当前出票率短板的本质
1. **一部分无票日其实是数据/证据不足日**
   - 这类日子不应直接通过规则放行来补票
2. **一部分无票日是强票被保守 confirmation 卡住**
   - 例如 `buy_confirmation`、`dynamic_signal_confirmation`、`opp_too_low`
3. **如果目标是提高出票率，必须分清是数据问题还是策略问题**
   - 数据问题：补数据，不补票
   - 策略问题：只能在高质量 continuation / near-miss 样本上研究安全旁路

## 下一步建议（供 T7 消费）
- 只研究 B 类 blocker
- 优先把“排序前移”与“票型限定旁路”作为出票率优化路径
- 明确禁止通过削弱 `NO_PICK` 或 A 类安全边界来追求每天有票