#!/usr/bin/env python3
"""量能分析器。

分析两市成交额、量价关系、资金活跃度。
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import baostock as bs


def fetch_market_amount(trade_date: str, days: int = 15) -> List[Dict[str, Any]]:
    """获取市场成交额数据。"""
    start_date = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')

    # 获取上证指数成交额
    sh_data = []
    rs = bs.query_history_k_data_plus(
        'sh.000001',
        'date,amount',
        start_date=start_date,
        end_date=trade_date,
        frequency='d'
    )
    while rs.next():
        row = rs.get_row_data()
        if row[1]:
            sh_data.append({'date': row[0], 'amount': float(row[1])})

    # 获取深证成指成交额
    sz_data = []
    rs = bs.query_history_k_data_plus(
        'sz.399001',
        'date,amount',
        start_date=start_date,
        end_date=trade_date,
        frequency='d'
    )
    while rs.next():
        row = rs.get_row_data()
        if row[1]:
            sz_data.append({'date': row[0], 'amount': float(row[1])})

    # 合并数据
    result = []
    sh_map = {d['date']: d['amount'] for d in sh_data}
    sz_map = {d['date']: d['amount'] for d in sz_data}

    for d in sorted(set(list(sh_map.keys()) + list(sz_map.keys()))):
        sh_amt = sh_map.get(d, 0)
        sz_amt = sz_map.get(d, 0)
        result.append({
            'date': d,
            'sh_amount': sh_amt,
            'sz_amount': sz_amt,
            'total_amount': sh_amt + sz_amt,
        })

    return result


def classify_volume_level(today_amount: float, avg_5d: float, avg_10d: float) -> str:
    """判断量能状态。"""
    if avg_5d <= 0:
        return '未知'

    ratio_5d = today_amount / avg_5d

    if ratio_5d >= 1.08:
        return '放量'
    elif ratio_5d <= 0.92:
        return '缩量'
    else:
        return '平量'


def classify_volume_price_combination(change_pct: float, volume_status: str) -> str:
    """判断量价组合。"""
    if change_pct is None:
        return '未知'

    if change_pct > 0 and volume_status == '放量':
        return '放量上涨'
    elif change_pct > 0 and volume_status == '缩量':
        return '缩量上涨'
    elif change_pct < 0 and volume_status == '放量':
        return '放量下跌'
    elif change_pct < 0 and volume_status == '缩量':
        return '缩量下跌'
    else:
        return '平量震荡'


def interpret_volume_signal(volume_status: str, price_change: float) -> str:
    """解读量能信号。"""
    if volume_status == '放量':
        if price_change > 0:
            return '资金积极进场，上涨有支撑'
        else:
            return '资金出逃，下跌有压力'
    elif volume_status == '缩量':
        if price_change > 0:
            return '上涨动能不足，需观察持续性'
        else:
            return '抛压减轻，可能企稳'
    else:
        return '量能平稳，多空平衡'


def suggest_trading_rhythm(volume_status: str, price_change: float, sentiment: str) -> str:
    """建议交易节奏。"""
    if volume_status == '放量' and price_change > 0:
        return '积极博弈'
    elif volume_status == '缩量' and price_change < 0:
        return '控仓观察'
    elif sentiment == '恐慌':
        return '空仓观望'
    elif sentiment == '偏空':
        return '轻仓试错'
    else:
        return '控仓观察'


def analyze_volume(trade_date: str) -> Dict[str, Any]:
    """分析量能。"""
    lg = bs.login()

    try:
        # 获取成交额数据
        amount_data = fetch_market_amount(trade_date, days=20)

        if not amount_data:
            return {'error': '无法获取成交额数据'}

        # 找到今天的成交额
        today_data = None
        for d in amount_data:
            if d['date'] == trade_date:
                today_data = d
                break

        if not today_data:
            return {'error': f'找不到 {trade_date} 的成交额数据'}

        # 计算均量
        amounts = [d['total_amount'] for d in amount_data if d['date'] <= trade_date]
        if len(amounts) < 5:
            return {'error': '历史数据不足'}

        today_amount = amounts[-1]
        avg_5d = sum(amounts[-6:-1]) / 5 if len(amounts) >= 6 else sum(amounts[:-1]) / max(len(amounts) - 1, 1)
        avg_10d = sum(amounts[-11:-1]) / 10 if len(amounts) >= 11 else sum(amounts[:-1]) / max(len(amounts) - 1, 1)

        # 量能状态
        volume_status = classify_volume_level(today_amount, avg_5d, avg_10d)

        # 计算涨跌幅（使用前一天数据）
        prev_amount = amounts[-2] if len(amounts) >= 2 else today_amount
        price_change = 0  # 简化：用成交额变化代替
        if prev_amount > 0:
            price_change = (today_amount / prev_amount - 1) * 100

        # 量价组合
        volume_price_combo = classify_volume_price_combination(price_change, volume_status)

        # 信号解读
        signal_interpretation = interpret_volume_signal(volume_status, price_change)

        # 交易节奏建议
        rhythm_suggestion = suggest_trading_rhythm(volume_status, price_change, '震荡')

        # 沪深占比
        sh_ratio = today_data['sh_amount'] / today_amount * 100 if today_amount > 0 else 0
        sz_ratio = today_data['sz_amount'] / today_amount * 100 if today_amount > 0 else 0

        return {
            'trade_date': trade_date,
            'today_amount': today_amount,
            'today_amount_yi': round(today_amount / 1e8, 2),
            'sh_amount': today_data['sh_amount'],
            'sh_amount_yi': round(today_data['sh_amount'] / 1e8, 2),
            'sz_amount': today_data['sz_amount'],
            'sz_amount_yi': round(today_data['sz_amount'] / 1e8, 2),
            'avg_5d': avg_5d,
            'avg_5d_yi': round(avg_5d / 1e8, 2),
            'avg_10d': avg_10d,
            'avg_10d_yi': round(avg_10d / 1e8, 2),
            'volume_status': volume_status,
            'volume_price_combination': volume_price_combo,
            'signal_interpretation': signal_interpretation,
            'trading_rhythm': rhythm_suggestion,
            'sh_ratio': round(sh_ratio, 1),
            'sz_ratio': round(sz_ratio, 1),
        }

    finally:
        bs.logout()


def format_volume_report(volume: Dict[str, Any]) -> str:
    """格式化量能报告。"""
    if 'error' in volume:
        return f"量能分析失败: {volume['error']}"

    lines = []
    lines.append(f"# 量能分析 ({volume['trade_date']})\n")

    lines.append("## 成交额\n")
    lines.append(f"- 今日两市成交额: **{volume['today_amount_yi']}亿**")
    lines.append(f"- 沪市成交额: {volume['sh_amount_yi']}亿 ({volume['sh_ratio']}%)")
    lines.append(f"- 深市成交额: {volume['sz_amount_yi']}亿 ({volume['sz_ratio']}%)")
    lines.append(f"- 近5日均量: {volume['avg_5d_yi']}亿")
    lines.append(f"- 近10日均量: {volume['avg_10d_yi']}亿")

    lines.append("\n## 量能判断\n")
    lines.append(f"- 今日量能状态: **{volume['volume_status']}**")
    lines.append(f"- 量价组合: **{volume['volume_price_combination']}**")
    lines.append(f"- 信号解读: {volume['signal_interpretation']}")

    lines.append("\n## 交易节奏建议\n")
    lines.append(f"- 建议节奏: **{volume['trading_rhythm']}**")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='量能分析')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    volume = analyze_volume(args.date)
    report = format_volume_report(volume)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding='utf-8')
        print(f'报告已保存到: {args.output}')
    else:
        print(report)
