当前交付（2026-07-30）：
- A 股运营看板已接入 `/dashboard/` 与 `/api/dashboard/overview`，只读 PostgreSQL 纸面结果。
- 最新验证：2026-07-30 runner dry-run 为 `NO_PICK`；不因“需要出票”强行放行，当前阻断为辅助证据不完整与缺少直接催化确认。
- 历史 ledger 已通过迁移脚本补入缺失的 10 条记录，第二次运行幂等（新增 0、跳过 0）。
- 全量测试 `451 passed, 2 skipped`；保留 Scrapy/Twisted pending-task 警告作为验证风险。
- 浏览器上下文无法访问 localhost，已完成 `/health`、`/dashboard/`、`/api/dashboard/overview` HTTP smoke；未伪造截图通过。

持续规则：
- 当日涨停/封死不可交易标的不得进入可交易候选池。
- T 日下跌票不直接排除，必须通过 T+1 获利证据门。
- `@sszcw` 只作软上下文与解释排序，不强制出票。
- 保持 PAPER_ONLY / NO_TRADE / ALLOW_TRADE=False，继续每日复盘 T+1 收益与出票稳定性。
