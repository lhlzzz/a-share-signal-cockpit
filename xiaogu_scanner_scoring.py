#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production structured-scoring library for xiaogu scanner/runner.

Live scan entrypoint: scrapy_scanner/runner_v2.py (HTTP/API-direct).
This module hosts structured scores, research signals, coverage audit, and
direct API enrichment helpers used by the production scanner.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

BASE = Path(os.environ.get('XIAOGU_HOME') or Path(__file__).resolve().parent)


def latest_completed_trading_day(now=None):
    n = now or datetime.now()
    d = n.date()
    if n.time() < datetime.strptime('15:30:00', '%H:%M:%S').time():
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _validate_trade_date(trade_date, now=None):
    """Validate that trade_date is not in the future relative to now."""
    n = now or datetime.now()
    today = n.date()
    if trade_date > today:
        print(f'WARN: trade_date {trade_date} is in the future (today={today}), clamping to today', file=_sys.stderr, flush=True)
        return today
    return trade_date


def last_trading_day(now=None):
    return latest_completed_trading_day(now)
sys.path.insert(0, str(BASE))

from six_repo_integration_real_v2_1 import aggregate_four_repo_native_signals
from xiaogu_v2_1_six_repo_real_integrated import fetch_all_sector_fund_flow, sector_fund_flow_stocks, extract_concept_board_ranking
from xiaogu_eastmoney_tail_scan_v0_2 import A_SHARE_CODE_RE, board_for_code, collect_quotes as collect_eastmoney_page_quotes, fnum, is_a_share_code
from xiaogu_forward_judge_scoreboard_v0_1 import (
    build_a_share_chain_scorecard,
    build_diagnosis_engine,
    build_market_regime_performance,
    build_registry_snapshots,
    load_rule_freeze_snapshot,
)

API_SCAN_SOURCE = 'eastmoney_api_scan_v2'
PIPELINE_VERSION = 'v2_scanner_api'
RISK_RECENT_DAYS = 45
FULL_UNIVERSE_MIN_QUOTE_COUNT = 4000
DIRECT_OPENER = build_opener(ProxyHandler({}))
CORE_A_SHARE_BOARDS = ('main', 'chinext')


def is_core_a_share_quote(quote):
    return (quote.get('board') or '') in CORE_A_SHARE_BOARDS

QUOTE_URL_TOKENS = ('quote.eastmoney.com/center/gridlist.html', 'hs_a_board')
FUND_URL_TOKENS = ('data.eastmoney.com/zjlx/detail.html',)
WATCHLIST_URL_TOKENS = ('quote.eastmoney.com/zixuan',)
WATCHLIST_STOCK_CODE_RE = A_SHARE_CODE_RE
EASTMONEY_DATA_DIRECTORY_CATALOG = [
    {
        'key': 'hot_data',
        'title': '热门数据',
        'url': 'https://data.eastmoney.com/',
        'items': [
            {'key': 'hsgt_holdings', 'title': '沪深港通持股', 'url': 'https://data.eastmoney.com/hsgtcg/'},
            {'key': 'latest_earnings', 'title': '最新业绩报表', 'url': 'https://data.eastmoney.com/bbsj/'},
            {'key': 'new_stock_subscription', 'title': '新股申购', 'url': 'https://data.eastmoney.com/xg/xg/default.html'},
            {'key': 'lhb_list', 'title': '龙虎榜单', 'url': 'https://data.eastmoney.com/stock/lhb.html'},
            {'key': 'ipo_registration_review', 'title': '注册审核企业', 'url': 'https://data.eastmoney.com/xg/zczsh.html'},
            {'key': 'valuation_analysis', 'title': '估值分析', 'url': 'https://data.eastmoney.com/gzfx/'},
        ],
    },
    {
        'key': 'capital_flow',
        'title': '资金流向',
        'url': 'https://data.eastmoney.com/zjlx/',
        'items': [
            {'key': 'market_capital_flow', 'title': '大盘资金流', 'url': 'https://data.eastmoney.com/zjlx/dpzjlx.html'},
            {'key': 'stock_capital_flow', 'title': '个股资金流', 'url': 'https://data.eastmoney.com/zjlx/detail.html'},
            {'key': 'main_force_rank', 'title': '主力排名', 'url': 'https://data.eastmoney.com/zjlx/list.html'},
            {'key': 'sector_capital_flow', 'title': '板块资金', 'url': 'https://data.eastmoney.com/bkzj/'},
            {'key': 'industry_capital_flow', 'title': '行业资金流', 'url': 'https://data.eastmoney.com/bkzj/hy.html'},
            {'key': 'concept_capital_flow', 'title': '概念资金流', 'url': 'https://data.eastmoney.com/bkzj/gn.html'},
            {'key': 'region_capital_flow', 'title': '地域资金流', 'url': 'https://data.eastmoney.com/bkzj/dy.html'},
            {'key': 'capital_flow_monitor', 'title': '资金流监测', 'url': 'https://data.eastmoney.com/bkzj/jlr.html'},
            {'key': 'hsgt_capital_flow', 'title': '沪深港通资金', 'url': 'https://data.eastmoney.com/hsgt/hsgtV2.html'},
            {'key': 'hsgt_turnover', 'title': '沪深港通成交', 'url': 'https://data.eastmoney.com/hsgt/hsgtDetail/sdcjg.html'},
            {'key': 'hsgt_holdings', 'title': '沪深港通持股', 'url': 'https://data.eastmoney.com/hsgtV2/hsgtDetail/ggzj.html'},
        ],
    },
    {
        'key': 'featured_data',
        'title': '特色数据',
        'url': 'https://data.eastmoney.com/stock/lhb.html',
        'items': [
            {'key': 'ab_price_compare', 'title': 'AB股比价', 'url': 'https://quote.eastmoney.com/center/list.html#absh_0_4'},
            {'key': 'ah_price_compare', 'title': 'AH股比价', 'url': 'https://quote.eastmoney.com/center/list.html#ah_1'},
            {'key': 'merger_reorg', 'title': '并购重组', 'url': 'https://data.eastmoney.com/bgcz/'},
            {'key': 'finance_calendar', 'title': '财经日历', 'url': 'https://data.eastmoney.com/dcrl/'},
            {'key': 'index_components', 'title': '成分股数据', 'url': 'https://data.eastmoney.com/other/index/'},
            {'key': 'block_trades', 'title': '大宗交易', 'url': 'https://data.eastmoney.com/dzjy/default.html'},
            {'key': 'analyst_index', 'title': '分析师指数', 'url': 'https://data.eastmoney.com/invest/invest/default.html'},
            {'key': 'company_topics', 'title': '公司题材', 'url': 'https://data.eastmoney.com/gstc/'},
            {'key': 'valuation_analysis', 'title': '估值分析', 'url': 'https://data.eastmoney.com/gzfx/'},
            {'key': 'institution_survey', 'title': '机构调研', 'url': 'https://data.eastmoney.com/jgdy/'},
            {'key': 'lhb_list', 'title': '龙虎榜单', 'url': 'https://data.eastmoney.com/stock/lhb.html'},
            {'key': 'trading_halts', 'title': '停复牌信息', 'url': 'https://data.eastmoney.com/tfpxx/'},
            {'key': 'main_force_data', 'title': '主力数据', 'url': 'https://data.eastmoney.com/zlsj/'},
            {'key': 'registration_review', 'title': '注册审核', 'url': 'https://data.eastmoney.com/xg/zczsh.html'},
            {'key': 'securities_monthly', 'title': '券商业绩月报', 'url': 'https://data.eastmoney.com/other/qsjy.html'},
        ],
    },
    {
        'key': 'new_stock_data',
        'title': '新股数据',
        'url': 'https://data.eastmoney.com/xg/',
        'items': [
            {'key': 'new_stock_subscription', 'title': '新股申购', 'url': 'https://data.eastmoney.com/xg/xg/default.html'},
            {'key': 'reits_subscription', 'title': 'Reits申购', 'url': 'https://data.eastmoney.com/reits/'},
            {'key': 'convertible_bond', 'title': '可转债', 'url': 'https://data.eastmoney.com/kzz/default.html'},
            {'key': 'ipo_review', 'title': 'IPO审核信息', 'url': 'https://data.eastmoney.com/xg/ipo'},
            {'key': 'new_stock_calendar', 'title': '新股日历', 'url': 'https://data.eastmoney.com/xg/xg/calendar.html'},
            {'key': 'new_stock_meeting', 'title': '新股上会', 'url': 'https://data.eastmoney.com/xg/gh/default.html'},
            {'key': 'ipo_guidance', 'title': '备案辅导信息', 'url': 'https://data.eastmoney.com/xg/ipo/fd.html'},
            {'key': 'new_stock_analysis', 'title': '新股解析', 'url': 'https://data.eastmoney.com/xg/xg/chart/zql.html'},
            {'key': 'private_placement', 'title': '增发', 'url': 'https://data.eastmoney.com/other/gkzf.html'},
            {'key': 'rights_issue', 'title': '配股', 'url': 'https://data.eastmoney.com/zrz/pg.html'},
            {'key': 'neeq_qualified', 'title': '三板达标企业', 'url': 'https://data.eastmoney.com/xg/ipo/dbqy.html'},
        ],
    },
    {
        'key': 'hsgt',
        'title': '沪深港通',
        'url': 'https://data.eastmoney.com/hsgt/index.html',
        'items': [
            {'key': 'hsgt_capital_flow', 'title': '沪深港通资金', 'url': 'https://data.eastmoney.com/hsgt/hsgtV2.html'},
            {'key': 'hsgt_turnover', 'title': '沪深港通成交', 'url': 'https://data.eastmoney.com/hsgtV2/hsgtDetail/sdcjg.html'},
            {'key': 'hsgt_holdings', 'title': '沪深港通持股', 'url': 'https://data.eastmoney.com/hsgtV2/hsgtDetail/ggzj.html'},
        ],
    },
    {
        'key': 'announcements',
        'title': '公告大全',
        'url': 'https://data.eastmoney.com/notices/',
        'items': [
            {'key': 'a_share_all_notices', 'title': '沪深京A股公告', 'url': 'https://data.eastmoney.com/notices/'},
            {'key': 'sh_a_notices', 'title': '沪市A股公告', 'url': 'https://data.eastmoney.com/notices/sha.html'},
            {'key': 'sz_a_notices', 'title': '深市A股公告', 'url': 'https://data.eastmoney.com/notices/sza.html'},
            {'key': 'bj_a_notices', 'title': '京市A股公告', 'url': 'https://data.eastmoney.com/notices/bja.html'},
            {'key': 'chinext_notices', 'title': '创业板公告', 'url': 'https://data.eastmoney.com/notices/cyb.html'},
            {'key': 'star_notices', 'title': '科创板公告', 'url': 'https://data.eastmoney.com/notices/kcb.html'},
            {'key': 'pre_listing_a_notices', 'title': '待上市A股公告', 'url': 'https://data.eastmoney.com/notices/dss.html'},
            {'key': 'hk_notices', 'title': '港股公告', 'url': 'https://data.eastmoney.com/notices/gg.html'},
            {'key': 'us_notices', 'title': '美股公告', 'url': 'https://data.eastmoney.com/notices/mg.html'},
            {'key': 'bond_notices', 'title': '债券公告', 'url': 'https://data.eastmoney.com/notices/zq.html'},
        ],
    },
    {
        'key': 'research_reports',
        'title': '研究报告',
        'url': 'https://data.eastmoney.com/report/',
        'items': [
            {'key': 'report_center', 'title': '研报中心', 'url': 'https://data.eastmoney.com/report/'},
            {'key': 'stock_reports', 'title': '个股研报', 'url': 'https://data.eastmoney.com/report/stock.jshtml'},
            {'key': 'profit_forecast', 'title': '盈利预测', 'url': 'https://data.eastmoney.com/report/profitforecast.jshtml'},
            {'key': 'industry_reports', 'title': '行业研报', 'url': 'https://data.eastmoney.com/report/industry.jshtml'},
            {'key': 'strategy_reports', 'title': '策略报告', 'url': 'https://data.eastmoney.com/report/strategyreport.jshtml'},
            {'key': 'broker_morning_reports', 'title': '券商晨会', 'url': 'https://data.eastmoney.com/report/brokerreport.jshtml'},
            {'key': 'macro_research', 'title': '宏观研究', 'url': 'https://data.eastmoney.com/report/macresearch.jshtml'},
            {'key': 'new_stock_reports', 'title': '新股研报', 'url': 'https://data.eastmoney.com/report/newstock.jshtml'},
        ],
    },
    {
        'key': 'financial_reports',
        'title': '年报季报',
        'url': 'https://data.eastmoney.com/bbsj/',
        'items': [
            {'key': 'latest_earnings', 'title': '最新业绩报表', 'url': 'https://data.eastmoney.com/bbsj/'},
            {'key': 'dividend_plan', 'title': '分红送配', 'url': 'https://data.eastmoney.com/yjfp/'},
            {'key': 'earnings_bulletin', 'title': '业绩快报', 'url': 'https://data.eastmoney.com/bbsj/yjkb.html'},
            {'key': 'earnings_preview', 'title': '业绩预告', 'url': 'https://data.eastmoney.com/bbsj/yjyg.html'},
            {'key': 'disclosure_schedule', 'title': '预约披露时间', 'url': 'https://data.eastmoney.com/bbsj/yysj.html'},
            {'key': 'balance_sheet', 'title': '资产负债表', 'url': 'https://data.eastmoney.com/bbsj/zcfz.html'},
            {'key': 'income_statement', 'title': '利润表', 'url': 'https://data.eastmoney.com/bbsj/lrb.html'},
            {'key': 'cash_flow_statement', 'title': '现金流量表', 'url': 'https://data.eastmoney.com/bbsj/xjll.html'},
        ],
    },
]
EVIDENCE_DOMAINS = ('announcements', 'risk_alerts', 'lhb', 'concept_industry', 'financials')
CORE_ENHANCED_EVIDENCE_DOMAINS = (
    'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength', 'yesterday_limit_strength',
    'popularity_heat', 'industry_board', 'sector_fund_flow', 'concept_capital_flow',
    'candidate_quote_recheck', 'candidate_fund_recheck', 'candidate_lhb_recheck',
    'candidate_announcement_recheck', 'candidate_intraday_replay',
)
EXPERIMENTAL_EVIDENCE_DOMAINS = (
    'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
ENHANCED_EVIDENCE_DOMAINS = CORE_ENHANCED_EVIDENCE_DOMAINS + EXPERIMENTAL_EVIDENCE_DOMAINS
ALL_EVIDENCE_DOMAINS = EVIDENCE_DOMAINS + ENHANCED_EVIDENCE_DOMAINS
HARD_BLOCK_DOMAINS = ('announcements', 'risk_alerts', 'lhb')
REQUIRED_EVIDENCE_DOMAINS = EVIDENCE_DOMAINS
CODE_KEYS = ('code', 'symbol', 'f12', '证券代码', '股票代码', 'SECURITY_CODE', 'SECUCODE', 'SECURITY_CODE_A')
DATE_KEYS = ('date', '公告日期', 'event_date', 'ANNOUNCE_DATE', 'NOTICE_DATE', 'TRADE_DATE', 'REPORT_DATE', 'END_DATE')
TEXT_KEYS = (
    'type', 'title', 'reason', 'summary', 'event', '公告标题', '名称', '内容', 'cells',
    'NOTICE_TITLE', 'TITLE', 'SECURITY_NAME_ABBR', 'SECURITY_NAME', 'BOARD_NAME',
    'CONCEPT_NAME', 'INDUSTRY_NAME', 'CHANGE_REASON', 'EXPLAIN', 'ABSTRACT',
    'REPORT_TYPE', 'PREDICT_TYPE', 'PERFORMANCE_CHANGE_REASON', 'RISK_TIP'
)
DOMAIN_PAGE_HINTS = {
    'announcements': ('公告', '异常波动', '监管函', '问询函'),
    'risk_alerts': ('风险', '风险提示', '重点监控', '异常交易', '退市风险'),
    'lhb': ('龙虎榜', '涨跌幅偏离', '买卖席位'),
    'concept_industry': ('概念', '行业', '板块'),
    'financials': ('财务', '业绩', '亏损', '年报', '季报'),
    'limitup_strength': ('涨停', '封板', '涨停板池'),
    'broken_limit_risk': ('炸板', '炸板池', '开板'),
    'consecutive_limit_strength': ('连板', '连板天数'),
    'yesterday_limit_strength': ('昨日涨停', '昨日连板'),
    'popularity_heat': ('人气榜', '热度', '股吧排行'),
    'industry_board': ('行业板块', '行业排行'),
    'sector_fund_flow': ('板块资金', '行业资金', '概念资金'),
    'candidate_quote_recheck': ('盘口', '五档', '分时', '行情走势'),
    'candidate_fund_recheck': ('个股资金流', '主力净流入'),
    'candidate_lhb_recheck': ('龙虎榜详情', '买卖席位'),
    'candidate_announcement_recheck': ('个股公告', '公告详情'),
    'block_trades': ('大宗交易', '折价', '溢价'),
    'lockup_expiry': ('解禁', '限售股'),
    'shareholder_changes': ('股东', '增持', '减持', '机构持仓', '股东户数'),
    'research_reports': ('研报', '评级', '目标价', '券商'),
    'earnings_preview': ('业绩预告', '业绩快报', '预增', '预减'),
    'ipo_calendar': ('新股', '申购', '发行', '中签率'),
    'trading_halts': ('停牌', '复牌', '重大事项'),
}
DOMAIN_URL_TOKENS = {
    'announcements': ('notice', 'notices', 'announc', 'pdf.dfcfw.com'),
    'risk_alerts': ('risk', 'warning', 'stib', 'regulator', 'supervise'),
    'lhb': ('lhb', 'longhubang'),
    'concept_industry': ('concept', 'boardlist.html#concept_board', 'gridlist.html#concept_board'),
    'financials': ('finance', 'financial', 'bbsj'),
    'limitup_strength': ('ztb/detail#type=ztgc',),
    'broken_limit_risk': ('ztb/detail#type=zbgc',),
    'consecutive_limit_strength': ('ztb/detail#type=ljb',),
    'yesterday_limit_strength': ('ztb/detail#type=zrzt',),
    'popularity_heat': ('guba.eastmoney.com/rank',),
    'industry_board': ('gridlist.html#industry_board',),
    'sector_fund_flow': ('bkzj/hy.html',),
    'concept_capital_flow': ('bkzj/gn.html',),
    'candidate_quote_recheck': ('quote.eastmoney.com/sh', 'quote.eastmoney.com/sz', 'quote.eastmoney.com/bj'),
    'candidate_fund_recheck': ('data.eastmoney.com/zjlx/0', 'data.eastmoney.com/zjlx/3', 'data.eastmoney.com/zjlx/6'),
    'candidate_lhb_recheck': ('data.eastmoney.com/stock/lhb,', 'lhb_stock'),
    'candidate_announcement_recheck': ('notices/stock', 'securitycode='),
    'block_trades': ('dzjy',),
    'lockup_expiry': ('dxf',),
    'shareholder_changes': ('gdhs',),
    'research_reports': ('data.eastmoney.com/report',),
    'earnings_preview': ('bbsj/yjyg',),
    'ipo_calendar': ('data.eastmoney.com/xg',),
    'trading_halts': ('tfpxx',),
}
RISK_PAGE_HINTS = tuple(hint for hints in DOMAIN_PAGE_HINTS.values() for hint in hints)
RISK_KEYWORDS = (
    '监管', '异常波动', '严重异常波动', '风险提示', '交易风险', '重点监控',
    '异常交易', '龙虎榜', '跌幅偏离', '亏损风险', '退市风险', '问询函', '监管函',
    '炸板', '减持', '解禁', '折价', '停牌', '复牌'
)
REGULATORY_HARD_KEYWORDS = (
    '严重异常波动', '异常波动', '交易风险提示', '风险提示公告', '风险警示',
    '重点监控', '异常交易', '问询函', '监管函', '关注函', '警示函',
    '退市风险', '实施其他风险警示', '实施退市风险警示'
)
REGULATORY_PAGE_CHROME_HINTS = (
    '财经 |', '股票 |', '行情 |', '数据 |', '全球 |', '美股 |', '港股 |', '期货 |',
    '外汇 |', '黄金 |', '银行 |', '基金 |', '理财 |', '保险 |', '债券 |', '视频 |',
    '股吧 |', '基金吧 |', '博客 |', '财富号 |', '搜索',
)
CATALYST_KEYWORDS = ('重大合同', '中标', '重组', '增持', '回购', '业绩预增', '扭亏', '订单', '项目', '涨停', '连板', '买入', '上调')
FINANCIAL_RISK_KEYWORDS = ('亏损', '业绩预亏', '大幅下降', '减值', '退市风险', '净利润为负')
ENHANCED_RISK_KEYWORDS = ('炸板', '减持', '解禁', '折价', '停牌', '下调')


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quotes-json', default='')
    ap.add_argument('--funds-json', default='')
    ap.add_argument('--risk-json', default='')
    ap.add_argument('--announcements-json', default='')
    ap.add_argument('--risk-alerts-json', default='')
    ap.add_argument('--lhb-json', default='')
    ap.add_argument('--concept-industry-json', default='')
    ap.add_argument('--financials-json', default='')
    ap.add_argument('--source-time', default='')
    ap.add_argument('--output-dir', default='')
    ap.add_argument('--min-pct', type=float, default=2.0)
    ap.add_argument('--max-pct', type=float, default=9.0)
    ap.add_argument('--max-candidates', type=int, default=80)
    ap.add_argument('--candidate-evidence-topn', type=int, default=None, help='candidate detail API coverage; default follows --max-candidates')
    ap.add_argument('--pages', type=int, default=80)
    ap.add_argument('--page-size', type=int, default=100)
    ap.add_argument('--risk-recent-days', type=int, default=RISK_RECENT_DAYS)
    ap.add_argument('--enhanced-candidate-tab-topn', type=int, default=3)
    ap.add_argument('--no-page-endpoint-fallback', action='store_true')
    ap.add_argument('--summarize-raw-jsonl', default='')
    return ap.parse_args()


_FILE_CACHE: dict = {}


def load_records(path, use_cache=True):
    if not path:
        return []
    path_str = str(path)
    if use_cache and path_str in _FILE_CACHE:
        return _FILE_CACHE[path_str]
    text = Path(path).read_text(encoding='utf-8').strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        result = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        if isinstance(payload, dict):
            result = None
            for key in ('quotes', 'funds', 'risks', 'records', 'data', 'rows'):
                if isinstance(payload.get(key), list):
                    result = payload[key]
                    break
            if result is None:
                result = [payload]
        elif isinstance(payload, list):
            result = payload
        else:
            result = []
    if use_cache:
        _FILE_CACHE[path_str] = result
    return result


def load_v2_scanner_data(scan_date=None):
    """Load data from the canonical direct API scanner output."""
    if not scan_date:
        scan_date = datetime.now().strftime('%Y-%m-%d')
    scan_dir = next(
        (
            BASE / 'data' / 'live_scan' / scan_date / label
            for label in ('eastmoney_scan_afternoon', 'eastmoney_scan_morning')
            if (BASE / 'data' / 'live_scan' / scan_date / label).exists()
        ),
        None,
    )
    if scan_dir is None or not scan_dir.exists():
        return {}
    
    data = {'scan_date': scan_date}
    files = {
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
                data[key] = load_records(str(path))
            except:
                data[key] = []
        else:
            data[key] = []
    
    return data


def enrich_candidates_with_v2_data(candidates, v2_data):
    """Enrich candidates with v2 scanner data (HSGT, earnings, lockup, announcements, stock list)."""
    if not v2_data:
        return candidates
    
    # Build stock list lookup for net_inflow_main
    stock_by_code = {}
    scan_date = v2_data.get('scan_date', '')
    if scan_date:
        stock_file = scan_dir / 'stock_all_a.jsonl'
        if stock_file.exists():
            try:
                with open(stock_file) as f:
                    for line in f:
                        stock = json.loads(line)
                        code = str(stock.get('f12', '')).zfill(6)
                        if code:
                            stock_by_code[code] = stock
            except:
                pass
    
    # Build lookup indices
    hsgt_by_code = {}
    for row in v2_data.get('hsgt_deals', []):
        code = str(row.get('SECURITY_CODE', '')).zfill(6)
        if code:
            hsgt_by_code.setdefault(code, []).append(row)
    
    earnings_by_code = {}
    for row in v2_data.get('earnings_preview', []):
        code = str(row.get('SECURITY_CODE', '')).zfill(6)
        if code:
            earnings_by_code.setdefault(code, []).append(row)
    
    lockup_by_code = {}
    for row in v2_data.get('lockup_expiry', []):
        code = str(row.get('SECURITY_CODE', '')).zfill(6)
        if code:
            lockup_by_code.setdefault(code, []).append(row)
    
    announcements_by_code = {}
    for row in v2_data.get('announcements', []):
        codes = row.get('codes', [])
        for c in codes:
            code = str(c.get('stock_code', '')).zfill(6)
            if code:
                announcements_by_code.setdefault(code, []).append(row)
    
    # HSGT summary for macro liquidity
    hsgt_summary = v2_data.get('hsgt_summary', [])
    hsgt_net_inflow = 0
    if hsgt_summary:
        for item in hsgt_summary:
            if isinstance(item, dict):
                for key in ['s2n', 'n2s']:
                    val = item.get(key)
                    if isinstance(val, (int, float)):
                        hsgt_net_inflow += val
    
    # Enrich each candidate
    for candidate in candidates:
        code = str(candidate.get('code', '')).zfill(6)
        
        # Stock list data (net_inflow_main from v2 scanner)
        v2_stock = stock_by_code.get(code)
        if v2_stock:
            net_inflow = v2_stock.get('f62')
            if net_inflow is not None and (candidate.get('net_inflow_main') is None or candidate.get('net_inflow_main') == 1.0):
                candidate['net_inflow_main'] = float(net_inflow)
        
        # HSGT data
        hsgt_rows = hsgt_by_code.get(code, [])
        if hsgt_rows:
            latest = hsgt_rows[0]
            net_amt = latest.get('NET_DEAL_AMT')
            if net_amt is not None:
                candidate['hsgt_net_inflow'] = float(net_amt)
            # Count consecutive days of net inflow
            consecutive = 0
            for row in hsgt_rows[:10]:
                if row.get('NET_DEAL_AMT') and float(row['NET_DEAL_AMT']) > 0:
                    consecutive += 1
                else:
                    break
            candidate['hsgt_consecutive_days'] = consecutive
        
        # Earnings preview
        earnings_rows = earnings_by_code.get(code, [])
        if earnings_rows:
            latest = earnings_rows[0]
            flags = []
            net_profit = latest.get('PARENT_NETPROFIT')
            if net_profit is not None:
                if float(net_profit) > 0:
                    flags.append('预增')
                else:
                    flags.append('预减')
            candidate['earnings_preview_flags'] = flags
        
        # Lockup expiry
        lockup_rows = lockup_by_code.get(code, [])
        if lockup_rows:
            latest = lockup_rows[0]
            free_date = latest.get('FREE_DATE', '')
            if free_date:
                try:
                    from datetime import date as _date
                    free_dt = _date.fromisoformat(free_date[:10])
                    today = _date.today()
                    days = (free_dt - today).days
                    candidate['lockup_days_to_expiry'] = days
                    free_shares = latest.get('FREE_SHARES', 0)
                    if free_shares:
                        candidate['lockup_amount_ratio'] = float(free_shares) / 100000000  # 亿股
                except:
                    pass
        
        # Announcements sentiment
        ann_rows = announcements_by_code.get(code, [])
        if ann_rows:
            sentiment = 'neutral'
            for ann in ann_rows[:5]:
                title = str(ann.get('title', ''))
                if any(kw in title for kw in ['利好', '增长', '突破', '中标', '签约']):
                    sentiment = 'positive'
                    break
                elif any(kw in title for kw in ['利空', '亏损', '处罚', '诉讼', '减持']):
                    sentiment = 'negative'
                    break
            candidate['announcement_sentiment'] = sentiment
        
        # Macro liquidity score
        candidate['macro_liquidity_score'] = 50 + min(50, max(-50, hsgt_net_inflow / 10000))

    # 计算sector_opportunity_score（基于板块资金流）
    # 构建板块资金流映射
    sector_flow_map = {}
    for flow in v2_data.get('flow_concept', []):
        name = flow.get('f14', '')
        inflow = float(flow.get('f62', 0) or 0)
        if name:
            sector_flow_map[name] = inflow / 1e8  # 转换为亿元

    for flow in v2_data.get('flow_industry', []):
        name = flow.get('f14', '')
        inflow = float(flow.get('f62', 0) or 0)
        if name:
            sector_flow_map[name] = inflow / 1e8

    # 为每个候选计算sector_opportunity_score
    for candidate in candidates:
        if candidate.get('sector_opportunity_score') is None or candidate.get('sector_opportunity_score') == 0:
            sector_name = candidate.get('sector_name', '')
            # 匹配板块
            matched_inflow = 0
            for sname, sinflow in sector_flow_map.items():
                if sname in sector_name or sector_name in sname:
                    matched_inflow = max(matched_inflow, sinflow)

            # 计算分数（0-1）
            if matched_inflow > 20:
                candidate['sector_opportunity_score'] = 1.0
            elif matched_inflow > 10:
                candidate['sector_opportunity_score'] = 0.8
            elif matched_inflow > 5:
                candidate['sector_opportunity_score'] = 0.6
            elif matched_inflow > 0:
                candidate['sector_opportunity_score'] = 0.4
            else:
                candidate['sector_opportunity_score'] = 0.2

    return candidates


def clear_file_cache():
    _FILE_CACHE.clear()


def build_data_directory_catalog_records(source_time):
    records = []
    ts = str(source_time or '')
    trade_date = ts[:10] if len(ts) >= 10 else datetime.now().strftime('%Y-%m-%d')
    for section_index, section in enumerate(EASTMONEY_DATA_DIRECTORY_CATALOG, start=1):
        section_key = str(section.get('key') or '').strip()
        section_title = str(section.get('title') or '').strip()
        section_url = str(section.get('url') or '').strip()
        items = section.get('items') or []
        for item_index, item in enumerate(items, start=1):
            item_key = str(item.get('key') or '').strip()
            item_title = str(item.get('title') or '').strip()
            item_url = str(item.get('url') or '').strip()
            if not item_key or not item_title or not item_url:
                continue
            records.append({
                'ts': ts,
                'date': trade_date,
                'domain': 'data_directory_catalog',
                'source': 'eastmoney_public_directory_catalog',
                'section_key': section_key,
                'section_title': section_title,
                'section_url': section_url,
                'section_index': section_index,
                'item_key': item_key,
                'item_title': item_title,
                'item_url': item_url,
                'item_index': item_index,
                'record_key': f'{section_key}:{item_key}',
                'title': f'{section_title} / {item_title}',
                'summary': f'{section_title} -> {item_title}',
            })
    return records


def build_data_directory_catalog(source_time=None):
    sections = []
    for section in EASTMONEY_DATA_DIRECTORY_CATALOG:
        items = [dict(item) for item in (section.get('items') or [])]
        sections.append({
            'key': section.get('key'),
            'title': section.get('title'),
            'url': section.get('url'),
            'item_count': len(items),
            'items': items,
        })
    records = build_data_directory_catalog_records(source_time)
    return {
        'source': 'eastmoney_public_directory_catalog',
        'section_count': len(sections),
        'record_count': len(records),
        'sections': sections,
        'records': records,
        'section_keys': [section.get('key') for section in sections if section.get('key')],
        'section_titles': [section.get('title') for section in sections if section.get('title')],
    }


A_SHARE_DATA_DIRECTORY_EXCLUDED_ITEM_KEYS = {
    'hk_notices',
    'us_notices',
    'bond_notices',
    'reits_subscription',
    'sh_a_notices',
    'sz_a_notices',
    'bj_a_notices',
    'chinext_notices',
    'star_notices',
    'pre_listing_a_notices',
    'dividend_plan',
    'earnings_bulletin',
    'earnings_preview',
    'disclosure_schedule',
    'balance_sheet',
    'income_statement',
    'cash_flow_statement',
    'stock_reports',
    'profit_forecast',
    'industry_reports',
    'strategy_reports',
    'broker_morning_reports',
    'macro_research',
    'new_stock_reports',
    'new_stock_calendar',
    'new_stock_meeting',
    'ipo_guidance',
    'new_stock_analysis',
    'neeq_qualified',
    'ab_price_compare',
    'ah_price_compare',
    'merger_reorg',
    'finance_calendar',
    'index_components',
    'analyst_index',
    'company_topics',
    'institution_survey',
    'securities_monthly',
    'hsgt_turnover',
    'hsgt_holdings',
    'ipo_registration_review',
    'valuation_analysis',
    'main_force_data',
    'new_stock_subscription',
    'private_placement',
    'rights_issue',
}


def normalized_url_key(url):
    parsed = urlparse(str(url or '').strip())
    netloc = (parsed.netloc or '').lower()
    path = (parsed.path or '/').rstrip('/').lower()
    fragment = (parsed.fragment or '').lower()
    return netloc, path, fragment


def build_a_share_data_directory_items():
    items = []
    seen = set()
    for section in EASTMONEY_DATA_DIRECTORY_CATALOG:
        section_key = str(section.get('key') or '').strip()
        section_title = str(section.get('title') or '').strip()
        section_url = str(section.get('url') or '').strip()
        for item in section.get('items') or []:
            item_key = str(item.get('key') or '').strip()
            item_title = str(item.get('title') or '').strip()
            item_url = str(item.get('url') or '').strip()
            if not item_key or not item_title or not item_url:
                continue
            if item_key in A_SHARE_DATA_DIRECTORY_EXCLUDED_ITEM_KEYS:
                continue
            identity = normalized_url_key(item_url)
            if identity in seen:
                continue
            seen.add(identity)
            items.append({
                'section_key': section_key,
                'section_title': section_title,
                'section_url': section_url,
                'item_key': item_key,
                'item_title': item_title,
                'item_url': item_url,
                'source_key': f'data_directory__{section_key}__{item_key}',
            })
    return items


def build_data_directory_catalog_content_source_map():
    return {item['source_key']: item['item_url'] for item in build_a_share_data_directory_items()}


def data_directory_item_for_url(url):
    identity = normalized_url_key(url)
    for item in build_a_share_data_directory_items():
        if normalized_url_key(item['item_url']) == identity:
            return item
    return None


def build_data_directory_content_records(item, rows, snapshot, source_time):
    records = []
    ts = str(source_time or '')
    trade_date = ts[:10] if len(ts) >= 10 else datetime.now().strftime('%Y-%m-%d')
    page_url = str((snapshot or {}).get('matched_url') or (snapshot or {}).get('url') or item.get('item_url') or '')
    page_title = str((snapshot or {}).get('matched_title') or (snapshot or {}).get('title') or item.get('item_title') or '')
    for row in rows:
        raw_text = str(row.get('raw_text') or '').strip()
        if not raw_text:
            continue
        text_id = hashlib.sha1(raw_text[:500].encode('utf-8')).hexdigest()[:16]
        code = code_from_row(row)
        title = str(first_present(row, ('名称', '股票名称', '简称', '证券简称', 'SECURITY_NAME_ABBR', 'SECURITY_NAME', 'BOARD_NAME', 'CONCEPT_NAME', 'INDUSTRY_NAME', 'TITLE'))[0] or raw_text[:120])
        record = {
            'ts': ts,
            'date': trade_date,
            'domain': 'data_directory_content',
            'source': 'eastmoney_data_directory_api_table',
            'section_key': item.get('section_key'),
            'section_title': item.get('section_title'),
            'section_url': item.get('section_url'),
            'item_key': item.get('item_key'),
            'item_title': item.get('item_title'),
            'item_url': item.get('item_url'),
            'page_url': page_url,
            'page_title': page_title,
            'table_index': row.get('table_index'),
            'row_index': row.get('row_index'),
            'record_key': f"{item.get('section_key')}:{item.get('item_key')}:{row.get('table_index')}:{row.get('row_index')}:{text_id}",
            'title': title[:200],
            'summary': raw_text[:500],
            'raw_text': raw_text,
            'header': row.get('header') or [],
            'cells': row.get('cells') or [],
        }
        if code:
            record['code'] = code
            record['SECURITY_CODE'] = code
        name_value, _ = first_present(row, ('名称', '股票名称', '简称', '证券简称', 'SECURITY_NAME_ABBR', 'SECURITY_NAME', 'BOARD_NAME', 'CONCEPT_NAME', 'INDUSTRY_NAME'))
        if name_value not in (None, ''):
            record['name'] = str(name_value)
        for key, value in row.items():
            if key in ('cells', 'table_index', 'row_index', 'header', 'raw_text'):
                continue
            if key not in record:
                record[key] = value
        records.append(record)
    return records


DATA_DIRECTORY_CONTENT_SOURCE_MAP = build_data_directory_catalog_content_source_map()


def normalize_code(value):
    if value is None:
        return ''
    text = str(value).strip()
    candidates = A_SHARE_CODE_RE.findall(text)
    if candidates:
        return candidates[0]
    if text.isdigit() and 1 <= len(text) <= 6:
        code = text.zfill(6)
        return code if is_a_share_code(code) else ''
    return ''


def code_from_free_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    candidates = A_SHARE_CODE_RE.findall(text)
    return candidates[0] if candidates else ''


_NAME_TO_CODE_CACHE = {}


def init_name_to_code_cache(quotes):
    global _NAME_TO_CODE_CACHE
    _NAME_TO_CODE_CACHE = {}
    for q in quotes:
        code = q.get('code') or ''
        name = q.get('name') or ''
        if code and name and len(name) >= 2:
            _NAME_TO_CODE_CACHE[name] = code


def code_from_name(name):
    if not name or len(str(name).strip()) < 2:
        return ''
    return _NAME_TO_CODE_CACHE.get(str(name).strip(), '')


def code_from_row(row):
    for key in CODE_KEYS:
        code = normalize_code(row.get(key))
        if code:
            return code
    cells = row.get('cells')
    if isinstance(cells, list):
        for cell in cells:
            code = code_from_free_text(cell)
            if code:
                return code
    text = evidence_text(row)
    code = code_from_free_text(text)
    if code:
        return code
    name = row.get('名称') or row.get('SECURITY_NAME_ABBR') or row.get('SECURITY_NAME') or row.get('name') or ''
    if name:
        resolved = code_from_name(name)
        if resolved:
            return resolved
    for key in row:
        if key in ('cells', 'header', 'raw_text', 'domain', 'source', 'table_index', 'row_index'):
            continue
        val = str(row[key])
        m = re.match(r'^(.{2,8})\[[-+]?\d+\.?\d*%\]', val)
        if m:
            resolved = code_from_name(m.group(1))
            if resolved:
                return resolved
    return ''


def num(value, default=0.0):
    if value in (None, '', '-'):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(',', '').strip()
    multiplier = 1.0
    if text.endswith('%'):
        text = text[:-1]
    if text.endswith('亿'):
        multiplier = 100000000.0
        text = text[:-1]
    elif text.endswith('万'):
        multiplier = 10000.0
        text = text[:-1]
    m = re.search(r'-?\d+(?:\.\d+)?', text)
    if not m:
        return fnum(value, default)
    return float(m.group(0)) * multiplier


def normalize_quote(row, source):
    code = normalize_code(row.get('code') or row.get('symbol') or row.get('f12'))
    return {
        'code': code,
        'exchange_market': row.get('exchange_market') or row.get('f13'),
        'name': row.get('name') or row.get('f14'),
        'board': board_for_code(code),
        'price': num(row.get('price', row.get('f2'))),
        'pct_chg': num(row.get('pct_chg', row.get('signal_pct', row.get('f3')))),
        'chg': num(row.get('chg', row.get('f4'))),
        'volume': num(row.get('volume', row.get('f5'))),
        'amount': num(row.get('amount', row.get('signal_amount', row.get('f6')))),
        'amplitude': num(row.get('amplitude', row.get('f7'))),
        'turnover_rate': num(row.get('turnover_rate', row.get('f8'))),
        'pe_dynamic': num(row.get('pe_dynamic', row.get('f9'))),
        'volume_ratio': num(row.get('volume_ratio', row.get('f10'))),
        'high': num(row.get('high', row.get('f15'))),
        'low': num(row.get('low', row.get('f16'))),
        'open': num(row.get('open', row.get('f17'))),
        'prev_close': num(row.get('prev_close', row.get('f18'))),
        'market_cap': num(row.get('market_cap', row.get('f20'))),
        'float_market_cap': num(row.get('float_market_cap', row.get('f21'))),
        'pb': num(row.get('pb', row.get('f23'))),
        'net_inflow_main': num(row.get('net_inflow_main', row.get('main_net_inflow', row.get('f62')))),
        'source': source,
        'raw_source': row,
    }


def merge_funds(quotes, fund_rows):
    fund_map = {}
    for row in fund_rows:
        code = normalize_code(row.get('code') or row.get('symbol') or row.get('f12'))
        if not code:
            continue
        fund_map[code] = row
    merged = []
    for quote in quotes:
        fund = fund_map.get(quote['code'])
        if fund:
            quote = dict(quote)
            quote['net_inflow_main'] = num(
                fund.get('net_inflow_main', fund.get('main_net_inflow', fund.get('主力净流入', quote.get('net_inflow_main'))))
            )
            quote['fund_flow_source'] = fund.get('source', 'eastmoney_api_scan_v2_fund_flow')
        merged.append(quote)
    return merged


def parse_date(value):
    if not value:
        return None
    text = str(value)
    m = re.search(r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})', text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def evidence_text(row):
    parts = []
    seen = set()
    for key in TEXT_KEYS:
        value = row.get(key)
        if isinstance(value, list):
            value = ' '.join(str(v) for v in value)
        if value:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                parts.append(text)
    for key, value in row.items():
        if key in TEXT_KEYS or key in CODE_KEYS or key in DATE_KEYS or isinstance(value, (dict, list)):
            continue
        if value in (None, ''):
            continue
        text = str(value).strip()
        if text and text not in seen and any(keyword in text for keyword in RISK_KEYWORDS + CATALYST_KEYWORDS + FINANCIAL_RISK_KEYWORDS + tuple(h for hints in DOMAIN_PAGE_HINTS.values() for h in hints)):
            seen.add(text)
            parts.append(text)
    return ' '.join(parts)


def event_date_from_row(row, fallback_text=''):
    for key in DATE_KEYS:
        date = parse_date(row.get(key))
        if date:
            return date
    return parse_date(fallback_text)


def has_regulatory_hard_keyword(text):
    return any(keyword in text for keyword in REGULATORY_HARD_KEYWORDS)


def regulatory_text(row):
    parts = []
    seen = set()
    for key in ('title', 'summary', 'reason', 'event', 'text', 'risk_tip', 'CHANGE_REASON', 'RISK_TIP'):
        value = row.get(key)
        if isinstance(value, list):
            value = ' '.join(str(item) for item in value if item)
        if not value:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    if parts:
        return ' '.join(parts)
    explicit_domain = row_domain(row)
    if explicit_domain in HARD_BLOCK_DOMAINS:
        return evidence_text(row)
    return ''


def risk_reason(row):
    text = regulatory_text(row)
    if not text:
        return ''
    if any(token in text for token in REGULATORY_PAGE_CHROME_HINTS) and not has_regulatory_hard_keyword(text):
        return ''
    if has_regulatory_hard_keyword(text):
        return text or 'EASTMONEY_RISK_DISCLOSURE'
    return ''


def row_domain(row, default='risk_alerts'):
    explicit = row.get('domain') or row.get('kind') or row.get('source_domain')
    if explicit in EVIDENCE_DOMAINS:
        return explicit
    text = evidence_text(row)
    for domain, hints in DOMAIN_PAGE_HINTS.items():
        if any(hint in text for hint in hints):
            return domain
    return default


def build_risk_map(risk_rows, source_time, recent_days):
    asof = parse_date(source_time) or datetime.now()
    risk_map = {}
    for row in risk_rows:
        code = code_from_row(row)
        if not code:
            continue
        reason = risk_reason(row)
        if not reason:
            continue
        event_date = event_date_from_row(row, reason)
        if event_date:
            age = (asof.date() - event_date.date()).days
            if age < 0 or age > recent_days:
                continue
            reason = f'{event_date.date().isoformat()} {reason}'
        else:
            reason = 'DATE_UNKNOWN ' + reason
        risk_map.setdefault(code, []).append(reason)
    return risk_map


def evidence_slot():
    slot = {domain: [] for domain in ALL_EVIDENCE_DOMAINS}
    slot['historical_risk_notes'] = []
    return slot


def build_evidence_pack(evidence_rows_by_domain, source_time, recent_days):
    asof = parse_date(source_time) or datetime.now()
    pack = {}
    historical_notes = {}
    for domain, rows in evidence_rows_by_domain.items():
        for row in rows:
            code = code_from_row(row)
            if not code:
                continue
            text = evidence_text(row)
            event_date = event_date_from_row(row, text)
            age_days = None if event_date is None else (asof.date() - event_date.date()).days
            record = {
                'domain': domain,
                'date': event_date.date().isoformat() if event_date else None,
                'age_days': age_days,
                'text': text[:500],
                'source': row.get('source', 'eastmoney_api_scan_v2_evidence'),
            }
            if age_days is not None and (age_days < 0 or age_days > recent_days) and domain in HARD_BLOCK_DOMAINS:
                historical_notes.setdefault(code, []).append(record)
                continue
            slot = pack.setdefault(code, evidence_slot())
            slot.setdefault(domain, []).append(record)
    for code, rows in historical_notes.items():
        slot = pack.setdefault(code, evidence_slot())
        slot['historical_risk_notes'].extend(rows)
    return pack


BOARD_LEVEL_DOMAINS = ('concept_capital_flow', 'sector_fund_flow', 'industry_board')


def enrich_evidence_pack_with_board_domains(evidence_pack, candidates, catalyst_index):
    sector_tags_by_symbol = catalyst_index.get('sector_tags_by_symbol', {})
    board_rows_by_domain = evidence_pack.pop('_board_rows_by_domain', {})
    lead_stock_map = defaultdict(lambda: defaultdict(list))
    for domain, rows in board_rows_by_domain.items():
        for row in rows:
            cells = row.get('cells', [])
            if len(cells) >= 2:
                lead_name = str(cells[-1]).strip()
                if lead_name and len(lead_name) >= 2 and lead_name not in ('净占比', '净额', '名称'):
                    lead_code = code_from_name(lead_name)
                    if lead_code:
                        lead_stock_map[lead_code][domain].append(row)
    for code, domains in lead_stock_map.items():
        slot = evidence_pack.setdefault(code, evidence_slot())
        for domain, rows in domains.items():
            if not slot.get(domain):
                slot[domain] = rows
    concept_api_map = catalyst_index.get('concept_api_members', {})
    code_to_concepts = defaultdict(list)
    for cname, members in concept_api_map.items():
        for code, _ in members:
            code_to_concepts[code].append(cname)
    for cand in candidates:
        code = cand.get('code', '')
        if not code:
            continue
        tags = set(sector_tags_by_symbol.get(code, []))
        tags.update(str(t) for t in (cand.get('sector_opportunity_tags') or []))
        tags.update(str(t) for t in (cand.get('concept_industry_tags') or []))
        tags.update(code_to_concepts.get(code, []))
        if not tags:
            continue
        slot = evidence_pack.setdefault(code, evidence_slot())
        for domain in BOARD_LEVEL_DOMAINS:
            if slot.get(domain):
                continue
            matched_rows = []
            for row in board_rows_by_domain.get(domain, []):
                board_name = row.get('_board_name', '')
                if board_name and board_name in tags:
                    matched_rows.append(row)
            if matched_rows:
                slot[domain] = matched_rows
    return evidence_pack


def texts_matching(evidence, domain, keywords):
    return [row.get('text', '') for row in evidence.get(domain, []) if any(keyword in row.get('text', '') for keyword in keywords)]


def evidence_flags(evidence):
    hard_rows = evidence.get('announcements', []) + evidence.get('risk_alerts', []) + evidence.get('lhb', [])
    def normalized_hard_text(row):
        text = str(row.get('text', '') or '').strip()
        if not text:
            return ''
        if len(text) > 240:
            return ''
        if text.count('\n') > 4:
            return ''
        if '|' in text and len(text) > 120:
            return ''
        return text
    texts = [normalized_hard_text(row) for row in hard_rows]
    texts = [text for text in texts if text]
    return {
        'risk_reasons': [text for text in texts if has_regulatory_hard_keyword(text)],
        'catalysts': [row.get('text', '') for row in evidence.get('announcements', []) if any(keyword in row.get('text', '') for keyword in CATALYST_KEYWORDS)],
        'financial_risk_flags': [row.get('text', '') for row in evidence.get('financials', []) if any(keyword in row.get('text', '') for keyword in FINANCIAL_RISK_KEYWORDS)],
        'lhb_risk_flags': [row.get('text', '') for row in evidence.get('lhb', []) if has_regulatory_hard_keyword(row.get('text', ''))],
        'concept_industry_tags': [row.get('text', '') for row in evidence.get('concept_industry', [])[:5]],
        'limitup_strength_tags': texts_matching(evidence, 'limitup_strength', ('涨停', '封板', '连板'))[:5],
        'broken_limit_risk_flags': texts_matching(evidence, 'broken_limit_risk', ('炸板', '开板'))[:5],
        'board_strength_tags': [row.get('text', '') for row in (evidence.get('industry_board', []) + evidence.get('sector_fund_flow', []))[:5]],
        'candidate_quote_recheck_tags': [row.get('text', '') for row in evidence.get('candidate_quote_recheck', [])[:5]],
        'candidate_fund_recheck_tags': texts_matching(evidence, 'candidate_fund_recheck', ('主力', '净流入', '资金'))[:5],
        'block_trade_flags': texts_matching(evidence, 'block_trades', ('大宗交易', '折价', '溢价'))[:5],
        'lockup_risk_flags': texts_matching(evidence, 'lockup_expiry', ('解禁', '限售'))[:5],
        'shareholder_change_flags': texts_matching(evidence, 'shareholder_changes', ('增持', '减持', '股东'))[:5],
        'research_rating_tags': texts_matching(evidence, 'research_reports', ('买入', '增持', '目标价', '评级'))[:5],
        'earnings_preview_flags': texts_matching(evidence, 'earnings_preview', ('预增', '预减', '预亏', '业绩'))[:5],
        'ipo_calendar_tags': texts_matching(evidence, 'ipo_calendar', ('新股', '申购', '发行'))[:5],
        'trading_halt_flags': texts_matching(evidence, 'trading_halts', ('停牌', '复牌'))[:5],
    }


def candidate_evidence_status(evidence):
    counts = {domain: len(evidence.get(domain, [])) for domain in EVIDENCE_DOMAINS}
    enhanced_counts = {domain: len(evidence.get(domain, [])) for domain in CORE_ENHANCED_EVIDENCE_DOMAINS}
    experimental_counts = {domain: len(evidence.get(domain, [])) for domain in EXPERIMENTAL_EVIDENCE_DOMAINS}
    all_soft_counts = {**enhanced_counts, **experimental_counts}
    matched_domains = [domain for domain, count in counts.items() if count]
    # risk_alerts empty = no risk = PASS (not missing)
    OPTIONAL_EVIDENCE_DOMAINS = {'risk_alerts'}
    missing_domains = [domain for domain in REQUIRED_EVIDENCE_DOMAINS if domain not in OPTIONAL_EVIDENCE_DOMAINS and not counts.get(domain)]
    return {
        'status': 'PASS' if not missing_domains else 'MISSING',
        'domain_counts': counts,
        'matched_domains': matched_domains,
        'missing_domains': missing_domains,
        'enhanced_domain_counts': enhanced_counts,
        'enhanced_matched_domains': [domain for domain, count in enhanced_counts.items() if count],
        'enhanced_missing_domains': [domain for domain, count in enhanced_counts.items() if not count],
        'experimental_domain_counts': experimental_counts,
        'experimental_matched_domains': [domain for domain, count in experimental_counts.items() if count],
        'experimental_missing_domains': [domain for domain, count in experimental_counts.items() if not count],
        'soft_domain_counts': all_soft_counts,
    }


def watchlist_stock_codes_from_snapshot(snapshot):
    text = str(snapshot.get('text', '')) if isinstance(snapshot, dict) else ''
    return sorted(set(WATCHLIST_STOCK_CODE_RE.findall(text)))


def structured_ts(source_time):
    return source_time.replace(' ', 'T') + ('+08:00' if '+' not in source_time and 'Z' not in source_time else '')


def row_name(row, fallback=''):
    return row.get('SECURITY_NAME_ABBR') or row.get('SECURITY_NAME') or row.get('name') or row.get('名称') or fallback


def event_type_for_text(text):
    if any(keyword in text for keyword in ('涨停', '封板', '连板')):
        return 'limit_up'
    if any(keyword in text for keyword in ('龙虎榜', '买入', '卖出', '营业部')):
        return 'lhb'
    if any(keyword in text for keyword in ('行业', '概念', '板块')):
        return 'sector'
    if any(keyword in text for keyword in ('主力', '净流入', '资金')):
        return 'fund_flow'
    if any(keyword in text for keyword in ('业绩', '公告', '预增', '预减', '亏损')):
        return 'earnings'
    if any(keyword in text for keyword in ('监管', '风险', '问询函', '关注函')):
        return 'risk'
    return 'unknown'


def sentiment_for_text(text):
    if any(keyword in text for keyword in ('风险', '监管', '问询', '减持', '解禁', '亏损', '炸板', '下调')):
        return 'negative'
    if any(keyword in text for keyword in ('涨停', '预增', '增持', '中标', '回购', '买入', '净流入')):
        return 'positive'
    return 'unknown'


def tag_tokens(text):
    tags = []
    for token in re.findall(r'[A-Za-z0-9]+|[一-龥]{2,8}', text):
        if token not in tags and token not in ('公告', '股票', '证券', '股份', '有限', '公司'):
            tags.append(token)
    return tags[:10]


BARE_SECTOR_TERMS = (
    '电力', '煤炭', '煤炭开采', '燃气', '燃气Ⅱ', '电网设备', '光伏设备',
    '风电设备', '火电', '水电', '绿色电力', '核能核电',
    'AI', '算力', '绿电', '电网', '核电', '储能', '机器人', '光伏', '风电', '半导体',
)
SECTOR_SUFFIXES = ('板块', '概念', '行业')

NEWS_CATALYST_REGULATORY_TERMS = (
    '股票交易异常波动', '异常波动', '严重异常波动', '监管', '问询函',
    '关注函', '异常交易', '停牌核查', '立案', '处罚',
)
NEWS_CATALYST_RISK_TERMS = (
    '风险提示', '重大风险提示', '退市风险', '减持', '解禁', '停牌', '复牌',
)
NEWS_CATALYST_POSITIVE_TERMS = (
    '中标', '订单', '合同', '预增', '利好', '业绩预增', '利润增长', '项目投产', '资产注入',
    '重组', '重组推进', '并购', '并购完成', '政策', '政策支持', '涨价', '产能扩张', 'AI', '算力',
    '电力', '煤炭', '绿电', '电网', '核电', '储能', '机器人', '光伏', '风电',
    '火电', '水电', '半导体',
)
NEWS_CATALYST_STALE_MINUTES = 24 * 60


def matched_terms(text, terms):
    text = text or ''
    return [term for term in terms if term and term in text]


def parse_source_time_dt(source_time):
    if not source_time:
        return None
    text = str(source_time).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text[:len(fmt)], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(source_time).replace('Z', '+00:00'))
    except ValueError:
        return None


def classify_news_catalyst_quality(text, source_time, publish_time=None, row=None):
    text = text or ''
    row = row if isinstance(row, dict) else {}
    publish_dt = publish_time if isinstance(publish_time, datetime) else parse_date(publish_time) or event_date_from_row(row, text)
    source_dt = parse_source_time_dt(source_time)
    freshness_minutes = None
    if publish_dt and source_dt:
        freshness_minutes = round((source_dt - publish_dt).total_seconds() / 60.0, 2)

    regulatory_terms = matched_terms(text, NEWS_CATALYST_REGULATORY_TERMS)
    risk_terms = matched_terms(text, NEWS_CATALYST_RISK_TERMS)
    positive_terms = matched_terms(text, NEWS_CATALYST_POSITIVE_TERMS)
    sector_terms = sector_tags_from_text(text, tag_tokens(text))

    if freshness_minutes is not None and freshness_minutes > NEWS_CATALYST_STALE_MINUTES:
        category = 'stale'
    elif regulatory_terms:
        category = 'regulatory_notice'
    elif risk_terms:
        category = 'risk_notice'
    elif sector_terms and (positive_terms or any(marker in text for marker in ('板块', '概念', '行业'))):
        category = 'sector_catalyst'
    elif positive_terms:
        category = 'positive_catalyst'
    else:
        category = 'neutral'

    usable = category in ('positive_catalyst', 'sector_catalyst')
    risk_evidence = regulatory_terms + [term for term in risk_terms if term not in regulatory_terms]
    confidence = 0.05
    if category in ('positive_catalyst', 'sector_catalyst'):
        confidence += 0.35
    elif category in ('regulatory_notice', 'risk_notice'):
        confidence += 0.4
    elif category == 'stale':
        confidence += 0.15
    else:
        confidence += 0.08
    confidence += min(0.18, 0.04 * len(positive_terms))
    confidence += min(0.12, 0.03 * len(sector_terms))
    confidence += min(0.16, 0.05 * len(risk_evidence))
    if freshness_minutes is not None:
        if freshness_minutes <= 60:
            confidence += 0.12
        elif freshness_minutes <= 240:
            confidence += 0.08
        elif freshness_minutes <= NEWS_CATALYST_STALE_MINUTES:
            confidence += 0.03
        else:
            confidence -= 0.08
    if category == 'stale':
        confidence *= 0.75
    confidence = round(max(0.0, min(1.0, confidence)), 4)
    evidence_refs = [evidence_reference_for_row(row)] if row else []
    evidence_refs = [ref for ref in evidence_refs if ref]
    observation = (
        'risk_observation' if category in ('regulatory_notice', 'risk_notice')
        else 'catalyst_observation' if usable
        else f'{category}_observation'
    )
    return {
        'category': category,
        'usable_for_candidate_generation': usable,
        'usable_for_paper_pick': usable,
        'risk_terms': risk_evidence,
        'risk_evidence': risk_evidence,
        'regulatory_hard_block': bool(risk_evidence),
        'positive_terms': positive_terms,
        'confidence': confidence,
        'freshness_minutes': freshness_minutes,
        'sector_terms': sector_terms,
        'evidence_refs': evidence_refs,
        'observation': observation,
    }


def sector_tags_from_text(text, tags=None):
    text = text or ''
    tags = [tag.strip() for tag in (tags or tag_tokens(text)) if isinstance(tag, str) and tag.strip()]
    sectors = []
    def add_sector(term):
        if term and term not in sectors:
            sectors.append(term)

    for source in [*tags, text]:
        if not source:
            continue
        for term in sorted(BARE_SECTOR_TERMS, key=len, reverse=True):
            if term and term in source:
                add_sector(term)
    for term in BARE_SECTOR_TERMS:
        if term in sectors:
            continue
        pattern = r'(?<![一-龥A-Za-z0-9])' + re.escape(term) + r'(?![一-龥A-Za-z0-9])'
        if re.search(pattern, text):
            add_sector(term)
    for tag in tags:
        if tag in sectors:
            continue
        if tag.endswith(SECTOR_SUFFIXES):
            stripped = re.sub(r'(板块|概念|行业)$', '', tag).strip()
            if stripped and stripped in BARE_SECTOR_TERMS:
                add_sector(stripped)
    return sectors


SECTOR_RESEARCH_MAP = {
    '电力': {
        'sub_chains': ['火电', '水电', '绿电', '电网', '煤电联动', '储能'],
        'positive_catalysts': ['电价', '容量电价', '迎峰度夏', '电力缺口', '算力用电', '绿电', '电网投资'],
        'risk_terms': ['煤价上涨', '限电风险', '监管处罚'],
        'candidate_tags': ['电力', '绿色电力', '火电', '水电', '电网设备', '储能'],
    },
    '煤炭': {
        'sub_chains': ['煤炭开采', '煤电联动', '动力煤', '焦煤'],
        'positive_catalysts': ['涨价', '保供', '供需改善', '煤电联动'],
        'risk_terms': ['煤价下跌', '安全检查', '产能收缩'],
        'candidate_tags': ['煤炭', '煤炭开采', '煤化工'],
    },
    '燃气': {
        'sub_chains': ['天然气', '城市燃气', 'LNG', '燃气管网'],
        'positive_catalysts': ['气价上涨', '冬季保供', '管网投资'],
        'risk_terms': ['气价波动', '限气', '安全事故'],
        'candidate_tags': ['燃气', '燃气Ⅱ', '天然气'],
    },
    '储能': {
        'sub_chains': ['电化学储能', '户储', '工商业储能', '储能电站'],
        'positive_catalysts': ['并网', '订单', '容量电价', '峰谷价差'],
        'risk_terms': ['安全事故', '价格竞争', '库存压力'],
        'candidate_tags': ['储能', '储能设备'],
    },
    '电网设备': {
        'sub_chains': ['特高压', '配网', '变压器', '电力装备'],
        'positive_catalysts': ['电网投资', '特高压', '配网升级', '海外订单'],
        'risk_terms': ['项目延期', '招标不及预期', '监管处罚'],
        'candidate_tags': ['电网设备', '电力设备', '输变电'],
    },
    '机器人': {
        'sub_chains': ['工业机器人', '服务机器人', '人形机器人', '伺服'],
        'positive_catalysts': ['订单', '量产', '客户突破', '国产替代'],
        'risk_terms': ['减速机价格战', '订单取消', '商誉减值'],
        'candidate_tags': ['机器人', '人形机器人', '工业机器人'],
    },
    'CPO': {
        'sub_chains': ['光模块', '高速光互联', '算力网络'],
        'positive_catalysts': ['AI需求', '算力扩容', '订单', '出货增长'],
        'risk_terms': ['需求放缓', '库存去化', '价格竞争'],
        'candidate_tags': ['CPO', '光模块', '算力'],
    },
    '半导体': {
        'sub_chains': ['芯片', '晶圆代工', '封测', '设备'],
        'positive_catalysts': ['国产替代', '订单', '扩产', '良率提升'],
        'risk_terms': ['制裁', '库存', '价格竞争', '减值'],
        'candidate_tags': ['半导体', '芯片', '集成电路'],
    },
    '创新药': {
        'sub_chains': ['生物药', 'CXO', '临床'],
        'positive_catalysts': ['临床', '审批', '医保', '出海', '授权'],
        'risk_terms': ['临床失败', '审批延迟', '医保降价'],
        'candidate_tags': ['创新药', '医药', '生物医药', 'CXO'],
    },
}


def research_chain_tags_for_text(text, sector_tags=None):
    text = text or ''
    sector_tags = [tag.strip() for tag in (sector_tags or []) if isinstance(tag, str) and tag.strip()]
    industry_chain_tags = []
    positive_catalyst_hits = []
    risk_term_hits = []
    matched_sector_keys = []

    def add_unique(values, value):
        if value and value not in values:
            values.append(value)

    observed_sector_tags = sector_tags_from_text(text, sector_tags)
    for tag in observed_sector_tags:
        add_unique(industry_chain_tags, tag)

    for sector, spec in SECTOR_RESEARCH_MAP.items():
        sector_aliases = [sector, *spec.get('candidate_tags', []), *spec.get('sub_chains', [])]
        sector_hit = sector in observed_sector_tags or any(alias in text for alias in sector_aliases)
        if not sector_hit:
            continue
        add_unique(matched_sector_keys, sector)
        add_unique(industry_chain_tags, sector)
        for chain_tag in spec.get('candidate_tags', []) + spec.get('sub_chains', []):
            if chain_tag in text or chain_tag in observed_sector_tags:
                add_unique(industry_chain_tags, chain_tag)
        for term in spec.get('positive_catalysts', []):
            if term in text:
                add_unique(positive_catalyst_hits, term)
        for term in spec.get('risk_terms', []):
            if term in text:
                add_unique(risk_term_hits, term)

    if not matched_sector_keys:
        for tag in sector_tags:
            add_unique(industry_chain_tags, tag)

    confidence = 0.0
    if matched_sector_keys:
        confidence += 0.24 + min(0.24, 0.08 * len(matched_sector_keys))
    if positive_catalyst_hits:
        confidence += 0.18 + min(0.20, 0.05 * len(positive_catalyst_hits))
    if observed_sector_tags:
        confidence += 0.10
    if risk_term_hits:
        confidence += 0.08
    confidence += min(0.12, 0.02 * len(industry_chain_tags))

    return {
        'industry_chain_tags': industry_chain_tags,
        'sector_mapping_confidence': round(min(1.0, confidence), 4),
        'positive_catalyst_hits': positive_catalyst_hits,
        'risk_term_hits': risk_term_hits,
    }


def evidence_reference_for_row(row):
    if not isinstance(row, dict):
        return {}
    ref = {}
    for key in ('domain', 'source', 'symbol'):
        value = row.get(key)
        if value not in (None, ''):
            ref[key] = str(value)
    text = row.get('title') or row.get('reason_text') or row.get('name') or row.get('NOTICE_TITLE') or row.get('raw_text')
    if text:
        ref['text'] = str(text)[:120]
    date = row.get('date') or row.get('publish_time') or row.get('notice_date') or row.get('ts')
    if date not in (None, ''):
        ref['date'] = str(date)[:19]
    return ref


def classify_catalyst_quality(text, source_time, evidence_rows):
    evidence_rows = [row for row in (evidence_rows or []) if isinstance(row, dict)]
    primary_row = evidence_rows[0] if evidence_rows else {}
    publish_time = primary_row.get('publish_time') or primary_row.get('date') or primary_row.get('notice_date')
    quality = dict(classify_news_catalyst_quality(text, source_time, publish_time, primary_row))
    combined_text = ' '.join(
        [text or ''] + [evidence_text(row) for row in evidence_rows if evidence_text(row)]
    ).strip()
    sector_tags = sector_tags_from_text(combined_text, tag_tokens(combined_text))
    chain = research_chain_tags_for_text(combined_text, sector_tags)
    evidence_refs = [evidence_reference_for_row(row) for row in evidence_rows[:5]]
    evidence_refs = [ref for ref in evidence_refs if ref]

    if quality.get('category') != 'stale':
        risk_hit = any(term in combined_text for term in (
            '股票交易异常波动', '异常波动', '严重异常波动', '异常交易', '停牌核查', '立案', '处罚',
        ))
        risk_warning_hit = any(term in combined_text for term in (
            '风险提示', '重大风险提示', '退市风险', '减持', '减持计划',
        ))
        if risk_hit:
            quality['category'] = 'regulatory_notice'
        elif risk_warning_hit:
            quality['category'] = 'risk_notice'
        elif quality.get('category') == 'neutral' and chain['positive_catalyst_hits']:
            quality['category'] = 'positive_catalyst'
        elif quality.get('category') == 'neutral' and (chain['industry_chain_tags'] or sector_tags):
            quality['category'] = 'sector_catalyst'

    quality['usable_for_candidate_generation'] = quality.get('category') in ('positive_catalyst', 'sector_catalyst')
    quality['usable_for_paper_pick'] = quality['usable_for_candidate_generation']
    quality['risk_terms'] = sorted(set((quality.get('risk_terms') or []) + chain['risk_term_hits']))
    quality['positive_terms'] = sorted(set((quality.get('positive_terms') or []) + chain['positive_catalyst_hits']))
    quality['industry_chain_tags'] = chain['industry_chain_tags']
    quality['sector_mapping_confidence'] = chain['sector_mapping_confidence']
    quality['evidence_refs'] = evidence_refs
    quality_confidence = num(quality.get('confidence'), 0.0)
    quality_confidence = max(quality_confidence, chain['sector_mapping_confidence'])
    quality_confidence += min(0.10, 0.02 * len(quality['positive_terms']))
    quality_confidence += min(0.08, 0.02 * len(quality['industry_chain_tags']))
    if quality.get('category') in ('positive_catalyst', 'sector_catalyst'):
        quality_confidence = max(quality_confidence, 0.55)
    elif quality.get('category') in ('regulatory_notice', 'risk_notice'):
        quality_confidence = max(quality_confidence, 0.80)
    elif quality.get('category') == 'stale':
        quality_confidence = min(quality_confidence, 0.35)
    quality['confidence'] = round(max(0.0, min(1.0, quality_confidence)), 4)
    quality['regulatory_hard_block'] = quality.get('category') in ('regulatory_notice', 'risk_notice') or bool(quality['risk_terms'])
    if quality.get('category') in ('regulatory_notice', 'risk_notice'):
        quality['observation'] = 'risk_observation'
    elif quality['usable_for_candidate_generation']:
        quality['observation'] = 'catalyst_observation'
    else:
        quality['observation'] = f"{quality.get('category') or 'neutral'}_observation"
    return quality


def classify_a_share_risk_review(rows):
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    abnormal_movement_notice = False
    risk_warning_notice = False
    reduction_risk = False
    financial_red_flags = []
    lhb_risk_flags = []
    evidence_refs = []

    def add_unique(values, value):
        if value and value not in values:
            values.append(value)

    for row in rows:
        text = evidence_text(row)
        if not text:
            continue
        evidence_ref = evidence_reference_for_row(row)
        if evidence_ref:
            evidence_refs.append(evidence_ref)
        if any(term in text for term in ('股票交易异常波动', '异常波动', '严重异常波动', '异常交易', '停牌核查')):
            abnormal_movement_notice = True
        if any(term in text for term in ('风险提示', '重大风险提示', '监管', '问询函', '关注函', '处罚', '立案')):
            risk_warning_notice = True
        if any(term in text for term in ('减持', '减持计划', '股份减持')):
            reduction_risk = True
        for term in ('业绩亏损', '商誉减值', '诉讼', '冻结', '质押', '退市风险', '重大风险提示'):
            if term in text:
                add_unique(financial_red_flags, term)
        if row.get('domain') == 'lhb' or '龙虎榜' in text:
            if any(term in text for term in ('卖出', '净卖出', '大幅卖出', '机构卖出')):
                add_unique(lhb_risk_flags, 'lhb_sell_pressure')
            buy_amt = num(row.get('BUY_AMT'), None)
            sell_amt = num(row.get('SELL_AMT'), None)
            if buy_amt is not None and sell_amt is not None and sell_amt > buy_amt:
                add_unique(lhb_risk_flags, 'lhb_net_sell_pressure')

    disqualified_for_paper_pick = bool(
        abnormal_movement_notice
        or risk_warning_notice
        or reduction_risk
        or financial_red_flags
        or lhb_risk_flags
    )
    return {
        'abnormal_movement_notice': abnormal_movement_notice,
        'risk_warning_notice': risk_warning_notice,
        'reduction_risk': reduction_risk,
        'financial_red_flags': financial_red_flags,
        'lhb_risk_flags': lhb_risk_flags,
        'disqualified_for_paper_pick': disqualified_for_paper_pick,
        'evidence_refs': evidence_refs[:5],
    }


def build_adversarial_review(candidate, evidence_rows):
    evidence_rows = [row for row in (evidence_rows or []) if isinstance(row, dict)]
    source_time = str(candidate.get('source_time') or candidate.get('signal_date') or '')
    primary_text = evidence_text(evidence_rows[0]) if evidence_rows else evidence_text(candidate)
    quality = classify_catalyst_quality(primary_text, source_time, evidence_rows)
    risk_review = classify_a_share_risk_review(evidence_rows)
    bear_case_flags = []
    disqualifying_flags = []

    if quality.get('category') == 'stale':
        bear_case_flags.append('stale_news')
    if (candidate.get('search_layer_hint') or '') in ('news_catalyst_low_position', 'sector_catalyst_low_position'):
        if (candidate.get('sector_catalyst_score') or 0.0) > 0 and (candidate.get('news_catalyst_strength') or 0.0) <= 0.1:
            bear_case_flags.append('concept_hype_without_company_link')
    if (candidate.get('fund_flow_momentum') or 0.0) < 0.35 and (candidate.get('volume_ratio') or 0.0) < 1.5:
        bear_case_flags.append('weak_fund_confirmation')
    if (candidate.get('signal_pct') or 0.0) >= 7.0 or (candidate.get('close_position_score') or 0.0) >= 0.9:
        bear_case_flags.append('near_limit_chase')

    if quality.get('category') in ('risk_notice', 'regulatory_notice'):
        add_reason = 'risk_notice_as_catalyst'
        if add_reason not in disqualifying_flags:
            disqualifying_flags.append(add_reason)
        if 'regulatory_hard_block' not in disqualifying_flags:
            disqualifying_flags.append('regulatory_hard_block')
    if risk_review.get('financial_red_flags'):
        if 'financial_red_flag' not in disqualifying_flags:
            disqualifying_flags.append('financial_red_flag')
    if not evidence_rows:
        if 'evidence_missing' not in disqualifying_flags:
            disqualifying_flags.append('evidence_missing')
    if risk_review.get('disqualified_for_paper_pick') and 'regulatory_hard_block' not in disqualifying_flags:
        disqualifying_flags.append('regulatory_hard_block')

    return {
        'bear_case_flags': bear_case_flags,
        'disqualifying_flags': disqualifying_flags,
    }


def build_research_panel(research_signals, candidate):
    research_signals = research_signals if isinstance(research_signals, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    quality = research_signals.get('catalyst_quality') or {}
    sector_mapping = research_signals.get('sector_mapping') or {}
    risk_review = research_signals.get('a_share_risk_review') or {}
    adversarial_review = research_signals.get('adversarial_review') or {}

    if quality.get('category') in ('positive_catalyst', 'sector_catalyst'):
        news_analyst = 'PASS'
    elif quality.get('category') == 'neutral' and (
        quality.get('evidence_refs') or quality.get('industry_chain_tags') or quality.get('positive_terms')
    ):
        news_analyst = 'PARTIAL'
    else:
        news_analyst = 'FAIL'

    mapping_confidence = num(sector_mapping.get('mapping_confidence'), 0.0)
    if mapping_confidence >= 0.5:
        sector_analyst = 'PASS'
    elif sector_mapping.get('sectors') or sector_mapping.get('related_symbols'):
        sector_analyst = 'PARTIAL'
    else:
        sector_analyst = 'FAIL'

    low_position_catalyst_score = num(candidate.get('low_position_catalyst_score'), 0.0)
    early_opportunity_score = num(candidate.get('early_opportunity_score'), 0.0)
    if low_position_catalyst_score >= 0.6 or early_opportunity_score >= 0.65:
        technical_analyst = 'PASS'
    elif (
        num(candidate.get('volume_ratio'), 0.0) >= 1.2
        or num(candidate.get('close_position_score'), 0.0) >= 0.55
        or num(candidate.get('fund_flow_momentum'), 0.0) >= 0.25
        or num(candidate.get('time_series_momentum'), 0.0) >= 0.15
        or num(candidate.get('net_inflow_main'), 0.0) > 0
    ):
        technical_analyst = 'PARTIAL'
    else:
        technical_analyst = 'FAIL'

    risk_analyst = 'FAIL' if risk_review.get('disqualified_for_paper_pick') else 'PASS'
    if adversarial_review.get('disqualifying_flags'):
        bear_case = 'FAIL'
    elif adversarial_review.get('bear_case_flags'):
        bear_case = 'PARTIAL'
    else:
        bear_case = 'PASS'

    statuses = [news_analyst, sector_analyst, technical_analyst, risk_analyst, bear_case]
    if 'FAIL' in (risk_analyst, bear_case):
        overall = 'FAIL'
    elif statuses.count('PASS') >= 3:
        overall = 'PASS'
    else:
        overall = 'PARTIAL'

    return {
        'news_analyst': news_analyst,
        'sector_analyst': sector_analyst,
        'technical_analyst': technical_analyst,
        'risk_analyst': risk_analyst,
        'bear_case': bear_case,
        'overall': overall,
    }


def build_historical_pattern(candidate, sector_mapping, catalyst_quality):
    candidate = candidate if isinstance(candidate, dict) else {}
    sector_mapping = sector_mapping if isinstance(sector_mapping, dict) else {}
    catalyst_quality = catalyst_quality if isinstance(catalyst_quality, dict) else {}
    search_layer_hint = str(candidate.get('search_layer_hint') or '')
    setup_type = str(candidate.get('setup_type') or '')
    candidate_stage = str(candidate.get('candidate_stage') or '')
    pattern_name = 'formal_high_score'
    if search_layer_hint == 'news_catalyst_low_position' or setup_type in ('NEWS_CATALYST_LOW_POSITION', 'TOPIC_FUND_IGNITION'):
        pattern_name = 'news_catalyst_low_position'
    elif search_layer_hint == 'sector_catalyst_low_position' or setup_type == 'SECTOR_NEWS_LOW_POSITION':
        pattern_name = 'sector_catalyst_low_position'
    elif search_layer_hint == 'intraday_alert_reversal' or setup_type == 'INTRADAY_ALERT_REVERSAL':
        pattern_name = 'intraday_alert_reversal'
    elif candidate_stage == 'underwater' or 'UNDERWATER' in setup_type:
        pattern_name = 'underwater_reversal'
    elif (candidate.get('low_position_catalyst_score') or 0.0) >= 0.6:
        pattern_name = 'topic_fund_ignition'
    elif (candidate.get('score') is not None) and (candidate.get('signal_pct') or 0.0) >= 7.0:
        pattern_name = 'formal_high_score'
    sector_label = ''
    for value in sector_mapping.get('sectors') or []:
        if value:
            sector_label = str(value)
            break
    if not sector_label:
        for value in catalyst_quality.get('industry_chain_tags') or []:
            if value in SECTOR_RESEARCH_MAP:
                sector_label = str(value)
                break
    if not sector_label:
        sector_label = str(candidate.get('code') or candidate.get('symbol') or 'generic')
    return {
        'pattern_name': pattern_name,
        'backtest_score': None,
        'forward_evidence_count': 0,
        'requires_forward_tracking': True,
        'forward_eval_key': f'{pattern_name}:{sector_label}',
    }


def build_research_signals(candidate, evidence_rows, source_time, sector_snapshot=None):
    candidate = candidate if isinstance(candidate, dict) else {}
    evidence_rows = [row for row in (evidence_rows or []) if isinstance(row, dict)]
    sector_snapshot = sector_snapshot or []
    source_time = str(source_time or candidate.get('source_time') or candidate.get('signal_date') or '')
    all_text = ' '.join(
        [evidence_text(row) for row in evidence_rows if evidence_text(row)]
    ).strip()
    sector_tags = normalize_string_tags(candidate.get('sector_opportunity_tags') or [])
    if not sector_tags and all_text:
        sector_tags = sector_tags_from_text(all_text, tag_tokens(all_text))
    chain = research_chain_tags_for_text(all_text or evidence_text(candidate), sector_tags)
    quality = classify_catalyst_quality(all_text or evidence_text(candidate), source_time, evidence_rows)
    candidate_quality_categories = normalize_string_tags(candidate.get('news_catalyst_quality_categories') or [])
    if quality.get('category') == 'neutral' and quality.get('positive_terms'):
        quality['category'] = 'positive_catalyst'
    elif quality.get('category') == 'neutral' and (chain['industry_chain_tags'] or sector_tags):
        quality['category'] = 'sector_catalyst'
    elif quality.get('category') == 'neutral' and candidate_quality_categories:
        if 'regulatory_notice' in candidate_quality_categories:
            quality['category'] = 'regulatory_notice'
        elif 'risk_notice' in candidate_quality_categories:
            quality['category'] = 'risk_notice'
        elif 'positive_catalyst' in candidate_quality_categories:
            quality['category'] = 'positive_catalyst'
        elif 'sector_catalyst' in candidate_quality_categories or sector_tags:
            quality['category'] = 'sector_catalyst'
    quality['usable_for_candidate_generation'] = quality.get('category') in ('positive_catalyst', 'sector_catalyst')
    quality['usable_for_paper_pick'] = quality['usable_for_candidate_generation']

    candidate_symbol = str(candidate.get('code') or candidate.get('symbol') or '')
    related_symbols = []
    for snapshot in sector_snapshot:
        sector_name = str(snapshot.get('sector') or '').strip()
        if not sector_name:
            continue
        if sector_name not in sector_tags and sector_name not in chain['industry_chain_tags']:
            continue
        for symbol in snapshot.get('symbols') or []:
            symbol = str(symbol).strip()
            if symbol and symbol != candidate_symbol and symbol not in related_symbols:
                related_symbols.append(symbol)

    mapping_confidence = max(
        chain['sector_mapping_confidence'],
        min(1.0, (candidate.get('sector_catalyst_score') or 0.0) * 0.8 + (candidate.get('sector_news_strength') or 0.0) * 0.2),
    )
    sector_mapping = {
        'sectors': normalize_string_tags(sector_tags or [tag for tag in chain['industry_chain_tags'] if tag in SECTOR_RESEARCH_MAP]),
        'related_symbols': related_symbols,
        'mapping_confidence': round(mapping_confidence, 4),
    }
    a_share_risk_review = classify_a_share_risk_review(evidence_rows)
    adversarial_review = build_adversarial_review(candidate, evidence_rows)
    historical_pattern = build_historical_pattern(candidate, sector_mapping, quality)
    research_signals = {
        'industry_chain_tags': chain['industry_chain_tags'],
        'catalyst_quality': quality,
        'sector_mapping': sector_mapping,
        'a_share_risk_review': a_share_risk_review,
        'adversarial_review': adversarial_review,
        'historical_pattern': historical_pattern,
    }
    research_signals['research_panel'] = build_research_panel(research_signals, candidate)
    return research_signals


def rows_for_domains(rows_by_domain, domains):
    rows = []
    for domain in domains:
        for row in rows_by_domain.get(domain, []):
            copy = dict(row)
            copy.setdefault('domain', domain)
            rows.append(copy)
    return rows


def first_present(row, keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ''):
            return value, key
    return None, None


def parse_rank_from_text(text):
    patterns = (
        r'人气榜\s*第?\s*(\d{1,4})',
        r'热度排名\s*第?\s*(\d{1,4})',
        r'排名\s*第?\s*(\d{1,4})',
        r'第\s*(\d{1,4})\s*名',
    )
    for pattern in patterns:
        match = re.search(pattern, text or '')
        if match:
            return float(match.group(1))
    return None


def parse_keyword_number(text, keywords):
    for keyword in keywords:
        match = re.search(re.escape(keyword) + r'\s*[:：]?\s*(-?\d+(?:\.\d+)?\s*(?:亿|万|%)?)', text or '')
        if match:
            return num(match.group(1), None), keyword
    return None, None


def safe_ratio(numerator, denominator):
    if denominator in (None, 0):
        return None
    return round(numerator / denominator, 4)


def parse_structured_dt(value):
    if not value:
        return None
    text = str(value).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_jsonl_rows(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def raw_jsonl_summary(path):
    path = Path(path)
    if not path.exists():
        return {'status': 'MISSING', 'path': str(path), 'error': 'RAW_JSONL_NOT_FOUND'}
    domain_counts = Counter()
    source_counts = Counter()
    examples = {}
    malformed_count = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if not isinstance(row, dict):
            continue
        domain = row.get('domain') or row.get('kind') or 'unknown'
        source = row.get('source') or 'unknown'
        domain_counts[domain] += 1
        source_counts[source] += 1
        if domain not in examples:
            examples[domain] = {key: row.get(key) for key in ('code', 'SECURITY_CODE', 'title', 'name', 'source') if row.get(key)}
    return {
        'status': 'PASS',
        'path': str(path),
        'line_count': sum(domain_counts.values()),
        'malformed_count': malformed_count,
        'domain_counts': dict(domain_counts),
        'source_counts': dict(source_counts),
        'examples': examples,
    }


def extract_structured_news(rows_by_domain, source_time, candidate_names=None):
    candidate_names = candidate_names or {}
    records = []
    for row in rows_for_domains(rows_by_domain, ('announcements', 'risk_alerts', 'research_reports', 'earnings_preview')):
        code = code_from_row(row)
        text = evidence_text(row)
        if not code or not text or text.startswith('NO_RECENT_'):
            continue
        event_date = event_date_from_row(row, text)
        name = row_name(row, candidate_names.get(code, ''))
        event_type = event_type_for_text(text)
        quality = classify_news_catalyst_quality(text, source_time, event_date, row)
        text_id = hashlib.sha1(text[:120].encode('utf-8')).hexdigest()[:12]
        record_id = f'news:{code}:{event_date.date().isoformat() if event_date else source_time[:10]}:{text_id}'
        entities = [{'type': 'stock', 'symbol': code, 'name': name}]
        for tag in tag_tokens(text):
            if tag != name:
                entities.append({'type': 'keyword', 'name': tag})
        records.append({
            'id': record_id,
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': name,
            'title': text[:200],
            'source': row.get('source', 'eastmoney'),
            'publish_time': event_date.date().isoformat() if event_date else None,
            'entities': entities[:8],
            'event_type': event_type,
            'sentiment': sentiment_for_text(text),
            'confidence': 0.7 if event_type != 'unknown' else 0.35,
            'raw_ref': {'domain': row.get('domain'), 'source': row.get('source')},
            'news_catalyst_quality': quality,
        })
    return records


def limitup_reason_category(text):
    if any(keyword in text for keyword in ('板块', '概念', '行业')) or sector_tags_from_text(text):
        return 'sector_driven'
    if any(keyword in text for keyword in ('公告', '中标', '订单', '业绩', '重组')):
        return 'news_driven'
    if any(keyword in text for keyword in ('资金', '主力', '净流入')):
        return 'fund_driven'
    if any(keyword in text for keyword in ('龙虎榜', '营业部', '买入')):
        return 'lhb_driven'
    return 'unknown'


def extract_limitup_reason_structures(rows_by_domain, source_time, candidate_names=None):
    candidate_names = candidate_names or {}
    records = []
    for row in rows_for_domains(rows_by_domain, ('limitup_strength', 'consecutive_limit_strength', 'yesterday_limit_strength', 'candidate_quote_recheck')):
        code = code_from_row(row)
        text = evidence_text(row)
        if not code or not text or not any(keyword in text for keyword in ('涨停', '封板', '连板', '涨停原因')):
            continue
        tags = tag_tokens(text)
        records.append({
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': row_name(row, candidate_names.get(code, '')),
            'reason_text': text[:300],
            'reason_tags': tags,
            'reason_category': limitup_reason_category(text),
            'related_sectors': sector_tags_from_text(text, tags),
            'related_news_ids': [],
            'confidence': 0.65,
            'raw_ref': {'domain': row.get('domain'), 'source': row.get('source')},
        })
    return records


def lhb_side(row, text):
    side_text = str(row.get('side') or row.get('BUY_SELL_TYPE') or row.get('DIRECTION') or '') + text
    if any(keyword in side_text for keyword in ('卖出', 'SELL', '净卖')):
        return 'sell'
    if any(keyword in side_text for keyword in ('买入', 'BUY', '净买')):
        return 'buy'
    return 'unknown'


def extract_lhb_seat_profiles(rows_by_domain, source_time, candidate_names=None):
    candidate_names = candidate_names or {}
    records = []
    for row in rows_for_domains(rows_by_domain, ('lhb', 'candidate_lhb_recheck')):
        code = code_from_row(row)
        text = evidence_text(row)
        if not code or text.startswith('NO_RECENT_'):
            continue
        cells = row.get('cells') if isinstance(row.get('cells'), list) else []
        seat_name = row.get('OPERATEDEPT_NAME') or row.get('EXPLANATION') or row.get('营业部名称') or row.get('席位名称') or ' '.join(str(cell) for cell in cells[:2]).strip()
        if not seat_name and '龙虎榜' not in text:
            continue
        buy_amount = num(row.get('BUY_AMT') or row.get('买入金额') or row.get('买入额'))
        sell_amount = num(row.get('SELL_AMT') or row.get('卖出金额') or row.get('卖出额'))
        net_amount = num(row.get('NET_AMT') or row.get('BILLBOARD_NET_AMT') or row.get('净买额'), buy_amount - sell_amount)
        seat_type = 'institution' if any(keyword in str(seat_name) + text for keyword in ('机构专用', '沪股通', '深股通')) else ('hot_money' if '营业部' in str(seat_name) else 'unknown')
        records.append({
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': row_name(row, candidate_names.get(code, '')),
            'seat_name': str(seat_name)[:120] if seat_name else '',
            'broker_name': str(row.get('BROKER_NAME') or row.get('券商') or '')[:80],
            'side': lhb_side(row, text),
            'amount': max(buy_amount, sell_amount, abs(net_amount)),
            'net_amount': net_amount,
            'rank': int(num(row.get('rank') or row.get('RANK') or row.get('序号'), 0)),
            'seat_type': seat_type,
            'historical_profile': {'appear_count': 1, 'win_rate_1d': None, 'avg_net_amount': net_amount},
            'raw_ref': {'domain': row.get('domain'), 'source': row.get('source')},
        })
    return records


def extract_sector_propagation_edges(rows_by_domain, source_time, quotes=None):
    edges = []
    seen_sector_market = set()
    seen_sector_stock = set()
    name_to_code = {}
    if quotes:
        for q in quotes:
            name = q.get('name') or ''
            code = q.get('code') or ''
            if name and code and len(name) >= 2:
                name_to_code[name] = code

    for row in rows_for_domains(rows_by_domain, ('concept_industry', 'industry_board', 'sector_fund_flow')):
        code = code_from_row(row)
        text = evidence_text(row)
        cells = row.get('cells') if isinstance(row.get('cells'), list) else []
        sector = row.get('BOARD_NAME') or row.get('CONCEPT_NAME') or row.get('INDUSTRY_NAME') or row.get('title') or ''
        if not sector and len(cells) > 1:
            sector = str(cells[1]).strip()
        if not sector:
            sector = text[:40]
        if not sector:
            continue
        sector = str(sector).strip()[:60]
        if code:
            edge_key = (sector, code)
            if edge_key not in seen_sector_stock:
                seen_sector_stock.add(edge_key)
                edges.append({
                    'ts': structured_ts(source_time),
                    'from_type': 'sector',
                    'from_id': sector,
                    'to_type': 'stock',
                    'to_id': code,
                    'edge_type': 'belongs_to',
                    'weight': 0.6,
                    'evidence_refs': [row.get('source', 'eastmoney')],
                })
        else:
            related_text = str(row.get('相关', '')) + ' ' + text
            for name, resolved_code in name_to_code.items():
                if len(name) >= 3 and name in related_text:
                    edge_key = (sector, resolved_code)
                    if edge_key not in seen_sector_stock:
                        seen_sector_stock.add(edge_key)
                        edges.append({
                            'ts': structured_ts(source_time),
                            'from_type': 'sector',
                            'from_id': sector,
                            'to_type': 'stock',
                            'to_id': resolved_code,
                            'edge_type': 'belongs_to',
                            'weight': 0.4,
                            'evidence_refs': [row.get('source', 'eastmoney')],
                        })
            edge_key = (sector, 'concept_industry' if row.get('domain') == 'concept_industry' else 'other')
            if edge_key not in seen_sector_market:
                seen_sector_market.add(edge_key)
                edges.append({
                    'ts': structured_ts(source_time),
                    'from_type': 'sector',
                    'from_id': sector,
                    'to_type': 'market',
                    'to_id': 'a_share_all_market',
                    'edge_type': 'leads',
                    'weight': 0.4,
                    'evidence_refs': [row.get('source', 'eastmoney')],
                })
    return edges


def extract_metric_timeseries(rows_by_domain, candidates, quotes, source_time):
    candidate_names = {c.get('code'): c.get('name', '') for c in candidates}
    quote_by_code = {q.get('code'): q for q in quotes}
    popularity, fund_flow, seal_order = [], [], []
    popularity_aliases = ('rank', '排名', '人气排名', '股吧排名', '热度排名', 'POPULARITY_RANK', 'HOT_RANK', 'RANK')
    for row in rows_for_domains(rows_by_domain, ('popularity_heat',)):
        code = code_from_row(row)
        text = evidence_text(row) or str(row.get('raw_text') or '')
        raw_value, basis = first_present(row, popularity_aliases)
        value = num(raw_value, None)
        confidence = 0.75
        if value is None:
            value = parse_rank_from_text(text)
            basis = 'text_rank' if value is not None else basis
            confidence = 0.65
        if value is None and code and row.get('row_index') not in (None, ''):
            value = num(row.get('row_index'), None)
            basis = 'table_row_position'
            confidence = 0.45
        if code and value is not None:
            popularity.append({'ts': structured_ts(source_time), 'symbol': code, 'name': row_name(row, candidate_names.get(code, '')), 'metric': 'popularity_rank', 'value': value, 'window': 'snapshot', 'source': row.get('source', 'eastmoney'), 'rank_basis': basis, 'raw_value': raw_value, 'source_domain': row.get('domain'), 'confidence': confidence, 'raw_ref': {'domain': row.get('domain')}})
    for code, quote in quote_by_code.items():
        value = quote.get('net_inflow_main')
        if value not in (None, ''):
            fund_flow.append({'ts': structured_ts(source_time), 'symbol': code, 'name': quote.get('name', ''), 'metric': 'fund_flow_main_net', 'value': num(value), 'window': 'snapshot', 'source': quote.get('fund_flow_source') or quote.get('source', 'eastmoney'), 'raw_ref': {'domain': 'quote_fund_merge'}})
    for row in rows_for_domains(rows_by_domain, ('sector_fund_flow', 'candidate_fund_recheck')):
        code = code_from_row(row)
        value = num(row.get('主力净流入') or row.get('net_inflow_main') or row.get('MAIN_NET_INFLOW'), None)
        if code and value is not None:
            fund_flow.append({'ts': structured_ts(source_time), 'symbol': code, 'name': row_name(row, candidate_names.get(code, '')), 'metric': 'fund_flow_main_net', 'value': value, 'window': 'snapshot', 'source': row.get('source', 'eastmoney'), 'raw_ref': {'domain': row.get('domain')}})
    seal_aliases = ('封单额', '封单金额', '封单资金', '封板资金', '封板金额', '封板额', 'ORDER_AMT', 'SEAL_AMOUNT', 'FB_AMOUNT', '封单量', '封板量', '封成比', '封流比', '封单比')
    amount_keys = ('封单额', '封单金额', '封单资金', '封板资金', '封板金额', '封板额', 'ORDER_AMT', 'SEAL_AMOUNT', 'FB_AMOUNT')
    volume_keys = ('封单量', '封板量')
    ratio_keys = ('封成比', '封流比', '封单比')
    for row in rows_for_domains(rows_by_domain, ('limitup_strength', 'consecutive_limit_strength', 'yesterday_limit_strength', 'candidate_quote_recheck')):
        code = code_from_row(row)
        text = evidence_text(row)
        raw_value, basis = first_present(row, seal_aliases)
        value = num(raw_value, None)
        key_text = str(basis or '')
        value_unit = 'ratio' if basis in ratio_keys else ('volume' if basis in volume_keys else 'amount')
        confidence = 0.75 if value is not None else 0.0
        if value is None:
            value, parsed_key = parse_keyword_number(text, amount_keys)
            if value is not None:
                basis = 'text_amount:' + parsed_key
                value_unit = 'amount'
                confidence = 0.65
        if value is None:
            value, parsed_key = parse_keyword_number(text, volume_keys)
            if value is not None:
                basis = 'text_volume:' + parsed_key
                value_unit = 'volume'
                confidence = 0.6
        if value is None:
            value, parsed_key = parse_keyword_number(text, ratio_keys)
            if value is not None:
                basis = 'text_ratio:' + parsed_key
                value_unit = 'ratio'
                confidence = 0.6
        if value is None and any(keyword in text for keyword in ('封板强', '封单强', '强封', '封板坚决')):
            value = 1.0
            basis = 'qualitative_keyword'
            value_unit = 'qualitative_score'
            confidence = 0.35
        if code and value is not None:
            seal_order.append({'ts': structured_ts(source_time), 'symbol': code, 'name': row_name(row, candidate_names.get(code, '')), 'metric': 'seal_order_strength', 'value': value, 'value_unit': value_unit, 'window': 'snapshot', 'source': row.get('source', 'eastmoney'), 'extraction_basis': basis or key_text, 'confidence': confidence, 'raw_ref': {'domain': row.get('domain')}})
    return popularity, fund_flow, seal_order


def extract_hsgt_signals(data_directory_content_rows, source_time):
    hsgt_signals = []
    hsgt_item_keys = ('hsgt_capital_flow', 'hsgt_holdings', 'hsgt_turnover')
    for row in data_directory_content_rows:
        item_key = str(row.get('item_key') or '')
        if item_key not in hsgt_item_keys:
            continue
        cells = row.get('cells') or []
        header = row.get('header') or []
        text = str(row.get('raw_text') or row.get('summary') or '')
        code = code_from_row(row)
        if not code and len(cells) >= 2:
            code = normalize_code(cells[1])
        signal_type = 'unknown'
        signal_value = 0.0
        if code and header and any(h in str(header) for h in ('代码', '股票代码', 'SECURITY_CODE')):
            signal_type = 'northbound_holding'
            signal_value = 0.4
        elif any(kw in text for kw in ('净流入', '净买', '成交净买额', '北向', '陆股通', '港股通')):
            signal_type = 'northbound_inflow'
            for cell in cells:
                cell_str = str(cell).replace(',', '')
                multiplier = 1
                if '亿' in cell_str:
                    multiplier = 100000000
                    cell_str = cell_str.replace('亿', '')
                elif '万' in cell_str:
                    multiplier = 10000
                    cell_str = cell_str.replace('万', '')
                cell_str = cell_str.replace('元', '').strip()
                val = num(cell_str, None)
                if val is not None and val > 0:
                    val *= multiplier
                    signal_value = max(signal_value, min(1.0, val / 100000000.0))
                    break
        elif any(kw in text for kw in ('增持', '加仓', '买入')):
            signal_type = 'northbound_accumulation'
            signal_value = 0.6
        elif any(kw in text for kw in ('减持', '减仓', '卖出')):
            signal_type = 'northbound_reduction'
            signal_value = -0.4
        if signal_type != 'unknown':
            hsgt_signals.append({
                'ts': structured_ts(source_time),
                'symbol': code or 'market_overall',
                'metric': f'hsgt_{signal_type}',
                'value': signal_value,
                'window': 'snapshot',
                'source': 'eastmoney_data_directory_hsgt',
                'item_key': item_key,
                'confidence': 0.55,
            })
    return hsgt_signals


def extract_experimental_signals(evidence_by_domain, source_time):
    experimental_signals = []
    signal_mapping = {
        'block_trades': ('block_trade_signal', ('大宗交易', '溢价', '折价')),
        'lockup_expiry': ('lockup_risk_signal', ('解禁', '限售', '流通')),
        'shareholder_changes': ('shareholder_signal', ('增持', '减持', '回购', '股东')),
        'research_reports': ('research_signal', ('买入', '增持', '目标价', '评级')),
        'earnings_preview': ('earnings_signal', ('预增', '预减', '预亏', '扭亏')),
    }
    for domain, (metric_name, keywords) in signal_mapping.items():
        rows = evidence_by_domain.get(domain, []) or []
        for row in rows:
            text = str(row.get('text', '') or row.get('raw_text', '') or '')
            if not text:
                continue
            code = code_from_row(row)
            signal_value = 0.0
            if any(kw in text for kw in ('增持', '买入', '预增', '扭亏', '溢价')):
                signal_value = 0.5
            elif any(kw in text for kw in ('减持', '卖出', '预减', '预亏', '折价')):
                signal_value = -0.3
            elif any(kw in text for kw in keywords):
                signal_value = 0.2
            if signal_value != 0.0:
                experimental_signals.append({
                    'ts': structured_ts(source_time),
                    'symbol': code or 'market_overall',
                    'metric': metric_name,
                    'value': signal_value,
                    'window': 'snapshot',
                    'source': f'eastmoney_{domain}',
                    'confidence': 0.45,
                })
    stock_report_rows = evidence_by_domain.get('stock_reports', []) or []
    rating_scores = {'买入': 0.8, '增持': 0.6, '推荐': 0.6, '优于大市': 0.5, '跑赢行业': 0.5}
    for row in stock_report_rows:
        cells = row.get('cells') or []
        if len(cells) < 6:
            continue
        code = normalize_code(cells[1])
        if not code:
            continue
        rating = str(cells[5]).strip()
        signal_value = 0.0
        for key, score in rating_scores.items():
            if key in rating:
                signal_value = score
                break
        if not signal_value:
            if any(kw in rating for kw in ('买入', '增持', '推荐', '优于大市', '跑赢行业')):
                signal_value = 0.4
            elif any(kw in rating for kw in ('减持', '卖出', '减持', '弱于大市')):
                signal_value = -0.3
        if signal_value != 0.0:
            experimental_signals.append({
                'ts': structured_ts(source_time),
                'symbol': code,
                'metric': 'stock_report_rating',
                'value': signal_value,
                'window': 'snapshot',
                'source': 'eastmoney_stock_reports',
                'confidence': 0.6,
            })
    return experimental_signals


def extract_order_book_snapshots(rows_by_domain, source_time, candidate_names=None):
    candidate_names = candidate_names or {}
    snapshots = []
    bid_price_aliases = (('买一价', '买1价', '买一', 'BID1_PRICE', 'bid1_price', 'bid_price_1', 'f31'), ('买二价', '买2价', '买二', 'BID2_PRICE', 'bid2_price', 'bid_price_2', 'f35'), ('买三价', '买3价', '买三', 'BID3_PRICE', 'bid3_price', 'bid_price_3', 'f37'), ('买四价', '买4价', '买四', 'BID4_PRICE', 'bid4_price', 'bid_price_4', 'f39'), ('买五价', '买5价', '买五', 'BID5_PRICE', 'bid5_price', 'bid_price_5'))
    bid_volume_aliases = (('买一量', '买1量', 'BID1_VOLUME', 'bid1_volume', 'bid_volume_1', 'f32'), ('买二量', '买2量', 'BID2_VOLUME', 'bid2_volume', 'bid_volume_2', 'f36'), ('买三量', '买3量', 'BID3_VOLUME', 'bid3_volume', 'bid_volume_3', 'f38'), ('买四量', '买4量', 'BID4_VOLUME', 'bid4_volume', 'bid_volume_4', 'f40'), ('买五量', '买5量', 'BID5_VOLUME', 'bid5_volume', 'bid_volume_5'))
    ask_price_aliases = (('卖一价', '卖1价', '卖一', 'ASK1_PRICE', 'ask1_price', 'ask_price_1', 'f33'), ('卖二价', '卖2价', '卖二', 'ASK2_PRICE', 'ask2_price', 'ask_price_2'), ('卖三价', '卖3价', '卖三', 'ASK3_PRICE', 'ask3_price', 'ask_price_3'), ('卖四价', '卖4价', '卖四', 'ASK4_PRICE', 'ask4_price', 'ask_price_4'), ('卖五价', '卖5价', '卖五', 'ASK5_PRICE', 'ask5_price', 'ask_price_5'))
    ask_volume_aliases = (('卖一量', '卖1量', 'ASK1_VOLUME', 'ask1_volume', 'ask_volume_1', 'f34'), ('卖二量', '卖2量', 'ASK2_VOLUME', 'ask2_volume', 'ask_volume_2'), ('卖三量', '卖3量', 'ASK3_VOLUME', 'ask3_volume', 'ask_volume_3'), ('卖四量', '卖4量', 'ASK4_VOLUME', 'ask4_volume', 'ask_volume_4'), ('卖五量', '卖5量', 'ASK5_VOLUME', 'ask5_volume', 'ask_volume_5'))
    seen_symbols = set()
    for row in rows_for_domains(rows_by_domain, ('candidate_quote_recheck',)):
        code = code_from_row(row)
        text = evidence_text(row)
        if not code:
            continue
        bid_levels, ask_levels, basis = [], [], []
        weibie = None
        weicha = None
        neipan = None
        waipan = None
        for idx, aliases in enumerate(bid_price_aliases, start=1):
            price_raw, price_key = first_present(row, aliases)
            price = num(price_raw, None)
            if price is None:
                continue
            volume_raw, volume_key = first_present(row, bid_volume_aliases[idx - 1])
            bid_levels.append({'price': price, 'volume': num(volume_raw, 0)})
            basis.append(price_key or volume_key or f'bid{idx}')
        for idx, aliases in enumerate(ask_price_aliases, start=1):
            price_raw, price_key = first_present(row, aliases)
            price = num(price_raw, None)
            if price is None:
                continue
            volume_raw, volume_key = first_present(row, ask_volume_aliases[idx - 1])
            ask_levels.append({'price': price, 'volume': num(volume_raw, 0)})
            basis.append(price_key or volume_key or f'ask{idx}')
        if not bid_levels and not ask_levels:
            continue
        if code in seen_symbols:
            continue
        seen_symbols.add(code)
        buy_pressure = sum(level['volume'] for level in bid_levels)
        sell_pressure = sum(level['volume'] for level in ask_levels)
        denominator = buy_pressure + sell_pressure
        bid_price = bid_levels[0]['price'] if bid_levels else None
        ask_price = ask_levels[0]['price'] if ask_levels else None
        snapshot = {
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': row_name(row, candidate_names.get(code, '')),
            'bid_levels': bid_levels,
            'ask_levels': ask_levels,
            'buy_pressure': buy_pressure,
            'sell_pressure': sell_pressure,
            'pressure_imbalance': safe_ratio(buy_pressure - sell_pressure, denominator),
            'spread': round(ask_price - bid_price, 4) if bid_price is not None and ask_price is not None else None,
            'extraction_basis': ','.join(str(item) for item in basis if item),
            'confidence': 0.8 if any(keyword in text for keyword in ('买一', '卖一', '盘口', '五档')) else 0.6,
            'raw_ref': {'domain': row.get('domain'), 'source': row.get('source')},
        }
        if weibie is not None:
            snapshot['weibie'] = weibie
        if weicha is not None:
            snapshot['weicha'] = weicha
        if neipan is not None:
            snapshot['neipan'] = neipan
        if waipan is not None:
            snapshot['waipan'] = waipan
        snapshots.append(snapshot)
    return snapshots


def extract_replay_structures(rows_by_domain, source_time, candidate_names=None):
    candidate_names = candidate_names or {}
    records = []
    for row in rows_for_domains(rows_by_domain, ('candidate_intraday_replay',)):
        code = code_from_row(row)
        text = str(row.get('raw_text') or evidence_text(row) or '')
        if not code or not text or text.startswith('NO_CANDIDATE_'):
            continue
        source = str(row.get('source') or '')
        record = {
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': row_name(row, candidate_names.get(code, '')),
            'source': source,
            'page_title': str(row.get('page_title') or ''),
            'page_url': str(row.get('page_url') or ''),
            'raw_text': text[:500],
            'industry_rank': num(parse_keyword_number(text, ('行业排名', '排名'))[0], None),
            'main_force_net_inflow': num(parse_keyword_number(text, ('主力净流入',))[0], None),
            'main_force_net_ratio': num(parse_keyword_number(text, ('主力净占比', '主力净流入占比'))[0], None),
            'super_large_net_inflow': num(parse_keyword_number(text, ('超大单净流入',))[0], None),
            'large_net_inflow': num(parse_keyword_number(text, ('大单净流入',))[0], None),
            'mid_net_inflow': num(parse_keyword_number(text, ('中单净流入',))[0], None),
            'small_net_inflow': num(parse_keyword_number(text, ('小单净流入',))[0], None),
            'has_history_flow': any(token in text for token in ('历史资金流向', '盘后资金流向趋势', '盘后资金流向', '资金流向统计')),
            'has_industry_rank': '行业排名' in text,
            'has_stock_profile': any(token in text for token in ('个股概况', '深度数据', '公司亮点')),
            'raw_ref': {'domain': row.get('domain'), 'source': source},
        }
        if 'zjlx/' in record['page_url']:
            record['replay_type'] = 'capital_flow_replay'
        elif 'stockdata/' in record['page_url']:
            record['replay_type'] = 'stockdata_replay'
        else:
            record['replay_type'] = 'intraday_trade_replay'
        records.append(record)
    return records


def latest_rows_by_symbol_metric(rows, current_dt):
    latest = {}
    for row in rows:
        row_dt = parse_structured_dt(row.get('ts'))
        if current_dt and row_dt and row_dt >= current_dt:
            continue
        code = row.get('symbol')
        metric = row.get('metric') or row.get('_metric')
        if not code or not metric:
            continue
        value = num(row.get('value'), None)
        if value is None:
            continue
        key = (code, metric)
        old_dt, _old = latest.get(key, (None, None))
        if old_dt is None or (row_dt and row_dt > old_dt):
            latest[key] = (row_dt, row)
    return {key: row for key, (_row_dt, row) in latest.items()}


def extract_metric_delta_timeseries(structured_bundle, prior_rows, source_time):
    current_dt = parse_structured_dt(structured_ts(source_time))
    current_rows = []
    current_rows.extend(structured_bundle.get('popularity_ts', []))
    current_rows.extend(structured_bundle.get('fund_flow_ts', []))
    current_rows.extend(structured_bundle.get('seal_order_ts', []))
    for row in structured_bundle.get('order_books', []):
        current_rows.append({**row, 'metric': 'order_book_pressure_imbalance', 'value': row.get('pressure_imbalance'), '_metric': 'order_book_pressure_imbalance'})
    prior_latest = latest_rows_by_symbol_metric(prior_rows, current_dt)
    deltas = []
    for row in current_rows:
        code = row.get('symbol')
        metric = row.get('metric') or row.get('_metric')
        value = num(row.get('value'), None)
        prior = prior_latest.get((code, metric))
        prior_value = num(prior.get('value'), None) if prior else None
        if not code or not metric or value is None or prior_value is None:
            continue
        delta = value - prior_value
        deltas.append({
            'ts': structured_ts(source_time),
            'symbol': code,
            'name': row.get('name', prior.get('name', '') if prior else ''),
            'metric': metric + '_delta',
            'value': round(delta, 4),
            'previous_value': prior_value,
            'current_value': value,
            'previous_ts': prior.get('ts'),
            'window': 'same_day_existing_scan_delta',
            'source': 'eastmoney_existing_scan_artifacts',
            'source_metric': metric,
            'value_unit': row.get('value_unit'),
            'confidence': round(min(num(row.get('confidence'), 0.5), num(prior.get('confidence'), 0.5)), 4),
            'raw_ref': {'domain': 'existing_structured_timeseries'},
        })
    return deltas


def load_prior_metric_rows(output_dir, source_time):
    date_root = BASE / 'data' / 'live_scan' / source_time[:10]
    if not date_root.exists():
        return []
    output_dir = output_dir.resolve()
    rows = []
    for name in ('popularity_rank_ts.jsonl', 'fund_flow_ts.jsonl', 'seal_order_strength_ts.jsonl', 'order_book_snapshots.jsonl'):
        for path in date_root.glob('**/' + name):
            try:
                if path.resolve().parent == output_dir:
                    continue
            except FileNotFoundError:
                continue
            loaded = load_jsonl_rows(path)
            if name == 'order_book_snapshots.jsonl':
                rows.extend({**row, 'metric': 'order_book_pressure_imbalance', 'value': row.get('pressure_imbalance'), '_metric': 'order_book_pressure_imbalance'} for row in loaded)
            else:
                rows.extend(loaded)
    return rows


def build_relationship_graph(news, limitup_reasons, lhb_profiles, sector_edges):
    nodes = {}
    edges = {}
    stock_themes = {}
    def add_node(node_id, node_type, label, item=None):
        node = nodes.setdefault(node_id, {'id': node_id, 'type': node_type, 'label': label, 'source_domains': [], 'evidence_count': 0, 'first_ts': None, 'last_ts': None})
        domain = ((item or {}).get('raw_ref') or {}).get('domain')
        if domain and domain not in node['source_domains']:
            node['source_domains'].append(domain)
        ts = (item or {}).get('ts')
        if ts:
            node['first_ts'] = min([value for value in (node['first_ts'], ts) if value])
            node['last_ts'] = max([value for value in (node['last_ts'], ts) if value])
        node['evidence_count'] += 1
    def add_edge(source, target, edge_type, weight, item=None, evidence_refs=None, confidence=None):
        key = (source, target, edge_type)
        domain = ((item or {}).get('raw_ref') or {}).get('domain')
        ts = (item or {}).get('ts')
        old = edges.get(key)
        if old:
            old['weight'] = round(max(old['weight'], weight), 4)
            old['evidence_count'] += 1
            if domain and domain not in old['source_domains']:
                old['source_domains'].append(domain)
            for ref in evidence_refs or []:
                if ref and ref not in old['evidence_refs']:
                    old['evidence_refs'].append(ref)
            if confidence is not None:
                old['confidence'] = round(max(old.get('confidence', 0), confidence), 4)
            if ts:
                old['last_ts'] = max([value for value in (old.get('last_ts'), ts) if value])
        else:
            edges[key] = {'source': source, 'target': target, 'type': edge_type, 'weight': round(weight, 4), 'evidence_refs': [ref for ref in (evidence_refs or []) if ref], 'source_domains': [domain] if domain else [], 'evidence_count': 1, 'confidence': round(confidence if confidence is not None else weight, 4), 'last_ts': ts}
    for item in news:
        stock_id = 'stock:' + item['symbol']
        add_node(stock_id, 'stock', item.get('name') or item['symbol'], item)
        stock_themes.setdefault(item['symbol'], set())
        for entity in item.get('entities', []):
            if entity.get('type') == 'keyword':
                keyword_id = 'keyword:' + entity['name']
                add_node(keyword_id, 'keyword', entity['name'], item)
                add_edge(keyword_id, stock_id, 'news_mentions', 0.35, item, [item.get('id')], item.get('confidence'))
                stock_themes[item['symbol']].add(entity['name'])
    reason_tags_by_symbol = {}
    for item in limitup_reasons:
        stock_id = 'stock:' + item['symbol']
        add_node(stock_id, 'stock', item.get('name') or item['symbol'], item)
        stock_themes.setdefault(item['symbol'], set())
        reason_tags_by_symbol.setdefault(item['symbol'], set())
        for sector in item.get('related_sectors', [])[:5]:
            sector_id = 'sector:' + sector
            add_node(sector_id, 'sector', sector, item)
            add_edge(sector_id, stock_id, 'sector_limitup_reason', 0.6, item, [sector], item.get('confidence'))
            stock_themes[item['symbol']].add(sector)
        for tag in item.get('reason_tags', [])[:5]:
            tag_id = 'reason:' + tag
            add_node(tag_id, 'reason', tag, item)
            add_edge(tag_id, stock_id, 'limitup_reason', 0.55, item, [tag], item.get('confidence'))
            stock_themes[item['symbol']].add(tag)
            reason_tags_by_symbol[item['symbol']].add(tag)
        news_tags = stock_themes.get(item['symbol'], set())
        for tag in reason_tags_by_symbol.get(item['symbol'], set()).intersection(news_tags):
            add_edge('keyword:' + tag, 'reason:' + tag, 'news_supports_limitup_reason', 0.5, item, [tag], item.get('confidence'))
    for item in lhb_profiles:
        stock_id = 'stock:' + item['symbol']
        seat_id = 'seat:' + item.get('seat_name', '')
        if item.get('seat_name'):
            add_node(stock_id, 'stock', item.get('name') or item['symbol'], item)
            add_node(seat_id, 'seat', item['seat_name'], item)
            add_edge(seat_id, stock_id, 'lhb_' + item.get('side', 'unknown'), 0.5, item, [item.get('seat_name')], 0.55)
    for edge in sector_edges:
        source = f"{edge['from_type']}:{edge['from_id']}"
        target = f"{edge['to_type']}:{edge['to_id']}"
        add_node(source, edge['from_type'], edge['from_id'], edge)
        add_node(target, edge['to_type'], edge['to_id'], edge)
        add_edge(source, target, edge['edge_type'], edge.get('weight', 0.0), edge, edge.get('evidence_refs'), edge.get('weight', 0.0))
        if edge.get('from_type') == 'sector' and edge.get('to_type') == 'stock':
            stock_themes.setdefault(edge.get('to_id'), set()).add(edge.get('from_id'))
    co_theme_count = 0
    symbols = sorted(stock_themes)
    for left_idx, left in enumerate(symbols):
        for right in symbols[left_idx + 1:]:
            shared = sorted(stock_themes[left].intersection(stock_themes[right]))
            if not shared:
                continue
            add_edge('stock:' + left, 'stock:' + right, 'co_theme', min(0.8, 0.2 + len(shared) * 0.1), None, shared[:5], 0.45)
            co_theme_count += 1
            if co_theme_count >= 30:
                break
        if co_theme_count >= 30:
            break
    if not nodes and not edges:
        return {'nodes': [], 'edges': []}
    return {'nodes': list(nodes.values()), 'edges': list(edges.values()), 'metadata': {'node_count': len(nodes), 'edge_count': len(edges), 'mode': 'active_scoring_support'}}


def build_sector_opportunity_snapshot(limitup_reasons, rows_by_domain=None):
    sector_counts = Counter()
    sector_symbols = defaultdict(set)
    sector_fund_flow_amount = defaultdict(float)
    for row in limitup_reasons:
        symbol = row.get('symbol')
        for sector in row.get('related_sectors', []):
            sector_counts[sector] += 1
            if symbol:
                sector_symbols[sector].add(symbol)
    rows_by_domain = rows_by_domain or {}
    for row in rows_for_domains(rows_by_domain, ('sector_fund_flow', 'industry_board', 'concept_industry')):
        cells = row.get('cells') if isinstance(row.get('cells'), list) else []
        sector = str(row.get('BOARD_NAME') or row.get('CONCEPT_NAME') or row.get('INDUSTRY_NAME') or row.get('title') or '').strip()
        if not sector and len(cells) > 1:
            sector = str(cells[1]).strip()
        if not sector:
            continue
        text = evidence_text(row)
        weight = 1
        if any(k in text for k in ('净流入', '领涨', '主线', '热点')):
            weight = 2
        sector_counts[sector] += weight
        symbol = code_from_row(row)
        if symbol:
            sector_symbols[sector].add(symbol)
    for row in rows_for_domains(rows_by_domain, ('concept_capital_flow',)):
        cells = row.get('cells') if isinstance(row.get('cells'), list) else []
        sector = str(row.get('板块名称') or row.get('CONCEPT_NAME') or row.get('BOARD_NAME') or '').strip()
        if not sector and len(cells) > 0:
            sector = str(cells[0]).strip()
        if not sector:
            continue
        fund_flow_str = str(row.get('主力净流入-净额') or row.get('主力净流入') or row.get('MAIN_NET_INFLOW') or '0')
        fund_flow = 0.0
        try:
            fund_flow = float(fund_flow_str.replace(',', '').replace('亿', '').replace('万', ''))
            if '亿' in fund_flow_str:
                fund_flow *= 100000000
            elif '万' in fund_flow_str:
                fund_flow *= 10000
        except (ValueError, TypeError):
            fund_flow = 0.0
        sector_fund_flow_amount[sector] += fund_flow
        if fund_flow > 0:
            sector_counts[sector] += 3
        symbol = code_from_row(row)
        if symbol:
            sector_symbols[sector].add(symbol)
    result = []
    for sector, count in sector_counts.most_common(30):
        entry = {
            'sector': sector,
            'evidence_count': count,
            'symbols': sorted(sector_symbols[sector])[:10],
        }
        if sector in sector_fund_flow_amount:
            entry['fund_flow_amount'] = round(sector_fund_flow_amount[sector], 2)
        result.append(entry)
    return result


def build_catalyst_index(rows_by_domain, source_time, candidate_names=None, quotes=None):
    candidate_names = candidate_names or {}
    news = extract_structured_news(rows_by_domain, source_time, candidate_names)
    limitup_reasons = extract_limitup_reason_structures(rows_by_domain, source_time, candidate_names)
    sector_edges = extract_sector_propagation_edges(rows_by_domain, source_time, quotes)
    sector_opportunity_snapshot = build_sector_opportunity_snapshot(limitup_reasons, rows_by_domain)

    news_by_symbol = defaultdict(list)
    news_keywords_by_symbol = defaultdict(set)
    news_quality_by_symbol = defaultdict(list)
    news_sector_tags_by_symbol = defaultdict(set)
    for item in news:
        symbol = item.get('symbol')
        if not symbol:
            continue
        news_by_symbol[symbol].append(item)
        quality = item.get('news_catalyst_quality') or {}
        news_quality_by_symbol[symbol].append(quality)
        for entity in item.get('entities', []):
            if entity.get('type') == 'keyword' and entity.get('name'):
                news_keywords_by_symbol[symbol].add(str(entity['name']))
        if quality.get('usable_for_candidate_generation') and quality.get('category') in ('positive_catalyst', 'sector_catalyst'):
            for sector in quality.get('sector_terms') or []:
                sector = str(sector).strip()
                if sector:
                    news_sector_tags_by_symbol[symbol].add(sector)

    limitup_reasons_by_symbol = defaultdict(list)
    sector_tags_by_symbol = defaultdict(set)
    for item in limitup_reasons:
        symbol = item.get('symbol')
        if not symbol:
            continue
        limitup_reasons_by_symbol[symbol].append(item)
        for sector in item.get('related_sectors', []) or []:
            if sector:
                sector_tags_by_symbol[symbol].add(str(sector))

    for edge in sector_edges:
        if edge.get('from_type') == 'sector' and edge.get('to_type') == 'stock':
            sector = str(edge.get('from_id') or '').strip()
            symbol = str(edge.get('to_id') or '').strip()
            if sector and symbol:
                sector_tags_by_symbol[symbol].add(sector)
    for symbol, sectors in news_sector_tags_by_symbol.items():
        sector_tags_by_symbol[symbol].update(sectors)

    sector_strength_by_tag = {}
    for row in sector_opportunity_snapshot:
        sector = str(row.get('sector') or '').strip()
        if not sector:
            continue
        evidence_score = min(1.0, component_score(num(row.get('evidence_count'), 0), 3))
        fund_flow_amount = num(row.get('fund_flow_amount'), 0.0)
        if fund_flow_amount > 0:
            fund_flow_score = min(1.0, fund_flow_amount / 1000000000.0)
            sector_strength_by_tag[sector] = min(1.0, evidence_score * 0.4 + fund_flow_score * 0.6)
        else:
            sector_strength_by_tag[sector] = evidence_score

    return {
        'news': news,
        'limitup_reasons': limitup_reasons,
        'sector_edges': sector_edges,
        'sector_opportunity_snapshot': sector_opportunity_snapshot,
        'news_by_symbol': {symbol: rows for symbol, rows in news_by_symbol.items()},
        'news_quality_by_symbol': {symbol: rows for symbol, rows in news_quality_by_symbol.items()},
        'news_keywords_by_symbol': {symbol: sorted(values) for symbol, values in news_keywords_by_symbol.items()},
        'news_sector_tags_by_symbol': {symbol: sorted(values) for symbol, values in news_sector_tags_by_symbol.items()},
        'limitup_reasons_by_symbol': {symbol: rows for symbol, rows in limitup_reasons_by_symbol.items()},
        'sector_tags_by_symbol': {symbol: sorted(values) for symbol, values in sector_tags_by_symbol.items()},
        'sector_strength_by_tag': sector_strength_by_tag,
    }


def build_sector_catalyst_diagnostics(catalyst_index, candidates, pool_counts):
    catalyst_index = catalyst_index or {}
    candidate_rows = candidates or []
    news_rows = catalyst_index.get('news') or []
    sector_news_rows = [
        row for row in news_rows
        if (row.get('news_catalyst_quality') or {}).get('category') == 'sector_catalyst'
    ]
    sector_opportunity_snapshot = catalyst_index.get('sector_opportunity_snapshot') or []
    sector_tags_by_symbol = catalyst_index.get('sector_tags_by_symbol') or {}

    sector_to_symbol_mapping = defaultdict(set)
    for symbol, tags in sector_tags_by_symbol.items():
        for tag in tags or []:
            if tag:
                sector_to_symbol_mapping[str(tag)].add(str(symbol))

    low_position_symbols_by_sector = defaultdict(list)
    low_position_candidates = 0
    for row in candidate_rows:
        symbol = str(row.get('code') or row.get('symbol') or '')
        if not symbol:
            continue
        stage = row.get('candidate_stage') or signal_stage_bucket(row.get('signal_pct'))
        if stage not in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
            continue
        search_layer_hint = str(row.get('search_layer_hint') or '')
        setup_type = str(row.get('setup_type') or '')
        if search_layer_hint != 'sector_catalyst_low_position' and setup_type not in ('SECTOR_NEWS_LOW_POSITION', 'TOPIC_FUND_IGNITION'):
            continue
        sectors = normalize_string_tags(row.get('sector_opportunity_tags') or [])
        if not sectors:
            sectors = normalize_string_tags(sector_tags_by_symbol.get(symbol) or [])
        if not sectors:
            continue
        low_position_candidates += 1
        for sector in sectors:
            if symbol not in low_position_symbols_by_sector[sector]:
                low_position_symbols_by_sector[sector].append(symbol)

    why_zero = []
    sector_news_count = len(sector_news_rows)
    sector_opportunity_snapshot_count = len(sector_opportunity_snapshot)
    sector_to_symbol_mapping_count = sum(len(symbols) for symbols in sector_to_symbol_mapping.values())
    sector_pool_count = int(num(pool_counts.get('SECTOR_NEWS_LOW_POSITION'), 0))
    if sector_news_count == 0:
        why_zero.append('sector_news_count=0')
    if sector_opportunity_snapshot_count == 0:
        why_zero.append('sector_opportunity_snapshot_count=0')
    if sector_to_symbol_mapping_count == 0:
        why_zero.append('sector_to_symbol_mapping_count=0')
    if low_position_candidates == 0:
        if sector_pool_count == 0:
            why_zero.append('sector_pool_count=0')
        if sector_news_count > 0 or sector_opportunity_snapshot_count > 0:
            why_zero.append('low_position_stage_or_flow_threshold_too_strict')
        if sector_news_count > 0 and sector_to_symbol_mapping_count > 0:
            why_zero.append('sector_news_not_mapped_to_low_position_symbols')
    return {
        'sector_news_count': sector_news_count,
        'sector_opportunity_snapshot_count': sector_opportunity_snapshot_count,
        'sector_to_symbol_mapping_count': sector_to_symbol_mapping_count,
        'low_position_symbols_by_sector': dict(sorted((sector, symbols) for sector, symbols in low_position_symbols_by_sector.items())),
        'why_sector_catalyst_low_position_zero': why_zero,
        'sector_pool_count': sector_pool_count,
        'sector_news_category_counts': dict(Counter(
            (row.get('news_catalyst_quality') or {}).get('category', 'unknown')
            for row in sector_news_rows
        )),
    }


def build_information_coverage_audit(source_status_map, candidates, structured_outputs=None):
    structured_outputs = structured_outputs or {}

    def domain_status(*domains):
        record_count = 0
        tab_count = 0
        saw_partial = False
        for domain in domains:
            status = source_status_map.get(domain, {}) if isinstance(source_status_map, dict) else {}
            record_count += int(num(status.get('record_count'), 0))
            tab_count += int(num(status.get('tab_count'), 0))
            saw_partial = saw_partial or status.get('status') == 'PARTIAL'
            if status.get('status') == 'PASS':
                saw_partial = True
        if record_count > 0:
            return 'PASS' if saw_partial or tab_count > 0 else 'PARTIAL'
        if tab_count > 0:
            return 'PARTIAL'
        return 'MISSING'

    candidate_layers = set()
    candidate_setup_types = set()
    for row in candidates or []:
        candidate_layers.update(str(layer) for layer in (row.get('source_layers') or []) if layer)
        hint = str(row.get('search_layer_hint') or '')
        if hint:
            candidate_layers.add(hint)
        setup_type = str(row.get('setup_type') or '')
        if setup_type:
            candidate_setup_types.add(setup_type)

    candidate_generation_uses_news = bool(candidate_layers.intersection({'news_catalyst_low_position', 'L5_NEWS_CATALYST'})) or bool(candidate_setup_types.intersection({'NEWS_CATALYST_LOW_POSITION', 'TOPIC_FUND_IGNITION'}))
    candidate_generation_uses_sector_news = bool(candidate_layers.intersection({'sector_catalyst_low_position', 'L6_SECTOR_CATALYST', 'L8_LIMITUP_REASON_PROPAGATION'})) or bool(candidate_setup_types.intersection({'SECTOR_NEWS_LOW_POSITION', 'TOPIC_FUND_IGNITION'}))
    candidate_generation_uses_intraday_alerts = bool(candidate_layers.intersection({'intraday_alert_reversal', 'L7_INTRADAY_ALERT'})) or bool(candidate_setup_types.intersection({'INTRADAY_ALERT_REVERSAL'}))

    news_sources = {
        'eastmoney_news': domain_status('announcements', 'risk_alerts', 'research_reports', 'earnings_preview'),
        'announcements': domain_status('announcements'),
        'limitup_reasons': domain_status('limitup_strength', 'consecutive_limit_strength', 'yesterday_limit_strength'),
        'sector_news': domain_status('concept_industry', 'industry_board', 'sector_fund_flow'),
        'intraday_alerts': 'PASS' if (
            domain_status('candidate_quote_recheck', 'candidate_fund_recheck', 'popularity_heat') != 'MISSING'
            or (structured_outputs.get('seal_order_strength_ts') or {}).get('rows')
            or (structured_outputs.get('order_book_snapshots') or {}).get('rows')
            or (structured_outputs.get('metric_delta_ts') or {}).get('rows')
        ) else 'MISSING',
        'popularity_rank': domain_status('popularity_heat'),
        'sector_fund_flow': domain_status('sector_fund_flow'),
    }

    # 主力/游资视角覆盖审计 (NN2: 缺失不可静默)
    main_force_sources = {
        'concept_capital_flow': domain_status('concept_capital_flow'),
        'block_trades': domain_status('block_trades'),
        'shareholder_changes': domain_status('shareholder_changes'),
        'lockup_expiry': domain_status('lockup_expiry'),
        'ipo_calendar': domain_status('ipo_calendar'),
        'trading_halts': domain_status('trading_halts'),
    }

    all_coverage = {**news_sources, **main_force_sources}

    coverage_gaps = [
        f'{name}:{status}'
        for name, status in all_coverage.items()
        if status != 'PASS'
    ]
    sources_now_used_for_candidate_generation = []
    if candidate_generation_uses_news:
        sources_now_used_for_candidate_generation.extend(['eastmoney_news', 'announcements'])
    if candidate_generation_uses_sector_news:
        sources_now_used_for_candidate_generation.extend(['sector_news', 'limitup_reasons', 'sector_fund_flow'])
    if candidate_generation_uses_intraday_alerts:
        sources_now_used_for_candidate_generation.extend(['intraday_alerts', 'popularity_rank'])

    sources_used_only_as_evidence = [
        name for name, status in news_sources.items()
        if status == 'PASS' and name not in sources_now_used_for_candidate_generation
    ]

    return {
        'news_sources': news_sources,
        'main_force_sources': main_force_sources,
        'coverage_gaps': coverage_gaps,
        'sources_used_only_as_evidence': sources_used_only_as_evidence,
        'sources_now_used_for_candidate_generation': sorted(set(sources_now_used_for_candidate_generation)),
        'candidate_generation_uses_news': candidate_generation_uses_news,
        'candidate_generation_uses_sector_news': candidate_generation_uses_sector_news,
        'candidate_generation_uses_intraday_alerts': candidate_generation_uses_intraday_alerts,
    }


def _sector_strength_for_tag(tag, sector_strength):
    if not tag:
        return 0.0
    best = sector_strength.get(tag, 0.0)
    for sector, strength in sector_strength.items():
        if sector and (sector in tag or tag in sector):
            best = max(best, strength)
    return best


def build_structured_bundle(rows_by_domain, evidence_pack, candidates, quotes, source_time, catalyst_index=None):
    candidate_names = {candidate.get('code'): candidate.get('name', '') for candidate in candidates}
    catalyst_index = catalyst_index or build_catalyst_index(rows_by_domain, source_time, candidate_names)
    news = catalyst_index.get('news') or extract_structured_news(rows_by_domain, source_time, candidate_names)
    limitup_reasons = catalyst_index.get('limitup_reasons') or extract_limitup_reason_structures(rows_by_domain, source_time, candidate_names)
    lhb_profiles = extract_lhb_seat_profiles(rows_by_domain, source_time, candidate_names)
    sector_edges = catalyst_index.get('sector_edges') or extract_sector_propagation_edges(rows_by_domain, source_time)
    popularity_ts, fund_flow_ts, seal_order_ts = extract_metric_timeseries(rows_by_domain, candidates, quotes, source_time)
    order_books = extract_order_book_snapshots(rows_by_domain, source_time, candidate_names)
    replay_structures = extract_replay_structures(rows_by_domain, source_time, candidate_names)
    graph = build_relationship_graph(news, limitup_reasons, lhb_profiles, sector_edges)
    data_directory_rows = rows_by_domain.get('data_directory_content', [])
    hsgt_signals = extract_hsgt_signals(data_directory_rows, source_time)
    experimental_signals = extract_experimental_signals(rows_by_domain, source_time)
    return {
        'news': news,
        'limitup_reasons': limitup_reasons,
        'lhb_profiles': lhb_profiles,
        'sector_edges': sector_edges,
        'popularity_ts': popularity_ts,
        'fund_flow_ts': fund_flow_ts,
        'seal_order_ts': seal_order_ts,
        'order_books': order_books,
        'replay_structures': replay_structures,
        'relationship_graph': graph,
        'evidence_symbol_count': len(evidence_pack),
        'sector_opportunity_snapshot': catalyst_index.get('sector_opportunity_snapshot') or build_sector_opportunity_snapshot(limitup_reasons),
        'hsgt_signals': hsgt_signals,
        'experimental_signals': experimental_signals,
    }


def component_score(count, cap):
    return round(min(1.0, count / cap), 4) if cap else 0.0


def limitup_capture_profile_for_signal(
    signal_pct,
    close_position_score,
    net_inflow_main,
    fund_flow_momentum,
    pre_limitup_anomaly,
    weak_to_strong_reversal,
    limitup_reason_propagation_score,
):
    signal_pct = num(signal_pct, None)
    close_position_score = num(close_position_score, 0.0) or 0.0
    net_inflow_main = num(net_inflow_main, 0.0) or 0.0
    fund_flow_momentum = num(fund_flow_momentum, 0.0) or 0.0
    pre_limitup_anomaly = num(pre_limitup_anomaly, 0.0) or 0.0
    weak_to_strong_reversal = num(weak_to_strong_reversal, 0.0) or 0.0
    limitup_reason_propagation_score = num(limitup_reason_propagation_score, 0.0) or 0.0
    reasons = []
    if signal_pct is None or not (5.0 <= signal_pct < 9.5):
        return {
            'limitup_capture_score': 0.0,
            'limitup_capture_profile': 'NONE',
            'limitup_capture_confirmed': False,
            'limitup_capture_reasons': ['signal_pct_outside_5_to_9_5'],
        }
    flow_score = 1.0 if net_inflow_main > 0 else (0.5 if fund_flow_momentum > 0 else 0.0)
    score = (
        pre_limitup_anomaly * 0.30
        + weak_to_strong_reversal * 0.25
        + limitup_reason_propagation_score * 0.20
        + close_position_score * 0.15
        + flow_score * 0.10
    )
    if pre_limitup_anomaly >= 0.70:
        reasons.append('pre_limitup_anomaly>=0.70')
    if weak_to_strong_reversal >= 0.75:
        reasons.append('weak_to_strong_reversal>=0.75')
    if limitup_reason_propagation_score >= 0.60:
        reasons.append('limitup_reason_propagation_score>=0.60')
    if close_position_score >= 0.70:
        reasons.append('close_position_score>=0.70')
    if flow_score > 0:
        reasons.append('positive_flow_evidence')
    strong = score >= 0.62 and close_position_score >= 0.70 and flow_score > 0 and (
        pre_limitup_anomaly >= 0.55 or weak_to_strong_reversal >= 0.65 or limitup_reason_propagation_score >= 0.80
    )
    medium = score >= 0.50 and close_position_score >= 0.65 and len(reasons) >= 2
    profile = 'STRONG_LIMITUP_CAPTURE' if strong else ('MEDIUM_LIMITUP_CAPTURE' if medium else 'NONE')
    return {
        'limitup_capture_score': round(max(0.0, min(1.0, score)), 4),
        'limitup_capture_profile': profile,
        'limitup_capture_confirmed': profile == 'STRONG_LIMITUP_CAPTURE',
        'limitup_capture_reasons': reasons,
    }


def normalize_string_tags(values):
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    normalized = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if not clean or clean in seen:
            continue
        normalized.append(clean)
        seen.add(clean)
    return normalized


def normalize_vei_phase_d_tags(tags):
    return normalize_string_tags(tags)


def inferred_vei_phase_d_tags(details):
    if not isinstance(details, dict):
        details = {}
    inferred = []
    if num(details.get('pre_limitup_anomaly'), 0.0) > 0:
        inferred.append('PRE_LIMITUP_ANOMALY')
    if num(details.get('weak_to_strong_reversal'), 0.0) > 0:
        inferred.append('WEAK_TO_STRONG_REVERSAL')
    if num(details.get('first_board_pre_signal'), 0.0) > 0:
        inferred.append('FIRST_BOARD_PRE_SIGNAL')
    if num(details.get('sector_opportunity_score'), 0.0) > 0:
        inferred.append('SECTOR_OPPORTUNITY')
    return inferred


VEI_COMPONENT_KEYS = ('pre_limitup_anomaly', 'weak_to_strong_reversal', 'first_board_pre_signal', 'sector_opportunity_score')


def freeze_vei(obj):
    cd = obj.get('component_details') or {}
    if not isinstance(cd, dict):
        cd = {}
    frozen_details = dict(cd)
    for key in VEI_COMPONENT_KEYS:
        frozen_details[key] = num(cd.get(key), 0.0)
    obj['component_details'] = frozen_details

    tags = normalize_vei_phase_d_tags(obj.get('vei_phase_d_tags'))
    inferred_tags = inferred_vei_phase_d_tags(frozen_details)
    obj['vei_phase_d_tags'] = normalize_vei_phase_d_tags(tags + inferred_tags)
    return obj


def vei_checksum(obj):
    cd = obj['component_details']
    payload = {key: cd[key] for key in VEI_COMPONENT_KEYS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def attach_checksum(obj):
    obj['vei_checksum'] = vei_checksum(obj)
    return obj


def build_structured_scores(scored_rows, structured_bundle):
    grouped = {}
    for key in ('news', 'limitup_reasons', 'lhb_profiles', 'popularity_ts', 'fund_flow_ts', 'seal_order_ts', 'order_books', 'metric_delta_ts'):
        for row in structured_bundle.get(key, []):
            code = row.get('symbol')
            if code:
                grouped.setdefault(code, {}).setdefault(key, []).append(row)
    sector_counts = Counter(edge.get('to_id') for edge in structured_bundle.get('sector_edges', []) if edge.get('to_type') == 'stock')
    sector_strength = {}
    for row in structured_bundle.get('sector_opportunity_snapshot', []):
        sector = row.get('sector')
        if not sector:
            continue
        evidence_score = min(1.0, component_score(num(row.get('evidence_count'), 0), 3))
        fund_flow_amount = num(row.get('fund_flow_amount'), 0.0)
        if fund_flow_amount > 0:
            fund_flow_score = min(1.0, fund_flow_amount / 1000000000.0)
            sector_strength[sector] = min(1.0, evidence_score * 0.4 + fund_flow_score * 0.6)
        else:
            sector_strength[sector] = evidence_score
    sector_tags_by_symbol = {}
    for row in structured_bundle.get('limitup_reasons', []):
        symbol = row.get('symbol')
        if symbol:
            sector_tags_by_symbol.setdefault(symbol, set()).update(row.get('related_sectors') or [])
    for edge in structured_bundle.get('sector_edges', []):
        if edge.get('from_type') == 'sector' and edge.get('to_type') == 'stock':
            sector_tags_by_symbol.setdefault(edge.get('to_id'), set()).add(edge.get('from_id'))
    concept_sector_names = set()
    for row in structured_bundle.get('sector_opportunity_snapshot', []):
        s = str(row.get('sector') or '').strip()
        if s:
            concept_sector_names.add(s)
    candidate_name_by_code = {}
    for scored in scored_rows:
        c = str(scored.get('code') or '').strip()
        n = str(scored.get('name') or '').strip()
        if c and n:
            candidate_name_by_code[c] = n
    code_by_name = {n: c for c, n in candidate_name_by_code.items()}
    for sector_row in structured_bundle.get('sector_opportunity_snapshot', []):
        sector = str(sector_row.get('sector') or '').strip()
        if not sector:
            continue
        for sym in sector_row.get('symbols') or []:
            if sym:
                sector_tags_by_symbol.setdefault(sym, set()).add(sector)
    for code, name in candidate_name_by_code.items():
        for sector in concept_sector_names:
            if sector in name or name in sector:
                sector_tags_by_symbol.setdefault(code, set()).add(sector)
    graph_degrees = Counter()
    for edge in structured_bundle.get('relationship_graph', {}).get('edges', []):
        for endpoint in (edge.get('source'), edge.get('target')):
            if isinstance(endpoint, str) and endpoint.startswith('stock:'):
                graph_degrees[endpoint.split(':', 1)[1]] += 1
    replay_structures_by_symbol = {}
    for replay in structured_bundle.get('replay_structures', []):
        symbol = str(replay.get('symbol') or '').strip()
        if symbol:
            replay_structures_by_symbol.setdefault(symbol, []).append(replay)
    rows = []
    for scored in scored_rows:
        code = scored.get('code')
        if not code:
            continue
        bucket = grouped.get(code, {})
        news_rows = bucket.get('news', [])
        limitup_rows = bucket.get('limitup_reasons', [])
        lhb_rows = bucket.get('lhb_profiles', [])
        popularity_rows = bucket.get('popularity_ts', [])
        fund_rows = bucket.get('fund_flow_ts', [])
        seal_rows = bucket.get('seal_order_ts', [])
        order_rows = bucket.get('order_books', [])
        delta_rows = bucket.get('metric_delta_ts', [])
        replay_rows = replay_structures_by_symbol.get(code, [])
        news_confidence = max([num(row.get('confidence'), 0) for row in news_rows] or [0])
        positive_news = 1 if any(row.get('sentiment') == 'positive' for row in news_rows) else 0
        category_weight = {'sector_driven': 1.0, 'news_driven': 0.9, 'fund_driven': 0.85, 'lhb_driven': 0.8, 'unknown': 0.35}
        limitup_strength = max([category_weight.get(row.get('reason_category'), 0.35) for row in limitup_rows] or [0])
        lhb_net = sum(max(0.0, num(row.get('net_amount'), 0)) for row in lhb_rows)
        lhb_seat_bonus = 0.25 if any(row.get('seat_type') in ('institution', 'hot_money') for row in lhb_rows) else 0.0
        popularity_rank = min([num(row.get('value'), 9999) for row in popularity_rows] or [9999])
        popularity_score = 0.0 if popularity_rank == 9999 else max(0.0, min(1.0, (101 - popularity_rank) / 100))
        fund_inflow = sum(max(0.0, num(row.get('value'), 0)) for row in fund_rows)
        seal_strength = 0.0
        for row in seal_rows:
            value = max(0.0, num(row.get('value'), 0))
            confidence = num(row.get('confidence'), 0.5)
            unit = row.get('value_unit')
            if unit == 'ratio':
                seal_strength = max(seal_strength, min(1.0, value / 10) * confidence)
            elif unit == 'qualitative_score':
                seal_strength = max(seal_strength, min(1.0, value) * confidence)
            else:
                seal_strength = max(seal_strength, min(1.0, value / 50000000) * confidence)
        order_pressure = 0.0
        for row in order_rows:
            imbalance = num(row.get('pressure_imbalance'), 0)
            order_pressure = max(order_pressure, max(0.0, imbalance) * num(row.get('confidence'), 0.5))
        time_series_momentum = 0.0
        positive_delta_count = 0
        for row in delta_rows:
            metric = row.get('source_metric') or ''
            delta = num(row.get('value'), 0)
            confidence = num(row.get('confidence'), 0.5)
            if metric == 'popularity_rank':
                contribution = max(0.0, -delta) / 20
            elif metric == 'fund_flow_main_net':
                contribution = max(0.0, delta) / 50000000
            elif metric == 'seal_order_strength':
                unit = row.get('value_unit')
                contribution = max(0.0, delta) if unit == 'qualitative_score' else max(0.0, delta) / (10 if unit == 'ratio' else 50000000)
            elif metric == 'order_book_pressure_imbalance':
                contribution = max(0.0, delta)
            else:
                contribution = 0.0
            if contribution > 0:
                positive_delta_count += 1
            time_series_momentum = max(time_series_momentum, min(1.0, contribution) * confidence)
        signal_pct = num(scored.get('signal_pct'), None)
        close_position_score = num(scored.get('close_position_score'), None)
        volume_ratio = num(scored.get('volume_ratio'), 0)
        fund_pctile = num(scored.get('full_universe_fund_pctile'), 0)
        source_layers = set(scored.get('source_layers') or [])
        search_layer_hint = str(scored.get('search_layer_hint') or '')
        news_catalyst_strength = num(scored.get('news_catalyst_strength'), 0.0)
        sector_catalyst_score = num(scored.get('sector_catalyst_score'), 0.0)
        topic_propagation_score = num(scored.get('topic_propagation_score'), 0.0)
        intraday_alert_strength = num(scored.get('intraday_alert_strength'), 0.0)
        limitup_reason_propagation_score = num(scored.get('limitup_reason_propagation_score'), 0.0)
        low_position_catalyst_score = num(scored.get('low_position_catalyst_score'), 0.0)
        sector_tags = sorted(tag for tag in sector_tags_by_symbol.get(code, set()) if tag)
        sector_snapshot = structured_bundle.get('sector_opportunity_snapshot', [])
        fallback_sector = ''
        if not sector_tags and sector_snapshot:
            fallback_sector = str((sector_snapshot[0] or {}).get('sector') or '').strip()
        if (
            not sector_tags
            and fallback_sector
            and source_layers.intersection({'L4_UNDERWATER_RECOVERY', 'L4_PRE_BREAKOUT'})
            and (close_position_score or 0.0) >= 0.55
            and volume_ratio >= 1.2
            and fund_pctile >= 0.5
        ):
            sector_tags = [fallback_sector]
        sector_opportunity_score = max([_sector_strength_for_tag(tag, sector_strength) for tag in sector_tags] or [0.0])
        for tag in sector_tags:
            for snapshot_row in structured_bundle.get('sector_opportunity_snapshot', []):
                if str(snapshot_row.get('sector') or '').strip() == tag:
                    fund_flow_amount = num(snapshot_row.get('fund_flow_amount'), 0.0)
                    if fund_flow_amount > 0:
                        fund_flow_bonus = min(0.3, fund_flow_amount / 500000000.0 * 0.3)
                        sector_opportunity_score = min(1.0, sector_opportunity_score + fund_flow_bonus)
                    break
        sector_snapshot_top = structured_bundle.get('sector_opportunity_snapshot', [])[:5]
        sector_snapshot_top_names = [str(row.get('sector') or '').strip() for row in sector_snapshot_top if str(row.get('sector') or '').strip()]
        main_theme_alignment_score = 0.0
        main_theme_core_score = 0.0
        if sector_tags and sector_snapshot_top_names:
            overlap = [tag for tag in sector_tags if tag in sector_snapshot_top_names]
            if overlap:
                main_theme_alignment_score = min(1.0, 0.4 + 0.2 * len(overlap) + 0.2 * sector_opportunity_score)
                core_points = 0.0
                if (close_position_score or 0.0) >= 0.82:
                    core_points += 0.3
                if volume_ratio >= 2.0:
                    core_points += 0.25
                if fund_pctile >= 0.8:
                    core_points += 0.25
                if (signal_pct or 0.0) >= 4.0:
                    core_points += 0.2
                main_theme_core_score = min(1.0, main_theme_alignment_score * 0.5 + core_points)
        pre_limitup_anomaly = 0.0
        if signal_pct is not None and 5.0 <= signal_pct < 9.5 and (close_position_score or 0.0) >= 0.70:
            pre_limitup_anomaly = min(1.0, (signal_pct - 5.0) / 4.5 * 0.55 + (close_position_score or 0.0) * 0.25 + fund_pctile * 0.20)
        weak_to_strong_reversal = 0.0
        if 'L4_UNDERWATER_RECOVERY' in source_layers:
            weak_to_strong_reversal = min(1.0, component_score(volume_ratio, 3.0) * 0.35 + fund_pctile * 0.35 + (close_position_score or 0.0) * 0.30)
        first_board_pre_signal = max(pre_limitup_anomaly, weak_to_strong_reversal * 0.85)
        candidate_stage = signal_stage_bucket(signal_pct)
        vei_phase_d_tags = []
        if pre_limitup_anomaly > 0:
            vei_phase_d_tags.append('PRE_LIMITUP_ANOMALY')
        if weak_to_strong_reversal > 0:
            vei_phase_d_tags.append('WEAK_TO_STRONG_REVERSAL')
        if 'L4_PRE_BREAKOUT' in source_layers:
            vei_phase_d_tags.append('FIRST_BOARD_PRE_SIGNAL')
        replay_main_force_net_inflow = max([num(row.get('main_force_net_inflow'), 0.0) for row in replay_rows] or [0.0])
        replay_main_force_net_ratio = max([num(row.get('main_force_net_ratio'), 0.0) for row in replay_rows] or [0.0])
        replay_industry_rank = min([num(row.get('industry_rank'), 9999.0) for row in replay_rows if row.get('industry_rank') not in (None, '')] or [9999.0])
        replay_has_history_flow = any(row.get('has_history_flow') for row in replay_rows)
        replay_has_stock_profile = any(row.get('has_stock_profile') for row in replay_rows)
        replay_has_industry_rank = any(row.get('has_industry_rank') for row in replay_rows)
        # Provenance only — never broadcast REPLAY_* into theme/sector tags.
        # Mixing REPLAY into sector_opportunity_tags polluted pool fingerprints and
        # also overwrote real-sector fallback assignment above.
        replay_provenance_tags = []
        if replay_has_industry_rank:
            replay_provenance_tags.append('REPLAY_INDUSTRY_RANK')
        if replay_has_history_flow:
            replay_provenance_tags.append('REPLAY_HISTORY_FLOW')
        if replay_has_stock_profile:
            replay_provenance_tags.append('REPLAY_STOCK_PROFILE')
        replay_fund_bonus = min(1.0, replay_main_force_net_inflow / 100000000) if replay_rows else 0.0
        replay_ratio_bonus = min(1.0, replay_main_force_net_ratio / 10.0) if replay_rows else 0.0
        replay_industry_bonus = 0.0 if replay_industry_rank == 9999.0 or not replay_rows else max(0.0, min(1.0, (100 - replay_industry_rank) / 100.0))
        hsgt_stock_rows = [r for r in structured_bundle.get('hsgt_signals', []) if r.get('symbol') == code]
        hsgt_market_rows = [r for r in structured_bundle.get('hsgt_signals', []) if r.get('symbol') == 'market_overall']
        hsgt_stock_holding = sum(max(0.0, num(r.get('value'), 0.0)) for r in hsgt_stock_rows if 'holding' in str(r.get('metric', '')))
        hsgt_market_inflow = sum(max(0.0, num(r.get('value'), 0.0)) for r in hsgt_market_rows if 'inflow' in str(r.get('metric', '')))
        hsgt_market_accumulation = sum(max(0.0, num(r.get('value'), 0.0)) for r in hsgt_market_rows if 'accumulation' in str(r.get('metric', '')))
        hsgt_inflow = hsgt_stock_holding + min(0.3, hsgt_market_inflow + hsgt_market_accumulation)
        hsgt_accumulation = 0.0
        experimental_stock_rows = [r for r in structured_bundle.get('experimental_signals', []) if r.get('symbol') == code]
        experimental_market_rows = [r for r in structured_bundle.get('experimental_signals', []) if r.get('symbol') == 'market_overall']
        experimental_positive = sum(max(0.0, num(r.get('value'), 0.0)) for r in experimental_stock_rows) + min(0.2, sum(max(0.0, num(r.get('value'), 0.0)) for r in experimental_market_rows))
        experimental_negative = sum(min(0.0, num(r.get('value'), 0.0)) for r in experimental_stock_rows)
        components = {
            'news_quality': round(min(1.0, component_score(len(news_rows), 3) * 0.65 + news_confidence * 0.25 + positive_news * 0.1), 4),
            'limitup_reason_strength': round(min(1.0, component_score(len(limitup_rows), 2) * 0.45 + limitup_strength * 0.55), 4),
            'lhb_seat_strength': round(min(1.0, component_score(len(lhb_rows), 5) * 0.45 + min(1.0, lhb_net / 50000000) * 0.3 + lhb_seat_bonus), 4),
            # candidate_intraday_replay is retained as provenance/audit data.
            # Research snapshots must not manufacture production sector strength.
            'sector_propagation': round(min(1.0, component_score(sector_counts.get(code, 0), 3) * 0.55 + component_score(graph_degrees.get(code, 0), 6) * 0.25), 4),
            'popularity_momentum': round(min(1.0, component_score(len(popularity_rows), 2) * 0.3 + popularity_score * 0.7), 4),
            # Direct candidate_fund_recheck / fund-flow snapshots are the only
            # production capital input. Replay flow/ratio remains diagnostic.
            'fund_flow_momentum': round(min(1.0, component_score(len(fund_rows), 2) * 0.20 + min(1.0, fund_inflow / 100000000) * 0.45), 4),
            'seal_order_strength': round(min(1.0, component_score(len(seal_rows), 2) * 0.35 + seal_strength * 0.65), 4),
            'order_book_pressure': round(min(1.0, component_score(len(order_rows), 1) * 0.3 + order_pressure * 0.7), 4),
            'time_series_momentum': round(min(1.0, component_score(len(delta_rows), 3) * 0.20 + time_series_momentum * 0.55), 4),
            'relationship_graph_centrality': component_score(graph_degrees.get(code, 0), 8),
            'low_position_catalyst_score': round(low_position_catalyst_score, 4),
            'main_theme_alignment_score': round(min(1.0, main_theme_alignment_score), 4),
            'main_theme_core_score': round(main_theme_core_score, 4),
            'hsgt_institutional_flow': round(min(1.0, hsgt_inflow + hsgt_accumulation), 4),
            'experimental_catalyst_signal': round(min(1.0, max(0.0, experimental_positive + experimental_negative)), 4),
        }
        early_opportunity_score = 0.0
        early_opportunity_score += {
            'underwater': 0.26,
            'flat_0_to_3': 0.24,
            'early_3_to_5': 0.20,
            'mid_5_to_7': 0.10,
            'high_7_to_9': -0.08,
            'near_limit_9_plus': -0.18,
        }.get(candidate_stage, 0.0)
        early_opportunity_score += min(0.30, sector_opportunity_score * 0.30)
        early_opportunity_score += min(0.12, main_theme_alignment_score * 0.12)
        early_opportunity_score += min(0.14, components['fund_flow_momentum'] * 0.14)
        early_opportunity_score += min(0.12, components['time_series_momentum'] * 0.12)
        early_opportunity_score += min(0.14, weak_to_strong_reversal * 0.14)
        early_opportunity_score += min(0.12, pre_limitup_anomaly * 0.12)
        early_opportunity_score += min(0.08, max(0.0, close_position_score or 0.0) * 0.08)
        early_opportunity_score += min(0.08, fund_pctile * 0.08)
        early_opportunity_score += min(0.10, component_score(volume_ratio, 3.0) * 0.10)
        early_opportunity_score += min(0.18, low_position_catalyst_score * 0.18)
        if signal_pct is not None and signal_pct >= 8.0:
            early_opportunity_score -= min(0.22, ((signal_pct - 8.0) / 2.0) * 0.16 + 0.05)
        if signal_pct is not None and signal_pct <= 0.0:
            early_opportunity_score += 0.04
        early_opportunity_score = max(0.0, min(1.0, early_opportunity_score))
        limitup_capture = limitup_capture_profile_for_signal(
            signal_pct,
            close_position_score,
            scored.get('net_inflow_main'),
            components['fund_flow_momentum'],
            pre_limitup_anomaly,
            weak_to_strong_reversal,
            limitup_reason_propagation_score,
        )
        evidence_counts = {key: len(bucket.get(key, [])) for key in ('news', 'limitup_reasons', 'lhb_profiles', 'popularity_ts', 'fund_flow_ts', 'seal_order_ts', 'order_books', 'metric_delta_ts')}
        component_details = {
            'popularity_best_rank': None if popularity_rank == 9999 else popularity_rank,
            'fund_inflow_positive': fund_inflow,
            'lhb_positive_net_amount': lhb_net,
            'graph_degree': graph_degrees.get(code, 0),
            'seal_order_rows': len(seal_rows),
            'order_book_rows': len(order_rows),
            'metric_delta_rows': len(delta_rows),
            'positive_metric_delta_count': positive_delta_count,
            'replay_rows': len(replay_rows),
            'replay_main_force_net_inflow': round(replay_main_force_net_inflow, 4),
            'replay_main_force_net_ratio': round(replay_main_force_net_ratio, 4),
            'replay_industry_rank': None if replay_industry_rank == 9999.0 else replay_industry_rank,
            'replay_has_history_flow': replay_has_history_flow,
            'replay_has_stock_profile': replay_has_stock_profile,
            'replay_has_industry_rank': replay_has_industry_rank,
            'replay_provenance_tags': list(replay_provenance_tags),
            'pre_limitup_anomaly': round(pre_limitup_anomaly, 4),
            'weak_to_strong_reversal': round(weak_to_strong_reversal, 4),
            'first_board_pre_signal': round(first_board_pre_signal, 4),
            'sector_opportunity_score': round(sector_opportunity_score, 4),
            'sector_opportunity_tags': sector_tags[:5],
            'main_theme_alignment_score': round(main_theme_alignment_score, 4),
            'main_theme_core_score': round(main_theme_core_score, 4),
            'main_theme_alignment_tags': sector_snapshot_top_names[:5],
            'candidate_stage': candidate_stage,
            'early_opportunity_score': round(early_opportunity_score, 4),
            'news_catalyst_strength': round(news_catalyst_strength, 4),
            'sector_catalyst_score': round(sector_catalyst_score, 4),
            'topic_propagation_score': round(topic_propagation_score, 4),
            'intraday_alert_strength': round(intraday_alert_strength, 4),
            'limitup_reason_propagation_score': round(limitup_reason_propagation_score, 4),
            'low_position_catalyst_score': round(low_position_catalyst_score, 4),
            'hsgt_institutional_flow': round(components.get('hsgt_institutional_flow', 0.0), 4),
            'experimental_catalyst_signal': round(components.get('experimental_catalyst_signal', 0.0), 4),
            'limitup_capture_score': limitup_capture['limitup_capture_score'],
            'limitup_capture_profile': limitup_capture['limitup_capture_profile'],
            'limitup_capture_confirmed': limitup_capture['limitup_capture_confirmed'],
            'limitup_capture_reasons': limitup_capture['limitup_capture_reasons'],
            'search_layer_hint': search_layer_hint,
        }
        structured_score = round(sum(components.values()) * 10, 4)
        base_score = scored.get('score')
        research_signals = scored.get('research_signals')
        if not isinstance(research_signals, dict):
            structured_source_time = f"{scored.get('signal_date') or ''} {scored.get('asof_time') or ''}".strip()
            research_evidence_rows = [*news_rows, *limitup_rows, *lhb_rows]
            research_signals = build_research_signals(scored, research_evidence_rows, structured_source_time, sector_snapshot)
        structured_scores_obj = {
            'symbol': code,
            'name': scored.get('name', ''),
            'base_score': base_score,
            'structured_score': structured_score,
            'components': components,
            'component_details': component_details,
            'structured_evidence_counts': evidence_counts,
            'vei_phase_d_tags': vei_phase_d_tags,
            'candidate_stage': candidate_stage,
            'early_opportunity_score': round(early_opportunity_score, 4),
            'limitup_capture_score': limitup_capture['limitup_capture_score'],
            'limitup_capture_profile': limitup_capture['limitup_capture_profile'],
            'limitup_capture_confirmed': limitup_capture['limitup_capture_confirmed'],
            'limitup_capture_reasons': limitup_capture['limitup_capture_reasons'],
            'research_signals': research_signals,
            'structured_score_version': 'structured_alpha_v0_4_active',
            'mode': 'active_scoring_support',
        }
        rows.append(attach_checksum(freeze_vei(structured_scores_obj)))
    return rows


def rows_symbol_count(rows):
    return len({row.get('symbol') for row in rows if row.get('symbol')})


def rows_metadata(path, rows):
    return {'path': str(path), 'rows': len(rows), 'symbols': rows_symbol_count(rows)}


def structured_rows_metadata(path, rows, empty_status='EMPTY', availability_note=''):
    metadata = rows_metadata(path, rows)
    metadata['status'] = 'PASS' if rows else empty_status
    if availability_note and not rows:
        metadata['availability_note'] = availability_note
    return metadata


def scan_governance_rows(scored):
    rows = []
    for row in scored:
        item = dict(row)
        item['symbol'] = item.get('symbol') or item.get('code')
        item['decision'] = 'PAPER_PICK' if item.get('score') is not None else 'NO_PICK'
        rows.append(item)
    return rows


def rows_with_evidence(rows):
    return sum(
        1 for row in rows
        if row.get('candidate_evidence_status') == 'PASS'
        or row.get('candidate_evidence_matched_domains')
        or row.get('enhanced_evidence_matched_domains')
        or row.get('experimental_evidence_matched_domains')
    )


def rows_from_table_payload(payload):
    rows = []
    for table_index, table in enumerate(payload.get('tables', [])):
        table_rows = table.get('rows') or []
        if not table_rows:
            continue
        header = table_rows[0]
        for row_index, cells in enumerate(table_rows[1:], start=1):
            row = {'cells': cells, 'table_index': table_index, 'row_index': row_index, 'header': header, 'raw_text': ' '.join(str(cell) for cell in cells)}
            for idx, header_name in enumerate(header):
                if idx < len(cells):
                    row[str(header_name).strip()] = cells[idx]
            rows.append(row)
    return rows


def table_rows_to_quotes(rows, source):
    quotes = []
    for row in rows:
        cells = row.get('cells') or []
        text = ' '.join(str(c) for c in cells)
        code = normalize_code(text)
        if not code or not is_a_share_code(code):
            continue
        name = row.get('名称') or row.get('股票名称') or row.get('name') or ''
        if not name:
            m = re.search(r'\d{6}\s+([一-龥A-Za-z0-9*]+)', text)
            name = m.group(1) if m else ''
        quote = normalize_quote({
            'code': code,
            'name': name,
            'price': row.get('最新价') or row.get('现价') or row.get('price'),
            'pct_chg': row.get('涨跌幅') or row.get('涨幅') or row.get('pct_chg'),
            'amount': row.get('成交额') or row.get('amount'),
            'turnover_rate': row.get('换手率') or row.get('turnover_rate'),
            'volume_ratio': row.get('量比') or row.get('volume_ratio'),
            'high': row.get('最高') or row.get('high'),
            'low': row.get('最低') or row.get('low'),
            'open': row.get('今开') or row.get('open'),
            'prev_close': row.get('昨收') or row.get('prev_close'),
        }, source)
        quotes.append(quote)
    return quotes


def should_bypass_proxy(url):
    host = urlparse(url).hostname or ''
    return host == 'localhost' or host == '::1' or host.startswith('127.') or host == 'eastmoney.com' or host.endswith('.eastmoney.com')


def http_json(url, method='GET'):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 XiaoguEastmoneyEvidence/0.1', 'Referer': 'https://data.eastmoney.com/'}, method=method)
    opener = DIRECT_OPENER if should_bypass_proxy(url) else None
    with (opener.open(req, timeout=5) if opener else urlopen(req, timeout=5)) as resp:
        text = resp.read().decode('utf-8')
    text = text.strip()
    if text and not text.startswith(('{', '[')):
        m = re.search(r'^[^(]+\((.*)\)\s*;?$', text, re.S)
        if m:
            text = m.group(1)
    return json.loads(text)


def exception_brief(exc):
    if isinstance(exc, HTTPError):
        return f'HTTPError(status={exc.code}, reason={exc.reason}, url={exc.url})'
    if isinstance(exc, URLError):
        return f'URLError(reason={exc.reason})'
    return f'{type(exc).__name__}({exc})'


def eastmoney_get(url, params):
    return http_json(url + '?' + urlencode(params))


def no_match_row(code, name, domain, source_time, label):
    return {
        'code': code,
        'SECURITY_CODE': code,
        'SECURITY_NAME_ABBR': name,
        'date': source_time[:10],
        'title': label,
        'domain': domain,
        'source': 'eastmoney_candidate_detail_no_match',
    }


def _chunk_list(items, chunk_count):
    items = list(items or [])
    if not items:
        return []
    chunk_count = max(1, int(chunk_count or 1))
    chunk_size = max(1, (len(items) + chunk_count - 1) // chunk_count)
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def intraday_replay_page_rows(code, name, source_time, snapshot, source_label, title_suffix):
    if not isinstance(snapshot, dict):
        return []
    rows = []
    for table in snapshot.get('tables', [])[:5]:
        for row in table.get('rows', [])[:80]:
            cells = row.get('cells') or []
            if not cells:
                continue
            raw_text = ' '.join(str(cell) for cell in cells if str(cell).strip())
            rows.append({
                'code': code,
                'SECURITY_CODE': code,
                'SECURITY_NAME_ABBR': name,
                'date': source_time[:10],
                'domain': 'candidate_intraday_replay',
                'source': source_label,
                'title': f'{name} {title_suffix}',
                'raw_text': raw_text,
                'cells': cells,
                'table_index': table.get('table_index'),
                'row_index': row.get('row_index'),
                'page_url': snapshot.get('url'),
                'page_title': snapshot.get('title'),
            })
    page_text = str(snapshot.get('text') or '')
    if page_text and len(page_text) > 200:
        has_key_data = any(kw in page_text for kw in ('主力净流入', '超大单', '行业排名', '个股概况', '深度数据', '历史资金流向', '盘后资金流向'))
        if has_key_data:
            rows.append({
                'code': code,
                'SECURITY_CODE': code,
                'SECURITY_NAME_ABBR': name,
                'date': source_time[:10],
                'domain': 'candidate_intraday_replay',
                'source': source_label,
                'title': f'{name} {title_suffix} 页面全文',
                'raw_text': page_text[:2000],
                'page_url': snapshot.get('url'),
                'page_title': snapshot.get('title'),
            })
    return rows


def intraday_replay_snapshot_has_content(snapshot):
    if not isinstance(snapshot, dict):
        return False
    for row in intraday_replay_page_rows('', '', '1970-01-01 00:00:00', snapshot, 'probe', 'probe'):
        text = str(row.get('raw_text') or '')
        if text and '-' not in text and '暂无' not in text:
            return True
    page_text = str(snapshot.get('text') or '')
    return any(token in page_text for token in ('主力净流入', '历史资金流向', '盘后资金流向', '行业排名', '个股概况', '深度数据'))


def market_prefix(code):
    return 'sh' if str(code).startswith('6') else 'sz'


def rows_from_announcement_api(candidate, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    rows_by_domain = {domain: [] for domain in EVIDENCE_DOMAINS}
    payload = eastmoney_get('https://np-anotice-stock.eastmoney.com/api/security/ann', {
        'ann_type': 'A',
        'client_source': 'web',
        'page_index': 1,
        'page_size': 10,
        'stock_list': code,
    })
    items = payload.get('data', {}).get('list') or []
    for item in items:
        columns = ','.join(col.get('column_name', '') for col in item.get('columns', []) if isinstance(col, dict))
        title = item.get('title') or ''
        notice_date = item.get('notice_date') or item.get('display_time') or source_time[:10]
        row = {
            'code': code,
            'SECURITY_CODE': code,
            'SECURITY_NAME_ABBR': name,
            'date': notice_date,
            'NOTICE_DATE': notice_date,
            'title': title,
            'NOTICE_TITLE': title,
            'columns': columns,
            'domain': 'announcements',
            'source': 'eastmoney_candidate_announcement_api',
        }
        rows_by_domain['announcements'].append(row)
        if any(keyword in (title + columns) for keyword in RISK_KEYWORDS):
            risk_row = dict(row)
            risk_row['domain'] = 'risk_alerts'
            risk_row['source'] = 'eastmoney_candidate_announcement_risk_api'
            rows_by_domain['risk_alerts'].append(risk_row)
    if not rows_by_domain['announcements']:
        rows_by_domain['announcements'].append(no_match_row(code, name, 'announcements', source_time, 'NO_RECENT_ANNOUNCEMENT_MATCH'))
    if not rows_by_domain['risk_alerts']:
        rows_by_domain['risk_alerts'].append(no_match_row(code, name, 'risk_alerts', source_time, 'NO_RECENT_RISK_ALERT_MATCH'))
    return rows_by_domain


def rows_from_lhb_api(candidate, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    rows = []
    try:
        payload = eastmoney_get('https://datacenter-web.eastmoney.com/api/data/v1/get', {
            'reportName': 'RPT_DAILYBILLBOARD_PROFILE',
            'columns': 'ALL',
            'filter': f'(SECURITY_CODE="{code}")',
            'pageNumber': 1,
            'pageSize': 10,
            'sortTypes': '-1',
            'sortColumns': 'TRADE_DATE',
            'source': 'WEB',
            'client': 'WEB',
        })
        rows = payload.get('result', {}).get('data') or []
    except Exception:
        rows = []
    if not rows:
        return [no_match_row(code, name, 'lhb', source_time, 'NO_RECENT_LHB_MATCH')]
    out = []
    for row in rows:
        row = dict(row)
        row.update({'code': code, 'date': row.get('TRADE_DATE') or source_time[:10], 'domain': 'lhb', 'source': 'eastmoney_candidate_lhb_api'})
        out.append(row)
    return out


def rows_from_financial_api(candidate, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    rows = []
    try:
        payload = eastmoney_get('https://datacenter-web.eastmoney.com/api/data/v1/get', {
            'reportName': 'RPT_LICO_FN_CPD',
            'columns': 'ALL',
            'filter': f'(SECURITY_CODE="{code}")',
            'pageNumber': 1,
            'pageSize': 3,
            'sortTypes': '-1',
            'sortColumns': 'REPORTDATE',
            'source': 'WEB',
            'client': 'WEB',
        })
        rows = payload.get('result', {}).get('data') or []
    except Exception:
        rows = []
    financial_rows = []
    concept_rows = []
    for row in rows:
        row = dict(row)
        row.update({'code': code, 'date': row.get('NOTICE_DATE') or row.get('REPORTDATE') or source_time[:10], 'domain': 'financials', 'source': 'eastmoney_candidate_financial_api'})
        financial_rows.append(row)
        board_name = row.get('BOARD_NAME') or row.get('PUBLISHNAME')
        if board_name:
            concept_rows.append({'code': code, 'SECURITY_CODE': code, 'SECURITY_NAME_ABBR': name, 'date': source_time[:10], 'title': str(board_name), 'BOARD_NAME': board_name, 'domain': 'concept_industry', 'source': 'eastmoney_candidate_financial_board_api'})
    if not financial_rows:
        financial_rows.append(no_match_row(code, name, 'financials', source_time, 'NO_RECENT_FINANCIAL_MATCH'))
    if not concept_rows:
        concept_rows.append(no_match_row(code, name, 'concept_industry', source_time, 'NO_CANDIDATE_INDUSTRY_MATCH'))
    return financial_rows, concept_rows


def rows_from_limitup_pool_api(source_time, page_size=100):
    try:
        payload = eastmoney_get('https://push2ex.eastmoney.com/getTopicZTPool', {
            'ut': '7eea3edcaed734bea9cbfc24409ed989',
            'dpt': 'wz.ztzt',
            'pageindex': 0,
            'pagesize': page_size,
            'sort': 'fbt:asc',
            'date': source_time[:10].replace('-', ''),
            '_': int(time.time() * 1000),
        })
    except Exception:
        return []
    rows = []
    for item in payload.get('data', {}).get('pool') or []:
        code = normalize_code(item.get('c'))
        if not code:
            continue
        zttj = item.get('zttj') if isinstance(item.get('zttj'), dict) else {}
        rows.append({
            'code': code,
            'SECURITY_CODE': code,
            'SECURITY_NAME_ABBR': item.get('n', ''),
            '名称': item.get('n', ''),
            'date': source_time[:10],
            'domain': 'limitup_strength',
            'source': 'eastmoney_limitup_pool_api',
            '封板资金': item.get('fund'),
            '封单额': item.get('fund'),
            '涨跌幅': item.get('zdp'),
            '成交额': item.get('amount'),
            '换手率': item.get('hs'),
            '连板数': item.get('lbc'),
            '炸板次数': item.get('zbc'),
            '涨停统计': json.dumps(zttj, ensure_ascii=False) if zttj else '',
            '所属行业': item.get('hybk'),
            'title': f"{item.get('n', '')} 涨停池 封板资金{item.get('fund', '')} 连板数{item.get('lbc', '')}",
        })
    return rows


def consecutive_rows_from_limitup_pool_rows(rows):
    consecutive_rows = []
    for row in rows:
        if row.get('连板数') not in (None, '', '-', 0, '0'):
            consecutive_row = dict(row)
            consecutive_row['domain'] = 'consecutive_limit_strength'
            consecutive_row['source'] = 'eastmoney_limitup_pool_api_lbc'
            consecutive_rows.append(consecutive_row)
    return consecutive_rows


def secid_for_code(code):
    text = normalize_code(code)
    board = board_for_code(text)
    if board == 'beijing':
        market = '2'
    elif board == 'star' or text.startswith('6'):
        market = '1'
    else:
        market = '0'
    return market + '.' + text


def fund_flow_secid_candidates_for_code(code):
    text = normalize_code(code)
    if not text:
        return []
    candidates = []
    if text.startswith('6'):
        candidates.append('1.' + text)
    elif text.startswith(('0', '3')):
        candidates.append('0.' + text)
    elif text.startswith(('8', '9')) or board_for_code(text) == 'beijing':
        candidates.append('0.' + text)
    else:
        candidates.append(secid_for_code(text))
    fallback = secid_for_code(text)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return candidates


def rows_from_candidate_fund_flow_api(candidate, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    seen_secids = set()
    last_error = ''
    fields = ','.join([
        'f12', 'f13', 'f14', 'f2', 'f3', 'f62', 'f184',
        'f66', 'f69', 'f72', 'f75', 'f78', 'f81', 'f84', 'f87', 'f124',
    ])
    for secid in fund_flow_secid_candidates_for_code(code):
        if not secid or secid in seen_secids:
            continue
        seen_secids.add(secid)
        try:
            payload = eastmoney_get('https://push2.eastmoney.com/api/qt/ulist.np/get', {
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
                'fltt': '2',
                'invt': '2',
                'fields': fields,
                'secids': secid,
                '_': int(time.time() * 1000),
            })
        except Exception as exc:
            last_error = str(exc)
            continue
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            last_error = 'NO_DATA'
            continue
        diff = data.get('diff')
        if not isinstance(diff, list) or not diff:
            last_error = 'NO_DIFF'
            continue
        row_data = diff[0] if isinstance(diff[0], dict) else {}
        fund_value = row_data.get('f62')
        if fund_value in (None, ''):
            last_error = 'NO_F62'
            continue
        row = {
            'code': normalize_code(row_data.get('f12')) or code,
            'SECURITY_CODE': normalize_code(row_data.get('f12')) or code,
            'SECURITY_NAME_ABBR': row_data.get('f14') or name,
            'date': source_time[:10],
            'domain': 'candidate_fund_recheck',
            'source': 'eastmoney_candidate_fund_recheck_fallback_api',
            '主力净流入': fund_value,
            '主力净占比': row_data.get('f184'),
            'title': f"{row_data.get('f14') or name} 个股资金流 主力净流入 {fund_value}",
            'f12': row_data.get('f12'),
            'f13': row_data.get('f13'),
            'f14': row_data.get('f14'),
            'f2': row_data.get('f2'),
            'f3': row_data.get('f3'),
            'f62': row_data.get('f62'),
            'f184': row_data.get('f184'),
            'f66': row_data.get('f66'),
            'f69': row_data.get('f69'),
            'f72': row_data.get('f72'),
            'f75': row_data.get('f75'),
            'f78': row_data.get('f78'),
            'f81': row_data.get('f81'),
            'f84': row_data.get('f84'),
            'f87': row_data.get('f87'),
            'f124': row_data.get('f124'),
            'secid': secid,
        }
        if row['f12']:
            row['SECURITY_CODE'] = normalize_code(row['f12']) or row['SECURITY_CODE']
        return [row]
    return []


def fetch_concept_board_list_api(page_size=50):
    try:
        payload = eastmoney_get('https://push2.eastmoney.com/api/qt/clist/get', {
            'pn': '1',
            'pz': str(page_size),
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'wbp2u': '|0|0|0|web',
            'fid': 'f3',
            'fs': 'm:90+t:3',
            'fields': 'f1,f2,f3,f4,f8,f12,f13,f14,f104,f105,f128,f136,f140,f141,f124,f62',
            '_': int(time.time() * 1000),
        })
    except Exception:
        return []
    boards = []
    diff = (payload.get('data') or {}).get('diff') or []
    if not isinstance(diff, list):
        return []
    for item in diff:
        if not isinstance(item, dict):
            continue
        board_code = item.get('f12') or ''
        if not board_code:
            continue
        boards.append({
            'board_code': board_code,
            'board_name': item.get('f14') or '',
            'pct_change': item.get('f3'),
            'main_force_net_inflow': item.get('f62'),
            'up_count': item.get('f104'),
            'down_count': item.get('f105'),
            'leading_stock_name': item.get('f140') or '',
        })
    return boards


def fetch_concept_member_stocks_api(board_code, page_size=200):
    if not board_code:
        return []
    try:
        payload = eastmoney_get('https://push2.eastmoney.com/api/qt/clist/get', {
            'pn': '1',
            'pz': str(page_size),
            'po': '1',
            'np': '1',
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
            'fltt': '2',
            'invt': '2',
            'wbp2u': '|0|0|0|web',
            'fid': 'f3',
            'fs': f'b:{board_code}+f:!50',
            'fields': 'f1,f2,f3,f4,f5,f6,f7,f8,f12,f13,f14,f15,f16,f17,f18,f20,f21,f62,f124,f128,f140,f141',
            '_': int(time.time() * 1000),
        })
    except Exception:
        return []
    stocks = []
    diff = (payload.get('data') or {}).get('diff') or []
    if not isinstance(diff, list):
        return []
    for item in diff:
        if not isinstance(item, dict):
            continue
        code = normalize_code(item.get('f12'))
        if not code or not is_a_share_code(code):
            continue
        stocks.append({
            'code': code,
            'SECURITY_CODE': code,
            'SECURITY_NAME_ABBR': item.get('f14') or '',
            'name': item.get('f14') or '',
            'price': item.get('f2'),
            'pct_change': item.get('f3'),
            'main_force_net_inflow': item.get('f62'),
            'board_code': board_code,
            'source': 'eastmoney_concept_member_api',
        })
    return stocks


def merge_concept_stocks_into_quotes(concept_stocks, existing_quotes, max_per_board=30):
    seen_codes = {q.get('code') for q in existing_quotes if q.get('code')}
    merged = list(existing_quotes)
    added = 0
    for stock in concept_stocks:
        code = stock.get('code')
        if not code or code in seen_codes:
            continue
        if added >= max_per_board:
            break
        board_name = stock.get('board_name', '')
        quote = {
            'code': code,
            'SECURITY_CODE': code,
            'name': stock.get('name') or stock.get('SECURITY_NAME_ABBR') or '',
            'price': stock.get('price') or 0,
            'pct_chg': stock.get('pct_change'),
            'net_inflow_main': stock.get('main_force_net_inflow'),
            'source': 'eastmoney_concept_detail_api',
            'from_concept_board': stock.get('board_code', ''),
            'from_concept_board_name': board_name,
            'concept_sector_tag': board_name,
        }
        merged.append(quote)
        seen_codes.add(code)
        added += 1
    return merged


def rows_from_candidate_quote_api(candidate, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    secid = secid_for_code(code)
    fields = ','.join([
        'f43', 'f44', 'f45', 'f46', 'f47', 'f48', 'f50', 'f51', 'f52', 'f57', 'f58', 'f60',
        'f84', 'f85', 'f86', 'f127', 'f128', 'f129', 'f135', 'f136', 'f137', 'f170',
        'f31', 'f32', 'f33', 'f34', 'f35', 'f36', 'f37', 'f38', 'f39', 'f40',
        'BID1_PRICE', 'BID1_VOLUME', 'ASK1_PRICE', 'ASK1_VOLUME',
    ])
    try:
        payload = eastmoney_get('https://push2delay.eastmoney.com/api/qt/stock/get', {
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            'fltt': '2',
            'invt': '2',
            'fields': fields,
            'secid': secid,
            '_': int(time.time() * 1000),
        })
    except Exception:
        return [no_match_row(code, name, 'candidate_quote_recheck', source_time, 'CANDIDATE_QUOTE_RECHECK_QUERY_FAILED')]
    data = payload.get('data') or {}
    if not isinstance(data, dict):
        return [no_match_row(code, name, 'candidate_quote_recheck', source_time, 'NO_CANDIDATE_QUOTE_RECHECK_MATCH')]
    row = dict(data)
    row.update({
        'code': normalize_code(data.get('f57')) or code,
        'SECURITY_CODE': normalize_code(data.get('f57')) or code,
        'SECURITY_NAME_ABBR': data.get('f58') or name,
        'date': source_time[:10],
        'domain': 'candidate_quote_recheck',
        'source': 'eastmoney_candidate_quote_recheck_api',
        'title': f"{data.get('f58') or name} 盘口 五档 行情复核",
        '最新价': data.get('f43'),
        '最高': data.get('f44'),
        '最低': data.get('f45'),
        '今开': data.get('f46'),
        '涨停价': data.get('f51'),
        '跌停价': data.get('f52'),
        '主力净流入': data.get('f137'),
        'BOARD_NAME': data.get('f127') or data.get('f128') or data.get('f129'),
    })
    return [row]


def rows_from_candidate_fund_recheck(candidate, quote_rows, source_time):
    code = candidate['code']
    name = candidate.get('name', '')
    rows = []
    for quote_row in quote_rows:
        fund_value = quote_row.get('主力净流入')
        if fund_value in (None, ''):
            continue
        row = {
            'code': normalize_code(quote_row.get('code') or quote_row.get('SECURITY_CODE')) or code,
            'SECURITY_CODE': normalize_code(quote_row.get('code') or quote_row.get('SECURITY_CODE')) or code,
            'SECURITY_NAME_ABBR': quote_row.get('SECURITY_NAME_ABBR') or name,
            'date': source_time[:10],
            'domain': 'candidate_fund_recheck',
            'source': 'eastmoney_candidate_fund_recheck_quote_api',
            '主力净流入': fund_value,
            'title': f"{quote_row.get('SECURITY_NAME_ABBR') or name} 个股资金流 主力净流入 {fund_value}",
        }
        for key in ('最新价', 'BOARD_NAME', 'f137'):
            if key in quote_row:
                row[key] = quote_row.get(key)
        rows.append(row)
    if rows:
        return rows
    return rows_from_candidate_fund_flow_api(candidate, source_time)


def percentile_rank(value, values):
    if not values:
        return 0.0
    return round(sum(1 for item in values if item <= value) / len(values), 6)


def signal_stage_bucket(signal_pct):
    pct = num(signal_pct, None)
    if pct is None:
        return 'unknown'
    if pct < 0:
        return 'underwater'
    if pct < 3:
        return 'flat_0_to_3'
    if pct < 5:
        return 'early_3_to_5'
    if pct < 7:
        return 'mid_5_to_7'
    if pct < 9:
        return 'high_7_to_9'
    return 'near_limit_9_plus'


def candidate_setup(q, close_position_score, amount_pctile, fund_pctile, min_pct, max_pct, catalyst_index=None, source_time=None):
    pct = q.get('pct_chg') or 0.0
    volume_ratio = q.get('volume_ratio') or 0.0
    net_flow = q.get('net_inflow_main') or 0.0
    amplitude = q.get('amplitude') or 0.0
    stage_bucket = signal_stage_bucket(pct)
    low_position_stage = stage_bucket in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7')

    source_layers = ['L0_FULL_UNIVERSE']
    setup_type = 'FULL_UNIVERSE_BASE'
    search_layer_hint = ''
    recovery_score = 0.0

    if min_pct <= pct <= max_pct:
        source_layers.append('L1_HOT_MOMENTUM')
        setup_type = 'HOT_MOMENTUM'
    if pct >= min(max_pct, 8.0) or q.get('high', 0.0) >= q.get('prev_close', 0.0) * 1.095:
        source_layers.append('L2_LIMIT_STRENGTH')
        setup_type = 'LIMIT_STRENGTH'
    if stage_bucket == 'underwater' and volume_ratio >= 1.4 and net_flow > 0 and (close_position_score or 0.0) >= 0.70:
        source_layers.append('L4_UNDERWATER_RECOVERY')
        setup_type = 'UNDERWATER_TO_RED_STRENGTH'
        recovery_score += 48
    if -3.0 <= pct < 0.0 and volume_ratio >= 1.2 and net_flow > 0 and (close_position_score or 0.0) >= 0.55 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L4_UNDERWATER_RECOVERY')
        setup_type = 'UNDERWATER_RED_FLAT_RECOVERY'
        recovery_score += 35
    if -5.0 <= pct < 0.0 and volume_ratio >= 1.2 and net_flow > 0 and (close_position_score or 0.0) >= 0.65 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L4_UNDERWATER_RECOVERY')
        setup_type = 'UNDERWATER_GREEN_STRONG_SUPPORT'
        recovery_score += 40
    if -2.0 <= pct <= 5.0 and net_flow > 0 and volume_ratio >= 1.3 and (close_position_score or 0.0) >= 0.60 and fund_pctile >= 0.75 and amount_pctile >= 0.50 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L4_PRE_BREAKOUT')
        setup_type = 'LOW_POSITION_SECTOR_LIFT'
        recovery_score += 44
    if -2.0 <= pct <= 5.0 and net_flow > 0 and volume_ratio >= 1.2 and (close_position_score or 0.0) >= 0.55 and fund_pctile >= 0.50 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L4_PRE_BREAKOUT')
        setup_type = 'EARLY_SECTOR_ROTATION'
        recovery_score += 38
    if 0.0 <= pct <= 4.5 and net_flow > 0 and volume_ratio >= 1.3 and (close_position_score or 0.0) >= 0.60 and amount_pctile >= 0.60 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L3_FUND_FLOW')
        setup_type = 'FUND_FLOW_IGNITION'
        recovery_score += 36
    if 0.0 <= pct <= 5.0 and amplitude <= 5.0 and 0.8 <= volume_ratio <= 2.8 and net_flow >= 0 and (close_position_score or 0.0) >= 0.55 and setup_type == 'FULL_UNIVERSE_BASE':
        source_layers.append('L4_PRE_BREAKOUT')
        setup_type = 'LOW_POSITION_LIMITUP_EXPECTATION'
        recovery_score += 25
    if net_flow > 0 and fund_pctile >= 0.80 and amount_pctile >= 0.50:
        source_layers.append('L3_FUND_FLOW')
        if setup_type == 'FULL_UNIVERSE_BASE':
            setup_type = 'FUND_FLOW_LEADER'
    if stage_bucket == 'underwater':
        recovery_score += 10
    elif stage_bucket in ('flat_0_to_3', 'early_3_to_5'):
        recovery_score += 8
    elif stage_bucket == 'mid_5_to_7':
        recovery_score += 3
    elif stage_bucket == 'high_7_to_9':
        recovery_score -= 8
    elif stage_bucket == 'near_limit_9_plus':
        recovery_score -= 16
    recovery_score += min(20.0, max(0.0, volume_ratio * 4))
    recovery_score += min(20.0, max(0.0, fund_pctile * 20))
    recovery_score += min(20.0, max(0.0, (close_position_score or 0.0) * 20))
    if pct >= 8.0:
        recovery_score -= min(18.0, (pct - 8.0) * 6.0)
    if pct <= 1.5:
        recovery_score += 4.0

    catalyst_index = catalyst_index or {}
    symbol = str(q.get('code') or '')
    news_rows = (catalyst_index.get('news_by_symbol') or {}).get(symbol, [])
    limitup_rows = (catalyst_index.get('limitup_reasons_by_symbol') or {}).get(symbol, [])
    sector_tags = list((catalyst_index.get('sector_tags_by_symbol') or {}).get(symbol, []))
    sector_strength_by_tag = catalyst_index.get('sector_strength_by_tag') or {}
    news_keywords = set((catalyst_index.get('news_keywords_by_symbol') or {}).get(symbol, []))
    news_quality_rows = (catalyst_index.get('news_quality_by_symbol') or {}).get(symbol, [])
    news_quality_categories = sorted({
        str((item or {}).get('category') or 'unknown')
        for item in news_quality_rows
        if isinstance(item, dict)
    })
    news_quality_category_set = set(news_quality_categories)
    usable_news_catalyst = bool(news_quality_category_set.intersection({'positive_catalyst', 'sector_catalyst'}))
    risk_news_catalyst = bool(news_quality_category_set.intersection({'risk_notice', 'regulatory_notice'}))

    def sentiment_factor(value):
        text = str(value or '').lower()
        if 'positive' in text or '利好' in text or 'bull' in text:
            return 1.0
        if 'negative' in text or '利空' in text or 'bear' in text:
            return 0.35
        return 0.7

    news_catalyst_strength = 0.0
    sector_news_strength = 0.0
    news_age_penalty = 0.0
    for item in news_rows:
        quality = item.get('news_catalyst_quality') or {}
        if not quality.get('usable_for_candidate_generation'):
            continue
        publish_time = parse_date(item.get('publish_time') or item.get('date'))
        asof = parse_date(source_time) or datetime.now()
        age_days = None if publish_time is None else max(0, (asof.date() - publish_time.date()).days)
        if age_days is None:
            recency = 0.45
        elif age_days == 0:
            recency = 1.0
        elif age_days == 1:
            recency = 0.85
        elif age_days == 2:
            recency = 0.65
        elif age_days == 3:
            recency = 0.45
        else:
            recency = max(0.1, 0.45 - min(age_days, 10) * 0.03)
        confidence = num(item.get('confidence'), 0.5)
        strength = min(1.0, recency * sentiment_factor(item.get('sentiment')) * confidence)
        category = str(quality.get('category') or '')
        if category == 'sector_catalyst':
            quality_sector_terms = set(str(term) for term in (quality.get('sector_terms') or []) if term)
            if not quality_sector_terms or not sector_tags or quality_sector_terms.intersection(sector_tags):
                sector_news_strength = max(sector_news_strength, strength)
        else:
            news_catalyst_strength = max(news_catalyst_strength, strength)
        if age_days is not None and age_days > 1:
            news_age_penalty = max(news_age_penalty, min(0.25, 0.05 + (age_days - 1) * 0.04))

    limitup_reason_strength = 0.0
    limitup_reason_overlap = 0.0
    limitup_tags = set()
    for item in limitup_rows:
        category_weight = {'sector_driven': 1.0, 'news_driven': 0.9, 'fund_driven': 0.8, 'lhb_driven': 0.7}
        limitup_reason_strength = max(limitup_reason_strength, category_weight.get(item.get('reason_category'), 0.35))
        limitup_tags.update(str(tag) for tag in (item.get('reason_tags') or []) if tag)
        limitup_tags.update(str(tag) for tag in (item.get('related_sectors') or []) if tag)
    if news_keywords and limitup_tags and news_keywords.intersection(limitup_tags):
        limitup_reason_overlap = min(0.25, 0.08 * len(news_keywords.intersection(limitup_tags)))

    sector_opportunity_score = max([num(sector_strength_by_tag.get(tag), 0.0) for tag in sector_tags] or [0.0])
    sector_snapshot = catalyst_index.get('sector_opportunity_snapshot', [])
    for tag in sector_tags:
        for snapshot_row in sector_snapshot:
            if str(snapshot_row.get('sector') or '').strip() == tag:
                fund_flow_amount = num(snapshot_row.get('fund_flow_amount'), 0.0)
                if fund_flow_amount > 0:
                    fund_flow_bonus = min(0.3, fund_flow_amount / 500000000.0 * 0.3)
                    sector_opportunity_score = min(1.0, sector_opportunity_score + fund_flow_bonus)
                break
    if sector_news_strength > 0:
        sector_opportunity_score = max(sector_opportunity_score, min(1.0, sector_news_strength * 0.9 + (0.05 if sector_tags else 0.0)))
    topic_propagation_score = min(1.0, news_catalyst_strength * 0.35 + sector_opportunity_score * 0.50 + sector_news_strength * 0.15 + limitup_reason_strength * 0.15 + limitup_reason_overlap)
    intraday_alert_strength = min(1.0, component_score(volume_ratio, 3.0) * 0.35 + fund_pctile * 0.35 + max(0.0, close_position_score or 0.0) * 0.25 + (0.10 if low_position_stage else 0.0))
    if low_position_stage and net_flow > 0:
        intraday_alert_strength = min(1.0, intraday_alert_strength + 0.05)
    if low_position_stage and sector_opportunity_score <= 0 and (sector_news_strength > 0 or limitup_reason_strength > 0):
        sector_opportunity_score = min(
            1.0,
            max(sector_opportunity_score, sector_news_strength * 0.8, limitup_reason_strength * 0.8),
        )

    underwater_reversal_score = min(1.0, recovery_score / 100.0)
    low_position_catalyst_score = min(
        1.0,
        max(
            0.0,
            news_catalyst_strength * 0.15
            + sector_opportunity_score * 0.35
            + sector_news_strength * 0.08
            + topic_propagation_score * 0.15
            + intraday_alert_strength * 0.15
            + fund_pctile * 0.10
            + amount_pctile * 0.05
            + max(0.0, close_position_score or 0.0) * 0.08
            + underwater_reversal_score * 0.12
            - news_age_penalty
            - min(0.25, max(0.0, pct - 5.0) * 0.04)
            - (0.20 if pct >= 8.0 else 0.0)
            - (0.10 if pct >= 9.5 else 0.0)
        ),
    )

    news_catalyst_admissible = low_position_stage and news_catalyst_strength >= 0.35 and usable_news_catalyst and not risk_news_catalyst
    if news_catalyst_admissible:
        source_layers.append('L5_NEWS_CATALYST')
    if low_position_stage and sector_opportunity_score >= 0.30:
        source_layers.append('L6_SECTOR_CATALYST')
    if low_position_stage and intraday_alert_strength >= 0.35:
        source_layers.append('L7_INTRADAY_ALERT')
    if low_position_stage and limitup_reason_strength >= 0.35:
        source_layers.append('L8_LIMITUP_REASON_PROPAGATION')

    if low_position_stage and ((news_catalyst_admissible and news_catalyst_strength >= 0.35) or sector_news_strength >= 0.35) and sector_opportunity_score >= 0.30 and net_flow > 0 and fund_pctile >= 0.70:
        setup_type = 'TOPIC_FUND_IGNITION'
        search_layer_hint = 'news_catalyst_low_position' if news_catalyst_admissible and news_catalyst_strength >= sector_news_strength else 'sector_catalyst_low_position'
    elif news_catalyst_admissible:
        setup_type = 'NEWS_CATALYST_LOW_POSITION'
        search_layer_hint = 'news_catalyst_low_position'
    elif low_position_stage and sector_opportunity_score >= 0.30 and (sector_news_strength >= 0.20 or limitup_reason_strength >= 0.35):
        setup_type = 'SECTOR_NEWS_LOW_POSITION'
        search_layer_hint = 'sector_catalyst_low_position'
    elif setup_type in ('FULL_UNIVERSE_BASE', 'EARLY_SECTOR_ROTATION') and low_position_stage and intraday_alert_strength >= 0.35 and net_flow > 0:
        setup_type = 'INTRADAY_ALERT_REVERSAL'
        search_layer_hint = 'intraday_alert_reversal'
    elif low_position_stage and limitup_reason_strength >= 0.35:
        setup_type = 'LIMITUP_REASON_PROPAGATION'
        search_layer_hint = 'intraday_alert_reversal'

    setup_priority = {
        'TOPIC_FUND_IGNITION': 60,
        'NEWS_CATALYST_LOW_POSITION': 55,
        'SECTOR_NEWS_LOW_POSITION': 50,
        'INTRADAY_ALERT_REVERSAL': 45,
        'LIMITUP_REASON_PROPAGATION': 42,
        'UNDERWATER_TO_RED_STRENGTH': 30,
        'UNDERWATER_RED_FLAT_RECOVERY': 29,
        'UNDERWATER_GREEN_STRONG_SUPPORT': 28,
        'LOW_POSITION_SECTOR_LIFT': 25,
        'EARLY_SECTOR_ROTATION': 24,
        'LOW_POSITION_LIMITUP_EXPECTATION': 23,
        'FUND_FLOW_IGNITION': 20,
        'FUND_FLOW_LEADER': 18,
        'HOT_MOMENTUM': 10,
        'LIMIT_STRENGTH': 8,
        'FULL_UNIVERSE_BASE': 0,
    }.get(setup_type, 0)

    if not search_layer_hint:
        if setup_type in ('UNDERWATER_TO_RED_STRENGTH', 'UNDERWATER_RED_FLAT_RECOVERY', 'UNDERWATER_GREEN_STRONG_SUPPORT'):
            search_layer_hint = 'underwater_reversal'
        elif sector_opportunity_score > 0:
            search_layer_hint = 'structured_sector'
        elif setup_type in ('HOT_MOMENTUM', 'LIMIT_STRENGTH', 'FUND_FLOW_IGNITION', 'FUND_FLOW_LEADER'):
            search_layer_hint = 'formal_high_score'
        else:
            search_layer_hint = 'formal_high_score'

    return {
        'setup_type': setup_type,
        'source_layers': sorted(set(source_layers)),
        'recovery_score': round(recovery_score, 4),
        'search_priority': setup_priority,
        'search_layer_hint': search_layer_hint,
        'news_catalyst_strength': round(news_catalyst_strength, 4),
        'sector_news_strength': round(sector_news_strength, 4),
        'sector_opportunity_score': round(sector_opportunity_score, 4),
        'sector_opportunity_tags': sorted(set(sector_tags)),
        'topic_propagation_score': round(topic_propagation_score, 4),
        'intraday_alert_strength': round(intraday_alert_strength, 4),
        'limitup_reason_propagation_score': round(limitup_reason_strength, 4),
        'low_position_catalyst_score': round(low_position_catalyst_score, 4),
        'news_catalyst_quality_categories': news_quality_categories,
        'search_layer_hint': search_layer_hint,
    }


def build_candidates(quotes, min_pct, max_pct, max_candidates, source_time, output_dir, catalyst_index=None):
    full_tradable = [
        q for q in quotes
        if q['price'] > 0
        and (q.get('board') or '') in CORE_A_SHARE_BOARDS
        and (q.get('pct_chg') or 0.0) < 9.5  # 排除涨停票
    ]
    full_tradable.sort(key=lambda q: (q.get('amount') or 0.0, q.get('pct_chg') or 0.0), reverse=True)
    amount_values = sorted(q.get('amount') or 0.0 for q in full_tradable)
    fund_values = sorted(q.get('net_inflow_main') or 0.0 for q in full_tradable)
    market_breadth = round(sum(1 for q in quotes if (q.get('pct_chg') or 0.0) > 0) / len(quotes) * 100, 2) if quotes else 0.0
    market_limitups = sum(1 for q in quotes if (q.get('pct_chg') or 0.0) >= 9.5)
    market_bigups = sum(1 for q in quotes if (q.get('pct_chg') or 0.0) >= 5.0)
    board_counts = Counter(q.get('board') or board_for_code(q['code']) for q in full_tradable)

    catalyst_index = catalyst_index or {}
    sector_snapshot = catalyst_index.get('sector_opportunity_snapshot', [])
    pools = {
        'NEWS_CATALYST_LOW_POSITION': [],
        'SECTOR_NEWS_LOW_POSITION': [],
        'INTRADAY_ALERT_REVERSAL': [],
        'LIMITUP_REASON_PROPAGATION': [],
        'HOT_MOMENTUM': [],
        'LIMIT_STRENGTH': [],
        'FUND_FLOW': [],
        'UNDERWATER_RECOVERY': [],
        'PRE_BREAKOUT': [],
    }
    full_rank_by_code = {q['code']: rank for rank, q in enumerate(full_tradable, start=1)}
    for q in full_tradable:
        high = q.get('high') or 0.0
        low = q.get('low') or 0.0
        close_position_score = round((q.get('price', 0) - low) / (high - low), 6) if high > low else None
        amount_pctile = percentile_rank(q.get('amount') or 0.0, amount_values)
        fund_pctile = percentile_rank(q.get('net_inflow_main') or 0.0, fund_values)
        setup_profile = candidate_setup(q, close_position_score, amount_pctile, fund_pctile, min_pct, max_pct, catalyst_index, source_time)
        research_evidence_rows = [
            *catalyst_index.get('news_by_symbol', {}).get(q['code'], []),
            *catalyst_index.get('limitup_reasons_by_symbol', {}).get(q['code'], []),
        ]
        annotated = dict(q)
        annotated['_close_position_score'] = close_position_score
        annotated['_amount_pctile'] = amount_pctile
        annotated['_fund_pctile'] = fund_pctile
        annotated['_setup_type'] = setup_profile['setup_type']
        annotated['_source_layers'] = setup_profile['source_layers']
        annotated['_underwater_recovery_score'] = setup_profile['recovery_score']
        annotated['_search_priority'] = setup_profile['search_priority']
        annotated['_search_layer_hint'] = setup_profile['search_layer_hint']
        annotated['_news_catalyst_strength'] = setup_profile['news_catalyst_strength']
        annotated['_sector_news_strength'] = setup_profile['sector_news_strength']
        annotated['_sector_opportunity_score'] = setup_profile['sector_opportunity_score']
        annotated['_sector_opportunity_tags'] = setup_profile['sector_opportunity_tags']
        annotated['_topic_propagation_score'] = setup_profile['topic_propagation_score']
        annotated['_intraday_alert_strength'] = setup_profile['intraday_alert_strength']
        annotated['_limitup_reason_propagation_score'] = setup_profile['limitup_reason_propagation_score']
        annotated['_low_position_catalyst_score'] = setup_profile['low_position_catalyst_score']
        annotated['_news_catalyst_quality_categories'] = setup_profile['news_catalyst_quality_categories']
        annotated['_candidate_stage'] = signal_stage_bucket(q.get('pct_chg') or 0.0)
        annotated['research_signals'] = build_research_signals(annotated, research_evidence_rows, source_time, sector_snapshot)
        if 'L5_NEWS_CATALYST' in annotated['_source_layers']:
            pools['NEWS_CATALYST_LOW_POSITION'].append(annotated)
        if 'L6_SECTOR_CATALYST' in annotated['_source_layers']:
            pools['SECTOR_NEWS_LOW_POSITION'].append(annotated)
        if 'L7_INTRADAY_ALERT' in annotated['_source_layers']:
            pools['INTRADAY_ALERT_REVERSAL'].append(annotated)
        if 'L8_LIMITUP_REASON_PROPAGATION' in annotated['_source_layers']:
            pools['LIMITUP_REASON_PROPAGATION'].append(annotated)
        if 'L1_HOT_MOMENTUM' in annotated['_source_layers']:
            pools['HOT_MOMENTUM'].append(annotated)
        if 'L2_LIMIT_STRENGTH' in annotated['_source_layers']:
            pools['LIMIT_STRENGTH'].append(annotated)
        if 'L3_FUND_FLOW' in annotated['_source_layers']:
            pools['FUND_FLOW'].append(annotated)
        if 'L4_UNDERWATER_RECOVERY' in annotated['_source_layers']:
            pools['UNDERWATER_RECOVERY'].append(annotated)
        if 'L4_PRE_BREAKOUT' in annotated['_source_layers']:
            pools['PRE_BREAKOUT'].append(annotated)

    per_pool_cap = max(5, max_candidates // max(1, len(pools))) if max_candidates else 20
    selected = {}
    pool_sort_keys = {
        'NEWS_CATALYST_LOW_POSITION': lambda q: (q['_low_position_catalyst_score'], q['_news_catalyst_strength'], q['_sector_news_strength'], q['_sector_opportunity_score'], q['_topic_propagation_score'], q['_intraday_alert_strength'], q.get('amount') or 0.0, q.get('pct_chg') or 0.0),
        'SECTOR_NEWS_LOW_POSITION': lambda q: (q['_low_position_catalyst_score'], q['_sector_news_strength'], q['_sector_opportunity_score'], q['_topic_propagation_score'], q['_news_catalyst_strength'], q['_intraday_alert_strength'], q.get('amount') or 0.0, q.get('pct_chg') or 0.0),
        'INTRADAY_ALERT_REVERSAL': lambda q: (q['_low_position_catalyst_score'], q['_intraday_alert_strength'], q['_fund_pctile'], q['_close_position_score'] or 0.0, q.get('amount') or 0.0, q.get('pct_chg') or 0.0),
        'LIMITUP_REASON_PROPAGATION': lambda q: (q['_low_position_catalyst_score'], q['_limitup_reason_propagation_score'], q['_topic_propagation_score'], q['_sector_opportunity_score'], q.get('amount') or 0.0, q.get('pct_chg') or 0.0),
        'HOT_MOMENTUM': lambda q: (q.get('amount') or 0.0, q.get('pct_chg') or 0.0),
        'LIMIT_STRENGTH': lambda q: (q.get('pct_chg') or 0.0, q.get('amount') or 0.0),
        'FUND_FLOW': lambda q: (q['_fund_pctile'], q.get('amount') or 0.0),
        'UNDERWATER_RECOVERY': lambda q: (q['_underwater_recovery_score'], q['_fund_pctile'], q.get('amount') or 0.0),
        'PRE_BREAKOUT': lambda q: (q['_underwater_recovery_score'], q['_amount_pctile'], q.get('amount') or 0.0),
    }
    for pool_name, pool_rows in pools.items():
        for q in sorted(pool_rows, key=pool_sort_keys[pool_name], reverse=True)[:per_pool_cap]:
            existing = selected.get(q['code'])
            if existing:
                existing['_source_layers'] = sorted(set(existing['_source_layers']) | set(q['_source_layers']))
                if (
                    q['_search_priority'] > existing.get('_search_priority', 0)
                    or q['_low_position_catalyst_score'] > existing.get('_low_position_catalyst_score', 0.0)
                    or q['_underwater_recovery_score'] > existing.get('_underwater_recovery_score', 0.0)
                ):
                    existing['_setup_type'] = q['_setup_type']
                    existing['_underwater_recovery_score'] = q['_underwater_recovery_score']
                    existing['_search_priority'] = q['_search_priority']
                    existing['_search_layer_hint'] = q['_search_layer_hint']
                    existing['_news_catalyst_strength'] = q['_news_catalyst_strength']
                    existing['_sector_news_strength'] = q['_sector_news_strength']
                    existing['_sector_opportunity_score'] = q['_sector_opportunity_score']
                    existing['_sector_opportunity_tags'] = q['_sector_opportunity_tags']
                    existing['_topic_propagation_score'] = q['_topic_propagation_score']
                    existing['_intraday_alert_strength'] = q['_intraday_alert_strength']
                    existing['_limitup_reason_propagation_score'] = q['_limitup_reason_propagation_score']
                    existing['_low_position_catalyst_score'] = q['_low_position_catalyst_score']
                    existing['_news_catalyst_quality_categories'] = q['_news_catalyst_quality_categories']
            else:
                selected[q['code']] = q
    tradable = sorted(
        selected.values(),
        key=lambda q: (
            q.get('_search_priority', 0),
            q.get('_low_position_catalyst_score', 0.0),
            q.get('_news_catalyst_strength', 0.0),
            q.get('_sector_opportunity_score', 0.0),
            q.get('_topic_propagation_score', 0.0),
            q.get('_intraday_alert_strength', 0.0),
            q.get('_underwater_recovery_score', 0.0),
            q.get('_fund_pctile', 0.0),
            q.get('amount') or 0.0,
            q.get('pct_chg') or 0.0,
        ),
        reverse=True,
    )[:max_candidates]

    candidates = []
    for rank, q in enumerate(tradable, start=1):
        close_position_score = q['_close_position_score']
        reversal_risk = quote_reversal_risk(q, close_position_score)
        pre_limitup_anomaly = 0.0
        pct_val = q.get('pct_chg') or 0.0
        if pct_val is not None and 5.0 <= pct_val < 9.5 and (close_position_score or 0.0) >= 0.70:
            pre_limitup_anomaly = min(1.0, (pct_val - 5.0) / 4.5 * 0.55 + (close_position_score or 0.0) * 0.25 + q.get('_fund_pctile', 0.0) * 0.20)
        weak_to_strong_reversal = 0.0
        if 'L4_UNDERWATER_RECOVERY' in (q.get('_source_layers') or []):
            weak_to_strong_reversal = min(1.0, component_score(q.get('volume_ratio') or 0.0, 3.0) * 0.35 + q.get('_fund_pctile', 0.0) * 0.35 + (close_position_score or 0.0) * 0.30)
        limitup_capture = limitup_capture_profile_for_signal(
            pct_val,
            close_position_score,
            q.get('net_inflow_main'),
            q.get('_fund_pctile'),
            pre_limitup_anomaly,
            weak_to_strong_reversal,
            q.get('_limitup_reason_propagation_score'),
        )
        candidates.append({
            'signal_date': source_time[:10],
            'asof_time': source_time[11:],
            'code': q['code'],
            'name': q.get('name', ''),
            'board': q.get('board') or board_for_code(q['code']),
            'signal_close': q.get('price'),
            'price': q.get('price'),
            'signal_pct': pct_val,
            'candidate_stage': q['_candidate_stage'],
            'signal_amount': q.get('amount'),
            'market_regime': 'direct_api',
            'market_limitups': market_limitups,
            'market_bigups': market_bigups,
            'market_breadth_up_pct': market_breadth,
            'non_climax': pct_val < 9.5,
            'search_layer_hint': q.get('_search_layer_hint') or '',
            'search_priority': q.get('_search_priority', 0),
            'news_catalyst_strength': q.get('_news_catalyst_strength', 0.0),
            'sector_news_strength': q.get('_sector_news_strength', 0.0),
            'sector_opportunity_score': q.get('_sector_opportunity_score', 0.0),
            'sector_catalyst_score': q.get('_sector_opportunity_score', 0.0),
            'sector_opportunity_tags': q.get('_sector_opportunity_tags') or q.get('concept_sector_tags') or [],
            'topic_propagation_score': q.get('_topic_propagation_score', 0.0),
            'intraday_alert_strength': q.get('_intraday_alert_strength', 0.0),
            'limitup_reason_propagation_score': q.get('_limitup_reason_propagation_score', 0.0),
            'low_position_catalyst_score': q.get('_low_position_catalyst_score', 0.0),
            'limitup_capture_score': limitup_capture['limitup_capture_score'],
            'limitup_capture_profile': limitup_capture['limitup_capture_profile'],
            'limitup_capture_confirmed': limitup_capture['limitup_capture_confirmed'],
            'limitup_capture_reasons': limitup_capture['limitup_capture_reasons'],
            'news_catalyst_quality_categories': q.get('_news_catalyst_quality_categories', []),
            'theme_strength': min(12.0, max(0.0, q['pct_chg'], q['_underwater_recovery_score'] / 10)),
            'theme_big_strength': min(12.0, max(0.0, q['pct_chg']) * 0.6 + q['_underwater_recovery_score'] / 20),
            'top_theme_token': 'EASTMONEY_API_FULL_UNIVERSE_LAYERED_SCAN',
            'setup_type': q['_setup_type'],
            'source_layers': q['_source_layers'],
            'underwater_recovery_score': q['_underwater_recovery_score'],
            'full_universe_rank': full_rank_by_code.get(q['code']),
            'full_universe_quote_count': len(quotes),
            'full_universe_tradable_count': len(full_tradable),
            'full_universe_amount_pctile': q['_amount_pctile'],
            'full_universe_fund_pctile': q['_fund_pctile'],
            'rank': rank,
            'pct_rank': round(rank / len(tradable), 6) if tradable else 1.0,
            'amount_pctile_rule': q['_amount_pctile'],
            'turnover_rate': q.get('turnover_rate', 0),
            'volume_ratio': q.get('volume_ratio', 0),
            'net_inflow_main': q.get('net_inflow_main', 0),
            'close_position_score': close_position_score,
            'intraday_high_pct': reversal_risk['intraday_high_pct'],
            'pullback_from_high_pct': reversal_risk['pullback_from_high_pct'],
            'broken_limit_risk': reversal_risk['broken_limit_risk'],
            'broken_limit_risk_reason': reversal_risk['broken_limit_risk_reason'],
            'intraday_pullback_risk': reversal_risk['intraday_pullback_risk'],
            'weak_close_risk': reversal_risk['intraday_pullback_risk'],
            'source_time': source_time,
            'data_cutoff': source_time,
            'evidence_path': str(output_dir / 'xiaogu_scan_summary.json'),
            'score_asof_provenance': 'eastmoney_direct_api_full_universe_layered_sample',
            'candidate_pool_count': len(full_tradable),
            'source_row_hash': f"eastmoney_direct_api:{source_time}:{q.get('code','')}:{q.get('price',0)}:{q.get('pct_chg',0)}:{q.get('amount',0)}",
            'paper_only': True,
            'no_trade': True,
        })
    return candidates, {
        'market_breadth_up_pct': market_breadth,
        'market_limitups': market_limitups,
        'market_bigups': market_bigups,
        'tradable_candidate_count': len(full_tradable),
        'candidate_pool_counts': {name: len(rows) for name, rows in sorted(pools.items())},
        'full_universe_scan': {
            'enabled': True,
            'quote_count': len(quotes),
            'tradable_count': len(full_tradable),
            'coverage_status': 'PASS' if len(quotes) >= FULL_UNIVERSE_MIN_QUOTE_COUNT else 'LOW_SAMPLE',
            'min_quote_count': FULL_UNIVERSE_MIN_QUOTE_COUNT,
            'board_counts': dict(sorted(board_counts.items())),
        },
        'sector_catalyst_diagnostics': build_sector_catalyst_diagnostics(
            catalyst_index,
            candidates,
            {name: len(rows) for name, rows in sorted(pools.items())},
        ),
    }


def score_candidates(candidates, risk_map=None, evidence_pack=None, evidence_rows_by_domain=None):
    """Compatibility surface backed by the single main-force T+1 chain.

    The scanner library no longer owns a separate technical/hot-money score.
    ``risk_map`` and evidence arguments remain accepted for old callers, but
    formal ranking is delegated to the production runner only.
    """
    del risk_map, evidence_pack, evidence_rows_by_domain
    from xiaogu_forward_d1_1450_runner_v0_1 import (
        formal_candidate_sort_key,
        ranking_basis_adjustment_components,
    )

    scored = []
    block_reasons = Counter()
    for candidate in candidates or []:
        row = dict(candidate) if isinstance(candidate, dict) else {}
        key = formal_candidate_sort_key(row)
        adjustment = ranking_basis_adjustment_components(row)
        score = float(key[0]) if key else None
        reasons = list(adjustment.get('counter_evidence') or [])
        row.update({
            'score': score,
            'final_score': score,
            'production_score': score,
            'ranking_view': 'main_force_behavior_chain',
            'score_source': 'formal_t1_profit_components',
            'ranking_basis_adjustment': adjustment,
            'blocked_reasons': reasons,
            'paper_only': True,
            'no_trade': True,
        })
        for reason in reasons:
            block_reasons[str(reason).split(':')[0]] += 1
        scored.append(row)

    passed = sorted(
        [row for row in scored if row.get('score') is not None],
        key=lambda row: row['score'],
        reverse=True,
    )
    return scored, passed, dict(block_reasons)


def build_candidate_selection_reason(candidate, final_score, integrated_score_value, hm_score, matched_sector_name, realtime_score, trend_score, repo_contribution_summary):
    candidate = candidate if isinstance(candidate, dict) else {}
    parts = []

    def add(text):
        text = str(text).strip()
        if text and text not in parts:
            parts.append(text)

    setup_type = candidate.get('setup_type')
    if setup_type:
        add(f'形态={setup_type}')
    if matched_sector_name:
        add(f'题材={matched_sector_name}')
    else:
        tags = [str(tag).strip() for tag in (candidate.get('sector_opportunity_tags') or []) if str(tag).strip()]
        if tags:
            add('题材=' + '/'.join(tags[:3]))
    net_inflow = num(candidate.get('net_inflow_main'), None)
    if net_inflow is not None and abs(net_inflow) > 0:
        add(f'主力净流入={net_inflow / 100000000:.2f}亿')
    sector_opp = num(candidate.get('sector_opportunity_score'), None)
    if sector_opp is not None:
        add(f'题材强度={sector_opp:.2f}')
    topic = num(candidate.get('topic_propagation_score'), None)
    if topic is not None:
        add(f'题材传播={topic:.2f}')
    intraday = num(candidate.get('intraday_alert_strength'), None)
    if intraday is not None:
        add(f'盘中异动={intraday:.2f}')
    low_pos = num(candidate.get('low_position_catalyst_score'), None)
    if low_pos is not None:
        add(f'低位催化={low_pos:.2f}')
    if candidate.get('research_panel_overall'):
        add(f'研究面={candidate.get("research_panel_overall")}')
    catalyst_category = candidate.get('catalyst_quality_category') or candidate.get('research_catalyst_category')
    if catalyst_category:
        add(f'催化分类={catalyst_category}')
    if realtime_score is not None:
        add(f'实时题材={realtime_score:.0f}')
    if trend_score is not None:
        add(f'趋势={trend_score:.0f}')
    if integrated_score_value is not None:
        add(f'技术面={integrated_score_value:.2f}')
    if hm_score is not None:
        add(f'主力视角={hm_score:.2f}')
    if final_score is not None:
        add(f'综合分={final_score:.2f}')
    if repo_contribution_summary:
        add(f'repo={repo_contribution_summary}')
    return '; '.join(parts)


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + ('\n' if rows else ''), encoding='utf-8')


def write_vei_jsonl(path, rows):
    assert rows is not None, 'NULL OBJECT'
    for row in rows:
        assert 'component_details' in row, 'VEI LOST BEFORE WRITE'
        assert 'vei_checksum' in row, 'VEI CHECKSUM LOST BEFORE WRITE'
    write_jsonl(path, rows)
    return verify_vei_jsonl(path)


def signal_records_from_candidate(row, source_time):
    code = str(row.get('code') or row.get('symbol') or '').strip()
    if not code:
        return []
    structured = row.get('structured_component_details') if isinstance(row.get('structured_component_details'), dict) else {}
    component_scores = row.get('structured_score_components') if isinstance(row.get('structured_score_components'), dict) else {}
    research = row.get('research_signals') if isinstance(row.get('research_signals'), dict) else {}
    records = [
        ('main_force_net_inflow', row.get('net_inflow_main')),
        ('sector_opportunity_score', row.get('sector_opportunity_score')),
        ('fund_flow_momentum', row.get('fund_flow_momentum')),
        ('topic_propagation_score', row.get('topic_propagation_score')),
        ('intraday_alert_strength', row.get('intraday_alert_strength')),
        ('limitup_reason_propagation_score', row.get('limitup_reason_propagation_score')),
        ('low_position_catalyst_score', row.get('low_position_catalyst_score')),
        ('early_opportunity_score', row.get('early_opportunity_score')),
        ('close_position_score', row.get('close_position_score')),
        ('structured_score', row.get('structured_score')),
    ]
    for key, value in component_scores.items():
        records.append((f'structured_component_{key}', value))
    for key in ('candidate_stage', 'setup_type', 'search_layer_hint', 'market_regime'):
        value = row.get(key)
        if value not in (None, ''):
            records.append((key, value))
    if structured:
        for key, value in structured.items():
            if isinstance(value, (int, float)):
                records.append((f'structured_detail_{key}', value))
    if research:
        catalyst = research.get('catalyst_quality') if isinstance(research.get('catalyst_quality'), dict) else {}
        sector_mapping = research.get('sector_mapping') if isinstance(research.get('sector_mapping'), dict) else {}
        risk_review = research.get('a_share_risk_review') if isinstance(research.get('a_share_risk_review'), dict) else {}
        adversarial_review = research.get('adversarial_review') if isinstance(research.get('adversarial_review'), dict) else {}
        historical_pattern = research.get('historical_pattern') if isinstance(research.get('historical_pattern'), dict) else {}
        records.extend([
            ('research_catalyst_category', catalyst.get('category')),
            ('research_sector_mapping_confidence', sector_mapping.get('mapping_confidence')),
            ('research_panel_overall', (research.get('research_panel') or {}).get('overall') if isinstance(research.get('research_panel'), dict) else None),
            ('research_disqualified_for_paper_pick', 1.0 if risk_review.get('disqualified_for_paper_pick') else 0.0),
            ('research_bear_case_count', float(len(adversarial_review.get('bear_case_flags') or []))),
            ('research_disqualifying_count', float(len(adversarial_review.get('disqualifying_flags') or []))),
            ('historical_pattern_name', historical_pattern.get('pattern_name')),
        ])
    out = []
    for key, value in records:
        if value in (None, '', [], {}):
            continue
        out.append({
            'trade_date': source_time[:10],
            'symbol': code,
            'signal_key': key,
            'signal_value': num(value, None),
            'raw_json': {'value': value, 'source_time': source_time},
        })
    return out


def verify_vei_jsonl(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        return True
    non_zero_rows = 0
    for row in rows:
        cd = row['component_details']
        expected = vei_checksum({'component_details': cd})
        assert row.get('vei_checksum') == expected, 'VEI CHECKSUM MISMATCH'
        if any(num(cd.get(key), 0.0) > 0 for key in VEI_COMPONENT_KEYS):
            non_zero_rows += 1
    print('[VEI VERIFY]', {'last_component_details': rows[-1]['component_details'], 'non_zero_rows': non_zero_rows}, file=sys.stderr)
    assert non_zero_rows > 0, '❌ STILL BROKEN → DO NOT ENTER PHASE E'
    return True


def extract_hot_sector_names_from_capital_flow(evidence_rows_by_domain, top_n=10):
    data_rows = evidence_rows_by_domain.get('data_directory_content', [])
    sectors = []
    for row in data_rows:
        if row.get('item_key') != 'concept_capital_flow':
            continue
        cells = row.get('cells') or []
        header = row.get('header') or []
        if not cells or len(cells) < 5:
            continue
        if any(h in str(header) for h in ('序号', '名称')) and not any(c in str(cells[0]) for c in ('净额', '净占比', '类型')):
            name = str(cells[1]).strip() if len(cells) > 1 else ''
            pct_str = str(cells[3]).strip().replace('%', '') if len(cells) > 3 else ''
            flow_str = str(cells[4]).strip().replace('亿', '').replace('万', '').replace(',', '') if len(cells) > 4 else ''
            try:
                pct = float(pct_str)
            except (ValueError, TypeError):
                pct = 0.0
            try:
                flow = float(flow_str)
            except (ValueError, TypeError):
                flow = 0.0
            if name and (pct > 0 or flow > 0):
                sectors.append({'name': name, 'pct': pct, 'flow': flow})
    sectors.sort(key=lambda s: (s['pct'], s['flow']), reverse=True)
    return [s['name'] for s in sectors[:top_n]]


def fetch_concept_members_from_api(concept_names, max_concepts=100):
    """Fetch concept member stocks from eastmoney push API. Returns {concept_name: [(code, name), ...]}."""
    try:
        url = 'https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=500&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14'
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urlopen(req, timeout=10)
        boards = {d['f14']: d['f12'] for d in json.loads(resp.read()).get('data', {}).get('diff', []) if d.get('f12') and d.get('f14')}
    except Exception:
        return {}
    matched = {n: boards[n] for n in concept_names if n in boards}
    result = {}
    from concurrent.futures import ThreadPoolExecutor as _TPEC
    def _fetch(code):
        try:
            u = f'https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=50&pn=1&np=1&fltt=2&invt=2&fs=b:{code}&fields=f12,f14'
            r = Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            d = json.loads(urlopen(r, timeout=5).read())
            return [(x['f12'], x['f14']) for x in d.get('data', {}).get('diff', []) if x.get('f12')]
        except Exception:
            return []
    with _TPEC(10) as pool:
        futures = {pool.submit(_fetch, c): n for n, c in list(matched.items())[:max_concepts]}
        for f in futures:
            try:
                members = f.result(timeout=8)
                if members:
                    result[futures[f]] = members
            except Exception:
                pass
    return result


def build_stock_capital_flow_map(evidence_by_stock, raw_rows):
    concept_flow = {}
    sector_flow = {}
    for row in raw_rows:
        domain = row.get('domain', row.get('kind', ''))
        name = row.get('\u540d\u79f0', '') or ''
        if not name or name in ('\u51c0\u5360\u6bd4', '\u540d\u79f0', '\u51c0\u989d'):
            continue
        flow_str = row.get('\u4eca\u65e5\u4e3b\u529b\u51c0\u6d41\u5165', '')
        change_str = row.get('\u4eca\u65e5\u6da8\u8dcc\u5e45', '') or row.get('\u4eca\u65e5\n\u6da8\u8dcc\u5e45', '')
        entry = {'flow': flow_str, 'change': change_str}
        if domain == 'concept_capital_flow':
            concept_flow[name] = entry
        elif domain == 'sector_fund_flow':
            sector_flow[name] = entry

    import re as _re
    def _parse_amount(s):
        m = _re.match(r'([-\d.]+)(\u4ebf|\u4e07)?', str(s).strip())
        if not m:
            return 0.0
        v = float(m.group(1))
        if m.group(2) == '\u4e07':
            v /= 10000
        return v

    stock_flow = {}
    for code, evidence in evidence_by_stock.items():
        if code.startswith('_') or not isinstance(evidence, dict):
            continue
        ci_rows = evidence.get('concept_industry', [])
        sectors = set()
        for r in ci_rows:
            text = r.get('text', '') if isinstance(r, dict) else ''
            parts = text.split()
            if parts:
                sectors.add(parts[0])
            for tag in _re.findall(r'[\u4e00-\u9fa5A-Za-z0-9]{2,10}(?:\u6982\u5ff5|\u677f\u5757|\u82af\u7247|\u7535\u5b50|\u901a\u4fe1|\u79d1\u6280|\u5143\u4ef6|\u5238\u5546|\u53c2\u80a1)', text):
                sectors.add(tag)

        best_concept = 0.0
        best_sector = 0.0
        matched = []
        for sector in sectors:
            for name, data in concept_flow.items():
                if sector in name or name in sector:
                    v = _parse_amount(data['flow'])
                    if v > best_concept:
                        best_concept = v
                    matched.append('concept:' + name + '=' + data['flow'])
            for name, data in sector_flow.items():
                if sector in name or name in sector:
                    v = _parse_amount(data['flow'])
                    if v > best_sector:
                        best_sector = v
                    matched.append('sector:' + name + '=' + data['flow'])

        stock_flow[code] = {
            'concept_flow_100m': best_concept,
            'sector_flow_100m': best_sector,
            'sectors': list(sectors)[:5],
            'matched': matched[:3],
        }
    return stock_flow


def rows_from_candidate_intraday_replay(*args, **kwargs):
    return []


def source_status(local_rows_by_domain=None, source_rows=None, quote_count=0, fund_count=0):
    """Build direct API source status for offline helpers and tests."""
    local_rows_by_domain = local_rows_by_domain or {}
    quote_count = int(quote_count or 0)
    fund_count = int(fund_count or 0)
    return {
        'quote_rank': {'status': 'PASS' if quote_count else 'MISSING', 'record_count': quote_count},
        'fund_flow': {'status': 'PASS' if fund_count else 'MISSING', 'record_count': fund_count},
        'scanner_transport': 'direct_api',
        'production_source': API_SCAN_SOURCE,
        'source_incomplete_flags': [],
    }
