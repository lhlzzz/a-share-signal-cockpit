"""Phase 2.3 production contract: one daily real-market scan, no fixed clock."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from xiaogu_forward_runner import (
    _empty_observation_output,
    _scan_observation_from_dir,
    evaluate_candidate_rows,
)
from xiaogu_forward_snapshot import MAX_STALENESS, snapshot_age, validate_and_build_canonical_snapshot
from xiaogu_portfolio_decision import evaluate_candidate_bundle


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(symbol="600001", pct=3.0, **extra):
    payload = {
        "symbol": symbol,
        "f12": symbol,
        "f13": 1 if str(symbol).startswith(("6", "9")) else 0,
        "f1": 2,
        "price": 10,
        "open": 9.9,
        "high": 10.3,
        "low": 9.7,
        "amount": 1_000,
        "volume": 100,
        "pct_chg": pct,
        "buyable": True,
        "liquidity_score": 1,
        "execution_quality": 1,
        "gap_risk": 0,
        "slippage": 0,
        "spread": 0,
        "market_impact": 0,
        "trade_date": "2026-08-26",
        "source_time": "2026-08-26T14:50:00+08:00",
        "market": "SH" if str(symbol).startswith(("6", "9")) else "SZ",
    }
    payload.update(extra)
    return payload


def _selected(items):
    papers = [item["paper_observation"] for item in items if item.get("paper_observation")]
    return [(paper["symbol"], paper["rank"], paper["top1_flag"]) for paper in sorted(papers, key=lambda item: item["rank"])]


def test_production_has_no_fixed_clock_contract():
    """Freshness is the only production time bound. Session clock is not a strategy."""
    assert MAX_STALENESS.total_seconds() == 120 * 60
    morning_source = "2026-08-26T10:00:00+08:00"
    afternoon_source = "2026-08-26T14:50:00+08:00"
    morning_clock = datetime.fromisoformat("2026-08-26T10:30:00+08:00")
    afternoon_clock = datetime.fromisoformat("2026-08-26T15:00:00+08:00")
    assert snapshot_age(morning_source, morning_clock) < MAX_STALENESS
    assert snapshot_age(afternoon_source, afternoon_clock) < MAX_STALENESS

    morning_rows = [
        validate_and_build_canonical_snapshot(
            _snapshot(f"60000{index}", pct, source_time=morning_source)
        )
        for index, pct in enumerate((1.0, 5.0, 3.0, 4.0), start=1)
    ]
    afternoon_rows = [
        validate_and_build_canonical_snapshot(
            _snapshot(f"60000{index}", pct, source_time=afternoon_source)
        )
        for index, pct in enumerate((1.0, 5.0, 3.0, 4.0), start=1)
    ]
    morning, _ = evaluate_candidate_rows(
        morning_rows,
        portfolio_state="WATCH",
        mode="DRY_RUN",
        trade_date="2026-08-26",
        workers=1,
        decision_clock=morning_clock,
    )
    afternoon, _ = evaluate_candidate_rows(
        afternoon_rows,
        portfolio_state="WATCH",
        mode="DRY_RUN",
        trade_date="2026-08-26",
        workers=1,
        decision_clock=afternoon_clock,
    )
    assert _selected(morning) == _selected(afternoon)
    assert all(item["buy_status"] == "BUY_BLOCKED" for item in morning + afternoon)
    assert all(item["state"] != "BUY" for item in morning + afternoon)
    assert all((item.get("paper_observation") or {}).get("live_order") is not True for item in morning + afternoon)

    owners = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "daily_pipeline.sh",
            "xiaogu_scheduler.py",
            "xiaogu_forward_runner.py",
            "xiaogu_forward_snapshot.py",
            "xiaogu_portfolio_decision.py",
            "xiaogu_db.py",
        )
    )
    for token in (
        "MORNING_WINDOW",
        "AFTERNOON_WINDOW",
        "OFFICIAL_14_30",
        "PREMARKET_PRODUCTION",
        "MIDDAY_PRODUCTION",
        "CLOSE_PRODUCTION",
        "SECOND_SCAN_WINDOW",
        "RETRY_SCAN_WINDOW",
        "INTRADAY_SIGNAL_WINDOW",
    ):
        assert token not in owners


def test_one_daily_scan_one_production_observation():
    """One trade_date may mint at most one official production observation batch."""
    import xiaogu_db as db
    from sqlalchemy import text

    db.ensure_production_schema()
    trade_date = "2026-07-10"
    first_lineage = "phase23-one-daily-first"
    second_lineage = "phase23-one-daily-second"
    clock = datetime(2026, 7, 10, 7, 0, tzinfo=timezone.utc)
    first_snapshots = [
        validate_and_build_canonical_snapshot(
            _snapshot(
                symbol,
                pct,
                lineage_id=first_lineage,
                trade_date=trade_date,
                source_time="2026-07-10T14:50:00+08:00",
            )
        )
        for symbol, pct in (("605021", 1.0), ("605022", 5.0), ("605023", 3.0))
    ]
    first_run = db.insert_scan_session(
        trade_date=trade_date,
        scan_time="2026-07-10T14:50:00+08:00",
        source_id="phase23_one_daily_first",
        quotes_count=len(first_snapshots),
        captured_count=len(first_snapshots),
        scan_dir="data/test/phase23_one_daily_first",
        lineage_id=first_lineage,
    )
    second_run = db.insert_scan_session(
        trade_date=trade_date,
        scan_time="2026-07-10T15:10:00+08:00",
        source_id="phase23_one_daily_second",
        quotes_count=1,
        captured_count=1,
        scan_dir="data/test/phase23_one_daily_second",
        lineage_id=second_lineage,
    )
    paper_ids = []
    decision_ids = []
    snapshot_ids = [item["snapshot_id"] for item in first_snapshots]
    try:
        for snapshot in first_snapshots:
            db.record_snapshot(snapshot)
        decisions, accounting = evaluate_candidate_rows(
            first_snapshots,
            portfolio_state="WATCH",
            mode="PRODUCTION",
            trade_date=trade_date,
            workers=1,
            decision_clock=clock,
        )
        assert accounting["publishable"] is True
        for decision in decisions:
            decision["production_run_id"] = first_run
            observation = decision.get("paper_observation")
            if isinstance(observation, dict):
                observation["production_run_id"] = first_run
                decision["paper_observation"] = observation
                paper_ids.append(observation["paper_signal_id"])
                decision_ids.append(decision["decision_id"])
        db.persist_production_facts(
            decisions,
            production_run_id=first_run,
            coverage={"paper_count": len(paper_ids), "top1_count": 1, "top3_count": len(paper_ids)},
        )
        db.persist_production_facts(
            decisions,
            production_run_id=first_run,
            coverage={"paper_count": len(paper_ids), "top1_count": 1, "top3_count": len(paper_ids)},
        )
        assert db.fetch_official_production_run_id(trade_date) == first_run
        official = [
            row for row in db.fetch_official_paper_observations()
            if row.get("production_run_id") == first_run
        ]
        assert official
        assert len({row["production_run_id"] for row in official}) == 1

        second_snapshot = validate_and_build_canonical_snapshot(
            _snapshot(
                "605024",
                8.0,
                lineage_id=second_lineage,
                trade_date=trade_date,
                source_time="2026-07-10T15:10:00+08:00",
            )
        )
        snapshot_ids.append(second_snapshot["snapshot_id"])
        db.record_snapshot(second_snapshot)
        second_decisions, _ = evaluate_candidate_rows(
            [second_snapshot],
            portfolio_state="WATCH",
            mode="PRODUCTION",
            trade_date=trade_date,
            workers=1,
            decision_clock=datetime(2026, 7, 10, 7, 20, tzinfo=timezone.utc),
        )
        for decision in second_decisions:
            decision["production_run_id"] = second_run
            observation = decision.get("paper_observation")
            if isinstance(observation, dict):
                observation["production_run_id"] = second_run
                decision["paper_observation"] = observation
                paper_ids.append(observation["paper_signal_id"])
                decision_ids.append(decision["decision_id"])
        with pytest.raises(RuntimeError, match="OFFICIAL_PRODUCTION_OBSERVATION_EXISTS"):
            db.persist_production_facts(
                second_decisions,
                production_run_id=second_run,
                coverage={"paper_count": 1, "top1_count": 1, "top3_count": 1},
            )
        assert db.fetch_official_production_run_id(trade_date) == first_run
    finally:
        with db.engine.begin() as connection:
            for paper_id in paper_ids:
                connection.execute(
                    text("DELETE FROM paper_observations WHERE paper_signal_id = :paper_signal_id"),
                    {"paper_signal_id": paper_id},
                )
            for decision_id in decision_ids:
                connection.execute(
                    text("DELETE FROM picks WHERE decision_id = :decision_id"),
                    {"decision_id": decision_id},
                )
            for snapshot_id in snapshot_ids:
                connection.execute(
                    text("DELETE FROM snapshots WHERE snapshot_id = :snapshot_id"),
                    {"snapshot_id": snapshot_id},
                )
            connection.execute(
                text("DELETE FROM production_runs WHERE production_run_id IN (:first_run, :second_run)"),
                {"first_run": first_run, "second_run": second_run},
            )
            connection.execute(
                text("DELETE FROM scan_sessions WHERE scan_dir IN (:first_dir, :second_dir)"),
                {
                    "first_dir": "data/test/phase23_one_daily_first",
                    "second_dir": "data/test/phase23_one_daily_second",
                },
            )


def test_stale_scan_attempt_is_not_official_ticket(tmp_path):
    """A blocked or stale scan attempt is SCAN ATTEMPT, not an official ticket."""
    import xiaogu_db as db
    from sqlalchemy import text

    blocked = _empty_observation_output("2026-09-04", "CRITICAL_SOURCE_INCOMPLETE:stock_all_a:5548")
    assert blocked["observation_kind"] == "SCAN_ATTEMPT"
    assert blocked["scan_status"] == "SCAN_BLOCKED"
    assert blocked["paper_observations"] == []

    stale = _empty_observation_output("2026-09-03", "STALE_DATA", scan_status="STALE_DATA")
    assert stale["observation_kind"] == "SCAN_ATTEMPT"
    assert stale["scan_status"] == "STALE_DATA"

    summary = tmp_path / "scan_summary.json"
    summary.write_text(
        '{"production_scan":"BLOCKED","block_reason":"CRITICAL_SOURCE_INCOMPLETE:stock_all_a:1",'
        '"source_time":"2026-09-04T09:25:00+08:00","lineage":{"lineage_id":"blocked-attempt"}}',
        encoding="utf-8",
    )
    observation = _scan_observation_from_dir(str(tmp_path))
    assert observation["status"] == "SCAN_BLOCKED"
    assert "CRITICAL_SOURCE_INCOMPLETE" in observation["reason"]

    db.ensure_production_schema()
    trade_date = "2026-07-13"
    lineage_id = "phase23-stale-attempt"
    run_id = db.insert_scan_session(
        trade_date=trade_date,
        scan_time="2026-07-13T09:25:00+08:00",
        source_id="phase23_stale_attempt",
        quotes_count=0,
        captured_count=0,
        scan_dir="data/test/phase23_stale_attempt",
        lineage_id=lineage_id,
    )
    try:
        run = db.fetch_production_run(run_id) or {}
        assert str(run.get("status") or "") != "DECISIONS_PERSISTED"
        assert db.fetch_official_production_run_id(trade_date) is None
        rank_only = {
            "paper_signal_id": "phase23-attempt-paper",
            "decision_id": "phase23-attempt-decision",
            "production_run_id": run_id,
            "snapshot_id": "phase23-attempt-snap",
            "lineage_id": lineage_id,
            "rank": 1,
            "top1_flag": True,
            "top3_flag": True,
            "production_alpha": "profit_window_alpha_5d_v4",
            "production_target": "opportunity_5d",
            "paper_only": True,
            "live_order": False,
        }
        assert db.has_official_observation_provenance(rank_only) is True
        assert db.has_official_observation_provenance(
            rank_only, require_persisted_run=True, run_status=str(run.get("status") or "")
        ) is False
        official = [
            row for row in db.fetch_official_paper_observations()
            if row.get("production_run_id") == run_id
        ]
        assert official == []
    finally:
        with db.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM production_runs WHERE production_run_id = :run_id"),
                {"run_id": run_id},
            )
            connection.execute(
                text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"),
                {"scan_dir": "data/test/phase23_stale_attempt"},
            )


def test_afternoon_name_is_not_production_contract():
    pipeline = (ROOT / "daily_pipeline.sh").read_text(encoding="utf-8")
    scheduler = (ROOT / "xiaogu_scheduler.py").read_text(encoding="utf-8")
    scanner = (ROOT / "scrapy_scanner" / "runner_v2.py").read_text(encoding="utf-8")
    assert "eastmoney_scan_afternoon" not in pipeline
    assert "eastmoney_scan_morning" not in pipeline
    assert 'SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan"' in pipeline
    assert "SLOT" not in pipeline
    assert "MORNING" not in pipeline
    assert "AFTERNOON" not in pipeline
    assert "job_morning_scan" not in scheduler
    assert "job_afternoon_scan_and_pick" not in scheduler
    assert "CronTrigger(hour=14" not in scheduler
    assert "CronTrigger(hour=9" not in scheduler
    assert "eastmoney_scan_afternoon" not in scanner
    assert "Not a morning or afternoon scanner" in scanner


def test_source_completeness_remains_fail_closed():
    from scrapy_scanner.runner_v2 import CriticalSourceError, _collect

    def _quote_row(symbol, **overrides):
        row = {
            "f12": symbol, "f14": "示例", "f2": 10.0, "f3": 1.0, "f5": 100, "f6": 1000,
            "f8": 1.0, "f10": 1.0, "f13": 1, "f15": 10.5, "f16": 9.5, "f17": 9.8,
            "f62": 400, "f100": "示例行业", "f1": 2, "f125": 0, "f148": 1, "f26": 20100101,
        }
        row.update(overrides)
        return row

    complete = _quote_row("600001")
    incomplete = _quote_row("600002", f2="-", f5="-", f6="-", f62="-", f18=10.5)
    timings = {}
    with pytest.raises(CriticalSourceError, match="CRITICAL_SOURCE_INCOMPLETE:stock_all_a:1"):
        _collect("stock_all_a", timings, lambda: [complete, incomplete], [], critical=True)
    assert timings["stock_all_a"]["universe_audit"]["active_required_incomplete"] == 1
    dist = timings["stock_all_a"]["incomplete_reason_distribution"]
    assert dist["MISSING_VOLUME"] == 1
    assert dist["MISSING_AMOUNT"] == 1
    assert dist["MISSING_MAIN_NET_INFLOW"] == 1


def test_alpha_not_validated_can_still_emit_paper_signal():
    decision = evaluate_candidate_bundle(
        _snapshot(),
        position_state="FLAT",
        as_of=datetime.fromisoformat("2026-08-26T15:00:00+08:00"),
    )
    alpha = decision["core_alpha"]
    paper = decision["paper_observation"]
    assert alpha["model_status"] != "VALIDATED"
    assert alpha["output_status"] == "DATA_INSUFFICIENT"
    assert alpha["signal_qualified"] is True
    assert paper is not None
    assert paper["status"] == "PAPER_OBSERVATION"
    assert paper["alpha_status"] == "DATA_INSUFFICIENT"
    assert decision["buy_status"] == "BUY_BLOCKED"
    assert decision["state"] != "BUY"
    assert "ALPHA_NOT_VALIDATED" in str(decision.get("reason") or "") or "ALPHA_NOT_VALIDATED" in (
        decision.get("production_blockers") or []
    )
