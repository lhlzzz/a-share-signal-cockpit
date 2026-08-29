"""PostgreSQL persistence for production runs, snapshots, decisions, and outcomes."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List

from sqlalchemy import create_engine, text

engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    connect_args={"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))},
)


@contextmanager
def get_db():
    with engine.begin() as connection:
        yield connection


def init_db(sql_path: str = "scripts/xiaogu_db_init.sql") -> None:
    with engine.begin() as connection:
        connection.execute(text(open(sql_path, encoding="utf-8").read()))
    ensure_production_schema()


def ensure_production_schema() -> None:
    """ADD-only production columns and indexes. Never drop or rewrite historical values."""
    statements = [
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS snapshot_id TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source TEXT",
        "ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS source_time TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS decision_id TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS state TEXT",
        "ALTER TABLE picks ADD COLUMN IF NOT EXISTS payload JSONB",
        "ALTER TABLE returns ADD COLUMN IF NOT EXISTS decision_id TEXT",
        "ALTER TABLE returns ADD COLUMN IF NOT EXISTS payload JSONB",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_picks_decision_id ON picks (decision_id) WHERE decision_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_returns_decision_id ON returns (decision_id)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_trade_date ON snapshots (trade_date)",
    ]
    for statement in statements:
        try:
            with engine.begin() as connection:
                connection.execute(text(statement))
        except Exception:
            continue
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE returns ADD CONSTRAINT returns_decision_id_fkey "
                "FOREIGN KEY (decision_id) REFERENCES picks (decision_id)"
            ))
    except Exception:
        pass


def verify_persisted_snapshot(
    snapshot_id: str = "",
    lineage_id: str = "",
    trade_date: str = "",
    source: str = "",
    source_time: str = "",
) -> bool:
    """Prove the canonical snapshot exists in PostgreSQL. Local files are not persistence."""
    if not lineage_id and not snapshot_id:
        return False
    try:
        ensure_production_schema()
        columns = _table_columns("snapshots")
        if not columns:
            return False
        clauses = []
        params: Dict[str, Any] = {}
        if "lineage_id" in columns and lineage_id:
            clauses.append("lineage_id = :lineage_id")
            params["lineage_id"] = lineage_id
        if "snapshot_id" in columns and snapshot_id:
            clauses.append("(snapshot_id = :snapshot_id OR payload->>'snapshot_id' = :snapshot_id)")
            params["snapshot_id"] = snapshot_id
        if "trade_date" in columns and trade_date:
            clauses.append("trade_date = CAST(:trade_date AS date)")
            params["trade_date"] = trade_date
        if not clauses:
            return False
        with engine.connect() as db:
            row = db.execute(
                text(f"SELECT payload, lineage_id FROM snapshots WHERE {' AND '.join(clauses)} LIMIT 1"),
                params,
            ).mappings().first()
        if not row:
            return False
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        if snapshot_id and str(payload.get("snapshot_id") or row.get("snapshot_id") or "") not in {snapshot_id, ""}:
            if str(payload.get("snapshot_id") or "") != snapshot_id:
                return False
        if source and str(payload.get("source") or "") not in {source, ""}:
            if str(payload.get("source") or "") != source:
                return False
        if source_time and str(payload.get("source_time") or "") not in {source_time, ""}:
            stored = str(payload.get("source_time") or "")
            if stored and stored != source_time:
                return False
        return True
    except Exception:
        return False


def fetch_persisted_canonical_snapshots(trade_date: str) -> List[Dict[str, Any]]:
    """Load DB-verified canonical snapshots for one T-day."""
    try:
        ensure_production_schema()
        from xiaogu_forward_snapshot import CanonicalSnapshot, validate_and_build_canonical_snapshot
        with engine.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    text(
                        """
                        SELECT lineage_id, trade_date, payload, snapshot_id, source, source_time
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
            try:
                snapshots.append(validate_and_build_canonical_snapshot(payload, target_trade_date=trade_date))
            except (TypeError, ValueError):
                continue
        return snapshots
    except Exception:
        return []


def record_snapshot(snapshot: Dict[str, Any]) -> None:
    ensure_production_schema()
    columns = _table_columns("snapshots")
    fields = ["lineage_id", "trade_date", "payload"]
    params = {
        "lineage_id": snapshot["lineage_id"],
        "trade_date": snapshot["trade_date"],
        "payload": json.dumps(snapshot, ensure_ascii=False, default=str),
    }
    if "snapshot_id" in columns:
        fields.append("snapshot_id")
        params["snapshot_id"] = snapshot.get("snapshot_id")
    if "source" in columns:
        fields.append("source")
        params["source"] = snapshot.get("source")
    if "source_time" in columns:
        fields.append("source_time")
        params["source_time"] = snapshot.get("source_time")
    placeholders = ", ".join(
        ":payload" if field == "payload" else f":{field}" if field != "payload" else ":payload"
        for field in fields
    )
    placeholders = ", ".join(
        "CAST(:payload AS jsonb)" if field == "payload" else f":{field}"
        for field in fields
    )
    with get_db() as db:
        db.execute(
            text(
                f"INSERT INTO snapshots ({', '.join(fields)}) VALUES ({placeholders}) "
                "ON CONFLICT (lineage_id) DO NOTHING"
            ),
            params,
        )


def record_decision(decision: Dict[str, Any]) -> None:
    ensure_production_schema()
    columns = _table_columns("picks")
    fields = ["trade_date", "symbol", "state", "payload"]
    canonical = decision.get("canonical_snapshot") or {}
    params = {
        "trade_date": canonical.get("trade_date") or decision.get("date") or decision.get("trade_date"),
        "symbol": decision.get("symbol") or canonical.get("symbol"),
        "state": decision.get("action") or decision["state"],
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
    if "payload" not in columns and "payload" in fields:
        fields.remove("payload")
    with get_db() as db:
        db.execute(
            text(
                f"INSERT INTO picks ({', '.join(fields)}) VALUES ("
                + ", ".join("CAST(:payload AS jsonb)" if field == "payload" else f":{field}" for field in fields)
                + ")"
            ),
            params,
        )


def record_returns(trade_date: str, symbol: str, payload: Dict[str, Any], decision_id: str = "") -> None:
    ensure_production_schema()
    columns = _table_columns("returns")
    fields = ["trade_date", "symbol", "payload"]
    params = {
        "trade_date": trade_date,
        "symbol": symbol,
        "payload": json.dumps(payload, ensure_ascii=False, default=str),
        "decision_id": decision_id or payload.get("decision_id") or payload.get("id") or "",
    }
    if "decision_id" in columns:
        fields.append("decision_id")
    with get_db() as db:
        db.execute(
            text(
                f"INSERT INTO returns ({', '.join(fields)}) VALUES ("
                + ", ".join("CAST(:payload AS jsonb)" if field == "payload" else f":{field}" for field in fields)
                + ")"
            ),
            params,
        )


def record_canonical_historical_snapshot(snapshot: Dict[str, Any]) -> None:
    """Persist one immutable PIT historical snapshot when PostgreSQL is enabled."""
    record_canonical_historical_snapshots([snapshot])


def record_canonical_historical_snapshots(snapshots: Iterable[Dict[str, Any]]) -> None:
    """Persist immutable PIT snapshots in one idempotent transaction."""
    rows = [
        {
            "lineage_id": snapshot["lineage_id"],
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
        for snapshot in snapshots
    ]
    if not rows:
        return
    with get_db() as db:
        db.execute(
            text(
                """
                INSERT INTO canonical_historical_snapshots
                    (lineage_id, symbol, trade_date, signal_time, source,
                     source_timestamp, snapshot_version, point_in_time,
                     available_at, price_basis, payload)
                VALUES (:lineage_id, :symbol, :trade_date, CAST(:signal_time AS timestamptz),
                        :source, CAST(:source_timestamp AS timestamptz), :snapshot_version,
                        :point_in_time, CAST(:available_at AS timestamptz), :price_basis,
                        CAST(:payload AS jsonb))
                ON CONFLICT (lineage_id) DO NOTHING
                """
            ),
            rows,
        )


def record_canonical_future_prices(bars: Iterable[Dict[str, Any]]) -> None:
    """Upsert source OHLC facts; target calculations remain in Python owners."""
    with get_db() as db:
        for bar in bars:
            db.execute(
                text(
                    """
                    INSERT INTO canonical_future_prices
                        (symbol, date, open, high, low, close, volume, amount,
                         source, source_timestamp, price_basis, payload)
                    VALUES (:symbol, :date, :open, :high, :low, :close, :volume,
                            :amount, :source, CAST(NULLIF(:source_timestamp, '') AS timestamptz),
                            :price_basis, CAST(:payload AS jsonb))
                    ON CONFLICT (symbol, date) DO UPDATE SET
                        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                        close = EXCLUDED.close, volume = EXCLUDED.volume, amount = EXCLUDED.amount,
                        source = EXCLUDED.source, source_timestamp = EXCLUDED.source_timestamp,
                        price_basis = EXCLUDED.price_basis, payload = EXCLUDED.payload
                    """
                ),
                {**bar, "payload": json.dumps(bar, ensure_ascii=False, default=str)},
            )


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
    with engine.connect() as db:
        rows = [
            dict(row)
            for row in db.execute(
                text(
                    f"""
                    SELECT DISTINCT ON (symbol) id, trade_date, symbol, {select_state}{select_payload}
                    FROM picks
                    ORDER BY symbol, id DESC
                    """
                )
            ).mappings()
        ]
    positions = []
    for row in rows:
        action = str(row.get("state") or row.get("decision") or "")
        if action not in {"BUY", "HOLD", "REDUCE"}:
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
            "previous_action": payload.get("action") or action,
            "position_state": payload.get("position_state") or "LONG",
            "decision": payload.get("action") or action,
            "decision_id": payload.get("decision_id") or row.get("decision_id") or row.get("id"),
        })
    return positions


def fetch_position_outcome(symbol: str, decision_id: str = "") -> Dict[str, Any]:
    """Read the 5D outcome bound to one decision_id. Symbol-only lookup is forbidden."""
    if not decision_id:
        return {"status": "OUTCOME_NOT_BOUND", "symbol": symbol}
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
    return [{"decision": decision, "outcomes": [
        outcome for outcome in outcomes
        if outcome.get("symbol") == decision.get("symbol")
        and outcome.get("trade_date") == decision.get("trade_date")
    ]} for decision in decisions]


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
                "picks": "Historical decisions and paper picks",
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
    return {"read_only": True, "tables": report}


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
            "source", "source_timestamp", "price_basis", "payload", "created_at",
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
                    SELECT COALESCE(jsonb_object_agg(item.key, item.value), '{{}}'::jsonb)
                    FROM jsonb_each("{column}") AS item
                    WHERE jsonb_typeof(item.value) NOT IN ('object', 'array')
                       OR item.key IN ({keys})
                )
                ELSE COALESCE("{column}", '{{}}'::jsonb)
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
    return {
        "picks": picks, "returns": returns, "daily_candidates": candidates,
        "manual_execution_records": executions, "production_runs": runs,
        "canonical_future_prices": future_prices,
    }
