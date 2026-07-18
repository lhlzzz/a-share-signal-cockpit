# xiaogu 胜率收口方案（2026-06-20）

## 目标
在不进一步压低涨停捕捉率的前提下，给后续 continuation / 出票率优化提供胜率保护网，避免为了追求高弹性而把明显弱票放进 official PAPER_PICK。

## 原则
1. **不以继续保守化来换胜率**
2. **只拦 continuation 误放票，不拦高质量 continuation 票**
3. **收口规则必须与涨停率目标兼容**

## 建议收口方向
### 方案 A：冲高回落失败确认
针对 continuation 票增加失败特征识别：
- 尾盘高位但 close_position 快速回落
- intraday volume-price 失真
- 高 signal_pct 但 `fund_flow_momentum` 跌弱

用途：
- 拦“看起来强，但尾盘已经衰减”的 continuation 误票

### 方案 B：高弹性票的反向保护
如果样本具备：
- 强 `seal_order_strength`
- 强 `intraday_volume_price_confirm`
- 主线一致性明确
则不能因为一般性保守 confirmation 再被误杀。

用途：
- 防止胜率收口重新伤害高弹性 continuation 票

### 方案 C：追高误放收口
对以下样本收口：
- `near_limit_up_risk` 已边缘化但未正式命中
- continuation 信号不足却依赖单一高涨幅上位
- 缺主线一致性、缺资金确认的高位票

用途：
- 避免为了涨停率去放出“伪 continuation 票”

## 实施顺序
1. 先加入 continuation 失败确认标签
2. 再加入高弹性正向保护
3. 最后做追高误放收口

## 风险控制
- 不得把一般保守确认重新抬升为全局 hard gate
- 不得因为两笔亏损票就把所有高弹性 continuation 票压掉
- 任何收口必须与涨停捕捉率一起做 before/after 对照

## 预期效果
- 胜率：维持或小幅提升
- 涨停捕捉率：不应因为收口而进一步下降
- 出票率：可能轻微波动，但不得靠削弱 `NO_PICK` 修正