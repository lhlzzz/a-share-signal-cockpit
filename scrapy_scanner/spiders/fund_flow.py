"""
Fund flow spider - hits push2.eastmoney.com API directly
Replaces CDP DOM extraction for fund_flow domain
"""
import json
import scrapy
from urllib.parse import urlencode

from ..items import FundFlow

MARKET_SECIDS = {
    '上证指数': '1.000001',
    '深证成指': '0.399001',
    '创业板指': '0.399006',
    '科创50': '1.000688',
    '北证50': '0.899050',
}


class FundFlowSpider(scrapy.Spider):
    name = 'fund_flow'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.1,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def start_requests(self):
        secids = ','.join(MARKET_SECIDS.values())
        params = {
            'fltt': '2',
            'fields': 'f1,f2,f3,f12,f13,f14,f62',
            'secids': secids,
        }
        url = f'https://push2delay.eastmoney.com/api/qt/ulist.np/get?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': 'https://quote.eastmoney.com/',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        if not data.get('data') or not data['data'].get('diff'):
            self.logger.warning('No fund flow data')
            return
        
        for row in data['data']['diff']:
            yield FundFlow(
                secid=row.get('f12', ''),
                name=row.get('f14', ''),
                price=self._fnum(row.get('f2')),
                pct_chg=self._fnum(row.get('f3')),
                net_inflow=self._fnum(row.get('f62')),
            )
    
    def _fnum(self, value, default=0.0):
        try:
            if value in (None, '', '-'):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
