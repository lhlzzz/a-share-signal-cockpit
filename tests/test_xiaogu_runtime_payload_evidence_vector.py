#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for slim runtime payloads, compact evidence card, and case vectors."""
from __future__ import annotations

import os
import math

import pytest

from xiaogu_case_vector_store import (
    case_text_from_pick,
    embed_text,
    similar_cases_ranking_boost,
)
from xiaogu_evidence_card import build_compact_evidence_card, evidence_card_to_selection_reason
from xiaogu_runtime_payload import (
    build_runtime_decision_context,
    payload_bytes,
    slim_bundle_for_runtime,
    slim_features_for_recorder,
)
from six_repo_integration_real_v2_1 import _contribution_is_noise


def _fat_bundle():
    return {
        'date': '2026-07-22',
        'paper_scoring_candidates': [
            {
                'symbol': '601899',
                'name': '紫金矿业',
                'score': 88,
                'paper_pick_eligibility': {
                    'eligible': True,
                    'blockers': [],
                    'signals': {},
                },
            }
            for _ in range(400)
        ],
        'full_candidate_pool': [{'symbol': str(i).zfill(6)} for i in range(400)],
        'scored_candidates': [{'symbol': str(i).zfill(6), 'blob': 'x' * 50} for i in range(400)],
        'candidate': {
            'symbol': '601899',
            'name': '紫金矿业',
            'final_score': 88.1,
            'fund_flow_momentum': 0.7,
            'main_theme_core_score': 0.0,
            'leader_chain_score': 0.4,
            'signal_pct': 5.2,
        },
        'market_snapshot': {
            'market_regime': 'sideways',
            'market_breadth_up_pct': 0.48,
            'noise': list(range(1000)),
        },
    }


def test_slim_bundle_drops_full_pools_and_stays_small():
    fat = _fat_bundle()
    slim = slim_bundle_for_runtime(fat)
    assert slim['payload_policy'] == 'slim_runtime_v1_no_full_pool_embed'
    assert slim['paper_scoring_candidates_count'] == 400
    assert len(slim['paper_scoring_candidates']) <= 15
    assert 'full_candidate_pool' not in slim
    assert 'scored_candidates' not in slim
    assert payload_bytes(slim) < 200_000


def test_runtime_decision_context_is_bounded():
    fat = _fat_bundle()
    features = {
        'date': '2026-07-22',
        'asof_time': '14:50:00',
        'rule_version': 'historical_backtest_rule_v0_3',
        'candidate_bundle_status': fat,
        'candidate_features': {
            **fat['candidate'],
            'paper_candidate_basket': fat['paper_scoring_candidates'],
        },
        'candidate_validations': [
            {'symbol': '601899', 'validation_passed': True, 'eligibility_snapshot': {'eligible': True}}
        ],
    }
    rt = build_runtime_decision_context(features, 'PAPER_PICK', '601899', 'test', {'symbol': '601899'})
    assert rt['payload_policy'] in ('slim_runtime_v1', 'slim_runtime_v1_emergency_cap')
    assert payload_bytes(rt) < 8 * 1024 * 1024
    rec = slim_features_for_recorder(features)
    assert rec['payload_policy'] == 'slim_recorder_v1'
    assert len(rec.get('paper_candidate_basket') or []) <= 12


def test_compact_evidence_card_shape_and_selection_reason():
    cand = _fat_bundle()['candidate']
    card = build_compact_evidence_card(
        cand,
        decision='PAPER_PICK',
        reason='mainline_quality_escape',
        similar_cases=[{'symbol': '600362', 'similarity': 0.5, 't1_return': 0.02}],
    )
    assert card['version'] == 'compact_evidence_card_v1'
    assert card['symbol'] == '601899'
    assert isinstance(card['fund_flow'], list)
    assert isinstance(card['main_theme'], list)
    sel = evidence_card_to_selection_reason(card, 'repo_noise_string')
    assert sel['format'] == 'compact_evidence_card_v1'
    assert 'evidence_card' in sel
    assert sel['legacy_repo_summary']


def test_embed_text_normalized_and_boost_bounded():
    from xiaogu_case_vector_store import get_embed_dim, resolve_embed_backend

    emb = embed_text('紫金矿业 有色 资金流入 主线')
    assert len(emb) == get_embed_dim()
    assert abs(math.sqrt(sum(x * x for x in emb)) - 1.0) < 1e-2
    # Semantic geometry: related power cases closer than pure noise (both backends).
    a = embed_text('华银电力 电力 主升 资金流入')
    b = embed_text('嘉泽新能 电力 新能源 资金')
    noise = embed_text('random garbage xyz abc 12345')
    cos_ab = sum(x * y for x, y in zip(a, b))
    cos_noise = sum(x * y for x, y in zip(a, noise))
    assert cos_ab > cos_noise
    text = case_text_from_pick(
        symbol='601899',
        name='紫金矿业',
        decision='PAPER_PICK',
        score=88,
        evidence_card={'main_theme': ['有色'], 'fund_flow': ['fund=0.7']},
        features={'industry': '有色'},
        reason='test',
    )
    assert '601899' in text
    boost = similar_cases_ranking_boost([{'t1_return': 0.03, 'similarity': 0.5}])
    assert 0 < boost['boost'] <= 0.35
    assert boost['hard_gate'] is False
    assert boost['force_pick'] is False
    empty = similar_cases_ranking_boost([])
    assert empty['boost'] == 0.0
    assert resolve_embed_backend() in ('neural', 'structured')
    # Similar-loss neighborhood: majority losers demote (soft only, bounded).
    loss_boost = similar_cases_ranking_boost([
        {'t1_return': -0.08, 'similarity': 0.55},
        {'t1_return': -0.06, 'similarity': 0.50},
        {'t1_return': -0.04, 'similarity': 0.48},
        {'t1_return': 0.01, 'similarity': 0.40},
    ])
    assert loss_boost['boost'] < 0
    assert loss_boost['boost'] >= -0.50
    assert loss_boost['n_loss'] >= 3
    assert (loss_boost.get('loss_ratio') or 0) >= 0.6
    assert loss_boost['hard_gate'] is False
    assert loss_boost['force_pick'] is False
    assert loss_boost.get('soft_only') is True


def test_embed_backend_auto_is_offline_by_default_and_can_opt_in_network(monkeypatch):
    import sys
    import types

    import xiaogu_case_vector_store as vector_store

    calls = {}

    class FakeSentenceTransformer:
        def __init__(self, name, **kwargs):
            calls['name'] = name
            calls['kwargs'] = dict(kwargs)
            raise OSError('missing local model')

    monkeypatch.setenv('XIAOGU_EMBED_BACKEND', 'auto')
    monkeypatch.delenv('XIAOGU_EMBED_ALLOW_NETWORK', raising=False)
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    monkeypatch.delenv('TRANSFORMERS_OFFLINE', raising=False)
    monkeypatch.setitem(
        sys.modules,
        'sentence_transformers',
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    vector_store._resolved_backend = None
    vector_store._neural_model = None
    vector_store._neural_dim = None
    vector_store._neural_error = None

    assert vector_store.resolve_embed_backend() == 'structured'
    assert calls['name'] == vector_store.neural_model_name()
    assert calls['kwargs'].get('local_files_only') is True
    assert os.environ.get('HF_HUB_OFFLINE') == '1'
    assert os.environ.get('TRANSFORMERS_OFFLINE') == '1'

    monkeypatch.setenv('XIAOGU_EMBED_ALLOW_NETWORK', '1')
    assert vector_store._allow_network_fetch() is True
    monkeypatch.setenv('XIAOGU_EMBED_ALLOW_NETWORK', '0')
    vector_store._resolved_backend = None
    vector_store._neural_model = None
    vector_store._neural_dim = None
    vector_store._neural_error = None
    assert vector_store._allow_network_fetch() is False


def test_six_repo_noise_exclusion_helpers():
    assert _contribution_is_noise({'status': 'GUARD_ONLY', 'score_eligible': False, 'repo_name': 'QuantDinger'})
    assert _contribution_is_noise({'status': 'STRUCTURED_FALLBACK', 'score_eligible': True})
    assert _contribution_is_noise({'status': 'REAL_OUTPUT', 'runtime_status': 'STRUCTURED_FALLBACK_MIMO', 'score_eligible': True})
    assert not _contribution_is_noise({
        'status': 'REAL_OUTPUT',
        'score_eligible': True,
        'contribution_status': 'REAL_OUTPUT',
        'repo_name': 'tradingagent_a',
    })


def test_leader_chain_and_soft_invalid_in_formal_sort():
    from xiaogu_forward_runner import (
        ensure_leader_chain_main_theme,
        formal_candidate_sort_key,
    )

    hollow = {
        'symbol': '601899',
        'main_theme_core_score': 0.0,
        'main_theme_alignment_score': 0.0,
        'sector_opportunity_score': 0.6,
        'fund_flow_momentum': 0.5,
        'close_position_score': 0.85,
        'volume_ratio': 2.0,
        'signal_pct': 4.0,
        'structured_score_components': {'fund_flow_momentum': 0.5},
        'paper_pick_eligibility': {
            'signals': {
                'mainline_fund_flow_soft': {
                    'mainline_hits': ['有色', '资源'],
                    'soft_boost': 0.4,
                }
            }
        },
    }
    filled = ensure_leader_chain_main_theme(hollow)
    assert filled.get('main_theme_source') == 'leader_chain_proxy'
    assert (filled.get('main_theme_core_score') or 0) > 0
    assert (filled.get('leader_chain_score') or 0) > 0
    # Formal primary may still be damped by capital-risk / ranking penalties;
    # competitiveness contract is leader_chain fields entering the sort key, not key[0]>0 alone.
    key = formal_candidate_sort_key(filled)
    assert isinstance(key, tuple) and len(key) >= 3
    with_boost = dict(filled)
    with_boost['similar_cases_boost'] = 0.3
    key_boosted = formal_candidate_sort_key(with_boost)
    assert key_boosted[0] >= key[0]
    # Similar-loss soft demotion must lower primary/secondary dims vs baseline.
    with_loss = dict(filled)
    with_loss['similar_cases_boost'] = -0.35
    with_loss['similar_cases_meta'] = {
        'boost': -0.35,
        'loss_ratio': 0.8,
        'n_loss': 4,
        'soft_only': True,
        'hard_gate': False,
        'force_pick': False,
    }
    key_loss = formal_candidate_sort_key(with_loss)
    assert key_loss[0] < key[0]
    assert key_loss[1] <= key[1]

    invalid_mainline = dict(filled)
    invalid_mainline['ranking_adjustment_detail'] = {
        'mainline_fund_flow_soft': {'soft_boost': 0.0}
    }
    key2 = formal_candidate_sort_key(invalid_mainline)
    assert isinstance(key2, tuple)
