#!/usr/bin/env bash
# xiaogu 每日出票流程 - v2 Scanner (API直接) + Runner 出票
# 用法: bash daily_pipeline.sh [日期]
# 示例: bash daily_pipeline.sh 2026-07-06

set -euo pipefail

WORKSPACE="${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$WORKSPACE"
python3 scripts/xiaogu_ensure_database.py

if [ "${1:-}" = "--manual-return-backfill" ]; then
    INPUT_TRADE_DATE="${2:-}"
    if [ -z "$INPUT_TRADE_DATE" ] || [ "${3:-}" != "--validate-on" ] || [ -z "${4:-}" ]; then
        echo "Usage: bash daily_pipeline.sh --manual-return-backfill YYYY-MM-DD --validate-on YYYY-MM-DD [--production-run-id RUN_ID]" >&2
        exit 2
    fi
    VALIDATION_TRADE_DATE="$4"
    MANUAL_RUN_ID=""
    if [ "${5:-}" = "--production-run-id" ]; then
        MANUAL_RUN_ID="${6:-}"
        if [ -z "$MANUAL_RUN_ID" ]; then
            echo "--production-run-id requires a value" >&2
            exit 2
        fi
    fi
    echo "Running T1_VALIDATION: ${INPUT_TRADE_DATE} -> ${VALIDATION_TRADE_DATE}"
    BACKFILL_ARGS=(--trade-date "$INPUT_TRADE_DATE" --validate-on "$VALIDATION_TRADE_DATE")
    if [ -n "$MANUAL_RUN_ID" ]; then
        BACKFILL_ARGS+=(--production-run-id "$MANUAL_RUN_ID")
    fi
    python3 scripts/xiaogu_return_backfill.py "${BACKFILL_ARGS[@]}"
    python3 - "$INPUT_TRADE_DATE" "$VALIDATION_TRADE_DATE" "$MANUAL_RUN_ID" <<'PY'
import json
import sys
from datetime import date
from pathlib import Path

from xiaogu_backtest_v0_1 import (
    _limitup_gene_signal_audit, build_daily_closure, build_db_cohort_report,
    build_db_completeness_gate, build_historical_live_replay_closure, write_daily_closure,
)
from xiaogu_db import (
    fetch_active_production_run,
    fetch_daily_candidates,
    fetch_latest_scan_session,
    fetch_picks,
    fetch_production_run_steps,
    fetch_returns,
    fetch_signals,
)

input_trade_date, validation_trade_date, requested_run_id = sys.argv[1:]
trade_day = date.fromisoformat(input_trade_date)
active = (
    {'production_run_id': requested_run_id}
    if requested_run_id
    else fetch_active_production_run(trade_day)
)
if not active or not active.get('production_run_id'):
    raise SystemExit('PRODUCTION_RUN_NOT_FOUND_FOR_MANUAL_T1_VALIDATION')
production_run_id = str(active['production_run_id'])
summary_path = Path('summary/return_backfill_results.json')
backfill_stats = json.loads(summary_path.read_text()) if summary_path.exists() else {}
report = build_db_cohort_report()
db_gate = build_db_completeness_gate(
    input_trade_date, mode='T1_VALIDATION', validation_trade_date=validation_trade_date,
    candidate_rows=fetch_daily_candidates(trade_day, production_run_id=production_run_id),
    pick_rows=fetch_picks(trade_day, production_run_id=production_run_id),
    return_rows=fetch_returns(trade_day, production_run_id=production_run_id),
    signal_rows=fetch_signals(trade_day, production_run_id=production_run_id),
    scan_session=fetch_latest_scan_session(trade_day, production_run_id=production_run_id),
)
replay = build_historical_live_replay_closure(
    input_trade_date, validation_trade_date, backfill_stats=backfill_stats,
)
candidates = fetch_daily_candidates(trade_day, production_run_id=production_run_id)
manual_status = 'FAIL' if backfill_stats.get('fatal_error') else db_gate['status']
closure = build_daily_closure(
    input_trade_date, report, backfill_stats,
    scan_completed=db_gate['checks']['scan_session_persisted'],
    paper_pick_written=db_gate['checks']['paper_pick_persisted'],
    return_backfill_completed=db_gate['checks']['t1_return_available'],
    run_mode='T1_VALIDATION',
    production_run_id=production_run_id,
    candidate_snapshot_id=str(active.get('candidate_snapshot_id') or production_run_id),
    active_pick_id=active.get('active_pick_id'),
    run_steps=fetch_production_run_steps(production_run_id),
)
closure.update({
    'validation_trade_date': validation_trade_date,
    'data_source': 'DB_SNAPSHOT',
    'uses_future_data_for_decision': False,
    'return_validation_status': 'PASS' if db_gate['checks']['t1_return_available'] else 'FAIL',
    'db_completeness_gate': db_gate,
    'db_completeness_summary': db_gate['db_completeness_summary'],
    'data_completeness_gate': {
        'status': db_gate['status'],
        'checks': db_gate['checks'],
        'candidate_pool_status': db_gate['candidate_pool_status'],
        'reason': db_gate['candidate_pool_warning_reason'],
    },
    'return_validation_gate': {
        'status': 'PASS' if db_gate['checks']['t1_return_available'] else 'FAIL',
        'reason': None if db_gate['checks']['t1_return_available'] else 'paper_pick_t1_return_missing',
    },
    'manual_return_backfill': {
        'status': manual_status,
        'input_trade_date': input_trade_date,
        'validation_trade_date': validation_trade_date,
        'paper_pick_t1_filled': db_gate['checks']['t1_return_available'],
        'top10_t1_coverage': backfill_stats.get('top10_return_coverage'),
        'rank2_to_rank6_t1_coverage': backfill_stats.get('rank2_to_rank6_return_coverage'),
        'failures': backfill_stats.get('failed_return_symbols', []),
    },
    'historical_live_replay': replay['historical_live_replay'],
    'historical_replay_leakage_gate': replay['historical_replay_leakage_gate'],
    'shadow_replay_leakage_gate': replay['shadow_replay_leakage_gate'],
    'historical_live_replay_sample_update': replay['historical_live_replay_sample_update'],
    'limitup_gene_signal_audit': _limitup_gene_signal_audit(
        [{'trade_date': input_trade_date, 'paper': {}, 'day': candidates}],
        {'daily_cases': []}, run_mode='T1_VALIDATION',
    ),
})
output_path = write_daily_closure(closure)
print(json.dumps(closure, ensure_ascii=False, sort_keys=True, default=str))
print(f'  daily_closure_latest: {output_path}')
PY
    # After T+1 validation: refresh shadow profit candidates with returns + vs-official compare.
    echo ""
    echo "[T1] 影子获利候选 T+1 回填对比（observation_only）..."
    python3 scripts/xiaogu_profit_candidates_shadow.py \
      --date "${INPUT_TRADE_DATE}" \
      --top "${XIAOGU_PROFIT_CANDIDATES_TOP:-5}" \
      --with-returns \
      --compare-official 2>&1 | tail -20 || true
    # Research diagnostics stay observation-only; they never mutate production scoring.
    echo ""
    echo "[T1] 有界因子自进化（仅 dry-run）..."
    python3 scripts/xiaogu_safe_self_evolve.py --date "${INPUT_TRADE_DATE}" --dry-run 2>&1 | tail -12 || true
    exit 0
fi

if [ "${1:-}" = "--historical-live-replay" ]; then
    INPUT_TRADE_DATE="${2:-}"
    if [ -z "$INPUT_TRADE_DATE" ] || [ "${3:-}" != "--validate-on" ] || [ -z "${4:-}" ]; then
        echo "Usage: bash daily_pipeline.sh --historical-live-replay YYYY-MM-DD --validate-on YYYY-MM-DD" >&2
        exit 2
    fi
    VALIDATION_TRADE_DATE="$4"
    echo "Running HISTORICAL_LIVE_REPLAY: ${INPUT_TRADE_DATE} -> ${VALIDATION_TRADE_DATE}"
    python3 - "$INPUT_TRADE_DATE" "$VALIDATION_TRADE_DATE" <<'PY'
import json
import sys
from pathlib import Path

from xiaogu_backtest_v0_1 import build_historical_live_replay_closure, write_daily_closure

summary_path = Path('summary/return_backfill_results.json')
backfill_stats = json.loads(summary_path.read_text()) if summary_path.exists() else {}
closure = build_historical_live_replay_closure(
    sys.argv[1], sys.argv[2], backfill_stats=backfill_stats,
)
output_path = write_daily_closure(closure)
print(json.dumps(closure, ensure_ascii=False, sort_keys=True, default=str))
print(f'  daily_closure_latest: {output_path}')
PY
    exit 0
fi

MANUAL_LIVE_DECISION_DAY=0
if [ "${1:-}" = "--manual-live-decision-day" ]; then
    DATE="${2:-}"
    if [ -z "$DATE" ]; then
        echo "Usage: bash daily_pipeline.sh --manual-live-decision-day YYYY-MM-DD" >&2
        exit 2
    fi
    MANUAL_LIVE_DECISION_DAY=1
else
    DATE="${1:-$(date +%Y-%m-%d)}"
fi
HOUR=$(date +%H)
PIPELINE_STARTED_SECONDS=$SECONDS
export XIAOGU_PRODUCTION_RUN_ID="${XIAOGU_PRODUCTION_RUN_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
PIPELINE_REQUIRED_FAILURE=0

echo "=========================================="
echo "xiaogu 出票流程 (v2 Scanner) - ${DATE}"
echo "production_run_id: ${XIAOGU_PRODUCTION_RUN_ID}"
echo "=========================================="

write_failure_closure() {
  python3 - "${DATE}" "${XIAOGU_PRODUCTION_RUN_ID}" "$1" <<'PY'
import json
import sys
from xiaogu_backtest_v0_1 import build_daily_closure, write_daily_closure
from xiaogu_db import fetch_production_run_steps

trade_date, run_id, reason = sys.argv[1:]
closure = build_daily_closure(
    trade_date,
    {},
    {'fatal_error': reason, 'failure_reasons': {reason: 1}},
    scan_completed=False,
    paper_pick_written=False,
    return_backfill_completed=False,
    production_run_id=run_id,
    candidate_snapshot_id=run_id,
    run_steps=fetch_production_run_steps(run_id),
    knowledge_status='FAIL',
)
closure['production_audit_conclusion'] = 'NOT_FREEZABLE'
closure['required_failure'] = reason
closure['production_run_steps'] = fetch_production_run_steps(run_id)
path = write_daily_closure(closure)
print(json.dumps(closure, ensure_ascii=False, sort_keys=True, default=str))
print(f'  daily_closure_latest: {path}')
PY
}

# 根据时间选择扫描目录
if [ $HOUR -lt 12 ]; then
    SCAN_DIR="eastmoney_scan_morning"
    SCAN_LABEL="早盘扫描"
else
    SCAN_DIR="eastmoney_scan_afternoon"
    SCAN_LABEL="尾盘扫描"
fi

echo "扫描类型: ${SCAN_LABEL}"
echo ""

# Step 1: 运行 v2 Scanner (API直接，实际耗时由 summary/timing 输出)
echo ""
echo "[1/6] 运行 v2 Scanner..."
SCANNER_STARTED_SECONDS=$SECONDS
if ! python3 scrapy_scanner/runner_v2.py \
  --output-dir "data/live_scan/${DATE}/${SCAN_DIR}" 2>&1 | tail -10; then
  python3 - "${DATE}" "${XIAOGU_PRODUCTION_RUN_ID}" <<'PY'
import sys
from datetime import date
from xiaogu_db import (
    create_production_run,
    fetch_production_run,
    update_production_run_status,
    update_production_run_step,
)
trade_date, run_id = sys.argv[1:]
if not fetch_production_run(run_id):
    create_production_run(date.fromisoformat(trade_date), run_id)
update_production_run_step(run_id, 'scanner', 'FAIL', required=True, error_message='SCANNER_FAILED')
update_production_run_status(run_id, 'FAIL', error_message='SCANNER_FAILED')
PY
  write_failure_closure "SCANNER_FAILED"
  exit 1
fi
SCANNER_ELAPSED_SECONDS=$((SECONDS - SCANNER_STARTED_SECONDS))
echo "  Scanner elapsed: ${SCANNER_ELAPSED_SECONDS}s"

# Step 2: 运行 Runner。Runner 只读取本次 direct API 扫描产物。
echo ""
echo "[2/5] 运行 Runner..."
FORWARD_STARTED_SECONDS=$SECONDS
RUNNER_ARGS=(--date "${DATE}" --asof-time "$(date +%H:%M:%S)")
# The scanner has just produced a realtime snapshot in this explicit date
# directory. The runner must consume that same directory rather than silently
# substituting a prior completed trading day during pre-market execution.
RUNNER_ARGS+=(--no-runtime-date-adjust --force)
if ! env NO_AUTO_TRADE=1 NO_ORDER_EXECUTION=1 python3 xiaogu_forward_runner.py "${RUNNER_ARGS[@]}" 2>&1 | tail -10; then
  python3 - "${DATE}" "${XIAOGU_PRODUCTION_RUN_ID}" <<'PY'
import sys
from datetime import date
from xiaogu_db import (
    create_production_run,
    fetch_production_run,
    update_production_run_status,
    update_production_run_step,
)
trade_date, run_id = sys.argv[1:]
if not fetch_production_run(run_id):
    create_production_run(date.fromisoformat(trade_date), run_id)
update_production_run_step(run_id, 'scanner', 'PASS', required=True)
update_production_run_step(run_id, 'decision_persistence', 'FAIL', required=True, error_message='RUNNER_FAILED')
update_production_run_status(run_id, 'FAIL', error_message='RUNNER_FAILED')
PY
  write_failure_closure "RUNNER_FAILED"
  exit 1
fi
FORWARD_ELAPSED_SECONDS=$((SECONDS - FORWARD_STARTED_SECONDS))
TOTAL_ELAPSED_SECONDS=$((SECONDS - PIPELINE_STARTED_SECONDS))
echo "  Forward elapsed: ${FORWARD_ELAPSED_SECONDS}s"
echo "  Total elapsed: ${TOTAL_ELAPSED_SECONDS}s"

# A full-chain row is operationally complete, but its performance conclusion
# remains pending until the return filler records T+1.
DATABASE_URL="${DATABASE_URL:-postgresql://xiaogu:xiaogu@localhost:5432/xiaogu}" python3 - "${DATE}" <<'PY'
import json
import os
import sys
from datetime import date
from xiaogu_db import fetch_daily_candidates, fetch_returns

trade_date = date.fromisoformat(sys.argv[1])
candidates = fetch_daily_candidates(trade_date, production_run_id=os.environ['XIAOGU_PRODUCTION_RUN_ID'])
full_chain = [row for row in candidates if row.get('cohort_quality') == 'FULL_CHAIN_COMPLETE']
returns = {
    str(row.get('symbol') or ''): row.get('t1_return')
    for row in fetch_returns(trade_date, production_run_id=os.environ['XIAOGU_PRODUCTION_RUN_ID'])
}
ready_day_count = int(bool(full_chain and any(returns.get(str(row.get('symbol') or '')) is not None for row in full_chain)))
pending_day_count = int(bool(full_chain and not ready_day_count))
full_chain_gate = {
    'ready_day_count': ready_day_count,
    'pending_day_count': pending_day_count,
    'minimum_ready_days_for_freeze': 5,
    'status': 'PASS' if ready_day_count >= 5 else 'WAITING',
}
if pending_day_count:
    print('  full_chain_complete_return_status: PENDING')
    print('  full_chain_complete_return_pending: T+1 未回填，禁止输出收益结论')
elif full_chain:
    print('  full_chain_complete_return_status: READY')
print('  full_chain_complete_gate: ' + json.dumps(full_chain_gate, ensure_ascii=False))
PY

if [ "$MANUAL_LIVE_DECISION_DAY" = "1" ]; then
    echo ""
    echo "[3/5] 跳过收益回填：当天仅持久化事前决策证据..."
else
    echo ""
    echo "[3/5] 回填可用历史收益..."
    BACKFILL_STARTED_SECONDS=$SECONDS
    if ! python3 scripts/xiaogu_return_backfill.py 2>&1 | tail -20; then
        PIPELINE_REQUIRED_FAILURE=1
        echo "  T+1 settlement required step failed; closure will record FAIL" >&2
    fi
    BACKFILL_ELAPSED_SECONDS=$((SECONDS - BACKFILL_STARTED_SECONDS))
    echo "  Return backfill elapsed: ${BACKFILL_ELAPSED_SECONDS}s"
fi

# Bounded data-disk hygiene: force-rerun garbage / oversized runtime JSON.
# Never deletes DB picks/returns. Safe for pick chain.
echo ""
echo "[3.5/5] 数据盘有界清理（force 重跑垃圾 / oversized runtime）..."
python3 scripts/xiaogu_data_retention.py --apply --keep-runtimes "${XIAOGU_KEEP_RUNTIMES_PER_DAY:-3}" --keep-snapshots "${XIAOGU_KEEP_SNAPSHOTS_PER_DAY:-5}" 2>&1 | tail -12 || true

# Knowledge assets are required evidence for an active production run. The DB
# snapshot remains authoritative; vector/Obsidian failures therefore fail the
# run visibly instead of being silently downgraded.
echo ""
echo "[4/5] 知识资产导出（正式票+前十 why/returns → summary/Obsidian/pgvector）..."
python3 - "${XIAOGU_PRODUCTION_RUN_ID}" <<'PY'
import sys
from xiaogu_db import update_production_run_step
update_production_run_step(sys.argv[1], 'knowledge_export', 'RUNNING', required=True)
PY
if ! python3 scripts/xiaogu_knowledge_asset_export.py --date "${DATE}" --production-run-id "${XIAOGU_PRODUCTION_RUN_ID}" 2>&1 | tail -12; then
  python3 - "${XIAOGU_PRODUCTION_RUN_ID}" <<'PY'
import sys
from xiaogu_db import update_production_run_status, update_production_run_step
run_id = sys.argv[1]
update_production_run_step(run_id, 'knowledge_export', 'FAIL', required=True, error_message='KNOWLEDGE_EXPORT_FAILED')
update_production_run_status(run_id, 'FAIL', error_message='KNOWLEDGE_EXPORT_FAILED')
PY
  PIPELINE_REQUIRED_FAILURE=1
else
  python3 - "${XIAOGU_PRODUCTION_RUN_ID}" <<'PY'
import sys
from xiaogu_db import update_production_run_step
update_production_run_step(sys.argv[1], 'knowledge_export', 'PASS', required=True)
PY
fi

echo ""
echo "[5/5] 生成 cohort 与系统闭环门禁..."
DATABASE_URL="${DATABASE_URL:-postgresql://xiaogu:xiaogu@localhost:5432/xiaogu}" python3 - "${DATE}" "${MANUAL_LIVE_DECISION_DAY}" "${XIAOGU_PRODUCTION_RUN_ID}" "${PIPELINE_REQUIRED_FAILURE}" <<'PY'
import json
import sys
from datetime import date
from pathlib import Path

from xiaogu_backtest_v0_1 import _limitup_gene_signal_audit, build_daily_closure, build_db_cohort_report, build_db_completeness_gate, write_daily_closure
from xiaogu_db import (
    fetch_daily_candidates,
    fetch_picks,
    fetch_production_run_steps,
    fetch_returns,
    fetch_signals,
    fetch_latest_scan_session,
    get_db,
    set_active_production_run,
    update_production_run_status,
    update_production_run_step,
)

trade_date = date.fromisoformat(sys.argv[1])
manual_live_decision_day = sys.argv[2] == '1'
production_run_id = sys.argv[3]
required_failure = sys.argv[4] == '1'
update_production_run_step(production_run_id, 'closure', 'RUNNING', required=True)
run_steps = fetch_production_run_steps(production_run_id)
knowledge_status = next(
    (str(step.get('status') or '') for step in run_steps if step.get('step_name') == 'knowledge_export'),
    'PENDING',
)
summary_path = Path('summary/return_backfill_results.json')
backfill_stats = {} if manual_live_decision_day else (json.loads(summary_path.read_text()) if summary_path.exists() else {})
report = build_db_cohort_report()
candidate_rows = fetch_daily_candidates(trade_date, production_run_id=production_run_id)
pick_rows = fetch_picks(trade_date, production_run_id=production_run_id)
candidate_snapshot_id = str(next(
    (row.get('candidate_snapshot_id') for row in candidate_rows if row.get('candidate_snapshot_id')),
    production_run_id,
))
active_pick_id = next((row.get('id') for row in pick_rows if row.get('id') is not None), None)
paper_pick_written = any(
    str(row.get('decision') or '').upper() == 'PAPER_PICK'
    for row in pick_rows
)
db_gate = build_db_completeness_gate(
    sys.argv[1],
    mode='LIVE_DECISION_DAY',
    candidate_rows=candidate_rows,
    pick_rows=pick_rows,
    return_rows=fetch_returns(trade_date, production_run_id=production_run_id),
    signal_rows=fetch_signals(trade_date, production_run_id=production_run_id),
    scan_session=fetch_latest_scan_session(trade_date, production_run_id=production_run_id),
)
closure = build_daily_closure(
    sys.argv[1], report, backfill_stats,
    scan_completed=True,
    paper_pick_written=paper_pick_written,
    return_backfill_completed=False if manual_live_decision_day else None,
    run_mode='LIVE_DECISION_DAY' if manual_live_decision_day else 'LIVE_DAILY_PIPELINE',
    production_run_id=production_run_id,
    candidate_snapshot_id=candidate_snapshot_id,
    active_pick_id=active_pick_id,
    run_steps=run_steps,
    knowledge_status=knowledge_status,
)
closure['db_completeness_gate'] = db_gate
closure['db_completeness_summary'] = db_gate['db_completeness_summary']
closure['data_completeness_gate'] = {
    'status': db_gate['status'],
    'checks': db_gate['checks'],
    'candidate_pool_status': db_gate['candidate_pool_status'],
    'reason': db_gate['candidate_pool_warning_reason'],
}
closure['production_chain'] = {
    'scanner': 'scrapy_scanner/runner_v2.py',
    'transport': 'direct_api',
    'runner': 'xiaogu_forward_runner.py',
    'fallbacks': [],
    'sidecars': [],
}
if manual_live_decision_day:
    candidates = fetch_daily_candidates(trade_date, production_run_id=production_run_id)
    closure.update({
        'data_source': 'LIVE_SCAN_TO_DB_SNAPSHOT',
        'uses_future_data_for_decision': False,
        'return_validation_status': 'PENDING_T1_VALIDATION',
        'db_completeness_gate': db_gate,
        'db_completeness_summary': db_gate['db_completeness_summary'],
        'limitup_gene_signal_audit': _limitup_gene_signal_audit(
            [{'trade_date': sys.argv[1], 'paper': {}, 'day': candidates}],
            {'daily_cases': []}, run_mode='LIVE_DECISION_DAY',
        ),
    })
closure_status = 'FAIL' if required_failure else 'PASS'
if closure_status == 'PASS':
    try:
        with get_db() as db:
            update_production_run_step(production_run_id, 'closure', 'PASS', required=True, db=db)
            update_production_run_status(production_run_id, 'PASS', db=db)
            set_active_production_run(
                trade_date,
                production_run_id,
                candidate_snapshot_id=candidate_snapshot_id,
                active_pick_id=active_pick_id,
                db=db,
            )
    except Exception as exc:
        closure_status = 'FAIL'
        closure['active_publication_error'] = repr(exc)[:500]
        update_production_run_step(
            production_run_id,
            'closure',
            'FAIL',
            required=True,
            error_message='ACTIVE_PUBLICATION_FAILED',
        )
        update_production_run_status(
            production_run_id,
            'FAIL',
            error_message='ACTIVE_PUBLICATION_FAILED',
        )
else:
    update_production_run_step(
        production_run_id,
        'closure',
        'FAIL',
        required=True,
        error_message='CLOSURE_BUILD_FAILED',
    )
    update_production_run_status(production_run_id, 'FAIL', error_message='CLOSURE_BUILD_FAILED')
closure['production_run_steps'] = fetch_production_run_steps(production_run_id)
output_path = write_daily_closure(closure)
print(json.dumps(closure, ensure_ascii=False, sort_keys=True, default=str))
print(f'  daily_closure_latest: {output_path}')
if closure_status != 'PASS':
    raise SystemExit(1)
PY

if [ "$PIPELINE_REQUIRED_FAILURE" = "1" ]; then
    exit 1
fi

# Shadow profit candidates (主力/游资主线): always write even on NO_PICK days.
# Diagnostic only — does NOT change official PAPER_PICK gates or ledger.
echo ""
echo "[4.2/5] 影子获利候选（主线资金+主力净流入，observation_only）..."
python3 scripts/xiaogu_profit_candidates_shadow.py \
  --date "${DATE}" \
  --top "${XIAOGU_PROFIT_CANDIDATES_TOP:-5}" \
  --compare-official 2>&1 | tail -20 || true

# Bounded self-evolution is observation-only. Production scoring has one owner:
# the runner's frozen scoring/config path, never a post-result research job.
echo ""
echo "[4.3/5] 有界因子自进化（仅 dry-run）..."
python3 scripts/xiaogu_safe_self_evolve.py --date "${DATE}" --dry-run 2>&1 | tail -12 || true

# 输出结果
echo ""
echo "=========================================="
echo "出票结果"
echo "=========================================="
tail -1 forward_paper_ledger_v0_1.jsonl 2>/dev/null | python3 -c "
import sys, json
try:
    r = json.loads(sys.stdin.read())
    print(f'日期: {r.get(\"date\")}')
    print(f'决策: {r.get(\"decision\")}')
    print(f'标的: {r.get(\"symbol\")}')
    feat = r.get('features_used', {})
    card = feat.get('single_target_card', {}) if isinstance(feat, dict) else {}
    print(f'名称: {card.get(\"name\", \"N/A\")}')
    print(f'分数: {card.get(\"final_score\", \"N/A\")}')
except:
    print('无法解析出票结果')
"
