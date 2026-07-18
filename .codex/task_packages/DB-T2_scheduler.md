# DB-T2: APScheduler 统一调度器

## 目标
创建 `xiaogu_scheduler.py`，用 APScheduler 统一管理4个交易日任务，替代所有手动触发和 cron。

## 工作目录
/workspace/hermes-workspaces/xiaogu

## 调度时间表
```
09:25 CST  → scanner（开盘实时数据采集）
14:30 CST  → scanner + runner（尾盘出票，25分钟操作窗口）
15:30 CST  → filler --fill-all-pending --auto-web（收盘收益回填）
20:00 CST  → effectiveness（信号分析，写 signal_effectiveness 表）
```

## 文件：xiaogu_scheduler.py

```python
#!/usr/bin/env python3
"""APScheduler-based unified scheduler for xiaogu A-share system."""
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("xiaogu.scheduler")

CDP_URL = os.environ.get("XIAOGU_CDP_URL", "http://localhost:9333")
DRY_RUN = os.environ.get("XIAOGU_DRY_RUN", "1") == "1"
PYTHON = sys.executable


def run_cmd(args: list[str], job_name: str) -> int:
    """Run subprocess, log output, return exit code."""
    logger.info(f"[{job_name}] starting: {' '.join(args)}")
    try:
        result = subprocess.run(
            args,
            cwd=str(BASE),
            capture_output=True,
            text=True,
            timeout=1800,  # 30min max
        )
        if result.stdout:
            for line in result.stdout.splitlines()[-20:]:
                logger.info(f"[{job_name}] {line}")
        if result.returncode != 0:
            logger.error(f"[{job_name}] FAILED (rc={result.returncode})")
            if result.stderr:
                for line in result.stderr.splitlines()[-10:]:
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


def is_trading_day() -> bool:
    """Skip weekends. TODO: integrate A-share holiday calendar."""
    now = datetime.now(TZ)
    return now.weekday() < 5  # Mon-Fri only


def job_morning_scan():
    """09:25 — morning scanner for pre-market data."""
    if not is_trading_day():
        logger.info("[morning_scan] skipped — not a trading day")
        return
    run_cmd([
        PYTHON, "xiaogu_eastmoney_web_tabs_scan_v0_1.py",
        "--cloak",
        "--cdp-url", CDP_URL,
        "--open-required-cdp-tabs",
        "--experimental",
    ], "morning_scan")


def job_afternoon_scan_and_pick():
    """14:30 — afternoon scanner + runner for ticket output."""
    if not is_trading_day():
        logger.info("[afternoon_pick] skipped — not a trading day")
        return
    rc = run_cmd([
        PYTHON, "xiaogu_eastmoney_web_tabs_scan_v0_1.py",
        "--cloak",
        "--cdp-url", CDP_URL,
        "--open-required-cdp-tabs",
        "--experimental",
    ], "afternoon_scan")
    if rc != 0:
        logger.error("[afternoon_pick] scan failed, skipping runner")
        return
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    runner_args = [
        PYTHON, "xiaogu_forward_d1_1450_runner_v0_1.py",
        "--date", today,
    ]
    if DRY_RUN:
        runner_args.append("--dry-run")
    run_cmd(runner_args, "afternoon_runner")


def job_result_fill():
    """15:30 — fill T+1/2/3 returns for previous picks."""
    run_cmd([
        PYTHON, "xiaogu_forward_result_filler_v0_1.py",
        "--fill-all-pending",
        "--auto-web",
    ], "result_fill")


def job_signal_effectiveness():
    """20:00 — daily signal effectiveness analysis."""
    run_cmd([
        PYTHON, "xiaogu_signal_effectiveness_v0_1.py",
        "--ledger", "forward_paper_ledger_v0_1.jsonl",
        "--min-samples", "3",
    ], "signal_effectiveness")


def main():
    scheduler = BlockingScheduler(timezone=TZ)

    scheduler.add_job(
        job_morning_scan,
        CronTrigger(hour=9, minute=25, timezone=TZ),
        id="morning_scan",
        name="09:25 Morning Scanner",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_afternoon_scan_and_pick,
        CronTrigger(hour=14, minute=30, timezone=TZ),
        id="afternoon_pick",
        name="14:30 Afternoon Scan + Runner",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_result_fill,
        CronTrigger(hour=15, minute=30, timezone=TZ),
        id="result_fill",
        name="15:30 Result Filler",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        job_signal_effectiveness,
        CronTrigger(hour=20, minute=0, timezone=TZ),
        id="signal_effectiveness",
        name="20:00 Signal Effectiveness",
        misfire_grace_time=1800,
    )

    logger.info("xiaogu scheduler starting — jobs registered:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.name} → next: {job.next_run_time}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler stopped")


if __name__ == "__main__":
    main()
```

## 验收标准
1. `python3 -m py_compile xiaogu_scheduler.py` 无错
2. `python3 -c "import xiaogu_scheduler; print('OK')"` 无错（需要 apscheduler 已安装；若未安装则 pip install apscheduler 后验证）
3. 4个 job 都注册：morning_scan / afternoon_pick / result_fill / signal_effectiveness
4. `python3 -m pytest tests/ -x -q` 仍然全部通过

## 禁止修改
- 任何现有 runner/scanner/filler 文件
- `forward_paper_ledger_v0_1.jsonl`
- 任何现有测试
