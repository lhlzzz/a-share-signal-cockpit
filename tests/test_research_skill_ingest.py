"""Research Skill ingest: existing owners interpret captured observations."""
from datetime import datetime
from pathlib import Path

from scrapy_scanner.runner_v2 import detect_capital_candidates
from xiaogu_core_alpha import build_core_alpha
from xiaogu_forward_eligibility import cheap_eligibility_blockers, execution_universe
from xiaogu_forward_features import build_feature_vector
from xiaogu_forward_result_filler_v0_1 import calculate_horizon_outcomes
from xiaogu_forward_snapshot import attach_research_observations, validate_and_build_canonical_snapshot
from xiaogu_portfolio_decision import attach_top_paper_observations, evaluate_candidate_bundle
from xiaogu_research_context import build_integrated_research_context


AS_OF = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
ROOT = Path(__file__).resolve().parents[1]


def _base_row(**extra):
    payload = {
        "f12": "600001",
        "f14": "示例公司",
        "f100": "示例行业",
        "f2": 10,
        "f3": 4,
        "f5": 100,
        "f6": 1_000,
        "f7": 3,
        "f15": 10.5,
        "f16": 9.5,
        "f17": 9.8,
        "f62": 100,
        "source_time": "2026-08-26T14:50:00+08:00",
        "buyable": True,
        "liquidity_score": 1,
        "execution_quality": 1,
        "gap_risk": 0,
        "slippage": 0,
        "spread": 0,
        "market_impact": 0,
        "trade_date": "2026-08-26",
        "market": "SH",
        "f13": 1,
        "f1": 2,
    }
    payload.update(extra)
    return payload


def _deep_snapshot(**extra):
    return validate_and_build_canonical_snapshot(attach_research_observations(
        _base_row(**extra),
        stock_capital_flow={
            "f62": 400,
            "observed_at": "2026-08-26T14:49:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.capital_flow",
            "event_id": "600001-2026-08-26-flow",
            "mechanism": "CAPITAL",
        },
        earnings_preview={
            "WEIGHTAVG_ROE": 20,
            "publication_time": "2026-08-26T14:40:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.earnings_preview",
            "event_id": "600001-2026-08-26-preview",
            "mechanism": "VALUATION",
        },
        industry_flow={
            "f3": 5,
            "observed_at": "2026-08-26T14:49:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.industry_flow",
            "event_id": "industry-2026-08-26",
            "mechanism": "DEMAND",
        },
        stock_reports=[{
            "title": "公司研究",
            "publication_time": "2026-08-26T14:40:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.stock_report",
            "event_id": "company-report-1",
            "mechanism": "VALUATION",
        }],
        industry_reports=[{
            "title": "行业研究",
            "publication_time": "2026-08-26T14:40:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.industry_report",
            "event_id": "industry-report-1",
            "mechanism": "DEMAND",
        }],
        lhb=[{
            "EXPLAIN": "1家机构买入",
            "NET_BS_AMT": -100,
            "ACCUM_AMOUNT": 100,
            "event_time": "2026-08-26T14:45:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.lhb",
            "event_id": "600001-lhb-1",
            "mechanism": "CAPITAL",
        }],
        announcements=[{
            "title": "产能公告",
            "publication_time": "2026-08-26T14:30:00+08:00",
            "available_at": "2026-08-26T14:50:00+08:00",
            "source_id": "eastmoney.announcement",
            "event_id": "ann-1",
            "mechanism": "CATALYST",
        }],
    ))


def test_owner_map_uses_existing_unique_functions():
    import scrapy_scanner.runner_v2 as scanner
    import xiaogu_core_alpha as alpha
    import xiaogu_forward_eligibility as eligibility
    import xiaogu_forward_result_filler_v0_1 as filler
    import xiaogu_portfolio_decision as decision
    import xiaogu_research_context as research

    assert scanner.detect_capital_candidates is detect_capital_candidates
    assert eligibility.cheap_eligibility_blockers is cheap_eligibility_blockers
    assert eligibility.execution_universe is execution_universe
    assert research.build_integrated_research_context is build_integrated_research_context
    assert alpha.build_core_alpha is build_core_alpha
    assert decision.attach_top_paper_observations is attach_top_paper_observations
    assert filler.calculate_horizon_outcomes is calculate_horizon_outcomes
    assert detect_capital_candidates.__module__ == "scrapy_scanner.runner_v2"
    assert cheap_eligibility_blockers.__module__ == "xiaogu_forward_eligibility"
    assert execution_universe.__module__ == "xiaogu_forward_eligibility"
    assert build_integrated_research_context.__module__ == "xiaogu_research_context"
    assert build_core_alpha.__module__ == "xiaogu_core_alpha"
    assert attach_top_paper_observations.__module__ == "xiaogu_portfolio_decision"
    assert calculate_horizon_outcomes.__module__ == "xiaogu_forward_result_filler_v0_1"
    assert not (ROOT / "xiaogu_research_v2.py").exists()
    assert not (ROOT / "xiaogu_skill_ranker.py").exists()


def test_buffett_skill_is_vendored_without_buy_action():
    skill = ROOT / ".agents" / "skills" / "buffett" / "SKILL.md"
    assert skill.exists()
    snapshot = _deep_snapshot()
    features = build_feature_vector(snapshot)
    company = build_integrated_research_context(snapshot, features)["company"]
    assert company.get("buy_sell") is None
    assert company.get("recommended_buy_price") is None
    assert "BUY" not in str(company.get("checklist") or "")


def test_serenity_interprets_captured_industry_evidence():
    snapshot = _deep_snapshot()
    features = build_feature_vector(snapshot)
    industry = build_integrated_research_context(snapshot, features)["industry"]
    assert industry["skill_ran"] is True
    assert industry["reports"][0]["title"] == "行业研究"
    evidence = industry.get("evidence") or []
    assert evidence
    assert evidence[0]["source_id"]
    assert evidence[0]["event_id"]
    assert evidence[0]["mechanism"]
    light = validate_and_build_canonical_snapshot(_base_row())
    light_industry = build_integrated_research_context(light, build_feature_vector(light))["industry"]
    assert light_industry["skill_ran"] is False


def test_buffett_checklist_has_no_trade_action():
    snapshot = _deep_snapshot()
    features = build_feature_vector(snapshot)
    company = build_integrated_research_context(snapshot, features)["company"]
    assert company["skill_ran"] is True
    assert company["earnings_preview"]["WEIGHTAVG_ROE"] == 20
    assert company["buy_sell"] is None
    assert isinstance(company.get("checklist"), list)
    assert len(company["checklist"]) == 8
    light = validate_and_build_canonical_snapshot(_base_row())
    light_company = build_integrated_research_context(light, build_feature_vector(light))["company"]
    assert light_company["skill_ran"] is False


def test_uzi_interprets_captured_lhb_without_judges():
    snapshot = _deep_snapshot()
    features = build_feature_vector(snapshot)
    capital = build_integrated_research_context(snapshot, features)["capital"]
    assert capital["skill_ran"] is True
    assert capital["institution_vs_hot_money"] == "institution"
    source = Path("xiaogu_research_context.py").read_text(encoding="utf-8")
    assert "investor_evaluator" not in source
    assert "65" not in source or "评委" not in source
    assert "评委" not in source
    light = validate_and_build_canonical_snapshot(_base_row())
    light_capital = build_integrated_research_context(light, build_feature_vector(light))["capital"]
    assert light_capital["skill_ran"] is False


def test_uncalibrated_ranking_uses_research_not_price_strength():
    from xiaogu_portfolio_decision import attach_top_paper_observations

    hot = evaluate_candidate_bundle(_base_row(f12="600001", f3=9.0), position_state="FLAT", as_of=AS_OF)
    researched = evaluate_candidate_bundle(
        _deep_snapshot(f12="600002", symbol="600002", f3=1.0),
        position_state="FLAT",
        as_of=AS_OF,
    )
    assert hot["core_alpha"]["model_status"] != "VALIDATED"
    assert researched["core_alpha"]["model_status"] != "VALIDATED"
    assert hot["core_alpha"]["selection_score_source"] == "research_thesis"
    assert researched["core_alpha"]["selection_score_source"] == "research_thesis"
    assert hot["core_alpha"]["signal_qualified"] is False
    assert researched["core_alpha"]["signal_qualified"] is True
    assert researched["core_alpha"]["selection_score"] is not None
    ranked = attach_top_paper_observations([hot, researched])
    papers = [item["paper_observation"] for item in ranked if item.get("paper_observation")]
    assert [paper["symbol"] for paper in papers] == ["600002"]
    assert papers[0]["top1_flag"] is True
    assert papers[0]["alpha_name"] == "research_thesis"
    source = Path("xiaogu_core_alpha.py").read_text(encoding="utf-8")
    body = source.split("def _selection_score")[1].split("def _signal_qualification")[0]
    assert "price_strength" not in body


def test_five_day_thesis_does_not_become_a_second_score():
    snapshot = _deep_snapshot()
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    thesis = decision["research_context"]["opportunity_5d_thesis"]
    assert thesis["why_5d"]
    assert thesis["falsify"]
    blob = str(thesis)
    assert "BUY" not in blob
    assert "SELL" not in blob
    assert "PICK" not in blob
    alpha = decision["core_alpha"]
    mutated = dict(decision["research_context"])
    mutated["opportunity_5d_thesis"] = {**thesis, "why_5d": ["unrelated text"]}
    rebuilt = build_core_alpha(
        decision["feature_vector"],
        industry=mutated.get("industry") or {},
        company=mutated.get("company") or {},
        capital=mutated.get("capital") or {},
        integrated=mutated.get("integrated") or {},
        future_buyer_map=mutated.get("future_buyer_map"),
        research=mutated,
    )
    assert rebuilt["selection_score"] == alpha["selection_score"]
    overlay = (decision.get("paper_observation") or {}).get("research_overlay") or {}
    if overlay:
        assert "rank" not in overlay or overlay.get("rank") is None


def test_gap_audit_lists_missing_history_without_changing_top1(monkeypatch):
    import xiaogu_research_context as research

    def fake_history(symbol, as_of):
        return {
            "status": "RESEARCH_ONLY",
            "historical_cases": [{
                "paper_signal_id": "hist-1",
                "failure_pattern": "SUPPLY_REVERSAL",
                "opportunity_5d": False,
                "knowledge_available_at": "2026-08-20T15:00:00+08:00",
            }],
            "historical_failure_patterns": ["SUPPLY_REVERSAL"],
            "case_count": 1,
            "usable_evidence_count": 0,
            "provider_available": True,
            "provider_succeeded": True,
        }

    def fake_memory(symbol, as_of):
        return {
            "status": "OK",
            "connected": True,
            "notes": [],
            "note_count": 0,
            "usable_evidence_count": 0,
            "provider_available": True,
            "provider_succeeded": True,
        }

    monkeypatch.setattr(research, "fetch_historical_research_cases", fake_history)
    monkeypatch.setattr(research, "fetch_memory_research_notes", fake_memory)
    snapshot = _deep_snapshot()
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    audit = decision["research_context"]["research_gap_audit"]
    assert "SUPPLY_REVERSAL" in str(audit.get("gaps") or audit)
    score = decision["core_alpha"]["selection_score"]
    monkeypatch.setattr(research, "fetch_historical_research_cases", lambda *_: {
        "status": "RESEARCH_ONLY",
        "historical_cases": [],
        "historical_failure_patterns": [],
        "case_count": 0,
        "usable_evidence_count": 0,
        "provider_available": True,
        "provider_succeeded": True,
    })
    other = evaluate_candidate_bundle(snapshot, position_state="FLAT", as_of=AS_OF)
    assert other["core_alpha"]["selection_score"] == score


def test_production_modules_do_not_import_judges_or_delete_assets():
    for path in ROOT.glob("xiaogu_*.py"):
        text = path.read_text(encoding="utf-8")
        assert "investor_evaluator" not in text
        assert "panel.json" not in text
    score_fns = (ROOT / ".agents" / "skills" / "uzi" / "deep-analysis" / "scripts" / "lib" / "pipeline" / "score_fns.py").read_text(encoding="utf-8")
    assert "from lib.investor_evaluator import evaluate" not in score_fns
    assert "65_JUDGES_REMOVED" in score_fns
    rrt = (ROOT / ".agents" / "skills" / "uzi" / "deep-analysis" / "scripts" / "run_real_test.py").read_text(encoding="utf-8")
    assert "from lib.investor_evaluator import evaluate" not in rrt
    assert (ROOT / ".agents" / "skills" / "uzi" / "deep-analysis" / "scripts" / "assemble_report.py").exists()
    assert (ROOT / "xiaogu_db.py").exists()
    assert (ROOT / "xiaogu_forward_paper_recorder_v0_1.py").exists()
