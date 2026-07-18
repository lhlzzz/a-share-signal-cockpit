"""
公告 spider - hits np-anotice-stock.eastmoney.com API directly
Replaces CDP DOM extraction for announcements domain
"""
import json
import scrapy
from urllib.parse import urlencode
from datetime import datetime, timedelta

from ..items import Announcement


class AnnouncementSpider(scrapy.Spider):
    name = 'announcements'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.3,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def start_requests(self):
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        
        params = {
            'ann_type': 'A',
            'client_source': 'WEB',
            'f_node': '0',
            'page_index': '1',
            'page_size': '200',
            's_node': '0',
            'begin_time': start_date,
            'end_time': end_date,
        }
        url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': 'https://data.eastmoney.com/notices/',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        if not data.get('data') or not data['data'].get('list'):
            self.logger.warning('No announcement data')
            return
        
        for row in data['data']['list']:
            codes = row.get('codes', [])
            symbol = codes[0].get('stock_code', '') if codes else ''
            if symbol:
                symbol = str(symbol).zfill(6)
            
            yield Announcement(
                symbol=symbol,
                title=row.get('title', ''),
                notice_date=row.get('notice_date', '')[:10],
                url=row.get('url', ''),
                type=row.get('columns', [{}])[0].get('column_name', '') if row.get('columns') else '',
            )
