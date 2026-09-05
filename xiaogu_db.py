"""PostgreSQL persistence for production runs, snapshots, decisions, and outcomes."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import calendar as calendar_module
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    connect_args={"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))},
    pool_size=int(os.environ.get("XIAOGU_DB_POOL_SIZE", "8")),
    max_overflow=int(os.environ.get("XIAOGU_DB_MAX_OVERFLOW", "24")),
    pool_timeout=int(os.environ.get("XIAOGU_DB_POOL_TIMEOUT", "30")),
    pool_pre_ping=True,
)
_ACTIVE_DB_CONNECTION: ContextVar[Any | None] = ContextVar("xiaogu_active_db_connection", default=None)

TRADING_CALENDAR_MARKET = "ASHARE"
TRADING_DAY = "TRUE"
NON_TRADING_DAY = "FALSE"
CALENDAR_UNKNOWN = "UNKNOWN"
CALENDAR_DATA_UNAVAILABLE = "CALENDAR_DATA_UNAVAILABLE"
CALENDAR_VERSION_CONTENT_CONFLICT = "CALENDAR_VERSION_CONTENT_CONFLICT"
CALENDAR_INCOMPLETE = "CALENDAR_INCOMPLETE"
CALENDAR_MARKET_CONFLICT = "CALENDAR_MARKET_CONFLICT"
CALENDAR_SOURCE_MISSING = "CALENDAR_SOURCE_MISSING"
CALENDAR_VERSION_MISSING = "CALENDAR_VERSION_MISSING"
INVALID_CALENDAR_YEAR = "INVALID_CALENDAR_YEAR"
CALENDAR_DATASET_DIR = Path(__file__).resolve().parent / "data" / "trading_calendar"
# Test/import override. Normal runtime always resolves the year-specific path.
CALENDAR_DATASET_PATH: Path | None = None
SCHEMA_VERSION = "xiaogu_production_schema_v6"
PRODUCTION_SCAN_BLOCKED = "PRODUCTION_SCAN_BLOCKED"
OFFICIAL_PRODUCTION_OBSERVATION_EXISTS = "OFFICIAL_PRODUCTION_OBSERVATION_EXISTS"
OFFICIAL_PRODUCTION_OBSERVATION_AMBIGUOUS = "OFFICIAL_PRODUCTION_OBSERVATION_AMBIGUOUS"
MIGRATION_TYPE_SCHEMA = "PRODUCTION_SCHEMA_MIGRATION"
MIGRATION_TYPE_HISTORICAL = "HISTORICAL_DATA_REPAIR"
HISTORICAL_SNAPSHOT_MIGRATION_ID = "historical-snapshot-identity"
SNAPSHOT_INSERTED = "INSERTED"
SNAPSHOT_IDEMPOTENT = "IDEMPOTENT"
SNAPSHOT_IDENTITY_CONFLICT = "SNAPSHOT_IDENTITY_CONFLICT"
SNAPSHOT_PERSISTENCE_FAILED = "SNAPSHOT_PERSISTENCE_FAILED"
SNAPSHOT_IDENTITY_IMMUTABLE = True
SCHEMA_V2_STATEMENTS = ('ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT',
 'ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source TEXT',
 'ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source_time TEXT',
 'ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS symbol TEXT',
 'ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS payload_hash TEXT',
 'ALTER TABLE canonical_historical_snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT',
 'ALTER TABLE picks ADD COLUMN IF NOT EXISTS decision_id TEXT',
 'ALTER TABLE picks ADD COLUMN IF NOT EXISTS state TEXT',
 'ALTER TABLE picks ADD COLUMN IF NOT EXISTS position_state TEXT',
 'ALTER TABLE picks ADD COLUMN IF NOT EXISTS payload JSONB',
 'CREATE TABLE IF NOT EXISTS paper_observations (\n'
 '            paper_signal_id TEXT PRIMARY KEY,\n'
 '            decision_id TEXT NOT NULL,\n'
 '            snapshot_id TEXT NOT NULL,\n'
 '            lineage_id TEXT NOT NULL,\n'
 '            symbol TEXT NOT NULL,\n'
 '            signal_time TIMESTAMPTZ NOT NULL,\n'
 '            reference_price DOUBLE PRECISION NOT NULL,\n'
 '            paper_observation_state TEXT NOT NULL,\n'
 '            paper_position_state TEXT NOT NULL,\n'
 '            alpha_name TEXT NOT NULL,\n'
 '            alpha_version TEXT,\n'
 '            feature_version TEXT,\n'
 '            decision_version TEXT NOT NULL,\n'
 '            cost_model_version TEXT NOT NULL,\n'
 '            paper_observation_contract_version TEXT NOT NULL,\n'
 '            paper_only BOOLEAN NOT NULL DEFAULT TRUE,\n'
 '            live_order BOOLEAN NOT NULL DEFAULT FALSE,\n'
 '            calendar_version TEXT,\n'
 '            calendar_content_hash TEXT,\n'
 "            payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),\n"
 '            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n'
 '            UNIQUE (decision_id)\n'
 '        )',
 'CREATE TABLE IF NOT EXISTS trading_calendar (\n'
 '            trade_date DATE PRIMARY KEY,\n'
 "            market TEXT NOT NULL DEFAULT 'ASHARE',\n"
 '            is_trading_day BOOLEAN NOT NULL,\n'
 '            source TEXT NOT NULL,\n'
 '            source_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n'
 "            calendar_version TEXT NOT NULL DEFAULT 'CN_A_SHARE_2026_V1',\n"
 "            payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),\n"
 '            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n'
 '        )',
 'CREATE TABLE IF NOT EXISTS trading_calendar_migrations (\n'
 '            id BIGSERIAL PRIMARY KEY,\n'
 '            migration_id TEXT NOT NULL,\n'
 '            trade_date DATE NOT NULL,\n'
 '            market TEXT NOT NULL,\n'
 '            previous_is_trading_day BOOLEAN,\n'
 '            previous_source TEXT,\n'
 '            previous_calendar_version TEXT,\n'
 '            new_is_trading_day BOOLEAN NOT NULL,\n'
 '            new_source TEXT NOT NULL,\n'
 '            new_calendar_version TEXT NOT NULL,\n'
 '            source_timestamp TIMESTAMPTZ NOT NULL,\n'
 '            reason TEXT NOT NULL,\n'
 '            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n'
 '        )',
 'CREATE TABLE IF NOT EXISTS trading_calendar_versions (\n'
 '            calendar_version TEXT NOT NULL,\n'
 '            market TEXT NOT NULL,\n'
 '            effective_year INTEGER NOT NULL,\n'
 '            source TEXT NOT NULL,\n'
 '            source_timestamp TIMESTAMPTZ NOT NULL,\n'
 '            content_hash TEXT NOT NULL,\n'
 '            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n'
 "            status TEXT NOT NULL CHECK (status IN ('REGISTERED', 'ACTIVE', 'SUPERSEDED')),\n"
 '            PRIMARY KEY (calendar_version, market, effective_year)\n'
 '        )',
 'CREATE TABLE IF NOT EXISTS xiaogu_schema_version (\n'
 '            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),\n'
 '            schema_version TEXT NOT NULL,\n'
 '            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n'
 '        )',
 'CREATE TABLE IF NOT EXISTS xiaogu_schema_migrations (\n'
 '            migration_id TEXT PRIMARY KEY,\n'
 '            from_version TEXT,\n'
 '            to_version TEXT NOT NULL,\n'
 '            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n'
 '            checksum TEXT NOT NULL\n'
 '        )',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS paper_signal_id TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS decision_id TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS snapshot_id TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS lineage_id TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS symbol TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS signal_time TIMESTAMPTZ',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS reference_price DOUBLE PRECISION',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS paper_observation_state TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS paper_position_state TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS alpha_name TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS alpha_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS feature_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS decision_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS cost_model_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS paper_observation_contract_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS paper_only BOOLEAN',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS live_order BOOLEAN',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS payload JSONB',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS calendar_version TEXT',
 'ALTER TABLE paper_observations ADD COLUMN IF NOT EXISTS calendar_content_hash TEXT',
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS trade_date DATE',
 "ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'ASHARE'",
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS is_trading_day BOOLEAN',
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS source TEXT',
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS source_timestamp TIMESTAMPTZ',
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS calendar_version TEXT NOT NULL DEFAULT '
 "'CN_A_SHARE_2026_V1'",
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT '
 'NOW()',
 "UPDATE trading_calendar SET market = 'ASHARE' WHERE market IS NULL OR BTRIM(market) = ''",
 "UPDATE trading_calendar SET calendar_version = 'CN_A_SHARE_2026_V1' WHERE calendar_version IS "
 "NULL OR BTRIM(calendar_version) = ''",
 'UPDATE trading_calendar SET source_timestamp = COALESCE(source_timestamp, created_at, NOW()) '
 'WHERE source_timestamp IS NULL',
 'ALTER TABLE trading_calendar ALTER COLUMN source_timestamp SET NOT NULL',
 'ALTER TABLE trading_calendar ADD COLUMN IF NOT EXISTS payload JSONB',
 'ALTER TABLE returns ADD COLUMN IF NOT EXISTS decision_id TEXT',
 'ALTER TABLE returns ADD COLUMN IF NOT EXISTS payload JSONB',
 'ALTER TABLE returns ADD COLUMN IF NOT EXISTS calendar_version TEXT',
 'ALTER TABLE returns ADD COLUMN IF NOT EXISTS calendar_content_hash TEXT',
 'ALTER TABLE canonical_future_prices ADD COLUMN IF NOT EXISTS price_fact_hash TEXT',
 'CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id '
 'IS NOT NULL',
 'CREATE INDEX IF NOT EXISTS idx_returns_decision_id ON returns (decision_id)',
 'CREATE UNIQUE INDEX IF NOT EXISTS idx_returns_decision_date ON returns (decision_id, trade_date) '
 'WHERE decision_id IS NOT NULL',
 'CREATE INDEX IF NOT EXISTS idx_paper_observations_signal_time ON paper_observations(signal_time)',
 'CREATE INDEX IF NOT EXISTS idx_trading_calendar_open_days ON trading_calendar(trade_date) WHERE '
 'is_trading_day',
 'CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date)',
 'CREATE INDEX IF NOT EXISTS idx_snapshots_lineage_id ON snapshots (lineage_id)',
 'CREATE INDEX IF NOT EXISTS idx_snapshots_date_symbol ON snapshots (trade_date, symbol)',
 'CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_lineage_id ON '
 'canonical_historical_snapshots (lineage_id)',
 'CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_date ON '
 'canonical_historical_snapshots (trade_date, symbol)')
SCHEMA_V3_STATEMENTS = (
    "ALTER TABLE xiaogu_schema_migrations ADD COLUMN IF NOT EXISTS migration_type TEXT",
    "UPDATE xiaogu_schema_migrations SET migration_type = 'PRODUCTION_SCHEMA_MIGRATION' "
    "WHERE migration_type IS NULL OR BTRIM(migration_type) = ''",
    "ALTER TABLE xiaogu_schema_migrations ALTER COLUMN migration_type SET DEFAULT 'PRODUCTION_SCHEMA_MIGRATION'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_lineage_symbol ON snapshots (lineage_id, symbol)",
)
SCHEMA_V4_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS snapshot_identity_conflicts (
        id BIGSERIAL PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        existing_payload_hash TEXT NOT NULL,
        incoming_payload_hash TEXT NOT NULL,
        source TEXT,
        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_snapshot_identity_conflicts_snapshot_id "
    "ON snapshot_identity_conflicts (snapshot_id, detected_at)",
    """
    CREATE OR REPLACE FUNCTION xiaogu_protect_snapshot_identity()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $protect_snapshot_identity$
    BEGIN
      IF TG_OP <> 'UPDATE' THEN
        RETURN NEW;
      END IF;
      IF OLD.snapshot_id IS NULL OR BTRIM(CAST(OLD.snapshot_id AS text)) = '' THEN
        RETURN NEW;
      END IF;
      IF NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
         OR NEW.lineage_id IS DISTINCT FROM OLD.lineage_id
         OR NEW.symbol IS DISTINCT FROM OLD.symbol
         OR NEW.trade_date IS DISTINCT FROM OLD.trade_date
         OR NEW.source IS DISTINCT FROM OLD.source
         OR NEW.payload IS DISTINCT FROM OLD.payload THEN
        RAISE EXCEPTION 'SNAPSHOT_IDENTITY_IMMUTABLE';
      END IF;
      IF TG_TABLE_NAME = 'snapshots' THEN
        IF NEW.source_time IS DISTINCT FROM OLD.source_time
           OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash THEN
          RAISE EXCEPTION 'SNAPSHOT_IDENTITY_IMMUTABLE';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $protect_snapshot_identity$;
    """.strip(),
    "DROP TRIGGER IF EXISTS snapshots_identity_immutable ON snapshots",
    """
    CREATE TRIGGER snapshots_identity_immutable
    BEFORE UPDATE ON snapshots
    FOR EACH ROW
    EXECUTE FUNCTION xiaogu_protect_snapshot_identity()
    """.strip(),
    "DROP TRIGGER IF EXISTS canonical_historical_snapshots_identity_immutable ON canonical_historical_snapshots",
    """
    CREATE TRIGGER canonical_historical_snapshots_identity_immutable
    BEFORE UPDATE ON canonical_historical_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION xiaogu_protect_snapshot_identity()
    """.strip(),
)
SCHEMA_V5_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        original_snapshot_id TEXT NOT NULL,
        position_state TEXT NOT NULL,
        opened_trade_date DATE NOT NULL,
        closed_trade_date DATE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT positions_position_state_check CHECK (position_state IN ('FLAT', 'LONG')),
        CONSTRAINT positions_closed_state_check CHECK (
            (position_state = 'LONG' AND closed_trade_date IS NULL)
            OR (position_state = 'FLAT')
        ),
        CONSTRAINT positions_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES picks (decision_id),
        CONSTRAINT positions_original_snapshot_id_fkey FOREIGN KEY (original_snapshot_id) REFERENCES snapshots (snapshot_id)
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (symbol)",
    "CREATE INDEX IF NOT EXISTS idx_positions_decision_id ON positions (decision_id)",
)
SCHEMA_V6_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS scan_sessions (
        id BIGSERIAL PRIMARY KEY,
        trade_date DATE NOT NULL,
        scan_time TIMESTAMPTZ NOT NULL,
        source_id TEXT,
        quotes_count INTEGER DEFAULT 0,
        scored_count INTEGER DEFAULT 0,
        passed_count INTEGER DEFAULT 0,
        scan_dir TEXT,
        status TEXT DEFAULT 'completed',
        error_message TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ,
        data_version TEXT,
        market_snapshot JSONB DEFAULT CAST('{}' AS jsonb),
        source_status JSONB DEFAULT CAST('{}' AS jsonb),
        source_counts JSONB DEFAULT CAST('{}' AS jsonb),
        source_diagnostics JSONB DEFAULT CAST('{}' AS jsonb),
        production_run_id TEXT
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS production_runs (
        production_run_id TEXT PRIMARY KEY,
        trade_date DATE NOT NULL,
        scan_session_id INTEGER,
        run_mode TEXT NOT NULL DEFAULT 'PRODUCTION',
        rule_version TEXT,
        runner_version TEXT,
        scanner_version TEXT,
        schema_version TEXT,
        scoring_config_snapshot JSONB DEFAULT CAST('{}' AS jsonb),
        scoring_config_hash TEXT,
        input_payload_hash TEXT,
        status TEXT NOT NULL DEFAULT 'PENDING',
        error_message TEXT,
        retry_command TEXT,
        lineage_id TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ
    )
    """.strip(),
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS production_run_id TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS trade_date DATE",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS scan_session_id INTEGER",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS run_mode TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS status TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS lineage_id TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS scanner_version TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS schema_version TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS runner_version TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS input_payload_hash TEXT",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
    "ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_production_runs_trade_date ON production_runs (trade_date, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_production_runs_status ON production_runs (status)",
    "CREATE INDEX IF NOT EXISTS idx_production_runs_lineage_id ON production_runs (lineage_id)",
)


@contextmanager
def get_db():
    active = _ACTIVE_DB_CONNECTION.get()
    if active is not None:
        yield active
        return
    with engine.begin() as connection:
        yield connection


def init_db(sql_path: str = "scripts/xiaogu_db_init.sql") -> None:
    with engine.begin() as connection:
        connection.execute(text(open(sql_path, encoding="utf-8").read()))
    ensure_production_schema()
    seed_authoritative_a_share_calendar()


def _exec_schema(statement: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement))


def _schema_error_already_exists(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "duplicate object" in message


def _constraint_columns(table_name: str, constraint_type: str) -> list[str]:
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = 'public'
                  AND tc.table_name = :table_name
                  AND tc.constraint_type = :constraint_type
                ORDER BY kcu.ordinal_position
                """
            ),
            {"table_name": table_name, "constraint_type": constraint_type},
        ).fetchall()
    return [str(row[0]) for row in rows]


def _check_constraints(table_name: str) -> dict[str, str]:
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT con.conname, pg_get_constraintdef(con.oid)
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                WHERE nsp.nspname = 'public'
                  AND rel.relname = :table_name
                  AND con.contype = 'c'
                ORDER BY con.conname
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _unique_index_columns(table_name: str) -> set[tuple[str, ...]]:
    """Return each unique index key as its exact ordered column tuple."""
    keys: set[tuple[str, ...]] = set()
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = :table_name
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    for row in rows:
        definition = str(row[0] or "")
        if "UNIQUE" not in definition.upper():
            continue
        start = definition.find("(", definition.upper().find("UNIQUE"))
        end = definition.find(")", start + 1)
        if start < 0 or end < 0:
            continue
        columns = []
        for raw in definition[start + 1:end].split(","):
            # PostgreSQL may render order/null modifiers in index definitions.
            name = re.sub(r"\s+(ASC|DESC|NULLS\s+(FIRST|LAST))\b.*$", "", raw.strip(), flags=re.I)
            name = name.strip().strip('"')
            if name:
                columns.append(name)
        if columns:
            keys.add(tuple(columns))
    return keys


def _unique_constraint_columns(table_name: str) -> set[tuple[str, ...]]:
    """Return UNIQUE and PRIMARY KEY constraints as exact ordered tuples."""
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT con.contype, array_agg(att.attname ORDER BY keys.ordinality)
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
                  ON TRUE
                JOIN pg_attribute att
                  ON att.attrelid = rel.oid AND att.attnum = keys.attnum
                WHERE nsp.nspname = 'public'
                  AND rel.relname = :table_name
                  AND con.contype IN ('u', 'p')
                GROUP BY con.contype, con.oid
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    return {tuple(str(column) for column in row[1]) for row in rows}


def _foreign_keys(table_name: str) -> list[dict[str, str]]:
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT
                    tc.constraint_name,
                    kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.table_schema = 'public'
                  AND tc.table_name = :table_name
                  AND tc.constraint_type = 'FOREIGN KEY'
                """
            ),
            {"table_name": table_name},
        ).mappings()
        return [dict(row) for row in rows]


def _exists_label(present: bool) -> str:
    return "EXISTS" if present else "MISSING"


def _count_unbound_decision_ids(table_name: str) -> int:
    columns = _table_columns(table_name)
    if "decision_id" not in columns:
        return -1
    with engine.connect() as db:
        return int(
            db.execute(
                text(
                    f'SELECT count(*) FROM "{table_name}" '
                    "WHERE decision_id IS NULL OR BTRIM(CAST(decision_id AS text)) = ''"
                )
            ).scalar_one()
        )


def _count_returns_decision_fk_conflicts() -> int:
    columns_returns = _table_columns("returns")
    columns_picks = _table_columns("picks")
    if "decision_id" not in columns_returns or "decision_id" not in columns_picks:
        return -1
    with engine.connect() as db:
        return int(
            db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM returns r
                    WHERE r.decision_id IS NOT NULL
                      AND BTRIM(CAST(r.decision_id AS text)) <> ''
                      AND NOT EXISTS (
                          SELECT 1 FROM picks p
                          WHERE p.decision_id = r.decision_id
                      )
                    """
                )
            ).scalar_one()
        )


def _count_unresolved_snapshot_ids(table_name: str) -> int:
    columns = _table_columns(table_name)
    if "snapshot_id" not in columns:
        return -1
    with engine.connect() as db:
        return int(
            db.execute(
                text(
                    f'SELECT count(*) FROM "{table_name}" '
                    "WHERE snapshot_id IS NULL OR BTRIM(CAST(snapshot_id AS text)) = ''"
                )
            ).scalar_one()
        )


def audit_production_schema() -> Dict[str, Any]:
    """Inspect required production identity objects. Never rewrite historical values."""
    required_columns = {
        "snapshots": (
            "snapshot_id", "lineage_id", "symbol", "trade_date", "source", "source_time",
            "payload_hash",
        ),
        "picks": ("decision_id", "position_state"),
        "paper_observations": (
            "paper_signal_id", "decision_id", "snapshot_id", "lineage_id", "symbol",
            "signal_time", "reference_price", "paper_observation_state",
            "paper_position_state", "alpha_name", "alpha_version", "feature_version",
            "decision_version", "cost_model_version",
            "paper_observation_contract_version", "paper_only", "live_order",
            "calendar_version", "calendar_content_hash", "payload",
        ),
        "returns": ("decision_id", "calendar_version", "calendar_content_hash"),
        "canonical_historical_snapshots": (
            "snapshot_id", "lineage_id", "symbol", "trade_date", "signal_time",
            "source", "source_timestamp", "snapshot_version", "point_in_time",
            "available_at", "price_basis", "payload", "created_at",
        ),
        "canonical_future_prices": ("symbol", "date", "source", "price_basis", "price_fact_hash"),
        "trading_calendar": (
            "trade_date", "market", "is_trading_day", "source", "source_timestamp",
            "calendar_version", "payload", "created_at",
        ),
        "trading_calendar_versions": (
            "calendar_version", "market", "effective_year", "source",
            "source_timestamp", "content_hash", "created_at", "status",
        ),
        "xiaogu_schema_version": ("singleton", "schema_version", "updated_at"),
        "xiaogu_schema_migrations": (
            "migration_id", "from_version", "to_version", "applied_at", "checksum",
            "migration_type",
        ),
        "snapshot_identity_conflicts": (
            "snapshot_id", "existing_payload_hash", "incoming_payload_hash", "source", "detected_at",
        ),
        "positions": (
            "position_id", "decision_id", "symbol", "original_snapshot_id",
            "position_state", "opened_trade_date", "closed_trade_date",
            "created_at", "updated_at",
        ),
        "production_runs": (
            "production_run_id", "trade_date", "status", "run_mode", "lineage_id",
            "created_at",
        ),
    }
    required_unique = {
        "snapshots": {("snapshot_id",), ("lineage_id", "symbol")},
        "picks": {("decision_id",)},
        "paper_observations": {("paper_signal_id",), ("decision_id",)},
        "returns": {("decision_id", "trade_date")},
        "canonical_historical_snapshots": {("snapshot_id",)},
        "trading_calendar": {("trade_date",)},
        "trading_calendar_versions": {
            ("calendar_version", "market", "effective_year"),
        },
        "positions": {("position_id",), ("decision_id",)},
        "production_runs": {("production_run_id",)},
    }
    required_indexes = {
        "snapshots": ("idx_snapshots_trade_date", "idx_snapshots_lineage_id"),
        "picks": ("idx_picks_decision_id",),
        "paper_observations": ("idx_paper_observations_signal_time",),
        "returns": ("idx_returns_decision_id",),
        "canonical_historical_snapshots": (
            "idx_canonical_historical_snapshots_lineage_id",
            "idx_canonical_historical_snapshots_date",
        ),
        "trading_calendar": ("idx_trading_calendar_open_days",),
        "snapshot_identity_conflicts": ("idx_snapshot_identity_conflicts_snapshot_id",),
        "positions": ("idx_positions_symbol", "idx_positions_decision_id"),
        "production_runs": ("idx_production_runs_trade_date", "idx_production_runs_lineage_id"),
    }
    required_triggers = {
        "snapshots": ("snapshots_identity_immutable",),
        "canonical_historical_snapshots": ("canonical_historical_snapshots_identity_immutable",),
    }
    required_primary_key = {
        "snapshots": ("snapshot_id",),
        "canonical_historical_snapshots": ("snapshot_id",),
        "paper_observations": ("paper_signal_id",),
        "picks": ("id",),
        "returns": ("id",),
        "canonical_future_prices": ("symbol", "date"),
        "trading_calendar": ("trade_date",),
        "trading_calendar_versions": ("calendar_version", "market", "effective_year"),
        "xiaogu_schema_version": ("singleton",),
        "xiaogu_schema_migrations": ("migration_id",),
        "positions": ("position_id",),
        "production_runs": ("production_run_id",),
    }
    tables = {}
    ok = True
    for table_name, columns in required_columns.items():
        present_columns = _table_columns(table_name)
        checks = _check_constraints(table_name)
        unique_keys = _unique_constraint_columns(table_name) | _unique_index_columns(table_name)
        primary_key = tuple(_constraint_columns(table_name, "PRIMARY KEY"))
        with engine.connect() as db:
            index_names = {
                str(row[0])
                for row in db.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public' AND tablename = :table_name
                        """
                    ),
                    {"table_name": table_name},
                ).fetchall()
            }
            trigger_names = {
                str(row[0])
                for row in db.execute(
                    text(
                        """
                        SELECT t.tgname
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = :table_name
                          AND NOT t.tgisinternal
                        """
                    ),
                    {"table_name": table_name},
                ).fetchall()
            }
        column_audit = {column: _exists_label(column in present_columns) for column in columns}
        unique_audit = {
            expected: _exists_label(expected in unique_keys)
            for expected in required_unique.get(table_name, set())
        }
        index_audit = {name: _exists_label(name in index_names) for name in required_indexes.get(table_name, ())}
        trigger_audit = {
            name: _exists_label(name in trigger_names)
            for name in required_triggers.get(table_name, ())
        }
        expected_pk = required_primary_key.get(table_name)
        if expected_pk is None:
            pk_status = "EXISTS" if primary_key else "MISSING"
        elif primary_key == expected_pk:
            pk_status = "EXISTS"
        elif primary_key:
            pk_status = "CONFLICT"
        else:
            pk_status = "MISSING"
        fk_audit = {
            f"{item['column_name']}->{item['foreign_table']}.{item['foreign_column']}": "EXISTS"
            for item in _foreign_keys(table_name)
        }
        if table_name == "returns":
            expected_fk = "decision_id->picks.decision_id"
            if expected_fk not in fk_audit:
                fk_audit[expected_fk] = "MISSING"
        if table_name == "paper_observations":
            expected_fk = "decision_id->picks.decision_id"
            if expected_fk not in fk_audit:
                fk_audit[expected_fk] = "MISSING"
        if table_name == "positions":
            for expected_fk in (
                "decision_id->picks.decision_id",
                "original_snapshot_id->snapshots.snapshot_id",
            ):
                if expected_fk not in fk_audit:
                    fk_audit[expected_fk] = "MISSING"
        tables[table_name] = {
            "columns": column_audit,
            "indexes": index_audit,
            "unique": unique_audit,
            "checks": checks,
            "primary_key": {"columns": list(primary_key), "status": pk_status},
            "foreign_keys": fk_audit,
            "unique_constraints": sorted(unique_keys),
            "triggers": trigger_audit,
        }
        ok = ok and all(value == "EXISTS" for value in column_audit.values())
        ok = ok and all(value == "EXISTS" for value in unique_audit.values())
        ok = ok and all(value == "EXISTS" for value in index_audit.values())
        ok = ok and all(value == "EXISTS" for value in trigger_audit.values())
        ok = ok and pk_status == "EXISTS"
        if table_name == "returns":
            ok = ok and fk_audit.get("decision_id->picks.decision_id") == "EXISTS"
        if table_name == "paper_observations":
            ok = ok and fk_audit.get("decision_id->picks.decision_id") == "EXISTS"
            paper_only_check = str(checks.get("paper_observations_paper_only_check") or "").replace("(", "").replace(")", "").strip()
            live_order_check = str(checks.get("paper_observations_live_order_check") or "").replace("(", "").replace(")", "").strip()
            ok = ok and paper_only_check == "CHECK paper_only"
            ok = ok and live_order_check == "CHECK NOT live_order"
        if table_name == "positions":
            ok = ok and fk_audit.get("decision_id->picks.decision_id") == "EXISTS"
            ok = ok and fk_audit.get("original_snapshot_id->snapshots.snapshot_id") == "EXISTS"
            state_check = str(checks.get("positions_position_state_check") or "")
            ok = ok and "FLAT" in state_check and "LONG" in state_check
    anomalies = {
        "picks_missing_decision_id": _count_unbound_decision_ids("picks"),
        "returns_missing_decision_id": _count_unbound_decision_ids("returns"),
        "returns_decision_fk_conflicts": _count_returns_decision_fk_conflicts(),
        "historical_snapshots_missing_snapshot_id": _count_unresolved_snapshot_ids("canonical_historical_snapshots"),
    }
    if anomalies["returns_decision_fk_conflicts"] > 0:
        tables["returns"]["foreign_keys"]["decision_id->picks.decision_id"] = "CONFLICT"
        ok = False
    with engine.connect() as db:
        schema_version = db.execute(
            text("SELECT schema_version FROM xiaogu_schema_version WHERE singleton = TRUE")
        ).scalar()
    schema_ok = str(schema_version or "") == SCHEMA_VERSION
    ok = ok and schema_ok
    last_migration = _last_schema_migration()
    return {
        "ok": ok,
        "audit": "PASS" if ok else "FAIL",
        "tables": tables,
        "historical_anomalies": anomalies,
        "schema_version": schema_version,
        "schema_version_status": "EXISTS" if schema_ok else "CONFLICT",
        "last_migration": last_migration,
    }


def _schema_version_ordinal(version: str | None) -> int:
    if not version:
        return 0
    match = re.search(r"v(\d+)$", str(version))
    if not match:
        raise RuntimeError(f"SCHEMA_VERSION_INVALID:{version}")
    return int(match.group(1))


def _migration_checksum(statements: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()


def _schema_migrations() -> tuple[dict[str, Any], ...]:
    return (
        {
            "migration_id": "schema-xiaogu_production_schema_v2",
            "from_version": None,
            "to_version": "xiaogu_production_schema_v2",
            "statements": SCHEMA_V2_STATEMENTS,
            "apply": _apply_v2_schema,
        },
        {
            "migration_id": "schema-xiaogu_production_schema_v3",
            "from_version": "xiaogu_production_schema_v2",
            "to_version": "xiaogu_production_schema_v3",
            "statements": SCHEMA_V3_STATEMENTS,
            "apply": _apply_identity_constraints,
        },
        {
            "migration_id": "schema-xiaogu_production_schema_v4",
            "from_version": "xiaogu_production_schema_v3",
            "to_version": "xiaogu_production_schema_v4",
            "statements": SCHEMA_V4_STATEMENTS,
            "apply": _apply_snapshot_identity_lock,
        },
        {
            "migration_id": "schema-xiaogu_production_schema_v5",
            "from_version": "xiaogu_production_schema_v4",
            "to_version": "xiaogu_production_schema_v5",
            "statements": SCHEMA_V5_STATEMENTS,
            "apply": _apply_position_identity_constraints,
        },
        {
            "migration_id": "schema-xiaogu_production_schema_v6",
            "from_version": "xiaogu_production_schema_v5",
            "to_version": "xiaogu_production_schema_v6",
            "statements": SCHEMA_V6_STATEMENTS,
            "apply": _apply_production_run_persistence_contract,
        },
    )


def _schema_version() -> str | None:
    if "schema_version" not in _table_columns("xiaogu_schema_version"):
        return None
    with engine.connect() as db:
        value = db.execute(
            text("SELECT schema_version FROM xiaogu_schema_version WHERE singleton = TRUE")
        ).scalar()
    return str(value) if value else None


def _applied_migration(migration_id: str) -> Dict[str, Any] | None:
    if "migration_id" not in _table_columns("xiaogu_schema_migrations"):
        return None
    with engine.connect() as db:
        row = db.execute(
            text(
                "SELECT migration_id, from_version, to_version, checksum, applied_at, "
                + (
                    "migration_type "
                    if "migration_type" in _table_columns("xiaogu_schema_migrations")
                    else "CAST(NULL AS text) AS migration_type "
                )
                + "FROM xiaogu_schema_migrations WHERE migration_id = :migration_id"
            ),
            {"migration_id": migration_id},
        ).mappings().first()
    return dict(row) if row else None


def _last_schema_migration() -> Dict[str, Any] | None:
    if "migration_id" not in _table_columns("xiaogu_schema_migrations"):
        return None
    type_sql = (
        "migration_type"
        if "migration_type" in _table_columns("xiaogu_schema_migrations")
        else "CAST(NULL AS text) AS migration_type"
    )
    with engine.connect() as db:
        row = db.execute(
            text(
                f"""
                SELECT migration_id, from_version, to_version, checksum, applied_at, {type_sql}
                FROM xiaogu_schema_migrations
                ORDER BY applied_at DESC, migration_id DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    return dict(row) if row else None


def _ensure_schema_registry() -> None:
    _exec_schema(
        """
        CREATE TABLE IF NOT EXISTS xiaogu_schema_version (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            schema_version TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _exec_schema(
        """
        CREATE TABLE IF NOT EXISTS xiaogu_schema_migrations (
            migration_id TEXT PRIMARY KEY,
            from_version TEXT,
            to_version TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            checksum TEXT NOT NULL
        )
        """
    )


def _record_schema_migration(
    *,
    migration_id: str,
    from_version: str | None,
    to_version: str,
    checksum: str,
    migration_type: str = MIGRATION_TYPE_SCHEMA,
    update_schema_version: bool = True,
) -> None:
    existing = _applied_migration(migration_id)
    if existing:
        if str(existing.get("checksum") or "") != checksum:
            raise RuntimeError("SCHEMA_MIGRATION_CHECKSUM_CONFLICT")
        return
    columns = _table_columns("xiaogu_schema_migrations")
    fields = ["migration_id", "from_version", "to_version", "checksum"]
    params: Dict[str, Any] = {
        "migration_id": migration_id,
        "from_version": from_version,
        "to_version": to_version,
        "checksum": checksum,
    }
    if "migration_type" in columns:
        fields.append("migration_type")
        params["migration_type"] = migration_type
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO xiaogu_schema_migrations ("
                + ", ".join(fields)
                + ") VALUES ("
                + ", ".join(f":{field}" for field in fields)
                + ")"
            ),
            params,
        )
        if update_schema_version:
            db.execute(
                text(
                    """
                    INSERT INTO xiaogu_schema_version (singleton, schema_version)
                    VALUES (TRUE, :schema_version)
                    ON CONFLICT (singleton) DO UPDATE
                    SET schema_version = EXCLUDED.schema_version,
                        updated_at = NOW()
                    """
                ),
                {"schema_version": to_version},
            )


def _assert_applied_migration_checksums() -> None:
    for migration in _schema_migrations():
        applied = _applied_migration(str(migration["migration_id"]))
        if not applied:
            continue
        expected = _migration_checksum(migration["statements"])
        if str(applied.get("checksum") or "") != expected:
            raise RuntimeError("SCHEMA_MIGRATION_CHECKSUM_CONFLICT")


def _backfill_future_price_hashes() -> None:
    columns = _table_columns("canonical_future_prices")
    if "price_fact_hash" not in columns:
        return
    with engine.begin() as db:
        legacy_rows = [
            dict(row)
            for row in db.execute(
                text(
                    "SELECT symbol, date, open, high, low, close, volume, amount, "
                    "source, source_timestamp, price_basis, price_fact_hash "
                    "FROM canonical_future_prices WHERE price_fact_hash IS NULL OR BTRIM(price_fact_hash) = ''"
                )
            ).mappings()
        ]
        for row in legacy_rows:
            fact = canonical_future_price_fact(row)
            db.execute(
                text(
                    "UPDATE canonical_future_prices SET price_fact_hash = :price_fact_hash "
                    "WHERE symbol = :symbol AND date = :date"
                ),
                fact,
            )


def _apply_identity_constraints() -> None:
    _ensure_snapshot_primary_key("snapshots")
    _ensure_table_primary_key("paper_observations", ("paper_signal_id",))
    _ensure_table_primary_key("trading_calendar", ("trade_date",))
    _ensure_table_primary_key("canonical_future_prices", ("symbol", "date"))
    try:
        _exec_schema("CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_lineage_symbol ON snapshots (lineage_id, symbol)")
    except SQLAlchemyError as exc:
        if not _schema_error_already_exists(exc):
            raise
    try:
        _exec_schema(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_snapshot_id "
            "ON canonical_historical_snapshots (snapshot_id)"
        )
    except SQLAlchemyError as exc:
        if not _schema_error_already_exists(exc):
            raise
    if ("decision_id",) not in (
        _unique_constraint_columns("picks") | _unique_index_columns("picks")
    ):
        _exec_schema("ALTER TABLE picks ADD CONSTRAINT picks_decision_id_key UNIQUE (decision_id)")
    paper_foreign_keys = {
        f"{item['column_name']}->{item['foreign_table']}.{item['foreign_column']}"
        for item in _foreign_keys("paper_observations")
    }
    if "decision_id->picks.decision_id" not in paper_foreign_keys:
        _exec_schema(
            "ALTER TABLE paper_observations ADD CONSTRAINT "
            "paper_observations_decision_id_fkey FOREIGN KEY (decision_id) "
            "REFERENCES picks (decision_id)"
        )
    paper_checks = _check_constraints("paper_observations")
    if "paper_observations_paper_only_check" not in paper_checks:
        _exec_schema(
            "ALTER TABLE paper_observations ADD CONSTRAINT "
            "paper_observations_paper_only_check CHECK (paper_only)"
        )
    if "paper_observations_live_order_check" not in paper_checks:
        _exec_schema(
            "ALTER TABLE paper_observations ADD CONSTRAINT "
            "paper_observations_live_order_check CHECK (NOT live_order)"
        )
    return_foreign_keys = {
        f"{item['column_name']}->{item['foreign_table']}.{item['foreign_column']}"
        for item in _foreign_keys("returns")
    }
    if "decision_id->picks.decision_id" not in return_foreign_keys:
        _exec_schema(
            "ALTER TABLE returns ADD CONSTRAINT returns_decision_id_fkey "
            "FOREIGN KEY (decision_id) REFERENCES picks (decision_id)"
        )


def _apply_v2_schema() -> None:
    _backfill_future_price_hashes()
    _apply_identity_constraints()


def _apply_snapshot_identity_lock() -> None:
    _apply_identity_constraints()


def _apply_position_identity_constraints() -> None:
    """Lock position identity without rewriting historical IDs or outcomes."""
    _apply_identity_constraints()
    if "position_id" not in _table_columns("positions"):
        raise RuntimeError("POSITION_SCHEMA_MISSING")
    fks = {
        f"{item['column_name']}->{item['foreign_table']}.{item['foreign_column']}"
        for item in _foreign_keys("positions")
    }
    if "decision_id->picks.decision_id" not in fks:
        _exec_schema(
            "ALTER TABLE positions ADD CONSTRAINT positions_decision_id_fkey "
            "FOREIGN KEY (decision_id) REFERENCES picks (decision_id)"
        )
    if "original_snapshot_id->snapshots.snapshot_id" not in fks:
        _exec_schema(
            "ALTER TABLE positions ADD CONSTRAINT positions_original_snapshot_id_fkey "
            "FOREIGN KEY (original_snapshot_id) REFERENCES snapshots (snapshot_id)"
        )


def _column_udt_name(table_name: str, column_name: str) -> str:
    with engine.connect() as db:
        value = db.execute(
            text(
                """
                SELECT udt_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    return str(value or "")


def _align_production_run_scan_session_fk() -> None:
    parent_type = _column_udt_name("scan_sessions", "id")
    child_type = _column_udt_name("production_runs", "scan_session_id")
    if not parent_type or not child_type or parent_type == child_type:
        return
    try:
        _exec_schema("ALTER TABLE production_runs DROP CONSTRAINT IF EXISTS production_runs_scan_session_id_fkey")
    except SQLAlchemyError as exc:
        if not _schema_error_already_exists(exc):
            raise
    _exec_schema(
        f"ALTER TABLE production_runs ALTER COLUMN scan_session_id TYPE {parent_type} "
        f"USING CAST(scan_session_id AS {parent_type})"
    )


def _apply_production_run_persistence_contract() -> None:
    """Adopt production_runs into the schema owner without rewriting historical run rows."""
    columns = _table_columns("production_runs")
    if not columns:
        raise RuntimeError("PRODUCTION_RUNS_SCHEMA_MISSING")
    if "lineage_id" not in columns:
        _exec_schema("ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS lineage_id TEXT")
        columns = _table_columns("production_runs")
    if "production_run_id" not in columns:
        _exec_schema("ALTER TABLE production_runs ADD COLUMN IF NOT EXISTS production_run_id TEXT")
        columns = _table_columns("production_runs")
    if "id" in _table_columns("production_runs") and "production_run_id" in _table_columns("production_runs"):
        _exec_schema(
            "UPDATE production_runs "
            "SET production_run_id = 'legacy-' || CAST(id AS text) "
            "WHERE production_run_id IS NULL OR BTRIM(CAST(production_run_id AS text)) = ''"
        )
    _ensure_table_primary_key("production_runs", ("production_run_id",))
    session_columns = _table_columns("scan_sessions")
    if session_columns and "id" in session_columns and "scan_session_id" in _table_columns("production_runs"):
        _align_production_run_scan_session_fk()
        fks = {
            f"{item['column_name']}->{item['foreign_table']}.{item['foreign_column']}"
            for item in _foreign_keys("production_runs")
        }
        if "scan_session_id->scan_sessions.id" not in fks:
            try:
                _exec_schema(
                    "ALTER TABLE production_runs ADD CONSTRAINT production_runs_scan_session_id_fkey "
                    "FOREIGN KEY (scan_session_id) REFERENCES scan_sessions (id)"
                )
            except SQLAlchemyError as exc:
                if not _schema_error_already_exists(exc):
                    raise


def _apply_schema_migration(migration: Dict[str, Any]) -> None:
    migration_id = str(migration["migration_id"])
    checksum = _migration_checksum(migration["statements"])
    applied = _applied_migration(migration_id)
    if applied:
        if str(applied.get("checksum") or "") != checksum:
            raise RuntimeError("SCHEMA_MIGRATION_CHECKSUM_CONFLICT")
        return
    for statement in migration["statements"]:
        _exec_schema(statement)
    apply_hook = migration.get("apply")
    if callable(apply_hook):
        apply_hook()
    _record_schema_migration(
        migration_id=migration_id,
        from_version=migration.get("from_version"),
        to_version=str(migration["to_version"]),
        checksum=checksum,
        migration_type=MIGRATION_TYPE_SCHEMA,
    )


def _apply_pending_schema_migrations() -> None:
    current = _schema_version()
    if current and _schema_version_ordinal(current) > _schema_version_ordinal(SCHEMA_VERSION):
        raise RuntimeError("SCHEMA_VERSION_AHEAD")
    _assert_applied_migration_checksums()
    for migration in _schema_migrations():
        current = _schema_version()
        if current and _schema_version_ordinal(current) > _schema_version_ordinal(SCHEMA_VERSION):
            raise RuntimeError("SCHEMA_VERSION_AHEAD")
        if current == SCHEMA_VERSION:
            break
        target = str(migration["to_version"])
        if _schema_version_ordinal(current) >= _schema_version_ordinal(target):
            continue
        _apply_schema_migration(migration)
    current = _schema_version()
    if current != SCHEMA_VERSION:
        raise RuntimeError("PRODUCTION_SCHEMA_CONTRACT_FAILED")
    _assert_applied_migration_checksums()


def ensure_production_schema() -> None:
    """Read schema version, apply pending migrations, then audit. ALTER failure blocks production."""
    _ensure_schema_registry()
    _apply_pending_schema_migrations()
    audit = audit_production_schema()
    if not audit["ok"]:
        raise RuntimeError("PRODUCTION_SCHEMA_CONTRACT_FAILED")


def _ensure_table_primary_key(table_name: str, columns: tuple[str, ...]) -> None:
    """Migrate a table's primary key only when existing facts prove the identity."""
    expected = list(columns)
    current = _constraint_columns(table_name, "PRIMARY KEY")
    if current == expected:
        return
    present = _table_columns(table_name)
    missing = [column for column in columns if column not in present]
    if missing:
        raise RuntimeError(f"SCHEMA_PRIMARY_KEY_COLUMN_MISSING:{table_name}:{','.join(missing)}")
    with engine.connect() as db:
        nulls = int(
            db.execute(
                text(
                    "SELECT count(*) FROM "
                    f'"{table_name}" WHERE ' + " OR ".join(
                        f'"{column}" IS NULL' for column in columns
                    )
                )
            ).scalar_one()
        )
        duplicates = int(
            db.execute(
                text(
                    "SELECT count(*) FROM (SELECT "
                    + ", ".join(f'"{column}"' for column in columns)
                    + f' FROM "{table_name}" GROUP BY '
                    + ", ".join(f'"{column}"' for column in columns)
                    + " HAVING count(*) > 1) AS duplicate_keys"
                )
            ).scalar_one()
        )
    if nulls or duplicates:
        raise RuntimeError(f"SCHEMA_PRIMARY_KEY_IDENTITY_UNRESOLVED:{table_name}")
    if current:
        _exec_schema(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{table_name}_pkey"')
    for column in columns:
        _exec_schema(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column}" SET NOT NULL')
    _exec_schema(
        f'ALTER TABLE "{table_name}" ADD PRIMARY KEY ('
        + ", ".join(f'"{column}"' for column in columns)
        + ")"
    )


def _ensure_snapshot_primary_key(table_name: str) -> None:
    """Switch PK to snapshot_id only when every row already has a real snapshot_id."""
    columns = _table_columns(table_name)
    if "snapshot_id" not in columns:
        raise RuntimeError(f"SCHEMA_SNAPSHOT_ID_MISSING:{table_name}")
    unresolved = _count_unresolved_snapshot_ids(table_name)
    primary_key = _constraint_columns(table_name, "PRIMARY KEY")
    if unresolved:
        if primary_key == ["snapshot_id"]:
            raise RuntimeError(f"SCHEMA_UNRESOLVED_SNAPSHOT_IDENTITY:{table_name}")
        return
    if primary_key == ["snapshot_id"]:
        return
    if primary_key == ["lineage_id"]:
        _exec_schema(f'ALTER TABLE "{table_name}" DROP CONSTRAINT {table_name}_pkey')
    _exec_schema(f'ALTER TABLE "{table_name}" ALTER COLUMN snapshot_id SET NOT NULL')
    if primary_key != ["snapshot_id"]:
        _exec_schema(f'ALTER TABLE "{table_name}" ADD PRIMARY KEY (snapshot_id)')


def snapshot_payload_identity(payload: Any) -> str:
    """Stable identity of one snapshot payload, excluding write-time metadata."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    from xiaogu_forward_snapshot import snapshot_payload_hash
    return snapshot_payload_hash(payload)


def canonical_future_price_fact(bar: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one immutable future OHLC fact before persistence."""
    required = ("symbol", "date", "open", "high", "low", "close", "source", "price_basis")
    missing = [field for field in required if bar.get(field) in (None, "")]
    if missing:
        raise ValueError("PRICE_FACT_REQUIRED:" + ",".join(missing))
    if str(bar["price_basis"]) != "UNADJUSTED":
        raise ValueError(f"UNSUPPORTED_PRICE_BASIS:{bar['price_basis']}")
    normalized = {
        "symbol": str(bar["symbol"]).zfill(6),
        "date": str(bar["date"])[:10],
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": None if bar.get("volume") in (None, "") else float(bar["volume"]),
        "amount": None if bar.get("amount") in (None, "") else float(bar["amount"]),
        "source": str(bar["source"]),
        "source_timestamp": str(bar.get("source_timestamp") or ""),
        "price_basis": str(bar["price_basis"]),
    }
    identity = {
        key: normalized[key]
        for key in ("symbol", "date", "open", "high", "low", "close", "volume", "amount", "price_basis", "source")
    }
    normalized["price_fact_hash"] = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized


def _recover_historical_snapshot_id(row: Dict[str, Any]) -> str:
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    existing = str(payload.get("snapshot_id") or row.get("snapshot_id") or "").strip()
    if existing:
        return existing
    symbol = str(row.get("symbol") or payload.get("symbol") or "").strip()
    trade_date = str(row.get("trade_date") or payload.get("trade_date") or "").strip()
    source = str(row.get("source") or payload.get("source") or "").strip()
    source_time = str(
        row.get("signal_time")
        or row.get("source_timestamp")
        or payload.get("source_time")
        or payload.get("signal_time")
        or ""
    ).strip()
    if not all((symbol, trade_date, source, source_time)):
        return ""
    return hashlib.sha256(
        json.dumps(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "source": source,
                "source_time": source_time,
                "lineage_id": str(row.get("lineage_id") or ""),
                "payload_identity": snapshot_payload_identity(payload),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def migrate_historical_snapshot_identity() -> Dict[str, Any]:
    """RESEARCH / DATA MIGRATION ONLY.

    Recover snapshot_id from existing historical facts. Never copy lineage_id.
    Never rewrite payload or historical prices. Production init must not call this.
    """
    ensure_production_schema()
    columns = _table_columns("canonical_historical_snapshots")
    if "snapshot_id" not in columns:
        raise RuntimeError("SCHEMA_SNAPSHOT_ID_MISSING:canonical_historical_snapshots")
    recovered = unresolved = conflicts = 0
    with get_db() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT lineage_id, snapshot_id, symbol, trade_date, signal_time,
                           source, source_timestamp, payload
                    FROM canonical_historical_snapshots
                    """
                )
            ).mappings()
        ]
        seen: Dict[str, str] = {}
        for row in rows:
            current = str(row.get("snapshot_id") or "").strip()
            recovered_id = _recover_historical_snapshot_id(row)
            identity = snapshot_payload_identity(row.get("payload"))
            if not recovered_id:
                unresolved += 1
                continue
            previous_identity = seen.get(recovered_id)
            if previous_identity and previous_identity != identity:
                conflicts += 1
                continue
            seen[recovered_id] = identity
            if current == recovered_id:
                recovered += 1
                continue
            if current and current != recovered_id:
                conflicts += 1
                continue
            db.execute(
                text(
                    """
                    UPDATE canonical_historical_snapshots
                    SET snapshot_id = :snapshot_id
                    WHERE lineage_id = :lineage_id
                      AND (snapshot_id IS NULL OR BTRIM(CAST(snapshot_id AS text)) = '')
                    """
                ),
                {"snapshot_id": recovered_id, "lineage_id": row["lineage_id"]},
            )
            recovered += 1
    if conflicts:
        raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")
    try:
        _ensure_snapshot_primary_key("canonical_historical_snapshots")
        _record_schema_migration(
            migration_id=HISTORICAL_SNAPSHOT_MIGRATION_ID,
            from_version=_schema_version(),
            to_version=_schema_version() or SCHEMA_VERSION,
            checksum=_migration_checksum((
                "historical-snapshot-identity",
                "never-copy-lineage-id",
                "never-rewrite-payload",
            )),
            migration_type=MIGRATION_TYPE_HISTORICAL,
            update_schema_version=False,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError("MIGRATION_FAILED") from exc
    return {
        "recovered": recovered,
        "unresolved": unresolved,
        "conflicts": conflicts,
        "primary_key": _constraint_columns("canonical_historical_snapshots", "PRIMARY KEY"),
        "migration_type": MIGRATION_TYPE_HISTORICAL,
    }


def verify_persisted_snapshot(
    snapshot_id: str = "",
    lineage_id: str = "",
    trade_date: str = "",
    source: str = "",
    source_time: str = "",
    symbol: str = "",
    payload_hash: str = "",
) -> bool:
    """Prove the canonical snapshot exists in PostgreSQL. Local files are not persistence."""
    if not snapshot_id:
        return False
    try:
        ensure_production_schema()
        columns = _table_columns("snapshots")
        if not columns:
            return False
        clauses = ["snapshot_id = :snapshot_id"]
        params: Dict[str, Any] = {"snapshot_id": snapshot_id}
        if lineage_id:
            clauses.append("lineage_id = :lineage_id")
            params["lineage_id"] = lineage_id
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if trade_date:
            clauses.append("trade_date = CAST(:trade_date AS date)")
            params["trade_date"] = trade_date
        with engine.connect() as db:
            row = db.execute(
                text(
                    "SELECT payload, lineage_id, snapshot_id, source, source_time, "
                    "payload_hash, symbol, trade_date "
                    f"FROM snapshots WHERE {' AND '.join(clauses)} LIMIT 1"
                ),
                params,
            ).mappings().first()
        if not row:
            return False
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        stored_snapshot_id = str(row.get("snapshot_id") or "")
        if stored_snapshot_id != snapshot_id or str(payload.get("snapshot_id") or "") != snapshot_id:
            return False
        stored_source = str(row.get("source") or payload.get("source") or "")
        if source and stored_source != source:
            return False
        stored_source_time = str(row.get("source_time") or payload.get("source_time") or "")
        if source_time and stored_source_time != source_time:
            return False
        if lineage_id and str(row.get("lineage_id") or "") != lineage_id:
            return False
        if symbol and str(row.get("symbol") or "") != symbol:
            return False
        from xiaogu_forward_snapshot import snapshot_payload_hash
        computed_hash = snapshot_payload_hash(payload)
        stored_hash = str(row.get("payload_hash") or payload.get("payload_hash") or "")
        if payload_hash:
            if payload_hash != stored_hash and payload_hash != computed_hash:
                return False
        return True
    except SQLAlchemyError:
        return False


def fetch_persisted_canonical_snapshots(
    trade_date: str,
    *,
    lineage_id: str = "",
    production_run_id: str = "",
    decision_clock: Any | None = None,
    require_fresh: bool = True,
) -> List[Dict[str, Any]]:
    """Load DB-verified snapshots for one production observation.

    trade_date is not an observation identity. Production never ranks
    max(source_time). A stale same-day lineage is not current input.
    """
    ensure_production_schema()
    from xiaogu_forward_snapshot import (
        select_production_observation_snapshots,
        validate_and_build_canonical_snapshot,
    )
    wanted_lineage = str(lineage_id or "").strip()
    run_id = str(production_run_id or "").strip()
    if run_id and not wanted_lineage:
        run = fetch_production_run(run_id) or {}
        wanted_lineage = str(run.get("lineage_id") or "").strip()
        if not wanted_lineage:
            raise ValueError("CANONICAL_SNAPSHOT_NOT_FOUND")
    params: Dict[str, Any] = {"trade_date": trade_date}
    query = """
                    SELECT lineage_id, trade_date, payload, snapshot_id, source, source_time, symbol
                    FROM snapshots
                    WHERE trade_date = CAST(:trade_date AS date)
                    """
    if wanted_lineage:
        query += " AND lineage_id = :lineage_id"
        params["lineage_id"] = wanted_lineage
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(text(query), params).mappings()
        ]
    snapshots = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        payload.setdefault("lineage_id", row.get("lineage_id"))
        payload.setdefault("trade_date", str(row.get("trade_date") or trade_date))
        payload.setdefault("snapshot_id", row.get("snapshot_id") or payload.get("snapshot_id"))
        payload.setdefault("source", row.get("source") or payload.get("source"))
        payload.setdefault("source_time", row.get("source_time") or payload.get("source_time"))
        payload.setdefault("symbol", row.get("symbol") or payload.get("symbol"))
        try:
            snapshots.append(validate_and_build_canonical_snapshot(payload, target_trade_date=trade_date))
        except (TypeError, ValueError):
            continue
    if not snapshots:
        return []
    return select_production_observation_snapshots(
        snapshots,
        trade_date=trade_date,
        lineage_id=wanted_lineage,
        decision_clock=decision_clock,
        require_fresh=require_fresh,
    )


def get_current_position_review_snapshot(*, symbol: str, review_trade_date: str) -> Dict[str, Any]:
    """Resolve the exact trusted canonical snapshot for Position Review on one trade date.

    Production never selects latest-by-source_time. Zero or multiple identities fail closed.
    """
    ensure_production_schema()
    wanted = str(symbol or "").zfill(6)[-6:]
    if not wanted or wanted == "000000" or not str(review_trade_date or "").strip():
        raise RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT lineage_id, trade_date, payload, snapshot_id, source, source_time, symbol
                    FROM snapshots
                    WHERE trade_date = CAST(:trade_date AS date)
                      AND symbol = :symbol
                    """
                ),
                {"trade_date": review_trade_date, "symbol": wanted},
            ).mappings()
        ]
    snapshots = []
    identities = set()
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            continue
        payload.setdefault("lineage_id", row.get("lineage_id"))
        payload.setdefault("trade_date", str(row.get("trade_date") or review_trade_date))
        payload.setdefault("snapshot_id", row.get("snapshot_id") or payload.get("snapshot_id"))
        payload.setdefault("source", row.get("source") or payload.get("source"))
        payload.setdefault("source_time", row.get("source_time") or payload.get("source_time"))
        payload.setdefault("symbol", row.get("symbol") or payload.get("symbol"))
        try:
            snapshot = validate_and_build_canonical_snapshot(payload, target_trade_date=review_trade_date)
        except (TypeError, ValueError):
            continue
        if snapshot.get("trusted_snapshot") is not True:
            continue
        if str(snapshot.get("symbol") or "").zfill(6)[-6:] != wanted:
            continue
        if str(snapshot.get("trade_date") or "") != str(review_trade_date):
            continue
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not snapshot_id:
            continue
        snapshots.append(snapshot)
        identities.add(snapshot_id)
    if not snapshots:
        raise RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_NOT_FOUND")
    if len(identities) != 1:
        raise RuntimeError("POSITION_REVIEW_BLOCKED:CURRENT_REVIEW_SNAPSHOT_AMBIGUOUS")
    return snapshots[0]


def _payload_as_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return {}
    return payload


def _snapshot_facts_hash(payload: Any) -> str:
    from xiaogu_forward_snapshot import snapshot_payload_hash
    return snapshot_payload_hash(_payload_as_dict(payload))


def _stored_snapshot_hash(row: Any) -> str:
    stored_hash = str(row.get("payload_hash") or "")
    if stored_hash:
        return stored_hash
    return _snapshot_facts_hash(row.get("payload"))


def _snapshot_hashes_match(row: Any, incoming_hash: str) -> bool:
    stored_col = str(row.get("payload_hash") or "")
    recomputed = _snapshot_facts_hash(row.get("payload"))
    return incoming_hash == stored_col or incoming_hash == recomputed


def _snapshot_row_matches_write(
    row: Any,
    *,
    snapshot_id: str,
    payload_hash: str,
    symbol: str,
    trade_date: str,
) -> bool:
    if not row:
        return False
    stored_id = str(row.get("snapshot_id") or "")
    stored_symbol = str(row.get("symbol") or "")
    stored_date = str(row.get("trade_date") or "")[:10]
    expected_date = str(trade_date or "")[:10]
    if stored_id != snapshot_id:
        return False
    if symbol and stored_symbol != str(symbol):
        return False
    if expected_date and stored_date != expected_date:
        return False
    return _snapshot_hashes_match(row, payload_hash)


def _fetch_snapshot_row(db: Any, snapshot_id: str) -> Any:
    return db.execute(
        text(
            "SELECT snapshot_id, lineage_id, symbol, trade_date, source, source_time, "
            "payload_hash, payload FROM snapshots WHERE snapshot_id = :snapshot_id"
        ),
        {"snapshot_id": snapshot_id},
    ).mappings().first()


def _fetch_snapshot_by_lineage_symbol(db: Any, lineage_id: str, symbol: str) -> Any:
    if not lineage_id or not symbol:
        return None
    return db.execute(
        text(
            "SELECT snapshot_id, lineage_id, symbol, trade_date, source, source_time, "
            "payload_hash, payload FROM snapshots "
            "WHERE lineage_id = :lineage_id AND symbol = :symbol"
        ),
        {"lineage_id": lineage_id, "symbol": symbol},
    ).mappings().first()


def _audit_snapshot_identity_conflict(
    *,
    snapshot_id: str,
    existing_hash: str,
    incoming_hash: str,
    source: str = "",
) -> None:
    with engine.begin() as audit_db:
        audit_db.execute(
            text(
                """
                INSERT INTO snapshot_identity_conflicts
                    (snapshot_id, existing_payload_hash, incoming_payload_hash, source)
                VALUES (:snapshot_id, :existing_payload_hash, :incoming_payload_hash, :source)
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "existing_payload_hash": existing_hash,
                "incoming_payload_hash": incoming_hash,
                "source": source or "record_snapshot",
            },
        )


def find_snapshot_identity_conflicts() -> List[Dict[str, Any]]:
    """Read-only audit of snapshot identity conflicts. Never repairs rows."""
    ensure_production_schema()
    if not _table_columns("snapshot_identity_conflicts"):
        return []
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT snapshot_id,
                       existing_payload_hash AS existing_hash,
                       incoming_payload_hash AS incoming_hash,
                       MIN(detected_at) AS first_seen,
                       MAX(detected_at) AS last_seen
                FROM snapshot_identity_conflicts
                GROUP BY snapshot_id, existing_payload_hash, incoming_payload_hash
                ORDER BY MIN(detected_at)
                """
            )
        ).mappings()
        return [dict(row) for row in rows]


def record_snapshot(snapshot: Dict[str, Any]) -> str:
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    snapshot_id = str(snapshot.get("snapshot_id") or "")
    lineage_id = str(snapshot.get("lineage_id") or "")
    if not snapshot_id or not lineage_id:
        raise ValueError("SNAPSHOT_IDENTITY_REQUIRED")
    if snapshot_id == lineage_id:
        raise ValueError("SNAPSHOT_IDENTITY_REQUIRED")
    columns = _table_columns("snapshots")
    fields = ["lineage_id", "trade_date", "payload"]
    from xiaogu_forward_snapshot import snapshot_payload_hash
    computed_hash = snapshot_payload_hash(snapshot)
    stored_payload = dict(snapshot)
    stored_payload["payload_hash"] = computed_hash
    params = {
        "lineage_id": lineage_id,
        "trade_date": snapshot["trade_date"],
        "payload": json.dumps(stored_payload, ensure_ascii=False, default=str),
        "snapshot_id": snapshot_id,
        "source": snapshot.get("source"),
        "source_time": snapshot.get("source_time"),
        "symbol": snapshot.get("symbol"),
        "payload_hash": computed_hash,
    }
    for field in ("snapshot_id", "source", "source_time", "symbol", "payload_hash"):
        if field in columns:
            fields.append(field)
    placeholders = ", ".join(
        "CAST(:payload AS jsonb)" if field == "payload" else f":{field}"
        for field in fields
    )
    with get_db() as db:
        try:
            with db.begin_nested():
                db.execute(
                    text(
                        f"INSERT INTO snapshots ({', '.join(fields)}) VALUES ({placeholders})"
                    ),
                    params,
                )
        except IntegrityError:
            stored = _fetch_snapshot_row(db, snapshot_id)
            if stored is None:
                stored = _fetch_snapshot_by_lineage_symbol(
                    db, lineage_id, str(snapshot.get("symbol") or "")
                )
            if stored is None:
                raise RuntimeError(SNAPSHOT_PERSISTENCE_FAILED)
            if (
                str(stored.get("snapshot_id") or "") == snapshot_id
                and _snapshot_hashes_match(stored, computed_hash)
            ):
                if not _snapshot_row_matches_write(
                    stored,
                    snapshot_id=snapshot_id,
                    payload_hash=computed_hash,
                    symbol=str(snapshot.get("symbol") or ""),
                    trade_date=str(snapshot.get("trade_date") or ""),
                ):
                    raise RuntimeError(SNAPSHOT_PERSISTENCE_FAILED)
                return SNAPSHOT_IDEMPOTENT
            _audit_snapshot_identity_conflict(
                snapshot_id=snapshot_id,
                existing_hash=_stored_snapshot_hash(stored),
                incoming_hash=computed_hash,
                source=str(snapshot.get("source") or "record_snapshot"),
            )
            raise ValueError(SNAPSHOT_IDENTITY_CONFLICT)
        stored = _fetch_snapshot_row(db, snapshot_id)
        if not _snapshot_row_matches_write(
            stored,
            snapshot_id=snapshot_id,
            payload_hash=computed_hash,
            symbol=str(snapshot.get("symbol") or ""),
            trade_date=str(snapshot.get("trade_date") or ""),
        ):
            raise RuntimeError(SNAPSHOT_PERSISTENCE_FAILED)
        return SNAPSHOT_INSERTED


def record_decision(decision: Dict[str, Any]) -> None:
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    if not str(decision.get("decision_id") or "").strip():
        raise ValueError("DECISION_ID_REQUIRED")
    columns = _table_columns("picks")
    fields = ["trade_date", "symbol", "state", "position_state", "payload"]
    canonical = decision.get("canonical_snapshot") or {}
    identity = {
        "snapshot_id": decision.get("snapshot_id") or canonical.get("snapshot_id"),
        "lineage_id": decision.get("lineage_id") or canonical.get("lineage_id"),
        "symbol": decision.get("symbol") or canonical.get("symbol"),
        "trade_date": canonical.get("trade_date") or decision.get("trade_date") or decision.get("date"),
    }
    if any(not str(value or "").strip() for value in identity.values()):
        raise ValueError("SNAPSHOT_IDENTITY_UNAVAILABLE")
    production_run_id = str(
        decision.get("production_run_id") or canonical.get("production_run_id") or ""
    ).strip()
    decision = {
        **decision,
        **identity,
        "canonical_snapshot": canonical,
    }
    if production_run_id:
        decision["production_run_id"] = production_run_id
    params = {
        "trade_date": identity["trade_date"],
        "symbol": identity["symbol"],
        "state": decision.get("action") or decision["state"],
        "position_state": decision.get("position_state"),
        "payload": json.dumps(decision, ensure_ascii=False, default=str),
        "decision_id": decision.get("decision_id"),
    }
    if "decision_id" in columns:
        fields.append("decision_id")
    if "decision" in columns and "decision" not in fields:
        fields.append("decision")
        params["decision"] = params["state"]
    if "production_run_id" in columns and production_run_id:
        fields.append("production_run_id")
        params["production_run_id"] = production_run_id
    if "state" not in columns and "state" in fields:
        fields.remove("state")
    if "position_state" not in columns and "position_state" in fields:
        fields.remove("position_state")
    if "payload" not in columns and "payload" in fields:
        fields.remove("payload")
    with get_db() as db:
        db.execute(
            text(
                f"INSERT INTO picks ({', '.join(fields)}) VALUES ("
                + ", ".join("CAST(:payload AS jsonb)" if field == "payload" else f":{field}" for field in fields)
                + ") ON CONFLICT (decision_id) DO NOTHING"
            ),
            params,
        )
    upsert_position(decision)


def record_snapshot_and_decision(snapshot: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """Persist one snapshot and its decision in the same PostgreSQL transaction."""
    if _ACTIVE_DB_CONNECTION.get() is not None:
        record_snapshot(snapshot)
        record_decision(decision)
        return
    ensure_production_schema()
    with engine.begin() as db:
        token = _ACTIVE_DB_CONNECTION.set(db)
        try:
            record_snapshot(snapshot)
            record_decision(decision)
        finally:
            _ACTIVE_DB_CONNECTION.reset(token)


def persist_production_facts(
    decisions: list[Dict[str, Any]],
    *,
    production_run_id: str = "",
    coverage: Dict[str, Any] | None = None,
) -> None:
    """Write production decisions and paper observations in one transaction."""
    ensure_production_schema()
    with engine.begin() as db:
        token = _ACTIVE_DB_CONNECTION.set(db)
        try:
            run_id = str(production_run_id or "").strip()
            if run_id and _incoming_official_observation(decisions):
                _assert_one_official_production_observation(run_id, decisions)
            for decision in decisions:
                canonical = decision.get("canonical_snapshot")
                observation = decision.get("paper_observation")
                if not isinstance(canonical, dict):
                    continue
                if run_id:
                    decision["production_run_id"] = decision.get("production_run_id") or run_id
                if decision.get("state") in {"BUY", "HOLD", "REDUCE", "SELL"} or isinstance(observation, dict):
                    record_snapshot(canonical)
                    record_decision(decision)
                if isinstance(observation, dict):
                    if run_id and not observation.get("production_run_id"):
                        observation = {**observation, "production_run_id": run_id}
                        decision["paper_observation"] = observation
                    paper_signal_id = str(observation.get("paper_signal_id") or "")
                    if paper_signal_id and not paper_observation_exists(paper_signal_id):
                        record_paper_observation({
                            **observation,
                            "canonical_snapshot": canonical,
                            "trade_date": canonical.get("trade_date") or observation.get("trade_date"),
                            "production_run_id": observation.get("production_run_id") or run_id,
                        })
            if run_id and "production_run_id" in _table_columns("production_runs"):
                _write_production_run_coverage(
                    db,
                    run_id,
                    coverage,
                    status="DECISIONS_PERSISTED",
                )
        finally:
            _ACTIVE_DB_CONNECTION.reset(token)


def mark_production_run_status(production_run_id: str, status: str) -> None:
    run_id = str(production_run_id or "").strip()
    if not run_id:
        return
    with engine.begin() as db:
        if "production_run_id" not in _table_columns("production_runs"):
            return
        db.execute(
            text(
                "UPDATE production_runs SET status = :status, updated_at = NOW() "
                "WHERE production_run_id = :run_id"
            ),
            {"status": status, "run_id": run_id},
        )


def _observation_coverage_payload(coverage: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(coverage, dict):
        return None
    return {
        "observation_coverage": coverage,
        "observation_layer": True,
        "influences_selection": False,
        "influences_alpha": False,
        "influences_buy": False,
    }


def _write_production_run_coverage(
    db: Any,
    run_id: str,
    coverage: Dict[str, Any] | None,
    *,
    status: str | None = None,
) -> None:
    columns = _table_columns("production_runs")
    if "production_run_id" not in columns:
        return
    payload = _observation_coverage_payload(coverage)
    assignments = ["updated_at = NOW()"]
    params: Dict[str, Any] = {"run_id": run_id}
    if status:
        assignments.append("status = :status")
        params["status"] = status
    if payload is not None and "scoring_config_snapshot" in columns:
        existing = db.execute(
            text(
                "SELECT scoring_config_snapshot FROM production_runs "
                "WHERE production_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).scalar()
        merged = {**_json_payload(existing), **payload}
        assignments.append("scoring_config_snapshot = CAST(:coverage AS jsonb)")
        params["coverage"] = json.dumps(merged, ensure_ascii=False, default=str)
    db.execute(
        text(f"UPDATE production_runs SET {', '.join(assignments)} WHERE production_run_id = :run_id"),
        params,
    )


def record_production_run_coverage(production_run_id: str, coverage: Dict[str, Any]) -> None:
    """Persist observation-funnel counts on an existing production_runs row. Not a second fact table."""
    run_id = str(production_run_id or "").strip()
    if not run_id:
        return
    ensure_production_schema()
    with engine.begin() as db:
        _write_production_run_coverage(db, run_id, coverage)


def record_returns(trade_date: str, symbol: str, payload: Dict[str, Any], decision_id: str = "") -> None:
    ensure_production_schema()
    decision_id = decision_id or payload.get("decision_id") or payload.get("id") or ""
    if not str(decision_id).strip():
        raise ValueError("DECISION_ID_REQUIRED")
    columns = _table_columns("returns")
    calendar = _calendar_metadata(trade_date)
    payload = {**payload, **calendar}
    fields = ["trade_date", "symbol", "payload"]
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    params = {
        "trade_date": trade_date,
        "symbol": symbol,
        "payload": serialized_payload,
        "decision_id": decision_id,
        **calendar,
    }
    if "decision_id" in columns:
        fields.append("decision_id")
    for field in ("calendar_version", "calendar_content_hash"):
        if field in columns:
            fields.append(field)
    conflict_clause = (
        " ON CONFLICT (decision_id, trade_date) WHERE decision_id IS NOT NULL DO NOTHING"
        if "decision_id" in columns else ""
    )
    with get_db() as db:
        decision = db.execute(
            text("SELECT 1 FROM picks WHERE decision_id = :decision_id"),
            {"decision_id": decision_id},
        ).first()
        if not decision:
            raise ValueError("DECISION_ID_NOT_FOUND")
        if "decision_id" in columns:
            existing = db.execute(
                text(
                    "SELECT payload FROM returns "
                    "WHERE decision_id = :decision_id "
                    "AND trade_date = CAST(:trade_date AS date) LIMIT 1"
                ),
                {"decision_id": decision_id, "trade_date": trade_date},
            ).mappings().first()
            if existing:
                stored = existing.get("payload")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                stored_payload = json.dumps(stored, ensure_ascii=False, sort_keys=True, default=str)
                if stored_payload == serialized_payload:
                    return
                raise ValueError("OUTCOME_IDENTITY_CONFLICT")
        db.execute(
            text(
                f"INSERT INTO returns ({', '.join(fields)}) VALUES ("
                + ", ".join("CAST(:payload AS jsonb)" if field == "payload" else f":{field}" for field in fields)
                + ")" + conflict_clause
            ),
            params,
        )


def record_canonical_historical_snapshot(snapshot: Dict[str, Any]) -> None:
    """Persist one immutable PIT historical snapshot when PostgreSQL is enabled."""
    record_canonical_historical_snapshots([snapshot])


def record_canonical_historical_snapshots(snapshots: Iterable[Dict[str, Any]]) -> None:
    """Persist immutable PIT snapshots in one idempotent transaction."""
    ensure_production_schema()
    rows = []
    for snapshot in snapshots:
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        lineage_id = str(snapshot.get("lineage_id") or "")
        if not snapshot_id or not lineage_id:
            raise ValueError("SNAPSHOT_IDENTITY_REQUIRED")
        if snapshot_id == lineage_id:
            raise ValueError("SNAPSHOT_IDENTITY_REQUIRED")
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "lineage_id": lineage_id,
                "symbol": snapshot["symbol"],
                "trade_date": snapshot["trade_date"],
                "signal_time": snapshot["signal_time"],
                "source": snapshot["source"],
                "source_timestamp": snapshot["source_timestamp"],
                "snapshot_version": snapshot["snapshot_version"],
                "point_in_time": snapshot["point_in_time"],
                "available_at": snapshot["available_at"],
                "price_basis": snapshot["price_basis"],
                "payload": json.dumps(snapshot, ensure_ascii=False, default=str),
            }
        )
    if not rows:
        return
    with get_db() as db:
        for row in rows:
            incoming_hash = snapshot_payload_identity(json.loads(row["payload"]))
            try:
                with db.begin_nested():
                    db.execute(
                        text(
                            """
                            INSERT INTO canonical_historical_snapshots
                                (snapshot_id, lineage_id, symbol, trade_date, signal_time, source,
                                 source_timestamp, snapshot_version, point_in_time,
                                 available_at, price_basis, payload)
                            VALUES (:snapshot_id, :lineage_id, :symbol, :trade_date, CAST(:signal_time AS timestamptz),
                                    :source, CAST(:source_timestamp AS timestamptz), :snapshot_version,
                                    :point_in_time, CAST(:available_at AS timestamptz), :price_basis,
                                    CAST(:payload AS jsonb))
                            """
                        ),
                        row,
                    )
            except IntegrityError:
                existing = db.execute(
                    text(
                        "SELECT snapshot_id, payload FROM canonical_historical_snapshots "
                        "WHERE snapshot_id = :snapshot_id"
                    ),
                    {"snapshot_id": row["snapshot_id"]},
                ).mappings().first()
                if existing is None:
                    raise RuntimeError(SNAPSHOT_PERSISTENCE_FAILED)
                stored_hash = snapshot_payload_identity(existing.get("payload"))
                if stored_hash == incoming_hash:
                    continue
                _audit_snapshot_identity_conflict(
                    snapshot_id=row["snapshot_id"],
                    existing_hash=stored_hash,
                    incoming_hash=incoming_hash,
                    source=str(row.get("source") or "canonical_historical_snapshots"),
                )
                raise ValueError(SNAPSHOT_IDENTITY_CONFLICT)


def record_canonical_future_prices(bars: Iterable[Dict[str, Any]]) -> None:
    """Persist immutable OHLC facts; conflicts are production-data failures."""
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    with get_db() as db:
        for bar in bars:
            fact = canonical_future_price_fact(bar)
            existing = db.execute(
                text(
                    "SELECT price_fact_hash FROM canonical_future_prices "
                    "WHERE symbol = :symbol AND date = CAST(:date AS date)"
                ),
                fact,
            ).mappings().first()
            if existing:
                if str(existing["price_fact_hash"] or "") != fact["price_fact_hash"]:
                    raise ValueError("PRICE_FACT_CONFLICT")
                continue
            result = db.execute(
                text(
                    """
                    INSERT INTO canonical_future_prices
                        (symbol, date, open, high, low, close, volume, amount,
                         source, source_timestamp, price_basis, price_fact_hash, payload)
                    VALUES (:symbol, :date, :open, :high, :low, :close, :volume,
                            :amount, :source, CAST(NULLIF(:source_timestamp, '') AS timestamptz),
                            :price_basis, :price_fact_hash, CAST(:payload AS jsonb))
                    ON CONFLICT (symbol, date) DO NOTHING
                    """
                ),
                {**fact, "payload": json.dumps({**bar, **fact}, ensure_ascii=False, default=str)},
            )
            if result.rowcount == 0:
                concurrent = db.execute(
                    text(
                        "SELECT price_fact_hash FROM canonical_future_prices "
                        "WHERE symbol = :symbol AND date = CAST(:date AS date)"
                    ),
                    fact,
                ).mappings().first()
                if not concurrent or str(concurrent["price_fact_hash"] or "") != fact["price_fact_hash"]:
                    raise ValueError("PRICE_FACT_CONFLICT")


def _jsonb_text(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def insert_scan_session(**payload: Any) -> str:
    """Persist scan-session metadata and a production run identity.

    production_run_id is run_id. lineage_id stays on the run as linkage only;
    snapshots remain the canonical snapshot store.
    """
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    trade_date = str(payload.get("trade_date") or "").strip()
    lineage_id = str(payload.get("lineage_id") or "").strip()
    if not trade_date:
        raise ValueError(f"{PRODUCTION_SCAN_BLOCKED}:TRADE_DATE_REQUIRED")
    if not lineage_id:
        raise ValueError(f"{PRODUCTION_SCAN_BLOCKED}:LINEAGE_ID_REQUIRED")
    run_columns = _table_columns("production_runs")
    required = ("production_run_id", "trade_date", "status", "run_mode", "lineage_id")
    missing = [column for column in required if column not in run_columns]
    if missing:
        raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:PRODUCTION_RUNS_SCHEMA_MISSING:{','.join(missing)}")
    run_id = str(uuid.uuid4())
    if run_id == lineage_id:
        raise ValueError(f"{PRODUCTION_SCAN_BLOCKED}:RUN_ID_LINEAGE_COLLISION")
    scan_time = str(payload.get("scan_time") or "").strip()
    session_id = None
    session_columns = _table_columns("scan_sessions")
    try:
        with get_db() as db:
            if session_columns:
                session_id = _insert_or_reuse_scan_session(db, payload, session_columns, scan_time)
            existing_run = db.execute(
                text(
                    """
                    SELECT production_run_id FROM production_runs
                    WHERE lineage_id = :lineage_id
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                {"lineage_id": lineage_id},
            ).fetchone()
            if existing_run and str(existing_run[0] or "").strip():
                return str(existing_run[0])
            row = db.execute(
                text(
                    """
                    INSERT INTO production_runs (
                        production_run_id, trade_date, scan_session_id, run_mode, status,
                        lineage_id, scanner_version, schema_version, runner_version,
                        input_payload_hash, started_at, updated_at
                    ) VALUES (
                        :production_run_id, CAST(:trade_date AS date), :scan_session_id, :run_mode, :status,
                        :lineage_id, :scanner_version, :schema_version, :runner_version,
                        :input_payload_hash, CAST(NULLIF(:started_at, '') AS timestamptz), NOW()
                    )
                    RETURNING production_run_id
                    """
                ),
                {
                    "production_run_id": run_id,
                    "trade_date": trade_date,
                    "scan_session_id": session_id,
                    "run_mode": str(payload.get("run_mode") or "PRODUCTION"),
                    "status": "SNAPSHOT_CAPTURED",
                    "lineage_id": lineage_id,
                    "scanner_version": str(payload.get("scanner_version") or "scrapy_scanner/runner_v2.py"),
                    "schema_version": SCHEMA_VERSION,
                    "runner_version": str(payload.get("runner_version") or ""),
                    "input_payload_hash": hashlib.sha256(
                        json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "started_at": scan_time,
                },
            ).fetchone()
            persisted_run_id = str(row[0]) if row else ""
            if not persisted_run_id:
                raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:RUN_ID_MISSING")
            if session_id is not None and "production_run_id" in session_columns:
                db.execute(
                    text(
                        "UPDATE scan_sessions SET production_run_id = :run_id, updated_at = NOW() "
                        "WHERE id = :session_id"
                    ),
                    {"run_id": persisted_run_id, "session_id": session_id},
                )
    except ValueError:
        raise
    except SQLAlchemyError as exc:
        raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:{type(exc).__name__}") from exc
    return persisted_run_id


def _insert_or_reuse_scan_session(
    db: Any, payload: Dict[str, Any], session_columns: set[str], scan_time: str,
) -> int | None:
    required = ("trade_date", "scan_time")
    if any(column not in session_columns for column in required):
        raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:SCAN_SESSIONS_SCHEMA_MISSING")
    trade_date = str(payload.get("trade_date") or "").strip()
    scan_dir = str(payload.get("scan_dir") or "").strip()
    quotes_count = payload.get("quotes_count")
    scored_count = payload.get("scored_count")
    if scored_count is None:
        scored_count = payload.get("captured_count")
    params = {
        "trade_date": trade_date,
        "scan_time": scan_time or None,
        "source_id": str(payload.get("source_id") or "eastmoney_api_scan_v2"),
        "quotes_count": 0 if quotes_count is None else quotes_count,
        "scored_count": 0 if scored_count is None else scored_count,
        "passed_count": 0 if payload.get("passed_count") is None else payload.get("passed_count"),
        "scan_dir": scan_dir or None,
        "market_snapshot": _jsonb_text(payload.get("market_snapshot")),
        "source_status": _jsonb_text(payload.get("source_status")),
        "source_counts": _jsonb_text(payload.get("source_counts")),
        "source_diagnostics": _jsonb_text(payload.get("source_diagnostics")),
    }
    existing = None
    if scan_dir and "scan_dir" in session_columns:
        existing = db.execute(
            text(
                """
                SELECT id FROM scan_sessions
                WHERE trade_date = CAST(:trade_date AS date) AND scan_dir = :scan_dir
                ORDER BY scan_time DESC, id DESC
                LIMIT 1
                """
            ),
            {"trade_date": trade_date, "scan_dir": scan_dir},
        ).fetchone()
    if existing:
        db.execute(
            text(
                """
                UPDATE scan_sessions
                SET scan_time = COALESCE(CAST(NULLIF(:scan_time, '') AS timestamptz), scan_time),
                    source_id = COALESCE(:source_id, source_id),
                    quotes_count = :quotes_count,
                    scored_count = :scored_count,
                    passed_count = :passed_count,
                    market_snapshot = CAST(:market_snapshot AS jsonb),
                    source_status = CAST(:source_status AS jsonb),
                    source_counts = CAST(:source_counts AS jsonb),
                    source_diagnostics = CAST(:source_diagnostics AS jsonb),
                    status = 'completed',
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {**params, "id": existing[0], "scan_time": scan_time},
        )
        return int(existing[0])
    row = db.execute(
        text(
            """
            INSERT INTO scan_sessions (
                trade_date, scan_time, source_id, quotes_count, scored_count, passed_count,
                scan_dir, market_snapshot, source_status, source_counts, source_diagnostics,
                status
            ) VALUES (
                CAST(:trade_date AS date),
                COALESCE(CAST(NULLIF(:scan_time, '') AS timestamptz), NOW()),
                :source_id, :quotes_count, :scored_count, :passed_count, :scan_dir,
                CAST(:market_snapshot AS jsonb), CAST(:source_status AS jsonb),
                CAST(:source_counts AS jsonb), CAST(:source_diagnostics AS jsonb),
                'completed'
            )
            RETURNING id
            """
        ),
        params,
    ).fetchone()
    if row is None:
        raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:SCAN_SESSION_ID_MISSING")
    return int(row[0])


def persist_scan_capture(
    *,
    snapshots: Iterable[Dict[str, Any]] | None = None,
    **session: Any,
) -> Dict[str, Any]:
    """Persist one production scan, its run identity, and canonical snapshots atomically.

    Raw scanner domain rows stay out of production_runs. Snapshots remain the
    canonical snapshot store; production_runs is scan-session metadata only.
    """
    ensure_production_schema()
    with engine.begin() as db:
        token = _ACTIVE_DB_CONNECTION.set(db)
        try:
            run_id = insert_scan_session(**session)
            run = fetch_production_run(run_id) or {}
            snapshot_count = 0
            for snapshot in snapshots or ():
                record_snapshot(snapshot)
                snapshot_count += 1
            if snapshot_count == 0:
                raise RuntimeError(f"{PRODUCTION_SCAN_BLOCKED}:CANONICAL_SNAPSHOT_NOT_FOUND")
            return {
                "status": "PASS",
                "run_id": run_id,
                "lineage_id": session.get("lineage_id"),
                "scan_session_id": run.get("scan_session_id"),
                "snapshot_count": snapshot_count,
            }
        finally:
            _ACTIVE_DB_CONNECTION.reset(token)


def fetch_production_run(run_id: str) -> Dict[str, Any] | None:
    if not str(run_id or "").strip():
        return None
    with get_db() as db:
        row = db.execute(
            text("SELECT * FROM production_runs WHERE production_run_id = :run_id"),
            {"run_id": run_id},
        ).mappings().first()
    return dict(row) if row else None


def _json_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"true", "1"}:
        return True
    if str(value).strip().lower() in {"false", "0"}:
        return False
    return None


def _coverage_from_run_row(row: Dict[str, Any] | None) -> Dict[str, Any]:
    row = row or {}
    snapshot = _json_payload(row.get("scoring_config_snapshot"))
    coverage = snapshot.get("observation_coverage")
    coverage = dict(coverage) if isinstance(coverage, dict) else {}
    run_id = str(row.get("production_run_id") or "")
    return {
        "production_run_id": run_id or None,
        "trade_date": str(row.get("trade_date") or coverage.get("trade_date") or "") or None,
        "lineage_id": row.get("lineage_id") or coverage.get("lineage_id"),
        "status": row.get("status"),
        "run_mode": row.get("run_mode"),
        "scan_count": _int_or_none(coverage.get("scan_count")),
        "execution_universe_count": _int_or_none(coverage.get("execution_universe_count")),
        "research_count": _int_or_none(coverage.get("research_count")),
        "alpha_count": _int_or_none(coverage.get("alpha_count")),
        "decision_count": _int_or_none(coverage.get("decision_count")),
        "top3_count": _int_or_none(coverage.get("top3_count")),
        "top1_count": _int_or_none(coverage.get("top1_count")),
        "paper_count": _int_or_none(coverage.get("paper_count")),
        "system_fault": _bool_or_none(coverage.get("system_fault")),
        "publishable": _bool_or_none(coverage.get("publishable")),
        "selection_status": coverage.get("selection_status"),
        "influences_selection": False,
        "source": "production_runs.scoring_config_snapshot.observation_coverage",
    }


def fetch_production_run_coverage(production_run_id: str) -> Dict[str, Any]:
    """Read observation-funnel counts for one production_run. Query-only."""
    run = fetch_production_run(production_run_id)
    if not run:
        return {
            "production_run_id": str(production_run_id or "") or None,
            "status": "MISSING",
            "scan_count": None,
            "execution_universe_count": None,
            "research_count": None,
            "alpha_count": None,
            "decision_count": None,
            "top3_count": None,
            "top1_count": None,
            "paper_count": None,
            "system_fault": None,
            "publishable": None,
            "influences_selection": False,
        }
    return _coverage_from_run_row(run)


OFFICIAL_PRODUCTION_ALPHA = "profit_window_alpha_5d_v4"
OFFICIAL_PRODUCTION_TARGET = "opportunity_5d"
OFFICIAL_PRODUCTION_RUN_STATUS = "DECISIONS_PERSISTED"


def _trade_date_from_run_or_decisions(
    production_run_id: str,
    decisions: Iterable[Dict[str, Any]] | None = None,
) -> str:
    run = fetch_production_run(production_run_id) or {}
    value = str(run.get("trade_date") or "")[:10]
    if value:
        return value
    for decision in decisions or ():
        value = str(decision.get("trade_date") or "")[:10]
        if value:
            return value
        canonical = decision.get("canonical_snapshot") or {}
        if isinstance(canonical, dict):
            value = str(canonical.get("trade_date") or "")[:10]
            if value:
                return value
    return ""


def fetch_official_production_run_id(trade_date: str) -> str | None:
    """Return the unique official production_run_id for one trade_date, if any.

    Official identity is DECISIONS_PERSISTED plus official Top1/Top3 provenance.
    SNAPSHOT_CAPTURED, SCAN_BLOCKED, and STALE_DATA attempts are not official.
    """
    wanted = str(trade_date or "")[:10]
    if not wanted:
        return None
    run_ids = sorted({
        str(row.get("production_run_id") or "").strip()
        for row in fetch_official_paper_observations()
        if str(row.get("trade_date") or "")[:10] == wanted
    })
    run_ids = [item for item in run_ids if item]
    if not run_ids:
        return None
    if len(run_ids) > 1:
        raise RuntimeError(f"{OFFICIAL_PRODUCTION_OBSERVATION_AMBIGUOUS}:{wanted}")
    return run_ids[0]


def _incoming_official_observation(decisions: Iterable[Dict[str, Any]] | None) -> bool:
    """True when this persist would mint an official Top1/Top3 paper observation."""
    for decision in decisions or ():
        observation = decision.get("paper_observation") if isinstance(decision, dict) else None
        if isinstance(observation, dict) and has_official_observation_provenance(observation):
            return True
    return False


def _assert_one_official_production_observation(
    production_run_id: str,
    decisions: Iterable[Dict[str, Any]] | None = None,
) -> None:
    run_id = str(production_run_id or "").strip()
    trade_date = _trade_date_from_run_or_decisions(run_id, decisions)
    if not run_id or not trade_date:
        return
    existing = fetch_official_production_run_id(trade_date)
    if existing and existing != run_id:
        raise RuntimeError(f"{OFFICIAL_PRODUCTION_OBSERVATION_EXISTS}:{trade_date}:{existing}")


def _official_observation_rank(observation: Dict[str, Any]) -> int | None:
    rank = observation.get("rank")
    try:
        return int(rank) if rank is not None and rank != "" else None
    except (TypeError, ValueError):
        return None


def has_official_observation_provenance(
    observation: Dict[str, Any],
    *,
    require_persisted_run: bool = False,
    run_status: str | None = None,
) -> bool:
    """Official forward observation identity. Rank alone is not enough."""
    if not isinstance(observation, dict):
        return False
    paper_signal_id = str(observation.get("paper_signal_id") or "").strip()
    decision_id = str(observation.get("decision_id") or "").strip()
    production_run_id = str(observation.get("production_run_id") or "").strip()
    snapshot_id = str(observation.get("snapshot_id") or "").strip()
    lineage_id = str(observation.get("lineage_id") or "").strip()
    if not paper_signal_id or not decision_id or paper_signal_id == decision_id:
        return False
    if not production_run_id or not snapshot_id or not lineage_id:
        return False
    alpha = str(observation.get("production_alpha") or observation.get("model_id") or "").strip()
    target = str(observation.get("production_target") or observation.get("target") or "").strip()
    if alpha != OFFICIAL_PRODUCTION_ALPHA or target != OFFICIAL_PRODUCTION_TARGET:
        return False
    rank = _official_observation_rank(observation)
    if not (observation.get("top1_flag") is True or observation.get("top3_flag") is True or rank in {1, 2, 3}):
        return False
    if observation.get("paper_only") is False or observation.get("live_order") is True:
        return False
    if require_persisted_run and (run_status or "") != OFFICIAL_PRODUCTION_RUN_STATUS:
        return False
    return True


def fetch_official_paper_observations() -> List[Dict[str, Any]]:
    """Official Top1/Top3 paper observations with production provenance only."""
    candidates = []
    run_ids: set[str] = set()
    for row in fetch_paper_observations():
        payload = _json_payload(row.get("payload"))
        merged = {**payload, **{key: value for key, value in row.items() if key != "payload"}}
        if not has_official_observation_provenance(merged):
            continue
        run_ids.add(str(merged.get("production_run_id") or "").strip())
        candidates.append(merged)
    run_status: Dict[str, str] = {}
    for run_id in run_ids:
        run = fetch_production_run(run_id) or {}
        run_status[run_id] = str(run.get("status") or "")
    rows = []
    for merged in candidates:
        run_id = str(merged.get("production_run_id") or "").strip()
        if has_official_observation_provenance(
            merged,
            require_persisted_run=True,
            run_status=run_status.get(run_id),
        ):
            rows.append(merged)
    return rows


def fetch_paper_observation_ledger(paper_signal_id: str) -> Dict[str, Any]:
    """Join one paper_signal_id to decision identity and T+1..T+5 facts."""
    wanted = str(paper_signal_id or "").strip()
    if not wanted:
        raise ValueError("PAPER_SIGNAL_ID_REQUIRED")
    ensure_production_schema()
    with engine.connect() as db:
        row = db.execute(
            text(
                "SELECT * FROM paper_observations WHERE paper_signal_id = :paper_signal_id"
            ),
            {"paper_signal_id": wanted},
        ).mappings().first()
    if not row:
        return {"paper_signal_id": wanted, "status": "MISSING"}
    observation = {**_json_payload(row.get("payload")), **{key: value for key, value in dict(row).items() if key != "payload"}}
    decision_id = str(observation.get("decision_id") or "")
    horizons = fetch_horizon_outcomes(decision_id) if decision_id else {
        "decision_id": "",
        "outcome_id": "",
        "days": {
            str(day): {"status": "MISSING", "horizon": day, "horizon_outcome_id": f":{day}"}
            for day in (1, 2, 3, 4, 5)
        },
    }
    days = horizons.get("days") if isinstance(horizons.get("days"), dict) else {}
    settled = all(str((days.get(str(day)) or {}).get("status") or "") == "SETTLED" for day in (1, 2, 3, 4, 5))
    hit = horizons.get("opportunity_5d")
    return {
        "paper_signal_id": wanted,
        "decision_id": decision_id or None,
        "snapshot_id": observation.get("snapshot_id"),
        "lineage_id": observation.get("lineage_id"),
        "production_run_id": observation.get("production_run_id"),
        "trade_date": str(observation.get("trade_date") or str(observation.get("signal_time") or "")[:10] or "") or None,
        "symbol": observation.get("symbol"),
        "alpha_id": observation.get("production_alpha") or observation.get("alpha_version") or "profit_window_alpha_5d_v4",
        "model_id": observation.get("production_alpha") or "profit_window_alpha_5d_v4",
        "selection_score": observation.get("selection_score") or observation.get("alpha_score"),
        "target": observation.get("production_target") or "opportunity_5d",
        "rank": observation.get("rank"),
        "top1_flag": bool(observation.get("top1_flag")),
        "top3_flag": bool(observation.get("top3_flag")),
        "decision_clock": observation.get("decision_clock"),
        "knowledge_available_at": observation.get("knowledge_available_at"),
        "paper_state": observation.get("paper_observation_state"),
        "T+1": days.get("1") or {"status": "MISSING", "horizon": 1},
        "T+2": days.get("2") or {"status": "MISSING", "horizon": 2},
        "T+3": days.get("3") or {"status": "MISSING", "horizon": 3},
        "T+4": days.get("4") or {"status": "MISSING", "horizon": 4},
        "T+5": days.get("5") or {"status": "MISSING", "horizon": 5},
        "outcome_status": horizons.get("status") or ("SETTLED" if settled else "MISSING"),
        "outcome_settled_at": horizons.get("settled_at"),
        "hit": hit,
        "MAE": horizons.get("mae") if horizons.get("mae") is not None else (
            (days.get("5") or {}).get("mae") if isinstance(days.get("5"), dict) else None
        ),
        "MFE": horizons.get("mfe") if horizons.get("mfe") is not None else (
            (days.get("5") or {}).get("mfe") if isinstance(days.get("5"), dict) else None
        ),
        "realized_return": horizons.get("realized_return") if horizons.get("realized_return") is not None else (
            (days.get("5") or {}).get("net_return") if isinstance(days.get("5"), dict) else None
        ),
        "market_baseline": None,
        "regime": observation.get("regime"),
        "influences_selection": False,
    }


def upsert_scan_market_data(scan_session_id: int, trade_date: Any, scan_time: Any, results: Dict[str, Any], diagnostics: Dict[str, Any]) -> int:
    payload = {
        "scan_session_id": scan_session_id, "trade_date": str(trade_date),
        "scan_time": str(scan_time), "results": results, "diagnostics": diagnostics,
    }
    with get_db() as db:
        db.execute(
            text("INSERT INTO ledger (payload) VALUES (CAST(:payload AS jsonb))"),
            {"payload": json.dumps(payload, ensure_ascii=False, default=str)},
        )
    return len(results)


def fetch_picks() -> List[Dict[str, Any]]:
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text("SELECT * FROM picks ORDER BY id DESC")).mappings()]


def record_paper_observation(observation: Dict[str, Any]) -> None:
    """Persist one observation wrapper; it is not a production decision."""
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    required = (
        "paper_signal_id", "decision_id", "snapshot_id", "lineage_id", "symbol",
        "signal_time", "reference_price", "paper_observation_state",
        "paper_position_state", "alpha_name", "alpha_version", "feature_version", "decision_version",
        "cost_model_version", "paper_observation_contract_version",
    )
    if any(str(observation.get(field) or "").strip() == "" for field in required):
        raise ValueError("PAPER_OBSERVATION_IDENTITY_REQUIRED")
    if observation.get("paper_observation_state") != "OBSERVED":
        raise ValueError("PAPER_OBSERVATION_STATE_INVALID")
    if observation.get("paper_position_state") != "PAPER_FLAT":
        raise ValueError("PAPER_ENTRY_OWNER_UNAVAILABLE")
    if observation.get("paper_only") is not True or observation.get("live_order") is not False:
        raise ValueError("PAPER_OBSERVATION_LIVE_EXECUTION_DISABLED")
    calendar = _calendar_metadata(observation.get("trade_date") or str(observation["signal_time"])[:10])
    observation_payload = {**observation, **calendar}
    params = {
        **observation_payload,
        "payload": json.dumps(observation_payload, ensure_ascii=False, default=str),
    }
    with get_db() as db:
        decision = db.execute(
            text("SELECT 1 FROM picks WHERE decision_id = :decision_id"),
            {"decision_id": observation["decision_id"]},
        ).first()
        if not decision:
            raise ValueError("DECISION_ID_NOT_FOUND")
        existing = db.execute(
            text(
                "SELECT payload FROM paper_observations "
                "WHERE paper_signal_id = :paper_signal_id OR decision_id = :decision_id"
            ),
            params,
        ).mappings().first()
        if existing:
            stored = existing.get("payload")
            if isinstance(stored, str):
                stored = json.loads(stored)
            identity_fields = (
                "paper_signal_id", "decision_id", "snapshot_id", "lineage_id", "symbol",
                "signal_time", "reference_price", "paper_observation_state",
                "paper_position_state", "alpha_name", "alpha_version",
                "feature_version", "decision_version", "cost_model_version",
                "paper_observation_contract_version", "paper_only", "live_order",
            )
            if any((stored or {}).get(field) != observation.get(field) for field in identity_fields):
                raise ValueError("PAPER_OBSERVATION_IDENTITY_CONFLICT")
            return
        db.execute(
            text(
                """
                INSERT INTO paper_observations
                    (paper_signal_id, decision_id, snapshot_id, lineage_id, symbol,
                     signal_time, reference_price, paper_observation_state,
                     paper_position_state, alpha_name, alpha_version,
                     feature_version, decision_version, cost_model_version,
                     paper_observation_contract_version, paper_only, live_order,
                     calendar_version, calendar_content_hash, payload)
                VALUES (:paper_signal_id, :decision_id, :snapshot_id, :lineage_id, :symbol,
                        CAST(:signal_time AS timestamptz), :reference_price,
                        :paper_observation_state, :paper_position_state, :alpha_name,
                        :alpha_version, :feature_version, :decision_version,
                        :cost_model_version, :paper_observation_contract_version,
                        :paper_only, :live_order, :calendar_version,
                        :calendar_content_hash, CAST(:payload AS jsonb))
                """
            ),
            params,
        )


def fetch_paper_observations() -> List[Dict[str, Any]]:
    """Read paper observations from PostgreSQL only."""
    with engine.connect() as db:
        return [dict(row) for row in db.execute(
            text("SELECT * FROM paper_observations ORDER BY signal_time DESC, paper_signal_id")
        ).mappings()]


def fetch_decision_snapshot(decision_id: str) -> Dict[str, Any]:
    """Return the exact canonical snapshot immutable-bound to one decision."""
    wanted = str(decision_id or "").strip()
    if not wanted:
        raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
    with engine.connect() as db:
        picks = list(db.execute(
            text("SELECT payload FROM picks WHERE decision_id = :decision_id"),
            {"decision_id": wanted},
        ).mappings())
        if not picks:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
        if len(picks) > 1:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:DECISION_IDENTITY_CONFLICT")
        pick = picks[0]
        payload = pick.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = payload if isinstance(payload, dict) else {}
        canonical = payload.get("canonical_snapshot") if isinstance(payload.get("canonical_snapshot"), dict) else {}
        identity = {
            "snapshot_id": payload.get("snapshot_id") or canonical.get("snapshot_id"),
            "lineage_id": payload.get("lineage_id") or canonical.get("lineage_id"),
            "symbol": payload.get("symbol") or canonical.get("symbol"),
            "trade_date": payload.get("trade_date") or canonical.get("trade_date"),
        }
        if any(not str(value or "").strip() for value in identity.values()):
            raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
    snapshot = get_snapshot_by_id(str(identity["snapshot_id"]))
    if any(
        str(snapshot.get(field) or "") != str(expected)
        for field, expected in identity.items()
    ):
        raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_CONFLICT")
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    return validate_and_build_canonical_snapshot(snapshot)


def get_snapshot_by_id(snapshot_id: str) -> Dict[str, Any]:
    """Resolve exactly one immutable canonical snapshot by its own identity."""
    wanted = str(snapshot_id or "").strip()
    if not wanted:
        raise RuntimeError("SNAPSHOT_NOT_FOUND")
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT snapshot_id, lineage_id, symbol, trade_date, source, source_time, payload
                    FROM snapshots
                    WHERE snapshot_id = :snapshot_id
                    LIMIT 2
                    """
                ),
                {"snapshot_id": wanted},
            ).mappings()
        ]
    if not rows:
        raise RuntimeError("SNAPSHOT_NOT_FOUND")
    if len(rows) > 1:
        raise RuntimeError("SNAPSHOT_IDENTITY_CONFLICT")
    row = rows[0]
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    snapshot = dict(payload) if isinstance(payload, dict) else {}
    for field in ("snapshot_id", "lineage_id", "symbol", "trade_date", "source", "source_time"):
        snapshot.setdefault(field, row.get(field))
    if str(snapshot.get("snapshot_id") or "") != wanted:
        raise RuntimeError("SNAPSHOT_IDENTITY_CONFLICT")
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    return validate_and_build_canonical_snapshot(snapshot)


def paper_observation_exists(paper_signal_id: str) -> bool:
    wanted = str(paper_signal_id or "").strip()
    if not wanted:
        return False
    if _ACTIVE_DB_CONNECTION.get() is None:
        ensure_production_schema()
    with get_db() as db:
        return bool(db.execute(
            text("SELECT 1 FROM paper_observations WHERE paper_signal_id = :paper_signal_id"),
            {"paper_signal_id": wanted},
        ).scalar())


def update_paper_observation_state(
    paper_signal_id: str,
    *,
    state: str,
    paper_position_state: str,
    exit_reason: str = "",
) -> None:
    """Persist a paper lifecycle transition without changing observation identity."""
    if state not in {"OBSERVED", "CLOSED"}:
        raise ValueError(f"INVALID_PAPER_OBSERVATION_STATE:{state}")
    if paper_position_state not in {"PAPER_FLAT", "PAPER_LONG"}:
        raise ValueError(f"INVALID_PAPER_POSITION_STATE:{paper_position_state}")
    wanted = str(paper_signal_id or "").strip()
    if not wanted:
        raise ValueError("PAPER_OBSERVATION_ID_REQUIRED")
    ensure_production_schema()
    with get_db() as db:
        row = db.execute(
            text(
                "SELECT payload FROM paper_observations "
                "WHERE paper_signal_id = :paper_signal_id"
            ),
            {"paper_signal_id": wanted},
        ).mappings().first()
        if not row:
            raise ValueError("PAPER_OBSERVATION_NOT_FOUND")
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload) if isinstance(payload, dict) else {}
        if paper_position_state == "PAPER_LONG" and not isinstance(payload.get("paper_entry_contract"), dict):
            raise ValueError("PAPER_ENTRY_CONTRACT_REQUIRED")
        payload["paper_observation_state"] = state
        payload["paper_position_state"] = paper_position_state
        if exit_reason:
            payload["paper_exit_reason"] = exit_reason
        db.execute(
            text(
                "UPDATE paper_observations "
                "SET paper_observation_state = :state, "
                "paper_position_state = :paper_position_state, "
                "payload = CAST(:payload AS jsonb) "
                "WHERE paper_signal_id = :paper_signal_id"
            ),
            {
                "paper_signal_id": wanted,
                "state": state,
                "paper_position_state": paper_position_state,
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
            },
        )


def fetch_open_paper_positions() -> List[Dict[str, Any]]:
    """Return only explicit Paper Entry records that remain PAPER_LONG."""
    from xiaogu_forward_result_filler_v0_1 import _row_payload

    open_rows = []
    for row in fetch_paper_observations():
        record = _row_payload(row)
        if (
            record.get("paper_position_state") != "PAPER_LONG"
            or record.get("paper_observation_state") == "CLOSED"
            or not isinstance(record.get("paper_entry_contract"), dict)
        ):
            continue
        open_rows.append(record)
    return open_rows


def fetch_production_model(model_id: str) -> Dict[str, Any] | None:
    """Return the sole registry-backed production model, never a research artifact."""
    if not model_id:
        return None
    columns = _table_columns("model_registry")
    if not columns:
        return None
    selected = ["model_id"]
    for column in ("acceptance_artifact", "performance_summary"):
        if column in columns:
            selected.append(column)
    with engine.connect() as db:
        row = db.execute(
            text(f"SELECT {', '.join(selected)} FROM model_registry WHERE model_id = :model_id"),
            {"model_id": model_id},
        ).mappings().first()
    if not row:
        return None
    artifact = row.get("acceptance_artifact")
    summary = row.get("performance_summary")
    if isinstance(artifact, str):
        artifact = json.loads(artifact)
    if isinstance(summary, str):
        summary = json.loads(summary)
    artifact = artifact if isinstance(artifact, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    if not artifact and not summary:
        return None
    return {**summary, **artifact, "model_id": artifact.get("model_id") or row["model_id"]}




def _table_columns(table_name: str) -> set[str]:
    with engine.connect() as db:
        return {
            str(row["column_name"])
            for row in db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = :table_name
                    """
                ),
                {"table_name": table_name},
            ).mappings()
        }


def position_id_for_decision(decision_id: str) -> str:
    """Deterministic position identity derived from the entry decision. Not a random UUID."""
    wanted = str(decision_id or "").strip()
    if not wanted:
        raise ValueError("DECISION_ID_REQUIRED")
    return f"POS|{wanted}"


def _position_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    action = str(row.get("state") or row.get("decision") or payload.get("action") or payload.get("state") or "")
    position_state = str(row.get("position_state") or payload.get("position_state") or "").upper()
    decision_id = str(row.get("decision_id") or payload.get("decision_id") or "").strip()
    position_id = str(row.get("position_id") or payload.get("position_id") or "").strip()
    opened = row.get("opened_trade_date") or row.get("trade_date") or payload.get("trade_date") or ""
    closed = row.get("closed_trade_date")
    original_snapshot_id = str(
        row.get("original_snapshot_id") or payload.get("original_snapshot_id") or payload.get("snapshot_id") or ""
    ).strip()
    if position_state and position_state not in {"FLAT", "LONG"}:
        raise ValueError(f"INVALID_POSITION_STATE:{position_state}")
    return {
        **payload,
        "position_id": position_id,
        "decision_id": decision_id,
        "symbol": str(row.get("symbol") or payload.get("symbol") or ""),
        "original_snapshot_id": original_snapshot_id,
        "position_state": position_state,
        "opened_at": str(opened)[:10],
        "opened_trade_date": str(opened)[:10],
        "closed_at": str(closed)[:10] if closed not in (None, "") else None,
        "closed_trade_date": str(closed)[:10] if closed not in (None, "") else None,
        "trade_date": str(opened)[:10],
        "state": action,
        "action": payload.get("action") or action,
        "previous_action": payload.get("action") or payload.get("previous_action"),
        "decision": action,
        "snapshot_id": payload.get("snapshot_id") or original_snapshot_id,
    }


def _load_positions(where_sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "position_id" not in _table_columns("positions"):
        raise RuntimeError("POSITION_SCHEMA_MISSING")
    sql = f"""
        SELECT
            p.position_id,
            p.decision_id,
            p.symbol,
            p.original_snapshot_id,
            p.position_state,
            p.opened_trade_date,
            p.closed_trade_date,
            p.created_at,
            p.updated_at,
            k.state,
            k.payload,
            k.trade_date
        FROM positions p
        LEFT JOIN picks k ON k.decision_id = p.decision_id
        WHERE {where_sql}
    """
    with engine.connect() as db:
        rows = [dict(row) for row in db.execute(text(sql), params).mappings()]
    positions = []
    seen = set()
    for row in rows:
        if row.get("state") is None and row.get("payload") is None:
            raise RuntimeError("POSITION_IDENTITY_CONFLICT")
        position = _position_payload(row)
        position_id = str(position.get("position_id") or "").strip()
        decision_id = str(position.get("decision_id") or "").strip()
        if not position_id or not decision_id:
            raise RuntimeError("POSITION_IDENTITY_UNAVAILABLE")
        if position_id in seen:
            raise RuntimeError("POSITION_IDENTITY_AMBIGUOUS")
        seen.add(position_id)
        positions.append(position)
    return positions


def fetch_open_positions() -> List[Dict[str, Any]]:
    """Return every active LONG position keyed by position_id."""
    opened = _load_positions("p.position_state = 'LONG'", {})
    return [row for row in opened if row.get("position_state") == "LONG"]


def get_position_by_id(position_id: str) -> Dict[str, Any] | None:
    wanted = str(position_id or "").strip()
    if not wanted:
        return None
    rows = _load_positions("p.position_id = :position_id", {"position_id": wanted})
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError("POSITION_IDENTITY_AMBIGUOUS")
    return rows[0]


def get_position_by_decision_id(decision_id: str) -> Dict[str, Any] | None:
    wanted = str(decision_id or "").strip()
    if not wanted:
        return None
    rows = _load_positions("p.decision_id = :decision_id", {"decision_id": wanted})
    if not rows:
        return None
    if len(rows) > 1:
        raise RuntimeError("POSITION_IDENTITY_AMBIGUOUS")
    return rows[0]


def fetch_position_by_decision_id(decision_id: str) -> Dict[str, Any] | None:
    """Compatibility alias. Position identity owner is get_position_by_id()."""
    return get_position_by_decision_id(decision_id)


def derive_position_state_by_symbol(symbol: str) -> str | None:
    """DERIVED READ ONLY. Never production position identity."""
    wanted = str(symbol or "").strip()
    if not wanted:
        return None
    if "position_id" not in _table_columns("positions"):
        raise RuntimeError("POSITION_SCHEMA_MISSING")
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT position_id, decision_id, position_state
                    FROM positions
                    WHERE symbol = :symbol
                      AND position_state IN ('FLAT', 'LONG')
                    """
                ),
                {"symbol": wanted},
            ).mappings()
        ]
    long_ids = {
        str(row.get("position_id") or "").strip()
        for row in rows
        if str(row.get("position_state") or "").upper() == "LONG" and str(row.get("position_id") or "").strip()
    }
    if len(long_ids) > 1:
        raise RuntimeError("POSITION_STATE_AMBIGUOUS")
    if len(long_ids) == 1:
        return "LONG"
    if any(str(row.get("position_state") or "").upper() == "FLAT" for row in rows):
        return "FLAT"
    return None


def upsert_position(decision: Dict[str, Any]) -> None:
    """Persist one position identity from a recorded decision. Never forges IDs."""
    state = str(decision.get("position_state") or "").upper()
    if state not in {"FLAT", "LONG"}:
        return
    decision_id = str(decision.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("DECISION_ID_REQUIRED")
    existing = get_position_by_decision_id(decision_id)
    if existing is None and state != "LONG":
        return
    position_id = str(decision.get("position_id") or (existing or {}).get("position_id") or "").strip()
    if not position_id:
        position_id = position_id_for_decision(decision_id)
    if existing and str(existing.get("position_id") or "").strip() != position_id:
        raise RuntimeError("POSITION_IDENTITY_CONFLICT")
    original_snapshot_id = str(
        (existing or {}).get("original_snapshot_id")
        or decision.get("original_snapshot_id")
        or decision.get("snapshot_id")
        or ""
    ).strip()
    if not original_snapshot_id:
        raise RuntimeError("SNAPSHOT_IDENTITY_UNAVAILABLE")
    symbol = str(decision.get("symbol") or (existing or {}).get("symbol") or "").strip()
    if not symbol:
        raise ValueError("SYMBOL_REQUIRED")
    opened = str((existing or {}).get("opened_trade_date") or decision.get("trade_date") or "")[:10]
    if not opened:
        raise RuntimeError("POSITION_OPENED_TRADE_DATE_UNAVAILABLE")
    closed = None
    if state == "FLAT":
        closed = str(decision.get("review_trade_date") or decision.get("trade_date") or "")[:10] or None
        if not closed:
            raise RuntimeError("POSITION_CLOSED_TRADE_DATE_UNAVAILABLE")
    if "position_id" not in _table_columns("positions"):
        raise RuntimeError("POSITION_SCHEMA_MISSING")
    with get_db() as db:
        snapshot = db.execute(
            text("SELECT 1 FROM snapshots WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": original_snapshot_id},
        ).first()
        if not snapshot:
            raise RuntimeError("SNAPSHOT_NOT_FOUND")
        pick = db.execute(
            text("SELECT 1 FROM picks WHERE decision_id = :decision_id"),
            {"decision_id": decision_id},
        ).first()
        if not pick:
            raise RuntimeError("DECISION_ID_NOT_FOUND")
        try:
            db.execute(
                text(
                    """
                    INSERT INTO positions (
                        position_id, decision_id, symbol, original_snapshot_id,
                        position_state, opened_trade_date, closed_trade_date
                    )
                    VALUES (
                        :position_id, :decision_id, :symbol, :original_snapshot_id,
                        :position_state, CAST(:opened_trade_date AS date),
                        CAST(:closed_trade_date AS date)
                    )
                    ON CONFLICT (position_id) DO UPDATE
                    SET position_state = EXCLUDED.position_state,
                        closed_trade_date = EXCLUDED.closed_trade_date,
                        updated_at = NOW()
                    """
                ),
                {
                    "position_id": position_id,
                    "decision_id": decision_id,
                    "symbol": symbol,
                    "original_snapshot_id": original_snapshot_id,
                    "position_state": state,
                    "opened_trade_date": opened,
                    "closed_trade_date": closed,
                },
            )
        except IntegrityError as exc:
            raise RuntimeError("POSITION_IDENTITY_CONFLICT") from exc


def _calendar_date(value: Any) -> date:
    wanted = value.isoformat() if hasattr(value, "isoformat") else str(value)
    try:
        return date.fromisoformat(str(wanted)[:10])
    except ValueError as exc:
        raise ValueError(f"INVALID_TRADE_DATE:{wanted}") from exc


def _calendar_dataset_path(year: int) -> Path:
    if CALENDAR_DATASET_PATH is not None:
        return CALENDAR_DATASET_PATH
    return CALENDAR_DATASET_DIR / f"ashare_{int(year):04d}.json"


def calendar_content_hash(records: Iterable[Dict[str, Any]]) -> str:
    """Hash only immutable calendar facts in ascending date order."""
    normalized = []
    for record in records:
        trade_date = _calendar_date(record.get("trade_date") or record.get("date"))
        market = str(record.get("market") or "").strip().upper()
        value = record.get("is_trading_day")
        if not market or not isinstance(value, bool):
            raise ValueError("CALENDAR_CONTENT_INVALID")
        normalized.append({
            "trade_date": trade_date.isoformat(),
            "market": market,
            "is_trading_day": value,
            "source": str(record.get("source") or "").strip(),
            "calendar_version": str(record.get("calendar_version") or "").strip(),
        })
    normalized.sort(key=lambda item: item["trade_date"])
    return hashlib.sha256(
        "\n".join(
            json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            for item in normalized
        ).encode("utf-8")
    ).hexdigest()


def _validate_calendar_dataset(payload: Any, effective_year: int) -> Dict[str, Any]:
    """Validate one complete annual dataset before it can enter PostgreSQL."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    source = str(payload.get("source") or "").strip()
    if not source:
        raise RuntimeError(CALENDAR_SOURCE_MISSING)
    source_timestamp = str(payload.get("source_timestamp") or "").strip()
    if not source_timestamp:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    version = str(payload.get("calendar_version") or "").strip()
    if not version:
        raise RuntimeError(CALENDAR_VERSION_MISSING)
    rows = payload["rows"]
    expected_count = 366 if calendar_module.isleap(int(effective_year)) else 365
    seen: set[date] = set()
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
        try:
            current = _calendar_date(raw.get("trade_date"))
        except ValueError as exc:
            raise RuntimeError(CALENDAR_DATA_UNAVAILABLE) from exc
        if current.year != int(effective_year):
            raise RuntimeError(INVALID_CALENDAR_YEAR)
        if current in seen:
            raise RuntimeError("CALENDAR_DATA_UNAVAILABLE:CALENDAR_DUPLICATE_DATE")
        seen.add(current)
        if str(raw.get("market") or "").strip().upper() != TRADING_CALENDAR_MARKET:
            raise RuntimeError(CALENDAR_MARKET_CONFLICT)
        if not isinstance(raw.get("is_trading_day"), bool):
            raise RuntimeError("CALENDAR_BOOLEAN_INVALID")
        normalized.append({
            "trade_date": current.isoformat(),
            "market": TRADING_CALENDAR_MARKET,
            "is_trading_day": raw["is_trading_day"],
            "source": source,
            "source_timestamp": source_timestamp,
            "calendar_version": version,
        })
    if len(rows) != expected_count or len(seen) != expected_count:
        raise RuntimeError(CALENDAR_INCOMPLETE)
    first = date(int(effective_year), 1, 1)
    last = date(int(effective_year), 12, 31)
    if seen != {first + timedelta(days=offset) for offset in range(expected_count)}:
        raise RuntimeError(CALENDAR_INCOMPLETE)
    if str(effective_year) not in version:
        raise RuntimeError(INVALID_CALENDAR_YEAR)
    return {
        "source": source,
        "source_timestamp": source_timestamp,
        "calendar_version": version,
        "effective_year": int(effective_year),
        "rows": normalized,
        "content_hash": calendar_content_hash(normalized),
    }


def load_trading_calendar(year: int) -> Dict[str, Any]:
    """Load and validate the authoritative annual dataset for ``year``."""
    effective_year = int(year)
    path = _calendar_dataset_path(effective_year)
    if not path.exists():
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE) from exc
    return _validate_calendar_dataset(payload, effective_year)


def get_calendar_version(year: int) -> str:
    return str(load_trading_calendar(int(year))["calendar_version"])


def _calendar_metadata(value: Any) -> Dict[str, Any]:
    wanted = _calendar_date(value)
    try:
        with engine.connect() as db:
            row = db.execute(
                text(
                    """
                    SELECT trade_date, market, is_trading_day, source,
                           source_timestamp, calendar_version
                    FROM trading_calendar
                    WHERE trade_date = CAST(:trade_date AS date)
                      AND market = :market
                    LIMIT 1
                    """
                ),
                {"trade_date": wanted.isoformat(), "market": TRADING_CALENDAR_MARKET},
            ).mappings().first()
    except (SQLAlchemyError, RuntimeError) as exc:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE) from exc
    if not row or not row.get("source") or not row.get("source_timestamp") or not row.get("calendar_version"):
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    return {
        "calendar_version": str(row["calendar_version"]),
        "calendar_content_hash": _calendar_runtime_hash(wanted.year),
        "calendar_source": str(row["source"]),
        "effective_year": wanted.year,
    }


def _calendar_runtime_hash(year: int) -> str:
    start = date(int(year), 1, 1)
    end = date(int(year), 12, 31)
    rows = _calendar_rows(start, end)
    return calendar_content_hash(rows)


def is_trading_date(value: Any) -> str:
    """Return TRUE, FALSE, or UNKNOWN from the persisted calendar fact."""
    wanted = _calendar_date(value)
    try:
        with engine.connect() as db:
            row = db.execute(
                text(
                    "SELECT is_trading_day, source, source_timestamp, calendar_version FROM trading_calendar "
                    "WHERE trade_date = CAST(:trade_date AS date) "
                    "AND market = :market LIMIT 1"
                ),
                {"trade_date": wanted.isoformat(), "market": TRADING_CALENDAR_MARKET},
            ).first()
    except (SQLAlchemyError, RuntimeError):
        return CALENDAR_UNKNOWN
    if row is None or not row[1] or row[2] is None or not row[3]:
        return CALENDAR_UNKNOWN
    return TRADING_DAY if bool(row[0]) else NON_TRADING_DAY


def _calendar_rows(start: date, end: date) -> list[Dict[str, Any]]:
    if end < start:
        return []
    try:
        with engine.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    text(
                        "SELECT trade_date, market, is_trading_day, source, "
                        "source_timestamp, calendar_version "
                        "FROM trading_calendar WHERE trade_date >= CAST(:start_date AS date) "
                        "AND trade_date <= CAST(:end_date AS date) "
                        "AND market = :market ORDER BY trade_date"
                    ),
                    {
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "market": TRADING_CALENDAR_MARKET,
                    },
                ).mappings()
            ]
    except (SQLAlchemyError, RuntimeError) as exc:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE) from exc
    expected = (end - start).days + 1
    if len(rows) != expected:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    versions = set()
    sources = set()
    for expected_date, row in zip(
        (start + timedelta(days=offset) for offset in range(expected)), rows
    ):
        if (
            row.get("trade_date") != expected_date
            or str(row.get("market") or "").upper() != TRADING_CALENDAR_MARKET
            or not isinstance(row.get("is_trading_day"), bool)
            or not row.get("source")
            or not row.get("source_timestamp")
            or not row.get("calendar_version")
        ):
            raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
        versions.add(str(row["calendar_version"]))
        sources.add(str(row["source"]))
    if len(versions) != 1 or len(sources) != 1:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    return rows


def trading_days_between(start: Any, end: Any) -> int:
    """Count trading days after start and through end; missing facts block."""
    start_date = _calendar_date(start)
    end_date = _calendar_date(end)
    if end_date < start_date:
        return 0
    rows = _calendar_rows(start_date, end_date)
    return sum(
        1 for row in rows
        if row["trade_date"] > start_date and bool(row["is_trading_day"])
    )


def _resolve_direction(trade_date: date, offset: int, direction: str) -> date:
    if offset < 0:
        raise ValueError("TRADING_DAY_OFFSET_MUST_BE_NON_NEGATIVE")
    if offset == 0:
        if is_trading_date(trade_date) != TRADING_DAY:
            raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
        return trade_date
    try:
        with engine.connect() as db:
            query = (
                "SELECT trade_date FROM trading_calendar "
                "WHERE trade_date {operator} CAST(:trade_date AS date) "
                "AND market = :market AND is_trading_day "
            "ORDER BY trade_date {order}"
            ).format(
                operator=">" if direction == "next" else "<",
                order="ASC" if direction == "next" else "DESC",
            )
            candidates = db.execute(
                text(query),
                {"trade_date": trade_date.isoformat(), "market": TRADING_CALENDAR_MARKET},
            ).fetchall()
    except (SQLAlchemyError, RuntimeError) as exc:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE) from exc
    if len(candidates) < offset:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    target = candidates[offset - 1][0]
    # Fetch a bounded interval so an omitted calendar row cannot be skipped.
    if direction == "next":
        rows = _calendar_rows(trade_date, target)
        trading = [row["trade_date"] for row in rows if row["is_trading_day"] and row["trade_date"] > trade_date]
    else:
        rows = _calendar_rows(target, trade_date)
        trading = [row["trade_date"] for row in rows if row["is_trading_day"] and row["trade_date"] < trade_date]
        trading.reverse()
    if len(trading) < offset:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    return trading[offset - 1]


def next_trading_date(value: Any) -> date:
    return _resolve_direction(_calendar_date(value), 1, "next")


def previous_trading_date(value: Any) -> date:
    return _resolve_direction(_calendar_date(value), 1, "previous")


def resolve_trading_date(trade_date: Any, offset: int) -> date:
    """Resolve T+offset strictly from the sole persisted calendar."""
    return _resolve_direction(_calendar_date(trade_date), int(offset), "next")


def resolve_t_plus_n(trade_date: Any, offset: int) -> date:
    if int(offset) not in {0, 1, 2, 3, 4, 5}:
        raise ValueError("T_PLUS_OFFSET_OUT_OF_RANGE")
    return resolve_trading_date(trade_date, int(offset))


def record_trading_calendar(records: Iterable[Dict[str, Any]]) -> None:
    """Persist immutable A-share trade-date facts from the calendar source."""
    rows = list(records)
    if not rows:
        return
    normalized = []
    for record in rows:
        source = str(record.get("source") or "").strip()
        trade_date = _calendar_date(record.get("trade_date") or record.get("date"))
        default_version = (
            "BAOSTOCK_TRADE_DATES_V1"
            if source == "baostock_trade_dates"
            else get_calendar_version(trade_date.year)
        )
        normalized.append({
            **record,
            "trade_date": trade_date.isoformat(),
            "market": str(record.get("market") or TRADING_CALENDAR_MARKET).upper(),
            "is_trading_day": record.get("is_trading_day"),
            "source": source,
            "source_timestamp": str(record.get("source_timestamp") or "").strip(),
            "calendar_version": str(record.get("calendar_version") or default_version).strip(),
        })
    if any(not row["source_timestamp"] for row in normalized):
        timestamp = datetime.now(timezone.utc).isoformat()
        for row in normalized:
            row["source_timestamp"] = timestamp
    migrate_trading_calendar(
        normalized,
        migration_id=f"calendar-record-{normalized[0]['calendar_version']}",
        reason="Load authoritative trading calendar records",
    )


def authoritative_calendar_records(start_date: str, end_date: str) -> list[Dict[str, Any]]:
    """Load complete annual official datasets, never infer from prices."""
    start = _calendar_date(start_date)
    end = _calendar_date(end_date)
    if end < start:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    selected = []
    for year in range(start.year, end.year + 1):
        dataset = load_trading_calendar(year)
        for row in dataset["rows"]:
            current = _calendar_date(row["trade_date"])
            if start <= current <= end:
                selected.append({
                    **row,
                    "payload": {
                        "dataset": _calendar_dataset_path(year).name,
                        "effective_year": year,
                        "calendar_content_hash": dataset["content_hash"],
                    },
                })
    expected = (end - start).days + 1
    if len(selected) != expected:
        raise RuntimeError(CALENDAR_DATA_UNAVAILABLE)
    return selected


def migrate_trading_calendar(
    records: Iterable[Dict[str, Any]],
    *,
    migration_id: str,
    reason: str,
) -> Dict[str, Any]:
    """Audit then apply an authoritative calendar version in one transaction."""
    ensure_production_schema()
    normalized = []
    effective_years = set()
    for record in records:
        trade_date = _calendar_date(record.get("trade_date") or record.get("date"))
        source = str(record.get("source") or "").strip()
        version = str(record.get("calendar_version") or "").strip()
        if not source or not version or record.get("is_trading_day") is None:
            raise ValueError("TRADING_CALENDAR_IDENTITY_REQUIRED")
        market = str(record.get("market") or TRADING_CALENDAR_MARKET).strip().upper()
        if market != TRADING_CALENDAR_MARKET:
            raise ValueError(CALENDAR_MARKET_CONFLICT)
        if not isinstance(record.get("is_trading_day"), bool):
            raise ValueError("CALENDAR_BOOLEAN_INVALID")
        effective_years.add(trade_date.year)
        source_timestamp = str(record.get("source_timestamp") or "").strip()
        if not source_timestamp:
            source_timestamp = datetime.now(timezone.utc).isoformat()
        normalized.append({
            **record,
            "trade_date": trade_date.isoformat(),
            "market": market,
            "is_trading_day": record["is_trading_day"],
            "source": source,
            "source_timestamp": source_timestamp,
            "calendar_version": version,
        })
    if len(effective_years) > 1:
        raise ValueError(INVALID_CALENDAR_YEAR)
    declared_hashes = {
        str(
            record.get("calendar_content_hash")
            or (record.get("payload") or {}).get("calendar_content_hash")
            or ""
        ).strip()
        for record in normalized
    }
    declared_hashes.discard("")
    version_hash = (
        next(iter(declared_hashes))
        if len(declared_hashes) == 1
        else calendar_content_hash(normalized) if normalized else ""
    )
    effective_year = next(iter(effective_years), None)
    with engine.begin() as db:
        if normalized:
            version_row = db.execute(
                text(
                    """
                    SELECT content_hash
                    FROM trading_calendar_versions
                    WHERE calendar_version = :calendar_version
                      AND market = :market
                      AND effective_year = :effective_year
                    """
                ),
                {
                    "calendar_version": normalized[0]["calendar_version"],
                    "market": normalized[0]["market"],
                    "effective_year": effective_year,
                },
            ).mappings().first()
            if version_row and str(version_row["content_hash"]) != version_hash:
                raise ValueError(CALENDAR_VERSION_CONTENT_CONFLICT)
            if not version_row:
                status = (
                    "ACTIVE"
                    if effective_year == datetime.now(timezone.utc).astimezone(
                        ZoneInfo("Asia/Shanghai")
                    ).year
                    else "REGISTERED"
                )
                db.execute(
                    text(
                        """
                        INSERT INTO trading_calendar_versions
                            (calendar_version, market, effective_year, source,
                             source_timestamp, content_hash, status)
                        VALUES (:calendar_version, :market, :effective_year, :source,
                                CAST(:source_timestamp AS timestamptz), :content_hash, :status)
                        """
                    ),
                    {
                        "calendar_version": normalized[0]["calendar_version"],
                        "market": normalized[0]["market"],
                        "effective_year": effective_year,
                        "source": normalized[0]["source"],
                        "source_timestamp": normalized[0]["source_timestamp"],
                        "content_hash": version_hash,
                        "status": status,
                    },
                )
        for record in normalized:
            previous = db.execute(
                text(
                    "SELECT is_trading_day, source, calendar_version FROM trading_calendar "
                    "WHERE trade_date = CAST(:trade_date AS date)"
                ),
                {"trade_date": record["trade_date"]},
            ).mappings().first()
            applied = db.execute(
                text(
                    """
                    SELECT 1
                    FROM trading_calendar_migrations
                    WHERE migration_id = :migration_id
                      AND trade_date = CAST(:trade_date AS date)
                      AND new_is_trading_day = :new_is_trading_day
                      AND new_source = :new_source
                      AND source_timestamp = CAST(:source_timestamp AS timestamptz)
                      AND new_calendar_version = :new_calendar_version
                    LIMIT 1
                    """
                ),
                {
                    "migration_id": migration_id,
                    "trade_date": record["trade_date"],
                    "new_is_trading_day": record["is_trading_day"],
                    "new_source": record["source"],
                    "source_timestamp": record["source_timestamp"],
                    "new_calendar_version": record["calendar_version"],
                },
            ).first()
            if applied:
                continue
            db.execute(
                text(
                    """
                    INSERT INTO trading_calendar_migrations (
                        migration_id, trade_date, market, previous_is_trading_day,
                        previous_source, previous_calendar_version, new_is_trading_day,
                        new_source, new_calendar_version, source_timestamp, reason
                    ) VALUES (
                        :migration_id, CAST(:trade_date AS date), :market,
                        :previous_is_trading_day, :previous_source,
                        :previous_calendar_version, :new_is_trading_day,
                        :new_source, :new_calendar_version,
                        CAST(:source_timestamp AS timestamptz), :reason
                    )
                    """
                ),
                {
                    "migration_id": migration_id,
                    "trade_date": record["trade_date"],
                    "market": record["market"],
                    "previous_is_trading_day": previous["is_trading_day"] if previous else None,
                    "previous_source": previous["source"] if previous else None,
                    "previous_calendar_version": previous["calendar_version"] if previous else None,
                    "new_is_trading_day": record["is_trading_day"],
                    "new_source": record["source"],
                    "new_calendar_version": record["calendar_version"],
                    "source_timestamp": record["source_timestamp"],
                    "reason": reason,
                },
            )
        if normalized:
            db.execute(
                text(
                    """
                    UPDATE trading_calendar_versions
                    SET status = 'SUPERSEDED'
                    WHERE market = :market
                      AND effective_year = :effective_year
                      AND calendar_version <> :calendar_version
                      AND status = 'ACTIVE'
                    """
                ),
                {
                    "market": normalized[0]["market"],
                    "effective_year": effective_year,
                    "calendar_version": normalized[0]["calendar_version"],
                },
            )
            db.execute(
                text(
                    """
                    UPDATE trading_calendar_versions
                    SET status = 'ACTIVE'
                    WHERE calendar_version = :calendar_version
                      AND market = :market
                      AND effective_year = :effective_year
                      AND :effective_year = EXTRACT(YEAR FROM CURRENT_DATE)
                    """
                ),
                {
                    "calendar_version": normalized[0]["calendar_version"],
                    "market": normalized[0]["market"],
                    "effective_year": effective_year,
                },
            )
            db.execute(
                text(
                    """
                    INSERT INTO trading_calendar (
                        trade_date, market, is_trading_day, source, source_timestamp,
                        calendar_version, payload
                    ) VALUES (
                        CAST(:trade_date AS date), :market, :is_trading_day, :source,
                        CAST(:source_timestamp AS timestamptz), :calendar_version,
                        CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (trade_date) DO UPDATE SET
                        market = EXCLUDED.market,
                        is_trading_day = EXCLUDED.is_trading_day,
                        source = EXCLUDED.source,
                        source_timestamp = EXCLUDED.source_timestamp,
                        calendar_version = EXCLUDED.calendar_version,
                        payload = EXCLUDED.payload
                    """
                ),
                {
                    **record,
                    "payload": json.dumps(record.get("payload") or {}, ensure_ascii=False, default=str),
                },
            )
    return {
        "migration_id": migration_id,
        "calendar_version": normalized[0]["calendar_version"] if normalized else "",
        "rows": len(normalized),
        "status": "PASS",
    }


def seed_authoritative_a_share_calendar(
    start_date: str | None = None,
    end_date: str | None = None,
) -> Dict[str, Any]:
    current_year = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).year
    start_date = start_date or f"{current_year}-01-01"
    end_date = end_date or f"{current_year}-12-31"
    records = authoritative_calendar_records(start_date, end_date)
    version = records[0]["calendar_version"] if records else ""
    return migrate_trading_calendar(
        records,
        migration_id=f"calendar-{start_date}-{end_date}-{version}",
        reason="Load authoritative A-share trading calendar dataset",
    )


def audit_trading_calendar(
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    today: Any | None = None,
) -> Dict[str, Any]:
    """Check database calendar content against its authoritative annual dataset."""
    requested_today = (
        _calendar_date(today)
        if today is not None
        else datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
    )
    start_date = start_date or f"{requested_today.year}-01-01"
    end_date = end_date or f"{requested_today.year}-12-31"
    start = _calendar_date(start_date)
    end = _calendar_date(end_date)
    report: Dict[str, Any] = {
        "market": TRADING_CALENDAR_MARKET,
        "effective_year": start.year,
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "status": "BLOCKED",
        "calendar_gap": False,
        "duplicate_count": 0,
        "missing_dates": [],
        "invalid_dates": [],
        "invalid_market": [],
        "invalid_boolean": [],
        "invalid_source": [],
        "invalid_version": [],
        "unexpected_weekend_trading": [],
        "unexpected_weekday_closure": [],
    }
    if start.year != end.year:
        report["reason"] = INVALID_CALENDAR_YEAR
        return report
    try:
        with engine.connect() as db:
            raw_rows = [
                dict(row)
                for row in db.execute(
                    text(
                        """
                        SELECT trade_date, market, is_trading_day, source,
                               source_timestamp, calendar_version
                        FROM trading_calendar
                        WHERE trade_date >= CAST(:start_date AS date)
                          AND trade_date <= CAST(:end_date AS date)
                        ORDER BY trade_date
                        """
                    ),
                    {"start_date": start.isoformat(), "end_date": end.isoformat()},
                ).mappings()
            ]
        date_counts: Dict[date, int] = {}
        for row in raw_rows:
            current = row.get("trade_date")
            date_counts[current] = date_counts.get(current, 0) + 1
            if str(row.get("market") or "").upper() != TRADING_CALENDAR_MARKET:
                report["invalid_market"].append(str(current))
            if not isinstance(row.get("is_trading_day"), bool):
                report["invalid_boolean"].append(str(current))
            if not row.get("source") or row.get("source_timestamp") is None:
                report["invalid_source"].append(str(current))
            if not row.get("calendar_version"):
                report["invalid_version"].append(str(current))
        report["duplicate_count"] = sum(count - 1 for count in date_counts.values() if count > 1)
        expected_dates = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
        report["missing_dates"] = sorted(
            current.isoformat() for current in expected_dates if date_counts.get(current, 0) == 0
        )
        rows = _calendar_rows(start, end)
    except (SQLAlchemyError, RuntimeError) as exc:
        report["reason"] = str(exc)
        return report
    report["row_count"] = len(rows)
    report["calendar_source"] = str(rows[0]["source"]) if rows else ""
    report["source"] = [report["calendar_source"]] if report["calendar_source"] else []
    report["versions"] = sorted({str(row["calendar_version"]) for row in rows})
    report["calendar_version"] = report["versions"][0] if len(report["versions"]) == 1 else ""
    report["calendar_content_hash"] = calendar_content_hash(rows)
    report["content_hash"] = report["calendar_content_hash"]
    try:
        expected_rows = authoritative_calendar_records(start_date, end_date)
        expected = {row["trade_date"]: row["is_trading_day"] for row in expected_rows}
        report["authoritative_content_hash"] = calendar_content_hash(expected_rows)
    except RuntimeError as exc:
        report["reason"] = str(exc)
        return report
    report["unexpected_weekend_trading"] = [
        row["trade_date"].isoformat()
        for row in rows
        if row["trade_date"] in expected
        and bool(row["is_trading_day"]) != bool(expected[row["trade_date"]])
        and not bool(expected[row["trade_date"]])
    ]
    report["unexpected_weekday_closure"] = [
        row["trade_date"].isoformat()
        for row in rows
        if row["trade_date"] in expected
        and bool(row["is_trading_day"]) != bool(expected[row["trade_date"]])
        and bool(expected[row["trade_date"]])
    ]
    current_date = requested_today
    report["today"] = current_date.isoformat()
    report["today_status"] = is_trading_date(current_date)
    report["today_available"] = report["today_status"] != CALENDAR_UNKNOWN
    report["today_source"] = (
        report["calendar_source"]
        if report["today_status"] != CALENDAR_UNKNOWN and start <= current_date <= end
        else None
    )
    report["regressions"] = {
        "2026-08-31": is_trading_date(date(2026, 8, 31)),
        "2026-09-25": is_trading_date(date(2026, 9, 25)),
        "2026-09-28": is_trading_date(date(2026, 9, 28)),
    }
    try:
        report["t5"] = resolve_t_plus_n("2026-09-21", 5).isoformat()
    except RuntimeError:
        report["t5"] = None
    report["calendar_integrity"] = (
        report["row_count"] == (366 if calendar_module.isleap(start.year) else 365)
        and report["coverage_start"] == f"{start.year}-01-01"
        and report["coverage_end"] == f"{start.year}-12-31"
        and report["calendar_version"] == get_calendar_version(start.year)
        and report["calendar_source"] == load_trading_calendar(start.year)["source"]
        and report["calendar_content_hash"] == report["authoritative_content_hash"]
    )
    report["status"] = "PASS" if (
        not report["unexpected_weekend_trading"]
        and not report["unexpected_weekday_closure"]
        and report["today_available"]
        and report["calendar_integrity"]
        and report["regressions"] == {
            "2026-08-31": TRADING_DAY,
            "2026-09-25": NON_TRADING_DAY,
            "2026-09-28": TRADING_DAY,
        }
        and report["t5"] == "2026-09-29"
    ) else "BLOCKED"
    return report


def calendar_health(*, today: Any | None = None) -> Dict[str, Any]:
    """Public read-only health report for the effective annual Calendar."""
    return audit_trading_calendar(today=today)


def refresh_a_share_trading_calendar(start_date: str, end_date: str) -> int:
    """Load only checked-in authoritative data; missing years fail closed."""
    rows = authoritative_calendar_records(start_date, end_date)
    record_trading_calendar(rows)
    return len(rows)


def fetch_canonical_future_bars(
    symbol: str,
    *,
    start_date: str,
    end_date: str = "",
) -> List[Dict[str, Any]]:
    clauses = [
        "symbol = :symbol",
        "date > CAST(:start_date AS date)",
    ]
    params = {"symbol": str(symbol).zfill(6), "start_date": start_date}
    if end_date:
        clauses.append("date <= CAST(:end_date AS date)")
        params["end_date"] = end_date
    with engine.connect() as db:
        return [dict(row) for row in db.execute(
            text(
                "SELECT symbol, date, open, high, low, close, volume, amount, "
                "source, source_timestamp, price_basis "
                f"FROM canonical_future_prices WHERE {' AND '.join(clauses)} ORDER BY date"
            ),
            params,
        ).mappings()]


def fetch_position_outcome(decision_id: str = "", *, symbol: str = "") -> Dict[str, Any]:
    """Read the 5D outcome bound to one decision_id. Symbol-only lookup is forbidden."""
    if not decision_id:
        return {"status": "OUTCOME_NOT_BOUND", "symbol": symbol, "decision_id": ""}
    columns = _table_columns("returns")
    with engine.connect() as db:
        if "decision_id" in columns:
            row = db.execute(
                text(
                    """
                    SELECT *
                    FROM returns
                    WHERE decision_id = :decision_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"decision_id": decision_id},
            ).mappings().first()
        elif "payload" in columns:
            row = db.execute(
                text(
                    """
                    SELECT *
                    FROM returns
                    WHERE payload->>'decision_id' = :decision_id
                       OR payload->>'id' = :decision_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"decision_id": decision_id},
            ).mappings().first()
        else:
            return {"status": "OUTCOME_NOT_BOUND", "symbol": symbol, "decision_id": decision_id}
    if not row:
        return {"status": "OUTCOME_NOT_BOUND", "symbol": symbol, "decision_id": decision_id}
    payload = row.get("payload") if "payload" in (row or {}) else None
    if isinstance(payload, str):
        payload = json.loads(payload)
    result = dict(row)
    if isinstance(payload, dict):
        result = {**payload, **{key: value for key, value in result.items() if key != "payload"}}
        result["payload"] = payload
    result["status"] = "BOUND"
    result["decision_id"] = decision_id
    return result

def fetch_returns() -> List[Dict[str, Any]]:
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text("SELECT * FROM returns ORDER BY id DESC")).mappings()]


def fetch_horizon_outcomes(decision_id: str) -> Dict[str, Any]:
    """Return persisted T+1..T+5 facts for one decision. Missing days stay MISSING."""
    decision_id = str(decision_id or "").strip()
    if not decision_id:
        raise ValueError("DECISION_ID_REQUIRED")
    with engine.connect() as db:
        row = db.execute(
            text(
                "SELECT decision_id, trade_date, symbol, payload "
                "FROM returns WHERE decision_id = :decision_id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"decision_id": decision_id},
        ).mappings().first()
    if not row:
        return {
            "decision_id": decision_id,
            "outcome_id": decision_id,
            "status": "MISSING",
            "days": {
                str(day): {
                    "status": "MISSING",
                    "horizon": day,
                    "horizon_outcome_id": f"{decision_id}:{day}",
                    "horizon_trade_date": None,
                }
                for day in (1, 2, 3, 4, 5)
            },
        }
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    days = payload.get("days") if isinstance(payload.get("days"), dict) else {}
    settled = {}
    for day in (1, 2, 3, 4, 5):
        item = days.get(str(day), days.get(day))
        if not isinstance(item, dict) or not item:
            settled[str(day)] = {
                "status": "MISSING",
                "horizon": day,
                "horizon_outcome_id": f"{decision_id}:{day}",
                "horizon_trade_date": None,
            }
        else:
            settled[str(day)] = {
                **item,
                "status": item.get("status") or "SETTLED",
                "horizon": item.get("horizon") or day,
                "horizon_outcome_id": f"{decision_id}:{day}",
                "horizon_trade_date": item.get("horizon_trade_date") or item.get("date"),
            }
            settled[str(day)].pop("outcome_id", None)
    return {
        "decision_id": decision_id,
        "paper_signal_id": payload.get("paper_signal_id"),
        "snapshot_id": payload.get("snapshot_id"),
        "production_run_id": payload.get("production_run_id") or row.get("production_run_id"),
        "outcome_id": decision_id,
        "horizon_identity": {
            str(day): f"{decision_id}:{day}" for day in (1, 2, 3, 4, 5)
        },
        "status": payload.get("data_status") or "PARTIAL",
        "opportunity_5d": payload.get("opportunity_5d", payload.get("profit_window")),
        "settled_at": payload.get("outcome_settled_at") or payload.get("settled_at"),
        "mae": payload.get("max_mae_5d"),
        "mfe": payload.get("future_5d_mfe") or payload.get("max_mfe_5d"),
        "realized_return": payload.get("realized_return_5d") or payload.get("future_5d_net_return"),
        "days": settled,
    }


def fetch_trade_records() -> List[Dict[str, Any]]:
    """Read production decisions and outcomes from the existing tables."""
    with engine.connect() as db:
        decisions = [dict(row) for row in db.execute(text("SELECT * FROM picks ORDER BY id ASC")).mappings()]
        outcomes = [dict(row) for row in db.execute(text("SELECT * FROM returns ORDER BY id ASC")).mappings()]
    def _decision_key(row: Dict[str, Any]) -> str:
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        return str(row.get("decision_id") or payload.get("decision_id") or "")

    return [
        {
            "decision": decision,
            "outcomes": [
                outcome for outcome in outcomes
                if _decision_key(outcome) and _decision_key(outcome) == _decision_key(decision)
            ],
        }
        for decision in decisions
    ]


def database_identity_coverage() -> Dict[str, Any]:
    """Read-only identity and outcome coverage for the historical audit."""
    requested = {
        "picks": ("decision_id", "snapshot_id", "lineage_id", "trade_date", "symbol"),
        "returns": (
            "decision_id", "candidate_snapshot_id", "production_run_id", "trade_date",
            "symbol", "entry_price", "entry_time",
        ),
        "daily_candidates": (
            "candidate_snapshot_id", "production_run_id", "trade_date", "symbol",
            "open_price", "high_price", "low_price", "close_price",
        ),
        "production_runs": ("production_run_id", "trade_date", "lineage_id"),
        "snapshots": ("snapshot_id", "lineage_id", "trade_date", "symbol"),
        "canonical_historical_snapshots": (
            "snapshot_id", "lineage_id", "trade_date", "signal_time", "available_at",
        ),
    }
    report: Dict[str, Any] = {}
    with engine.connect() as db:
        for table, fields in requested.items():
            columns = {
                str(row["column_name"])
                for row in db.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = :table
                        """
                    ),
                    {"table": table},
                ).mappings()
            }
            if not columns:
                continue
            total = int(db.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            coverage: Dict[str, Any] = {}
            for field in fields:
                if field not in columns:
                    coverage[field] = {"present": 0, "total": total, "coverage": None}
                    continue
                present = int(
                    db.execute(
                        text(
                            f'''SELECT count(*) FROM "{table}"
                                WHERE "{field}" IS NOT NULL
                                  AND BTRIM(CAST("{field}" AS text)) <> '' '''
                        )
                    ).scalar_one()
                )
                coverage[field] = {
                    "present": present,
                    "total": total,
                    "coverage": present / total if total else None,
                }
            if table == "returns":
                for day in range(1, 6):
                    field = f"t{day}_return"
                    if field in columns:
                        present = int(
                            db.execute(
                                text(
                                    f'''SELECT count(*) FROM "{table}"
                                        WHERE "{field}" IS NOT NULL'''
                                )
                            ).scalar_one()
                        )
                    else:
                        present = 0
                    coverage[f"T+{day}"] = {
                        "present": present,
                        "total": total,
                        "coverage": present / total if total else None,
                        "basis": field if field in columns else "NOT_PERSISTED_AS_SCALAR",
                    }
            report[table] = {"row_count": total, "coverage": coverage}
    return report


def database_asset_report() -> Dict[str, Any]:
    """Read-only inventory of the live production schema and its assets."""
    relevant = {
        "picks", "returns", "daily_candidates", "production_runs",
        "production_run_steps", "production_run_active", "scan_sessions",
        "scan_market_data", "signals", "research_runs", "manual_execution_records",
        "signal_effectiveness", "ledger", "model_registry", "scoring_config",
        "production_alpha_health", "pick_case_embeddings", "scan_data_directory_catalog",
        "scan_data_directory_content", "snapshots", "canonical_historical_snapshots",
        "canonical_future_prices",
    }
    with engine.connect() as db:
        tables = [
            dict(row)
            for row in db.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
            ).mappings()
        ]
        report = []
        for table in tables:
            name = table["table_name"]
            if name not in relevant:
                continue
            columns = {
                row["column_name"]
                for row in db.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = :table_name
                        """
                    ),
                    {"table_name": name},
                ).mappings()
            }
            count = int(db.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
            date_expr = None
            for candidate in ("trade_date", "analysis_date", "health_date", "created_at", "updated_at"):
                if candidate in columns:
                    date_expr = f'MIN("{candidate}")::text || \'..\' || MAX("{candidate}")::text'
                    break
            if "symbol" in columns:
                symbol_expr = 'count(DISTINCT "symbol")'
            elif "payload" in columns:
                symbol_expr = "count(DISTINCT NULLIF(payload->>'symbol', ''))"
            else:
                symbol_expr = "count(*) * 0"
            date_range = (
                db.execute(text(f'SELECT {date_expr} FROM "{name}"')).scalar_one()
                if date_expr else None
            )
            symbol_count = int(db.execute(text(f'SELECT {symbol_expr} FROM "{name}"')).scalar_one())
            purpose = {
                "picks": "Production decision truth",
                "returns": "Historical T+1..T+5 result records",
                "daily_candidates": "T-day candidate snapshots and rationale",
                "production_runs": "Production run identity and configuration",
                "production_run_steps": "Production run execution steps",
                "production_run_active": "Active run/pick pointers",
                "scan_sessions": "Scanner session metadata",
                "scan_market_data": "Scanner market-data payloads",
                "signals": "Raw signal snapshots",
                "research_runs": "Research execution metadata",
                "manual_execution_records": "Paper execution evidence",
                "signal_effectiveness": "Historical signal diagnostics",
                "ledger": "Append-only scanner and paper ledger records",
                "model_registry": "Registered model and version metadata",
                "scoring_config": "Persisted scoring configuration snapshots",
                "production_alpha_health": "Production alpha health checks",
                "pick_case_embeddings": "Historical pick case representations",
                "scan_data_directory_catalog": "Scanner source catalog",
                "scan_data_directory_content": "Scanner source content index",
                "snapshots": "Persisted point-in-time snapshots",
                "canonical_historical_snapshots": "Immutable T-day replay snapshots",
                "canonical_future_prices": "Canonical future OHLC evidence",
            }.get(name, "Related production asset")
            report.append({
                "table": name, "purpose": purpose, "row_count": count,
                "date_range": date_range, "symbol_count": symbol_count,
            })
    return {
        "read_only": True,
        "tables": report,
        "schema_audit": audit_production_schema(),
        "identity_coverage": database_identity_coverage(),
    }


def fetch_historical_replay_assets(
    *, start_date: str | None = None, end_date: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load persisted replay inputs without assuming one migration's schema.

    The production database has evolved in place.  Replay must therefore read
    complete rows and let the Python owner normalize missing fields; selecting
    a hard-coded column list makes old or partially migrated databases
    impossible to replay.
    """
    scalar_columns = {
        "picks": {
            "id", "trade_date", "symbol", "decision", "decision_id", "rule_version", "stock_name",
            "production_run_id", "data_version", "created_at", "updated_at",
            "blockers", "source_layers",
        },
        "returns": {
            "id", "pick_id", "decision_id", "trade_date", "symbol", "t1_return", "t2_return",
            "t3_return", "t5_return", "t1_return_close", "t1_return_high",
            "t1_open_return", "t1_high_return", "t1_low_return", "t1_close_return",
            "t1_mfe", "t1_mae", "entry_price", "entry_price_source", "entry_price_basis",
            "entry_date", "entry_time", "t1_open_price", "t1_high_price", "t1_low_price",
            "t1_close_price", "production_run_id", "candidate_snapshot_id",
            "return_status", "label_status", "label_version", "label_source",
            "market_data_source", "price_adjustment_mode", "trading_calendar_source",
            "t1_net_return", "slippage", "commission", "stamp_duty", "transfer_fee",
            "market_impact", "created_at", "updated_at",
        },
        "daily_candidates": {
            "id", "trade_date", "symbol", "stock_name", "decision", "open_price",
            "close_price", "high_price", "low_price", "volume", "amount", "pct_chg",
            "turnover_rate", "production_run_id", "candidate_snapshot_id", "data_version",
            "created_at", "updated_at", "hard_gate_status", "blockers",
        },
        "manual_execution_records": {
            "id", "production_run_id", "candidate_snapshot_id", "pick_id", "trade_date",
            "symbol", "status", "operator_id", "risk_fraction", "intended_price",
            "intended_quantity", "confirmation_note", "confirmed_at", "executed_price",
            "executed_quantity", "executed_at", "execution_note", "created_at", "updated_at",
        },
        "production_runs": {
            "production_run_id", "trade_date", "scan_session_id", "run_mode", "rule_version",
            "runner_version", "scanner_version", "schema_version", "scoring_config_hash",
            "input_payload_hash", "status", "error_message", "retry_command", "lineage_id",
            "created_at", "started_at", "completed_at", "updated_at",
        },
        "canonical_future_prices": {
            "symbol", "date", "open", "high", "low", "close", "volume", "amount",
            "source", "source_timestamp", "price_basis", "price_fact_hash", "payload", "created_at",
        },
        "canonical_historical_snapshots": {
            "snapshot_id", "lineage_id", "symbol", "trade_date", "signal_time", "source",
            "source_timestamp", "snapshot_version", "point_in_time", "available_at", "price_basis",
            "created_at",
        },
    }
    json_columns = {
        "picks": {
            "features", "source_layers", "risk_flags", "official_target_exclusion_reasons",
            "auxiliary_evidence_status", "information_coverage_audit_snapshot",
            "payload",
        },
        "returns": {"settlement_evidence", "payload"},
        "daily_candidates": {
            # raw_json is the persisted T-day source snapshot.  The other
            # Other candidate JSON columns are not inputs to the current
            # decision owner.
            "raw_json", "source_layers",
        },
        "manual_execution_records": {"risk_snapshot", "payload"},
        "production_runs": {"scoring_config_snapshot"},
        "canonical_future_prices": {"payload"},
        "canonical_historical_snapshots": {"payload"},
    }
    preserved_json_keys = (
        "stock_capital_flow", "industry_flow", "earnings_preview", "lhb",
        "shareholder_changes", "lockup_expiry", "future_buyers", "current_buyer",
        "tradingagents", "industry_reports", "stock_reports", "capital_flow",
        "fund_flow", "market", "risk", "raw", "candidate_features",
        "eligibility_snapshot", "factor_snapshot", "auxiliary_evidence_snapshot",
        "source_layers",
    )

    def compact_json(column: str) -> str:
        keys = ", ".join(f"'{key}'" for key in preserved_json_keys)
        return f"""(
            CASE
                WHEN jsonb_typeof("{column}") = 'object' THEN (
                    SELECT COALESCE(jsonb_object_agg(item.key, item.value), CAST('{{}}' AS jsonb))
                    FROM jsonb_each("{column}") AS item
                    WHERE jsonb_typeof(item.value) NOT IN ('object', 'array')
                       OR item.key IN ({keys})
                )
                ELSE COALESCE("{column}", CAST('{{}}' AS jsonb))
            END
        )"""

    def rows_for(db: Any, table: str) -> List[Dict[str, Any]]:
        column_types = {
            row["column_name"]: row["udt_name"]
            for row in db.execute(
                text(
                    """
                    SELECT column_name, udt_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = :table
                    """
                ),
                {"table": table},
            ).mappings()
        }
        columns = set(column_types)
        if not columns:
            return []
        selected = [
            f'"{column}"'
            for column in sorted(columns & (scalar_columns.get(table, set()) | json_columns.get(table, set())))
            if column_types[column] not in {"json", "jsonb"}
        ]
        selected.extend(
            f'{compact_json(column)} AS "{column}"'
            for column in sorted(columns & json_columns.get(table, set()))
            if column_types[column] in {"json", "jsonb"}
        )
        if not selected:
            return []
        clauses = []
        params: Dict[str, Any] = {}
        if "trade_date" in columns and start_date:
            clauses.append('"source_row"."trade_date" >= :start_date')
            params["start_date"] = start_date
        if "trade_date" in columns and end_date:
            clauses.append('"source_row"."trade_date" <= :end_date')
            params["end_date"] = end_date
        if (
            table == "daily_candidates"
            and {"production_run_id", "candidate_snapshot_id", "symbol"} <= columns
        ):
            clauses.append(
                """EXISTS (
                    SELECT 1
                    FROM "returns" AS "return_row"
                    WHERE "return_row"."production_run_id" = "source_row"."production_run_id"
                      AND "return_row"."candidate_snapshot_id" = "source_row"."candidate_snapshot_id"
                      AND "return_row"."symbol" = "source_row"."symbol"
                )"""
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = (
            ' ORDER BY "source_row"."trade_date", "source_row"."id"'
            if {"trade_date", "id"} <= columns
            else ' ORDER BY "source_row"."trade_date"'
            if "trade_date" in columns
            else ""
        )
        return [
            dict(row)
            for row in db.execute(
                text(f'SELECT {", ".join(selected)} FROM "{table}" AS "source_row"{where}{order}'),
                params,
            ).mappings()
        ]

    with engine.connect() as db:
        picks = rows_for(db, "picks")
        returns = rows_for(db, "returns")
        candidates = rows_for(db, "daily_candidates")
        executions = rows_for(db, "manual_execution_records")
        runs = rows_for(db, "production_runs")
        future_prices = rows_for(db, "canonical_future_prices")
        historical_snapshots = rows_for(db, "canonical_historical_snapshots")
    return {
        "picks": picks, "returns": returns, "daily_candidates": candidates,
        "manual_execution_records": executions, "production_runs": runs,
        "canonical_future_prices": future_prices,
        "canonical_historical_snapshots": historical_snapshots,
    }
