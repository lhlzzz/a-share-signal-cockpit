# Scrapy items for xiaogu scanner
import scrapy


class StockQuote(scrapy.Item):
    """A-share stock quote from push2 API"""
    code = scrapy.Field()           # f12
    exchange = scrapy.Field()       # f13
    name = scrapy.Field()           # f14
    price = scrapy.Field()          # f2
    pct_chg = scrapy.Field()        # f3
    chg = scrapy.Field()            # f4
    volume = scrapy.Field()         # f5
    amount = scrapy.Field()         # f6
    amplitude = scrapy.Field()      # f7
    turnover_rate = scrapy.Field()  # f8
    pe_dynamic = scrapy.Field()     # f9
    volume_ratio = scrapy.Field()   # f10
    high = scrapy.Field()           # f15
    low = scrapy.Field()            # f16
    open = scrapy.Field()           # f17
    prev_close = scrapy.Field()     # f18
    market_cap = scrapy.Field()     # f20
    float_market_cap = scrapy.Field()  # f21
    pb = scrapy.Field()             # f23
    net_inflow_main = scrapy.Field()  # f62


class FundFlow(scrapy.Item):
    """Market index fund flow"""
    secid = scrapy.Field()
    name = scrapy.Field()
    price = scrapy.Field()
    pct_chg = scrapy.Field()
    net_inflow = scrapy.Field()


class LHBRecord(scrapy.Item):
    """龙虎榜记录"""
    trade_date = scrapy.Field()
    symbol = scrapy.Field()
    name = scrapy.Field()
    close_price = scrapy.Field()
    pct_change = scrapy.Field()
    deal_amount_ratio = scrapy.Field()
    billboard_buy_amt = scrapy.Field()
    billboard_sell_amt = scrapy.Field()
    net_amount = scrapy.Field()
    turnover_rate = scrapy.Field()
    reason = scrapy.Field()


class LimitUpStock(scrapy.Item):
    """涨停股"""
    code = scrapy.Field()
    name = scrapy.Field()
    price = scrapy.Field()
    pct_chg = scrapy.Field()
    amount = scrapy.Field()
    seal_amount = scrapy.Field()
    first_seal_time = scrapy.Field()
    last_seal_time = scrapy.Field()
    open_count = scrapy.Field()
    industry = scrapy.Field()
    reason = scrapy.Field()


class SectorFundFlow(scrapy.Item):
    """板块资金流"""
    sector_name = scrapy.Field()
    sector_code = scrapy.Field()
    pct_chg = scrapy.Field()
    net_inflow = scrapy.Field()
    main_net_inflow = scrapy.Field()
    lead_stock = scrapy.Field()
    lead_pct_chg = scrapy.Field()


class Announcement(scrapy.Item):
    """公告"""
    symbol = scrapy.Field()
    title = scrapy.Field()
    notice_date = scrapy.Field()
    url = scrapy.Field()
    type = scrapy.Field()


class FinancialReport(scrapy.Item):
    """财务报表"""
    symbol = scrapy.Field()
    name = scrapy.Field()
    report_date = scrapy.Field()
    revenue = scrapy.Field()
    net_profit = scrapy.Field()
    eps = scrapy.Field()
    roe = scrapy.Field()
