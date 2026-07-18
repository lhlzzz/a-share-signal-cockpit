# RESEARCH

## 2026-06-17 系统升级方向分析

基于历史 ledger 归因（19笔 PAPER_PICK、14笔已填 T+1、胜率 85.7%）和系统架构审查，按优先级排序升级方向：

### P0 - 直接影响涨停率/获利率

1. **封单强度分析**（limitup_seal_strength）：涨停板封单金额/流通市值比是次日高开核心信号。需从东财涨停池 API 补强 `封单额/流通市值` 指标。
2. **炸板回封正向信号**（broken_limit_recovery）：炸板后回封+封单加大 = 资金认可度高。当前只有 broken_limit_risk 惩罚项，缺正向信号。
3. **分时量价确认**（intraday_volume_price_confirm）：最后30分钟量价配合替代单一 close_position_score。尾盘放量+高收盘=强延续；尾盘缩量+低收盘=弱势。

### P1 - 减少亏损/冲高回落

4. **连板高度限制**（consecutive_board_cap）：3板以上票风险特征完全不同，需区分首板/2板和3板+。
5. **竞价强度预判**（auction_intensity_proxy）：集合竞价量价是当天走势领先指标。需调整出票窗口到 9:25-9:35。
6. **换手率异常检测**（turnover_anomaly_detection）：加入相对历史N日换手率偏离度。

### P2 - 提高每天出票率

7. **弱市出票降级**（weak_market_graceful_degradation）：弱市降低仓位/提高门槛，而非完全不出票。
8. **多时间窗口出票**（multi_window_ticket）：增加 9:35/11:25/14:45 三个窗口。

### 历史归因摘要

高获利票共同点（002273/603601/601615/600031）：
- signal_pct 6-8%，排名靠前（rank<=7）
- 成交额 high（amount_pctile >= 0.73）
- 强市或高潮市（breadth >= 61%，limitups >= 19）
- 有收盘确认（close_position_score >= 0.78）

亏损票特征（000002 万科A）：
- close_position_score = 0.71（偏低）
- volume_ratio = 3.8（异常高，冲高回落信号）
- market_bigups = 260（过热拥挤）
- blowoff_risk = 0.84（已被新 Qlib 特征正确标记）

### last30days 技能状态

- 仓库：https://github.com/mvanhorn/last30days-skill
- 本地安装：`/root/.claude/skills/last30days/`（scripts 已部署）
- 阻塞：需要 Python 3.12+，当前系统只有 Python 3.10
- 解决方案：安装 Python 3.12 或使用 webfetch 替代
