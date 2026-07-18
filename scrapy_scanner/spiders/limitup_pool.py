"""
涨停池 spider - hits push2ex.eastmoney.com API directly
Replaces CDP DOM extraction for limitup_pool domain
"""
import json
import scrapy
from urllib.parse import urlencode
from datetime import datetime

from ..items import LimitUpStock


class LimitUpSpider(scrapy.Spider):
    name = 'limitup_pool'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.2,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def start_requests(self):
        params = {
            'ut': '7eea3edcaed734bea9cbfc24409ed989',
            'dpt': 'wz.ztzt',
            'Ession': datetime.now().strftime('%Y%m%d'),
        }
        url = f'https://push2ex.eastmoney.com/getTopicZTPool?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': 'https://quote.eastmoney.com/ztb/detail',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        pool = data.get('data', {})
        if not pool or not pool.get('pool'):
            self.logger.warning('No limit-up data')
            return
        
        for row in pool['pool']:
            yield LimitUpStock(
                code=str(row.get('c', '')).zfill(6),
                name=row.get('n', ''),
                price=self._fnum(row.get('p')),
                pct_chg=self._fnum(row.get('zdp')),
                amount=self._fnum(row.get('amount')),
                seal_amount=self._fnum(row.get('fund')),
                first_seal_time=row.get('fbt', ''),
                last_seal_time=row.get('lbt', ''),
                open_count=self._int(row.get('oc')),
                industry=row.get('hybk', ''),
                reason=row.get('zttj', {}).get('ct', ''),
            )
    
    def _fnum(self, value, default=0.0):
        try:
            if value in (None, '', '-'):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
    
    def _int(self, value, default=0):
        try:
            if value in (None, '', '-'):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default
