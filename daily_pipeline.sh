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
        echo "Usage: bash daily_pipeline.sh --manual-return-backfill YYYY-MM-DD --validate-on YYYY-MM-DD" >&2
        exit 2
    fi
    VALIDATION_TRADE_DATE="$4"
    echo "Running T1_VALIDATION: ${INPUT_TRADE_DATE} -> ${VALIDATION_TRADE_DATE}"
    python3 scripts/xiaogu_return_backfill.py --trade-date "$INPUT_TRADE_DATE" --validate-on "$VALIDATION_TRADE_DATE"
    # pick_id hygiene is owned by xiaogu_return_backfill (backfill_return_pick_ids after writes)
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
    # After T+1 validation: refresh shadow profit candidates with returns + vs-official compare.
    echo ""
    echo "[T1] 影子获利候选 T+1 回填对比（observation_only）..."
    python3 scripts/xiaogu_profit_candidates_shadow.py \
      --date "${INPUT_TRADE_DATE}" \
      --top "${XIAOGU_PROFIT_CANDIDATES_TOP:-5}" \
      --with-returns \
      --compare-official 2>&1 | tail -20 || true
    # After T+1 validation closure is fresh: chain self-evolves bounded knobs when gate READY.
    echo ""
    echo "[T1] 有界因子自进化（gate READY 时自动 apply）..."
    python3 scripts/xiaogu_safe_self_evolve.py --date "${INPUT_TRADE_DATE}" --apply-if-ready 2>&1 | tail -12 || true
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

# Social sidecar: Eastmoney Guba via public JSON API (HTML/CDP only as fallback).
# Failure remains explicit and cannot bypass the official risk gates.
SOCIAL_GATE_STATUS="DISABLED"
SOCIAL_GATE_REASON="XIAOGU_SOCIAL_DISABLED"
if [ "${XIAOGU_SOCIAL_ENABLED:-1}" = "1" ]; then
    echo ""
    echo "[2/6] 收集东财股吧证据..."
    SOCIAL_SOURCES="${XIAOGU_SOCIAL_SOURCES:-eastmoney_guba}"
    # Expanded default coverage (was 25–50); still bounded, not full 400 LLM read.
    SOCIAL_TOPN="${XIAOGU_SOCIAL_TOPN:-150}"
    SOCIAL_STATUS_FILE="$(mktemp -t xiaogu_social_status.XXXXXX)"
    # Compact status line for gate detection (avoid rg on multi-MB pretty JSON).
    SOCIAL_OUTPUT="$(python3 xiaogu_social_sentiment.py \
        --trade-date "${DATE}" \
        --from-scan "data/live_scan/${DATE}/${SCAN_DIR}/eastmoney_web_tabs_summary_runner.json" \
        --sources "${SOCIAL_SOURCES}" \
        --topn "${SOCIAL_TOPN}" \
        --ensure-formal-top10 \
        --status-file "${SOCIAL_STATUS_FILE}" 2>&1)" || true
    printf '%s\n' "$SOCIAL_OUTPUT" | tail -8
    SOCIAL_TOP_STATUS=""
    if [ -s "${SOCIAL_STATUS_FILE}" ]; then
        SOCIAL_TOP_STATUS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "${SOCIAL_STATUS_FILE}" 2>/dev/null || true)"
    fi
    rm -f "${SOCIAL_STATUS_FILE}" || true
    if [ "${SOCIAL_TOP_STATUS}" = "PASS" ] || printf '%s\n' "$SOCIAL_OUTPUT" | rg -q '"status": "PASS"'; then
        SOCIAL_GATE_STATUS="PASS"
        SOCIAL_GATE_REASON=""
    else
        SOCIAL_GATE_STATUS="WARN"
        SOCIAL_GATE_REASON="SOCIAL_SOURCE_UNAVAILABLE"
    fi
    echo "  social_gate=${SOCIAL_GATE_STATUS} top_status=${SOCIAL_TOP_STATUS:-unknown}"
fi
export SOCIAL_GATE_STATUS SOCIAL_GATE_REASON

# Pre-pick soft market context from @sszcw last 3 days (diagnostic + soft sector bias only).
# Live primary: CloakChrome/CDP rendered timeline, then Scrapy/public fallback.
# Optional: X_BEARER_TOKEN / XIAOGU_SSZCW_LIVE_URL / live_inbox.jsonl for extra replies.
# Seed only as fallback. Never forces PAPER_PICK.
echo ""
echo "[2.5/6] 生成 @sszcw 近3日市场上下文（soft only, live prefer）..."
python3 scripts/xiaogu_sszcw_market_context.py \
    --date "${DATE}" \
    --days 3 \
    --prefer-live \
    --no-seed \
    --handles "${XIAOGU_SSZCW_HANDLES:-sszcw}" 2>&1 | tail -12 || true

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

# Soft social backfill after runner so formal PAPER_PICK + top10 always have gbapi evidence.
if [ "${XIAOGU_SOCIAL_ENABLED:-1}" = "1" ]; then
    echo ""
    echo "[3.2/6] 补采 social（正式票 + top10）..."
    python3 xiaogu_social_sentiment.py \
        --trade-date "${DATE}" \
        --ensure-formal-top10 \
        --topn 20 \
        --sources "${XIAOGU_SOCIAL_SOURCES:-eastmoney_guba}" 2>&1 | tail -6 || true
fi
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
    # Explicit filler path for same-day pending T+1 (blocked until exit day 15:05).
    python3 xiaogu_forward_result_filler_v0_1.py --date "${DATE}" --fill-all-pending --auto-eastmoney 2>&1 | tail -15 || true
    # Hygiene: link returns.pick_id by trade_date+symbol when possible (PAPER_PICK JOIN path).
    python3 - <<'PY'
from xiaogu_db import backfill_return_pick_ids
stats = backfill_return_pick_ids()
print(f"  returns.pick_id backfill: linked={stats.get('linked')} null_remaining={stats.get('null_pick_id_remaining')} total={stats.get('returns_total')}")
PY
    BACKFILL_ELAPSED_SECONDS=$((SECONDS - BACKFILL_STARTED_SECONDS))
    echo "  Return backfill elapsed: ${BACKFILL_ELAPSED_SECONDS}s"
fi

# Bounded data-disk hygiene: force-rerun garbage / oversized runtime JSON.
# Never deletes DB picks/returns. Safe for pick chain.
echo ""
echo "[4.5/6] 数据盘有界清理（force 重跑垃圾 / oversized runtime）..."
python3 scripts/xiaogu_data_retention.py --apply --keep-runtimes "${XIAOGU_KEEP_RUNTIMES_PER_DAY:-3}" --keep-snapshots "${XIAOGU_KEEP_SNAPSHOTS_PER_DAY:-5}" 2>&1 | tail -12 || true

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

# Shadow profit candidates (主力/游资主线): always write even on NO_PICK days.
# Diagnostic only — does NOT change official PAPER_PICK gates or ledger.
echo ""
echo "[5.4/6] 影子获利候选（主线资金+主力净流入，observation_only）..."
python3 scripts/xiaogu_profit_candidates_shadow.py \
  --date "${DATE}" \
  --top "${XIAOGU_PROFIT_CANDIDATES_TOP:-5}" \
  --compare-official 2>&1 | tail -20 || true

# Bounded self-evolution is owned by the pick chain (not a manual operator step).
# --apply-if-ready writes scoring_config knobs only when production_ranking_change_gate is READY_*.
# Still forbids formal sort-key rewrite and freeze_paper_pick.
echo ""
echo "[5.5/6] 有界因子自进化（gate READY 时自动 apply）..."
if [ "${XIAOGU_SAFE_SELF_EVOLVE_DRY_RUN:-0}" = "1" ]; then
    python3 scripts/xiaogu_safe_self_evolve.py --date "${DATE}" --dry-run 2>&1 | tail -12 || true
else
    python3 scripts/xiaogu_safe_self_evolve.py --date "${DATE}" --apply-if-ready 2>&1 | tail -12 || true
fi

# Second-brain knowledge assets: formal pick + top10 why/returns → summary + Obsidian + TOP10 vectors.
echo ""
echo "[5.6/6] 知识资产导出（正式票+前十 why/returns → summary/Obsidian/pgvector）..."
python3 scripts/xiaogu_knowledge_asset_export.py --date "${DATE}" 2>&1 | tail -12 || true

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
