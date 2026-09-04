# xiaogu 项目约束

## Karpathy Skills (每次编码必须遵循)

### 1. 思考再编码
- 明确陈述假设
- 不确定就问
- 有更简单的方案就提出
- 不清楚就停止并说明

### 2. 简单优先
- 不写超出需求的功能
- 不为单次使用代码创建抽象
- 不添加未请求的"灵活性"
- 200行能50行完成的，重写

### 3. 精准修改
- 只改必须改的
- 不"改进"相邻代码
- 匹配现有风格
- 每行改动追溯到用户请求

### 4. 目标驱动
- 定义成功标准
- 循环直到验证通过
- 多步骤任务列出验证点

## 检查清单 (提交前)
- [ ] 是否有更简单的方案？
- [ ] 是否过度设计？
- [ ] 每行改动是否必要？
- [ ] 成功标准是否明确？


## 任务启动强制顺序（每次任务）

> **全局 owner（Grok CLI 全项目）**: `~/.grok/AGENTS.md` + skills `/task-startup` `/karpathy-guidelines`。本文件仅补充 xiaogu 域细节。

0. **Karpathy** — 本文件 + `.skills/karpathy-daily.md`；写假设与成功标准
1. **UNDERSTAND** — codebase-memory **主索引**（符号/调用链/架构 cluster）；Understand-Anything 图谱用于架构与历史关系交叉校验；冲突以 source 为准
2. **PLAN** — 有歧义：`plan-enforcer-discuss` → draft → review；无歧义机械改可跳过 discuss
3. **IMPLEMENT** — 精准修改，不扩 scope
4. **VALIDATE** — 相关测试通过
5. **COMPLETE** — `agentmemory__memory_save` 必做；知识有变则写 Obsidian（`Project/A股` + 神临想法池/总索引/项目接口）

未走 0→1 不得开写；主链绿但 AgentMemory/应有笔记未更新 = 未完成。详见 `AGENTS.md` Development Cycle。

## 项目特定约束

### 数据源
- 行情中心: 沪深A股、行业/概念/地域板块、涨停板
- 资金流: 个股/板块/大盘资金流
- 数据中心: 龙虎榜、北向资金、业绩预告、机构调研、限售解禁、股东变动、IPO日历
- 指数: 上证/深证/创业板/科创50/北证50等

### 候选池规则
- 只做A股主板（上海 600/601/603/605，深圳 000/001/002/003）
- 候选池400只
- 涨幅范围 0.5%-9.5% 是 L2 路由 / research ablation（WITH_GATE vs WITHOUT_GATE），不是已冻结的 Alpha 规则
- Paper 仅记录 `OBSERVED + PAPER_FLAT`；没有 `PAPER_PICK`、Paper Entry 或模拟持仓
- 唯一 Production Target = `opportunity_5d`；唯一 Production Alpha = `profit_window_alpha_5d_v4`（测量仍用 `price_strength`）；Capital 仅限 `RESEARCH_ONLY`
- Production BUY 保持 `BLOCKED`，Live Trading 保持 `DISABLED`
- `original_snapshot_id` = provenance；`review_snapshot_id` = current truth
- Production Clock = `xiaogu_forward_snapshot.production_now()`，不得用 `source_time` 冒充
- Research positive != Production blocker；Confirmed negative evidence = Production blocker
- Gate owner = `evaluate_production_gates()`
- Position identity = `position_id` (not symbol). Same symbol / different decision remain isolated.
- Paper REDUCE unsupported without quantity model. Paper review = `PAPER_HOLD` / `PAPER_SELL`. REDUCE does not equal FLAT.
- Distribution requires distribution-specific evidence and `mechanism ∈ DISTRIBUTION_MECHANISMS`; SELL/EXITING is not distribution.
- Evidence identity is strictly `(source_id, event_id, mechanism)`; missing any field is not confirmed.
- Negative evidence is PIT; missing identity is not confirmed; future evidence is excluded.
- Health is behavior-first, not `inspect.getsource()`.
- Production Gate has one owner: `evaluate_production_gates()`.

### 工具使用
- RTK 替代 bash 命令
- AgentMemory 存储决策（任务收口必更新）
- Codebase-Memory **主索引**（定位/调用链/架构）+ Understand-Anything（架构交叉校验与当前图谱维护）
- Plan Enforcer discuss：实现有歧义时先 discuss 再 draft
- Obsidian：仅由 Memory Adapter 写入；A股证据 → Project/A股；跨域 → 神临

### Schema / Snapshot Truth
- Bootstrap SQL (`scripts/xiaogu_db_init.sql`) = fresh DB only.
- Python (`xiaogu_db.ensure_production_schema`) = runtime migration owner.
- Historical snapshot repair = explicit migration only; never production init.
- Calendar = unique owner (`xiaogu_db.py`).
- Canonical Snapshot = exact identity; ambiguity fail-closed.
- Snapshot rows are immutable. Same `snapshot_id` + same hash is idempotent; same identity + different hash is `SNAPSHOT_IDENTITY_CONFLICT`. Concurrent writes are resolved atomically.

### Trading Calendar Truth
- 唯一 Calendar Owner 是 `xiaogu_db.py`。
- `trading_calendar` 使用按 `effective_year` 选择的版本化权威数据
  `ashare_YYYY.json`，并保存 `calendar_content_hash`；`ASHARE` 统一覆盖 SSE/SZSE。
- `is_trading_date()` 只返回 `TRUE`、`FALSE`、`UNKNOWN`；缺失必须 `CALENDAR_DATA_UNAVAILABLE` 并 fail closed。
- 不得使用 future prices、scanner 是否有数据、snapshot、paper outcome 或 weekday 推断交易日。
- Scheduler、Runner、Outcome、Horizon、Position Review 只能调用 Calendar Owner。
- Future prices != Calendar；weekday != Calendar。Calendar 数据集缺失、版本冲突、
  hash 冲突或年度覆盖不完整时必须 BLOCK。
