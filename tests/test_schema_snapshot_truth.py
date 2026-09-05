"""Unit, contract, integration, and production-smoke tests for schema/snapshot truth."""
from __future__ import annotations

import inspect
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

from xiaogu_forward_snapshot import (
    select_canonical_snapshot,
    select_unique_canonical_snapshots,
    validate_and_build_canonical_snapshot,
)
from xiaogu_portfolio_decision import evaluate_candidate_bundle



ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _temporary_database(name: str):
    """Create an isolated PostgreSQL database and rebind xiaogu_db.engine to it."""
    import xiaogu_db as db

    base = os.environ.get("DATABASE_URL", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu")
    candidates = [
        str(make_url(base).set(database="postgres")),
        "postgresql://postgres:postgres@localhost:5432/postgres",
    ]
    admin = None
    last_error = None
    for url in candidates:
        engine = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                connection.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"),
                    {"name": name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                connection.execute(text(f'CREATE DATABASE "{name}" OWNER xiaogu'))
            admin = engine
            break
        except Exception as exc:
            last_error = exc
            engine.dispose()
    if admin is None:
        raise last_error
    temp = create_engine(str(make_url(base).set(database=name)), connect_args={"connect_timeout": 5})
    previous = db.engine
    db.engine = temp
    try:
        yield temp
    finally:
        db.engine = previous
        temp.dispose()
        with admin.connect() as connection:
            connection.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _snapshot(*, symbol: str, trade_date: str, lineage_id: str, source_time: str | None = None):
    return validate_and_build_canonical_snapshot(
        {
            "symbol": symbol,
            "price": 10,
            "open": 9.9,
            "high": 10.3,
            "low": 9.7,
            "amount": 1_000,
            "volume": 100,
            "pct_chg": 3,
            "trade_date": trade_date,
            "source_time": source_time or f"{trade_date}T14:50:00+08:00",
            "lineage_id": lineage_id,
            "f12": symbol,
            "f13": 1 if str(symbol).startswith(("6", "5", "9")) else 0,
            "f1": 2,
            "market": "SH" if str(symbol).startswith(("6", "5", "9")) else "SZ",
        },
        lineage_id=lineage_id,
    )


def _calendar_rows():
    from xiaogu_db import load_trading_calendar

    loaded = load_trading_calendar(2026)
    return {row["trade_date"]: row["is_trading_day"] for row in loaded["rows"]}


def test_schema_composite_unique_exact():
    import xiaogu_db as db

    db.ensure_production_schema()
    keys = db._unique_constraint_columns("returns") | db._unique_index_columns("returns")
    assert ("decision_id", "trade_date") in keys
    assert ("trade_date",) not in keys
    flattened = {column for key in keys for column in key}
    assert flattened != keys
    audit = db.audit_production_schema()
    assert ("decision_id", "trade_date") in audit["tables"]["returns"]["unique_constraints"]
    assert audit["tables"]["returns"]["unique"][("decision_id", "trade_date")] == "EXISTS"


def test_schema_conflict_blocks(monkeypatch):
    import xiaogu_db as db

    original_unique = db._unique_constraint_columns
    original_index = db._unique_index_columns

    def hide_composite(table_name: str):
        return {
            key for key in original_unique(table_name)
            if key != ("decision_id", "trade_date")
        }

    def hide_composite_index(table_name: str):
        return {
            key for key in original_index(table_name)
            if key != ("decision_id", "trade_date")
        }

    monkeypatch.setattr(db, "_unique_constraint_columns", hide_composite)
    monkeypatch.setattr(db, "_unique_index_columns", hide_composite_index)
    audit = db.audit_production_schema()
    assert audit["ok"] is False
    assert audit["audit"] == "FAIL"


def test_schema_version_ahead_blocks():
    import xiaogu_db as db

    db.ensure_production_schema()
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE xiaogu_schema_version SET schema_version = 'xiaogu_production_schema_v99'")
        )
    try:
        with pytest.raises(RuntimeError, match="SCHEMA_VERSION_AHEAD"):
            db.ensure_production_schema()
    finally:
        with db.engine.begin() as connection:
            connection.execute(
                text("UPDATE xiaogu_schema_version SET schema_version = :version"),
                {"version": db.SCHEMA_VERSION},
            )


def test_bootstrap_creates_latest_schema():
    sql = (ROOT / "scripts" / "xiaogu_db_init.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE" not in sql
    assert "UPDATE snapshots SET" not in sql
    assert "UPDATE canonical_historical_snapshots SET" not in sql
    assert "CREATE TABLE IF NOT EXISTS xiaogu_schema_version" in sql
    assert "CREATE TABLE IF NOT EXISTS xiaogu_schema_migrations" in sql
    assert "idx_snapshots_lineage_symbol" in sql
    assert "migration_type" in sql
    assert "CREATE TABLE IF NOT EXISTS snapshot_identity_conflicts" in sql
    assert "CREATE TABLE IF NOT EXISTS positions" in sql
    assert "position_id TEXT PRIMARY KEY" in sql
    assert "snapshots_identity_immutable" in sql
    assert "xiaogu_protect_snapshot_identity" in sql
    assert "CREATE TABLE IF NOT EXISTS production_runs" in sql
    assert "production_run_id TEXT PRIMARY KEY" in sql
    assert "lineage_id TEXT" in sql
    assert "id BIGSERIAL PRIMARY KEY,\n    trade_date DATE NOT NULL,\n    status TEXT NOT NULL,\n    payload JSONB" not in sql


def test_runtime_migration_upgrade():
    import xiaogu_db as db

    db.ensure_production_schema()
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE xiaogu_schema_version SET schema_version = 'xiaogu_production_schema_v5'")
        )
        connection.execute(
            text("DELETE FROM xiaogu_schema_migrations WHERE migration_id = :migration_id"),
            {"migration_id": "schema-xiaogu_production_schema_v6"},
        )
    try:
        db.ensure_production_schema()
        audit = db.audit_production_schema()
        assert audit["schema_version"] == db.SCHEMA_VERSION
        assert audit["ok"] is True
        applied = db._applied_migration("schema-xiaogu_production_schema_v6")
        assert applied is not None
        assert applied["checksum"] == db._migration_checksum(db.SCHEMA_V6_STATEMENTS)
        assert audit["tables"]["production_runs"]["primary_key"]["status"] == "EXISTS"
        assert audit["tables"]["production_runs"]["columns"]["production_run_id"] == "EXISTS"
        assert audit["tables"]["production_runs"]["columns"]["lineage_id"] == "EXISTS"
        assert audit["tables"]["positions"]["primary_key"]["status"] == "EXISTS"
    finally:
        db.ensure_production_schema()


def test_migration_checksum_conflict():
    import xiaogu_db as db

    db.ensure_production_schema()
    migration_id = "schema-xiaogu_production_schema_v3"
    original = db._applied_migration(migration_id)["checksum"]
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE xiaogu_schema_migrations SET checksum = 'deadbeef' WHERE migration_id = :migration_id"),
            {"migration_id": migration_id},
        )
    try:
        with pytest.raises(RuntimeError, match="SCHEMA_MIGRATION_CHECKSUM_CONFLICT"):
            db.ensure_production_schema()
    finally:
        with db.engine.begin() as connection:
            connection.execute(
                text("UPDATE xiaogu_schema_migrations SET checksum = :checksum WHERE migration_id = :migration_id"),
                {"checksum": original, "migration_id": migration_id},
            )
        db.ensure_production_schema()


def test_historical_snapshot_migration_not_called_by_production_init():
    import xiaogu_db as db

    assert "migrate_historical_snapshot_identity" not in inspect.getsource(db.init_db)
    assert "migrate_historical_snapshot_identity" not in inspect.getsource(db.ensure_production_schema)
    assert "migrate_historical_snapshot_identity" not in inspect.getsource(db._apply_pending_schema_migrations)


def test_historical_snapshot_unresolved_stays_unresolved():
    from xiaogu_db import _recover_historical_snapshot_id

    assert _recover_historical_snapshot_id({"payload": {}, "symbol": "600001"}) == ""
    assert _recover_historical_snapshot_id({"payload": {"symbol": "600001"}}) == ""


def test_historical_snapshot_conflict_blocks_migration():
    import xiaogu_db as db

    db.ensure_production_schema()
    payload = {
        "symbol": "699992",
        "trade_date": "2026-08-26",
        "source": "eastmoney_api_scan_v2",
        "source_time": "2026-08-26T14:50:00+08:00",
        "snapshot_id": "recovered-historical-id",
        "price": 10,
    }
    with db.engine.begin() as connection:
        connection.execute(text("DELETE FROM canonical_historical_snapshots WHERE lineage_id = 'hist-conflict-lineage'"))
        connection.execute(
            text(
                """
                INSERT INTO canonical_historical_snapshots (
                    snapshot_id, lineage_id, symbol, trade_date, signal_time, source,
                    source_timestamp, snapshot_version, point_in_time, available_at,
                    price_basis, payload
                ) VALUES (
                    'wrong-historical-id', 'hist-conflict-lineage', '699992',
                    CAST('2026-08-26' AS date), CAST('2026-08-26T14:50:00+08:00' AS timestamptz),
                    'eastmoney_api_scan_v2', CAST('2026-08-26T14:50:00+08:00' AS timestamptz),
                    'canonical_snapshot_v2', TRUE, CAST('2026-08-26T14:50:00+08:00' AS timestamptz),
                    'UNADJUSTED', CAST(:payload AS jsonb)
                )
                """
            ),
            {"payload": __import__("json").dumps(payload)},
        )
    try:
        with pytest.raises(ValueError, match="SNAPSHOT_IDENTITY_CONFLICT"):
            db.migrate_historical_snapshot_identity()
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM canonical_historical_snapshots WHERE lineage_id = 'hist-conflict-lineage'"))


def test_canonical_single_candidate():
    snapshot = _snapshot(symbol="600001", trade_date="2026-08-26", lineage_id="lineage-one")
    selected = select_canonical_snapshot([snapshot, snapshot], symbol="600001", trade_date="2026-08-26")
    assert selected["snapshot_id"] == snapshot["snapshot_id"]
    unique = select_unique_canonical_snapshots([snapshot, snapshot], trade_date="2026-08-26")
    assert len(unique) == 1


def test_canonical_multiple_candidates_blocks():
    first = _snapshot(symbol="600001", trade_date="2026-08-26", lineage_id="lineage-a")
    second = _snapshot(
        symbol="600001",
        trade_date="2026-08-26",
        lineage_id="lineage-b",
        source_time="2026-08-26T15:00:00+08:00",
    )
    assert first["snapshot_id"] != second["snapshot_id"]
    with pytest.raises(ValueError, match="CANONICAL_SNAPSHOT_AMBIGUOUS"):
        select_canonical_snapshot([first, second], symbol="600001", trade_date="2026-08-26")
    with pytest.raises(ValueError, match="CANONICAL_SNAPSHOT_AMBIGUOUS"):
        select_unique_canonical_snapshots([first, second], trade_date="2026-08-26")


def test_production_never_uses_latest_snapshot_fallback():
    for relative in ("xiaogu_db.py", "xiaogu_forward_snapshot.py", "xiaogu_forward_runner.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ORDER BY source_time DESC NULLS LAST, created_at DESC" not in source
        assert "return max(matched, key=_key)" not in source
        assert "max(matched" not in source


def test_stale_same_day_lineage_is_not_current_production_input():
    from xiaogu_forward_snapshot import select_production_observation_snapshots

    stale = _snapshot(symbol="600001", trade_date="2026-09-03", lineage_id="lin-stale", source_time="2026-09-03T02:11:41+08:00")
    fresh = _snapshot(symbol="600001", trade_date="2026-09-03", lineage_id="lin-fresh", source_time="2026-09-03T14:40:00+08:00")
    clock = datetime.fromisoformat("2026-09-03T14:50:00+08:00")
    selected = select_production_observation_snapshots(
        [stale, fresh], trade_date="2026-09-03", decision_clock=clock,
    )
    assert len(selected) == 1
    assert selected[0]["lineage_id"] == "lin-fresh"
    assert selected[0]["source_time"] != "2026-09-03T02:11:41+08:00"


def test_stale_only_observation_is_not_current_production_input():
    from xiaogu_forward_snapshot import select_production_observation_snapshots

    stale = _snapshot(symbol="600001", trade_date="2026-09-03", lineage_id="lin-stale-only", source_time="2026-09-03T02:11:41+08:00")
    clock = datetime.fromisoformat("2026-09-03T14:50:00+08:00")
    with pytest.raises(ValueError, match="CANONICAL_SNAPSHOT_NOT_FOUND"):
        select_production_observation_snapshots([stale], trade_date="2026-09-03", decision_clock=clock)
    with pytest.raises(ValueError, match="STALE_DATA"):
        select_production_observation_snapshots(
            [stale], trade_date="2026-09-03", lineage_id="lin-stale-only", decision_clock=clock,
        )


def test_two_fresh_observations_are_ambiguous_without_lineage():
    from xiaogu_forward_snapshot import select_production_observation_snapshots

    first = _snapshot(symbol="600001", trade_date="2026-09-03", lineage_id="lin-a", source_time="2026-09-03T14:40:00+08:00")
    second = _snapshot(symbol="600001", trade_date="2026-09-03", lineage_id="lin-b", source_time="2026-09-03T14:45:00+08:00")
    clock = datetime.fromisoformat("2026-09-03T14:50:00+08:00")
    with pytest.raises(ValueError, match="CANONICAL_SNAPSHOT_AMBIGUOUS"):
        select_production_observation_snapshots([first, second], trade_date="2026-09-03", decision_clock=clock)
    selected = select_production_observation_snapshots(
        [first, second], trade_date="2026-09-03", lineage_id="lin-b", decision_clock=clock,
    )
    assert selected[0]["lineage_id"] == "lin-b"


def test_same_day_production_observations_persist_separately():
    """A new production_run_id must not collide with an earlier same-day pick."""
    import xiaogu_db as db

    db.ensure_production_schema()
    first = _snapshot(
        symbol="601234",
        trade_date="2026-07-02",
        lineage_id="test-p0-lin-first",
        source_time="2026-07-02T02:11:41+08:00",
    )
    second = _snapshot(
        symbol="601234",
        trade_date="2026-07-02",
        lineage_id="test-p0-lin-second",
        source_time="2026-07-02T15:24:25+08:00",
    )
    first_decision = {
        "decision_id": "test-p0-first-ready-601234",
        "symbol": "601234",
        "trade_date": "2026-07-02",
        "state": "READY",
        "position_state": "FLAT",
        "snapshot_id": first["snapshot_id"],
        "lineage_id": first["lineage_id"],
        "canonical_snapshot": first,
    }
    second_decision = {
        "decision_id": "test-p0-second-ready-601234",
        "symbol": "601234",
        "trade_date": "2026-07-02",
        "state": "READY",
        "position_state": "FLAT",
        "snapshot_id": second["snapshot_id"],
        "lineage_id": second["lineage_id"],
        "production_run_id": "test-p0-run-second",
        "canonical_snapshot": second,
    }
    with db.engine.begin() as connection:
        connection.execute(text(
            "DELETE FROM picks WHERE decision_id IN ('test-p0-first-ready-601234', 'test-p0-second-ready-601234')"
        ))
        connection.execute(text(
            "DELETE FROM snapshots WHERE lineage_id IN ('test-p0-lin-first', 'test-p0-lin-second')"
        ))
    try:
        db.record_snapshot(first)
        db.record_snapshot(second)
        db.record_decision(first_decision)
        db.record_decision(second_decision)
        db.record_decision(second_decision)
        with db.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT decision_id, production_run_id FROM picks "
                "WHERE decision_id IN ('test-p0-first-ready-601234', 'test-p0-second-ready-601234') "
                "ORDER BY decision_id"
            )).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("test-p0-first-ready-601234", None),
            ("test-p0-second-ready-601234", "test-p0-run-second"),
        ]
    finally:
        with db.engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM picks WHERE decision_id IN ('test-p0-first-ready-601234', 'test-p0-second-ready-601234')"
            ))
            connection.execute(text(
                "DELETE FROM snapshots WHERE lineage_id IN ('test-p0-lin-first', 'test-p0-lin-second')"
            ))


def test_decision_snapshot_id_is_exact():
    snapshot = _snapshot(symbol="600001", trade_date="2026-08-26", lineage_id="decision-lineage")
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT")
    assert decision["snapshot_id"] == snapshot["snapshot_id"]
    assert decision["canonical_snapshot"]["snapshot_id"] == snapshot["snapshot_id"]


def test_calendar_fixture_2026_08_31():
    assert _calendar_rows()["2026-08-31"] is True


def test_calendar_fixture_2026_09_25():
    assert _calendar_rows()["2026-09-25"] is False


def test_calendar_fixture_2026_09_28():
    assert _calendar_rows()["2026-09-28"] is True


def test_calendar_cross_year():
    from xiaogu_db import CALENDAR_DATA_UNAVAILABLE, resolve_t_plus_n

    with pytest.raises(RuntimeError, match=CALENDAR_DATA_UNAVAILABLE):
        resolve_t_plus_n("2026-12-31", 1)
    assert date.fromisoformat("2026-12-31")


def test_paper_reuses_decision_snapshot_identity():
    snapshot = _snapshot(symbol="600001", trade_date="2026-08-26", lineage_id="paper-lineage")
    decision = evaluate_candidate_bundle(snapshot, position_state="FLAT")
    paper = decision["paper_observation"]
    assert paper["decision_id"] == decision["decision_id"]
    assert paper["snapshot_id"] == snapshot["snapshot_id"]
    assert paper["paper_signal_id"] != paper["decision_id"]
    assert paper["paper_position_state"] == "PAPER_FLAT"
    source = inspect.getsource(__import__("xiaogu_db").record_paper_observation)
    assert "select_canonical_snapshot" not in source
    assert "ORDER BY source_time" not in source


def test_outcome_reuses_decision_identity():
    import xiaogu_db as db
    from xiaogu_forward_result_filler_v0_1 import fill_pending_results

    assert "decision_id" in inspect.getsource(db.record_returns)
    filler = inspect.getsource(fill_pending_results)
    assert "select_canonical_snapshot" not in filler
    assert "get_latest_snapshot" not in filler
    assert "ORDER BY source_time" not in filler



def test_insert_scan_session_matches_production_schema():
    import xiaogu_db as db

    db.ensure_production_schema()
    audit = db.audit_production_schema()
    assert audit["schema_version"] == "xiaogu_production_schema_v6"
    assert audit["tables"]["production_runs"]["columns"]["production_run_id"] == "EXISTS"
    assert audit["tables"]["production_runs"]["columns"]["lineage_id"] == "EXISTS"
    assert audit["tables"]["production_runs"]["primary_key"]["columns"] == ["production_run_id"]
    source = inspect.getsource(db.insert_scan_session)
    assert "INSERT INTO production_runs" in source
    assert "RETURNING production_run_id" in source
    assert "RETURNING id" not in source
    assert "payload" not in source or "CAST(:payload AS jsonb)" not in source
    lineage_id = "test-persist-lineage-insert"
    scan_dir = "data/test/production_runs_contract/insert"
    run_id = db.insert_scan_session(
        trade_date="2026-09-02",
        scan_time="2026-09-02T14:50:00+08:00",
        source_id="test_persist",
        quotes_count=3,
        captured_count=3,
        scan_dir=scan_dir,
        lineage_id=lineage_id,
        market_snapshot={"test": True},
    )
    try:
        assert run_id
        assert run_id != lineage_id
        run = db.fetch_production_run(run_id)
        assert run is not None
        assert run["production_run_id"] == run_id
        assert str(run["lineage_id"]) == lineage_id
        assert str(run["status"]) == "SNAPSHOT_CAPTURED"
        assert "payload" not in run
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM production_runs WHERE production_run_id = :run_id"), {"run_id": run_id})
            connection.execute(text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"), {"scan_dir": scan_dir})


def test_insert_scan_session_new_run_id_on_retry():
    import xiaogu_db as db

    db.ensure_production_schema()
    lineage_one = "test-persist-lineage-retry-1"
    lineage_two = "test-persist-lineage-retry-2"
    scan_dir = "data/test/production_runs_contract/retry"
    first = db.insert_scan_session(
        trade_date="2026-09-02",
        scan_time="2026-09-02T14:50:00+08:00",
        scan_dir=scan_dir,
        lineage_id=lineage_one,
    )
    second = db.insert_scan_session(
        trade_date="2026-09-02",
        scan_time="2026-09-02T14:55:00+08:00",
        scan_dir=scan_dir,
        lineage_id=lineage_two,
    )
    try:
        assert first and second
        assert first != second
        assert db.fetch_production_run(first)["lineage_id"] == lineage_one
        assert db.fetch_production_run(second)["lineage_id"] == lineage_two
    finally:
        with db.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM production_runs WHERE production_run_id IN (:a, :b)"),
                {"a": first, "b": second},
            )
            connection.execute(text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"), {"scan_dir": scan_dir})


def test_insert_scan_session_invalid_metadata_fails_closed():
    import xiaogu_db as db

    db.ensure_production_schema()
    with pytest.raises(ValueError, match="PRODUCTION_SCAN_BLOCKED:TRADE_DATE_REQUIRED"):
        db.insert_scan_session(lineage_id="test-persist-missing-date")
    with pytest.raises(ValueError, match="PRODUCTION_SCAN_BLOCKED:LINEAGE_ID_REQUIRED"):
        db.insert_scan_session(trade_date="2026-09-02")


def test_persist_scan_capture_rolls_back_run_on_snapshot_failure():
    import xiaogu_db as db

    db.ensure_production_schema()
    lineage_id = "test-persist-lineage-rollback"
    scan_dir = "data/test/production_runs_contract/rollback"
    with db.engine.connect() as connection:
        before = int(
            connection.execute(
                text("SELECT count(*) FROM production_runs WHERE lineage_id = :lineage_id"),
                {"lineage_id": lineage_id},
            ).scalar_one()
        )
    with pytest.raises((ValueError, RuntimeError)):
        db.persist_scan_capture(
            trade_date="2026-09-02",
            scan_time="2026-09-02T14:50:00+08:00",
            scan_dir=scan_dir,
            lineage_id=lineage_id,
            snapshots=[{"symbol": "600000", "trade_date": "2026-09-02"}],
        )
    with db.engine.connect() as connection:
        after = int(
            connection.execute(
                text("SELECT count(*) FROM production_runs WHERE lineage_id = :lineage_id"),
                {"lineage_id": lineage_id},
            ).scalar_one()
        )
        leftover_sessions = int(
            connection.execute(
                text("SELECT count(*) FROM scan_sessions WHERE scan_dir = :scan_dir"),
                {"scan_dir": scan_dir},
            ).scalar_one()
        )
    assert after == before
    assert leftover_sessions == 0


def test_persist_scan_capture_writes_run_and_snapshot():
    import xiaogu_db as db

    db.ensure_production_schema()
    lineage_id = "test-persist-lineage-success"
    scan_dir = "data/test/production_runs_contract/success"
    snapshot = _snapshot(symbol="600000", trade_date="2026-09-02", lineage_id=lineage_id)
    try:
        result = db.persist_scan_capture(
            trade_date="2026-09-02",
            scan_time="2026-09-02T14:50:00+08:00",
            scan_dir=scan_dir,
            lineage_id=lineage_id,
            snapshots=(item for item in [snapshot]),
        )
        assert result["status"] == "PASS"
        assert result["run_id"]
        assert result["run_id"] != lineage_id
        assert result["snapshot_count"] == 1
        run = db.fetch_production_run(result["run_id"])
        assert run is not None
        assert str(run["lineage_id"]) == lineage_id
        assert "payload" not in run
        assert db.verify_persisted_snapshot(
            snapshot_id=snapshot["snapshot_id"],
            lineage_id=lineage_id,
            trade_date="2026-09-02",
            source=snapshot["source"],
            source_time=snapshot["source_time"],
            symbol=snapshot["symbol"],
            payload_hash=snapshot["payload_hash"],
        )
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM snapshots WHERE lineage_id = :lineage_id"), {"lineage_id": lineage_id})
            connection.execute(text("DELETE FROM production_runs WHERE lineage_id = :lineage_id"), {"lineage_id": lineage_id})
            connection.execute(text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"), {"scan_dir": scan_dir})


def test_persist_scan_capture_empty_snapshots_fails_closed():
    import xiaogu_db as db

    db.ensure_production_schema()
    lineage_id = "test-persist-lineage-empty"
    scan_dir = "data/test/production_runs_contract/empty"
    with pytest.raises(RuntimeError, match="PRODUCTION_SCAN_BLOCKED:CANONICAL_SNAPSHOT_NOT_FOUND"):
        db.persist_scan_capture(
            trade_date="2026-09-02",
            scan_time="2026-09-02T14:50:00+08:00",
            scan_dir=scan_dir,
            lineage_id=lineage_id,
            snapshots=[],
        )
    with db.engine.connect() as connection:
        leftover_runs = int(
            connection.execute(
                text("SELECT count(*) FROM production_runs WHERE lineage_id = :lineage_id"),
                {"lineage_id": lineage_id},
            ).scalar_one()
        )
        leftover_sessions = int(
            connection.execute(
                text("SELECT count(*) FROM scan_sessions WHERE scan_dir = :scan_dir"),
                {"scan_dir": scan_dir},
            ).scalar_one()
        )
    assert leftover_runs == 0
    assert leftover_sessions == 0


def test_insert_scan_session_invalid_trade_date_fails_closed():
    import xiaogu_db as db

    db.ensure_production_schema()
    with pytest.raises(RuntimeError, match="PRODUCTION_SCAN_BLOCKED"):
        db.insert_scan_session(trade_date="not-a-date", lineage_id="test-persist-bad-date")


def test_fresh_db_insert_scan_session_matches_schema():
    import xiaogu_db as db

    with _temporary_database("xiaogu_tmp_persist_fresh"):
        db.init_db()
        audit = db.audit_production_schema()
        assert audit["ok"] is True
        assert audit["schema_version"] == "xiaogu_production_schema_v6"
        lineage_id = "fresh-db-lineage"
        run_id = db.insert_scan_session(
            trade_date="2026-09-02",
            scan_time="2026-09-02T14:50:00+08:00",
            scan_dir="data/test/production_runs_contract/fresh",
            lineage_id=lineage_id,
        )
        assert run_id
        assert run_id != lineage_id
        run = db.fetch_production_run(run_id)
        assert run is not None
        assert run["production_run_id"] == run_id
        assert str(run["lineage_id"]) == lineage_id
        assert "payload" not in run


def test_v5_payload_table_migrates_without_rewriting_rows():
    import xiaogu_db as db

    with _temporary_database("xiaogu_tmp_persist_v5"):
        db.init_db()
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE production_runs DROP CONSTRAINT IF EXISTS production_runs_scan_session_id_fkey"))
            connection.execute(text("DROP TABLE production_runs"))
            connection.execute(
                text(
                    """
                    CREATE TABLE production_runs (
                        id BIGSERIAL PRIMARY KEY,
                        trade_date DATE NOT NULL,
                        status TEXT NOT NULL,
                        payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO production_runs (trade_date, status, payload)
                    VALUES (CAST('2026-08-25' AS date), 'PASS', CAST(:payload AS jsonb))
                    """
                ),
                {"payload": '{"historical": true}'},
            )
            connection.execute(text("UPDATE xiaogu_schema_version SET schema_version = 'xiaogu_production_schema_v5'"))
            connection.execute(
                text("DELETE FROM xiaogu_schema_migrations WHERE migration_id = :migration_id"),
                {"migration_id": "schema-xiaogu_production_schema_v6"},
            )
        db.ensure_production_schema()
        audit = db.audit_production_schema()
        assert audit["ok"] is True
        assert audit["schema_version"] == "xiaogu_production_schema_v6"
        with db.engine.connect() as connection:
            row = connection.execute(
                text("SELECT production_run_id, status, payload FROM production_runs WHERE status = 'PASS'")
            ).mappings().first()
            count = int(connection.execute(text("SELECT count(*) FROM production_runs")).scalar_one())
        assert count == 1
        assert row is not None
        assert str(row["production_run_id"]).startswith("legacy-")
        assert str(row["status"]) == "PASS"
        assert row["payload"]["historical"] is True
        lineage_id = "migrated-v5-lineage"
        run_id = db.insert_scan_session(
            trade_date="2026-09-02",
            scan_time="2026-09-02T14:50:00+08:00",
            scan_dir="data/test/production_runs_contract/v5",
            lineage_id=lineage_id,
        )
        assert run_id
        assert run_id != lineage_id
        run = db.fetch_production_run(run_id)
        assert run is not None
        assert str(run["lineage_id"]) == lineage_id
        assert str(run["production_run_id"]) == run_id


def test_scan_status_distinguishes_blocked_from_no_signal():
    from xiaogu_forward_runner import _block_funnel, _scan_status_from_run

    stale = [{"reason": "STALE_DATA", "failed_gates": ["FRESH_DATA"], "buy_status": None, "paper_observation": None}]
    funnel = _block_funnel(stale)
    status, reason = _scan_status_from_run(
        paper_count=0, decision_count=1, freshness_blocked=funnel["freshness_blocked"], buy_allowed=0,
    )
    assert status == "STALE_DATA"
    assert funnel["freshness_blocked"] == 1

    blocked = [{
        "reason": "BUY_BLOCKED_PENDING_HARD_GATE:ALPHA_NOT_VALIDATED",
        "failed_gates": ["ALPHA_VALIDATED", "PROFIT_WINDOW_MODEL"],
        "production_blockers": ["ALPHA_NOT_VALIDATED"],
        "buy_status": "BUY_BLOCKED",
        "paper_observation": {"status": "PAPER_OBSERVATION"},
    }]
    funnel = _block_funnel(blocked)
    status, reason = _scan_status_from_run(
        paper_count=1, decision_count=1, freshness_blocked=funnel["freshness_blocked"], buy_allowed=0,
    )
    assert status == "BUY_BLOCKED"
    assert funnel["alpha_blocked"] == 1
    assert reason == "PAPER_OBSERVATION_RECORDED"

    status, reason = _scan_status_from_run(
        paper_count=0, decision_count=0, freshness_blocked=0, buy_allowed=0,
    )
    assert status == "NO_SIGNAL"


def test_same_lineage_scan_session_is_idempotent():
    import xiaogu_db as db

    db.ensure_production_schema()
    lineage_id = "test-persist-lineage-idempotent"
    scan_dir = "data/test/production_runs_contract/idempotent"
    first = db.insert_scan_session(
        trade_date="2026-09-02",
        scan_time="2026-09-02T14:50:00+08:00",
        scan_dir=scan_dir,
        lineage_id=lineage_id,
    )
    second = db.insert_scan_session(
        trade_date="2026-09-02",
        scan_time="2026-09-02T14:50:00+08:00",
        scan_dir=scan_dir,
        lineage_id=lineage_id,
    )
    try:
        assert first == second
    finally:
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM production_runs WHERE production_run_id = :run_id"), {"run_id": first})
            connection.execute(text("DELETE FROM scan_sessions WHERE scan_dir = :scan_dir"), {"scan_dir": scan_dir})


def test_due_horizon_report_does_not_claim_t1_t5_fill():
    from xiaogu_forward_result_filler_v0_1 import fill_due_horizon_results

    source = inspect.getsource(fill_due_horizon_results)
    assert "t1_t5_persisted" in source
    assert "persist_horizon" in source
    assert "Only persist when the full T+1..T+5 window is due" in source
