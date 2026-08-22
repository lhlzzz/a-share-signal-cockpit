#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper-pick eligibility surfaces extracted from the forward runner.

Host binding: call `bind_host(runner_module)` after the runner defines shared
helpers. Free names resolve from host on each public call (monkeypatch-safe).
Production entry remains xiaogu_forward_runner.py which re-exports.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from xiaogu_forward_gates import (
    candidate_evidence_missing_flags,
    production_evidence_missing_flags,
)
from xiaogu_forward_host_binding import create_host_binding


_HOST = None

PAPER_PICK_ONE_LOT_SHARES = 100
PAPER_PICK_MAX_PRICE = 70.0

REQUIRED_FROM_HOST = (
    'PRODUCTION_POLICY',
    'PRODUCTION_POLICY_ZH',
    'SCORING_CONFIG_DEFAULTS',
    '_cached_paper_pick_eligibility_profile',
    '_cached_structured_signal_profile',
    '_candidate_lifecycle_profile',
    '_candidate_runtime_cache_key',
    'candidate_capital_risk_profile',
    'candidate_repo_delta_by_repo',
    'candidate_score_value',
    'classify_limitup_reason_evidence',
    'detect_pool_hollow_theme_tags',
    'early_opportunity_score_for_row',
    'get_scoring_config_snapshot',
    'is_routine_regulatory_block',
    'limitup_quality_block_reason',
    'limitup_reason_supports_hard_confirmation',
    'historical_t1_loss_streak_before',
    'market_adaptive_context',
    'market_adaptive_thresholds',
    'normalize_tag_list',
    'normalized_block_bucket',
    'normalized_source_time_for_candidate',
    'opportunity_hard_block_reason',
    'paper_pick_risk_explanation_gate',
    'paper_sizing_context',
    'regulatory_hard_block_reason',
    'replay_only_sector_opportunity',
    'safe_float',
    'scan_age_minutes',
    'sector_gate_threshold_for_market',
    'shadow_risk_profile',
    'signal_stage_bucket',
    'strong_sector_theme_partial_aux_exception_allowed',
    'unique_text_values'
)

bind_host, _inject_host, _with_host = create_host_binding(
    globals(),
    REQUIRED_FROM_HOST,
    (
        'LIVE_SCAN_ROOT', 'CANDIDATE_BUNDLE_ROOT', 'RAW_ROOT', 'BASE',
        'SCAN_SUMMARY_NAME', 'SCAN_SUMMARY_RUNNER_NAME', 'RULE_VERSION',
        'SCORING_CONFIG_DEFAULTS', 'ALLOWED_A_SHARE_SOURCE_TOKENS',
    ),
    preserve_existing_on_missing=True,
)


def _buyability_signal_pct(row: Dict[str, Any]) -> Optional[float]:
    """Best available same-day % change for final-pick buyability checks."""
    _sf = globals().get('safe_float')
    for key in ('signal_pct', 'pct_chg', 'change_pct'):
        raw = row.get(key)
        if raw is None or raw == '':
            continue
        if callable(_sf):
            value = _sf(raw)
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
        if value is not None:
            return float(value)
    return None


def _mainboard_like_limit_seal_threshold(symbol: str) -> float:
    """Pct above which an unmarked name is treated as sealed limit-up.

    Project paper chain is main-board oriented (10% daily limit). Growth/STAR
    20% boards use a higher seal threshold so we do not over-block mid-move names.
    """
    code = str(symbol or '').strip().zfill(6)
    if code.startswith(('300', '301', '688', '689')):
        return 19.0
    return 9.5


def _inferred_sealed_limit_up(row: Dict[str, Any], details: Dict[str, Any] | None = None) -> bool:
    """True when explicit seal flag is set OR price action is already at board.

    Historical rows often omit sealed_limit_up even when pct≈10 and the name sits
    in the limit-up pool — those tickets cannot be bought as a final PAPER_PICK.
    """
    details = details if isinstance(details, dict) else {}
    current_limitup_markers = (
        'sealed_limit_up',
        'is_limit_up',
        'is_limitup',
        'limit_up',
        'limitup_pool_member',
        'in_limitup_pool',
        'from_limitup_pool',
    )
    if any(bool(row.get(key) or details.get(key)) for key in current_limitup_markers):
        return True
    pct = _buyability_signal_pct(row)
    if pct is None:
        return False
    symbol = str(row.get('symbol') or row.get('code') or '')
    # 9.5% is the near-cap chase boundary used by ranking, not proof of a
    # sealed board. Require a strict exceedance when no explicit limitup marker
    # is present; explicit pool/seal flags remain hard evidence.
    return pct > _mainboard_like_limit_seal_threshold(symbol)


def current_day_tradable_filter_reason(
    row: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
) -> str:
    """Return why a current-day candidate must leave the tradable pool.

    This is deliberately narrower than full PAPER_PICK eligibility. A current
    limit-up name is useful evidence for sector/continuation analysis, but it is
    not a buyable T-day ticket and must not occupy a candidate-pool seat.
    A negative T-day move is not a hard rejection. Water-under reversal setups
    are evaluated by the T+1 profit gate below.
    """
    del bundle
    details = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    if _inferred_sealed_limit_up(row, details):
        return 'CURRENT_DAY_LIMIT_UP_NOT_TRADABLE'
    research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    if catalyst_quality.get('regulatory_hard_block') or catalyst_quality.get('category') in ('risk_notice', 'regulatory_notice'):
        return 'REGULATORY_HARD_BLOCK:' + str(catalyst_quality.get('category') or 'regulatory_notice')
    if risk_review.get('disqualified_for_paper_pick'):
        return 'REGULATORY_HARD_BLOCK:a_share_risk_review_disqualified'
    return ''


def filter_current_day_tradable_candidates(
    rows: List[Dict[str, Any]],
    bundle: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Drop current-day sealed limit-ups; retain underwater rows for T+1 gating."""
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    reasons: Dict[str, int] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        reason = current_day_tradable_filter_reason(row, bundle)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            dropped.append({
                'symbol': str(row.get('symbol') or row.get('code') or ''),
                'name': row.get('name') or row.get('stock_name') or '',
                'reason': reason,
                'signal_pct': _buyability_signal_pct(row),
            })
            continue
        kept.append(row)
    return kept, {
        'source_count': len(kept) + len(dropped),
        'kept_count': len(kept),
        'dropped_count': len(dropped),
        'drop_reasons': reasons,
        'dropped': dropped,
        'policy': 'current_day_limitup_excluded;negative_move_retained_for_underwater_reversal_gate',
    }


def broken_limitup_continuation_exception(
    row: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Admit a tightly bounded yesterday-limitup/broken-board continuation path.

    The exception is only for a buyable main-board row with strong close,
    volume, positive capital flow, and at least one independent confirmation.
    It never bypasses sealed-limit-up buyability, regulatory blocks, risk
    notices, or material capital-risk codes.
    """
    row = row if isinstance(row, dict) else {}
    bundle = bundle if isinstance(bundle, dict) else {}
    symbol = str(row.get('symbol') or row.get('code') or '').strip().zfill(6)
    details = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    feature_bool = globals().get('_profit_feature_bool')
    feature_float = globals().get('_profit_feature_float')
    evidence = row.get('broken_limitup_evidence')
    if not isinstance(evidence, dict):
        evidence = {}
    broken = bool(
        evidence.get('eligible')
        or str(evidence.get('status') or '').upper() in ('PASS', 'OK', 'CONFIRMED')
        or row.get('broken_limitup')
        or row.get('failed_limitup')
        or (callable(feature_bool) and feature_bool(row, 'broken_limitup', 'failed_limitup'))
        or (safe_float(row.get('zbc')) or 0.0) > 0
    )
    yesterday_gene = row.get('yesterday_limitup_gene_evidence')
    if not isinstance(yesterday_gene, dict):
        yesterday_gene = {}
    previous_limitup = bool(
        row.get('previous_limitup')
        or row.get('was_yesterday_limitup')
        or yesterday_gene.get('candidate_was_yesterday_limitup')
        or str(yesterday_gene.get('status') or '').upper() in ('PASS', 'OK', 'CONFIRMED', 'TRUE', '1')
        or (safe_float(row.get('prev_day_pct_chg') if row.get('prev_day_pct_chg') is not None else row.get('yesterday_pct_chg')) or 0.0) >= 9.5
        or (callable(feature_bool) and feature_bool(row, 'previous_limitup', 'was_yesterday_limitup', 'candidate_was_yesterday_limitup'))
    )
    source = '当日炸板后延续' if broken else '昨日涨停延续'
    reasons: List[str] = []
    hard_blockers: List[str] = []

    if not symbol.startswith(('600', '601', '603', '605', '000', '001', '002', '003')):
        hard_blockers.append('非主板股票')
    if not (broken or previous_limitup):
        hard_blockers.append('缺少昨日涨停或当日炸板证据')
    if _inferred_sealed_limit_up(row, details):
        hard_blockers.append('当前仍封死涨停，不可买入')
    pct = _buyability_signal_pct(row)
    if pct is None:
        hard_blockers.append('缺少当日涨幅')
    elif pct < 5.0 or pct >= _mainboard_like_limit_seal_threshold(symbol):
        hard_blockers.append('涨幅不在可控延续区间')

    market = market_adaptive_context(row, bundle)
    market_regime = str(
        market.get('production_regime')
        or market.get('market_regime')
        or row.get('market_regime')
        or bundle.get('market_regime')
        or ''
    ).upper()
    weak_market = bool(
        market.get('weak_acceptance_market')
        or market.get('broken_limit_pressure')
        or market_regime in ('WEAK', 'RISK_OFF_TECH_DEFENSIVE', 'DEFENSIVE_ROTATION', 'DOWNTREND')
    )
    close_position = safe_float(row.get('close_position_score'))
    volume_ratio = safe_float(row.get('volume_ratio')) or 0.0
    fund_flow = safe_float(row.get('fund_flow_momentum'))
    net_inflow = safe_float(row.get('net_inflow_main')) or 0.0
    if fund_flow is None:
        fund_flow = safe_float(details.get('fund_flow_momentum'))
    if fund_flow is None and callable(feature_float):
        fund_flow = feature_float(row, 'fund_flow_momentum')
    if net_inflow <= 0:
        capital_flow = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
        net_inflow = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
    close_floor = 0.84 if weak_market else 0.80
    if close_position is None or close_position < close_floor:
        hard_blockers.append(f'收盘位置不足{close_floor:.2f}')
    if volume_ratio < 1.50:
        hard_blockers.append('成交量未达到1.50倍')
    if (fund_flow or 0.0) < 0.55 and net_inflow <= 0:
        hard_blockers.append('主力资金未确认流入')

    regulatory = regulatory_hard_block_reason(row, bundle)
    risk_penalty = safe_float(row.get('risk_notice_penalty')) or safe_float(details.get('risk_notice_penalty')) or 0.0
    capital_risk = row.get('capital_risk_profile') if isinstance(row.get('capital_risk_profile'), dict) else {}
    if regulatory:
        hard_blockers.append('监管硬阻断:' + regulatory)
    if risk_penalty >= 0.60:
        hard_blockers.append('风险公告惩罚过高')
    if row.get('limitup_reason_hard_block'):
        hard_blockers.append('涨停理由证据硬阻断')
    if capital_risk.get('risk_codes'):
        hard_blockers.append('资金风险硬阻断:' + str(capital_risk['risk_codes'][0]))

    continuation_gene = max(
        safe_float(row.get('continuation_gene_score')) or 0.0,
        safe_float(details.get('continuation_gene_score')) or 0.0,
        (feature_float(row, 'continuation_gene_score') if callable(feature_float) else 0.0) or 0.0,
    )
    announcement_score = max(
        safe_float(row.get('announcement_catalyst_score')) or 0.0,
        safe_float(details.get('announcement_catalyst_score')) or 0.0,
        (feature_float(row, 'announcement_catalyst_score') if callable(feature_float) else 0.0) or 0.0,
    )
    sector_news = max(
        safe_float(row.get('sector_news_catalyst_score')) or 0.0,
        safe_float(row.get('sector_news_strength')) or 0.0,
        safe_float(details.get('sector_news_catalyst_score')) or 0.0,
        (feature_float(row, 'sector_news_catalyst_score', 'sector_news_strength') if callable(feature_float) else 0.0) or 0.0,
    )
    sector_score = max(
        safe_float(row.get('sector_opportunity_score')) or 0.0,
        safe_float(row.get('sector_catalyst_score')) or 0.0,
    )
    theme_core = safe_float(row.get('main_theme_core_score')) or safe_float(details.get('main_theme_core_score')) or 0.0
    theme_alignment = safe_float(row.get('main_theme_alignment_score')) or safe_float(details.get('main_theme_alignment_score')) or 0.0
    reason_quality = max(
        safe_float(row.get('limitup_reason_quality_score')) or 0.0,
        safe_float(details.get('limitup_reason_quality_score')) or 0.0,
    )
    reason_status = str(row.get('limitup_reason_status') or details.get('limitup_reason_status') or '').upper()
    pre_limitup_anomaly = max(
        safe_float(row.get('pre_limitup_anomaly')) or 0.0,
        safe_float(details.get('pre_limitup_anomaly')) or 0.0,
    )
    intraday_alert = max(
        safe_float(row.get('intraday_alert_strength')) or 0.0,
        safe_float(details.get('intraday_alert_strength')) or 0.0,
    )
    announcement_evidence = row.get('announcement_evidence') if isinstance(row.get('announcement_evidence'), list) else []
    positive_announcement = any(
        any(
            token in str(item.get('title') or item.get('text') or '')
            for token in ('增持', '回购', '合同', '订单', '业绩', '重组', '投资', '融资', '预增')
        )
        for item in announcement_evidence
        if isinstance(item, dict)
    )
    confirmations: List[str] = []
    if continuation_gene >= 0.45:
        confirmations.append('涨停基因/延续性')
    if announcement_score >= 0.75 or positive_announcement:
        confirmations.append('公告催化')
    if sector_news >= 0.65 or sector_score >= 0.60 or (theme_core >= 0.55 and theme_alignment >= 0.60):
        confirmations.append('主线/板块确认')
    if reason_quality >= 0.45 or reason_status in ('PASS', 'OK', 'CONFIRMED', 'DIRECT'):
        confirmations.append('涨停理由确认')
    if pre_limitup_anomaly >= 0.75 and intraday_alert >= 0.75:
        confirmations.append('盘中强承接')
    if not confirmations:
        hard_blockers.append('缺少公告、主线、涨停基因或盘中承接确认')

    if not hard_blockers:
        reasons.append(f'{source}，当前仍可交易')
        if close_position is not None:
            reasons.append(f'收盘位置{close_position:.2f}，弱市门槛{close_floor:.2f}')
        reasons.append(f'主力资金动量{fund_flow or 0.0:.2f}，净流入{net_inflow:.0f}')
        reasons.append(f'成交量放大{volume_ratio:.2f}倍')
        reasons.extend(confirmations)
        if weak_market:
            reasons.append('弱市提高确认门槛已满足')

    return {
        'eligible': not hard_blockers,
        'source': source,
        'market_regime': market_regime.lower() or 'unknown',
        'weak_market': weak_market,
        'confirmations': confirmations,
        'reasons_zh': reasons,
        'hard_blockers': hard_blockers,
        'features': {
            'signal_pct': pct,
            'close_position_score': close_position,
            'volume_ratio': volume_ratio,
            'fund_flow_momentum': fund_flow,
            'net_inflow_main': net_inflow,
            'continuation_gene_score': continuation_gene,
            'announcement_catalyst_score': announcement_score,
            'sector_news_catalyst_score': sector_news,
            'sector_opportunity_score': sector_score,
            'pre_limitup_anomaly': pre_limitup_anomaly,
            'intraday_alert_strength': intraday_alert,
        },
        'policy': 'controlled_limitup_continuation_exception;hard_buyability_and_regulatory_gates_unchanged',
    }


_PROFIT_FEATURE_NESTED_KEYS = (
    'structured_components',
    'structured_score_components',
    'structured_component_details',
    'components',
    'component_details',
    'candidate_features',
    'factor_snapshot',
    'raw_json',
    'paper_pick_eligibility',
    'signals',
    'research_signals',
    'catalyst_quality',
    'a_share_risk_review',
    'yesterday_limitup_gene_evidence',
    'yesterday_one_word_limitup_gene_evidence',
    'data_directory_capital_flow',
    'observed_recorded_features',
)


def _profit_feature_sources(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return bounded, pre-T-day snapshot containers in precedence order."""
    sources: List[Dict[str, Any]] = []
    pending: List[Tuple[Dict[str, Any], int]] = [(row, 0)]
    seen: set[int] = set()
    while pending:
        source, depth = pending.pop(0)
        if id(source) in seen:
            continue
        seen.add(id(source))
        sources.append(source)
        if depth >= 3:
            continue
        for nested_key in _PROFIT_FEATURE_NESTED_KEYS:
            nested = source.get(nested_key)
            if isinstance(nested, dict):
                pending.append((nested, depth + 1))
    return sources


def _profit_feature_value(row: Dict[str, Any], key: str) -> Any:
    """Read one T-day profit feature from bounded recorded snapshots."""
    for source in _profit_feature_sources(row):
        if source.get(key) is not None:
            return source.get(key)
    return None


def _profit_feature_float(row: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _profit_feature_value(row, key)
        if value is None or value == '':
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _profit_feature_bool(row: Dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = _profit_feature_value(row, key)
        if isinstance(value, str):
            if value.strip().lower() in ('true', '1', 'yes', 'pass'):
                return True
            continue
        if value:
            return True
    return False


def t1_profit_candidate_profile(
    row: Dict[str, Any],
    bundle: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Score whether a T-day row has enough evidence for positive T+1 expectation.

    This is a pre-sort admission gate, not a promise of profit. It uses only
    T-day fields and requires an independent confirmation path, so a high legacy
    score or a sector proxy cannot by itself create a candidate-pool seat.
    """
    stamped_profile = row.get('t1_profit_profile')
    if (
        isinstance(stamped_profile, dict)
        and isinstance(stamped_profile.get('eligible'), bool)
        and row.get('t1_profit_candidate') is not None
    ):
        return {
            **stamped_profile,
            'eligible': bool(stamped_profile['eligible']),
            'admission_source': 'upstream_t1_profit_gate',
        }
    pct = _buyability_signal_pct(row)
    pct = float(pct) if pct is not None else 0.0
    close_position = _profit_feature_float(row, 'close_position_score')
    volume_ratio = _profit_feature_float(row, 'volume_ratio')
    fund_flow = _profit_feature_float(row, 'fund_flow_momentum')
    net_inflow = _profit_feature_float(row, 'net_inflow_main', 'fund_inflow_positive', 'main_force_net_inflow')
    continuation_gene = _profit_feature_float(row, 'continuation_gene_score')
    theme_core = _profit_feature_float(row, 'main_theme_core_score')
    theme_alignment = _profit_feature_float(row, 'main_theme_alignment_score')
    limitup_quality = _profit_feature_float(row, 'limitup_reason_quality_score')
    announcement = _profit_feature_float(row, 'announcement_catalyst_score')
    sector_news = _profit_feature_float(
        row,
        'sector_news_catalyst_score',
        'sector_news_strength',
    )
    low_position = _profit_feature_float(row, 'low_position_catalyst_score')
    intraday_alert = _profit_feature_float(row, 'intraday_alert_strength')
    direct_news_count = _profit_feature_float(row, 'direct_symbol_news_count')
    turnover = _profit_feature_float(row, 'turnover_rate')
    underwater_recovery = _profit_feature_float(row, 'underwater_recovery_score')
    if underwater_recovery > 1.0:
        underwater_recovery = min(1.0, underwater_recovery / 100.0)
    weak_to_strong = _profit_feature_float(row, 'weak_to_strong_reversal')
    first_board_pre_signal = _profit_feature_float(row, 'first_board_pre_signal')
    pre_limitup_anomaly = _profit_feature_float(row, 'pre_limitup_anomaly')
    candidate_stage = str(_profit_feature_value(row, 'candidate_stage') or '').lower()
    search_layer_hint = str(_profit_feature_value(row, 'search_layer_hint') or '').lower()
    setup_type = str(_profit_feature_value(row, 'setup_type') or '').upper()
    risk_penalty = _profit_feature_float(row, 'risk_notice_penalty')
    failed_limitup = _profit_feature_bool(row, 'failed_limitup', 'post_limitup_weak_continuation')
    continuation_exception = broken_limitup_continuation_exception(row, bundle)
    previous_limitup = _profit_feature_bool(
        row,
        'previous_limitup',
        'was_yesterday_limitup',
        'candidate_was_yesterday_limitup',
    )
    near_limitup = _profit_feature_bool(row, 'near_limitup_close') or close_position >= 0.80
    high_turnover_continuation = _profit_feature_bool(
        row,
        'high_turnover_continuation',
    ) or turnover >= 8.0
    weak_fund = fund_flow < 0.30 and net_inflow <= 0.0
    previous_limitup_continuation = bool(
        previous_limitup
        and (
            continuation_gene >= 0.45
            or (
                volume_ratio >= 1.0
                and close_position >= 0.55
                and (fund_flow >= 0.35 or net_inflow > 0.0)
            )
            or (
                fund_flow >= 0.55
                and (close_position >= 0.45 or volume_ratio >= 1.0)
            )
        )
    )

    confirmations: List[str] = []
    if (
        continuation_gene >= 0.45
        or previous_limitup_continuation
        or (near_limitup and high_turnover_continuation)
    ):
        confirmations.append('continuation_structure')
    if (
        pct >= 3.0
        and close_position >= 0.72
        and volume_ratio >= 1.15
        and (fund_flow >= 0.30 or net_inflow > 0.0)
    ):
        confirmations.append('momentum_and_flow')
    if (
        (direct_news_count > 0 or announcement >= 0.75)
        and pct >= 2.5
        and close_position >= 0.60
        and volume_ratio >= 0.95
        and (fund_flow >= 0.25 or net_inflow > 0.0)
    ):
        confirmations.append('catalyst_and_flow')
    if (
        theme_alignment >= 0.75
        and theme_core >= 0.55
        and pct >= 3.0
        and close_position >= 0.65
        and volume_ratio >= 1.10
        and (fund_flow >= 0.30 or net_inflow > 0.0)
    ):
        confirmations.append('theme_breakout')
    if (
        low_position >= 0.60
        and intraday_alert >= 0.60
        and pct >= 2.5
        and close_position >= 0.65
        and volume_ratio >= 1.10
        and net_inflow > 0.0
    ):
        confirmations.append('low_position_breakout')
    if continuation_exception.get('eligible'):
        confirmations.append('limitup_continuation_exception')
    intraday_reversal = (
        pct < 0.0
        and (
            search_layer_hint == 'intraday_alert_reversal'
            or setup_type in ('INTRADAY_ALERT_REVERSAL', 'LIMITUP_REASON_PROPAGATION')
        )
        and volume_ratio >= 2.0
        and turnover >= 12.0
        and max(intraday_alert, limitup_quality, _profit_feature_float(
            row,
            'limitup_reason_propagation_score',
            'limitup_reason_strength',
        )) >= 0.35
        and (net_inflow > 0.0 or fund_flow >= 0.30 or low_position >= 0.35)
    )
    underwater_continuation = (
        pct < 0.0
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5')
        and volume_ratio >= 1.0
        and (
            (
                previous_limitup
                and (fund_flow >= 0.55 or net_inflow > 0.0)
                and (close_position >= 0.45 or turnover >= 5.0)
            )
            or (
                fund_flow >= 0.75
                and net_inflow > 0.0
                and (
                    sector_news >= 0.70
                    or low_position >= 0.55
                    or intraday_alert >= 0.45
                )
            )
        )
    )
    underwater_reversal_signal = (
        pct < 0.0
        and (
            (
                max(weak_to_strong, first_board_pre_signal, pre_limitup_anomaly) >= 0.65
                and volume_ratio >= 1.15
                and (net_inflow > 0.0 or fund_flow >= 0.15)
                and (close_position >= 0.55 or underwater_recovery >= 0.65)
            )
            or (
                underwater_recovery >= 0.70
                and volume_ratio >= 1.15
                and (net_inflow > 0.0 or fund_flow >= 0.25)
                and (
                    max(theme_core, theme_alignment, low_position, intraday_alert) >= 0.55
                    or candidate_stage == 'underwater'
                    or search_layer_hint == 'underwater_reversal'
                    or setup_type == 'UNDERWATER_TO_RED_STRENGTH'
                )
            )
        )
    )
    underwater_reversal = bool(
        underwater_reversal_signal
        or intraday_reversal
        or underwater_continuation
    )
    if intraday_reversal:
        confirmations.append('intraday_reversal')
    if underwater_continuation:
        confirmations.append('underwater_continuation')
    if underwater_reversal:
        confirmations.append('underwater_reversal')

    # This is the specific failure shape seen in 002452: a mild green close
    # with weak participation and only sector-proxy support.
    weak_low_move = (
        pct < 3.0
        and close_position < 0.70
        and volume_ratio < 1.0
        and continuation_gene < 0.45
        and direct_news_count <= 0.0
        and announcement < 0.75
    )
    risk_reasons: List[str] = []
    if failed_limitup:
        risk_reasons.append('failed_limitup_or_weak_post_limitup')
    if weak_fund and close_position < 0.70:
        risk_reasons.append('weak_fund_confirmation')
    if risk_penalty >= 0.50:
        risk_reasons.append('risk_notice_penalty')

    # Bounded diagnostic score. It is intentionally not used as a calibrated
    # probability until a larger full-pool label set is available.
    score = (
        min(1.0, max(0.0, pct / 8.0)) * 0.15
        + min(1.0, max(0.0, close_position)) * 0.17
        + min(1.0, max(0.0, volume_ratio / 2.0)) * 0.15
        + min(1.0, max(0.0, fund_flow)) * 0.15
        + min(1.0, max(0.0, continuation_gene)) * 0.18
        + underwater_recovery * 0.16
        + max(weak_to_strong, first_board_pre_signal, pre_limitup_anomaly) * 0.12
        + max(theme_core, theme_alignment) * 0.10
        + max(limitup_quality, announcement, sector_news) * 0.10
        + min(0.10, low_position * 0.05 + intraday_alert * 0.05)
        + (0.16 if underwater_continuation else 0.0)
        + (0.32 if intraday_reversal else 0.0)
        - min(0.35, risk_penalty * 0.25)
        - (0.12 if weak_fund else 0.0)
        - (0.06 if failed_limitup and continuation_exception.get('eligible') else (0.18 if failed_limitup else 0.0))
        - (0.08 if pct < 0.0 and not underwater_reversal else 0.0)
        + (0.16 if continuation_exception.get('eligible') else 0.0)
    )
    score = round(max(0.0, min(1.0, score)), 4)
    blocked = bool(
        weak_low_move
        or not confirmations
        or len(risk_reasons) >= 2
        or score < 0.48
    )
    return {
        'eligible': not blocked,
        'reason': (
            'T1_PROFIT_EVIDENCE_INSUFFICIENT'
            if weak_low_move or not confirmations
            else ('T1_PROFIT_RISK_DOMINATES' if len(risk_reasons) >= 2 else 'T1_PROFIT_SCORE_BELOW_FLOOR')
            if blocked
            else ''
        ),
        'expected_t1_profit_score': score,
        'confirmations': confirmations,
        'risk_reasons': risk_reasons,
        'features': {
            'signal_pct': round(pct, 4),
            'close_position_score': round(close_position, 4),
            'volume_ratio': round(volume_ratio, 4),
            'fund_flow_momentum': round(fund_flow, 4),
            'net_inflow_main': round(net_inflow, 4),
            'continuation_gene_score': round(continuation_gene, 4),
            'underwater_recovery_score': round(underwater_recovery, 4),
            'weak_to_strong_reversal': round(weak_to_strong, 4),
            'first_board_pre_signal': round(first_board_pre_signal, 4),
            'pre_limitup_anomaly': round(pre_limitup_anomaly, 4),
            'underwater_reversal': underwater_reversal,
            'intraday_reversal': intraday_reversal,
            'underwater_continuation': underwater_continuation,
            'previous_limitup_continuation': previous_limitup_continuation,
            'main_theme_core_score': round(theme_core, 4),
            'main_theme_alignment_score': round(theme_alignment, 4),
            'limitup_reason_quality_score': round(limitup_quality, 4),
            'announcement_catalyst_score': round(announcement, 4),
            'sector_news_catalyst_score': round(sector_news, 4),
            'low_position_catalyst_score': round(low_position, 4),
            'intraday_alert_strength': round(intraday_alert, 4),
            'direct_symbol_news_count': round(direct_news_count, 4),
            'risk_notice_penalty': round(risk_penalty, 4),
            'weak_low_move': weak_low_move,
            'limitup_continuation_exception': continuation_exception,
        },
        'policy': 't1_expected_profit_gate_before_top10_and_paper_pick',
    }


def filter_t1_profit_candidates(
    rows: List[Dict[str, Any]],
    bundle: Dict[str, Any] | None = None,
    *,
    enforce: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Admit only rows with independent T-day evidence for T+1 profit."""
    tradable_rows, tradable_summary = filter_current_day_tradable_candidates(rows, bundle)
    if not enforce:
        return tradable_rows, {
            **tradable_summary,
            't1_profit_gate': {'enabled': False, 'source_count': len(tradable_rows)},
        }

    kept: List[Dict[str, Any]] = []
    dropped = list(tradable_summary.get('dropped') or [])
    reasons = dict(tradable_summary.get('drop_reasons') or {})
    for row in tradable_rows:
        profile = t1_profit_candidate_profile(row, bundle)
        if not profile['eligible']:
            reason = str(profile['reason'] or 'T1_PROFIT_EVIDENCE_INSUFFICIENT')
            reasons[reason] = reasons.get(reason, 0) + 1
            dropped.append({
                'symbol': str(row.get('symbol') or row.get('code') or ''),
                'name': row.get('name') or row.get('stock_name') or '',
                'reason': reason,
                'signal_pct': _buyability_signal_pct(row),
                't1_profit_profile': profile,
            })
            continue
        trade_date = str((bundle or {}).get('date') or (bundle or {}).get('source_time') or '')[:10]
        symbol = str(row.get('symbol') or row.get('code') or '').zfill(6)
        historical_streak_lookup = globals().get('historical_t1_loss_streak_before')
        prior_t1_return = None
        loss_streak = 0
        if trade_date and symbol and callable(historical_streak_lookup):
            try:
                loss_streak, prior_t1_return = historical_streak_lookup(trade_date, symbol)
            except Exception:
                loss_streak, prior_t1_return = 0, None
        if loss_streak >= 2 and prior_t1_return is not None and prior_t1_return <= 0:
            reason = 'RECENT_T1_NONPROFIT_HARD_BLOCK'
            reasons[reason] = reasons.get(reason, 0) + 1
            dropped.append({
                'symbol': symbol,
                'name': row.get('name') or row.get('stock_name') or '',
                'reason': reason,
                'signal_pct': _buyability_signal_pct(row),
                'latest_prior_t1_return': prior_t1_return,
                'prior_loss_streak': loss_streak,
                't1_profit_profile': profile,
            })
            continue
        stamped = dict(row)
        stamped['t1_profit_candidate'] = True
        stamped['t1_profit_profile'] = profile
        stamped['expected_t1_profit_score'] = profile['expected_t1_profit_score']
        kept.append(stamped)

    return kept, {
        'source_count': len(rows or []),
        'tradable_count': len(tradable_rows),
        'kept_count': len(kept),
        'dropped_count': len(dropped),
        'drop_reasons': reasons,
        'dropped': dropped,
        'policy': 't1_expected_profit_gate_before_top10_and_paper_pick',
        't1_profit_gate': {
            'enabled': True,
            'admitted_count': len(kept),
            'rejected_count': len(tradable_rows) - len(kept),
            'floor': 0.48,
        },
    }


def paper_pick_buyability_block_reason(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> str:
    """Block final picks that cannot be bought before close.

    Keep this intentionally narrow: a sealed (or effectively sealed) limit-up is
    not a buyable final ticket, and an explicit small-account buyability failure
    should also block. Limit-up names may remain in the pool for observation.
    """
    details = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    if _inferred_sealed_limit_up(row, details):
        return 'FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP'

    price = safe_float(row.get('price'))
    if price is not None and price > PAPER_PICK_MAX_PRICE:
        return 'FINAL_PICK_PRICE_EXCEEDS_70'

    small_account_buyable = row.get('small_account_buyable')
    if small_account_buyable is None and isinstance(details, dict):
        small_account_buyable = details.get('small_account_buyable')
    if small_account_buyable is False:
        return 'FINAL_PICK_MUST_BE_BUYABLE_SMALL_ACCOUNT_FALSE'
    return ''


def paper_pick_eligibility_profile(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> Dict[str, Any]:
    bundle = bundle if isinstance(bundle, dict) else {}
    main_force_behavior_mode = str(bundle.get('ranking_view') or '').strip().lower() == 'main_force_behavior_chain'
    profile = _cached_structured_signal_profile(row, bundle)
    scoring_config = get_scoring_config_snapshot()
    scoring_config_values = scoring_config.get('config') if isinstance(scoring_config, dict) else {}
    if not isinstance(scoring_config_values, dict):
        scoring_config_values = dict(SCORING_CONFIG_DEFAULTS)
    price = safe_float(row.get('price'))
    evidence_missing_flags = production_evidence_missing_flags(bundle)
    candidate_evidence_flags = candidate_evidence_missing_flags(row, bundle)
    normalized_source_time = normalized_source_time_for_candidate(row, bundle)
    repo_contributions = row.get('repo_contributions') if isinstance(row.get('repo_contributions'), dict) else {}
    vei_repo_contribution = repo_contributions.get('VEI') if isinstance(repo_contributions.get('VEI'), dict) else {}
    vei_repo_score_delta = safe_float(vei_repo_contribution.get('score_delta'))
    if vei_repo_score_delta is None:
        vei_repo_score_delta = safe_float(candidate_repo_delta_by_repo(row).get('VEI'))
    # Empty string is intentional ("block no weekdays"); do not collapse via `or` to defaults.
    _weekday_blocklist_cfg = scoring_config_values.get(
        'weekday_blocklist',
        SCORING_CONFIG_DEFAULTS.get('weekday_blocklist', ''),
    )
    _weekday_blocklist = '' if _weekday_blocklist_cfg is None else str(_weekday_blocklist_cfg)
    signals = {
        'data_gate_status': str(bundle.get('data_gate_status') or profile['data_gate_status'] or ''),
        'candidate_evidence_status': profile['candidate_evidence_status'] or str(bundle.get('candidate_evidence_status') or ''),
        'source_time': normalized_source_time or profile['source_time'] or str(bundle.get('source_time') or ''),
        'runner_asof_time': profile['runner_asof_time'] or str(bundle.get('_runner_asof_time') or bundle.get('runner_asof_time') or ''),
        'trade_mode': profile['trade_mode'],
        'primary_return_field': profile['primary_return_field'],
        'primary_trade_horizon': profile['primary_trade_horizon'],
        'production_policy': PRODUCTION_POLICY,
        'production_policy_zh': PRODUCTION_POLICY_ZH,
        'scoring_config_loaded': bool(scoring_config.get('loaded')),
        'scoring_config_source': str(scoring_config.get('source') or 'defaults'),
        'scoring_config_error': str(scoring_config.get('error') or ''),
        'weekday_blocklist': _weekday_blocklist,
        'max_score_cap': safe_float(scoring_config_values.get('max_score_cap')) or safe_float(SCORING_CONFIG_DEFAULTS['max_score_cap']),
        'one_lot_cost': profile['one_lot_cost'] if profile['one_lot_cost'] is not None else (
            price * PAPER_PICK_ONE_LOT_SHARES if price is not None else None
        ),
        'one_lot_cost_cap': safe_float(paper_sizing_context(bundle).get('one_lot_cost_cap')),
        'price_cap': PAPER_PICK_MAX_PRICE,
        'one_lot_shares': PAPER_PICK_ONE_LOT_SHARES,
        'risk_penalty': profile['risk_penalty'],
        'regulatory_hard_block': profile['regulatory_hard_block'] or regulatory_hard_block_reason(row, bundle),
        'opportunity_hard_block': profile['opportunity_hard_block'] or opportunity_hard_block_reason(row, bundle) or limitup_quality_block_reason(row, bundle),
        'limitup_reason_strength': profile['limitup_reason_strength'],
        'seal_order_strength': profile['seal_order_strength'],
        'order_book_pressure': profile['order_book_pressure'],
        'sector_opportunity_score': profile['sector_opportunity_score'],
        'sector_opportunity_tags': profile['sector_opportunity_tags'],
        'sector_news_strength': profile['sector_news_strength'],
        'fund_flow_momentum': profile['fund_flow_momentum'],
        'time_series_momentum': profile['time_series_momentum'],
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
        'limitup_capture_score': profile['limitup_capture_score'],
        'limitup_capture_profile': profile['limitup_capture_profile'],
        'limitup_capture_confirmed': profile['limitup_capture_confirmed'],
        'limitup_capture_reasons': profile['limitup_capture_reasons'],
        'close_position_score': profile['close_position_score'],
        'low_position_catalyst_score': profile['low_position_catalyst_score'],
        'candidate_stage': profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct']),
        'early_opportunity_score': profile['early_opportunity_score'] if profile['early_opportunity_score'] is not None else early_opportunity_score_for_row(row, bundle),
        'setup_type': profile['setup_type'],
        'research_signals': profile['research_signals'],
        'research_panel_overall': profile['research_panel_overall'],
        'catalyst_quality_category': profile['catalyst_quality_category'],
        'a_share_risk_review_disqualified_for_paper_pick': profile['a_share_risk_review_disqualified_for_paper_pick'],
        'historical_pattern_name': profile['historical_pattern_name'],
        'vei_repo_score_delta': vei_repo_score_delta,
    }
    buyability_block_reason = paper_pick_buyability_block_reason(row, bundle)
    signals['buyability_hard_block'] = buyability_block_reason
    signals['final_pick_buyable'] = not bool(buyability_block_reason)
    candidate_lifecycle = _candidate_lifecycle_profile(row, bundle)
    signals['candidate_lifecycle'] = candidate_lifecycle
    signals['trade_mode'] = candidate_lifecycle.get('trade_mode') or signals['trade_mode']
    signals['primary_return_field'] = candidate_lifecycle.get('primary_return_field') or signals['primary_return_field']
    signals['primary_trade_horizon'] = candidate_lifecycle.get('primary_trade_horizon') or signals['primary_trade_horizon']
    signals['setup_class'] = candidate_lifecycle.get('setup_class')
    signals['setup_rank'] = candidate_lifecycle.get('setup_rank')
    signals['setup_reason'] = candidate_lifecycle.get('setup_reason')
    signals['repeat_count'] = candidate_lifecycle.get('repeat_count')
    signals['stale_decay'] = candidate_lifecycle.get('stale_decay')
    signals['lifecycle_score'] = candidate_lifecycle.get('lifecycle_score')
    blockers: List[str] = []
    positive_conditions: List[str] = []
    missing_conditions: List[str] = []
    capital_risk_profile = candidate_capital_risk_profile(row)
    signals['capital_risk_profile'] = capital_risk_profile
    signals['continuation_gene_score'] = safe_float(row.get('continuation_gene_score')) or 0.0
    signals['limitup_reason_status'] = str(row.get('limitup_reason_status') or 'MISSING')
    signals['limitup_reason_hard_block'] = bool(row.get('limitup_reason_hard_block', False))
    if capital_risk_profile.get('risk_codes'):
        missing_conditions.extend(capital_risk_profile['risk_codes'])
    if capital_risk_profile.get('risk_softened_by_dark_pool_inflow'):
        positive_conditions.append('risk_softened_by_dark_pool_inflow')
    auxiliary_status = signals.get('mainboard_auxiliary_evidence_status')
    auxiliary_status_normalized = str(auxiliary_status or '').upper()
    if auxiliary_status_normalized in ('PASS', 'OK'):
        positive_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    elif auxiliary_status:
        missing_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    if (signals.get('risk_notice_penalty') or 0.0) >= 0.60:
        blockers.append('mainboard_auxiliary_risk_notice_penalty>=0.60')
    elif (signals.get('risk_notice_penalty') or 0.0) > 0:
        positive_conditions.append('mainboard_auxiliary_risk_notice_under_review')
    if buyability_block_reason:
        blockers.append(buyability_block_reason)
        missing_conditions.append('final_pick_must_be_buyable')

    price = safe_float(row.get('price'))
    data_gate_status = str(signals['data_gate_status'] or bundle.get('data_gate_status') or 'PASS')
    candidate_evidence_status = str(
        signals['candidate_evidence_status']
        or ('PASS' if not candidate_evidence_flags and not evidence_missing_flags else 'PARTIAL_OR_FAIL')
    )
    source_time = str(signals['source_time'] or '')
    runner_asof_time = str(signals['runner_asof_time'] or '')
    one_lot_cost = safe_float(signals['one_lot_cost'])
    if one_lot_cost is None and price is not None:
        one_lot_cost = price * PAPER_PICK_ONE_LOT_SHARES
    one_lot_cap = safe_float(signals['one_lot_cost_cap'])
    price_cap = safe_float(signals['price_cap']) or PAPER_PICK_MAX_PRICE
    risk_penalty = safe_float(signals['risk_penalty'])
    regulatory_block = str(signals['regulatory_hard_block'] or '')
    opportunity_block = str(signals['opportunity_hard_block'] or '')
    limitup_reason_strength = profile['limitup_reason_strength']
    seal_order_strength = profile['seal_order_strength']
    order_book_pressure = profile['order_book_pressure']
    replay_only_sector = replay_only_sector_opportunity(profile, row)
    raw_sector_opportunity_score = profile['sector_opportunity_score'] if profile['sector_opportunity_score'] is not None else 0.0
    sector_opportunity_score = 0.0 if replay_only_sector else raw_sector_opportunity_score
    sector_opportunity_tags = profile['sector_opportunity_tags']
    main_theme_alignment_score = profile.get('main_theme_alignment_score') or 0.0
    main_theme_core_score = profile.get('main_theme_core_score') or 0.0
    fund_flow_momentum = profile['fund_flow_momentum']
    time_series_momentum = profile['time_series_momentum']
    candidate_stage = profile['candidate_stage'] or signal_stage_bucket(profile['signal_pct'])
    early_opportunity_score = profile['early_opportunity_score'] if profile['early_opportunity_score'] is not None else early_opportunity_score_for_row(row, bundle)
    research_signals = profile.get('research_signals') if isinstance(profile.get('research_signals'), dict) else {}
    research_panel = research_signals.get('research_panel') if isinstance(research_signals.get('research_panel'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    a_share_risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    adversarial_review = research_signals.get('adversarial_review') if isinstance(research_signals.get('adversarial_review'), dict) else {}
    catalyst_quality_category_global = str(catalyst_quality.get('category') or profile['catalyst_quality_category'] or '')
    research_layer = str(profile['search_layer_hint'] or profile['setup_type'] or '')
    blocked_reasons = [str(reason) for reason in (row.get('blocked_reasons') or []) if str(reason)]
    near_limit_up_risk = bool(row.get('near_limit_up_risk')) or any(normalized_block_bucket(reason) == 'near_limit_up_risk' for reason in blocked_reasons)
    source_date = str(bundle.get('date') or row.get('date') or source_time[:10] or '')
    age_minutes = scan_age_minutes(source_time, source_date, runner_asof_time) if source_time and runner_asof_time and source_date else None
    time_gate_known = bool(source_time and runner_asof_time and source_date and age_minutes is not None)

    market_context = market_adaptive_context(row, bundle)
    market_regime = str(market_context.get('market_regime') or '')
    market_follow_through_score = safe_float(market_context.get('market_follow_through_score'))
    market_limitups = safe_float(market_context.get('market_limitups'))
    market_breadth_up_pct = safe_float(market_context.get('market_breadth_up_pct'))
    limitup_broken_ratio = safe_float(market_context.get('limitup_broken_ratio'))
    broken_limitups = safe_float(market_context.get('broken_limitups'))
    market_supports_high_chase = bool(market_context.get('supportive_market')) and not bool(market_context.get('weak_acceptance_market'))
    weak_acceptance_market = bool(market_context.get('weak_acceptance_market'))
    broken_limit_pressure = bool(market_context.get('broken_limit_pressure'))
    overheated_market = bool(market_context.get('overheated_market'))
    signals['market_regime'] = market_regime
    signals['market_follow_through_score'] = market_follow_through_score
    signals['limitup_broken_ratio'] = limitup_broken_ratio
    signals['broken_limitups'] = broken_limitups
    signals['market_supports_high_chase'] = market_supports_high_chase
    signals['weak_acceptance_market'] = weak_acceptance_market
    signals['broken_limit_pressure'] = broken_limit_pressure
    signals['overheated_market'] = overheated_market
    limitup_continuation = broken_limitup_continuation_exception(row, bundle)
    signals['limitup_continuation_exception'] = limitup_continuation
    if limitup_continuation.get('eligible'):
        positive_conditions.append('limitup_continuation_exception')
        positive_conditions.extend(
            'limitup_continuation:' + str(item)
            for item in (limitup_continuation.get('confirmations') or [])
        )
    else:
        missing_conditions.extend(
            'limitup_continuation:' + str(item)
            for item in (limitup_continuation.get('hard_blockers') or [])[:3]
        )
    shadow_profile = shadow_risk_profile(row, bundle, profile)
    signals.update({
        'market_regime_risk': shadow_profile['market_regime_risk'],
        'weak_market': shadow_profile['weak_market'],
        'market_regime_risk_reason': shadow_profile['market_regime_risk_reason'],
        'chase_high_risk': shadow_profile['chase_high_risk'],
        'chase_high_shadow_penalty': shadow_profile['chase_high_shadow_penalty'],
        'chase_high_reason': shadow_profile['chase_high_reason'],
        'defensive_carry_score': shadow_profile['defensive_carry_score'],
        'defensive_reason': shadow_profile['defensive_reason'],
        'limitup_gene_strength': shadow_profile['limitup_gene_strength'],
        'limitup_gene_shadow_gate': shadow_profile['limitup_gene_shadow_gate'],
        'limitup_gene_block_reason': shadow_profile['limitup_gene_block_reason'],
        'social_confirmation': shadow_profile['social_confirmation'],
    })
    enhanced_counts = row.get('enhanced_evidence_domain_counts') if isinstance(row.get('enhanced_evidence_domain_counts'), dict) else {}
    limitup_context_present = (safe_float(enhanced_counts.get('limitup_context')) or 0.0) > 0.0
    research_panel_overall = str(research_panel.get('overall') or signals.get('research_panel_overall') or '')
    setup_type_signal = str(signals.get('setup_type') or profile.get('setup_type') or row.get('setup_type') or '')
    sector_follower_diagnostic_only = bool(
        str(profile.get('search_layer_hint') or '') == 'sector_follower'
        or str(row.get('search_layer') or '') == 'sector_follower'
        or setup_type_signal == 'SECTOR_FOLLOWER'
    )
    signals['sector_follower_diagnostic_only'] = sector_follower_diagnostic_only
    if sector_follower_diagnostic_only:
        blockers.append('sector_follower_diagnostic_only')
        missing_conditions.append('official_layer_not_sector_follower')
    continuation_gene_score = safe_float(signals.get('continuation_gene_score')) or 0.0
    limitup_capture_score_for_gap = safe_float(signals.get('limitup_capture_score')) or 0.0
    limitup_reason_status_for_gap = str(signals.get('limitup_reason_status') or '').upper()
    sector_catalyst_score_for_gap = safe_float(signals.get('sector_catalyst_score')) or 0.0
    topic_propagation_score_for_gap = safe_float(signals.get('topic_propagation_score')) or 0.0
    sector_news_strength_for_gap = safe_float(signals.get('sector_news_strength')) or 0.0
    news_catalyst_strength_for_gap = safe_float(signals.get('news_catalyst_strength')) or 0.0
    announcement_catalyst_score_for_gap = safe_float(signals.get('announcement_catalyst_score')) or 0.0
    limitup_reason_quality_score_for_gap = safe_float(signals.get('limitup_reason_quality_score')) or 0.0
    low_position_catalyst_score_for_gap = safe_float(signals.get('low_position_catalyst_score')) or 0.0
    amount_for_gap = safe_float(row.get('amount')) or safe_float(row.get('成交额')) or 0.0
    net_inflow_for_gap = safe_float(row.get('net_inflow_main')) or 0.0
    if net_inflow_for_gap <= 0:
        capital_flow_for_gap = row.get('data_directory_capital_flow') if isinstance(row.get('data_directory_capital_flow'), dict) else {}
        net_inflow_for_gap = safe_float(capital_flow_for_gap.get('main_force_net_inflow')) or 0.0
    amount_pctile_for_gap = max(
        safe_float(row.get('full_universe_amount_pctile')) or 0.0,
        safe_float(row.get('amount_pctile_rule')) or 0.0,
    )
    fund_pctile_for_gap = max(
        safe_float(row.get('full_universe_fund_pctile')) or 0.0,
        safe_float(row.get('fund_pctile_rule')) or 0.0,
    )
    turnover_rate_for_gap = safe_float(row.get('turnover_rate')) or 0.0
    amplitude_for_gap = safe_float(row.get('amplitude')) or 0.0
    in_limitup_pool_for_gap = bool(
        row.get('sealed_limit_up')
        or row.get('is_limit_up')
        or row.get('is_limitup')
        or row.get('limit_up')
        or row.get('limitup_pool_member')
    )
    consecutive_limit_for_gap = max(
        safe_float(row.get('consecutive_limit_count')) or 0.0,
        safe_float(row.get('consecutive_limit_days')) or 0.0,
        safe_float(row.get('consecutive_limitups')) or 0.0,
    )
    weak_limitup_reason_status = limitup_reason_status_for_gap in ('', 'MISSING', 'PARTIAL', 'PARTIAL_OR_FAIL', 'FAIL', 'FAILED', 'WEAK', 'UNKNOWN', 'NONE')
    has_direct_gap_confirmation = bool(
        limitup_context_present
        or signals.get('limitup_capture_confirmed')
        or continuation_gene_score > 0.0
        or in_limitup_pool_for_gap
        or consecutive_limit_for_gap > 0.0
        or limitup_reason_status_for_gap in ('PASS', 'OK', 'CONFIRMED', 'PROXY')
        or research_panel_overall == 'PASS'
        or max(
            announcement_catalyst_score_for_gap,
            news_catalyst_strength_for_gap,
            limitup_reason_quality_score_for_gap,
        ) >= 0.75
    )
    strong_money_confirmation = bool(
        (amount_for_gap >= 1_000_000_000 and net_inflow_for_gap >= 50_000_000)
        or (amount_pctile_for_gap >= 0.90 and fund_pctile_for_gap >= 0.80 and net_inflow_for_gap >= 30_000_000)
    )
    low_position_sector_news_confirmation = bool(
        low_position_catalyst_score_for_gap >= 0.60
        and max(
            sector_opportunity_score,
            sector_catalyst_score_for_gap,
            sector_news_strength_for_gap,
            news_catalyst_strength_for_gap,
            main_theme_alignment_score,
            main_theme_core_score,
        ) >= 0.45
    )
    early_sector_confirmation_gap = bool(
        sector_opportunity_score <= 0.0
        or (
            sector_opportunity_score < 0.80
            and sector_catalyst_score_for_gap <= 0.0
            and topic_propagation_score_for_gap < 0.30
        )
    )
    early_market_pressure = bool(
        weak_acceptance_market
        or broken_limit_pressure
        or market_regime.lower() == 'weak'
        or (limitup_broken_ratio is not None and limitup_broken_ratio >= 0.70)
    )
    early_hot_momentum_missing_evidence = bool(
        early_market_pressure
        and candidate_stage == 'early_3_to_5'
        and limitup_capture_score_for_gap <= 0.0
        and weak_limitup_reason_status
        and early_sector_confirmation_gap
        and sector_catalyst_score_for_gap <= 0.0
        and topic_propagation_score_for_gap < 0.30
        and not has_direct_gap_confirmation
        and not strong_money_confirmation
        and not low_position_sector_news_confirmation
        and (turnover_rate_for_gap >= 10.0 or amplitude_for_gap >= 7.0)
    )
    weak_market_hot_momentum_evidence_gap = bool(
        setup_type_signal in ('HOT_MOMENTUM', 'L1_HOT_MOMENTUM')
        and (
            (
                (weak_acceptance_market or broken_limit_pressure)
                and candidate_stage in ('mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
                and not limitup_context_present
                and not signals.get('limitup_capture_confirmed')
                and continuation_gene_score <= 0.0
                and str(signals.get('mainboard_auxiliary_evidence_status') or '') in ('', 'PARTIAL', 'MISSING', 'PARTIAL_OR_FAIL')
                and research_panel_overall != 'PASS'
                and not limitup_continuation.get('eligible')
            )
            or early_hot_momentum_missing_evidence
        )
    )
    signals['limitup_context_present'] = limitup_context_present
    signals['weak_market_hot_momentum_evidence_gap'] = weak_market_hot_momentum_evidence_gap
    signals['early_hot_momentum_missing_evidence'] = early_hot_momentum_missing_evidence
    signals['early_hot_momentum_market_pressure'] = early_market_pressure
    signals['early_hot_momentum_sector_confirmation_gap'] = early_sector_confirmation_gap
    signals['hot_momentum_real_confirmation_escape'] = bool(
        has_direct_gap_confirmation
        or strong_money_confirmation
        or low_position_sector_news_confirmation
    )

    signal_pct = profile['signal_pct']
    adaptive_thresholds = market_adaptive_thresholds(candidate_stage, market_context)
    buy_confirmation_min = adaptive_thresholds['buy_confirmation_min']
    order_book_confirmation_min = adaptive_thresholds['order_book_min']
    stock_level_limitup_expectation_pass = False
    strong_high_momentum_continuation_pass = False

    if data_gate_status in ('PASS', 'OK', True):
        positive_conditions.append('data_gate_status=PASS')
    else:
        blockers.append('data_gate_status!=PASS')
        missing_conditions.append('data_gate_status=PASS')

    if candidate_evidence_status == 'PASS':
        positive_conditions.append('candidate_evidence_status=PASS')
    else:
        positive_conditions.append('candidate_evidence_status!=PASS')
        missing_conditions.append('candidate_evidence_status=PASS')

    if time_gate_known:
        if age_minutes is not None and age_minutes >= 0:
            positive_conditions.append('source_time<=asof_time')
        else:
            blockers.append('source_time>asof_time')
            missing_conditions.append('source_time<=asof_time')
    else:
        missing_conditions.append('source_time<=asof_time')

    if one_lot_cost is None or one_lot_cost <= 0:
        blockers.append('one_lot_cost_invalid')
        missing_conditions.append('one_lot_cost_valid')
    else:
        positive_conditions.append('one_lot_cost_valid')
        if price is not None and price <= price_cap:
            positive_conditions.append('price<=70')
        else:
            blockers.append('price>70')
            missing_conditions.append('price<=70')
        if one_lot_cap is not None:
            if one_lot_cost <= one_lot_cap:
                positive_conditions.append('one_lot_cost<=account_cash')
            else:
                blockers.append('one_lot_cost>account_cash')
                missing_conditions.append('one_lot_cost<=account_cash')

    if risk_penalty is None or risk_penalty == 0:
        positive_conditions.append('risk_penalty=0')
    else:
        blockers.append('risk_penalty!=0')
        missing_conditions.append('risk_penalty=0')

    if regulatory_block:
        # Routine blocks (异常波动/风险提示) can be bypassed by strong momentum
        if is_routine_regulatory_block(regulatory_block):
            _close_pos = profile['close_position_score'] or safe_float(row.get('close_position_score'))
            _capture_profile = str(profile.get('limitup_capture_profile') or '')
            _capture_score = safe_float(profile.get('limitup_capture_score')) or 0.0
            has_strong_momentum = (
                (limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min)
                or (seal_order_strength is not None and seal_order_strength >= buy_confirmation_min)
                or (_close_pos is not None and _close_pos >= max(0.70, adaptive_thresholds['dynamic_close_position_min']))
                or (_capture_profile == 'STRONG_LIMITUP_CAPTURE' and _capture_score >= 0.50)
                or (signal_pct is not None and signal_pct >= 5.0 and not weak_acceptance_market)
                or (fund_flow_momentum is not None and fund_flow_momentum >= max(0.3, adaptive_thresholds['dynamic_fund_flow_min'] - 0.1))
            )
            if has_strong_momentum:
                positive_conditions.append('routine_regulatory_bypassed_by_momentum')
            else:
                blockers.append('regulatory_soft_block:' + regulatory_block)
                missing_conditions.append('no_regulatory_hard_block_or_strong_momentum')
        else:
            blockers.append('regulatory_hard_block:' + regulatory_block)
            missing_conditions.append('no_regulatory_hard_block')
    else:
        positive_conditions.append('no_regulatory_hard_block')

    # Limitup reason class + hard-confirmation gate (PROXY alone cannot hard-pass buy confirmation).
    limitup_hard_gate = limitup_reason_supports_hard_confirmation(
        row,
        limitup_reason_strength=limitup_reason_strength,
        seal_order_strength=seal_order_strength,
        order_book_pressure=order_book_pressure,
        buy_confirmation_min=buy_confirmation_min,
        order_book_confirmation_min=order_book_confirmation_min,
        news_catalyst_strength=news_catalyst_strength_for_gap,
        announcement_catalyst_score=announcement_catalyst_score_for_gap,
    )
    limitup_reason_evidence_class = str(limitup_hard_gate.get('limitup_reason_evidence_class') or 'MISSING')
    limitup_reason_hard_confirmation_allowed = bool(limitup_hard_gate.get('limitup_reason_hard_confirmation_allowed'))
    limitup_reason_soft_only = bool(limitup_hard_gate.get('limitup_reason_soft_only'))
    signals['limitup_reason_evidence_class'] = limitup_reason_evidence_class
    signals['limitup_reason_has_direct'] = bool(limitup_hard_gate.get('limitup_reason_has_direct'))
    signals['limitup_reason_has_proxy'] = bool(limitup_hard_gate.get('limitup_reason_has_proxy'))
    signals['limitup_reason_has_gene'] = bool(limitup_hard_gate.get('limitup_reason_has_gene'))
    signals['limitup_reason_hard_confirmation_allowed'] = limitup_reason_hard_confirmation_allowed
    signals['limitup_reason_soft_only'] = limitup_reason_soft_only
    signals['limitup_reason_companion_hits'] = list(limitup_hard_gate.get('limitup_reason_companion_hits') or [])

    near_limit_l2_confirmed = False
    near_limit_gate_blocked = near_limit_up_risk
    if near_limit_up_risk:
        # L2 exemption: near_limit + L2_LIMIT_STRENGTH confirmed = positive momentum, not a blocker.
        # Pure PROXY limitup strength alone must not unlock L2 hard exemption.
        _source_layers = list(row.get('source_layers') or [])
        _l2_in_layers = 'L2_LIMIT_STRENGTH' in _source_layers
        _l2_by_strength = bool(
            limitup_reason_strength is not None
            and limitup_reason_strength >= 0.60
            and limitup_reason_hard_confirmation_allowed
        )
        _near_limit_l2_exemption = str(
            (scoring_config_values or {}).get('near_limit_l2_exemption') or
            SCORING_CONFIG_DEFAULTS.get('near_limit_l2_exemption') or ''
        ).lower() == 'true'
        if _near_limit_l2_exemption and (_l2_in_layers or _l2_by_strength):
            near_limit_l2_confirmed = True
            near_limit_gate_blocked = False
            positive_conditions.append('near_limit_with_L2_confirmation')
        else:
            blockers.append('near_limit_up_risk')
            missing_conditions.append('no_near_limit_up_risk')
    else:
        positive_conditions.append('no_near_limit_up_risk')
    signals['near_limit_with_l2_confirmation'] = near_limit_l2_confirmed

    weekday_blocklist_raw = str(signals['weekday_blocklist'] or '')
    if weekday_blocklist_raw and source_date:
        try:
            blocked_days = [int(d.strip()) for d in weekday_blocklist_raw.split(',') if d.strip()]
            weekday = dt.date.fromisoformat(str(source_date)[:10]).weekday()
            if weekday in blocked_days:
                blockers.append('WEEKDAY_SOFT_BLOCKED')
                missing_conditions.append(f'weekday_not_in_{blocked_days}')
        except Exception:
            blockers.append('WEEKDAY_BLOCKLIST_INVALID_SOFT')
            missing_conditions.append('weekday_blocklist_valid')

    if evidence_missing_flags:
        positive_conditions.append('evidence_coverage_partial')
        missing_conditions.append('evidence_coverage_complete')
        signals['evidence_missing_flags'] = evidence_missing_flags
    if candidate_evidence_flags:
        positive_conditions.append('candidate_evidence_partial')
        missing_conditions.append('candidate_evidence_complete')
        signals['candidate_evidence_flags'] = candidate_evidence_flags
    if (
        not main_force_behavior_mode
        and candidate_lifecycle.get('setup_class') == 'STALE_REPEAT'
        and not (
            (profile.get('limitup_capture_confirmed') or False)
            or (profile.get('seal_order_strength') or 0.0) >= 0.60
            or (profile.get('order_book_pressure') or 0.0) >= 0.50
        )
    ):
        blockers.append('candidate_lifecycle_stale_repeat')
        missing_conditions.append('candidate_lifecycle_non_stale')

    buy_confirmation_hits: List[str] = []
    if limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min:
        if limitup_reason_hard_confirmation_allowed:
            positive_conditions.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
            buy_confirmation_hits.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
        elif limitup_reason_soft_only:
            positive_conditions.append(f'limitup_reason_strength_soft_only:{limitup_reason_evidence_class}')
    if seal_order_strength is not None and seal_order_strength >= buy_confirmation_min:
        positive_conditions.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
        buy_confirmation_hits.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
    if order_book_pressure is not None and order_book_pressure >= order_book_confirmation_min:
        positive_conditions.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
        buy_confirmation_hits.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
    if candidate_lifecycle.get('setup_class') == 'INSTANT_MOMENTUM_SETUP':
        positive_conditions.append('candidate_lifecycle=INSTANT_MOMENTUM_SETUP')

    sector_gate_pass = False
    all_sector_tags = list(sector_opportunity_tags) + list(row.get('sector_opportunity_tags') or [])
    has_real_sector_tag = bool([t for t in all_sector_tags if not str(t).startswith('REPLAY_')])
    if replay_only_sector and not has_real_sector_tag:
        blockers.append('replay_only_sector_opportunity_not_official_gate')
        missing_conditions.append('live_sector_or_limitup_confirmation')
    sector_gate_threshold = sector_gate_threshold_for_market(market_context)
    if sector_opportunity_score >= sector_gate_threshold:
        positive_conditions.append(f'sector_opportunity_score>={sector_gate_threshold}')
        sector_gate_pass = True
    if (not replay_only_sector) and 'SECTOR_OPPORTUNITY' in sector_opportunity_tags:
        positive_conditions.append('vei_phase_d_tags includes SECTOR_OPPORTUNITY')
        sector_gate_pass = True
    if (not replay_only_sector) and 'SECTOR_OPPORTUNITY' in profile['vei_phase_d_tags']:
        positive_conditions.append('vei_phase_d_tags includes SECTOR_OPPORTUNITY')
        sector_gate_pass = True
    if main_theme_core_score >= 0.6:
        positive_conditions.append('main_theme_core_score>=0.6')
        sector_gate_pass = True
    elif main_theme_alignment_score >= 0.5 and fund_flow_momentum is not None and fund_flow_momentum >= 0.5:
        positive_conditions.append('main_theme_alignment_score>=0.5_with_flow')
        sector_gate_pass = True
    component_details = profile['structured_component_details'] if isinstance(profile['structured_component_details'], dict) else {}
    weak_to_strong_reversal = safe_float(component_details.get('weak_to_strong_reversal'))
    first_board_pre_signal = safe_float(component_details.get('first_board_pre_signal'))
    pre_limitup_anomaly = safe_float(component_details.get('pre_limitup_anomaly'))
    independent_reversal_confirmation = bool(
        (
            weak_to_strong_reversal is not None
            and weak_to_strong_reversal >= 0.75
        )
        or (
            first_board_pre_signal is not None
            and first_board_pre_signal >= 0.80
        )
        or (
            pre_limitup_anomaly is not None
            and pre_limitup_anomaly >= 0.70
        )
    )
    limitup_capture_score = profile['limitup_capture_score'] or safe_float(component_details.get('limitup_capture_score')) or 0.0
    limitup_capture_profile = profile['limitup_capture_profile'] or str(component_details.get('limitup_capture_profile') or '')
    limitup_capture_reasons = profile['limitup_capture_reasons'] or normalize_tag_list(component_details.get('limitup_capture_reasons') or [])
    close_position_score = profile['close_position_score'] or 0.0
    limitup_capture_confirmation_pass = False
    strong_vei_stage = candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
    strong_vei_momentum = (
        early_opportunity_score is not None
        and early_opportunity_score >= 0.65
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and time_series_momentum is not None
        and time_series_momentum > 0
    )
    if strong_vei_stage and strong_vei_momentum:
        if weak_to_strong_reversal is not None and weak_to_strong_reversal >= 0.75:
            positive_conditions.append('vei_strong_signal:weak_to_strong_reversal>=0.75')
            sector_gate_pass = True
        if first_board_pre_signal is not None and first_board_pre_signal >= 0.80:
            positive_conditions.append('vei_strong_signal:first_board_pre_signal>=0.80')
            sector_gate_pass = True
        if vei_repo_score_delta is not None and vei_repo_score_delta >= 1.0:
            positive_conditions.append('vei_strong_signal:integrated_vei_repo_delta>=1.0')
            sector_gate_pass = True
    if (
        limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE'
        and limitup_capture_score >= 0.62
        and close_position_score >= 0.70
        and (pre_limitup_anomaly or 0.0) >= 0.55
        and (profile['limitup_reason_propagation_score'] or 0.0) >= 0.60
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and not near_limit_gate_blocked
        and not regulatory_block
    ):
        limitup_capture_confirmation_pass = True
        positive_conditions.append('limitup_capture_confirmation_pass')
        positive_conditions.append(f'limitup_capture_profile={limitup_capture_profile}')
        positive_conditions.append(f'limitup_capture_score={limitup_capture_score:.4f}')
        for reason in limitup_capture_reasons:
            positive_conditions.append('limitup_capture:' + str(reason))

    dynamic_confirmation_hits: List[str] = []
    if (
        limitup_reason_strength is not None
        and limitup_reason_strength >= buy_confirmation_min
        and limitup_reason_hard_confirmation_allowed
    ):
        dynamic_confirmation_hits.append(f'limitup_reason_strength>={buy_confirmation_min:.2f}')
    if seal_order_strength is not None and seal_order_strength >= buy_confirmation_min:
        dynamic_confirmation_hits.append(f'seal_order_strength>={buy_confirmation_min:.2f}')
    if order_book_pressure is not None and order_book_pressure >= order_book_confirmation_min:
        dynamic_confirmation_hits.append(f'order_book_pressure>={order_book_confirmation_min:.2f}')
    if limitup_capture_confirmation_pass:
        dynamic_confirmation_hits.append('limitup_capture_confirmation_pass')
    if (profile['intraday_alert_strength'] or 0.0) >= 0.90:
        dynamic_confirmation_hits.append('intraday_alert_strength>=0.9')
    if near_limit_l2_confirmed:
        dynamic_confirmation_hits.append('near_limit_with_L2_confirmation')
    if main_theme_core_score >= 0.60 or main_theme_alignment_score >= 0.55:
        dynamic_confirmation_hits.append('theme_alignment_confirmation')

    dynamic_required_confirmations = int(adaptive_thresholds['dynamic_required_confirmations'])
    dynamic_signal_confirmation_pass = (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and close_position_score is not None
        and close_position_score >= adaptive_thresholds['dynamic_close_position_min']
        and fund_flow_momentum is not None
        and fund_flow_momentum >= adaptive_thresholds['dynamic_fund_flow_min']
        and time_series_momentum is not None
        and time_series_momentum >= adaptive_thresholds['dynamic_time_series_min']
        and len(dynamic_confirmation_hits) >= dynamic_required_confirmations
        and not regulatory_block
        and not near_limit_gate_blocked
    )
    signals['dynamic_confirmation_hits'] = dynamic_confirmation_hits
    signals['dynamic_required_confirmations'] = dynamic_required_confirmations
    signals['dynamic_signal_confirmation_pass'] = dynamic_signal_confirmation_pass
    if dynamic_signal_confirmation_pass:
        positive_conditions.append('dynamic_signal_confirmation_pass')

    strong_high_momentum_continuation_pass = (
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and (
            bool(signals.get('dynamic_signal_confirmation_pass'))
            or limitup_continuation.get('eligible')
        )
        and (
            not weak_market_hot_momentum_evidence_gap
            or limitup_continuation.get('eligible')
        )
        and (
            (market_supports_high_chase and not weak_acceptance_market)
            or limitup_capture_confirmation_pass
            or limitup_continuation.get('eligible')
        )
    )
    # Profit-first escape: near-limit names with gene / sector proxy / LIMIT_STRENGTH
    # must not be hard-killed only by weak_acceptance high-chase soft
    # (7/27-28 中利/顺钠/华天 were in pool but never decision-eligible).
    # Keep local (no import of runner) to avoid circular deps.
    gene_for_escape = float(safe_float(signals.get('continuation_gene_score')) or 0.0)
    if gene_for_escape <= 0.0:
        gene_for_escape = float(safe_float(row.get('continuation_gene_score')) or 0.0)
    proxy_escape = row.get('sector_yesterday_limitup_gene_proxy')
    proxy_gene = 0.0
    proxy_match_n = 0
    if isinstance(proxy_escape, dict) and str(proxy_escape.get('status') or '').upper() in (
        'PROXY', 'PASS', 'OK', 'CONFIRMED'
    ):
        proxy_gene = float(safe_float(proxy_escape.get('continuation_gene_score')) or 0.0)
        matches = proxy_escape.get('sector_matches') or []
        if isinstance(matches, list):
            proxy_match_n = sum(int(safe_float(m.get('count')) or 0) for m in matches if isinstance(m, dict))
            if proxy_match_n <= 0:
                proxy_match_n = len(matches)
    setup_for_escape = str(signals.get('setup_type') or profile.get('setup_type') or row.get('setup_type') or '')
    limit_strength_setup = setup_for_escape in (
        'LIMIT_STRENGTH', 'L2_LIMIT_STRENGTH', 'STRONG_LIMITUP_CAPTURE'
    )
    profit_structure_high_chase_escape = bool(
        candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and not bool(row.get('sealed_limit_up'))
        and (signal_pct or 0.0) >= 7.0
        and (
            gene_for_escape >= 0.35
            or proxy_gene >= 0.35
            or proxy_match_n >= 2
            or (limit_strength_setup and (sector_opportunity_score or 0.0) >= 0.30)
            or (limit_strength_setup and (main_theme_core_score or 0.0) >= 0.40)
        )
    )
    signals['profit_structure_high_chase_escape'] = profit_structure_high_chase_escape
    signals['profit_escape_gene'] = gene_for_escape
    signals['profit_escape_proxy_gene'] = proxy_gene
    if profit_structure_high_chase_escape and not strong_high_momentum_continuation_pass:
        # Soft promotion path: treat as continuation-capable for sector/high-chase gates.
        strong_high_momentum_continuation_pass = True
        positive_conditions.append('profit_structure_high_chase_escape')
        buy_confirmation_hits.append('profit_structure_high_chase_escape')
    if strong_high_momentum_continuation_pass:
        if 'strong_high_momentum_continuation_pass' not in positive_conditions:
            positive_conditions.append('strong_high_momentum_continuation_pass')
        if 'strong_high_momentum_continuation_pass' not in buy_confirmation_hits:
            buy_confirmation_hits.append('strong_high_momentum_continuation_pass')
    if strong_high_momentum_continuation_pass:
        sector_gate_pass = True
        if 'high_momentum_continuation_bypasses_sector_vei_gate' not in positive_conditions:
            positive_conditions.append('high_momentum_continuation_bypasses_sector_vei_gate')
    elif (
        candidate_stage in ('mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
        and close_position_score >= 0.65
        and ((fund_flow_momentum is not None and fund_flow_momentum >= 0.5) or (profile['intraday_alert_strength'] or 0.0) >= 0.95)
        and ((profile['intraday_alert_strength'] or 0.0) >= 0.9 or (limitup_reason_strength or 0.0) >= 0.6)
        and ((main_theme_alignment_score or 0.0) >= 0.5 or candidate_stage == 'high_7_to_9')
        and bool(buy_confirmation_hits)
    ):
        sector_gate_pass = True
        positive_conditions.append('strong_continuation_bypasses_sector_vei_gate')
    elif (
        candidate_stage in ('flat_0_to_3', 'mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
        and close_position_score >= 0.75
        and fund_flow_momentum is not None and fund_flow_momentum >= 0.6
        and (time_series_momentum is None or time_series_momentum >= 0.15)
        and bool(buy_confirmation_hits)
        and (main_theme_core_score >= 0.4 or main_theme_alignment_score >= 0.3)
    ):
        sector_gate_pass = True
        positive_conditions.append('strong_stock_priority_bypasses_sector_vei_gate')
    if not (sector_gate_pass or limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
        missing_conditions.append(f'sector_opportunity_score>={sector_gate_threshold} or VEI strong signal')

    if research_layer in ('news_catalyst_low_position', 'sector_catalyst_low_position', 'TOPIC_FUND_IGNITION'):
        catalyst_quality_category = str(catalyst_quality.get('category') or profile['catalyst_quality_category'] or '')
        if catalyst_quality_category in ('risk_notice', 'regulatory_notice'):
            blockers.append('research_catalyst_quality:' + catalyst_quality_category)
            missing_conditions.append('research_catalyst_quality_not_risk_notice')
        elif catalyst_quality_category:
            positive_conditions.append('research_catalyst_quality=' + catalyst_quality_category)
        if a_share_risk_review.get('disqualified_for_paper_pick'):
            blockers.append('a_share_risk_review_soft_disqualified')
            missing_conditions.append('a_share_risk_review_clean')
        if research_panel.get('overall') == 'FAIL':
            blockers.append('research_panel_overall_SOFT_FAIL')
            missing_conditions.append('research_panel_overall!=FAIL')
        elif research_panel.get('overall') in ('PASS', 'PARTIAL'):
            positive_conditions.append('research_panel_overall=' + str(research_panel.get('overall')))
        if adversarial_review.get('disqualifying_flags'):
            for flag in adversarial_review.get('disqualifying_flags', []):
                blockers.append('adversarial_review_soft:' + str(flag))
            missing_conditions.append('adversarial_review_clean')

    if fund_flow_momentum is not None and fund_flow_momentum > 0:
        positive_conditions.append('fund_flow_momentum>0')
    if time_series_momentum is not None and time_series_momentum > 0:
        positive_conditions.append('time_series_momentum>0')
    if candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        positive_conditions.append('candidate_stage=' + candidate_stage)
    if early_opportunity_score is not None and early_opportunity_score >= 0.65 and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
        positive_conditions.append('early_opportunity_score>=0.65')
        buy_confirmation_hits.append('early_opportunity_score>=0.65')
    if (
        profile['low_position_catalyst_score'] is not None
        and profile['low_position_catalyst_score'] >= 0.60
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
    ):
        positive_conditions.append('low_position_catalyst_score>=0.6')
        buy_confirmation_hits.append('low_position_catalyst_score>=0.6')

    capital_flow = row.get('data_directory_capital_flow') or {}
    capital_flow_net = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
    if capital_flow_net > 0:
        positive_conditions.append('data_directory_capital_flow>0')
    score_value = candidate_score_value(row) or 0.0
    code_text = str(row.get('code') or row.get('symbol') or '')
    inferred_board = 'chinext' if code_text.startswith(('300', '301')) else ('main' if code_text.startswith(('600', '601', '603', '605', '000', '001', '002', '003')) else '')
    board = str(row.get('board') or inferred_board or '')
    signal_pct_value = signal_pct or 0.0
    stock_level_limitup_expectation_pass = (
        board == 'chinext'
        and candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
        and signal_pct_value >= 12.0
        and close_position_score >= 0.75
        and fund_flow_momentum is not None and fund_flow_momentum >= 0.75
        and time_series_momentum is not None and time_series_momentum >= 0.45
        and (order_book_pressure or 0.0) >= 0.45
        and capital_flow_net >= 50000000
        and sector_gate_pass
        and not regulatory_block
        and candidate_evidence_status == 'PASS'
    )

    if stock_level_limitup_expectation_pass:
        signals['stock_level_limitup_expectation_pass'] = True
        positive_conditions.append('stock_level_limitup_expectation_pass')
        positive_conditions.append('stock_level_confirmation:data_directory_capital_flow>=5000w')
        buy_confirmation_hits.append('stock_level_limitup_expectation_pass')

    if (
        not main_force_behavior_mode
        and weak_market_hot_momentum_evidence_gap
        and not limitup_continuation.get('eligible')
    ):
        blockers.append('weak_market_hot_momentum_without_d1_continuation_evidence')
        missing_conditions.extend([
            'limitup_context_present',
            'limitup_capture_confirmed',
            'continuation_gene_score>0',
            'mainboard_auxiliary_evidence_status=PASS',
            'research_panel_overall=PASS',
        ])

    direct_catalyst_confirmation = max(
        safe_float(signals.get('announcement_catalyst_score')) or 0.0,
        safe_float(signals.get('news_catalyst_strength')) or 0.0,
        safe_float(signals.get('limitup_reason_quality_score')) or 0.0,
    ) >= 0.75
    signals['direct_catalyst_confirmation'] = direct_catalyst_confirmation
    signals['sector_gate_pass'] = sector_gate_pass
    signals['main_theme_core_score'] = main_theme_core_score
    signals['main_theme_alignment_score'] = main_theme_alignment_score

    t1_profit_profile = t1_profit_candidate_profile(row, bundle)
    t1_profit_evidence_pass = bool(t1_profit_profile.get('eligible'))
    signals['t1_profit_evidence_pass'] = t1_profit_evidence_pass
    signals['t1_profit_profile'] = t1_profit_profile
    # Regime-aware quality escape floor (single owner: xiaogu_regime_policy).
    try:
        from xiaogu_regime_policy import quality_escape_score_floor as _quality_escape_floor

        _escape_floor = float(_quality_escape_floor(market_context if isinstance(market_context, dict) else {}))
    except Exception:
        _escape_floor = 65.0
    quality_floor_pass = bool(
        sector_gate_pass
        or (fund_flow_momentum or 0.0) >= 0.50
        or (close_position_score or 0.0) >= 0.70
        or (main_theme_core_score or 0.0) >= 0.45
        or (main_theme_alignment_score or 0.0) >= 0.45
    )
    theme_resilient_escape = bool(
        sector_gate_pass
        and (main_theme_core_score or 0.0) >= 0.60
        and (main_theme_alignment_score or 0.0) >= 0.75
        and (
            direct_catalyst_confirmation
            or limitup_capture_confirmation_pass
            or strong_high_momentum_continuation_pass
            or stock_level_limitup_expectation_pass
            or (continuation_gene_score or 0.0) >= 0.20
            or (fund_flow_momentum or 0.0) >= 0.20
            or ((signals.get('mainline_fund_flow_soft') or {}).get('soft_boost') or 0.0) >= 0.20
        )
    )
    if theme_resilient_escape:
        _escape_floor = min(_escape_floor, 45.0)
    elif sector_gate_pass and (main_theme_core_score or 0.0) >= 0.60 and (main_theme_alignment_score or 0.0) >= 0.70:
        _escape_floor = min(_escape_floor, 50.0)
    quality_floor_pass = bool(
        sector_gate_pass
        or (fund_flow_momentum or 0.0) >= 0.50
        or (close_position_score or 0.0) >= 0.70
        or (main_theme_core_score or 0.0) >= 0.45
        or (main_theme_alignment_score or 0.0) >= 0.45
    )
    signals['quality_escape_score_floor'] = _escape_floor
    signals['theme_resilient_escape'] = theme_resilient_escape
    if isinstance(market_context, dict) and market_context.get('production_regime'):
        signals['production_regime'] = market_context.get('production_regime')
    # Quality escape is earned by the production theme/capital chain only.
    quality_daily_ticket_escape = bool(
        quality_floor_pass
        and theme_resilient_escape
        and not regulatory_block
    )
    theme_substance_ok = bool(
        (main_theme_core_score or 0.0) >= 0.25
        or (main_theme_alignment_score or 0.0) >= 0.30
        or direct_catalyst_confirmation
        or (fund_flow_momentum or 0.0) >= 0.50
        or theme_resilient_escape
    )
    market_stance = str(market_context.get('market_stance') or market_context.get('market_regime') or '').upper()
    quality_escape_hard_waive_ok = bool(
        quality_daily_ticket_escape
        and theme_substance_ok
    )
    signals['quality_daily_ticket_escape'] = quality_daily_ticket_escape
    signals['quality_escape_hard_waive_ok'] = quality_escape_hard_waive_ok
    signals['quality_escape_theme_substance_ok'] = theme_substance_ok

    # Quality-first escape: soft-waive hot-momentum evidence gap (not force-pick).
    # Hard waive only when theme/live-soft substance present.
    if quality_escape_hard_waive_ok and weak_market_hot_momentum_evidence_gap:
        signals['weak_market_hot_momentum_evidence_gap'] = False
        signals['quality_escape_waived_hot_momentum_gap'] = True
        blockers = [
            item for item in blockers
            if item != 'weak_market_hot_momentum_without_d1_continuation_evidence'
        ]
        gap_missing = {
            'limitup_context_present',
            'limitup_capture_confirmed',
            'continuation_gene_score>0',
            'mainboard_auxiliary_evidence_status=PASS',
            'research_panel_overall=PASS',
        }
        missing_conditions = [item for item in missing_conditions if item not in gap_missing]
        positive_conditions.append('quality_escape_waived_hot_momentum_gap')

    weak_market_requires_direct_confirmation = (
        (weak_acceptance_market or broken_limit_pressure)
        and research_panel_overall in ('', 'MISSING', 'PARTIAL')
        and not direct_catalyst_confirmation
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
        and not stock_level_limitup_expectation_pass
        and (safe_float(signals.get('continuation_gene_score')) or 0.0) < 0.70
        and not (quality_escape_hard_waive_ok or theme_resilient_escape)
        and not t1_profit_evidence_pass
        and not limitup_continuation.get('eligible')
    )
    signals['weak_market_requires_direct_confirmation'] = weak_market_requires_direct_confirmation
    strong_sector_theme_partial_aux_exception = strong_sector_theme_partial_aux_exception_allowed(
        row,
        board=board,
        auxiliary_status_normalized=auxiliary_status_normalized,
        research_panel_overall=research_panel_overall,
        sector_gate_pass=sector_gate_pass,
        main_theme_core_score=main_theme_core_score,
        main_theme_alignment_score=main_theme_alignment_score,
        sector_catalyst_score=sector_catalyst_score_for_gap,
        topic_propagation_score=topic_propagation_score_for_gap,
        near_limit_up_risk=near_limit_up_risk,
        regulatory_block=regulatory_block,
        opportunity_block=opportunity_block,
        capital_risk_codes=capital_risk_profile.get('risk_codes'),
        price=price,
        limitup_quality_block=str(signals.get('opportunity_hard_block') or opportunity_block or ''),
        limitup_reason_evidence_class=limitup_reason_evidence_class,
        direct_catalyst_confirmation=direct_catalyst_confirmation,
        news_catalyst_strength=news_catalyst_strength_for_gap,
        announcement_catalyst_score=announcement_catalyst_score_for_gap,
    )
    signals['strong_sector_theme_partial_aux_exception'] = strong_sector_theme_partial_aux_exception
    # Quality escape can waive PARTIAL aux only (not bare MISSING with zero domains).
    # R2 edge (2026-07-26 full_mainboard re-eval): Haixing-style residual leak —
    # weak theme core + PROXY limitup class must not quality-escape
    # past mainboard aux hard. Strong-theme partial path stays via
    # strong_sector_theme_partial_aux_exception_allowed guards.
    quality_escape_partial_aux_edge_block = bool(
        board == 'main'
        and auxiliary_status_normalized == 'PARTIAL'
        and (main_theme_core_score or 0.0) < 0.25
        and (
            str(limitup_reason_evidence_class or '').upper() == 'PROXY'
        )
        and not direct_catalyst_confirmation
    )
    quality_escape_partial_aux_exception = bool(
        quality_escape_hard_waive_ok
        and board == 'main'
        and auxiliary_status_normalized == 'PARTIAL'
        and not quality_escape_partial_aux_edge_block
    )
    signals['quality_escape_partial_aux_edge_block'] = quality_escape_partial_aux_edge_block
    signals['quality_escape_partial_aux_exception'] = quality_escape_partial_aux_exception
    # Strong continuation candidates can be valid despite an empty theme
    # vector. Keep this escape inside the production eligibility chain and
    # require T+1 evidence, capital confirmation, and current buyability.
    strong_hollow_theme_confirmation_escape = bool(
        t1_profit_evidence_pass
        and (safe_float(signals.get('continuation_gene_score')) or 0.0) >= 0.70
        and close_position_score >= 0.72
        and (safe_float(row.get('volume_ratio')) or 0.0) >= 1.0
        and signal_pct is not None
        and signal_pct > 0.0
        and signal_pct < _mainboard_like_limit_seal_threshold(
            str(row.get('symbol') or row.get('code') or '').strip().zfill(6)
        )
        and not buyability_block_reason
        and not near_limit_gate_blocked
        and not regulatory_block
        and risk_penalty in (None, 0)
        and not capital_risk_profile.get('risk_codes')
        and (
            (fund_flow_momentum is not None and fund_flow_momentum >= 0.55)
            or net_inflow_for_gap > 0.0
        )
    )
    signals['strong_hollow_theme_confirmation_escape'] = (
        strong_hollow_theme_confirmation_escape
    )
    if strong_hollow_theme_confirmation_escape:
        positive_conditions.extend([
            'strong_hollow_theme_confirmation_escape',
            'strong_continuation_gene_score>=0.70',
            'continuation_capital_confirmation',
            'continuation_remains_buyable',
        ])
        sector_gate_pass = True
        signals['sector_gate_pass'] = True
        buy_confirmation_hits.append('strong_hollow_theme_confirmation_escape')
        missing_conditions = [
            item
            for item in missing_conditions
            if not (
                item.startswith('sector_opportunity_score>=')
                and 'VEI strong signal' in item
            )
        ]
    strong_official_exception = bool(
        limitup_capture_confirmation_pass
        or strong_high_momentum_continuation_pass
        or stock_level_limitup_expectation_pass
        or strong_sector_theme_partial_aux_exception
        or quality_escape_partial_aux_exception
        or strong_hollow_theme_confirmation_escape
        or limitup_continuation.get('eligible')
    )
    weak_low_confidence_mainboard = bool(
        weak_acceptance_market
        or broken_limit_pressure
        or research_panel_overall in ('', 'MISSING', 'PARTIAL')
        or not direct_catalyst_confirmation
        or candidate_stage in ('early_3_to_5', 'mid_5_to_7')
    )
    mainboard_auxiliary_hard_block = bool(
        board == 'main'
        and auxiliary_status_normalized not in ('PASS', 'OK')
        and weak_low_confidence_mainboard
        and not strong_official_exception
        and not limitup_continuation.get('eligible')
    )
    signals['mainboard_auxiliary_evidence_hard_block'] = mainboard_auxiliary_hard_block
    if strong_sector_theme_partial_aux_exception:
        positive_conditions.append('strong_sector_theme_partial_aux_exception')
    if quality_escape_partial_aux_exception:
        positive_conditions.append('quality_escape_partial_aux_exception')
    if mainboard_auxiliary_hard_block and not main_force_behavior_mode:
        blockers.append('mainboard_auxiliary_evidence_status_not_PASS')
        missing_conditions.append('mainboard_auxiliary_evidence_status=PASS')
    low_score_without_direct_catalyst_confirmation = False
    signals['low_score_without_direct_catalyst_confirmation'] = False
    if weak_market_requires_direct_confirmation and not main_force_behavior_mode:
        blockers.append('weak_market_requires_direct_confirmation')
        missing_conditions.append(
            'direct_catalyst_limitup_research_PASS_or_sector_confirmation'
        )

    # Post-limitup weak continuation (7/23 山金: D-1 +10%, D0 +1.29%, T1 -4.4%).
    # Observation: yesterday sealed names with weak same-day follow-through should not
    # become PAPER_PICK via soft/quality escape alone.
    yesterday_gene = (
        row.get('yesterday_limitup_gene_evidence')
        if isinstance(row.get('yesterday_limitup_gene_evidence'), dict)
        else {}
    )
    was_yesterday_limitup = bool(
        row.get('previous_limitup')
        or row.get('was_yesterday_limitup')
        or yesterday_gene.get('candidate_was_yesterday_limitup')
        or str(yesterday_gene.get('status') or '').upper() in ('PASS', 'PROXY', 'TRUE', '1')
        or (safe_float(row.get('prev_day_pct_chg') if row.get('prev_day_pct_chg') is not None else row.get('yesterday_pct_chg')) or 0.0) >= 9.5
    )
    post_limitup_weak_continuation = bool(
        was_yesterday_limitup
        and (signal_pct or 0.0) < 3.0
        and (fund_flow_momentum or 0.0) < 0.45
        and (main_theme_core_score or 0.0) < 0.40
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
        and not direct_catalyst_confirmation
    )
    signals['was_yesterday_limitup'] = was_yesterday_limitup
    signals['post_limitup_weak_continuation'] = post_limitup_weak_continuation
    if post_limitup_weak_continuation:
        blockers.append('post_limitup_weak_continuation')
        missing_conditions.append(
            'post_limitup_requires_strong_same_day_follow_through_or_theme'
        )
        positive_conditions = [
            item for item in positive_conditions
            if item not in (
                'quality_escape_partial_aux_exception',
                'quality_escape_waived_hot_momentum_gap',
            )
        ]

    # Hollow theme + fund shell (7/13 莲花: core/align=0, strong fund, no stock catalyst).
    # Bounded hard from 2026-07-26 counterfactual: requires fund_shell (ffm>=0.55)
    # so weak-fund hollow names are not mass-blocked. Does not replace soft ranking
    # hollow penalties; only blocks empty-theme fund shells from PAPER_PICK.
    sector_news_catalyst_for_hollow = (
        safe_float(signals.get('sector_news_catalyst_score'))
        if signals.get('sector_news_catalyst_score') is not None
        else safe_float(row.get('sector_news_catalyst_score'))
    ) or 0.0
    hollow_theme_empty = bool(
        (main_theme_core_score or 0.0) < 0.15
        and (main_theme_alignment_score or 0.0) < 0.15
        and (news_catalyst_strength_for_gap or 0.0) < 0.15
        and (announcement_catalyst_score_for_gap or 0.0) < 0.15
        and (sector_catalyst_score_for_gap or 0.0) < 0.35
        and sector_news_catalyst_for_hollow < 0.45
    )
    fund_shell_strong = bool(
        fund_flow_momentum is not None and (fund_flow_momentum or 0.0) >= 0.55
    )
    hollow_theme_fund_shell = bool(
        hollow_theme_empty
        and fund_shell_strong
        and not direct_catalyst_confirmation
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
        and not independent_reversal_confirmation
        and not strong_hollow_theme_confirmation_escape
        # Chinext stock-level limitup expectation is an independent hard path
        # (capital-flow confirmation); do not let hollow-theme fund shell cancel it.
        and not stock_level_limitup_expectation_pass
    )
    signals['hollow_theme_empty'] = hollow_theme_empty
    signals['fund_shell_strong'] = fund_shell_strong
    signals['hollow_theme_fund_shell'] = hollow_theme_fund_shell
    if hollow_theme_fund_shell:
        blockers.append('hollow_theme_fund_shell')
        missing_conditions.append(
            'theme_core_or_stock_catalyst_required_when_fund_shell_strong'
        )
        positive_conditions = [
            item for item in positive_conditions
            if item not in (
                'quality_escape_partial_aux_exception',
                'quality_escape_waived_hot_momentum_gap',
            )
        ]

    # High-core chase with weak fund + no stock catalyst (7/20 平煤: core=0.95,
    # pct=6.83, ffm=0.376, T1=-4.14%). Bounded hard from 2026-07-26 counterfactual
    # on paper-like + full_mainboard_t1: hit Pingmei only, 0 winners. Requires
    # high core/align + chase pct + weak fund + empty stock catalyst + weak cont.
    cont_gene_for_chase = safe_float(signals.get('continuation_gene_score')) or 0.0
    high_core_chase_weak_fund = bool(
        (
            (main_theme_core_score or 0.0) >= 0.70
            or (main_theme_alignment_score or 0.0) >= 0.70
        )
        and (signal_pct or 0.0) >= 6.0
        and fund_flow_momentum is not None
        and (fund_flow_momentum or 0.0) < 0.45
        and (news_catalyst_strength_for_gap or 0.0) < 0.15
        and (announcement_catalyst_score_for_gap or 0.0) < 0.15
        and cont_gene_for_chase < 0.35
        and not direct_catalyst_confirmation
        and not limitup_capture_confirmation_pass
        and not strong_high_momentum_continuation_pass
    )
    signals['high_core_chase_weak_fund'] = high_core_chase_weak_fund
    if high_core_chase_weak_fund:
        blockers.append('high_core_chase_weak_fund')
        missing_conditions.append(
            'high_core_chase_requires_fund_flow_or_stock_catalyst_or_continuation'
        )
        positive_conditions = [
            item for item in positive_conditions
            if item not in (
                'quality_escape_partial_aux_exception',
                'quality_escape_waived_hot_momentum_gap',
            )
        ]

    is_high_position = candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
    if is_high_position and weak_acceptance_market and not (
        strong_high_momentum_continuation_pass
        or stock_level_limitup_expectation_pass
        or quality_escape_partial_aux_exception
        or profit_structure_high_chase_escape
        or limitup_continuation.get('eligible')
    ):
        blockers.append('broken_limit_weak_feedback_high_chase_soft' if broken_limit_pressure else 'weak_acceptance_market_high_chase_soft')
        missing_conditions.append('weak_market_requires_stronger_high_chase_confirmation')

    raw_research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    raw_research_panel = raw_research_signals.get('research_panel') if isinstance(raw_research_signals.get('research_panel'), dict) else {}
    raw_research_panel_missing = bool(raw_research_signals and not raw_research_panel.get('overall'))
    weak_underwater_without_confirmation = (
        candidate_stage == 'underwater'
        and signal_pct is not None
        and signal_pct <= 0
        and (research_panel.get('overall') == 'FAIL' or raw_research_panel_missing)
        and catalyst_quality_category_global in ('', 'neutral')
        and not limitup_capture_confirmation_pass
        and not stock_level_limitup_expectation_pass
    )
    if weak_underwater_without_confirmation:
        blockers.append('weak_underwater_without_forward_confirmation')
        missing_conditions.append('limitup_or_catalyst_confirmation_for_underwater_candidate')

    if buy_confirmation_hits:
        positive_conditions.append('buy_confirmation>=0.6')
    else:
        positive_conditions.append('buy_confirmation_below_threshold')
        missing_conditions.append('buy_confirmation>=0.6')
        if opportunity_block == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION' and not limitup_continuation.get('eligible'):
            blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')

    underwater_reversal_confirmation_pass = False
    if (
        data_gate_status in ('PASS', 'OK', True)
        and candidate_evidence_status == 'PASS'
        and time_gate_known
        and age_minutes is not None
        and age_minutes >= 0
        and one_lot_cost is not None
        and one_lot_cost > 0
        and price is not None
        and price <= price_cap
        and (one_lot_cap is None or one_lot_cost <= one_lot_cap)
        and (risk_penalty in (None, 0))
        and not regulatory_block
        and not near_limit_up_risk
        and not evidence_missing_flags
        and not candidate_evidence_flags
        and not opportunity_block
        and not blockers
        and candidate_stage in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')
        and (profile['search_layer_hint'] == 'underwater_reversal' or profile['setup_type'] == 'UNDERWATER_TO_RED_STRENGTH')
        and early_opportunity_score is not None
        and early_opportunity_score >= 0.65
        and fund_flow_momentum is not None
        and fund_flow_momentum > 0
        and time_series_momentum is not None
        and time_series_momentum > 0
        and bool(buy_confirmation_hits)
    ):
        underwater_reversal_confirmation_pass = True
        positive_conditions.append('underwater_reversal_confirmation_pass')
        missing_conditions = [item for item in missing_conditions if not (item.startswith('sector_opportunity_score>=') and 'VEI strong signal' in item)]

    if opportunity_block and opportunity_block != 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION':
        blockers.append(opportunity_block)

    if quality_escape_partial_aux_exception and 'quality_escape_partial_aux_exception' not in positive_conditions:
        positive_conditions.append('quality_escape_partial_aux_exception')

    # Keep blockers deduplicated and preserve first-seen order.
    unique_blockers: List[str] = []
    seen_blockers = set()
    for blocker in blockers:
        if blocker not in seen_blockers:
            unique_blockers.append(blocker)
            seen_blockers.add(blocker)

    unique_positive: List[str] = []
    seen_positive = set()
    for condition in positive_conditions:
        if condition not in seen_positive:
            unique_positive.append(condition)
            seen_positive.add(condition)

    unique_missing: List[str] = []
    seen_missing = set()
    for condition in missing_conditions:
        if condition not in seen_missing:
            unique_missing.append(condition)
            seen_missing.add(condition)

    eligible = not unique_blockers and data_gate_status in ('PASS', 'OK', True) and (sector_gate_pass or underwater_reversal_confirmation_pass or limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass)
    if one_lot_cost is not None and one_lot_cost > 0 and one_lot_cap is not None and one_lot_cost > one_lot_cap:
        eligible = False
    if price is None or price > price_cap:
        eligible = False
    if risk_penalty not in (None, 0):
        eligible = False
    if regulatory_block:
        # Routine blocks can be bypassed by strong momentum
        if is_routine_regulatory_block(regulatory_block):
            has_strong_momentum = (
                (limitup_reason_strength is not None and limitup_reason_strength >= buy_confirmation_min)
                or (seal_order_strength is not None and seal_order_strength >= buy_confirmation_min)
                or (close_position_score is not None and close_position_score >= max(0.82, adaptive_thresholds['dynamic_close_position_min']))
                or (limitup_capture_profile == 'STRONG_LIMITUP_CAPTURE' and limitup_capture_score >= 0.62)
            )
            if not has_strong_momentum:
                eligible = False
        else:
            eligible = False
    if near_limit_gate_blocked:
        eligible = False
    if opportunity_block == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION':
        if stock_level_limitup_expectation_pass:
            positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_STOCK_LEVEL_LIMITUP_EXPECTATION')
        elif limitup_continuation.get('eligible'):
            positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_LIMITUP_CONTINUATION')
        elif dynamic_signal_confirmation_pass and market_supports_high_chase and not weak_acceptance_market:
            positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_DYNAMIC_MARKET_CONFIRMATION')
        else:
            sector_opp = safe_float(row.get('sector_opportunity_score')) or 0.0
            fund_mom = safe_float(fund_flow_momentum) or 0.0
            if sector_opp >= 1.5 or fund_mom >= 0.8:
                positive_conditions.append('CHASE_HIGH_OVERRIDDEN_BY_SECTOR_OR_FUND_FLOW')
            else:
                blockers.append('CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION')
    if time_gate_known and age_minutes is not None and age_minutes < 0:
        eligible = False

    # Minimum factor requirements - prevent blind selection
    setup_type = str(profile.get('setup_type') or '')
    has_valid_setup = bool(setup_type and setup_type not in ('', 'NONE', 'UNKNOWN'))
    has_close_position = close_position_score is not None and close_position_score > 0
    has_signal_pct = signal_pct is not None and signal_pct > 0
    
    # Require at least setup_type OR close_position_score for eligibility
    if not has_valid_setup and not has_close_position:
        blockers.append('missing_critical_factors')
        missing_conditions.append('setup_type_or_close_position_required')
        eligible = False

    capital_flow = row.get('data_directory_capital_flow') or {}
    capital_flow_net = safe_float(capital_flow.get('main_force_net_inflow')) or 0.0
    if capital_flow_net < -50000000:
        blockers.append('main_force_heavy_sell_soft')
        missing_conditions.append('main_force_not_heavy_sell')
        eligible = False

    market_snapshot = bundle.get('market_snapshot') or {}
    market_limitups = safe_float(market_snapshot.get('market_limitups')) or safe_float(bundle.get('market_limitups')) or 0.0
    market_breadth_up_pct = safe_float(market_snapshot.get('market_breadth_up_pct')) or safe_float(bundle.get('market_breadth_up_pct')) or 0.0
    is_high_position = candidate_stage in ('high_7_to_9', 'near_limit_9_plus')
    if overheated_market:
        if is_high_position and not (
            limitup_capture_confirmation_pass
            or strong_high_momentum_continuation_pass
            or profit_structure_high_chase_escape
        ):
            blockers.append('overheated_market_no_strong_confirmation_soft')
            missing_conditions.append('overheated_market_requires_strong_confirmation')
            eligible = False

    research_signals_local = profile.get('research_signals') if isinstance(profile.get('research_signals'), dict) else {}
    catalyst_quality_local = research_signals_local.get('catalyst_quality') if isinstance(research_signals_local.get('catalyst_quality'), dict) else {}
    news_category = str(catalyst_quality_local.get('category') or '')
    if news_category in ('risk_notice', 'regulatory_notice'):
        if not (limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
            blockers.append('news_catalyst_risk_notice_soft')
            missing_conditions.append('news_catalyst_not_risk_notice')
            eligible = False

    lhb_rows = row.get('lhb_profiles') or []
    hot_money_buy = False
    for lhb in lhb_rows:
        seat_name = str(lhb.get('seat_name') or '')
        net_buy = safe_float(lhb.get('net_buy')) or 0.0
        if net_buy > 0 and ('游资' in seat_name or '营业部' in seat_name):
            hot_money_buy = True
            break
    if hot_money_buy and capital_flow_net < 0:
        if not (limitup_capture_confirmation_pass or strong_high_momentum_continuation_pass):
            blockers.append('hot_money_buy_with_main_sell_soft')
            missing_conditions.append('no_hot_money_with_main_sell')
            eligible = False
    if underwater_reversal_confirmation_pass:
        signals['underwater_reversal_confirmation_pass'] = True
    if limitup_capture_confirmation_pass:
        signals['limitup_capture_confirmation_pass'] = True
    if strong_high_momentum_continuation_pass:
        signals['strong_high_momentum_continuation_pass'] = True

    for blocker in blockers:
        if blocker not in seen_blockers:
            unique_blockers.append(blocker)
            seen_blockers.add(blocker)
    for condition in positive_conditions:
        if condition not in seen_positive:
            unique_positive.append(condition)
            seen_positive.add(condition)
    for condition in missing_conditions:
        if condition not in seen_missing:
            unique_missing.append(condition)
            seen_missing.add(condition)

    return {
        'eligible': eligible,
        'blockers': unique_blockers,
        'positive_conditions': unique_positive,
        'missing_conditions': unique_missing,
        'signals': signals,
        'selection_reasons_zh': list(limitup_continuation.get('reasons_zh') or []),
        'shadow_risk_profile': shadow_profile,
        'candidate_lifecycle': candidate_lifecycle,
        'setup_class': candidate_lifecycle.get('setup_class'),
        'setup_rank': candidate_lifecycle.get('setup_rank'),
        'stale_decay': candidate_lifecycle.get('stale_decay'),
        'repeat_count': candidate_lifecycle.get('repeat_count'),
        'lifecycle_score': candidate_lifecycle.get('lifecycle_score'),
    }



def official_target_exclusion_reasons(row: Dict[str, Any], bundle: Dict[str, Any] | None = None) -> List[str]:
    bundle = bundle if isinstance(bundle, dict) else {}
    row = row if isinstance(row, dict) else {}
    main_force_behavior_mode = str(bundle.get('ranking_view') or '').strip().lower() == 'main_force_behavior_chain'
    reasons: List[str] = []
    regulatory_block = regulatory_hard_block_reason(row, bundle)
    if regulatory_block:
        reasons.append('regulatory_hard_block:' + regulatory_block)

    eligibility = row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    risk_explanation_gate = eligibility.get('paper_pick_risk_explanation_gate') if isinstance(eligibility.get('paper_pick_risk_explanation_gate'), dict) else paper_pick_risk_explanation_gate(row)
    if risk_explanation_gate.get('status') == 'FAIL':
        reasons.append('PAPER_PICK_RISK_EXPLANATION_GATE_FAIL')
    signals = eligibility.get('signals') if isinstance(eligibility.get('signals'), dict) else {}
    limitup_continuation = signals.get('limitup_continuation_exception') if isinstance(signals.get('limitup_continuation_exception'), dict) else broken_limitup_continuation_exception(row, bundle)
    profile = row
    if row.get('sector_opportunity_score') is None:
        profile = _cached_structured_signal_profile(row, bundle)
    market_context = market_adaptive_context(row, bundle)
    sector_gate_threshold = sector_gate_threshold_for_market(market_context)
    strong_sector = (profile.get('sector_opportunity_score') or 0.0) >= sector_gate_threshold and not replay_only_sector_opportunity(profile, row)
    strong_momentum_override = bool(
        signals.get('strong_high_momentum_continuation_pass')
        or signals.get('dynamic_signal_confirmation_pass')
        or signals.get('limitup_capture_confirmation_pass')
        or signals.get('stock_level_limitup_expectation_pass')
    )
    soft_exclusion_override = bool(eligibility.get('eligible')) and (strong_sector or strong_momentum_override)
    hard_diagnostic_blockers = {
        'sector_follower_diagnostic_only',
    }
    if not main_force_behavior_mode:
        hard_diagnostic_blockers.update({
            'mainboard_auxiliary_evidence_status_not_PASS',
        })
    partial_aux_exception = bool(signals.get('strong_sector_theme_partial_aux_exception'))
    for blocker in [str(item) for item in (eligibility.get('blockers') or []) if item]:
        if blocker == 'mainboard_auxiliary_evidence_status_not_PASS' and partial_aux_exception:
            continue
        if blocker == 'mainboard_auxiliary_evidence_status_not_PASS' and limitup_continuation.get('eligible'):
            continue
        if blocker in hard_diagnostic_blockers:
            reasons.append(blocker)
        elif any(token in blocker for token in ('regulatory_hard_block', 'risk_too_high')):
            reasons.append(blocker)
        elif 'a_share_risk_review_soft_disqualified' in blocker and not soft_exclusion_override:
            reasons.append(blocker)
        elif 'research_panel_overall_SOFT_FAIL' in blocker and not soft_exclusion_override:
            reasons.append(blocker)
        elif blocker.startswith('adversarial_review_soft:') and not soft_exclusion_override:
            reasons.append(blocker)

    research_signals = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    catalyst_quality = research_signals.get('catalyst_quality') if isinstance(research_signals.get('catalyst_quality'), dict) else {}
    catalyst_category = str(catalyst_quality.get('category') or '')
    if catalyst_category in ('risk_notice', 'regulatory_notice') and not (soft_exclusion_override and catalyst_category == 'risk_notice'):
        reasons.append('research_catalyst_quality:' + catalyst_category)
    a_share_risk_review = research_signals.get('a_share_risk_review') if isinstance(research_signals.get('a_share_risk_review'), dict) else {}
    if a_share_risk_review.get('disqualified_for_paper_pick') and not soft_exclusion_override:
        reasons.append('a_share_risk_review_soft_disqualified')
    research_panel = research_signals.get('research_panel') if isinstance(research_signals.get('research_panel'), dict) else {}
    if research_panel.get('overall') == 'FAIL' and not soft_exclusion_override:
        reasons.append('research_panel_overall_SOFT_FAIL')
    adversarial_review = research_signals.get('adversarial_review') if isinstance(research_signals.get('adversarial_review'), dict) else {}
    if not soft_exclusion_override:
        for flag in [str(item) for item in (adversarial_review.get('disqualifying_flags') or []) if item]:
            reasons.append('adversarial_review_soft:' + flag)

    enhanced_missing = [str(item) for item in (row.get('enhanced_evidence_missing_domains') or []) if item]
    if 'candidate_fund_recheck' in enhanced_missing and not soft_exclusion_override:
        reasons.append('candidate_fund_recheck_missing')
    if any('fund_flow_conflict' in str(item) or 'weak_fund' in str(item) for item in (row.get('blocked_reasons') or [])) and not soft_exclusion_override:
        reasons.append('funding_quality_conflict')
    if any('WEEKDAY_BLOCKED' in str(item) for item in (row.get('blocked_reasons') or [])) and not soft_exclusion_override:
        reasons.append('weekday_blocked_soft')
    if (safe_float(row.get('risk_notice_penalty')) or 0.0) >= 0.60:
        for notice in row.get('risk_notice_evidence') or []:
            category = str(notice.get('category') or 'risk_notice')
            title = str(notice.get('title') or '')[:120]
            reasons.append(f'mainboard_auxiliary_risk:{category}:{title}')

    return unique_text_values(reasons)



def structure_block_machine_codes(row: Dict[str, Any], eligibility: Dict[str, Any] | None = None) -> List[str]:
    """Machine-readable why_not_official codes for structure candidates (M4 explainability)."""
    codes: List[str] = []
    eligibility = eligibility if isinstance(eligibility, dict) else (
        row.get('paper_pick_eligibility') if isinstance(row.get('paper_pick_eligibility'), dict) else {}
    )
    signals = eligibility.get('signals') if isinstance(eligibility.get('signals'), dict) else {}
    blockers = [str(item) for item in (eligibility.get('blockers') or []) if item]
    cont = safe_float(row.get('continuation_gene_score')) or 0.0
    snc = safe_float(row.get('sector_news_catalyst_score')) or 0.0
    if cont >= 0.5 or snc >= 0.5:
        codes.append('STRUCTURE_CANDIDATE_PRESENT')
    if any('mainboard_auxiliary_evidence_status_not_PASS' in item for item in blockers):
        codes.append('AUXILIARY_NOT_PASS')
    if row.get('official_target_excluded') or row.get('official_target_exclusion_reasons'):
        codes.append('OFFICIAL_TARGET_EXCLUDED')
    if bool(eligibility.get('eligible')) is False and not codes:
        codes.append('ELIGIBILITY_BLOCKED')
    if bool(eligibility.get('eligible')) is True and not row.get('official_target_excluded'):
        if cont >= 0.5 or snc >= 0.5:
            codes.append('STRUCTURE_ELIGIBLE_COMPETING')
    return unique_text_values(codes)



def attach_paper_pick_eligibility(bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(bundle, dict):
        return bundle

    primary_items = bundle.get('paper_scoring_candidates')
    if isinstance(primary_items, list):
        filtered_primary, filter_summary = filter_current_day_tradable_candidates(primary_items, bundle)
        bundle['paper_scoring_candidates'] = filtered_primary
        bundle['current_day_tradable_filter'] = filter_summary
        existing_drops = [
            item for item in (bundle.get('candidate_drop_diagnostics') or [])
            if isinstance(item, dict)
        ]
        bundle['candidate_drop_diagnostics'] = existing_drops + list(filter_summary.get('dropped') or [])
    elif isinstance(bundle.get('candidate'), dict):
        filtered_candidate, filter_summary = filter_current_day_tradable_candidates([bundle['candidate']], bundle)
        if not filtered_candidate:
            bundle['candidate'] = {}
            bundle['current_day_tradable_filter'] = filter_summary

    # Pool-level hollow theme tags (soft ranking pollution diagnostic).
    pool_rows: List[Dict[str, Any]] = []
    for key in ('paper_scoring_candidates', 'full_candidate_pool', 'passed_candidates'):
        items = bundle.get(key)
        if isinstance(items, list) and items:
            pool_rows = [item for item in items if isinstance(item, dict)]
            if pool_rows:
                break
    hollow_meta = detect_pool_hollow_theme_tags(pool_rows)
    hollow = bool(hollow_meta.get('hollow'))
    bundle['theme_tags_hollow'] = hollow
    bundle['theme_tags_hollow_meta'] = hollow_meta

    def enrich(candidate: Any) -> Any:
        if not isinstance(candidate, dict):
            return candidate
        updated = dict(candidate)
        updated['theme_tags_hollow'] = hollow
        updated['theme_tags_hollow_meta'] = hollow_meta
        capital_risk_profile = candidate_capital_risk_profile(updated)
        updated['capital_risk_profile'] = capital_risk_profile
        for key in (
            'failed_limitup_risk', 'main_buy_outflow_pressure', 'dark_pool_inflow_support',
            'popularity_crowding_risk', 'profit_taking_pressure', 'post_broken_board_selloff_risk',
            'high_popularity_trap_risk', 'capital_divergence_score', 'risk_softened_by_dark_pool_inflow',
        ):
            updated[key] = capital_risk_profile.get(key)
        eligibility = _cached_paper_pick_eligibility_profile(updated, bundle)
        eligibility_signals = eligibility.setdefault('signals', {})
        eligibility_signals['capital_risk_profile'] = capital_risk_profile
        risk_gate = paper_pick_risk_explanation_gate(updated)
        eligibility['paper_pick_risk_explanation_gate'] = risk_gate
        eligibility_signals['paper_pick_risk_explanation_gate'] = risk_gate
        if risk_gate['status'] == 'FAIL':
            eligibility['eligible'] = False
            eligibility['blockers'] = unique_text_values([
                *(eligibility.get('blockers') or []),
                'PAPER_PICK_RISK_EXPLANATION_GATE_FAIL',
            ])
            eligibility['missing_conditions'] = unique_text_values([
                *(eligibility.get('missing_conditions') or []),
                'explicit_catalyst_or_risk_rebuttal_required',
            ])
        if capital_risk_profile.get('risk_codes'):
            eligibility['missing_conditions'] = unique_text_values([
                *(eligibility.get('missing_conditions') or []),
                *capital_risk_profile['risk_codes'],
            ])
        if capital_risk_profile.get('risk_softened_by_dark_pool_inflow'):
            eligibility['positive_conditions'] = unique_text_values([
                *(eligibility.get('positive_conditions') or []),
                'risk_softened_by_dark_pool_inflow',
            ])
        updated['paper_pick_eligibility'] = eligibility
        updated['selection_reasons_zh'] = list(eligibility.get('selection_reasons_zh') or [])
        exclusion_reasons = official_target_exclusion_reasons(updated, bundle)
        updated['official_target_excluded'] = bool(exclusion_reasons)
        updated['official_target_exclusion_reasons'] = exclusion_reasons
        if exclusion_reasons:
            updated['diagnostic_only'] = True
        updated['structured_formal_paper_pick_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
        updated['formal_eligible'] = bool(eligibility['eligible']) and not exclusion_reasons
        # M4 explainability: machine codes for structure / gate survivors.
        updated['why_not_official_pick_codes'] = structure_block_machine_codes(updated, eligibility)
        return updated

    primary_enriched_by_key: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for key in ('paper_scoring_candidates', 'full_candidate_pool'):
        items = bundle.get(key)
        if not isinstance(items, list):
            continue
        enriched_items = [enrich(item) for item in items]
        bundle[key] = enriched_items
        if key == 'paper_scoring_candidates':
            primary_enriched_by_key = {
                _candidate_runtime_cache_key(item): item
                for item in enriched_items
                if isinstance(item, dict)
            }

    for key in ('candidate', 'candidate_features'):
        if key not in bundle:
            continue
        item = bundle.get(key)
        if isinstance(item, dict):
            cached_item = primary_enriched_by_key.get(_candidate_runtime_cache_key(item))
            bundle[key] = dict(cached_item) if isinstance(cached_item, dict) else enrich(item)
        else:
            bundle[key] = enrich(item)
    for key in ('structured_observation_basket', 'structured_sector_observation_basket'):
        items = bundle.get(key)
        if isinstance(items, list):
            bundle[key] = [enrich(item) for item in items]
    impact = bundle.get('structured_formal_impact')
    if isinstance(impact, dict):
        for key in ('top_structured_only_candidates', 'sector_opportunity_candidates', 'structured_observation_candidates'):
            items = impact.get(key)
            if isinstance(items, list):
                impact[key] = [enrich(item) for item in items]
    return bundle



# Live host re-bind wrappers (public API)
paper_pick_eligibility_profile = _with_host(paper_pick_eligibility_profile)
official_target_exclusion_reasons = _with_host(official_target_exclusion_reasons)
structure_block_machine_codes = _with_host(structure_block_machine_codes)
attach_paper_pick_eligibility = _with_host(attach_paper_pick_eligibility)
filter_t1_profit_candidates = _with_host(filter_t1_profit_candidates)
