# Xiaogu T+1-Only Production Rewrite

Canonical intent packet: `.plan-enforcer/discuss.md`.

将唯一正式生产链收敛为 canonical `T1_NET_RETURN` 的预测与成本后 edge。
删除五模块、PATH、主力/主题/新闻人工组合分、`expected_t1_profit_score` 和
旧 final score 对正式 `PAPER_PICK` 的任何准入、排序或 fallback 权。没有
生产验收模型或合格预测时必须 `NO_PICK`。禁止第二 scorer、selector、runner、
生产入口或回填链；`xiaogu_forward_runner.main` 仍是唯一正式入口。
