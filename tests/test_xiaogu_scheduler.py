import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import xiaogu_ensure_database as database_startup
import xiaogu_api
import xiaogu_forward_d1_1450_runner_v0_1 as runner
import xiaogu_scheduler as scheduler


def _run_pipeline_failure_case(tmp_path, failure_stage):
    fake_python = tmp_path / 'python3'
    call_log = tmp_path / 'pipeline-python.log'
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "${XIAOGU_PIPELINE_TEST_LOG}"
if [ "${1:-}" = "-" ]; then
  body="$(cat)"
  printf '%s\\n' "$body" >> "${XIAOGU_PIPELINE_TEST_LOG}"
  if [[ "$body" == *"write_daily_closure"* ]]; then
    printf '%s\\n' "FAILURE_CLOSURE" >> "${XIAOGU_PIPELINE_TEST_LOG}"
  fi
fi
if [ "${1:-}" = "scrapy_scanner/runner_v2.py" ] && [ "${XIAOGU_PIPELINE_TEST_FAILURE}" = "scanner" ]; then
  exit 23
fi
if [ "${1:-}" = "xiaogu_forward_d1_1450_runner_v0_1.py" ] && [ "${XIAOGU_PIPELINE_TEST_FAILURE}" = "runner" ]; then
  exit 24
fi
""",
        encoding='utf-8',
    )
    fake_python.chmod(0o755)
    repo = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env.update({
        'PATH': f'{tmp_path}{os.pathsep}{env["PATH"]}',
        'XIAOGU_HOME': str(repo),
        'XIAOGU_PRODUCTION_RUN_ID': f'test-{failure_stage}-run',
        'XIAOGU_PIPELINE_TEST_LOG': str(call_log),
        'XIAOGU_PIPELINE_TEST_FAILURE': failure_stage,
    })
    completed = subprocess.run(
        ['bash', 'daily_pipeline.sh', '--manual-live-decision-day', '2026-08-07'],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed, call_log.read_text(encoding='utf-8')


def test_daily_pipeline_heredocs_compile():
    source = Path('daily_pipeline.sh').read_text(encoding='utf-8').splitlines()
    blocks = []
    current = None
    start = 0
    for line_no, line in enumerate(source, 1):
        if current is None and "<<'PY'" in line:
            current = []
            start = line_no + 1
            continue
        if current is not None:
            if line.strip() == 'PY':
                blocks.append((start, line_no - 1, '\n'.join(current)))
                current = None
            else:
                current.append(line)

    assert blocks
    for start, end, block in blocks:
        compile(block, f'<daily_pipeline:{start}-{end}>', 'exec')


def test_daily_pipeline_closure_output_serializes_datetime_values():
    pipeline = Path("daily_pipeline.sh").read_text(encoding="utf-8")

    assert pipeline.count(
        "print(json.dumps(closure, ensure_ascii=False, sort_keys=True, default=str))"
    ) == 4


def test_schema_init_retires_legacy_global_picks_unique_index():
    schema = Path("scripts/xiaogu_db_init.sql").read_text(encoding="utf-8")

    assert "DROP INDEX IF EXISTS idx_picks_unique_pick;" in schema
    assert schema.index("DROP INDEX IF EXISTS idx_picks_unique_pick;") < schema.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_picks_production_run_symbol_decision"
    )


def test_daily_pipeline_scanner_failure_closes_run_without_publishing_active(tmp_path):
    completed, call_log = _run_pipeline_failure_case(tmp_path, 'scanner')

    assert completed.returncode != 0
    assert "update_production_run_step(run_id, 'scanner', 'FAIL'" in call_log
    assert "update_production_run_status(run_id, 'FAIL'" in call_log
    assert 'SCANNER_FAILED' in call_log
    assert 'FAILURE_CLOSURE' in call_log
    assert 'set_active_production_run' not in call_log


def test_daily_pipeline_runner_failure_closes_run_without_publishing_active(tmp_path):
    completed, call_log = _run_pipeline_failure_case(tmp_path, 'runner')

    assert completed.returncode != 0
    assert "update_production_run_step(run_id, 'decision_persistence', 'FAIL'" in call_log
    assert "update_production_run_status(run_id, 'FAIL'" in call_log
    assert 'RUNNER_FAILED' in call_log
    assert 'FAILURE_CLOSURE' in call_log
    assert 'set_active_production_run' not in call_log


def test_morning_scan_runs_api_scanner(monkeypatch):
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
    assert args[args.index("--output-dir") + 1].endswith("/eastmoney_scan_morning")


def test_afternoon_scan_runs_single_pipeline(monkeypatch):
    """Afternoon pick ownership is daily_pipeline.sh."""
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
    result_args, _ = calls[0]
    assert result_args[-1] == "--auto-eastmoney"
    assert "--auto-web" not in result_args
    t1_args, _ = calls[1]
    assert t1_args[:2] == ["bash", "daily_pipeline.sh"]
    assert "--manual-return-backfill" in t1_args
    assert "--validate-on" in t1_args


def test_result_fill_propagates_filler_failure_and_stops_pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 7,
    )

    with pytest.raises(RuntimeError, match="RESULT_FILL_FAILED:rc=7"):
        scheduler.job_result_fill()

    assert [job_name for _, job_name in calls] == ["result_fill"]


def test_result_fill_propagates_t1_failure(monkeypatch):
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
        lambda args, job_name: calls.append((args, job_name))
        or (0 if job_name == "result_fill" else 9),
    )

    with pytest.raises(RuntimeError, match="T1_VALIDATION_FAILED:rc=9"):
        scheduler.job_result_fill()

    assert [job_name for _, job_name in calls] == [
        "result_fill",
        "t1_validation_and_self_evolve",
    ]


def test_result_fill_forwards_explicit_production_run_id(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "is_trading_day", lambda: True)
    monkeypatch.setattr(
        scheduler,
        "previous_trading_day",
        lambda d: d.__class__(2026, 7, 21),
    )
    monkeypatch.setenv("XIAOGU_T1_PRODUCTION_RUN_ID", "run-explicit")
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda args, job_name: calls.append((args, job_name)) or 0,
    )

    scheduler.job_result_fill()

    for args, _ in calls:
        assert "--production-run-id" in args
        assert args[args.index("--production-run-id") + 1] == "run-explicit"


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
    assert "--dry-run" in evolve_args
    assert "--apply-if-ready" not in evolve_args
    assert "--apply-weights" not in " ".join(str(a) for a in evolve_args)


def test_daily_pipeline_keeps_self_evolve_observation_only():
    pipeline = Path("daily_pipeline.sh").read_text(encoding="utf-8")

    assert "--dry-run" in pipeline
    assert "--apply-if-ready" not in pipeline
    assert "--apply-weights" not in pipeline


def test_production_chain_has_no_browser_or_alternate_provider_fallback():
    pipeline = Path("daily_pipeline.sh").read_text(encoding="utf-8")
    scheduler_source = Path("xiaogu_scheduler.py").read_text(encoding="utf-8")
    filler_source = Path("xiaogu_forward_result_filler_v0_1.py").read_text(encoding="utf-8")
    assert "auto-web" not in pipeline
    assert "auto-web" not in scheduler_source
    assert "tencent" not in filler_source.lower()
    assert "WEB_EVIDENCE" not in filler_source


def test_scan_summary_paths_excludes_legacy_source(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LIVE_SCAN_ROOT", tmp_path)
    trade_date = "2026-07-14"

    api_summary = tmp_path / trade_date / "eastmoney_scan_afternoon" / runner.SCAN_SUMMARY_NAME
    api_summary.parent.mkdir(parents=True)
    api_summary.write_text(
        json.dumps({"source": "eastmoney_api_scan_v2", "pipeline_version": "v2_scanner_api"}),
        encoding="utf-8",
    )

    legacy_summary = tmp_path / trade_date / "legacy_scan" / runner.SCAN_SUMMARY_NAME
    legacy_summary.parent.mkdir(parents=True)
    legacy_summary.write_text(
        json.dumps({"source": "legacy_source", "pipeline_version": "legacy_scan"}),
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
    def column_rows(required_returns_columns=None, lineage_columns=None):
        rows = [('scan_sessions', column) for column in database_startup.REQUIRED_SCAN_SESSION_COLUMNS]
        rows.extend(
            ('returns', column)
            for column in (required_returns_columns or database_startup.REQUIRED_RETURNS_COLUMNS)
        )
        for table_name, required_columns in (
            lineage_columns or database_startup.REQUIRED_LINEAGE_COLUMNS
        ).items():
            rows.extend((table_name, column) for column in required_columns)
        return rows

    class FakeConnection:
        extensions = [('vector',)]
        returns_columns = database_startup.REQUIRED_RETURNS_COLUMNS
        lineage_columns = database_startup.REQUIRED_LINEAGE_COLUMNS

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            query_text = str(query)
            if 'pg_extension' in query_text:
                return self.extensions
            return column_rows(self.returns_columns, self.lineage_columns)

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

    class MissingLineageColumnConnection(FakeConnection):
        lineage_columns = {
            **database_startup.REQUIRED_LINEAGE_COLUMNS,
            'signals': set(),
        }

    class MissingLineageColumnEngine(FakeEngine):
        connection_class = MissingLineageColumnConnection

    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: MissingLineageColumnEngine())

    assert database_startup.has_required_schema('postgresql://xiaogu:xiaogu@localhost:5432/xiaogu') is False

    class MissingVectorExtensionConnection(FakeConnection):
        extensions = []

    class MissingVectorExtensionEngine(FakeEngine):
        connection_class = MissingVectorExtensionConnection

    monkeypatch.setattr('sqlalchemy.create_engine', lambda *args, **kwargs: MissingVectorExtensionEngine())

    assert database_startup.has_required_schema('postgresql://xiaogu:xiaogu@localhost:5432/xiaogu') is False


def test_database_startup_waits_for_configured_database_without_service_control(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: False)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: calls.append(("schema", database_url)))
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
        ("wait", ("localhost", 5432), 7),
        ("schema", "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"),
    ]


def test_database_startup_waits_for_remote_database(monkeypatch):
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: False)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: None)
    monkeypatch.setattr(database_startup, "wait_for_database", lambda target, timeout_seconds: True)

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@db:5432/xiaogu",
        timeout_seconds=7,
    )


def test_database_startup_uses_reachable_database_without_waiting(monkeypatch):
    calls = []
    monkeypatch.setattr(database_startup, "is_database_reachable", lambda target: True)
    monkeypatch.setattr(database_startup, "ensure_schema", lambda database_url: calls.append(("schema", database_url)))
    monkeypatch.setattr(
        database_startup,
        "wait_for_database",
        lambda target, timeout_seconds: (_ for _ in ()).throw(AssertionError("unexpected wait")),
    )

    assert database_startup.ensure_database_ready(
        "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu",
        timeout_seconds=30,
    )
    assert calls == [
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


def test_daily_pipeline_uses_database_startup_entrypoint():
    script = Path("daily_pipeline.sh").read_text(encoding="utf-8")

    assert "python3 scripts/xiaogu_ensure_database.py" in script
    assert script.index("python3 scripts/xiaogu_ensure_database.py") < script.index("scrapy_scanner/runner_v2.py")
