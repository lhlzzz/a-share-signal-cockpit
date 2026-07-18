#!/usr/bin/env python3
"""指数价位与节奏分析器。

分析三大指数的支撑压力、运行区间、突破跌破应对。
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import baostock as bs


def fetch_index_data(symbol: str, days: int = 60) -> List[Dict[str, Any]]:
    """获取指数历史数据。"""
    end_date = date.today().isoformat()
    start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')

    rs = bs.query_history_k_data_plus(
        symbol,
        'date,open,high,low,close,volume',
        start_date=start_date,
        end_date=end_date,
        frequency='d'
    )
    data = []
    while rs.next():
        row = rs.get_row_data()
        data.append({
            'date': row[0],
            'open': float(row[1]) if row[1] else None,
            'high': float(row[2]) if row[2] else None,
            'low': float(row[3]) if row[3] else None,
            'close': float(row[4]) if row[4] else None,
            'volume': float(row[5]) if row[5] else None,
        })
    return data


def calc_ma(closes: List[float], period: int) -> Optional[float]:
    """计算移动平均线。"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """计算ATR（平均真实波幅）。"""
    if len(highs) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)

    return sum(true_ranges[-period:]) / period


def find_support_resistance(data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算支撑位和压力位。"""
    if len(data) < 20:
        return {'error': '数据不足'}

    closes = [d['close'] for d in data if d['close']]
    highs = [d['high'] for d in data if d['high']]
    lows = [d['low'] for d in data if d['low']]

    current = closes[-1]
    if not current:
        return {'error': '无当前价格'}

    # 均线
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)

    # 近期高低点
    recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)

    # ATR
    atr = calc_atr(highs, lows, closes)

    # 支撑位
    supports = []
    if ma20 and ma20 < current:
        supports.append(('MA20', round(ma20, 2)))
    if ma10 and ma10 < current:
        supports.append(('MA10', round(ma10, 2)))
    supports.append(('近期低点', round(recent_low, 2)))
    if atr:
        supports.append(('ATR支撑', round(current - atr, 2)))

    # 压力位
    resistances = []
    if ma5 and ma5 > current:
        resistances.append(('MA5', round(ma5, 2)))
    if ma10 and ma10 > current:
        resistances.append(('MA10', round(ma10, 2)))
    if ma20 and ma20 > current:
        resistances.append(('MA20', round(ma20, 2)))
    resistances.append(('近期高点', round(recent_high, 2)))
    if atr:
        resistances.append(('ATR压力', round(current + atr, 2)))

    # 排序
    supports.sort(key=lambda x: x[1], reverse=True)
    resistances.sort(key=lambda x: x[1])

    # 短期支撑/压力（最近的）
    short_support = supports[0] if supports else ('无', current * 0.98)
    short_resistance = resistances[0] if resistances else ('无', current * 1.02)

    # 强支撑/压力
    strong_support = supports[-1] if len(supports) > 1 else short_support
    strong_resistance = resistances[-1] if len(resistances) > 1 else short_resistance

    # 判断当前状态
    if current > (ma20 or current) and current > (ma10 or current):
        status = '单边上行'
    elif current < (ma20 or current) and current < (ma10 or current):
        status = '单边下行'
    elif abs(current - (ma20 or current)) / current < 0.02:
        status = '横盘震荡'
    elif current > (ma5 or current):
        status = '冲高遇阻'
    else:
        status = '震荡筑底'

    # 明日运行区间
    if atr:
        expected_low = round(current - atr * 0.5, 2)
        expected_high = round(current + atr * 0.5, 2)
    else:
        expected_low = round(current * 0.99, 2)
        expected_high = round(current * 1.01, 2)

    return {
        'current': round(current, 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'short_support': {'name': short_support[0], 'value': short_support[1]},
        'strong_support': {'name': strong_support[0], 'value': strong_support[1]},
        'short_resistance': {'name': short_resistance[0], 'value': short_resistance[1]},
        'strong_resistance': {'name': strong_resistance[0], 'value': strong_resistance[1]},
        'status': status,
        'expected_range': {'low': expected_low, 'high': expected_high},
        'atr': round(atr, 2) if atr else None,
    }


def analyze_index_level(index_name: str, symbol: str, trade_date: str) -> Dict[str, Any]:
    """分析单个指数。"""
    data = fetch_index_data(symbol, days=60)

    if not data:
        return {'error': f'无法获取 {index_name} 数据'}

    # 找到今天的收盘价
    today_close = None
    for d in reversed(data):
        if d['date'] <= trade_date and d['close']:
            today_close = d['close']
            break

    # 计算支撑压力
    sr = find_support_resistance(data)

    if 'error' in sr:
        return sr

    # 生成建议
    breakout_advice = []
    breakdown_advice = []

    if sr['short_resistance']['value']:
        breakout_advice.append(f"突破 {sr['short_resistance']['name']} ({sr['short_resistance']['value']}) 后可看高一线")
    if sr['strong_resistance']['value']:
        breakout_advice.append(f"强压力 {sr['strong_resistance']['name']} ({sr['strong_resistance']['value']})")

    if sr['short_support']['value']:
        breakdown_advice.append(f"跌破 {sr['short_support']['name']} ({sr['short_support']['value']}) 需警惕")
    if sr['strong_support']['value']:
        breakdown_advice.append(f"强支撑 {sr['strong_support']['name']} ({sr['strong_support']['value']})")

    return {
        'index_name': index_name,
        'symbol': symbol,
        'trade_date': trade_date,
        **sr,
        'breakout_advice': breakout_advice,
        'breakdown_advice': breakdown_advice,
    }


def analyze_all_indices(trade_date: str) -> Dict[str, Any]:
    """分析三大指数。"""
    lg = bs.login()

    try:
        indices = {
            '上证指数': 'sh.000001',
            '深证成指': 'sz.399001',
            '创业板指': 'sz.399006',
        }

        results = {}
        for name, symbol in indices.items():
            results[name] = analyze_index_level(name, symbol, trade_date)

        return results

    finally:
        bs.logout()


def format_index_report(index_data: Dict[str, Any]) -> str:
    """格式化单个指数报告。"""
    if 'error' in index_data:
        return f"{index_data.get('index_name', '指数')}: {index_data['error']}"

    lines = []
    lines.append(f"### {index_data['index_name']}\n")
    lines.append(f"- 当前: {index_data['current']}")
    if index_data.get('ma5'):
        lines.append(f"- MA5: {index_data['ma5']}")
    if index_data.get('ma10'):
        lines.append(f"- MA10: {index_data['ma10']}")
    if index_data.get('ma20'):
        lines.append(f"- MA20: {index_data['ma20']}")

    lines.append(f"\n**支撑位:**")
    lines.append(f"- 短期支撑: {index_data['short_support']['name']} ({index_data['short_support']['value']})")
    lines.append(f"- 强支撑: {index_data['strong_support']['name']} ({index_data['strong_support']['value']})")

    lines.append(f"\n**压力位:**")
    lines.append(f"- 短期压力: {index_data['short_resistance']['name']} ({index_data['short_resistance']['value']})")
    lines.append(f"- 强压力: {index_data['strong_resistance']['name']} ({index_data['strong_resistance']['value']})")

    lines.append(f"\n**当前状态:** {index_data['status']}")
    lines.append(f"**明日预估区间:** {index_data['expected_range']['low']} - {index_data['expected_range']['high']}")

    if index_data.get('breakout_advice'):
        lines.append("\n**突破应对:**")
        for a in index_data['breakout_advice']:
            lines.append(f"- {a}")

    if index_data.get('breakdown_advice'):
        lines.append("\n**跌破应对:**")
        for a in index_data['breakdown_advice']:
            lines.append(f"- {a}")

    return '\n'.join(lines)


def format_indices_report(indices: Dict[str, Any], trade_date: str) -> str:
    """格式化三大指数报告。"""
    lines = []
    lines.append(f"# 三大指数价位与节奏 ({trade_date})\n")

    for name, data in indices.items():
        lines.append(format_index_report(data))
        lines.append("")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='指数价位分析')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    indices = analyze_all_indices(args.date)
    report = format_indices_report(indices, args.date)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding='utf-8')
        print(f'报告已保存到: {args.output}')
    else:
        print(report)
