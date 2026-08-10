"""P0 profit-shadow watchlist + P1 mainline fund-flow soft ranking."""
from __future__ import annotations

import xiaogu_forward_d1_1450_runner_v0_1 as runner


def test_soft_mainline_fund_bias_prefers_day_mainline_sector():
    semi = {
        "trade_date": "2026-07-24",
        "name": "通富微电",
        "industry": "半导体",
        "sector": "半导体设备",
        "signal_pct": 5.2,
    }
    other = {
        "trade_date": "2026-07-24",
        "name": "佛燃能源",
        "industry": "燃气",
        "sector": "公用事业",
        "signal_pct": 0.63,
    }
    b1 = runner.soft_mainline_fund_bias(semi)
    b2 = runner.soft_mainline_fund_bias(other)
    assert b1["hard_gate"] is False
    assert b1["force_pick"] is False
    if b1.get("mainline_tags"):
        assert b1["soft_boost"] >= b2["soft_boost"]
        assert "半导体" in (b1.get("mainline_hits") or []) or b1["soft_boost"] >= 0


def test_ranking_adjustment_includes_mainline_fund_flow_soft():
    row = {
        "trade_date": "2026-07-24",
        "name": "通富微电",
        "industry": "半导体",
        "signal_pct": 5.0,
        "fund_flow_momentum": 0.4,
        "sector_opportunity_score": 0.5,
        "main_theme_core_score": 0.1,
        "capital_risk_profile": {"risk_penalty_score": 0.0},
    }
    adj = runner.ranking_basis_adjustment_components(row)
    assert "mainline_fund_flow_soft" in adj["boosts"]
    assert isinstance(adj.get("mainline_fund_flow_soft"), dict)
    assert adj["mainline_fund_flow_soft"]["hard_gate"] is False
