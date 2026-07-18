#!/usr/bin/env python3
"""市场大局分析器。

分析三大指数、成交额、涨跌家数、涨停跌停等，输出市场情绪判断。
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import baostock as bs
from sqlalchemy import text
from xiaogu_db import engine


def fetch_index_kline(symbol: str, start_date: str, end_date: str) -> list:
    """从 baostock 获取指数日K线。"""
    rs = bs.query_history_k_data_plus(
        symbol,
        'date,open,high,low,close,volume,amount',
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
            'amount': float(row[6]) if row[6] else None,
        })
    return data


def fetch_market_stats(trade_date: str) -> Dict[str, Any]:
    """获取市场统计数据（涨跌家数、涨停跌停等）。"""
    stats = {
        'trade_date': trade_date,
        'up_count': 0,
        'down_count': 0,
        'flat_count': 0,
        'limit_up_count': 0,
        'limit_down_count': 0,
        'broken_limit_count': 0,
        'total_amount': 0,
        'sh_amount': 0,
        'sz_amount': 0,
    }

    with engine.connect() as conn:
        # 从 daily_candidates 获取涨跌数据
        r = conn.execute(text("""
            SELECT
                count(CASE WHEN pct_chg > 0 THEN 1 END) as up_count,
                count(CASE WHEN pct_chg < 0 THEN 1 END) as down_count,
                count(CASE WHEN pct_chg = 0 OR pct_chg IS NULL THEN 1 END) as flat_count,
                count(CASE WHEN pct_chg >= 9.5 THEN 1 END) as limit_up_count,
                count(CASE WHEN pct_chg <= -9.5 THEN 1 END) as limit_down_count,
                sum(COALESCE(amount, 0)) as total_amount
            FROM daily_candidates
            WHERE trade_date = :td
        """), {'td': trade_date})
        row = r.fetchone()
        if row:
            stats['up_count'] = row[0] or 0
            stats['down_count'] = row[1] or 0
            stats['flat_count'] = row[2] or 0
            stats['limit_up_count'] = row[3] or 0
            stats['limit_down_count'] = row[4] or 0
            stats['total_amount'] = row[5] or 0

    return stats


def classify_market_sentiment(up_count: int, down_count: int, limit_up: int, limit_down: int) -> str:
    """判断市场情绪。"""
    total = up_count + down_count
    if total == 0:
        return '未知'

    up_ratio = up_count / total
    limit_ratio = (limit_up - limit_down) / max(total, 1)

    if up_ratio > 0.7 and limit_up > 20:
        return '乐观'
    elif up_ratio > 0.55:
        return '偏多'
    elif up_ratio < 0.3 and limit_down > 10:
        return '恐慌'
    elif up_ratio < 0.45:
        return '偏空'
    else:
        return '震荡'


def classify_profit_effect(up_count: int, down_count: int, limit_up: int, limit_down: int) -> str:
    """判断赚钱效应。"""
    total = up_count + down_count
    if total == 0:
        return '未知'

    up_ratio = up_count / total

    if up_ratio > 0.7 and limit_up > 30:
        return '强'
    elif up_ratio > 0.55:
        return '偏强'
    elif up_ratio < 0.3:
        return '弱'
    elif up_ratio < 0.45:
        return '偏弱'
    else:
        return '一般'


def analyze_market_overview(trade_date: str) -> Dict[str, Any]:
    """分析市场大局。"""
    # 登录 baostock
    lg = bs.login()

    try:
        # 获取三大指数数据
        sh_klines = fetch_index_kline('sh.000001', trade_date, trade_date)
        sz_klines = fetch_index_kline('sz.399001', trade_date, trade_date)
        cy_klines = fetch_index_kline('sz.399006', trade_date, trade_date)

        # 获取近10日数据用于对比
        start_10d = (datetime.strptime(trade_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d')
        sh_klines_10d = fetch_index_kline('sh.000001', start_10d, trade_date)
        sz_klines_10d = fetch_index_kline('sz.399001', start_10d, trade_date)
        cy_klines_10d = fetch_index_kline('sz.399006', start_10d, trade_date)

        # 获取市场统计
        market_stats = fetch_market_stats(trade_date)

        # 计算指数涨跌幅
        def calc_change(klines):
            if len(klines) < 2:
                return None
            today = klines[-1]
            yesterday = klines[-2]
            if today['close'] and yesterday['close'] and yesterday['close'] > 0:
                return round((today['close'] / yesterday['close'] - 1) * 100, 2)
            return None

        sh_change = calc_change(sh_klines_10d)
        sz_change = calc_change(sz_klines_10d)
        cy_change = calc_change(cy_klines_10d)

        # 判断市场情绪
        sentiment = classify_market_sentiment(
            market_stats['up_count'],
            market_stats['down_count'],
            market_stats['limit_up_count'],
            market_stats['limit_down_count']
        )

        # 判断赚钱效应
        profit_effect = classify_profit_effect(
            market_stats['up_count'],
            market_stats['down_count'],
            market_stats['limit_up_count'],
            market_stats['limit_down_count']
        )

        # 生成核心特点
        core_features = []
        if sh_change is not None:
            if sh_change > 1:
                core_features.append('上证放量上涨')
            elif sh_change < -1:
                core_features.append('上证放量下跌')
            else:
                core_features.append('上证窄幅震荡')

        if market_stats['limit_up_count'] > 30:
            core_features.append(f'涨停家数较多({market_stats["limit_up_count"]}家)')
        if market_stats['limit_down_count'] > 10:
            core_features.append(f'跌停家数较多({market_stats["limit_down_count"]}家)')

        # 风险点
        risk_points = []
        if sentiment in ('恐慌', '偏空'):
            risk_points.append('市场情绪偏弱，注意风险')
        if market_stats['limit_down_count'] > 20:
            risk_points.append('跌停家数过多，系统性风险')
        if sh_change is not None and sh_change < -2:
            risk_points.append('上证跌幅较大，可能继续下探')

        # 机会方向
        opportunity_points = []
        if sentiment in ('乐观', '偏多'):
            opportunity_points.append('市场情绪偏强，可适当参与')
        if market_stats['limit_up_count'] > 50:
            opportunity_points.append('涨停家数多，赚钱效应好')
        if profit_effect in ('强', '偏强'):
            opportunity_points.append('赚钱效应较好，短线可操作')

        return {
            'trade_date': trade_date,
            'indices': {
                'sh_composite': {
                    'close': sh_klines[-1]['close'] if sh_klines else None,
                    'change_pct': sh_change,
                },
                'sz_component': {
                    'close': sz_klines[-1]['close'] if sz_klines else None,
                    'change_pct': sz_change,
                },
                'chinext': {
                    'close': cy_klines[-1]['close'] if cy_klines else None,
                    'change_pct': cy_change,
                },
            },
            'market_stats': market_stats,
            'sentiment': sentiment,
            'profit_effect': profit_effect,
            'core_features': core_features,
            'risk_points': risk_points,
            'opportunity_points': opportunity_points,
        }

    finally:
        bs.logout()


def format_overview_report(overview: Dict[str, Any]) -> str:
    """格式化市场大局报告。"""
    lines = []
    lines.append(f"# 今日市场总览 ({overview['trade_date']})\n")

    # 三大指数
    lines.append("## 三大指数\n")
    indices = overview.get('indices', {})
    for name, data in indices.items():
        label = {'sh_composite': '上证指数', 'sz_component': '深证成指', 'chinext': '创业板指'}.get(name, name)
        close = data.get('close')
        change = data.get('change_pct')
        if close and change is not None:
            emoji = '🔴' if change < 0 else '🟢'
            lines.append(f"- {label}: {close:.2f} ({emoji} {change:+.2f}%)")
        else:
            lines.append(f"- {label}: 数据缺失")

    # 市场统计
    stats = overview.get('market_stats', {})
    lines.append("\n## 市场统计\n")
    lines.append(f"- 上涨家数: {stats.get('up_count', 0)}")
    lines.append(f"- 下跌家数: {stats.get('down_count', 0)}")
    lines.append(f"- 涨停家数: {stats.get('limit_up_count', 0)}")
    lines.append(f"- 跌停家数: {stats.get('limit_down_count', 0)}")

    # 市场情绪
    lines.append("\n## 市场情绪\n")
    lines.append(f"- 情绪: **{overview.get('sentiment', '未知')}**")
    lines.append(f"- 赚钱效应: **{overview.get('profit_effect', '未知')}**")

    # 核心特点
    if overview.get('core_features'):
        lines.append("\n## 今日核心特点\n")
        for f in overview['core_features']:
            lines.append(f"- {f}")

    # 风险点
    if overview.get('risk_points'):
        lines.append("\n## 风险点\n")
        for r in overview['risk_points']:
            lines.append(f"- ⚠️ {r}")

    # 机会方向
    if overview.get('opportunity_points'):
        lines.append("\n## 机会方向\n")
        for o in overview['opportunity_points']:
            lines.append(f"- ✅ {o}")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='市场大局分析')
    ap.add_argument('--date', default=date.today().isoformat(), help='交易日期')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    overview = analyze_market_overview(args.date)
    report = format_overview_report(overview)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report, encoding='utf-8')
        print(f'报告已保存到: {args.output}')
    else:
        print(report)
