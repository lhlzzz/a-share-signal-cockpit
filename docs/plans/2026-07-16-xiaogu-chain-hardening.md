# xiaogu 实时出票链路硬化任务包

**Date:** 2026-07-16

## Discuss Packet Preservation

**Normalized Goal:** 把 runner 的最终输出语义改成：当日如果原本会走 `NO_PICK`，则对外结果直接输出当天最高分标的，同时保留原始 NO_PICK 的诊断信息和完整运行数据；同时修复 scanner 在候选证据收集阶段的超时问题，保证最终产物完整落盘且不缩减候选/证据域。剩下还需要修复的兼容性、测试和落库细节整理成可执行任务包，交给 mimocode compose 继续完成。

**Non-Negotiable NN1:** 最终对外输出不能再停留在空的 `NO_PICK`，必须提升为当天最高分标的。

**Proof Requirement PR1:** 有真实运行结果证明原本 NO_PICK 的场景被提升成最高分标的。

## Current Goal

修复本轮全链路审计发现的问题：彻底消除 official 出票链路里的旧 `urllib`/直连东财路径，统一 `daily_pipeline` 与 `scheduler` 行为，修复 API 展示 superseded 旧票风险，并把 DB/raw-domain 消费契约压实到可验证状态，同时保持 `NO_PICK -> 当日最高分标的` 的对外输出语义。

## Primary Files

`scrapy_scanner/runner_v2.py`, `xiaogu_forward_d1_1450_runner_v0_1.py`, `xiaogu_db.py`, `xiaogu_api.py`, `xiaogu_scheduler.py`, `daily_pipeline.sh`, `scripts/xiaogu_daily_health_check.py`, existing tests under `tests/`.

## Constraints

- modify-before-create；不新增平行 scanner/runner；不新增 `*_v2.py` / `*_fixed.py`。
- 不改真实交易边界；不为过测试缩减候选池或证据域。
- 不删除 superseded rows；只修默认 active 查询和 API 展示。
- 不回退 `NO_PICK -> highest score candidate` 的对外输出语义，不丢弃原始 NO_PICK 诊断链路。

## Out of Scope

新因子研究、收益率策略调参、真实交易/券商接入、删除历史文件的大规模清理。filler/backfill 的 T+1 收益网络访问不属于“出票前 official 决策路径”，本任务只加边界说明和健康检查分类，不强行改收益回填数据源。

## Must-Haves

- MH1: Official scanner/runner 出票路径不再通过 Python `urllib`/direct Eastmoney 网络补抓数据；所有出票前东财数据只能来自 `runner_v2` CDP/API scan summary 或 DB 中由该 scan 持久化的 raw-domain payload。
- MH2: `daily_pipeline.sh` 与 `xiaogu_scheduler.py` 对同日已有票、日期调整、DB 启动、scanner 目录选择的行为一致；scheduler 自动运行不会因为已有旧票而跳过最新健康链路 correction。
- MH3: API 和 DB helper 默认只展示 active pick，不会把 superseded 历史 correction row 当作当前正式票；仍保留显式审计入口查看 superseded rows。
- MH4: Runner 对 scanner 数据的消费契约明确且可测试：优先使用同日最新 CDP/API scanner summary，其次使用 DB 中对应 API scan session/raw-domain payload；不得优先选中没有 raw payload 的 `manual_pipeline_snapshot` 作为 raw-domain 事实源。
- MH5: 数据完整性报告区分“硬门禁 PASS”和“非硬阻断 PROXY/MISSING”，不再输出或暗示所有域 100% 全齐；缺口必须进入 summary/health check/DB evidence。
- MH6: 所有修复有自动化验证：py_compile、health check、scheduler/API/runner targeted tests、scanner summary/DB active pick 一致性检查。
- MH7: 最终对外输出不能再停留在空的 `NO_PICK`，必须提升为当天最高分标的。原始 NO_PICK 诊断、`daily_best_paper_watch` 和完整运行数据必须保留。

## Completion Receipt Requirements

- Update the existing plan/ledger receipt rather than creating a second plan file after execution。
- Record final changed files, validation commands, WARN paths, and rollback notes for scheduler/API/runner/DB/NO_PICK changes。
- CDP/Chrome 可用时 run `NO_AUTO_TRADE=1 NO_ORDER_EXECUTION=1 bash daily_pipeline.sh <trade-date>`；不可用时 receipt 记录跳过原因。
- Receipt must cover MH1-MH7, with each Must-Have tied to at least one verified task row。

## Acceptance Checklist

- [ ] MH1 covered by Tasks 1-9, 24 plus Completion Receipt Requirements。
- [ ] MH2 covered by Tasks 10-12, 24-26 plus Completion Receipt Requirements。
- [ ] MH3 covered by Tasks 13-15, 24-26。
- [ ] MH4 covered by Tasks 16-18, 24-26。
- [ ] MH5 covered by Tasks 19-21, 24-26。
- [ ] MH6 covered by Tasks 24-26 plus Completion Receipt Requirements。
- [ ] MH7 covered by Tasks 22-23 plus Completion Receipt Requirements。

## Suggested Execution Order

1. Tasks 1-2 health-check boundary。
2. Tasks 3-6 runner direct-network removal。
3. Tasks 7-9 legacy scanner guards。
4. Tasks 10-12 scheduler/daily parity。
5. Tasks 13-15 API/DB active pick fix。
6. Tasks 16-19 DB/raw-domain fallback contract。
7. Tasks 20-21 completeness wording/evidence。
8. Tasks 22-23 NO_PICK highest-score regression proof。
9. Tasks 24-30 validation。
10. Complete receipt and rollback notes。

## Decision Log

| type | scope | decision | why |
| --- | --- | --- | --- |
| delete | docs/plans/2026-07-16-xiaogu-chain-hardening.md | Remove duplicated bottom metadata sections after moving receipt/checklist/order above executable tasks. | The duplicate sections were being counted as part of the final task by Plan Enforcer review, creating false oversized-task findings. |
| delete | scrapy_scanner/run.py, scrapy_scanner/run_all.py, scrapy_scanner/eastmoney_full_scan.py, scrapy_scanner/eastmoney_market_center.py, scrapy_scanner/test_all_domains.py | Delete legacy direct-scanner entrypoints after user clarified deletion is acceptable when official ticket chain, DB, and backtest remain normal. | Validation shows official scanner remains `scrapy_scanner/runner_v2.py`, northbound/HSGT remains in runner_v2, DB is reachable, and DB-backed backtest smoke runs. Keeping wrappers would preserve parallel entrypoints without operational value. |

## Execution Receipt 2026-07-16

- Legacy scanner deletion inventory: removed `scrapy_scanner/run.py`, `scrapy_scanner/run_all.py`, `scrapy_scanner/eastmoney_full_scan.py`, `scrapy_scanner/eastmoney_market_center.py`, `scrapy_scanner/test_all_domains.py`; no source references remain except this plan and historical data output. `scrapy_scanner/README.md` now points only to `scrapy_scanner/runner_v2.py`.
- Official domain preservation: `rtk grep -n "hsgt_summary\|hsgt_deals\|hsgt_holdings\|北向" scrapy_scanner/runner_v2.py xiaogu_forward_d1_1450_runner_v0_1.py` confirms 北向/HSGT ownership remains in `scrapy_scanner/runner_v2.py`.
- Static validation: `python3 -m py_compile scrapy_scanner/runner_v2.py scripts/xiaogu_daily_health_check.py xiaogu_db.py xiaogu_forward_d1_1450_runner_v0_1.py xiaogu_scheduler.py xiaogu_api.py tests/test_xiaogu_a_share_forward_runner.py tests/test_xiaogu_api.py` exited 0.
- Health validation: `python3 scripts/xiaogu_daily_health_check.py --json` passed 17/17, including `official_pre_pick_direct_network_boundary=PASS` and `northbound_in_scoring=PASS`; post-pick filler/backfill/social paths remain WARN/INFO only.
- Targeted tests: `pytest tests/test_xiaogu_a_share_forward_runner.py tests/test_xiaogu_api.py tests/test_xiaogu_scheduler.py tests/test_db_backfill.py -q` passed 280 tests.
- DB/backtest smoke: `python3 scripts/xiaogu_ensure_database.py` reported `database ready: localhost:5432`; `python3 xiaogu_backtest_v0_1.py --date 2026-07-10 --source db --report` completed with one DB-backed PAPER_PICK and saved a report under `data/backtest/`.
- DB row-count note: historical `2026-07-10` has 33 `daily_candidates` and no latest API raw scan session, so the 200-candidate/latest-raw-session invariant was not asserted on that old date; this does not block legacy scanner deletion because DB readiness and DB-backed backtest path are functional.
- Rollback notes: restore deleted legacy files from git only if a future validated official dependency appears; otherwise rollback is not recommended because `runner_v2.py` is the single scanner owner.

## Tasks

### Task 1: 建立 official direct-network health check

- [ ] 在 `scripts/xiaogu_daily_health_check.py` 中新增/调整 official pre-pick path 检查：`scrapy_scanner/runner_v2.py`, `xiaogu_forward_d1_1450_runner_v0_1.py`, `daily_pipeline.sh`, `xiaogu_scheduler.py`。
- [ ] official path 对 `urllib.request.urlopen`, `DIRECT_OPENER.open`, `requests.get/post` 的东财 API 使用 fail；允许 `runner_v2.py` local CDP `/json/*` opener。
- [ ] Verification: `python3 scripts/xiaogu_daily_health_check.py --json` 显示 official direct-network 为 PASS。

### Task 2: 分类 post-pick 与 sidecar 网络检查

- [ ] 在 health check 中把 `xiaogu_forward_result_filler_v0_1.py`, `scripts/xiaogu_return_backfill.py` 归入 post-pick validation path。
- [ ] 把 `xiaogu_social_sentiment.py` 归入 optional sidecar path，post-pick/sidecar 只 WARN/INFO，不阻断 official 出票健康分。
- [ ] Verification: health check JSON 显示 post-pick direct-network 为 WARN/INFO。

### Task 3: 移除 runner 新闻 direct fallback

- [ ] 修改 `_load_news_kuaixun()`，删除/禁用 `urllib.request.urlopen` 在线 fallback。
- [ ] 只允许读取同日 scanner `news_kuaixun.jsonl`、summary `files.news_kuaixun`、DB `scan_market_data.domain='news_kuaixun'`。
- [ ] Verification: runner 单测 monkeypatch `urllib.request.urlopen` 抛错，scanner file/DB payload 存在时仍返回数据。

### Task 4: 记录新闻缺口 evidence

- [ ] `_load_news_kuaixun()` 三个本地/DB 来源都缺失时返回 `[]`。
- [ ] 记录 `NEWS_KUAIXUN_SOURCE_MISSING` 到 candidate evidence 或 coverage audit。
- [ ] Verification: 单测覆盖缺失时返回 `[]` 且不调用 urlopen。

### Task 5: 禁用 candidate fund flow live direct fetch

- [ ] 修改 `fetch_candidate_fund_flow_live()`：official runner 默认不发起 live direct Eastmoney 请求。
- [ ] 保留函数名时改成纯本地/DB 读取，或只在显式 diagnostic/test flag 下允许 live fetch；official path 默认关闭。
- [ ] Verification: 单测 monkeypatch `urllib.request.urlopen` 抛错，official path 不触发网络。

### Task 6: 用 scanner/DB 资金流替代 live supplement

- [ ] 修改 `inject_live_fund_flow_into_candidates()`：优先使用 `data_directory_capital_flow_by_code` / `stock_capital_flow.jsonl` / DB `scan_market_data.domain='stock_capital_flow'`。
- [ ] scanner 资金流存在时不得被 live supplement 覆盖；缺失时标记 `candidate_fund_recheck_missing` 或现有缺口字段。
- [ ] Verification: `test_live_fund_flow_does_not_overwrite_data_directory_capital_flow` 继续通过，并新增缺失时记录缺口、不调用 urlopen 的测试。

### Task 7: Inventory legacy scanner scripts

- [ ] 列明 `scrapy_scanner/run.py`, `scrapy_scanner/run_all.py`, `scrapy_scanner/eastmoney_full_scan.py`, `scrapy_scanner/eastmoney_market_center.py`, `scrapy_scanner/test_all_domains.py` 当前用途。
- [ ] 执行记录写入现有 receipt/ledger，不创建新文档。
- [ ] Verification: inventory 明确哪些文件会被 guard，哪些只作为历史入口保留。

### Task 8: Guard legacy scanner scripts

- [ ] 不删除文件；把 legacy scripts 替换为 thin wrapper 或 explicit deprecation guard。
- [ ] 默认打印“official scanner is scrapy_scanner/runner_v2.py CDP-only”，参数透传给 `runner_v2.py` 或直接退出非 0 并提示迁移入口。
- [ ] Verification: legacy scripts 文件内不再包含 `urlopen` / `DIRECT_OPENER`。

### Task 9: 更新 scanner 入口文档

- [ ] 更新 `README.md` / 已有入口文档中 scanner 入口为唯一 `scrapy_scanner/runner_v2.py`。
- [ ] 不创建新 scanner 文档。
- [ ] Verification: `rtk grep -n "urlopen\|DIRECT_OPENER" scrapy_scanner` 只允许 `runner_v2.py` 的 local CDP opener/import 或 Scrapy spider 的 URL encode。

### Task 10: 对齐 scheduler runner 参数

- [ ] 修改 `xiaogu_scheduler.py::job_afternoon_scan_and_pick()`，runner 参数对齐 `daily_pipeline.sh`：`--date today --asof-time <source_time_or_now> --no-runtime-date-adjust --force`。
- [ ] `daily_pipeline.sh` 保持 `python3 scripts/xiaogu_ensure_database.py` 在入口最前。
- [ ] Verification: `tests/test_xiaogu_scheduler.py` 覆盖 `--force`、`--no-runtime-date-adjust`。

### Task 11: scheduler 使用 summary source_time

- [ ] scheduler 在 scanner 成功后读取 `eastmoney_web_tabs_summary_runner.json.source_time`，用其 HH:MM:SS 作为 runner `--asof-time`。
- [ ] 读取失败回退当前时间并记录 WARN。
- [ ] Verification: `tests/test_xiaogu_scheduler.py` 覆盖 summary source_time asof。

### Task 12: 保留 scheduler DB 启动门禁

- [ ] 保持 `ensure_database_ready()` 在 scheduler 启动前执行。
- [ ] DB startup fail 时 scheduler abort，不继续 scanner/runner。
- [ ] Verification: `tests/test_xiaogu_scheduler.py` 覆盖 DB startup fail abort。

### Task 13: API /picks 默认隐藏 superseded

- [ ] 修改 `xiaogu_api.py`：`/picks` 默认排除 `features->>'superseded' = 'true'`。
- [ ] 增加 query 参数 `include_superseded=false`，显式 true 才展示审计旧行。
- [ ] Verification: API 单测证明默认看不到 superseded，`include_superseded=true` 可看到旧行。

### Task 14: API summary 使用 active paper pick

- [ ] 修改 `/picks/{trade_date}/summary`：`paper_pick` 只从 active rows 中选取。
- [ ] 同日多 correction 的当前票排序使用 `updated_at DESC` 或 active marker，不用 `final_score DESC` 决定当前票。
- [ ] Verification: API 单测构造两个 superseded PAPER_PICK + 一个 active PAPER_PICK，summary 返回 active symbol。

### Task 15: DB helper active/superseded 语义

- [ ] 修改/补充 DB helper：保留 `fetch_picks(include_superseded=False)` 默认 active 语义。
- [ ] 保留显式审计入口查看 superseded rows。
- [ ] Verification: DB helper 单测覆盖默认 active 与 include_superseded=true。

### Task 16: DB helper 选择 latest API scan session

- [ ] 修改 `xiaogu_db.py` 或 runner helper，提供 latest API scan session 查询：优先 `cdp_url='eastmoney_api_direct'` 且有 `scan_market_data` rows。
- [ ] 不把 `manual_pipeline_snapshot` 空 raw payload 当成 raw-domain source。
- [ ] Verification: DB fixture 同时存在 manual snapshot 和 api scan snapshot 时选择 api scan snapshot。

### Task 17: runner DB fallback 映射 raw-domain payload

- [ ] 修改 `build_research_basket_from_db()`：DB fallback 使用同一 `scan_session_id` 的 `scan_market_data` payload。
- [ ] 映射 `stock_capital_flow`, `news_kuaixun`, `announcements`, `lhb`, `limitup_pool`, `limitup_yesterday`, `sector_capital_flow`, `hsgt_*`。
- [ ] Verification: `scan_market_data.stock_capital_flow` 能进入 candidate capital flow。

### Task 18: runner DB fallback 缺口处理

- [ ] DB 只有 manual snapshot 且没有 raw payload 时，runner 回退同日 scanner summary file。
- [ ] DB 与 scanner summary 都没有时明确 `NO_SAME_DAY_VERIFIED_CANDIDATE_BUNDLE`。
- [ ] Verification: 缺失 domain 记录为 PROXY/MISSING，而非静默通过。

### Task 19: information_coverage_audit 记录 domain 状态

- [ ] 把“已采集、已持久化、已用于 gating/scoring/proxy/unused”的状态写入 `information_coverage_audit`。
- [ ] 缺失 domain 记录为 `PROXY` / `MISSING`，不得输出成 100% 全齐。
- [ ] Verification: runner targeted test 覆盖缺失 domain 进入 `information_coverage_audit`。

### Task 20: 修正数据完整性 summary/health 口径

- [ ] scanner summary/health check 区分 `hard_gate_status` / `source_completeness.status`、`optional_or_proxy_gaps`、`official_pick_allowed`。
- [ ] 对 `sector_news=PROXY` 样本显示“硬门禁 PASS + optional/proxy gap present”。
- [ ] Verification: health check/summary 不再宣称所有源全齐。

### Task 21: 持久化 source completeness 证据

- [ ] DB persistence 保存 source_counts/source_status 时，latest API scan session 的 `source_counts` 必须非空。
- [ ] manual pipeline snapshot 不覆盖 API scan session 的 raw source_counts 语义。
- [ ] Verification: API 或 summary 返回当前票时附带 `source_summary_path`, `scan_source_time`, `source_completeness_status`, `optional_or_proxy_gaps`。

### Task 22: 固化 NO_PICK 提升语义

- [ ] 确认 runner 原本 `NO_PICK` 场景下，对外结果输出当天最高分标的。
- [ ] 保留原始 `NO_PICK` 理由、`daily_best_paper_watch`、`highest_score_candidate`、`no_pick_candidate_diagnostics` 和 runtime snapshot。
- [ ] Verification: targeted test 覆盖新的输出语义和保留的诊断信息。

### Task 23: 记录 NO_PICK 真实运行证明

- [ ] 执行一次真实或 fixture 运行，使用原本会 `NO_PICK` 的样本。
- [ ] 证明对外输出变成当天最高分标的，且原始 NO_PICK 诊断仍可回放。
- [ ] Verification: receipt 中记录“有真实运行结果证明原本 NO_PICK 的场景被提升成最高分标的。”

### Task 24: 静态验证 official path

- [ ] 运行 `python3 -m py_compile scrapy_scanner/runner_v2.py scripts/xiaogu_daily_health_check.py xiaogu_db.py xiaogu_forward_d1_1450_runner_v0_1.py xiaogu_scheduler.py xiaogu_api.py`。
- [ ] 运行 `python3 scripts/xiaogu_daily_health_check.py --json`。
- [ ] 运行 `rtk grep -n "urllib.request.urlopen\|DIRECT_OPENER" scrapy_scanner xiaogu_forward_d1_1450_runner_v0_1.py daily_pipeline.sh xiaogu_scheduler.py xiaogu_api.py scripts/xiaogu_daily_health_check.py`。
- [ ] Verification: py_compile exit 0；health check JSON official direct-network PASS；grep 只出现允许的 CDP/local 或非 official 引用。

### Task 25: targeted tests

- [ ] 运行 `pytest tests/test_xiaogu_scheduler.py -q`。
- [ ] 运行 `pytest tests/test_db_backfill.py -q`。
- [ ] 运行 Tasks 3-6, 13-19, 22 添加或更新的 targeted runner/API/DB tests。
- [ ] Verification: targeted pytest commands exit 0；失败项必须在 receipt 中标为阻塞并修复后再完成。

### Task 26: DB source_time 对齐验证

- [ ] DB 可用时验证 latest scanner source_time equals active official pick source summary time/path。
- [ ] Verification: DB 可用时记录 source_time/path 查询结果；DB 不可用时 receipt 记录不可用原因。

### Task 27: DB daily_candidates 数量验证

- [ ] DB 可用时验证 `daily_candidates` has 200 rows。
- [ ] Verification: DB 可用时记录 daily_candidates row-count；DB 不可用时 receipt 记录不可用原因。

### Task 28: DB active official pick 验证

- [ ] DB 可用时验证 active official candidate exactly one row。
- [ ] Verification: DB 可用时记录 active official candidate 查询结果；DB 不可用时 receipt 记录不可用原因。

### Task 29: DB superseded 默认隐藏验证

- [ ] DB 可用时验证 superseded rows 默认隐藏。
- [ ] Verification: DB 可用时记录 default 查询结果；DB 不可用时 receipt 记录不可用原因。

### Task 30: DB superseded 审计可见验证

- [ ] DB 可用时验证 superseded rows 显式审计可见。
- [ ] Verification: DB 可用时记录 audit 查询结果；DB 不可用时 receipt 记录不可用原因。

