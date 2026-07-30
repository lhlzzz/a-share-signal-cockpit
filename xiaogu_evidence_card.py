#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compact one-page evidence card for official picks (digest, not full LLM dump).

Rules + multi-factor only. Bounded soft context (sszcw). Never end-to-end LLM over 400 names.
"""
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
        bullets.append(f'announcement_catalyst_score={score:.2f}')
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
            bullets.append(f"news_status={news.get('status')}")
    elif isinstance(news, list):
        for item in _list_head(news, 3):
            if isinstance(item, dict):
                title = item.get('title') or item.get('text')
                if title:
                    bullets.append(_clip_text(title, 100))
    strength = _f(row.get('news_catalyst_strength'))
    if strength is not None:
        bullets.append(f'news_catalyst_strength={strength:.2f}')
    return bullets[:4]


def _fund_bullets(row: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for key, label in (
        ('fund_flow_momentum', 'fund_flow_momentum'),
        ('net_inflow_main', 'net_inflow_main'),
        ('full_universe_fund_pctile', 'fund_pctile'),
        ('volume_ratio', 'volume_ratio'),
        ('close_position_score', 'close_position'),
        ('hsgt_institutional_flow', 'hsgt'),
    ):
        val = _f(row.get(key))
        if val is not None:
            bullets.append(f'{label}={val:.4g}')
    capital = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
    if capital:
        for key in ('main_net_inflow', 'main_force_net_inflow', 'super_net_inflow'):
            val = _f(capital.get(key))
            if val is not None:
                bullets.append(f'{key}={val:.4g}')
                break
    return bullets[:6]


def _theme_bullets(row: Dict[str, Any], soft: Optional[Dict[str, Any]] = None) -> List[str]:
    bullets: List[str] = []
    for key in (
        'main_theme_core_score', 'main_theme_alignment_score', 'leader_chain_score',
        'sector_opportunity_score', 'continuation_gene_score',
    ):
        val = _f(row.get(key))
        if val is not None:
            bullets.append(f'{key}={val:.3f}')
    if row.get('main_theme_source'):
        bullets.append(f"main_theme_source={row.get('main_theme_source')}")
    tags = row.get('sector_opportunity_tags') or row.get('theme_tags') or []
    if isinstance(tags, list) and tags:
        bullets.append('tags=' + ','.join(str(t) for t in tags[:5]))
    for key in ('industry', 'sector', 'predicted_sector', 'main_theme'):
        if row.get(key):
            bullets.append(f'{key}={_clip_text(row.get(key), 40)}')
    if isinstance(soft, dict):
        if soft.get('favored_hits'):
            bullets.append('sszcw_favored=' + ','.join(str(x) for x in soft.get('favored_hits')[:4]))
        if soft.get('risk_hits'):
            bullets.append('sszcw_risk=' + ','.join(str(x) for x in soft.get('risk_hits')[:4]))
        if soft.get('market_stance'):
            bullets.append(f"sszcw_stance={soft.get('market_stance')}")
        if soft.get('soft_context_valid') is not None:
            bullets.append(f"soft_context_valid={soft.get('soft_context_valid')}")
    return bullets[:10]


def _risk_bullets(row: Dict[str, Any], eligibility: Optional[Dict[str, Any]] = None) -> List[str]:
    bullets: List[str] = []
    capital = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else {}
    if capital.get('risk_codes'):
        bullets.extend(str(c) for c in list(capital.get('risk_codes') or [])[:4])
    if capital.get('risk_penalty_score') is not None:
        bullets.append(f"capital_risk_penalty={_f(capital.get('risk_penalty_score')):.3f}")
    if row.get('risk_notice_penalty') is not None:
        bullets.append(f"risk_notice_penalty={_f(row.get('risk_notice_penalty')):.3f}")
    for item in _list_head(row.get('risk_notice_evidence') or [], 2):
        if isinstance(item, dict):
            bullets.append(_clip_text(item.get('title') or item.get('text') or item, 80))
        elif item:
            bullets.append(_clip_text(item, 80))
    elig = eligibility if isinstance(eligibility, dict) else (
        row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    )
    for b in list(elig.get('blockers') or [])[:3]:
        bullets.append(f'blocker:{b}')
    for flag in list(row.get('risk_flags') or [])[:3]:
        bullets.append(str(flag))
    return bullets[:8]


def build_compact_evidence_card(
    candidate: Dict[str, Any] | None,
    *,
    features: Dict[str, Any] | None = None,
    soft_context: Dict[str, Any] | None = None,
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
    soft = soft_context
    if soft is None:
        elig = row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
        signals = elig.get('signals') if isinstance(elig.get('signals'), dict) else {}
        soft = signals.get('pre_pick_market_context_soft') if isinstance(signals.get('pre_pick_market_context_soft'), dict) else {}
        if not soft and isinstance(features, dict):
            soft = features.get('pre_pick_market_context_soft') if isinstance(features.get('pre_pick_market_context_soft'), dict) else {}

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
        'main_theme': _theme_bullets(row, soft if isinstance(soft, dict) else None),
        'risks': _risk_bullets(row),
        'social': {
            'sentiment_score': social,
            'status': 'present' if social_present else 'missing',
            'quality': social_quality or None,
            'collection_status': social_collection or None,
            'source_layers': social_layers[:4],
        },
        'soft_context': {
            'valid': bool((soft or {}).get('soft_context_valid', soft.get('high_confidence_favored') if soft else False)),
            'source': (soft or {}).get('soft_context_source') or (soft or {}).get('importance') or '',
            'stance': (soft or {}).get('market_stance') or '',
            'favored_hits': list((soft or {}).get('favored_hits') or [])[:5],
            'risk_hits': list((soft or {}).get('risk_hits') or [])[:5],
            'confidence': _f((soft or {}).get('confidence')),
            'hard_gate': False,
            'force_pick': False,
        },
        'repo_summary': _clip_text(row.get('repo_contribution_summary') or '', 240),
        'decision_reason': _clip_text(reason or row.get('decision_reason') or '', 200),
        'similar_cases': list(similar_cases or [])[:5],
        'one_liner': '',
    }
    theme_bits = []
    if card['main_theme']:
        theme_bits.append(card['main_theme'][0])
    fund_bits = card['fund_flow'][:1]
    risk_bits = card['risks'][:1]
    parts = [f"{symbol} {name}".strip()]
    if score is not None:
        parts.append(f'score={score:.2f}')
    parts.extend(theme_bits)
    parts.extend(fund_bits)
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
        'soft_context': card.get('soft_context') or {},
        'similar_cases': list(card.get('similar_cases') or [])[:3],
        'legacy_repo_summary': _clip_text(legacy_reason or card.get('repo_summary') or '', 200),
        'decision_reason': card.get('decision_reason') or '',
    }
