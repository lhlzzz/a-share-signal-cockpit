# 东方财富股吧社交证据集成设计

> 目标: 将 xiaogu 的社交/舆情侧车收敛为东方财富股吧单一来源，降低外部平台噪声和运行复杂度。

## [S1] 问题

历史设计曾评估多类外部社交平台，但这些来源存在登录态、API、环境版本和话题噪声问题。当前项目要求更专一：社交证据只来自 A 股直接相关的东方财富股吧。

## [S2] 数据源边界

| 数据源 | 覆盖 | 成本 | 当前状态 |
|--------|------|------|---------|
| 东方财富股吧 | A股个股 | 免费 | ✅ 唯一保留 |
| 外部社交平台 | 非 A 股直接语境 | API/登录态/环境依赖/噪声高 | ❌ 移除 |

## [S3] 集成架构

```
东方财富股吧 → xiaogu_social_sentiment.py → signals 表 shadow diagnostics
                                      ↓
                            runner attach_social_features
                                      ↓
                         social_confirmation（诊断-only）
```

## [S4] 实施边界

1. `xiaogu_social_sentiment.py` 是唯一社交采集 owner。
2. active 采集只允许东方财富股吧：公开页面优先，必要时复用 CDP fallback。
3. 标准化写入现有 `signals` 表：
   - `social_sentiment_eastmoney_guba`
   - `social_catalyst_score`
   - `social_noise_risk`
   - `social_sentiment_score`
   - `social_collection_status`
4. 不再写入任何外部社交平台或主题热度 signal。
5. Scanner 和 runner 只读取 shadow diagnostics；不得改变正式排序或 `PAPER_PICK` eligibility。

## [S5] 预期效果

- 降低外部平台采集失败导致的 WARN 噪声。
- 保持 A 股主板语境一致，避免海外社交话题污染。
- 继续保留可诊断的 `social_confirmation`，但不作为 official ranking 或硬门禁。

## [S6] 风险与处理

- 东方财富股吧可能反爬或结构变化：失败写 `social_collection_status=WARN`，不阻塞 daily pipeline。
- 情绪词简单：作为 shadow-only 观测信号，不直接影响出票。
- 历史 DB 中可能仍有旧 signal key：本设计只停止未来写入，不清理历史数据。
