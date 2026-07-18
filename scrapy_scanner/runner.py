"""
Scrapy-based xiaogu scanner - hits Eastmoney APIs directly
Faster alternative to CDP DOM extraction

Usage:
    python3 scrapy_scanner/runner.py
    python3 scrapy_scanner/runner.py --output-dir data/live_scan/2026-07-05/scrapy_scan
    python3 scrapy_scanner/runner.py --spiders stock_list,fund_flow,lhb
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

BASE = Path('/workspace/hermes-workspaces/xiaogu')
sys.path.insert(0, str(BASE))

from scrapy_scanner.spiders.stock_list import StockListSpider
from scrapy_scanner.spiders.fund_flow import FundFlowSpider
from scrapy_scanner.spiders.lhb import LHBSipder
from scrapy_scanner.spiders.limitup_pool import LimitUpSpider
from scrapy_scanner.spiders.sector_fund_flow import SectorFundFlowSpider
from scrapy_scanner.spiders.announcements import AnnouncementSpider


SPIDER_MAP = {
    'stock_list': StockListSpider,
    'fund_flow': FundFlowSpider,
    'lhb': LHBSipder,
    'limitup_pool': LimitUpSpider,
    'sector_fund_flow_industry': lambda: SectorFundFlowSpider(sector_type='industry'),
    'sector_fund_flow_concept': lambda: SectorFundFlowSpider(sector_type='concept'),
    'announcements': AnnouncementSpider,
}

DEFAULT_SPIDERS = ['stock_list', 'fund_flow', 'lhb', 'limitup_pool', 'sector_fund_flow_industry', 'sector_fund_flow_concept']


class CollectorPipeline:
    """Collect all items into memory"""
    def __init__(self):
        self.items = {}
    
    def open_spider(self, spider):
        self.items[spider.name] = []
    
    def process_item(self, item, spider):
        self.items[spider.name].append(dict(item))
        return item
    
    def close_spider(self, spider):
        pass


def run_scrapy_scan(spider_names, output_dir):
    """Run Scrapy spiders and collect results"""
    settings = get_project_settings()
    settings.setmodule('scrapy_scanner.settings', priority='project')
    
    # Override output directory
    settings.set('OUTPUT_DIR', str(output_dir))
    
    # Use custom pipeline to collect items
    settings.set('ITEM_PIPELINES', {
        '__main__.CollectorPipeline': 100,
    })
    
    process = CrawlerProcess(settings)
    
    results = {}
    
    for name in spider_names:
        spider_cls = SPIDER_MAP.get(name)
        if not spider_cls:
            print(f'Unknown spider: {name}')
            continue
        
        # Handle lambda factories
        if callable(spider_cls) and not isinstance(spider_cls, type):
            spider = spider_cls()
        else:
            spider = spider_cls
        
        process.crawl(spider)
    
    process.start()
    return results


def save_results(output_dir, spider_results):
    """Save results in format compatible with xiaogu scanner output"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    source_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Save raw data
    for spider_name, items in spider_results.items():
        raw_path = output_dir / f'scrapy_{spider_name}.jsonl'
        with open(raw_path, 'w', encoding='utf-8') as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    # Build summary compatible with runner
    stock_quotes = spider_results.get('stock_list', [])
    fund_flow = spider_results.get('fund_flow', [])
    lhb = spider_results.get('lhb', [])
    limitup = spider_results.get('limitup_pool', [])
    
    summary = {
        'source': 'scrapy_api_scan',
        'pipeline_version': 'scrapy_scanner_v0_1',
        'source_time': source_time,
        'universe_quote_count': len(stock_quotes),
        'stock_list_count': len(stock_quotes),
        'fund_flow_count': len(fund_flow),
        'lhb_count': len(lhb),
        'limitup_count': len(limitup),
        'scored_count': 0,  # Will be filled by runner
        'passed_count': 0,  # Will be filled by runner
        'files': {},
    }
    
    # Save summary
    summary_path = output_dir / 'scrapy_summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f'\n=== Scrapy Scan Complete ===')
    print(f'Stock quotes: {len(stock_quotes)}')
    print(f'Fund flow: {len(fund_flow)}')
    print(f'LHB records: {len(lhb)}')
    print(f'Limit-up stocks: {len(limitup)}')
    print(f'Output: {output_dir}')
    
    return summary


def main():
    parser = argparse.ArgumentParser(description='Scrapy-based xiaogu scanner')
    parser.add_argument('--spiders', default=','.join(DEFAULT_SPIDERS),
                        help='Comma-separated spider names')
    parser.add_argument('--output-dir', default=None,
                        help='Output directory')
    args = parser.parse_args()
    
    spider_names = [s.strip() for s in args.spiders.split(',')]
    
    source_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    output_dir = Path(args.output_dir) if args.output_dir else BASE / 'data' / 'live_scan' / source_time[:10] / 'scrapy_scan'
    
    # Run Scrapy
    run_scrapy_scan(spider_names, output_dir)
    
    print(f'\nDone. Output: {output_dir}')


if __name__ == '__main__':
    main()
