# 待修复项清单 (2026-07-01)

## 已修复 ✅

| # | 问题 | 修复 | commit |
|---|------|------|--------|
| 1 | NO_PICK输出为空 | fallback到highest_score_candidate | 8701d11 |
| 2 | daily_best_paper_watch丢失 | 保留原始NO_PICK诊断 | 8701d11 |
| 3 | runtime snapshot不一致 | fallback元数据写入candidate_features | 8701d11 |
| 4 | NO_PICK测试断言旧语义 | 更新为PAPER_PICK + fallback元数据 | 8701d11 |
| 5 | Scanner summary在detail evidence之后写入 | Phase 1 summary在detail evidence之前写入 | eed417b |
| 6 | candidate_detail_topn缩减候选 | 恢复全量(不缩减候选或域) | eed417b |
| 7 | Bundle candidate_source=None | Phase 1 summary添加candidate_source | d61bca0 |

## 未修复（已知限制）

| # | 问题 | 原因 | 建议 |
|---|------|------|------|
| 1 | Scanner CDP证据收集超时 | 43候选×3域×5秒=645秒, 超过900秒budget | 需要CDP并行化或减少证据域切换 |
| 2 | Scanner在"fetching concept board members"卡住 | CDP API调用慢 | 增加concept board超时 |
| 3 | Phase 1 summary无评分候选 | 评分在detail evidence之后 | runner对无评分候选使用默认评分 |
| 4 | Understand-Anything未集成 | 需要运行`/understand` | 下次session执行 |
| 5 | Scanner partial summary未实现 | Phase 1只在detail evidence之前写入 | 需要在每个batch后更新summary |

## 兼容性风险

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| 1 | Phase 1 summary无评分 | runner可能输出NO_PICK | runner对无评分候选使用默认评分 |
| 2 | NO_PICK fallback改变输出语义 | 依赖NO_PICK的下游可能受影响 | 保留daily_best_paper_watch原始诊断 |
| 3 | candidate_detail_topn恢复全量 | scanner可能更慢 | Phase 1保证summary先写入 |
