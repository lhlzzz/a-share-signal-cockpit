#!/usr/bin/env bash
# xiaogu 每日扫描脚本 - 支持早盘/尾盘扫描
# 用法:
#   bash daily_scan.sh              # 默认扫描当前时间
#   bash daily_scan.sh morning      # 早盘扫描 (9:30开盘后)
#   bash daily_scan.sh afternoon    # 尾盘扫描 (14:30后)
#   bash daily_scan.sh 2026-07-07   # 指定日期扫描

set -euo pipefail

WORKSPACE="${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$WORKSPACE"
python3 scripts/xiaogu_ensure_database.py

# 解析参数
SCAN_TYPE="${1:-auto}"
DATE="${SCAN_TYPE}"
if [[ "$SCAN_TYPE" =~ ^(morning|afternoon|auto)$ ]]; then
    DATE="$(date +%Y-%m-%d)"
fi

HOUR=$(date +%H)
MINUTE=$(date +%M)

echo "=========================================="
echo "xiaogu 数据扫描 - ${DATE}"
echo "=========================================="
echo "扫描类型: ${SCAN_TYPE}"
echo "当前时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 确定扫描类型
if [[ "$SCAN_TYPE" == "morning" ]] || [[ "$SCAN_TYPE" == "auto" && $HOUR -lt 12 ]]; then
    SCAN_LABEL="早盘扫描"
    SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan_morning"
elif [[ "$SCAN_TYPE" == "afternoon" ]] || [[ "$SCAN_TYPE" == "auto" && $HOUR -ge 12 ]]; then
    SCAN_LABEL="尾盘扫描"
    SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan_afternoon"
else
    SCAN_LABEL="全量扫描"
    SCAN_DIR="data/live_scan/${DATE}/eastmoney_scan"
fi

echo "扫描标签: ${SCAN_LABEL}"
echo "输出目录: ${SCAN_DIR}"
echo ""

# 运行 Scanner (31个数据域全量扫描)
echo "[1/2] 运行 Scanner (31个域全量扫描)..."
echo "------------------------------------------"
python3 scrapy_scanner/runner_v2.py --output-dir "${SCAN_DIR}" 2>&1

# 创建 symlink 让 Runner 找到数据
echo ""
echo "[2/2] 创建 symlink..."
ln -sf "$(basename ${SCAN_DIR})" "data/live_scan/${DATE}/eastmoney_web_tabs_scan_v0_1" 2>/dev/null || true
ln -sf "$(basename ${SCAN_DIR})" "data/live_scan/${DATE}/eastmoney_scan" 2>/dev/null || true
echo "  symlink 创建完成"

# 输出汇总
echo ""
echo "=========================================="
echo "扫描完成汇总"
echo "=========================================="
echo "扫描类型: ${SCAN_LABEL}"
echo "输出目录: ${SCAN_DIR}"
echo "数据文件: $(ls -1 ${SCAN_DIR}/*.jsonl 2>/dev/null | wc -l) 个"
echo ""

# 显示各域数据量
echo "数据域详情:"
echo "------------------------------------------"
python3 -c "
import json
from pathlib import Path

scan_dir = Path('${SCAN_DIR}')
summary_file = scan_dir / 'scan_summary.json'
if summary_file.exists():
    with open(summary_file) as f:
        summary = json.load(f)
    domains = summary.get('domains', {})
    total = summary.get('total_items', 0)

    # 按类别分组显示
    categories = {
        '行情中心': ['stock_all_a', 'sector_industry', 'sector_concept', 'sector_region', 'indexes',
                    'limitup_pool', 'limitup_broken', 'limitup_consecutive', 'limitup_yesterday',
                    'block_trades', 'trading_halts', 'popularity_rank'],
        '资金流': ['stock_capital_flow', 'sector_capital_flow', 'market_capital_flow',
                 'flow_industry', 'flow_concept'],
        '数据中心': ['lhb', 'hsgt_summary', 'hsgt_deals', 'hsgt_holdings',
                  'earnings_preview', 'lockup_expiry', 'org_survey', 'margin_trading',
                  'shareholder_changes', 'ipo_calendar'],
        '研报/公告/新闻': ['stock_reports', 'industry_reports', 'announcements', 'news_kuaixun'],
    }

    for cat, keys in categories.items():
        cat_total = sum(domains.get(k, 0) for k in keys)
        print(f'\n{cat} ({cat_total}条):')
        for k in keys:
            count = domains.get(k, 0)
            status = '✅' if count > 0 else '⚠️'
            print(f'  {status} {k:25} {count:5} 条')

    print(f'\n------------------------------------------')
    print(f'总计: {total} 条数据')
else:
    print('扫描汇总文件不存在')
"

echo ""
echo "=========================================="
echo "扫描完成时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
