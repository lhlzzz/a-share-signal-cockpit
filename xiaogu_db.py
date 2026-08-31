"""PostgreSQL persistence for production runs, snapshots, decisions, and outcomes."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterable, List

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    connect_args={"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))},
)
_ACTIVE_DB_CONNECTION: ContextVar[Any | None] = ContextVar("xiaogu_active_db_connection", default=None)


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
    migrate_historical_snapshot_identity()


def _exec_schema(statement: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement))


def _schema_error_already_exists(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "duplicate" in message


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


def _unique_index_columns(table_name: str) -> set[str]:
    columns: set[str] = set()
    with engine.connect() as db:
        rows = db.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = :table_name
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    for row in rows:
        definition = str(row[0] or "")
        if "UNIQUE" not in definition.upper():
            continue
        start = definition.find("(")
        end = definition.find(")", start + 1)
        if start < 0 or end < 0:
            continue
        for raw in definition[start + 1:end].split(","):
            name = raw.strip().strip('"')
            if name:
                columns.add(name)
    return columns


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
            "paper_observation_contract_version", "paper_only", "live_order", "payload",
        ),
        "returns": ("decision_id",),
        "canonical_historical_snapshots": (
            "snapshot_id", "lineage_id", "symbol", "trade_date", "signal_time",
            "source", "source_timestamp", "snapshot_version", "point_in_time",
            "available_at", "price_basis", "payload", "created_at",
        ),
        "canonical_future_prices": ("symbol", "date", "source", "price_basis", "price_fact_hash"),
        "trading_calendar": ("trade_date", "is_trading_day", "source", "payload"),
    }
    required_unique = {
        "snapshots": ("snapshot_id",),
        "picks": ("decision_id",),
        "paper_observations": ("paper_signal_id", "decision_id"),
        "canonical_historical_snapshots": ("snapshot_id",),
        "trading_calendar": ("trade_date",),
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
    }
    required_primary_key = {
        "snapshots": ("snapshot_id",),
        "canonical_historical_snapshots": ("snapshot_id",),
        "paper_observations": ("paper_signal_id",),
        "picks": ("id",),
        "returns": ("id",),
        "canonical_future_prices": ("symbol", "date"),
        "trading_calendar": ("trade_date",),
    }
    tables = {}
    ok = True
    for table_name, columns in required_columns.items():
        present_columns = _table_columns(table_name)
        checks = _check_constraints(table_name)
        unique_cols = set(_constraint_columns(table_name, "UNIQUE")) | set(
            _constraint_columns(table_name, "PRIMARY KEY")
        ) | _unique_index_columns(table_name)
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
        column_audit = {column: _exists_label(column in present_columns) for column in columns}
        unique_audit = {column: _exists_label(column in unique_cols) for column in required_unique.get(table_name, ())}
        index_audit = {name: _exists_label(name in index_names) for name in required_indexes.get(table_name, ())}
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
        tables[table_name] = {
            "columns": column_audit,
            "indexes": index_audit,
            "unique": unique_audit,
            "checks": checks,
            "primary_key": {"columns": list(primary_key), "status": pk_status},
            "foreign_keys": fk_audit,
        }
        ok = ok and all(value == "EXISTS" for value in column_audit.values())
        ok = ok and all(value == "EXISTS" for value in unique_audit.values())
        ok = ok and all(value == "EXISTS" for value in index_audit.values())
        ok = ok and pk_status == "EXISTS"
        if table_name == "returns":
            ok = ok and fk_audit.get("decision_id->picks.decision_id") == "EXISTS"
        if table_name == "paper_observations":
            ok = ok and fk_audit.get("decision_id->picks.decision_id") == "EXISTS"
            paper_only_check = str(checks.get("paper_observations_paper_only_check") or "").replace("(", "").replace(")", "").strip()
            live_order_check = str(checks.get("paper_observations_live_order_check") or "").replace("(", "").replace(")", "").strip()
            ok = ok and paper_only_check == "CHECK paper_only"
            ok = ok and live_order_check == "CHECK NOT live_order"
    anomalies = {
        "picks_missing_decision_id": _count_unbound_decision_ids("picks"),
        "returns_missing_decision_id": _count_unbound_decision_ids("returns"),
        "returns_decision_fk_conflicts": _count_returns_decision_fk_conflicts(),
        "historical_snapshots_missing_snapshot_id": _count_unresolved_snapshot_ids("canonical_historical_snapshots"),
    }
    if anomalies["returns_decision_fk_conflicts"] > 0:
        tables["returns"]["foreign_keys"]["decision_id->picks.decision_id"] = "CONFLICT"
        ok = False
    return {
        "ok": ok,
        "tables": tables,
        "historical_anomalies": anomalies,
    }


def ensure_production_schema() -> None:
    """ADD-only production identity. ALTER failure raises and blocks production."""
    statements = [
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source_time TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS symbol TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS payload_hash TEXT",
        "ALTER TABLE canonical_historical_snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS decision_id TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS state TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS position_state TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS payload JSONB",
        """CREATE TABLE IF NOT EXISTS paper_observations (
            paper_signal_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            lineage_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_time TIMESTAMPTZ NOT NULL,
            reference_price DOUBLE PRECISION NOT NULL,
            paper_observation_state TEXT NOT NULL,
            paper_position_state TEXT NOT NULL,
            alpha_name TEXT NOT NULL,
            alpha_version TEXT,
            feature_version TEXT,
            decision_version TEXT NOT NULL,
            cost_model_version TEXT NOT NULL,
            paper_observation_contract_version TEXT NOT NULL,
            paper_only BOOLEAN NOT NULL DEFAULT TRUE,
            live_order BOOLEAN NOT NULL DEFAULT FALSE,
            payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (decision_id)
        )""",
        """CREATE TABLE IF NOT EXISTS trading_calendar (
            trade_date DATE PRIMARY KEY,
            is_trading_day BOOLEAN NOT NULL,
            source TEXT NOT NULL,
            source_timestamp TIMESTAMPTZ,
            payload JSONB NOT NULL DEFAULT CAST('{}' AS jsonb),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "ALTER TABLE returns ADD COLUMN IF NOT EXISTS decision_id TEXT",
        "ALTER TABLE returns ADD COLUMN IF NOT EXISTS payload JSONB",
        "ALTER TABLE canonical_future_prices ADD COLUMN IF NOT EXISTS price_fact_hash TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_returns_decision_id ON returns (decision_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_returns_decision_date ON returns (decision_id, trade_date) WHERE decision_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_paper_observations_signal_time ON paper_observations(signal_time)",
        "CREATE INDEX IF NOT EXISTS idx_trading_calendar_open_days ON trading_calendar(trade_date) WHERE is_trading_day",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_lineage_id ON snapshots (lineage_id)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_date_symbol ON snapshots (trade_date, symbol)",
        "CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_lineage_id ON canonical_historical_snapshots (lineage_id)",
        "CREATE INDEX IF NOT EXISTS idx_canonical_historical_snapshots_date ON canonical_historical_snapshots (trade_date, symbol)",
    ]
    for statement in statements:
        _exec_schema(statement)
    with engine.begin() as db:
        legacy_rows = [
            dict(row) for row in db.execute(
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
    _ensure_snapshot_primary_key("snapshots")
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
    try:
        _exec_schema("ALTER TABLE picks ADD CONSTRAINT picks_decision_id_key UNIQUE (decision_id)")
    except SQLAlchemyError as exc:
        if not _schema_error_already_exists(exc):
            raise
    try:
        _exec_schema(
            "ALTER TABLE paper_observations ADD CONSTRAINT "
            "paper_observations_decision_id_fkey FOREIGN KEY (decision_id) "
            "REFERENCES picks (decision_id)"
        )
    except SQLAlchemyError as exc:
        if not _schema_error_already_exists(exc):
            raise
    for statement in (
        "ALTER TABLE paper_observations ADD CONSTRAINT "
        "paper_observations_paper_only_check CHECK (paper_only)",
        "ALTER TABLE paper_observations ADD CONSTRAINT "
        "paper_observations_live_order_check CHECK (NOT live_order)",
    ):
        try:
            _exec_schema(statement)
        except SQLAlchemyError as exc:
            if not _schema_error_already_exists(exc):
                raise
    try:
        _exec_schema(
            "ALTER TABLE returns ADD CONSTRAINT returns_decision_id_fkey "
            "FOREIGN KEY (decision_id) REFERENCES picks (decision_id)"
        )
    except SQLAlchemyError as exc:
        if _schema_error_already_exists(exc):
            pass
        else:
            raise
    audit = audit_production_schema()
    if not audit["ok"]:
        raise RuntimeError("PRODUCTION_SCHEMA_CONTRACT_FAILED")


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
    cleaned = {
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "updated_at"}
    }
    return hashlib.sha256(
        json.dumps(cleaned, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


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
    """Recover snapshot_id from payload identity. Never copy lineage_id. Never rewrite payload."""
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
    _ensure_snapshot_primary_key("canonical_historical_snapshots")
    return {
        "recovered": recovered,
        "unresolved": unresolved,
        "conflicts": conflicts,
        "primary_key": _constraint_columns("canonical_historical_snapshots", "PRIMARY KEY"),
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
        computed_hash = snapshot_payload_hash(payload)
        stored_hash = str(row.get("payload_hash") or payload.get("payload_hash") or "")
        if stored_hash and stored_hash != computed_hash:
            return False
        if payload_hash:
            if computed_hash != payload_hash:
                return False
        return True
    except SQLAlchemyError:
        return False


def fetch_persisted_canonical_snapshots(trade_date: str) -> List[Dict[str, Any]]:
    """Load DB-verified canonical snapshots for one T-day."""
    ensure_production_schema()
    try:
        from xiaogu_forward_snapshot import select_unique_canonical_snapshots, validate_and_build_canonical_snapshot
        with engine.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    text(
                        """
                        SELECT lineage_id, trade_date, payload, snapshot_id, source, source_time, symbol
                        FROM snapshots
                        WHERE trade_date = CAST(:trade_date AS date)
                        ORDER BY source_time DESC NULLS LAST, created_at DESC
                        """
                    ),
                    {"trade_date": trade_date},
                ).mappings()
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
        return select_unique_canonical_snapshots(snapshots, trade_date=trade_date)
    except SQLAlchemyError:
        return []


def record_snapshot(snapshot: Dict[str, Any]) -> None:
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
    provided_hash = str(snapshot.get("payload_hash") or "")
    if provided_hash and provided_hash != computed_hash:
        raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")
    params = {
        "lineage_id": lineage_id,
        "trade_date": snapshot["trade_date"],
        "payload": json.dumps(snapshot, ensure_ascii=False, default=str),
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
        existing = db.execute(
            text("SELECT payload, payload_hash FROM snapshots WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": snapshot_id},
        ).mappings().first()
        if existing:
            stored_payload = existing.get("payload")
            stored_hash = str(existing.get("payload_hash") or "")
            if not stored_hash:
                stored_hash = snapshot_payload_hash(stored_payload)
            if stored_hash != str(params["payload_hash"]):
                raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")
            return
        db.execute(
            text(
                f"INSERT INTO snapshots ({', '.join(fields)}) VALUES ({placeholders}) "
                "ON CONFLICT (snapshot_id) DO NOTHING"
            ),
            params,
        )
        stored = db.execute(
            text("SELECT payload, payload_hash FROM snapshots WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": snapshot_id},
        ).mappings().first()
        stored_hash = snapshot_payload_hash(stored.get("payload")) if stored else ""
        if stored and stored.get("payload_hash"):
            stored_hash = str(stored["payload_hash"])
        if not stored or stored_hash != str(params["payload_hash"]):
            raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")


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
    decision = {
        **decision,
        **identity,
        "canonical_snapshot": canonical,
    }
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


def record_snapshot_and_decision(snapshot: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """Persist one snapshot and its decision in the same PostgreSQL transaction."""
    ensure_production_schema()
    with engine.begin() as db:
        token = _ACTIVE_DB_CONNECTION.set(db)
        try:
            record_snapshot(snapshot)
            record_decision(decision)
        finally:
            _ACTIVE_DB_CONNECTION.reset(token)


def record_returns(trade_date: str, symbol: str, payload: Dict[str, Any], decision_id: str = "") -> None:
    ensure_production_schema()
    decision_id = decision_id or payload.get("decision_id") or payload.get("id") or ""
    if not str(decision_id).strip():
        raise ValueError("DECISION_ID_REQUIRED")
    columns = _table_columns("returns")
    fields = ["trade_date", "symbol", "payload"]
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    params = {
        "trade_date": trade_date,
        "symbol": symbol,
        "payload": serialized_payload,
        "decision_id": decision_id,
    }
    if "decision_id" in columns:
        fields.append("decision_id")
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
            existing = db.execute(
                text("SELECT payload FROM canonical_historical_snapshots WHERE snapshot_id = :snapshot_id"),
                {"snapshot_id": row["snapshot_id"]},
            ).mappings().first()
            if existing:
                stored = existing.get("payload")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                if snapshot_payload_identity(stored) != snapshot_payload_identity(json.loads(row["payload"])):
                    raise ValueError("SNAPSHOT_IDENTITY_CONFLICT")
                continue
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
                    ON CONFLICT (snapshot_id) DO NOTHING
                    """
                ),
                row,
            )


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


def insert_scan_session(**payload: Any) -> int:
    with get_db() as db:
        row = db.execute(
            text("INSERT INTO production_runs (trade_date, status, payload) VALUES (:trade_date, 'SNAPSHOT_CAPTURED', CAST(:payload AS jsonb)) RETURNING id"),
            {"trade_date": payload["trade_date"], "payload": json.dumps(payload, ensure_ascii=False, default=str)},
        ).fetchone()
    return int(row[0])


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
    params = {
        **observation,
        "payload": json.dumps(observation, ensure_ascii=False, default=str),
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
                     paper_observation_contract_version, paper_only, live_order, payload)
                VALUES (:paper_signal_id, :decision_id, :snapshot_id, :lineage_id, :symbol,
                        CAST(:signal_time AS timestamptz), :reference_price,
                        :paper_observation_state, :paper_position_state, :alpha_name,
                        :alpha_version, :feature_version, :decision_version,
                        :cost_model_version, :paper_observation_contract_version,
                        :paper_only, :live_order, CAST(:payload AS jsonb))
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
        pick = db.execute(
            text("SELECT payload FROM picks WHERE decision_id = :decision_id"),
            {"decision_id": wanted},
        ).mappings().first()
        if not pick:
            raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
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
        row = db.execute(
            text("SELECT payload FROM snapshots WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": identity["snapshot_id"]},
        ).mappings().first()
    if not row:
        raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_UNAVAILABLE")
    snapshot = row.get("payload")
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if any(
        str(snapshot.get(field) or "") != str(expected)
        for field, expected in identity.items()
    ):
        raise RuntimeError("POSITION_REVIEW_BLOCKED:SNAPSHOT_IDENTITY_CONFLICT")
    from xiaogu_forward_snapshot import validate_and_build_canonical_snapshot

    return validate_and_build_canonical_snapshot(snapshot)


def paper_observation_exists(paper_signal_id: str) -> bool:
    wanted = str(paper_signal_id or "").strip()
    if not wanted:
        return False
    ensure_production_schema()
    with engine.connect() as db:
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


def fetch_open_positions() -> List[Dict[str, Any]]:
    """Latest DB decision per symbol that still has a LONG position."""
    columns = _table_columns("picks")
    if "state" in columns and "decision" in columns:
        select_state = "COALESCE(state, decision) AS state"
    elif "state" in columns:
        select_state = "state"
    else:
        select_state = "decision AS state"
    select_payload = ", payload" if "payload" in columns else ""
    select_decision_id = ", decision_id" if "decision_id" in columns else ""
    select_position_state = ", position_state" if "position_state" in columns else ""
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (symbol) id, trade_date, symbol, {select_state}{select_payload}{select_decision_id}{select_position_state}
                    FROM picks
                    ORDER BY symbol, id DESC
                    """
                )
            ).mappings()
        ]
    positions = []
    for row in rows:
        action = str(row.get("state") or row.get("decision") or "")
        position_state = str(row.get("position_state") or "")
        if position_state != "LONG":
            continue
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        positions.append({
            **payload,
            "id": row.get("id"),
            "trade_date": str(row.get("trade_date") or payload.get("trade_date") or ""),
            "symbol": str(row.get("symbol") or payload.get("symbol") or ""),
            "state": action,
            "action": payload.get("action") or action,
            "previous_action": payload.get("action"),
            "position_state": position_state,
            "decision": payload.get("action") or action,
            "decision_id": payload.get("decision_id") or row.get("decision_id") or row.get("id"),
        })
    return positions


def fetch_position_state(symbol: str) -> str | None:
    """Read the latest explicit PostgreSQL position state for one symbol."""
    wanted = str(symbol or "").strip()
    if not wanted:
        return None
    columns = _table_columns("picks")
    if "position_state" not in columns:
        raise RuntimeError("POSITION_STATE_SCHEMA_MISSING")
    with engine.connect() as db:
        row = db.execute(
            text(
                """
                SELECT position_state
                FROM picks
                WHERE symbol = :symbol
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"symbol": wanted},
        ).mappings().first()
    if not row or row.get("position_state") in (None, ""):
        return None
    state = str(row["position_state"]).upper()
    if state not in {"FLAT", "LONG"}:
        raise ValueError(f"INVALID_POSITION_STATE:{state}")
    return state


def count_trading_days(start: Any, end: Any) -> int:
    """Count only independently persisted A-share calendar facts."""
    start_date = start.isoformat() if hasattr(start, "isoformat") else str(start)
    end_date = end.isoformat() if hasattr(end, "isoformat") else str(end)
    if start_date >= end_date:
        return 0
    with engine.connect() as db:
        return int(db.execute(
            text(
                """
                SELECT count(*)
                FROM trading_calendar
                WHERE trade_date > CAST(:start_date AS date)
                  AND trade_date <= CAST(:end_date AS date)
                  AND is_trading_day
                """
            ),
            {"start_date": start_date, "end_date": end_date},
        ).scalar_one())


def is_trading_date(value: Any) -> bool:
    """Read the independently persisted A-share calendar owner."""
    wanted = value.isoformat() if hasattr(value, "isoformat") else str(value)
    with engine.connect() as db:
        return bool(db.execute(
            text(
                "SELECT 1 FROM trading_calendar "
                "WHERE trade_date = CAST(:trade_date AS date) AND is_trading_day LIMIT 1"
            ),
            {"trade_date": wanted},
        ).scalar())


def record_trading_calendar(records: Iterable[Dict[str, Any]]) -> None:
    """Persist immutable A-share trade-date facts from the calendar source."""
    ensure_production_schema()
    with get_db() as db:
        for record in records:
            trade_date = str(record.get("trade_date") or record.get("date") or "")[:10]
            source = str(record.get("source") or "").strip()
            if not trade_date or not source or record.get("is_trading_day") is None:
                raise ValueError("TRADING_CALENDAR_IDENTITY_REQUIRED")
            payload = {
                **record,
                "trade_date": trade_date,
                "is_trading_day": bool(record["is_trading_day"]),
                "source": source,
            }
            existing = db.execute(
                text(
                    "SELECT is_trading_day, source FROM trading_calendar "
                    "WHERE trade_date = CAST(:trade_date AS date)"
                ),
                {"trade_date": trade_date},
            ).mappings().first()
            if existing:
                if (
                    bool(existing["is_trading_day"]) != payload["is_trading_day"]
                    or str(existing["source"]) != source
                ):
                    raise ValueError("TRADING_CALENDAR_CONFLICT")
                continue
            db.execute(
                text(
                    """
                    INSERT INTO trading_calendar
                        (trade_date, is_trading_day, source, source_timestamp, payload)
                    VALUES (
                        CAST(:trade_date AS date), :is_trading_day, :source,
                        CAST(NULLIF(:source_timestamp, '') AS timestamptz),
                        CAST(:payload AS jsonb)
                    )
                    """
                ),
                {
                    **payload,
                    "source_timestamp": str(record.get("source_timestamp") or ""),
                    "payload": json.dumps(payload, ensure_ascii=False, default=str),
                },
            )


def refresh_a_share_trading_calendar(start_date: str, end_date: str) -> int:
    """Load Baostock's official A-share trading-day feed into the DB owner."""
    import baostock as bs

    login = bs.login()
    if str(getattr(login, "error_code", "")) != "0":
        raise RuntimeError(f"TRADING_CALENDAR_SOURCE_UNAVAILABLE:{getattr(login, 'error_msg', '')}")
    try:
        result = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        if str(getattr(result, "error_code", "")) != "0":
            raise RuntimeError(f"TRADING_CALENDAR_SOURCE_UNAVAILABLE:{getattr(result, 'error_msg', '')}")
        rows = []
        while result.next():
            values = result.get_row_data()
            rows.append({
                "trade_date": values[0],
                "is_trading_day": str(values[1]) == "1",
                "source": "baostock_trade_dates",
            })
        record_trading_calendar(rows)
        return len(rows)
    finally:
        bs.logout()


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
        "production_runs": ("production_run_id", "trade_date"),
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
            "input_payload_hash", "status", "error_message", "retry_command",
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
        "production_runs": {"scoring_config_snapshot", "payload"},
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
