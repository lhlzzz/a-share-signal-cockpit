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
1. **UNDERSTAND** — codebase-memory **主索引**（符号/调用链/架构 cluster）；冲突以 source 为准。Understand-Anything 已从 xiaogu 移除
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
- 涨幅范围: 0.5%-9.5%
- 正式 PAPER_PICK 股价不得超过 70.00 元；真实账户可买性仍按实际账户快照判断

### 工具使用
- RTK 替代 bash 命令
- AgentMemory 存储决策（任务收口必更新）
- Codebase-Memory **主索引**（定位/调用链/架构；Understand-Anything 已停用）
- Plan Enforcer discuss：实现有歧义时先 discuss 再 draft
- Obsidian：A股证据 → Project/A股；跨域 → 神临
