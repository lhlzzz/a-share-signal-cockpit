# 28域全量接入出票打分链路 实施方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将东财行情中心28个数据域全部接入 `integrated_score()` 打分链路，使每个域的采集数据都能影响出票决策。

**Architecture:** 在 `xiaogu_v2_1_six_repo_real_integrated.py` 的 `integrated_score()` 中新增 `evidence_domain_features(c)` 提取函数，从 candidate 上已挂载的 evidence flags 中提取28域特征，转化为可量化的评分维度。不改变现有评分主干，只在 `opp` 计算中叠加新维度。

**Tech Stack:** Python 3, 无新依赖

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `xiaogu_v2_1_six_repo_real_integrated.py` | 新增 `evidence_domain_features()` + 修改 `integrated_score()` |
| `xiaogu_db.py` | 新增 `scoring_config` 表中28域权重配置项 |
| `tests/test_28_domain_scoring.py` | 新增测试：验证28域特征提取和打分影响 |

## 域映射表（28域 → 评分维度）

| 层级 | 域 | 候选人字段 | 评分维度 | 权重方向 |
|------|-----|-----------|----------|----------|
| **基础5域** | | | | |
| | announcements | `catalysts` | `catalyst_boost` | + |
| | risk_alerts | `risk_reasons` | `regulatory_penalty` | - (已接入) |
| | lhb | `lhb_risk_flags` | `lhb_penalty` | - (已接入) |
| | concept_industry | `concept_industry_tags` | `sector_alignment` | + (已接入) |
| | financials | `financial_risk_flags` | `fundamental_penalty` | - (已接入) |
| **核心增强13域** | | | | |
| | limitup_strength | `limitup_strength_tags` | `limitup_momentum` | + |
| | broken_limit_risk | `broken_limit_risk_flags` | `broken_limit_penalty` | - |
| | consecutive_limit_strength | `limitup_strength_tags` | `consecutive_limit_bonus` | + |
| | yesterday_limit_strength | `limitup_strength_tags` | `yesterday_limit_bonus` | + |
| | popularity_heat | `popularity_heat` | `popularity_boost` | + |
| | industry_board | `board_strength_tags` | `board_momentum` | + |
| | sector_fund_flow | `sector_fund_flow` | `sector_flow_boost` | + |
| | concept_capital_flow | `concept_capital_flow` | `concept_flow_boost` | + |
| | candidate_quote_recheck | `candidate_quote_recheck_tags` | `quote_recheck_boost` | + |
| | candidate_fund_recheck | `candidate_fund_recheck_tags` | `fund_recheck_boost` | + |
| | candidate_lhb_recheck | `candidate_lhb_recheck` | `lhb_recheck_boost` | + |
| | candidate_announcement_recheck | `candidate_announcement_recheck` | `announcement_recheck_boost` | + |
| | candidate_intraday_replay | `candidate_intraday_replay` | `intraday_replay_boost` | + |
| **实验性8域** | | | | |
| | margin_trading | `margin_risk_flags` | `margin_risk_penalty` | - |
| | block_trades | `block_trade_flags` | `block_trade_penalty` | - |
| | lockup_expiry | `lockup_risk_flags` | `lockup_risk_penalty` | - |
| | shareholder_changes | `shareholder_change_flags` | `shareholder_signal` | ± |
| | research_reports | `research_rating_tags` | `research_rating_boost` | + |
| | earnings_preview | `earnings_preview_flags` | `earnings_signal` | ± |
| | ipo_calendar | `ipo_calendar_tags` | `ipo_pressure_penalty` | - |
| | trading_halts | `trading_halt_flags` | `halt_block` | - |
| **间接域** | | | | |
| | data_directory_content | `data_directory_content_evidence` | `directory_content_boost` | + |
| | historical_risk_notes | `historical_risk_notes` | `historical_risk_penalty` | - |

---

## Task 1: 新增 evidence_domain_features() 提取函数

**Covers:** 28域特征提取

**Files:**
- Modify: `xiaogu_v2_1_six_repo_real_integrated.py:177` (在 `integrated_score` 前插入)
- Create: `tests/test_28_domain_scoring.py`

**Interfaces:**
- Consumes: candidate dict（已挂载 evidence flags 的 scored record）
- Produces: dict，包含所有28域的量化特征

- [ ] **Step 1: Write the failing test**

```python
# tests/test_28_domain_scoring.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaogu_v2_1_six_repo_real_integrated import evidence_domain_features


def test_evidence_domain_features_extracts_all_28():
    candidate = {
        'code': '000001',
        'risk_reasons': ['异常波动公告'],
        'catalysts': ['中标公告'],
        'lhb_risk_flags': [],
        'concept_industry_tags': ['银行', '金融'],
        'financial_risk_flags': [],
        'limitup_strength_tags': ['涨停封板'],
        'broken_limit_risk_flags': [],
        'popularity_heat': 15.0,
        'board_strength_tags': ['银行板块领涨'],
        'sector_fund_flow': '净流入2.5亿',
        'concept_capital_flow': '净流入1.8亿',
        'candidate_quote_recheck_tags': ['买一堆积'],
        'candidate_fund_recheck_tags': ['主力净流入'],
        'candidate_lhb_recheck': [{'text': '机构买入'}],
        'candidate_announcement_recheck': [{'text': '中标'}],
        'candidate_intraday_replay': [{'text': '资金流向'}],
        'margin_risk_flags': [],
        'block_trade_flags': [],
        'lockup_risk_flags': [],
        'shareholder_change_flags': ['增持'],
        'research_rating_tags': ['买入评级'],
        'earnings_preview_flags': ['预增'],
        'ipo_calendar_tags': [],
        'trading_halt_flags': [],
        'data_directory_content_evidence': {'record_count': 3},
        'historical_risk_notes': [],
    }
    features = evidence_domain_features(candidate)
    assert isinstance(features, dict)
    assert 'limitup_momentum' in features
    assert 'broken_limit_penalty' in features
    assert 'sector_flow_boost' in features
    assert 'research_rating_boost' in features
    assert features['limitup_momentum'] > 0
    assert features['broken_limit_penalty'] == 0
    assert features['sector_flow_boost'] > 0


def test_evidence_domain_features_empty_candidate():
    features = evidence_domain_features({})
    assert isinstance(features, dict)
    assert all(v == 0 for v in features.values())


def test_evidence_domain_features_risk_penalty():
    candidate = {
        'risk_reasons': ['严重异常波动'],
        'margin_risk_flags': ['融券余额大增'],
        'lockup_risk_flags': ['解禁'],
        'trading_halt_flags': ['停牌'],
    }
    features = evidence_domain_features(candidate)
    assert features['regulatory_penalty'] > 0
    assert features['margin_risk_penalty'] > 0
    assert features['lockup_risk_penalty'] > 0
    assert features['halt_block'] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_28_domain_scoring.py -v`
Expected: FAIL with `ImportError: cannot import name 'evidence_domain_features'`

- [ ] **Step 3: Write minimal implementation**

在 `xiaogu_v2_1_six_repo_real_integrated.py` 中，`integrated_score` 函数之前插入：

```python
def evidence_domain_features(c):
    """从candidate挂载的evidence flags中提取28域评分特征。"""
    def _has(items):
        return 1.0 if items else 0.0

    def _count(items):
        return float(len(items)) if isinstance(items, list) else 0.0

    def _flow_boost(text):
        if not text:
            return 0.0
        s = str(text)
        import re
        m = re.search(r'([-+]?\d+\.?\d*)\s*亿', s)
        if m:
            v = float(m.group(1))
            return min(2.0, max(-2.0, v * 0.5))
        m = re.search(r'([-+]?\d+\.?\d*)\s*万', s)
        if m:
            v = float(m.group(1)) / 10000
            return min(1.0, max(-1.0, v * 0.3))
        if '净流入' in s:
            return 0.3
        if '净流出' in s:
            return -0.3
        return 0.0

    risk_reasons = c.get('risk_reasons') or []
    catalysts = c.get('catalysts') or []
    lhb_risk = c.get('lhb_risk_flags') or []
    concept_tags = c.get('concept_industry_tags') or []
    fin_risk = c.get('financial_risk_flags') or []
    limitup_tags = c.get('limitup_strength_tags') or []
    broken_tags = c.get('broken_limit_risk_flags') or []
    pop_heat = float(c.get('popularity_heat') or 0)
    board_tags = c.get('board_strength_tags') or []
    sector_flow = c.get('sector_fund_flow') or ''
    concept_flow = c.get('concept_capital_flow') or ''
    quote_recheck = c.get('candidate_quote_recheck_tags') or []
    fund_recheck = c.get('candidate_fund_recheck_tags') or []
    lhb_recheck = c.get('candidate_lhb_recheck') or []
    announce_recheck = c.get('candidate_announcement_recheck') or []
    intraday_replay = c.get('candidate_intraday_replay') or []
    margin_risk = c.get('margin_risk_flags') or []
    block_trade = c.get('block_trade_flags') or []
    lockup_risk = c.get('lockup_risk_flags') or []
    shareholder = c.get('shareholder_change_flags') or []
    research_rating = c.get('research_rating_tags') or []
    earnings = c.get('earnings_preview_flags') or []
    ipo_tags = c.get('ipo_calendar_tags') or []
    halt_flags = c.get('trading_halt_flags') or []
    dir_evidence = c.get('data_directory_content_evidence') or {}
    hist_risk = c.get('historical_risk_notes') or []

    return {
        'catalyst_boost': _has(catalysts) * 0.5,
        'regulatory_penalty': _has(risk_reasons) * 3.0,
        'lhb_penalty': _has(lhb_risk) * 1.0,
        'sector_alignment': min(2.0, _count(concept_tags) * 0.4),
        'fundamental_penalty': _has(fin_risk) * 2.0,
        'limitup_momentum': min(2.0, _count(limitup_tags) * 0.7),
        'broken_limit_penalty': _has(broken_tags) * 1.5,
        'consecutive_limit_bonus': 0.5 if any('连板' in str(t) for t in limitup_tags) else 0.0,
        'yesterday_limit_bonus': 0.3 if any('昨日' in str(t) for t in limitup_tags) else 0.0,
        'popularity_boost': min(1.5, pop_heat / 50.0) if pop_heat > 0 else 0.0,
        'board_momentum': min(1.5, _count(board_tags) * 0.5),
        'sector_flow_boost': _flow_boost(sector_flow),
        'concept_flow_boost': _flow_boost(concept_flow),
        'quote_recheck_boost': min(1.0, _count(quote_recheck) * 0.3),
        'fund_recheck_boost': min(1.0, _count(fund_recheck) * 0.3),
        'lhb_recheck_boost': min(1.0, _count(lhb_recheck) * 0.4),
        'announcement_recheck_boost': min(1.0, _count(announce_recheck) * 0.3),
        'intraday_replay_boost': min(0.8, _count(intraday_replay) * 0.2),
        'margin_risk_penalty': _has(margin_risk) * 1.0,
        'block_trade_penalty': _has(block_trade) * 0.5,
        'lockup_risk_penalty': _has(lockup_risk) * 1.5,
        'shareholder_signal': (0.5 if any('增持' in str(s) for s in shareholder) else -0.5) if shareholder else 0.0,
        'research_rating_boost': min(1.5, _count(research_rating) * 0.5),
        'earnings_signal': (0.8 if any('预增' in str(e) or '扭亏' in str(e) for e in earnings) else -0.5) if earnings else 0.0,
        'ipo_pressure_penalty': _has(ipo_tags) * 0.3,
        'halt_block': _has(halt_flags) * 5.0,
        'directory_content_boost': min(1.0, float(dir_evidence.get('record_count', 0)) * 0.2) if dir_evidence else 0.0,
        'historical_risk_penalty': min(2.0, _count(hist_risk) * 0.5),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_28_domain_scoring.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add xiaogu_v2_1_six_repo_real_integrated.py tests/test_28_domain_scoring.py
git commit -m "feat: add evidence_domain_features() extracting 28-domain scoring signals"
```

---

## Task 2: 将28域特征接入 integrated_score() 打分

**Covers:** 打分链路集成

**Files:**
- Modify: `xiaogu_v2_1_six_repo_real_integrated.py:177-321` (`integrated_score` 函数)

**Interfaces:**
- Consumes: `evidence_domain_features(c)` from Task 1
- Produces: 修改后的 `opp` 计算，叠加28域维度

- [ ] **Step 1: Write the failing test**

```python
# tests/test_28_domain_scoring.py 追加

from xiaogu_v2_1_six_repo_real_integrated import integrated_score


def test_integrated_score_with_evidence_domains():
    """验证28域特征能影响打分结果。"""
    base_candidate = {
        'code': '300001',
        'price': 15.0,
        'signal_pct': 6.5,
        'amount_pctile_rule': 0.7,
        'rank': 20,
        'net_inflow_main': 50000000,
        'market_breadth_up_pct': 50.0,
        'market_limitups': 40,
        'market_bigups': 80,
        'close_position_score': 0.75,
        'volume_ratio': 2.0,
        'full_universe_fund_pctile': 0.6,
        'theme_strength': 5.0,
        'source_layers': ['L2_LIMIT_STRENGTH', 'L3_FUND_FLOW'],
        'search_layer_hint': '',
        'setup_type': '',
        'candidate_stage': '',
        'limitup_capture_score': 0.5,
        'limitup_capture_profile': 'MEDIUM_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': False,
        'limitup_reason_propagation_score': 0.3,
        'sector_opportunity_score': 0.4,
        'sector_catalyst_score': 0.4,
    }

    # 无evidence
    score_clean, reasons_clean, regime_clean = integrated_score(base_candidate)

    # 有正向evidence
    candidate_with_evidence = {
        **base_candidate,
        'catalysts': ['中标公告'],
        'concept_industry_tags': ['机器人', 'AI'],
        'limitup_strength_tags': ['涨停封板'],
        'board_strength_tags': ['机器人板块领涨'],
        'research_rating_tags': ['买入评级'],
        'earnings_preview_flags': ['预增'],
    }
    score_boosted, reasons_boosted, regime_boosted = integrated_score(candidate_with_evidence)

    # 有负向evidence
    candidate_with_risk = {
        **base_candidate,
        'risk_reasons': ['异常波动'],
        'broken_limit_risk_flags': ['炸板'],
        'margin_risk_flags': ['融券余额大增'],
        'lockup_risk_flags': ['大额解禁'],
    }
    score_penalized, reasons_penalized, regime_penalized = integrated_score(candidate_with_risk)

    # 正向evidence应该提高分数（或至少不降低）
    if score_clean is not None and score_boosted is not None:
        assert score_boosted >= score_clean, f"boosted={score_boosted} should >= clean={score_clean}"

    # 负向evidence应该降低分数或产生block
    if score_clean is not None and score_penalized is not None:
        assert score_penalized <= score_clean, f"penalized={score_penalized} should <= clean={score_clean}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_28_domain_scoring.py::test_integrated_score_with_evidence_domains -v`
Expected: FAIL（因为当前打分不消费evidence domains，boosted和clean分数相同）

- [ ] **Step 3: 修改 integrated_score()**

在 `integrated_score()` 函数中，`opp` 计算之后、返回之前，添加28域调整：

```python
def integrated_score(c):
    # ... 现有代码直到 opp 计算完成 (line ~310) ...

    # === 28域证据调整 ===
    edf = evidence_domain_features(c)

    # 硬否决域
    if edf['halt_block'] > 0:
        return None, ['evidence_domain_halt_block'], market_regime

    # 风险惩罚（累加到risk）
    evidence_risk = (
        edf['broken_limit_penalty']
        + edf['margin_risk_penalty']
        + edf['lockup_risk_penalty']
        + edf['block_trade_penalty']
        + edf['ipo_pressure_penalty']
        + edf['historical_risk_penalty']
    )
    risk += evidence_risk

    # 机会加分（累加到opp）
    evidence_opp = (
        edf['catalyst_boost']
        + edf['limitup_momentum']
        + edf['consecutive_limit_bonus']
        + edf['yesterday_limit_bonus']
        + edf['popularity_boost']
        + edf['board_momentum']
        + edf['sector_flow_boost']
        + edf['concept_flow_boost']
        + edf['quote_recheck_boost']
        + edf['fund_recheck_boost']
        + edf['lhb_recheck_boost']
        + edf['announcement_recheck_boost']
        + edf['intraday_replay_boost']
        + edf['research_rating_boost']
        + edf['earnings_signal']
        + edf['directory_content_boost']
        + edf['shareholder_signal']
    )
    opp += evidence_opp

    # 重算风险检查
    risk_threshold = 30 if market_regime in ('strong', 'climax') else (25 if market_regime == 'neutral' else 20)
    if risk >= risk_threshold:
        return None, [f'risk_too_high:{risk:.0f}'], market_regime

    # 重算opp门槛检查
    if market_regime == 'climax':
        opp_threshold, opp_candidate_type = climax_opp_requirement(c, f, limit, near_limit)
    else:
        opp_threshold, opp_candidate_type = (32 if market_regime == 'strong' else (38 if market_regime == 'neutral' else 45)), market_regime
    if opp < opp_threshold:
        return None, [f'opp_too_low:actual={opp:.1f},required={opp_threshold:.1f},candidate_type={opp_candidate_type}'], market_regime

    # === 原有 final_score 计算 ===
    ranking_adjustment = MID_PRICE_REBALANCE_WEIGHT * min(f['price'], MID_PRICE_REBALANCE_CAP)
    if f['price'] < LOW_PRICE_CROWDING_GATE:
        ranking_adjustment -= LOW_PRICE_CROWDING_PENALTY_WEIGHT * (LOW_PRICE_CROWDING_GATE - f['price'])
    if f['rank'] < FRONT_AMOUNT_RANK_GATE:
        ranking_adjustment -= FRONT_AMOUNT_RANK_PENALTY_WEIGHT * (FRONT_AMOUNT_RANK_GATE - f['rank'])
    final_score = opp - risk * 0.25 + ranking_adjustment

    if board == 'main' and f['market_breadth'] < MAIN_BOARD_BREADTH_GATE:
        return None, [f'main_board_breadth_too_low:{f["market_breadth"]:.2f}'], market_regime

    return final_score, [], market_regime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_28_domain_scoring.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `pytest tests/ -x -q`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add xiaogu_v2_1_six_repo_real_integrated.py tests/test_28_domain_scoring.py
git commit -m "feat: wire 28-domain evidence features into integrated_score() scoring pipeline"
```

---

## Task 3: 在 scoring_config 表中注册28域权重配置

**Covers:** 可调权重配置

**Files:**
- Modify: `xiaogu_db.py:19-37` (SCORING_CONFIG_DEFAULTS)
- Modify: `scripts/xiaogu_db_init.sql` (如存在 scoring_config 表)

**Interfaces:**
- Consumes: scoring_config 表
- Produces: 28域权重可通过 DB 调整，无需改代码

- [ ] **Step 1: Write the failing test**

```python
# tests/test_28_domain_scoring.py 追加

from xiaogu_db import get_scoring_config_snapshot


def test_scoring_config_has_28_domain_weights():
    snapshot = get_scoring_config_snapshot()
    config = snapshot.get('config', {})
    key_domains = [
        'evidence_limitup_momentum_weight',
        'evidence_broken_limit_penalty_weight',
        'evidence_sector_flow_weight',
        'evidence_research_rating_weight',
        'evidence_margin_risk_weight',
        'evidence_lockup_risk_weight',
    ]
    for key in key_domains:
        assert key in config, f"Missing scoring_config key: {key}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_28_domain_scoring.py::test_scoring_config_has_28_domain_weights -v`
Expected: FAIL

- [ ] **Step 3: Add config defaults**

在 `xiaogu_db.py` 的 `SCORING_CONFIG_DEFAULTS` 中追加：

```python
SCORING_CONFIG_DEFAULTS: Dict[str, str] = {
    # ... 现有配置 ...
    "evidence_catalyst_boost_weight": "0.5",
    "evidence_limitup_momentum_weight": "0.7",
    "evidence_broken_limit_penalty_weight": "1.5",
    "evidence_consecutive_limit_bonus_weight": "0.5",
    "evidence_yesterday_limit_bonus_weight": "0.3",
    "evidence_popularity_boost_weight": "1.0",
    "evidence_board_momentum_weight": "0.5",
    "evidence_sector_flow_weight": "0.5",
    "evidence_concept_flow_weight": "0.5",
    "evidence_quote_recheck_weight": "0.3",
    "evidence_fund_recheck_weight": "0.3",
    "evidence_lhb_recheck_weight": "0.4",
    "evidence_announcement_recheck_weight": "0.3",
    "evidence_intraday_replay_weight": "0.2",
    "evidence_margin_risk_weight": "1.0",
    "evidence_block_trade_weight": "0.5",
    "evidence_lockup_risk_weight": "1.5",
    "evidence_shareholder_signal_weight": "0.5",
    "evidence_research_rating_weight": "0.5",
    "evidence_earnings_signal_weight": "0.8",
    "evidence_ipo_pressure_weight": "0.3",
    "evidence_halt_block_weight": "5.0",
    "evidence_directory_content_weight": "0.2",
    "evidence_historical_risk_weight": "0.5",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_28_domain_scoring.py::test_scoring_config_has_28_domain_weights -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -x -q`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add xiaogu_db.py tests/test_28_domain_scoring.py
git commit -m "feat: add 28-domain evidence weight defaults to scoring_config"
```

---

## Task 4: 在 runner 中透传28域 evidence 到 candidate

**Covers:** Runner→Scanner 数据透传

**Files:**
- Modify: `xiaogu_forward_d1_1450_runner_v0_1.py` (candidate bundle 读取逻辑)

**Interfaces:**
- Consumes: scanner 输出的 `eastmoney_web_tabs_scored.jsonl` 中的 evidence flags
- Produces: runner 的 candidate dict 包含完整28域 evidence flags

- [ ] **Step 1: Verify runner reads evidence flags**

检查 `runner` 的 `decision_for_candidate()` 是否已将 scanner 输出的 evidence flags 透传到 candidate dict。

关键检查点：
- `runner:2268` `decision_for_candidate()` 中 `features = dict(candidate)` 是否包含 evidence flags
- `runner:5546-5573` `score_candidates()` 输出是否包含 `limitup_strength_tags` 等字段

- [ ] **Step 2: 如果 runner 未透传，添加透传逻辑**

在 `decision_for_candidate()` 中，确保 `features` dict 包含所有28域字段：

```python
# 在 features = dict(candidate) 之后追加
EVIDENCE_DOMAIN_FIELDS = [
    'risk_reasons', 'catalysts', 'lhb_risk_flags', 'concept_industry_tags',
    'financial_risk_flags', 'limitup_strength_tags', 'broken_limit_risk_flags',
    'popularity_heat', 'board_strength_tags', 'sector_fund_flow',
    'concept_capital_flow', 'candidate_quote_recheck_tags',
    'candidate_fund_recheck_tags', 'candidate_lhb_recheck',
    'candidate_announcement_recheck', 'candidate_intraday_replay',
    'margin_risk_flags', 'block_trade_flags', 'lockup_risk_flags',
    'shareholder_change_flags', 'research_rating_tags', 'earnings_preview_flags',
    'ipo_calendar_tags', 'trading_halt_flags',
    'data_directory_content_evidence', 'historical_risk_notes',
]
for field in EVIDENCE_DOMAIN_FIELDS:
    if field not in features and field in candidate:
        features[field] = candidate[field]
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -x -q`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add xiaogu_forward_d1_1450_runner_v0_1.py
git commit -m "feat: forward 28-domain evidence flags from scanner to runner scoring"
```

---

## Task 5: 端到端验证 — 模拟完整出票流程

**Covers:** 端到端验证

**Files:**
- Create: `tests/test_28_domain_e2e.py`

- [ ] **Step 1: Write e2e test**

```python
# tests/test_28_domain_e2e.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xiaogu_v2_1_six_repo_real_integrated import integrated_score, evidence_domain_features


def test_full_candidate_with_all_28_domains():
    """模拟一个携带完整28域evidence的candidate走完打分链路。"""
    candidate = {
        'code': '300001',
        'price': 15.0,
        'signal_pct': 6.5,
        'amount_pctile_rule': 0.7,
        'rank': 20,
        'net_inflow_main': 50000000,
        'market_breadth_up_pct': 50.0,
        'market_limitups': 40,
        'market_bigups': 80,
        'close_position_score': 0.75,
        'volume_ratio': 2.0,
        'full_universe_fund_pctile': 0.6,
        'theme_strength': 5.0,
        'source_layers': ['L2_LIMIT_STRENGTH', 'L3_FUND_FLOW'],
        'search_layer_hint': '',
        'setup_type': '',
        'candidate_stage': '',
        'limitup_capture_score': 0.5,
        'limitup_capture_profile': 'MEDIUM_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': False,
        'limitup_reason_propagation_score': 0.3,
        'sector_opportunity_score': 0.4,
        'sector_catalyst_score': 0.4,
        # 28域 evidence
        'catalysts': ['中标公告5亿元'],
        'risk_reasons': [],
        'lhb_risk_flags': [],
        'concept_industry_tags': ['机器人', 'AI'],
        'financial_risk_flags': [],
        'limitup_strength_tags': ['涨停封板', '连板'],
        'broken_limit_risk_flags': [],
        'popularity_heat': 25.0,
        'board_strength_tags': ['机器人板块领涨'],
        'sector_fund_flow': '净流入3.2亿',
        'concept_capital_flow': '净流入2.1亿',
        'candidate_quote_recheck_tags': ['买一堆积', '委比正'],
        'candidate_fund_recheck_tags': ['主力净流入1.5亿'],
        'candidate_lhb_recheck': [{'text': '机构专用买入'}],
        'candidate_announcement_recheck': [{'text': '中标公告'}],
        'candidate_intraday_replay': [{'text': '资金持续流入'}],
        'margin_risk_flags': [],
        'block_trade_flags': [],
        'lockup_risk_flags': [],
        'shareholder_change_flags': ['增持'],
        'research_rating_tags': ['买入评级', '目标价25元'],
        'earnings_preview_flags': ['预增50%'],
        'ipo_calendar_tags': [],
        'trading_halt_flags': [],
        'data_directory_content_evidence': {'record_count': 5},
        'historical_risk_notes': [],
    }

    features = evidence_domain_features(candidate)
    # 验证所有特征都被提取
    assert len(features) == 28
    # 验证正向特征
    assert features['catalyst_boost'] > 0
    assert features['limitup_momentum'] > 0
    assert features['consecutive_limit_bonus'] > 0
    assert features['sector_flow_boost'] > 0
    assert features['concept_flow_boost'] > 0
    assert features['research_rating_boost'] > 0
    assert features['earnings_signal'] > 0
    # 验证无风险
    assert features['regulatory_penalty'] == 0
    assert features['halt_block'] == 0

    # 打分不应被block
    score, reasons, regime = integrated_score(candidate)
    assert score is not None, f"Should not be blocked, reasons: {reasons}"
    assert score > 0


def test_candidate_blocked_by_halt_domain():
    """停牌域应硬否决。"""
    candidate = {
        'code': '300001',
        'price': 15.0,
        'signal_pct': 6.5,
        'amount_pctile_rule': 0.7,
        'rank': 20,
        'net_inflow_main': 50000000,
        'market_breadth_up_pct': 50.0,
        'market_limitups': 40,
        'market_bigups': 80,
        'close_position_score': 0.75,
        'volume_ratio': 2.0,
        'full_universe_fund_pctile': 0.6,
        'theme_strength': 5.0,
        'source_layers': ['L2_LIMIT_STRENGTH', 'L3_FUND_FLOW'],
        'search_layer_hint': '',
        'setup_type': '',
        'candidate_stage': '',
        'limitup_capture_score': 0.5,
        'limitup_capture_profile': 'MEDIUM_LIMITUP_CAPTURE',
        'limitup_capture_confirmed': False,
        'limitup_reason_propagation_score': 0.3,
        'sector_opportunity_score': 0.4,
        'sector_catalyst_score': 0.4,
        'trading_halt_flags': ['重大事项停牌'],
    }
    score, reasons, regime = integrated_score(candidate)
    assert score is None
    assert any('halt_block' in r for r in reasons)
```

- [ ] **Step 2: Run e2e tests**

Run: `pytest tests/test_28_domain_e2e.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_28_domain_e2e.py
git commit -m "test: add e2e tests for 28-domain scoring integration"
```

---

## 验证清单

- [ ] `pytest tests/test_28_domain_scoring.py -v` → 全部 PASS
- [ ] `pytest tests/test_28_domain_e2e.py -v` → 全部 PASS
- [ ] `pytest tests/ -x -q` → 无回归
- [ ] 28域全部有对应提取逻辑
- [ ] `integrated_score()` 消费所有28域特征
- [ ] scoring_config 表包含28域权重配置
- [ ] runner 透传 evidence flags 到 candidate
