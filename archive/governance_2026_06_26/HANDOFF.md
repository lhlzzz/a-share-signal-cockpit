# HANDOFF

## 2026-06-23 per-stock HSGT/experimental 信号全链路验证

完成：
- 修复 `concept_capital_flow` 域映射（`bkzj/gn.html` 精确匹配）。
- 修复 `data_directory_content` 未注入 structured bundle 的断点。
- HSGT extractor 新增 per-stock `northbound_holding` 类型，从 `cells[1]` 提取个股代码。
- experimental extractor 去掉 `[:10]` 限制，新增 `stock_reports` per-stock 解析（`cells[1]` 代码 + `cells[5]` 评级）。
- `component_details` 补 `hsgt_institutional_flow`/`experimental_catalyst_signal`。
- `candidate_setup` return dict 补 `sector_momentum_score`/`sector_momentum_fund_flow`/`news_catalyst_quality_categories`。
- 最终 scan：`data/live_scan/2026-06-23/eastmoney_web_tabs_scan_v0_1_perstock_full_184500/`，5515 quotes、39 scored、18 passed。
- runner dry-run：`PAPER_PICK 300077 国民技术`，score=104.84，`hsgt_institutional_flow=1.0`、`experimental_catalyst_signal=1.0`。
- 安全字段：`ledger_line_added=false`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。

下一步：
- 明天盘中用 CDP 9333 重跑 fresh scan + runner dry-run，验证 per-stock 信号对出票排序有区分度。
- 确认 `SECTOR_MOMENTUM_LOW_POSITION` pool 在板块资金流入时发现低位个股。
- 300077 国民技术作为今日 paper-only 观察票，T+1 回填收益。

风险：
- 本轮所有出票均为 dry-run 纸面票，不写 ledger、不接 broker、不交易。
- `hsgt_institutional_flow` 和 `experimental_catalyst_signal` 对所有候选等值加分（市场级信号），per-stock 区分度来自个股研报评级和北向持股表。

完成：
- CDP 9333 scan 已完成：`data/live_scan/2026-06-11/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_195252/`，5511 quotes、40 scored、22 passed，required/full evidence PASS。
- 当日盘中有效 runner 票为 `PAPER_PICK 300435 中泰股份`；19:52 盘后 shadow runner 输出 `PAPER_PICK 688599 天合光能`，科创板，一手成本 `1428.0`，但不替代盘中票；安全字段 `ledger_line_added=false`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。
- T+1 / 候选补写已按用户要求完成：新增 DECISION line 40-44（`300263`、`920368`、`601012`、`600031`、`300435`），新增 RESULT_FILL line 45-49；已填 `300263 +19.7635%`、`920368 -1.7454%`、`601012 -1.2784%`、`600031 +7.1850%`、`000070 -2.1586%`，`603993` 原已填 `+2.0770%`；`300435` 待 2026-06-12 15:05 后填 T+1。

下一步：
- 下一次盘中重新跑 CDP 9333 scan + 7000 cap runner，不复用盘后结果做执行信号。
- 2026-06-12 15:05 后只用 result_filler append `300435 中泰股份` T+1，不改原 DECISION。

风险：
- 300435 是当日盘中 paper-only/no-trade dry-run 票；688599 只是 19:52 盘后 shadow 结果，不是自动交易或实盘下单。用户若人工参考，需次日实时核对资金、权限、盘口和风险。

## 2026-06-10 CDP 9333 loopback / candidate_fund_recheck 收尾

完成：
- 已确认 Codex 对 `127.0.0.1:9333` 的直接访问受沙箱 loopback 限制；提权后可访问正式 CDP。
- 已把正式 scan 默认口径收回到 `--open-required-cdp-tabs`，`--open-enhanced-cdp-tabs` 改成显式 opt-in。
- 已用本地 mock smoke 验证 `rows_from_candidate_fund_recheck()` 的 fallback：`920368` 会返回 `eastmoney_candidate_fund_recheck_fallback_api`、`secid=0.920368`、`f62` / `主力净流入`。

下一步：
- 如要把这轮 `/tmp` 新 scan 继续喂给 runner，把 summary 放进 `data/live_scan/2026-06-10/` 后重跑。
- 若只需要确认代码修复，当前 fallback 已验证通过。

风险：
- `920368` 本轮没有进入新 scan 候选池，因此 live 侧只证明了 fallback 代码可用，没有证明该票在当前时点一定入池。

## 2026-06-10 历史亏损票规则升级 / 实时出票 Codex 包

完成：
- 已为“2026-05-18 起历史亏损票/低收益票原因复盘并升级规则”完成 Plan Enforcer discuss、draft、review；计划路径 `docs/plans/2026-06-10-xiaogu-historical-losing-ticket-rule-upgrade.md`，最终 review `Verdict: pass`。
- 已给 Codex 准备执行边界：必须先盘点历史证据、分类亏损原因，再最小修改现有 scorer/gate/ticketing；不得 symbol hardcode、不得亏损票黑名单、不得新建平行规则系统。
- 已给 Codex 准备实时出票 dry-run 任务包：只跑当前实时运行链路，不改代码/规则，不使用 CodeGraph/GitNexus/Plan/AgentMemory 参与出票决策。

下一步：
- Codex 回贴实时出票结果后，Claude 验收当前 date/asof、bundle/snapshot/account readonly evidence、`manual_trade_only/paper_only/no_trade/allow_trade=false/auto_order=false` 和 blockers/next recheck。
- Codex 回贴历史规则升级结果后，Claude 验收历史 inventory、loss taxonomy、before/after replay 指标、focused tests、py_compile、diff check、GitNexus detect_changes。

风险：
- 不能把历史收益率优化说成未来盈利保证。
- 不能通过压低 ticket_count 而不报告机会成本来伪装收益提高。
- 不能在实时出票链路里调用治理工具或做规则改动。
- 仓库仍有大量既存无关脏改动；任何 stage/commit 必须精确文件清单。

## 2026-06-10 601012 亏损出票规则回收

完成：
- `single_target_card_status` 已把 `sector_opportunity_score>=1.0 or VEI strong signal` 的资格缺口收口为 `BLOCKED_TARGET`，不再停留在 `MANUAL_WATCH_TARGET`。
- 真实 14:43 bundle replay 已验证：`official_decision=NO_PICK`、`target_status=BLOCKED_TARGET`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。

下一步：
- 如用户继续，再决定是否把同类资格失败规则推广到其他候选或进入 stage/commit 收口。

风险：
- 不改 official decision，不放宽 hard gate，不接 broker / order endpoint。

## 2026-06-10 VEI card inheritance fix

完成：
- `single_target_card` 已修复 scan-level VEI 继承：候选 bundle 只要带 `repo_delta_by_repo.VEI=0.5153`，即使缺 `repo_contributions.VEI`，runner 也会保留 VEI 值并合成 `FBP / first_board_pre_signal` 来源。
- `why_not_official_pick` 现在显示 `VEI:WEAK_OR_PARTIAL`，Qlib 保持 `QLIB_FEATURE_PROXY_NO_MODEL`，QuantDinger 保持 `GUARD_ONLY`。
- 直接复算 14:43 真实 bundle 已确认输出与预期一致。

下一步：
- 如用户继续，再考虑是否把同样的 scan-level fallback 扩展到其它候选；当前 601012 已修复并验证。

风险：
- 不改 Qlib 真模型语义，不改 QuantDinger guard-only 语义，不放宽 official hard gate。

## 2026-06-09 14:43 single target / VEI-Qlib / social research 接续

完成：
- Research layer MVP 已提交为 `2dc67316bc8c7d46eba7fe0a1daafcceed04febf Land xiaogu research layer MVP`，Claude 已验收 commit 只包含允许清单 10 文件；未 push。
- 出票前工作区审计确认主 worktree 脏改动会污染 live ticket path；已用 `/tmp/xiaogu-clean-2dc67316/company-ai-system` 干净隔离 worktree 跑 2026-06-09 14:43 fresh scan。
- 14:43 scan 证据：CDP 9333，`source_time=2026-06-09 14:43:00`，`universe_quote_count=5514`、`scored_count=44`、`passed_count=18`，required/evidence/watchlist 全 PASS。
- 用户将手动卖出 `600396`，后续今日决策账户统一为 `manual_available_cash_6800`；东财实际账户快照只作背景，不再作为并行资金口径。
- 当前唯一标的卡片：`601012 隆基绿能`，`official_decision=NO_PICK`、`target_status=MANUAL_WATCH_TARGET`、6800 口径可买一手，但缺 `sector_opportunity_score>=1.0 or VEI strong signal`，不能伪装成 official `PAPER_PICK`。
- Codex 只读归因：top20 证据 PASS，`sector_opportunity_score` 全 0；6800 下 13 只非硬拦候选资金可买但卡同一机会确认门槛；高涨幅候选多被近涨停/追高/监管硬拦；未发现非主板自动排除。

下一步：
- Claude 发包给 Codex 执行 `/last30days` / 社交平台研究和 Qlib/VEI 诊断：查 A股 14:30 尾盘、小资金 6800、光伏/隆基、Qlib live prediction、VEI 类指标，报告 source 可用性和可复核证据。
- Codex 还需读 Qlib README/docs 与 xiaogu adapter，确认 Qlib 是否实际产生候选级 live prediction、VEI 是否仅弱/中信号、四仓集合是否存在名义接入/实质弱贡献。
- Claude 验收后再决定是否只改报告层、把 social evidence 接入 `research_panel`，或设计 replay 后的 sector/VEI 门槛校准。

风险：
- 不得放宽 `regulatory_hard_block` / `risk_notice` / `near_limit_up_risk` / `CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`。
- 社交平台/last30days 结果只能作为 research/manual-watch 证据，不得直接变成 official `PAPER_PICK`。
- 交易由用户手动；Claude/Codex 不点击交易、不输入密码、不接 broker/order endpoint、不写 live ledger。

## premerge review (2026-06-09)

完成：
- `xiaogu_research_layer_mvp` premerge review 已完成；补强了 `risk_notice` / `a_share_risk_review` 的顶层 `regulatory_hard_block` 传播，formal-high-score 不再能仅靠 `research_signals` 漏过硬拦。

下一步：
- 如需合入，只 stage 本次 `xiaogu` 相关代码、测试和状态文件，不要混入其他 workspace 噪音。

风险：
- 现有工作树仍有大量无关改动，stage 前必须只选本次 review 的目标文件。

## xiaogu_research_layer_mvp (2026-06-09)

完成：
- scanner / runner 已贯通 `research_signals` contract：`industry_chain_tags`、`catalyst_quality.confidence` / `evidence_refs`、`sector_mapping`、`a_share_risk_review`、`adversarial_review`、`historical_pattern`、`research_panel` 均进入 structured scores、paper_scoring_candidates 与 runtime context。
- 风险公告继续硬拦，正向 `positive_catalyst` / `sector_catalyst` 继续可用；`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false` 保持不变。

下一步：
- 如需继续，只做 non-weak-market live scan / replay 对比，不新增 runner，不放宽 hard gate。

风险：
- 真实 fresh scan 仍依赖 CDP 9333 可用性；不自动交易、不接 broker / order endpoint。

完成：
- 2026-06-08 23:09 fresh scan / runner 验证已完成：summary `information_coverage_audit` 非空，runner 顶层 audit 已透传，`paper_scoring_candidates` 真实出现 `news_catalyst_low_position` 与 `intraday_alert_reversal`。
- 2026-06-08 23:15 `NO_PICK` 仍成立，但原因明确为 `002735` 监管 hard block + `QUALIFIED_CANDIDATE_FALSE`；`sector_catalyst_low_position` 本轮仍为 0。
- `sector_fund_flow` 仍为 PARTIAL，说明 sector 侧缺口还在，但不是 silent null。

下一步：
- 若继续查 sector 为空，只追 `sector_fund_flow:PARTIAL` / sector mapping / sector-news 召回，不放宽 hard gate。
- 若准备提交，只 stage 当前 xiaogu 相关代码/测试/状态/ledger 文件，不要混入其他 workspace 的噪音或 runtime dump。

风险：
- hard gate 保持不变，真实样本 NO_PICK 是预期。
- cleanup / archive / stage 仍需按现有审批边界和精确清单执行。

## news catalyst / sector replay (2026-06-09)

完成：
- `classify_news_catalyst_quality()` 已补齐 `risk_evidence` / `regulatory_hard_block` / `observation`，风险公告不会再污染正向 news catalyst。
- `sector_tags_from_text()` 已 canonicalize sector tag，`build_catalyst_index()` 把正向 sector/news 的 sector tag 回灌进 symbol 映射。
- replay fixture 已证明 scan→runner 链路：`NEWS_CATALYST_LOW_POSITION=1`、`SECTOR_NEWS_LOW_POSITION=1`，runner 侧 layers 真实出现 `news_catalyst_low_position` / `underwater_reversal`。

下一步：
- 如果还要看真实 live 数据，只在可访问的 CDP 9333 环境里重跑 fresh scan；否则继续用 replay fixture 做对比，不放宽 hard gate。

风险：
- 这次 live CDP fresh scan 命令在沙箱里没有落盘，当前证据来自 replay fixture + 定向 pytest，不是线上交易或自动化下单结果。

## news catalyst / sector replay (2026-06-09) 补充

完成：
- news / sector replay fixture 已分离，runner 侧 `paper_scoring_candidates` 可分别落到 `news_catalyst_low_position` 和 `sector_catalyst_low_position`，pytest `68 passed`。

下一步：
- 如需继续验证 sector 的真实召回，只能在可访问的 CDP 9333 环境里重跑现有 scanner。

风险：
- 真实 fresh scan 仍 sector zero，原因已明确为 `sector_pool_count=0` / `sector_news_not_mapped_to_low_position_symbols`。

## PM2 ecosystem bootstrap (2026-06-05)

完成：
- `xiaogu` 已新增 PM2 ecosystem，指向 `/root/.local/bin/hermes-cdp xiaogu`，CDP 端口 `9333`。

下一步：
- `/clear` 或 compact 后继续六主项目瘦身第一轮；先做可逆、非破坏性整理。

风险：
- 不要用 PM2 配置替代人类确认点；遇到验证码、登录、交易、支付、税务或外部可见动作仍需停下确认。
