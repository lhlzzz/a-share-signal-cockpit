import asyncio
import json
from pathlib import Path

from scripts import xiaogu_ensure_database as database_startup
import xiaogu_api
import xiaogu_forward_d1_1450_runner_v0_1 as runner
import xiaogu_scheduler as scheduler


def test_morning_scan_runs_api_scanner_without_cdp(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )

    scheduler.job_morning_scan()

    assert len(calls) == 1
    args, job_name = calls[0]
    assert job_name == "morning_scan"
    assert args[:2] == [scheduler.PYTHON, scheduler.API_SCANNER]
    assert "--cdp-url" not in args
    assert "--open-required-cdp-tabs" not in args
    assert args[args.index("--output-dir") + 1].endswith("/eastmoney_scan_morning")


def test_afternoon_scan_runs_api_scanner_before_runner(monkeypatch):
    """Afternoon pick ownership is daily_pipeline.sh (scan+sszcw+runner+backfill+evolve)."""
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )

    scheduler.job_afternoon_scan_and_pick()

    assert [job_name for _, job_name in calls] == ["afternoon_daily_pipeline"]
    args, _ = calls[0]
    assert args[:2] == ["bash", "daily_pipeline.sh"]
    assert len(args) == 3  # bash daily_pipeline.sh YYYY-MM-DD


def test_result_fill_uses_t1_validation_pipeline_for_pick_id_and_evolve(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "previous_trading_day",
        lambda d: d.__class__(2026, 7, 21),
    )
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )

    scheduler.job_result_fill()

    assert [job_name for _, job_name in calls] == ["result_fill", "t1_validation_and_self_evolve"]
    t1_args, _ = calls[1]
    assert t1_args[:2] == ["bash", "daily_pipeline.sh"]
    assert "--manual-return-backfill" in t1_args
    assert "--validate-on" in t1_args


def test_signal_effectiveness_runs_safe_self_evolve_not_blind_apply_weights(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )
    monkeypatch.delenv("XIAOGU_WEIGHT_AUTO_TUNE_LEGACY", raising=False)

    scheduler.job_signal_effectiveness()

    assert [job_name for _, job_name in calls] == ["signal_effectiveness", "safe_self_evolve"]
    evolve_args, _ = calls[1]
    assert "scripts/xiaogu_safe_self_evolve.py" in evolve_args
    assert "--apply-if-ready" in evolve_args
    assert "--apply-weights" not in " ".join(str(a) for a in evolve_args)


def test_scan_summary_paths_excludes_legacy_cdp_source(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LIVE_SCAN_ROOT", tmp_path)
    trade_date = "2026-07-14"

    api_summary = tmp_path / trade_date / "eastmoney_scan_afternoon" / runner.SCAN_SUMMARY_NAME
    api_summary.parent.mkdir(parents=True)
    api_summary.write_text(
        json.dumps({"source": "eastmoney_api_scan_v2", "pipeline_version": "v2_scanner_api"}),
        encoding="utf-8",
    )

    legacy_summary = tmp_path / trade_date / "eastmoney_web_tabs_scan_v0_1" / runner.SCAN_SUMMARY_NAME
    legacy_summary.parent.mkdir(parents=True)
    legacy_summary.write_text(
        json.dumps({"source": "eastmoney_web_tabs", "pipeline_version": "eastmoney_web_tabs_scan_v0_1"}),
        encoding="utf-8",
    )

    assert runner.scan_summary_paths(trade_date) == [api_summary]


def test_scheduler_uses_single_database_startup_entrypoint(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )

    assert scheduler.ensure_database_ready() is True
    assert calls == [
        ([scheduler.PYTHON, scheduler.DATABASE_STARTUP], "database_startup"),
    ]


def test_database_startup_validates_schema_when_database_is_already_reachable(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: True)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: calls.append(database_url))

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu",
    )
    assert calls == ["postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"]


def test_database_startup_schema_check_requires_returns_execution_columns(monkeypatch):
    def column_rows(required_returns_columns=None):
        rows = [('scan_sessions', column) for column in database_startup.REQUIRED_SCAN_SESSION_COLUMNS]
        rows.extend(
            ('returns', column)
            for column in (required_returns_columns or database_startup.REQUIRED_RETURNS_COLUMNS)
        )
        return rows

    class FakeConnection:
        extensions = [('vector',)]
        returns_columns = database_startup.REQUIRED_RETURNS_COLUMNS

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            query_text = str(query)
            if 'pg_extension' in query_text:
                return self.extensions
            return column_rows(self.returns_columns)

    class FakeEngine:
        connection_class = FakeConnection

        def connect(self):
            return self.connection_class()

    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: FakeEngine())

    assert database_startup.has_required_schema('postgresql://xiaogu:xiaogu@localhost:5432/xiaogu') is True

    class MissingReturnColumnConnection(FakeConnection):
        returns_columns = database_startup.REQUIRED_RETURNS_COLUMNS - {'t1_return_close'}

    class MissingReturnColumnEngine(FakeEngine):
        connection_class = MissingReturnColumnConnection

    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: MissingReturnColumnEngine())

    assert database_startup.has_required_schema('postgresql://xiaogu:xiaogu@localhost:5432/xiaogu') is False

    class MissingVectorExtensionConnection(FakeConnection):
        extensions = []

    class MissingVectorExtensionEngine(FakeEngine):
        connection_class = MissingVectorExtensionConnection

    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: MissingVectorExtensionEngine())

    assert database_startup.has_required_schema('postgresql://xiaogu:xiaogu@localhost:5432/xiaogu') is False


def test_database_startup_launches_local_compose_only_when_needed(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: False)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: calls.append(("schema", database_url)))
    monkeypatch.setattr(
        database_startup,
        "start_host_postgresql",
        lambda: calls.append(("host_postgresql",)) or False,
    )
    monkeypatch.setattr(
        database_startup,
        "start_docker_daemon",
        lambda timeout_seconds: calls.append(("docker", timeout_seconds)),
    )
    monkeypatch.setattr(
        database_startup,
        "start_local_database",
        lambda: calls.append(("compose",)),
    )
    monkeypatch.setattr(
        database_startup,
        "wait_for_database",
        lambda target, timeout_seconds: calls.append(("wait", target, timeout_seconds)) or True,
    )

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu",
        timeout_seconds=7,
    )
    assert calls == [
        ("host_postgresql",),
        ("docker", 7),
        ("compose",),
        ("wait", ("localhost", 5432), 7),
        ("schema", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    ]


def test_database_startup_never_controls_remote_database(monkeypatch):
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: False)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: None)
    monkeypatch.setattr(
        database_startup,
        "start_docker_daemon",
        lambda timeout_seconds: (_ for _ in ()).throw(AssertionError("unexpected Docker startup")),
    )
    monkeypatch.setattr(
        database_startup,
        "start_local_database",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected Compose startup")),
    )
    monkeypatch.setattr(database_startup, "wait_for_database", lambda target, timeout_seconds: True)

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@db:5432/xiaogu",
        timeout_seconds=7,
    )


def test_database_startup_prefers_existing_host_postgresql_service(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: False)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: calls.append(("schema", database_url)))
    monkeypatch.setattr(
        database_startup,
        "start_host_postgresql",
        lambda: calls.append(("host_postgresql",)) or True,
    )
    monkeypatch.setattr(
        database_startup,
        "wait_for_database",
        lambda target, timeout_seconds: calls.append(("wait", target, timeout_seconds)) or True,
    )
    monkeypatch.setattr(
        database_startup,
        "start_docker_daemon",
        lambda timeout_seconds: (_ for _ in ()).throw(AssertionError("unexpected Docker startup")),
    )

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu",
        timeout_seconds=30,
    )
    assert calls == [
        ("host_postgresql",),
        ("wait", ("localhost", 5432), 15),
        ("schema", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    ]


def test_database_startup_migrates_only_when_required_schema_is_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "has_required_schema", lambda database_url: len(calls) > 0)
    monkeypatch.setattr(database_startup, "initialize_schema", lambda: calls.append("init"))

    database_startup.ensure_schema("postgresql://xiaogu:xiaogu@localhost:5432/xiaogu")

    assert calls == ["init"]


def test_api_uses_the_shared_database_startup_entrypoint(monkeypatch):
    calls = []
    monkeypatch.setattr(
        xiaogu_api,
        "ensure_database_ready",
        lambda database_url: calls.append(database_url) or True,
    )

    async def run_lifespan():
        async with xiaogu_api.app_lifespan(xiaogu_api.app):
            pass

    asyncio.run(run_lifespan())

    assert calls == [xiaogu_api.DATABASE_URL]


def test_daily_scan_uses_database_startup_entrypoint():
    script = Path("daily_scan.sh").read_text(encoding="utf-8")

    assert "python3 scripts/xiaogu_ensure_database.py" in script
    assert script.index("python3 scripts/xiaogu_ensure_database.py") < script.index("scrapy_scanner/runner_v2.py")
