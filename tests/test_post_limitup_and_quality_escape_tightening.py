"""Regression coverage for the production main-force behavior gates."""
from __future__ import annotations

import xiaogu_forward_d1_1450_runner_v0_1 as runner
from tests.test_xiaogu_a_share_forward_runner import (
    full_candidate_evidence_counts,
    make_bundle,
    make_candidate,
)


def _weak_market_bundle(candidates):
    return make_bundle(
        candidates,
        candidate_source="eastmoney_api_scan_v2",
        market_snapshot={
            "market_breadth_up_pct": 26.0,
            "market_limitups": 78,
            "broken_limitups": 36,
            "limitup_broken_ratio": 1.27,
            "market_regime": "weak",
        },
        market_regime="weak",
    )


def _candidate(symbol="000975", name="山金国际", **kwargs):
    required, enhanced = full_candidate_evidence_counts()
    params = {
        "score": 80.0,
        "rank": 5,
        "price": 22.81,
        "sector_score": 1.0,
        "search_layer_hint": "formal_high_score",
        "setup_type": "ACCUMULATION_READY",
        "candidate_stage": "flat_0_to_3",
        "signal_pct": 2.0,
        "close_position_score": 0.70,
        "fund_flow_momentum": 0.55,
        "time_series_momentum": 0.20,
        "research_panel_overall": "PASS",
        "mainboard_auxiliary_evidence_status": "PASS",
        "mainboard_auxiliary_confidence": 0.80,
        "candidate_evidence_domain_counts": required,
        "enhanced_evidence_domain_counts": enhanced,
    }
    params.update(kwargs)
    row = make_candidate(symbol, name, **params)
    row["trade_date"] = "2026-08-21"
    row["source_layers"] = ["L0_FULL_UNIVERSE", "L1_HOT_MOMENTUM"]
    return row


def test_post_limitup_weak_continuation_blocks_paper_pick():
    row = _candidate(
        signal_pct=1.29,
        close_position_score=0.70,
        fund_flow_momentum=0.20,
        main_theme_core_score=0.10,
        main_theme_alignment_score=0.10,
    )
    row["yesterday_limitup_gene_evidence"] = {
        "status": "PASS",
        "candidate_was_yesterday_limitup": True,
    }
    row["prev_day_pct_chg"] = 10.01
    eligibility = runner.paper_pick_eligibility_profile(
        row,
        _weak_market_bundle([row]),
    )
    assert eligibility["signals"]["post_limitup_weak_continuation"] is True
    assert "post_limitup_weak_continuation" in eligibility["blockers"]
    assert eligibility["eligible"] is False


def test_hollow_theme_fund_shell_blocks_without_catalyst():
    row = _candidate(
        "600186",
        "莲花控股",
        sector_score=0.2,
        signal_pct=1.19,
        close_position_score=0.50,
        fund_flow_momentum=1.0,
        main_theme_core_score=0.0,
        main_theme_alignment_score=0.0,
        sector_opportunity_tags=["消费", "食品"],
    )
    row.update({
        "news_catalyst_strength": 0.0,
        "announcement_catalyst_score": 0.0,
        "sector_catalyst_score": 0.0,
        "sector_news_catalyst_score": 0.36,
    })
    eligibility = runner.paper_pick_eligibility_profile(row, _weak_market_bundle([row]))
    assert eligibility["signals"]["hollow_theme_fund_shell"] is True
    assert "hollow_theme_fund_shell" in eligibility["blockers"]
    assert eligibility["eligible"] is False


def test_strong_continuation_can_escape_hollow_theme_but_not_sealed_limit():
    row = _candidate(
        "000811",
        "冰轮环境",
        signal_pct=2.13,
        close_position_score=0.75,
        fund_flow_momentum=0.70,
        main_theme_core_score=0.0,
        main_theme_alignment_score=0.0,
    )
    row["volume_ratio"] = 1.02
    row["continuation_gene_score"] = 0.70
    row["yesterday_limitup_gene_evidence"] = {
        "status": "PROXY",
        "candidate_was_yesterday_limitup": True,
    }
    row["prev_day_pct_chg"] = 10.01
    eligibility = runner.paper_pick_eligibility_profile(row, _weak_market_bundle([row]))
    assert eligibility["signals"]["strong_hollow_theme_confirmation_escape"] is True
    assert eligibility["eligible"] is True

    row["sealed_limit_up"] = True
    sealed = runner.paper_pick_eligibility_profile(row, _weak_market_bundle([row]))
    assert sealed["eligible"] is False
    assert "FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP" in sealed["blockers"]


def test_high_core_chase_with_weak_fund_is_blocked():
    row = _candidate(
        "601666",
        "平煤股份",
        signal_pct=6.83,
        close_position_score=0.65,
        fund_flow_momentum=0.376,
        main_theme_core_score=0.95,
        main_theme_alignment_score=1.0,
        sector_opportunity_tags=["煤炭"],
    )
    row.update({
        "news_catalyst_strength": 0.0,
        "announcement_catalyst_score": 0.0,
        "continuation_gene_score": 0.10,
    })
    eligibility = runner.paper_pick_eligibility_profile(row, _weak_market_bundle([row]))
    assert eligibility["signals"]["high_core_chase_weak_fund"] is True
    assert "high_core_chase_weak_fund" in eligibility["blockers"]
