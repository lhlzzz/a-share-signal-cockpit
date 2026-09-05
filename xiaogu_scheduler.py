"""Post-close calendar-gated horizon fill and position review.

Production capture is `bash daily_pipeline.sh`. This module does not define a
scan clock, morning window, or afternoon ticket.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")


def is_trading_day(check_date=None) -> bool:
    from xiaogu_db import CALENDAR_UNKNOWN, TRADING_DAY, is_trading_date
    status = is_trading_date(check_date or datetime.now(TZ).date())
    if isinstance(status, bool):
        return status
    if status == CALENDAR_UNKNOWN:
        raise RuntimeError("CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE")
    return status == TRADING_DAY


def _run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=BASE, check=True)


def job_horizon_evaluation() -> None:
    if not is_trading_day():
        return
    _run("xiaogu_forward_result_filler_v0_1.py", "--due", "--timeout-seconds", "90")
    _run("xiaogu_forward_runner.py", "--date", f"{datetime.now(TZ):%F}", "--position-review")


def main() -> None:
    is_trading_day()
    scheduler = BlockingScheduler(timezone=TZ)
    scheduler.add_job(job_horizon_evaluation, CronTrigger(hour=20, minute=0, timezone=TZ))
    scheduler.start()


if __name__ == "__main__":
    main()
