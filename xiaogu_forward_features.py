#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-owner extraction from the production forward runner.

The production entry remains ``xiaogu_forward_runner.py``. This module only
owns the responsibility named in its filename and is host-bound so existing
imports and test monkeypatches retain their behavior.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
import json
import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xiaogu_forward_host_binding import create_host_binding

_HOST = None
REQUIRED_FROM_HOST = ('BASE', 'LOCKED_SAFETY', 'PRIMARY_RETURN_FIELD', 'PRIMARY_TRADE_HORIZON', 'RESEARCH_BASKET_SIZE', 'SCORING_CONFIG_DEFAULTS', 'TRADE_MODE', '_cached_structured_signal_profile', '_parse_date', '_positive_numeric', 'broken_limitup_continuation_exception', 'candidate_score_value', 'fetch_candidate_fund_flow_live', 'get_scoring_config_snapshot', 'is_routine_regulatory_block', 'opportunity_hard_block_reason', 'paper_pick_eligibility_profile', 'regulatory_hard_block_reason', 'repo_contribution_context', 'safe_float', 'safe_int', 'scan_summary_paths', 'symbol_for')

bind_host, _inject_host, _with_host = create_host_binding(
    globals(), REQUIRED_FROM_HOST, preserve_existing_on_missing=True,
)

_SECTOR_NAMES_CACHE: List[str] = []
_NEWS_CACHE: Optional[Tuple[List[Dict], Dict[str, float]]] = None
_NEWS_CACHE_DATE: str = ''
MAINLINE_THEME_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    '贵金属': ('贵金属', '黄金', '白银', '金银', '金矿', '有色金属'),
    '油气': ('油气', '石油', '原油', '天然气', '油服', '海油', '炼化'),
    '有色': ('有色', '有色金属', '铜', '铝', '锌', '铅', '锡', '小金属', '锂'),
    '电力': ('电力', '火电', '水电', '电网', '发电'),
    '煤炭': ('煤炭', '煤', '焦煤', '动力煤'),
    '半导体': ('半导体', '芯片', '集成电路', '光刻'),
}

def _parse_flow_amount(flow_str: str) -> float:
    import re as _re
    m = _re.match(r'([-\d.]+)(亿|万)?', str(flow_str).strip())
    if not m:
        return 0.0
    val = float(m.group(1))
    if m.group(2) == '万':
        val /= 10000
    return val

def build_capital_flow_lookup(evidence_by_stock: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    precomputed = {}
    import pathlib as _pl
    for p in sorted(_pl.Path('data/live_scan').rglob('xiaogu_scan_summary.json'), reverse=True):
        try:
            import json as _json
            ev = _json.loads(p.read_text())
            if 'stock_capital_flow_map' in ev:
                precomputed = ev['stock_capital_flow_map']
                break
        except Exception:
            continue

    if precomputed:
        return precomputed

    ccf = {}
    sff = {}

    import re as _re
    stock_flows = {}
    for sym, evidence in evidence_by_stock.items():
        if sym.startswith('_'):
            continue
        ci_rows = evidence.get('concept_industry', []) if isinstance(evidence, dict) else []
        tags = set()
        for r in ci_rows:
            text = r.get('text', '') if isinstance(r, dict) else ''
            parts = text.split()
            if parts:
                tags.add(parts[0])
            for tag in _re.findall(r'[\u4e00-\u9fa5A-Za-z0-9]{2,10}(?:\u6982\u5ff5|\u677f\u5757|\u82af\u7247|\u7535\u5b50|\u901a\u4fe1|\u79d1\u6280|\u5143\u4ef6|\u5238\u5546|\u53c2\u80a1)', text):
                tags.add(tag)
            for tag in _re.findall(r'([\u4e00-\u9fa5]{2,6}[\u2160\u2161\u2162\u2163]?)\s', text):
                if len(tag) >= 2:
                    tags.add(tag)

        best_concept_flow = 0.0
        best_sector_flow = 0.0
        for tag in tags:
            for name, data in ccf.items():
                if tag in name or name in tag:
                    best_concept_flow = max(best_concept_flow, _parse_flow_amount(data['flow']))
            for name, data in sff.items():
                if tag in name or name in tag:
                    best_sector_flow = max(best_sector_flow, _parse_flow_amount(data['flow']))

        stock_flows[sym] = {
            'concept_flow_100m': best_concept_flow,
            'sector_flow_100m': best_sector_flow,
            'tags': list(tags)[:5],
        }
    return stock_flows

def archetype_score_adjustments(candidate: Dict[str, Any]) -> Dict[str, Any]:
    features = candidate.get('candidate_features') if isinstance(candidate.get('candidate_features'), dict) else candidate
    profile = structured_signal_profile(candidate)
    details = profile.get('structured_component_details') if isinstance(profile.get('structured_component_details'), dict) else {}
    score = candidate_score_value(candidate) or 50.0
    score_boost = 0.0
    score_penalty = 0.0
    reasons: List[str] = []

    raw_flow = candidate.get('data_directory_capital_flow') if isinstance(candidate.get('data_directory_capital_flow'), dict) else {}
    capital_flow = _positive_numeric(raw_flow.get('main_force_net_inflow')) or _positive_numeric(features.get('net_inflow_main'))
    if capital_flow > 0:
        boost = min(3.5, math.log10(capital_flow / 1000000.0 + 1.0) * 1.5)
        score_boost += boost
        reasons.append(f'positive_capital_flow:+{boost:.2f}')

    sector_strength = max(
        _positive_numeric(features.get('sector_opportunity_score')),
        _positive_numeric(features.get('sector_catalyst_score')),
        _positive_numeric(features.get('main_theme_core_score')),
        _positive_numeric(features.get('main_theme_alignment_score')),
    )
    # 板块催化加分：只在板块强度适中时加分（历史数据: 0.5-0.8最优）
    if 0.5 <= sector_strength <= 0.8:
        boost = min(3.5, sector_strength * 2.4)
        score_boost += boost
        reasons.append(f'sector_theme_catalyst_optimal:+{boost:.2f}')
    elif sector_strength > 0 and sector_strength < 0.5:
        boost = min(2.0, sector_strength * 2.0)
        score_boost += boost
        reasons.append(f'sector_theme_catalyst_mild:+{boost:.2f}')
    # 板块强度>=0.8时不加分（在contrarian_re_score中动态惩罚）

    # 中等分数(80-90) + 低板块催化 = 最优组合（历史数据: 85.7%胜率）
    score_val = candidate_score_value(candidate) or 50.0
    if 80 <= score_val <= 90 and sector_strength < 0.5:
        boost = 3.0
        score_boost += boost
        reasons.append(f'sweet_spot_medium_score_low_catalyst:+{boost:.2f}')

    intraday_alert = max(
        _positive_numeric(features.get('intraday_alert_strength')),
        _positive_numeric(details.get('pre_limitup_anomaly')),
        _positive_numeric(details.get('weak_to_strong_reversal')),
        _positive_numeric(features.get('limitup_reason_propagation_score')),
    )
    if intraday_alert > 0:
        boost = min(2.8, intraday_alert * 2.1)
        score_boost += boost
        reasons.append(f'intraday_alert:+{boost:.2f}')

    candidate_stage = str(features.get('candidate_stage') or profile.get('candidate_stage') or '')
    recovery_signal = max(
        _positive_numeric(features.get('close_position_score')),
        _positive_numeric(features.get('low_position_catalyst_score')),
        _positive_numeric(features.get('early_opportunity_score')),
    )
    if candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        boost = 1.0 + min(1.6, recovery_signal * 1.6)
        score_boost += boost
        reasons.append(f'underwater_recovery:+{boost:.2f}')

    one_lot_cost = safe_float(features.get('one_lot_cost'))
    if one_lot_cost is None:
        price = safe_float(features.get('price'))
        if price is not None:
            one_lot_cost = price * 100
    cap = safe_float(features.get('one_lot_cost_cap'))
    if one_lot_cost is not None and cap is not None and one_lot_cost <= cap:
        score_boost += 0.6
        reasons.append('affordable_lot:+0.60')

    regulatory_block = str(features.get('regulatory_hard_block') or '')
    if not regulatory_block or is_routine_regulatory_block(regulatory_block):
        score_boost += 0.4
        reasons.append('non_hard_regulatory:+0.40')

    limitup_confirmation = max(
        _positive_numeric(features.get('limitup_reason_strength')),
        _positive_numeric(features.get('limitup_capture_score')),
        _positive_numeric(features.get('seal_order_strength')),
        _positive_numeric(features.get('order_book_pressure')),
    )
    news_catalyst_strength = _positive_numeric(features.get('news_catalyst_strength'))
    signal_pct = _positive_numeric(features.get('signal_pct'))
    fund_flow_momentum = _positive_numeric(features.get('fund_flow_momentum'))
    time_series_momentum = _positive_numeric(features.get('time_series_momentum'))
    market_cap_proxy = max(
        _positive_numeric(features.get('full_universe_amount_pctile')),
        _positive_numeric(features.get('full_universe_fund_pctile')),
        _positive_numeric(features.get('amount_pctile_rule')),
    )
    sector_tags = ' '.join(normalize_tag_list(features.get('sector_opportunity_tags')))
    name_text = f"{features.get('name') or ''} {features.get('sector_name') or ''} {features.get('industry_name') or ''}"
    is_financial = any(token in f"{sector_tags} {name_text}" for token in ('金融', '银行', '证券', '保险'))

    if score >= 88 and sector_strength < 0.4 and limitup_confirmation < 0.55:
        penalty = 4.5
        score_penalty += penalty
        reasons.append(f'high_score_no_real_confirmation:-{penalty:.2f}')
    if candidate_stage in ('high_7_to_9', 'near_limit_9_plus') and limitup_confirmation < 0.55:
        penalty = 2.5
        if signal_pct >= 8.0:
            penalty += 0.7
        score_penalty += penalty
        reasons.append(f'chase_high_without_confirmation:-{penalty:.2f}')
    if news_catalyst_strength > 0 and sector_strength < 0.35 and limitup_confirmation < 0.35:
        penalty = 1.6
        score_penalty += penalty
        reasons.append(f'news_only_without_limitup_reason:-{penalty:.2f}')
    if market_cap_proxy >= 0.85:
        penalty = min(3.0, 0.8 + (market_cap_proxy - 0.75) * 4.0)
        score_penalty += penalty
        reasons.append(f'crowded_or_large_cap_proxy:-{penalty:.2f}')
    if is_financial:
        penalty = 1.8
        score_penalty += penalty
        reasons.append(f'financial_or_banking_drag:-{penalty:.2f}')
    if score >= 85 and fund_flow_momentum < 0.2 and time_series_momentum < 0.15:
        penalty = 1.4
        score_penalty += penalty
        reasons.append(f'weak_next_day_proxy:-{penalty:.2f}')
    # 板块过热惩罚（历史数据: sector_cat>=0.8 胜率18%，需动态判断）
    # 在弱势/震荡市场，板块热是坏事；在强势市场，板块热可以追
    market_breadth = _positive_numeric(features.get('market_breadth_up_pct'))
    if sector_strength >= 0.8 and market_breadth < 60:  # 弱势/震荡市场
        penalty = 8.0
        score_penalty += penalty
        reasons.append(f'sector_overheated_weak_market:-{penalty:.2f}')
    elif sector_strength >= 0.8 and market_breadth < 70:  # 中性市场
        penalty = 4.0
        score_penalty += penalty
        reasons.append(f'sector_overheated_neutral_market:-{penalty:.2f}')
    if score >= 90 and recovery_signal < 0.45 and limitup_confirmation < 0.45:
        penalty = 1.2
        score_penalty += penalty
        reasons.append(f'no_follow_through_support:-{penalty:.2f}')

    return {
        'score_boost': round(score_boost, 4),
        'score_penalty': round(score_penalty, 4),
        'net_adjustment': round(score_boost - score_penalty, 4),
        'reasons': reasons,
    }

def _normalize_news_kuaixun_rows(rows: Any) -> List[Dict[str, str]]:
    news_list: List[Dict[str, str]] = []
    items = rows if isinstance(rows, list) else ((rows.get('list') or rows.get('items') or []) if isinstance(rows, dict) else [])
    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get('title', '') or item.get('showTitle', '') or ''
        content = item.get('content', '') or item.get('digest', '') or title or ''
        if title or content:
            news_list.append({'title': str(title).strip(), 'content': str(content).strip()[:500]})
    return news_list

def _load_news_kuaixun(target_date: str = '') -> List[Dict[str, str]]:
    """加载东财7x24快讯新闻；official path 只读 scanner summary/DB raw payload。"""
    if not target_date:
        target_date = dt.date.today().isoformat()

    candidate_paths = [
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan_afternoon' / 'news_kuaixun.jsonl',
        BASE / 'data' / 'live_scan' / target_date / 'eastmoney_scan_morning' / 'news_kuaixun.jsonl',
    ]
    try:
        for summary_path in scan_summary_paths(target_date):
            summary = read_json(Path(summary_path))
            files = summary.get('files') if isinstance(summary, dict) else {}
            news_file = files.get('news_kuaixun') if isinstance(files, dict) else None
            if isinstance(news_file, dict):
                news_file = news_file.get('path') or news_file.get('file')
            if news_file:
                candidate_paths.append(Path(news_file))
    except Exception:
        pass

    for news_path in candidate_paths:
        if not news_path.exists():
            continue
        try:
            rows = []
            with open(news_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            news_list = _normalize_news_kuaixun_rows(rows)
            if news_list:
                return news_list
        except Exception:
            continue

    try:
        from xiaogu_db import fetch_latest_api_scan_session_with_market_data, fetch_scan_market_data_payloads
        session = fetch_latest_api_scan_session_with_market_data(_parse_date(target_date))
        if session and session.get('id'):
            payloads = fetch_scan_market_data_payloads(int(session['id']))
            news_list = _normalize_news_kuaixun_rows(payloads.get('news_kuaixun'))
            if news_list:
                return news_list
    except Exception as exc:
        print(f'WARN: NEWS_KUAIXUN_DB_SOURCE_UNAVAILABLE: {exc}', file=sys.stderr)

    print('WARN: NEWS_KUAIXUN_SOURCE_MISSING', file=sys.stderr)
    return []

def _load_sector_names() -> List[str]:
    """从全市场行情快照直接加载个股行业与概念名称。"""
    today = dt.date.today().isoformat()
    sector_names = []
    quote_path = BASE / 'data' / 'live_scan' / today / 'eastmoney_scan_afternoon' / 'stock_all_a.jsonl'
    if quote_path.exists():
        try:
            with open(quote_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    industry = str(item.get('f100') or '').strip()
                    if industry:
                        sector_names.append(industry)
                    concepts = str(item.get('f103') or '')
                    sector_names.extend(
                        value.strip()
                        for value in re.split(r'[,，;；|]+', concepts)
                        if value.strip() and value.strip() not in ('-', '--')
                    )
        except (OSError, json.JSONDecodeError):
            pass

    return list(dict.fromkeys(sector_names))

def _get_sector_names() -> List[str]:
    """获取板块名称（带缓存）。"""
    global _SECTOR_NAMES_CACHE
    if not _SECTOR_NAMES_CACHE:
        _SECTOR_NAMES_CACHE = _load_sector_names()
    return _SECTOR_NAMES_CACHE

def _analyze_news_sentiment(news_list: List[Dict[str, str]]) -> Dict[str, float]:
    """分析新闻情感，返回各板块得分（动态匹配实际板块名称）。"""
    # 核心关键词（用于从新闻中提取信号）
    core_keywords = {
        '银行': ['银行', '降准', '降息', 'LPR', '金融改革'],
        '创新药': ['创新药', 'CXO', '医药', '生物制品', '疫苗', '单抗', '减肥药'],
        '红利': ['红利', '高股息', '央企估值', '破净修复', '中特估'],
        '机器人': ['人形机器人', '特斯拉机器人', '减速器', '伺服电机'],
        'AI': ['大模型', '算力', 'GPU', 'AI应用', '人工智能'],
        '半导体': ['芯片法案', '半导体补贴', '国产替代', '光刻机'],
        '军工': ['军费增长', '装备订单', '航天发射', '卫星互联网'],
        '煤炭': ['煤炭', '能源安全', '煤价', '限产'],
        '新能源': ['光伏补贴', '储能政策', '锂电池', '充电桩'],
    }

    # 从API加载实际板块名称
    sector_names = _get_sector_names()

    # 计算每个核心关键词的得分
    keyword_scores = {}
    for keyword, synonyms in core_keywords.items():
        score = 0
        match_count = 0
        for news in news_list:
            text = (news.get('title', '') + ' ' + news.get('content', '')).lower()
            for kw in synonyms:
                if kw.lower() in text:
                    score += 15
                    match_count += 1
                    break
        if match_count > 0:
            keyword_scores[keyword] = min(100, score)

    # 将核心关键词得分映射到实际板块名称
    sector_scores = {}
    for sector_name in sector_names:
        sector_lower = sector_name.lower()
        for keyword, score in keyword_scores.items():
            # 如果板块名称包含关键词，继承该关键词的得分
            if keyword.lower() in sector_lower:
                sector_scores[sector_name] = max(sector_scores.get(sector_name, 0), score)
                break

    return sector_scores

def _get_news_analysis(target_date: str = '') -> Tuple[List[Dict], Dict[str, float]]:
    """获取新闻分析结果（带缓存）。"""
    global _NEWS_CACHE, _NEWS_CACHE_DATE
    if not target_date:
        target_date = dt.date.today().isoformat()
    if _NEWS_CACHE is None or _NEWS_CACHE_DATE != target_date:
        news_list = _load_news_kuaixun(target_date)
        sector_scores = _analyze_news_sentiment(news_list)
        _NEWS_CACHE = (news_list, sector_scores)
        _NEWS_CACHE_DATE = target_date
    return _NEWS_CACHE

def contrarian_re_score(candidate: Dict[str, Any]) -> float:
    """Dynamic regime-aware re-scoring: reward momentum in strong markets, contrarian in weak.

    Data insight:
      强势市场: 高信号+4.79% vs 低信号+0.04% → 追强有效
      弱势市场: 高信号-3.74% vs 低信号+0.85% → 反共识有效
    """
    features = candidate.get('candidate_features') or candidate
    original = candidate_score_value(candidate) or 50.0
    scoring_config = get_scoring_config_snapshot()
    config = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(config, dict):
        config = dict(SCORING_CONFIG_DEFAULTS)

    signal_pct = safe_float(features.get('signal_pct')) or 0.5
    close_pos = safe_float(features.get('close_position_score')) or 0.5
    fund_mom = safe_float(features.get('fund_flow_momentum')) or 0.5
    sector_cat = safe_float(features.get('sector_catalyst_score')) or 0.5
    early_opp = safe_float(features.get('early_opportunity_score')) or 0.5
    market_regime = str(features.get('market_regime', '') or '').lower()

    # 加载新闻分析
    news_list, news_sector_scores = _get_news_analysis()

    # 市场级别信号（更准确的市场风格判断）
    market_breadth = safe_float(features.get('market_breadth_up_pct')) or 50.0
    market_limitups = safe_float(features.get('market_limitups')) or 0
    market_bigups = safe_float(features.get('market_bigups')) or 0

    # Detect market regime from both candidate signals AND market-level signals
    # Strong: high signal_pct + high fund_flow + positive momentum + strong market breadth
    # Weak: low signal_pct or negative fund_flow OR weak market breadth
    momentum_signals = signal_pct * 0.3 + fund_mom * 0.2 + sector_cat * 0.2
    market_signals = (market_breadth / 100.0) * 0.2 + min(1.0, market_limitups / 100.0) * 0.1

    combined_signal = momentum_signals + market_signals

    if combined_signal > 1.2 or 'bull' in market_regime or 'strong' in market_regime:
        regime = 'strong'
    elif combined_signal < 0.6 or 'bear' in market_regime or 'weak' in market_regime or market_breadth < 40:
        regime = 'weak'
    else:
        regime = 'sideways'

    if regime == 'strong':
        # 追强: reward high signal, high flow, hot sector
        signal_adj = signal_pct * 3          # 高信号加分
        position_adj = close_pos * 5         # 高位加分（动量延续）
        flow_adj = fund_mom * 4              # 高资金流加分
        sector_adj = sector_cat * 3          # 热板块加分
        early_adj = early_opp * 2            # 早期机会少量加分
    elif regime == 'weak':
        # 反共识: reward low signal, low position, low flow
        signal_adj = max(0, (1.0 - signal_pct / 5.0)) * 15
        position_adj = max(0, (0.9 - close_pos)) * 20
        flow_adj = max(0, (0.9 - fund_mom)) * 10
        # 弱势市场：板块热是坏事（历史数据: sector_cat>=0.8 胜率18%）
        if sector_cat >= 0.8:
            sector_adj = -15  # 主动惩罚
        elif sector_cat >= 0.5:
            sector_adj = -5   # 轻微惩罚
        else:
            sector_adj = max(0, (0.9 - sector_cat)) * 8  # 冷门加分
        early_adj = early_opp * 12
    else:
        # 震荡: balanced
        signal_adj = (1.0 - abs(signal_pct - 1.5) / 3.0) * 8   # 中等信号最优
        position_adj = max(0, (0.85 - close_pos)) * 15           # 中低位最优
        flow_adj = max(0, (0.85 - fund_mom)) * 8                 # 中等资金流最优
        # 震荡市场：板块热也要小心
        if sector_cat >= 0.8:
            sector_adj = -8   # 惩罚
        elif sector_cat >= 0.5:
            sector_adj = max(0, (0.85 - sector_cat)) * 6  # 中等最优
        else:
            sector_adj = max(0, (0.85 - sector_cat)) * 6  # 冷门加分
        early_adj = early_opp * 10                                # 早期机会加分

    archetype_adjustment = archetype_score_adjustments(candidate)
    re_score = original + signal_adj + position_adj + flow_adj + sector_adj + early_adj
    re_score += archetype_adjustment['net_adjustment']

    # 新闻驱动加分：如果候选板块有新闻催化，加分
    if news_sector_scores:
        candidate_name = str(features.get('name') or '') + ' ' + str(features.get('sector_name') or '')
        news_boost = 0
        for sector, score in news_sector_scores.items():
            if sector in candidate_name:
                # 新闻分数越高，加分越多（最多+10）
                news_boost = min(10, score / 10)
                break
        if news_boost > 0:
            re_score += news_boost
            archetype_adjustment.setdefault('reasons', []).append(f'news_catalyst:+{news_boost:.2f}')

    # Social signals are intentionally diagnostic/shadow-only until the
    # production ranking gate is unlocked. Do not add them to this score.

    # Score cap: penalize candidates above threshold (100+ scores have 0% win rate)
    cap = safe_float(config.get('max_score_cap')) or safe_float(SCORING_CONFIG_DEFAULTS['max_score_cap']) or 95.0
    if original > cap:
        penalty = (original - cap) * 0.3
        re_score -= penalty
        archetype_adjustment.setdefault('reasons', []).append(f'max_score_cap_penalty:-{penalty:.2f}')

    # 风险调整
    # 涨幅风险惩罚: signal_pct > 8% 时追高风险大 (历史数据: >8% 胜率 0%)
    # 只对有实际分数的 case 应用
    if original > 50 and signal_pct > 8.0:
        overextend_penalty = (signal_pct - 8.0) * 5  # 每超过 1% 扣 5 分
        re_score -= overextend_penalty
        archetype_adjustment.setdefault('reasons', []).append(f'overextend_risk_penalty:-{overextend_penalty:.2f}')

    # Climax 市场惩罚: 高潮期容易见顶 (历史数据: climax 胜率 50%, 平均收益 0.04%)
    # 无条件应用，因为 climax 市场下所有候选都危险
    if 'climax' in market_regime:
        climax_penalty = 20  # 固定惩罚 20 分（从 15 提高到 20）
        re_score -= climax_penalty
        archetype_adjustment.setdefault('reasons', []).append(f'climax_market_penalty:-{climax_penalty:.2f}')

    # 强势市场奖励: 强势市场最安全 (历史数据: strong 胜率 80%, 平均收益 1.85%)
    if 'strong' in market_regime or 'bull' in market_regime:
        strong_bonus = 10  # 固定奖励 10 分
        re_score += strong_bonus
        archetype_adjustment.setdefault('reasons', []).append(f'strong_market_bonus:+{strong_bonus:.2f}')

    candidate['_re_score_archetype_adjustment'] = archetype_adjustment
    candidate['_re_score_scoring_config_source'] = str(scoring_config.get('source') or 'defaults')
    candidate['_re_score_scoring_config_loaded'] = bool(scoring_config.get('loaded'))
    candidate['_re_score_scoring_config_error'] = str(scoring_config.get('error') or '')

    return round(re_score, 1)

def candidate_rank_value(candidate: Dict[str, Any]) -> int:
    rank = safe_int(candidate.get('rank'))
    return rank if rank is not None else 999999

def scan_summary_for_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    summary_path = str(bundle.get('scan_summary_path') or '')
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            try:
                summary = read_json(path)
                if isinstance(summary, dict):
                    return summary
            except Exception:
                pass
    source_evidence = bundle.get('source_evidence') if isinstance(bundle.get('source_evidence'), dict) else {}
    summary_path = str(source_evidence.get('summary_path') or '')
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            try:
                summary = read_json(path)
                if isinstance(summary, dict):
                    return summary
            except Exception:
                pass
    return {}

def bundle_metric(bundle: Dict[str, Any], key: str, default: Any = None) -> Any:
    summary = scan_summary_for_bundle(bundle)
    for source in (summary, bundle.get('market_snapshot'), bundle.get('source_status')):
        if isinstance(source, dict):
            value = source.get(key)
            if value not in (None, ''):
                return value
    return default

def structured_component(row: Dict[str, Any], key: str) -> Optional[float]:
    components = row.get('structured_score_components') or row.get('components') or {}
    if not isinstance(components, dict):
        return None
    return safe_float(components.get(key))

def normalize_tag_list(tags: Any) -> List[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        tags = [tags]
    normalized: List[str] = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        clean = tag.strip()
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)
    return normalized

def candidate_theme_tag_set(row: Dict[str, Any]) -> Tuple[str, ...]:
    """Stable fingerprint of candidate theme tags for hollow-pool detection (M5)."""
    if not isinstance(row, dict):
        return tuple()
    details = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    values: List[Any] = []
    for key in (
        'theme_tags', 'predicted_sector', 'sector_opportunity_tags',
        'industry_chain_tags', 'concept_tags', 'main_theme_tags',
    ):
        values.append(row.get(key))
        values.append(details.get(key))
    tags = normalize_tag_list([item for group in values for item in (group if isinstance(group, list) else [group])])
    # Drop pure process/layer pseudo tags so only theme-like labels participate.
    filtered = [
        tag for tag in tags
        if not str(tag).startswith('REPLAY_')
        and str(tag).upper() not in {
            'SECTOR_OPPORTUNITY', 'PASS', 'FAIL', 'PARTIAL', 'MISSING',
            'L0_FULL_UNIVERSE', 'L7_INTRADAY_ALERT', 'FULL_UNIVERSE',
        }
        and not (str(tag).upper().startswith('L') and len(str(tag)) >= 2 and str(tag)[1].isdigit())
    ]
    return tuple(sorted(filtered))

def detect_pool_hollow_theme_tags(rows: List[Dict[str, Any]], *, min_rows: int = 5, dominance: float = 0.80, min_tags: int = 3) -> Dict[str, Any]:
    """Detect full-pool identical theme tags pollution. Diagnostic + soft ranking only."""
    fingerprints: List[Tuple[str, ...]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        fingerprint = candidate_theme_tag_set(row)
        if fingerprint:
            fingerprints.append(fingerprint)
    if len(fingerprints) < min_rows:
        return {
            'hollow': False,
            'dominance_ratio': 0.0,
            'dominant_tags': [],
            'sample_count': len(fingerprints),
            'reason': 'insufficient_nonempty_tag_rows',
        }
    dominant = max(set(fingerprints), key=fingerprints.count)
    ratio = fingerprints.count(dominant) / max(1, len(fingerprints))
    hollow = bool(ratio >= dominance and len(dominant) >= min_tags)
    return {
        'hollow': hollow,
        'dominance_ratio': round(ratio, 4),
        'dominant_tags': list(dominant),
        'sample_count': len(fingerprints),
        'reason': 'pool_identical_theme_tags' if hollow else 'ok',
    }

def normalize_vei_phase_d_tags(tags: Any) -> List[str]:
    return normalize_tag_list(tags)

def inferred_vei_phase_d_tags(details: Dict[str, Any]) -> List[str]:
    if not isinstance(details, dict):
        details = {}
    inferred: List[str] = []
    if safe_float(details.get('pre_limitup_anomaly')) and safe_float(details.get('pre_limitup_anomaly')) > 0:
        inferred.append('PRE_LIMITUP_ANOMALY')
    if safe_float(details.get('weak_to_strong_reversal')) and safe_float(details.get('weak_to_strong_reversal')) > 0:
        inferred.append('WEAK_TO_STRONG_REVERSAL')
    if safe_float(details.get('first_board_pre_signal')) and safe_float(details.get('first_board_pre_signal')) > 0:
        inferred.append('FIRST_BOARD_PRE_SIGNAL')
    if safe_float(details.get('sector_opportunity_score')) and safe_float(details.get('sector_opportunity_score')) > 0:
        inferred.append('SECTOR_OPPORTUNITY')
    return inferred

def signal_stage_bucket(signal_pct: Any) -> str:
    pct = safe_float(signal_pct)
    if pct is None:
        return 'unknown'
    if pct < 0:
        return 'underwater'
    if pct < 3:
        return 'flat_0_to_3'
    if pct < 5:
        return 'early_3_to_5'
    if pct < 7:
        return 'mid_5_to_7'
    if pct < 9:
        return 'high_7_to_9'
    return 'near_limit_9_plus'

@lru_cache(maxsize=128)
def _historical_t1_return_map_for_date(trade_date: str) -> Dict[str, float]:
    trade_date = str(trade_date or '')[:10]
    if not trade_date:
        return {}
    try:
        from xiaogu_db import fetch_returns
    except Exception:
        return {}
    try:
        rows = fetch_returns(dt.date.fromisoformat(trade_date))
    except Exception:
        return {}
    return {
        str(row.get('symbol') or '').zfill(6)[-6:]: float(row.get('t1_return'))
        for row in rows or []
        if str(row.get('symbol') or '').zfill(6)[-6:] and row.get('t1_return') is not None
    }

def candidate_theme_text(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        'sector_opportunity_tags', 'theme_tags', 'industry', 'sector', 'sector_name',
        'name', 'stock_name', 'predicted_sector', 'main_theme',
        'concept', 'concepts', 'concept_tags', 'industry_chain_tags',
    ):
        value = row.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    details = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    for item in details.get('sector_opportunity_tags') or []:
        if item:
            parts.append(str(item))
    research = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    sector_mapping = research.get('sector_mapping') if isinstance(research.get('sector_mapping'), dict) else {}
    for item in sector_mapping.get('sectors') or []:
        if item:
            parts.append(str(item))
    for item in (row.get('limitup_reason_evidence') or [])[:5]:
        if isinstance(item, dict) and item.get('reason'):
            parts.append(str(item.get('reason')))
        elif item:
            parts.append(str(item))
    return ' '.join(parts)

def theme_token_hits(themes: List[str], text: str) -> List[str]:
    """Match mainline themes with bounded synonym expansion."""
    hits: List[str] = []
    text = str(text or '')
    for theme in themes:
        theme = str(theme or '').strip()
        if not theme:
            continue
        synonyms = MAINLINE_THEME_SYNONYMS.get(theme, (theme,))
        if any(token and token in text for token in synonyms):
            hits.append(theme)
    return hits

def load_profit_shadow_watchlist(trade_date: str, top_n: int = 5) -> Dict[str, Any]:
    """Observation-only profit shadow topN for NO_PICK days.

    Prefer existing summary/profit_candidates_{date}.json (no recompute).
    Never promotes to PAPER_PICK; official gates unchanged.
    """
    trade_date = str(trade_date or '')[:10]
    empty = {
        'status': 'MISSING',
        'trade_date': trade_date,
        'decision_class': 'PROFIT_CANDIDATE_SHADOW',
        'not_official_paper_pick': True,
        'observation_only': True,
        'official_gates_unchanged': True,
        'candidates': [],
        'mainline_tags': [],
        **LOCKED_SAFETY,
    }
    if not trade_date:
        return empty
    path = BASE / 'summary' / f'profit_candidates_{trade_date}.json'
    payload: Dict[str, Any] = {}
    if path.exists():
        try:
            loaded = read_json(path)
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}
    if not payload:
        # Best-effort build without T+1 network when scan exists.
        try:
            from scripts.xiaogu_profit_candidates_shadow import run_for_date as _shadow_run

            payload = _shadow_run(trade_date, top_n=top_n, with_returns=False) or {}
        except Exception as exc:
            empty['status'] = 'ERROR'
            empty['error'] = f'{type(exc).__name__}:{exc}'
            return empty
    if not isinstance(payload, dict):
        return empty
    cands_out: List[Dict[str, Any]] = []
    for row in list(payload.get('candidates') or [])[: max(1, int(top_n or 5))]:
        if not isinstance(row, dict):
            continue
        sym = str(row.get('symbol') or row.get('code') or '').zfill(6)[-6:]
        if not sym or not sym.isdigit():
            continue
        cands_out.append(
            {
                'symbol': sym,
                'name': row.get('name') or row.get('stock_name'),
                'profit_score': row.get('profit_score') or row.get('score'),
                'signal_pct': row.get('signal_pct') or row.get('pct_chg'),
                'from_limitup_pool': bool(row.get('from_limitup_pool') or row.get('limitup')),
                'mainline_hits': list(row.get('mainline_hits') or [])[:6],
                'ret_t1_close': (
                    (row.get('t1') or {}).get('ret_t1_close')
                    if isinstance(row.get('t1'), dict)
                    else row.get('ret_t1_close')
                ),
                'observation_only': True,
                'not_official_paper_pick': True,
            }
        )
    mainline = payload.get('mainline') if isinstance(payload.get('mainline'), dict) else {}
    tags = list(mainline.get('mainline_tags') or payload.get('mainline_tags') or [])[:12]
    return {
        'status': str(payload.get('status') or ('OK' if cands_out else 'EMPTY')),
        'trade_date': trade_date,
        'decision_class': 'PROFIT_CANDIDATE_SHADOW',
        'not_official_paper_pick': True,
        'observation_only': True,
        'official_gates_unchanged': True,
        'valid_for_conclusion': bool(payload.get('valid_for_conclusion')),
        'source_path': str(path) if path.exists() else str(payload.get('output_path') or ''),
        'mainline_tags': tags,
        'candidates': cands_out,
        'candidate_count': len(cands_out),
        'selection_basis': list(payload.get('selection_basis') or [])[:8],
        'explanation': (
            'Profit-shadow watchlist for NO_PICK days; observation only; '
            'does not change official PAPER_PICK gates or allow_trade.'
        ),
        **LOCKED_SAFETY,
        'allow_trade': False,
        'manual_paper_execution_allowed': False,
    }

def load_mainline_fund_flow_context(trade_date: str, top_n: int = 8) -> Dict[str, Any]:
    """Day mainline tags from sector fund inflow (scan flow_*.jsonl or shadow summary).

    Soft ranking evidence only — never a hard gate / never force-pick.
    """
    trade_date = str(trade_date or '')[:10]
    empty = {
        'trade_date': trade_date,
        'mainline_tags': [],
        'industry_top': [],
        'concept_top': [],
        'source': 'missing',
        'soft_only': True,
        'hard_gate': False,
        'force_pick': False,
    }
    if not trade_date:
        return empty

    # Prefer already-built shadow mainline (same selection language as profit shadow).
    shadow_path = BASE / 'summary' / f'profit_candidates_{trade_date}.json'
    if shadow_path.exists():
        try:
            payload = read_json(shadow_path)
            if isinstance(payload, dict):
                mainline = payload.get('mainline') if isinstance(payload.get('mainline'), dict) else {}
                tags = list(mainline.get('mainline_tags') or [])[: max(4, int(top_n or 8) * 2)]
                if tags:
                    return {
                        'trade_date': trade_date,
                        'mainline_tags': tags,
                        'industry_top': list(mainline.get('industry_top') or [])[:top_n],
                        'concept_top': list(mainline.get('concept_top') or [])[:top_n],
                        'source': 'profit_candidates_shadow_summary',
                        'soft_only': True,
                        'hard_gate': False,
                        'force_pick': False,
                    }
        except Exception:
            pass

    # Fallback: parse scan flow files directly.
    try:
        from scripts.xiaogu_profit_candidates_shadow import load_sector_flows, resolve_scan_dir

        scan_dir = resolve_scan_dir(trade_date)
        if scan_dir is None:
            return empty
        flows = load_sector_flows(scan_dir, top_n=top_n)
        tags = list(flows.get('mainline_tags') or [])[: max(4, int(top_n or 8) * 2)]
        return {
            'trade_date': trade_date,
            'mainline_tags': tags,
            'industry_top': list(flows.get('industry_top') or [])[:top_n],
            'concept_top': list(flows.get('concept_top') or [])[:top_n],
            'source': 'scan_flow_industry_concept',
            'soft_only': True,
            'hard_gate': False,
            'force_pick': False,
        }
    except Exception as exc:
        empty['error'] = f'{type(exc).__name__}:{exc}'
        return empty

def soft_mainline_fund_bias(
    row: Dict[str, Any],
    mainline_ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Soft ranking bias for candidates aligned with day fund-flow mainline.

    Does not force PAPER_PICK. Caps boost so sealed chase cannot dominate alone.
    """
    trade_date = str(row.get('trade_date') or row.get('date') or '')
    ctx = mainline_ctx if isinstance(mainline_ctx, dict) else load_mainline_fund_flow_context(trade_date)
    tags = [str(t) for t in (ctx.get('mainline_tags') or []) if t]
    text = candidate_theme_text(row)
    hits = theme_token_hits(tags, text) if tags else []
    # Rank position among mainline tags (earlier industry tags weigh more).
    rank_boost = 0.0
    for i, tag in enumerate(tags[:8]):
        if tag in hits:
            rank_boost += max(0.0, 0.28 - 0.03 * i)
    hit_boost = min(0.85, 0.22 * len(hits) + rank_boost)
    signal_pct = float(safe_float(row.get('signal_pct')) or 0.0)
    # Soft-dampen pure sealed extension unless also strong fund/theme (still soft).
    if signal_pct >= 9.5 and hit_boost > 0:
        hit_boost *= 0.72
    return {
        'mainline_tags': tags[:12],
        'mainline_hits': hits,
        'soft_boost': round(hit_boost, 4),
        'source': str(ctx.get('source') or 'missing'),
        'soft_only': True,
        'hard_gate': False,
        'force_pick': False,
        'selected_for_production': False,
    }

def market_adaptive_context(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    market_snapshot = bundle.get('market_snapshot') if isinstance(bundle.get('market_snapshot'), dict) else {}
    external_market = market_snapshot.get('external_market') if isinstance(market_snapshot.get('external_market'), dict) else {}

    def market_float(key: str) -> float | None:
        value = safe_float(row.get(key))
        if value is None:
            value = safe_float(bundle.get(key))
        if value is None:
            value = safe_float(market_snapshot.get(key))
        return value

    market_follow_through_score = market_float('market_follow_through_score')
    market_breadth_up_pct = market_float('market_breadth_up_pct')
    market_limitups = market_float('market_limitups')
    limitup_broken_ratio = market_float('limitup_broken_ratio')
    broken_limitups = market_float('broken_limitups')
    max_consecutive = market_float('max_consecutive')
    sentiment_score = market_float('sentiment_score')
    market_regime = str(row.get('market_regime') or bundle.get('market_regime') or market_snapshot.get('market_regime') or '').lower()
    external_market_status = str(external_market.get('status') or 'MISSING')
    external_market_signal_score = safe_float(external_market.get('external_market_signal_score'))
    external_market_risk_off = bool(
        external_market_status == 'PASS'
        and external_market_signal_score is not None
        and external_market_signal_score <= -1.0
    )
    external_market_supportive = bool(
        external_market_status == 'PASS'
        and external_market_signal_score is not None
        and external_market_signal_score >= 1.0
    )

    broken_limit_pressure = bool(
        (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.95)
        or (
            market_limitups is not None
            and broken_limitups is not None
            and broken_limitups >= max(18.0, market_limitups * 0.75)
        )
    )
    if not market_regime:
        if (
            market_follow_through_score is not None and market_follow_through_score >= 0.62
            and market_breadth_up_pct is not None and market_breadth_up_pct >= 58
            and limitup_broken_ratio is not None and limitup_broken_ratio >= 1.2
        ):
            market_regime = 'strong'
        elif (
            (market_follow_through_score is not None and market_follow_through_score <= 0.38)
            or (market_breadth_up_pct is not None and market_breadth_up_pct <= 45)
            or (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.85)
            or broken_limit_pressure
        ):
            market_regime = 'weak'
        else:
            market_regime = 'neutral'
    if external_market_risk_off and market_regime != 'strong':
        market_regime = 'weak'

    supportive_market = bool(
        market_regime == 'strong'
        or (market_follow_through_score is not None and market_follow_through_score >= 0.62)
        or (
            market_breadth_up_pct is not None
            and market_breadth_up_pct >= 58
            and limitup_broken_ratio is not None
            and limitup_broken_ratio >= 1.2
        )
    )
    supportive_market = supportive_market or (
        external_market_supportive and market_regime != 'weak'
    )
    weak_acceptance_market = bool(
        market_regime == 'weak'
        or (limitup_broken_ratio is not None and limitup_broken_ratio <= 0.85)
        or (
            market_limitups is not None
            and broken_limitups is not None
            and broken_limitups >= max(20.0, market_limitups * 0.9)
        )
    )
    weak_acceptance_market = weak_acceptance_market or external_market_risk_off
    overheated_market = bool(
        (market_breadth_up_pct is not None and market_breadth_up_pct >= 80)
        or (market_limitups is not None and market_limitups >= 150)
        or (
            sentiment_score is not None and sentiment_score >= 0.75
            and max_consecutive is not None and max_consecutive >= 5
        )
    )
    context = {
        'market_regime': market_regime,
        'market_follow_through_score': market_follow_through_score,
        'market_breadth_up_pct': market_breadth_up_pct,
        'market_limitups': market_limitups,
        'limitup_broken_ratio': limitup_broken_ratio,
        'broken_limitups': broken_limitups,
        'max_consecutive': max_consecutive,
        'sentiment_score': sentiment_score,
        'external_market_status': external_market_status,
        'external_market_signal_score': external_market_signal_score,
        'external_market_risk_off': external_market_risk_off,
        'external_market_supportive': external_market_supportive,
        'supportive_market': supportive_market,
        'weak_acceptance_market': weak_acceptance_market,
        'broken_limit_pressure': broken_limit_pressure,
        'overheated_market': overheated_market,
    }
    try:
        from xiaogu_regime_policy import attach_regime_to_context

        attach_regime_to_context(context)
    except Exception:
        context['production_regime'] = (
            'strong' if market_regime == 'strong' else ('weak' if market_regime == 'weak' else 'sideways')
        )
    return context

def market_adaptive_thresholds(candidate_stage: str, market_context: Dict[str, Any]) -> Dict[str, float]:
    """Delegate to xiaogu_regime_policy (single owner for dynamic strategy gates)."""
    from xiaogu_regime_policy import market_adaptive_thresholds as _regime_thresholds

    return _regime_thresholds(candidate_stage, market_context if isinstance(market_context, dict) else {})

def sector_gate_threshold_for_market(market_context: Dict[str, Any]) -> float:
    """Delegate to xiaogu_regime_policy sector gate table."""
    from xiaogu_regime_policy import sector_gate_threshold_for_market as _regime_sector_gate

    return float(_regime_sector_gate(market_context if isinstance(market_context, dict) else {}))

def normalize_bundle_vei_tags(bundle: Dict[str, Any]) -> Dict[str, Any]:
    def normalize_candidate(candidate: Any) -> Any:
        if isinstance(candidate, dict):
            candidate['vei_phase_d_tags'] = normalize_vei_phase_d_tags(candidate.get('vei_phase_d_tags'))
        return candidate

    if not isinstance(bundle, dict):
        return bundle

    normalize_candidate(bundle.get('candidate'))
    normalize_candidate(bundle.get('candidate_features'))
    for key in (
        'full_candidate_pool',
        'paper_scoring_candidates',
        'structured_observation_basket',
        'structured_sector_observation_basket',
    ):
        items = bundle.get(key)
        if isinstance(items, list):
            bundle[key] = [normalize_candidate(item) for item in items]
    impact = bundle.get('structured_formal_impact')
    if isinstance(impact, dict):
        for key in ('top_structured_only_candidates', 'sector_opportunity_candidates', 'structured_observation_candidates'):
            items = impact.get(key)
            if isinstance(items, list):
                impact[key] = [normalize_candidate(item) for item in items]
    return bundle

def _strip_replay_production_contributions(row: Dict[str, Any]) -> Dict[str, Any]:
    """Keep replay snapshots for audit while removing them from production inputs."""
    out = dict(row) if isinstance(row, dict) else {}
    if out.get('replay_production_contributions_stripped'):
        return out
    details = (
        dict(out.get('structured_component_details'))
        if isinstance(out.get('structured_component_details'), dict)
        else {}
    )
    replay_tags = set(normalize_tag_list(details.get('replay_provenance_tags')))
    if not replay_tags:
        return out
    components = (
        dict(out.get('structured_score_components'))
        if isinstance(out.get('structured_score_components'), dict)
        else dict(out.get('structured_components'))
        if isinstance(out.get('structured_components'), dict)
        else {}
    )
    replay_inflow = max(0.0, safe_float(details.get('replay_main_force_net_inflow')) or 0.0)
    replay_ratio = max(0.0, safe_float(details.get('replay_main_force_net_ratio')) or 0.0)
    replay_flow_bonus = min(1.0, replay_inflow / 100_000_000.0) * 0.25
    replay_ratio_bonus = min(1.0, replay_ratio / 10.0) * 0.10
    if 'fund_flow_momentum' in components:
        components['fund_flow_momentum'] = round(
            max(0.0, (safe_float(components.get('fund_flow_momentum')) or 0.0) - replay_flow_bonus - replay_ratio_bonus),
            4,
        )
    if 'REPLAY_HISTORY_FLOW' in replay_tags and 'time_series_momentum' in components:
        components['time_series_momentum'] = round(
            max(0.0, (safe_float(components.get('time_series_momentum')) or 0.0) - 0.25),
            4,
        )
    alignment_replay_bonus = 0.10 * int('REPLAY_STOCK_PROFILE' in replay_tags)
    alignment_replay_bonus += 0.10 * int('REPLAY_HISTORY_FLOW' in replay_tags)
    if alignment_replay_bonus:
        for key in ('main_theme_alignment_score',):
            if key in components:
                components[key] = round(
                    max(0.0, (safe_float(components.get(key)) or 0.0) - alignment_replay_bonus),
                    4,
                )
            if key in details:
                details[key] = round(
                    max(0.0, (safe_float(details.get(key)) or 0.0) - alignment_replay_bonus),
                    4,
                )
            if key in out:
                out[key] = round(
                    max(0.0, (safe_float(out.get(key)) or 0.0) - alignment_replay_bonus),
                    4,
                )
    out['structured_score_components'] = components
    out['structured_component_details'] = details
    out['replay_production_contributions_stripped'] = True
    return out

def structured_signal_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    row = _strip_replay_production_contributions(row)
    details = row.get('structured_component_details') or row.get('component_details') or {}
    if not isinstance(details, dict):
        details = {}
    components = row.get('structured_score_components') or row.get('components') or {}
    if not isinstance(components, dict):
        components = {}
    research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    research_catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    regulatory_hard_block = str(row.get('regulatory_hard_block') or '')
    if not regulatory_hard_block:
        research_catalyst_category = str(research_catalyst_quality.get('category') or '')
        if research_catalyst_category in ('regulatory_notice', 'risk_notice'):
            regulatory_hard_block = research_catalyst_category
        elif bool((research_signals.get('a_share_risk_review') or {}).get('disqualified_for_paper_pick')):
            regulatory_hard_block = 'a_share_risk_review_disqualified'
    tags = normalize_tag_list(details.get('sector_opportunity_tags') or row.get('sector_opportunity_tags') or [])
    # Defense-in-depth: strip historical REPLAY_* broadcast pollution from theme tags.
    tags = [tag for tag in tags if not str(tag).startswith('REPLAY_')]
    vei_tags = normalize_vei_phase_d_tags((row.get('vei_phase_d_tags') or []) + inferred_vei_phase_d_tags(details))
    sector_opportunity_score = safe_float(details.get('sector_opportunity_score'))
    if sector_opportunity_score is None:
        sector_opportunity_score = safe_float(row.get('sector_opportunity_score'))
    search_layer_hint = str(
        row.get('search_layer_hint')
        or details.get('search_layer_hint')
        or ''
    )
    early_opportunity_score = row.get('early_opportunity_score')
    if early_opportunity_score is None:
        early_opportunity_score = details.get('early_opportunity_score')
    news_catalyst_strength = safe_float(row.get('news_catalyst_strength'))
    if news_catalyst_strength is None:
        news_catalyst_strength = safe_float(details.get('news_catalyst_strength'))
    sector_news_strength = safe_float(row.get('sector_news_strength'))
    if sector_news_strength is None:
        sector_news_strength = safe_float(details.get('sector_news_strength'))
    sector_catalyst_score = safe_float(row.get('sector_catalyst_score'))
    if sector_catalyst_score is None:
        sector_catalyst_score = safe_float(details.get('sector_catalyst_score'))
    news_catalyst_quality_categories = normalize_tag_list(row.get('news_catalyst_quality_categories') or details.get('news_catalyst_quality_categories') or [])
    topic_propagation_score = safe_float(row.get('topic_propagation_score'))
    if topic_propagation_score is None:
        topic_propagation_score = safe_float(details.get('topic_propagation_score'))
    intraday_alert_strength = safe_float(row.get('intraday_alert_strength'))
    if intraday_alert_strength is None:
        intraday_alert_strength = safe_float(details.get('intraday_alert_strength'))
    limitup_reason_propagation_score = safe_float(row.get('limitup_reason_propagation_score'))
    if limitup_reason_propagation_score is None:
        limitup_reason_propagation_score = safe_float(details.get('limitup_reason_propagation_score'))
    limitup_capture_score = safe_float(row.get('limitup_capture_score'))
    if limitup_capture_score is None:
        limitup_capture_score = safe_float(details.get('limitup_capture_score'))
    limitup_capture_profile = str(row.get('limitup_capture_profile') or details.get('limitup_capture_profile') or '')
    limitup_capture_confirmed = bool(row.get('limitup_capture_confirmed') or details.get('limitup_capture_confirmed'))
    limitup_capture_reasons = normalize_tag_list(row.get('limitup_capture_reasons') or details.get('limitup_capture_reasons') or [])
    low_position_catalyst_score = safe_float(row.get('low_position_catalyst_score'))
    if low_position_catalyst_score is None:
        low_position_catalyst_score = safe_float(details.get('low_position_catalyst_score'))
    main_theme_alignment_score = safe_float(row.get('main_theme_alignment_score'))
    if main_theme_alignment_score is None:
        main_theme_alignment_score = safe_float(details.get('main_theme_alignment_score'))
    main_theme_core_score = safe_float(row.get('main_theme_core_score'))
    if main_theme_core_score is None:
        main_theme_core_score = safe_float(details.get('main_theme_core_score'))
    announcement_catalyst_score = safe_float(row.get('announcement_catalyst_score'))
    if announcement_catalyst_score is None:
        announcement_catalyst_score = safe_float(details.get('announcement_catalyst_score'))
    sector_news_catalyst_score = safe_float(row.get('sector_news_catalyst_score'))
    if sector_news_catalyst_score is None:
        sector_news_catalyst_score = safe_float(details.get('sector_news_catalyst_score'))
    limitup_reason_quality_score = safe_float(row.get('limitup_reason_quality_score'))
    if limitup_reason_quality_score is None:
        limitup_reason_quality_score = safe_float(details.get('limitup_reason_quality_score'))
    risk_notice_penalty = safe_float(row.get('risk_notice_penalty'))
    if risk_notice_penalty is None:
        risk_notice_penalty = safe_float(details.get('risk_notice_penalty'))
    mainboard_auxiliary_confidence = safe_float(row.get('mainboard_auxiliary_confidence'))
    if mainboard_auxiliary_confidence is None:
        mainboard_auxiliary_confidence = safe_float(details.get('mainboard_auxiliary_confidence'))
    profile = {
        'trade_mode': TRADE_MODE,
        'primary_return_field': PRIMARY_RETURN_FIELD,
        'primary_trade_horizon': PRIMARY_TRADE_HORIZON,
        'structured_score': safe_float(row.get('structured_score')),
        'base_score': safe_float(row.get('score')),
        'sector_opportunity_score': sector_opportunity_score,
        'sector_opportunity_tags': tags,
        'vei_phase_d_tags': vei_tags,
        'candidate_stage': str(row.get('candidate_stage') or details.get('candidate_stage') or ''),
        'early_opportunity_score': safe_float(early_opportunity_score),
        'setup_type': str(row.get('setup_type') or row.get('setup_type_refined') or ''),
        'search_layer_hint': search_layer_hint,
        'structured_component_details': details,
        'structured_score_components': components,
        'limitup_reason_strength': safe_float(components.get('limitup_reason_strength')),
        'seal_order_strength': safe_float(components.get('seal_order_strength')),
        'order_book_pressure': safe_float(components.get('order_book_pressure')),
        'fund_flow_momentum': safe_float(components.get('fund_flow_momentum')),
        'time_series_momentum': safe_float(components.get('time_series_momentum')),
        'news_catalyst_strength': news_catalyst_strength,
        'sector_news_strength': sector_news_strength,
        'sector_catalyst_score': sector_catalyst_score,
        'news_catalyst_quality_categories': news_catalyst_quality_categories,
        'topic_propagation_score': topic_propagation_score,
        'intraday_alert_strength': intraday_alert_strength,
        'limitup_reason_propagation_score': limitup_reason_propagation_score,
        'limitup_capture_score': limitup_capture_score,
        'limitup_capture_profile': limitup_capture_profile,
        'limitup_capture_confirmed': limitup_capture_confirmed,
        'limitup_capture_reasons': limitup_capture_reasons,
        'low_position_catalyst_score': low_position_catalyst_score,
        'main_theme_alignment_score': main_theme_alignment_score,
        'main_theme_core_score': main_theme_core_score,
        'mainboard_auxiliary_evidence_status': str(row.get('mainboard_auxiliary_evidence_status') or details.get('mainboard_auxiliary_evidence_status') or ''),
        'mainboard_auxiliary_missing_domains': normalize_tag_list(row.get('mainboard_auxiliary_missing_domains') or details.get('mainboard_auxiliary_missing_domains') or []),
        'announcement_catalyst_score': announcement_catalyst_score,
        'sector_news_catalyst_score': sector_news_catalyst_score,
        'limitup_reason_quality_score': limitup_reason_quality_score,
        'risk_notice_penalty': risk_notice_penalty,
        'mainboard_auxiliary_confidence': mainboard_auxiliary_confidence,
        'announcement_evidence': row.get('announcement_evidence') or [],
        'news_evidence': row.get('news_evidence') or {},
        'sector_news_evidence': row.get('sector_news_evidence') or [],
        'limitup_reason_evidence': row.get('limitup_reason_evidence') or [],
        'risk_notice_evidence': row.get('risk_notice_evidence') or [],
        'close_position_score': safe_float(row.get('close_position_score')),
        'hsgt_institutional_flow': safe_float(row.get('hsgt_institutional_flow')) or safe_float((row.get('data_directory_capital_flow') or {}).get('hsgt_institutional_flow')),
        'volume_ratio': safe_float(row.get('volume_ratio')),
        'signal_pct': safe_float(row.get('signal_pct')),
        'full_universe_fund_pctile': safe_float(row.get('full_universe_fund_pctile')),
        'full_universe_amount_pctile': safe_float(row.get('full_universe_amount_pctile')),
        'risk_penalty': safe_float(row.get('risk_penalty')),
        'data_gate_status': str(row.get('data_gate_status') or row.get('data_gate') or ''),
        'candidate_evidence_status': str(row.get('candidate_evidence_status') or ''),
        'source_time': str(row.get('source_time') or ''),
        'runner_asof_time': str(row.get('runner_asof_time') or row.get('_runner_asof_time') or ''),
        'one_lot_cost': safe_float(row.get('one_lot_cost')),
        'regulatory_hard_block': regulatory_hard_block,
        'opportunity_hard_block': str(row.get('opportunity_hard_block') or ''),
        'blocked_reasons': [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)],
    }
    profile['research_signals'] = build_research_signals_from_profile(profile, row, bundle)
    profile['research_panel_overall'] = str((profile['research_signals'].get('research_panel') or {}).get('overall') or '')
    profile['catalyst_quality_category'] = str((profile['research_signals'].get('catalyst_quality') or {}).get('category') or '')
    profile['a_share_risk_review_disqualified_for_paper_pick'] = bool((profile['research_signals'].get('a_share_risk_review') or {}).get('disqualified_for_paper_pick'))
    profile['historical_pattern_name'] = str((profile['research_signals'].get('historical_pattern') or {}).get('pattern_name') or '')
    return profile

def build_research_signals_from_profile(profile: Dict[str, Any], row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    row = row if isinstance(row, dict) else {}
    bundle = bundle if isinstance(bundle, dict) else {}
    raw = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    source_time = str(profile.get('source_time') or bundle.get('source_time') or row.get('source_time') or '')
    sector_tags = normalize_tag_list((raw.get('sector_mapping') or {}).get('sectors') or profile.get('sector_opportunity_tags') or row.get('sector_opportunity_tags') or [])
    industry_chain_tags = normalize_tag_list(raw.get('industry_chain_tags') or sector_tags or profile.get('sector_opportunity_tags') or [])

    quality = dict(raw.get('catalyst_quality') or {})
    if not quality:
        category = str(profile.get('catalyst_quality_category') or '')
        if not category:
            news_categories = normalize_tag_list(profile.get('news_catalyst_quality_categories') or [])
            if 'regulatory_notice' in news_categories:
                category = 'regulatory_notice'
            elif 'risk_notice' in news_categories:
                category = 'risk_notice'
            elif 'positive_catalyst' in news_categories:
                category = 'positive_catalyst'
            elif 'sector_catalyst' in news_categories:
                category = 'sector_catalyst'
            elif profile.get('news_catalyst_strength') or profile.get('sector_news_strength') or profile.get('sector_catalyst_score'):
                category = 'sector_catalyst' if (profile.get('sector_news_strength') or profile.get('sector_catalyst_score') or sector_tags) else 'positive_catalyst'
            else:
                category = 'neutral'
        quality = {
            'category': category,
            'confidence': round(min(1.0, max(
                safe_float(profile.get('news_catalyst_strength')) or 0.0,
                safe_float(profile.get('sector_news_strength')) or 0.0,
                safe_float(profile.get('sector_catalyst_score')) or 0.0,
            )), 4),
            'freshness_minutes': None,
            'evidence_refs': [],
            'usable_for_candidate_generation': category in ('positive_catalyst', 'sector_catalyst'),
            'usable_for_paper_pick': category in ('positive_catalyst', 'sector_catalyst'),
            'risk_terms': [],
            'positive_terms': [],
            'sector_terms': sector_tags,
            'regulatory_hard_block': category in ('regulatory_notice', 'risk_notice'),
            'observation': 'risk_observation' if category in ('regulatory_notice', 'risk_notice') else ('catalyst_observation' if category in ('positive_catalyst', 'sector_catalyst') else 'neutral_observation'),
        }
    else:
        quality.setdefault('category', str(profile.get('catalyst_quality_category') or 'neutral'))
        quality.setdefault('confidence', round(min(1.0, max(
            safe_float(profile.get('news_catalyst_strength')) or 0.0,
            safe_float(profile.get('sector_news_strength')) or 0.0,
            safe_float(profile.get('sector_catalyst_score')) or 0.0,
        )), 4))
        quality.setdefault('freshness_minutes', None)
        quality.setdefault('evidence_refs', [])
        quality.setdefault('usable_for_candidate_generation', quality.get('category') in ('positive_catalyst', 'sector_catalyst'))
        quality.setdefault('usable_for_paper_pick', quality.get('usable_for_candidate_generation'))
        quality.setdefault('risk_terms', [])
        quality.setdefault('positive_terms', [])
        quality.setdefault('sector_terms', sector_tags)
        quality.setdefault('regulatory_hard_block', quality.get('category') in ('regulatory_notice', 'risk_notice'))
        quality.setdefault('observation', 'risk_observation' if quality.get('category') in ('regulatory_notice', 'risk_notice') else ('catalyst_observation' if quality.get('category') in ('positive_catalyst', 'sector_catalyst') else 'neutral_observation'))
        quality['sector_terms'] = normalize_tag_list(quality.get('sector_terms') or sector_tags)
        if quality.get('category') == 'neutral' and profile.get('catalyst_quality_category') and profile.get('catalyst_quality_category') != 'neutral':
            quality['category'] = profile.get('catalyst_quality_category')

    sector_mapping = dict(raw.get('sector_mapping') or {})
    if not sector_mapping:
        sector_mapping = {
            'sectors': sector_tags,
            'related_symbols': [],
            'mapping_confidence': round(min(1.0, max(
                safe_float(profile.get('sector_opportunity_score')) or 0.0,
                safe_float(profile.get('sector_catalyst_score')) or 0.0,
                safe_float(profile.get('news_catalyst_strength')) or 0.0,
            )), 4),
        }
    else:
        sector_mapping['sectors'] = normalize_tag_list(sector_mapping.get('sectors') or sector_tags)
        sector_mapping['related_symbols'] = normalize_tag_list(sector_mapping.get('related_symbols') or [])
        sector_mapping['mapping_confidence'] = round(min(1.0, safe_float(sector_mapping.get('mapping_confidence')) or max(
            safe_float(profile.get('sector_opportunity_score')) or 0.0,
            safe_float(profile.get('sector_catalyst_score')) or 0.0,
            safe_float(profile.get('news_catalyst_strength')) or 0.0,
        )), 4)

    risk_review = dict(raw.get('a_share_risk_review') or {})
    if not risk_review:
        disqualified = bool(profile.get('a_share_risk_review_disqualified_for_paper_pick') or profile.get('regulatory_hard_block'))
        risk_review = {
            'abnormal_movement_notice': disqualified,
            'risk_warning_notice': disqualified,
            'reduction_risk': False,
            'financial_red_flags': [],
            'lhb_risk_flags': [],
            'disqualified_for_paper_pick': disqualified,
        }
    else:
        risk_review['abnormal_movement_notice'] = bool(risk_review.get('abnormal_movement_notice'))
        risk_review['risk_warning_notice'] = bool(risk_review.get('risk_warning_notice'))
        risk_review['reduction_risk'] = bool(risk_review.get('reduction_risk'))
        risk_review['financial_red_flags'] = normalize_tag_list(risk_review.get('financial_red_flags') or [])
        risk_review['lhb_risk_flags'] = normalize_tag_list(risk_review.get('lhb_risk_flags') or [])
        risk_review['disqualified_for_paper_pick'] = bool(risk_review.get('disqualified_for_paper_pick') or risk_review['abnormal_movement_notice'] or risk_review['risk_warning_notice'] or risk_review['reduction_risk'] or risk_review['financial_red_flags'] or risk_review['lhb_risk_flags'])

    adversarial_review = dict(raw.get('adversarial_review') or {})
    if not adversarial_review:
        bear_case_flags = []
        disqualifying_flags = []
        if quality.get('category') == 'stale':
            bear_case_flags.append('stale_news')
        if (profile.get('sector_opportunity_score') or 0.0) > 0 and (profile.get('news_catalyst_strength') or 0.0) <= 0.1 and (profile.get('sector_news_strength') or 0.0) <= 0.1:
            bear_case_flags.append('concept_hype_without_company_link')
        if (profile.get('volume_ratio') or 0.0) < 1.2 and (profile.get('close_position_score') or 0.0) < 0.55:
            bear_case_flags.append('weak_fund_confirmation')
        if (profile.get('signal_pct') or 0.0) >= 7.0 or (profile.get('close_position_score') or 0.0) >= 0.9:
            bear_case_flags.append('near_limit_chase')
        if quality.get('category') in ('risk_notice', 'regulatory_notice'):
            disqualifying_flags.extend(['risk_notice_as_catalyst', 'regulatory_hard_block'])
        if risk_review.get('financial_red_flags'):
            disqualifying_flags.append('financial_red_flag')
        if not sector_mapping.get('sectors') and not quality.get('evidence_refs'):
            disqualifying_flags.append('evidence_missing')
        adversarial_review = {
            'bear_case_flags': bear_case_flags,
            'disqualifying_flags': list(dict.fromkeys(disqualifying_flags)),
        }
    else:
        adversarial_review['bear_case_flags'] = normalize_tag_list(adversarial_review.get('bear_case_flags') or [])
        adversarial_review['disqualifying_flags'] = normalize_tag_list(adversarial_review.get('disqualifying_flags') or [])

    historical_pattern = dict(raw.get('historical_pattern') or {})
    if not historical_pattern:
        pattern_name = 'formal_high_score'
        setup_type = str(profile.get('setup_type') or row.get('setup_type') or row.get('setup_type_refined') or '')
        search_layer_hint = str(profile.get('search_layer_hint') or row.get('search_layer_hint') or '')
        candidate_stage = str(profile.get('candidate_stage') or '')
        if search_layer_hint == 'news_catalyst_low_position' or setup_type in ('NEWS_CATALYST_LOW_POSITION', 'TOPIC_FUND_IGNITION'):
            pattern_name = 'news_catalyst_low_position'
        elif search_layer_hint == 'sector_catalyst_low_position' or setup_type == 'SECTOR_NEWS_LOW_POSITION':
            pattern_name = 'sector_catalyst_low_position'
        elif search_layer_hint == 'intraday_alert_reversal' or setup_type == 'INTRADAY_ALERT_REVERSAL':
            pattern_name = 'intraday_alert_reversal'
        elif candidate_stage == 'underwater' or 'UNDERWATER' in setup_type:
            pattern_name = 'underwater_reversal'
        elif (profile.get('low_position_catalyst_score') or 0.0) >= 0.6:
            pattern_name = 'topic_fund_ignition'
        elif profile.get('base_score') is not None and (profile.get('signal_pct') or 0.0) >= 7.0:
            pattern_name = 'formal_high_score'
        sector_label = ''
        for value in sector_mapping.get('sectors') or []:
            if value:
                sector_label = str(value)
                break
        if not sector_label:
            for value in industry_chain_tags:
                if value in SECTOR_RESEARCH_MAP:
                    sector_label = str(value)
                    break
        if not sector_label:
            sector_label = str(row.get('code') or row.get('symbol') or 'generic')
        historical_pattern = {
            'pattern_name': pattern_name,
            'backtest_score': None,
            'forward_evidence_count': 0,
            'requires_forward_tracking': True,
            'forward_eval_key': f'{pattern_name}:{sector_label}',
        }
    else:
        historical_pattern.setdefault('pattern_name', 'formal_high_score')
        historical_pattern.setdefault('backtest_score', None)
        historical_pattern.setdefault('forward_evidence_count', 0)
        historical_pattern.setdefault('requires_forward_tracking', True)
        historical_pattern.setdefault('forward_eval_key', f"{historical_pattern.get('pattern_name') or 'formal_high_score'}:{(sector_mapping.get('sectors') or [str(row.get('code') or row.get('symbol') or 'generic')])[0]}")

    research_signals = {
        'industry_chain_tags': industry_chain_tags,
        'catalyst_quality': quality,
        'sector_mapping': sector_mapping,
        'a_share_risk_review': risk_review,
        'adversarial_review': adversarial_review,
        'historical_pattern': historical_pattern,
    }
    research_panel = dict(raw.get('research_panel') or {})
    if not research_panel or 'overall' not in research_panel:
        research_panel = build_research_panel(research_signals, row)
    else:
        research_panel['news_analyst'] = research_panel.get('news_analyst') or 'PARTIAL'
        research_panel['sector_analyst'] = research_panel.get('sector_analyst') or 'PARTIAL'
        research_panel['technical_analyst'] = research_panel.get('technical_analyst') or 'PARTIAL'
        research_panel['risk_analyst'] = research_panel.get('risk_analyst') or 'PASS'
        research_panel['bear_case'] = research_panel.get('bear_case') or 'PASS'
        research_panel['overall'] = research_panel.get('overall') or 'PARTIAL'
    research_signals['research_panel'] = research_panel
    candidate_code = str(row.get('code') or row.get('symbol') or '').strip()
    if candidate_code and bundle:
        content_by_code = bundle.get('data_directory_content_by_code') or {}
        matched_content = content_by_code.get(candidate_code) or []
        if matched_content:
            research_signals['data_directory_content_evidence'] = {
                'record_count': len(matched_content),
                'item_keys': sorted(set(str(r.get('item_key') or '') for r in matched_content if r.get('item_key'))),
                'section_titles': sorted(set(str(r.get('section_title') or '') for r in matched_content if r.get('section_title'))),
                'records': matched_content[:20],
            }
            for rec in matched_content:
                item_key = str(rec.get('item_key') or '')
                if 'research_reports' in item_key or 'report' in item_key:
                    quality.setdefault('positive_terms', []).append(str(rec.get('title') or '')[:80])
                if 'financial' in item_key or 'earnings' in item_key:
                    quality.setdefault('positive_terms', []).append(str(rec.get('title') or '')[:80])
                if 'halt' in item_key or 'trading_halts' in item_key:
                    quality['regulatory_hard_block'] = True
                    quality.setdefault('risk_terms', []).append(str(rec.get('title') or '')[:80])
    fund_flow = (bundle or {}).get('data_directory_capital_flow_by_code', {}).get(candidate_code, {})
    if fund_flow:
        research_signals['data_directory_capital_flow'] = fund_flow
        net_inflow = safe_float(fund_flow.get('main_force_net_inflow')) or 0.0
        if net_inflow > 0:
            quality.setdefault('positive_terms', []).append(f'主力净流入{net_inflow/100000000:.2f}亿')
            existing_inflow = safe_float(row.get('net_inflow_main')) or 0.0
            if existing_inflow <= 0:
                row['net_inflow_main'] = net_inflow
                row['_net_inflow_main_from_data_directory'] = True
    return research_signals

def build_research_panel(research_signals: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    research_signals = research_signals if isinstance(research_signals, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    quality = research_signals.get('catalyst_quality') or {}
    sector_mapping = research_signals.get('sector_mapping') or {}
    risk_review = research_signals.get('a_share_risk_review') or {}
    adversarial_review = research_signals.get('adversarial_review') or {}

    if quality.get('category') in ('positive_catalyst', 'sector_catalyst'):
        news_analyst = 'PASS'
    elif quality.get('category') == 'neutral' and (
        quality.get('evidence_refs') or quality.get('industry_chain_tags') or quality.get('positive_terms')
    ):
        news_analyst = 'PARTIAL'
    else:
        news_analyst = 'FAIL'

    mapping_confidence = safe_float(sector_mapping.get('mapping_confidence'))
    if mapping_confidence is None:
        mapping_confidence = max(
            safe_float(candidate.get('sector_opportunity_score')) or 0.0,
            safe_float(candidate.get('sector_catalyst_score')) or 0.0,
            safe_float(candidate.get('news_catalyst_strength')) or 0.0,
        )
    if mapping_confidence >= 0.5:
        sector_analyst = 'PASS'
    elif sector_mapping.get('sectors') or sector_mapping.get('related_symbols'):
        sector_analyst = 'PARTIAL'
    else:
        sector_analyst = 'FAIL'

    low_position_catalyst_score = safe_float(candidate.get('low_position_catalyst_score')) or 0.0
    early_opportunity_score = safe_float(candidate.get('early_opportunity_score')) or 0.0
    if low_position_catalyst_score >= 0.6 or early_opportunity_score >= 0.65:
        technical_analyst = 'PASS'
    elif (
        (safe_float(candidate.get('volume_ratio')) or 0.0) >= 1.2
        or (safe_float(candidate.get('close_position_score')) or 0.0) >= 0.55
        or (safe_float(candidate.get('full_universe_fund_pctile')) or 0.0) >= 0.4
        or (safe_float(candidate.get('time_series_momentum')) or 0.0) >= 0.15
        or (safe_float(candidate.get('fund_flow_momentum')) or 0.0) >= 0.25
    ):
        technical_analyst = 'PARTIAL'
    else:
        technical_analyst = 'FAIL'

    risk_analyst = 'FAIL' if risk_review.get('disqualified_for_paper_pick') else 'PASS'
    if adversarial_review.get('disqualifying_flags'):
        bear_case = 'FAIL'
    elif adversarial_review.get('bear_case_flags'):
        bear_case = 'PARTIAL'
    else:
        bear_case = 'PASS'

    statuses = [news_analyst, sector_analyst, technical_analyst, risk_analyst, bear_case]
    if 'FAIL' in (risk_analyst, bear_case):
        overall = 'FAIL'
    elif statuses.count('PASS') >= 3:
        overall = 'PASS'
    else:
        overall = 'PARTIAL'

    return {
        'news_analyst': news_analyst,
        'sector_analyst': sector_analyst,
        'technical_analyst': technical_analyst,
        'risk_analyst': risk_analyst,
        'bear_case': bear_case,
        'overall': overall,
    }

def early_opportunity_score_for_row(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> float:
    profile = _cached_structured_signal_profile(row, bundle) if isinstance(bundle, dict) else structured_signal_profile(row)
    existing = profile['early_opportunity_score']
    if existing is not None:
        return max(0.0, min(1.0, existing))
    signal_pct = profile['signal_pct']
    candidate_stage = profile['candidate_stage'] or signal_stage_bucket(signal_pct)
    score = {
        'underwater': 0.26,
        'flat_0_to_3': 0.24,
        'early_3_to_5': 0.20,
        'mid_5_to_7': 0.10,
        'high_7_to_9': -0.08,
        'near_limit_9_plus': -0.18,
    }.get(candidate_stage, 0.0)
    score += min(0.18, (profile['sector_opportunity_score'] or 0.0) * 0.18)
    score += min(0.14, (profile['fund_flow_momentum'] or 0.0) * 0.14)
    score += min(0.12, (profile['time_series_momentum'] or 0.0) * 0.12)
    score += min(0.14, (profile['structured_component_details'].get('weak_to_strong_reversal') or 0.0) * 0.14)
    score += min(0.12, (profile['structured_component_details'].get('pre_limitup_anomaly') or 0.0) * 0.12)
    score += min(0.18, (profile['low_position_catalyst_score'] or 0.0) * 0.18)
    score += min(0.08, max(0.0, profile['close_position_score'] or 0.0) * 0.08)
    score += min(0.06, max(0.0, profile['hsgt_institutional_flow'] or 0.0) * 0.06)
    score += min(0.08, (profile['full_universe_fund_pctile'] or 0.0) * 0.08)
    score += min(0.10, min(1.0, max(0.0, profile['volume_ratio'] or 0.0) / 3.0) * 0.10)
    if signal_pct is not None and signal_pct >= 8.0:
        score -= min(0.22, ((signal_pct - 8.0) / 2.0) * 0.16 + 0.05)
    if signal_pct is not None and signal_pct <= 0.0:
        score += 0.04
    return max(0.0, min(1.0, score))

def structured_signal_present(row: Dict[str, Any]) -> bool:
    profile = structured_signal_profile(row)
    return (
        profile['structured_score'] is not None
        or profile['sector_opportunity_score'] is not None
        or bool(profile['vei_phase_d_tags'])
        or bool(profile['structured_component_details'])
    )

def normalized_block_bucket(reason: str) -> str:
    text = str(reason or '').lower()
    if 'near_limit_up_risk' in text or 'near_limit_up' in text:
        return 'near_limit_up_risk'
    if 'main_board_breadth_too_low' in text:
        return 'main_board_breadth_too_low'
    if 'opp_too_low' in text:
        return 'opp_too_low'
    if 'risk_too_high' in text:
        return 'risk_too_high'
    return ''

def block_reason_bucket(reason: str) -> str:
    normalized = normalized_block_bucket(reason)
    if normalized:
        return normalized
    text = str(reason or '')
    if text.startswith('main_board_breadth_too_low:'):
        return 'main_board_breadth_too_low'
    if ':' in text:
        text = text.split(':', 1)[1]
    return text.strip() or 'unknown'

def formal_blockers_for_row(row: Dict[str, Any]) -> List[str]:
    reasons = [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)]
    regulatory_block = regulatory_hard_block_reason(row, {})
    opportunity_block = opportunity_hard_block_reason(row, {}) or limitup_quality_block_reason(row, {})
    if regulatory_block:
        reasons.append('regulatory_hard_block:' + regulatory_block)
    if opportunity_block:
        reasons.append('opportunity_hard_block:' + opportunity_block)
    unique: List[str] = []
    seen = set()
    for reason in reasons:
        if reason not in seen:
            unique.append(reason)
            seen.add(reason)
    return unique

def why_not_formal_candidate(row: Dict[str, Any]) -> str:
    reasons = formal_blockers_for_row(row)
    parts: List[str] = []
    if safe_float(row.get('score')) is None:
        parts.append('score=null')
    if reasons:
        parts.append('blocked=' + ','.join(reasons))
    elif safe_float(row.get('score')) is not None:
        parts.append('not_selected_in_formal_basket')
    return ';'.join(parts) if parts else 'not_selected_in_formal_basket'

def structured_observation_candidate(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = structured_signal_profile(row)
    eligibility = paper_pick_eligibility_profile(row, bundle)
    formal_blockers = eligibility['blockers']
    observation_class = 'STRUCTURED_SHADOW_SIGNAL'
    if profile['sector_opportunity_score'] and profile['sector_opportunity_score'] > 0:
        observation_class = 'STRUCTURED_SECTOR_OPPORTUNITY'
    elif profile['vei_phase_d_tags']:
        observation_class = 'STRUCTURED_VEI_SIGNAL'
    return {
        'symbol': symbol_for(row),
        'name': row.get('name'),
        'observation_class': observation_class,
        'price': row.get('price'),
        'one_lot_cost': row.get('one_lot_cost') if row.get('one_lot_cost') is not None else (
            safe_float(row.get('price')) * 100 if safe_float(row.get('price')) is not None else None
        ),
        'source_time': profile['source_time'] or str((bundle or {}).get('source_time') or ''),
        'runner_asof_time': profile['runner_asof_time'] or str((bundle or {}).get('_runner_asof_time') or (bundle or {}).get('runner_asof_time') or (bundle or {}).get('asof_time') or ''),
        'data_gate_status': profile['data_gate_status'] or str((bundle or {}).get('data_gate_status') or ''),
        'candidate_evidence_status': profile['candidate_evidence_status'] or str((bundle or {}).get('candidate_evidence_status') or ''),
        'candidate_evidence_domain_counts': row.get('candidate_evidence_domain_counts', {}),
        'candidate_evidence_matched_domains': row.get('candidate_evidence_matched_domains', []),
        'candidate_evidence_missing_domains': row.get('candidate_evidence_missing_domains', []),
        'enhanced_evidence_domain_counts': row.get('enhanced_evidence_domain_counts', {}),
        'enhanced_evidence_matched_domains': row.get('enhanced_evidence_matched_domains', []),
        'enhanced_evidence_missing_domains': row.get('enhanced_evidence_missing_domains', []),
        'experimental_evidence_domain_counts': row.get('experimental_evidence_domain_counts', {}),
        'experimental_evidence_matched_domains': row.get('experimental_evidence_matched_domains', []),
        'experimental_evidence_missing_domains': row.get('experimental_evidence_missing_domains', []),
        'structured_score_components': row.get('structured_score_components') or row.get('components'),
        'structured_score_mode': row.get('structured_score_mode') or row.get('mode'),
        'risk_penalty': profile['risk_penalty'],
        'regulatory_hard_block': profile['regulatory_hard_block'],
        'opportunity_hard_block': profile['opportunity_hard_block'],
        'candidate_stage': profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']),
        'early_opportunity_score': early_opportunity_score_for_row(row),
        'setup_type': profile['setup_type'] or str(row.get('setup_type') or row.get('setup_type_refined') or ''),
        'near_limit_up_risk': bool(row.get('near_limit_up_risk')) or any(
            normalized_block_bucket(reason) == 'near_limit_up_risk'
            for reason in (row.get('blocked_reasons') or [])
        ),
        'blocked_reasons': row.get('blocked_reasons') or [],
        'formal_eligible': eligibility['eligible'],
        'formal_blockers': formal_blockers,
        'paper_pick_eligibility': eligibility,
        'structured_score': profile['structured_score'],
        'base_score': profile['base_score'],
        'score': profile['base_score'],
        'sector_opportunity_score': profile['sector_opportunity_score'],
        'sector_opportunity_tags': profile['sector_opportunity_tags'],
        'sector_news_strength': profile['sector_news_strength'],
        'vei_phase_d_tags': profile['vei_phase_d_tags'],
        'search_layer_hint': profile['search_layer_hint'],
        'news_catalyst_strength': profile['news_catalyst_strength'],
        'mainboard_auxiliary_evidence_status': profile['mainboard_auxiliary_evidence_status'],
        'mainboard_auxiliary_missing_domains': profile['mainboard_auxiliary_missing_domains'],
        'announcement_catalyst_score': profile['announcement_catalyst_score'],
        'sector_news_catalyst_score': profile['sector_news_catalyst_score'],
        'limitup_reason_quality_score': profile['limitup_reason_quality_score'],
        'risk_notice_penalty': profile['risk_notice_penalty'],
        'mainboard_auxiliary_confidence': profile['mainboard_auxiliary_confidence'],
        'news_catalyst_quality_categories': profile['news_catalyst_quality_categories'],
        'sector_catalyst_score': profile['sector_catalyst_score'],
        'topic_propagation_score': profile['topic_propagation_score'],
        'intraday_alert_strength': profile['intraday_alert_strength'],
        'limitup_reason_propagation_score': profile['limitup_reason_propagation_score'],
        'low_position_catalyst_score': profile['low_position_catalyst_score'],
        'structured_component_details': profile['structured_component_details'],
        'research_signals': profile['research_signals'],
        'research_panel_overall': profile['research_panel_overall'],
        'catalyst_quality_category': profile['catalyst_quality_category'],
        'a_share_risk_review_disqualified_for_paper_pick': profile['a_share_risk_review_disqualified_for_paper_pick'],
        'historical_pattern_name': profile['historical_pattern_name'],
        'why_not_formal_candidate': why_not_formal_candidate(row),
        **repo_contribution_context(row),
    }

def structured_formal_impact_summary(enriched_rows: List[Dict[str, Any]], formal_rows: List[Dict[str, Any]], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    formal_symbols = {symbol_for(row) for row in formal_rows if symbol_for(row)}
    structured_rows = [row for row in enriched_rows if structured_signal_present(row)]
    structured_only_rows = [
        row for row in structured_rows
        if safe_float(row.get('score')) is None and symbol_for(row) not in formal_symbols
    ]
    stage_priority = {
        'underwater': 5,
        'flat_0_to_3': 4,
        'early_3_to_5': 3,
        'mid_5_to_7': 2,
        'high_7_to_9': 1,
        'near_limit_9_plus': 0,
        'unknown': 0,
    }

    def structured_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        profile = structured_signal_profile(row)
        return (
            early_opportunity_score_for_row(row),
            stage_priority.get(profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']), 0),
            profile['sector_opportunity_score'] or 0.0,
            profile['structured_score'] or 0.0,
            safe_float(row.get('amount_pctile_rule')) or 0.0,
        )

    sector_opportunity_rows = [
        row for row in structured_only_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) > 0
    ]
    structured_only_non_sector_rows = [
        row for row in structured_only_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) <= 0
    ]
    structured_only_sorted = sorted(structured_only_non_sector_rows, key=structured_sort_key, reverse=True)

    def sector_sort_key(row: Dict[str, Any]) -> Tuple[float, float, float, float]:
        profile = structured_signal_profile(row)
        return (
            profile['sector_opportunity_score'] or 0.0,
            early_opportunity_score_for_row(row),
            stage_priority.get(profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']), 0),
            safe_float(row.get('structured_score')) or 0.0,
        )

    counts = Counter()
    for row in structured_only_rows:
        buckets = {normalized_block_bucket(reason) for reason in formal_blockers_for_row(row)}
        for bucket in buckets:
            if bucket:
                counts[bucket] += 1

    top_structured_only_candidates = [structured_observation_candidate(row, bundle) for row in structured_only_sorted[:RESEARCH_BASKET_SIZE]]
    sector_opportunity_candidates = [
        structured_observation_candidate(row, bundle)
        for row in sorted(sector_opportunity_rows, key=sector_sort_key, reverse=True)
    ]
    structured_observation_candidates = []
    seen_symbols = set()
    for candidate in top_structured_only_candidates + sector_opportunity_candidates:
        symbol = candidate.get('symbol') or candidate.get('code')
        if not symbol or symbol in seen_symbols:
            continue
        structured_observation_candidates.append(candidate)
        seen_symbols.add(symbol)
    structured_sector_opportunity_count = sum(
        1 for row in structured_rows
        if (structured_signal_profile(row)['sector_opportunity_score'] or 0) > 0
    )
    return {
        'structured_candidate_count': len(structured_rows),
        'structured_sector_opportunity_count': structured_sector_opportunity_count,
        'structured_only_not_in_formal_basket_count': len(structured_only_rows),
        'top_structured_only_candidates': top_structured_only_candidates,
        'sector_opportunity_candidates': sector_opportunity_candidates,
        'structured_observation_candidates': structured_observation_candidates,
        'block_reason_counts': dict(counts),
    }

def limitup_quality_block_reason(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> str:
    bundle = bundle if isinstance(bundle, dict) else {}
    signal_pct = safe_float(row.get('signal_pct'))
    close_position_score = safe_float(row.get('close_position_score'))
    if signal_pct is None:
        return ''

    candidate_stage = str(row.get('candidate_stage') or signal_stage_bucket(signal_pct))
    if signal_pct < 5.0 and candidate_stage not in ('high_7_to_9', 'near_limit_9_plus'):
        return ''
    if signal_pct < 7.0 and candidate_stage not in ('high_7_to_9', 'near_limit_9_plus') and (close_position_score is None or close_position_score < 0.70):
        return ''

    market_context = market_adaptive_context(row, bundle)
    supportive_market = bool(market_context.get('supportive_market'))
    weak_acceptance_market = bool(market_context.get('weak_acceptance_market'))
    broken_limit_pressure = bool(market_context.get('broken_limit_pressure'))
    thresholds = market_adaptive_thresholds(candidate_stage, market_context)

    limitup_reason = structured_component(row, 'limitup_reason_strength')
    seal_order = structured_component(row, 'seal_order_strength')
    order_book = structured_component(row, 'order_book_pressure')
    components_seen = [x for x in (limitup_reason, seal_order, order_book) if x is not None]
    auxiliary_status = str(row.get('mainboard_auxiliary_evidence_status') or '')
    auxiliary_limitup_quality = safe_float(row.get('limitup_reason_quality_score')) or 0.0
    auxiliary_news = safe_float(row.get('news_catalyst_strength')) or 0.0
    auxiliary_announcement = safe_float(row.get('announcement_catalyst_score')) or 0.0
    auxiliary_sector_news = safe_float(row.get('sector_news_catalyst_score')) or 0.0
    auxiliary_confirmation = max(auxiliary_limitup_quality, auxiliary_news, auxiliary_announcement, auxiliary_sector_news)
    if candidate_stage in ('high_7_to_9', 'near_limit_9_plus') and auxiliary_confirmation >= 0.65:
        return ''
    if not components_seen:
        return 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' if auxiliary_status else ''

    if max(components_seen) >= thresholds['component_min']:
        return ''

    limitup_capture_score = safe_float(row.get('limitup_capture_score'))
    if limitup_capture_score is None:
        limitup_capture_score = safe_float((row.get('structured_component_details') or {}).get('limitup_capture_score'))
    limitup_capture_profile = str(row.get('limitup_capture_profile') or (row.get('structured_component_details') or {}).get('limitup_capture_profile') or '')
    limitup_reason_propagation_score = safe_float(row.get('limitup_reason_propagation_score'))
    if limitup_reason_propagation_score is None:
        limitup_reason_propagation_score = safe_float((row.get('structured_component_details') or {}).get('limitup_reason_propagation_score'))
    intraday_alert_strength = safe_float(row.get('intraday_alert_strength'))
    if intraday_alert_strength is None:
        intraday_alert_strength = safe_float((row.get('structured_component_details') or {}).get('intraday_alert_strength'))
    main_theme_alignment_score = safe_float(row.get('main_theme_alignment_score'))
    if main_theme_alignment_score is None:
        main_theme_alignment_score = safe_float((row.get('structured_component_details') or {}).get('main_theme_alignment_score'))
    main_theme_core_score = safe_float(row.get('main_theme_core_score'))
    if main_theme_core_score is None:
        main_theme_core_score = safe_float((row.get('structured_component_details') or {}).get('main_theme_core_score'))

    if (
        supportive_market
        and not weak_acceptance_market
        and candidate_stage == 'high_7_to_9'
        and close_position_score is not None
        and close_position_score >= 0.84
        and sum(1 for value in components_seen if value >= 0.52) >= 2
    ):
        return ''

    if (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and not weak_acceptance_market
        and close_position_score is not None
        and close_position_score >= (0.82 if candidate_stage == 'near_limit_9_plus' else 0.80)
        and limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE'
        and (limitup_capture_score or 0.0) >= 0.62
        and (limitup_reason_propagation_score or 0.0) >= 0.60
        and (
            (intraday_alert_strength or 0.0) >= 0.90
            or (main_theme_alignment_score or 0.0) >= 0.55
            or (main_theme_core_score or 0.0) >= 0.60
        )
    ):
        return ''

    if (
        broken_limit_pressure
        and candidate_stage == 'high_7_to_9'
        and close_position_score is not None
        and close_position_score < 0.84
    ):
        return 'BROKEN_LIMIT_WEAK_FOLLOW_THROUGH_CONFIRMATION_GAP'
    return 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'

def candidate_capital_risk_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    def first_value(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)

    capital_flow = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
    failed_limitup = bool(
        row.get('failed_limitup')
        or row.get('broken_limit_risk')
        or row.get('is_broken_limit')
        or row.get('opened_limit_up')
        or row.get('炸板')
    )
    main_buy_net = first_value(
        safe_float(row.get('main_buy_net')),
        safe_float(row.get('main_buy_net_inflow')),
        safe_float(capital_flow.get('main_buy_net')),
        safe_float(capital_flow.get('main_buy_net_inflow')),
        safe_float(capital_flow.get('main_force_net_inflow')),
        safe_float(row.get('net_inflow_main')),
        0.0,
    )
    dark_pool_net = first_value(
        safe_float(row.get('dark_pool_net')),
        safe_float(row.get('dark_pool_net_inflow')),
        safe_float(row.get('hidden_fund_net_inflow')),
        safe_float(capital_flow.get('dark_pool_net')),
        safe_float(capital_flow.get('dark_pool_net_inflow')),
        safe_float(capital_flow.get('hidden_fund_net_inflow')),
        0.0,
    )
    popularity_value = row.get('popularity_rank')
    if isinstance(popularity_value, dict):
        popularity_value = first_value(
            safe_float(popularity_value.get('rank')),
            safe_float(popularity_value.get('ranking')),
            safe_float(popularity_value.get('current_rank')),
        )
    popularity_rank = safe_float(popularity_value)
    failed_limitup_risk = 1.0 if failed_limitup else 0.0
    main_buy_outflow_pressure = min(1.0, abs(main_buy_net) / 500000000.0) if main_buy_net < 0 else 0.0
    dark_pool_inflow_support = min(1.0, dark_pool_net / 500000000.0) if dark_pool_net > 0 else 0.0
    if popularity_rank is None or popularity_rank <= 0:
        popularity_crowding_risk = 0.0
    elif popularity_rank == 1:
        popularity_crowding_risk = 1.0
    elif popularity_rank <= 3:
        popularity_crowding_risk = 0.8
    elif popularity_rank <= 10:
        popularity_crowding_risk = 0.5
    else:
        popularity_crowding_risk = 0.0
    announcement_strength = safe_float(row.get('announcement_catalyst_score')) or 0.0
    news_strength = safe_float(row.get('news_catalyst_strength')) or 0.0
    sector_news_strength = safe_float(row.get('sector_news_catalyst_score')) or 0.0
    continuation_gene = continuation_gene_evidence(row)['effective_score']
    direct_catalyst_strength = max(announcement_strength, news_strength)
    catalyst_strength = max(direct_catalyst_strength, sector_news_strength * 0.50)
    weak_limitup_confirmation = max(safe_float(row.get('limitup_reason_quality_score')) or 0.0, continuation_gene) < 0.45
    profit_taking_pressure = min(
        1.0,
        failed_limitup_risk * 0.45
        + popularity_crowding_risk * 0.35
        + (0.20 if bool((row.get('yesterday_one_word_limitup_gene_evidence') or {}).get('candidate_was_yesterday_one_word_limitup')) else 0.0),
    )
    post_broken_board_selloff_risk = min(1.0, failed_limitup_risk * 0.55 + main_buy_outflow_pressure * 0.45)
    high_popularity_trap_risk = min(
        1.0,
        failed_limitup_risk * 0.40 + main_buy_outflow_pressure * 0.35 + popularity_crowding_risk * 0.25,
    ) if catalyst_strength < 0.60 and weak_limitup_confirmation else 0.0
    risk_softened_by_dark_pool_inflow = bool(
        dark_pool_inflow_support >= 0.35
        and (continuation_gene >= 0.35 or direct_catalyst_strength >= 0.50)
    )
    gross_risk = max(post_broken_board_selloff_risk, high_popularity_trap_risk, profit_taking_pressure)
    softened_risk = max(0.0, gross_risk - (dark_pool_inflow_support * 0.30 if risk_softened_by_dark_pool_inflow else 0.0))
    risk_codes = []
    if failed_limitup and main_buy_outflow_pressure > 0:
        risk_codes.append('BROKEN_BOARD_WITH_MAIN_BUY_OUTFLOW')
    if popularity_crowding_risk >= 0.8 and profit_taking_pressure > 0:
        risk_codes.append('POPULARITY_CROWDING_PROFIT_TAKING_RISK')
    if weak_limitup_confirmation and (failed_limitup or popularity_crowding_risk >= 0.8):
        risk_codes.append('HIGH_CHASE_WITH_WEAK_LIMITUP_CONFIRMATION')
    if risk_softened_by_dark_pool_inflow:
        risk_codes.append('risk_softened_by_dark_pool_inflow')
    return {
        'failed_limitup': failed_limitup,
        'main_buy_net': main_buy_net,
        'dark_pool_net': dark_pool_net,
        'popularity_rank': int(popularity_rank) if popularity_rank is not None else None,
        'failed_limitup_risk': round(failed_limitup_risk, 4),
        'main_buy_outflow_pressure': round(main_buy_outflow_pressure, 4),
        'dark_pool_inflow_support': round(dark_pool_inflow_support, 4),
        'popularity_crowding_risk': round(popularity_crowding_risk, 4),
        'profit_taking_pressure': round(profit_taking_pressure, 4),
        'post_broken_board_selloff_risk': round(post_broken_board_selloff_risk, 4),
        'high_popularity_trap_risk': round(high_popularity_trap_risk, 4),
        'capital_divergence_score': round(dark_pool_inflow_support - main_buy_outflow_pressure, 4),
        'risk_softened_by_dark_pool_inflow': risk_softened_by_dark_pool_inflow,
        'risk_penalty_score': round(softened_risk, 4),
        'risk_codes': risk_codes,
        'catalyst_strength': round(catalyst_strength, 4),
        'continuation_gene_score': round(continuation_gene, 4),
        'weak_limitup_confirmation': weak_limitup_confirmation,
    }

def continuation_gene_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """Separate own continuation evidence from sector-only yesterday-limitup proxy.

    Scanner v2 uses the same ``continuation_gene_score`` field for both
    candidate-owned yesterday-limitup evidence and sector breadth proxy. A
    proxy-only row must remain explainable context, not a positive stock-level
    continuation signal in the profit-first rank.
    """
    raw_score = min(1.0, max(0.0, safe_float(row.get('continuation_gene_score')) or 0.0))
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    yesterday_gene = (
        row.get('yesterday_limitup_gene_evidence')
        if isinstance(row.get('yesterday_limitup_gene_evidence'), dict)
        else auxiliary.get('yesterday_limitup_gene')
        if isinstance(auxiliary.get('yesterday_limitup_gene'), dict)
        else {}
    )
    one_word_gene = (
        row.get('yesterday_one_word_limitup_gene_evidence')
        if isinstance(row.get('yesterday_one_word_limitup_gene_evidence'), dict)
        else auxiliary.get('yesterday_one_word_limitup_gene')
        if isinstance(auxiliary.get('yesterday_one_word_limitup_gene'), dict)
        else {}
    )
    previous_pct = safe_float(
        row.get('prev_day_pct_chg')
        if row.get('prev_day_pct_chg') is not None
        else row.get('yesterday_pct_chg')
    ) or 0.0
    explicit_yesterday_missing = bool(
        str(yesterday_gene.get('status') or '').strip().upper() == 'MISSING'
        and not yesterday_gene.get('candidate_was_yesterday_limitup')
        and not yesterday_gene.get('records')
    )
    own_yesterday_evidence = bool(
        (
            (row.get('previous_limitup') or row.get('was_yesterday_limitup'))
            and not explicit_yesterday_missing
        )
        or yesterday_gene.get('candidate_was_yesterday_limitup')
        or yesterday_gene.get('records')
        or one_word_gene.get('candidate_was_yesterday_one_word_limitup')
        or one_word_gene.get('records')
        or previous_pct >= 9.5
    )
    sector_proxy = (
        row.get('sector_yesterday_limitup_gene_proxy')
        if isinstance(row.get('sector_yesterday_limitup_gene_proxy'), dict)
        else auxiliary.get('sector_yesterday_limitup_gene_proxy')
        if isinstance(auxiliary.get('sector_yesterday_limitup_gene_proxy'), dict)
        else {}
    )
    proxy_status = str(sector_proxy.get('status') or '').strip().upper()
    sector_matches = sector_proxy.get('sector_matches') or []
    one_word_matches = sector_proxy.get('one_word_sector_matches') or []
    sector_proxy_match_counts: Dict[str, int] = {}
    for match in [*sector_matches, *one_word_matches]:
        if not isinstance(match, dict):
            continue
        sector_name = str(match.get('sector') or '').strip().lower()
        match_key = sector_name or f'unknown_{len(sector_proxy_match_counts)}'
        match_count = max(1, int(safe_float(match.get('count')) or 1))
        sector_proxy_match_counts[match_key] = max(
            sector_proxy_match_counts.get(match_key, 0),
            match_count,
        )
    sector_proxy_match_count = sum(sector_proxy_match_counts.values())
    direct_limitup_reason = any(
        isinstance(item, dict)
        and not bool(item.get('proxy'))
        and 'sector_proxy' not in str(item.get('source') or '').lower()
        and str(item.get('reason') or item.get('text') or '').strip()
        for item in (row.get('limitup_reason_evidence') or [])
    )
    proxy_declared = bool(
        proxy_status == 'PROXY'
        and (sector_matches or one_word_matches or safe_float(sector_proxy.get('continuation_gene_score')) is not None)
    )
    proxy_only = bool(
        raw_score > 0.0
        and proxy_declared
        and not own_yesterday_evidence
        and not direct_limitup_reason
    )
    effective_score = 0.0 if proxy_only else raw_score
    return {
        'raw_score': round(raw_score, 4),
        'effective_score': round(effective_score, 4),
        'own_yesterday_evidence': own_yesterday_evidence,
        'direct_limitup_reason': direct_limitup_reason,
        'proxy_declared': proxy_declared,
        'proxy_only': proxy_only,
        'sector_proxy_match_count': sector_proxy_match_count,
        'source': 'sector_yesterday_limitup_proxy_only' if proxy_only else (
            'candidate_yesterday_limitup' if own_yesterday_evidence else (
                'direct_limitup_reason' if direct_limitup_reason else 'explicit_candidate_gene'
            )
        ),
    }

def classify_limitup_reason_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify limitup-reason evidence quality for eligibility hard paths.

    DIRECT: non-proxy stock-level limitup_pool reason.
    PROXY: only sector/limitup_pool_sector_proxy (or status PROXY without direct items).
    GENE: no direct/proxy reason text but yesterday/continuation gene present.
    MISSING: none of the above.

    Pure PROXY may remain soft/diagnostic; it must not alone hard-pass
    buy_confirmation or L2 near-limit exemption.
    """
    status = str(row.get('limitup_reason_status') or '').strip().upper()
    evidence = row.get('limitup_reason_evidence') or []
    if not isinstance(evidence, list):
        evidence = []
    has_direct = False
    has_proxy = False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = str(item.get('source') or '').lower()
        is_proxy_flag = bool(item.get('proxy'))
        if is_proxy_flag or 'sector_proxy' in source or source.endswith('_proxy'):
            has_proxy = True
            continue
        reason = str(item.get('reason') or item.get('text') or '').strip()
        if reason or source in ('limitup_pool', 'limitup_reason', 'direct'):
            has_direct = True
    gene_evidence = continuation_gene_evidence(row)
    continuation_gene = gene_evidence['effective_score']
    yesterday_gene = row.get('yesterday_limitup_gene_evidence') if isinstance(row.get('yesterday_limitup_gene_evidence'), dict) else {}
    has_gene = bool(
        continuation_gene > 0.0
        or yesterday_gene.get('candidate_was_yesterday_limitup')
        or yesterday_gene.get('records')
    )
    if has_direct or status in ('PASS', 'OK', 'CONFIRMED', 'DIRECT'):
        # Explicit DIRECT/PASS wins unless evidence is only proxy-marked.
        if has_direct or (status in ('PASS', 'OK', 'CONFIRMED', 'DIRECT') and not has_proxy):
            evidence_class = 'DIRECT'
        elif has_proxy:
            evidence_class = 'PROXY'
        else:
            evidence_class = 'DIRECT'
    elif has_proxy or status == 'PROXY':
        evidence_class = 'PROXY'
    elif has_gene or status == 'GENE':
        evidence_class = 'GENE'
    else:
        evidence_class = 'MISSING'
    return {
        'limitup_reason_evidence_class': evidence_class,
        'limitup_reason_has_direct': has_direct,
        'limitup_reason_has_proxy': has_proxy,
        'limitup_reason_has_gene': has_gene,
        'continuation_gene_evidence': gene_evidence,
        'limitup_reason_status_normalized': status or 'MISSING',
    }

def limitup_reason_supports_hard_confirmation(
    row: Dict[str, Any],
    *,
    limitup_reason_strength: float | None,
    seal_order_strength: float | None = None,
    order_book_pressure: float | None = None,
    buy_confirmation_min: float = 0.60,
    order_book_confirmation_min: float = 0.50,
    news_catalyst_strength: float | None = None,
    announcement_catalyst_score: float | None = None,
) -> Dict[str, Any]:
    """Whether limitup_reason_strength may count as a hard buy/L2 confirmation hit.

    Pure PROXY strength alone is soft-only. PROXY may hard-confirm only when
    paired with seal/order_book/direct news/announcement above threshold.
    """
    classification = classify_limitup_reason_evidence(row)
    evidence_class = classification['limitup_reason_evidence_class']
    strength_ok = limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min
    companion_hits: List[str] = []
    if seal_order_strength is not None and seal_order_strength >= buy_confirmation_min:
        companion_hits.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
    if order_book_pressure is not None and order_book_pressure >= order_book_confirmation_min:
        companion_hits.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
    if (news_catalyst_strength or 0.0) >= 0.75:
        companion_hits.append('news_catalyst_strength>=0.75')
    if (announcement_catalyst_score or 0.0) >= 0.75:
        companion_hits.append('announcement_catalyst_score>=0.75')
    hard_allowed = False
    soft_only = False
    if not strength_ok:
        hard_allowed = False
    elif evidence_class == 'DIRECT':
        hard_allowed = True
    elif evidence_class == 'PROXY':
        if companion_hits:
            hard_allowed = True
        else:
            soft_only = True
    else:
        # GENE / MISSING: strength alone is not stock-level reason hard-pass.
        soft_only = True
    return {
        **classification,
        'limitup_reason_strength_meets_threshold': strength_ok,
        'limitup_reason_hard_confirmation_allowed': hard_allowed,
        'limitup_reason_soft_only': soft_only,
        'limitup_reason_companion_hits': companion_hits,
    }

def strong_sector_theme_partial_aux_exception_allowed(
    row: Dict[str, Any],
    *,
    board: str,
    auxiliary_status_normalized: str,
    research_panel_overall: str,
    sector_gate_pass: bool,
    main_theme_core_score: float,
    main_theme_alignment_score: float,
    sector_catalyst_score: float,
    topic_propagation_score: float,
    near_limit_up_risk: bool,
    regulatory_block: str,
    opportunity_block: str,
    capital_risk_codes: Any,
    price: float | None,
    limitup_quality_block: str,
    limitup_reason_evidence_class: str,
    direct_catalyst_confirmation: bool,
    news_catalyst_strength: float,
    announcement_catalyst_score: float,
) -> bool:
    """Partial aux exception with production guardrails (M3).

    Keeps legitimate strong-theme PARTIAL paths; blocks Haixing-style leaks:
    near price cap, chase/quality block, pure PROXY reason, no stock catalyst.
    """
    if board != 'main':
        return False
    if auxiliary_status_normalized != 'PARTIAL':
        return False
    if research_panel_overall not in ('PARTIAL', 'PASS'):
        return False
    theme_strong = bool(
        sector_gate_pass
        or main_theme_core_score >= 0.70
        or main_theme_alignment_score >= 0.70
        or sector_catalyst_score >= 0.75
        or topic_propagation_score >= 0.75
    )
    if not theme_strong:
        return False
    if near_limit_up_risk or regulatory_block or opportunity_block or capital_risk_codes:
        return False
    quality_block = str(limitup_quality_block or '').strip().upper()
    if quality_block:
        return False
    proxy_only = limitup_reason_evidence_class == 'PROXY'
    if proxy_only and not direct_catalyst_confirmation:
        return False
    no_stock_catalyst = (
        (main_theme_core_score or 0.0) == 0.0
        and (news_catalyst_strength or 0.0) == 0.0
        and (announcement_catalyst_score or 0.0) == 0.0
    )
    if no_stock_catalyst and not direct_catalyst_confirmation:
        return False
    return True

def limitup_probability_proxy_components(
    row: Dict[str, Any],
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Explainable auxiliary proxy; it never independently determines PAPER_PICK."""
    profile = profile if isinstance(profile, dict) else structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    sector_proxy = row.get('sector_yesterday_limitup_gene_proxy') or auxiliary.get('sector_yesterday_limitup_gene_proxy') or {}
    gene_evidence = continuation_gene_evidence(row)
    sector_gene = safe_float(sector_proxy.get('continuation_gene_score')) if isinstance(sector_proxy, dict) else None
    sector_gene = 0.0 if (
        gene_evidence['proxy_only']
        and gene_evidence['sector_proxy_match_count'] < 3
    ) else (
        sector_gene if sector_gene is not None else gene_evidence['effective_score']
    )
    positive = {
        'sector_yesterday_limitup_gene_proxy': min(1.0, sector_gene) * 0.13,
        'limitup_reason_strength': min(1.0, profile.get('limitup_reason_strength') or 0.0) * 0.10,
        'seal_order_strength': min(1.0, profile.get('seal_order_strength') or 0.0) * 0.10,
        'close_position_score': min(1.0, profile.get('close_position_score') or 0.0) * 0.08,
        'volume_ratio': min(1.0, (profile.get('volume_ratio') or 0.0) / 3.0) * 0.07,
        'fund_flow_momentum': min(1.0, max(0.0, profile.get('fund_flow_momentum') or 0.0)) * 0.05,
        'time_series_momentum': min(1.0, max(0.0, profile.get('time_series_momentum') or 0.0)) * 0.06,
        'confirmed_news_catalyst': min(1.0, profile.get('news_catalyst_strength') or 0.0) * 0.11,
        'announcement_catalyst': min(1.0, profile.get('announcement_catalyst_score') or 0.0) * 0.10,
        'sector_news_catalyst': min(1.0, profile.get('sector_news_catalyst_score') or 0.0) * 0.08,
        'low_position_catalyst_score': min(1.0, safe_float(row.get('low_position_catalyst_score')) or 0.0) * 0.09,
        'main_theme_alignment_score': min(1.0, profile.get('main_theme_alignment_score') or 0.0) * 0.12,
        'continuation_gene_score': gene_evidence['effective_score'] * 0.14,
    }
    negative = {
        'failed_limitup_risk': min(1.0, capital.get('failed_limitup_risk') or 0.0) * 0.25,
        'weak_limitup_confirmation': 0.12 if capital.get('weak_limitup_confirmation') else 0.0,
        'open_board_risk': min(1.0, capital.get('open_board_risk') or 0.0) * 0.15,
        'main_buy_outflow_pressure': min(1.0, capital.get('main_buy_outflow_pressure') or 0.0) * 0.20,
        'popularity_crowding_risk': min(1.0, capital.get('popularity_crowding_risk') or 0.0) * 0.10,
        'high_popularity_trap_risk': min(1.0, capital.get('high_popularity_trap_risk') or 0.0) * 0.15,
        'risk_notice_evidence': min(1.0, profile.get('risk_notice_penalty') or 0.0) * 0.10,
        'capital_risk_penalty': min(1.0, capital.get('risk_penalty_score') or 0.0) * 0.10,
    }
    score = max(0.0, min(1.0, sum(positive.values()) - sum(negative.values())))
    blocked = negative['failed_limitup_risk'] >= 0.20 and negative['main_buy_outflow_pressure'] >= 0.15
    status = 'BLOCKED' if blocked else ('STRONG' if score >= 0.55 else ('MEDIUM' if score >= 0.28 else 'WEAK'))
    return {
        'limitup_probability_proxy': round(score, 4),
        'limitup_proxy_positive_components': [key for key, value in positive.items() if value > 0],
        'limitup_proxy_negative_components': [key for key, value in negative.items() if value > 0],
        'limitup_proxy_status': status,
    }

def social_confirmation_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    """Describe social evidence without promoting a candidate to PAPER_PICK."""
    catalyst = safe_float(row.get('social_catalyst_score'))
    theme = safe_float(row.get('theme_strength_last30d'))
    sentiment = safe_float(row.get('social_sentiment_score'))
    noise = safe_float(row.get('social_noise_risk'))
    quality = str(row.get('social_signal_quality') or 'MISSING').upper()
    source_layers = list(row.get('social_source_layers') or [])
    collection_status = str(row.get('social_signal_collection_status') or '').upper()
    collection_errors = list(row.get('social_signal_error') or [])
    reasons: List[str] = []
    has_layers = bool(source_layers) or quality in ('MEDIUM', 'HIGH', 'LOW') or collection_status == 'PASS'
    if not has_layers and quality == 'MISSING':
        status = 'MISSING'
        reasons.append('social_signal_missing')
    elif (noise or 0.0) >= 0.70:
        status = 'NOISY'
        reasons.append('social_noise_risk_high')
    elif (catalyst or 0.0) >= 0.60 and quality in ('MEDIUM', 'HIGH'):
        # theme_strength_last30d is intentionally unused on eastmoney-only path;
        # catalyst + quality is enough for soft confirmation.
        status = 'PASS'
        reasons.append('social_catalyst_confirmation')
    elif (catalyst or 0.0) >= 0.60 and (theme or 0.0) >= 0.50:
        status = 'PASS'
        reasons.append('social_theme_confirmation')
    else:
        status = 'WEAK'
        reasons.append('social_confirmation_below_shadow_threshold')
    if collection_status == 'WARN':
        reasons.append('social_collection_warn')
    if collection_errors:
        reasons.append('social_collection_error_recorded')
    return {
        'status': status,
        'social_catalyst_score': catalyst,
        'theme_strength_last30d': theme,
        'social_sentiment_score': sentiment,
        'social_noise_risk': noise,
        'social_signal_quality': quality,
        'source_count': len(source_layers),
        'source_layers': source_layers,
        'collection_status': collection_status or ('PASS' if source_layers else 'MISSING'),
        'collection_errors': collection_errors,
        'reason': reasons,
        'used_for_official_ranking': False,
    }

def shadow_risk_profile(
    row: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Produce explainable weak-market and chase diagnostics for shadow replay."""
    bundle = bundle if isinstance(bundle, dict) else {}
    profile = profile if isinstance(profile, dict) else structured_signal_profile(row, bundle)
    market = market_adaptive_context(row, bundle)
    breadth = safe_float(market.get('market_breadth_up_pct')) or 50.0
    broken_ratio = safe_float(market.get('limitup_broken_ratio')) or 0.0
    broken_count = safe_float(market.get('broken_limitups')) or 0.0
    index_returns = (bundle.get('market_snapshot') or {}).get('index_returns') or row.get('index_returns') or {}
    if not isinstance(index_returns, dict):
        index_returns = {}
    negative_indexes = [
        name for name, value in index_returns.items()
        if (safe_float(value) or 0.0) <= (-2.5 if '500' in str(name) else -2.0)
    ]
    risk_reasons = []
    risk_points = 0
    if negative_indexes:
        risk_points += 2
        risk_reasons.append('index_drawdown:' + ','.join(sorted(map(str, negative_indexes))))
    if breadth < 40:
        risk_points += 2
        risk_reasons.append('market_breadth_below_40pct')
    elif breadth < 48:
        risk_points += 1
        risk_reasons.append('market_breadth_below_48pct')
    if market.get('weak_acceptance_market'):
        risk_points += 2
        risk_reasons.append('weak_acceptance_market')
    if market.get('broken_limit_pressure') or broken_ratio < 1.0 or broken_count >= 30:
        risk_points += 1
        risk_reasons.append('broken_limit_pressure')
    risk_level = 'EXTREME' if risk_points >= 5 else ('HIGH' if risk_points >= 3 else ('MEDIUM' if risk_points >= 1 else 'LOW'))
    weak_market = risk_level in ('HIGH', 'EXTREME')

    proxy = limitup_probability_proxy_components(row, profile)
    social = social_confirmation_profile(row)
    signal_pct = profile.get('signal_pct') or 0.0
    close_position = profile.get('close_position_score') or 0.0
    fund_flow = profile.get('fund_flow_momentum') or 0.0
    high_stage = signal_stage_bucket(signal_pct) in ('mid_5_to_7', 'high_7_to_9')
    chase_reasons = []
    penalty = 0.0
    if signal_pct >= 5.0 and proxy['limitup_proxy_status'] in ('WEAK', 'BLOCKED'):
        penalty += 7.0
        chase_reasons.append('high_pct_with_weak_limitup_proxy')
    if close_position < 0.70:
        penalty += 3.0
        chase_reasons.append('close_position_not_strong')
    if fund_flow <= 0:
        penalty += 3.0
        chase_reasons.append('fund_flow_not_confirmed')
    if social['status'] in ('MISSING', 'WEAK', 'NOISY'):
        penalty += 2.0
        chase_reasons.append('no_clean_social_confirmation')
    if weak_market and high_stage:
        penalty += 5.0
        chase_reasons.append('weak_market_high_chase_stage')
    penalty = min(20.0, penalty)
    chase_level = 'HIGH' if penalty >= 12 else ('MEDIUM' if penalty >= 6 else 'LOW')

    defensive = 0.0
    defensive_reasons = []
    if signal_pct <= 3.0:
        defensive += 0.25
        defensive_reasons.append('low_pct_start')
    if 0.45 <= close_position <= 0.85:
        defensive += 0.15
        defensive_reasons.append('non_climax_close_position')
    if fund_flow > 0:
        defensive += 0.25
        defensive_reasons.append('fund_flow_stable')
    if str(row.get('sector_name') or row.get('industry') or '') in ('电力', '银行', '运营商', '公用事业'):
        defensive += 0.20
        defensive_reasons.append('defensive_industry')
    if (safe_float(row.get('turnover_rate')) or 0.0) <= 5.0:
        defensive += 0.15
        defensive_reasons.append('low_turnover')
    defensive = round(min(1.0, defensive), 4)

    gene_strength = 'STRONG' if proxy['limitup_proxy_status'] == 'STRONG' else (
        'MEDIUM' if proxy['limitup_proxy_status'] == 'MEDIUM' else (
            'WEAK' if proxy['limitup_proxy_status'] == 'WEAK' else 'NONE'
        )
    )
    gene_reasons = []
    gene_gate = 'PASS'
    if weak_market and gene_strength == 'WEAK' and signal_pct >= 5.0:
        confluence = (
            (profile.get('sector_catalyst_score') or 0.0) >= 0.60
            and fund_flow > 0
            and social['status'] == 'PASS'
        )
        if not confluence:
            gene_gate = 'BLOCK_SHADOW'
            gene_reasons.append('weak_market_weak_gene_without_sector_fund_social_confluence')
        else:
            gene_gate = 'WARN'
            gene_reasons.append('weak_market_weak_gene_confluence_only')
    elif gene_strength == 'WEAK':
        gene_gate = 'WARN'
        gene_reasons.append('weak_gene_risk_notice')

    return {
        'market_regime_risk': risk_level,
        'weak_market': weak_market,
        'market_regime_risk_reason': risk_reasons,
        'chase_high_risk': chase_level,
        'chase_high_shadow_penalty': round(penalty, 4),
        'chase_high_reason': chase_reasons,
        'defensive_carry_score': defensive,
        'defensive_reason': defensive_reasons,
        'limitup_gene_strength': gene_strength,
        'limitup_gene_shadow_gate': gene_gate,
        'limitup_gene_block_reason': gene_reasons,
        'social_confirmation': social,
        'used_for_official_ranking': False,
    }

def paper_pick_risk_explanation_gate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Reject unexplained broken-board/outflow/popularity-trap PAPER_PICK paths."""
    profile = structured_signal_profile(row)
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(row)
    proxy = limitup_probability_proxy_components(row)
    failed_limitup = (capital.get('failed_limitup_risk') or 0.0) > 0
    outflow = (capital.get('main_buy_outflow_pressure') or 0.0) > 0
    high_popularity = (
        (capital.get('high_popularity_trap_risk') or 0.0) > 0
        or (capital.get('popularity_crowding_risk') or 0.0) >= 0.8
    )
    triple_risk = bool(failed_limitup and outflow and high_popularity)
    # 07-13 style: broken board + main outflow without strong catalyst/gene rebuttal.
    dual_broken_outflow = bool(failed_limitup and outflow)
    strong_rebuttals = []
    if (profile.get('news_catalyst_strength') or 0.0) >= 0.75:
        strong_rebuttals.append('confirmed_news_catalyst_strong')
    if (profile.get('announcement_catalyst_score') or 0.0) >= 0.75:
        strong_rebuttals.append('announcement_catalyst_strong')
    if continuation_gene_evidence(row)['effective_score'] >= 0.70:
        strong_rebuttals.append('sector_yesterday_limitup_gene_proxy_strong')
    if proxy['limitup_probability_proxy'] >= 0.65 and proxy['limitup_proxy_status'] != 'BLOCKED':
        strong_rebuttals.append('limitup_probability_proxy_strong')
    continuation_exception = broken_limitup_continuation_exception(row)
    if continuation_exception.get('eligible'):
        strong_rebuttals.append('controlled_limitup_continuation_exception')
    blocked = (triple_risk and not strong_rebuttals) or (dual_broken_outflow and not strong_rebuttals)
    return {
        'status': 'FAIL' if blocked else 'PASS',
        'triple_risk': triple_risk,
        'dual_broken_outflow_risk': dual_broken_outflow,
        'strong_rebuttals': strong_rebuttals,
        'rule': (
            'failed_limitup + outflow (+ high_popularity) requires explicit catalyst/gene rebuttal; '
            'dual broken-board+outflow also blocked without rebuttal'
        ),
    }

def replay_only_sector_opportunity(profile: Dict[str, Any], row: Dict[str, Any] | None = None) -> bool:
    details = profile.get('structured_component_details') if isinstance(profile.get('structured_component_details'), dict) else {}
    tags = normalize_tag_list(profile.get('sector_opportunity_tags')) + normalize_tag_list(details.get('sector_opportunity_tags'))
    orig_tags = normalize_tag_list(row.get('sector_opportunity_tags')) if isinstance(row, dict) else []
    all_tags = tags + orig_tags
    if not all_tags:
        return False
    non_replay_tags = [tag for tag in all_tags if not str(tag).startswith('REPLAY_')]
    has_replay = any(str(tag).startswith('REPLAY_') for tag in tags)
    return has_replay and not non_replay_tags

def parse_capital_flow_from_content_records(content_records):
    fund_by_code = {}
    for rec in content_records:
        item_key = str(rec.get('item_key') or '')
        if item_key != 'stock_capital_flow':
            continue
        code = str(rec.get('code') or rec.get('SECURITY_CODE') or '').strip()
        if not code:
            continue
        cells = rec.get('cells') or []
        if len(cells) < 7:
            continue
        def parse_money(val):
            s = str(val).replace('\xa0', ' ').strip()
            if not s or s in ('-', '--', ''):
                return 0.0
            m = re.search(r'(-?[\d.]+)\s*(亿|万)?', s)
            if not m:
                return 0.0
            v = float(m.group(1))
            if m.group(2) == '亿':
                v *= 100000000
            elif m.group(2) == '万':
                v *= 10000
            return v
        def parse_pct(val):
            s = str(val).replace('\xa0', ' ').strip()
            m = re.search(r'(-?[\d.]+)%', s)
            return float(m.group(1)) / 100.0 if m else 0.0
        main_net = parse_money(cells[6]) if len(cells) > 6 else 0.0
        super_large_net = parse_money(cells[7]) if len(cells) > 7 else 0.0
        large_net = parse_money(cells[8]) if len(cells) > 8 else 0.0
        medium_net = parse_money(cells[9]) if len(cells) > 9 else 0.0
        small_net = parse_money(cells[10]) if len(cells) > 10 else 0.0
        price = float(cells[4]) if len(cells) > 4 and cells[4] not in ('-', '') else 0.0
        pct_chg = parse_pct(cells[5]) if len(cells) > 5 else 0.0
        if code not in fund_by_code or abs(main_net) > abs(fund_by_code[code].get('main_force_net_inflow', 0)):
            fund_by_code[code] = {
                'main_force_net_inflow': main_net,
                'main_force_net_inflow_pct': parse_money(cells[7]) / 100000000 if len(cells) > 7 else 0.0,
                'super_large_net_inflow': super_large_net,
                'large_net_inflow': large_net,
                'medium_net_inflow': medium_net,
                'small_net_inflow': small_net,
                'price': price,
                'pct_chg': pct_chg,
                'source': 'data_directory_content_stock_capital_flow',
            }
    return fund_by_code

def stock_capital_flow_by_code_from_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize scanner raw-domain stock_capital_flow payload to runner flow map."""
    rows = payload if isinstance(payload, list) else ((payload.get('rows') or payload.get('data') or []) if isinstance(payload, dict) else [])
    fund_by_code: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_code = row.get('code') or row.get('symbol') or row.get('f12') or ''
        code = str(raw_code).strip().zfill(6)
        if len(code) != 6:
            continue
        main_net = safe_float(row.get('main_force_net_inflow'))
        if main_net is None:
            main_net = safe_float(row.get('net_inflow_main'))
        if main_net is None:
            main_net = safe_float(row.get('f62')) or 0.0
        fund_by_code[code] = {
            'main_force_net_inflow': main_net,
            'main_force_net_inflow_pct': safe_float(row.get('main_force_net_inflow_pct')) or safe_float(row.get('f184')) or 0.0,
            'super_large_net_inflow': safe_float(row.get('super_large_net_inflow')) or safe_float(row.get('f66')) or 0.0,
            'large_net_inflow': safe_float(row.get('large_net_inflow')) or safe_float(row.get('f72')) or 0.0,
            'medium_net_inflow': safe_float(row.get('medium_net_inflow')) or safe_float(row.get('f78')) or 0.0,
            'small_net_inflow': safe_float(row.get('small_net_inflow')) or safe_float(row.get('f84')) or 0.0,
            'source': 'scan_market_data_stock_capital_flow',
        }
    return fund_by_code

def inject_capital_flow_boost(bundle, fund_by_code):
    for key in ('candidate', 'candidate_features'):
        cand = bundle.get(key)
        if isinstance(cand, dict):
            code = str(cand.get('code') or cand.get('symbol') or '').strip()
            if code and code in fund_by_code:
                flow = fund_by_code[code]
                cand['data_directory_capital_flow'] = flow
                if cand.get('price') is None and flow.get('price') is not None:
                    cand['price'] = flow.get('price')
    for key in ('paper_scoring_candidates', 'structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    code = str(item.get('code') or item.get('symbol') or '').strip()
                    if code and code in fund_by_code:
                        flow = fund_by_code[code]
                        item['data_directory_capital_flow'] = flow
                        if item.get('price') is None and flow.get('price') is not None:
                            item['price'] = flow.get('price')
                        if item.get('score') is None:
                            net = fund_by_code[code].get('main_force_net_inflow', 0)
                            if net > 0:
                                item['score'] = round(min(100, 50 + net / 100000000 * 2), 4)
                                item['_score_from_data_directory_capital_flow'] = True

def fetch_candidate_fund_flow_live(codes, timeout=5):
    """Official runner no longer performs live direct Eastmoney fund-flow fetches."""
    return {}

def inject_live_fund_flow_into_candidates(bundle):
    candidates = bundle.get('full_candidate_pool') or bundle.get('paper_scoring_candidates') or []
    if not candidates:
        return
    ranked_candidates = sorted(
        [candidate for candidate in candidates if isinstance(candidate, dict)],
        key=lambda candidate: (safe_float(candidate.get('rank')) or 999999.0, -(safe_float(candidate.get('final_score')) or safe_float(candidate.get('score')) or 0.0)),
    )[:10]
    codes = []
    for candidate in ranked_candidates:
        existing_flow = candidate.get('data_directory_capital_flow') if isinstance(candidate.get('data_directory_capital_flow'), dict) else {}
        existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
        scanner_net = safe_float(candidate.get('net_inflow_main'))
        if existing_net is None and scanner_net is not None:
            continue
        code = str(candidate.get('code') or candidate.get('symbol') or '').strip()
        if code and len(code) == 6:
            codes.append(code)
    if not codes:
        return
    live_fund = fetch_candidate_fund_flow_live(codes)
    if codes and not live_fund:
        bundle['candidate_fund_recheck_missing'] = True
        for candidate in ranked_candidates:
            if isinstance(candidate, dict):
                candidate['candidate_fund_recheck_missing'] = True
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        code = str(cand.get('code') or cand.get('symbol') or '').strip()
        if code in live_fund:
            existing_flow = cand.get('data_directory_capital_flow') if isinstance(cand.get('data_directory_capital_flow'), dict) else {}
            existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
            live_net = safe_float(live_fund[code].get('main_force_net_inflow')) or 0.0
            if existing_net is None or existing_net == 0.0:
                cand['data_directory_capital_flow'] = live_fund[code]
                if live_net > 0:
                    existing = safe_float(cand.get('net_inflow_main')) or 0
                    if existing <= 0:
                        cand['net_inflow_main'] = live_net
                        cand['_net_inflow_main_from_live_fund_flow'] = True
            else:
                cand['data_directory_capital_flow_live_supplement'] = live_fund[code]
    for key in ('full_candidate_pool', 'structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    code = str(item.get('code') or item.get('symbol') or '').strip()
                    if code in live_fund:
                        existing_flow = item.get('data_directory_capital_flow') if isinstance(item.get('data_directory_capital_flow'), dict) else {}
                        existing_net = safe_float(existing_flow.get('main_force_net_inflow'))
                        if existing_net is None or existing_net == 0.0:
                            item['data_directory_capital_flow'] = live_fund[code]
                        else:
                            item['data_directory_capital_flow_live_supplement'] = live_fund[code]
    content_by_code = bundle.get('data_directory_content_by_code') or {}
    fund_content = {}
    for code, recs in content_by_code.items():
        for rec in recs:
            if rec.get('item_key') == 'stock_capital_flow':
                fund_content[code] = rec
                break
    top_fund_codes = sorted(fund_content.keys(), key=lambda c: len(fund_content.get(c, {}).get('raw_text', '')), reverse=True)[:10]
    existing_codes = {str(c.get('code') or '').strip() for c in candidates if isinstance(c, dict)}
    for code in top_fund_codes:
        if code in existing_codes or len(code) != 6:
            continue
        rec = fund_content.get(code, {})
        raw = rec.get('raw_text', '')
        nums = re.findall(r'-?[\d]+(?:\.[\d]+)?(?:亿|万)?', raw)
        cleaned = []
        for n in nums:
            val = float(n.replace('亿', '').replace('万', ''))
            if '亿' in n:
                val *= 100000000
            elif '万' in n:
                val *= 10000
            cleaned.append(val)
        main_net = cleaned[5] if len(cleaned) > 5 else 0.0
        if main_net <= 0:
            continue
        name_match = re.search(r'\d{6}\s+([一-龥A-Za-z0-9*]+)', raw)
        name = name_match.group(1) if name_match else ''
        fund_cand = {
            'code': code,
            'symbol': code,
            'name': name,
            'price': float(cleaned[0]) if cleaned else 0,
            'signal_pct': float(nums[2].replace('%', '')) if len(nums) > 2 and '%' in str(nums[2]) else 0,
            'signal_amount': cleaned[3] if len(cleaned) > 3 else 0,
            'rank': int(nums[0]) if nums else 999,
            'net_inflow_main': main_net,
            'data_directory_capital_flow': {
                'main_force_net_inflow': main_net,
                'source': 'data_directory_content_stock_capital_flow',
            },
            '_from_data_directory_capital_flow': True,
            'paper_only': True,
            'no_trade': True,
        }
        candidates.append(fund_cand)
    bundle['paper_scoring_candidates'] = candidates


for _name, _value in tuple(globals().items()):
    if (
        callable(_value)
        and getattr(_value, '__module__', None) == __name__
        and _name not in {'bind_host', '_inject_host', '_with_host'}
    ):
        globals()[_name] = _with_host(_value)
