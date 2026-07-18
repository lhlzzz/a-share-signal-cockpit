下一步 1：已新增 `L11_LOW_POSITION_AMBUSH` 低位潜伏池，可捕获高振幅+高换手+低位的主力洗盘信号。下一次盘中用 CDP 9333 跑 fresh scan 验证新池在真实数据下的出票效果。
下一步 2：已修复 3 个 bug：(1) RESEARCH_BASKET_SIZE 3→8；(2) official_target_exclusion_reasons 移除 research-only 排除；(3) load_candidate_bundle 传入 asof_time 避免 SCAN_TOO_OLD。
下一步 3：保持 PAPER_ONLY / NO_TRADE / ALLOW_TRADE=False，继续每日复盘 T+1 收益与出票稳定性。
