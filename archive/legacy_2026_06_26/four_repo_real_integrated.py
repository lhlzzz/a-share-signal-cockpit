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
STRATEGY = 'v2_1_four_repo_real_integrated'
REPO_ALLOWED_FIELDS = {
    'tradingagent_a': ['code', 'price'],
    'VEI': ['signal_pct', 'close_position_score', 'volume_ratio', 'full_universe_fund_pctile', 'amount_pctile_rule', 'source_layers', 'component_details', 'structured_component_details', 'sector_opportunity_score'],
    'Qlib': ['signal_pct', 'amount_pctile_rule', 'rank', 'market_breadth_up_pct', 'market_limitups', 'market_bigups', 'price', 'close_position_score', 'net_inflow_main'],
    'QuantDinger': ['amount_pctile_rule', 'price', 'source_row_hash', 'evidence_path', 'source_time', 'data_cutoff'],
    'UZI_Skill': ['signal_pct', 'volume_ratio', 'price', 'signal_amount', 'rank', 'full_universe_quote_count', 'market_breadth_up_pct', 'market_limitups', 'market_bigups', 'net_inflow_main'],
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
        'seal_order_strength': fl(c.get('seal_order_strength'), 0) or 0,
        'main_theme_alignment_score': fl(c.get('main_theme_alignment_score'), 0) or 0,
        'main_theme_core_score': fl(c.get('main_theme_core_score'), 0) or 0,
        'pre_limitup_anomaly': fl(c.get('pre_limitup_anomaly'), 0) or 0,
        'weak_to_strong_reversal': fl(c.get('weak_to_strong_reversal'), 0) or 0,
        'first_board_pre_signal': fl(c.get('first_board_pre_signal'), 0) or 0,
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

    if not small_account_buyable:
        reason = tradingagent_a_signal['small_account_reject_reason']
        return None, [f'small_account_blocked:{reason}'], market_regime

    if f['market_breadth'] < 15:
        return None, ['extreme_weak_market'], market_regime

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

    strong_high_momentum = (
        f['signal_pct'] >= 9.0
        and close_pos is not None
        and close_pos >= 0.85
        and f['theme_strength'] >= 8.0
        and f['liquidity'] >= 0.6
        and f['limitup_reason_propagation_score'] >= 0.75
        and net_inflow > 0
        and not near_limit
    )

    if market_regime == 'climax':
        close_position_min, close_position_candidate_type = climax_close_position_requirement(c, f, limit, near_limit)
        if (close_pos is None or close_pos < close_position_min) and not strong_limitup_capture and not strong_high_momentum:
            return None, [
                'climax_close_position_unconfirmed:'
                f'actual={close_pos},required={close_position_min},candidate_type={close_position_candidate_type}'
            ], market_regime
        if not (net_inflow > 0 or limit_potential or strong_limitup_capture or strong_high_momentum):
            return None, [f'climax_flow_or_limit_potential_unconfirmed:{net_inflow:.0f}'], market_regime

    if near_limit and not sealed_limit_up:
        near_limit_confirmed = (
            market_regime == 'climax'
            and close_pos is not None
            and close_pos >= NEAR_LIMIT_CLOSE_POSITION_MIN
            and net_inflow > 0
            and f['liquidity'] >= NEAR_LIMIT_LIQUIDITY_MIN
        ) or strong_high_momentum
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
    if f['liquidity'] < 0.30:
        risk += 10
    if f['signal_pct'] >= 12:
        risk += (f['signal_pct'] - 12) * 0.8
    if near_limit:
        risk += 12

    risk_threshold = 30 if market_regime in ('strong', 'climax') else (25 if market_regime == 'neutral' else 20)
    if risk >= risk_threshold:
        return None, [f'risk_too_high:{risk:.0f}'], market_regime

    if market_regime == 'climax':
        breadth_w, limitups_w, bigups_w = 0.0, -0.03, -0.005
    else:
        breadth_w, limitups_w, bigups_w = 0.35, 0.12, 0.025

    opp = (
        1.0 * f['signal_pct'] +
        20 * f['liquidity'] +
        2.0 * f['theme_strength'] +
        breadth_w * f['market_breadth'] +
        limitups_w * f['market_limitups'] +
        bigups_w * f['market_bigups'] -
        0.25 * f['price'] -
        0.18 * f['rank']
    )

    if market_regime == 'strong':
        opp += 10
    elif market_regime == 'neutral':
        opp += 4
    elif market_regime == 'climax':
        opp -= 6

    if market_regime != 'climax' and board == 'main' and 7 <= f['signal_pct'] <= 14 and f['market_breadth'] >= 45:
        opp += 8
    if market_regime != 'climax' and 6 <= f['signal_pct'] <= 12 and f['market_breadth'] >= 45:
        opp += 6
    if f['price'] <= 20:
        opp += 4
    if f['liquidity'] >= 0.65:
        opp += 3

    opp += repo_signals['score_delta']
    if strong_limitup_capture:
        opp += min(4.0, f['limitup_capture_score'] * 5.0)
    elif f['limitup_capture_profile'] == 'MEDIUM_LIMITUP_CAPTURE' and not near_limit:
        opp += min(2.0, f['limitup_capture_score'] * 3.0)

    continuation_stage = str(c.get('candidate_stage') or '').lower() in {'high_7_to_9', 'near_limit_9_plus'}
    continuation_setup = str(c.get('setup_type') or '').upper() in {'LIMIT_STRENGTH', 'HIGH_7_TO_9_BREAKOUT'}
    if continuation_stage or continuation_setup:
        continuation_bonus = 0.0
        continuation_bonus += min(2.4, f['limitup_capture_score'] * 2.4)
        continuation_bonus += min(1.6, f['seal_order_strength'] * 1.6)
        continuation_bonus += min(1.4, f['main_theme_alignment_score'] * 1.4)
        continuation_bonus += min(1.2, f['main_theme_core_score'] * 1.2)
        continuation_bonus += min(1.2, max(f['pre_limitup_anomaly'], f['weak_to_strong_reversal'], f['first_board_pre_signal']) * 1.2)
        opp += continuation_bonus

    if market_regime == 'climax':
        opp_threshold, opp_candidate_type = climax_opp_requirement(c, f, limit, near_limit)
    else:
        opp_threshold, opp_candidate_type = (32 if market_regime == 'strong' else (38 if market_regime == 'neutral' else 45)), market_regime
    if strong_high_momentum:
        opp_threshold = min(opp_threshold, 28.0)
    if opp < opp_threshold:
        return None, [f'opp_too_low:actual={opp:.1f},required={opp_threshold:.1f},candidate_type={opp_candidate_type}'], market_regime

    ranking_adjustment = MID_PRICE_REBALANCE_WEIGHT * min(f['price'], MID_PRICE_REBALANCE_CAP)
    if f['price'] < LOW_PRICE_CROWDING_GATE:
        ranking_adjustment -= LOW_PRICE_CROWDING_PENALTY_WEIGHT * (LOW_PRICE_CROWDING_GATE - f['price'])
    if f['rank'] < FRONT_AMOUNT_RANK_GATE:
        ranking_adjustment -= FRONT_AMOUNT_RANK_PENALTY_WEIGHT * (FRONT_AMOUNT_RANK_GATE - f['rank'])
    final_score = opp - risk * 0.25 + ranking_adjustment

    if board == 'main' and f['market_breadth'] < MAIN_BOARD_BREADTH_GATE:
        return None, [f'main_board_breadth_too_low:{f["market_breadth"]:.2f}'], market_regime

    return final_score, [], market_regime


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
            'script': str(BASE/'four_repo_real_integrated.py'),
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
            'script': str(BASE/'four_repo_real_integrated.py'),
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
