#!/bin/bash
# xiaogu 完整扫描脚本 - 早盘+尾盘
# 确保数据完整性：扫描、出票、回填收益、生成信号分析

set -euo pipefail

DATE="${1:-$(date +%Y-%m-%d)}"
WORKSPACE="${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
TIME=$(date +%H:%M:%S)

cd "$WORKSPACE"

echo "=========================================="
echo "xiaogu 完整扫描流程"
echo "日期: $DATE"
echo "时间: $TIME"
echo "=========================================="

# Step 1: 运行扫描+出票
echo ""
echo "[1/4] 运行扫描+出票..."
bash daily_pipeline.sh "$DATE"

# Step 2: 回填收益数据
echo ""
echo "[2/4] 回填收益数据..."
python3 xiaogu_data_repair_v0_1.py --task returns --date "$DATE" 2>&1 | tail -5

# Step 3: 生成信号有效性分析
echo ""
echo "[3/4] 生成信号有效性分析..."
python3 xiaogu_data_repair_v0_1.py --task effectiveness --date "$DATE" 2>&1 | tail -5

# Step 4: 记录研究运行
echo ""
echo "[4/4] 记录研究运行..."
python3 xiaogu_data_repair_v0_1.py --task research --date "$DATE" 2>&1 | tail -5

# 输出数据完整性报告
echo ""
echo "=========================================="
echo "数据完整性检查"
echo "=========================================="
psql postgresql://xiaogu:xiaogu@localhost:5432/xiaogu -t -c "
SELECT
  'picks' as table_name, COUNT(*) as count FROM picks WHERE trade_date = '$DATE'
UNION ALL
SELECT 'returns', COUNT(*) FROM returns WHERE trade_date = '$DATE'
UNION ALL
SELECT 'signals', COUNT(*) FROM signals WHERE trade_date = '$DATE'
UNION ALL
SELECT 'signal_effectiveness', COUNT(*) FROM signal_effectiveness WHERE analysis_date = '$DATE'
UNION ALL
SELECT 'research_runs', COUNT(*) FROM research_runs WHERE trade_date = '$DATE'
UNION ALL
SELECT 'daily_candidates', COUNT(*) FROM daily_candidates WHERE trade_date = '$DATE';
" 2>&1

echo ""
echo "=========================================="
echo "扫描完成"
echo "=========================================="
