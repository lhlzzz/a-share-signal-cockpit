# xiaogu 出票率安全优化方案（2026-06-20）

## 目标
在不削弱 `NO_PICK` 能力、不动监管 / near-limit / candidate evidence / source_time / 一手成本 hard gate 的前提下，提升当前 80% 的出票率，并尽量只对高质量 near-miss continuation 票产生影响。

## 优化原则
1. **不通过放松 A 类安全边界提出票率**
2. **只研究 B 类策略 blocker**
3. **先做排序优化，再考虑 eligibility 旁路**
4. **优先 continuation / near-miss 样本，不动普通弱票**

## 建议方案
### 方案 A：closest-to-pick 排序前移
- 保持 official gate 不变
- 对 continuation 强票在 `closest-to-pick` / 候选排序中前移
- 目的：先确认“高质量 near-miss 是否稳定出现”，而不是立刻强行出票

### 方案 B：票型限定的 buy-confirmation 细化
仅对以下票型研究更细 confirmation：
- `high_momentum`
- `high_7_to_9_breakout`
- `limit_strength`

做法：
- 允许更强的分时量价 / 封单强度信号补偿部分 buy_confirmation 缺口
- 但只在 continuation 样本中生效

### 方案 C：opp / dynamic confirmation 的 continuation 旁路
- 不改变所有票的 `opp_too_low` / `dynamic_signal_confirmation_pass`
- 只在 near-miss continuation 样本上评估补充旁路
- 目的：提高真实 continuation 票在无票日的放出概率

## 风险控制
- 不碰 `regulatory_hard_block`
- 不碰 `near_limit_up_risk`
- 不碰 `candidate_fund_recheck` / `candidate_evidence_status`
- 不把 data-missing 日伪装成可出票日

## 推荐顺序
1. 先做排序前移（最低风险）
2. 再做 buy-confirmation continuation 细化
3. 最后才评估 opp / dynamic confirmation continuation 旁路

## 预期收益
- 出票率：有机会从 80% 提升，但不保证每天有票
- 质量：比“直接放宽 gate”更安全
- 风险：维持 `NO_PICK` 的正确性

## 供后续验证使用的判断标准
- 如果某项优化只是让更多弱票上位，则拒绝进入主链
- 如果某项优化能让高质量 near-miss 更稳定进入 official 候选，同时不破坏 hard gate，则可继续推进