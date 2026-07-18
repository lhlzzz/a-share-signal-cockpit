# T2: 出票流程固化 + CloakChrome 绕过验证确认

## 目标
验证并固化完整出票流程：
1. 使用 CloakChrome（`--cloak` 模式）启动浏览器不触发东财反爬验证
2. 三层行情数据（REQUIRED/ENHANCED/DATA_DIRECTORY）全部能被 scanner 采集并注入 runner
3. 每次出票输出三类结果：PAPER_PICK（正式决策）+ highest_score_candidate（当日最高分）+ closest_to_pick_candidate（最接近出票阈值）
4. 出票流程可以每个交易日无人值守运行

## 文件范围（仅修改这些文件）
- `xiaogu_forward_d1_1450_runner_v0_1.py`（输出格式确认/补全）
- `tests/test_xiaogu_a_share_forward_runner.py`（新增验证测试）

## 禁止修改
- `forward_paper_ledger_v0_1.jsonl`
- `xiaogu_forward_result_filler_v0_1.py`

## 实现要求

### 1. 三类出票输出验证
在 runner 的最终 JSON 输出中确认以下三个字段都已输出（不论决策是否为 PAPER_PICK）：
```python
{
  "decision": "PAPER_PICK" | "NO_PICK" | ...,
  "symbol": "...",
  # 以下三字段必须存在
  "paper_pick": {...},           # PAPER_PICK 信息（无票时为 null）
  "highest_score_candidate": {   # 当日最高分候选
    "symbol": "...",
    "name": "...",
    "score": ...,
    "diagnostic_role": "highest_score_candidate"
  },
  "closest_to_pick_candidate": { # 最接近出票阈值候选
    "symbol": "...",
    "name": "...",
    "score": ...,
    "blocking_reasons": [...],
    "diagnostic_role": "closest_to_pick_candidate"
  }
}
```
若当日有 PAPER_PICK，highest_score 和 closest_to_pick 可以相同或不同。

### 2. 确认 CloakChrome 绕过机制
在测试中验证：
- scanner 的 `collect_cdp_payloads` 接收 `cdp_url="http://localhost:9333"` 时能正常解析东财页面（mock 返回）
- 若 CDP 连接成功，不触发额外的验证拦截逻辑

### 3. 三层数据消费完整性检查
新增函数 `validate_data_completeness(scan_output: dict) -> dict`：
```python
{
  "required_sources_present": [...],   # REQUIRED 层已有数据的 source
  "required_sources_missing": [...],   # REQUIRED 层缺失的 source
  "enhanced_sources_present": [...],
  "enhanced_sources_missing": [...],
  "data_directory_keys_present": [...],
  "data_directory_keys_missing": [...],
  "completeness_score": float,          # 0.0-1.0
  "is_sufficient_for_pick": bool,       # 必须 REQUIRED 全有才为 True
}
```

### 4. 日常运行入口确认
在文件顶部注释中确认运行命令（不改逻辑，只更新 docstring/注释）：
```
python3 xiaogu_eastmoney_web_tabs_scan_v0_1.py --cloak --cdp-url http://localhost:9333 --open-required-cdp-tabs --experimental
python3 xiaogu_forward_d1_1450_runner_v0_1.py --bundle <latest_bundle> --dry-run
python3 xiaogu_forward_result_filler_v0_1.py --fill-all-pending --auto-web
```

## 验收标准
1. `python3 -m pytest tests/ -x -q` 所有测试通过
2. 新增测试 `test_output_has_three_candidate_slots` 验证 runner 输出中三个候选位都存在
3. 新增测试 `test_validate_data_completeness_required_all_present` 验证完整性函数
4. `python3 -m py_compile xiaogu_forward_d1_1450_runner_v0_1.py` 无错
