# 早期历史样本 NO_PICK 规则级归因（2026-06-20）

## 结论
早期历史样本 replay 大量仍是 `NO_CLEAN_CANDIDATE_AFTER_ALL_LAYERS`，主因不是兼容层没补好，而是**当前 active 主链的几类硬规则在这些历史样本上真实不通过**。

## 主要拦截规则

### 1. 板块机会确认门槛
- 规则：`sector_opportunity_score>=1.0 or VEI strong signal`
- 现象：大量早期候选缺少板块机会数据或 VEI 强信号
- 结果：无法进入 official eligibility

### 2. 动态确认不足
- 规则：`dynamic_signal_confirmation_pass`
- 现象：早期样本普遍缺当前 active chain 依赖的动态确认组合
- 结果：即使 score 不低，也仍被拦

### 3. 候选资金复核缺失
- 规则：`candidate_fund_recheck_missing`
- 现象：部分早期候选没有通过当前要求的资金复核证据门
- 结果：被正式排除

### 4. 近涨停 / 追高风控
- 规则：`near_limit_up_risk`
- 规则：`CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`
- 现象：早期一些候选属于高位冲板或近涨停样本，但缺延续确认
- 结果：被风控规则直接拦截

### 5. research / 证据质量
- 现象：部分早期候选 `research_panel_overall=FAIL`，或 adversarial review 带 `evidence_missing`
- 结果：在当前规则下仍不能直接作为 official pick

## 结论
早期历史样本 NO_PICK 的根因已经收敛为：
> **不是代码没吃到字段，而是这些历史样本在当前更严格的 active 规则下，真实不满足 official 出票门槛。**

## 建议
- 如果目标是“完整闭环验证”，早期历史样本更适合当作**参考集和压力测试集**，不直接作为当前规则必须回放成功的对象
- 如果目标是“提高涨停率/收益率”，当前更高价值路径是继续在**较新样本 + fixed-time dry-run + future forward** 上验证，而不是为了回放历史而放松当前 hard gate