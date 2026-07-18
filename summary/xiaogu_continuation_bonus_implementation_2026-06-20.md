# xiaogu continuation 排序提权实现与验证（2026-06-20）

## 实现内容
### 1. 主链 continuation bonus
在 `xiaogu_v2_1_six_repo_real_integrated.py` 的 `integrated_score()` 中新增 continuation bonus，且只对以下 continuation 票型生效：
- `candidate_stage in {'high_7_to_9', 'near_limit_9_plus'}`
- `setup_type in {'LIMIT_STRENGTH', 'HIGH_7_TO_9_BREAKOUT'}`

bonus 来源：
- `limitup_capture_score`
- `seal_order_strength`
- `main_theme_alignment_score`
- `main_theme_core_score`
- `pre_limitup_anomaly / weak_to_strong_reversal / first_board_pre_signal`

### 2. scanner 落盘字段补齐
在 `xiaogu_eastmoney_web_tabs_scan_v0_1.py` 的 scored record 中补齐上述 continuation 相关字段，确保 `integrated_score()` 能消费。

## 验证结果
### A. 定向回归
- continuation bonus 定向测试：PASS
- near-limit 安全测试：PASS
- 概念成分股既有测试：PASS

### B. 全量 runner tests
- `54 passed`

### C. fixed-time dry-run
- 时间点：`2026-06-20 16:23:46`
- official 结果：`PAPER_PICK 300017 网宿科技`
- 安全字段保持：
  - `paper_only=true`
  - `no_trade=true`
  - `allow_trade=false`
  - `auto_order=false`
  - `ledger_line_added=false`

## 影响判断
- 本次 continuation 排序提权没有破坏当前 fixed-time dry-run 的 official 结果
- 本次 continuation 排序提权没有破坏 runner 主测试
- 本次 continuation 排序提权仍保持既有安全边界

## 当前缺口
- 工作区当前缺少活跃 one-year replay 脚本；只存在归档 replay 工具
- 因此本轮无法完成计划里理想态的完整 replay before/after 对照
- 当前只能以：
  1. 定向测试
  2. 全量 runner tests
  3. fixed-time dry-run
  作为本轮实现验证证据

## 结论
continuation 排序提权已最小实现并通过可用验证，属于“可继续推进下一阶段全生命周期全量检测”的状态，但若要正式宣称对历史涨停率/收益率已提升，仍需补活跃 replay 对照链路。