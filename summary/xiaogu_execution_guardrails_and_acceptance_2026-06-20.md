# xiaogu 执行红线与验收机制（2026-06-20）

## 执行红线
1. 不做持仓自动化。
2. 不取消 hard gate。
3. 不为了每天有票放弃 `NO_PICK`。
4. 不做 symbol hardcode。
5. 不放宽 `regulatory_hard_block`、`near_limit_up_risk`、`candidate_evidence_status`、`candidate_fund_recheck`、`source_time/asof`、`one_lot_cost_cap`。
6. 不把第三层信号整体恢复成全局 hard gate。

## 后续实现阶段必跑验证
### 代码级
- `python3 -m py_compile`
- 相关 `pytest`（至少 runner 主测试）

### 策略级
- fixed-time dry-run
- 历史 replay 对照
- ledger 指标复算：涨停捕捉率 / 出票率 / 胜率

### 安全级
- 复核 `paper_only=true`
- 复核 `no_trade=true`
- 复核 `allow_trade=false`
- 复核 `auto_order=false`

## 验收规则
- 若只改善单一指标而未报告另外两项，判定为不通过。
- 若通过放宽安全边界提指标，判定为不通过。
- 若没有 before/after 证据，判定为不通过。
- 若引入 drift（比如混入持仓自动化），判定为不通过。

## 阶段完成判定
只有在以下条件同时满足时，某一轮实施才可宣称完成：
1. 相关代码验证通过。
2. fixed-time dry-run 通过。
3. replay / ledger 指标对照齐全。
4. 三项核心指标变化已同时报告。
5. 未破坏安全字段。