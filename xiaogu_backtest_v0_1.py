#!/usr/bin/env python3
"""
Backtest engine — replay runner over historical scan data.

Usage:
    python3 xiaogu_backtest_v0_1.py --start 2026-06-06 --end 2026-06-25
    python3 xiaogu_backtest_v0_1.py --date 2026-06-25
    python3 xiaogu_backtest_v0_1.py --all --report
"""
import argparse
import json
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent
LIVE_SCAN_ROOT = BASE / 'data' / 'live_scan'
BUNDLE_ROOT = BASE / 'data' / 'forward_candidate_bundles'
BACKTEST_OUTPUT_ROOT = BASE / 'data' / 'backtest'
LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
PYTHON = sys.executable
RETURN_COVERAGE_THRESHOLDS = {
    'top10_t1_coverage': 0.70,
    'mainboard_top10_t1_coverage': 0.70,
    'paper_pick_t1_coverage': 0.90,
    'mainboard_rank2_to_rank6_t1_coverage': 0.70,
}
MINIMUM_READY_DAYS_FOR_FREEZE = 5
MINIMUM_PERFORMANCE_SAMPLE_COUNT = 10
PERFORMANCE_GATE_THRESHOLDS = {
    'avg_t1_return': 0.0,
    'win_rate': 0.50,
    'limitup_rate': 0.10,
    'large_loss_rate': 0.20,
    'max_drawdown': -0.10,
}

try:
    from xiaogu_db import (
        fetch_available_trade_dates, fetch_daily_candidates, fetch_picks, fetch_returns,
        fetch_latest_scan_session, fetch_signals, classify_candidate_cohort, is_mainboard_symbol,
        LIMITUP_GENE_SHADOW_SIGNALS, limitup_gene_signal_values,
    )
except Exception:  # pragma: no cover - DB may be unavailable in some environments
    fetch_available_trade_dates = None  # type: ignore[assignment]
    fetch_daily_candidates = None  # type: ignore[assignment]
    fetch_picks = None  # type: ignore[assignment]
    fetch_returns = None  # type: ignore[assignment]
    fetch_latest_scan_session = None  # type: ignore[assignment]
    fetch_signals = None  # type: ignore[assignment]
    classify_candidate_cohort = None  # type: ignore[assignment]
    is_mainboard_symbol = None  # type: ignore[assignment]
    LIMITUP_GENE_SHADOW_SIGNALS = ()  # type: ignore[assignment]
    limitup_gene_signal_values = None  # type: ignore[assignment]


def _nonempty(value: Any) -> bool:
    return value not in (None, '', {}, [])


def _date_key(value: Any) -> str:
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


def _is_backtest_trading_day(value: Any) -> bool:
    """Keep DB cohort replay scoped to actual A-share sessions."""
    try:
        trade_date = value if isinstance(value, date) else date.fromisoformat(_date_key(value))
    except (TypeError, ValueError):
        return False
    try:
        from xiaogu_scheduler import is_trading_day
        return bool(is_trading_day(trade_date))
    except Exception:
        return trade_date.weekday() < 5


def _filter_trading_day_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if _is_backtest_trading_day(row.get('trade_date'))]


def _return_limitup_summary(values: List[float]) -> Dict[str, Any]:
    """Use one explicit T+1 return/elasticity definition across all cohorts."""
    if not values:
        return {
            'sample_count': 0, 'avg_return': None, 'median_return': None,
            'win_rate': None, 'limitup_rate': None, 'near_limitup_rate': None,
            'loss_rate': None, 'large_loss_rate': None,
        }
    count = len(values)
    return {
        'sample_count': count,
        'avg_return': round(sum(values) / count, 6),
        'median_return': round(statistics.median(values), 6),
        'win_rate': round(sum(value > 0 for value in values) / count, 4),
        'limitup_rate': round(sum(value >= 0.095 for value in values) / count, 4),
        'near_limitup_rate': round(sum(value >= 0.07 for value in values) / count, 4),
        'loss_rate': round(sum(value < 0 for value in values) / count, 4),
        'large_loss_rate': round(sum(value <= -0.05 for value in values) / count, 4),
    }


def _db_rows_since(start_date: str = '2026-06-20', end_date: Optional[str] = None) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Load every candidate/pick/return row from DB for a comparable replay."""
    from sqlalchemy import create_engine, text
    import os
    engine = create_engine(os.environ.get('DATABASE_URL', 'postgresql://xiaogu:xiaogu@localhost:5432/xiaogu'))
    end_clause = 'AND trade_date <= :end_date' if end_date else ''
    params = {'start_date': start_date, 'end_date': end_date}
    with engine.connect() as conn:
        candidates = [dict(row) for row in conn.execute(text(f'SELECT * FROM daily_candidates WHERE trade_date >= :start_date {end_clause} ORDER BY trade_date, rank, symbol'), params).mappings().all()]
        picks = [dict(row) for row in conn.execute(text(f'SELECT * FROM picks WHERE trade_date >= :start_date {end_clause} ORDER BY trade_date, id'), params).mappings().all()]
        returns = [dict(row) for row in conn.execute(text(f'SELECT * FROM returns WHERE trade_date >= :start_date {end_clause} ORDER BY trade_date, symbol'), params).mappings().all()]
    candidates = _filter_trading_day_rows(candidates)
    picks = _filter_trading_day_rows(picks)
    returns = _filter_trading_day_rows(returns)
    return_map = {_date_key(row.get('trade_date')) + ':' + str(row.get('symbol') or ''): row for row in returns}
    pick_map: Dict[str, Dict[str, Any]] = {}
    for row in picks:
        key = _date_key(row.get('trade_date')) + ':' + str(row.get('symbol') or '')
        if key in return_map:
            row['_t1_return'] = return_map[key].get('t1_return')
    for row in picks:
        if str(row.get('decision') or '').upper() != 'PAPER_PICK':
            continue
        key = _date_key(row.get('trade_date'))
        current = pick_map.get(key)
        if current is None or (float(row.get('final_score') or 0), str(row.get('symbol') or '')) > (float(current.get('final_score') or 0), str(current.get('symbol') or '')):
            pick_map[key] = row
    pick_map['__all__'] = {'rows': [row for row in picks if str(row.get('decision') or '').upper() == 'PAPER_PICK']}
    return candidates, pick_map, return_map


def _cohort_rows(candidates: List[Dict[str, Any]], return_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_date.setdefault(_date_key(candidate.get('trade_date')), []).append(candidate)
    enriched = []
    for trade_date, rows in by_date.items():
        top10_count = sum(1 for row in rows if 1 <= int(row.get('rank') or 999999) <= 10)
        for row in rows:
            key = trade_date + ':' + str(row.get('symbol') or '')
            ret = return_map.get(key, {})
            has_return = ret.get('t1_return') is not None
            info = classify_candidate_cohort(row, top10_count=top10_count, has_return=has_return, trade_date=trade_date)
            enriched.append({
                **row, **info,
                't1_return': ret.get('t1_return'), 't2_return': ret.get('t2_return'),
                't3_return': ret.get('t3_return'), 't5_return': ret.get('t5_return'),
                'is_limit_up': ret.get('is_limit_up'),
                'next_day_open_return': ret.get('next_day_open_return'),
                'next_day_high_return': ret.get('next_day_high_return'),
                'next_day_low_return': ret.get('next_day_low_return'),
                'next_day_drawdown': ret.get('next_day_drawdown'),
                'high_to_close_retrace': ret.get('high_to_close_retrace'),
            })
    return enriched


def _performance_for_rows(rows: List[Dict[str, Any]], pick_map: Dict[str, Dict[str, Any]], *, name: str) -> Dict[str, Any]:
    dates = sorted({_date_key(row.get('trade_date')) for row in rows})
    top10 = [row for row in rows if 1 <= int(row.get('rank') or 999999) <= 10]
    filled_top10 = [row for row in top10 if row.get('t1_return') is not None]
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in top10:
        by_date.setdefault(_date_key(row.get('trade_date')), []).append(row)
    paper_rows = []
    for trade_date in dates:
        pick = pick_map.get(trade_date)
        if not pick:
            continue
        symbol = str(pick.get('symbol') or '')
        candidate = next((row for row in by_date.get(trade_date, []) if str(row.get('symbol') or '') == symbol), None)
        if candidate is None and name == 'all_since_2026_06_20':
            candidate = dict(pick)
            candidate['t1_return'] = pick.get('_t1_return')
        if candidate is None:
            # A filtered cohort must not inherit a PAPER_PICK from another
            # board or quality layer.
            continue
        ret = candidate.get('t1_return') if candidate else None
        paper_rows.append({**(candidate or pick), 't1_return': ret, 'trade_date': trade_date})
    paper_filled = [row for row in paper_rows if row.get('t1_return') is not None]
    daily_best = [
        max([row for row in day if row.get('t1_return') is not None], key=lambda row: row['t1_return'])
        for day in by_date.values()
        if any(row.get('t1_return') is not None for row in day)
    ]
    daily_best_values = [row['t1_return'] for row in daily_best if row.get('t1_return') is not None]
    rank26 = [row['t1_return'] for row in top10 if 2 <= int(row.get('rank') or 999) <= 6 and row.get('t1_return') is not None]
    missed = []
    false_positive = []
    for paper in paper_filled:
        day = by_date.get(paper['trade_date'], [])
        if paper['t1_return'] <= 0:
            for alternative in day:
                if alternative.get('t1_return') is not None and alternative['t1_return'] > 0 and str(alternative.get('symbol')) != str(paper.get('symbol')):
                    underestimated = [key for key in ('continuation_gene_score', 'news_catalyst', 'sector_catalyst_score', 'fund_flow_momentum', 'structured_score') if _nonempty(alternative.get(key))]
                    missed.append({'trade_date': paper['trade_date'], 'paper_pick': paper.get('symbol'), 'paper_pick_return': paper['t1_return'], 'alternative_symbol': alternative.get('symbol'), 'alternative_rank': alternative.get('rank'), 'alternative_return': alternative['t1_return'], 'not_selected_reason': alternative.get('not_selected_reason') or [], 'underestimated_factors': underestimated, 'would_reconstructed_rules_promote': bool(alternative.get('cohort_quality') in ('FULL_CHAIN_COMPLETE', 'TRANSITION_RECONSTRUCTABLE'))})
        if paper['t1_return'] <= 0:
            false_positive.append({'trade_date': paper['trade_date'], 'symbol': paper.get('symbol'), 't1_return': paper['t1_return'], 'risk_flags': paper.get('risk_flags') or paper.get('blockers') or []})
    values = [row['t1_return'] for row in filled_top10]
    paper_values = [row['t1_return'] for row in paper_filled]
    paper_summary = _return_limitup_summary(paper_values)
    best_summary = _return_limitup_summary(daily_best_values)
    rank26_summary = _return_limitup_summary(rank26)
    return {
        'cohort': name, 'dates': len(dates), 'candidate_count': len(rows), 'top10_count': len(top10),
        'return_coverage': round(len(filled_top10) / len(top10), 4) if top10 else None,
        'paper_pick_count': len(paper_rows),
        'paper_pick_win_rate': round(sum(v > 0 for v in paper_values) / len(paper_values), 4) if paper_values else None,
        'paper_pick_avg_return': round(sum(paper_values) / len(paper_values), 6) if paper_values else None,
        'paper_pick_max_drawdown': _max_drawdown(paper_values),
        'paper_pick_limitup_rate': round(sum(v >= 0.095 for v in paper_values) / len(paper_values), 4) if paper_values else None,
        'top10_avg_return': round(sum(values) / len(values), 6) if values else None,
        'top10_best_return': round(sum(daily_best_values) / len(daily_best_values), 6) if daily_best_values else None,
        'top10_win_rate': round(sum(v > 0 for v in values) / len(values), 4) if values else None,
        'top10_limitup_hit_rate': round(sum(v >= 0.095 for v in values) / len(values), 4) if values else None,
        'paper_pick_vs_top10_best_gap': round(sum(paper_values) / len(paper_values) - sum(daily_best_values) / len(daily_best_values), 6) if paper_values and daily_best_values else None,
        'paper_pick_vs_rank2_to_rank6_gap': round(sum(paper_values) / len(paper_values) - sum(rank26) / len(rank26), 6) if paper_values and rank26 else None,
        'paper_pick_return_limitup_summary': paper_summary,
        'top10_best_return_limitup_summary': best_summary,
        'rank2_to_rank6_return_limitup_summary': rank26_summary,
        'missed_profitable_candidates': missed, 'false_positive_paper_picks': false_positive,
    }


def _attribution_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Merge persisted snapshots into one replay-safe ranking row."""
    merged = dict(row)
    factor = row.get('factor_snapshot') if isinstance(row.get('factor_snapshot'), dict) else {}
    auxiliary = row.get('auxiliary_evidence_snapshot') if isinstance(row.get('auxiliary_evidence_snapshot'), dict) else {}
    for key, value in factor.items():
        merged.setdefault(key, value)
    merged['auxiliary_evidence_snapshot'] = auxiliary
    if isinstance(factor.get('capital_risk_profile'), dict):
        merged['capital_risk_profile'] = factor['capital_risk_profile']
    return merged


def _ranking_factor_explanation(row: Dict[str, Any], direction: str) -> List[str]:
    from xiaogu_forward_d1_1450_runner_v0_1 import ranking_basis_adjustment_components
    components = ranking_basis_adjustment_components(_attribution_candidate(row))
    source = components['penalties'] if direction == 'overestimated' else components['boosts']
    threshold = 0.15 if direction == 'overestimated' else 0.10
    return [key for key, value in source.items() if value >= threshold]


def _ranking_miss_types(paper: Dict[str, Any], alternative: Dict[str, Any]) -> List[str]:
    from xiaogu_forward_d1_1450_runner_v0_1 import candidate_capital_risk_profile
    paper_row = _attribution_candidate(paper)
    paper_profile = paper_row.get('capital_risk_profile') if isinstance(paper_row.get('capital_risk_profile'), dict) else candidate_capital_risk_profile(paper_row)
    alternative_row = _attribution_candidate(alternative)
    miss_types = []
    if paper_profile.get('popularity_crowding_risk', 0) >= 0.8:
        miss_types.append('HIGH_POPULARITY_OVERWEIGHT')
    if paper_profile.get('failed_limitup_risk', 0) > 0:
        miss_types.append('FAILED_LIMITUP_RISK_UNDERPENALIZED')
    if paper_profile.get('main_buy_outflow_pressure', 0) > 0:
        miss_types.append('CAPITAL_OUTFLOW_UNDERPENALIZED')
    under = _ranking_factor_explanation(alternative_row, 'underestimated')
    if 'confirmed_news_catalyst' in under or 'announcement_catalyst' in under:
        miss_types.append('NEWS_CATALYST_UNDERWEIGHTED')
    if 'sector_yesterday_limitup_gene_proxy' in under:
        miss_types.append('SECTOR_PROXY_UNDERWEIGHTED')
    if 'low_position_catalyst_score' in under:
        miss_types.append('LOW_POSITION_SETUP_UNDERWEIGHTED')
    if _nonempty(alternative_row.get('yesterday_limitup_gene_evidence')) or (alternative_row.get('continuation_gene_score') or 0) > 0:
        miss_types.append('LIMITUP_GENE_UNDERWEIGHTED')
    if (alternative_row.get('time_series_momentum') or 0) > 0 or (alternative_row.get('close_position_score') or 0) >= 0.8:
        miss_types.append('NEAR_LIMITUP_MOMENTUM_UNDERWEIGHTED')
    if 'low_position_catalyst_score' in under and 'sector_yesterday_limitup_gene_proxy' in under:
        miss_types.append('HIGH_ELASTICITY_SETUP_UNDERWEIGHTED')
    if paper_profile.get('open_board_risk', 0) > 0:
        miss_types.append('OPEN_BOARD_RISK_UNDERPENALIZED')
    if paper_profile.get('weak_limitup_confirmation'):
        miss_types.append('WEAK_CONTINUATION_OVERSELECTED')
    if not _ranking_factor_explanation(paper_row, 'overestimated') and under:
        miss_types.append('SAFE_BUT_LOW_ELASTICITY_OVERSELECTED')
    rank = int(alternative.get('rank') or 999)
    if 4 <= rank <= 6:
        miss_types.append('RANK4_TO_6_UNDERVALUED')
    return miss_types or ['UNCLASSIFIED_RANKING_MISS']


def _primary_fix_direction(miss_types: List[str]) -> str:
    mapping = {
        'FAILED_LIMITUP_RISK_UNDERPENALIZED': 'DECREASE_FAILED_LIMITUP_WEIGHT',
        'CAPITAL_OUTFLOW_UNDERPENALIZED': 'DECREASE_OUTFLOW_PRESSURE_WEIGHT',
        'HIGH_POPULARITY_OVERWEIGHT': 'DECREASE_POPULARITY_CROWDING_WEIGHT',
        'OPEN_BOARD_RISK_UNDERPENALIZED': 'DECREASE_WEAK_CONFIRMATION_WEIGHT',
        'WEAK_CONTINUATION_OVERSELECTED': 'DECREASE_WEAK_CONFIRMATION_WEIGHT',
        'NEWS_CATALYST_UNDERWEIGHTED': 'INCREASE_CATALYST_WEIGHT',
        'SECTOR_PROXY_UNDERWEIGHTED': 'INCREASE_SECTOR_PROXY_WEIGHT',
        'LOW_POSITION_SETUP_UNDERWEIGHTED': 'INCREASE_LOW_POSITION_CATALYST_WEIGHT',
        'LIMITUP_GENE_UNDERWEIGHTED': 'INCREASE_LIMITUP_GENE_WEIGHT',
        'NEAR_LIMITUP_MOMENTUM_UNDERWEIGHTED': 'INCREASE_CONFIRMED_CAPITAL_INFLOW_WEIGHT',
        'HIGH_ELASTICITY_SETUP_UNDERWEIGHTED': 'INCREASE_LIMITUP_GENE_WEIGHT',
    }
    return next((mapping[item] for item in miss_types if item in mapping), 'NO_ACTION_INSUFFICIENT_EVIDENCE')


def build_ranking_improvement_analysis(rows: List[Dict[str, Any]], pick_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Explain daily PAPER_PICK misses and replay the existing ranking basis."""
    from xiaogu_forward_d1_1450_runner_v0_1 import formal_candidate_sort_key, ranking_basis_adjustment_components

    mainboard_rows = [row for row in rows if row.get('is_mainboard') and 1 <= int(row.get('rank') or 999) <= 10]
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in mainboard_rows:
        by_date.setdefault(_date_key(row.get('trade_date')), []).append(row)
    daily = []
    replay_baseline = []
    replay_adjusted = []
    rank26_rows = []
    best_rank_distribution: Dict[str, int] = {}
    factor_patterns: Dict[str, int] = {}
    promotion_candidates = []
    for trade_date, day in sorted(by_date.items()):
        pick = pick_map.get(trade_date)
        paper = next((row for row in day if pick and str(row.get('symbol') or '') == str(pick.get('symbol') or '')), None)
        returned = [row for row in day if row.get('t1_return') is not None]
        if paper is None or paper.get('t1_return') is None or not returned:
            daily.append({
                'trade_date': trade_date, 'paper_pick': (pick or {}).get('symbol'),
                'paper_pick_rank': paper.get('rank') if paper else None,
                'paper_pick_return': paper.get('t1_return') if paper else None,
                'paper_pick_limitup_hit': False, 'paper_pick_near_limitup_hit': False,
                'top10_best': None, 'top10_best_rank': None, 'top10_best_return': None,
                'top10_best_limitup_hit': False, 'top10_best_near_limitup_hit': False,
                'gap': None, 'limitup_gap': False,
                'paper_pick_overestimated_factors': [], 'top10_best_underestimated_factors': [],
                'ranking_miss_type': ['RETURN_MISSING_CANNOT_JUDGE'],
                'primary_fix_direction': 'NO_ACTION_INSUFFICIENT_EVIDENCE',
            })
            continue
        best = max(returned, key=lambda row: row['t1_return'])
        paper_return = paper['t1_return']
        gap = paper_return - best['t1_return']
        miss_types = [] if str(best.get('symbol')) == str(paper.get('symbol')) else _ranking_miss_types(paper, best)
        daily.append({
            'trade_date': trade_date, 'paper_pick': paper.get('symbol'), 'paper_pick_rank': paper.get('rank'),
            'paper_pick_return': paper_return, 'paper_pick_limitup_hit': paper_return >= 0.095,
            'paper_pick_near_limitup_hit': paper_return >= 0.07,
            'top10_best': best.get('symbol'), 'top10_best_rank': best.get('rank'),
            'top10_best_return': best['t1_return'], 'top10_best_limitup_hit': best['t1_return'] >= 0.095,
            'top10_best_near_limitup_hit': best['t1_return'] >= 0.07,
            'gap': gap, 'limitup_gap': best['t1_return'] >= 0.095 and paper_return < 0.095,
            'paper_pick_overestimated_factors': _ranking_factor_explanation(paper, 'overestimated'),
            'top10_best_underestimated_factors': _ranking_factor_explanation(best, 'underestimated'),
            'ranking_miss_type': miss_types, 'primary_fix_direction': _primary_fix_direction(miss_types),
        })
        replay_baseline.append(paper_return)
        adjusted = max(returned, key=lambda row: formal_candidate_sort_key(_attribution_candidate(row)))
        replay_adjusted.append(adjusted['t1_return'])
        for row in returned:
            rank = int(row.get('rank') or 999)
            if 2 <= rank <= 6:
                rank26_rows.append(row)
        if 2 <= int(best.get('rank') or 999) <= 6:
            rank_key = str(best.get('rank'))
            best_rank_distribution[rank_key] = best_rank_distribution.get(rank_key, 0) + 1
        if 2 <= int(best.get('rank') or 999) <= 6 and best['t1_return'] > paper_return:
            factors = _ranking_factor_explanation(best, 'underestimated')
            for factor in factors:
                factor_patterns[factor] = factor_patterns.get(factor, 0) + 1
            best_row = _attribution_candidate(best)
            capital = best_row.get('capital_risk_profile') if isinstance(best_row.get('capital_risk_profile'), dict) else {}
            safe_to_promote = bool(factors) and not any(
                capital.get(key, 0) for key in ('failed_limitup_risk', 'main_buy_outflow_pressure', 'high_popularity_trap_risk')
            )
            promotion_candidates.append({
                'trade_date': trade_date, 'symbol': best.get('symbol'), 'rank': best.get('rank'),
                't1_return': best['t1_return'], 'limitup_hit': best['t1_return'] >= 0.095,
                'near_limitup_hit': best['t1_return'] >= 0.07,
                'paper_pick_symbol': paper.get('symbol'), 'paper_pick_return': paper_return,
                'gap_vs_paper_pick': best['t1_return'] - paper_return,
                'underestimated_factors': factors,
                'paper_pick_overestimated_factors': _ranking_factor_explanation(paper, 'overestimated'),
                'not_selected_reason': best.get('not_selected_reason') or [],
                'promotion_reason': 'rank2_to_rank6_positive_pattern' if safe_to_promote else 'posthoc_return_without_safe_confirmation',
                'safe_to_promote': safe_to_promote,
                'ranking_adjustment': ranking_basis_adjustment_components(best_row),
            })
    rank26_returns = [row['t1_return'] for row in rank26_rows]
    rank_level_metrics = {}
    for rank in range(2, 7):
        rank_values = [row['t1_return'] for row in rank26_rows if int(row.get('rank') or 999) == rank]
        rank_level_metrics[str(rank)] = _return_limitup_summary(rank_values)
    elasticity = {
        **_return_limitup_summary(rank26_returns),
        'best_rank_distribution': best_rank_distribution,
        'rank_level_metrics': rank_level_metrics,
        'positive_patterns': factor_patterns,
        'negative_patterns': {},
        'promotion_candidates': promotion_candidates,
    }
    return {
        'paper_pick_vs_top10_best_daily': daily,
        'rank2_to_rank6_analysis': {
            **elasticity,
            'factor_patterns': factor_patterns,
        },
        'rank2_to_rank6_elasticity_analysis': elasticity,
        'ranking_basis_replay': {
            'sample_count': len(replay_baseline),
            'baseline_avg_return': round(sum(replay_baseline) / len(replay_baseline), 6) if replay_baseline else None,
            'adjusted_avg_return': round(sum(replay_adjusted) / len(replay_adjusted), 6) if replay_adjusted else None,
            'paper_pick_vs_top10_best_gap_before': round(sum(item['gap'] for item in daily if item.get('gap') is not None) / max(1, sum(item.get('gap') is not None for item in daily)), 6),
            'paper_pick_vs_top10_best_gap_after': round(sum(value - max(row['t1_return'] for row in by_date[trade_date] if row.get('t1_return') is not None) for trade_date, value in zip([item['trade_date'] for item in daily if item.get('gap') is not None], replay_adjusted)) / len(replay_adjusted), 6) if replay_adjusted else None,
            'win_rate_before': round(sum(value > 0 for value in replay_baseline) / len(replay_baseline), 4) if replay_baseline else None,
            'win_rate_after': round(sum(value > 0 for value in replay_adjusted) / len(replay_adjusted), 4) if replay_adjusted else None,
        },
    }


def build_cross_date_case_studies(candidates: List[Dict[str, Any]], pick_map: Dict[str, Dict[str, Any]], return_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = _cohort_rows(candidates, return_map)
    def symbol_case(symbol: str, name: str) -> Dict[str, Any]:
        samples = [row for row in rows if str(row.get('symbol') or '') == symbol]
        dates = sorted({_date_key(row.get('trade_date')) for row in samples})
        all_picks = (pick_map.get('__all__') or {}).get('rows') or []
        if not all_picks:
            all_picks = [value for key, value in pick_map.items() if key != '__all__' and isinstance(value, dict)]
        picked = sorted({_date_key(row.get('trade_date')) for row in all_picks if str(row.get('symbol') or '') == symbol})
        top10 = [d for d in dates if int(next(row.get('rank') or 999 for row in samples if _date_key(row.get('trade_date')) == d)) <= 10]
        returns = {_date_key(row.get('trade_date')): {'t1': row.get('t1_return'), 't2': row.get('t2_return'), 't3': row.get('t3_return')} for row in samples}
        return {'symbol': symbol, 'name': name, 'sample_dates': dates, 'legacy_chain_pick_dates': [], 'current_chain_pick_dates': picked, 'would_current_chain_pick': bool(picked), 'factor_snapshot_comparison': {d: {'factor_snapshot': next((r.get('factor_snapshot') or {} for r in samples if _date_key(r.get('trade_date')) == d), {}), 'auxiliary_evidence': next((r.get('auxiliary_evidence_snapshot') or {} for r in samples if _date_key(r.get('trade_date')) == d), {})} for d in dates}, 'actual_returns': returns, 'top10_dates': top10, 'lesson': '样本按日期保留；旧链路选择记录若不存在，不推断为旧链路命中。'}
    giant = symbol_case('002558', '巨人网络')
    huatian_rows = [row for row in rows if str(row.get('symbol') or '') == '002185']
    huatian_dates = sorted({_date_key(row.get('trade_date')) for row in huatian_rows})
    broken = [d for d in huatian_dates if ((next((r.get('factor_snapshot') or {} for r in huatian_rows if _date_key(r.get('trade_date')) == d), {}).get('capital_risk_profile') or {}).get('failed_limitup'))]
    divergence = [d for d in huatian_dates if ((next((r.get('factor_snapshot') or {} for r in huatian_rows if _date_key(r.get('trade_date')) == d), {}).get('capital_risk_profile') or {}).get('capital_divergence_score') is not None)]
    huatian = {'symbol': '002185', 'name': '华天科技', 'sample_dates': huatian_dates, 'broken_board_risk_dates': broken, 'capital_divergence_dates': divergence, 'paper_pick_dates': [d for d in huatian_dates if str((pick_map.get(d) or {}).get('symbol') or '') == '002185'], 'top10_not_selected_dates': [d for d in huatian_dates if int(next((row.get('rank') or 999 for row in huatian_rows if _date_key(row.get('trade_date')) == d), 999)) <= 10 and str((pick_map.get(d) or {}).get('symbol') or '') != '002185'], 'postmortem': {'interpretation': '炸板、主买流出、人气拥挤组合应降权；暗盘流入只能软化，不能抵消未确认催化。', 'rows': [{k: row.get(k) for k in ('trade_date', 'rank', 't1_return', 'factor_snapshot', 'auxiliary_evidence_snapshot')} for row in huatian_rows]}}
    return {'giant_network': giant, 'huatian_tech': huatian}


def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _candidate_signal(row: Dict[str, Any], key: str) -> Any:
    if key in row and row.get(key) is not None:
        return row.get(key)
    for source_name in ('factor_snapshot', 'auxiliary_evidence_snapshot', 'ranking_basis', 'candidate_features'):
        source = row.get(source_name)
        if isinstance(source, dict) and key in source and source.get(key) is not None:
            return source.get(key)
    return None


def _capital_risk(row: Dict[str, Any]) -> Dict[str, Any]:
    profile = _candidate_signal(row, 'capital_risk_profile')
    return profile if isinstance(profile, dict) else {}


COMPLETED_PAPER_PICK_EXECUTION_FIELDS = (
    't1_return',
    'next_day_open_return',
    'next_day_high_return',
    'next_day_low_return',
    'high_to_close_retrace',
)


def completed_paper_pick_sample_days(
    rows: List[Dict[str, Any]],
    pick_map: Dict[str, Dict[str, Any]],
    pending_dates: set[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Return the single comparable PAPER_PICK cohort and explicit exclusions."""
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if row.get('is_mainboard') and 1 <= int(row.get('rank') or 999) <= 10:
            by_date.setdefault(_date_key(row.get('trade_date')), []).append(row)
    completed = []
    excluded: List[Dict[str, str]] = []
    for trade_date, day in sorted(by_date.items()):
        pick = pick_map.get(trade_date)
        paper = next((row for row in day if pick and str(row.get('symbol') or '') == str(pick.get('symbol') or '')), None)
        if pick is None:
            continue
        if paper is None:
            excluded.append({'trade_date': trade_date, 'reason': 'paper_pick_not_in_mainboard_top10_snapshot'})
            continue
        if trade_date in pending_dates:
            excluded.append({'trade_date': trade_date, 'reason': 'return_pending'})
            continue
        missing_execution = [
            field for field in COMPLETED_PAPER_PICK_EXECUTION_FIELDS
            if paper.get(field) is None
        ]
        if missing_execution:
            excluded.append({'trade_date': trade_date, 'reason': 'missing_execution_metrics:' + ','.join(missing_execution)})
            continue
        decision_snapshot = {
            key: value for key, value in paper.items()
            if key not in COMPLETED_PAPER_PICK_EXECUTION_FIELDS + (
                't2_return', 't3_return', 't5_return', 'is_limit_up', 'next_day_drawdown',
            )
        }
        if _future_field_violations(decision_snapshot):
            excluded.append({'trade_date': trade_date, 'reason': 'future_data_in_decision_snapshot'})
            continue
        if str(paper.get('cohort_quality') or '') == 'INSUFFICIENT_EVIDENCE':
            excluded.append({'trade_date': trade_date, 'reason': 'candidate_snapshot_insufficient_evidence'})
            continue
        returned = [row for row in day if row.get('t1_return') is not None]
        if returned:
            completed.append({'trade_date': trade_date, 'paper': paper, 'day': returned})
        else:
            excluded.append({'trade_date': trade_date, 'reason': 'top10_return_coverage_missing'})
    return completed, excluded


def _completed_paper_pick_days(
    rows: List[Dict[str, Any]],
    pick_map: Dict[str, Dict[str, Any]],
    pending_dates: set[str],
) -> List[Dict[str, Any]]:
    """Compatibility wrapper for callers that only need the completed rows."""
    completed, _excluded = completed_paper_pick_sample_days(rows, pick_map, pending_dates)
    return completed


def _average(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _paper_pick_performance_gate(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [day['paper']['t1_return'] for day in completed_days]
    summary = _return_limitup_summary(values)
    top10_best = [max(day['day'], key=lambda row: row['t1_return'])['t1_return'] for day in completed_days]
    rank26_best = [
        max(rank26, key=lambda row: row['t1_return'])['t1_return']
        for day in completed_days
        if (rank26 := [row for row in day['day'] if 2 <= int(row.get('rank') or 999) <= 6])
    ]
    gate = {
        'status': 'INSUFFICIENT_SAMPLE',
        'sample_count': len(values),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'avg_t1_return': summary['avg_return'],
        'win_rate': summary['win_rate'],
        'limitup_rate': summary['limitup_rate'],
        'near_limitup_rate': summary['near_limitup_rate'],
        'large_loss_rate': summary['large_loss_rate'],
        'max_drawdown': _max_drawdown(values),
        'benchmarks': {
            'top10_best_avg_t1_return': _average(top10_best),
            'rank2_to_rank6_best_avg_t1_return': _average(rank26_best),
        },
        'thresholds': PERFORMANCE_GATE_THRESHOLDS,
        'blocking_reason': f'sample_count={len(values)} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}',
    }
    if len(values) < MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        return gate
    failures = [
        name for name, threshold in PERFORMANCE_GATE_THRESHOLDS.items()
        if gate.get(name) is None or (
            gate[name] > threshold if name == 'large_loss_rate'
            else gate[name] < threshold
        )
    ]
    gate['status'] = 'PASS' if not failures else 'FAIL'
    gate['blocking_reason'] = None if not failures else 'threshold_failed:' + ','.join(failures)
    return gate


def _paper_pick_loss_attribution(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    daily_cases = []
    distribution: Dict[str, int] = {}
    for record in completed_days:
        paper = record['paper']
        best = max(record['day'], key=lambda row: row['t1_return'])
        if str(paper.get('symbol') or '') == str(best.get('symbol') or ''):
            continue
        paper_risk = _capital_risk(paper)
        best_risk = _capital_risk(best)
        miss_types = []
        if _numeric(paper_risk.get('failed_limitup_risk')) > 0:
            miss_types.append('FAILED_LIMITUP_RISK_UNDERPENALIZED')
        if _numeric(paper_risk.get('main_buy_outflow_pressure')) > 0:
            miss_types.append('OUTFLOW_RISK_UNDERPENALIZED')
        if _numeric(paper_risk.get('popularity_crowding_risk')) >= 0.8 or _numeric(paper_risk.get('high_popularity_trap_risk')) > 0:
            miss_types.append('HIGH_POPULARITY_REVERSAL_RISK')
        if _numeric(_candidate_signal(best, 'low_position_catalyst_score')) > 0 or _nonempty(_candidate_signal(best, 'confirmed_news_catalyst')):
            miss_types.append('LOW_POSITION_CATALYST_UNDERWEIGHTED')
        if _numeric(_candidate_signal(best, 'sector_catalyst_score')) > 0 or _numeric(_candidate_signal(best, 'sector_heat_score')) > 0:
            miss_types.append('SECTOR_HEAT_UNDERWEIGHTED')
        if _limitup_gene_components(best)['signals']:
            miss_types.append('LIMITUP_GENE_UNDERWEIGHTED')
        if 4 <= int(best.get('rank') or 999) <= 6:
            miss_types.append('RANK4_TO_6_UNDERVALUED')
        if any(_numeric(best_risk.get(key)) > 0 for key in ('failed_limitup_risk', 'main_buy_outflow_pressure', 'high_popularity_trap_risk')):
            miss_types = [item for item in miss_types if item != 'LIMITUP_GENE_UNDERWEIGHTED'] or ['NO_ACTION_INSUFFICIENT_EVIDENCE']
        if not miss_types:
            miss_types = ['NO_ACTION_INSUFFICIENT_EVIDENCE']
        for item in miss_types:
            distribution[item] = distribution.get(item, 0) + 1
        direction = {
            'LOW_POSITION_CATALYST_UNDERWEIGHTED': 'increase_shadow_weight_for_low_position_catalyst',
            'LIMITUP_GENE_UNDERWEIGHTED': 'increase_shadow_weight_for_limitup_gene',
            'FAILED_LIMITUP_RISK_UNDERPENALIZED': 'increase_shadow_penalty_for_failed_limitup_risk',
            'OUTFLOW_RISK_UNDERPENALIZED': 'increase_shadow_penalty_for_outflow_risk',
            'HIGH_POPULARITY_REVERSAL_RISK': 'increase_shadow_penalty_for_popularity_reversal',
        }
        daily_cases.append({
            'trade_date': record['trade_date'],
            'paper_pick_symbol': paper.get('symbol'), 'paper_pick_rank': paper.get('rank'),
            'paper_pick_t1_return': paper['t1_return'],
            'top10_best_symbol': best.get('symbol'), 'top10_best_rank': best.get('rank'),
            'top10_best_t1_return': best['t1_return'],
            'return_gap': round(paper['t1_return'] - best['t1_return'], 6),
            'top10_best_limitup_hit': best['t1_return'] >= 0.095,
            'miss_types': miss_types,
            'primary_fix_direction': next((direction[item] for item in miss_types if item in direction), 'NO_ACTION_INSUFFICIENT_EVIDENCE'),
        })
    return {
        'status': 'PASS', 'sample_count': len(completed_days), 'daily_cases': daily_cases,
        'miss_type_distribution': dict(sorted(distribution.items())),
    }


def _risk_flag_names(row: Dict[str, Any]) -> List[str]:
    risk = _capital_risk(row)
    labels = []
    if _numeric(risk.get('main_buy_outflow_pressure')) > 0:
        labels.append('MAIN_BUY_OUTFLOW')
    if _numeric(risk.get('popularity_crowding_risk')) >= 0.8 or _numeric(risk.get('high_popularity_trap_risk')) > 0:
        labels.append('HIGH_POPULARITY_REVERSAL')
    if _numeric(risk.get('failed_limitup_risk')) > 0:
        labels.append('FAILED_LIMITUP')
    if _numeric(_candidate_signal(row, 'next_day_low_open_risk')) > 0 or _numeric(_candidate_signal(row, 'gap_down_risk')) > 0:
        labels.append('GAP_DOWN_RISK')
    return labels


def _limitup_gene_components(row: Dict[str, Any]) -> Dict[str, Any]:
    signals = limitup_gene_signal_values(row) if limitup_gene_signal_values is not None else {
        name: False for name in LIMITUP_GENE_SHADOW_SIGNALS
    }
    risk_flags = _risk_flag_names(row)
    return {
        'signals': [name for name in LIMITUP_GENE_SHADOW_SIGNALS if signals[name]],
        'risk_flags': risk_flags,
        'boost': float(sum(signals.values())),
        'penalty': float(len(risk_flags)) * 4.0,
    }


def _shadow_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    profile = _candidate_signal(row, 'shadow_risk_profile')
    return profile if isinstance(profile, dict) else {}


def _shadow_score(row: Dict[str, Any], variant: str) -> float:
    score = -float(int(row.get('rank') or 999))
    risk = _capital_risk(row)
    shadow = _shadow_profile(row)
    if variant == 'low_position_catalyst_shadow_plus':
        score += 3.0 * _numeric(_candidate_signal(row, 'low_position_catalyst_score'))
        score += 1.5 * min(1.0, _numeric(_candidate_signal(row, 'fund_flow_momentum')))
        score += 1.0 if _nonempty(_candidate_signal(row, 'confirmed_news_catalyst')) else 0.0
    elif variant == 'limitup_gene_shadow_plus':
        gene = _limitup_gene_components(row)
        score += gene['boost'] * 2.0
        score += min(1.0, _numeric(_candidate_signal(row, 'continuation_gene_score')))
        score -= gene['penalty']
    elif variant == 'risk_penalty_shadow_plus':
        score -= 3.0 * _numeric(risk.get('failed_limitup_risk'))
        score -= 2.0 * _numeric(risk.get('main_buy_outflow_pressure'))
        score -= 2.0 * max(_numeric(risk.get('popularity_crowding_risk')), _numeric(risk.get('high_popularity_trap_risk')))
    elif variant == 'weak_market_defensive_shadow':
        score -= _numeric(shadow.get('chase_high_shadow_penalty'))
        score += 8.0 * _numeric(shadow.get('defensive_carry_score'))
        if str(shadow.get('limitup_gene_shadow_gate') or '') == 'BLOCK_SHADOW':
            score -= 8.0
    elif variant == 'social_catalyst_shadow':
        social = shadow.get('social_confirmation') if isinstance(shadow.get('social_confirmation'), dict) else {}
        if social.get('status') == 'PASS' and str(shadow.get('chase_high_risk') or '') != 'HIGH':
            score += 3.0 * _numeric(social.get('social_catalyst_score'))
        if social.get('status') == 'NOISY':
            score -= 2.0
    return score


MAINLINE_ALIAS_GROUPS = {
    '创新药': ('创新药', '医药', 'cro', '生物医药', '药'),
    '电力': ('电力', '电网', '绿色电力', '特高压'),
    '机器人': ('机器人', '减速器', '人形机器人'),
    '半导体': ('半导体', '芯片', '光刻胶', '存储'),
    '人工智能': ('人工智能', 'ai', '算力', '大模型'),
}


MAINLINE_SOURCE_FIELDS = (
    'predicted_sector', 'sector_prediction_boost', 'sector_opportunity_tags',
    'sector_news_strength', 'sector_opportunity_score', 'main_theme_core_score',
    'main_theme_alignment_score', 'concept_board', 'industry_board',
    'concept_capital_flow', 'sector_fund_flow', 'sector_catalyst_score',
    'sector_heat_score', 'source_layers',
)


def _flatten_theme_values(value: Any) -> List[str]:
    if isinstance(value, dict):
        return [str(key) for key, inner in value.items() if _nonempty(inner)]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value in (None, ''):
        return []
    return [str(value)]


def _normalize_mainline_theme(value: str) -> Optional[str]:
    text = str(value or '').strip().lower()
    if not text:
        return None
    for canonical, aliases in MAINLINE_ALIAS_GROUPS.items():
        if any(alias.lower() in text for alias in aliases):
            return canonical
    if len(text) <= 24 and not text.replace('.', '', 1).isdigit():
        return str(value).strip()
    return None


def _candidate_themes(row: Dict[str, Any]) -> List[str]:
    themes = []
    for field in MAINLINE_SOURCE_FIELDS:
        for item in _flatten_theme_values(_candidate_signal(row, field)):
            theme = _normalize_mainline_theme(item)
            if theme:
                themes.append(theme)
    return list(dict.fromkeys(themes))


def _daily_mainlines(day: List[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in day:
        for theme in _candidate_themes(row):
            item = groups.setdefault(theme, {
                'theme': theme, 'candidate_count': 0, 'symbols': [], 'score_total': 0.0,
                'fund_flow_total': 0.0, 'limitup_gene_count': 0, 'evidence_sources': set(),
            })
            item['candidate_count'] += 1
            item['symbols'].append(str(row.get('symbol') or ''))
            item['score_total'] += _numeric(row.get('final_score') or row.get('score'))
            item['fund_flow_total'] += _numeric(_candidate_signal(row, 'fund_flow_momentum'))
            if _limitup_gene_components(row)['signals'] or _numeric(_candidate_signal(row, 'limitup_capture_score')) > 0:
                item['limitup_gene_count'] += 1
            item['evidence_sources'].add('persisted_candidate_evidence')
    ranked = []
    for item in groups.values():
        count = max(1, item['candidate_count'])
        score = (
            item['candidate_count']
            + item['fund_flow_total']
            + item['score_total'] / count / 100.0
            + item['limitup_gene_count'] * 0.75
        )
        ranked.append({
            'theme': item['theme'], 'score': round(score, 6),
            'candidate_count': item['candidate_count'],
            'symbols': sorted(set(item['symbols'])),
            'score_components': {
                'breadth_count': item['candidate_count'],
                'fund_flow': round(item['fund_flow_total'], 6),
                'avg_candidate_score': round(item['score_total'] / count, 6),
                'limitup_gene_count': item['limitup_gene_count'],
            },
            'evidence_source': sorted(item['evidence_sources']),
        })
    ranked.sort(key=lambda item: (-item['score'], item['theme']))
    return {
        'status': 'PASS' if ranked else 'MAINLINE_DATA_PARTIAL',
        'top3': ranked[:3], 'top5': ranked[:5],
    }


def _row_matches_mainline(row: Dict[str, Any], themes: List[str]) -> bool:
    row_themes = set(_candidate_themes(row))
    return any(theme in row_themes for theme in themes)


def _mainline_alignment_score(row: Dict[str, Any], mainlines: Dict[str, Any]) -> float:
    top5 = [item['theme'] for item in mainlines.get('top5') or []]
    if not top5:
        return 0.0
    row_themes = set(_candidate_themes(row))
    for index, theme in enumerate(top5):
        if theme in row_themes:
            return round(1.0 - index * 0.15, 4)
    return 0.0


def _mainline_blockers(row: Dict[str, Any]) -> List[str]:
    blockers = []
    for field in ('not_selected_reason', 'official_target_exclusion_reasons', 'blockers', 'risk_flags'):
        value = row.get(field)
        if isinstance(value, list):
            blockers.extend(str(item) for item in value if str(item).strip())
        elif _nonempty(value):
            blockers.append(str(value))
    if _numeric(_candidate_signal(row, 'mainboard_auxiliary_evidence_hard_block')) > 0:
        blockers.append('mainboard_auxiliary_evidence_hard_block')
    if str(_candidate_signal(row, 'source_layer') or '').lower() == 'sector_follower' or 'sector_follower' in [str(x) for x in _flatten_theme_values(row.get('source_layers'))]:
        blockers.append('sector_follower_diagnostic_only')
    return sorted(set(blockers))


def _mainline_primary_bucket(record: Dict[str, Any], mainlines: Dict[str, Any]) -> str:
    top3 = [item['theme'] for item in mainlines.get('top3') or []]
    if not top3:
        return 'MAINLINE_NOT_IN_DATA'
    mainline_rows = [row for row in record['day'] if _row_matches_mainline(row, top3)]
    if not mainline_rows:
        return 'MAINLINE_NOT_IN_POOL'
    clean_rows = [row for row in mainline_rows if not _mainline_blockers(row)]
    if not clean_rows:
        return 'MAINLINE_BLOCKED_BY_GATE'
    paper = record['paper']
    if _row_matches_mainline(paper, top3):
        return 'PAPER_PICK_MAINLINE_HIT'
    return 'MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK'


def _failed_limit_reversal_risk_score(row: Dict[str, Any]) -> float:
    risk = _capital_risk(row)
    score = 0.0
    score += 2.0 * _numeric(risk.get('failed_limitup_risk'))
    score += 1.5 * _numeric(risk.get('main_buy_outflow_pressure'))
    score += 1.0 * max(_numeric(risk.get('popularity_crowding_risk')), _numeric(risk.get('high_popularity_trap_risk')))
    score += 1.0 * _numeric(_candidate_signal(row, 'broken_limit_risk'))
    score += 1.0 * _numeric(_candidate_signal(row, 'intraday_pullback'))
    score += 1.0 * _numeric(_candidate_signal(row, 'weak_close_risk'))
    score += 0.5 * _numeric(_candidate_signal(row, 'high_turnover_risk'))
    return round(score, 6)


def _mainline_shadow_score(row: Dict[str, Any], variant: str, mainlines: Dict[str, Any]) -> float:
    score = -float(int(row.get('rank') or 999))
    alignment = _mainline_alignment_score(row, mainlines)
    score += alignment * 20.0
    if variant == 'mainline_limitup_gene_shadow':
        score += _limitup_gene_components(row)['boost'] * 3.0
    elif variant == 'mainline_low_position_catalyst_shadow':
        score += 4.0 * _numeric(_candidate_signal(row, 'low_position_catalyst_score'))
        score += 1.0 if _nonempty(_candidate_signal(row, 'confirmed_news_catalyst')) else 0.0
    elif variant == 'mainline_risk_penalty_shadow':
        score -= 3.0 * _failed_limit_reversal_risk_score(row)
    elif variant == 'sector_follower_mainline_shadow':
        if 'sector_follower_diagnostic_only' not in _mainline_blockers(row):
            score -= 8.0
        score -= 2.0 * max(0.0, -_numeric(_candidate_signal(row, 'fund_flow_momentum')))
        score -= 2.0 * _numeric(_candidate_signal(row, 'near_limit_unconfirmed_risk'))
    elif variant == 'mainline_composite_shadow':
        score += _limitup_gene_components(row)['boost'] * 2.0
        score += 2.5 * _numeric(_candidate_signal(row, 'low_position_catalyst_score'))
        score -= 2.0 * _failed_limit_reversal_risk_score(row)
    return score


def _mainline_candidate_summary(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        'symbol': row.get('symbol'), 'name': row.get('name'), 'rank': row.get('rank'),
        't1_return': row.get('t1_return'), 'limitup_hit': _numeric(row.get('t1_return')) >= 0.095,
        'theme_tags': _candidate_themes(row), 'blockers': _mainline_blockers(row),
        'failed_limit_reversal_risk_score': _failed_limit_reversal_risk_score(row),
    }


def _mainline_diagnostics(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    daily = []
    distribution: Dict[str, int] = {}
    for record in completed_days:
        mainlines = _daily_mainlines(record['day'])
        top3 = [item['theme'] for item in mainlines.get('top3') or []]
        paper = record['paper']
        mainline_rows = [row for row in record['day'] if _row_matches_mainline(row, top3)] if top3 else []
        best_mainline = max(mainline_rows, key=lambda row: (_numeric(row.get('t1_return')), -int(row.get('rank') or 999)), default=None)
        bucket = _mainline_primary_bucket(record, mainlines)
        distribution[bucket] = distribution.get(bucket, 0) + 1
        daily.append({
            'trade_date': record['trade_date'],
            'market_mainline_top3': mainlines.get('top3') or [],
            'market_mainline_top5': mainlines.get('top5') or [],
            'data_status': mainlines['status'],
            'paper_pick_symbol': paper.get('symbol'),
            'paper_pick_theme_tags': _candidate_themes(paper),
            'paper_pick_mainline_hit_top3': _row_matches_mainline(paper, top3),
            'paper_pick_mainline_hit_top5': _row_matches_mainline(paper, [item['theme'] for item in mainlines.get('top5') or []]),
            'paper_pick_mainline_alignment_score': _mainline_alignment_score(paper, mainlines),
            'best_mainline_candidate': _mainline_candidate_summary(best_mainline),
            'miss_bucket': bucket,
        })
    return {'daily': daily, 'distribution': dict(sorted(distribution.items()))}


def _mainline_pool_coverage(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for record in completed_days:
        mainlines = _daily_mainlines(record['day'])
        top3 = [item['theme'] for item in mainlines.get('top3') or []]
        source_counts: Dict[str, int] = {}
        for row in record['day']:
            if not _row_matches_mainline(row, top3):
                continue
            for layer in _flatten_theme_values(row.get('source_layers')) or [_candidate_signal(row, 'source_layer') or 'unknown']:
                key = str(layer or 'unknown')
                source_counts[key] = source_counts.get(key, 0) + 1
        rows.append({
            'trade_date': record['trade_date'],
            'top_mainlines': top3,
            'paper_scoring_candidates_count': len(record['day']),
            'top10_count': sum(1 for row in record['day'] if int(row.get('rank') or 999) <= 10),
            'mainboard_top10_count': sum(1 for row in record['day'] if row.get('is_mainboard', True) and int(row.get('rank') or 999) <= 10),
            'mainline_candidate_count': sum(1 for row in record['day'] if _row_matches_mainline(row, top3)),
            'source_layer_counts': dict(sorted(source_counts.items())),
            'mapping_status': 'MAINLINE_ALIAS_UNMAPPED' if top3 and not any(_row_matches_mainline(row, top3) for row in record['day']) else 'MAPPED',
        })
    return {'status': 'PASS', 'sample_count': len(completed_days), 'rows': rows}


def _mainline_shadow_replay(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    variants = [
        'baseline_current', 'mainline_first_shadow', 'mainline_limitup_gene_shadow',
        'mainline_low_position_catalyst_shadow', 'mainline_risk_penalty_shadow',
        'sector_follower_mainline_shadow', 'mainline_composite_shadow',
    ]
    baseline_values = [_numeric(day['paper'].get('t1_return')) for day in completed_days]
    results = []
    for variant in variants:
        selected = []
        for day in completed_days:
            mainlines = _daily_mainlines(day['day'])
            row = day['paper'] if variant == 'baseline_current' else max(
                day['day'], key=lambda item: (_mainline_shadow_score(item, variant, mainlines), str(item.get('symbol') or '')),
            )
            selected.append(row)
        values = [_numeric(row.get('t1_return')) for row in selected]
        summary = _return_limitup_summary(values)
        results.append({
            'name': variant, 'sample_count': len(values), 'avg_t1_return': summary['avg_return'],
            'win_rate': summary['win_rate'], 'limitup_rate': summary['limitup_rate'],
            'large_loss_rate': summary['large_loss_rate'], 'max_drawdown': _max_drawdown(values),
            'beats_baseline': None if variant == 'baseline_current' else bool(_average(values) is not None and _average(baseline_values) is not None and _average(values) > _average(baseline_values)),
            'selected_for_production': False,
            'daily_selected': [
                {
                    'trade_date': day['trade_date'], 'symbol': row.get('symbol'), 'rank': row.get('rank'),
                    'mainline_tag': _candidate_themes(row), 't1_return': row.get('t1_return'),
                    'limitup_hit': _numeric(row.get('t1_return')) >= 0.095,
                    'large_loss_hit': _numeric(row.get('t1_return')) <= -0.05,
                }
                for day, row in zip(completed_days, selected)
            ],
        })
    return {
        'status': 'DIAGNOSTIC_ONLY', 'sample_count': len(completed_days),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'selected_for_production': False,
        'variants': results,
        'blocking_reason': f'sample_count={len(completed_days)} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}' if len(completed_days) < MINIMUM_PERFORMANCE_SAMPLE_COUNT else 'production_change_requires_separate_plan',
    }


def _mainline_case_book(completed_days: List[Dict[str, Any]], diagnostics: Dict[str, Any], shadow_replay: Dict[str, Any]) -> Dict[str, Any]:
    diagnostics_by_date = {row['trade_date']: row for row in diagnostics.get('daily') or []}
    shadow_winners: Dict[str, List[str]] = {}
    for variant in shadow_replay.get('variants') or []:
        if variant.get('name') == 'baseline_current':
            continue
        for item in variant.get('daily_selected') or []:
            shadow_winners.setdefault(item['trade_date'], []).append(f"{variant['name']}:{item.get('symbol')}")
    cases = []
    for record in completed_days:
        diag = diagnostics_by_date.get(record['trade_date']) or {}
        best_pool = max(record['day'], key=lambda row: _numeric(row.get('t1_return')), default=None)
        miss_bucket = diag.get('miss_bucket') or 'MAINLINE_NOT_IN_DATA'
        action = {
            'MAINLINE_NOT_IN_DATA': 'WAIT_FOR_SAMPLE',
            'MAINLINE_NOT_IN_POOL': 'FIX_POOL_COVERAGE',
            'MAINLINE_BLOCKED_BY_GATE': 'SHADOW_REPLAY_MORE_DATES',
            'MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK': 'CONSIDER_MAINLINE_WEIGHT_AFTER_GATE',
            'PAPER_PICK_MAINLINE_HIT': 'WAIT_FOR_SAMPLE',
        }.get(miss_bucket, 'WAIT_FOR_SAMPLE')
        cases.append({
            'trade_date': record['trade_date'],
            'market_mainline_top3': diag.get('market_mainline_top3') or [],
            'market_mainline_top5': diag.get('market_mainline_top5') or [],
            'paper_pick': _mainline_candidate_summary(record['paper']),
            'paper_pick_t1_return': record['paper'].get('t1_return'),
            'best_mainline_candidate': diag.get('best_mainline_candidate'),
            'best_pool_candidate': _mainline_candidate_summary(best_pool),
            'miss_bucket': miss_bucket,
            'blockers': (diag.get('best_mainline_candidate') or {}).get('blockers') or [],
            'shadow_variant_winners': sorted(shadow_winners.get(record['trade_date'], [])),
            'recommended_next_action': action,
        })
    return {'status': 'PASS', 'case_count': len(cases), 'cases': sorted(cases, key=lambda case: case['trade_date'])}


def _mainline_next_phase_recommendation(diagnostics: Dict[str, Any], shadow_replay: Dict[str, Any]) -> str:
    if int(shadow_replay.get('sample_count') or 0) < MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        return 'WAIT_FOR_SAMPLE'
    distribution = diagnostics.get('distribution') or {}
    dominant = max(distribution, key=lambda key: distribution[key], default='MAINLINE_NOT_IN_DATA')
    return {
        'MAINLINE_NOT_IN_DATA': 'PREPARE_DATA_COVERAGE_PLAN',
        'MAINLINE_NOT_IN_POOL': 'PREPARE_CANDIDATE_POOL_MAPPING_PLAN',
        'MAINLINE_BLOCKED_BY_GATE': 'PREPARE_ELIGIBILITY_GATE_REVIEW_PLAN',
        'MAINLINE_AVAILABLE_BUT_RANKED_BELOW_PICK': 'PREPARE_PRODUCTION_RANKING_REVIEW_PLAN_AFTER_GATE',
    }.get(dominant, 'WAIT_FOR_SAMPLE')


def _shadow_ranking_replay(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    variants = [
        'baseline_current',
        'low_position_catalyst_shadow_plus',
        'limitup_gene_shadow_plus',
        'risk_penalty_shadow_plus',
        'weak_market_defensive_shadow',
        'social_catalyst_shadow',
    ]
    results = []
    baseline_values = [day['paper']['t1_return'] for day in completed_days]
    for variant in variants:
        values = baseline_values if variant == 'baseline_current' else [
            max(day['day'], key=lambda row: (_shadow_score(row, variant), str(row.get('symbol') or '')))['t1_return']
            for day in completed_days
        ]
        summary = _return_limitup_summary(values)
        results.append({
            'name': variant, 'avg_t1_return': summary['avg_return'], 'win_rate': summary['win_rate'],
            'limitup_rate': summary['limitup_rate'], 'max_drawdown': _max_drawdown(values),
            'beats_baseline': None if variant == 'baseline_current' else bool(
                values and _average(values) is not None and _average(baseline_values) is not None and _average(values) > _average(baseline_values)
            ),
        })
    replay = {
        'status': 'INSUFFICIENT_SAMPLE', 'sample_count': len(completed_days),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT, 'variants': results,
        'selected_candidate_variant': None,
        'blocking_reason': f'sample_count={len(completed_days)} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}',
    }
    if len(completed_days) < MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        return replay
    baseline = results[0]
    eligible = [
        variant for variant in results[1:]
        if variant['beats_baseline']
        and variant['win_rate'] >= baseline['win_rate']
        and variant['limitup_rate'] >= baseline['limitup_rate']
        and variant['max_drawdown'] >= baseline['max_drawdown']
    ]
    if eligible:
        winner = max(eligible, key=lambda item: (item['avg_t1_return'], item['win_rate'], item['limitup_rate']))
        replay.update({'status': 'PASS', 'selected_candidate_variant': winner['name'], 'blocking_reason': None})
    else:
        replay.update({'status': 'FAIL', 'blocking_reason': 'no_shadow_variant_beats_baseline_with_risk_guards'})
    return replay


def _limitup_gene_shadow_replay(
    completed_days: List[Dict[str, Any]], attribution: Dict[str, Any],
) -> Dict[str, Any]:
    before = [day['paper']['t1_return'] for day in completed_days]
    selected_rows = [
        max(day['day'], key=lambda row: (_shadow_score(row, 'limitup_gene_shadow_plus'), str(row.get('symbol') or '')))
        for day in completed_days
    ]
    after = [row['t1_return'] for row in selected_rows]
    before_summary = _return_limitup_summary(before)
    after_summary = _return_limitup_summary(after)
    missed_cases = [
        case for case in attribution['daily_cases']
        if 'LIMITUP_GENE_UNDERWEIGHTED' in case['miss_types']
    ]
    gene_groups: Dict[str, List[float]] = {}
    for day in completed_days:
        for row in day['day']:
            strength = str(_shadow_profile(row).get('limitup_gene_strength') or 'MISSING')
            gene_groups.setdefault(strength, []).append(_numeric(row.get('t1_return')))
    replay = {
        'status': 'INSUFFICIENT_SAMPLE', 'sample_count': len(completed_days),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'variant': 'limitup_gene_shadow_plus', 'signals_used': list(LIMITUP_GENE_SHADOW_SIGNALS),
        'avg_t1_return_before': before_summary['avg_return'], 'avg_t1_return_after': after_summary['avg_return'],
        'limitup_rate_before': before_summary['limitup_rate'], 'limitup_rate_after': after_summary['limitup_rate'],
        'large_loss_rate_before': before_summary['large_loss_rate'], 'large_loss_rate_after': after_summary['large_loss_rate'],
        'missed_cases': missed_cases, 'selected_for_production': False,
        'gene_strength_performance': {
            strength: {
                'sample_count': len(values),
                'avg_t1_return': _average(values),
                'limitup_rate': _return_limitup_summary(values)['limitup_rate'],
            }
            for strength, values in sorted(gene_groups.items())
        },
        'blocking_reason': f'sample_count={len(completed_days)} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}',
    }
    if len(completed_days) < MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        return replay
    beats_baseline = (
        after_summary['avg_return'] is not None and before_summary['avg_return'] is not None
        and after_summary['avg_return'] > before_summary['avg_return']
        and after_summary['limitup_rate'] >= before_summary['limitup_rate']
        and after_summary['large_loss_rate'] <= before_summary['large_loss_rate']
    )
    replay['status'] = 'PASS' if beats_baseline else 'FAIL'
    replay['blocking_reason'] = None if beats_baseline else 'shadow_variant_does_not_beat_baseline_with_risk_guards'
    return replay


def _limitup_capture_gate(completed_days: List[Dict[str, Any]], attribution: Dict[str, Any]) -> Dict[str, Any]:
    paper_values = [day['paper']['t1_return'] for day in completed_days]
    best_values = [max(day['day'], key=lambda row: row['t1_return'])['t1_return'] for day in completed_days]
    rank26_values = [
        row['t1_return'] for day in completed_days for row in day['day']
        if 2 <= int(row.get('rank') or 999) <= 6
    ]
    missed_limitup = 0
    missed_near_limitup = 0
    false_positive_high_risk = 0
    for day in completed_days:
        paper = day['paper']
        alternatives = [row for row in day['day'] if str(row.get('symbol') or '') != str(paper.get('symbol') or '')]
        limitups = [row for row in alternatives if row['t1_return'] >= 0.095]
        near_limitups = [row for row in alternatives if row['t1_return'] >= 0.07]
        if limitups and paper['t1_return'] < 0.095:
            missed_limitup += 1
        if near_limitups and paper['t1_return'] < 0.07:
            missed_near_limitup += 1
        for row in limitups:
            risk = _capital_risk(row)
            if any(_numeric(risk.get(key)) > 0 for key in ('failed_limitup_risk', 'main_buy_outflow_pressure', 'high_popularity_trap_risk')):
                false_positive_high_risk += 1
    distribution = attribution['miss_type_distribution']
    primary_blocker = next(iter(distribution), None) if distribution else None
    gate = {
        'status': 'INSUFFICIENT_SAMPLE', 'sample_count': len(completed_days),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'paper_pick_limitup_rate': _return_limitup_summary(paper_values)['limitup_rate'],
        'top10_best_limitup_rate': _return_limitup_summary(best_values)['limitup_rate'],
        'rank2_to_rank6_limitup_rate': _return_limitup_summary(rank26_values)['limitup_rate'],
        'missed_limitup_count': missed_limitup, 'missed_near_limitup_count': missed_near_limitup,
        'false_positive_high_risk_count': false_positive_high_risk,
        'primary_blocker': primary_blocker,
    }
    if len(completed_days) >= MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        gate['status'] = 'PASS' if missed_limitup == 0 else 'FAIL'
    return gate


def _positive_signal_names(row: Dict[str, Any]) -> List[str]:
    gene = _limitup_gene_components(row)
    labels = ['LIMITUP_GENE' for _ in gene['signals']]
    if _numeric(_candidate_signal(row, 'sector_catalyst_score')) > 0 or _numeric(_candidate_signal(row, 'sector_heat_score')) > 0:
        labels.append('SECTOR_HEAT')
    if _numeric(_candidate_signal(row, 'low_position_catalyst_score')) > 0:
        labels.append('LOW_POSITION_CATALYST')
    return sorted(set(labels))


def _paper_pick_case_book(completed_days: List[Dict[str, Any]], attribution: Dict[str, Any]) -> Dict[str, Any]:
    attribution_by_date = {case['trade_date']: case for case in attribution['daily_cases']}
    cases = []
    for record in completed_days:
        case = attribution_by_date.get(record['trade_date'])
        if case is None:
            continue
        paper = record['paper']
        alternative = max(record['day'], key=lambda row: row['t1_return'])
        miss_types = case['miss_types']
        has_actionable_evidence = miss_types != ['NO_ACTION_INSUFFICIENT_EVIDENCE'] and not _risk_flag_names(alternative)
        cases.append({
            'trade_date': record['trade_date'],
            'paper_pick': {
                'symbol': paper.get('symbol'), 'rank': paper.get('rank'), 't1_return': paper['t1_return'],
                'limitup_hit': paper['t1_return'] >= 0.095, 'risk_flags': _risk_flag_names(paper),
            },
            'best_alternative': {
                'symbol': alternative.get('symbol'), 'rank': alternative.get('rank'), 't1_return': alternative['t1_return'],
                'limitup_hit': alternative['t1_return'] >= 0.095,
                'positive_signals': _positive_signal_names(alternative), 'risk_flags': _risk_flag_names(alternative),
            },
            'decision_gap': {
                'return_gap': case['return_gap'], 'miss_types': miss_types,
                'actionability': 'SHADOW_REPLAY_ONLY' if has_actionable_evidence else 'NO_ACTION',
            },
        })
    return {'status': 'PASS', 'case_count': len(cases), 'cases': sorted(cases, key=lambda case: case['trade_date'])}


def _paper_pick_vs_pool_diagnostic(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    daily = []
    for record in completed_days:
        paper = record['paper']
        alternatives = [
            row for row in record['day']
            if str(row.get('symbol') or '') != str(paper.get('symbol') or '')
            and _numeric(row.get('t1_return')) > _numeric(paper.get('t1_return'))
        ]
        rank13 = [
            _numeric(row.get('t1_return')) for row in record['day']
            if 1 <= int(row.get('rank') or 999) <= 3
        ]
        rank46 = [
            _numeric(row.get('t1_return')) for row in record['day']
            if 4 <= int(row.get('rank') or 999) <= 6
        ]
        diagnosis = []
        if alternatives:
            diagnosis.append('paper_pick_not_superior_to_pool')
        if _average(rank46) is not None and _average(rank13) is not None and _average(rank46) > _average(rank13):
            diagnosis.append('rank_4_to_6_more_stable')
        if any(_numeric(_shadow_profile(row).get('defensive_carry_score')) >= 0.50 for row in alternatives):
            diagnosis.append('weak_market_defensive_candidates_outperformed')
        daily.append({
            'trade_date': record['trade_date'],
            'paper_pick': str(paper.get('symbol') or ''),
            'paper_pick_rank': paper.get('rank'),
            'paper_pick_t1_return': paper.get('t1_return'),
            'better_t1_candidates': [str(row.get('symbol') or '') for row in alternatives],
            'rank_1_to_3_avg_t1': _average(rank13),
            'rank_4_to_6_avg_t1': _average(rank46),
            'paper_pick_outperformance': _average([
                _numeric(paper.get('t1_return')) - _numeric(row.get('t1_return'))
                for row in alternatives
            ]) if alternatives else 0.0,
            'diagnosis': diagnosis or ['paper_pick_not_outperformed_by_measured_pool'],
        })
    return {
        'status': 'PASS',
        'sample_count': len(completed_days),
        'actionability': 'SHADOW_REPLAY_ONLY',
        'daily': daily,
    }


def _sell_strategy_gate(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    close_values = [day['paper']['t1_return'] for day in completed_days]
    open_values = [day['paper'].get('next_day_open_return') for day in completed_days if day['paper'].get('next_day_open_return') is not None]
    high_rows = [day['paper'] for day in completed_days if day['paper'].get('next_day_high_return') is not None]
    take_profit_values = [
        min(
            row['next_day_high_return'] * 0.70,
            row['next_day_high_return'] + min(0.0, _numeric(row.get('high_to_close_retrace'))) * 0.50,
        )
        for row in high_rows
    ]
    drawdown_values = []
    for row in (day['paper'] for day in completed_days):
        if row.get('next_day_low_return') is None:
            continue
        if _numeric(row['next_day_low_return']) <= -0.05 and _numeric(row.get('next_day_high_return')) <= 0.015:
            drawdown_values.append(max(_numeric(row['next_day_low_return']) * 0.65, -0.03))
        else:
            drawdown_values.append(row['t1_return'])
    missing_fields = sorted({
        field for field in ('next_day_open_return', 'next_day_high_return', 'next_day_low_return', 'high_to_close_retrace')
        if any(day['paper'].get(field) is None for day in completed_days)
    })
    def strategy(values: List[float]) -> Dict[str, Any]:
        return {
            'sample_count': len(values), 'avg_return': _average(values),
            'win_rate': _return_limitup_summary(values)['win_rate'],
            'large_loss_rate': _return_limitup_summary(values)['large_loss_rate'],
        }
    strategies = {
        'close': strategy(close_values), 'next_open': strategy(open_values),
        'take_profit_intraday': {**strategy(take_profit_values), 'rule': 'next_day_high_return * 0.70 with retrace guard'},
        'drawdown_guard': {**strategy(drawdown_values), 'rule': 'low <= -5% uses conservative stop; otherwise close'},
    }
    gate = {
        'status': 'INSUFFICIENT_SAMPLE', 'sample_count': len(completed_days),
        'minimum_sample_count': MINIMUM_PERFORMANCE_SAMPLE_COUNT, 'strategies': strategies,
        'recommended_sell_strategy': None, 'missing_execution_fields': missing_fields,
        'blocking_reason': f'sample_count={len(completed_days)} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}',
    }
    if len(completed_days) >= MINIMUM_PERFORMANCE_SAMPLE_COUNT and not missing_fields:
        eligible = [
            (name, metrics) for name, metrics in strategies.items()
            if metrics['avg_return'] is not None
            and metrics['avg_return'] >= strategies['close']['avg_return']
            and metrics['large_loss_rate'] <= strategies['close']['large_loss_rate']
        ]
        if eligible:
            winner = max(eligible, key=lambda item: item[1]['avg_return'])
            gate.update({'status': 'PASS', 'recommended_sell_strategy': winner[0], 'blocking_reason': None})
        else:
            gate.update({'status': 'FAIL', 'blocking_reason': 'no_sell_strategy_beats_close_with_loss_guard'})
    elif len(completed_days) >= MINIMUM_PERFORMANCE_SAMPLE_COUNT:
        gate.update({'status': 'WARN', 'blocking_reason': 'missing_execution_fields:' + ','.join(missing_fields)})
    return gate


def _sell_strategy_execution_gate(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    gate = _sell_strategy_gate(completed_days)
    strategies = gate['strategies']
    return {
        'status': gate['status'], 'sample_count': gate['sample_count'],
        'minimum_sample_count': gate['minimum_sample_count'],
        'strategies': {
            'next_open': strategies['next_open'], 'close': strategies['close'],
            'conservative_intraday_take_profit': strategies['take_profit_intraday'],
            'drawdown_guard': strategies['drawdown_guard'],
        },
        'execution_assumptions': {
            'intraday_high_capture_max_ratio': 0.70,
            'requires_retrace_guard': True,
            'forbid_high_price_as_certain_fill': True,
        },
        'recommended_sell_strategy': gate['recommended_sell_strategy'],
        'missing_execution_fields': gate['missing_execution_fields'],
        'blocking_reason': gate['blocking_reason'],
    }


def _sell_strategy_replay(completed_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare explicit T+1 execution proxies without changing T-day decisions."""
    rows = [day['paper'] for day in completed_days]

    def close(row: Dict[str, Any]) -> float:
        return _numeric(row.get('t1_return'))

    def open_stop_loss(row: Dict[str, Any]) -> float:
        if _numeric(row.get('next_day_open_return')) <= -0.02 and _numeric(row.get('next_day_high_return')) <= 0.0:
            return max(_numeric(row.get('next_day_open_return')), -0.03)
        return close(row)

    def take_profit(row: Dict[str, Any], threshold: float) -> float:
        high = _numeric(row.get('next_day_high_return'))
        return threshold if high >= threshold else close(row)

    def retrace_stop(row: Dict[str, Any]) -> float:
        high = _numeric(row.get('next_day_high_return'))
        close_value = close(row)
        if high - close_value >= 0.03:
            return min(high, high * 0.70)
        return close_value

    strategies = {
        'hold_to_close': [close(row) for row in rows],
        'open_stop_loss': [open_stop_loss(row) for row in rows],
        'take_profit_2pct': [take_profit(row, 0.02) for row in rows],
        'take_profit_3pct': [take_profit(row, 0.03) for row in rows],
        'take_profit_5pct': [take_profit(row, 0.05) for row in rows],
        'high_to_close_retrace_stop': [retrace_stop(row) for row in rows],
    }
    metrics = {
        name: {
            'avg_return': _average(values),
            'win_rate': _return_limitup_summary(values)['win_rate'],
            'sample_count': len(values),
        }
        for name, values in strategies.items()
    }
    best_rule = max(
        (name for name, values in strategies.items() if values),
        key=lambda name: (metrics[name]['avg_return'], metrics[name]['win_rate'], name),
        default=None,
    )
    return {
        'sample_count': len(rows),
        'hold_to_close_avg': metrics['hold_to_close']['avg_return'],
        'take_profit_2pct_avg': metrics['take_profit_2pct']['avg_return'],
        'take_profit_3pct_avg': metrics['take_profit_3pct']['avg_return'],
        'take_profit_5pct_avg': metrics['take_profit_5pct']['avg_return'],
        'open_stop_loss_avg': metrics['open_stop_loss']['avg_return'],
        'high_to_close_retrace_stop_avg': metrics['high_to_close_retrace_stop']['avg_return'],
        'best_rule': best_rule,
        'strategies': metrics,
        'run_mode': 'T1_POST_HOC_REPLAY_ONLY',
        'optimistic_upper_bound_rules': [
            'take_profit_2pct', 'take_profit_3pct', 'take_profit_5pct',
            'high_to_close_retrace_stop',
        ],
        'warning': 'OHLC high-based exits are optimistic upper-bound estimates, not fill certainty.',
    }


def build_daily_system_gate(
    report: Dict[str, Any], *, scan_completed: bool, paper_pick_written: bool,
    return_backfill_completed: bool, backfill_failure_reasons: Optional[Dict[str, int]] = None,
    trade_date: Optional[str] = None, run_mode: str = 'MANUAL_COHORT_REPORT', backfill_fatal: bool = False,
) -> Dict[str, Any]:
    missing_report_gates = [
        name for name in (
            'strategy_readiness', 'paper_pick_performance_gate', 'limitup_capture_gate',
            'sell_strategy_gate', 'sell_strategy_execution_gate', 'shadow_ranking_replay',
            'limitup_gene_shadow_replay', 'paper_pick_case_book', 'sample_accumulation_gate',
            'production_ranking_change_gate', 'sell_strategy_replay',
            'paper_pick_vs_pool_diagnostic', 'mainline_diagnostic_gate',
            'mainline_pool_coverage', 'mainline_shadow_replay', 'mainline_case_book',
        )
        if name not in report
    ]
    blocked_by = []
    if not scan_completed and run_mode != 'T1_VALIDATION':
        blocked_by.append('scan_not_completed')
    if not paper_pick_written:
        blocked_by.append('paper_pick_not_written')
    if not return_backfill_completed:
        blocked_by.append('return_backfill_not_completed')
    if backfill_fatal:
        blocked_by.append('return_backfill_fatal_error')
    if missing_report_gates:
        blocked_by.append('cohort_report_missing:' + ','.join(missing_report_gates))
    nonfatal_failures = {
        reason: count for reason, count in (backfill_failure_reasons or {}).items()
        if count and reason in ('NO_TRADING_DATA', 'SYMBOL_FORMAT_ERROR')
    }
    if run_mode == 'MANUAL_COHORT_REPORT':
        status = 'NOT_LIVE_RUN'
    elif not blocked_by and not nonfatal_failures:
        status = 'PASS'
    elif 'scan_not_completed' in blocked_by or 'paper_pick_not_written' in blocked_by or missing_report_gates or backfill_fatal:
        status = 'FAIL'
    else:
        status = 'WARN'
    return {
        'status': status, 'scan_completed': scan_completed, 'paper_pick_written': paper_pick_written,
        'return_backfill_completed': return_backfill_completed,
        'cohort_report_generated': not missing_report_gates,
        'strategy_readiness_status': (report.get('strategy_readiness') or {}).get('status'),
        'blocked_by': blocked_by,
        'return_backfill_failure_reasons': dict(sorted((backfill_failure_reasons or {}).items())),
        'nonfatal_backfill_failures': dict(sorted(nonfatal_failures.items())),
        'run_mode': run_mode, 'trade_date': trade_date,
    }


def _sample_accumulation_gate(
    sample_count: int, *, completed_trade_dates: Optional[List[str]] = None,
    pending_trade_dates: Optional[List[str]] = None, non_mainboard_trade_dates: Optional[List[str]] = None,
    no_t1_return_trade_dates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if sample_count < 10:
        status, action, next_unlock = 'WAITING', 'continue daily PAPER_PICK and return backfill', 'READY_FOR_SHADOW_SELECTION'
    elif sample_count < 20:
        status, action, next_unlock = 'READY_FOR_SHADOW_SELECTION', 'review shadow replay candidates only', 'READY_FOR_STRATEGY_REPLAY'
    elif sample_count < 30:
        status, action, next_unlock = 'READY_FOR_STRATEGY_REPLAY', 'evaluate strategy replay across market regimes', 'READY_FOR_FREEZE_REVIEW'
    else:
        status, action, next_unlock = 'READY_FOR_FREEZE_REVIEW', 'start PAPER_PICK freeze review with replay evidence', None
    return {
        'mainboard_comparable_paper_pick_dates': sample_count,
        'minimum_required': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'remaining_dates': max(0, MINIMUM_PERFORMANCE_SAMPLE_COUNT - sample_count),
        'status': status, 'next_unlock': next_unlock, 'next_action': action,
        'completed_trade_dates': sorted(set(completed_trade_dates or [])),
        'latest_completed_trade_date': max(completed_trade_dates or [], default=None),
        'pending_trade_dates': sorted(set(pending_trade_dates or [])),
        'non_mainboard_paper_pick_dates': sorted(set(non_mainboard_trade_dates or [])),
        'no_t1_return_paper_pick_dates': sorted(set(no_t1_return_trade_dates or [])),
    }


def _production_ranking_change_gate(
    *, sample_gate: Dict[str, Any], shadow_replay: Dict[str, Any],
    full_pytest_gate_status: str, return_coverage_gate_status: str,
    full_chain_ready_days: int, market_regime_count: int = 0,
) -> Dict[str, Any]:
    sample_count = sample_gate['mainboard_comparable_paper_pick_dates']
    requirements = {
        'minimum_comparable_dates': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'shadow_variant_beats_baseline': shadow_replay.get('status') == 'PASS',
        'large_loss_rate_not_worse': shadow_replay.get('status') == 'PASS',
        'limitup_rate_not_worse': shadow_replay.get('status') == 'PASS',
        'full_pytest_gate': full_pytest_gate_status,
        'return_coverage_gate': return_coverage_gate_status,
    }
    status = 'LOCKED'
    if full_pytest_gate_status == 'PASS' and return_coverage_gate_status == 'PASS' and sample_count >= 10 and shadow_replay.get('status') == 'PASS':
        status = 'READY_FOR_PROPOSAL'
        if sample_count >= 20 and market_regime_count >= 2:
            status = 'READY_FOR_SMALL_STEP_CHANGE'
    if sample_count < 10:
        reason = f'mainboard_comparable_paper_pick_dates={sample_count} < {MINIMUM_PERFORMANCE_SAMPLE_COUNT}'
    elif full_pytest_gate_status != 'PASS' or return_coverage_gate_status != 'PASS':
        reason = 'quality_gates_not_passed'
    elif shadow_replay.get('status') != 'PASS':
        reason = 'shadow_variant_not_verified'
    elif status == 'READY_FOR_PROPOSAL':
        reason = 'minimum_sample_and_shadow_requirements_passed'
    else:
        reason = 'cross_regime_shadow_requirements_passed'
    return {
        'status': status, 'reason': reason, 'requirements': requirements,
        'full_chain_ready_days': full_chain_ready_days, 'market_regime_count': market_regime_count,
        'allowed_actions': ['diagnose', 'shadow_replay', 'case_book'],
        'forbidden_actions': [
            'change_formal_candidate_sort_key', 'change_production_ranking_weights', 'freeze_paper_pick',
        ],
    }


def build_daily_closure(
    trade_date: str, report: Dict[str, Any], backfill_stats: Dict[str, Any], *,
    scan_completed: bool, paper_pick_written: bool, run_mode: str = 'LIVE_DAILY_PIPELINE',
    return_backfill_completed: Optional[bool] = None,
) -> Dict[str, Any]:
    failure_reasons = backfill_stats.get('failure_reasons') or {}
    timeout_count = sum(count for reason, count in failure_reasons.items() if 'TIMEOUT' in reason)
    daily_system_gate = build_daily_system_gate(
        report, scan_completed=scan_completed, paper_pick_written=paper_pick_written,
        return_backfill_completed=(
            not bool(backfill_stats.get('fatal_error'))
            if return_backfill_completed is None else return_backfill_completed
        ),
        backfill_failure_reasons=failure_reasons, backfill_fatal=bool(backfill_stats.get('fatal_error')),
        trade_date=trade_date, run_mode=run_mode,
    )
    cohort_gate_names = (
        'return_coverage_gate', 'strategy_readiness', 'paper_pick_performance_gate',
        'limitup_capture_gate', 'sell_strategy_gate', 'sell_strategy_execution_gate',
        'shadow_ranking_replay', 'limitup_gene_shadow_replay', 'sample_accumulation_gate',
        'production_ranking_change_gate', 'paper_pick_case_book',
        'sell_strategy_replay', 'sample_count_reconciliation', 'paper_pick_vs_pool_diagnostic',
        'mainline_diagnostic_gate', 'mainline_pool_coverage', 'mainline_shadow_replay',
        'mainline_case_book',
    )
    strategy_readiness = dict(report.get('strategy_readiness') or {})
    return_validation_status = 'PASS' if return_backfill_completed else 'PENDING'
    return {
        'trade_date': trade_date, 'run_mode': run_mode,
        'return_backfill': {
            'new_success_count': backfill_stats.get('new_success_count', backfill_stats.get('fetched', 0)),
            'new_failure_count': backfill_stats.get('new_failure_count', backfill_stats.get('fetch_failed', 0)),
            'skipped_existing_success_count': backfill_stats.get('skipped_existing_success_count', 0),
            'timeout_count': timeout_count, 'failure_reason_distribution': dict(sorted(failure_reasons.items())),
        },
        'cohort_gates': {name: report.get(name) for name in cohort_gate_names},
        'daily_system_gate': daily_system_gate,
        'operational_gate': {
            'status': daily_system_gate['status'],
            'checks': daily_system_gate,
        },
        'data_completeness_gate': {
            'status': 'PENDING',
            'reason': 'db_completeness_gate_not_attached_for_this_run',
        },
        'return_validation_gate': {
            'status': return_validation_status,
            'reason': None if return_validation_status == 'PASS' else 't1_validation_pending',
        },
        'strategy_readiness_gate': {
            'status': strategy_readiness.get('status', 'LOCKED'),
            'sample_count': strategy_readiness.get('sample_count', 0),
            'blocking_reasons': strategy_readiness.get('blocking_reasons', []),
        },
        'production_ranking_change_gate': report.get('production_ranking_change_gate'),
        'paper_pick_performance_gate': report.get('paper_pick_performance_gate'),
        'sample_count_reconciliation': report.get('sample_count_reconciliation'),
        'sell_strategy_replay': report.get('sell_strategy_replay'),
        'social_signal_gate': {
            'status': 'WARN',
            'reason': 'SOCIAL_SIGNAL_NOT_ATTACHED_FOR_THIS_RUN',
            'used_for_official_ranking': False,
        },
    }


def write_daily_closure(closure: Dict[str, Any], output_path: Optional[Path] = None) -> Path:
    path = output_path or (BASE / 'summary' / 'daily_closure_latest.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(closure, ensure_ascii=False, indent=2, default=str) + '\n')
    return path


def _snapshot_has_decision_evidence(row: Dict[str, Any]) -> bool:
    return all(_nonempty(row.get(key)) for key in (
        'candidate_entry_reason', 'factor_snapshot', 'auxiliary_evidence_snapshot',
        'ranking_basis', 'source_layers', 'candidate_features',
    ))


def _candidate_pool_context(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in candidates:
        features = row.get('candidate_features')
        if isinstance(features, dict) and isinstance(features.get('candidate_pool_context'), dict):
            return dict(features['candidate_pool_context'])
        raw = row.get('raw_json')
        if isinstance(raw, dict) and isinstance(raw.get('candidate_pool_context'), dict):
            return dict(raw['candidate_pool_context'])
    return {}


def build_db_completeness_gate(
    trade_date: str, *, mode: str, validation_trade_date: Optional[str] = None,
    candidate_rows: Optional[List[Dict[str, Any]]] = None,
    pick_rows: Optional[List[Dict[str, Any]]] = None,
    return_rows: Optional[List[Dict[str, Any]]] = None,
    signal_rows: Optional[List[Dict[str, Any]]] = None,
    scan_session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate whether a persisted decision can support live and replay evidence."""
    parsed_trade_date = date.fromisoformat(trade_date)
    if candidate_rows is None:
        candidate_rows = fetch_daily_candidates(parsed_trade_date) if fetch_daily_candidates else []
    if pick_rows is None:
        pick_rows = fetch_picks(parsed_trade_date) if fetch_picks else []
    if return_rows is None:
        return_rows = fetch_returns(parsed_trade_date) if fetch_returns else []
    if signal_rows is None:
        signal_rows = fetch_signals(parsed_trade_date) if fetch_signals else []
    if scan_session is None and fetch_latest_scan_session:
        scan_session = fetch_latest_scan_session(parsed_trade_date)

    candidates = list(candidate_rows or [])
    picks = list(pick_rows or [])
    returns = list(return_rows or [])
    signals = list(signal_rows or [])
    top10 = [row for row in candidates if 1 <= int(row.get('rank') or 999999) <= 10]
    paper_picks = [row for row in picks if str(row.get('decision') or '').upper() == 'PAPER_PICK']
    paper_pick = max(paper_picks, key=lambda row: (_numeric(row.get('final_score')), str(row.get('symbol') or '')), default=None)
    return_by_symbol = {str(row.get('symbol') or ''): row for row in returns}
    paper_t1_available = bool(paper_pick and return_by_symbol.get(str(paper_pick.get('symbol') or ''), {}).get('t1_return') is not None)
    top10_t1_coverage = (
        round(sum(return_by_symbol.get(str(row.get('symbol') or ''), {}).get('t1_return') is not None for row in top10) / len(top10), 4)
        if top10 else None
    )

    def horizon_return(row: Dict[str, Any], horizon: str) -> Optional[float]:
        value = row.get(f'{horizon}_return')
        if value is None and horizon == 't1':
            value = row.get('t1_return_close')
        return value

    horizon_counts = {
        horizon: sum(horizon_return(row, horizon) is not None for row in returns)
        for horizon in ('t1', 't2', 't3')
    }
    horizon_positive_counts = {
        horizon: sum((horizon_return(row, horizon) or 0) > 0 for row in returns if horizon_return(row, horizon) is not None)
        for horizon in ('t1', 't2', 't3')
    }
    t1_non_positive_rows = [
        row for row in returns
        if horizon_return(row, 't1') is not None and (horizon_return(row, 't1') or 0) <= 0
    ]
    late_bloom_count = sum((horizon_return(row, 't2') or 0) > 0 for row in t1_non_positive_rows)
    paper_pick_return = return_by_symbol.get(str(paper_pick.get('symbol') or ''), {}) if paper_pick else {}
    paper_pick_best_available_horizon = None
    for horizon in ('t3', 't2', 't1'):
        value = horizon_return(paper_pick_return, horizon)
        if value is not None:
            paper_pick_best_available_horizon = {'horizon': horizon.upper(), 'return': value}
            break
    horizon_summary = {
        'coverage_counts': horizon_counts,
        'positive_counts': horizon_positive_counts,
        'late_bloom_count': late_bloom_count,
        'late_bloom_rate': round(late_bloom_count / len(t1_non_positive_rows), 4) if t1_non_positive_rows else None,
        'paper_pick_best_available_horizon': paper_pick_best_available_horizon,
        'official_metric': 'T1_VALIDATION',
        'diagnostic_only': True,
    }
    persisted_gene_keys = {
        (str(row.get('symbol') or ''), str(row.get('signal_key') or ''))
        for row in signals if str(row.get('signal_key') or '') in LIMITUP_GENE_SHADOW_SIGNALS
    }
    expected_gene_rows = len(candidates) * len(LIMITUP_GENE_SHADOW_SIGNALS)
    persisted_gene_rows = sum(
        (str(candidate.get('symbol') or ''), signal) in persisted_gene_keys
        for candidate in candidates for signal in LIMITUP_GENE_SHADOW_SIGNALS
    )
    future_violations = _future_field_violations(candidates)
    pool_context = _candidate_pool_context(candidates)
    candidate_count_expected = int(pool_context.get('target_count') or 200)
    source_status = str(pool_context.get('source_status') or '').upper()
    tradable_count = pool_context.get('mainboard_tradable_count')
    exclusion_summary = dict(pool_context.get('top_exclusion_reasons') or {})
    try:
        duplicate_symbol_count = int(pool_context.get('duplicate_symbol_count') or 0)
    except (TypeError, ValueError):
        duplicate_symbol_count = 0
    duplicate_symbols = list(pool_context.get('duplicate_symbols') or [])
    deduplication_applied = bool(pool_context.get('deduplication_applied')) or duplicate_symbol_count > 0
    legacy_partial_pool = bool(pool_context.get('legacy_partial_pool')) or (
        bool(candidates)
        and not pool_context
        and len(candidates) < candidate_count_expected
    )
    if len(candidates) >= candidate_count_expected:
        candidate_pool_status = 'PASS'
        candidate_pool_warning_reason = None
    elif legacy_partial_pool:
        candidate_pool_status = 'WARN'
        candidate_pool_warning_reason = 'legacy_partial_pool'
    elif source_status == 'FAIL':
        candidate_pool_status = 'FAIL'
        candidate_pool_warning_reason = 'scanner_source_unavailable'
    elif deduplication_applied and duplicate_symbol_count > 0:
        candidate_pool_status = 'WARN'
        candidate_pool_warning_reason = 'candidate_pool_source_duplicates_collapsed'
    elif isinstance(tradable_count, (int, float)) and tradable_count < candidate_count_expected:
        candidate_pool_status = 'WARN'
        candidate_pool_warning_reason = 'eligible_mainboard_tradable_count_below_target'
    else:
        candidate_pool_status = 'WARN'
        candidate_pool_warning_reason = 'candidate_pool_partial_without_source_explanation'
    checks = {
        'scan_session_persisted': bool(scan_session),
        'candidate_snapshot_persisted': bool(candidates),
        'candidate_count': len(candidates),
        'candidate_count_expected': candidate_count_expected,
        'candidate_pool_status': candidate_pool_status,
        'top10_count': len(top10),
        'paper_pick_persisted': paper_pick is not None,
        'paper_pick_mainboard': bool(paper_pick and is_mainboard_symbol and is_mainboard_symbol(str(paper_pick.get('symbol') or ''))),
        'decision_evidence_persisted': bool(top10) and all(_snapshot_has_decision_evidence(row) for row in top10),
        'ranking_basis_persisted': bool(top10) and all(_nonempty(row.get('ranking_basis')) for row in top10),
        'limitup_gene_signals_persisted': bool(candidates) and persisted_gene_rows == expected_gene_rows,
        'future_fields_absent_from_decision_snapshot': not future_violations,
        't1_return_available': paper_t1_available,
    }
    missing: List[str] = []
    warnings: List[str] = []
    fatal_checks = ('scan_session_persisted', 'candidate_snapshot_persisted', 'paper_pick_persisted',
                    'limitup_gene_signals_persisted', 'future_fields_absent_from_decision_snapshot')
    if mode == 'LIVE_DECISION_DAY':
        missing.extend(name for name in fatal_checks if not checks[name])
        if candidate_pool_status != 'PASS':
            warnings.append('candidate_pool_completeness=' + str(candidate_pool_warning_reason))
        if not checks['decision_evidence_persisted']:
            missing.append('decision_evidence_persisted')
        if not checks['ranking_basis_persisted']:
            missing.append('ranking_basis_persisted')
        if not checks['t1_return_available']:
            warnings.append('t1_return_pending_until_next_trade_date')
    elif mode == 'T1_VALIDATION':
        missing.extend(name for name in (
            'candidate_snapshot_persisted', 'paper_pick_persisted',
            'future_fields_absent_from_decision_snapshot',
        ) if not checks[name])
        if not checks['t1_return_available']:
            missing.append('paper_pick_t1_return')
        if validation_trade_date is None or validation_trade_date <= trade_date:
            missing.append('validation_trade_date_mismatch')
        if not checks['scan_session_persisted']:
            warnings.append('scan_session_not_persisted_for_legacy_snapshot')
        if not checks['limitup_gene_signals_persisted']:
            warnings.append('signal_not_persisted')
        if top10_t1_coverage != 1.0:
            warnings.append('top10_t1_coverage_incomplete')
    else:
        if not checks['candidate_snapshot_persisted']:
            missing.append('candidate_snapshot_persisted')
        if not checks['paper_pick_persisted']:
            missing.append('paper_pick_persisted')
        if not checks['future_fields_absent_from_decision_snapshot']:
            missing.append('future_fields_absent_from_decision_snapshot')
        if not checks['limitup_gene_signals_persisted']:
            warnings.append('signal_not_persisted')
        if not checks['t1_return_available']:
            warnings.append('missing_t1_return')
    status = 'FAIL' if missing else ('WARN' if warnings else 'PASS')
    return {
        'status': status, 'trade_date': trade_date, 'mode': mode, 'checks': checks,
        'missing': sorted(set(missing)), 'warnings': sorted(set(warnings)),
        'candidate_count': len(candidates),
        'candidate_count_expected': candidate_count_expected,
        'candidate_pool_status': candidate_pool_status,
        'candidate_pool_warning_reason': candidate_pool_warning_reason,
        'candidate_pool_exclusion_summary': exclusion_summary,
        'horizon_summary': horizon_summary,
        'candidate_pool_completeness': {
            'status': candidate_pool_status,
            'expected': candidate_count_expected, 'actual': len(candidates),
            'reason': candidate_pool_warning_reason,
            'raw_universe_count': pool_context.get('raw_universe_count'),
            'mainboard_tradable_count': tradable_count,
            'source_row_count': pool_context.get('source_row_count'),
            'raw_full_candidate_pool_rows': pool_context.get('raw_full_candidate_pool_rows'),
            'unique_full_candidate_pool_symbols': pool_context.get('unique_full_candidate_pool_symbols'),
            'duplicate_symbol_count': duplicate_symbol_count,
            'duplicate_symbols': duplicate_symbols,
            'deduplication_applied': deduplication_applied,
            'final_persisted_count': pool_context.get('final_persisted_count', len(candidates)),
            'top_exclusion_reasons': exclusion_summary,
        },
        'db_completeness_summary': {
            'trade_date': trade_date, 'candidate_count': len(candidates), 'top10_count': len(top10),
            'paper_pick_count': len(paper_picks), 'persisted_limitup_gene_signal_rows': persisted_gene_rows,
            'expected_limitup_gene_signal_rows': expected_gene_rows,
            'paper_pick_t1_available': paper_t1_available,
            'top10_t1_coverage': top10_t1_coverage if mode != 'LIVE_DECISION_DAY' else None,
            'signal_persistence_scope': 'FULL_CANDIDATE_POOL',
            'candidate_pool_status': candidate_pool_status,
            'candidate_pool_warning_reason': candidate_pool_warning_reason,
            'candidate_pool_exclusion_summary': exclusion_summary,
            'candidate_pool_duplicate_symbol_count': duplicate_symbol_count,
            'candidate_pool_duplicate_symbols': duplicate_symbols,
            'horizon_summary': horizon_summary,
            'status': status,
        },
    }


HISTORICAL_REPLAY_FORBIDDEN_DECISION_FIELDS = (
    't1_return', 'next_day_high_return', 'next_day_limit_touch',
    'validation_trade_date_return',
)


def _future_field_violations(payload: Any, *, path: str = '') -> List[str]:
    if isinstance(payload, dict):
        violations = []
        for key, value in payload.items():
            child_path = f'{path}.{key}' if path else key
            if key == 'future_return_fields_placeholder':
                continue
            if key in HISTORICAL_REPLAY_FORBIDDEN_DECISION_FIELDS and value is not None:
                violations.append(child_path)
            violations.extend(_future_field_violations(value, path=child_path))
        return violations
    if isinstance(payload, list):
        return [
            violation for index, value in enumerate(payload)
            for violation in _future_field_violations(value, path=f'{path}[{index}]')
        ]
    return []


def _sanitize_decision_snapshot_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            key: value for key, value in row.items()
            if key != 'future_return_fields_placeholder'
        }
        for row in rows
    ]


def _historical_replay_leakage_gate(
    decision_rows: List[Dict[str, Any]], decision_picks: List[Dict[str, Any]],
    input_trade_date: str, validation_trade_date: str,
) -> Dict[str, Any]:
    violations = sorted(set(
        _future_field_violations({'candidates': decision_rows, 'picks': decision_picks})
    ))
    return {
        'status': 'PASS' if not violations else 'FAIL',
        'input_trade_date': input_trade_date, 'validation_trade_date': validation_trade_date,
        'decision_data_cutoff': input_trade_date,
        'forbidden_decision_fields': list(HISTORICAL_REPLAY_FORBIDDEN_DECISION_FIELDS),
        'violations': violations,
    }


def _shadow_replay_leakage_gate(decision_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    variants = ('low_position_catalyst_shadow_plus', 'limitup_gene_shadow_plus', 'risk_penalty_shadow_plus')
    violations = []
    for index, row in enumerate(decision_rows):
        for variant in variants:
            baseline = _shadow_score(row, variant)
            injected = {
                **row,
                't1_return': 0.20,
                'next_day_high_return': 0.20,
                'next_day_limit_touch': True,
            }
            if _shadow_score(injected, variant) != baseline:
                violations.append(f'row[{index}].{variant}')
    return {
        'status': 'PASS' if not violations else 'FAIL',
        'checked_variants': list(variants),
        'forbidden_fields': list(HISTORICAL_REPLAY_FORBIDDEN_DECISION_FIELDS[:3]),
        'violations': violations,
    }


def _historical_replay_sample_update(
    before_sample_count: int, *, input_trade_date: str, validation_trade_date: str,
    has_paper_pick: bool, paper_pick_is_mainboard: bool, has_t1_return: bool,
    already_counted: bool, paper_pick_candidate_available: bool = True,
) -> Dict[str, Any]:
    if not has_paper_pick:
        reason = 'no_paper_pick'
    elif not paper_pick_candidate_available:
        reason = 'candidate_snapshot_incomplete'
    elif not paper_pick_is_mainboard:
        reason = 'paper_pick_not_mainboard'
    elif not has_t1_return:
        reason = 'missing_t1_return'
    elif already_counted:
        reason = 'already_counted_comparable_sample'
    else:
        reason = None
    changed = reason is None
    return {
        'before_sample_count': before_sample_count,
        'after_sample_count': before_sample_count + int(changed),
        'new_completed_trade_date': input_trade_date if changed else None,
        'validation_trade_date': validation_trade_date,
        'sample_count_changed': changed,
        'reason': reason,
    }


def _limitup_gene_signal_audit(
    completed_days: List[Dict[str, Any]], attribution: Dict[str, Any], *, run_mode: str,
    candidate_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    missed_cases = [
        case for case in attribution['daily_cases']
        if case['top10_best_t1_return'] >= 0.095 and case['paper_pick_t1_return'] < 0.095
    ]
    audit_rows = candidate_rows if candidate_rows is not None else [row for day in completed_days for row in day['day']]
    expected = len(audit_rows) * len(LIMITUP_GENE_SHADOW_SIGNALS)
    persisted = sum(
        _candidate_signal(row, signal) is not None
        for row in audit_rows for signal in LIMITUP_GENE_SHADOW_SIGNALS
    )
    coverage = round(persisted / expected, 4) if expected else 0.0
    if audit_rows and coverage < 1.0:
        status, diagnosis = 'WARN', 'signal_not_persisted'
    elif not completed_days:
        status, diagnosis = ('PASS', 'signal_persisted') if audit_rows else ('WARN', 'no_action_insufficient_evidence')
    elif missed_cases:
        status, diagnosis = 'WARN', 'signal_not_predictive'
    else:
        status, diagnosis = 'PASS', 'signal_persisted'
    return {
        'status': status, 'run_mode': run_mode, 'missed_case_count': len(missed_cases),
        'signals_checked': list(LIMITUP_GENE_SHADOW_SIGNALS),
        'persisted_signal_coverage': coverage, 'diagnosis': diagnosis,
    }


def build_historical_live_replay_closure(
    input_trade_date: str, validation_trade_date: str, *, backfill_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if fetch_daily_candidates is None or fetch_picks is None or fetch_returns is None:
        raise RuntimeError('database access is unavailable for historical replay')
    input_date = date.fromisoformat(input_trade_date)
    raw_decision_rows = fetch_daily_candidates(input_date)
    decision_rows = _sanitize_decision_snapshot_rows(raw_decision_rows)
    decision_picks = fetch_picks(input_date)
    paper_picks = [row for row in decision_picks if str(row.get('decision') or '').upper() == 'PAPER_PICK']
    paper_pick = max(
        paper_picks, key=lambda row: (_numeric(row.get('final_score')), str(row.get('symbol') or '')),
        default=None,
    )
    leakage_gate = _historical_replay_leakage_gate(
        raw_decision_rows, decision_picks, input_trade_date, validation_trade_date,
    )
    shadow_leakage_gate = _shadow_replay_leakage_gate(decision_rows)
    validation_rows = fetch_returns(input_date)
    validation_by_symbol = {str(row.get('symbol') or ''): row for row in validation_rows}
    paper_validation = validation_by_symbol.get(str((paper_pick or {}).get('symbol') or ''))
    validation_available = paper_validation is not None and paper_validation.get('t1_return') is not None
    return_map = {
        input_trade_date + ':' + str(row.get('symbol') or ''): row for row in validation_rows
    }
    evaluated_rows = _cohort_rows(decision_rows, return_map)
    evaluated_by_symbol = {str(row.get('symbol') or ''): row for row in evaluated_rows}
    paper_candidate = evaluated_by_symbol.get(str((paper_pick or {}).get('symbol') or ''))
    replay_day = [
        row for row in evaluated_rows
        if row.get('is_mainboard') and 1 <= int(row.get('rank') or 999) <= 10 and row.get('t1_return') is not None
    ]
    completed_days = []
    if paper_candidate and paper_candidate.get('is_mainboard') and paper_candidate.get('t1_return') is not None and replay_day:
        completed_days.append({'trade_date': input_trade_date, 'paper': paper_candidate, 'day': replay_day})
    attribution = _paper_pick_loss_attribution(completed_days)
    report = build_db_cohort_report()
    sample_gate = report['sample_accumulation_gate']
    reconciliation = dict(report.get('sample_count_reconciliation') or {})
    sample_update = _historical_replay_sample_update(
        sample_gate['mainboard_comparable_paper_pick_dates'],
        input_trade_date=input_trade_date, validation_trade_date=validation_trade_date,
        has_paper_pick=paper_pick is not None,
        paper_pick_is_mainboard=bool(paper_candidate and paper_candidate.get('is_mainboard')),
        has_t1_return=validation_available,
        already_counted=input_trade_date in sample_gate.get('completed_trade_dates', []),
        paper_pick_candidate_available=paper_candidate is not None,
    )
    reconciled_count = int(reconciliation.get(
        'completed_paper_pick_sample_days',
        sample_gate.get('mainboard_comparable_paper_pick_dates', 0),
    ))
    excluded_by_date = {
        str(item.get('trade_date') or ''): str(item.get('reason') or '')
        for item in reconciliation.get('excluded_dates', [])
        if isinstance(item, dict)
    }
    reconciled_dates = set(reconciliation.get('completed_trade_dates') or sample_gate.get('completed_trade_dates') or [])
    sample_update['after_sample_count'] = reconciled_count
    sample_update['completed_paper_pick_sample_count'] = reconciled_count
    if reconciled_count == sample_update['before_sample_count']:
        sample_update['sample_count_changed'] = False
        sample_update['new_completed_trade_date'] = None
        if sample_update['reason'] is None:
            sample_update['reason'] = (
                excluded_by_date.get(input_trade_date)
                or 'not_completed_by_unified_sample_definition'
            )
    elif input_trade_date not in reconciled_dates:
        sample_update['new_completed_trade_date'] = None
        sample_update['reason'] = 'cohort_reconciliation_changed_outside_replayed_date'
    reconciliation['historical_live_replay'] = reconciled_count
    snapshot_available = bool(decision_rows)
    historical_backfill_stats = dict(backfill_stats or {})
    historical_backfill_stats['failure_reasons'] = {}
    historical_backfill_stats['fetch_failed'] = 0
    historical_backfill_stats['new_failure_count'] = 0
    closure = build_daily_closure(
        input_trade_date, report, historical_backfill_stats,
        scan_completed=snapshot_available, paper_pick_written=paper_pick is not None,
        return_backfill_completed=validation_available, run_mode='HISTORICAL_LIVE_REPLAY',
    )
    db_completeness_gate = build_db_completeness_gate(
        input_trade_date,
        mode='HISTORICAL_REPLAY',
        validation_trade_date=validation_trade_date,
        candidate_rows=raw_decision_rows,
        pick_rows=decision_picks,
        return_rows=validation_rows,
    )
    if leakage_gate['status'] != 'PASS' or shadow_leakage_gate['status'] != 'PASS':
        closure['daily_system_gate']['status'] = 'FAIL'
        closure['daily_system_gate']['blocked_by'].append('historical_replay_leakage_gate_failed')
    replay_status = closure['daily_system_gate']['status']
    closure.update({
        'validation_trade_date': validation_trade_date,
        'data_source': 'DB_SNAPSHOT', 'uses_future_data_for_decision': False,
        'historical_live_replay': {
            'status': replay_status, 'run_mode': 'HISTORICAL_LIVE_REPLAY',
            'input_trade_date': input_trade_date, 'validation_trade_date': validation_trade_date,
            'data_source': 'DB_SNAPSHOT', 'uses_future_data_for_decision': False,
        },
        'historical_replay_leakage_gate': leakage_gate,
        'shadow_replay_leakage_gate': shadow_leakage_gate,
        'db_completeness_gate': db_completeness_gate,
        'db_completeness_summary': db_completeness_gate['db_completeness_summary'],
        'data_completeness_gate': {
            'status': db_completeness_gate['status'],
            'checks': db_completeness_gate['checks'],
            'candidate_pool_status': db_completeness_gate['candidate_pool_status'],
            'reason': db_completeness_gate['candidate_pool_warning_reason'],
        },
        'return_validation_gate': {
            'status': 'PASS' if validation_available else 'FAIL',
            'reason': None if validation_available else 'paper_pick_t1_return_missing',
        },
        'historical_live_replay_sample_update': sample_update,
        'sample_count_reconciliation': reconciliation,
        'limitup_gene_signal_audit': _limitup_gene_signal_audit(
            completed_days, attribution, run_mode='HISTORICAL_LIVE_REPLAY', candidate_rows=decision_rows,
        ),
        'historical_replay_case_book': _paper_pick_case_book(completed_days, attribution),
    })
    return closure


def build_db_cohort_report(
    start_date: str = '2026-06-20',
    end_date: Optional[str] = None,
    *,
    full_pytest_gate_status: str = 'PASS',
) -> Dict[str, Any]:
    """Build the authoritative DB-wide cohort report over every stored date."""
    candidates, pick_map, return_map = _db_rows_since(start_date, end_date)
    rows = _cohort_rows(candidates, return_map)
    quality_groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        quality_groups.setdefault(row['cohort_quality'], []).append(row)
    reports = {name: _performance_for_rows(group, pick_map, name=name) for name, group in quality_groups.items()}
    reports['all_since_2026_06_20'] = _performance_for_rows(rows, pick_map, name='all_since_2026_06_20')
    mainboard_rows = [row for row in rows if row.get('is_mainboard')]
    reports['mainboard_only_since_2026_06_20'] = _performance_for_rows(mainboard_rows, pick_map, name='mainboard_only_since_2026_06_20')
    reports['legacy_non_mainboard_reference'] = _performance_for_rows([row for row in rows if not row.get('is_mainboard')], pick_map, name='legacy_non_mainboard_reference')
    reports['cohort_summary'] = {name: {'candidate_count': len(group), 'top10_count': sum(1 for row in group if int(row.get('rank') or 999) <= 10), 't1_return_rows': sum(1 for row in group if row.get('t1_return') is not None), 'dates': len({_date_key(row.get('trade_date')) for row in group})} for name, group in quality_groups.items()}
    reports['cohort_summary']['NO_RETURN_YET'] = {'candidate_count': sum(1 for row in rows if 'NO_RETURN_YET' in row.get('status_flags', [])), 'top10_count': sum(1 for row in rows if 'NO_RETURN_YET' in row.get('status_flags', []) and int(row.get('rank') or 999) <= 10)}
    reports['case_studies'] = build_cross_date_case_studies(candidates, pick_map, return_map)
    reports['ranking_improvement_analysis'] = build_ranking_improvement_analysis(rows, pick_map)
    top10_rows = [row for row in rows if 1 <= int(row.get('rank') or 999) <= 10]
    mainboard_top10_rows = [row for row in top10_rows if row.get('is_mainboard')]
    mainboard_rank26_rows = [row for row in mainboard_top10_rows if 2 <= int(row.get('rank') or 999) <= 6]
    paper_pick_rows = [
        row for row in mainboard_top10_rows
        if pick_map.get(_date_key(row.get('trade_date')))
        and str(pick_map[_date_key(row.get('trade_date'))].get('symbol') or '') == str(row.get('symbol') or '')
    ]
    returns_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        returns_by_date.setdefault(_date_key(row.get('trade_date')), []).append(row)
    pending_dates = {
        trade_date for trade_date, date_rows in returns_by_date.items()
        if not any(item.get('t1_return') is not None for item in date_rows)
    }

    def coverage(items: List[Dict[str, Any]]) -> Optional[float]:
        comparable = [item for item in items if _date_key(item.get('trade_date')) not in pending_dates]
        return round(sum(item.get('t1_return') is not None for item in comparable) / len(comparable), 4) if comparable else None
    all_paper_pick_records = (pick_map.get('__all__') or {}).get('rows') or []
    comparable_paper_pick_records = [
        record for record in all_paper_pick_records
        if _date_key(record.get('trade_date')) not in pending_dates
    ]
    return_coverage_gate = {
        'top10_t1_coverage': coverage(top10_rows),
        'mainboard_top10_t1_coverage': coverage(mainboard_top10_rows),
        'paper_pick_t1_coverage': round(
            sum(record.get('_t1_return') is not None for record in comparable_paper_pick_records) / len(comparable_paper_pick_records), 4
        ) if comparable_paper_pick_records else None,
        'mainboard_rank2_to_rank6_t1_coverage': coverage(mainboard_rank26_rows),
        'thresholds': RETURN_COVERAGE_THRESHOLDS,
    }
    coverage_failures = [
        name for name, threshold in RETURN_COVERAGE_THRESHOLDS.items()
        if return_coverage_gate.get(name) is None or return_coverage_gate[name] < threshold
    ]
    return_coverage_gate['status'] = 'PASS' if not coverage_failures else 'FAIL'
    return_coverage_gate['blocking_reason'] = None if not coverage_failures else 'coverage_below_threshold:' + ','.join(coverage_failures)
    reports['return_coverage_gate'] = return_coverage_gate
    full_chain = reports.get('FULL_CHAIN_COMPLETE', {})
    full_chain_rows = [row for row in rows if row.get('cohort_quality') == 'FULL_CHAIN_COMPLETE']
    ready_full_chain_dates = {
        _date_key(row.get('trade_date')) for row in full_chain_rows if row.get('t1_return') is not None
    }
    pending_full_chain_dates = {
        trade_date for trade_date in {_date_key(row.get('trade_date')) for row in full_chain_rows}
        if not any(
            row.get('t1_return') is not None and _date_key(row.get('trade_date')) == trade_date
            for row in full_chain_rows
        )
    }
    reports['full_chain_complete_return_pending'] = bool(pending_full_chain_dates)
    reports['full_chain_complete_return_status'] = 'PENDING' if reports['full_chain_complete_return_pending'] else 'READY'
    reports['full_chain_complete_performance'] = None if reports['full_chain_complete_return_pending'] else full_chain
    reports['full_chain_complete_gate'] = {
        'ready_day_count': len(ready_full_chain_dates),
        'pending_day_count': len(pending_full_chain_dates),
        'minimum_ready_days_for_freeze': MINIMUM_READY_DAYS_FOR_FREEZE,
        'status': 'PASS' if len(ready_full_chain_dates) >= MINIMUM_READY_DAYS_FOR_FREEZE else 'WAITING',
    }
    completed_paper_days, sample_excluded_dates = completed_paper_pick_sample_days(
        rows, pick_map, pending_dates | pending_full_chain_dates,
    )
    candidate_by_key = {
        _date_key(row.get('trade_date')) + ':' + str(row.get('symbol') or ''): row
        for row in rows
    }
    non_mainboard_paper_pick_dates = []
    no_t1_return_paper_pick_dates = []
    for record in all_paper_pick_records:
        trade_date = _date_key(record.get('trade_date'))
        candidate = candidate_by_key.get(trade_date + ':' + str(record.get('symbol') or ''))
        if candidate is not None and not candidate.get('is_mainboard'):
            non_mainboard_paper_pick_dates.append(trade_date)
        if record.get('_t1_return') is None:
            no_t1_return_paper_pick_dates.append(trade_date)
    reports['paper_pick_performance_gate'] = _paper_pick_performance_gate(completed_paper_days)
    reports['paper_pick_loss_attribution'] = _paper_pick_loss_attribution(completed_paper_days)
    reports['shadow_ranking_replay'] = _shadow_ranking_replay(completed_paper_days)
    reports['limitup_gene_shadow_replay'] = _limitup_gene_shadow_replay(
        completed_paper_days, reports['paper_pick_loss_attribution'],
    )
    reports['limitup_capture_gate'] = _limitup_capture_gate(
        completed_paper_days, reports['paper_pick_loss_attribution'],
    )
    reports['paper_pick_case_book'] = _paper_pick_case_book(
        completed_paper_days, reports['paper_pick_loss_attribution'],
    )
    reports['paper_pick_vs_pool_diagnostic'] = _paper_pick_vs_pool_diagnostic(completed_paper_days)
    mainline_diagnostics = _mainline_diagnostics(completed_paper_days)
    reports['mainline_diagnostic_gate'] = {
        'status': 'DIAGNOSTIC_ONLY',
        'production_mutation_allowed': False,
        'sample_count': len(completed_paper_days),
        'minimum_comparable_dates': MINIMUM_PERFORMANCE_SAMPLE_COUNT,
        'constraints': {
            'production_ranking_change': 'LOCKED',
            'paper_pick_freeze': 'FORBIDDEN',
            'formal_candidate_sort_key_change': 'FORBIDDEN',
        },
        'daily': mainline_diagnostics['daily'],
        'miss_bucket_distribution': mainline_diagnostics['distribution'],
    }
    reports['mainline_pool_coverage'] = _mainline_pool_coverage(completed_paper_days)
    reports['mainline_shadow_replay'] = _mainline_shadow_replay(completed_paper_days)
    reports['mainline_case_book'] = _mainline_case_book(
        completed_paper_days, mainline_diagnostics, reports['mainline_shadow_replay'],
    )
    reports['next_phase_recommendation'] = _mainline_next_phase_recommendation(
        mainline_diagnostics, reports['mainline_shadow_replay'],
    )
    reports['sell_strategy_gate'] = _sell_strategy_gate(completed_paper_days)
    reports['sell_strategy_execution_gate'] = _sell_strategy_execution_gate(completed_paper_days)
    reports['sell_strategy_replay'] = _sell_strategy_replay(completed_paper_days)
    reports['sample_accumulation_gate'] = _sample_accumulation_gate(
        len(completed_paper_days),
        completed_trade_dates=[record['trade_date'] for record in completed_paper_days],
        pending_trade_dates=sorted(pending_full_chain_dates),
        non_mainboard_trade_dates=non_mainboard_paper_pick_dates,
        no_t1_return_trade_dates=no_t1_return_paper_pick_dates,
    )
    completed_sample_count = len(completed_paper_days)
    reports['sample_count_reconciliation'] = {
        'completed_paper_pick_sample_days': completed_sample_count,
        'paper_pick_performance_gate': reports['paper_pick_performance_gate']['sample_count'],
        'strategy_readiness': completed_sample_count,
        'sample_accumulation_gate': reports['sample_accumulation_gate']['mainboard_comparable_paper_pick_dates'],
        'historical_live_replay': completed_sample_count,
        'excluded_dates': sample_excluded_dates,
    }
    mainboard = reports.get('mainboard_only_since_2026_06_20', {})
    ranking_replay = reports['ranking_improvement_analysis']['ranking_basis_replay']
    reports['ranking_improvement_gate'] = {
        'status': 'PASS' if (
            (ranking_replay.get('sample_count') or 0) >= 10
            and ranking_replay.get('paper_pick_vs_top10_best_gap_after') is not None
            and ranking_replay.get('paper_pick_vs_top10_best_gap_before') is not None
            and ranking_replay['paper_pick_vs_top10_best_gap_after'] >= ranking_replay['paper_pick_vs_top10_best_gap_before']
            and (ranking_replay.get('win_rate_after') or 0.0) >= (ranking_replay.get('win_rate_before') or 0.0)
        ) else 'INSUFFICIENT_EVIDENCE',
        'reason': 'requires_at_least_10_comparable_mainboard_paper_pick_dates_and_non_degrading_replay',
        'top10_t1_coverage': mainboard.get('return_coverage'),
        'paper_pick_sample_count': ranking_replay.get('sample_count'),
        'gap_before': ranking_replay.get('paper_pick_vs_top10_best_gap_before'),
        'gap_after': ranking_replay.get('paper_pick_vs_top10_best_gap_after'),
    }
    regime_values = {
        str(_candidate_signal(row, 'market_regime'))
        for row in rows if _nonempty(_candidate_signal(row, 'market_regime'))
    }
    reports['production_ranking_change_gate'] = _production_ranking_change_gate(
        sample_gate=reports['sample_accumulation_gate'],
        shadow_replay=reports['limitup_gene_shadow_replay'],
        full_pytest_gate_status=full_pytest_gate_status,
        return_coverage_gate_status=return_coverage_gate['status'],
        full_chain_ready_days=len(ready_full_chain_dates), market_regime_count=len(regime_values),
    )
    failed_risk_dates = []
    for row in paper_pick_rows:
        attributed = _attribution_candidate(row)
        eligibility = attributed.get('paper_pick_eligibility') if isinstance(attributed.get('paper_pick_eligibility'), dict) else {}
        gate = eligibility.get('paper_pick_risk_explanation_gate') if isinstance(eligibility.get('paper_pick_risk_explanation_gate'), dict) else {}
        if gate.get('status') == 'FAIL' or 'PAPER_PICK_RISK_EXPLANATION_GATE_FAIL' in (row.get('official_target_exclusion_reasons') or []):
            failed_risk_dates.append(_date_key(row.get('trade_date')))
    reports['paper_pick_risk_explanation_gate'] = {
        'status': 'PASS' if not failed_risk_dates else 'FAIL',
        'failed_dates': sorted(set(failed_risk_dates)),
        'rule': 'failed_limitup + outflow + high_popularity requires explicit catalyst/risk rebuttal',
    }
    reports['db_cohort_consistency_gate'] = {
        'status': 'PASS' if all(_nonempty(row.get('cohort')) for row in rows) else 'FAIL',
        'cohort_null_count_since_2026_06_20': sum(not _nonempty(row.get('cohort')) for row in rows),
        'mainboard_scope_consistent': all(bool(row.get('is_mainboard')) == bool(is_mainboard_symbol(str(row.get('symbol') or ''))) for row in rows),
    }
    replay = reports['ranking_improvement_analysis']['ranking_basis_replay']
    comparable_sample_count = reports['sample_accumulation_gate']['mainboard_comparable_paper_pick_dates']
    if full_pytest_gate_status != 'PASS':
        strategy_status = 'BLOCKED_BY_FULL_PYTEST'
        blocking_reasons = [f'full_pytest_gate={full_pytest_gate_status}']
        next_required_condition = 'make pytest -q pass before interpreting strategy performance'
    elif return_coverage_gate['status'] != 'PASS':
        strategy_status = 'BLOCKED_BY_RETURN_COVERAGE'
        blocking_reasons = [return_coverage_gate['blocking_reason']]
        next_required_condition = 'restore all return coverage thresholds'
    elif comparable_sample_count < 10:
        strategy_status = 'INSUFFICIENT_COMPARABLE_SAMPLE'
        blocking_reasons = [f'mainboard_comparable_paper_pick_dates={comparable_sample_count} < 10']
        next_required_condition = 'accumulate at least 10 comparable mainboard PAPER_PICK dates'
    elif reports['ranking_improvement_gate']['status'] != 'PASS':
        strategy_status = 'RANKING_IMPROVEMENT_FAILED'
        blocking_reasons = ['ranking replay did not improve all required metrics']
        next_required_condition = 'investigate ranking replay degradation before retuning'
    elif reports['paper_pick_risk_explanation_gate']['status'] != 'PASS':
        strategy_status = 'RANKING_IMPROVEMENT_PARTIAL'
        blocking_reasons = ['paper_pick_risk_explanation_gate failed']
        next_required_condition = 'resolve the remaining PAPER_PICK risk explanation failures'
    elif reports['full_chain_complete_gate']['status'] != 'PASS':
        strategy_status = 'RANKING_IMPROVEMENT_VERIFIED'
        blocking_reasons = ['full_chain_ready_days_below_freeze_minimum']
        next_required_condition = f'accumulate at least {MINIMUM_READY_DAYS_FOR_FREEZE} ready FULL_CHAIN_COMPLETE days'
    else:
        strategy_status = 'READY_FOR_PAPER_PICK_FREEZE'
        blocking_reasons = []
        next_required_condition = None
    reports['full_pytest_gate'] = {'status': full_pytest_gate_status}
    reports['strategy_status'] = strategy_status
    reports['strategy_readiness'] = {
        'status': strategy_status,
        'sample_count': completed_sample_count,
        'blocking_reasons': blocking_reasons,
        'next_required_condition': next_required_condition,
    }
    evidence_fields = ('candidate_entry_reason', 'factor_snapshot', 'auxiliary_evidence_snapshot', 'ranking_basis', 'not_selected_reason')
    reconstruction_confidence: Dict[str, int] = {}
    missing_fields: Dict[str, int] = {}
    for row in rows:
        provenance = row.get('reconstruction_provenance') or {}
        for field in evidence_fields:
            details = provenance.get(field) if isinstance(provenance, dict) else None
            if isinstance(details, dict):
                level = details.get('reconstruction_confidence') or 'UNKNOWN'
                reconstruction_confidence[level] = reconstruction_confidence.get(level, 0) + 1
                for missing in details.get('missing_fields') or []:
                    missing_fields[missing] = missing_fields.get(missing, 0) + 1
    reports['reconstruction_summary'] = {
        'window': {'start': start_date, 'end': end_date},
        'top10_rows': sum(1 for row in rows if int(row.get('rank') or 999) <= 10),
        'coverage': {field: round(sum(1 for row in rows if int(row.get('rank') or 999) <= 10 and _nonempty(row.get(field))) / max(1, sum(1 for candidate in rows if int(candidate.get('rank') or 999) <= 10)), 4) for field in evidence_fields},
        'reconstruction_confidence': reconstruction_confidence,
        'missing_fields': missing_fields,
    }
    reports['inventory'] = {'dates': len({_date_key(row.get('trade_date')) for row in candidates}), 'daily_candidates': len(candidates), 'top10_candidates': sum(1 for row in rows if int(row.get('rank') or 999) <= 10), 'mainboard_candidates': sum(1 for row in rows if row.get('is_mainboard')), 'non_mainboard_candidates': sum(1 for row in rows if not row.get('is_mainboard')), 't1_return_rows': sum(1 for row in rows if row.get('t1_return') is not None)}
    return reports


def find_best_scan_for_date(trade_date: str) -> Optional[Path]:
    """Find the latest scan directory for a given trade date."""
    scan_dir = LIVE_SCAN_ROOT / trade_date
    if not scan_dir.exists():
        return None
    # Prefer realtime scans around 14:30 window, fall back to latest
    candidates = sorted([
        d for d in scan_dir.iterdir()
        if d.is_dir() and 'realtime' in d.name
    ])
    if not candidates:
        return None
    # Prefer scan closest to 14:30 (1430), fall back to latest
    def scan_time_key(d):
        name = d.name
        for part in name.split('_'):
            if part.isdigit() and len(part) == 6:
                return int(part)
        return 0
    # Find scan <= 1500 if possible, else latest
    afternoon = [d for d in candidates if 1400 <= scan_time_key(d) <= 1500]
    return afternoon[-1] if afternoon else candidates[-1]


def find_bundle_for_date(trade_date: str) -> Optional[Path]:
    """Find candidate bundle for a given trade date."""
    bundle_dir = BUNDLE_ROOT / trade_date
    if not bundle_dir.exists():
        return None
    bundles = list(bundle_dir.glob('*.json'))
    return bundles[0] if bundles else None


def _pick_row_to_result(pick: Dict[str, Any], return_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    features = pick.get('features') or {}
    candidate_features = features.get('candidate_features') if isinstance(features, dict) else {}
    if not isinstance(candidate_features, dict):
        candidate_features = {}
    watch = features.get('daily_best_paper_watch') if isinstance(features, dict) else {}
    symbol = str(pick.get('symbol') or '').strip()
    if pick.get('decision') == 'NO_PICK' and symbol.upper() == 'NO_PICK' and isinstance(watch, dict):
        symbol = str(watch.get('symbol') or candidate_features.get('symbol') or '').strip()
    if not symbol:
        symbol = str(candidate_features.get('symbol') or candidate_features.get('code') or '').strip()
    result = {
        'trade_date': str(pick.get('trade_date') or ''),
        'decision': pick.get('decision'),
        'symbol': symbol,
        'final_score': pick.get('final_score'),
        'error': None,
        'scan_dir': pick.get('scan_dir') or '',
        'source': 'db',
        'pick_features': features,
    }
    if symbol:
        for row in return_rows:
            if str(row.get('symbol') or '').strip() == symbol:
                result['final_score'] = result['final_score'] if result['final_score'] is not None else row.get('t1_return')
                break
    return result


def _load_db_result_for_date(trade_date: str) -> Optional[Dict[str, Any]]:
    if fetch_picks is None or fetch_returns is None:
        return None
    try:
        from datetime import date as _date
        picks = fetch_picks(_date.fromisoformat(trade_date))
    except Exception:
        return None
    if not picks:
        return None
    official = [r for r in picks if str(r.get('decision') or '').upper() == 'PAPER_PICK']
    if official:
        pick = sorted(official, key=lambda r: (-(float(r.get('final_score') or 0.0)), str(r.get('symbol') or '')))[0]
    else:
        no_picks = [r for r in picks if str(r.get('decision') or '').upper() == 'NO_PICK']
        pick = no_picks[0] if no_picks else picks[0]
    try:
        return_rows = fetch_returns(_date.fromisoformat(trade_date))
    except Exception:
        return_rows = []
    result = _pick_row_to_result(pick, return_rows)
    result['return_rows'] = return_rows
    if fetch_daily_candidates is not None:
        try:
            candidate_rows = fetch_daily_candidates(_date.fromisoformat(trade_date))
            ranked_top10 = [row for row in candidate_rows if 1 <= int(row.get('rank') or 999999) <= 10]
            result['top10_candidates'] = (ranked_top10 or candidate_rows)[:10]
        except Exception:
            result['top10_candidates'] = []
    else:
        result['top10_candidates'] = []
    return result


def run_backtest_for_date(trade_date: str, source: str = 'auto') -> Dict[str, Any]:
    """Run runner dry-run for a single date, return result dict."""
    result = {
        'trade_date': trade_date,
        'decision': None,
        'symbol': None,
        'final_score': None,
        'error': None,
        'scan_dir': None,
        'source': None,
    }

    if source in ('auto', 'db'):
        db_result = _load_db_result_for_date(trade_date)
        if db_result is not None:
            return {**result, **db_result}

    scan_dir = find_best_scan_for_date(trade_date)
    if scan_dir:
        result['scan_dir'] = str(scan_dir)
        # Extract asof_time from dir name
        asof_time = '14:30:00'
        for part in scan_dir.name.split('_'):
            if part.isdigit() and len(part) == 6:
                h, m, s = part[:2], part[2:4], part[4:6]
                asof_time = f'{h}:{m}:{s}'
                break

    try:
        cmd = [
            PYTHON, 'xiaogu_forward_d1_1450_runner_v0_1.py',
            '--date', trade_date,
            '--dry-run',
        ]
        if scan_dir:
            cmd += ['--asof-time', asof_time]

        proc = subprocess.run(
            cmd, cwd=str(BASE), capture_output=True, text=True, timeout=120
        )
        output = proc.stdout
        # Find last JSON object
        depth = 0
        start = -1
        for i, ch in enumerate(output):
            if ch == '{':
                if depth == 0: start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(output[start:i+1])
                        if obj.get('decision') in ('PAPER_PICK', 'NO_PICK', 'RESEARCH_CANDIDATE'):
                            result['decision'] = obj.get('decision')
                            result['symbol'] = obj.get('symbol') or ''
                            sc = obj.get('single_target_card') or {}
                            result['final_score'] = sc.get('final_score') or sc.get('score')
                            result['source'] = 'runner'
                            break
                    except Exception:
                        pass
    except Exception as exc:
        result['error'] = str(exc)

    return result


def load_ledger_returns() -> Dict[str, float]:
    """Load t1_returns from ledger fills."""
    returns = {}
    if not LEDGER.exists():
        return returns
    with LEDGER.open(encoding='utf-8') as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get('record_type') == 'RESULT_FILL':
                    key = f"{r.get('date')}:{r.get('symbol')}"
                    t1 = r.get('t1_return')
                    if t1 is not None:
                        returns[key] = float(t1)
            except Exception:
                pass
    return returns


def load_db_returns() -> Dict[str, float]:
    if fetch_returns is None:
        return {}
    returns: Dict[str, float] = {}
    try:
        from datetime import date as _date
    except Exception:
        return returns
    # Pull returns lazily by date from the picks table to avoid a full-table scan
    # in environments where DB access is available.
    if fetch_picks is None:
        return returns
    try:
        import os
        from sqlalchemy import create_engine, text

        engine = create_engine(os.environ.get('DATABASE_URL', 'postgresql://xiaogu:xiaogu@localhost:5432/xiaogu'))
        with engine.connect() as conn:
            dates = [row[0] for row in conn.execute(text('SELECT DISTINCT trade_date FROM picks ORDER BY trade_date')).fetchall()]
        for trade_date in dates:
            try:
                rows = fetch_returns(trade_date)
            except Exception:
                continue
            for row in rows:
                key = f"{trade_date}:{row.get('symbol')}"
                t1 = row.get('t1_return')
                if t1 is not None:
                    returns[key] = float(t1)
    except Exception:
        return {}
    return returns


def _return_by_symbol(result: Dict[str, Any], fallback_returns: Dict[str, float]) -> Dict[str, float]:
    rows = result.get('return_rows') or []
    mapped = {
        str(row.get('symbol') or ''): float(row['t1_return'])
        for row in rows
        if row.get('symbol') and row.get('t1_return') is not None
    }
    for candidate in result.get('top10_candidates') or []:
        symbol = str(candidate.get('symbol') or '')
        key = f"{result.get('trade_date')}:{symbol}"
        if symbol and symbol not in mapped and key in fallback_returns:
            mapped[symbol] = fallback_returns[key]
    return mapped


def _candidate_factor_labels(candidate: Dict[str, Any]) -> List[str]:
    factor_snapshot = candidate.get('factor_snapshot') if isinstance(candidate.get('factor_snapshot'), dict) else {}
    auxiliary = candidate.get('auxiliary_evidence_snapshot') if isinstance(candidate.get('auxiliary_evidence_snapshot'), dict) else {}
    features = candidate.get('candidate_features') if isinstance(candidate.get('candidate_features'), dict) else {}
    capital_risk = factor_snapshot.get('capital_risk_profile') if isinstance(factor_snapshot.get('capital_risk_profile'), dict) else features.get('capital_risk_profile') or {}
    labels = []
    if capital_risk.get('popularity_rank') == 1:
        labels.append('popularity_rank_1')
    if capital_risk.get('failed_limitup') and (capital_risk.get('main_buy_net') or 0) < 0:
        labels.append('failed_limitup_with_main_buy_outflow')
    if capital_risk.get('risk_softened_by_dark_pool_inflow'):
        labels.append('dark_pool_inflow_softening')
    if factor_snapshot.get('continuation_gene_score') or (auxiliary.get('yesterday_limitup_gene') or {}).get('status') == 'PROXY':
        labels.append('yesterday_limitup_proxy')
    if auxiliary.get('announcements'):
        labels.append('announcement_catalyst')
    news = auxiliary.get('news') if isinstance(auxiliary.get('news'), dict) else {}
    if news.get('direct_symbol_news'):
        labels.append('news_catalyst')
    if auxiliary.get('risk_notices'):
        labels.append('risk_notice')
    return labels


def build_legacy_chain_replay(legacy_pick: Dict[str, Any], current_candidates: List[Dict[str, Any]], actual_t1_return: Optional[float]) -> Dict[str, Any]:
    symbol = str(legacy_pick.get('symbol') or '')
    current = next((row for row in current_candidates if str(row.get('symbol') or '') == symbol), None)
    if current is None:
        return {
            'symbol': symbol,
            'name': legacy_pick.get('name') or '',
            'legacy_chain_pick_reason': legacy_pick.get('reason') or legacy_pick.get('selection_reason') or '',
            'current_full_chain_replay_reason': 'not_present_in_current_full_chain_top10',
            'would_current_xiaogu_pick_it': False,
            'if_not_pick_why': ['not_present_in_current_full_chain_top10'],
            'if_pick_why': [],
            'actual_t1_return': actual_t1_return,
            'factor_snapshot_comparison': {
                'legacy': legacy_pick.get('factor_snapshot') or {},
                'current': {},
            },
        }
    eligibility = current.get('eligibility_snapshot') if isinstance(current.get('eligibility_snapshot'), dict) else {}
    would_pick = bool(current.get('is_official_pick') or (eligibility.get('eligible') and int(current.get('rank') or 999) == 1))
    entry_reasons = list(current.get('candidate_entry_reason') or [])
    not_selected = list(current.get('not_selected_reason') or [])
    return {
        'symbol': symbol,
        'name': current.get('stock_name') or legacy_pick.get('name') or '',
        'legacy_chain_pick_reason': legacy_pick.get('reason') or legacy_pick.get('selection_reason') or '',
        'current_full_chain_replay_reason': 'current_candidate_replayed_from_daily_candidates',
        'would_current_xiaogu_pick_it': would_pick,
        'if_not_pick_why': [] if would_pick else (not_selected or ['rank_or_eligibility_not_first']),
        'if_pick_why': entry_reasons if would_pick else [],
        'actual_t1_return': actual_t1_return,
        'factor_snapshot_comparison': {
            'legacy': legacy_pick.get('factor_snapshot') or {},
            'current': current.get('factor_snapshot') or {},
        },
    }


def build_top10_comparison(results: List[Dict[str, Any]], fallback_returns: Dict[str, float]) -> Dict[str, Any]:
    candidate_returns = []
    daily_best_returns = []
    rank2_to_rank6_returns = []
    paper_returns = []
    missed_profitable_candidates = []
    false_positive_paper_picks = []
    factor_rows: Dict[str, List[float]] = {}
    rank_rows: Dict[int, List[float]] = {}
    total_top10_candidates = 0
    named_samples: Dict[str, Dict[str, Any]] = {}
    for result in results:
        return_map = _return_by_symbol(result, fallback_returns)
        paper_symbol = str(result.get('symbol') or '')
        paper_return = return_map.get(paper_symbol)
        if result.get('decision') == 'PAPER_PICK' and paper_return is not None:
            paper_returns.append(paper_return)
            if paper_return <= 0:
                false_positive_paper_picks.append({
                    'trade_date': result.get('trade_date'),
                    'symbol': paper_symbol,
                    't1_return': paper_return,
                    'ticket_reason': (result.get('pick_features') or {}).get('decision_reason') or '',
                })
        day_returns = []
        top10_candidates = result.get('top10_candidates') or []
        total_top10_candidates += len(top10_candidates)
        for candidate in top10_candidates:
            symbol = str(candidate.get('symbol') or '')
            t1_return = return_map.get(symbol)
            if t1_return is None:
                continue
            enriched = {**candidate, 't1_return': t1_return}
            candidate_returns.append(enriched)
            day_returns.append(enriched)
            rank = int(candidate.get('rank') or 999)
            rank_rows.setdefault(rank, []).append(t1_return)
            if 2 <= rank <= 6:
                rank2_to_rank6_returns.append(t1_return)
            for label in _candidate_factor_labels(candidate):
                factor_rows.setdefault(label, []).append(t1_return)
            name = str(candidate.get('stock_name') or '')
            if name in ('华天科技', '巨人网络'):
                named_samples[name] = enriched
        if day_returns:
            best = max(day_returns, key=lambda row: row['t1_return'])
            daily_best_returns.append(best['t1_return'])
            if paper_return is not None and paper_return <= 0:
                for candidate in day_returns:
                    if candidate['t1_return'] > 0 and str(candidate.get('symbol') or '') != paper_symbol:
                        missed_profitable_candidates.append({
                            'trade_date': result.get('trade_date'),
                            'rank': candidate.get('rank'),
                            'symbol': candidate.get('symbol'),
                            'name': candidate.get('stock_name'),
                            't1_return': candidate['t1_return'],
                            'not_selected_reason': candidate.get('not_selected_reason') or [],
                        })
    top10_values = [row['t1_return'] for row in candidate_returns]
    required_factor_labels = (
        'popularity_rank_1',
        'failed_limitup_with_main_buy_outflow',
        'dark_pool_inflow_softening',
        'yesterday_limitup_proxy',
        'announcement_catalyst',
        'news_catalyst',
        'risk_notice',
    )
    factor_performance = {}
    for label in required_factor_labels:
        values = factor_rows.get(label, [])
        factor_performance[label] = ({
            'count': len(values),
            'win_rate': round(sum(value > 0 for value in values) / len(values), 4),
            'avg_return': round(sum(values) / len(values), 6),
            'limitup_rate': round(sum(value >= 0.095 for value in values) / len(values), 4),
            'status': 'PASS',
        } if values else {
            'count': 0,
            'win_rate': None,
            'avg_return': None,
            'limitup_rate': None,
            'status': 'INSUFFICIENT_RETURN_SAMPLES',
        })
    factor_contribution_to_profit = sorted(
        ({'factor': label, **metrics} for label, metrics in factor_performance.items() if metrics['avg_return'] is not None and metrics['avg_return'] > 0),
        key=lambda row: row['avg_return'], reverse=True,
    )
    factor_contribution_to_loss = sorted(
        ({'factor': label, **metrics} for label, metrics in factor_performance.items() if metrics['avg_return'] is not None and metrics['avg_return'] <= 0),
        key=lambda row: row['avg_return'],
    )
    paper_avg = sum(paper_returns) / len(paper_returns) if paper_returns else None
    top10_best_avg = sum(daily_best_returns) / len(daily_best_returns) if daily_best_returns else None
    rank2_to_rank6_avg = sum(rank2_to_rank6_returns) / len(rank2_to_rank6_returns) if rank2_to_rank6_returns else None
    return {
        'paper_pick_win_rate': round(sum(value > 0 for value in paper_returns) / len(paper_returns), 4) if paper_returns else None,
        'paper_pick_avg_return': round(paper_avg, 6) if paper_avg is not None else None,
        'paper_pick_limitup_rate': round(sum(value >= 0.095 for value in paper_returns) / len(paper_returns), 4) if paper_returns else None,
        'top10_best_return': round(top10_best_avg, 6) if top10_best_avg is not None else None,
        'top10_avg_return': round(sum(top10_values) / len(top10_values), 6) if top10_values else None,
        'top10_win_rate': round(sum(value > 0 for value in top10_values) / len(top10_values), 4) if top10_values else None,
        'top10_limitup_hit_rate': round(sum(value >= 0.095 for value in top10_values) / len(top10_values), 4) if top10_values else None,
        'paper_pick_vs_top10_best_gap': round((paper_avg - top10_best_avg), 6) if paper_avg is not None and top10_best_avg is not None else None,
        'paper_pick_vs_rank2_to_rank6_gap': round((paper_avg - rank2_to_rank6_avg), 6) if paper_avg is not None and rank2_to_rank6_avg is not None else None,
        'missed_profitable_candidates': missed_profitable_candidates,
        'false_positive_paper_picks': false_positive_paper_picks,
        'factor_contribution_to_profit': factor_contribution_to_profit,
        'factor_contribution_to_loss': factor_contribution_to_loss,
        'factor_performance': factor_performance,
        'top10_candidate_return_count': len(top10_values),
        'top10_candidate_count': total_top10_candidates,
        'top10_return_coverage_rate': round(len(top10_values) / total_top10_candidates, 4) if total_top10_candidates else None,
        'paper_pick_return_count': len(paper_returns),
        'paper_pick_return_coverage_rate': round(len(paper_returns) / sum(result.get('decision') == 'PAPER_PICK' for result in results), 4) if any(result.get('decision') == 'PAPER_PICK' for result in results) else None,
        'rank_performance': {
            str(rank): {
                'count': len(values),
                'win_rate': round(sum(value > 0 for value in values) / len(values), 4),
                'avg_return': round(sum(values) / len(values), 6),
            }
            for rank, values in sorted(rank_rows.items())
            if values
        },
        'huatian_tech_postmortem': named_samples.get('华天科技'),
        'giant_network_replay': build_legacy_chain_replay(
            {
                'symbol': (named_samples.get('巨人网络') or {}).get('symbol') or '002558',
                'name': '巨人网络',
                'reason': 'legacy_chain_historical_pick',
            },
            [row for result in results for row in (result.get('top10_candidates') or [])],
            (named_samples.get('巨人网络') or {}).get('t1_return'),
        ),
    }


def _max_drawdown(returns: List[float]) -> Optional[float]:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    return round(max_drawdown, 6)


def build_report(results: List[Dict[str, Any]], returns_source: str = 'ledger') -> Dict[str, Any]:
    """Build backtest performance report."""
    ledger_returns = load_db_returns() if returns_source == 'db' else load_ledger_returns()
    paper_picks = [r for r in results if r.get('decision') == 'PAPER_PICK']
    no_picks = [r for r in results if r.get('decision') == 'NO_PICK']

    enriched = []
    for r in paper_picks:
        key = f"{r['trade_date']}:{r.get('symbol','')}"
        t1 = ledger_returns.get(key)
        enriched.append({**r, 't1_return': t1,
                         'is_limit_up': (t1 >= 0.095) if t1 is not None else None})

    filled = [e for e in enriched if e['t1_return'] is not None]
    limit_ups = [e for e in filled if e.get('is_limit_up')]
    avg_t1 = sum(e['t1_return'] for e in filled) / len(filled) if filled else None
    limit_up_rate = len(limit_ups) / len(filled) if filled else None

    top10_comparison = build_top10_comparison(results, ledger_returns)
    report = {
        'backtest_dates': len(results),
        'paper_picks': len(paper_picks),
        'no_picks': len(no_picks),
        'filled_returns': len(filled),
        'limit_up_count': len(limit_ups),
        'limit_up_rate': round(limit_up_rate, 3) if limit_up_rate is not None else None,
        'avg_t1_return': round(avg_t1, 4) if avg_t1 is not None else None,
        'picks_detail': enriched,
        'errors': [r for r in results if r.get('error')],
        'returns_source': returns_source,
    }
    report.update(top10_comparison)
    report['paper_pick_max_drawdown'] = _max_drawdown([
        item['t1_return'] for item in enriched if item.get('t1_return') is not None
    ])
    return report


def get_trading_dates(start: str, end: str) -> List[str]:
    """Get weekday dates between start and end inclusive."""
    dates = []
    cur = datetime.strptime(start, '%Y-%m-%d').date()
    end_d = datetime.strptime(end, '%Y-%m-%d').date()
    while cur <= end_d:
        if cur.weekday() < 5:  # Mon-Fri
            # Only include dates with scan data
            if (LIVE_SCAN_ROOT / cur.isoformat()).exists():
                dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def main():
    ap = argparse.ArgumentParser(description='xiaogu backtest engine')
    ap.add_argument('--date', help='Single date YYYY-MM-DD')
    ap.add_argument('--start', help='Start date YYYY-MM-DD')
    ap.add_argument('--end', help='End date YYYY-MM-DD')
    ap.add_argument('--all', action='store_true', help='All available scan dates')
    ap.add_argument('--report', action='store_true', help='Print performance report')
    ap.add_argument('--db-cohort-report', action='store_true', help='Report every DB sample since the late-June boundary by cohort')
    ap.add_argument('--source', choices=('auto', 'db', 'ledger'), default='auto', help='Result source preference')
    args = ap.parse_args()

    if args.db_cohort_report:
        report = build_db_cohort_report(args.start or '2026-06-20', args.end)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        BACKTEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        out_path = BACKTEST_OUTPUT_ROOT / f"db_cohort_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f"Report saved: {out_path}")
        return

    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dates = get_trading_dates(args.start, args.end)
    elif args.all:
        if args.source == 'db' and fetch_available_trade_dates is not None:
            try:
                dates = [value.isoformat() for value in fetch_available_trade_dates()]
            except Exception:
                dates = sorted([d.name for d in LIVE_SCAN_ROOT.iterdir() if d.is_dir() and not d.name.startswith('.')])
        else:
            dates = sorted([d.name for d in LIVE_SCAN_ROOT.iterdir()
                           if d.is_dir() and not d.name.startswith('.')])
    else:
        ap.print_help()
        return

    print(f"Running backtest over {len(dates)} dates...")
    results = []
    for d in dates:
        r = run_backtest_for_date(d, source=args.source)
        status = f"{'✅' if r['decision']=='PAPER_PICK' else '❌'} {r['trade_date']} {r.get('decision')} {r.get('symbol','')} score={r.get('final_score')}"
        print(status)
        results.append(r)

    if args.report or len(dates) > 1:
        report = build_report(results, returns_source='db' if args.source == 'db' else 'ledger')
        print('\n=== BACKTEST REPORT ===')
        print(f"Dates:          {report['backtest_dates']}")
        print(f"PAPER_PICK:     {report['paper_picks']}")
        print(f"NO_PICK:        {report['no_picks']}")
        print(f"Filled returns: {report['filled_returns']}")
        print(f"Limit-up rate:  {report['limit_up_rate']:.1%}" if report['limit_up_rate'] else "Limit-up rate:  N/A")
        print(f"Avg T+1 return: {report['avg_t1_return']:+.2%}" if report['avg_t1_return'] else "Avg T+1 return: N/A")
        print(f"Top10 win rate: {report['top10_win_rate']:.1%}" if report['top10_win_rate'] is not None else "Top10 win rate: N/A")
        print(f"Pick vs best:   {report['paper_pick_vs_top10_best_gap']:+.2%}" if report['paper_pick_vs_top10_best_gap'] is not None else "Pick vs best:   N/A")
        if report['errors']:
            print(f"Errors:         {len(report['errors'])}")

        # Save to file
        BACKTEST_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as dt
        out_path = BACKTEST_OUTPUT_ROOT / f"backtest_{dt.now().strftime('%Y%m%d_%H%M%S')}.json"
        with out_path.open('w') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nReport saved: {out_path}")


if __name__ == '__main__':
    main()
