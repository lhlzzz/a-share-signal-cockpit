#!/usr/bin/env python3
"""Ensure the configured xiaogu PostgreSQL database is reachable at startup."""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}
REQUIRED_SCAN_SESSION_COLUMNS = {
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
}
REQUIRED_EXTENSIONS = {"vector"}


def database_target(database_url: str) -> tuple[str, int]:
    parsed = urlparse(database_url)
    host = parsed.hostname
    if not host:
        raise ValueError("DATABASE_URL must include a database host")
    return host, parsed.port or 5432


def is_local_database(database_url: str) -> bool:
    host, _ = database_target(database_url)
    return host in LOCAL_DATABASE_HOSTS


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


def start_host_postgresql() -> bool:
    service = shutil.which("service")
    if not service:
        return False
    result = subprocess.run(
        [service, "postgresql", "start"],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def docker_is_ready() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    result = subprocess.run(
        [docker, "info"],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def start_docker_daemon(timeout_seconds: int) -> None:
    if docker_is_ready():
        return

    dockerd = shutil.which("dockerd")
    if not dockerd:
        raise RuntimeError("Docker daemon is not running and dockerd is unavailable")

    subprocess.Popen(
        [dockerd, "--host=unix:///var/run/docker.sock"],
        cwd=BASE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if docker_is_ready():
            return
        time.sleep(1)
    raise RuntimeError("Docker daemon did not become ready")


def compose_command() -> list[str]:
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [docker_compose]

    docker = shutil.which("docker")
    if docker:
        return [docker, "compose"]
    raise RuntimeError("Docker Compose is unavailable")


def start_local_database() -> None:
    result = subprocess.run(
        [*compose_command(), "up", "-d", "db"],
        cwd=BASE,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Unable to start xiaogu database: {detail}")


def has_required_schema(database_url: str) -> bool:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        rows = connection.execute(
            text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('scan_sessions', 'returns')
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
    if is_database_reachable(target):
        ensure_schema(database_url)
        return True

    if is_local_database(database_url):
        if start_host_postgresql() and wait_for_database(target, min(timeout_seconds, 15)):
            ensure_schema(database_url)
            return True
        start_docker_daemon(timeout_seconds)
        start_local_database()

    if not wait_for_database(target, timeout_seconds):
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
