#!/usr/bin/env python3
"""Tests for xiaogu_regime_policy single-owner dynamic gates."""
from __future__ import annotations

from xiaogu_regime_policy import (
    PRODUCTION_REGIMES,
    SELF_EVOLVE_BOUNDS,
    classify_production_regime,
    market_adaptive_thresholds,
    sector_gate_threshold_for_market,
    preferred_shadow_variant,
    clamp_self_evolve_value,
    attach_regime_to_context,
    quality_escape_score_floor,
)


def test_production_regimes_cover_five_labels():
    assert set(PRODUCTION_REGIMES) == {"strong", "weak", "sideways", "climax", "no_main"}


def test_classify_strong_weak_sideways():
    assert classify_production_regime({"market_regime": "strong", "supportive_market": True}) == "strong"
    assert classify_production_regime({"market_regime": "weak", "weak_acceptance_market": True}) == "weak"
    assert classify_production_regime({"market_regime": "neutral"}) == "sideways"


def test_classify_climax_and_no_main_from_soft():
    assert (
        classify_production_regime(
            {"market_regime": "neutral", "overheated_market": True},
            None,
        )
        == "climax"
    )
    assert (
        classify_production_regime(
            {"market_regime": "weak", "weak_acceptance_market": True},
            {"mainline_stage_hint": "NO_MAIN"},
        )
        == "no_main"
    )
    assert (
        classify_production_regime(
            {"market_regime": "strong", "supportive_market": True},
            {"mainline_stage_hint": "CLIMAX"},
        )
        == "climax"
    )


def test_thresholds_legacy_weak_supportive_overheated_numbers():
    weak = market_adaptive_thresholds("high_7_to_9", {"weak_acceptance_market": True})
    assert weak["component_min"] == 0.70
    assert weak["buy_confirmation_min"] == 0.68

    strong = market_adaptive_thresholds(
        "high_7_to_9",
        {"supportive_market": True, "weak_acceptance_market": False},
    )
    assert strong["component_min"] == 0.55
    assert strong["dynamic_required_confirmations"] == 3

    hot = market_adaptive_thresholds(
        "high_7_to_9",
        {"overheated_market": True, "weak_acceptance_market": False, "supportive_market": False},
    )
    assert hot["component_min"] == 0.66


def test_sector_gate_by_regime():
    assert sector_gate_threshold_for_market({"market_regime": "weak", "weak_acceptance_market": True}) == 0.2
    assert sector_gate_threshold_for_market({"market_regime": "strong", "supportive_market": True}) == 0.5
    assert sector_gate_threshold_for_market({"market_regime": "neutral"}) == 0.4
    ctx = attach_regime_to_context(
        {"market_regime": "weak", "weak_acceptance_market": True},
        {"mainline_stage_hint": "NO_MAIN"},
    )
    assert ctx["production_regime"] == "no_main"
    assert sector_gate_threshold_for_market(ctx) == 0.15


def test_preferred_shadow_and_escape_floor():
    assert preferred_shadow_variant("strong") == "limitup_gene_shadow_plus"
    assert preferred_shadow_variant("weak") == "weak_market_defensive_shadow"
    assert preferred_shadow_variant("climax") == "risk_penalty_shadow_plus"
    assert quality_escape_score_floor({"production_regime": "no_main"}) == 62.0
    assert quality_escape_score_floor({"production_regime": "climax"}) == 72.0


def test_self_evolve_bounds_clamp_and_forbid_unknown():
    assert "evidence_limitup_momentum_weight" in SELF_EVOLVE_BOUNDS
    assert clamp_self_evolve_value("evidence_limitup_momentum_weight", 9.0) == 1.5
    assert clamp_self_evolve_value("evidence_limitup_momentum_weight", 0.1) == 0.4
    assert clamp_self_evolve_value("formal_candidate_sort_key", 1.0) is None


def test_runner_delegates_thresholds():
    import xiaogu_forward_d1_1450_runner_v0_1 as runner

    ctx = {
        "supportive_market": True,
        "weak_acceptance_market": False,
        "overheated_market": False,
        "market_regime": "strong",
    }
    a = runner.market_adaptive_thresholds("high_7_to_9", ctx)
    b = market_adaptive_thresholds("high_7_to_9", ctx)
    assert a == b
    assert runner.sector_gate_threshold_for_market({"market_regime": "weak", "weak_acceptance_market": True}) == 0.2


def test_safe_self_evolve_uses_regime_table_bounds():
    import scripts.xiaogu_safe_self_evolve as evolve

    assert evolve.ALLOWED_KEYS["evidence_limitup_momentum_weight"] == SELF_EVOLVE_BOUNDS[
        "evidence_limitup_momentum_weight"
    ]


def test_ranking_evidence_scales_default_sideways_is_identity():
    from xiaogu_regime_policy import resolve_ranking_evidence_scales

    scales = resolve_ranking_evidence_scales({}, "sideways")
    assert scales["limitup_scale"] == 1.0
    assert scales["catalyst_scale"] == 1.0
    assert scales["broken_scale"] == 1.0
    assert scales["production_regime"] == "sideways"


def test_ranking_evidence_scales_self_evolve_weight_and_regime():
    from xiaogu_regime_policy import resolve_ranking_evidence_scales

    base = resolve_ranking_evidence_scales(
        {"evidence_limitup_momentum_weight": 0.7}, "sideways"
    )
    evolved = resolve_ranking_evidence_scales(
        {"evidence_limitup_momentum_weight": 1.1}, "sideways"
    )
    strong = resolve_ranking_evidence_scales(
        {"evidence_limitup_momentum_weight": 1.1}, "strong"
    )
    climax = resolve_ranking_evidence_scales(
        {"evidence_limitup_momentum_weight": 1.1}, "climax"
    )
    assert evolved["limitup_scale"] > base["limitup_scale"]
    assert strong["limitup_scale"] > evolved["limitup_scale"]
    assert climax["limitup_scale"] < evolved["limitup_scale"]
    # 1.1 / 0.7 ≈ 1.5714
    assert abs(evolved["limitup_scale"] - (1.1 / 0.7)) < 1e-3
