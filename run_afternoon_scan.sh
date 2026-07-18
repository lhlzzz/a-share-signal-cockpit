#!/bin/bash
# 午盘扫描出票启动脚本
# 每天下午14:30运行

cd /workspace/hermes-workspaces/xiaogu

DATE=$(date +%Y-%m-%d)
TIME="14:30"

echo "=== 午盘扫描出票 ==="
echo "日期: $DATE"
echo "时间: $TIME"
echo ""

# 运行午盘扫描 + 出票
bash daily_pipeline.sh "$DATE"

echo ""
echo "=== 完成 ==="
