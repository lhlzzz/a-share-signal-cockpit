#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compact one-page evidence card for official picks (digest, not full LLM dump)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip_text(value: Any, limit: int = 120) -> str:
    text = str(value or '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def _decision_reason_zh(value: Any) -> str:
    text = str(value or '').strip()
    mapping = {
        'ALL_FORWARD_PAPER_HARD_GATES_PASS': '全部正式出票门禁通过',
        'NO_PICK_PROMOTED_TO_HIGHEST_SCORE_CANDIDATE': '无其他正式票时，提升为当前最高获利证据候选',
        'NO_PICK': '未形成正式出票',
    }
    return mapping.get(text, text)


def _market_stance_zh(value: Any) -> str:
    mapping = {
        'DEFENSIVE_ROTATION': '防御轮动',
        'RISK_OFF_TECH_DEFENSIVE': '风险厌恶，偏防御',
        'AVOID_CLIMAX_TECH': '回避高位加速',
        'RISK_ON': '风险偏好回升',
        'WATCH': '观察',
        'NO_MAIN': '暂无明确主线',
    }
    text = str(value or '').strip()
    return mapping.get(text, text.replace('_', ' ') if text else '')


def _list_head(items: Any, n: int = 3) -> List[Any]:
    if not isinstance(items, list):
        return []
    return items[:n]


def _announcement_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for item in _list_head(row.get('announcement_evidence') or [], 3):
        if isinstance(item, dict):
            title = item.get('title') or item.get('reason') or item.get('text') or item.get('summary')
            if title:
                bullets.append(_clip_text(title, 100))
        elif item:
            bullets.append(_clip_text(item, 100))
    score = _f(row.get('announcement_catalyst_score'))
    if score is not None and not bullets:
        bullets.append(f'公告催化强度：{score:.2f}')
    return bullets


def _news_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    news = row.get('news_evidence')
    if isinstance(news, dict):
        for item in _list_head(news.get('direct_symbol_news') or news.get('items') or [], 3):
            if isinstance(item, dict):
                title = item.get('title') or item.get('text') or item.get('summary')
                if title:
                    bullets.append(_clip_text(title, 100))
            elif item:
                bullets.append(_clip_text(item, 100))
        if news.get('status') and not bullets:
            bullets.append(f"新闻证据状态：{news.get('status')}")
    elif isinstance(news, list):
        for item in _list_head(news, 3):
            if isinstance(item, dict):
                title = item.get('title') or item.get('text')
                if title:
                    bullets.append(_clip_text(title, 100))
    strength = _f(row.get('news_catalyst_strength'))
    if strength is not None:
        bullets.append(f'新闻催化强度：{strength:.2f}')
    return bullets[:4]


def _fund_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for key, label in (
        ('fund_flow_momentum', '主力资金动量'),
        ('net_inflow_main', '主力净流入'),
        ('full_universe_fund_pctile', '资金分位'),
        ('volume_ratio', '成交量放大'),
        ('close_position_score', '收盘位置'),
        ('hsgt_institutional_flow', '沪深港通资金'),
    ):
        val = _f(row.get(key))
        if val is not None:
            bullets.append(f'{label}：{val:.4g}')
    capital = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
    if capital:
        for key in ('main_net_inflow', 'main_force_net_inflow', 'super_net_inflow'):
            val = _f(capital.get(key))
            if val is not None:
                bullets.append(f'资金明细：{val:.4g}')
                break
    return bullets[:6]


def _theme_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for key in (
        'main_theme_core_score', 'main_theme_alignment_score', 'leader_chain_score',
        'sector_opportunity_score', 'continuation_gene_score',
    ):
        val = _f(row.get(key))
        if val is not None:
            labels = {
                'main_theme_core_score': '主线核心强度',
                'main_theme_alignment_score': '主线匹配度',
                'leader_chain_score': '龙头链强度',
                'sector_opportunity_score': '板块机会强度',
                'continuation_gene_score': '涨停基因强度',
            }
            bullets.append(f"{labels[key]}：{val:.3f}")
    if row.get('main_theme_source'):
        bullets.append(f"主线来源：{row.get('main_theme_source')}")
    tags = row.get('sector_opportunity_tags') or row.get('theme_tags') or []
    if isinstance(tags, list) and tags:
        bullets.append('主线标签：' + '、'.join(str(t) for t in tags[:5]))
    for key in ('industry', 'sector', 'predicted_sector', 'main_theme'):
        if row.get(key):
            labels = {
                'industry': '行业',
                'sector': '板块',
                'predicted_sector': '预测板块',
                'main_theme': '主线',
            }
            bullets.append(f"{labels[key]}：{_clip_text(row.get(key), 40)}")
    return bullets[:10]


def _profit_evidence_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for key, label in (
        ('continuation_gene_score', '涨停基因强度'),
        ('close_position_score', '盘中承接/收盘位置'),
        ('volume_ratio', '成交量放大'),
        ('expected_t1_profit_score', 'T+1获利证据分'),
    ):
        val = _f(row.get(key))
        if val is not None:
            bullets.append(f'{label}：{val:.3f}')
    if row.get('previous_limitup') or row.get('was_yesterday_limitup'):
        bullets.append('昨日涨停延续：是')
    return bullets[:6]


def _risk_bullets(row: Dict[str, Any], eligibility: Optional[Dict[str, Any]] = None) -> List[str]:
    bullets: List[str] = []
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else {}
    if capital.get('risk_codes'):
        bullets.extend(str(c) for c in list(capital.get('risk_codes') or [])[:4])
    if capital.get('risk_penalty_score') is not None:
        bullets.append(f"资金风险惩罚：{_f(capital.get('risk_penalty_score')):.3f}")
    if row.get('risk_notice_penalty') is not None:
        bullets.append(f"风险公告惩罚：{_f(row.get('risk_notice_penalty')):.3f}")
    for item in _list_head(row.get('risk_notice_evidence') or [], 2):
        if isinstance(item, dict):
            bullets.append(_clip_text(item.get('title') or item.get('text') or item, 80))
        elif item:
            bullets.append(_clip_text(item, 80))
    elig = eligibility if isinstance(eligibility, dict) else (
        row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    )
    for b in list(elig.get('blockers') or [])[:3]:
        bullets.append(f'门禁提示：{b}')
    for flag in list(row.get('risk_flags') or [])[:3]:
        bullets.append(str(flag))
    return bullets[:8]


def build_compact_evidence_card(
    candidate: Dict[str, Any] | None,
    *,
    features: Dict[str, Any] | None = None,
    similar_cases: List[Dict[str, Any]] | None = None,
    decision: str = '',
    reason: str = '',
) -> Dict[str, Any]:
    """One-page compact card: announcements / news / fund / theme / risk / social / similar."""
    row = dict(candidate or {})
    if isinstance(features, dict):
        # features may be outer bag or candidate-level
        nested = features.get('candidate_features') if isinstance(features.get('candidate_features'), dict) else None
        if nested:
            row = {**row, **nested}
        else:
            row = {**row, **features}
    symbol = str(row.get('symbol') or row.get('code') or '').zfill(6) if (row.get('symbol') or row.get('code')) else ''
    name = str(row.get('name') or row.get('stock_name') or '')
    score = _f(row.get('final_score') if row.get('final_score') is not None else row.get('score'))
    social = _f(row.get('social_sentiment_score'))
    social_quality = str(row.get('social_signal_quality') or '').upper()
    social_collection = str(row.get('social_signal_collection_status') or '').upper()
    social_layers = list(row.get('social_source_layers') or [])
    social_present = bool(
        social_quality in ('MEDIUM', 'HIGH', 'LOW')
        or social_collection == 'PASS'
        or social_layers
        or social is not None
    )

    card = {
        'version': 'compact_evidence_card_v1',
        'symbol': symbol,
        'name': name,
        'decision': decision or str(row.get('decision') or ''),
        'score': score,
        'signal_pct': _f(row.get('signal_pct')),
        'price': _f(row.get('price')),
        'structured_score': _f(row.get('structured_score')),
        'announcements': _announcement_bullets(row),
        'news': _news_bullets(row),
        'fund_flow': _fund_bullets(row),
        'main_theme': _theme_bullets(row),
        'profit_evidence': _profit_evidence_bullets(row),
        'risks': _risk_bullets(row),
        'social': {
            'sentiment_score': social,
            'status': 'present' if social_present else 'missing',
            'quality': social_quality or None,
            'collection_status': social_collection or None,
            'source_layers': social_layers[:4],
        },
        # 仓库贡献只用于机器诊断，不进入正式出票依据，避免多套模型噪音污染。
        'repo_summary': '',
        'decision_reason': _clip_text(
            _decision_reason_zh(reason or row.get('decision_reason') or ''),
            200,
        ),
        'similar_cases': list(similar_cases or [])[:5],
        'one_liner': '',
    }
    theme_bits = []
    if card['main_theme']:
        theme_bits.append(card['main_theme'][0])
    fund_bits = card['fund_flow'][:1]
    profit_bits = card['profit_evidence'][:2]
    risk_bits = card['risks'][:1]
    parts = [f"{symbol} {name}".strip()]
    if score is not None:
        parts.append(f'综合分数：{score:.2f}')
    parts.extend(theme_bits)
    parts.extend(fund_bits)
    parts.extend(profit_bits)
    parts.extend(risk_bits)
    card['one_liner'] = _clip_text(' | '.join(str(p) for p in parts if p), 220)
    return card


def evidence_card_to_selection_reason(card: Dict[str, Any], legacy_reason: str = '') -> Dict[str, Any]:
    """Stable selection_reason shape: card + short bullets + optional legacy string."""
    return {
        'format': 'compact_evidence_card_v1',
        'one_liner': card.get('one_liner') or '',
        'evidence_card': card,
        'why_selected': [
            card.get('one_liner') or '',
            *list(card.get('main_theme') or [])[:2],
            *list(card.get('fund_flow') or [])[:2],
            *list(card.get('announcements') or [])[:1],
        ],
        'risks': list(card.get('risks') or [])[:4],
        'similar_cases': list(card.get('similar_cases') or [])[:3],
        # Compatibility field for persisted payload readers. It is deliberately
        # kept out of the evidence card and dashboard display.
        'legacy_repo_summary': _clip_text(legacy_reason or '', 200),
        'decision_reason': _clip_text(card.get('decision_reason') or '', 200),
    }
