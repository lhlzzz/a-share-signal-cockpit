#!/usr/bin/env python3
"""热点预测回测脚本。

用前一天数据预测当天热点，对比实际结果，计算准确率。

Usage:
    python3 xiaogu_backtest_prediction.py --days 10
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def fnum(v, d=0.0):
    try:
        if v in (None, '', '-'): return d
        return float(v)
    except: return d


def load_jsonl(p):
    items = []
    try:
        with open(p) as f:
            for line in f:
                if line.strip():
                    try: items.append(json.loads(line))
                    except: pass
    except: pass
    return items


def extract_sectors_from_cdp(scan_dir: Path) -> List[Dict]:
    """从CDP scanner输出提取板块数据。"""
    # 查找raw文件
    raw_files = list(scan_dir.glob('**/eastmoney_web_tabs_raw.jsonl'))
    if not raw_files:
        return []

    sectors = []
    with open(raw_files[0], 'r') as f:
        for line in f:
            if line.strip():
                try:
                    item = json.loads(line)
                    if item.get('kind') == 'concept_industry':
                        cells = item.get('cells', [])
                        if len(cells) >= 12:
                            name = cells[1]
                            chg_str = cells[5].replace('%', '')
                            try:
                                chg = float(chg_str)
                            except:
                                chg = 0
                            leader = cells[10] if len(cells) > 10 else ''
                            sectors.append({
                                'name': name,
                                'pct_chg': chg,
                                'leader': leader,
                            })
                except:
                    continue

    return sectors


def extract_sectors_from_v2(scan_dir: Path) -> List[Dict]:
    """从v2 scanner输出提取板块数据。"""
    # 查找sector_concept文件
    sector_files = list(scan_dir.glob('**/sector_concept.jsonl'))
    if not sector_files:
        return []

    sectors = []
    with open(sector_files[0], 'r') as f:
        for line in f:
            if line.strip():
                try:
                    item = json.loads(line)
                    sectors.append({
                        'name': item.get('f14', ''),
                        'pct_chg': fnum(item.get('f3')),
                        'inflow': fnum(item.get('f62')),
                        'leader': item.get('f204', ''),
                        'leader_code': str(item.get('f205', '')).zfill(6),
                    })
                except:
                    continue

    return sectors


def get_hot_sectors(scan_dir: Path, top_n: int = 5) -> List[str]:
    """获取热点板块。"""
    # 先尝试v2数据
    sectors = extract_sectors_from_v2(scan_dir)

    # 如果没有v2数据，尝试CDP数据
    if not sectors:
        sectors = extract_sectors_from_cdp(scan_dir)

    if not sectors:
        return []

    # 综合评分（如果有资金流数据）
    for s in sectors:
        chg = s.get('pct_chg', 0)
        inflow = s.get('inflow', 0) / 1e8 if s.get('inflow') else 0
        flow_score = min(100, inflow * 2) if inflow > 0 else 0
        chg_score = min(100, max(0, chg * 10))
        s['_score'] = flow_score * 0.6 + chg_score * 0.4 if flow_score > 0 else chg_score

    # 按分数排序
    sectors.sort(key=lambda x: x['_score'], reverse=True)

    # 提取热点板块名称
    hot_names = []
    for s in sectors[:top_n]:
        name = s.get('name', '')
        if name:
            hot_names.append(name)

    return hot_names


def calculate_accuracy(predicted: List[str], actual: List[str]) -> Tuple[float, List[str]]:
    """计算预测准确率。"""
    if not predicted or not actual:
        return 0.0, []

    # 提取关键词进行模糊匹配
    def extract_keywords(name):
        keywords = set()
        keywords.add(name)
        # 去掉常见后缀
        for suffix in ['概念', '板块', '风格', 'Ⅱ', 'Ⅲ', '_', '（', '）']:
            name = name.replace(suffix, '')
        keywords.add(name)
        # 提取核心词（取前4个字）
        if len(name) >= 4:
            keywords.add(name[:4])
        return keywords

    matched = []
    for pred in predicted:
        pred_kw = extract_keywords(pred)
        for act in actual:
            act_kw = extract_keywords(act)
            if pred_kw & act_kw:  # 交集不为空
                matched.append(act)
                break

    accuracy = len(matched) / len(actual) if actual else 0
    return accuracy, matched


def run_backtest(days: int = 10) -> Dict[str, Any]:
    """运行回测。"""
    # 获取所有可用日期
    available_dates = []
    for d in sorted(ROOT.glob('data/live_scan/2026-*/')):
        date_str = d.name.rstrip('/')
        if len(date_str) == 10 and date_str.startswith('2026-'):
            # 检查是否有板块数据
            has_data = False
            # v2数据
            if list(d.glob('**/sector_concept.jsonl')):
                has_data = True
            # CDP数据
            if list(d.glob('**/eastmoney_web_tabs_raw.jsonl')):
                has_data = True
            if has_data:
                available_dates.append(date_str)

    # 取最近N+1天（需要前一天数据）
    if len(available_dates) < days + 1:
        days = len(available_dates) - 1

    test_dates = available_dates[-days-1:]  # 多取一天作为前一天数据

    results = []
    total_accuracy = 0
    total_matched = 0
    total_actual = 0

    print(f'=== 回测 {days} 个交易日 ===\n')
    print(f'{"预测日":<12} {"预测板块":<35} {"实际板块":<35} {"匹配":<15} {"准确率":<8}')
    print('-' * 110)

    for i in range(len(test_dates) - 1):
        prev_date = test_dates[i]
        curr_date = test_dates[i + 1]

        prev_dir = ROOT / 'data' / 'live_scan' / prev_date
        curr_dir = ROOT / 'data' / 'live_scan' / curr_date

        # 用前一天数据预测
        predicted = get_hot_sectors(prev_dir, top_n=5)
        # 实际热点
        actual = get_hot_sectors(curr_dir, top_n=5)

        # 计算准确率
        accuracy, matched = calculate_accuracy(predicted, actual)

        # 统计
        total_accuracy += accuracy
        total_matched += len(matched)
        total_actual += len(actual)

        # 格式化输出
        pred_str = ', '.join(predicted[:3]) if predicted else '无数据'
        act_str = ', '.join(actual[:3]) if actual else '无数据'
        match_str = ', '.join(matched[:3]) if matched else '无'

        print(f'{curr_date:<12} {pred_str:<35} {act_str:<35} {match_str:<15} {accuracy:.0%}')

        results.append({
            'date': curr_date,
            'predicted': predicted,
            'actual': actual,
            'matched': matched,
            'accuracy': accuracy,
        })

    # 汇总
    avg_accuracy = total_accuracy / len(results) if results else 0
    match_rate = total_matched / total_actual if total_actual else 0

    print('-' * 110)
    print(f'平均准确率: {avg_accuracy:.1%}')
    print(f'匹配率: {total_matched}/{total_actual} = {match_rate:.1%}')

    return {
        'days': days,
        'results': results,
        'avg_accuracy': avg_accuracy,
        'match_rate': match_rate,
        'total_matched': total_matched,
        'total_actual': total_actual,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description='热点预测回测')
    ap.add_argument('--days', type=int, default=10, help='回测天数')
    ap.add_argument('--output', default='', help='输出文件路径')
    args = ap.parse_args()

    result = run_backtest(args.days)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'\n已保存到: {output_path}')


if __name__ == '__main__':
    main()
