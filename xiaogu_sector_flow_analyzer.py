#!/usr/bin/env python3
"""板块资金流向分析器。

分析行业板块和概念板块的资金流入流出。
"""
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from xiaogu_db import engine


def fetch_sector_fund_flow(trade_date: str) -> Dict[str, Any]:
    """从数据库获取板块资金流向。"""
    result = {
        'trade_date': trade_date,
        'industry_flows': [],
        'concept_flows': [],
    }

    with engine.connect() as conn:
        # 行业板块资金流向（从 signals 表获取）
        r = conn.execute(text("""
            SELECT
                signal_value
            FROM signals
            WHERE trade_date = :td
              AND signal_key = 'industry_fund_flow'
            LIMIT 1
        """), {'td': trade_date})
        row = r.fetchone()
        if row and row[0]:
            try:
                flow_data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if isinstance(flow_data, list):
                    result['industry_flows'] = flow_data
                elif isinstance(flow_data, dict):
                    result['industry_flows'] = [
                        {'name': k, 'net_inflow': v, 'net_inflow_yi': round(v / 1e8, 2) if v else 0}
                        for k, v in flow_data.items()
                    ]
            except (json.JSONDecodeError, TypeError):
                pass

        # 概念板块资金流向（从 signals 表获取）
        r = conn.execute(text("""
            SELECT
                signal_value
            FROM signals
            WHERE trade_date = :td
              AND signal_key = 'sector_fund_flow'
            LIMIT 1
        """), {'td': trade_date})
        row = r.fetchone()
        if row and row[0]:
            try:
                concept_data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if isinstance(concept_data, dict):
                    result['concept_flows'] = [
                        {'name': k, 'net_inflow': v}
                        for k, v in concept_data.items()
                    ]
            except (json.JSONDecodeError, TypeError):
                pass

    return result


def classify_fund_flow_direction(net_inflow: float) -> str:
    """判断资金流向。"""
    if net_inflow > 1e8:
        return '大幅流入'
    elif net_inflow > 0:
        return '小幅流入'
    elif net_inflow < -1e8:
        return '大幅流出'
    elif net_inflow < 0:
        return '小幅流出'
    else:
        return '平衡'


def assess_persistence(name: str, net_inflow: float) -> str:
    """评估持续性（简化版）。"""
    # 实际应该对比历史数据
    if net_inflow > 5e8:
        return '可能持续'
    elif net_inflow > 1e8:
        return '待观察'
    else:
        return '一日游可能'


def format_sector_flow_report(flow_data: Dict[str, Any]) -> str:
    """格式化板块资金报告。"""
    lines = []
    lines.append(f"# 板块资金流向 ({flow_data['trade_date']})\n")

    # 行业板块
    lines.append("## 行业板块资金流向\n")
    industry = flow_data.get('industry_flows', [])
    if industry:
        lines.append("### 净流入前十\n")
        lines.append("| 排名 | 板块 | 净流入(亿) | 涨跌幅 | 判断 |")
        lines.append("|------|------|-----------|--------|------|")
        for i, f in enumerate(industry[:10], 1):
            direction = classify_fund_flow_direction(f['net_inflow'])
            lines.append(f"| {i} | {f['name']} | {f['net_inflow_yi']:.2f} | {f.get('pct_chg', 0):.2f}% | {direction} |")

        lines.append("\n### 净流出前十\n")
        lines.append("| 排名 | 板块 | 净流出(亿) | 涨跌幅 | 判断 |")
        lines.append("|------|------|-----------|--------|------|")
        for i, f in enumerate(reversed(industry[-10:]), 1):
            direction = classify_fund_flow_direction(f['net_inflow'])
            lines.append(f"| {i} | {f['name']} | {abs(f['net_inflow_yi']):.2f} | {f.get('pct_chg', 0):.2f}% | {direction} |")
    else:
        lines.append("无行业板块资金数据\n")

    # 概念板块
    concepts = flow_data.get('concept_flows', [])
    if concepts:
        lines.append("\n## 概念板块资金流向\n")
        lines.append("| 概念 | 资金方向 |")
        lines.append("|------|----------|")
        for c in concepts[:15]:
            direction = classify_fund_flow_direction(c.get('net_inflow', 0))
            lines.append(f"| {c['name']} | {direction} |")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='板块资金分析')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    flow_data = fetch_sector_fund_flow(args.date)
    report = format_sector_flow_report(flow_data)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding='utf-8')
        print(f'报告已保存到: {args.output}')
    else:
        print(report)
