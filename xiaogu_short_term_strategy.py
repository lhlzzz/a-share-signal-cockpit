#!/usr/bin/env python3
"""短线交易策略生成器。

根据市场环境生成保守的交易策略建议。
"""
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def generate_strategy(market_sentiment: str, volume_status: str, profit_effect: str) -> Dict[str, Any]:
    """生成短线策略。"""
    # 交易节奏
    if market_sentiment in ('乐观',) and volume_status == '放量':
        rhythm = '积极博弈'
        position_pct = '单票不超过20%'
    elif market_sentiment in ('偏多',) and profit_effect in ('强', '偏强'):
        rhythm = '轻仓试错'
        position_pct = '单票不超过10%'
    elif market_sentiment in ('恐慌',):
        rhythm = '空仓观望'
        position_pct = '空仓'
    elif market_sentiment in ('偏空',):
        rhythm = '控仓等待'
        position_pct = '总仓位不超过30%'
    else:
        rhythm = '控仓观察'
        position_pct = '总仓位不超过50%'

    # 适合关注的板块类型
    focus_sectors = []
    if market_sentiment in ('乐观', '偏多'):
        focus_sectors.append('强势板块领涨股')
        focus_sectors.append('资金持续流入板块')
    if volume_status == '放量':
        focus_sectors.append('放量突破个股')
    focus_sectors.append('政策受益方向')

    # 需要规避的方向
    avoid_sectors = []
    if market_sentiment in ('恐慌', '偏空'):
        avoid_sectors.append('所有追高操作')
    avoid_sectors.append('一日游题材')
    avoid_sectors.append('尾盘无量拉升个股')
    avoid_sectors.append('高位放量滞涨个股')
    avoid_sectors.append('利好兑现后个股')

    # 入场条件
    entry_conditions = []
    if rhythm == '积极博弈':
        entry_conditions.append('板块龙头首封或二封')
        entry_conditions.append('放量突破关键压力位')
    elif rhythm == '轻仓试错':
        entry_conditions.append('回踩支撑不破')
        entry_conditions.append('缩量企稳后放量')
    else:
        entry_conditions.append('仅观察，不建议入场')

    # 止盈止损
    take_profit = '盈利5-8%考虑减仓'
    stop_loss = '亏损3%必须止损'

    # 适合人群
    suitable_for = []
    if rhythm == '积极博弈':
        suitable_for.append('有经验的短线交易者')
        suitable_for.append('能快速反应')
    elif rhythm == '轻仓试错':
        suitable_for.append('有纪律的交易者')
    else:
        suitable_for.append('建议观望')

    # 必须避开的误区
    mistakes_to_avoid = [
        '盲目追高',
        '不止损',
        '重仓博弈',
        '追一日游题材',
        '尾盘无量拉升次日追高',
        '利好兑现后接盘',
    ]

    return {
        'rhythm': rhythm,
        'position_pct': position_pct,
        'focus_sectors': focus_sectors,
        'avoid_sectors': avoid_sectors,
        'entry_conditions': entry_conditions,
        'take_profit': take_profit,
        'stop_loss': stop_loss,
        'suitable_for': suitable_for,
        'mistakes_to_avoid': mistakes_to_avoid,
    }


def format_strategy_report(strategy: Dict[str, Any], trade_date: str) -> str:
    """格式化策略报告。"""
    lines = []
    lines.append(f"# 明日交易节奏建议 ({trade_date})\n")

    lines.append("## 当前节奏\n")
    lines.append(f"**{strategy['rhythm']}**\n")

    lines.append("## 仓位建议\n")
    lines.append(f"- {strategy['position_pct']}\n")

    lines.append("## 可关注方向\n")
    for s in strategy['focus_sectors']:
        lines.append(f"- ✅ {s}")
    lines.append("")

    lines.append("## 需要规避方向\n")
    for s in strategy['avoid_sectors']:
        lines.append(f"- ⚠️ {s}")
    lines.append("")

    lines.append("## 入场条件\n")
    for c in strategy['entry_conditions']:
        lines.append(f"- {c}")
    lines.append("")

    lines.append("## 止盈止损\n")
    lines.append(f"- 止盈: {strategy['take_profit']}")
    lines.append(f"- 止损: {strategy['stop_loss']}")
    lines.append("")

    lines.append("## 适合人群\n")
    for p in strategy['suitable_for']:
        lines.append(f"- {p}")
    lines.append("")

    lines.append("## 必须避开的误区\n")
    for m in strategy['mistakes_to_avoid']:
        lines.append(f"- ❌ {m}")
    lines.append("")

    lines.append("---\n")
    lines.append("*免责声明：以上仅为行情逻辑分析，不构成任何投资建议。*")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='短线策略生成')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--sentiment', default='震荡', help='市场情绪')
    ap.add_argument('--volume', default='平量', help='量能状态')
    ap.add_argument('--profit', default='一般', help='赚钱效应')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    strategy = generate_strategy(args.sentiment, args.volume, args.profit)
    report = format_strategy_report(strategy, args.date)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding='utf-8')
        print(f'报告已保存到: {args.output}')
    else:
        print(report)
