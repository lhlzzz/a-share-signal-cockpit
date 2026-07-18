# xiaogu 阶段总结：DB 复盘 + 代码收敛

## 已完成
- DB-first 复盘报表已落地，支持终端和 HTML 双输出。
- Scanner 和 Runner 的文件读取重复问题已收敛到缓存层。
- 历史数据回填结果已确认进入 DB，`daily_candidates / picks / returns` 都有可核对样本。
- 分析侧已具备 `setup_class`、`rank_bucket`、即时/滞后收益分层。

## 关键发现
- Rank 4-6 在当前样本里优于 Rank 1-3 和 7-10，说明“越靠前越好”不成立。
- `UNDERWATER_RED_FLAT_RECOVERY` 是当前高质量样本，`INTRADAY_ALERT_REVERSAL` 是当前弱样本。
- 这类分层结果应先做权重偏置和回放验证，不建议直接硬编码成绝对过滤。

## 运行中暴露的问题
- 历史回填脚本对整文件/重模块导入敏感，说明需要流式与惰性导入。
- 历史数据存在多来源边界：6/6 之后以 `forward_candidate_bundles` 为主，5 月需要 `forward_result_evidence` 补位。
- 执行路径对环境变量和导入边界较敏感，需要继续收敛入口与验证方式。

## 后续建议
- 先把 rank 分层、setup_class 分层、即时/滞后收益这些结论转成稳定的报表和回放验证。
- 再把 Rank 4-6 的偏置做成权重而不是硬过滤，避免过拟合阶段样本。
- 继续收敛 scanner 主入口与 DB 读写路径，减少重复 IO 和多口径问题。
