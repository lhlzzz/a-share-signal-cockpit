# T4: 收益回填 → 评分反馈优化循环

## 目标
在现有 `xiaogu_forward_result_filler_v0_1.py` 收益回填基础上，新增一个轻量的信号有效性分析模块：
从 ledger 历史记录中读取 t1_return，分析哪些信号/池子组合的涨停率和盈利率最高，输出人工可读的权重建议（不自动修改评分代码）。

## 文件范围
- `xiaogu_forward_judge_scoreboard_v0_1.py`（修改或扩展）
- 新建 `xiaogu_signal_effectiveness_v0_1.py`（如 judge_scoreboard 无法扩展）
- `tests/test_xiaogu_a_share_forward_runner.py`（新增测试）

## 优先修改现有文件
先检查 `xiaogu_forward_judge_scoreboard_v0_1.py` 是否已有分析逻辑，在其基础上扩展，
只有当其职责完全不同时才新建文件。

## 实现要求

### 4.1 从 ledger 读取并聚合
```python
def analyze_signal_effectiveness(
    ledger_path: Path,
    min_samples: int = 3,
) -> dict:
    """
    读取 forward_paper_ledger_v0_1.jsonl，按信号组合聚合：
    - PAPER_PICK 记录的 t1_return（已回填的）
    - 识别 limit_up（t1_return >= 0.095）
    - 计算 hit_rate、avg_return、limit_up_rate
    返回按 limit_up_rate 降序排列的信号有效性表
    """
```

### 4.2 输出格式
```python
{
  "analysis_date": "2026-06-25",
  "total_picks": 12,
  "filled_picks": 8,
  "overall_limit_up_rate": 0.375,
  "overall_avg_t1_return": 0.042,
  "signal_effectiveness": [
    {
      "signal_key": "hsgt_institutional_flow",
      "present_count": 5,
      "limit_up_rate": 0.6,
      "avg_t1_return": 0.072,
      "weight_suggestion": "INCREASE",  # INCREASE / MAINTAIN / DECREASE
    },
    ...
  ],
  "pool_effectiveness": [
    {
      "pool": "L11_LOW_POSITION_AMBUSH",
      "count": 3,
      "limit_up_rate": 0.667,
      "avg_return": 0.081,
    }
  ]
}
```

### 4.3 CLI 接口
```
python3 xiaogu_signal_effectiveness_v0_1.py --ledger forward_paper_ledger_v0_1.jsonl [--min-samples 3]
```

### 4.4 limit_up 定义
`t1_return >= 0.095`（涨停约 10%，保守识别）

### 4.5 weight_suggestion 规则（简单阈值，无需 ML）
- limit_up_rate > 0.5 且 count >= min_samples → INCREASE
- limit_up_rate < 0.2 且 count >= min_samples → DECREASE
- 其余 → MAINTAIN

## 验收标准
1. `python3 -m py_compile xiaogu_signal_effectiveness_v0_1.py` 无错（或对应文件）
2. 新增测试 `test_signal_effectiveness_from_mock_ledger` 使用 3 条 mock ledger 记录，
   验证 `analyze_signal_effectiveness` 返回正确的 limit_up_rate 和 weight_suggestion
3. `python3 -m pytest tests/ -x -q` 全部通过
4. 不得修改 `forward_paper_ledger_v0_1.jsonl`，只读
5. 不得修改 runner 评分代码，只输出建议
