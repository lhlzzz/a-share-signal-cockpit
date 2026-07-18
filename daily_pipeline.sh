#!/usr/bin/env bash
# xiaogu 每日出票流程 - v2 Scanner (API直接) + Runner 出票
# 用法: bash daily_pipeline.sh [日期]
# 示例: bash daily_pipeline.sh 2026-07-06

set -euo pipefail

WORKSPACE="/workspace/hermes-workspaces/xiaogu"
cd "$WORKSPACE"
python3 scripts/xiaogu_ensure_database.py

if [ "${1:-}" = "--manual-return-backfill" ]; then
    INPUT_TRADE_DATE="${2:-}"
    if [ -z "$INPUT_TRADE_DATE" ] || [ "${3:-}" != "--validate-on" ] || [ -z "${4:-}" ]; then
        echo "Usage: bash daily_pipeline.sh --manual-return-backfill YYYY-MM-DD --validate-on YYYY-MM-DD" >&2
        exit 2
    fi
    VALIDATION_TRADE_DATE="$4"
    echo "Running T1_VALIDATION: ${INPUT_TRADE_DATE} -> ${VALIDATION_TRADE_DATE}"
    python3 scripts/xiaogu_return_backfill.py --trade-date "$INPUT_TRADE_DATE" --validate-on "$VALIDATION_TRADE_DATE"
    python3 - "$INPUT_TRADE_DATE" "$VALIDATION_TRADE_DATE" <<'PY'
import json
import sys
from datetime import date
from pathlib import Path

from xiaogu_backtest_v0_1 import (
    _limitup_gene_signal_audit, build_daily_closure, build_db_cohort_report,
    build_db_completeness_gate, build_historical_live_replay_closure, write_daily_closure,
)
from xiaogu_db import fetch_daily_candidates

input_trade_date, validation_trade_date = sys.argv[1:]
summary_path = Path('summary/return_backfill_results.json')
backfill_stats = json.loads(summary_path.read_text()) if summary_path.exists() else {}
report = build_db_cohort_report()
db_gate = build_db_completeness_gate(
    input_trade_date, mode='T1_VALIDATION', validation_trade_date=validation_trade_date,
)
replay = build_historical_live_replay_closure(
    input_trade_date, validation_trade_date, backfill_stats=backfill_stats,
)
candidates = fetch_daily_candidates(date.fromisoformat(input_trade_date))
manual_status = 'FAIL' if backfill_stats.get('fatal_error') else db_gate['status']
closure = build_daily_closure(
    input_trade_date, report, backfill_stats,
    scan_completed=db_gate['checks']['scan_session_persisted'],
    paper_pick_written=db_gate['checks']['paper_pick_persisted'],
    return_backfill_completed=db_gate['checks']['t1_return_available'],
    run_mode='T1_VALIDATION',
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
print(json.dumps(closure, ensure_ascii=False, sort_keys=True))
print(f'  daily_closure_latest: {output_path}')
PY
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
print(json.dumps(closure, ensure_ascii=False, sort_keys=True))
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

echo "=========================================="
echo "xiaogu 出票流程 (v2 Scanner) - ${DATE}"
echo "=========================================="

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
python3 scrapy_scanner/runner_v2.py \
  --output-dir "data/live_scan/${DATE}/${SCAN_DIR}" 2>&1 | tail -10
SCANNER_ELAPSED_SECONDS=$((SECONDS - SCANNER_STARTED_SECONDS))
echo "  Scanner elapsed: ${SCANNER_ELAPSED_SECONDS}s"

# Step 2: 创建 symlink 让 Runner 找到数据
echo ""
echo "[2/6] 创建 symlink..."
ln -sf "${SCAN_DIR}" "data/live_scan/${DATE}/eastmoney_web_tabs_scan_v0_1" 2>/dev/null || true
ln -sf "${SCAN_DIR}" "data/live_scan/${DATE}/eastmoney_scan" 2>/dev/null || true
echo "  symlink 创建完成"

# Social sidecar: Eastmoney Guba only, using public pages plus CDP fallback.
# Failure remains explicit and cannot bypass the official risk gates.
SOCIAL_GATE_STATUS="DISABLED"
SOCIAL_GATE_REASON="XIAOGU_SOCIAL_DISABLED"
if [ "${XIAOGU_SOCIAL_ENABLED:-1}" = "1" ]; then
    echo ""
    echo "[2/6] 收集东财股吧证据..."
    SOCIAL_SOURCES="${XIAOGU_SOCIAL_SOURCES:-eastmoney_guba}"
    SOCIAL_TOPN="${XIAOGU_SOCIAL_TOPN:-50}"
    SOCIAL_OUTPUT="$(python3 xiaogu_social_sentiment.py \
        --trade-date "${DATE}" \
        --from-scan "data/live_scan/${DATE}/${SCAN_DIR}/eastmoney_web_tabs_summary_runner.json" \
        --sources "${SOCIAL_SOURCES}" \
        --topn "${SOCIAL_TOPN}" 2>&1)" || true
    printf '%s\n' "$SOCIAL_OUTPUT" | tail -8
    if printf '%s\n' "$SOCIAL_OUTPUT" | rg -q '"status": "PASS"'; then
        SOCIAL_GATE_STATUS="PASS"
        SOCIAL_GATE_REASON=""
    else
        SOCIAL_GATE_STATUS="WARN"
        SOCIAL_GATE_REASON="SOCIAL_SOURCE_UNAVAILABLE"
    fi
fi
export SOCIAL_GATE_STATUS SOCIAL_GATE_REASON

# Step 3: 运行 Runner
echo ""
echo "[3/6] 运行 Runner..."
FORWARD_STARTED_SECONDS=$SECONDS
RUNNER_ARGS=(--date "${DATE}" --asof-time "$(date +%H:%M:%S)")
# The scanner has just produced a realtime snapshot in this explicit date
# directory. The runner must consume that same directory rather than silently
# substituting a prior completed trading day during pre-market execution.
RUNNER_ARGS+=(--no-runtime-date-adjust --force)
NO_AUTO_TRADE=1 NO_ORDER_EXECUTION=1 python3 xiaogu_forward_d1_1450_runner_v0_1.py "${RUNNER_ARGS[@]}" 2>&1 | tail -10
FORWARD_ELAPSED_SECONDS=$((SECONDS - FORWARD_STARTED_SECONDS))
TOTAL_ELAPSED_SECONDS=$((SECONDS - PIPELINE_STARTED_SECONDS))
echo "  Forward elapsed: ${FORWARD_ELAPSED_SECONDS}s"
echo "  Total elapsed: ${TOTAL_ELAPSED_SECONDS}s"

# A full-chain row is operationally complete, but its performance conclusion
# remains pending until the return filler records T+1.
DATABASE_URL="${DATABASE_URL:-postgresql://xiaogu:xiaogu@localhost:5432/xiaogu}" python3 - "${DATE}" <<'PY'
import json
import sys
from datetime import date
from xiaogu_db import fetch_daily_candidates, fetch_returns

trade_date = date.fromisoformat(sys.argv[1])
candidates = fetch_daily_candidates(trade_date)
full_chain = [row for row in candidates if row.get('cohort_quality') == 'FULL_CHAIN_COMPLETE']
returns = {str(row.get('symbol') or ''): row.get('t1_return') for row in fetch_returns(trade_date)}
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
    echo "[4/6] 跳过收益回填：当天仅持久化事前决策证据..."
else
    echo ""
    echo "[4/6] 回填可用历史收益..."
    BACKFILL_STARTED_SECONDS=$SECONDS
    python3 scripts/xiaogu_return_backfill.py 2>&1 | tail -20
    BACKFILL_ELAPSED_SECONDS=$((SECONDS - BACKFILL_STARTED_SECONDS))
    echo "  Return backfill elapsed: ${BACKFILL_ELAPSED_SECONDS}s"
fi

echo ""
echo "[5/6] 生成 cohort 与系统闭环门禁..."
DATABASE_URL="${DATABASE_URL:-postgresql://xiaogu:xiaogu@localhost:5432/xiaogu}" python3 - "${DATE}" "${MANUAL_LIVE_DECISION_DAY}" <<'PY'
import json
import sys
from datetime import date
from pathlib import Path

from xiaogu_backtest_v0_1 import _limitup_gene_signal_audit, build_daily_closure, build_db_cohort_report, build_db_completeness_gate, write_daily_closure
from xiaogu_db import fetch_daily_candidates, fetch_picks

trade_date = date.fromisoformat(sys.argv[1])
manual_live_decision_day = sys.argv[2] == '1'
summary_path = Path('summary/return_backfill_results.json')
backfill_stats = {} if manual_live_decision_day else (json.loads(summary_path.read_text()) if summary_path.exists() else {})
report = build_db_cohort_report()
paper_pick_written = any(str(row.get('decision') or '').upper() == 'PAPER_PICK' for row in fetch_picks(trade_date))
db_gate = build_db_completeness_gate(
    sys.argv[1],
    mode='LIVE_DECISION_DAY',
)
closure = build_daily_closure(
    sys.argv[1], report, backfill_stats,
    scan_completed=True,
    paper_pick_written=paper_pick_written,
    return_backfill_completed=False if manual_live_decision_day else None,
    run_mode='LIVE_DECISION_DAY' if manual_live_decision_day else 'LIVE_DAILY_PIPELINE',
)
closure['db_completeness_gate'] = db_gate
closure['db_completeness_summary'] = db_gate['db_completeness_summary']
closure['data_completeness_gate'] = {
    'status': db_gate['status'],
    'checks': db_gate['checks'],
    'candidate_pool_status': db_gate['candidate_pool_status'],
    'reason': db_gate['candidate_pool_warning_reason'],
}
closure['social_signal_gate'] = {
    'status': __import__('os').environ.get('SOCIAL_GATE_STATUS', 'DISABLED'),
    'reason': __import__('os').environ.get('SOCIAL_GATE_REASON', ''),
    'used_for_official_ranking': False,
}
if manual_live_decision_day:
    candidates = fetch_daily_candidates(trade_date)
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
output_path = write_daily_closure(closure)
print(json.dumps(closure, ensure_ascii=False, sort_keys=True))
print(f'  daily_closure_latest: {output_path}')
PY

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
