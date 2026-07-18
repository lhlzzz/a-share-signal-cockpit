#!/usr/bin/env python3
"""每日A股复盘报告生成器。

整合所有分析模块，生成完整的每日复盘报告。
"""
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from xiaogu_market_overview_analyzer import analyze_market_overview, format_overview_report
from xiaogu_volume_analyzer import analyze_volume, format_volume_report
from xiaogu_index_level_analyzer import analyze_all_indices, format_indices_report
from xiaogu_sector_flow_analyzer import fetch_sector_fund_flow, format_sector_flow_report
from xiaogu_short_term_strategy import generate_strategy, format_strategy_report


DISCLAIMER = """
---

## 0. 免责声明

本报告仅基于公开行情、资金、技术、消息面数据做逻辑分析，不构成任何投资建议、买入建议、卖出建议或仓位指导。股市有风险，交易需谨慎，所有决策由使用者独立承担。

"""


def generate_daily_report(trade_date: str) -> Dict[str, Any]:
    """生成完整的每日复盘报告。"""
    print(f"正在生成 {trade_date} 的每日复盘报告...")

    errors = []

    # 1. 市场大局
    print("  [1/5] 分析市场大局...")
    try:
        overview = analyze_market_overview(trade_date)
    except Exception as e:
        overview = {'error': str(e)}
        errors.append(f"市场大局分析失败: {e}")

    # 2. 量能分析
    print("  [2/5] 分析量能...")
    try:
        volume = analyze_volume(trade_date)
    except Exception as e:
        volume = {'error': str(e)}
        errors.append(f"量能分析失败: {e}")

    # 3. 指数价位
    print("  [3/5] 分析指数价位...")
    try:
        indices = analyze_all_indices(trade_date)
    except Exception as e:
        indices = {'error': str(e)}
        errors.append(f"指数价位分析失败: {e}")

    # 4. 板块资金
    print("  [4/5] 分析板块资金...")
    try:
        sector_flow = fetch_sector_fund_flow(trade_date)
    except Exception as e:
        sector_flow = {'error': str(e)}
        errors.append(f"板块资金分析失败: {e}")

    # 5. 策略生成
    print("  [5/5] 生成策略建议...")
    sentiment = overview.get('sentiment', '震荡') if isinstance(overview, dict) else '震荡'
    vol_status = volume.get('volume_status', '平量') if isinstance(volume, dict) else '平量'
    profit = overview.get('profit_effect', '一般') if isinstance(overview, dict) else '一般'
    strategy = generate_strategy(sentiment, vol_status, profit)

    # 组装报告
    report_parts = []
    report_parts.append(f"# 今日 A 股完整复盘报告 ({trade_date})")
    report_parts.append(DISCLAIMER)

    # 市场总览
    if 'error' not in overview:
        report_parts.append(format_overview_report(overview))
    else:
        report_parts.append(f"## 1. 今日市场总览\n\n⚠️ {overview['error']}\n")

    # 量能分析
    report_parts.append("\n---\n")
    if 'error' not in volume:
        report_parts.append(format_volume_report(volume))
    else:
        report_parts.append(f"## 2. 量能分析\n\n⚠️ {volume['error']}\n")

    # 指数价位
    report_parts.append("\n---\n")
    if not isinstance(indices, dict) or 'error' not in indices:
        report_parts.append(format_indices_report(indices, trade_date))
    else:
        report_parts.append(f"## 3. 三大指数价位与节奏\n\n⚠️ {indices['error']}\n")

    # 板块资金
    report_parts.append("\n---\n")
    if 'error' not in sector_flow:
        report_parts.append(format_sector_flow_report(sector_flow))
    else:
        report_parts.append(f"## 4. 板块资金流向\n\n⚠️ {sector_flow['error']}\n")

    # 策略建议
    report_parts.append("\n---\n")
    report_parts.append(format_strategy_report(strategy, trade_date))

    # 数据缺失提示
    if errors:
        report_parts.append("\n---\n")
        report_parts.append("## 数据缺失提示\n")
        for e in errors:
            report_parts.append(f"- ⚠️ {e}")

    full_report = '\n'.join(report_parts)

    return {
        'trade_date': trade_date,
        'report': full_report,
        'errors': errors,
        'overview': overview,
        'volume': volume,
        'indices': indices,
        'sector_flow': sector_flow,
        'strategy': strategy,
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='每日A股复盘报告生成')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    result = generate_daily_report(args.date)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result['report'], encoding='utf-8')
        print(f'报告已保存到: {args.output}')
        if result['errors']:
            print(f'警告: {len(result["errors"])} 个模块有数据缺失')
    else:
        print(result['report'])
