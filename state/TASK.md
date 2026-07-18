# TASK

## 2026-06-23 全链路增强 + 买入时机 + 价格上限调整

当前状态：
- 已完成全链路增强：replay_only_sector_opportunity 改为只拦截无真实板块标签的票；overheated_market 只拦截近涨停阶段；eligibility 恢复 sector_gate_pass；entry_price_plan 已集成到 single_target_card。
- 已修复价格上限：ONE_LOT_COST_CAP 从 7000→10000，价格上限从 70→100 元，覆盖更多好票。
- 已完成 CDP 浏览器重启：`pm2 restart xiaogu-cdp` 后 `http://127.0.0.1:9333/json/version` 返回 `Chrome/146.0.7680.177`。
- 已完成 23 号数据重跑复核：runner 在同源 `--asof-time 15:00:00` 下仍输出 `PAPER_PICK 301236 软通动力`，`entry_strategy=dip_entry`，`ideal_buy=40.08`；在 `--asof-time 18:45:00` 下因 `SCAN_TOO_OLD_225.0M_GT_15M` 变为 `NO_PICK`，closest-to-pick 仍是 `301236 软通动力`。
- 24 号软通动力跌约 5%，验证了出票逻辑需要继续迭代。
- py_compile PASS；59 tests PASS；governance_check PASS。

下一步任务：
1. 如有 2026-06-24 same-day verified candidate bundle，用新口径再跑一次；当前 runner 仍因 `NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE` 无法给出 watch 候选。
2. 如需让 23 号“新规则重跑”完全切到最新 18:45 scan 产物，需要补做同源 candidate bundle 重建，避免 runner 回落到 15:00 旧 bundle。
3. 保持 PAPER_ONLY / NO_TRADE / ALLOW_TRADE=False，继续每日复盘 T+1 收益。

当前状态：
- 已实现 `extract_hot_sector_names_from_capital_flow()`：从 `concept_capital_flow` 实时排行动态提取 top N 热点板块（按涨跌幅排序），替代硬编码板块列表。
- 已修复 `hsgt_institutional_flow` 无个股区分度问题：per-stock 聚合改为个股级 `northbound_holding` + `min(0.3, market_inflow+accumulation)`，不再被 market_overall 信号饱和。hsgt 已分化为 0.3（仅市场级）和 0.7（个股级+市场级）两档。
- 已修复 `experimental_catalyst_signal` 无个股区分度问题：per-stock 聚合改为个股级 `stock_report_rating` + `min(0.2, market_research)`。
- 已实现概念成分股板块标签注入：`merge_concept_stocks_into_quotes` 后新增 `code_to_board` 映射，给已有行情池股票打上 `concept_sector_tags`，确保 sector_edges 和 `sector_opportunity_score` 正确计算。
- 验证：23 号收盘数据重跑 scan 15 个板块 738 只成分股（重组蛋白/阿兹海默/CAR-T/CRO/创新药/减肥药/人形机器人/国产芯片/5G 等）；17 个候选有热点板块标签。
- 已确认 `300077 国民技术` 后续下跌暴露了严重 gate 缺陷：它与 `002283 天润工业`、`301236 软通动力` 的共同点是缺 `limitup_capture/seal_order/limitup_reason`，却因板块标签、历史复盘资金流和轻微盘口确认被提升为 official。已收紧 eligibility：板块/历史 replay 只能召回和排序，official 必须有个股级延续确认（强涨停捕捉、封单/涨停原因、强 VEI、明确正向催化或真实 data_directory 主力资金流）。
- 已进一步确认 `301236 软通动力` 的直接漏口：它不是纯 replay-only，而是 `LOW_POSITION_SECTOR_LIFT` + 真实板块标签 + 低位催化分 + 轻微买点确认，再叠加 `data_directory_capital_flow>=5000w` 被误当作个股延续确认，因此 6/23 被放行为 official，但 6/24 并未次日强兑现。
- 已修复 `LOW_POSITION_SECTOR_LIFT` official gate：对该低位启动路径，不再允许仅凭 `data_directory_capital_flow>=5000w` 充当个股级 continuation confirmation；若缺 `limitup_capture_confirmation_pass` / `underwater_reversal_confirmation_pass` / `strong_high_momentum_continuation_pass` / 其它 stock-level confirmation，则新增 blocker `stock_level_continuation_confirmation_required`。
- 回放验证：修复后 2026-06-23 15:00 official 从 `PAPER_PICK 301236 软通动力` 变为 `NO_PICK`，closest-to-pick 变为 `300077 国民技术`；2026-06-24 15:00 仍为 `NO_PICK`（原因仍是 `NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE`，不是本次规则回归）。
- 验证：新增 focused test 通过（`2 passed`），相邻 breakout/underwater 回归 `4 passed`，`py_compile` PASS。

下一步任务：
1. 下一次盘中用 CDP 9333 跑 fresh scan，验证动态热点板块在不同市场日（如医药日/新能源日/科技日）自动切换。
2. 盘中验证 `SECTOR_MOMENTUM_LOW_POSITION` pool 在板块资金流入时发现低位个股。
3. 确认 hsgt/experimental 信号在有 stock_reports/北向个股数据时能产生更高区分度。

## 2026-06-23 前瞻板块探测器实现

当前状态：
- 已完成数据源消费审计：`concept_capital_flow`（概念板块资金流）完全未采集，`hsgt_holdings`/`hsgt_capital_flow` 完全未采集，`sector_fund_flow` 弱消费（只基于证据数量，不基于资金流入量）。
- 已实现前瞻板块探测器：
  1. 新增 `concept_capital_flow` 到 `DEFAULT_ENHANCED_CDP_TAB_URLS` 和 `CORE_ENHANCED_EVIDENCE_DOMAINS`，开始采集概念板块资金流数据。
  2. 更新 `build_sector_opportunity_snapshot()` 解析概念资金流数据，提取板块资金流入量 `fund_flow_amount`。
  3. 更新 `sector_strength_by_tag` 计算：从纯证据数量改为 `evidence_score * 0.4 + fund_flow_score * 0.6`，使用实际资金流入量。
  4. 更新 `sector_opportunity_score` 计算：对有正向资金流入的板块加成 0.3 分（`fund_flow_amount / 5亿 * 0.3`）。
  5. 新增 `L9_SECTOR_MOMENTUM` source layer：检测板块资金流入 + 低位个股 = 前瞻信号。
  6. 新增 `SECTOR_MOMENTUM_LOW_POSITION` pool（priority 48，介于 `SECTOR_NEWS_LOW_POSITION` 50 和 `INTRADAY_ALERT_REVERSAL` 45 之间）。
  7. 新增 `sector_momentum_score` 和 `sector_momentum_fund_flow` 字段到 setup_profile 和 annotated candidate。
- 验证：`py_compile` PASS；`tests/test_xiaogu_a_share_forward_runner.py` 55 passed；`scripts/xiaogu_governance_check.py` PASS。
- 本轮未放宽任何 hard gate，未交易、未下单、未写 live ledger。

下一步任务：
1. 用真实 CDP 9333 跑一次 scan + runner dry-run，验证概念资金流数据被正确采集和消费。
2. 确认 `SECTOR_MOMENTUM_LOW_POSITION` pool 能在创新药/传统中药等板块资金流入时发现低位个股。
3. 验证 `sector_opportunity_score` 因资金流入加成而提升，帮助候选通过 `sector_opportunity_score>=1.0` hard gate。
4. 继续审计其他未消费数据源（`hsgt_holdings`/`hsgt_capital_flow`）的接入优先级。

## 2026-06-23 天润工业出票偏离目标根因

当前状态：
- 已确认 `002283 天润工业` 正式出票记录来自 `2026-06-23 15:10:00` runtime，不是 2026-06-22 当日 runtime 的 official pick。
- 直接根因是 6/22 接入的 `candidate_intraday_replay` 结构化字段被用于实时 official gate：`REPLAY_HISTORY_FLOW` / `REPLAY_INDUSTRY_RANK` / `REPLAY_STOCK_PROFILE` 推高 `sector_opportunity_score=1.0` 和 `main_theme_core_score`，让 `LOW_POSITION_SECTOR_LIFT` 低位票通过。
- 该票缺涨停预期关键确认：`limitup_capture_score=0`、`limitup_capture_confirmed=false`、`seal_order_strength=0`、`limitup_reason_strength=0`，且 research panel 多项 FAIL，因此偏离“高收益 / 涨停板预期”总目标。

当前修复：
- 已在 `xiaogu_forward_d1_1450_runner_v0_1.py` 新增 replay-only sector gate：`candidate_intraday_replay` 产生的 `REPLAY_HISTORY_FLOW` / `REPLAY_INDUSTRY_RANK` / `REPLAY_STOCK_PROFILE` 只能作为 replay/diagnostic/ranking-assist，不能单独满足 official `sector_opportunity_score>=1.0`，也不能压制 research/adversarial FAIL。
- 已用 2026-06-23 天润工业 runtime 回放确认：修复后会出现 `replay_only_sector_opportunity_not_official_gate` blocker，不再 eligible。
- 验证：`py_compile` PASS；focused gate regressions `5 passed, 50 deselected`；full runner tests `55 passed`；`git diff --check` PASS；GitNexus detect_changes low risk / 0 changed symbols / 0 affected processes。

下一步任务：
1. 下一次 live scan + runner 重点确认复盘-only 票不会再进入 official pick。
2. official PAPER_PICK 继续要求真实当日 sector/limitup/order-book/buy confirmation 证据；历史资金流/行业排名/stock profile 只能作为诊断或辅助分，不得绕过涨停确认。

# TASK

## 2026-06-20 真实 CDP 9333 跑通：从热门题材出票

当前状态：
- 已在 `xiaogu_eastmoney_web_tabs_scan_v0_1.py` 新增三个核心函数：`fetch_concept_board_list_api()`、`fetch_concept_member_stocks_api()`、`merge_concept_stocks_into_quotes()`。
- 扫描管线已集成：`catalyst_index` 构建后，自动从 `sector_opportunity_snapshot` 取 top 10 板块，通过东财 API 拉取每个板块成分股（最多 50 只/板块），合并到 quotes 池后再走 `build_candidates()`。
- summary 已新增 `concept_member_stock_fetch` 字段，记录拉取的板块、成分股数量和使用的 top 概念列表。
- 验证：`py_compile` PASS；`git diff --check` PASS；full runner tests `52 passed`（含 4 个新增概念相关测试）。
- 本轮未放宽任何 hard gate，未交易、未下单、未写 live ledger。

下一步任务：
1. 用真实 CDP 9333 跑一次 scan + runner dry-run，验证概念成分股是否真实进入候选池，且出票来自热门题材板块（机器人/芯片/5G等）而非一般零售。
2. 若概念 API 不可用（反爬/限流），需要 fallback 到 CDP 详情页抓取。
3. 验证新出票的 `sector_opportunity_score`、`VEI` 和 `tradingagent_a` 是否来自真实概念板块映射。
4. 继续保留 broken_limit_recovery、intraday_volume_price_confirm、seal_order_strength 作为 shadow/diagnostic 字段。

## 2026-06-19 数据中心资金流候选 final ordering 阻塞

当前状态：
- 已确认新接入的数据中心资金流候选能进入 runner 候选池，且此前修复已让 `300166 东方国信` 这种主力净流入候选达到 `eligible=True`、`score=69.4`。
- 本轮已修复 final runner 仍选 `000679 大连友谊` 的直接原因：`evaluate_candidate_bundle()` 不再按 `paper_scoring_candidates` 原始顺序遇到第一个 `PAPER_PICK` 就返回，而是在所有已通过 official hard gate 的 `PAPER_PICK` 候选中按 `data_directory_capital_flow.main_force_net_inflow`、data directory 来源、data directory 补分、score、rank 做稳定优先级选择。
- 本轮同时修复 `inject_live_fund_flow_into_candidates()` 覆盖主源的问题：已有行情中心资金流时，live API 只写 `data_directory_capital_flow_live_supplement`，不覆盖 `data_directory_capital_flow` 主源，避免 9.70 亿被小值覆盖导致排序和诊断失真。
- 本轮没有放宽监管/证据/资金/source_time/no-trade/near-limit/research hard gate；早先会让资金流绕过 gate 的 diff 已收回，资金流只参与已 eligible 候选之间的 official ordering。

验证：
- GitNexus impact：`evaluate_candidate_bundle` LOW，`decision_for_candidate` LOW；新增本地函数 `inject_live_fund_flow_into_candidates` 尚未进索引。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile xiaogu_forward_d1_1450_runner_v0_1.py tests/test_xiaogu_a_share_forward_runner.py` PASS。
- focused capital-flow regression：`2 passed, 45 deselected`。
- full runner tests：`47 passed`。
- `git diff --check` PASS；GitNexus detect_changes low risk，changed_symbols=0，affected_processes=0。

下一步任务：
1. 已完成 2026-06-19 同源 dry-run 复核：runner 使用 `source_time=2026-06-19 15:05:00` 同源运行后 official 为 `NO_PICK`，不是 `000679/000625`，也没有强行把资金流候选绕过 hard gate 变成 official。
2. 当前主要后续缺口不是 final ordering 继续按原始顺序误选，而是热点题材/资金流候选仍可能因 `near_limit_up_risk`、`sector_opportunity_score>=1.0 or VEI strong signal` 等 hard gate 被挡；若继续推进，只追“热点题材页/资金流页数据是否贯通到 sector/VEI/候选映射和 final ordering”，不要放宽 runner hard gate。
3. 如需提交，精确 stage `xiaogu_forward_d1_1450_runner_v0_1.py`、`xiaogu_eastmoney_web_tabs_scan_v0_1.py`、`tests/test_xiaogu_a_share_forward_runner.py`、`TASK.md`、`LOG.md` 及 memory spillover，避免混入大量 runtime/data 噪音。

## 2026-06-18 第三层增强证据降回观察层

当前状态：
- 已按最新实验结论撤回第三层增强证据对 official 主链的硬约束地位：`seal_order_strength`、`broken_limit_recovery`、`intraday_volume_price_confirm` 不再作为 `dynamic_signal_confirmation_profile()` 中 `high_momentum` / `high_7_to_9` 的 hard gate。
- scanner 侧字段继续保留：`quote_reversal_risk()` 仍显式产出 `broken_limit_recovery` / `broken_limit_recovery_reason`，`build_structured_scores()` 仍产出 `intraday_volume_price_confirm` 与 `seal_order_strength`，供 observation / ranking-assist / shadow compare 使用。
- official breakout 动态确认已恢复到 baseline 口径：只要求 `close_position` / `fund_flow` / `time_series` / `pre_signal`，不再要求 `high_7_to_9_breakout_recovery_or_seal_confirmed`，也不再要求 `high_7_to_9_breakout_intraday_volume_price_confirm>=0.75`。
- 撤回原因固定：这轮实验只证明第三层会更严格筛票，但未证明涨停率更高、胜率更高、总收益更高；同时已观察到出票变少、胜率变差。

下一步任务：
1. 固定当前 baseline 主链，按实时路径继续只读出票观察，不再把第三层当 official hard gate。
2. 保留 `broken_limit_recovery`、`intraday_volume_price_confirm`、`seal_order_strength` 作为诊断字段、辅助排序字段和复盘标签。
3. 后续只有在可比样本足够且不降低 baseline 胜率/收益的前提下，才允许重新评估是否晋级为 hard gate。

## 2026-06-17 手动卖出价格区间 / 社交信息主来源口径

当前状态：
- 已在 `xiaogu_forward_d1_1450_runner_v0_1.py` 新增 `manual_exit_price_plan()`，当前会随 `single_target_card` 与 `daily_best_paper_watch` 一起输出：次日早盘手动卖出价格区间、弱开参考价、强开止盈参考价、止损价。
- 该输出严格保持 observation-only / manual-only：不改 official `PAPER_PICK`、不改东财行情源、不改 no-trade/allow_trade=false/auto_order=false 安全边界。
- 社交平台口径已更新：`last30days` / 公开社交资料 / MediaCrawler 可作为 `research_signals` / `social_catalyst` 的主要信息来源，但只能走 sidecar / research path，不替代东财正式行情、资金流、盘口、自选、公告数据源。
- 验证已完成：`python3 -m py_compile xiaogu_forward_d1_1450_runner_v0_1.py tests/test_xiaogu_a_share_forward_runner.py` PASS；`python3 -m pytest -q tests/test_xiaogu_a_share_forward_runner.py -k "manual_exit_price_plan or daily_best_paper_watch or single_target_card_paper_pick_behavior_unchanged or no_pick_main_emits_daily_best_paper_watch or social_catalyst_is_diagnostic_only_for_daily_watch"` => `3 passed, 58 deselected`。

下一步任务：
1. 生成一份真实 `last30days_social_catalyst.json` sidecar，验证公开社交资料能否稳定进入 `research_signals`。
2. 盘中用 CDP 9333 fresh scan + runner dry-run 验证新卖出区间是否对 `PAPER_PICK` 和 `DAILY_BEST_PAPER_WATCH` 都正常落盘。
3. 继续补强封单强度、炸板回封、分时量价确认，不改东财正式行情源。

## 2026-06-11 实时/盘后出票确认 + T+1 更新

当前状态：
- 本轮已按实时运行链路完成只读 scan + runner dry-run；最新 scan 为 `data/live_scan/2026-06-11/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_195252/`，`source_time=2026-06-11 19:52:52`，5511 quotes、40 scored、22 passed，full evidence PASS。
- 当日盘中有效 runner 结果仍为 `PAPER_PICK 300435 中泰股份`；19:52 盘后 shadow runner 使用 7000 cap account snapshot 输出 `PAPER_PICK 688599 天合光能`，一手成本 `1428.0`，`one_lot_cost_cap=7000.0`，原因 `ALL_FORWARD_PAPER_HARD_GATES_PASS`，但不替代盘中票。
- 安全检查全部通过：`ledger_line_added=false`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`；本轮未连接 broker、未交易、未下单。
- 用户要求全部写入后，已新增 DECISION line 40-44：`300263 隆华科技`、`920368 连城数控`、`601012 隆基绿能`、`600031 三一重工`、`300435 中泰股份`；新增 RESULT_FILL line 45-49：`300263 +19.7635%`、`920368 -1.7454%`、`601012 -1.2784%`、`600031 +7.1850%`、`000070 -2.1586%`；`603993` 原已填 `+2.0770%`。
- scoreboard dry-run 当前：14 笔 `PAPER_PICK`、已填 13 笔、胜率 `84.6154%`、平均收益 `2.9607%`、profit factor `8.0613`、A_SHARE_CHAIN score `88.03`；`300435` T+1 待 2026-06-12 15:05 后回填。

下一步任务：
1. 保持当前规则不变，不改 gate / threshold / scoring / official decision / candidate generation。
2. 下一次盘中重新跑 CDP 9333 scan + 7000 cap runner，不把 19:52 盘后结果当作执行信号。
3. 2026-06-12 15:05 后继续只用 result_filler append `300435 中泰股份` T+1；禁止改写原 DECISION。

## 2026-06-11 300263 missed winner 修复完成

当前状态：
- `xiaogu_forward_d1_1450_runner_v0_1.py` 已改为对 candidate `source_time` 做 asof-valid 选择，不再把 `date` / `source_market_date` 这类日期字段当成有效时间戳。
- `decision_for_candidate()` 和 `single_target_card` 现在都使用同一条 normalized `source_time`，`source_time<=asof_time` 的诊断可回显。
- `underwater_reversal` 的窄确认路径保留，仍然要求 `data_gate_status=PASS`、`candidate_evidence_status=PASS`、`source_time<=asof_time`、`risk_penalty=0`、无安全硬拦；`sector/VEI` 仍只对非 underwater 路径强制。
- `tests/test_xiaogu_a_share_forward_runner.py` 已补齐对应回归测试数据，`pytest` 通过。
- 真实 `--dry-run` 仍然是 `NO_PICK`，但当前实际 bundle 里 300263 仍被别的硬拦挡住；这次修复验证的是通用路径正确，不是现场强塞出票。

下一步任务：
1. 如继续，只做诊断或更晚时点复跑，不改 gate / threshold / scoring / official decision。
2. 保持 `source_time` 语义为 asof-valid evidence time，不回退到日期字段。

## 2026-06-10 CDP 9333 loopback / candidate_fund_recheck 收尾

当前状态：
- Codex 沙箱直连 `http://127.0.0.1:9333` 时会触发 `Operation not permitted`；按 `require_escalated` 重跑后可访问，确认是 loopback 权限问题，不是正式浏览器坏。
- `xiaogu_eastmoney_web_tabs_scan_v0_1.py` 现已把 `--open-enhanced-cdp-tabs` 收口为显式 opt-in，正式 scan 默认只开 `--open-required-cdp-tabs`。
- `rows_from_candidate_fund_recheck()` 已有 fallback API，本地 smoke 已确认 `920368` 会走 `eastmoney_candidate_fund_recheck_fallback_api`，返回 `secid=0.920368`、`f62` / `主力净流入`。
- 这轮 `/tmp/xiaogu-candidate-fundflow-20260610` 中 `601012` 的 `candidate_fund_recheck=1` 正常；`920368` 没进入该轮候选池，所以 live scan 侧没有直接看到它。
- runner dry-run 仍是 `NO_PICK`，但它读取的是日期目录下既有 latest summary，不是 `/tmp` 新输出；官方卡点仍是 `EASTMONEY_CANDIDATE_RECHECK_EVIDENCE_MISSING_candidate_fund_recheck` + `QUALIFIED_CANDIDATE_FALSE`。

下一步任务：
1. 如要让 runner 消费本轮新 scan，把 summary 放到 `data/live_scan/2026-06-10/` 后重跑。
2. 若只看代码修复，`candidate_fund_recheck` fallback 已在本地 smoke 通过，无需继续改 gate。
3. 保持正式 scan 口径为 `--open-required-cdp-tabs`，不要默认打开 enhanced tabs。

## 2026-06-11 300263 missed winner 根因审计

当前状态：
- 已完成 `300263 隆华科技` 的 closest-to-pick / runtime / scan 证据审计。
- 结论：没有真实安全硬拦；主要是 `qualified_candidate=false` 下的 formal confirmation gate + 15:10:30 runner 读取了更晚的 21:51:58 bundle/source_time 证据。
- `sector_opportunity_score=0`、`VEI=0`，因此仍缺 `sector_opportunity_score>=1.0 or VEI strong signal`。

下一步任务：
1. 如要回归，只补 asof/source_time 证据传递，不改 gate / threshold / scoring / official decision。
2. 如要做回归诊断，优先输出 `would_have_picked_under_underwater_reversal_rule=true` 之类的只读诊断，不写 forward ledger。

## 2026-06-10 四仓命名收敛 / 浏览器证据阻塞

当前状态：
- 实时链路已从 six_repo 语义收敛到 four-repo/current-repo 语义：`six_repo_integration_real_v2_1.py` 新增 `aggregate_four_repo_native_signals()`，`xiaogu_v2_1_six_repo_real_integrated.py`、`xiaogu_eastmoney_web_tabs_scan_v0_1.py`、`xiaogu_forward_d1_1450_runner_v0_1.py`、`xiaogu_eastmoney_tail_scan_v0_2.py` 已改为调用 four-repo 入口。
- 历史 replay `xiaogu_v2_1_six_repo_one_year_topk_replay.py` 已从活跃 workspace 删除，`PIPELINE.md` / `FILE_MANIFEST.md` 已标明其为历史归档，不再是活跃入口。
- 第二部分所需的项目隔离浏览器/MCP 入口不可用；本轮没有拿到 920368 的真实资金流请求证据，因此不能继续猜 fallback 接口。

下一步任务：
1. 保持 six_repo 兼容壳仅作过渡，不再扩展 six-repo 语义。
2. 等待项目隔离浏览器/MCP 入口可用后，再抓 920368 / 601012 资金流真实请求证据。
3. 证据齐全后再最小补接 `candidate_fund_recheck` fallback。

## 2026-06-10 candidate_fund_recheck fallback 阻塞

当前状态：
- 已确认现有实现只从 `push2delay.eastmoney.com/api/qt/stock/get` 的 `f137` 读 `主力净流入`，`rows_from_candidate_fund_recheck()` 只是复用 quote 行，没有独立 fallback。
- 已确认资金流页面稳定 URL 为 `https://data.eastmoney.com/zjlx/detail.html`，个股页为 `https://data.eastmoney.com/zjlx/{code}.html`。
- 本轮浏览器/CDP 与外网都不可用，无法可靠拿到真实请求 URL / 参数 / 返回字段名，因此不能安全补 fallback。

下一步任务：
1. 先补浏览器/CDP 或人工导出的东财资金流请求证据。
2. 证据齐全后再最小修改 `xiaogu_eastmoney_web_tabs_scan_v0_1.py` 和相关测试。
3. fallback 失败时继续保持 missing，不改 gate。

## 2026-06-10 candidate_fund_recheck 诊断

当前状态：
- `920368 连城数控` 的 `candidate_fund_recheck` 缺失是真实源证据不可得，不是 scan→bundle→runner 丢字段；`601012 隆基绿能` 同日对照有完整 `candidate_fund_recheck=3`。
- runner 的 `EASTMONEY_CANDIDATE_RECHECK_EVIDENCE_MISSING_candidate_fund_recheck` 仍是正确风控拦截，`NO_TICKET` 保持成立。

下一步任务：
1. 如继续，只看更晚时点或其他候选，不把 missing 改成 pass。
2. 如要减少误判，只补诊断输出，不改门槛。

## 2026-06-10 实时出票 dry-run

当前状态：
- 已完成今天的实时 scan→runner dry-run，使用固定东财 CDP 9333，只读读取行情/候选/证据，不接交易接口。
- scan 结果：`source_time=2026-06-10 15:10:00`，`universe_quote_count=5513`，`market_breadth_up_pct=28.22`，`scored_count=42`，`passed_count=20`，full evidence pack PASS，enhanced / experimental coverage PASS。
- 代表性候选：`601012 隆基绿能` 仍在 scan 侧高分（`score=76.5814`，`VEI=+1.5167`，`Qlib=+1.0204`），但 runner 官方结果仍是 `NO_PICK`。
- runner 结果：`decision=NO_PICK`，`single_target_card.symbol=920368`，`target_status=BLOCKED_TARGET`，官方拦截为 `EASTMONEY_CANDIDATE_RECHECK_EVIDENCE_MISSING_candidate_fund_recheck` + `QUALIFIED_CANDIDATE_FALSE`；`manual_trade_only=true`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。

下一步任务：
1. 如继续出票，只在现有 live chain 上用更晚的 as-of 重跑，不新增链路、不改 gate。
2. 若要补票，先补 `candidate_fund_recheck` / 资格判定证据，再看是否仍然 `NO_PICK`。
3. 今天结论固定为 `NO_TICKET`，不要把 scan 高分误写成官方出票。

## 2026-06-10 601012 亏损出票规则回收

当前状态：
- `single_target_card_status` 已收口：`paper_pick_eligibility.missing_conditions` 含 `sector_opportunity_score>=1.0 or VEI strong signal` 时，`target_status` 从 `MANUAL_WATCH_TARGET` 升为 `BLOCKED_TARGET`。
- 14:43 真 bundle replay 已验证：`official_decision=NO_PICK`、`target_status=BLOCKED_TARGET`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`、`ledger_line_added=false`。

下一步任务：
1. 如用户继续，再决定是否把同类资格失败规则推广到其他候选或收口提交；本次 601012 已完成。

## 2026-06-10 VEI card inheritance fix

当前状态：
- `single_target_card` 已修复：当候选 bundle 只有 scan-level `repo_delta_by_repo.VEI=0.5153`、缺 `repo_contributions.VEI` 时，runner 会保留 VEI 值并合成 `FBP / first_board_pre_signal` 来源。
- `why_not_official_pick` 现在显示 `VEI:WEAK_OR_PARTIAL`，不再是 `PLACEHOLDER`；Qlib 仍是 `QLIB_FEATURE_PROXY_NO_MODEL`，QuantDinger 仍是 `GUARD_ONLY`。
- 已做直接复算验证：14:43 真实 bundle 输出 `repo_delta_by_repo.VEI=0.5153`、`repo_contributions.VEI.candidate_signal=FBP / first_board_pre_signal`。

下一步任务：
1. 如用户继续，再评估是否把同样的 scan-level fallback 扩展到其它候选；本次 601012 已完成。

## 2026-06-09 single target / VEI-Qlib / last30days research

当前状态：
- `xiaogu_research_layer_mvp` 已提交为 `2dc67316bc8c7d46eba7fe0a1daafcceed04febf Land xiaogu research layer MVP`，未 push。
- 今日出票决策账户口径统一为 `manual_available_cash_6800`：用户手动卖出 `600396 华电辽能` 后按可用资金约 6800、空仓情景判断；实际东财账户快照只作背景证据。
- 14:43 fresh scan / runner 在隔离 worktree `/tmp/xiaogu-clean-2dc67316/company-ai-system` 完成：CDP 9333，`source_time=2026-06-09 14:43:00`，`universe_quote_count=5514`、`scored_count=44`、`passed_count=18`，证据覆盖 PASS。
- 当前 `single_target_card` 样例：`601012 隆基绿能`，`official_decision=NO_PICK`、`target_status=MANUAL_WATCH_TARGET`、`available_cash=6800`、`one_lot_cost=1293`、`ledger_line_added=false`；未过 official `PAPER_PICK` 是因为缺 `sector_opportunity_score>=1.0 or VEI strong signal`。
- Codex 归因：不是行情读取不足；top20 候选 evidence PASS，但 `sector_opportunity_score` 全 0。6800 下 13 只非硬拦候选资金可买但卡同一机会确认门槛；高涨幅候选主要被近涨停/追高/监管 hard gate 拦截；未发现非主板自动排除。

下一步任务：
1. Claude 发包给 Codex 执行 `/last30days` / 社交平台研究，查证 A股 14:30 尾盘、小资金 6800、隆基/光伏、Qlib live path、VEI 类指标；所有外部证据只能进入 `research_panel` / `MANUAL_WATCH_TARGET`，不得绕过 official hard gate。
2. Codex 专项读取 Qlib README/docs 与 xiaogu adapter，确认 Qlib 是否真正产生 live 候选级 prediction，VEI 是否只是弱/中信号未达 strong，`sector_opportunity_score` 全 0 是召回/映射/写入/adapter 读取哪一层问题。
3. Claude 验收 Codex 结果后，再决定是只改报告层、接入 social evidence，还是设计 replay 后的 sector/VEI 阈值校准。

禁止：
- 不点击买入/卖出/撤单/提交/确认，不输入交易密码/资金密码/短信验证码，不接 broker/API key/order endpoint。
- 不写 live ledger，不把社交热度直接变成 official `PAPER_PICK`。
- 不放宽 `regulatory_hard_block`、`risk_notice`、`near_limit_up_risk`、`CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`。
- 主工作区仍 dirty，禁止直接在主工作区跑出票、`git add .`、reset/clean/stash。

## 2026-06-09 premerge review
- 已完成 `xiaogu_research_layer_mvp` premerge review。
- 已补强 `risk_notice` / `a_share_risk_review` 在 `structured_signal_profile` 中的顶层 `regulatory_hard_block` 传播，避免 formal-high-score 仅凭 `research_signals` 漏过 hard gate。
- 验证：`69 passed, 8 subtests passed`；`py_compile` PASS；`git diff --check` PASS。

当前状态：
1. 已完成：治理规则已落入 A 股主出票程序 `xiaogu_forward_d1_1450_runner_v0_1.py` 的执行路径。
2. 已完成：新增/强化 active-chain governance hard gate，旧规则、旧链路、research-only、historical validation、backup/rollback/archive 来源不能产生当前 A 股 `PAPER_PICK`。
3. 已保留：非 A 股研究链路仍是 research/observation-only，不进入 A 股主出票规则或自动执行层。
4. 已验证：`scripts/xiaogu_governance_check.py` PASS；A 股 runner dry-run 输出 `PAPER_PICK 000700` 且 `ledger_line_added=false`；scoreboard dry-run 仍 `paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。
5. 已完成：2026-06-06 已用固定东财 CDP 9333 正式复跑 scan；输出目录 `data/live_scan/2026-06-06/eastmoney_web_tabs_scan_v0_1_cdp9333_consecutive_recheck_022552_rerun/`。
6. 当前结果：923 条行情、80 个候选、`passed_count=39`；full evidence pack PASS、enhanced evidence PASS、experimental evidence PASS、候选 evidence coverage PASS；`consecutive_limit_strength` 已可用，source status 为 PASS，`record_count=73`，evidence 样本来自 `eastmoney_limitup_pool_api_lbc`。
7. 已确认：东财正式行情/出票链路只用 `http://127.0.0.1:9333` + profile `/root/.claude/browser-profiles/xiaogu/cdp-debug`；scan 对非 9333 URL fail-closed，runner 也会拦截非 9333 Eastmoney bundle。
8. 已完成本轮继续跑：2026-06-06 22:31 固定 CDP 9333 scan 输出 `data/live_scan/2026-06-06/eastmoney_web_tabs_scan_v0_1/`，`universe_quote_count=5514`、74 个评分候选、`passed_count=31`，full/candidate evidence PASS；runner dry-run 为 `NO_PICK`、`ledger_line_added=false`，原因含 `SCAN_AFTER_RUNNER_ASOF_461.8M` 与 `CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`。
9. 已完成 forward result append-only 回填：T1 pending 清零；新增 T1 2 条、T2 11 条、T3 10 条可验证 `RESULT_FILL`，仍跳过未到足够交易日的 `2026-06-04 000700` T2/T3 与 `2026-06-03 002171` T3。
10. 已完成：本轮 diff 与验证已复核完成；如用户要求再决定是否提交，提交时只 stage 本轮相关文件。
11. 已补齐工具链：Karpathy guardrails 已在本轮执行前加载；Plan Enforcer active ledger 已建立在 `.plan-enforcer/ledger.md`；Understand-Anything baseline graph 已生成到 `.understand-anything/knowledge-graph.json`，仅用于 onboarding/地图/阶段分析。
12. 已完成：`XIAOGU_REPO_INTEGRATION_V3` governance 口径已收敛进 `FILE_MANIFEST.md`、`PIPELINE.md`、`RULES.md`；`scripts/xiaogu_governance_check.py` 与 CodeGraph health check 均 PASS。
13. 已完成：Qlib runtime/source 验证 PASS，`quant-python` 可 import `qlib`，共享源码为 `tools/external/repos/qlib` 的 `main@d5379c5`，remote 为 `https://github.com/microsoft/qlib.git`。
14. 已完成：用户提供 vn.py URL 后，已 clone `https://github.com/vnpy/vnpy.git` 到 `tools/external/repos/vnpy`，当前 `master@1b78494`；仅 source-only / research-only，不接 gateway/broker/account/order endpoint。
15. 已完成：Failure Attribution v0.1 已按“能不新增就不新增”落入现有 scoreboard diagnosis layer；七类标准归因 taxonomy 与 counts 已输出，保持 observation-only，不改 runner/ledger/生产评分/交易语义。
16. 已完成：PHASE D 按 attribution counts 继续补 VEI 映射；`LIMITUP_MISS=12` 保持最高，已细分 `limitup_feature_gap_reason_counts`，当前 canonical ledger 显示 `pre_limitup_anomaly_without_vei_confirmation=11`、`limitup_confirmation_feature_gap=1`；scan structured score 透出 `vei_phase_d_tags` 与弱转强/首板前/涨停前异动诊断详情，不提升生产评分、不新增 runner/ledger。
17. 已完成：REPO_INTEGRATION_V4 targeted pytest `50 passed`，top1/top2 replay 已写回既有 summary/ledger，V4 4 仓 full-real 校验已修正为 `strict_full_real=true`；当前 cleanup/提交前阻塞为 rollback proof 缺失，`scripts/xiaogu_governance_check.py` FAIL `rollback_backups`，需用户提供/批准重建或批准调整检查口径。
18. 已复核：2026-06-08 继续任务时重新确认 rollback proof 仍缺；全仓 `rg --files -uu` 与 git history 均未找到 `forward_paper_ledger_v0_1.jsonl.bak_20260525_ledger_split_repair`，治理检查仍唯一 FAIL `rollback_backups`；当前不能伪造该 proof，需用户提供/批准重建或批准调整检查口径后才能继续 cleanup/提交。
19. 已处理：用户批准重建 rollback proof 后，已从当前 `forward_paper_ledger_v0_1.jsonl` 复制生成 `forward_paper_ledger_v0_1.jsonl.bak_20260525_ledger_split_repair`；两者 sha256 均为 `b1385f238eedf6bda1e0dd0c45784da4d3d0cdc34f3526ff9a1052f58d04a37e`。复跑 `python3 scripts/xiaogu_governance_check.py` 已 PASS。该 proof 是 2026-06-08 按批准从当前 ledger 重建，不是找回的原始历史文件。
20. 已完成：scoped diff review 已按 xiaogu 范围完成；GitNexus detect_changes low risk、0 changed symbols、0 affected processes。复核中修正 `xiaogu_native_repo_runtime_v0_1.py` 旧 7000/板块权限信号为当前 6000/all-board 口径，并补对应测试。验证：targeted pytest 53 passed、governance PASS、CodeGraph health PASS、GitNexus detect_changes PASS。
21. 当前阻塞：cleanup dry-run 候选共 21 项，均为 P3 local noise（`.codegraph/`、`.gitnexus/`、`.pytest_cache/`、`.rtk`、`__pycache__`）；删除/归档仍需用户逐批批准。未跟踪 evidence/runtime 输出 6816 项，不适合直接提交或批量删除；后续必须按 scoped path stage 或 approval-gated cleanup。
22. 2026-06-08 已刷新 A 股链路进化验证证据：从 `/root/hermes/company-ai-system` 根目录复跑 targeted pytest `53 passed`、governance PASS、CodeGraph health PASS、GitNexus detect_changes low risk/0 changed symbols/0 affected processes；未改代码、未删除/归档、未 stage/commit/push、未交易/下单。当前仍停在 cleanup 审批或精确 stage/commit 决策点。
23. 2026-06-08 已按用户纠正确认正式东财链路使用 CloakBrowser：`pm2 describe xiaogu-cdp` 显示托管 `start_xiaogu_cdp_9333.sh`；宿主进程为 `/root/.cloakbrowser/chromium-146.0.7680.177.5/chrome --remote-debugging-port=9333 --user-data-dir=/root/.claude/browser-profiles/xiaogu/cdp-debug`。通过 `/tmp/xiaogu_cloak_cdp_probe.py` 在同一 CDP 打开东财 `bbsj` 与 A 股行情中心页面，均读取到表格/股票代码并保存截图到 `summary/eastmoney_bbsj_cloak_9333_20260608_probe.png`、`summary/eastmoney_hs_a_board_cloak_9333_20260608_probe.png`。
24. 2026-06-08 已用 CloakBrowser CDP 9333 实跑完整 A 股出票 dry-run 链路：scan 输出 `data/live_scan/2026-06-08/eastmoney_web_tabs_scan_v0_1_cloak_9333_ticket_flow_0405/`，`source_time=2026-06-08 04:05:04`、`universe_quote_count=5514`、`scored_count=74`、`passed_count=48`，full/enhanced/experimental evidence 均 PASS。runner dry-run 两个口径均为 `NO_PICK` 且 `ledger_line_added=false`：正式 `14:50:00` 口径被 `SCAN_TOO_OLD_644.9M_GT_15M` 与 `CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION` 拦截；对齐 `04:05:04` 口径去掉 freshness blocker 后仍被 `000070`/`300482` 追高无涨停确认拦截，顺延候选 `600031` 缺 `candidate_fund_recheck`，不能出票。未交易、未下单、未写 ledger、未 stage/commit/push。
25. 2026-06-08 已修复候选级资金复核覆盖：`candidate_evidence_topn` 默认改为跟随 `--max-candidates`，候选详情 API 现在覆盖本次全部 74 个候选；`candidate_fund_recheck` 从候选 quote API 的 `主力净流入` 派生，不再只依赖 top3 增强 CDP 页签。CloakBrowser CDP 9333 修复后 scan 输出 `data/live_scan/2026-06-08/eastmoney_web_tabs_scan_v0_1_cloak_9333_fund_recheck_fix_0420/`，`candidate_detail_topn=74`，`600031 三一重工` 的 `candidate_fund_recheck=1`。对齐 `04:22:00` 的 runner dry-run 输出 `PAPER_PICK 600031`、`ledger_line_added=false`，确认不再因缺该证据错过顺延候选；未交易、未下单、未写 ledger、未 stage/commit/push。
26. 2026-06-08 已修复电力等裸行业名漏识别：涨停/连板/炸板文本里的 `电力`、`煤炭开采`、`燃气`、`电网设备` 等不再必须带“板块/概念/行业”后缀才算 sector；`structured_limitup_reasons.related_sectors`、relationship graph 和 summary `sector_opportunity_snapshot` 现在能显示此类板块机会。用 06-08 既有 scan 原始 evidence 本地重算，已产出 `{"sector":"电力","evidence_count":4,"symbols":["001896"]}`。未改 runner gate，未把电力票硬塞进出票；下一步如要让板块轮动参与候选加权，必须先 replay/forward 验证。
27. 2026-06-08 四仓集合已在 active code 中落地：`REPO_ORDER=['tradingagent_a','VEI','Qlib','QuantDinger']`，`REPO_PATHS` 也是同四仓，`run_all_native_adapters()` 只调这四个；Kronos 不在 active order。按用户确认“要的”，已把板块轮动以 `sector_opportunity_score` 接入 existing VEI 四仓分支：scan structured score 产出 `sector_opportunity_score/tags`，runner 已有逻辑会合并 `structured_component_details`，VEI adapter 读取该字段并以 0.5 权重计入 VEI score delta，仍受原 VEI cap 和 runner hard gate 约束；未新增第五仓、未放宽出票 gate。
28. 2026-06-08 已用新的 CloakBrowser CDP 9333 scan 验证板块加权效果：输出 `data/live_scan/2026-06-08/eastmoney_web_tabs_scan_v0_1_cloak_9333_sector_weight_1351/`，`source_time=2026-06-08 13:51:52`、`universe_quote_count=5514`、`scored_count=77`、`passed_count=0`，full/enhanced/experimental evidence 均 PASS。`sector_opportunity_snapshot` 显示 `煤炭开采` 与 `电力`；实际候选中 4 票 `sector_opportunity_score>0`，其中电力为 `600505 西昌电力`、`600578 京能电力`，各得 `sector_score=0.6667`、VEI 理论加分 `+0.3333`。但本轮 `market_breadth_up_pct=12.82` 触发 `extreme_weak_market` 早退，正式 `score` 全为 null，runner 对齐 dry-run 输出 `RESEARCH_CANDIDATE 002361`、`ledger_line_added=false`；因此本轮只能证明电力/板块机会已进入 VEI delta，不能证明它已实际改变正式 PAPER_PICK 排序。
29. 2026-06-08 已补齐“可审计 runtime context”输出：`xiaogu_forward_d1_1450_runner_v0_1.py` 将 `structured_observation_basket` / `structured_formal_impact` 写入 `runtime_decision_context.json`；`xiaogu_eastmoney_web_tabs_scan_v0_1.py` 顶层 summary 也同步 `sector_opportunity_snapshot`。已用 fresh scan `data/live_scan/2026-06-08/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_ticket_160323/`（`source_time=2026-06-08 16:03:24`）和同源 runner `--asof-time 16:03:24 --dry-run` 复跑，runtime context 现已包含 `structured_observation_basket=true`、`structured_formal_impact=true`；结果仍为 `NO_PICK`、`ledger_line_added=false`，但可审计字段已落盘。`sector_opportunity_snapshot` 顶层与 `structured_outputs.sector_opportunity_snapshot` 一致，包含 `煤炭开采` 与 `电力`。
30. 2026-06-08 已复核 16:03:24 同源 runtime：`NO_PICK` 保持不变，但 `structured_observation_basket` 与 `structured_sector_observation_basket` 现在都直接看到 `600505 西昌电力`，其 `sector_opportunity_score=1.0`、`sector_opportunity_tags=["电力"]`、`vei_phase_d_tags=["SECTOR_OPPORTUNITY"]`，且没有 `[1]` 之类的数值标签残留。测试 `55 passed`，未交易、未写 live ledger、未 stage/commit/push。
31. 2026-06-08 已完成低位/水下启动 / 板块扩散型 PAPER_PICK 收口：新增 `paper_pick_eligibility`、`daily_ticket_search_result`、`early_opportunity_score` 和 layered search 路径，真实 2026-06-08 16:03:24 样本仍 `NO_PICK`（`CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION`、`QUALIFIED_CANDIDATE_FALSE`），pytest `61 passed`；硬风控未放宽，纸面/只读链路保持 `paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。
32. 2026-06-08 已完成低位涨停预期补齐：新闻/题材/板块/盘中异动已接入 candidate generation；`information_coverage_audit` 现在能分辨 candidate generation 与后置 evidence；`structured_signal_profile` 已能从 component_details 归一化 `SECTOR_OPPORTUNITY`。验证：`64 passed, 8 subtests passed`。
33. 2026-06-08 23:09 fresh scan / runner 再验证完成，`information_coverage_audit` 在新 scan summary 中非空，runner 顶层 `features.information_coverage_audit` 已透传；`daily_ticket_search_result` 真实出现 `news_catalyst_low_position=3` 与 `intraday_alert_reversal=1`，`sector_catalyst_low_position=0`，`paper_scoring_candidates` 里也真实包含新闻/异动层候选；本轮 `NO_PICK` 仍由 `002735` 监管 hard block + `QUALIFIED_CANDIDATE_FALSE` 决定，不是 silent null。验证 `py_compile`/`git diff --check`/targeted pytest `65 passed, 8 subtests passed`。
34. 2026-06-09 已补 `classify_news_catalyst_quality()` 的可审计输出：风险公告现在显式返回 `risk_evidence`、`regulatory_hard_block`、`observation`，并且不会再作为正向 `NEWS_CATALYST_LOW_POSITION` / `TOPIC_FUND_IGNITION` 使用；sector tag 也改成 canonical term 规范化，`煤炭` / `电力` / `光伏` 这类裸行业名和复合短语都能回收到同板块映射。定向 pytest `68 passed`。
35. 2026-06-09 用本地 replay fixture 重新证明 scan→runner 链路：正向新闻样本生成 `NEWS_CATALYST_LOW_POSITION`，电力板块样本生成 `SECTOR_NEWS_LOW_POSITION`，风险公告 `王子新材 股票交易异常波动公告 风险提示` 被判为 `regulatory_notice` 且 `usable_for_candidate_generation=false`、`usable_for_paper_pick=false`。runner 侧 `paper_scoring_candidates` 真实出现 `news_catalyst_low_position`、`sector_catalyst_low_position`、`underwater_reversal`；整体仍 `NO_PICK`，原因是 replay bundle 只用于链路证明，未满足全量 candidate evidence hard gate。
36. 2026-06-09 01:06 已把 news / sector replay fixture 分离：news fixture 继续证明 `news_catalyst_low_position`，sector fixture 证明 `sector_catalyst_low_position` 进入 `paper_scoring_candidates`；fresh scan 仍 sector zero，但原因已明确为 `sector_pool_count=0` / `sector_news_not_mapped_to_low_position_symbols`。验证 `68 passed`。
37. 2026-06-09 已完成 `xiaogu_research_layer_mvp`：scanner / runner 贯通 `research_signals` contract，包含 `industry_chain_tags`、`catalyst_quality.confidence` / `evidence_refs`、`sector_mapping`、`a_share_risk_review`、`adversarial_review`、`historical_pattern`、`research_panel`；风险公告 / 正向催化 / 电力 sector 规则和 runtime 透传已验证，`pytest 68 passed, 8 subtests passed`，`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false` 保持不变。
38. 2026-06-14 19:03:54 已用真实浏览器/CDP 9333 重跑东财 scan + runner dry-run：scan 输出 `data/live_scan/2026-06-14/eastmoney_web_tabs_scan_v0_1_cloak_9333_realtime_190354/`，source_time/asof `2026-06-14 19:03:54`，universe=5513、tradable=5080、passed_count=10，required/full/enhanced/candidate evidence PASS；runner paper-only dry-run 输出 `PAPER_PICK 600060 海信视像`，final_score `27.07504`，one_lot_cost `2773.0`，available_cash `7000.0`，`ledger_line_added=false`、`paper_only=true`、`no_trade=true`、`allow_trade=false`、`auto_order=false`。因 2026-06-14 为周日/非交易时段，本轮只作为真实浏览器链路验证和 paper-only 输出，不作为盘中执行信号。
39. 2026-06-14 用户确认保存：周五 official ticket 为 `601801 皖新传媒`，用户当前持仓为 `601801 皖新传媒`；`600060 海信视像` 仅作为当前链路最佳标的 / 周一观察对照。下一步等待周一开盘后刷新账户只读状态并重跑 CDP 9333 实时 scan + runner dry-run；继续 NO_AUTO_TRADE / NO_ORDER_EXECUTION，不自动交易。

硬约束：
- A 股进入 LIVE_ACCOUNT_TRACKING / MANUAL_TRADE_ONLY / auto_order=false；系统记录持仓和资金，但不自动下单。
- A 股稳定总链路允许所有 A 股板块进入候选；一手成本和仓位约束必须结合用户提供的真实可用资金重新判断，并继续通过既有监管/风险/数据 gate。
- 治理规则必须作为每次出票规则迭代时的操作约束使用，不是只写进文档或外部检查脚本。
- xiaogu 固定分开发链路和实时运行链路：治理工具用于“改系统”，不用于“跑系统”。开发链路按 PM/task → Plan → CodeGraph → GitNexus → 修改 → 验证 → Plan Enforcer → AgentMemory/LOG 执行；实时行情扫描/结构化提取/评分/排序/出票热路径只读取当前配置、当前数据、当前规则、当前模型并输出轻量证据，禁止 CodeGraph/GitNexus/UA/AgentMemory/PM/task/Plan 参与实时决策。
- 已有本地 commit：`f0a60790`、`37926b7c`；未 push。下一轮先看实时 git 状态。
