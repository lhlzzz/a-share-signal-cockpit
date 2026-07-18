# xiaogu replay 恢复与阻塞定位（2026-06-20）

## 已完成
- 活跃 replay 入口已恢复：`xiaogu_runner_chain_replay_compare.py`
- 脚本可运行并输出 compare json
- 说明生命周期不再卡在“没有 replay 入口”

## 新暴露的第二层阻塞
### legacy bundle 被 active-chain governance 全拦
replay 现在能读到 ledger fallback，但在 `evaluate_candidate_bundle()` 前被 governance 拦掉，典型原因：
- `ACTIVE_RULE_VERSION_MISMATCH_missing`
- `ACTIVE_A_SHARE_SOURCE_NOT_ALLOWED_legacy_verified_bundle`
- `ACTIVE_CANDIDATE_BUNDLE_PATH_NOT_ALLOWED`

## 影响
- replay 入口恢复了，但早期历史样本仍无法用当前主链规则重放
- 这意味着“全生命周期完整 replay 闭环”仍未彻底修通

## 当前判断
xaiogu 的全链路最大薄弱点已从：
1. **没有活跃 replay 入口**
转变为：
2. **active-chain governance 不允许 replay 使用 legacy bundle**

## 下一步修复方向
- 只对 replay / historical compare 场景增加受控的 legacy bundle governance 旁路
- 不能把该旁路放进实时主链 official 运行路径
- 必须保持 live / replay 分层