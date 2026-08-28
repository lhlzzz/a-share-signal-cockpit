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


def record_snapshot(snapshot: Dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            text("INSERT INTO snapshots (lineage_id, trade_date, payload) VALUES (:lineage_id, :trade_date, CAST(:payload AS jsonb)) ON CONFLICT (lineage_id) DO NOTHING"),
            {"lineage_id": snapshot["lineage_id"], "trade_date": snapshot["trade_date"], "payload": json.dumps(snapshot, ensure_ascii=False, default=str)},
        )


def record_decision(decision: Dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            text("INSERT INTO picks (trade_date, symbol, state, payload) VALUES (:trade_date, :symbol, :state, CAST(:payload AS jsonb))"),
            {
                "trade_date": decision["canonical_snapshot"]["trade_date"],
                "symbol": decision["symbol"],
                "state": decision["state"],
                "payload": json.dumps(decision, ensure_ascii=False, default=str),
            },
        )


def record_returns(trade_date: str, symbol: str, payload: Dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            text("INSERT INTO returns (trade_date, symbol, payload) VALUES (:trade_date, :symbol, CAST(:payload AS jsonb))"),
            {"trade_date": trade_date, "symbol": symbol, "payload": json.dumps(payload, ensure_ascii=False, default=str)},
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
            "id", "trade_date", "symbol", "decision", "rule_version", "stock_name",
            "production_run_id", "data_version", "created_at", "updated_at",
            "blockers", "source_layers",
        },
        "returns": {
            "id", "pick_id", "trade_date", "symbol", "t1_return", "t2_return",
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
