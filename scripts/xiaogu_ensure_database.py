#!/usr/bin/env python3
"""Ensure the configured xiaogu PostgreSQL database is reachable at startup."""
import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
REQUIRED_SCAN_SESSION_COLUMNS = {
    "source_id",
    "market_snapshot",
    "source_status",
    "source_counts",
    "source_diagnostics",
}
REQUIRED_RETURNS_COLUMNS = {
    "t1_return_close",
    "t1_return_high",
    "t1_vwap",
    "next_day_open_return",
    "next_day_high_return",
    "next_day_low_return",
    "next_day_gap_return",
    "next_day_drawdown",
    "high_to_close_retrace",
    "t1_open_return",
    "t1_high_return",
    "t1_low_return",
    "t1_close_return",
    "t1_mfe",
    "t1_mae",
    "t1_vwap_return",
    "t1_gap_return",
    "t1_net_return",
    "slippage",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "market_impact",
    "entry_price",
    "entry_price_source",
    "entry_price_basis",
    "entry_date",
    "entry_time",
    "t1_open_price",
    "t1_high_price",
    "t1_low_price",
    "t1_close_price",
    "label_status",
    "label_version",
    "label_source",
    "label_generated_at",
    "market_data_source",
    "price_adjustment_mode",
    "trading_calendar_source",
}
REQUIRED_LINEAGE_COLUMNS = {
    "scan_sessions": {"production_run_id"},
    "picks": {
        "production_run_id",
        "formal_rank_snapshot_id",
        "formal_rank_snapshot_version",
        "scoring_config_hash",
    },
    "returns": {
        "production_run_id",
        "candidate_snapshot_id",
        "return_status",
        "settlement_evidence",
    },
    "daily_candidates": {"production_run_id", "candidate_snapshot_id"},
    "signals": {"production_run_id"},
    "production_runs": {"production_run_id", "trade_date", "scan_session_id", "status"},
    "production_run_steps": {"production_run_id", "step_name", "status", "required"},
    "production_run_active": {
        "trade_date",
        "production_run_id",
        "candidate_snapshot_id",
        "active_pick_id",
    },
    "model_registry": {
        "model_id",
        "feature_version",
        "label_version",
        "status",
    },
    "production_alpha_health": {
        "health_date",
        "status",
        "kill_switch",
    },
}
REQUIRED_EXTENSIONS = {"vector"}


def database_target(database_url: str) -> tuple[str, int]:
    parsed = urlparse(database_url)
    host = parsed.hostname
    if not host:
        raise ValueError("DATABASE_URL must include a database host")
    return host, parsed.port or 5432


def is_database_reachable(target: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(target, timeout=1):
            return True
    except OSError:
        return False


def wait_for_database(target: tuple[str, int], timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_database_reachable(target):
            return True
        time.sleep(1)
    return is_database_reachable(target)


def has_required_schema(database_url: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'scan_sessions',
                      'picks',
                      'returns',
                      'daily_candidates',
                      'signals',
                      'production_runs',
                      'production_run_steps',
                      'production_run_active'
                  )
            """)
        )
        columns_by_table: dict[str, set[str]] = {}
        for table_name, column_name in rows:
            columns_by_table.setdefault(str(table_name), set()).add(str(column_name))

        extension_rows = connection.execute(
            text("""
                SELECT extname
                FROM pg_extension
                WHERE extname IN ('vector')
            """)
        )
        extensions = {str(extension_name) for (extension_name,) in extension_rows}
    return (
        REQUIRED_SCAN_SESSION_COLUMNS.issubset(columns_by_table.get('scan_sessions', set()))
        and REQUIRED_RETURNS_COLUMNS.issubset(columns_by_table.get('returns', set()))
        and all(
            required_columns.issubset(columns_by_table.get(table_name, set()))
            for table_name, required_columns in REQUIRED_LINEAGE_COLUMNS.items()
        )
        and REQUIRED_EXTENSIONS.issubset(extensions)
    )


def initialize_schema() -> None:
    result = subprocess.run(
        [sys.executable, "xiaogu_db.py", "init"],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Unable to initialize xiaogu schema: {detail}")


def ensure_schema(database_url: str) -> None:
    if has_required_schema(database_url):
        return
    initialize_schema()
    if not has_required_schema(database_url):
        raise RuntimeError("xiaogu database is missing required schema columns or extensions")


def ensure_database_ready(database_url: str, timeout_seconds: int = 60) -> bool:
    target = database_target(database_url)
    if not is_database_reachable(target) and not wait_for_database(target, timeout_seconds):
        return False
    ensure_schema(database_url)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure xiaogu PostgreSQL is ready")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        target = database_target(database_url)
        if ensure_database_ready(database_url, args.timeout):
            print(f"database ready: {target[0]}:{target[1]}")
            return 0
        print(f"database unavailable after {args.timeout}s: {target[0]}:{target[1]}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as exc:
        print(f"database startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
