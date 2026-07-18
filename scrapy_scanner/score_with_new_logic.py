#!/usr/bin/env python3
"""
Score candidates using new logic with enriched data.
Usage: python3 scrapy_scanner/score_with_new_logic.py --date 2026-07-03
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

BASE = Path('/workspace/hermes-workspaces/xiaogu')
sys.path.insert(0, str(BASE))

def fnum(v, default=0.0):
    try:
        if v in (None, '', '-'):
            return default
        return float(v)
    except:
        return default

def load_v2_data(scan_date):
    scan_dir = BASE / 'data' / 'live_scan' / scan_date / 'eastmoney_scan'
    data = {'scan_date': scan_date}
    files = {
        'stock_list': 'stock_all_a.jsonl',
        'hsgt_deals': 'hsgt_deals.jsonl',
        'earnings_preview': 'earnings_preview.jsonl',
        'lockup_expiry': 'lockup_expiry.jsonl',
        'announcements': 'announcements.jsonl',
        'hsgt_summary': 'hsgt_summary.jsonl',
        'lhb': 'lhb.jsonl',
        'flow_industry': 'flow_industry.jsonl',
        'flow_concept': 'flow_concept.jsonl',
    }
    for key, filename in files.items():
        path = scan_dir / filename
        if path.exists():
            try:
                with open(path) as f:
                    data[key] = [json.loads(line) for line in f if line.strip()]
            except:
                data[key] = []
        else:
            data[key] = []
    return data

def score_candidate(candidate, v2_data):
    """Score a candidate using new logic with enriched data."""
    code = str(candidate.get('code', '')).zfill(6)
    
    # Get enriched data from v2 scanner
    stock_by_code = {}
    for stock in v2_data.get('stock_list', []):
        c = str(stock.get('f12', '')).zfill(6)
        if c:
            stock_by_code[c] = stock
    
    v2_stock = stock_by_code.get(code, {})
    net_inflow = fnum(v2_stock.get('f62')) if v2_stock else 0
    
    # Base score from candidate
    base_score = fnum(candidate.get('final_score'))
    
    # New scoring bonuses
    signal_pct = fnum(candidate.get('signal_pct'))
    setup_type = str(candidate.get('setup_type', ''))
    
    bonus = 0
    
    # High signal_pct bonus
    if signal_pct >= 7.0:
        bonus += 10
    elif signal_pct >= 5.0:
        bonus += 6
    
    # Consecutive limit-up bonus
    if 'LIMITUP_REASON_PROPAGATION' in setup_type or 'LIMIT_STRENGTH' in setup_type:
        bonus += 5
    
    # Sector momentum bonus
    sector_score = fnum(candidate.get('sector_catalyst_score'))
    if sector_score >= 0.5:
        bonus += 4
    
    # Fund flow bonus
    if net_inflow > 10000000:  # > 1000万
        bonus += 5
    
    return base_score + bonus, net_inflow

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--top', type=int, default=20)
    args = parser.parse_args()
    
    # Load v2 data
    v2_data = load_v2_data(args.date)
    
    # Load candidates from bundle
    bundle_dir = BASE / 'data' / 'forward_candidate_bundles' / args.date
    bundles = sorted(bundle_dir.glob('*candidate*.json'))
    
    if not bundles:
        print(f'No bundles found for {args.date}')
        return
    
    bundle = json.loads(bundles[-1].read_text())
    candidates = bundle.get('paper_scoring_candidates', [])
    
    print(f'=== 新评分逻辑重选候选 ({args.date}) ===')
    print(f'候选总数: {len(candidates)}')
    
    # Score all candidates
    scored = []
    for c in candidates:
        new_score, net_inflow = score_candidate(c, v2_data)
        scored.append({
            **c,
            'new_score': new_score,
            'net_inflow_enriched': net_inflow,
        })
    
    # Sort by new score
    scored.sort(key=lambda x: x['new_score'], reverse=True)
    
    # Show top N
    print(f'\n=== TOP {args.top} (新评分逻辑) ===')
    print(f'{"排名":4} {"代码":8} {"名称":10} {"原分":>6} {"新分":>6} {"涨幅":>6} {"净流入":>12} {"Setup":25}')
    print('-' * 90)
    
    for i, c in enumerate(scored[:args.top], 1):
        code = c.get('code', '')
        name = c.get('name', '')
        old_score = c.get('final_score', 0)
        new_score = c['new_score']
        signal = c.get('signal_pct', 0)
        net_inflow = c['net_inflow_enriched']
        setup = c.get('setup_type', '')
        print(f'{i:4} {code:8} {name:10} {old_score:>6.1f} {new_score:>6.1f} {signal:>5.1f}% {net_inflow:>12.0f} {setup}')
    
    # Save scored candidates
    output_dir = BASE / 'data' / 'live_scan' / args.date / 'scored_candidates'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / 'scored_with_new_logic.json'
    with open(output_path, 'w') as f:
        json.dump(scored[:args.top], f, ensure_ascii=False, indent=2)
    
    print(f'\nSaved to: {output_path}')

if __name__ == '__main__':
    main()
