#!/usr/bin/env python3
"""
Compare CDP scanner vs Scrapy scanner output
Usage: python3 scrapy_scanner/compare.py
"""
import json
from pathlib import Path

BASE = Path('/workspace/hermes-workspaces/xiaogu')

def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

def load_json(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text())

# Find latest scan directories
live_scan = BASE / 'data' / 'live_scan'
today = sorted([d.name for d in live_scan.iterdir() if d.is_dir()])[-1]

cdp_dir = live_scan / today / 'eastmoney_web_tabs_scan_v0_1'
scrapy_dir = live_scan / today / 'scrapy_scan'

print(f'=== Scan Comparison for {today} ===\n')

# CDP scanner
cdp_summary = load_json(cdp_dir / 'eastmoney_web_tabs_summary.json')
print(f'CDP Scanner:')
print(f'  Stocks: {cdp_summary.get("universe_quote_count", "N/A")}')
print(f'  Scored: {cdp_summary.get("scored_count", "N/A")}')
print(f'  Passed: {cdp_summary.get("passed_count", "N/A")}')
print(f'  Market breadth: {cdp_summary.get("market_breadth_up_pct", "N/A")}%')
print(f'  Limit-ups: {cdp_summary.get("market_limitups", "N/A")}')

print()

# Scrapy scanner
scrapy_summary = load_json(scrapy_dir / 'scrapy_summary.json')
print(f'Scrapy Scanner:')
print(f'  Stocks: {scrapy_summary.get("universe_quote_count", "N/A")}')
print(f'  Market breadth: {scrapy_summary.get("market_breadth_up_pct", "N/A")}%')
print(f'  Limit-ups: {scrapy_summary.get("market_limitups", "N/A")}')
print(f'  Sectors: {scrapy_summary.get("sector_industry_count", "N/A")} industry + {scrapy_summary.get("sector_concept_count", "N/A")} concept')

print()

# Compare stock data
cdp_stocks = load_jsonl(cdp_dir / 'eastmoney_web_tabs_raw.jsonl')
scrapy_stocks = load_jsonl(scrapy_dir / 'scrapy_stock_list.jsonl')

cdp_codes = {s.get('code') for s in cdp_stocks if s.get('code')}
scrapy_codes = {s.get('code') for s in scrapy_stocks if s.get('code')}

print(f'Stock overlap:')
print(f'  CDP codes: {len(cdp_codes)}')
print(f'  Scrapy codes: {len(scrapy_codes)}')
print(f'  Overlap: {len(cdp_codes & scrapy_codes)}')
print(f'  CDP only: {len(cdp_codes - scrapy_codes)}')
print(f'  Scrapy only: {len(scrapy_codes - cdp_codes)}')

print()

# Data quality comparison
print(f'Data fields comparison (first stock):')
if cdp_stocks and scrapy_stocks:
    cdp_first = cdp_stocks[0]
    scrapy_first = scrapy_stocks[0]
    
    common_fields = set(cdp_first.keys()) & set(scrapy_first.keys())
    for field in sorted(common_fields):
        cdp_val = cdp_first.get(field)
        scrapy_val = scrapy_first.get(field)
        if cdp_val != scrapy_val:
            print(f'  {field}: CDP={cdp_val}, Scrapy={scrapy_val}')
