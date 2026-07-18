#!/usr/bin/env python3
"""APScheduler-based unified scheduler for xiaogu A-share system."""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("xiaogu.scheduler")

CHINA_HOLIDAYS_2026 = {
    '2026-01-01',
    '2026-01-26', '2026-01-27', '2026-01-28', '2026-01-29', '2026-01-30',
    '2026-02-02', '2026-02-03',
    '2026-04-06',
    '2026-05-01', '2026-05-04', '2026-05-05',
    '2026-06-22',
    '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06',
    '2026-10-07', '2026-10-08', '2026-10-09',
}

DRY_RUN = os.environ.get("XIAOGU_DRY_RUN", "0") == "1"
PYTHON = sys.executable
API_SCANNER = "scrapy_scanner/runner_v2.py"
DATABASE_STARTUP = "scripts/xiaogu_ensure_database.py"


def run_cmd(args: list, job_name: str) -> int:
    logger.info(f"[{job_name}] starting: {' '.join(args)}")
    try:
        result = subprocess.run(
            args, cwd=str(BASE), capture_output=True, text=True, timeout=1800,
        )
        for line in (result.stdout or "").splitlines()[-20:]:
            logger.info(f"[{job_name}] {line}")
        if result.returncode != 0:
            logger.error(f"[{job_name}] FAILED (rc={result.returncode})")
            for line in (result.stderr or "").splitlines()[-10:]:
                logger.error(f"[{job_name}] stderr: {line}")
        else:
            logger.info(f"[{job_name}] completed OK")
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"[{job_name}] TIMEOUT after 30min")
        return -1
    except Exception as exc:
        logger.error(f"[{job_name}] exception: {exc}")
        return -1


def ensure_database_ready() -> bool:
    """Start or wait for PostgreSQL before scheduler jobs can use persistence."""
    return run_cmd([PYTHON, DATABASE_STARTUP], "database_startup") == 0


def is_trading_day(check_date=None) -> bool:
    """Return True if *check_date* is an A-share trading day."""
    if check_date is None:
        check_date = datetime.now(TZ).date()
    elif isinstance(check_date, datetime):
        check_date = check_date.date()

    if check_date.weekday() >= 5:
        return False

    try:
        import exchange_calendars as xcals
        xshg = xcals.get_calendar("XSHG")
        return xshg.is_session(check_date.strftime("%Y-%m-%d"))
    except ImportError:
        pass
    except Exception:
        pass

    return check_date.strftime("%Y-%m-%d") not in CHINA_HOLIDAYS_2026


def previous_trading_day(check_date=None):
    """Return the most recent completed A-share trading day."""
    current = (check_date or datetime.now(TZ).date()) - timedelta(days=1)
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def scanner_summary_asof_time(output_dir: str) -> str:
    """Read scanner source_time HH:MM:SS for runner parity with daily_pipeline."""
    summary_path = BASE / output_dir / "eastmoney_web_tabs_summary_runner.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        source_time = str(summary.get("source_time") or "")
        if len(source_time) >= 19:
            return source_time[11:19]
    except Exception as exc:
        logger.warning("[afternoon_pick] could not read scanner source_time from %s: %s", summary_path, exc)
    return datetime.now(TZ).strftime("%H:%M:%S")


def job_morning_scan():
    """09:25 — morning scanner."""
    if not is_trading_day():
        logger.info("[morning_scan] skipped — not a trading day")
        return
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    run_cmd([
        PYTHON, API_SCANNER,
        "--output-dir", f"data/live_scan/{today}/eastmoney_scan_morning",
    ], "morning_scan")


def job_afternoon_scan_and_pick():
    """14:30 — afternoon scanner + runner."""
    if not is_trading_day():
        logger.info("[afternoon_pick] skipped — not a trading day")
        return
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    output_dir = f"data/live_scan/{today}/eastmoney_scan_afternoon"
    rc = run_cmd([
        PYTHON, API_SCANNER,
        "--output-dir", output_dir,
    ], "afternoon_scan")
    if rc != 0:
        logger.error("[afternoon_pick] scan failed, skipping runner")
        return
    runner_args = [
        PYTHON, "xiaogu_forward_d1_1450_runner_v0_1.py",
        "--date", today,
        "--asof-time", scanner_summary_asof_time(output_dir),
        "--no-runtime-date-adjust",
        "--force",
    ]
    if DRY_RUN:
        runner_args.append("--dry-run")
    run_cmd(runner_args, "afternoon_runner")


def job_result_fill():
    """15:30 — fill paper picks and the previous trading day's Top-10 returns."""
    if not is_trading_day():
        logger.info("[result_fill] skipped — not a trading day")
        return
    run_cmd([
        PYTHON, "xiaogu_forward_result_filler_v0_1.py",
        "--fill-all-pending", "--auto-web",
    ], "result_fill")
    validation_date = datetime.now(TZ).date()
    input_date = previous_trading_day(validation_date)
    run_cmd([
        PYTHON, "scripts/xiaogu_return_backfill.py",
        "--trade-date", input_date.isoformat(),
        "--validate-on", validation_date.isoformat(),
    ], "top10_return_backfill")


def job_signal_effectiveness():
    """20:00 — daily signal effectiveness analysis."""
    run_cmd([
        PYTHON, "xiaogu_signal_effectiveness_v0_1.py",
        "--ledger", "forward_paper_ledger_v0_1.jsonl",
        "--min-samples", "20",
        "--persist",
    ], "signal_effectiveness")
    if os.environ.get('XIAOGU_WEIGHT_AUTO_TUNE') == '1':
        run_cmd([
            PYTHON, 'xiaogu_signal_effectiveness_v0_1.py',
            '--ledger', 'forward_paper_ledger_v0_1.jsonl',
            '--min-samples', '3',
            '--apply-weights',
        ], 'weight_auto_tune')


def main():
    if not ensure_database_ready():
        logger.error("scheduler startup aborted: PostgreSQL is unavailable")
        raise SystemExit(1)

    scheduler = BlockingScheduler(timezone=TZ)
    scheduler.add_job(job_morning_scan, CronTrigger(hour=9, minute=25, timezone=TZ),
                      id="morning_scan", name="09:25 Morning Scanner", misfire_grace_time=300)
    scheduler.add_job(job_afternoon_scan_and_pick, CronTrigger(hour=14, minute=30, timezone=TZ),
                      id="afternoon_pick", name="14:30 Afternoon Scan + Runner", misfire_grace_time=300)
    scheduler.add_job(job_result_fill, CronTrigger(hour=15, minute=30, timezone=TZ),
                      id="result_fill", name="15:30 Result Filler", misfire_grace_time=600)
    scheduler.add_job(job_signal_effectiveness, CronTrigger(hour=20, minute=0, timezone=TZ),
                      id="signal_effectiveness", name="20:00 Signal Effectiveness", misfire_grace_time=1800)

    logger.info("xiaogu scheduler starting — jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.name}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler stopped")


if __name__ == "__main__":
    main()
