"""
龙虎榜 spider - hits datacenter-web.eastmoney.com API directly
Replaces CDP DOM extraction for lhb domain
"""
import json
import scrapy
from urllib.parse import urlencode
from datetime import datetime, timedelta

from ..items import LHBRecord


class LHBSipder(scrapy.Spider):
    name = 'lhb'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.2,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def start_requests(self):
        # Get last 7 days of LHB data
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        
        params = {
            'reportName': 'RPT_DAILYBILLBOARD_DETAILSNEW',
            'columns': 'ALL',
            'filter': f'(TRADE_DATE>=\'{start_date}\')(TRADE_DATE<=\'{end_date}\')',
            'pageNumber': '1',
            'pageSize': '500',
            'sortTypes': '-1',
            'sortColumns': 'TRADE_DATE,DEAL_AMOUNT_RATIO',
            'source': 'WEB',
            'client': 'WEB',
        }
        url = f'https://datacenter-web.eastmoney.com/api/data/v1/get?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': 'https://data.eastmoney.com/stock/lhb.html',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        result = data.get('result', {})
        if not result or not result.get('data'):
            self.logger.warning('No LHB data')
            return
        
        for row in result['data']:
            yield LHBRecord(
                trade_date=row.get('TRADE_DATE', '')[:10],
                symbol=self._extract_code(row.get('SECURITY_CODE', '')),
                name=row.get('SECURITY_NAME_ABBR', ''),
                close_price=self._fnum(row.get('CLOSE_PRICE')),
                pct_change=self._fnum(row.get('CHANGE_RATE')),
                deal_amount_ratio=self._fnum(row.get('DEAL_AMOUNT_RATIO')),
                billboard_buy_amt=self._fnum(row.get('BILLBOARD_BUY_AMT')),
                billboard_sell_amt=self._fnum(row.get('BILLBOARD_SELL_AMT')),
                net_amount=self._fnum(row.get('BILLBOARD_NET_AMT')),
                turnover_rate=self._fnum(row.get('TURNOVERRATE')),
                reason=row.get('EXPLAIN', ''),
            )
    
    def _extract_code(self, code):
        if not code:
            return ''
        return str(code).zfill(6)
    
    def _fnum(self, value, default=0.0):
        try:
            if value in (None, '', '-'):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
