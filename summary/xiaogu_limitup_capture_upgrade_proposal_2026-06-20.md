# xiaogu 涨停捕捉率主链升级方案（2026-06-20）

## 目标
在不放宽监管 / near-limit / candidate evidence / source_time / 一手成本 hard gate 的前提下，把当前 0% 的涨停捕捉率从“纯稳健盈利票过滤器”提升为“能更稳定抓到强 continuation / 近涨停票”的主链。

## 当前问题复述
- 当前 13 笔已回填 T+1 样本中，0 笔 >= 9.5%
- 最接近涨停的 `300435` 达到 +9.36%，说明主链已经接近抓到高弹性票，但缺最后一层 continuation 确认
- 第三层信号整体升级为 hard gate 会伤害出票率和胜率，因此不能回到“全局硬拦”方案

## 升级原则
1. **不全局恢复第三层 hard gate**
   - `seal_order_strength`
   - `broken_limit_recovery`
   - `intraday_volume_price_confirm`
   这三项不能作为所有票型的统一硬门。
2. **只作用于高弹性 continuation 票型**
   - `high_momentum`
   - `near_limit`
   - `limit_strength`
   - `high_7_to_9_breakout`
3. **优先做排序提权 + 补充确认旁路**，而不是直接替代现有 confirmation。

## 建议方案
### 方案 A：Continuation 排序提权（优先）
在 scanner / structured_score 层为高弹性 continuation 票增加单独的 `continuation_bonus`，来源：
- `seal_order_strength`
- `intraday_volume_price_confirm`
- `broken_limit_recovery`
- `limitup_capture_score`

用途：
- 只影响高弹性票之间的排序
- 不绕过 hard gate
- 不改变 `NO_PICK` 语义

### 方案 B：Dynamic confirmation 补充旁路（次优先）
仅对 continuation 票型允许以下补充旁路：
- 如果 `close_position_score`、`fund_flow_momentum`、`time_series_momentum` 已达标
- 且 `seal_order_strength` / `intraday_volume_price_confirm` / `broken_limit_recovery` 中至少两项达到 continuation 阈值
- 则视为 `continuation_confirmation_pass`

用途：
- 只让“已经接近通过”的高弹性票多一个确认通道
- 不给普通票、水下票、无主线票开放

### 方案 C：近涨停样本 replay 验证集
正例：
- `300435`（+9.36%，最接近涨停）
- `603601`（+7.67%）
- `600031`（+7.18%）

反例：
- `300603`（亏损）
- `000002`（亏损）
- 任意 `near_limit_up_risk` 命中票

要求：
- before/after 对照必须明确显示：
  - 正例排序是否提升
  - 反例是否仍被安全拦截
  - 是否影响 `NO_PICK` 天数

## 不建议的方案
- 把第三层信号整体恢复成所有票型 hard gate
- 用 `seal_order_strength` 单指标直接放票
- 为了涨停率放宽 `near_limit_up_risk`
- 为了 continuation 提高票数而弱化 `candidate_fund_recheck` / `candidate_evidence_status`

## 推荐实施顺序
1. 先做 **方案 A：排序提权**
2. 再评估是否需要 **方案 B：补充确认旁路**
3. 最后用 **方案 C：固定时点 replay / dry-run** 做决策

## 预期收益
- 涨停捕捉率：有机会从 0% 提升到至少“出现近涨停票稳定前移”
- 出票率：理论上不应明显下降
- 胜率：如果只做 continuation 票型内的提权，理论上不应被明显破坏

## 风险
- 如果 continuation_bonus 设得过高，可能让高位追涨票压过稳健票
- 如果旁路条件过宽，可能引入更多 chase-high 误票
- 因此必须坚持 fixed-time replay + dry-run 双验证