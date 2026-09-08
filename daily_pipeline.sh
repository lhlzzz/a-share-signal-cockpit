#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$WORKSPACE"
export PYTHONPATH="$WORKSPACE${PYTHONPATH:+:$PYTHONPATH}"
export XIAOGU_PERSIST_DB=1
# One trading-day production pipeline. Directory name is capture identity, not a clock.
DATE="${1:-$(date +%F)}"
SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan"
REPLACE_OFFICIAL="${REPLACE_OFFICIAL:-0}"
AS_PREVIOUS="${AS_PREVIOUS_TRADING_DATE:-0}"

python3 -c "
from pathlib import Path
import os
vault = Path(os.environ.get('XIAOGU_OBSIDIAN_VAULT') or '/mnt/d/obisidian/Obsidian/Project/A股')
daily = vault / 'xiaogu_memory' / 'daily'
if not vault.exists() or not daily.exists():
    raise SystemExit(f'OBSIDIAN_VAULT_UNAVAILABLE vault={vault} daily={daily}')
print('Obsidian vaults OK', vault)
"
python3 scripts/xiaogu_ensure_database.py
SCANNER_ARGS=(--output-dir "$SCAN_DIR")
if [[ "$AS_PREVIOUS" == "1" ]]; then
  SCANNER_ARGS+=(--as-previous-trading-date)
fi
if [[ "$REPLACE_OFFICIAL" == "1" ]]; then
  SCANNER_ARGS+=(--force-recapture)
fi
python3 scrapy_scanner/runner_v2.py "${SCANNER_ARGS[@]}"
RUNNER_ARGS=(--date "$DATE" --scan-dir "$SCAN_DIR")
if [[ "$REPLACE_OFFICIAL" == "1" ]]; then
  RUNNER_ARGS+=(--replace-official)
fi
python3 xiaogu_forward_runner.py "${RUNNER_ARGS[@]}"
python3 xiaogu_forward_result_filler_v0_1.py --due --end-date "$DATE" --timeout-seconds 90
