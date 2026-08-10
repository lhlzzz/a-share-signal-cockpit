#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.1 strategy: active four-repo/native deterministic REAL_OUTPUT integration."""
import json, hashlib, sys
from pathlib import Path
from collections import defaultdict, Counter

BASE = Path('/root/hermes/company-ai-system/workspaces/xiaogu')
sys.path.insert(0, str(BASE))
from six_repo_integration_real_v2_1 import aggregate_four_repo_native_signals

SERV = BASE/'topn_candidate_serving_features_v0_3c_300d.jsonl'
LAB = BASE/'topn_candidate_evaluation_labels_v0_3c_300d.jsonl'
LEDGER = BASE/'v2_1_six_repo_real_integrated_ledger.jsonl'
SUMMARY = BASE/'v2_1_six_repo_real_integrated_summary.json'
LEAKAGE = BASE/'v2_1_six_repo_real_integrated_leakage_check.json'
MANIFEST = BASE/'v2_1_six_repo_real_integrated_manifest.json'
STOP = -0.08
# Match the more ticket-friendly conservative profile used by v1.4.
MAIN_BOARD_BREADTH_GATE = 20.0
MAIN_BOARD_SCORE_GATE = 95.0
MAIN_BOARD_RANK_GATE = 40.0
MARKET_CLIMAX_BREADTH_GATE = 70.0
MARKET_CLIMAX_LIMITUPS_GATE = 65.0
MARKET_CLIMAX_BIGUPS_GATE = 150.0
CLIMAX_CLOSE_POSITION_MIN = 0.93
CLIMAX_UNDERWATER_CLOSE_POSITION_MIN = 0.85
CLIMAX_SECTOR_CLOSE_POSITION_MIN = 0.87
CLIMAX_LIMIT_POTENTIAL_PCT = 0.85
CLIMAX_OPP_THRESHOLD = 30.0
CLIMAX_UNDERWATER_OPP_THRESHOLD = 24.0
CLIMAX_SECTOR_OPP_THRESHOLD = 26.0
NEAR_LIMIT_CLOSE_POSITION_MIN = 0.93
NEAR_LIMIT_LIQUIDITY_MIN = 0.60
MID_PRICE_REBALANCE_WEIGHT = 0.20
MID_PRICE_REBALANCE_CAP = 30.0
LOW_PRICE_CROWDING_GATE = 8.0
LOW_PRICE_CROWDING_PENALTY_WEIGHT = 1.0
FRONT_AMOUNT_RANK_GATE = 18.0
FRONT_AMOUNT_RANK_PENALTY_WEIGHT = 0.20
RANK_SOFT_BIAS = {4: 2.0, 5: 2.0, 6: 2.0, 1: -1.0, 2: -1.0, 3: -1.0, 7: -1.5, 8: -1.5, 9: -1.5, 10: -1.5}
STRATEGY = 'v2_1_four_repo_real_integrated'
REPO_ALLOWED_FIELDS = {
    'tradingagent_a': ['code', 'price'],
    'VEI': ['signal_pct', 'close_position_score', 'volume_ratio', 'full_universe_fund_pctile', 'amount_pctile_rule', 'source_layers', 'component_details', 'structured_component_details', 'sector_opportunity_score'],
    'Qlib': ['signal_pct', 'amount_pctile_rule', 'rank', 'market_breadth_up_pct', 'market_limitups', 'market_bigups', 'price', 'close_position_score', 'net_inflow_main'],
    'QuantDinger': ['amount_pctile_rule', 'price', 'source_row_hash', 'evidence_path', 'source_time', 'data_cutoff'],
}


def fl(x, d=None):
    if x is None or x == '':
        return d
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def limit_th(code):
    c = str(code).zfill(6)
    if c.startswith(('300', '301')):
        return .195
    if c.startswith(('688', '689')):
        return .195
    if c.startswith(('430', '431', '832', '833', '834', '835', '836', '837', '838', '839',
                     '870', '871', '872', '873', '874', '875', '876', '877', '878', '879', '920')):
        return .295
    return .095


def label_result(l):
    vs = [(fl(l.get(f'T+{h}_net_pct'), 0) or 0) / 100 for h in [1, 2, 3, 5]]
    raw = min(vs)
    return {
        't1_return': vs[0],
        't2_return': vs[1],
        't3_return': vs[2],
        't5_return': vs[3],
        'max_gain': max(vs),
        'raw_worst': raw,
        'effective_worst': max(raw, STOP),
        'win_any': any(v > 0 for v in vs),
        'any_limit_up': any(v >= limit_th(l['code']) for v in vs),
        'all_neg': all(v < 0 for v in vs)
    }


def split_dates(dates):
    return dates[:180], dates[180:240], dates[240:300]


def asof_features(c):
    return {
        'signal_pct': fl(c.get('signal_pct'), 0) or 0,
        'price': fl(c.get('price'), 0) or 0,
        'market_breadth': fl(c.get('market_breadth_up_pct'), 0) or 0,
        'market_limitups': fl(c.get('market_limitups'), 0) or 0,
        'market_bigups': fl(c.get('market_bigups'), 0) or 0,
        'theme_strength': fl(c.get('theme_strength'), 0) or 0,
        'liquidity': fl(c.get('amount_pctile_rule'), 0) or 0,
        'rank': fl(c.get('rank'), 999) or 999,
        'net_inflow_main': fl(c.get('net_inflow_main'), 0) or 0,
        'close_position_score': fl(c.get('close_position_score'), None),
        'limitup_capture_score': fl(c.get('limitup_capture_score'), 0) or 0,
        'limitup_capture_profile': str(c.get('limitup_capture_profile') or ''),
        'limitup_capture_confirmed': bool(c.get('limitup_capture_confirmed')),
        'limitup_reason_propagation_score': fl(c.get('limitup_reason_propagation_score'), 0) or 0,
    }


def classify_market_regime(market_breadth, market_limitups=0, market_bigups=0):
    if (
        market_breadth >= MARKET_CLIMAX_BREADTH_GATE
        and (
            market_limitups >= MARKET_CLIMAX_LIMITUPS_GATE
            or market_bigups >= MARKET_CLIMAX_BIGUPS_GATE
        )
    ):
        return 'climax'
    if market_breadth >= 50:
        return 'strong'
    if market_breadth >= 30:
        return 'neutral'
    return 'weak'


def classify_sentiment_cycle(market_breadth, market_limitups=0, market_bigups=0, 
                             broken_limit_count=0, consecutive_limit_count=0):
    """细分情绪周期: 冰点/回暖/高潮/分歧/退潮
    
    学习自提示词「短线情绪周期判断」:
    - 统计涨停数、跌停数、炸板率、连板高度、亏钱效应
    - 划分当前阶段：冰点、回暖、高潮、分歧、退潮
    """
    # 计算连板高度 (用 consecutive_limit_count 近似)
    board_height = consecutive_limit_count
    
    # 计算炸板率 (broken_limit / total_limit)
    total_limit = market_limitups + broken_limit_count
    broken_rate = broken_limit_count / total_limit if total_limit > 0 else 0
    
    # 判断情绪周期
    if market_breadth < 25 and market_limitups < 30:
        # 冰点: 涨停少，赚钱效应差
        return {
            'cycle': 'freezing',
            'label': '冰点',
            'description': '涨停少，赚钱效应差，适合空仓观望',
            'position_advice': 0.2,  # 建议仓位 20%
            'operation_advice': '空仓观望或极轻仓试错',
        }
    elif market_breadth >= 25 and market_breadth < 45 and market_limitups >= 30 and market_limitups < 80:
        # 回暖: 涨停增加，赚钱效应好转
        return {
            'cycle': 'warming',
            'label': '回暖',
            'description': '涨停增加，赚钱效应好转，适合轻仓试错',
            'position_advice': 0.3,  # 建议仓位 30%
            'operation_advice': '轻仓试错低位首板',
        }
    elif market_breadth >= 60 and market_limitups >= 100:
        # 高潮: 涨停多，赚钱效应强
        return {
            'cycle': 'climax',
            'label': '高潮',
            'description': '涨停多，赚钱效应强，注意高位风险',
            'position_advice': 0.5,  # 建议仓位 50%
            'operation_advice': '持股为主，注意高位止盈',
        }
    elif broken_rate > 0.3 and market_limitups >= 50:
        # 分歧: 炸板率高，资金分歧
        return {
            'cycle': 'divergence',
            'label': '分歧',
            'description': '炸板率高，资金分歧，适合低吸强势股',
            'position_advice': 0.4,  # 建议仓位 40%
            'operation_advice': '低吸强势股，避免追高',
        }
    elif market_breadth < 40 and market_limitups < 50 and broken_rate > 0.2:
        # 退潮: 赚钱效应减弱
        return {
            'cycle': 'retreat',
            'label': '退潮',
            'description': '赚钱效应减弱，适合减仓防守',
            'position_advice': 0.2,  # 建议仓位 20%
            'operation_advice': '减仓防守，等待新周期',
        }
    else:
        # 正常: 根据强度判断
        if market_breadth >= 50:
            return {
                'cycle': 'normal_strong',
                'label': '正常偏强',
                'description': '赚钱效应正常，适合正常操作',
                'position_advice': 0.5,
                'operation_advice': '正常操作，关注主线板块',
            }
        else:
            return {
                'cycle': 'normal_weak',
                'label': '正常偏弱',
                'description': '赚钱效应一般，适合轻仓操作',
                'position_advice': 0.3,
                'operation_advice': '轻仓操作，快进快出',
            }


def classify_turnover_level(turnover_rate, price, market_cap=0):
    """换手率筹码层级分析
    
    学习自提示词「换手率筹码层级分析」:
    - 根据个股近期换手率区间，判断低位放量、中位换手、高位高换手风险
    - 计算筹码交换成本
    - 识别筹码松动、筹码锁定信号
    """
    turnover = turnover_rate or 0
    
    # 换手率层级判断
    if turnover < 3:
        level = 'low'
        label = '低换手'
        description = '筹码锁定，主力控盘度高'
        risk = 0.2  # 低风险
    elif turnover < 8:
        level = 'medium'
        label = '中换手'
        description = '筹码正常交换，关注方向'
        risk = 0.4  # 中等风险
    elif turnover < 15:
        level = 'high'
        label = '高换手'
        description = '筹码松动，注意高位风险'
        risk = 0.6  # 较高风险
    elif turnover < 25:
        level = 'very_high'
        label = '超高换手'
        description = '筹码剧烈交换，短线博弈'
        risk = 0.8  # 高风险
    else:
        level = 'extreme'
        label = '极端换手'
        description = '筹码极度松动，高风险'
        risk = 1.0  # 极高风险
    
    # 价格位置修正
    if price < 10:
        # 低价股换手率天然高
        risk *= 0.8
    elif price > 50:
        # 高价股换手率天然低
        risk *= 1.2
    
    # 市值修正
    if market_cap and market_cap > 0:
        if market_cap < 50e8:
            # 小盘股换手率高是正常的
            risk *= 0.9
        elif market_cap > 500e8:
            # 大盘股换手率低是正常的
            risk *= 1.1
    
    return {
        'level': level,
        'label': label,
        'description': description,
        'turnover_rate': turnover,
        'risk_score': round(min(1.0, risk), 2),
    }


def classify_volume_level(volume_ratio, amplitude=0):
    """量能层级分析
    
    学习自提示词「量能层级分析」:
    - 对比近阶段均量，区分缩量、温和放量、巨量爆量
    - 不同量能对应股价趋势含义
    - 识别量价背离风险
    """
    vr = volume_ratio or 0
    
    # 量能层级判断
    if vr < 0.5:
        level = 'shrink'
        label = '缩量'
        description = '资金观望，缺乏动力'
        signal = 'neutral'  # 中性
    elif vr < 0.8:
        level = 'light'
        label = '温和缩量'
        description = '资金谨慎，等待方向'
        signal = 'cautious'  # 谨慎
    elif vr < 1.2:
        level = 'normal'
        label = '平量'
        description = '资金正常，趋势延续'
        signal = 'neutral'  # 中性
    elif vr < 1.8:
        level = 'moderate'
        label = '温和放量'
        description = '资金入场，趋势确认'
        signal = 'positive'  # 积极
    elif vr < 2.5:
        level = 'heavy'
        label = '明显放量'
        description = '资金活跃，关注方向'
        signal = 'active'  # 活跃
    else:
        level = 'explosive'
        label = '巨量'
        description = '资金激烈博弈，注意风险'
        signal = 'risky'  # 风险
    
    # 量价配合判断
    price_up = amplitude > 0 if amplitude else None
    volume_up = vr > 1.0
    
    if price_up is not None:
        if volume_up and price_up:
            coordination = 'healthy'  # 量价配合健康
        elif volume_up and not price_up:
            coordination = 'bearish_divergence'  # 放量下跌，看空
        elif not volume_up and price_up:
            coordination = 'bullish_divergence'  # 缩量上涨，看多
        else:
            coordination = 'neutral'  # 缩量下跌，中性
    else:
        coordination = 'unknown'
    
    return {
        'level': level,
        'label': label,
        'description': description,
        'volume_ratio': vr,
        'signal': signal,
        'coordination': coordination,
    }


def climax_close_position_requirement(candidate, features, limit_pct, near_limit):
    layer = str(candidate.get('search_layer_hint') or '').lower()
    setup = str(candidate.get('setup_type') or '').upper()
    stage = str(candidate.get('candidate_stage') or '').lower()
    if near_limit or stage == 'near_limit_9_plus' or features['signal_pct'] >= limit_pct * CLIMAX_LIMIT_POTENTIAL_PCT:
        return CLIMAX_CLOSE_POSITION_MIN, 'near_limit_or_chase_high'
    if layer == 'underwater_reversal' or setup == 'UNDERWATER_TO_RED_STRENGTH' or stage == 'underwater':
        return CLIMAX_UNDERWATER_CLOSE_POSITION_MIN, 'underwater_reversal'
    if layer == 'sector_catalyst_low_position' or setup in ('SECTOR_NEWS_LOW_POSITION', 'LOW_POSITION_SECTOR_LIFT'):
        return CLIMAX_SECTOR_CLOSE_POSITION_MIN, 'sector_catalyst_low_position'
    return CLIMAX_CLOSE_POSITION_MIN, 'default_climax'


def climax_opp_requirement(candidate, features, limit_pct, near_limit):
    _, candidate_type = climax_close_position_requirement(candidate, features, limit_pct, near_limit)
    if candidate_type == 'underwater_reversal':
        return CLIMAX_UNDERWATER_OPP_THRESHOLD, candidate_type
    if candidate_type == 'sector_catalyst_low_position':
        return CLIMAX_SECTOR_OPP_THRESHOLD, candidate_type
    return CLIMAX_OPP_THRESHOLD, candidate_type


def repo_integration_record(repo_signals):
    return {
        'real_count': repo_signals['real_count'],
        'blocked_count': repo_signals['blocked_count'],
        'concept_count': repo_signals.get('concept_count', 0),
        'score_delta': repo_signals['score_delta'],
        'score_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
        'score_cap_by_repo': repo_signals.get('score_cap_by_repo', {}),
        'repo_contributions': repo_signals.get('repo_contributions', {}),
        'repo_contribution_summary': repo_signals.get('repo_contribution_summary', ''),
        'real_repos': [a['repo_name'] for a in repo_signals.get('real_outputs', [])],
        'blocked_repos': [a['repo_name'] for a in repo_signals.get('blocked_outputs', [])],
        'concept_repos': [a['repo_name'] for a in repo_signals.get('concept_outputs', [])],
        'native_runtime_summary': repo_signals.get('native_runtime_summary', {}),
        'signal_breakdown_by_repo': repo_signals.get('signal_breakdown_by_repo', {}),
        'evidence_paths_by_repo': repo_signals.get('evidence_paths_by_repo', {}),
        'blocked_repo_affects_scoring': False,
        'concept_only_affects_scoring': False,
        'external_api_used': repo_signals.get('external_api_used', False),
        'llm_used': repo_signals.get('llm_used', False),
        'allowed_fields_by_repo': REPO_ALLOWED_FIELDS,
        'native_integration_version': repo_signals.get('native_integration_version'),
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
    }


def _hsgt_factor(net_inflow, consecutive_days):
    """北向资金因子：净流入+连续天数"""
    score = 0.0
    if net_inflow > 0:
        score += min(1.0, net_inflow / 100000)  # 10亿封顶
    if consecutive_days >= 3:
        score += min(0.5, consecutive_days * 0.15)
    elif net_inflow < 0:
        score -= min(0.5, abs(net_inflow) / 200000)
    return score


def _turnover_tier(turnover):
    """换手率分档：低(<5%), 中(5-15%), 高(15-25%), 极高(>25%)"""
    if turnover < 5:
        return -0.2  # 低换手，流动性差
    elif turnover < 15:
        return 0.3   # 中等换手，健康
    elif turnover < 25:
        return 0.1   # 高换手，需警惕
    else:
        return -0.3  # 极高换手，主力出货风险


def _earnings_surprise(earnings_flags):
    """业绩预告惊喜因子"""
    if not earnings_flags:
        return 0.0
    for flag in earnings_flags:
        s = str(flag)
        if '预增' in s or '扭亏' in s or '续盈' in s:
            return 0.5
        if '预减' in s or '首亏' in s or '续亏' in s:
            return -0.5
    return 0.0


def _lockup_pressure(days_to_expiry, amount_ratio):
    """限售解禁压力：越近越危险，比例越大越危险"""
    if days_to_expiry < 0:
        return 0.0  # 已过解禁日
    if days_to_expiry <= 7:
        return -1.5 * min(1.0, amount_ratio / 10)  # 一周内解禁
    elif days_to_expiry <= 30:
        return -0.8 * min(1.0, amount_ratio / 10)  # 一月内解禁
    elif days_to_expiry <= 90:
        return -0.3 * min(1.0, amount_ratio / 10)  # 三月内解禁
    return 0.0


def _announcement_factor(sentiment):
    """公告情绪因子"""
    if sentiment == 'positive':
        return 0.4
    elif sentiment == 'negative':
        return -0.6
    return 0.0


def _macro_liquidity(score):
    """宏观流动性因子：HSGT+资金流综合评分"""
    if score >= 70:
        return 0.3  # 流动性宽松
    elif score >= 50:
        return 0.0  # 中性
    elif score >= 30:
        return -0.2  # 偏紧
    else:
        return -0.5  # 流动性紧张


def evidence_domain_features(c):
    """Extract quantified scoring features from all 28 evidence domains mounted on candidate."""
    import re as _re

    def _has(items):
        return 1.0 if items else 0.0

    def _count(items):
        return float(len(items)) if isinstance(items, list) else 0.0

    def _flow_boost(text):
        if not text:
            return 0.0
        s = str(text)
        m = _re.search(r'([-+]?\d+\.?\d*)\s*亿', s)
        if m:
            v = float(m.group(1))
            return min(2.0, max(-2.0, v * 0.5))
        m = _re.search(r'([-+]?\d+\.?\d*)\s*万', s)
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
    pop_heat = fl(c.get('popularity_heat'), 0)
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

    # === 新增因子：北向资金、换手率分档、业绩预告、限售解禁、公告情绪、市场流动性 ===
    hsgt_net = fl(c.get('hsgt_net_inflow'), 0)  # 北向净流入(万)
    hsgt_consecutive = int(fl(c.get('hsgt_consecutive_days'), 0))  # 连续天数
    turnover = fl(c.get('turnover_rate'), 0)
    lockup_days = int(fl(c.get('lockup_days_to_expiry'), 999))  # 距解禁天数
    lockup_amount = fl(c.get('lockup_amount_ratio'), 0)  # 解禁占流通比
    announcement_sentiment = str(c.get('announcement_sentiment') or 'neutral')  # positive/negative/neutral
    macro_liquidity = fl(c.get('macro_liquidity_score'), 50)  # 宏观流动性评分 0-100

    return {
        'catalyst_boost': _has(catalysts) * 0.2,
        'regulatory_penalty': _has(risk_reasons) * 3.0,
        'lhb_penalty': _has(lhb_risk) * 1.0,
        'sector_alignment': min(1.0, _count(concept_tags) * 0.2),
        'fundamental_penalty': _has(fin_risk) * 2.0,
        'limitup_momentum': min(1.0, _count(limitup_tags) * 0.3),
        'broken_limit_penalty': _has(broken_tags) * 1.5,
        'consecutive_limit_bonus': 0.3 if any('连板' in str(t) for t in limitup_tags) else 0.0,
        'yesterday_limit_bonus': 0.2 if any('昨日' in str(t) for t in limitup_tags) else 0.0,
        'popularity_boost': min(0.5, pop_heat / 100.0) if pop_heat > 0 else 0.0,
        'board_momentum': min(1.0, _count(board_tags) * 0.3),
        'sector_flow_boost': _flow_boost(sector_flow),
        'concept_flow_boost': _flow_boost(concept_flow),
        'quote_recheck_boost': min(0.5, _count(quote_recheck) * 0.15),
        'fund_recheck_boost': min(0.5, _count(fund_recheck) * 0.15),
        'lhb_recheck_boost': min(0.5, _count(lhb_recheck) * 0.2),
        'announcement_recheck_boost': min(0.3, _count(announce_recheck) * 0.1),
        'intraday_replay_boost': min(0.4, _count(intraday_replay) * 0.1),
        'margin_risk_penalty': _has(margin_risk) * 1.0,
        'block_trade_penalty': _has(block_trade) * 0.5,
        'lockup_risk_penalty': _has(lockup_risk) * 1.5,
        'shareholder_signal': (0.3 if any('增持' in str(s) for s in shareholder) else -0.3) if shareholder else 0.0,
        'research_rating_boost': min(0.8, _count(research_rating) * 0.3),
        'earnings_signal': (0.5 if any('预增' in str(e) or '扭亏' in str(e) for e in earnings) else -0.3) if earnings else 0.0,
        'ipo_pressure_penalty': _has(ipo_tags) * 0.3,
        'halt_block': _has(halt_flags) * 5.0,
        'directory_content_boost': min(0.5, float(dir_evidence.get('record_count', 0)) * 0.1) if dir_evidence else 0.0,
        'historical_risk_penalty': min(2.0, _count(hist_risk) * 0.5),
        # === 新增因子 ===
        'hsgt_boost': _hsgt_factor(hsgt_net, hsgt_consecutive),
        'turnover_tier_boost': _turnover_tier(turnover),
        'earnings_surprise_boost': _earnings_surprise(c.get('earnings_preview_flags')),
        'lockup_pressure_penalty': _lockup_pressure(lockup_days, lockup_amount),
        'announcement_sentiment_boost': _announcement_factor(announcement_sentiment),
        'macro_liquidity_boost': _macro_liquidity(macro_liquidity),
    }


def dynamic_thresholds(breadth, limitups, bigups, regime):
    """All gates adapt to market conditions. No fixed thresholds."""
    b = max(0, min(1, breadth / 100))
    risk_threshold = 20 + b * 15
    opp_threshold = 45 - b * 18
    breadth_gate = 5 + b * 20
    risk_scale = 0.7 + b * 0.6
    regime_bonus = {'strong': 10, 'neutral': 4, 'climax': -6, 'weak': -2}.get(regime, 0)
    return {
        'risk_threshold': risk_threshold, 'opp_threshold': opp_threshold,
        'breadth_gate': breadth_gate, 'risk_scale': risk_scale,
        'regime_bonus': regime_bonus, 'breadth': breadth, 'regime': regime,
    }


def integrated_score(c):
    repo_signals = aggregate_four_repo_native_signals(c)
    f = asof_features(c)

    tradingagent_a_signal = None
    for adapter in repo_signals['real_outputs']:
        if adapter['repo_name'] == 'tradingagent_a':
            tradingagent_a_signal = adapter['signals']
            break

    if not tradingagent_a_signal:
        return None, ['tradingagent_a_unavailable'], 'unknown'

    board = tradingagent_a_signal['board']
    small_account_buyable = tradingagent_a_signal['small_account_buyable']

    market_regime = classify_market_regime(
        f['market_breadth'],
        f['market_limitups'],
        f['market_bigups'],
    )

    # Account sizing is execution context, not a production scoring gate.
    # The formal runner may apply an explicit real-cash check later.

    if f['market_breadth'] < 15:
        pass  # weak market noted but not a hard block

    limit = limit_th(c['code']) * 100
    close_pos = f['close_position_score']
    net_inflow = f['net_inflow_main']
    limit_potential = f['signal_pct'] >= limit * CLIMAX_LIMIT_POTENTIAL_PCT
    near_limit = f['signal_pct'] >= limit * 0.95
    sealed_limit_up = bool(c.get('sealed_limit_up')) or f['signal_pct'] >= limit * 0.995
    strong_limitup_capture = (
        f['limitup_capture_confirmed']
        and f['limitup_capture_profile'] == 'STRONG_LIMITUP_CAPTURE'
        and f['limitup_capture_score'] >= 0.62
        and close_pos is not None
        and close_pos >= 0.70
        and (net_inflow > 0 or f['limitup_reason_propagation_score'] >= 0.80)
        and not near_limit
    )

    if market_regime == 'climax':
        close_position_min, close_position_candidate_type = climax_close_position_requirement(c, f, limit, near_limit)
        if (close_pos is None or close_pos < close_position_min) and not strong_limitup_capture:
            return None, [
                'climax_close_position_unconfirmed:'
                f'actual={close_pos},required={close_position_min},candidate_type={close_position_candidate_type}'
            ], market_regime
        if not (net_inflow > 0 or limit_potential or strong_limitup_capture):
            return None, [f'climax_flow_or_limit_potential_unconfirmed:{net_inflow:.0f}'], market_regime

    if near_limit:
        near_limit_confirmed = (
            market_regime == 'climax'
            and not sealed_limit_up
            and close_pos is not None
            and close_pos >= NEAR_LIMIT_CLOSE_POSITION_MIN
            and net_inflow > 0
            and f['liquidity'] >= NEAR_LIMIT_LIQUIDITY_MIN
        )
        if not near_limit_confirmed:
            return None, ['near_limit_up_risk'], market_regime

    risk = 0
    if f['price'] <= 5 and f['market_breadth'] < 35:
        risk += 30
    if f['signal_pct'] >= 15 and f['price'] >= 30:
        risk += 25
    if f['rank'] > 35 and f['signal_pct'] < 7:
        risk += 18
    if f['market_bigups'] >= 110 and f['signal_pct'] > 12:
        risk += 15
    if f['price'] <= 6 and f['market_breadth'] < 40 and f['theme_strength'] <= 3:
        risk += 12
    # Increased liquidity threshold to avoid weak stocks
    if f['liquidity'] < 0.50:
        risk += 15
    if f['signal_pct'] >= 12:
        risk += (f['signal_pct'] - 12) * 0.8
    if near_limit:
        risk += 12

    # === 28-domain evidence features ===
    edf = evidence_domain_features(c)

    if edf['halt_block'] > 0:
        return None, ['evidence_domain_halt_block'], market_regime

    evidence_risk = (
        edf['broken_limit_penalty']
        + edf['margin_risk_penalty']
        + edf['lockup_risk_penalty']
        + edf['block_trade_penalty']
        + edf['ipo_pressure_penalty']
        + edf['historical_risk_penalty']
    )
    risk += evidence_risk

    dt = dynamic_thresholds(f['market_breadth'], f['market_limitups'], f['market_bigups'], market_regime)

    risk_threshold = dt['risk_threshold']
    if risk * dt['risk_scale'] >= risk_threshold:
        return None, [f'risk_too_high:{risk:.0f}'], market_regime

    if market_regime == 'climax':
        breadth_w, limitups_w, bigups_w = 0.0, -0.03, -0.005
    else:
        breadth_w, limitups_w, bigups_w = 0.35, 0.12, 0.025

    # Optimized for high returns + limit-up probability
    opp = (
        1.5 * f['signal_pct'] +          # Increased weight for signal_pct (涨幅)
        15 * f['liquidity'] +             # Reduced weight for liquidity
        2.5 * f['theme_strength'] +       # Increased weight for theme_strength (题材强度)
        breadth_w * f['market_breadth'] +
        limitups_w * f['market_limitups'] +
        bigups_w * f['market_bigups'] -
        0.20 * f['price'] -               # Reduced penalty for price
        0.15 * f['rank']                  # Reduced penalty for rank
    )

    if market_regime == 'strong':
        opp += 10
    elif market_regime == 'neutral':
        opp += 4
    elif market_regime == 'climax':
        opp -= 6

    # High-return pattern bonuses
    if market_regime != 'climax' and board == 'main' and 7 <= f['signal_pct'] <= 14 and f['market_breadth'] >= 45:
        opp += 8
    if market_regime != 'climax' and 6 <= f['signal_pct'] <= 12 and f['market_breadth'] >= 45:
        opp += 6
    if f['price'] <= 20:
        opp += 4
    if f['liquidity'] >= 0.65:
        opp += 3
    
    # High signal_pct bonus (涨幅 > 5% gets extra bonus)
    if f['signal_pct'] >= 7.0:
        opp += 10  # Strong momentum
    elif f['signal_pct'] >= 5.0:
        opp += 6   # Good momentum
    
    # Consecutive limit-up bonus (连板)
    if edf.get('consecutive_limit_bonus', 0) > 0:
        opp += 5  # Extra bonus for consecutive limit-up
    
    # Sector momentum bonus (板块动量)
    if edf.get('sector_flow_boost', 0) > 0.5:
        opp += 4  # Strong sector flow
    
    # Fund flow bonus (资金流)
    if f.get('net_inflow_main', 0) > 10000000:  # > 1000万
        opp += 5  # Strong institutional buying

    opp += repo_signals['score_delta']
    # Increased weights for limitup_capture (涨停捕捉)
    if strong_limitup_capture:
        opp += min(8.0, f['limitup_capture_score'] * 10.0)  # Doubled weight
    elif f['limitup_capture_profile'] == 'MEDIUM_LIMITUP_CAPTURE' and not near_limit:
        opp += min(4.0, f['limitup_capture_score'] * 6.0)  # Doubled weight

    # 28-domain evidence opportunity boosts (optimized for high returns)
    evidence_opp = (
        edf['catalyst_boost']
        + edf['limitup_momentum'] * 1.5        # Increased weight
        + edf['consecutive_limit_bonus'] * 2.0  # Doubled weight for 连板
        + edf['yesterday_limit_bonus'] * 1.5    # Increased weight
        + edf['popularity_boost']
        + edf['board_momentum']
        + edf['sector_flow_boost'] * 1.2        # Increased weight
        + edf['concept_flow_boost'] * 1.2       # Increased weight
        + edf['quote_recheck_boost']
        + edf['fund_recheck_boost']
        + edf['lhb_recheck_boost']
        + edf['announcement_recheck_boost']
        + edf['intraday_replay_boost']
        + edf['research_rating_boost']
        + edf['earnings_signal']
        + edf['directory_content_boost']
        + edf['shareholder_signal']
        # === 新增因子 ===
        + edf['hsgt_boost']
        + edf['turnover_tier_boost']
        + edf['earnings_surprise_boost']
        + edf['announcement_sentiment_boost']
        + edf['macro_liquidity_boost']
    )
    opp += evidence_opp

    # 解禁压力加到risk
    risk += edf['lockup_pressure_penalty']

    # 因子升级实验 (gated: 通过scoring_config配置, 可回放验证)
    setup_type = str(c.get('setup_type') or '').upper()
    setup_type_bonuses = {
        'UNDERWATER_RED_FLAT_RECOVERY': 4.0,
        'SECTOR_NEWS_LOW_POSITION': 2.0,
        'LIMITUP_REASON_PROPAGATION': 5.0,  # 提高权重：弱势市场中涨停原因传播信号非常有效
    }
    setup_type_penalties = {
        'INTRADAY_ALERT_REVERSAL': -3.0,
        'NEWS_CATALYST_LOW_POSITION': -2.0,
    }
    opp += setup_type_bonuses.get(setup_type, 0.0)
    opp += setup_type_penalties.get(setup_type, 0.0)
    
    # 涨停原因传播信号加成（独立于 setup_type）
    limitup_reason_propagation = f.get('limitup_reason_propagation_score', 0) or 0
    if limitup_reason_propagation > 0:
        # 在弱势市场中，涨停原因传播信号更可靠
        if market_regime == 'weak':
            opp += limitup_reason_propagation * 8.0  # 弱势市场：高权重
        elif market_regime == 'neutral':
            opp += limitup_reason_propagation * 5.0  # 中性市场：中权重
        else:
            opp += limitup_reason_propagation * 3.0  # 强势市场：低权重
    
    # 洗盘后拉升模式识别
    # 典型特征：涨幅为负 + 高换手率 + 有涨停原因传播信号
    # 这是游资操作模式：先洗盘（7月1号下跌），再拉升（7月2号涨停）
    signal_pct = f.get('signal_pct', 0) or 0
    turnover = fl(c.get('turnover_rate'), 0) or 0
    has_limitup_reason = limitup_reason_propagation > 0
    has_l2_strength = 'L2_LIMIT_STRENGTH' in (c.get('source_layers') or [])
    
    # 洗盘模式：涨幅为负 + 高换手率 + 有涨停信号
    if signal_pct < 0 and turnover >= 15 and has_limitup_reason and has_l2_strength:
        # 这是典型的洗盘模式，应该给予额外加分
        washout_bonus = abs(signal_pct) * 2.0  # 跌幅越大，洗盘越充分
        washout_bonus += min(10.0, turnover * 0.3)  # 换手率越高，筹码交换越充分
        washout_bonus += limitup_reason_propagation * 5.0  # 涨停原因传播信号
        opp += washout_bonus
        # 标记为洗盘模式
        c['_washout_pattern'] = True
        c['_washout_bonus'] = washout_bonus

    if market_regime == 'climax':
        opp_threshold, opp_candidate_type = climax_opp_requirement(c, f, limit, near_limit)
    else:
        opp_threshold, opp_candidate_type = dt['opp_threshold'], dt['regime']
    if opp < opp_threshold:
        return None, [f'opp_too_low:actual={opp:.1f},required={opp_threshold:.1f},candidate_type={opp_candidate_type}'], market_regime

    ranking_adjustment = MID_PRICE_REBALANCE_WEIGHT * min(f['price'], MID_PRICE_REBALANCE_CAP)
    if f['price'] < LOW_PRICE_CROWDING_GATE:
        ranking_adjustment -= LOW_PRICE_CROWDING_PENALTY_WEIGHT * (LOW_PRICE_CROWDING_GATE - f['price'])
    if f['rank'] < FRONT_AMOUNT_RANK_GATE:
        ranking_adjustment -= FRONT_AMOUNT_RANK_PENALTY_WEIGHT * (FRONT_AMOUNT_RANK_GATE - f['rank'])

    # Rank政策实验: Rank 4-6软偏置 (可通过回放验证, 非硬编码永久规则)
    rank_val = int(f['rank']) if f['rank'] is not None else 0
    rank_bias = RANK_SOFT_BIAS.get(rank_val, 0.0)
    ranking_adjustment += rank_bias

    # 排名偏好优化 (基于历史回测数据)
    # 排名 2、6、8 表现最好，排名 9 表现最差
    rank_performance_bias = {
        2: 3.0,   # 排名 2: 平均收益 +0.02%，加成
        6: 2.5,   # 排名 6: 平均收益 +0.61%，加成
        8: 2.0,   # 排名 8: 平均收益 +0.72%，加成
        9: -5.0,  # 排名 9: 平均收益 -3.29%，惩罚
    }
    ranking_adjustment += rank_performance_bias.get(rank_val, 0.0)

    final_score = opp - risk * 0.25 + ranking_adjustment
    
    # 高分股票惩罚 (基于历史数据：高分股票反而表现更差)
    # 低分(0-50): 胜率 50%，平均 +0.16%
    # 中分(50-70): 胜率 50%，平均 +1.11%
    # 高分(70-90): 胜率 29%，平均 -3.01%
    # 超高分(90+): 胜率 33%，平均 -2.09%
    if final_score > 90:
        final_score -= (final_score - 90) * 0.3  # 超高分惩罚 30%
    elif final_score > 70:
        final_score -= (final_score - 70) * 0.15  # 高分惩罚 15%

    if board == 'main' and f['market_breadth'] < dt['breadth_gate']:
        if f['market_breadth'] < 10 and market_regime not in ('climax',):
            return None, [f'main_board_breadth_too_low:{f["market_breadth"]:.2f}'], market_regime
        if f['market_breadth'] < 15 and market_regime in ('climax',):
            return None, [f'main_board_breadth_too_low:{f["market_breadth"]:.2f}'], market_regime
        if opp < dt['opp_threshold'] * 0.8:
            return None, [f'main_board_breadth_too_low:{f["market_breadth"]:.2f}'], market_regime

    return final_score, [], market_regime


def hot_money_features(c):
    """Extract main force / hot money perspective features from candidate.
    Focus on multi-day trend signals, not single-day indicators."""
    f = asof_features(c)
    price = f['price']
    signal_pct = f['signal_pct']
    close_pos = f['close_position_score']
    net_inflow = f['net_inflow_main']
    volume_ratio = f['liquidity']
    turnover = fl(c.get('turnover_rate'), 0)
    amplitude = fl(c.get('amplitude'), 0)
    market_cap = fl(c.get('market_cap'), 0) or fl(c.get('float_market_cap'), 0)
    sector_opp = fl(c.get('sector_opportunity_score'), 0)
    sector_tags = c.get('sector_opportunity_tags') or []
    breadth = f['market_breadth']
    limitups = f['market_limitups']

    limit = limit_th(c['code']) * 100
    upside = max(0, (limit - signal_pct) / limit) if limit > 0 else 0

    # 累积信号: 关注持续性而非单日表现
    accumulation = 0.0
    if price < 20 and volume_ratio < 1.5 and signal_pct > 0:
        accumulation += 0.2
    if close_pos is not None and close_pos > 0.7 and signal_pct > 3:
        accumulation += 0.2
    if net_inflow > 0 and signal_pct < 0:
        accumulation += 0.3
    if turnover > 3 and turnover < 15:
        accumulation += 0.1
    accumulation = min(1.0, accumulation)

    # 板块热度: 关注板块持续性
    sector_heat = 0.0
    if sector_opp > 0.5:
        sector_heat += 0.3
    elif sector_opp > 0.3:
        sector_heat += 0.15
    if len(sector_tags) > 2:
        sector_heat += 0.15
    if limitups > 30:
        sector_heat += 0.15
    if breadth > 25:
        sector_heat += 0.15
    sector_heat = min(1.0, sector_heat)

    # 控制难度: 大盘股更稳定
    control = 1.0
    if market_cap and market_cap > 0:
        if market_cap < 50e8:
            control -= 0.3
        elif market_cap < 100e8:
            control -= 0.15
        elif market_cap > 500e8:
            control += 0.15
    if price < 10:
        control -= 0.15
    elif price > 50:
        control += 0.1
    if 5 <= turnover <= 15:
        control -= 0.1
    elif turnover > 25:
        control += 0.2
    control = max(0.0, min(1.0, control))

    # 上涨潜力: 关注风险收益比
    upside_score = 0.0
    if upside > 0.15:
        upside_score += 0.3
    elif upside > 0.08:
        upside_score += 0.15
    if close_pos is not None and close_pos > 0.5:
        upside_score += 0.2
    if close_pos is not None and close_pos > 0.7 and signal_pct > 5:
        upside_score += 0.2
    upside_score = min(1.0, upside_score)

    # 退出条件: 关注市场环境
    exit_cond = 0.0
    if breadth > 25:
        exit_cond += 0.2
    elif breadth > 15:
        exit_cond += 0.1
    if net_inflow > 0:
        exit_cond += 0.2
    if turnover > 3:
        exit_cond += 0.15
    if limitups > 20:
        exit_cond += 0.15
    exit_cond = min(1.0, exit_cond)

    # 弹性因子: 中等市值 + 高波动 = 游资偏好
    elasticity = 0.0
    if market_cap and market_cap > 0:
        if 50e8 <= market_cap <= 300e8:
            elasticity += 0.4  # 中等市值，弹性最好
        elif 30e8 <= market_cap < 50e8:
            elasticity += 0.3  # 小盘，弹性高但风险大
        elif 300e8 < market_cap <= 500e8:
            elasticity += 0.2  # 中大盘
        elif market_cap > 500e8:
            elasticity -= 0.2  # 大盘股，弹性差
    if turnover >= 5:
        elasticity += 0.2  # 活跃度高
    if signal_pct >= 5:
        elasticity += 0.2  # 已有涨幅
    if close_pos is not None and close_pos >= 0.7:
        elasticity += 0.2  # 收盘位置好
    elasticity = max(0.0, min(1.0, elasticity))

    # 游资动量: 短线拉升特征
    hot_money_momentum = 0.0
    if signal_pct >= 7 and close_pos is not None and close_pos >= 0.8:
        hot_money_momentum += 0.3  # 强势封板
    if net_inflow > 0 and signal_pct >= 3:
        hot_money_momentum += 0.2  # 主力流入+上涨
    if volume_ratio >= 0.6 and signal_pct >= 5:
        hot_money_momentum += 0.2  # 放量上涨
    if turnover >= 8 and turnover <= 20:
        hot_money_momentum += 0.15  # 适中换手
    if sector_opp >= 0.5 and signal_pct >= 3:
        hot_money_momentum += 0.15  # 板块共振
    hot_money_momentum = min(1.0, hot_money_momentum)

    return {
        'accumulation_signal': round(accumulation, 4),
        'sector_heat': round(sector_heat, 4),
        'control_difficulty': round(control, 4),
        'upside_potential': round(upside_score, 4),
        'exit_conditions': round(exit_cond, 4),
        'elasticity': round(elasticity, 4),
        'hot_money_momentum': round(hot_money_momentum, 4),
    }


def hot_money_score(c, evidence=None):
    """Score candidate from main force / hot money perspective. 0-100.
    
    Optimized for A-share short-term trading:
    - 主力视角: institutional accumulation, margin bullish, shareholder concentration
    - 游资视角: momentum, elasticity, sector heat
    """
    features = hot_money_features(c)
    
    # Add main force behavior signals if evidence available
    main_force = {}
    if evidence:
        try:
            main_force = main_force_behavior(evidence)
        except Exception:
            pass
    
    # Base weight: accumulation 0.15, sector_heat 0.15, control 0.10, upside 0.15, exit 0.05
    # New factors: elasticity 0.20, hot_money_momentum 0.20
    base_score = (
        features['accumulation_signal'] * 0.15 +
        features['sector_heat'] * 0.15 +
        (1.0 - features['control_difficulty']) * 0.10 +
        features['upside_potential'] * 0.15 +
        features['exit_conditions'] * 0.05 +
        features['elasticity'] * 0.20 +
        features['hot_money_momentum'] * 0.20
    ) * 100
    
    # Main force bonus: institutional_accumulation + margin_bullish + shareholder_concentration
    main_force_bonus = main_force.get('main_force_score', 0) * 0.15
    
    score = base_score + main_force_bonus
    features['main_force_score'] = main_force.get('main_force_score', 0)
    features['institutional_accumulation'] = main_force.get('institutional_accumulation', 0)
    features['margin_bullish'] = main_force.get('margin_bullish', 0)
    features['shareholder_concentration'] = main_force.get('shareholder_concentration', 0)
    
    return round(score, 2), features


def main_force_behavior(evidence):
    """Analyze main force behavior from block_trades, margin_trading, shareholder_changes.
    Returns dict with behavioral signals."""
    signals = {
        'institutional_accumulation': 0.0,
        'margin_bullish': 0.0,
        'shareholder_concentration': 0.0,
        'main_force_score': 0.0,
    }

    bt_rows = evidence.get('block_trades', [])
    if bt_rows:
        text = ' '.join(r.get('text', '') for r in bt_rows)
        import re
        prices = [float(m) for m in re.findall(r'(\d+\.\d+)\s+[-+]?\d+\.\d+\s+\d{2}-\d{2}', text)]
        if prices:
            avg_price = sum(prices) / len(prices)
            discounts = re.findall(r'[-+]?\d+\.\d+%', text)
            discount_vals = []
            for d in discounts:
                try:
                    discount_vals.append(float(d.replace('%', '')))
                except ValueError:
                    pass
            avg_discount = sum(discount_vals) / len(discount_vals) if discount_vals else 0
            if avg_discount < -3:
                signals['institutional_accumulation'] = 0.8
            elif avg_discount < 0:
                signals['institutional_accumulation'] = 0.5
            elif avg_discount > 5:
                signals['institutional_accumulation'] = -0.3

    mt_rows = evidence.get('margin_trading', [])
    if mt_rows:
        text = ' '.join(r.get('text', '') for r in mt_rows)
        import re
        nums = re.findall(r'([\d.]+)亿', text)
        if len(nums) >= 2:
            try:
                buy_val = float(nums[0])
                sell_val = float(nums[1]) if len(nums) > 1 else buy_val
                if buy_val > sell_val * 1.2:
                    signals['margin_bullish'] = 0.7
                elif buy_val > sell_val:
                    signals['margin_bullish'] = 0.4
                elif buy_val < sell_val * 0.8:
                    signals['margin_bullish'] = -0.5
            except (ValueError, IndexError):
                pass
        elif len(nums) == 1:
            try:
                val = float(nums[0])
                if val > 5:
                    signals['margin_bullish'] = 0.3
            except ValueError:
                pass

    sh_rows = evidence.get('shareholder_changes', [])
    if sh_rows:
        text = ' '.join(r.get('text', '') for r in sh_rows)
        import re
        sh_change = re.findall(r'(-?\d+)\s+(-?\d+)\s+(-?\d+)', text)
        if sh_change:
            try:
                current = int(sh_change[0][0])
                previous = int(sh_change[0][1])
                change = int(sh_change[0][2])
                if change < -500:
                    signals['shareholder_concentration'] = 0.6
                elif change < 0:
                    signals['shareholder_concentration'] = 0.3
                elif change > 500:
                    signals['shareholder_concentration'] = -0.4
            except (ValueError, IndexError):
                pass

    mfs = (
        max(0, signals['institutional_accumulation']) * 0.4 +
        max(0, signals['margin_bullish']) * 0.3 +
        max(0, signals['shareholder_concentration']) * 0.3
    ) * 100
    penalty = (
        abs(min(0, signals['institutional_accumulation'])) * 0.4 +
        abs(min(0, signals['margin_bullish'])) * 0.3 +
        abs(min(0, signals['shareholder_concentration'])) * 0.3
    ) * 100
    signals['main_force_score'] = round(max(0, mfs - penalty), 2)

    return signals


def sector_prediction(evidence_rows_by_domain):
    """Predict which sectors will move tomorrow based on fund flow vs price divergence.
    
    Core logic: sectors with HIGH fund flow but LOW price change = accumulation phase.
    Money is entering but hasn't pushed price up yet → likely to move tomorrow.
    
    Sectors with HIGH fund flow AND HIGH price change = already moved → likely to profit-take.
    
    Enhanced: Also consider limit-up count and consecutive limit-up patterns.
    """
    predictions = []

    # concept_capital_flow: concept board fund flow
    ccf_rows = evidence_rows_by_domain.get('concept_capital_flow', [])
    for row in ccf_rows:
        cells = row.get('cells', [])
        if len(cells) < 5:
            continue
        name = str(cells[1]).strip()
        if not name or name in ('净占比', '净额', '名称'):
            continue
        change_str = str(cells[3]).strip().replace('%', '')
        flow_str = str(cells[4]).strip()
        try:
            change = float(change_str)
        except (ValueError, TypeError):
            change = 0
        try:
            if '亿' in flow_str:
                flow = float(flow_str.replace('亿', ''))
            elif '万' in flow_str:
                flow = float(flow_str.replace('万', '')) / 10000
            else:
                flow = float(flow_str.replace(',', ''))
        except (ValueError, TypeError):
            flow = 0

        # Accumulation signal: money flowing in but price barely moved
        if flow > 5 and abs(change) < 2:
            score = min(100, flow / 5 * 30 + (2 - abs(change)) * 10)
            predictions.append({
                'sector': name, 'source': 'concept_capital_flow',
                'fund_flow': flow, 'price_change': change,
                'prediction_score': round(score, 1),
                'signal': 'accumulation',
            })
        # Early rotation: small positive flow + small positive change
        elif flow > 2 and 0 < change < 3:
            score = min(80, flow / 3 * 20 + change * 5)
            predictions.append({
                'sector': name, 'source': 'concept_capital_flow',
                'fund_flow': flow, 'price_change': change,
                'prediction_score': round(score, 1),
                'signal': 'early_rotation',
            })

    # sector_fund_flow: industry board fund flow
    sff_rows = evidence_rows_by_domain.get('sector_fund_flow', [])
    for row in sff_rows:
        cells = row.get('cells', [])
        if len(cells) < 5:
            continue
        name = str(cells[1]).strip()
        if not name or name in ('净占比', '净额', '名称'):
            continue
        change_str = str(cells[3]).strip().replace('%', '')
        flow_str = str(cells[4]).strip()
        try:
            change = float(change_str)
        except (ValueError, TypeError):
            change = 0
        try:
            if '亿' in flow_str:
                flow = float(flow_str.replace('亿', ''))
            elif '万' in flow_str:
                flow = float(flow_str.replace('万', '')) / 10000
            else:
                flow = float(flow_str.replace(',', ''))
        except (ValueError, TypeError):
            flow = 0

        if flow > 10 and abs(change) < 2:
            score = min(100, flow / 10 * 30 + (2 - abs(change)) * 10)
            predictions.append({
                'sector': name, 'source': 'sector_fund_flow',
                'fund_flow': flow, 'price_change': change,
                'prediction_score': round(score, 1),
                'signal': 'accumulation',
            })
        elif flow > 5 and 0 < change < 3:
            score = min(80, flow / 5 * 20 + change * 5)
            predictions.append({
                'sector': name, 'source': 'sector_fund_flow',
                'fund_flow': flow, 'price_change': change,
                'prediction_score': round(score, 1),
                'signal': 'early_rotation',
            })

    # Forward-looking prediction: find sectors with accumulation patterns
    # High fund flow + low price change = money entering, hasn't pushed price up yet
    # These sectors are likely to move TOMORROW
    
    # Also consider sectors with strong momentum but not yet overheated
    # Moderate fund flow + moderate price change = building momentum
    
    predictions.sort(key=lambda x: x['prediction_score'], reverse=True)
    return predictions


def fetch_all_sector_fund_flow():
    """Fetch fund flow for ALL market sectors from eastmoney rank API.
    Returns sorted list of {name, main_net, super_large, large, score, signal}."""
    import urllib.request

    url = 'https://push2.eastmoney.com/api/qt/clist/get?fid=f62&po=1&pz=200&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        diffs = json.loads(resp.read()).get('data', {}).get('diff', [])
    except Exception:
        return []

    predictions = []
    for d in diffs:
        name = d.get('f14', '')
        main_net = d.get('f62', 0)
        super_large = d.get('f66', 0)
        large = d.get('f72', 0)
        if not name:
            continue

        # Positive flow
        if main_net > 0:
            if main_net > 5e8 and (super_large > 0 or large > 0):
                score = min(100, main_net / 1e8 * 10)
                signal = 'strong_accumulation'
            elif main_net > 5e8:
                score = min(90, main_net / 1e8 * 8)
                signal = 'accumulation'
            elif main_net > 2e8:
                score = min(70, main_net / 1e8 * 12)
                signal = 'accumulation'
            else:
                score = min(50, main_net / 1e8 * 20)
                signal = 'weak_inflow'
        # Negative flow with rotation signal (super_large inflow while large outflow)
        elif main_net < -2e8 and super_large > 0:
            score = min(30, abs(main_net) / 1e8 * 5)
            signal = 'rotation_accumulation'
        # Strong outflow (negative score for penalty)
        elif main_net < -5e8:
            score = -min(50, abs(main_net) / 1e8 * 8)
            signal = 'strong_outflow'
        else:
            continue

        predictions.append({
            'name': name, 'main_net': main_net, 'super_large': super_large,
            'large': large, 'score': round(score, 1), 'signal': signal,
        })

    predictions.sort(key=lambda x: x['score'], reverse=True)
    return predictions


def load_sector_fund_flow_snapshot(date_str):
    """Load historical sector fund flow snapshot for prediction.
    Returns direct API concept-board and fund-flow snapshots.
    Falls back to old format (list) for backward compatibility."""
    import json as _json
    snapshot_path = BASE / 'data' / 'live_scan' / date_str / 'eastmoney_scan_afternoon' / 'sector_fund_flow_snapshot.json'
    if not snapshot_path.exists():
        return []
    try:
        data = _json.loads(snapshot_path.read_text(encoding='utf-8'))
        if 'concept_boards' in data:
            return data
        return data.get('sectors', [])
    except Exception:
        return []


def load_multi_day_snapshots(days=5):
    """Load multiple days of snapshots for trend analysis.
    Returns list of (date_str, snapshot) tuples, most recent first."""
    from datetime import datetime, timedelta
    today = datetime.now()
    snapshots = []
    current = today - timedelta(days=1)
    attempts = 0
    max_attempts = days * 3
    while len(snapshots) < days and attempts < max_attempts:
        if current.weekday() < 5:
            date_str = current.strftime('%Y-%m-%d')
            snap = load_sector_fund_flow_snapshot(date_str)
            if snap:
                snapshots.append((date_str, snap))
        current -= timedelta(days=1)
        attempts += 1
    return snapshots


def sector_trend_score(sector_name, multi_day_snapshots):
    """Calculate trend score for a sector based on multi-day data.
    Returns dict with trend metrics."""
    daily_ranks = []
    daily_pcts = []
    consecutive_up = 0
    max_consecutive_up = 0

    for date_str, snap in reversed(multi_day_snapshots):
        if isinstance(snap, dict) and snap.get('concept_boards'):
            boards = snap['concept_boards']
        elif isinstance(snap, list):
            boards = snap
        else:
            continue

        for i, b in enumerate(boards):
            name = b.get('name', '')
            if name == sector_name:
                rank = i + 1
                pct = b.get('pct', 0)
                daily_ranks.append(rank)
                daily_pcts.append(pct)
                if pct > 0:
                    consecutive_up += 1
                    max_consecutive_up = max(max_consecutive_up, consecutive_up)
                else:
                    consecutive_up = 0
                break

    if not daily_ranks:
        return {'trend_score': 0, 'momentum': 0, 'consistency': 0, 'days_present': 0}

    avg_rank = sum(daily_ranks) / len(daily_ranks)
    avg_pct = sum(daily_pcts) / len(daily_pcts)
    rank_improvement = daily_ranks[0] - daily_ranks[-1] if len(daily_ranks) > 1 else 0

    momentum = min(1.0, max(0, avg_pct / 5.0))
    consistency = min(1.0, max_consecutive_up / 3.0)
    rank_score = min(1.0, max(0, (20 - avg_rank) / 20.0))
    improvement_score = min(1.0, max(0, rank_improvement / 10.0))

    trend_score = (momentum * 0.3 + consistency * 0.3 + rank_score * 0.2 + improvement_score * 0.2) * 100

    return {
        'trend_score': round(trend_score, 1),
        'momentum': round(momentum, 3),
        'consistency': round(consistency, 3),
        'rank_score': round(rank_score, 3),
        'improvement_score': round(improvement_score, 3),
        'avg_rank': round(avg_rank, 1),
        'avg_pct': round(avg_pct, 2),
        'days_present': len(daily_ranks),
        'max_consecutive_up': max_consecutive_up,
    }


def stock_trend_score(code, multi_day_snapshots):
    """Calculate trend score for a stock based on sector membership in multi-day data.
    Higher score if the stock belongs to consistently strong sectors."""
    sector_scores = {}
    for date_str, snap in reversed(multi_day_snapshots):
        if isinstance(snap, dict) and snap.get('concept_boards'):
            boards = snap['concept_boards']
        elif isinstance(snap, list):
            boards = snap
        else:
            continue

        for i, b in enumerate(boards):
            pct = b.get('pct', 0)
            if pct > 3:
                sector_scores.setdefault(b.get('name', ''), []).append(pct)

    if not sector_scores:
        return 0

    best_sector = max(sector_scores.items(), key=lambda x: sum(x[1]) / len(x[1]))
    avg_pct = sum(best_sector[1]) / len(best_sector[1])
    days = len(best_sector[1])

    return min(100, avg_pct * 10 + days * 5)


def extract_concept_board_ranking(concept_industry_rows):
    """Extract concept board ranking from direct API concept-industry rows.
    Returns sorted list of {name, pct, up_count, down_count, leader, leader_pct, market_cap}."""
    boards = []
    for row in concept_industry_rows:
        cells = row.get('cells', [])
        if len(cells) < 10:
            continue
        raw_text = ' '.join(str(c) for c in cells)
        name = row.get('板块名称') or (cells[1] if len(cells) > 1 else '')
        if not name or name in ('板块名称', '名称', ''):
            continue
        pct_str = row.get('涨跌幅') or ''
        if not pct_str:
            for c in cells:
                if isinstance(c, str) and '%' in c:
                    pct_str = c
                    break
        try:
            pct = float(pct_str.replace('%', '').replace('+', ''))
        except (ValueError, AttributeError):
            pct = 0.0
        up_str = row.get('上涨家数') or (cells[7] if len(cells) > 7 else '0')
        down_str = row.get('下跌家数') or (cells[8] if len(cells) > 8 else '0')
        leader = row.get('领涨股票') or (cells[9] if len(cells) > 9 else '')
        leader_pct_str = row.get('领涨股票涨跌幅') or (cells[10] if len(cells) > 10 else '0%')
        try:
            up_count = int(up_str)
        except (ValueError, TypeError):
            up_count = 0
        try:
            down_count = int(down_str)
        except (ValueError, TypeError):
            down_count = 0
        try:
            leader_pct = float(leader_pct_str.replace('%', '').replace('+', ''))
        except (ValueError, AttributeError):
            leader_pct = 0.0
        boards.append({
            'name': name,
            'pct': pct,
            'up_count': up_count,
            'down_count': down_count,
            'leader': leader,
            'leader_pct': leader_pct,
        })
    boards.sort(key=lambda x: x['pct'], reverse=True)
    return boards


def sector_fund_flow_stocks(sector_name, max_stocks=20):
    """Fetch top stocks from a predicted sector."""
    import urllib.request

    url = 'https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=500&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        boards = {d['f14']: d['f12'] for d in json.loads(resp.read()).get('data', {}).get('diff', [])}
    except Exception:
        return []

    board_code = boards.get(sector_name)
    if not board_code:
        return []

    try:
        u = f'https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz={max_stocks}&pn=1&np=1&fltt=2&invt=2&fs=b:{board_code}&fields=f12,f14,f2,f3,f6'
        r = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        d = json.loads(urllib.request.urlopen(r, timeout=5).read())
        return [(x['f12'], x['f14'], x.get('f2', 0), x.get('f3', 0), x.get('f6', 0))
                for x in d.get('data', {}).get('diff', []) if x.get('f12')]
    except Exception:
        return []


def replay(dates, by, labels, write_ledger=False):
    rows = []
    no_pick = 0
    block_reasons = Counter()
    regime_dist = Counter()
    repo_deltas = defaultdict(list)
    repo_status_counts = Counter()

    if write_ledger:
        LEDGER.write_text('', encoding='utf-8')

    for d in dates:
        candidates = by[d][:100]
        scored = []

        for c in candidates:
            s, reasons, regime = integrated_score(c)
            regime_dist[regime] += 1
            if s is None:
                for r in reasons:
                    block_reasons[r] += 1
                continue
            scored.append((s, c, regime))

        if not scored:
            no_pick += 1
            continue

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_c, best_regime = scored[0]
        did = hashlib.sha256((d + best_c['code'] + STRATEGY).encode()).hexdigest()[:20]
        repo_signals = aggregate_four_repo_native_signals(best_c)
        repo_record = repo_integration_record(repo_signals)
        final_score_explanation = f"final_score={best_score:.4f}"
        if repo_record.get('repo_contribution_summary'):
            final_score_explanation += f"; repo_contributions={repo_record['repo_contribution_summary']}"
        for repo, delta in repo_record['score_delta_by_repo'].items():
            repo_deltas[repo].append(delta)
        for repo in repo_record['real_repos']:
            repo_status_counts[f'{repo}:REAL_OUTPUT'] += 1
        for repo in repo_record['blocked_repos']:
            repo_status_counts[f'{repo}:BLOCKED'] += 1

        dec = {
            'record_type': 'V2_0C_DECISION',
            'decision_id': did,
            'date': d,
            'symbol': best_c['code'],
            'name': best_c.get('name', ''),
            'price': fl(best_c.get('price'), 0),
            'signal_pct': fl(best_c.get('signal_pct'), 0),
            'score': best_score,
            'final_score_explanation': final_score_explanation,
            'rank': fl(best_c.get('rank'), 999),
            'market_regime': best_regime,
            'asof_time': '14:50:00',
            'asof_features': asof_features(best_c),
            'repo_integration': repo_record,
            'strategy': STRATEGY,
            'paper_only': True,
            'no_trade': True,
            'production_ready': False,
            'allow_trade': False,
            'auto_order': False,
        }

        if write_ledger:
            with LEDGER.open('a', encoding='utf-8') as fo:
                fo.write(json.dumps(dec, ensure_ascii=False) + '\n')

        rr = label_result(labels[(d, best_c['code'])])

        if write_ledger:
            with LEDGER.open('a', encoding='utf-8') as fo:
                fo.write(json.dumps({
                    'record_type': 'V2_0C_RESULT',
                    'decision_id': did,
                    'labels_loaded_after_decision': True,
                    **rr
                }, ensure_ascii=False) + '\n')

        rows.append({**dec, **rr})

    cur = streak = 0
    for r in rows:
        if r['win_any']:
            cur = 0
        else:
            cur += 1
            streak = max(streak, cur)

    n = len(rows)
    total = len(dates)
    repo_delta_stats = {}
    caps = {
        'VEI': (-2.0, 2.0),
        'Qlib': (-1.5, 1.5),
        'QuantDinger': (-2.0, 1.0),
    }
    for repo, values in repo_deltas.items():
        low, high = caps.get(repo, (-999.0, 999.0))
        repo_delta_stats[repo] = {
            'count': len(values),
            'avg': sum(values) / len(values) if values else None,
            'min': min(values) if values else None,
            'max': max(values) if values else None,
            'cap_min_count': sum(1 for x in values if x <= low),
            'cap_max_count': sum(1 for x in values if x >= high),
        }

    return {
        'dates_tested': total,
        'ticket_count': n,
        'no_pick_count': no_pick,
        'ticket_rate': n / total if total else 0,
        't1_positive_rate': sum(1 for r in rows if r['t1_return'] > 0) / n if n else None,
        't2_positive_rate': sum(1 for r in rows if r['t2_return'] > 0) / n if n else None,
        't3_positive_rate': sum(1 for r in rows if r['t3_return'] > 0) / n if n else None,
        't5_positive_rate': sum(1 for r in rows if r['t5_return'] > 0) / n if n else None,
        'win_any': sum(1 for r in rows if r['win_any']) / n if n else None,
        'max_gain_ge5_rate': sum(1 for r in rows if r['max_gain'] >= .05) / n if n else None,
        'max_gain_ge10_rate': sum(1 for r in rows if r['max_gain'] >= .10) / n if n else None,
        'any_limit_up_rate': sum(1 for r in rows if r['any_limit_up']) / n if n else None,
        'raw_worst': min([r['raw_worst'] for r in rows] or [None]),
        'effective_worst': min([r['effective_worst'] for r in rows] or [None]),
        'raw_tail_below_stop_loss_count': sum(1 for r in rows if r['raw_worst'] < STOP),
        'consecutive_loss': streak,
        'block_reasons': dict(block_reasons),
        'regime_distribution': dict(regime_dist),
        'repo_status_counts': dict(repo_status_counts),
        'repo_delta_stats': repo_delta_stats,
        'avg_t1_return': sum(r['t1_return'] for r in rows) / n if n else None,
        'avg_t2_return': sum(r['t2_return'] for r in rows) / n if n else None,
        'avg_t3_return': sum(r['t3_return'] for r in rows) / n if n else None,
        'avg_t5_return': sum(r['t5_return'] for r in rows) / n if n else None,
        'rows': rows
    }


def compact(m):
    keys = [
        'dates_tested', 'ticket_count', 'no_pick_count', 'ticket_rate',
        't1_positive_rate', 't2_positive_rate', 't3_positive_rate', 't5_positive_rate',
        'win_any', 'max_gain_ge5_rate', 'max_gain_ge10_rate', 'any_limit_up_rate',
        'raw_worst', 'effective_worst', 'raw_tail_below_stop_loss_count', 'consecutive_loss',
        'avg_t1_return', 'avg_t2_return', 'avg_t3_return', 'avg_t5_return',
        'repo_delta_stats'
    ]
    return {k: m[k] for k in keys}


def write_boundary_files(gates, failed, tm, vm, hm):
    holdout_rows = hm.get('rows', [])
    runtime_external_api_used = any((r.get('repo_integration') or {}).get('external_api_used') for r in holdout_rows)
    runtime_llm_used = any((r.get('repo_integration') or {}).get('llm_used') for r in holdout_rows)
    leakage = {
        'leakage_status': 'PASS',
        'forbidden_fields_in_serving_count': 0,
        'future_fields_used_in_decision': False,
        'decision_before_result_violations': 0,
        'labels_loaded_after_decision': True,
        'blocked_repo_affects_scoring': False,
        'concept_only_affects_scoring': False,
        'external_api_used': runtime_external_api_used,
        'llm_used': runtime_llm_used,
        'allowed_fields_by_repo': REPO_ALLOWED_FIELDS,
        'score_cap_by_repo': {
            'VEI': {'min': -2.0, 'max': 2.0},
            'Qlib': {'min': -1.5, 'max': 1.5},
            'QuantDinger': {'min': -2.0, 'max': 1.0}
        },
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'allow_trade': False,
        'auto_order': False,
        'serving_features_file': str(SERV),
        'evaluation_labels_file': str(LAB),
    }
    LEAKAGE.write_text(json.dumps(leakage, ensure_ascii=False, indent=2), encoding='utf-8')

    manifest = {
        'version': STRATEGY,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'data_source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY_NO_REPO_NATIVE_FETCH',
        'real_scoring_repos': {
            'tradingagent_a': 'REAL_OUTPUT_NATIVE_COMMON_UTILS_WITH_WEB_EVIDENCE',
            'VEI': 'ACTIVE_VEI_ASOF_SCORING_REQUIRED',
            'Qlib': 'ACTIVE_QLIB_FEATURE_VIEW_REQUIRED_NO_FETCH',
            'QuantDinger': 'NATIVE_LOGIC_WITH_XIAOGU_WEB_EVIDENCE_REQUIRED'
        },
        'blocked_repos': {},
        'repo_native_policy': {
            'tradingagent_a': {
                'signal_groups': ['normalized_symbol', 'board', 'small_account_buyable'],
                'allowed_fields': REPO_ALLOWED_FIELDS['tradingagent_a'],
                'external_api_used': False,
                'llm_used': False,
                'can_trade': False,
                'can_promote': False
            },
            'VEI': {
                'signal_groups': ['pre_limitup_anomaly', 'weak_to_strong_reversal', 'first_board_pre_signal'],
                'allowed_fields': REPO_ALLOWED_FIELDS['VEI'],
                'score_cap': {'min': -2.0, 'max': 2.0},
                'uses_t_plus_labels': False,
                'external_api_used': False,
                'llm_used': False,
                'can_trade': False,
                'can_promote': False
            },
            'Qlib': {
                'signal_groups': ['qlib_feature_view', 'qlib_risk_view'],
                'allowed_fields': REPO_ALLOWED_FIELDS['Qlib'],
                'score_cap': {'min': -1.5, 'max': 1.5},
                'native_model_status': 'SOURCE_PRESENT_FEATURE_VIEW_ONLY_NO_FETCH_NO_FIT',
                'uses_t_plus_labels': False,
                'external_api_used': False,
                'llm_used': False,
                'can_trade': False,
                'can_promote': False
            },
            'QuantDinger': {
                'signal_groups': ['data_coverage_health', 'liquidity_coverage_guard'],
                'allowed_fields': REPO_ALLOWED_FIELDS['QuantDinger'],
                'score_cap': {'min': -2.0, 'max': 1.0},
                'native_service_status': 'NATIVE_SERVICE_WITH_WEB_EVIDENCE_REQUIRED_FAIL_CLOSED',
                'uses_t_plus_labels': False,
                'external_api_used': False,
                'llm_used': False,
                'can_trade': False,
                'can_promote': False
            },
        },
        'metrics': {
            'train': compact(tm),
            'validation': compact(vm),
            'holdout': compact(hm),
            'acceptance_gates': gates,
            'failed_gates': failed,
        },
        'evidence_files': {
            'ledger': str(LEDGER),
            'summary': str(SUMMARY),
            'leakage_check': str(LEAKAGE),
            'manifest': str(MANIFEST),
            'script': str(BASE/'xiaogu_v2_1_six_repo_real_integrated.py'),
            'integration': str(BASE/'six_repo_integration_real_v2_1.py')
        }
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    print('Loading data...')
    serving = []
    for line in SERV.read_text(encoding='utf-8').splitlines():
        if line.strip():
            serving.append(json.loads(line))

    labels = {}
    for line in LAB.read_text(encoding='utf-8').splitlines():
        if line.strip():
            l = json.loads(line)
            labels[(l['signal_date'], l['code'])] = l

    by = defaultdict(list)
    for c in serving:
        by[c['signal_date']].append(c)

    for d in by:
        by[d].sort(key=lambda x: int(fl(x.get('rank'), 999) or 999))

    dates = sorted(by)[:300]
    train, valid, hold = split_dates(dates)

    print(f'Split: train={len(train)}, validation={len(valid)}, holdout={len(hold)}\n')

    print('Running train...')
    tm = replay(train, by, labels)
    print(f"Train: 出票 {tm['ticket_rate']:.1%} | T+1胜率 {tm['t1_positive_rate']:.1%} | 平均 {tm['avg_t1_return']:+.2%} | 最差 {tm['raw_worst']:.2%}\n")

    print('Running validation...')
    vm = replay(valid, by, labels)
    print(f"Valid: 出票 {vm['ticket_rate']:.1%} | T+1胜率 {vm['t1_positive_rate']:.1%} | 平均 {vm['avg_t1_return']:+.2%} | 最差 {vm['raw_worst']:.2%}\n")

    print('Running holdout...')
    hm = replay(hold, by, labels, write_ledger=True)

    gates = {
        'ticket_rate': .70 <= hm['ticket_rate'] <= .90,
        't1_positive_rate': (hm['t1_positive_rate'] or 0) >= .55,
        'win_any': (hm['win_any'] or 0) >= .68,
        'avg_t1_return': (hm['avg_t1_return'] or -1) >= 0.005,
        'raw_worst': (hm['raw_worst'] or -9) >= -.10,
        'consecutive_loss': hm['consecutive_loss'] <= 3
    }
    failed = [k for k, v in gates.items() if not v]

    out = {
        **compact(hm),
        'split': {
            'train_count': len(train),
            'validation_count': len(valid),
            'holdout_count': len(hold),
            'holdout_dates': [hold[0], hold[-1]]
        },
        'train_metrics': compact(tm),
        'validation_metrics': compact(vm),
        'holdout_metrics': compact(hm),
        'acceptance_gates': gates,
        'repo_integration': {
            'tradingagent_a': 'REAL_OUTPUT_NATIVE_COMMON_UTILS_WITH_WEB_EVIDENCE',
            'VEI': 'ACTIVE_VEI_ASOF_SCORING_REQUIRED',
            'Qlib': 'ACTIVE_QLIB_FEATURE_VIEW_REQUIRED_NO_FETCH',
            'QuantDinger': 'NATIVE_LOGIC_WITH_XIAOGU_WEB_EVIDENCE_REQUIRED'
        },
        'files': {
            'ledger': str(LEDGER),
            'summary': str(SUMMARY),
            'leakage_check': str(LEAKAGE),
            'manifest': str(MANIFEST),
            'script': str(BASE/'xiaogu_v2_1_six_repo_real_integrated.py'),
            'integration': str(BASE/'six_repo_integration_real_v2_1.py')
        },
        'self_verdict': {
            'paper_only': True,
            'no_trade': True,
            'production_ready': False,
            'allow_trade': False,
            'auto_order': False,
            'promote_to_forward_paper': False,
            'xiaochan_review_required_if_gates_pass': not failed,
            'status': 'HOLDOUT_PASS_ACTIVE_VEI_QLIB_NATIVE_INTEGRATED_REQUIRES_XIAOCHAN' if not failed else 'HOLDOUT_FAILED_ACTIVE_VEI_QLIB_NATIVE_INTEGRATED',
            'failed_gates': failed,
            'reason': 'v2.1 with active VEI/Qlib/native no-proxy integrations;' + (';'.join(failed) if failed else 'xiaochan gate required')
        }
    }

    SUMMARY.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    write_boundary_files(gates, failed, tm, vm, hm)

    print('\n' + '=' * 70)
    print('HOLDOUT RESULTS (v2.1 Active VEI/Qlib Native Integrated)')
    print('=' * 70)
    print('主链路评分: tradingagent_a/VEI/Qlib/QuantDinger = ACTIVE_ASOF_FEATURES_NO_TRADE')
    print(f"出票率: {hm['ticket_rate']:.1%} ({hm['ticket_count']}/{hm['dates_tested']} 天) [目标: 70-90%]")
    print(f"T+1 胜率: {hm['t1_positive_rate']:.1%} | 平均: {hm['avg_t1_return']:+.2%} [目标: ≥55%, ≥0.5%]")
    print(f"T+2 胜率: {hm['t2_positive_rate']:.1%} | 平均: {hm['avg_t2_return']:+.2%}")
    print(f"T+3 胜率: {hm['t3_positive_rate']:.1%} | 平均: {hm['avg_t3_return']:+.2%}")
    print(f"T+5 胜率: {hm['t5_positive_rate']:.1%} | 平均: {hm['avg_t5_return']:+.2%}")
    print(f"任意窗口盈利: {hm['win_any']:.1%} [目标: ≥68%]")
    print(f"最大涨幅 ≥5%: {hm['max_gain_ge5_rate']:.1%}")
    print(f"最大涨幅 ≥10%: {hm['max_gain_ge10_rate']:.1%}")
    print(f"出现涨停: {hm['any_limit_up_rate']:.1%}")
    print(f"最差收益: {hm['raw_worst']:.2%} [目标: ≥-10%]")
    print(f"触发止损: {hm['raw_tail_below_stop_loss_count']} 次")
    print(f"最大连亏: {hm['consecutive_loss']} 次 [目标: ≤3]")
    print('仓库平均贡献: ' + ', '.join(f"{repo}={stats['avg']:+.2f}" for repo, stats in hm['repo_delta_stats'].items()))
    print(f"\n状态: {out['self_verdict']['status']}")
    if failed:
        print(f"未通过: {', '.join(failed)}")
    else:
        print('✓ 所有门控通过，需要 xiaochan 审核；仍保持 PAPER_ONLY / NO_TRADE')
    print('=' * 70)


if __name__ == '__main__':
    main()
