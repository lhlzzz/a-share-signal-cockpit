"""
A-share stock list spider - hits push2.eastmoney.com API directly
Replaces CDP DOM extraction for quote_rank domain
"""
import json
import scrapy
from urllib.parse import urlencode

from ..items import StockQuote

FIELDS = 'f12,f13,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21,f23,f62'
A_SHARE_FS = 'm:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048'


class StockListSpider(scrapy.Spider):
    name = 'stock_list'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.1,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def start_requests(self):
        params = {
            'pn': '1',
            'pz': '5500',
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'fid': 'f3',
            'fs': A_SHARE_FS,
            'fields': FIELDS,
        }
        url = f'https://push2delay.eastmoney.com/api/qt/clist/get?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': 'https://quote.eastmoney.com/center/gridlist.html#hs_a_board',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        if not data.get('data') or not data['data'].get('diff'):
            self.logger.warning('No stock data in response')
            return
        
        for row in data['data']['diff']:
            code = str(row.get('f12', '')).zfill(6)
            if not code or len(code) != 6:
                continue
            
            yield StockQuote(
                code=code,
                exchange=row.get('f13'),
                name=row.get('f14'),
                price=self._fnum(row.get('f2')),
                pct_chg=self._fnum(row.get('f3')),
                chg=self._fnum(row.get('f4')),
                volume=self._fnum(row.get('f5')),
                amount=self._fnum(row.get('f6')),
                amplitude=self._fnum(row.get('f7')),
                turnover_rate=self._fnum(row.get('f8')),
                pe_dynamic=self._fnum(row.get('f9')),
                volume_ratio=self._fnum(row.get('f10')),
                high=self._fnum(row.get('f15')),
                low=self._fnum(row.get('f16')),
                open=self._fnum(row.get('f17')),
                prev_close=self._fnum(row.get('f18')),
                market_cap=self._fnum(row.get('f20')),
                float_market_cap=self._fnum(row.get('f21')),
                pb=self._fnum(row.get('f23')),
                net_inflow_main=self._fnum(row.get('f62')),
            )
    
    def _fnum(self, value, default=0.0):
        try:
            if value in (None, '', '-'):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
