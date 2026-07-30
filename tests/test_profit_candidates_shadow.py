"""Unit tests for shadow profit candidates (no network, no official gate changes)."""
from __future__ import annotations

import json
from pathlib import Path

import scripts.xiaogu_profit_candidates_shadow as pcs


def test_tradable_filter_mainboard_and_chase():
    ok, reason = pcs.tradable_filter(
        {"code": "002185", "price": 12.0, "signal_pct": 5.2, "net_inflow_main": 1e8}
    )
    assert ok and reason == "ok"

    ok, reason = pcs.tradable_filter(
        {"code": "300750", "price": 12.0, "signal_pct": 5.2, "net_inflow_main": 1e8}
    )
    assert not ok and reason == "not_mainboard"

    ok, reason = pcs.tradable_filter(
        {"code": "002185", "price": 80.0, "signal_pct": 5.2, "net_inflow_main": 1e8}
    )
    assert not ok and reason == "price_over_cap"

    ok, reason = pcs.tradable_filter(
        {"code": "002185", "price": 12.0, "signal_pct": 10.0, "net_inflow_main": 1e8}
    )
    assert not ok and reason == "pct_above_max_chase"

    ok, reason = pcs.tradable_filter(
        {"code": "002185", "price": 12.0, "signal_pct": 5.2, "net_inflow_main": -1e6}
    )
    assert not ok and reason == "main_force_not_inflow"


def test_build_prefers_mainline_plus_inflow():
    sector_flow = {
        "mainline_tags": ["半导体", "集成电路封测", "中芯概念"],
        "industry_top": [{"name": "半导体", "net_inflow_yi": 40.0}],
        "concept_top": [],
    }
    scored = [
        {
            "code": "002185",
            "name": "华天科技",
            "price": 15.0,
            "signal_pct": 5.2,
            "net_inflow_main": 5e8,
            "industry": "集成电路封测",
            "candidate_stage": "mid_5_to_7",
            "early_opportunity_score": 0.6,
        },
        {
            "code": "601678",
            "name": "滨化股份",
            "price": 6.5,
            "signal_pct": 5.3,
            "net_inflow_main": 1.3e8,
            "industry": "化学原料",
            "candidate_stage": "mid_5_to_7",
            "early_opportunity_score": 0.7,
        },
        {
            "code": "600000",
            "name": "浦发银行",
            "price": 10.0,
            "signal_pct": 1.0,
            "net_inflow_main": 9e8,
            "industry": "银行",
            "candidate_stage": "flat_0_to_3",
            "early_opportunity_score": 0.5,
        },
        {
            "code": "300750",
            "name": "宁德时代",
            "price": 200.0,
            "signal_pct": 3.0,
            "net_inflow_main": 20e8,
            "industry": "电池",
            "candidate_stage": "early_3_to_5",
        },
    ]
    built = pcs.build_profit_candidates(scored, sector_flow, top_n=3)
    symbols = [c["symbol"] for c in built["candidates"]]
    assert "002185" in symbols
    assert symbols[0] == "002185"
    assert "300750" not in symbols  # not mainboard / over price
    assert all(c["not_official_paper_pick"] for c in built["candidates"])
    assert all(c["decision_class"] == "PROFIT_CANDIDATE_SHADOW" for c in built["candidates"])


def test_noise_sectors_filtered_from_mainline(tmp_path: Path):
    flow = tmp_path / "flow_concept.jsonl"
    rows = [
        {"f14": "历史新高", "f62": 9e9, "f3": 3.0, "f12": "BK1"},
        {"f14": "中芯概念", "f62": 3e9, "f3": 1.5, "f12": "BK2"},
        {"f14": "半导体概念", "f62": 2e9, "f3": 0.5, "f12": "BK3"},
    ]
    flow.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    (tmp_path / "flow_industry.jsonl").write_text("", encoding="utf-8")
    sector = pcs.load_sector_flows(tmp_path, top_n=5)
    names = [x["name"] for x in sector["concept_top"]]
    assert "历史新高" not in names
    assert "中芯概念" in names


def test_mainline_stem_match_for_sparse_industry():
    """Stock tags may omit industry name; multi-char synonym still links to 半导体 mainline."""
    score, hits = pcs.mainline_match_score(
        {
            "name": "华天科技",
            "sector_opportunity_tags": ["氮化镓", "传感器", "5G概念"],
            "industry": None,
        },
        ["半导体", "集成电路封测", "中芯概念"],
    )
    assert score > 0
    assert hits


def test_mainline_rejects_copper_cable_false_positive():
    """铜缆高速连接 must not map 消费电子 to 有色 mainline."""
    score, hits = pcs.mainline_match_score(
        {
            "name": "立讯精密",
            "industry": "电子",
            "sector": "电子",
            "sector_opportunity_tags": ["铜缆高速连接", "消费电子概念", "5G概念", "数据中心"],
        },
        ["有色金属", "小金属概念", "黄金"],
    )
    assert score == 0.0
    assert hits == []


def test_chip_tag_does_not_cross_wire_to_packaging_board():
    """国产芯片 tag must not hit 集成电路封测 unless packaging stems present."""
    score, hits = pcs.mainline_match_score(
        {
            "name": "中兴通讯",
            "industry": "通信网络设备及器件",
            "sector_opportunity_tags": ["云计算", "边缘计算", "国产芯片", "人工智能"],
        },
        ["集成电路封测", "有色金属", "黄金"],
    )
    assert "集成电路封测" not in hits


def test_industry_mainline_outranks_concept_only_mega_inflow():
    sector_flow = {
        "mainline_tags": ["有色金属", "黄金", "边缘计算", "云计算"],
        "industry_top": [{"name": "有色金属", "net_inflow_yi": 20.0}, {"name": "黄金", "net_inflow_yi": 10.0}],
        "concept_top": [{"name": "边缘计算", "net_inflow_yi": 5.0}],
    }
    scored = [
        {
            "code": "000063",
            "name": "中兴通讯",
            "price": 35.0,
            "signal_pct": 7.5,
            "net_inflow_main": 30e8,
            "industry": "通信网络设备及器件",
            "sector_opportunity_tags": ["边缘计算", "云计算"],
            "candidate_stage": "high_7_to_9",
            "early_opportunity_score": 0.5,
        },
        {
            "code": "601899",
            "name": "紫金矿业",
            "price": 32.0,
            "signal_pct": 6.5,
            "net_inflow_main": 16e8,
            "industry": "有色金属",
            "sector_opportunity_tags": ["黄金概念"],
            "candidate_stage": "mid_5_to_7",
            "early_opportunity_score": 0.6,
        },
    ]
    built = pcs.build_profit_candidates(scored, sector_flow, top_n=2)
    assert built["candidates"][0]["symbol"] == "601899"
    assert built["candidates"][0]["selection_role"] == "industry_mainline"


def test_compare_shadow_vs_official_no_pick_day(tmp_path, monkeypatch):
    monkeypatch.setattr(pcs, "SUMMARY", tmp_path)
    formal = tmp_path / "2026-07-24_formal_paper_pick.json"
    formal.write_text(json.dumps({"trade_date": "2026-07-24", "decision": "NO_PICK", "symbol": ""}), encoding="utf-8")
    shadow_day = {
        "candidates": [
            {
                "symbol": "002185",
                "name": "华天科技",
                "profit_score": 99.0,
                "mainline_hits": ["半导体"],
                "t1": {"status": "OK", "ret_t1_close": 0.03},
            }
        ]
    }
    cmp = pcs.compare_shadow_vs_official("2026-07-24", shadow_day, with_returns=False)
    assert cmp["official"]["decision"] == "NO_PICK"
    assert cmp["shadow_top1"]["symbol"] == "002185"
    assert cmp["shadow_beats_official"] is True  # positive T+1 while official NO_PICK


def test_scan_fingerprint_matches_identical_flow(tmp_path: Path):
    net_by_code = {"002185": 5e8, "600000": 3e8, "000001": 1e8}

    def write_scan(base: Path, flow_payload: str, codes: list[str]) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        (base / "flow_industry.jsonl").write_text(flow_payload, encoding="utf-8")
        (base / "flow_concept.jsonl").write_text("", encoding="utf-8")
        lines = []
        for code in codes:
            lines.append(
                json.dumps(
                    {
                        "code": code,
                        "name": f"n{code}",
                        "net_inflow_main": net_by_code[code],
                        "signal_pct": 3.0,
                    },
                    ensure_ascii=False,
                )
            )
        (base / "eastmoney_web_tabs_scored.jsonl").write_text("\n".join(lines), encoding="utf-8")
        return base

    flow = json.dumps({"f14": "半导体", "f62": 9e9, "f3": 1.0, "f12": "BK1"}, ensure_ascii=False) + "\n"
    a = write_scan(tmp_path / "a", flow, ["002185", "600000", "000001"])
    b = write_scan(tmp_path / "b", flow, ["600000", "002185", "000001"])  # order differs, same nets
    assert pcs.scan_content_fingerprint(a) == pcs.scan_content_fingerprint(b)
    c = write_scan(
        tmp_path / "c",
        json.dumps({"f14": "有色金属", "f62": 5e9, "f3": 1.0, "f12": "BK2"}, ensure_ascii=False) + "\n",
        ["002185", "600000", "000001"],
    )
    assert pcs.scan_content_fingerprint(a) != pcs.scan_content_fingerprint(c)


def test_find_stale_scan_source_from_known():
    known = {"abc123": "2026-07-10"}
    assert pcs.find_stale_scan_source("2026-07-11", "abc123", known=known) == "2026-07-10"
    assert pcs.find_stale_scan_source("2026-07-10", "abc123", known=known) is None
    assert pcs.find_stale_scan_source("2026-07-11", "other", known=known) is None


def test_compute_basket_stats_top_strategies():
    cands = [
        {"rank": 1, "t1": {"ret_t1_close": -0.01}},
        {"rank": 2, "t1": {"ret_t1_close": 0.03}},
        {"rank": 3, "t1": {"ret_t1_close": 0.05}},
        {"rank": 4, "ret_t1_close": None},
        {"rank": 5, "t1": {"ret_t1_close": 0.01}},
    ]
    stats = pcs.compute_basket_stats(cands)
    assert stats["top1"]["ret"] == -0.01
    assert stats["top1"]["win"] is False
    assert stats["top2_eq"]["ret"] == round((-0.01 + 0.03) / 2, 6)
    assert stats["top3_eq"]["ret"] == round((-0.01 + 0.03 + 0.05) / 3, 6)
    assert stats["basket_eq"]["n"] == 4
    assert stats["best_in_basket"]["ret"] == 0.05
    assert stats["best_in_basket"]["win"] is True


def test_aggregate_strategy_stats_cross_day():
    day_stats = [
        pcs.compute_basket_stats(
            [
                {"t1": {"ret_t1_close": 0.02}},
                {"t1": {"ret_t1_close": 0.04}},
            ]
        ),
        pcs.compute_basket_stats(
            [
                {"t1": {"ret_t1_close": -0.03}},
                {"t1": {"ret_t1_close": 0.05}},  # day eq = +0.01
            ]
        ),
    ]
    agg = pcs.aggregate_strategy_stats(day_stats)
    assert agg["top1"]["n_days"] == 2
    assert agg["top1"]["win_rate"] == 0.5
    assert agg["top2_eq"]["n_days"] == 2
    assert agg["top2_eq"]["win_rate"] == 1.0  # both day-eq positive
    assert agg["top2_eq"]["avg_ret"] == round((0.03 + 0.01) / 2, 6)


def test_run_for_date_marks_stale_and_excludes_from_conclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(pcs, "SUMMARY", tmp_path / "summary")
    monkeypatch.setattr(pcs, "LIVE_SCAN", tmp_path / "live")
    (tmp_path / "summary").mkdir()

    def make_day(day: str, flow_name: str) -> None:
        scan = tmp_path / "live" / day / "eastmoney_scan_afternoon"
        scan.mkdir(parents=True)
        flow = json.dumps({"f14": flow_name, "f62": 8e9, "f3": 1.0, "f12": "BK1"}, ensure_ascii=False)
        (scan / "flow_industry.jsonl").write_text(flow + "\n", encoding="utf-8")
        (scan / "flow_concept.jsonl").write_text("", encoding="utf-8")
        rows = [
            {
                "code": "002185",
                "name": "华天科技",
                "price": 12.0,
                "signal_pct": 5.0,
                "net_inflow_main": 5e8,
                "industry": flow_name,
                "candidate_stage": "mid_5_to_7",
            },
            {
                "code": "600000",
                "name": "浦发银行",
                "price": 10.0,
                "signal_pct": 1.0,
                "net_inflow_main": 1e8,
                "industry": "银行",
                "candidate_stage": "flat_0_to_3",
            },
        ]
        (scan / "eastmoney_web_tabs_scored.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
            encoding="utf-8",
        )

    make_day("2026-07-10", "半导体")
    make_day("2026-07-11", "半导体")  # identical flow + top codes → stale
    known: dict = {}
    d0 = pcs.run_for_date("2026-07-10", with_returns=False, known_fingerprints=known)
    d1 = pcs.run_for_date("2026-07-11", with_returns=False, known_fingerprints=known)
    assert d0["status"] == "OK"
    assert d0["valid_for_conclusion"] is True
    assert d1["status"] == "STALE_SCAN"
    assert d1["valid_for_conclusion"] is False
    assert d1["stale_of"] == "2026-07-10"


def test_limitup_merge_keeps_exclusion_diagnostics_but_not_candidate_seats():
    """Current-day sealed names never enter the tradable shadow candidate pool."""
    scored = [
        {
            "code": "600000",
            "name": "浦发银行",
            "price": 10.0,
            "signal_pct": 1.0,
            "net_inflow_main": 2e8,
            "industry": "银行",
            "candidate_stage": "flat_0_to_3",
        },
        {
            "code": "002185",
            "name": "华天科技",
            "price": 12.0,
            "signal_pct": 5.0,
            "net_inflow_main": 3e8,
            "industry": "半导体",
            "candidate_stage": "mid_5_to_7",
        },
    ]
    limitups = [
        {
            "code": "002156",
            "name": "通富微电",
            "price": 76.8,  # over normal 70, under limitup cap 150
            "signal_pct": 10.0,
            "net_inflow_main": 8.8e8,
            "industry": "半导体",
            "from_limitup_pool": True,
            "limitup_fund": 8.8e8,
            "limitup_reason": "半导体",
            "candidate_stage": "near_limit_9_plus",
            "sector_opportunity_tags": ["半导体", "涨停"],
        },
        {
            "code": "000768",
            "name": "中航西飞",
            "price": 30.0,
            "signal_pct": 10.0,
            "net_inflow_main": 5e8,
            "industry": "地面兵装",
            "from_limitup_pool": True,
            "limitup_fund": 5e8,
            "limitup_reason": "军工",
            "candidate_stage": "near_limit_9_plus",
            "limitup_theme_count": 3,
            "sector_opportunity_tags": ["地面兵装", "军工", "涨停"],
        },
        {
            "code": "600967",
            "name": "内蒙一机",
            "price": 20.0,
            "signal_pct": 10.0,
            "net_inflow_main": 4e8,
            "industry": "地面兵装",
            "from_limitup_pool": True,
            "limitup_fund": 4e8,
            "limitup_reason": "军工",
            "candidate_stage": "near_limit_9_plus",
            "limitup_theme_count": 3,
            "sector_opportunity_tags": ["地面兵装", "军工", "涨停"],
        },
    ]
    merged, meta = pcs.merge_scored_with_limitup(scored, limitups)
    codes = {_code_safe(r) for r in merged}
    assert "002156" in codes
    assert meta["limitup_added_missing_from_scored"] == 3

    sector_flow = {
        "mainline_tags": ["半导体"],
        "industry_top": [{"name": "半导体", "net_inflow_yi": 20.0}],
        "concept_top": [],
    }
    built = pcs.build_profit_candidates(merged, sector_flow, top_n=5)
    symbols = [c["symbol"] for c in built["candidates"]]
    assert "002156" not in symbols
    assert not any(c.get("from_limitup_pool") for c in built["candidates"])
    assert not any(c.get("selection_role") == "limitup_watch" for c in built["candidates"])
    assert built.get("limitup_watch") == []


def _code_safe(row: dict) -> str:
    return str(row.get("code") or row.get("symbol") or "").zfill(6)[-6:]


def test_fit_rank_weights_raises_correlated_feature():
    """Feature strongly aligned with ret should gain weight after Spearman blend."""
    rows = []
    for i in range(30):
        # inflow_norm tracks return; mainline_score anti-tracks
        ret = (i - 15) / 100.0
        rows.append(
            {
                "ret": ret,
                "features": {
                    "inflow_norm": float(i) / 30.0,
                    "mainline_score": 1.0 - float(i) / 30.0,
                    "industry_boost": 0.5,
                    "stage_w": 0.5,
                    "early_theme": 0.5,
                    "limitup_boost": 0.0,
                    "theme_cluster_boost": 0.0,
                },
            }
        )
    fit = pcs.fit_rank_weights_from_returns(rows, min_n=20)
    assert fit["status"] == "OK"
    assert fit["n"] == 30
    assert fit["correlations"]["inflow_norm"] > 0.5
    assert fit["correlations"]["mainline_score"] < 0
    # Positive-corr feature gains vs default; anti-corr loses vs default
    assert fit["weights"]["inflow_norm"] > pcs.DEFAULT_RANK_WEIGHTS["inflow_norm"]
    assert fit["weights"]["mainline_score"] < pcs.DEFAULT_RANK_WEIGHTS["mainline_score"]
    assert fit["weights"]["inflow_norm"] > fit["weights"]["mainline_score"]
    assert fit["official_gates_unchanged"] is True


def test_load_limitup_pool_parses_eastmoney_fields(tmp_path: Path):
    scan = tmp_path / "scan"
    scan.mkdir()
    # Eastmoney limitup pool: p in 厘 (price*1000), fund sealed amount
    row = {
        "c": "002156",
        "n": "通富微电",
        "p": 76800,  # 76.80 yuan in 厘
        "zdp": 10.01,
        "fund": 880000000,
        "amount": 2e9,
        "hybk": "半导体",
    }
    (scan / "limitup_pool.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rows = pcs.load_limitup_pool(scan)
    assert len(rows) == 1
    assert rows[0]["code"] == "002156"
    assert rows[0]["from_limitup_pool"] is True
    assert abs(rows[0]["price"] - 76.8) < 0.01
    assert rows[0]["limitup_fund"] == 880000000
    ok, reason = pcs.tradable_filter_shadow(rows[0])
    assert not ok and reason == "current_day_limitup_not_tradable"
