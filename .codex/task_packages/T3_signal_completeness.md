# T3: 信号完整性验证 — 板块轮动/主力资金/北向/水位/过热全链路

## 目标
确认以下所有信号从 scanner 采集→runner 评分→决策输出的完整链路都已落地并可测试验证：
1. 板块轮动（sector_rotation：热门板块轮入/轮出信号）
2. 主力资金流向（main_force_net_inflow，正负向评分）
3. 北向资金（northbound / hsgt_institutional_flow，个股级别）
4. 水下盘/水上盘（below_water_price / above_water_price：当前价相对成本均价的位置）
5. 过热盘（overheated_market：过热市场下的强确认要求）

## 文件范围（仅修改这些文件）
- `xiaogu_forward_d1_1450_runner_v0_1.py`（信号评分补全）
- `tests/test_xiaogu_a_share_forward_runner.py`（新增信号链路测试）

## 禁止修改
- `xiaogu_eastmoney_web_tabs_scan_v0_1.py`（scanner 侧已有数据提取，不动）
- `forward_paper_ledger_v0_1.jsonl`

## 需要验证的信号链路

### 3.1 板块轮动信号
- scanner 已有：`hot_sector_names`（从 concept_capital_flow/sector_fund_flow 提取）
- runner 需要：candidate 所属板块在 `hot_sector_names` 中时，`sector_rotation_signal` += 1
- 如果候选股所在板块是当日净流出前 3，则作为减分（`sector_outflow_penalty`）
- 验证：bundle 中有 `hot_sector_names`，runner structured_score 中有 `sector_rotation_signal`

### 3.2 主力资金流向
- 已有 `data_directory_capital_flow.main_force_net_inflow`
- 验证：`structured_score.components.data_directory_capital_flow > 0` 当 net_inflow > 0
- 边界：net_inflow < -50_000_000（-5000万）触发 `main_force_heavy_sell` blocker

### 3.3 北向资金（个股级）
- 已有 `hsgt_institutional_flow` in structured_score
- 验证：bundle 中 `hsgt_signals` 包含 per-stock 北向持仓
- 验证：candidate 在北向持仓中时 `hsgt_institutional_flow > 0`

### 3.4 水下盘/水上盘
检查 runner 是否对以下字段评分（若无则补充）：
- `below_water`: 当前价 < 成本均价（低位股，轻仓建议）
- `above_water`: 当前价 > 成本均价（获利盘，动力来源）
- `score_below_water_ambush`（对应 L11_LOW_POSITION_AMBUSH 信号池）

如 runner 中已有但字段名不同，确认等价实现，在测试中验证字段存在。

### 3.5 过热市场逻辑
- 已有 `overheated_market_no_strong_confirmation` blocker
- 验证触发条件：市场整体涨停数 > 80 或板块平均涨幅 > 5%
- 若无触发条件代码，补充 `is_market_overheated(bundle) -> bool` 函数，使用 `limitup_pool` 行数作为代理

## 验收标准
1. 新增测试 `test_sector_rotation_signal_scores_hot_sector_candidate`
2. 新增测试 `test_northbound_signal_per_stock_in_runner`
3. 新增测试 `test_overheated_market_blocker_triggered`
4. 新增测试 `test_below_water_ambush_signal_in_structured_score`
5. `python3 -m pytest tests/ -x -q` 全部通过
6. `python3 -m py_compile xiaogu_forward_d1_1450_runner_v0_1.py` 无错
