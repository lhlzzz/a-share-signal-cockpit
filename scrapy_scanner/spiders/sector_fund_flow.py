"""
板块资金流 spider - hits push2.eastmoney.com API directly
Replaces CDP DOM extraction for sector_fund_flow / concept_capital_flow domains
"""
import json
import scrapy
from urllib.parse import urlencode

from ..items import SectorFundFlow

FIELDS = 'f12,f14,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124'


class SectorFundFlowSpider(scrapy.Spider):
    name = 'sector_fund_flow'
    
    custom_settings = {
        'DOWNLOAD_DELAY': 0.2,
        'CONCURRENT_REQUESTS': 1,
    }
    
    def __init__(self, sector_type='industry', *args, **kwargs):
        super().__init__(*args, **kwargs)
        # industry = 行业, concept = 概念
        self.sector_type = sector_type
    
    def start_requests(self):
        if self.sector_type == 'concept':
            fs = 'm:90+t:3'
            referer = 'https://data.eastmoney.com/bkzj/gn.html'
        else:
            fs = 'm:90+t:2'
            referer = 'https://data.eastmoney.com/bkzj/hy.html'
        
        params = {
            'pn': '1',
            'pz': '100',
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'fid': 'f62',
            'fs': fs,
            'fields': FIELDS,
        }
        url = f'https://push2delay.eastmoney.com/api/qt/clist/get?{urlencode(params)}'
        yield scrapy.Request(
            url,
            headers={
                'Referer': referer,
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            },
            callback=self.parse,
        )
    
    def parse(self, response):
        data = json.loads(response.text)
        if not data.get('data') or not data['data'].get('diff'):
            self.logger.warning('No sector fund flow data')
            return
        
        for row in data['data']['diff']:
            yield SectorFundFlow(
                sector_name=row.get('f14', ''),
                sector_code=row.get('f12', ''),
                pct_chg=self._fnum(row.get('f3')),
                net_inflow=self._fnum(row.get('f62')),
                main_net_inflow=self._fnum(row.get('f66')),
                lead_stock=row.get('f204', ''),
                lead_pct_chg=self._fnum(row.get('f205')),
            )
    
    def _fnum(self, value, default=0.0):
        try:
            if value in (None, '', '-'):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
