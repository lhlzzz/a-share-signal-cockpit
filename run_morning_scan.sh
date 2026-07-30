#!/bin/bash
# 早盘扫描出票启动脚本
# 每天早上9:15运行

cd "${XIAOGU_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

DATE=$(date +%Y-%m-%d)
TIME="09:15"

echo "=== 早盘扫描出票 ==="
echo "日期: $DATE"
echo "时间: $TIME"
echo ""

# 运行早盘扫描 + 出票
bash daily_pipeline.sh "$DATE"

echo ""
echo "=== 完成 ==="
