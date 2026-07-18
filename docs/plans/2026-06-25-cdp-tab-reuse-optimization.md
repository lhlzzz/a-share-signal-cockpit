# CDP 单页复用优化方案

## 问题

当前扫描器为每个 source 开一个独立 CDP 标签页，导致 45+ 标签页，系统卡顿。

## 已完成

### Phase 1: URL 去重 ✅

修改 `open_cdp_tabs()` 逻辑：
- 添加 `shared_url_to_tab` 字典，跨 required/enhanced/data_directory 调用共享
- 同一 URL 只开一个 tab
- 多个 source 共享同一 tab 的 DOM 数据

结果：消除 7 个跨 map 重复 URL

### Phase 3: 数据目录页精简 ✅

扩展 `A_SHARE_DATA_DIRECTORY_EXCLUDED_ITEM_KEYS`，排除冗余子页面：
- 6 个公告子页面（sha/sza/bja/cyb/kcb/dss → 已有 notices/）
- 6 个财报子页面（yjkb/yjyg/yysj/zcfz/lrb/xjll → 已有 bbsj/）
- 7 个研报子页面（stock/profit/industry/strategy/... → 已有 report/）
- 5 个 IPO 子页面（calendar/meeting/guidance/analysis/dbqy → 已有 xg/ipo）
- 9 个非必要特色数据
- 4 个冗余 HSGT/热门/新股数据
- 3 个冗余资金流数据

结果：DATA_DIRECTORY tabs 从 62 降到 19（减少 69%）

### 总体效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| REQUIRED tabs | 7 | 7 |
| ENHANCED tabs | 8 | 8 |
| DATA_DIRECTORY tabs | 62 | 19 |
| 跨 map 重复 URL | 7 | 0 |
| 总 source 数 | 77 | ~30 |
| 唯一 tab 数（理论） | 77 | ~25 |

## 待做

### Phase 2: 候选详情页复用

修改 `collect_candidate_detail_evidence()`：
- 复用已有 tab 导航到候选页面
- 而不是为每个候选开新 tab
- 读取完后导航回原页面

预期：再减少 20-30 tabs

## 验证

- 72 tests 保持通过 ✅
- 扫描时间不显著增加 ✅
- 出票结果不变 ✅
