# T5: 数据完整性端到端验收测试 + 日常运行健康检查

## 目标
写一个可以每天交易日运行的健康检查脚本，验证 xiaogu 完整链路：
scanner → bundle → runner → output → filler 每个环节都有输出且数据完整。

## 文件范围
- 修改 `scripts/xiaogu_codegraph_health_check.py`（若存在）或新建 `scripts/xiaogu_daily_health_check.py`
- `tests/test_xiaogu_a_share_forward_runner.py`（新增集成测试）

## 优先查看
先 `cat scripts/xiaogu_codegraph_health_check.py` 看是否可扩展，优先扩展，不新建。

## 实现要求

### 5.1 健康检查项目
```python
checks = [
    # Scanner 层
    "scanner_py_compile",           # py_compile 无错
    "runner_py_compile",            # py_compile 无错
    "filler_py_compile",            # py_compile 无错

    # Tab 配置完整性
    "required_tabs_defined_7",      # REQUIRED_CDP_TAB_URLS 有 7 个 key
    "enhanced_tabs_defined_8",      # DEFAULT_ENHANCED_CDP_TAB_URLS 有 8 个 key
    "data_directory_tabs_gte_15",   # DATA_DIRECTORY_CONTENT_CDP_TAB_URLS 有 ≥15 个条目

    # Runner 输出格式
    "runner_has_highest_score_output",    # highest_score_candidate 字段存在
    "runner_has_closest_to_pick_output",  # closest_to_pick_candidate 字段存在

    # 信号注册
    "sector_rotation_in_scoring",   # runner 代码中含 sector_rotation
    "northbound_in_scoring",        # runner 代码中含 hsgt_institutional_flow
    "overheated_in_scoring",        # runner 代码中含 overheated_market

    # 回填
    "filler_has_fill_all_pending",  # filler CLI 支持 --fill-all-pending
    "ledger_readable",              # forward_paper_ledger_v0_1.jsonl 可读（若存在）
]
```

### 5.2 输出格式
```
[PASS] scanner_py_compile
[PASS] required_tabs_defined_7
[FAIL] data_directory_tabs_gte_15 — got 12, expected ≥15
...
SUMMARY: 11/13 checks passed
EXIT_CODE: 1 (有 FAIL 则非零)
```

### 5.3 运行方式
```
python3 scripts/xiaogu_daily_health_check.py [--json]
```

## 验收标准
1. 脚本本身 `python3 -m py_compile` 无错
2. 在当前 workspace 运行后所有已实现功能都显示 `[PASS]`
3. 新增测试 `test_health_check_all_pass` 验证所有 PASS 状态
4. `python3 -m pytest tests/ -x -q` 全部通过
