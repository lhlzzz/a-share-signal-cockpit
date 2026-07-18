#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Active four-repo/native integration layer for xiaogu paper-only scoring."""
from __future__ import annotations

import json
from typing import Any, Dict

from xiaogu_native_repo_runtime_v0_1 import (
    repo_contribution_from_adapter,
    repo_contribution_summary_text,
    run_all_native_adapters,
)

FOUR_REPO_ORDER = ['tradingagent_a', 'VEI', 'Qlib', 'QuantDinger', 'Kaixin_Factors', 'UZI_Skill']
REPO_ORDER = FOUR_REPO_ORDER
SCORE_CAP_BY_REPO = {
    'tradingagent_a': {'min': 0.0, 'max': 0.0},
    'VEI': {'min': -2.0, 'max': 2.0},
    'Qlib': {'min': -1.5, 'max': 1.5},
    'QuantDinger': {'min': -2.0, 'max': 1.0},
    'UZI_Skill': {'min': -1.0, 'max': 1.0},
    'Kaixin_Factors': {'min': -1.5, 'max': 1.5},
}


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_delta(adapter: Dict[str, Any]) -> float:
    repo = adapter.get('repo_name')
    cap = adapter.get('score_cap') or SCORE_CAP_BY_REPO.get(repo, {'min': 0.0, 'max': 0.0})
    if adapter.get('status') != 'REAL_OUTPUT' or not adapter.get('score_eligible'):
        return 0.0
    return round(clamp(fnum(adapter.get('score_delta')), fnum(cap.get('min')), fnum(cap.get('max'))), 4)


def aggregate_four_repo_native_signals(candidate: Dict[str, Any]) -> Dict[str, Any]:
    adapters = run_all_native_adapters(candidate)
    real_outputs = [a for a in adapters if a.get('status') == 'REAL_OUTPUT']
    blocked_outputs = [a for a in adapters if a.get('status') == 'BLOCKED']
    concept_outputs = [a for a in adapters if a.get('status') == 'CONCEPT_ONLY']

    score_delta_by_repo = {a['repo_name']: _score_delta(a) for a in adapters}
    score_delta = round(sum(score_delta_by_repo.values()), 4)
    score_cap_by_repo = {a['repo_name']: a.get('score_cap') or SCORE_CAP_BY_REPO.get(a['repo_name'], {}) for a in adapters}
    signal_breakdown_by_repo = {a['repo_name']: a.get('signals', {}) for a in adapters}
    evidence_paths_by_repo = {a['repo_name']: a.get('evidence_paths', []) for a in adapters}
    repo_contributions = {a['repo_name']: repo_contribution_from_adapter(a) for a in adapters}
    repo_contribution_summary = repo_contribution_summary_text(repo_contributions)

    return {
        'adapters': adapters,
        'real_outputs': real_outputs,
        'blocked_outputs': blocked_outputs,
        'concept_outputs': concept_outputs,
        'score_delta': score_delta,
        'score_delta_by_repo': score_delta_by_repo,
        'score_cap_by_repo': score_cap_by_repo,
        'repo_contributions': repo_contributions,
        'repo_contribution_summary': repo_contribution_summary,
        'signal_breakdown_by_repo': signal_breakdown_by_repo,
        'evidence_paths_by_repo': evidence_paths_by_repo,
        'native_runtime_summary': {
            a['repo_name']: {
                'status': a.get('status'),
                'runtime_status': a.get('runtime_status'),
                'score_eligible': a.get('score_eligible'),
                'score_delta': score_delta_by_repo.get(a['repo_name'], 0.0),
                'evidence_count': len(a.get('evidence_paths') or []),
            }
            for a in adapters
        },
        'real_count': len(real_outputs),
        'blocked_count': len(blocked_outputs),
        'concept_count': len(concept_outputs),
        'future_fields_used': any(a.get('used_future_fields') for a in adapters),
        'external_api_used': any(a.get('external_api_used') for a in adapters),
        'llm_used': any(a.get('llm_used') for a in adapters),
        'blocked_repo_affects_scoring': False,
        'concept_only_affects_scoring': False,
        'native_integration_version': 'xiaogu_native_repo_runtime_v0_1',
        'repo_order': FOUR_REPO_ORDER,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
    }


def aggregate_six_repo_native_signals(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return aggregate_four_repo_native_signals(candidate)


if __name__ == '__main__':
    test_candidate = {
        'code': '002709',
        'symbol': '002709',
        'signal_date': '2026-05-21',
        'price': 58.0,
        'signal_pct': 6.54,
        'market_breadth_up_pct': 84.62,
        'market_limitups': 41,
        'market_bigups': 48,
        'theme_strength': 6.54,
        'amount_pctile_rule': 0.714286,
        'rank': 3,
        'source_time': '2026-05-21 14:50:00',
        'data_cutoff': '2026-05-21 14:50:00',
    }
    print(json.dumps(aggregate_four_repo_native_signals(test_candidate), ensure_ascii=False, indent=2))
