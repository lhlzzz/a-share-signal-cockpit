# xiaogu 生命周期链路全量盘点（2026-06-20）

## 已存在并可工作的环节
### 1. live scan
- `data/live_scan/**/eastmoney_web_tabs_summary.json`
- 当前实时链路可持续产出 scan summary / raw / scored / evidence / source_status
- CDP 9333 固定入口已稳定

### 2. candidate bundle
- `data/forward_candidate_bundles/**/*.json`
- 2026-06-08 到 2026-06-20 的研究篮子 bundle 均存在
- runner 可直接消费

### 3. runtime decision context
- `data/forward_raw_runtime/**/runtime_decision_context.json`
- fixed-time dry-run 产物完整
- 可以做 official/no-pick 诊断

### 4. result evidence
- `data/forward_result_evidence/**/*.json`
- T+1 回填证据较完整
- eastmoney 与 tencent_fqkline 都有缓存

### 5. scoreboard
- `xiaogu_forward_judge_scoreboard_v0_1.py`
- 可用于当前 forward 结果与归因诊断

## 当前最大缺口
### 活跃 replay 执行入口缺失
- 工作区没有活跃的 one-year replay 脚本
- 当前只剩归档版本：
  - `archive/legacy_root_2026-05-23/scripts/xiaogu_runner_chain_replay_compare.py`
- 这意味着：
  - 可以做 live dry-run
  - 可以做 ledger/result 统计
  - 但缺少一条“当前主链代码 -> 历史样本 -> before/after 全量 replay 指标对照”的活跃入口

## 影响
1. 无法高置信度证明主链改动对历史涨停率/收益率是否真正提升
2. 当前优化更容易停留在：
   - 定向测试
   - fixed-time dry-run
   - 局部样本归因
3. 难以完成你要的“完整的高性能 xiaogu、明确提升涨停率/收益率”的闭环

## 结论
当前 xiaogu 不是“行情数据链路不完整”，而是“策略评估 / replay 闭环缺活跃执行入口”。

## 下一步优先级
1. **P0：重建活跃 replay 入口**
   - 让当前主链代码可以跑历史样本对照
2. **P1：把 continuation 提权、出票率优化、胜率收口接入 replay 评估**
3. **P2：统一输出三指标（涨停率 / 出票率 / 胜率）before/after 报告**