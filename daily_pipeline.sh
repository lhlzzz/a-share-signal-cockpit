#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$WORKSPACE"
export PYTHONPATH="$WORKSPACE${PYTHONPATH:+:$PYTHONPATH}"
export XIAOGU_PERSIST_DB=1
# One trading-day production pipeline. Directory name is capture identity, not a clock.
DATE="${1:-$(date +%F)}"
SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan"

python3 scripts/xiaogu_ensure_database.py
python3 scrapy_scanner/runner_v2.py --output-dir "$SCAN_DIR"
python3 xiaogu_forward_runner.py --date "$DATE" --scan-dir "$SCAN_DIR"
python3 xiaogu_forward_result_filler_v0_1.py --due --end-date "$DATE" --timeout-seconds 90
