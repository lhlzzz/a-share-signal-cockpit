"""Unit, contract, integration, and production-smoke tests for schema/snapshot truth."""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

from xiaogu_forward_snapshot import (
    select_canonical_snapshot,
    select_unique_canonical_snapshots,
    validate_and_build_canonical_snapshot,
)
from xiaogu_portfolio_decision import evaluate_candidate_bundle


ROOT = Path(__file__).resolve().parents[1]


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


def test_runtime_migration_upgrade():
    import xiaogu_db as db

    db.ensure_production_schema()
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE xiaogu_schema_version SET schema_version = 'xiaogu_production_schema_v4'")
        )
        connection.execute(
            text("DELETE FROM xiaogu_schema_migrations WHERE migration_id = :migration_id"),
            {"migration_id": "schema-xiaogu_production_schema_v5"},
        )
    try:
        db.ensure_production_schema()
        audit = db.audit_production_schema()
        assert audit["schema_version"] == db.SCHEMA_VERSION
        assert audit["ok"] is True
        applied = db._applied_migration("schema-xiaogu_production_schema_v5")
        assert applied is not None
        assert applied["checksum"] == db._migration_checksum(db.SCHEMA_V5_STATEMENTS)
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
