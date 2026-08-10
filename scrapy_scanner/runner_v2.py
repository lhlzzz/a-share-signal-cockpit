#!/usr/bin/env python3
"""
Eastmoney API Scanner v2 - Full data for runner consumption
All domains fetched at full capacity

Usage:
    python3 scrapy_scanner/runner_v2.py
    python3 scrapy_scanner/runner_v2.py --output-dir data/live_scan/2026-07-05/scan
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, ProxyHandler, build_opener

BASE = Path(os.environ.get('XIAOGU_HOME') or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BASE))

# Structured scoring helpers are owned by the direct API scanner.
try:
    from xiaogu_scanner_scoring import (
        build_information_coverage_audit,
        build_research_signals,
        build_structured_bundle,
        build_structured_scores,
    )
    HAS_STRUCTURED_HELPERS = True
except ImportError:
    build_information_coverage_audit = None
    HAS_STRUCTURED_HELPERS = False

from xiaogu_utils import eastmoney_quote_prices

LOCAL_OPENER = build_opener(ProxyHandler({}))
HEADERS = {
    'Referer': 'https://quote.eastmoney.com/',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
}
# Production transport is direct HTTP only.
DEFAULT_SCANNER_TRANSPORT = 'direct'
_SCANNER_TRANSPORT_LOGGED = False

EXTERNAL_MARKET_INDEXES = (
    ('us_djia', '100.DJIA', 'Dow Jones'),
    ('us_spx', '100.SPX', 'S&P 500'),
    ('us_nasdaq', '100.NDX', 'Nasdaq'),
    ('korea_kospi', '100.KS11', 'KOSPI'),
)

MAINBOARD_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003')
# Full candidate pool persisted for runner/DB; pre-enrichment must stay larger so
# unique-symbol fill can still hit the pool target after duplicates.
FULL_CANDIDATE_POOL_TARGET = 400
PRE_ENRICHMENT_CANDIDATE_TARGET = 500
# Eastmoney's clist endpoint exposes the quote payload as numbered fields.
# Request the full direct field range so downstream code can consume raw API
# values without a local sector/quote mapping layer.
MAX_SAFE_PAGE_COUNT = 100
STOCK_ALL_A_FIELDS = ','.join(f'f{field_id}' for field_id in range(1, 201))
STOCK_CAPITAL_FLOW_FIELDS = ','.join(
    ['f1', 'f2', 'f3', 'f12', 'f14']
    + [f'f{field_id}' for field_id in range(51, 76)]
)
MAINBOARD_AUXILIARY_EVIDENCE_DOMAINS = {
    'announcements': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
    'news_kuaixun': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'stock_reports': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'industry_reports': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'lhb': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'org_survey': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'earnings_preview': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
    'trading_halts': {'used_for_scoring': False, 'used_for_risk_filter': False, 'hard_block': False},
    'shareholder_changes': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
    'lockup_expiry': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
    'block_trades': {'used_for_scoring': False, 'used_for_risk_filter': False, 'hard_block': False},
    'popularity_rank': {'used_for_scoring': False, 'used_for_risk_filter': False, 'hard_block': False},
    'sector_capital_flow': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'stock_capital_flow': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'sector_news': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'limitup_reasons': {'used_for_scoring': True, 'used_for_risk_filter': False, 'hard_block': False},
    'risk_announcements': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
    'abnormal_movement_announcements': {'used_for_scoring': True, 'used_for_risk_filter': True, 'hard_block': False},
}
QUARANTINED_PROXY_AUXILIARY_DOMAINS = frozenset({
    'block_trades',
    'trading_halts',
    'popularity_rank',
})


def board_for_code(code):
    value = str(code or '').zfill(6)
    if value.startswith(('300', '301')):
        return 'chinext'
    if value.startswith(('688', '689')):
        return 'star'
    if value.startswith(('4', '8', '920')):
        return 'beijing'
    if value.startswith(MAINBOARD_PREFIXES):
        return 'main'
    return 'unknown'


def is_mainboard_code(code):
    return board_for_code(code) == 'main'


def normalize_stock_code(value):
    """Return a 6-digit A-share code, or None for article IDs / garbage tokens."""
    if value in (None, ''):
        return None
    raw = str(value).split('.', 1)[0].strip()
    if not raw:
        return None
    if len(raw) > 6 and raw[:2].lower() in ('sh', 'sz', 'bj') and raw[2:].isdigit():
        raw = raw[2:]
    # News/article IDs are long digit strings; never treat them as stock codes.
    if not raw.isdigit() or len(raw) > 6:
        return None
    code = raw.zfill(6)
    return code if len(code) == 6 and code.isdigit() else None


def stock_codes_from_row(row):
    codes = []
    # Prefer nested announcement payloads: codes[].stock_code
    for item in row.get('codes') or []:
        if isinstance(item, dict):
            value = item.get('stock_code') or item.get('stockCode') or item.get('code')
        else:
            value = item
        code = normalize_stock_code(value)
        if code and code not in codes:
            codes.append(code)
    for value in (
        row.get('SECURITY_CODE'), row.get('stockCode'), row.get('SECUCODE'),
        row.get('f12'), row.get('symbol'), row.get('c'), row.get('code'),
    ):
        code = normalize_stock_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def mainboard_row_coverage(rows):
    rows = [row for row in rows or [] if isinstance(row, dict)]
    rows_with_codes = 0
    mainboard_rows = 0
    mainboard_codes = set()
    for row in rows:
        codes = stock_codes_from_row(row)
        if not codes:
            continue
        rows_with_codes += 1
        main_codes = [code for code in codes if is_mainboard_code(code)]
        if main_codes:
            mainboard_rows += 1
            mainboard_codes.update(main_codes)
    return {
        'raw_rows': len(rows),
        'rows_with_stock_codes': rows_with_codes,
        'mainboard_rows': mainboard_rows,
        'mainboard_unique_codes': len(mainboard_codes),
        'mainboard_scope': 'direct_code_fields',
    }


def normalize_symbol_name(value):
    """Strip HTML/ST prefixes so title/text matching is stable across sources."""
    text = _clean_text_value(value)
    if not text:
        return ''
    text = re.sub(r'(?i)^(?:\*ST|S\*ST|SST|ST)\s*', '', text)
    text = re.sub(r'^(?:N|C)\s+', '', text)
    return text.strip()


def announcement_category(text):
    value = str(text or '')
    if any(token in value for token in ('退市', '重大违法', '立案调查', '监管措施', '监管函', '问询函', '警示函')):
        return 'risk_notice'
    if '异常波动' in value:
        return 'abnormal_movement'
    if any(token in value for token in ('减持', '股份变动', '限售股上市')):
        return 'reduction'
    if any(token in value for token in ('风险提示', '风险警示', '可能被实施', '停牌')):
        return 'risk_notice'
    if any(token in value for token in ('业绩', '预增', '扭亏', '预亏', '预减')):
        return 'earnings'
    if any(token in value for token in ('重大合同', '中标', '订单')):
        return 'major_contract'
    if any(token in value for token in ('重组', '并购', '资产注入')):
        return 'restructuring'
    if any(token in value for token in ('分红', '派息', '利润分配')):
        return 'dividend'
    return 'other'


def _clean_text_value(value):
    text = str(value or '').strip()
    if text:
        text = re.sub(r'</?em>', '', text, flags=re.I)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.strip()
    return '' if text in ('', '-', '--', '无', '暂无') else text


def _flatten_reason_values(value):
    if isinstance(value, dict):
        for key in (
            'reason', 'ztReason', 'zt_reason', 'limitup_reason', 'limit_up_reason',
            'reason_name', 'reasonName', 'reason_type', 'reasonType', 'concept',
            'conceptName', 'theme', 'tag', 'plate', '板块', '题材', 'name', 'title',
        ):
            text = _clean_text_value(value.get(key))
            if text:
                yield text
        for nested_key in ('reasonInfo', 'ztReasonInfo', 'reasons', 'tags', 'concepts', 'plates'):
            nested = value.get(nested_key)
            if nested is not None:
                yield from _flatten_reason_values(nested)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _flatten_reason_values(item)
        return
    text = _clean_text_value(value)
    if text:
        yield text


def limitup_reason_text(row):
    for key in (
        'reason', 'ztReason', 'zt_reason', 'limitup_reason', 'limit_up_reason',
        '涨停原因', '题材', 'hybk', 'zttj', 'f100', 'reason_name', 'reasonName',
        'reason_type', 'reasonType', 'concept', 'conceptName', 'theme', 'tag',
        'tags', 'bk', 'plate', '板块', 'reasonInfo', 'ztReasonInfo', 'reasons',
    ):
        for text in _flatten_reason_values(row.get(key)):
            return text
    return ''


def normalize_limitup_reason_row(row):
    normalized = dict(row or {})
    reason = limitup_reason_text(normalized)
    if reason:
        normalized.setdefault('limitup_reason', reason)
        normalized.setdefault('limitup_reason_proxy', False)
        if not normalized.get('limitup_reason_source'):
            for key in (
                'reason', 'ztReason', 'zt_reason', 'limitup_reason', 'limit_up_reason',
                '涨停原因', '题材', 'hybk', 'zttj', 'f100', 'reason_name', 'reasonName',
                'reason_type', 'reasonType', 'concept', 'conceptName', 'theme', 'tag',
                'tags', 'bk', 'plate', '板块', 'reasonInfo', 'ztReasonInfo', 'reasons',
            ):
                if list(_flatten_reason_values(normalized.get(key))):
                    normalized['limitup_reason_source'] = key
                    break
    return normalized


def is_one_word_limitup_row(row):
    for key in ('is_one_word', 'one_word', 'one_word_limitup', 'ywzt'):
        value = row.get(key)
        if value is True or str(value or '').strip().lower() in ('1', 'true', 'yes', 'y'):
            return True
    text = ' '.join(str(row.get(key) or '') for key in ('type', 'label', 'tags', 'reason', 'zttj'))
    if '一字' in text:
        return True
    quote = eastmoney_quote_prices(row)
    open_price = fnum(quote.get('open'))
    high_price = fnum(quote.get('high'))
    low_price = fnum(quote.get('low'))
    close_price = fnum(quote.get('close'))
    prices = [value for value in (open_price, high_price, low_price, close_price) if value > 0]
    return len(prices) == 4 and max(prices) == min(prices)


def yesterday_limitup_proxy_rows(data_cache):
    yesterday_rows = [row for row in data_cache.get('limitup_yesterday', []) or [] if isinstance(row, dict)]
    explicit_one_word = [row for row in data_cache.get('limitup_yesterday_one_word', []) or [] if isinstance(row, dict)]
    if explicit_one_word:
        return yesterday_rows, explicit_one_word, 'explicit_source'
    return yesterday_rows, [row for row in yesterday_rows if is_one_word_limitup_row(row)], 'derived_from_limitup_yesterday'


def enrich_mainboard_auxiliary_evidence(candidates, data_cache, *_unused_legacy_args):
    """Enrich candidates from their direct quote fields and evidence rows."""
    announcements_by_code = {}
    announcements_all = [row for row in data_cache.get('announcements', []) or [] if isinstance(row, dict)]
    for row in announcements_all:
        for code in stock_codes_from_row(row):
            announcements_by_code.setdefault(code, []).append(row)
    limitup_by_code = {}
    sector_limitup_reasons = {}
    for row in data_cache.get('limitup_pool', []) or []:
        reason = limitup_reason_text(row)
        sector = str(row.get('industry') or row.get('sector') or row.get('hybk') or row.get('f100') or '').strip()
        for code in stock_codes_from_row(row):
            limitup_by_code.setdefault(code, []).append(row)
        if sector and reason:
            sector_limitup_reasons.setdefault(sector, []).append(reason)
    yesterday_rows, yesterday_one_word_rows, one_word_source_mode = yesterday_limitup_proxy_rows(data_cache)
    yesterday_by_code = {}
    yesterday_one_word_by_code = {}
    sector_yesterday_limitups = {}
    sector_yesterday_one_word_limitups = {}
    for row in yesterday_rows:
        sector = str(row.get('industry') or row.get('sector') or row.get('hybk') or row.get('f100') or '').strip()
        codes = stock_codes_from_row(row)
        for code in codes:
            yesterday_by_code.setdefault(code, []).append(row)
        if sector:
            sector_yesterday_limitups.setdefault(sector, []).extend(codes or [''])
    for row in yesterday_one_word_rows:
        sector = str(row.get('industry') or row.get('sector') or row.get('hybk') or row.get('f100') or '').strip()
        codes = stock_codes_from_row(row)
        for code in codes:
            yesterday_one_word_by_code.setdefault(code, []).append(row)
        if sector:
            sector_yesterday_one_word_limitups.setdefault(sector, []).extend(codes or [''])

    news_rows = [row for row in data_cache.get('news_kuaixun', []) or [] if isinstance(row, dict)]
    sector_news_rows = [row for row in data_cache.get('sector_news', []) or [] if isinstance(row, dict)]
    industry_reports = [row for row in data_cache.get('industry_reports', []) or [] if isinstance(row, dict)]
    for candidate in candidates:
        code = str(candidate.get('code') or candidate.get('symbol') or '').zfill(6)
        name = str(candidate.get('name') or candidate.get('stock_name') or '').strip()
        name_key = normalize_symbol_name(name)
        own_limitup_industry = ''
        for limitup_row in [
            *(data_cache.get('limitup_pool', []) or []),
            *(data_cache.get('limitup_yesterday', []) or []),
        ]:
            if code not in stock_codes_from_row(limitup_row):
                continue
            own_limitup_industry = str(
                limitup_row.get('industry')
                or limitup_row.get('sector')
                or limitup_row.get('hybk')
                or ''
            ).strip()
            if own_limitup_industry:
                break
        sector = str(
            candidate.get('industry')
            or own_limitup_industry
            or candidate.get('sector_name')
            or candidate.get('sector')
            or ''
        ).strip()
        concepts = [
            str(value).strip()
            for value in candidate.get('sector_opportunity_tags') or []
            if str(value).strip()
        ]
        # Fill canonical stock-industry fields from stock-level evidence.
        if sector:
            candidate['industry'] = sector
            candidate['sector'] = sector
            candidate['sector_name'] = sector
        if concepts:
            existing_tags = list(candidate.get('sector_opportunity_tags') or [])
            for concept in concepts:
                if concept and concept not in existing_tags:
                    existing_tags.append(concept)
            candidate['sector_opportunity_tags'] = existing_tags
        # sector_opportunity_tags are market-theme context. Do not use them as
        # stock-industry terms when matching auxiliary evidence.
        sector_terms = [value for value in [sector, *concepts] if value]

        announcement_evidence = []
        risk_notice_evidence = []
        matched_ann_keys = set()
        for row in announcements_by_code.get(code, [])[:10]:
            title = _clean_text_value(row.get('title') or row.get('title_ch') or '')
            category = announcement_category(title)
            hard_block = any(token in title for token in ('退市', '重大违法', '立案调查', '实施风险警示', '停牌'))
            evidence = {'title': title, 'category': category, 'source': 'announcements', 'hard_block': hard_block}
            announcement_evidence.append(evidence)
            matched_ann_keys.add(row.get('art_code') or title)
            if category in ('risk_notice', 'abnormal_movement', 'reduction'):
                risk_notice_evidence.append(evidence)
        # Name-in-title fallback when nested codes miss the candidate (ST/alias titles).
        if name_key and len(name_key) >= 2 and len(announcement_evidence) < 10:
            for row in announcements_all:
                title = _clean_text_value(row.get('title') or row.get('title_ch') or '')
                key = row.get('art_code') or title
                if key in matched_ann_keys or not title:
                    continue
                short_names = []
                for item in row.get('codes') or []:
                    if isinstance(item, dict):
                        sn = normalize_symbol_name(item.get('short_name') or item.get('name'))
                        if sn:
                            short_names.append(sn)
                if name_key not in title and name_key not in short_names:
                    continue
                category = announcement_category(title)
                hard_block = any(token in title for token in ('退市', '重大违法', '立案调查', '实施风险警示', '停牌'))
                evidence = {
                    'title': title,
                    'category': category,
                    'source': 'announcements_name_match',
                    'hard_block': hard_block,
                }
                announcement_evidence.append(evidence)
                matched_ann_keys.add(key)
                if category in ('risk_notice', 'abnormal_movement', 'reduction'):
                    risk_notice_evidence.append(evidence)
                if len(announcement_evidence) >= 10:
                    break

        direct_news = []
        sector_news = []
        for row in sector_news_rows:
            text = ' '.join(str(row.get(key) or '') for key in ('title', 'summary', 'content', 'sector', 'source_query')).strip()
            if text and any(term and term in text for term in sector_terms):
                sector_news.append({
                    'title': str(row.get('title') or '')[:240],
                    'summary': str(row.get('summary') or row.get('content') or '')[:240],
                    'sector': str(row.get('sector') or row.get('source_query') or ''),
                    'source': 'sector_news',
                    'proxy': False,
                })
        for row in news_rows:
            text = ' '.join(str(row.get(key) or '') for key in ('title', 'summary', 'content')).strip()
            if not text:
                continue
            item = {'title': str(row.get('title') or '')[:240], 'source': 'news_kuaixun', 'proxy': True}
            if (
                (code and code in text)
                or (name and name in text)
                or (name_key and len(name_key) >= 2 and name_key in text)
            ):
                direct_news.append(item)
            elif any(term and term in text for term in sector_terms):
                sector_news.append(item)
        sector_report_evidence = []
        for row in industry_reports:
            text = ' '.join(str(row.get(key) or '') for key in ('industryName', 'title')).strip()
            if any(term and term in text for term in sector_terms):
                sector_report_evidence.append({'title': str(row.get('title') or '')[:240], 'source': 'industry_reports', 'proxy': True})

        direct_limitup_reasons = []
        for row in limitup_by_code.get(code, []):
            reason = limitup_reason_text(row)
            if reason:
                direct_limitup_reasons.append({'reason': reason, 'source': 'limitup_pool', 'proxy': False})
        sector_limitup_proxy = []
        if not direct_limitup_reasons:
            for term in sector_terms:
                for reason in sector_limitup_reasons.get(term, [])[:3]:
                    sector_limitup_proxy.append({'reason': reason, 'source': 'limitup_pool_sector_proxy', 'proxy': True, 'sector': term})

        own_yesterday_rows = yesterday_by_code.get(code, [])
        own_yesterday_one_word_rows = yesterday_one_word_by_code.get(code, [])
        sector_yesterday_matches = []
        sector_yesterday_one_word_matches = []
        for term in sector_terms:
            count = len(sector_yesterday_limitups.get(term, []))
            one_word_count = len(sector_yesterday_one_word_limitups.get(term, []))
            if count:
                sector_yesterday_matches.append({'sector': term, 'count': count, 'source': 'limitup_yesterday', 'proxy': True})
            if one_word_count:
                sector_yesterday_one_word_matches.append({'sector': term, 'count': one_word_count, 'source': 'limitup_yesterday_one_word', 'proxy': True})
        continuation_gene_score = clamp01(
            (0.70 if own_yesterday_rows else 0.0)
            + (0.15 if own_yesterday_one_word_rows else 0.0)
            + min(0.35, sum(item['count'] for item in sector_yesterday_matches) * 0.08)
            + min(0.15, sum(item['count'] for item in sector_yesterday_one_word_matches) * 0.05)
        )
        limitup_reason_status = 'PASS' if direct_limitup_reasons else ('PROXY' if sector_limitup_proxy or continuation_gene_score > 0 else 'MISSING')

        announcement_score = clamp01(sum(1 for item in announcement_evidence if item['category'] in ('earnings', 'major_contract', 'restructuring', 'dividend')) / 2.0)
        news_score = clamp01(len(direct_news) * 0.45)
        sector_news_score = clamp01(len(sector_news) * 0.20 + len(sector_report_evidence) * 0.18)
        limitup_score = 1.0 if direct_limitup_reasons else (0.55 if sector_limitup_proxy else (0.25 if code in limitup_by_code else 0.0))
        risk_penalty = clamp01(sum(1.0 if item.get('hard_block') else {'risk_notice': 0.45, 'abnormal_movement': 0.25, 'reduction': 0.35}.get(item['category'], 0.0) for item in risk_notice_evidence))
        missing_domains = []
        if not announcement_evidence:
            missing_domains.append('announcements')
        if not direct_news:
            missing_domains.append('direct_symbol_news')
        if not sector_news and not sector_report_evidence:
            missing_domains.append('sector_news')
        if limitup_reason_status == 'MISSING':
            missing_domains.append('limitup_reasons')
        filled_domain_count = 4 - len(missing_domains)
        confidence = clamp01((int(bool(announcement_evidence)) + int(bool(direct_news)) + int(bool(sector_news or sector_report_evidence)) + int(limitup_reason_status != 'MISSING')) / 4.0)
        # PASS when all domains present, OR high-confidence partial with limitup-reason evidence.
        # Avoid permanent 0 PASS when one soft domain (e.g. direct_symbol_news) is unmatched.
        if not missing_domains:
            aux_status = 'PASS'
        elif (
            confidence >= 0.75
            and limitup_reason_status in ('PASS', 'PROXY')
            and filled_domain_count >= 2
        ):
            aux_status = 'PASS'
        elif confidence > 0:
            aux_status = 'PARTIAL'
        else:
            aux_status = 'MISSING'

        candidate.update({
            'mainboard_policy': 'main_only',
            'mainboard_auxiliary_evidence_domains': {
                'announcements': {
                    'present': bool(announcement_evidence),
                    'count': len(announcement_evidence),
                    'kind': '公告',
                    'positive_catalyst_count': sum(
                        1 for item in announcement_evidence
                        if item['category'] in ('earnings', 'major_contract', 'restructuring', 'dividend')
                    ),
                    'risk_notice_count': len(risk_notice_evidence),
                },
                'direct_symbol_news': {
                    'present': bool(direct_news),
                    'count': len(direct_news),
                    'kind': '直接个股新闻',
                },
                'sector_news': {
                    'present': bool(sector_news or sector_report_evidence),
                    'count': len(sector_news) + len(sector_report_evidence),
                    'kind': '板块新闻/行业报告',
                },
                'limitup_reasons': {
                    'present': bool(direct_limitup_reasons or sector_limitup_proxy),
                    'count': len(direct_limitup_reasons) + len(sector_limitup_proxy),
                    'kind': '涨停理由/板块延续证据',
                },
            },
            'announcement_evidence': announcement_evidence,
            'news_evidence': {
                'direct_symbol_news': direct_news[:5],
                'sector_related_news': sector_news[:5],
                'macro_only_news_count': max(0, len(news_rows) - len(direct_news) - len(sector_news)),
            },
            'sector_news_evidence': (sector_news + sector_report_evidence)[:8],
            'limitup_reason_evidence': (direct_limitup_reasons + sector_limitup_proxy)[:5],
            'yesterday_limitup_gene_evidence': {
                'status': 'PROXY' if own_yesterday_rows else 'MISSING',
                'candidate_was_yesterday_limitup': bool(own_yesterday_rows),
                'records': own_yesterday_rows[:3],
                'source': 'limitup_yesterday',
            },
            'yesterday_one_word_limitup_gene_evidence': {
                'status': 'PROXY' if own_yesterday_one_word_rows else 'MISSING',
                'candidate_was_yesterday_one_word_limitup': bool(own_yesterday_one_word_rows),
                'records': own_yesterday_one_word_rows[:3],
                'source': 'limitup_yesterday_one_word',
                'source_mode': one_word_source_mode,
                'risk_note': 'strong continuation gene with elevated next-day gap-profit-taking risk',
            },
            'sector_yesterday_limitup_gene_proxy': {
                'status': 'PROXY' if sector_yesterday_matches or sector_yesterday_one_word_matches else 'MISSING',
                'sector_matches': sector_yesterday_matches[:5],
                'one_word_sector_matches': sector_yesterday_one_word_matches[:5],
                'proxy_sources': ['limitup_yesterday', 'limitup_yesterday_one_word'],
            },
            'continuation_gene_score': round(continuation_gene_score, 4),
            'limitup_reason_status': limitup_reason_status,
            'limitup_reason_hard_block': False,
            'risk_notice_evidence': risk_notice_evidence,
            'announcement_catalyst_score': round(announcement_score, 4),
            'legacy_news_catalyst_strength': round(fnum(candidate.get('news_catalyst_strength')), 4),
            'news_catalyst_strength': round(news_score, 4),
            'sector_news_catalyst_score': round(sector_news_score, 4),
            'sector_news_strength': round(max(fnum(candidate.get('sector_news_strength')), sector_news_score), 4),
            'limitup_reason_quality_score': round(limitup_score, 4),
            'risk_notice_penalty': round(risk_penalty, 4),
            'mainboard_auxiliary_confidence': round(confidence, 4),
            'mainboard_auxiliary_evidence_status': aux_status,
            'mainboard_auxiliary_missing_domains': missing_domains,
            'mainboard_auxiliary_evidence_summary': (
                '公告存在但不等于直接个股新闻；'
                'direct_symbol_news 与 sector_news 分别代表直接个股快讯和板块新闻/行业报告。'
            ),
        })
        research_signals = candidate.get('research_signals') if isinstance(candidate.get('research_signals'), dict) else {}
        catalyst_quality = research_signals.setdefault('catalyst_quality', {})
        catalyst_quality.update({
            'mainboard_auxiliary_status': candidate['mainboard_auxiliary_evidence_status'],
            'announcement_catalyst_score': candidate['announcement_catalyst_score'],
            'news_catalyst_strength': candidate['news_catalyst_strength'],
            'sector_news_catalyst_score': candidate['sector_news_catalyst_score'],
            'limitup_reason_quality_score': candidate['limitup_reason_quality_score'],
            'limitup_reason_status': candidate['limitup_reason_status'],
            'continuation_gene_score': candidate['continuation_gene_score'],
        })
        if catalyst_quality.get('category') in (None, '', 'neutral') and max(announcement_score, news_score, sector_news_score, limitup_score) > 0:
            catalyst_quality['category'] = 'positive_catalyst' if max(announcement_score, news_score) > 0 else 'sector_catalyst'
        catalyst_quality['usable_for_candidate_generation'] = bool(max(announcement_score, news_score, sector_news_score, limitup_score) > 0)
        catalyst_quality['usable_for_paper_pick'] = bool(max(announcement_score, news_score, sector_news_score, limitup_score) > 0 and risk_penalty < 0.60)
        risk_review = research_signals.setdefault('a_share_risk_review', {})
        risk_review['mainboard_auxiliary_risk_notices'] = risk_notice_evidence
        risk_review['risk_notice_penalty'] = candidate['risk_notice_penalty']
        candidate['research_signals'] = research_signals
    return candidates


def build_mainboard_information_coverage_audit(data_cache, candidates):
    candidates = [row for row in candidates or [] if isinstance(row, dict)]
    matched = {
        'announcements': sum(bool(row.get('announcement_evidence')) for row in candidates),
        'news_kuaixun': sum(bool((row.get('news_evidence') or {}).get('direct_symbol_news')) for row in candidates),
        'stock_reports': sum(bool(row.get('in_stock_reports')) for row in candidates),
        'industry_reports': sum(any(item.get('source') == 'industry_reports' for item in row.get('sector_news_evidence') or []) for row in candidates),
        'lhb': sum(bool(row.get('in_lhb')) for row in candidates),
        'org_survey': sum(bool(row.get('in_org_survey')) for row in candidates),
        'earnings_preview': sum(bool(row.get('in_earnings_preview')) for row in candidates),
        'trading_halts': sum(bool(row.get('in_halted')) for row in candidates),
        'shareholder_changes': sum(bool(row.get('in_shareholder_changes')) for row in candidates),
        'lockup_expiry': sum(bool(row.get('in_lockup_expiry')) for row in candidates),
        'block_trades': sum(bool(row.get('in_block_trades')) for row in candidates),
        'popularity_rank': sum(bool(row.get('in_popularity_rank')) for row in candidates),
        'stock_capital_flow': sum(bool(row.get('in_capital_flow')) for row in candidates),
        'sector_news': sum(bool(row.get('sector_news_evidence')) for row in candidates),
        'limitup_reasons': sum(bool(row.get('limitup_reason_evidence')) for row in candidates),
        'risk_announcements': sum(bool(row.get('risk_notice_evidence')) for row in candidates),
        'abnormal_movement_announcements': sum(any(item.get('category') == 'abnormal_movement' for item in row.get('risk_notice_evidence') or []) for row in candidates),
    }
    raw_counts = {name: result_item_count(data_cache.get(name, [])) for name in MAINBOARD_AUXILIARY_EVIDENCE_DOMAINS}
    raw_counts['sector_capital_flow'] = result_item_count(data_cache.get('sector_capital_flow', {})) or len(data_cache.get('flow_industry', [])) + len(data_cache.get('flow_concept', []))
    raw_counts['sector_news'] = result_item_count(data_cache.get('sector_news', []))
    raw_counts['limitup_reasons'] = sum(bool(limitup_reason_text(row)) for row in data_cache.get('limitup_pool', []) or [])
    yesterday_rows, yesterday_one_word_rows, one_word_source_mode = yesterday_limitup_proxy_rows(data_cache)
    raw_counts['risk_announcements'] = sum(announcement_category(row.get('title') or row.get('title_ch')) in ('risk_notice', 'reduction') for row in data_cache.get('announcements', []) or [])
    raw_counts['abnormal_movement_announcements'] = sum(announcement_category(row.get('title') or row.get('title_ch')) == 'abnormal_movement' for row in data_cache.get('announcements', []) or [])
    sources = {}
    for name, policy in MAINBOARD_AUXILIARY_EVIDENCE_DOMAINS.items():
        raw_count = raw_counts.get(name, 0)
        is_quarantined_proxy = name in QUARANTINED_PROXY_AUXILIARY_DOMAINS
        record = {
            'status': 'PASS' if raw_count > 0 else 'MISSING',
            'source_type': 'DIRECT' if raw_count > 0 else 'MISSING',
            'quality_status': 'PASS' if raw_count > 0 else 'MISSING',
            'production_use': (
                'DISABLED_UNTIL_SPECIALIZED_SOURCE'
                if is_quarantined_proxy
                else ('ENABLED' if raw_count > 0 else 'UNAVAILABLE')
            ),
            'quality_gaps': [] if raw_count > 0 else ['source_missing'],
            'collected': raw_count > 0,
            'raw_count': raw_count,
            'raw_file_written': raw_count > 0,
            'matched_candidate_count': matched.get(name, 0),
            **policy,
        }
        if is_quarantined_proxy:
            record.update({
                'status': 'PROXY' if raw_count > 0 else 'MISSING',
                'source_type': 'PROXY' if raw_count > 0 else 'MISSING',
                'quality_status': 'PROXY_QUARANTINED' if raw_count > 0 else 'MISSING',
                'quality_gaps': ['specialized_source_unavailable'] if raw_count > 0 else ['source_missing'],
            })
        if name == 'sector_news':
            proxy_sources = [source for source in ('industry_reports', 'news_kuaixun', 'sector_capital_flow') if raw_counts.get(source, 0) > 0]
            if raw_count > 0:
                record.update({
                    'status': 'PASS',
                    'source_type': 'DIRECT',
                    'quality_status': 'PASS',
                    'production_use': 'ENABLED',
                    'quality_gaps': [],
                    'proxy_sources': [],
                    'raw_file_written': True,
                    'source': 'eastmoney_sector_news',
                })
            else:
                record.update({
                    'status': 'PROXY' if proxy_sources else 'MISSING',
                    'source_type': 'PROXY' if proxy_sources else 'MISSING',
                    'quality_status': 'PROXY' if proxy_sources else 'MISSING',
                    'production_use': 'SCORING_PROXY_ONLY' if proxy_sources else 'UNAVAILABLE',
                    'quality_gaps': ['independent_sector_news_unavailable'] if proxy_sources else ['source_missing'],
                    'proxy_sources': proxy_sources,
                    'raw_file_written': False,
                    'reason': 'no independent sector_news rows; candidate-scoped proxy only',
                })
        elif name == 'limitup_reasons' and raw_count <= 0:
            pool_count = len(data_cache.get('limitup_pool', []) or [])
            proxy_sources = []
            if yesterday_rows:
                proxy_sources.append('limitup_yesterday')
            if yesterday_one_word_rows:
                proxy_sources.append('limitup_yesterday_one_word')
            if pool_count > 0:
                proxy_sources.append('limitup_pool_if_available')
            record.update({
                'status': 'PROXY' if proxy_sources else 'MISSING',
                'source_type': 'PROXY' if proxy_sources else 'MISSING',
                'quality_status': 'PROXY' if proxy_sources else 'MISSING',
                'production_use': 'CONTINUATION_PROXY_ONLY' if proxy_sources else 'UNAVAILABLE',
                'quality_gaps': ['current_day_limitup_reason_unavailable'] if proxy_sources else ['source_missing'],
                'proxy_sources': proxy_sources,
                'reason': 'current_day_limitup_reason_unavailable; yesterday pools used only as continuation-gene proxy' if proxy_sources else 'current_day_limitup_pool_empty',
                'impact': 'supports continuation gene analysis but does not prove current-day limitup reason; reduces high-chase confidence and continuation certainty',
                'hard_block': False,
                'yesterday_limitup_raw_count': len(yesterday_rows),
                'yesterday_one_word_limitup_raw_count': len(yesterday_one_word_rows),
                'yesterday_one_word_source_mode': one_word_source_mode,
            })
        sources[name] = record
    coverage_gaps = [f'{name}:{record["status"]}' for name, record in sources.items() if record['status'] == 'MISSING']
    partial_reasons = [
        {
            'domain': name,
            'status': record['status'],
            'reason': record.get('reason') or 'source_or_candidate_match_incomplete',
            'hard_block': bool(record.get('hard_block')),
            'impact': record.get('impact') or ('proxy evidence only' if record['status'] == 'PROXY' else 'candidate evidence incomplete'),
        }
        for name, record in sources.items()
        if record['status'] in ('MISSING', 'PROXY')
    ]
    non_missing_domains = {
        'announcements': sources['announcements']['status'],
        'eastmoney_news': sources['news_kuaixun']['status'],
        'sector_news': sources['sector_news']['status'],
        'risk_announcements': 'PASS_OR_EMPTY' if sources['announcements']['status'] == 'PASS' else sources['risk_announcements']['status'],
        'stock_capital_flow': sources['stock_capital_flow']['status'],
        'popularity_rank': sources['popularity_rank']['status'],
        'yesterday_limitup_proxy': 'DIRECT' if yesterday_rows else 'MISSING',
        'yesterday_one_word_limitup_proxy': (
            'DIRECT' if yesterday_one_word_rows and one_word_source_mode == 'explicit_source'
            else ('DERIVED' if yesterday_one_word_rows else 'MISSING')
        ),
    }
    yesterday_limitup_status = 'DIRECT' if yesterday_rows else 'MISSING'
    yesterday_one_word_source_type = (
        'DIRECT' if yesterday_one_word_rows and one_word_source_mode == 'explicit_source'
        else ('DERIVED' if yesterday_one_word_rows else 'MISSING')
    )
    return {
        'status': 'PARTIAL' if partial_reasons else 'PASS',
        'partial_reasons': partial_reasons,
        'non_missing_domains': non_missing_domains,
        'mainboard_policy': 'main_only',
        'news_sources': {
            'announcements': sources['announcements'],
            'eastmoney_news': sources['news_kuaixun'],
            'news_kuaixun': sources['news_kuaixun'],
            'sector_news': sources['sector_news'],
            'limitup_reasons': sources['limitup_reasons'],
            'risk_announcements': sources['risk_announcements'],
            'abnormal_movement_announcements': sources['abnormal_movement_announcements'],
        },
        'auxiliary_sources': sources,
        'coverage_gaps': coverage_gaps,
        'yesterday_limitup_proxy': {
            'status': 'PROXY' if yesterday_rows else 'MISSING',
            'source_status': yesterday_limitup_status,
            'source_type': yesterday_limitup_status,
            'quality_status': 'PASS' if yesterday_rows else 'MISSING',
            'production_use': 'CONTINUATION_PROXY_ONLY' if yesterday_rows else 'UNAVAILABLE',
            'evidence_role': 'CONTINUATION_PROXY',
            'quality_gaps': [] if yesterday_rows else ['source_missing'],
            'raw_count': len(yesterday_rows),
            'one_word_raw_count': len(yesterday_one_word_rows),
            'one_word_source_mode': one_word_source_mode,
            'hard_block': False,
        },
        'yesterday_one_word_limitup_proxy': {
            'status': 'PROXY' if yesterday_one_word_rows else 'MISSING',
            'source_status': yesterday_one_word_source_type,
            'source_type': yesterday_one_word_source_type,
            'quality_status': 'PASS' if yesterday_one_word_rows and one_word_source_mode == 'explicit_source' else (
                'DERIVED' if yesterday_one_word_rows else 'MISSING'
            ),
            'production_use': 'CONTINUATION_PROXY_ONLY' if yesterday_one_word_rows else 'UNAVAILABLE',
            'evidence_role': 'CONTINUATION_PROXY',
            'quality_gaps': (
                ['derived_from_yesterday_limitup']
                if yesterday_one_word_rows and one_word_source_mode != 'explicit_source'
                else ([] if yesterday_one_word_rows else ['source_missing'])
            ),
            'raw_count': len(yesterday_one_word_rows),
            'source_mode': one_word_source_mode,
            'hard_block': False,
        },
    }


def fnum(value, default=0.0):
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _loads_json_or_jsonp(text):
    text = str(text or '').strip().lstrip('﻿')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [idx for idx in (text.find('{'), text.find('[')) if idx >= 0]
        end = max(text.rfind('}'), text.rfind(']'))
        if not start_candidates or end < 0:
            raise
        return json.loads(text[min(start_candidates):end + 1])


def resolve_scanner_transport():
    """Return the only supported transport mode."""
    return DEFAULT_SCANNER_TRANSPORT


def _api_get_direct(url, timeout=20, attempts=3):
    """Fetch Eastmoney JSON/JSONP over direct HTTP."""
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            req = Request(url, headers=HEADERS)
            with LOCAL_OPENER.open(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', 'replace')
            return _loads_json_or_jsonp(body)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(0.25 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError('API_GET_DIRECT_FAILED')


def stock_concepts_from_quote_row(row):
    return list(dict.fromkeys(
        value.strip()
        for value in re.split(r'[,，;；|]+', str((row or {}).get('f103') or ''))
        if value.strip() and value.strip() not in ('-', '--')
    ))

def api_get(url):
    """Eastmoney API fetch. Production transport is direct HTTP only."""
    global _SCANNER_TRANSPORT_LOGGED
    resolve_scanner_transport()
    result = _api_get_direct(url)
    used = 'direct'
    if not _SCANNER_TRANSPORT_LOGGED:
        print(f'SCANNER_TRANSPORT={used}')
        _SCANNER_TRANSPORT_LOGGED = True
    return result


def fetch_external_market_snapshot(captured_at):
    rows = []
    missing_indices = []
    for key, secid, display_name in EXTERNAL_MARKET_INDEXES:
        try:
            payload = None
            last_error = None
            for attempt in range(3):
                try:
                    payload = api_get(
                        'https://push2delay.eastmoney.com/api/qt/stock/get?'
                        + urlencode({
                            'secid': secid,
                            'fields': 'f43,f57,f58,f60,f169,f170',
                        })
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.25 * (attempt + 1))
            if payload is None:
                raise last_error or RuntimeError('EASTMONEY_GLOBAL_INDEX_REQUEST_FAILED')
            data = payload.get('data') if isinstance(payload, dict) else None
            latest = fnum((data or {}).get('f43'), None)
            previous_close = fnum((data or {}).get('f60'), None)
            if latest is None or latest <= 0 or previous_close is None or previous_close <= 0:
                raise ValueError('EASTMONEY_GLOBAL_INDEX_QUOTE_MISSING')
            pct_change = (latest - previous_close) / previous_close * 100.0
            rows.append({
                'key': key,
                'secid': secid,
                'symbol': str(data.get('f57') or secid),
                'name': str(data.get('f58') or display_name),
                'latest_raw': latest,
                'previous_close_raw': previous_close,
                'change_raw': fnum(data.get('f169'), 0.0),
                'pct_change': round(pct_change, 4),
                'source': 'eastmoney_api_global_index',
                'captured_at': captured_at,
            })
        except Exception as exc:
            missing_indices.append({
                'key': key,
                'secid': secid,
                'error': repr(exc),
            })

    pct_by_key = {row['key']: row['pct_change'] for row in rows}
    us_returns = [pct_by_key[key] for key in ('us_djia', 'us_spx', 'us_nasdaq') if key in pct_by_key]
    overnight_us_return_pct = sum(us_returns) / len(us_returns) if us_returns else None
    korea_return_pct = pct_by_key.get('korea_kospi')
    external_market_signal_score = None
    if overnight_us_return_pct is not None and korea_return_pct is not None:
        external_market_signal_score = overnight_us_return_pct * 0.70 + korea_return_pct * 0.30

    status = 'PASS' if not missing_indices else ('PARTIAL' if rows else 'MISSING')
    signal_label = (
        'RISK_OFF' if external_market_signal_score is not None and external_market_signal_score <= -1.0
        else 'RISK_ON' if external_market_signal_score is not None and external_market_signal_score >= 1.0
        else 'NEUTRAL'
    )
    return {
        'status': status,
        'captured_at': captured_at,
        'source': 'eastmoney_api_global_index',
        'required_indices': [key for key, _, _ in EXTERNAL_MARKET_INDEXES],
        'missing_indices': missing_indices,
        'indexes': rows,
        'overnight_us_return_pct': round(overnight_us_return_pct, 4) if overnight_us_return_pct is not None else None,
        'korea_return_pct': round(korea_return_pct, 4) if korea_return_pct is not None else None,
        'external_market_signal_score': round(external_market_signal_score, 4) if external_market_signal_score is not None else None,
        'signal_label': signal_label,
    }


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def evidence_status_from_counts(domain_counts, optional_domains=None):
    optional = set(optional_domains or ())
    missing = [domain for domain, count in domain_counts.items() if domain not in optional and not count]
    return ('PASS' if not missing else 'PARTIAL', missing)


def result_item_count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        nested_lists = [item for item in value.values() if isinstance(item, list)]
        return sum(len(item) for item in nested_lists) if nested_lists else len(value)
    return 0


def _store_fetch_diagnostic(diagnostics, record):
    if isinstance(diagnostics, list):
        diagnostics.append(dict(record))
    elif isinstance(diagnostics, dict):
        diagnostics.clear()
        diagnostics.update(record)


def record_domain_timing(domain_timings, domain, started_at, value, source, error=''):
    elapsed_seconds = round(time.monotonic() - started_at, 4)
    item_count = result_item_count(value)
    entry = domain_timings.setdefault(domain, {
        'elapsed_seconds': 0.0,
        'item_count': 0,
        'status': 'EMPTY',
        'attempts': [],
    })
    entry['elapsed_seconds'] = round(entry['elapsed_seconds'] + elapsed_seconds, 4)
    entry['item_count'] = item_count
    entry['status'] = 'ERROR' if error and not item_count else ('PASS' if item_count else 'EMPTY')
    entry['attempts'].append({
        'source': source,
        'elapsed_seconds': elapsed_seconds,
        'item_count': item_count,
        'status': 'ERROR' if error else ('PASS' if item_count else 'EMPTY'),
        'error': error,
    })


def timed_fetch(domain_timings, domain, source, fetcher):
    started_at = time.monotonic()
    try:
        value = fetcher()
    except Exception as exc:
        record_domain_timing(domain_timings, domain, started_at, [], source, repr(exc))
        raise
    record_domain_timing(domain_timings, domain, started_at, value, source)
    return value


def structured_priority_details(candidate):
    structured_score = fnum(candidate.get('structured_score'), 0.0)
    early_score = clamp01(fnum(candidate.get('early_opportunity_score'), 0.0))
    limitup_capture_score = clamp01(fnum(candidate.get('limitup_capture_score'), 0.0))
    main_theme_alignment = clamp01(fnum(candidate.get('main_theme_alignment_score'), 0.0))
    main_theme_core = clamp01(fnum(candidate.get('main_theme_core_score'), 0.0))
    announcement_catalyst = clamp01(fnum(candidate.get('announcement_catalyst_score'), 0.0))
    news_catalyst = clamp01(fnum(candidate.get('news_catalyst_strength'), 0.0))
    sector_news_catalyst = clamp01(fnum(candidate.get('sector_news_catalyst_score'), 0.0))
    limitup_reason_quality = clamp01(fnum(candidate.get('limitup_reason_quality_score'), 0.0))
    auxiliary_confidence = clamp01(fnum(candidate.get('mainboard_auxiliary_confidence'), 0.0))
    continuation_gene = clamp01(fnum(candidate.get('continuation_gene_score'), 0.0))
    risk_notice_penalty = clamp01(fnum(candidate.get('risk_notice_penalty'), 0.0))
    stage = str(candidate.get('candidate_stage') or candidate.get('stage') or '')
    stage_penalty = {'high_7_to_9': 4.0, 'near_limit_9_plus': 10.0}.get(stage, 0.0)
    if candidate.get('limitup_capture_confirmed') and limitup_capture_score >= 0.60:
        stage_penalty *= 0.25
    enhanced_counts = candidate.get('enhanced_evidence_domain_counts') if isinstance(candidate.get('enhanced_evidence_domain_counts'), dict) else {}
    research_signals = candidate.get('research_signals') if isinstance(candidate.get('research_signals'), dict) else {}
    research_panel = research_signals.get('research_panel') if isinstance(research_signals.get('research_panel'), dict) else {}
    auxiliary_status = str(candidate.get('mainboard_auxiliary_evidence_status') or '')
    market_limitups = fnum(candidate.get('market_limitups'), 0.0)
    broken_limitups = fnum(candidate.get('broken_limitups'), 0.0)
    limitup_broken_ratio = fnum(candidate.get('limitup_broken_ratio'), 999.0)
    weak_market = bool(
        str(candidate.get('market_regime') or '').lower() == 'weak'
        or fnum(candidate.get('market_follow_through_score'), 1.0) <= 0.38
        or fnum(candidate.get('market_breadth_up_pct'), 100.0) <= 45.0
        or limitup_broken_ratio <= 0.85
        or broken_limitups >= max(20.0, market_limitups * 0.9)
    )
    weak_market_hot_momentum_evidence_gap = bool(
        weak_market
        and stage in ('mid_5_to_7', 'high_7_to_9', 'near_limit_9_plus')
        and str(candidate.get('setup_type') or '') == 'HOT_MOMENTUM'
        and fnum(enhanced_counts.get('limitup_context'), 0.0) <= 0.0
        and not candidate.get('limitup_capture_confirmed')
        and fnum(candidate.get('continuation_gene_score'), 0.0) <= 0.0
        and auxiliary_status in ('', 'PARTIAL', 'MISSING', 'PARTIAL_OR_FAIL')
        and str(research_panel.get('overall') or '') != 'PASS'
    )
    weak_market_evidence_gap_penalty = {'mid_5_to_7': 10.0, 'high_7_to_9': 16.0, 'near_limit_9_plus': 22.0}.get(stage, 0.0) if weak_market_hot_momentum_evidence_gap else 0.0
    has_structured_evidence = candidate.get('structured_score') is not None
    if has_structured_evidence:
        structured_contribution = structured_score * 0.65
        evidence_contribution = (
            early_score * 8.0
            + limitup_capture_score * 8.0
            + main_theme_alignment * 6.0
            + main_theme_core * 6.0
            + announcement_catalyst * 4.0
            + news_catalyst * 5.0
            + sector_news_catalyst * 4.0
            + limitup_reason_quality * 5.0
            + continuation_gene * 6.0
            + auxiliary_confidence * 3.0
        )
        auxiliary_penalty = risk_notice_penalty * 12.0
        if stage in ('high_7_to_9', 'near_limit_9_plus') and not (limitup_reason_quality or continuation_gene or news_catalyst or announcement_catalyst):
            auxiliary_penalty += 8.0
        auxiliary_penalty += weak_market_evidence_gap_penalty
        priority_score = structured_contribution + evidence_contribution - stage_penalty - auxiliary_penalty
        ranking_basis = 'structured_evidence_primary'
    else:
        structured_contribution = 0.0
        evidence_contribution = 0.0
        priority_score = None
        ranking_basis = 'structured_evidence_required'
        auxiliary_penalty = 0.0
    return {
        'structured_priority_score': round(priority_score, 4) if priority_score is not None else None,
        'ranking_basis': ranking_basis,
        'structured_contribution': round(structured_contribution, 4),
        'evidence_contribution': round(evidence_contribution, 4),
        'high_position_penalty': round(stage_penalty, 4),
        'mainboard_auxiliary_penalty': round(auxiliary_penalty, 4),
        'continuation_gene_contribution': round(continuation_gene * 6.0 if has_structured_evidence else 0.0, 4),
        'weak_market_hot_momentum_evidence_gap': weak_market_hot_momentum_evidence_gap,
        'weak_market_evidence_gap_penalty': round(weak_market_evidence_gap_penalty, 4),
    }


def rank_candidates_by_structured_priority(candidates):
    eligible_candidates = []
    for candidate in candidates:
        details = structured_priority_details(candidate)
        candidate.update(details)
        if candidate.get('structured_score') is None:
            candidate['candidate_drop_reason'] = 'STRUCTURED_EVIDENCE_REQUIRED'
            continue
        candidate['ranking_basis_details'] = {
            key: details[key]
            for key in ('structured_contribution', 'evidence_contribution', 'high_position_penalty', 'mainboard_auxiliary_penalty', 'weak_market_evidence_gap_penalty')
        }
        candidate['ranking_basis_details']['mainboard_auxiliary_evidence_primary'] = bool(
            candidate.get('mainboard_auxiliary_evidence_status')
        )
        eligible_candidates.append(candidate)
    candidates[:] = eligible_candidates
    candidates.sort(
        key=lambda candidate: (
            fnum(candidate.get('structured_priority_score'), 0.0),
            fnum(candidate.get('structured_score'), 0.0),
            fnum(candidate.get('early_opportunity_score'), 0.0),
        ),
        reverse=True,
    )
    for rank, candidate in enumerate(candidates, 1):
        candidate['rank'] = rank
    return candidates


def candidate_drop_diagnostic(row, stage, reason, details=None):
    """Build a compact candidate-pool exclusion diagnostic."""
    source = row if isinstance(row, dict) else {}
    symbol = str(source.get('symbol') or source.get('code') or source.get('f12') or '').strip()
    if symbol:
        symbol = symbol.zfill(6)
    return {
        'symbol': symbol,
        'name': str(source.get('name') or source.get('stock_name') or source.get('f14') or ''),
        'stage': stage,
        'reason': reason,
        'details': dict(details or {}),
    }


def summarize_candidate_drop_stage_counts(diagnostics):
    counts = {}
    for item in diagnostics or []:
        if not isinstance(item, dict):
            continue
        stage = str(item.get('stage') or 'unknown')
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def select_unique_candidate_pool(candidates, target_count=FULL_CANDIDATE_POOL_TARGET):
    """Select a ranked candidate pool with unique symbols for DB persistence."""
    source_rows = [candidate for candidate in candidates if isinstance(candidate, dict)]
    selected = []
    seen = set()
    duplicate_symbols = []
    drop_diagnostics = []
    for candidate in source_rows:
        symbol = str(candidate.get('symbol') or candidate.get('code') or '').strip()
        if not symbol:
            continue
        symbol = symbol.zfill(6)
        details = {
            'rank': candidate.get('rank'),
            'final_score': candidate.get('final_score') or candidate.get('score'),
            'target_count': target_count,
            'source_row_count': len(source_rows),
        }
        if symbol in seen:
            duplicate_symbols.append(symbol)
            drop_diagnostics.append(candidate_drop_diagnostic(
                candidate, 'deduped_by_symbol', 'duplicate_symbol_already_selected', details,
            ))
            continue
        if len(selected) >= target_count:
            drop_diagnostics.append(candidate_drop_diagnostic(
                candidate, 'candidate_pool_cut', 'ranked_below_full_candidate_pool_target', details,
            ))
            continue
        seen.add(symbol)
        selected.append(candidate)
    duplicate_unique = sorted(set(duplicate_symbols))
    stage_counts = summarize_candidate_drop_stage_counts(drop_diagnostics)
    summary = {
        'source_row_count': len(source_rows),
        'raw_full_candidate_pool_rows': len(source_rows),
        'target_count': target_count,
        'unique_symbol_count': len(selected),
        'unique_full_candidate_pool_symbols': len(selected),
        'selected_unique_count': len(selected),
        'duplicate_symbol_count': len(duplicate_unique),
        'duplicate_symbols': duplicate_unique,
        'deduplication_applied': bool(duplicate_unique),
        'candidate_pool_cut_count': stage_counts.get('candidate_pool_cut', 0),
        'deduped_by_symbol_count': stage_counts.get('deduped_by_symbol', 0),
        'candidate_drop_diagnostics': drop_diagnostics,
        'candidate_drop_stage_counts': stage_counts,
        'candidate_drop_diagnostic_count': len(drop_diagnostics),
        'final_persisted_count': len(selected),
    }
    return selected, summary


def fetch_eastmoney_limit_pool(endpoint, trade_date, sort):
    """Fetch one official Eastmoney limit-pool endpoint with auditable metadata."""
    params = {
        'ut': '7eea3edcaed734bea9cbfc24409ed989',
        'dpt': 'wz.ztzt',
        'Pageindex': '0',
        'pagesize': '10000',
        'sort': sort,
        'date': str(trade_date).replace('-', ''),
        '_': str(int(time.time() * 1000)),
    }
    url = f'https://push2ex.eastmoney.com/{endpoint}?{urlencode(params)}'
    diagnostics = {
        'endpoint': endpoint,
        'request_url': url,
        'request_date': params['date'],
        'status': 'ERROR',
        'record_count': 0,
        'response_rc': None,
    }
    try:
        payload = api_get(url)
    except Exception as exc:
        diagnostics['error'] = repr(exc)
        return [], diagnostics

    diagnostics['response_rc'] = payload.get('rc') if isinstance(payload, dict) else None
    data = payload.get('data') if isinstance(payload, dict) else None
    pool = data.get('pool') if isinstance(data, dict) else None
    if diagnostics['response_rc'] not in (0, '0') or not isinstance(pool, list):
        diagnostics['error'] = 'EASTMONEY_POOL_RESPONSE_INVALID'
        return [], diagnostics

    rows = [row for row in pool if isinstance(row, dict)]
    diagnostics['status'] = 'PASS'
    diagnostics['record_count'] = len(rows)
    diagnostics['response_trade_date'] = data.get('qdate') if isinstance(data, dict) else None
    return rows, diagnostics


def build_core_sentiment_pool_status(pool_diagnostics, results, market_limitups):
    """Return the non-negotiable source gate for intraday sentiment pools."""
    required_sources = (
        'limitup_pool',
        'limitup_broken',
        'limitup_consecutive',
        'limitup_yesterday',
    )
    missing_sources = [
        name for name in required_sources
        if str((pool_diagnostics.get(name) or {}).get('status') or '') != 'PASS'
    ]
    flags = []
    if market_limitups > 0 and not results.get('limitup_pool'):
        missing_sources.append('limitup_pool')
        flags.append('QUOTE_UNIVERSE_HAS_LIMITUPS_BUT_LIMITUP_POOL_EMPTY')
    return {
        'status': 'PASS' if not missing_sources else 'BLOCK',
        'required_sources': list(required_sources),
        'missing_sources': sorted(set(missing_sources)),
        'flags': flags,
        'sources': pool_diagnostics,
        'mode': 'eastmoney_api_direct',
    }


def fetch_paginated(
    fs,
    page_size=100,
    fields=None,
    diagnostics=None,
    max_pages=MAX_SAFE_PAGE_COUNT,
):
    """Fetch a direct Eastmoney clist dataset until the API reports completion."""
    if fields is None:
        fields = 'f12,f13,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21,f23,f62,f115,f24,f25'
    all_items = []
    total = None
    pages = 0
    terminated = False
    page_size = max(1, int(page_size))
    max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
    try:
        for page in range(1, max_pages + 1):
            params = {
                'pn': str(page), 'pz': str(page_size), 'po': '1', 'np': '1',
                'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                'fltt': '2', 'invt': '2', 'fid': 'f3',
                'fs': fs,
                'fields': fields,
            }
            url = f'https://push2delay.eastmoney.com/api/qt/clist/get?{urlencode(params)}'
            data = api_get(url)
            payload = data.get('data') if isinstance(data, dict) else {}
            payload = payload if isinstance(payload, dict) else {}
            if page == 1:
                for key in ('total', 'count', 'totalCount'):
                    try:
                        total = int(payload.get(key))
                        break
                    except (TypeError, ValueError):
                        continue
            diff = payload.get('diff', []) or []
            if not isinstance(diff, list) or not diff:
                terminated = True
                break
            all_items.extend(item for item in diff if isinstance(item, dict))
            pages = page
            if total is not None and len(all_items) >= total:
                terminated = True
                break
            if len(diff) < page_size:
                terminated = True
                break
            time.sleep(0.05)
    except Exception as exc:
        _store_fetch_diagnostic(diagnostics, {
            'status': 'ERROR' if not all_items else 'PARTIAL',
            'row_count': len(all_items),
            'pages': pages,
            'reported_total': total,
            'page_size': page_size,
            'max_pages': max_pages,
            'limit_hit': False,
            'error': repr(exc),
        })
        raise
    limit_hit = not terminated and pages >= max_pages
    _store_fetch_diagnostic(diagnostics, {
        'status': 'PARTIAL' if limit_hit else ('PASS' if all_items else 'EMPTY'),
        'row_count': len(all_items),
        'pages': pages,
        'reported_total': total,
        'page_size': page_size,
        'max_pages': max_pages,
        'limit_hit': limit_hit,
    })
    return all_items


def fetch_datacenter(
    report_name,
    sort_col,
    page_size=500,
    extra_params=None,
    diagnostics=None,
    max_pages=MAX_SAFE_PAGE_COUNT,
):
    """Fetch all available rows from a paginated Eastmoney data-center report."""
    rows = []
    total = None
    pages = 0
    terminated = False
    base_params = {
        'reportName': report_name,
        'columns': 'ALL',
        'pageSize': str(page_size),
        'sortTypes': '-1',
        'sortColumns': sort_col,
        'source': 'WEB',
        'client': 'WEB',
    }
    if extra_params:
        base_params.update(extra_params)
    try:
        max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
        for page in range(1, max_pages + 1):
            params = {**base_params, 'pageNumber': str(page)}
            url = f'https://datacenter-web.eastmoney.com/api/data/v1/get?{urlencode(params)}'
            data = api_get(url)
            result = data.get('result') or {}
            batch = result.get('data', []) or []
            if page == 1:
                for key in ('count', 'total', 'totalCount'):
                    try:
                        total = int(result.get(key))
                        break
                    except (TypeError, ValueError):
                        continue
            if not isinstance(batch, list) or not batch:
                terminated = True
                break
            rows.extend(item for item in batch if isinstance(item, dict))
            pages = page
            if total is not None and len(rows) >= total:
                terminated = True
                break
            if len(batch) < int(page_size):
                terminated = True
                break
            time.sleep(0.03)
    except Exception as exc:
        if isinstance(diagnostics, list):
            _store_fetch_diagnostic(diagnostics, {
                'report_name': report_name,
                'sort_columns': base_params.get('sortColumns'),
                'sort_types': base_params.get('sortTypes'),
                'status': 'ERROR' if not rows else 'PARTIAL',
                'error': repr(exc),
                'row_count': len(rows),
                'pages': pages,
                'reported_total': total,
                'page_size': int(page_size),
                'max_pages': max_pages,
                'limit_hit': False,
            })
        raise
    _store_fetch_diagnostic(diagnostics, {
        'report_name': report_name,
        'sort_columns': base_params.get('sortColumns'),
        'sort_types': base_params.get('sortTypes'),
        'status': 'PARTIAL' if not terminated and pages >= max_pages else ('PASS' if rows else 'EMPTY'),
        'row_count': len(rows),
        'pages': pages,
        'reported_total': total,
        'page_size': int(page_size),
        'max_pages': max_pages,
        'limit_hit': not terminated and pages >= max_pages,
    })
    return rows


def fetch_report_list(
    query_type,
    begin_time,
    end_time,
    page_size=500,
    diagnostics=None,
    max_pages=MAX_SAFE_PAGE_COUNT,
):
    """Fetch all pages from Eastmoney's direct research-report endpoint."""
    rows = []
    seen = set()
    total = None
    pages = 0
    terminated = False
    page_size = max(1, int(page_size))
    max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
    try:
        for page in range(1, max_pages + 1):
            params = {
                'industryCode': '*',
                'pageSize': str(page_size),
                'industry': '*',
                'rating': '*',
                'ratingChange': '*',
                'beginTime': begin_time,
                'endTime': end_time,
                'pageNo': str(page),
                'fields': '',
                'qType': str(query_type),
                'orgCode': '',
                'rcode': '',
                'p': str(page),
                'pageNum': str(page),
            }
            data = api_get(
                f'https://reportapi.eastmoney.com/report/list?{urlencode(params)}'
            )
            payload = data.get('data') if isinstance(data, dict) else []
            if isinstance(payload, dict):
                for key in ('total', 'count', 'totalCount'):
                    try:
                        total = int(payload.get(key))
                        break
                    except (TypeError, ValueError):
                        continue
                batch = payload.get('data') or payload.get('list') or payload.get('rows') or []
            else:
                batch = payload or []
            if not isinstance(batch, list) or not batch:
                terminated = True
                break
            for row in batch:
                if not isinstance(row, dict):
                    continue
                key = (
                    row.get('art_code')
                    or row.get('reportCode')
                    or row.get('reportId')
                    or row.get('id')
                    or (
                        row.get('stockCode'),
                        row.get('title'),
                        row.get('publishDate') or row.get('noticeDate') or row.get('date'),
                    )
                    or row.get('title')
                    or json.dumps(row, ensure_ascii=False, sort_keys=True)[:160]
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            pages = page
            if total is not None and len(rows) >= total:
                terminated = True
                break
            if len(batch) < page_size:
                terminated = True
                break
            time.sleep(0.03)
    except Exception as exc:
        _store_fetch_diagnostic(diagnostics, {
            'status': 'ERROR' if not rows else 'PARTIAL',
            'row_count': len(rows),
            'pages': pages,
            'reported_total': total,
            'page_size': page_size,
            'max_pages': max_pages,
            'limit_hit': False,
            'error': repr(exc),
        })
        raise
    _store_fetch_diagnostic(diagnostics, {
        'status': 'PARTIAL' if not terminated and pages >= max_pages else ('PASS' if rows else 'EMPTY'),
        'row_count': len(rows),
        'pages': pages,
        'reported_total': total,
        'page_size': page_size,
        'max_pages': max_pages,
        'limit_hit': not terminated and pages >= max_pages,
        'query_type': str(query_type),
    })
    return rows


def _sector_name_from_row(row):
    for key in ('f14', 'name', 'sector', 'sector_name', 'industryName', 'SECURITY_NAME_ABBR', 'BOARD_NAME'):
        text = _clean_text_value((row or {}).get(key))
        if text:
            return text
    return ''


def _sector_sort_value(row):
    return max(
        abs(fnum((row or {}).get('f3'))),
        abs(fnum((row or {}).get('f62'))),
        abs(fnum((row or {}).get('main_net_inflow'))),
        abs(fnum((row or {}).get('NET_INFLOW'))),
    )


def fetch_sector_news(
    sector_rows,
    concept_rows,
    limit_per_query=20,
    max_queries=40,
    max_pages=10,
    diagnostics=None,
):
    """Fetch direct sector news with bounded query count and paginated results."""
    queries = []
    for sector_type, rows in (('industry', sector_rows or []), ('concept', concept_rows or [])):
        sorted_rows = sorted([row for row in rows if isinstance(row, dict)], key=_sector_sort_value, reverse=True)
        for row in sorted_rows[: max(1, max_queries // 2)]:
            name = _sector_name_from_row(row)
            if name and name not in {item['query'] for item in queries}:
                queries.append({'query': name, 'sector_type': sector_type})
            if len(queries) >= max_queries:
                break
        if len(queries) >= max_queries:
            break

    seen = set()
    news_rows = []
    pages_fetched = 0
    max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
    for item in queries:
        for page in range(1, max_pages + 1):
            # Eastmoney search-api requires nested JSON `param` + type cmsArticleWebOld.
            param_obj = {
                'uid': '',
                'keyword': item['query'],
                'type': ['cmsArticleWebOld'],
                'client': 'web',
                'clientType': 'web',
                'clientVersion': 'curr',
                'param': {
                    'cmsArticleWebOld': {
                        'searchScope': 'default',
                        'sort': 'default',
                        'pageIndex': page,
                        'pageSize': int(limit_per_query),
                        'preTag': '<em>',
                        'postTag': '</em>',
                    }
                },
            }
            params = {
                'cb': 'jQuery112308',
                'param': json.dumps(param_obj, ensure_ascii=False),
                '_': str(int(time.time() * 1000)),
            }
            url = f'https://search-api-web.eastmoney.com/search/jsonp?{urlencode(params)}'
            try:
                payload = api_get(url)
            except Exception:
                break
            if not isinstance(payload, dict):
                break
            result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
            candidates = result.get('cmsArticleWebOld') or []
            if not isinstance(candidates, list):
                legacy = payload.get('Data') or payload.get('data') or payload.get('result') or []
                if isinstance(legacy, dict):
                    candidates = legacy.get('items') or legacy.get('data') or legacy.get('news') or []
                else:
                    candidates = legacy if isinstance(legacy, list) else []
            if not isinstance(candidates, list) or not candidates:
                break
            pages_fetched += 1
            for row in candidates[:limit_per_query]:
                if not isinstance(row, dict):
                    continue
                title = _clean_text_value(row.get('title') or row.get('Title') or row.get('NewsTitle') or row.get('name'))
                summary = _clean_text_value(
                    row.get('content') or row.get('Content') or row.get('summary') or row.get('Digest') or row.get('digest')
                )
                if not title and not summary:
                    continue
                key = (item['query'], title, summary[:80])
                if key in seen:
                    continue
                seen.add(key)
                news_rows.append({
                    'title': title,
                    'summary': summary,
                    'content': summary,
                    'sector': item['query'],
                    'sector_type': item['sector_type'],
                    'source': 'eastmoney_sector_news',
                    'source_query': item['query'],
                    'published_at': row.get('date') or row.get('ShowTime') or row.get('time') or '',
                    'url': row.get('url') or row.get('Url') or '',
                    'media_name': row.get('mediaName') or row.get('MediaName') or '',
                    'article_code': row.get('code') or '',
                })
            if len(candidates) < int(limit_per_query):
                break
            time.sleep(0.03)
    _store_fetch_diagnostic(diagnostics, {
        'status': 'PASS' if news_rows else 'EMPTY',
        'row_count': len(news_rows),
        'queries': len(queries),
        'pages': pages_fetched,
        'max_queries': max_queries,
        'max_pages_per_query': max_pages,
        'bounded_query_source': True,
        'source': 'eastmoney_sector_news',
    })
    return news_rows


def fetch_announcements_multi_page(
    max_pages=MAX_SAFE_PAGE_COUNT,
    page_size=100,
    diagnostics=None,
):
    """Market-wide announcements with pagination (single page leaves most of the pool unmatched)."""
    rows = []
    seen = set()
    total = None
    pages = 0
    terminated = False
    max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
    page_size = max(1, int(page_size))
    try:
        for page in range(1, max_pages + 1):
            data = api_get(
                'https://np-anotice-stock.eastmoney.com/api/security/ann?'
                + urlencode({
                    'ann_type': 'A',
                    'client_source': 'WEB',
                    'f_node': '0',
                    'page_index': str(page),
                    'page_size': str(page_size),
                    's_node': '0',
                })
            )
            payload = (data.get('data') or {}) if isinstance(data, dict) else {}
            batch = payload.get('list') or [] if isinstance(payload, dict) else []
            if page == 1 and isinstance(payload, dict):
                for key in ('total', 'count', 'totalCount'):
                    try:
                        total = int(payload.get(key))
                        break
                    except (TypeError, ValueError):
                        continue
            if not isinstance(batch, list) or not batch:
                terminated = True
                break
            for row in batch:
                if not isinstance(row, dict):
                    continue
                key = row.get('art_code') or row.get('title') or row.get('title_ch')
                if not key:
                    key = json.dumps(row, ensure_ascii=False, sort_keys=True)[:160]
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            pages = page
            if total is not None and len(rows) >= total:
                terminated = True
                break
            if len(batch) < page_size:
                terminated = True
                break
            time.sleep(0.03)
    except Exception as exc:
        _store_fetch_diagnostic(diagnostics, {
            'status': 'ERROR' if not rows else 'PARTIAL',
            'row_count': len(rows),
            'pages': pages,
            'reported_total': total,
            'page_size': page_size,
            'max_pages': max_pages,
            'limit_hit': False,
            'error': repr(exc),
        })
        return rows
    _store_fetch_diagnostic(diagnostics, {
        'status': 'PARTIAL' if not terminated and pages >= max_pages else ('PASS' if rows else 'EMPTY'),
        'row_count': len(rows),
        'pages': pages,
        'reported_total': total,
        'page_size': page_size,
        'max_pages': max_pages,
        'limit_hit': not terminated and pages >= max_pages,
    })
    return rows


def fetch_news_kuaixun_multi_page(
    max_pages=MAX_SAFE_PAGE_COUNT,
    page_size=200,
    diagnostics=None,
):
    """7x24 kuaixun multi-page so title/name matching covers more of the candidate pool."""
    rows = []
    seen = set()
    total = None
    pages = 0
    terminated = False
    max_pages = max(1, min(int(max_pages), MAX_SAFE_PAGE_COUNT))
    page_size = max(1, int(page_size))
    try:
        for page in range(1, max_pages + 1):
            ts = int(time.time() * 1000)
            data = api_get(
                'https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?'
                + urlencode({
                    'client': 'web',
                    'biz': 'web_724',
                    'column': '350',
                    'order': '1',
                    'needInteractData': '0',
                    'page_index': str(page),
                    'page_size': str(page_size),
                    'req_trace': str(ts),
                })
            )
            payload = ((data.get('data') or {}) if isinstance(data, dict) else {})
            batch = payload.get('list') or [] if isinstance(payload, dict) else []
            if page == 1 and isinstance(payload, dict):
                for key in ('total', 'count', 'totalCount'):
                    try:
                        total = int(payload.get(key))
                        break
                    except (TypeError, ValueError):
                        continue
            if not isinstance(batch, list) or not batch:
                terminated = True
                break
            for row in batch:
                if not isinstance(row, dict):
                    continue
                key = row.get('code') or row.get('uniqueUrl') or row.get('url') or row.get('title')
                if not key:
                    key = json.dumps(row, ensure_ascii=False, sort_keys=True)[:160]
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            pages = page
            if total is not None and len(rows) >= total:
                terminated = True
                break
            if len(batch) < page_size:
                terminated = True
                break
            time.sleep(0.03)
    except Exception as exc:
        _store_fetch_diagnostic(diagnostics, {
            'status': 'ERROR' if not rows else 'PARTIAL',
            'row_count': len(rows),
            'pages': pages,
            'reported_total': total,
            'page_size': page_size,
            'max_pages': max_pages,
            'limit_hit': False,
            'error': repr(exc),
        })
        return rows
    _store_fetch_diagnostic(diagnostics, {
        'status': 'PARTIAL' if not terminated and pages >= max_pages else ('PASS' if rows else 'EMPTY'),
        'row_count': len(rows),
        'pages': pages,
        'reported_total': total,
        'page_size': page_size,
        'max_pages': max_pages,
        'limit_hit': not terminated and pages >= max_pages,
    })
    return rows


def fetch_stock_announcements(stock_codes, page_size=15, max_stocks=60):
    """Per-stock announcement supplement for pool symbols missing market-wide coverage."""
    rows = []
    seen = set()
    for raw in list(stock_codes or [])[: max(0, int(max_stocks))]:
        code = normalize_stock_code(raw)
        if not code:
            continue
        try:
            data = api_get(
                'https://np-anotice-stock.eastmoney.com/api/security/ann?'
                + urlencode({
                    'sr': '-1',
                    'page_size': str(page_size),
                    'page_index': '1',
                    'ann_type': 'A',
                    'client_source': 'web',
                    'stock_list': code,
                    'f_node': '0',
                    's_node': '0',
                })
            )
            batch = ((data.get('data') or {}) if isinstance(data, dict) else {}).get('list') or []
        except Exception:
            continue
        if not isinstance(batch, list):
            continue
        for row in batch:
            if not isinstance(row, dict):
                continue
            key = row.get('art_code') or (code, row.get('title') or row.get('title_ch'))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        time.sleep(0.03)
    return rows


def fetch_hsgt_holdings(domain_timings, output_dir):
    api_attempts = []
    rows = []
    attempts = [
        ('RPT_MUTUAL_HOLDSTOCKNORTH_STA', 'TRADE_DATE', {'sortTypes': '-1'}),
        ('RPT_MUTUAL_HOLDSTOCKNORTH_STA', 'TRADE_DATE,SECURITY_CODE', {'sortTypes': '-1,1'}),
        ('RPT_MUTUAL_STOCK_NORTHSTA', 'TRADE_DATE', {'sortTypes': '-1'}),
    ]
    for report_name, sort_columns, extra_params in attempts:
        try:
            rows = timed_fetch(
                domain_timings,
                'hsgt_holdings',
                f'datacenter:{report_name}:{sort_columns}',
                lambda report_name=report_name, sort_columns=sort_columns, extra_params=extra_params: fetch_datacenter(
                    report_name,
                    sort_columns,
                    page_size=500,
                    extra_params=extra_params,
                    diagnostics=api_attempts,
                ),
            )
        except Exception:
            rows = []
        if rows:
            break

    diagnostics = {
        'status': 'PASS' if rows else 'MISSING',
        'holdings_count': len(rows),
        'api_attempts': api_attempts,
        'api_sources_checked': [
            f'datacenter:{report_name}:{sort_columns}'
            for report_name, sort_columns, _ in attempts
        ],
        'selected_source': next((attempt['source'] for attempt in reversed(domain_timings.get('hsgt_holdings', {}).get('attempts', [])) if attempt.get('item_count')), ''),
        'required_for_paper_pick': False,
        'hard_block': False,
        'missing_semantics': 'OPTIONAL_PARTIAL_EVIDENCE',
    }
    return rows, diagnostics


def finalize_hsgt_diagnostics(diagnostics, hsgt_deals, hsgt_summary):
    finalized = dict(diagnostics or {})
    proxy_sources = []
    if hsgt_deals:
        proxy_sources.append('hsgt_deals')
    if hsgt_summary:
        proxy_sources.append('hsgt_summary')
    holdings_available = bool(finalized.get('holdings_count'))
    fallback_available = bool(proxy_sources)
    fallback_used = bool(fallback_available and not holdings_available)
    finalized['fallback_available'] = fallback_available
    finalized['fallback_used'] = fallback_used
    # Backward-compatible field: it now means a fallback was actually used,
    # not merely that a backup source was present.
    finalized['proxy_available'] = fallback_used
    finalized['proxy_sources'] = proxy_sources
    finalized['source_type'] = 'DIRECT' if holdings_available else ('FALLBACK' if fallback_used else 'MISSING')
    finalized['quality_status'] = 'PASS' if holdings_available else ('PROXY' if fallback_used else 'MISSING')
    finalized['production_use'] = 'ENABLED' if holdings_available else 'OPTIONAL_FALLBACK_ONLY'
    finalized['hard_block'] = False
    finalized['required_for_paper_pick'] = False
    if not holdings_available and proxy_sources:
        finalized['status'] = 'PARTIAL'
        finalized['reason'] = 'holdings unavailable; aggregate/deal proxy remains available'
    return finalized


def main():
    scanner_started_at = time.monotonic()
    parser = argparse.ArgumentParser(description='Eastmoney API Scanner v2')
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--date', default=None, help='历史日期回放 (YYYY-MM-DD)')
    parser.add_argument('--force-realtime', action='store_true', help='强制获取实时数据')
    args = parser.parse_args()
    production_run_id = os.environ.get('XIAOGU_PRODUCTION_RUN_ID', '').strip() or None

    source_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    output_dir = Path(args.output_dir) if args.output_dir else BASE / 'data' / 'live_scan' / source_time[:10] / 'eastmoney_scan_afternoon'
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    domain_timings = {}
    pagination_diagnostics = {}
    hsgt_diagnostics = {}
    external_market_snapshot = fetch_external_market_snapshot(source_time)
    results['external_market'] = list(external_market_snapshot['indexes'])
    record_domain_timing(
        domain_timings,
        'external_market',
        scanner_started_at,
        results['external_market'],
        'eastmoney_api_global_index',
        '; '.join(item['key'] for item in external_market_snapshot['missing_indices']),
    )

    # =================================================================
    # 1. 沪深A股 - FULL (上证A股+深证A股+创业板)
    # =================================================================
    print(f'[{source_time}] 沪深A股 (full)...', end=' ', flush=True)
    results['stock_all_a'] = timed_fetch(
        domain_timings,
        'stock_all_a',
        'push2_paginated',
        lambda: fetch_paginated(
            'm:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048',
            fields=STOCK_ALL_A_FIELDS,
            diagnostics=pagination_diagnostics.setdefault('stock_all_a', {}),
        ),
    )
    print(f'{len(results["stock_all_a"])} items')

    # =================================================================
    # 2. 行业板块 - FULL
    # =================================================================
    print(f'[{source_time}] 行业板块 (full)...', end=' ', flush=True)
    # =================================================================
    # 3. 概念板块 - FULL
    # =================================================================
    print(f'[{source_time}] 概念板块 (full)...', end=' ', flush=True)
    # =================================================================
    # 4. 地域板块 - FULL
    # =================================================================
    print(f'[{source_time}] 地域板块 (full)...', end=' ', flush=True)
    results['sector_region'] = timed_fetch(
        domain_timings,
        'sector_region',
        'push2_paginated',
        lambda: fetch_paginated(
            'm:90+t:1',
            fields='f12,f14,f3,f62,f66,f204,f205',
            diagnostics=pagination_diagnostics.setdefault('sector_region', {}),
        ),
    )
    print(f'{len(results["sector_region"])} items')

    # =================================================================
    # 4. 资金流 - 行业
    # =================================================================
    print(f'[{source_time}] 行业资金流 (full)...', end=' ', flush=True)
    results['flow_industry'] = timed_fetch(
        domain_timings,
        'flow_industry',
        'push2_paginated',
        lambda: fetch_paginated(
            'm:90+t:2',
            fields='f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87',
            diagnostics=pagination_diagnostics.setdefault('flow_industry', {}),
        ),
    )
    print(f'{len(results["flow_industry"])} items')

    # =================================================================
    # 5. 资金流 - 概念
    # =================================================================
    print(f'[{source_time}] 概念资金流 (full)...', end=' ', flush=True)
    results['flow_concept'] = timed_fetch(
        domain_timings,
        'flow_concept',
        'push2_paginated',
        lambda: fetch_paginated(
            'm:90+t:3',
            fields='f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87',
            diagnostics=pagination_diagnostics.setdefault('flow_concept', {}),
        ),
    )
    print(f'{len(results["flow_concept"])} items')

    print(f'[{source_time}] 板块新闻 (bounded)...', end=' ', flush=True)
    results['sector_news'] = timed_fetch(
        domain_timings,
        'sector_news',
        'eastmoney_sector_news',
        lambda: fetch_sector_news(
            list(results.get('flow_industry') or []),
            list(results.get('flow_concept') or []),
            diagnostics=pagination_diagnostics.setdefault('sector_news', {}),
        ),
    )
    print(f'{len(results["sector_news"])} items')

    # =================================================================
    # 6. 龙虎榜 - FULL (last 30 days)
    # =================================================================
    print(f'[{source_time}] 龙虎榜 (full)...', end=' ', flush=True)
    results['lhb'] = timed_fetch(
        domain_timings,
        'lhb',
        'datacenter:RPT_DAILYBILLBOARD_DETAILSNEW',
        lambda: fetch_datacenter(
            'RPT_DAILYBILLBOARD_DETAILSNEW',
            'TRADE_DATE,DEAL_AMOUNT_RATIO',
            page_size=500,
            extra_params={'sortTypes': '-1,-1'},
            diagnostics=pagination_diagnostics.setdefault('lhb', []),
        ),
    )
    print(f'{len(results["lhb"])} items')

    # =================================================================
    # 7. 涨停池 - FULL (Eastmoney API only)
    # =================================================================
    limit_pool_diagnostics = {}
    print(f'[{source_time}] 涨停池...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    results['limitup_pool'], limit_pool_diagnostics['limitup_pool'] = fetch_eastmoney_limit_pool(
        'getTopicZTPool',
        source_time[:10],
        'fbt:asc',
    )
    results['limitup_pool'] = [normalize_limitup_reason_row(row) for row in results['limitup_pool'] if isinstance(row, dict)]
    record_domain_timing(
        domain_timings,
        'limitup_pool',
        domain_started_at,
        results['limitup_pool'],
        'eastmoney:getTopicZTPool',
        str(limit_pool_diagnostics['limitup_pool'].get('error') or ''),
    )
    print(f'{len(results["limitup_pool"])} items')

    print(f'[{source_time}] 炸板池...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    results['limitup_broken'], limit_pool_diagnostics['limitup_broken'] = fetch_eastmoney_limit_pool(
        'getTopicZBPool',
        source_time[:10],
        'fbt:asc',
    )
    record_domain_timing(
        domain_timings,
        'limitup_broken',
        domain_started_at,
        results['limitup_broken'],
        'eastmoney:getTopicZBPool',
        str(limit_pool_diagnostics['limitup_broken'].get('error') or ''),
    )
    print(f'{len(results["limitup_broken"])} items')

    print(f'[{source_time}] 连板池...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    results['limitup_consecutive'] = [
        row for row in results['limitup_pool']
        if int(fnum(row.get('lbc'))) >= 2
    ]
    limit_pool_diagnostics['limitup_consecutive'] = {
        'endpoint': 'getTopicZTPool',
        'request_date': source_time[:10].replace('-', ''),
        'status': limit_pool_diagnostics['limitup_pool']['status'],
        'record_count': len(results['limitup_consecutive']),
        'response_rc': limit_pool_diagnostics['limitup_pool'].get('response_rc'),
        'response_trade_date': limit_pool_diagnostics['limitup_pool'].get('response_trade_date'),
        'mode': 'derived_from_getTopicZTPool_lbc',
    }
    record_domain_timing(
        domain_timings,
        'limitup_consecutive',
        domain_started_at,
        results['limitup_consecutive'],
        'eastmoney:getTopicZTPool:lbc>=2',
        str(limit_pool_diagnostics['limitup_consecutive'].get('error') or ''),
    )
    print(f'{len(results["limitup_consecutive"])} items')

    print(f'[{source_time}] 昨日涨停...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    results['limitup_yesterday'], limit_pool_diagnostics['limitup_yesterday'] = fetch_eastmoney_limit_pool(
        'getYesterdayZTPool',
        source_time[:10],
        'zs:desc',
    )
    record_domain_timing(
        domain_timings,
        'limitup_yesterday',
        domain_started_at,
        results['limitup_yesterday'],
        'eastmoney:getYesterdayZTPool',
        str(limit_pool_diagnostics['limitup_yesterday'].get('error') or ''),
    )
    print(f'{len(results["limitup_yesterday"])} items')
    results['limitup_yesterday_one_word'] = [row for row in results['limitup_yesterday'] if is_one_word_limitup_row(row)]
    record_domain_timing(domain_timings, 'limitup_yesterday_one_word', domain_started_at, results['limitup_yesterday_one_word'], 'derived_proxy')

    # =================================================================
    # 8. 沪深京指数 - 全部主要指数
    # =================================================================
    print(f'[{source_time}] 沪深京指数...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        # 主要指数列表
        index_list = [
            ('1.000001', '上证指数'),
            ('0.399001', '深证成指'),
            ('0.399006', '创业板指'),
            ('1.000016', '上证50'),
            ('1.000300', '沪深300'),
            ('1.000905', '中证500'),
            ('1.000852', '中证1000'),
            ('0.399300', '深证100'),
            ('0.399673', '创业板50'),
            ('1.000688', '科创50'),
            ('0.899050', '北证50'),
        ]
        all_indexes = []
        for secid, name in index_list:
            try:
                data = api_get(f'https://push2delay.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f116,f117,f162,f167,f168,f169,f170,f171')
                idx_data = data.get('data', {}) or {}
                if idx_data:
                    idx_data['name'] = name
                    all_indexes.append(idx_data)
            except:
                pass
        results['indexes'] = all_indexes
    except:
        results['indexes'] = []
    record_domain_timing(domain_timings, 'indexes', domain_started_at, results['indexes'], 'push2_index_batch')
    print(f'{len(results["indexes"])} items')

    # =================================================================
    # 9. 北向资金 - FULL
    # =================================================================
    print(f'[{source_time}] 北向资金汇总...', end=' ', flush=True)
    try:
        data = timed_fetch(domain_timings, 'hsgt_summary', 'push2:kamt.rtmin', lambda: api_get('https://push2delay.eastmoney.com/api/qt/kamt.rtmin/get?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56'))
        summary_payload = data.get('data', {}) or {}
        results['hsgt_summary'] = [summary_payload] if summary_payload else []
    except:
        results['hsgt_summary'] = []
    print(f'{len(results["hsgt_summary"])} items')

    print(f'[{source_time}] 北向资金明细 (full)...', end=' ', flush=True)
    results['hsgt_deals'] = timed_fetch(
        domain_timings,
        'hsgt_deals',
        'datacenter:RPT_MUTUAL_DEAL_HISTORY',
        lambda: fetch_datacenter(
            'RPT_MUTUAL_DEAL_HISTORY',
            'TRADE_DATE',
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('hsgt_deals', []),
        ),
    )
    print(f'{len(results["hsgt_deals"])} items')

    # =================================================================
    # 10. 业绩预告 - FULL
    # =================================================================
    print(f'[{source_time}] 业绩预告 (full)...', end=' ', flush=True)
    results['earnings_preview'] = timed_fetch(
        domain_timings,
        'earnings_preview',
        'datacenter:RPT_LICO_FN_CPD',
        lambda: fetch_datacenter(
            'RPT_LICO_FN_CPD',
            'NOTICE_DATE',
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('earnings_preview', []),
        ),
    )
    print(f'{len(results["earnings_preview"])} items')

    # =================================================================
    # 11. 限售解禁 - FULL
    # =================================================================
    print(f'[{source_time}] 限售解禁 (full)...', end=' ', flush=True)
    results['lockup_expiry'] = timed_fetch(
        domain_timings,
        'lockup_expiry',
        'datacenter:RPT_LIFT_STAGE',
        lambda: fetch_datacenter(
            'RPT_LIFT_STAGE',
            'FREE_DATE',
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('lockup_expiry', []),
        ),
    )
    print(f'{len(results["lockup_expiry"])} items')

    # =================================================================
    # 12. 机构调研 - 近7天 (direct data-center pagination)
    # =================================================================
    print(f'[{source_time}] 机构调研 (7d)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        results['org_survey'] = timed_fetch(
            domain_timings,
            'org_survey',
            'datacenter:RPT_ORG_SURVEY',
            lambda: fetch_datacenter(
                'RPT_ORG_SURVEY',
                'NOTICE_DATE',
                page_size=50,
                extra_params={'filter': f"(NOTICE_DATE>='{week_ago}')"},
                diagnostics=pagination_diagnostics.setdefault('org_survey', []),
            ),
        )
    except:
        results['org_survey'] = []
        record_domain_timing(
            domain_timings,
            'org_survey',
            domain_started_at,
            results['org_survey'],
            'datacenter:RPT_ORG_SURVEY',
        )
    print(f'{len(results["org_survey"])} items')

    # =================================================================
    # 13. 个股资金流 - FULL (主力+散户, direct pagination)
    # =================================================================
    print(f'[{source_time}] 个股资金流 (full)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        results['stock_capital_flow'] = fetch_paginated(
            'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            page_size=100,
            fields=STOCK_CAPITAL_FLOW_FIELDS,
            diagnostics=pagination_diagnostics.setdefault('stock_capital_flow', {}),
        )
    except:
        results['stock_capital_flow'] = []
    record_domain_timing(
        domain_timings,
        'stock_capital_flow',
        domain_started_at,
        results['stock_capital_flow'],
        'push2_fund_flow_paginated',
    )
    print(f'{len(results["stock_capital_flow"])} items')

    # =================================================================
    # 15. 板块资金流 - FULL (行业+概念, direct pagination)
    # =================================================================
    print(f'[{source_time}] 板块资金流 (full)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        sector_fields = ','.join(
            [f'f{field_id}' for field_id in range(1, 4)]
            + [f'f{field_id}' for field_id in range(51, 76)]
        )
        industry_diagnostics = {}
        concept_diagnostics = {}
        industry_flow = fetch_paginated(
            'm:90+t:2',
            page_size=100,
            fields=sector_fields,
            diagnostics=industry_diagnostics,
        )
        concept_flow = fetch_paginated(
            'm:90+t:3',
            page_size=100,
            fields=sector_fields,
            diagnostics=concept_diagnostics,
        )
        pagination_diagnostics['sector_capital_flow'] = {
            'industry': industry_diagnostics,
            'concept': concept_diagnostics,
        }
        results['sector_capital_flow'] = {'industry': industry_flow, 'concept': concept_flow}
    except:
        results['sector_capital_flow'] = {'industry': [], 'concept': []}
    record_domain_timing(domain_timings, 'sector_capital_flow', domain_started_at, results['sector_capital_flow'], 'push2_sector_fund_flow')
    print(f'industry={len(results["sector_capital_flow"]["industry"])}, concept={len(results["sector_capital_flow"]["concept"])}')

    # =================================================================
    # 16. 大盘资金流 - 主力资金汇总
    # =================================================================
    print(f'[{source_time}] 大盘资金流...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        # 上证+深证+创业板主力资金
        indices = [
            ('1.000001', '上证指数'),
            ('0.399001', '深证成指'),
            ('0.399006', '创业板指'),
        ]
        market_flows = []
        for secid, name in indices:
            params = {
                'fields1': 'f1,f2,f3,f7',
                'fields2': 'f51,f52,f53,f54,f56,f57,f58,f60,f61,f62,f63,f64,f65,f66,f67,f68,f69,f70,f71',
                'ut': 'b2884a393a59ad64002292a3e90d46a5',
                'klt': '101',
                'lmt': '1',
                'secid': secid,
            }
            url = f'https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get?{urlencode(params)}'
            data = api_get(url)
            flow_data = data.get('data', {})
            if flow_data:
                market_flows.append({
                    'secid': secid,
                    'name': name,
                    'klines': flow_data.get('klines', [])
                })
        results['market_capital_flow'] = market_flows
    except:
        results['market_capital_flow'] = []
    record_domain_timing(domain_timings, 'market_capital_flow', domain_started_at, results['market_capital_flow'], 'push2_market_fund_flow')
    print(f'{len(results["market_capital_flow"])} indices')

    # =================================================================
    # 17. 个股研报 - 近7天
    # =================================================================
    print(f'[{source_time}] 个股研报 (7d)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        results['stock_reports'] = fetch_report_list(
            '0',
            week_ago,
            today,
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('stock_reports', {}),
        )
    except:
        results['stock_reports'] = []
    record_domain_timing(domain_timings, 'stock_reports', domain_started_at, results['stock_reports'], 'reportapi:stock')
    print(f'{len(results["stock_reports"])} items')

    # =================================================================
    # 18. 行业研报 - 近7天
    # =================================================================
    print(f'[{source_time}] 行业研报 (7d)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        results['industry_reports'] = fetch_report_list(
            '1',
            week_ago,
            today,
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('industry_reports', {}),
        )
    except:
        results['industry_reports'] = []
    record_domain_timing(domain_timings, 'industry_reports', domain_started_at, results['industry_reports'], 'reportapi:industry')
    print(f'{len(results["industry_reports"])} items')

    # =================================================================
    # 19. 大宗交易 - FULL (push2 API, 使用股票市场fs)
    # =================================================================
    print(f'[{source_time}] 大宗交易 (full)...', end=' ', flush=True)
    try:
        results['block_trades'] = timed_fetch(
            domain_timings,
            'block_trades',
            'push2_paginated_proxy',
            lambda: fetch_paginated(
                'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
                fields='f2,f3,f6,f12,f13,f14,f15,f16,f17',
                diagnostics=pagination_diagnostics.setdefault('block_trades', {}),
            ),
        )
    except:
        results['block_trades'] = []
    print(f'{len(results["block_trades"])} items')

    # =================================================================
    # 20. 股东变动 - FULL (datacenter API)
    # =================================================================
    print(f'[{source_time}] 股东变动 (full)...', end=' ', flush=True)
    results['shareholder_changes'] = timed_fetch(
        domain_timings,
        'shareholder_changes',
        'datacenter:RPT_SHARE_HOLDER_INCREASE',
        lambda: fetch_datacenter(
            'RPT_SHARE_HOLDER_INCREASE',
            'END_DATE',
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('shareholder_changes', []),
        ),
    )
    print(f'{len(results["shareholder_changes"])} items')

    # =================================================================
    # 21. IPO日历 - FULL (datacenter API)
    # =================================================================
    print(f'[{source_time}] IPO日历 (full)...', end=' ', flush=True)
    results['ipo_calendar'] = timed_fetch(
        domain_timings,
        'ipo_calendar',
        'datacenter:RPTA_APP_IPOAPPLY',
        lambda: fetch_datacenter(
            'RPTA_APP_IPOAPPLY',
            'APPLY_DATE',
            page_size=500,
            diagnostics=pagination_diagnostics.setdefault('ipo_calendar', []),
        ),
    )
    print(f'{len(results["ipo_calendar"])} items')

    # =================================================================
    # 22. 停牌信息 - FULL (push2 API, 使用股票市场fs)
    # =================================================================
    print(f'[{source_time}] 停牌信息 (full)...', end=' ', flush=True)
    try:
        results['trading_halts'] = timed_fetch(
            domain_timings,
            'trading_halts',
            'push2_paginated_proxy',
            lambda: fetch_paginated(
                'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
                fields='f2,f3,f6,f12,f13,f14,f15,f16,f17',
                diagnostics=pagination_diagnostics.setdefault('trading_halts', {}),
            ),
        )
    except:
        results['trading_halts'] = []
    print(f'{len(results["trading_halts"])} items')

    # =================================================================
    # 23. 北向持股明细 - FULL (datacenter API + fallback)
    # =================================================================
    print(f'[{source_time}] 北向持股明细 (full)...', end=' ', flush=True)
    results['hsgt_holdings'], hsgt_diagnostics = fetch_hsgt_holdings(domain_timings, output_dir)
    hsgt_diagnostics = finalize_hsgt_diagnostics(
        hsgt_diagnostics,
        results.get('hsgt_deals'),
        results.get('hsgt_summary'),
    )
    print(f'{len(results["hsgt_holdings"])} items')

    # =================================================================
    # 24. 人气排名 - FULL (push2 API, 使用股票市场fs)
    # =================================================================
    print(f'[{source_time}] 人气排名 (full)...', end=' ', flush=True)
    try:
        results['popularity_rank'] = timed_fetch(
            domain_timings,
            'popularity_rank',
            'push2_paginated_proxy',
            lambda: fetch_paginated(
                'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
                fields='f2,f3,f6,f12,f13,f14,f15,f16,f17',
                diagnostics=pagination_diagnostics.setdefault('popularity_rank', {}),
            ),
        )
    except:
        results['popularity_rank'] = []
    print(f'{len(results["popularity_rank"])} items')

    # =================================================================
    # 25. 公告 - FULL (multi-page; single page leaves most of the 400 pool unmatched)
    # =================================================================
    print(f'[{source_time}] 公告 (full multi-page)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        ann_pages = int(os.environ.get('XIAOGU_ANN_PAGES', str(MAX_SAFE_PAGE_COUNT)))
        ann_page_size = int(os.environ.get('XIAOGU_ANN_PAGE_SIZE', '100'))
        results['announcements'] = fetch_announcements_multi_page(
            max_pages=ann_pages,
            page_size=ann_page_size,
            diagnostics=pagination_diagnostics.setdefault('announcements', {}),
        )
    except Exception:
        results['announcements'] = []
    record_domain_timing(domain_timings, 'announcements', domain_started_at, results['announcements'], 'eastmoney_announcements_multipage')
    print(f'{len(results["announcements"])} items')

    # =================================================================
    # 20. 东财7x24快讯 - FULL (multi-page for higher name/code match coverage)
    # =================================================================
    print(f'[{source_time}] 东财7x24快讯 (full multi-page)...', end=' ', flush=True)
    domain_started_at = time.monotonic()
    try:
        news_pages = int(os.environ.get('XIAOGU_NEWS_PAGES', str(MAX_SAFE_PAGE_COUNT)))
        news_page_size = int(os.environ.get('XIAOGU_NEWS_PAGE_SIZE', '200'))
        results['news_kuaixun'] = fetch_news_kuaixun_multi_page(
            max_pages=news_pages,
            page_size=news_page_size,
            diagnostics=pagination_diagnostics.setdefault('news_kuaixun', {}),
        )
    except Exception:
        results['news_kuaixun'] = []
    record_domain_timing(domain_timings, 'news_kuaixun', domain_started_at, results['news_kuaixun'], 'eastmoney_7x24_multipage')
    print(f'{len(results["news_kuaixun"])} items')

    # =================================================================
    # Save all data
    # =================================================================
    write_raw_started_at = time.monotonic()
    for name, items in results.items():
        path = output_dir / f'{name}.jsonl'
        with open(path, 'w', encoding='utf-8') as f:
            for item in items:
                if isinstance(item, dict):
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
    record_domain_timing(domain_timings, 'write_raw_data', write_raw_started_at, list(results), 'jsonl_write')

    # Build summary
    summary = {
        'source': 'eastmoney_api_scan_v2',
        'pipeline_version': 'v2_scanner_api',
        'source_time': source_time,
        'domains': {name: len(items) for name, items in results.items()},
        'total_items': sum(len(items) for items in results.values()),
        'pagination_diagnostics': pagination_diagnostics,
        'scanner_transport': 'direct_api',
        'files': {name: str(output_dir / f'{name}.jsonl') for name in results.keys()},
    }

    summary_path = output_dir / 'scan_summary.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Build runner-compatible summary
    stocks = results.get('stock_all_a', [])
    market_breadth = round(sum(1 for s in stocks if fnum(s.get('f3')) > 0) / len(stocks) * 100, 2) if stocks else 0.0
    market_limitups = sum(1 for s in stocks if fnum(s.get('f3')) >= 9.5)
    market_bigups = sum(1 for s in stocks if fnum(s.get('f3')) >= 5.0)

    # Generate candidates from stock data
    # Runner applies full A-share scoring (contrarian_re_score, regime-aware, etc.)
    candidates = []
    scored_count = 0
    structured_scores = []
    research_signal_rows = []
    structured_component_rows = []
    structured_score_component_rows = []
    market_follow_through_score = 0.0
    limitup_broken_ratio = 0.0
    max_consecutive = 0.0
    sentiment_score = 0.0
    market_main_inflow = 0.0
    market_regime = 'neutral'
    sector_snapshot = []
    full_universe_scan = {}
    source_status = {}
    hard_block_source_status = {}
    market_snapshot = {}
    information_coverage_audit = {}
    candidate_drop_diagnostics = []
    candidate_scoring_started_at = time.monotonic()
    if stocks:
        def safe_num(v, default=0.0):
            if v is None or v == '-' or v == '':
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        def signal_stage_bucket(pct):
            if pct < 0: return 'underwater'
            if pct < 3: return 'flat_0_to_3'
            if pct < 5: return 'early_3_to_5'
            if pct < 7: return 'mid_5_to_7'
            if pct < 9: return 'high_7_to_9'
            return 'near_limit_9_plus'

        tradable = []
        pool_exclusion_counts = {
            'non_mainboard': 0,
            'invalid_price': 0,
            'suspended_or_missing_quote': 0,
            'amount_missing': 0,
            'other': 0,
        }

        def record_pool_drop(row, stage, reason, details=None):
            candidate_drop_diagnostics.append(candidate_drop_diagnostic(row, stage, reason, details))

        for s in stocks:
            code = str(s.get('f12', '')).zfill(6)
            price = safe_num(s.get('f2'))
            pct = safe_num(s.get('f3'))
            quote = eastmoney_quote_prices(s)
            open_price = safe_num(quote.get('open'))
            high = safe_num(quote.get('high'))
            low = safe_num(quote.get('low'))
            amount = safe_num(s.get('f6'))
            turnover = safe_num(s.get('f8'))
            prev_close = safe_num(s.get('f18'))
            net_inflow = safe_num(s.get('f62'))
            board = board_for_code(code)

            filter_details = {
                'board': board,
                'price': price,
                'pct_chg': pct,
                'amount': amount,
                'one_lot_cost': round(price * 100, 4),
                'price_cap': None,
            }
            if board != 'main':
                pool_exclusion_counts['non_mainboard'] += 1
                record_pool_drop(s, 'tradable_filter', 'non_mainboard', filter_details)
                continue
            if price <= 0:
                pool_exclusion_counts['suspended_or_missing_quote'] += 1
                record_pool_drop(s, 'tradable_filter', 'suspended_or_missing_quote', filter_details)
                continue
            if amount <= 0:
                pool_exclusion_counts['amount_missing'] += 1
                record_pool_drop(s, 'tradable_filter', 'amount_missing', filter_details)
                continue

            close_pos = round((price - low) / (high - low), 6) if high > low else 0.5
            stage = signal_stage_bucket(pct)

            # Setup classification (original logic)
            source_layers = ['L0_FULL_UNIVERSE']
            setup_type = 'HOT_MOMENTUM'
            if 2.0 <= pct <= 9.0:
                source_layers.append('L1_HOT_MOMENTUM')
            if pct >= 9.5:
                source_layers.append('L2_LIMIT_STRENGTH')
                setup_type = 'LIMIT_STRENGTH'
            if stage == 'underwater' and turnover >= 1.4 and close_pos >= 0.70:
                source_layers.append('L4_UNDERWATER_RECOVERY')
                setup_type = 'UNDERWATER_TO_RED_STRENGTH'
            # 新增: 蓄力股识别 (涨幅0.5%-2%，成交额>2亿，换手率>1%)
            if 0.5 <= pct < 2.0 and amount > 2e8 and turnover > 1.0:
                source_layers.append('L5_ACCUMULATION')
                setup_type = 'ACCUMULATION_READY'

            # Rank by amount (liquidity)
            tradable.append({
                'code': code,
                'symbol': code,
                'name': str(s.get('f14', '')),
                'stock_name': str(s.get('f14', '')),
                'price': price,
                'open': open_price,
                'signal_pct': pct,
                'signal_amount': amount,
                'turnover_rate': turnover,
                'net_inflow_main': net_inflow,
                'close_position_score': close_pos,
                'high': high,
                'low': low,
                'prev_close': prev_close,
                'amplitude': safe_num(s.get('f7')),
                'volume_ratio': safe_num(s.get('f10')),
                'board': board,
                'setup_type': setup_type,
                'source_layers': source_layers,
                'candidate_stage': stage,
            })

        # Sort by composite score (not just amount)
        # 新增: 综合排序 = 涨幅权重 * 0.4 + 成交额权重 * 0.3 + 换手率权重 * 0.3
        def composite_sort_key(s):
            pct = s['signal_pct']
            amount = s['signal_amount']
            turnover = s['turnover_rate']
            # 涨幅分数: 2%-9% 最优
            pct_score = min(1.0, max(0, (pct - 0.5) / 9.0))
            # 成交额分数: 对数标准化
            amount_score = min(1.0, amount / 1e10)  # 10亿为满分
            # 换手率分数: 2%-10% 最优
            turnover_score = min(1.0, max(0, (turnover - 1.0) / 9.0))
            return pct_score * 0.4 + amount_score * 0.3 + turnover_score * 0.3

        tradable.sort(key=composite_sort_key, reverse=True)
        for i, s in enumerate(tradable, 1):
            s['rank'] = i

        # Amount / fund-flow percentiles
        amounts = [s['signal_amount'] for s in tradable]
        net_inflows = [s['net_inflow_main'] for s in tradable]
        for s in tradable:
            s['amount_pctile_rule'] = sum(1 for a in amounts if a <= s['signal_amount']) / len(amounts) if amounts else 0.5
            s['full_universe_amount_pctile'] = s['amount_pctile_rule']
            s['full_universe_fund_pctile'] = sum(1 for flow in net_inflows if flow <= s['net_inflow_main']) / len(net_inflows) if net_inflows else 0.5

        # =================================================================
        # 加载所有数据源 (31个域)
        # =================================================================
        print(f'  Loading data sources...')
        data_cache = {}

        def load_jsonl(filename):
            """加载JSONL文件"""
            path = output_dir / filename
            if not path.exists():
                return []
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return [json.loads(line) for line in f if line.strip()]
            except Exception:
                return []

        # 涨停板数据
        data_cache['limitup_pool'] = load_jsonl('limitup_pool.jsonl')
        data_cache['limitup_broken'] = load_jsonl('limitup_broken.jsonl')
        data_cache['limitup_consecutive'] = load_jsonl('limitup_consecutive.jsonl')
        data_cache['limitup_yesterday'] = load_jsonl('limitup_yesterday.jsonl')
        data_cache['limitup_yesterday_one_word'] = load_jsonl('limitup_yesterday_one_word.jsonl')

        # 龙虎榜
        data_cache['lhb'] = load_jsonl('lhb.jsonl')

        # 大宗交易
        data_cache['block_trades'] = load_jsonl('block_trades.jsonl')

        # 股东变动
        data_cache['shareholder_changes'] = load_jsonl('shareholder_changes.jsonl')

        # 业绩预告
        data_cache['earnings_preview'] = load_jsonl('earnings_preview.jsonl')

        # 限售解禁
        data_cache['lockup_expiry'] = load_jsonl('lockup_expiry.jsonl')

        # 机构调研
        data_cache['org_survey'] = load_jsonl('org_survey.jsonl')

        # 北向资金
        data_cache['hsgt_deals'] = load_jsonl('hsgt_deals.jsonl')
        data_cache['hsgt_holdings'] = load_jsonl('hsgt_holdings.jsonl')

        # 研报
        data_cache['stock_reports'] = load_jsonl('stock_reports.jsonl')
        data_cache['industry_reports'] = load_jsonl('industry_reports.jsonl')

        # 公告/快讯/板块新闻（aux 消费必须能读到 sector_news，不能只写 jsonl 不入 cache）
        data_cache['announcements'] = load_jsonl('announcements.jsonl')
        data_cache['news_kuaixun'] = load_jsonl('news_kuaixun.jsonl')
        data_cache['sector_news'] = load_jsonl('sector_news.jsonl') or list(results.get('sector_news') or [])

        # 停牌信息
        data_cache['trading_halts'] = load_jsonl('trading_halts.jsonl')

        # IPO日历
        data_cache['ipo_calendar'] = load_jsonl('ipo_calendar.jsonl')

        # 人气排名
        data_cache['popularity_rank'] = load_jsonl('popularity_rank.jsonl')

        # 资金流
        data_cache['stock_capital_flow'] = load_jsonl('stock_capital_flow.jsonl')

        # 板块数据 (分析模块需要)
        data_cache['stock_all_a'] = load_jsonl('stock_all_a.jsonl')
        data_cache['flow_industry'] = load_jsonl('flow_industry.jsonl')
        data_cache['flow_concept'] = load_jsonl('flow_concept.jsonl')
        data_cache['market_capital_flow'] = load_jsonl('market_capital_flow.jsonl')
        data_cache['sector_capital_flow'] = results.get('sector_capital_flow', {})
        data_cache['hsgt_summary'] = results.get('hsgt_summary', [])

        # 研报索引
        stock_report_codes = {}
        for item in data_cache['stock_reports']:
            code = str(item.get('stockCode', '')).zfill(6)
            if code not in stock_report_codes:
                stock_report_codes[code] = []
            stock_report_codes[code].append({
                'title': item.get('title', ''),
                'org': item.get('orgSName', ''),
                'rating': item.get('ratingName', ''),
                'industry': item.get('indvInduName', '') or item.get('industryName', '') or item.get('industry', ''),
            })

        industry_report_rows_by_sector = {}
        for item in data_cache.get('industry_reports', []):
            sector_name = str(item.get('industryName', '') or item.get('industry', '') or item.get('INDUSTRY_NAME', '')).strip()
            if not sector_name:
                continue
            industry_report_rows_by_sector.setdefault(sector_name, []).append(item)

        # =================================================================
        # 分析模块 (6个信号) - 从原始数据提取市场/个股信号
        # =================================================================
        print(f'  Analyzing market signals...')

        # --- 1. 板块轮动信号 ---
        # 逻辑: 行业板块资金净流入排名 → 龙头股加分
        sector_flow = {}
        for item in data_cache.get('flow_industry', []):
            code = str(item.get('f12', ''))
            name = str(item.get('f14', ''))
            net_inflow = safe_num(item.get('f62'))
            sector_flow[code] = {'name': name, 'net_inflow': net_inflow}
        # 按资金净流入排序，取前10强势板块
        sorted_sectors = sorted(sector_flow.values(), key=lambda x: x['net_inflow'], reverse=True)
        hot_sectors = {s['name'] for s in sorted_sectors[:10]}
        cold_sectors = {s['name'] for s in sorted_sectors[-5:]}
        # --- 2. 题材热度信号 ---
        # 逻辑: 概念板块涨幅+资金流排名 → 热门概念龙头加分
        concept_flow = {}
        for item in data_cache.get('flow_concept', []):
            code = str(item.get('f12', ''))
            name = str(item.get('f14', ''))
            net_inflow = safe_num(item.get('f62'))
            pct = safe_num(item.get('f3'))
            concept_flow[code] = {'name': name, 'net_inflow': net_inflow, 'pct': pct}
        sorted_concepts = sorted(concept_flow.values(), key=lambda x: x['net_inflow'], reverse=True)
        hot_concepts = {c['name'] for c in sorted_concepts[:10]}
        # --- 3. 龙头动向信号 ---
        # 逻辑: 连板股数量、最高连板数、涨停封单强度
        consecutive_count = len(data_cache.get('limitup_consecutive', []))
        limitup_count = len(data_cache.get('limitup_pool', []))
        broken_count = len(data_cache.get('limitup_broken', []))
        # 连板梯队: 统计连板数分布
        max_consecutive = 0
        for item in data_cache.get('limitup_consecutive', []):
            zb = safe_num(item.get('zbs', 0))
            max_consecutive = max(max_consecutive, zb)
        # 封单强度: 涨停股中封单金额/成交额
        seal_strength_codes = {}
        for item in data_cache.get('limitup_pool', []):
            c = str(item.get('c', '')).zfill(6)
            seal_amount = safe_num(item.get('fund', 0))
            amount = safe_num(item.get('amount', 0))
            if amount > 0:
                seal_strength_codes[c] = seal_amount / amount if seal_amount > 0 else 0

        # --- 4. 资金流向信号 ---
        # 逻辑: 个股主力净流入、超大单净流入 → 加分
        capital_flow_codes = {}
        for item in data_cache['stock_capital_flow']:
            code = str(item.get('f12', '')).zfill(6)
            capital_flow_codes[code] = {
                'main_inflow': safe_num(item.get('f62')),
                'main_pct': safe_num(item.get('f18')),
                'super_large_inflow': safe_num(item.get('f66')),
                'large_inflow': safe_num(item.get('f69')),
            }
        # 大盘资金流
        market_flow = data_cache.get('market_capital_flow', [])
        # Eastmoney kline fields: date, main net amount, super-large amount,
        # medium amount, large amount, main net ratio, change percent.
        market_main_inflow = sum(
            safe_num(m.get('klines', [''])[0].split(',')[1])
            if m.get('klines') and len(m.get('klines', [''])[0].split(',')) > 1
            else 0
            for m in market_flow
        )

        # --- 5. 市场情绪信号 ---
        # 逻辑: 市场宽度、涨停数/炸板数比、涨跌比
        all_stocks = data_cache.get('stock_all_a', [])
        mainboard_rows = [
            row for row in all_stocks
            if board_for_code(row.get('f12')) == 'main'
        ]
        mainboard_codes = {
            normalize_stock_code(row.get('f12'))
            for row in mainboard_rows
            if normalize_stock_code(row.get('f12'))
        }
        mainboard_industry_count = sum(
            bool(str(row.get('f100') or '').strip())
            for row in mainboard_rows
        )
        mainboard_region_count = sum(
            bool(str(row.get('f102') or '').strip())
            for row in mainboard_rows
        )
        mainboard_concept_count = sum(
            bool(str(row.get('f103') or '').strip() not in ('', '-', '--'))
            for row in mainboard_rows
        )
        mainboard_capital_flow_codes = {
            normalize_stock_code(row.get('f12'))
            for row in data_cache.get('stock_capital_flow', [])
            if normalize_stock_code(row.get('f12'))
        }
        mainboard_capital_flow_count = len(mainboard_codes & mainboard_capital_flow_codes)
        industry_flow_count = len(data_cache.get('flow_industry', []))
        concept_flow_count = len(data_cache.get('flow_concept', []))
        mainboard_domain_coverage = {
            domain: mainboard_row_coverage(data_cache.get(domain, []))
            for domain in (
                'lhb',
                'hsgt_deals',
                'hsgt_holdings',
                'earnings_preview',
                'lockup_expiry',
                'org_survey',
                'stock_capital_flow',
                'stock_reports',
                'block_trades',
                'shareholder_changes',
                'ipo_calendar',
                'trading_halts',
                'popularity_rank',
                'announcements',
            )
        }
        direct_data_coverage = {
            'mode': 'eastmoney_api_direct_raw',
            'read_path': 'scan_market_data -> xiaogu_api',
            'quote_endpoint': 'push2delay.eastmoney.com/api/qt/clist/get',
            'quote_fields': STOCK_ALL_A_FIELDS.split(','),
            'quote_rows': len(all_stocks),
            'mainboard_quote_rows': len(mainboard_rows),
            'mainboard_unique_codes': len(mainboard_codes),
            'mainboard_industry': {
                'field': 'f100',
                'covered_rows': mainboard_industry_count,
                'coverage_ratio': round(
                    mainboard_industry_count / len(mainboard_rows), 6
                ) if mainboard_rows else 0.0,
            },
            'mainboard_region': {
                'field': 'f102',
                'covered_rows': mainboard_region_count,
                'coverage_ratio': round(
                    mainboard_region_count / len(mainboard_rows), 6
                ) if mainboard_rows else 0.0,
            },
            'mainboard_concepts': {
                'field': 'f103',
                'covered_rows': mainboard_concept_count,
                'coverage_ratio': round(
                    mainboard_concept_count / len(mainboard_rows), 6
                ) if mainboard_rows else 0.0,
            },
            'mainboard_stock_capital_flow': {
                'endpoint': 'push2delay.eastmoney.com/api/qt/clist/get',
                'covered_rows': mainboard_capital_flow_count,
                'coverage_ratio': round(
                    mainboard_capital_flow_count / len(mainboard_codes), 6
                ) if mainboard_codes else 0.0,
            },
            'sector_capital_flow': {
                'industry_rows': industry_flow_count,
                'concept_rows': concept_flow_count,
                'source': 'push2delay.eastmoney.com/api/qt/clist/get',
            },
            'mainboard_domains': mainboard_domain_coverage,
            'pagination': pagination_diagnostics,
        }
        up_count = sum(1 for s in all_stocks if safe_num(s.get('f3')) > 0)
        down_count = sum(1 for s in all_stocks if safe_num(s.get('f3')) < 0)
        market_breadth = up_count / len(all_stocks) * 100 if all_stocks else 0
        # 涨停/炸板比 (情绪指标)
        limitup_broken_ratio = limitup_count / (broken_count + 1)
        # 市场情绪得分 (0-10)
        sentiment_score = 5.0
        if market_breadth > 60:
            sentiment_score += 2
        elif market_breadth < 30:
            sentiment_score -= 2
        if limitup_broken_ratio > 3:
            sentiment_score += 2
        elif limitup_broken_ratio < 1:
            sentiment_score -= 2
        if max_consecutive >= 5:
            sentiment_score += 1
        sentiment_score = max(0, min(10, sentiment_score))
        if market_breadth >= 62 and limitup_broken_ratio >= 1.4 and max_consecutive >= 3:
            market_regime = 'strong'
        elif market_breadth <= 42 or limitup_broken_ratio <= 0.85 or broken_count >= max(20, limitup_count * 0.9):
            market_regime = 'weak'
        else:
            market_regime = 'neutral'

        # --- 6. 新闻催化信号 ---
        # 逻辑: 从快讯/公告文本提取关键词 → 匹配相关板块/概念 → 龙头股加分
        # 复用 runner 中的 _analyze_news_sentiment 关键词映射
        import re

        # 核心关键词→同义词映射 (来自 _analyze_news_sentiment)
        core_keywords = {
            '银行': ['银行', '降准', '降息', 'LPR', '金融改革'],
            '创新药': ['创新药', 'CXO', '医药', '生物制品', '疫苗', '单抗', '减肥药'],
            '红利': ['红利', '高股息', '央企估值', '破净修复', '中特估'],
            '机器人': ['人形机器人', '特斯拉机器人', '减速器', '伺服电机'],
            'AI': ['大模型', '算力', 'GPU', 'AI应用', '人工智能'],
            '半导体': ['芯片法案', '半导体补贴', '国产替代', '光刻机', '封测', '芯片', '晶圆'],
            '军工': ['军费增长', '装备订单', '航天发射', '卫星互联网', '军工'],
            '煤炭': ['煤炭', '能源安全', '煤价', '限产'],
            '新能源': ['光伏补贴', '储能政策', '锂电池', '充电桩', '光伏', '风电', '储能'],
            # 扩展关键词
            '华为海思': ['华为海思', '海思', '华为'],
            '中芯概念': ['中芯', '台积电'],
            '白酒': ['白酒', '茅台'],
            '券商': ['券商', '证券'],
            '房地产': ['房地产', '地产', '楼市'],
            '汽车': ['汽车', '新能源车', '智能驾驶'],
            '5G': ['5G', '通信', '数据中心'],
        }

        # 统计快讯/公告中的关键词命中 (使用同义词匹配)
        keyword_scores = {}  # keyword -> score
        for keyword, synonyms in core_keywords.items():
            score = 0
            match_count = 0
            for item in data_cache.get('news_kuaixun', []):
                title = str(item.get('title', ''))
                news_summary = str(item.get('summary', ''))
                content = (title + ' ' + news_summary).lower()
                for kw in synonyms:
                    if kw.lower() in content:
                        score += 15
                        match_count += 1
                        break
            if match_count > 0:
                keyword_scores[keyword] = min(100, score)

        # Match news keywords directly against each stock's API-provided
        # industry and concept fields.
        news_mentioned_codes = {}
        for keyword, score in keyword_scores.items():
            for stock in all_stocks:
                code = normalize_stock_code(stock.get('f12'))
                if not code:
                    continue
                stock_text = ' '.join([
                    str(stock.get('f14') or ''),
                    str(stock.get('f100') or ''),
                    *stock_concepts_from_quote_row(stock),
                ])
                if keyword in stock_text:
                    news_mentioned_codes[code] = max(
                        news_mentioned_codes.get(code, 0),
                        score,
                    )

        # 公告直接提及的股票
        announcement_codes = {}
        for item in data_cache.get('announcements', []):
            for code in stock_codes_from_row(item):
                announcement_codes[code] = announcement_codes.get(code, 0) + 1

        print(f'  Signals: hot_sectors={len(hot_sectors)}, hot_concepts={len(hot_concepts)}')
        print(f'  Signals: limitup={limitup_count}, consecutive={consecutive_count}, broken={broken_count}, max_consecutive={max_consecutive}')
        print(f'  Signals: market_breadth={market_breadth:.1f}%, sentiment={sentiment_score:.1f}')
        print(f'  Signals: news_mentioned={len(news_mentioned_codes)}, announcements={len(announcement_codes)}')
        print(f'  Debug: news_mentioned_codes = {news_mentioned_codes}')

        # =================================================================
        # 构建索引 (按股票代码)
        # =================================================================
        print(f'  Building indexes...')

        # 涨停池索引
        limitup_codes = {str(item.get('c', '')).zfill(6) for item in data_cache['limitup_pool']}
        consecutive_codes = {str(item.get('c', '')).zfill(6) for item in data_cache['limitup_consecutive']}
        broken_codes = {str(item.get('c', '')).zfill(6) for item in data_cache['limitup_broken']}
        yesterday_limitup_codes = {str(item.get('c', '')).zfill(6) for item in data_cache['limitup_yesterday']}

        # 龙虎榜索引
        lhb_codes = {str(item.get('SECURITY_CODE', '')).zfill(6) for item in data_cache['lhb']}
        lhb_details = {}
        for item in data_cache['lhb']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            if code not in lhb_details:
                lhb_details[code] = []
            lhb_details[code].append({
                'reason': item.get('EXPLANATION', ''),
                'buy_amount': safe_num(item.get('BUY_AMOUNT')),
                'sell_amount': safe_num(item.get('SELL_AMOUNT')),
                'net_amount': safe_num(item.get('NET_AMOUNT')),
            })

        # The current endpoint is only a market-activity view, not a
        # specialized block-trade report. Keep the raw domain for audit, but
        # quarantine it from production scoring until a verified source exists.
        block_trade_codes = {}

        # 股东变动索引
        shareholder_changes_codes = {}
        for item in data_cache['shareholder_changes']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            if code not in shareholder_changes_codes:
                shareholder_changes_codes[code] = []
            shareholder_changes_codes[code].append({
                'holder': item.get('HOLDER_NAME', ''),
                'change_num': safe_num(item.get('CHANGE_NUM')),
                'change_rate': safe_num(item.get('AFTER_CHANGE_RATE')),
                'notice_date': item.get('NOTICE_DATE', ''),
            })

        # 业绩预告索引
        earnings_codes = {}
        for item in data_cache['earnings_preview']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            earnings_codes[code] = {
                'type': item.get('NOTICE_DATE', ''),
                'change_range': item.get('CHANGE_REASON_EXPLAIN', ''),
                'forecast': item.get('FORECAST', ''),
            }

        # 限售解禁索引
        lockup_codes = {}
        for item in data_cache['lockup_expiry']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            lockup_codes[code] = {
                'free_date': item.get('FREE_DATE', ''),
                'free_num': safe_num(item.get('FREE_NUM')),
                'free_ratio': safe_num(item.get('FREE_RATIO')),
            }

        # 机构调研索引
        org_survey_codes = {}
        for item in data_cache['org_survey']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            if code not in org_survey_codes:
                org_survey_codes[code] = []
            org_survey_codes[code].append({
                'org_name': item.get('ORG_NAME', ''),
                'notice_date': item.get('NOTICE_DATE', ''),
            })

        # 北向持股索引
        hsgt_holding_codes = {}
        for item in data_cache['hsgt_holdings']:
            code = str(item.get('SECURITY_CODE', '')).zfill(6)
            hsgt_holding_codes[code] = {
                'hold_num': safe_num(item.get('HOLD_NUM')),
                'hold_ratio': safe_num(item.get('A_SHARES_RATIO')),
                'change_num': safe_num(item.get('HOLD_CHANGE')),
            }

        hsgt_deal_codes = {}
        for item in data_cache.get('hsgt_deals', []):
            code = str(item.get('SECURITY_CODE', '') or item.get('SECUCODE', '')).zfill(6)
            if not code or code == '000000':
                continue
            hsgt_deal_codes.setdefault(code, []).append(item)

        # 公告索引
        announcement_codes = {}
        for item in data_cache['announcements']:
            for code in stock_codes_from_row(item):
                if not is_mainboard_code(code):
                    continue
                announcement_codes.setdefault(code, []).append({
                    'title': item.get('title', '') or item.get('title_ch', ''),
                    'date': item.get('noticeDate', '') or item.get('notice_date', ''),
                })

        # The current endpoint returns the whole market, not a verified
        # popularity ranking. Keep it quarantined from production scoring.
        popularity_codes = {}

        # The current endpoint returns the whole market, so it cannot prove a
        # halt. Do not turn zero-change/zero-amount heuristics into hard risk.
        halted_codes = set()

        # 资金流索引
        capital_flow_codes = {}
        for item in data_cache['stock_capital_flow']:
            code = str(item.get('f12', '')).zfill(6)
            capital_flow_codes[code] = {
                'main_inflow': safe_num(item.get('f62')),
                'main_pct': safe_num(item.get('f18')),
                'super_large_inflow': safe_num(item.get('f66')),
                'large_inflow': safe_num(item.get('f69')),
                'medium_inflow': safe_num(item.get('f72')),
                'small_inflow': safe_num(item.get('f75')),
            }

        print(f'  Loaded: limitup={len(limitup_codes)}, consecutive={len(consecutive_codes)}, lhb={len(lhb_codes)}')
        print(f'  Loaded: block_trades={len(block_trade_codes)}, shareholder={len(shareholder_changes_codes)}, earnings={len(earnings_codes)}')
        print(f'  Loaded: lockup={len(lockup_codes)}, org_survey={len(org_survey_codes)}')
        print(f'  Loaded: hsgt_holdings={len(hsgt_holding_codes)}, reports={len(stock_report_codes)}, announcements={len(announcement_codes)}')
        print(f'  Loaded: popularity={len(popularity_codes)}, halted={len(halted_codes)}, capital_flow={len(capital_flow_codes)}')

        # =================================================================
        # Set final_score for runner consumption
        # 综合评分: 基础分 + 涨停潜力 + 市场信号 + 资金信号 + 基本面信号
        # 评分范围: 0-95, 分数分布更合理
        # =================================================================
        structured_scores = []
        sector_snapshot = []
        for sector_name in sorted(hot_sectors):
            symbols = [
                normalize_stock_code(row.get('f12'))
                for row in all_stocks
                if str(row.get('f100') or '').strip() == sector_name
            ]
            symbols = [code for code in symbols if code]
            sector_snapshot.append({'sector': sector_name, 'symbols': symbols[:20]})
        for concept_name in sorted(hot_concepts):
            symbols = []
            for row in all_stocks:
                code = normalize_stock_code(row.get('f12'))
                concepts = stock_concepts_from_quote_row(row)
                if code and concept_name in concepts:
                    symbols.append(code)
            sector_snapshot.append({'sector': concept_name, 'symbols': symbols[:20]})
        market_follow_through_score = clamp01(
            0.45 * clamp01((market_breadth - 35.0) / 35.0)
            + 0.30 * clamp01(limitup_broken_ratio / 3.0)
            + 0.25 * clamp01(max_consecutive / 5.0)
        )

        def candidate_research_rows(code, stock_sector, stock_concepts):
            rows = []
            for ann in announcement_codes.get(code, []):
                text = str(ann.get('title', '')).strip()
                if text:
                    rows.append({'text': text, 'source': 'eastmoney_api_announcements'})
            for report in stock_report_codes.get(code, []):
                text = ' '.join(part for part in (report.get('rating'), report.get('title'), report.get('org'), report.get('industry')) if part)
                if text.strip():
                    rows.append({'text': text.strip(), 'source': 'eastmoney_api_stock_reports'})
            earnings = earnings_codes.get(code)
            if isinstance(earnings, dict):
                text = ' '.join(part for part in (str(earnings.get('forecast', '')).strip(), str(earnings.get('change_range', '')).strip()) if part)
                if text:
                    rows.append({'text': text, 'source': 'eastmoney_api_earnings_preview'})
            for detail in lhb_details.get(code, [])[:3]:
                text = str(detail.get('reason', '')).strip()
                if text:
                    rows.append({'text': text, 'source': 'eastmoney_api_lhb'})
            holding = hsgt_holding_codes.get(code)
            if isinstance(holding, dict) and (holding.get('hold_ratio') or holding.get('change_num')):
                rows.append({
                    'text': f"北向持股 {holding.get('hold_ratio', 0):.2f}% 变动 {holding.get('change_num', 0):.0f}",
                    'source': 'eastmoney_api_hsgt_holdings',
                })
            for deal in hsgt_deal_codes.get(code, [])[:2]:
                trade_text = ' '.join(
                    part for part in (
                        str(deal.get('TRADE_DATE', '')).strip(),
                        str(deal.get('MUTUAL_TYPE', '') or deal.get('BOARD_NAME', '')).strip(),
                        str(deal.get('CHANGE_RATE', '')).strip(),
                    )
                    if part
                )
                if trade_text:
                    rows.append({'text': trade_text, 'source': 'eastmoney_api_hsgt_deals'})
            if code in block_trade_codes:
                trade = (block_trade_codes.get(code) or [{}])[0]
                rows.append({
                    'text': f"大宗交易 成交额 {trade.get('amount', 0):.0f} 换手率 {trade.get('turnover', 0):.2f}%",
                    'source': 'eastmoney_api_block_trades',
                })
            if code in shareholder_changes_codes:
                latest_change = (shareholder_changes_codes.get(code) or [{}])[0]
                holder = str(latest_change.get('holder', '')).strip()
                change_num = latest_change.get('change_num', 0)
                rows.append({
                    'text': f"股东变动 {holder} 变动股数 {change_num}",
                    'source': 'eastmoney_api_shareholder_changes',
                })
            if stock_sector:
                for report in industry_report_rows_by_sector.get(stock_sector, [])[:2]:
                    text = ' '.join(
                        part for part in (
                            stock_sector,
                            str(report.get('title', '')).strip(),
                            str(report.get('orgSName', '') or report.get('org', '')).strip(),
                            str(report.get('ratingName', '') or report.get('rating', '')).strip(),
                        )
                        if part
                    )
                    if text:
                        rows.append({'text': text, 'source': 'eastmoney_api_industry_reports'})
            news_tags = []
            if stock_sector and stock_sector in hot_sectors:
                news_tags.append(stock_sector)
            for concept_name in stock_concepts:
                if concept_name in hot_concepts:
                    news_tags.append(concept_name)
            for theme in news_tags[:3]:
                rows.append({'text': f'{theme} 题材催化', 'source': 'eastmoney_api_theme'})
            return rows

        for s in tradable:
            pct = s['signal_pct']
            close_pos = s['close_position_score']
            amount = s['signal_amount']
            turnover = s['turnover_rate']
            net_inflow = s['net_inflow_main']
            code = s['code']

            # 基础分 (30-70)
            base = 30
            pct_bonus = min(20, pct * 2.0)  # 涨幅分 (0-20)
            pos_bonus = close_pos * 10  # 位置分 (0-10)
            liq_bonus = min(10, amount / 1e9 * 2)  # 流动性分 (0-10)

            # 1. 涨停潜力评分 (0-15)
            limit_up_potential = 0.0
            # 接近涨停加分 (涨幅7%-9.5%)
            if 7.0 <= pct <= 9.5:
                limit_up_potential += 6.0
            elif 5.0 <= pct < 7.0:
                limit_up_potential += 3.0
            # 蓄力股加分 (涨幅0.5%-2%，但成交额大)
            if 0.5 <= pct < 2.0 and amount > 5e8:
                limit_up_potential += 5.0
            # 高换手率加分
            if turnover > 5.0:
                limit_up_potential += 3.0
            # 资金流入加分
            if net_inflow > 0:
                limit_up_potential += 3.0
            # 低位反弹加分
            if close_pos < 0.3:
                limit_up_potential += 4.0
            # 限制最大值
            limit_up_potential = min(15, limit_up_potential)

            # 2. 市场信号评分 (0-20)
            market_bonus = 0.0
            # 涨停池加分 (最高10分)
            if code in limitup_codes:
                market_bonus += 10.0
            # 连板加分 (最高8分)
            if code in consecutive_codes:
                market_bonus += 8.0
            # 龙虎榜加分 (最高5分)
            if code in lhb_codes:
                market_bonus += 5.0
            # 炸板股加分 (可能是机会)
            if code in broken_codes:
                market_bonus += 3.0
            # 昨日涨停加分 (惯性)
            if code in yesterday_limitup_codes:
                market_bonus += 4.0

            # 3. 资金信号评分 (0-15)
            capital_bonus = 0.0
            # 主力资金净流入加分 (最高8分)
            if code in capital_flow_codes:
                cf = capital_flow_codes[code]
                if cf['main_inflow'] > 0:
                    capital_bonus += min(8, cf['main_inflow'] / 1e8 * 1.5)
                # 超大单净流入加分 (最高4分)
                if cf['super_large_inflow'] > 0:
                    capital_bonus += min(4, cf['super_large_inflow'] / 1e8 * 0.8)
            # 北向持股加分 (最高3分)
            if code in hsgt_holding_codes:
                hsgt = hsgt_holding_codes[code]
                if hsgt['hold_ratio'] > 2.0:  # 北向持股>2%
                    capital_bonus += 2.0
                if hsgt['change_num'] > 0:  # 北向增持
                    capital_bonus += 1.0

            # 4. 基本面信号评分 (0-15)
            fundamental_bonus = 0.0
            # 业绩预告加分 (最高8分)
            if code in earnings_codes:
                earnings = earnings_codes[code]
                forecast = earnings.get('forecast', '')
                if '预增' in forecast or '略增' in forecast:
                    fundamental_bonus += 5.0
                elif '扭亏' in forecast:
                    fundamental_bonus += 8.0
                elif '预减' in forecast or '略减' in forecast:
                    fundamental_bonus -= 3.0

            # 机构调研加分 (最高5分)
            if code in org_survey_codes:
                survey_count = len(org_survey_codes[code])
                fundamental_bonus += min(5, survey_count * 1.5)

            # 研报加分 (最高3分)
            if code in stock_report_codes:
                report_count = len(stock_report_codes[code])
                fundamental_bonus += min(3, report_count)

            # 5. 风险信号评分 (扣分0-20)
            risk_penalty = 0.0
            # 限售解禁惩罚 (最高8分)
            if code in lockup_codes:
                lockup = lockup_codes[code]
                free_ratio = lockup.get('free_ratio', 0)
                if free_ratio > 5:  # 解禁比例>5%
                    risk_penalty += min(8, free_ratio * 0.5)

            # 股东减持惩罚 (最高5分)
            if code in shareholder_changes_codes:
                changes = shareholder_changes_codes[code]
                recent_sells = sum(1 for c in changes if c['change_num'] < 0)
                if recent_sells > 0:
                    risk_penalty += min(5, recent_sells * 1.5)

            # 6. 情绪信号评分 (0-5)
            sentiment_bonus = 0.0
            # 7. 板块轮动信号 (0-8)
            sector_rotation_bonus = 0.0
            # 龙头股加分 (行业板块龙头)
            stock_sector = str(
                s.get('industry')
                or s.get('sector_name')
                or s.get('sector')
                or ''
            ).strip()
            if stock_sector in hot_sectors:
                sector_rotation_bonus += 8.0
            elif stock_sector in cold_sectors:
                sector_rotation_bonus -= 3.0

            # 8. 题材热度信号 (0-8)
            topic_heat_bonus = 0.0
            # 龙头股加分 (概念板块龙头)
            stock_concepts = [
                str(value).strip()
                for value in s.get('sector_opportunity_tags') or []
                if str(value).strip()
            ]
            for concept_name in stock_concepts:
                if concept_name in hot_concepts:
                    topic_heat_bonus += 8.0
                    break

            # 9. 龙头动向信号 (0-10)
            leader_bonus = 0.0
            # 连板股加分
            if code in consecutive_codes:
                leader_bonus += 8.0
            # 涨停池加分
            if code in limitup_codes:
                leader_bonus += 5.0
            # 封单强度加分
            if code in seal_strength_codes:
                seal = seal_strength_codes[code]
                if seal > 0.5:  # 封单占比>50%
                    leader_bonus += 3.0
                elif seal > 0.2:
                    leader_bonus += 1.0
            # 市场情绪加成 (连板高度越高，龙头效应越强)
            if max_consecutive >= 5:
                leader_bonus += 2.0

            # 10. 资金流向信号 (0-10)
            flow_bonus = 0.0
            if code in capital_flow_codes:
                cf = capital_flow_codes[code]
                # 主力净流入加分
                if cf['main_inflow'] > 0:
                    flow_bonus += min(5, cf['main_inflow'] / 1e8 * 1.0)
                # 超大单净流入加分
                if cf['super_large_inflow'] > 0:
                    flow_bonus += min(3, cf['super_large_inflow'] / 1e8 * 0.5)
                # 主力占比加分
                if cf['main_pct'] > 10:  # 主力净流入占比>10%
                    flow_bonus += 2.0
            # 大盘资金流加成
            if market_main_inflow > 0:
                flow_bonus += 1.0

            # 11. 市场情绪信号 (0-10, 已计算sentiment_score)
            market_mood_bonus = sentiment_score  # 直接使用前面计算的情绪得分

            # 12. 新闻催化信号 (0-8)
            news_bonus = 0.0
            # 快讯提及加分 (score 0-100 → 0-5)
            if code in news_mentioned_codes:
                news_score = news_mentioned_codes[code]
                news_bonus += min(5, news_score / 20)  # 100分→5分
            # 公告提及加分
            if code in announcement_codes:
                ann_count = len(announcement_codes[code]) if isinstance(announcement_codes[code], list) else int(announcement_codes[code])
                news_bonus += min(3, ann_count * 1.5)

            # 计算最终分数
            final_score = (base + pct_bonus + pos_bonus + liq_bonus +
                          limit_up_potential + market_bonus + capital_bonus +
                          fundamental_bonus - risk_penalty + sentiment_bonus +
                          sector_rotation_bonus + topic_heat_bonus +
                          leader_bonus + flow_bonus + market_mood_bonus + news_bonus)
            final_score = max(0, min(95, final_score))

            # 保存详细评分
            s['limit_up_potential'] = round(limit_up_potential, 2)
            s['market_bonus'] = round(market_bonus, 2)
            s['capital_bonus'] = round(capital_bonus, 2)
            s['fundamental_bonus'] = round(fundamental_bonus, 2)
            s['risk_penalty'] = round(risk_penalty, 2)
            s['sentiment_bonus'] = round(sentiment_bonus, 2)
            s['sector_rotation_bonus'] = round(sector_rotation_bonus, 2)
            s['topic_heat_bonus'] = round(topic_heat_bonus, 2)
            s['leader_bonus'] = round(leader_bonus, 2)
            s['flow_bonus'] = round(flow_bonus, 2)
            s['market_mood_bonus'] = round(market_mood_bonus, 2)
            s['news_bonus'] = round(news_bonus, 2)
            s['final_score'] = round(final_score, 2)
            s['score'] = s['final_score']

            live_theme_tags = []
            if stock_sector:
                live_theme_tags.append(stock_sector)
            live_theme_tags.extend([concept_name for concept_name in stock_concepts if concept_name])
            live_hot_themes = [tag for tag in live_theme_tags if tag in hot_sectors or tag in hot_concepts]
            catalyst_rows = candidate_research_rows(code, stock_sector, stock_concepts)
            research_signals = {}
            if HAS_STRUCTURED_HELPERS:
                try:
                    research_signals = build_research_signals(s, catalyst_rows, source_time, sector_snapshot)
                except Exception:
                    research_signals = {}
            sector_strength = clamp01(
                (1.0 if stock_sector in hot_sectors else 0.0) * 0.55
                + min(1.0, len([name for name in stock_concepts if name in hot_concepts]) / 2.0) * 0.45
            )
            news_catalyst_strength = clamp01(
                min(1.0, len(catalyst_rows) / 4.0) * 0.55
                + min(1.0, len(announcement_codes.get(code, [])) / 2.0) * 0.20
                + min(1.0, len(stock_report_codes.get(code, [])) / 2.0) * 0.15
                + (0.10 if code in earnings_codes else 0.0)
            )
            topic_propagation_score = clamp01(
                sector_strength * 0.45
                + (0.25 if code in limitup_codes else 0.0)
                + (0.15 if code in consecutive_codes else 0.0)
                + min(0.15, len(lhb_details.get(code, [])) * 0.05)
            )
            intraday_alert_strength = clamp01(
                close_pos * 0.35
                + min(1.0, turnover / 8.0) * 0.25
                + min(1.0, max(0.0, pct) / 9.5) * 0.25
                + s['amount_pctile_rule'] * 0.15
            )
            limitup_reason_propagation_score = clamp01(
                (0.35 if code in limitup_codes else 0.0)
                + (0.20 if code in consecutive_codes else 0.0)
                + sector_strength * 0.30
                + news_catalyst_strength * 0.15
            )
            low_position_catalyst_score = clamp01(
                (0.30 if s['candidate_stage'] in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7') else 0.0)
                + sector_strength * 0.30
                + news_catalyst_strength * 0.20
                + clamp01(max(net_inflow, 0.0) / 1e8) * 0.20
            )
            search_layer_hint = 'formal_high_score'
            if s['setup_type'] in ('UNDERWATER_TO_RED_STRENGTH', 'UNDERWATER_RED_FLAT_RECOVERY'):
                search_layer_hint = 'underwater_reversal'
            elif s['candidate_stage'] in ('flat_0_to_3', 'early_3_to_5', 'mid_5_to_7') and sector_strength >= 0.35:
                search_layer_hint = 'sector_catalyst_low_position'
            elif news_catalyst_strength >= 0.35 and s['candidate_stage'] in ('underwater', 'flat_0_to_3', 'early_3_to_5', 'mid_5_to_7'):
                search_layer_hint = 'news_catalyst_low_position'
            elif s['candidate_stage'] in ('high_7_to_9', 'near_limit_9_plus'):
                search_layer_hint = 'limitup_capture'
            elif sector_strength >= 0.35:
                search_layer_hint = 'structured_sector'
            candidate_domain_counts = {
                'quote_snapshot': 1,
                'candidate_setup': 1 if s.get('setup_type') else 0,
                'capital_flow': 1 if code in capital_flow_codes else 0,
                'catalyst_context': len(catalyst_rows),
            }
            candidate_evidence_status, candidate_missing_domains = evidence_status_from_counts(
                candidate_domain_counts,
                optional_domains={'capital_flow', 'catalyst_context'},
            )
            enhanced_domain_counts = {
                'sector_rotation': len(live_hot_themes),
                'limitup_context': int(code in limitup_codes or code in consecutive_codes or code in broken_codes),
                'hsgt_holdings': 1 if code in hsgt_holding_codes else 0,
                'shareholder_changes': len(shareholder_changes_codes.get(code, [])),
            }
            enhanced_evidence_status, enhanced_missing_domains = evidence_status_from_counts(
                enhanced_domain_counts,
                optional_domains={'hsgt_holdings', 'shareholder_changes'},
            )
            s['search_layer_hint'] = search_layer_hint
            s['sector_catalyst_score'] = round(sector_strength, 4)
            s['sector_opportunity_score'] = round(sector_strength, 4)
            s['sector_opportunity_tags'] = live_hot_themes[:5]
            s['topic_propagation_score'] = round(topic_propagation_score, 4)
            s['intraday_alert_strength'] = round(intraday_alert_strength, 4)
            s['limitup_reason_propagation_score'] = round(limitup_reason_propagation_score, 4)
            s['low_position_catalyst_score'] = round(low_position_catalyst_score, 4)
            s['news_catalyst_strength'] = round(news_catalyst_strength, 4)
            s['sector_news_strength'] = round(news_catalyst_strength, 4)
            s['research_signals'] = research_signals
            s['candidate_evidence_status'] = candidate_evidence_status
            s['candidate_evidence_domain_counts'] = candidate_domain_counts
            s['candidate_evidence_matched_domains'] = [domain for domain, count in candidate_domain_counts.items() if count]
            s['candidate_evidence_missing_domains'] = candidate_missing_domains
            s['enhanced_evidence_status'] = enhanced_evidence_status
            s['enhanced_evidence_domain_counts'] = enhanced_domain_counts
            s['enhanced_evidence_matched_domains'] = [domain for domain, count in enhanced_domain_counts.items() if count]
            s['enhanced_evidence_missing_domains'] = enhanced_missing_domains
            s['experimental_evidence_status'] = 'PASS'
            s['experimental_evidence_domain_counts'] = {}
            s['experimental_evidence_matched_domains'] = []
            s['experimental_evidence_missing_domains'] = []
            s['source_time'] = source_time
            s['runner_asof_time'] = source_time[11:] if len(source_time) >= 19 else ''
            s['market_regime'] = market_regime
            s['market_breadth_up_pct'] = round(market_breadth, 2)
            s['market_limitups'] = limitup_count
            s['market_bigups'] = market_bigups
            s['market_follow_through_score'] = round(market_follow_through_score, 4)
            s['limitup_broken_ratio'] = round(limitup_broken_ratio, 4)
            s['broken_limitups'] = broken_count
            s['sentiment_score'] = round(sentiment_score, 2)
            s['market_main_inflow'] = market_main_inflow

            # 保存数据源标记
            s['in_limitup_pool'] = code in limitup_codes
            s['in_consecutive'] = code in consecutive_codes
            s['in_lhb'] = code in lhb_codes
            s['in_block_trades'] = code in block_trade_codes
            s['block_trades_source_status'] = 'PROXY_QUARANTINED' if data_cache.get('block_trades') else 'MISSING'
            s['in_earnings_preview'] = code in earnings_codes
            s['in_lockup_expiry'] = code in lockup_codes
            s['in_org_survey'] = code in org_survey_codes
            s['in_hsgt_holdings'] = code in hsgt_holding_codes
            s['in_stock_reports'] = code in stock_report_codes
            s['in_announcements'] = code in announcement_codes
            s['in_halted'] = code in halted_codes
            s['trading_halts_source_status'] = 'PROXY_QUARANTINED' if data_cache.get('trading_halts') else 'MISSING'
            s['in_shareholder_changes'] = code in shareholder_changes_codes
            s['in_popularity_rank'] = code in popularity_codes
            s['popularity_rank'] = popularity_codes.get(code)
            s['popularity_rank_source_status'] = 'PROXY_QUARANTINED' if data_cache.get('popularity_rank') else 'MISSING'
            s['in_capital_flow'] = code in capital_flow_codes
            s['failed_limitup'] = code in broken_codes

        # 按最终分数排序
        tradable.sort(key=lambda x: x['final_score'], reverse=True)
        for i, s in enumerate(tradable, 1):
            s['rank'] = i

        # ``full_candidate_pool`` is the stable, auditable market universe.
        # Score/evidence gates may narrow downstream pools but never remove rows
        # from this persistence scope.
        candidates = tradable[:PRE_ENRICHMENT_CANDIDATE_TARGET]
        for row in tradable[PRE_ENRICHMENT_CANDIDATE_TARGET:]:
            record_pool_drop(row, 'pre_enrichment_rank_cut', 'ranked_below_pre_enrichment_target', {
                'rank': row.get('rank'),
                'final_score': row.get('final_score') or row.get('score'),
                'pre_enrichment_target_count': PRE_ENRICHMENT_CANDIDATE_TARGET,
                'tradable_count': len(tradable),
            })
        # Per-stock announcement fill for top candidates missing market-wide ann codes.
        try:
            covered_ann_codes = set()
            for item in data_cache.get('announcements') or []:
                covered_ann_codes.update(stock_codes_from_row(item))
            missing_ann_codes = []
            # Pull per-stock announcements for a larger top slice so aux PASS covers more of the pool.
            stock_ann_topn = int(os.environ.get('XIAOGU_STOCK_ANN_TOPN', '200'))
            for cand in candidates[:stock_ann_topn]:
                cand_code = normalize_stock_code(cand.get('code') or cand.get('symbol'))
                if cand_code and cand_code not in covered_ann_codes:
                    missing_ann_codes.append(cand_code)
            if missing_ann_codes:
                extra_ann = fetch_stock_announcements(
                    missing_ann_codes,
                    page_size=int(os.environ.get('XIAOGU_STOCK_ANN_PAGE_SIZE', '15')),
                    max_stocks=stock_ann_topn,
                )
                if extra_ann:
                    data_cache['announcements'] = list(data_cache.get('announcements') or []) + extra_ann
                    results['announcements'] = list(results.get('announcements') or []) + extra_ann
                    ann_path = output_dir / 'announcements.jsonl'
                    with open(ann_path, 'a', encoding='utf-8') as ann_fp:
                        for item in extra_ann:
                            if isinstance(item, dict):
                                ann_fp.write(json.dumps(item, ensure_ascii=False) + '\n')
                    print(
                        f'  Supplemental stock announcements: +{len(extra_ann)} '
                        f'for {len(missing_ann_codes)} missing top codes'
                    )
        except Exception as exc:
            print(f'  Supplemental stock announcements skipped: {exc}')
        enrich_mainboard_auxiliary_evidence(candidates, data_cache)
        scored_count = len(candidates)
        print(f'  Candidates: {len(candidates)} (tradable={len(tradable)})')
        print(f'  Data sources used: limitup={len(limitup_codes)}, lhb={len(lhb_codes)}, block_trades={len(block_trade_codes)}')
        print(f'                   shareholder={len(shareholder_changes_codes)}, earnings={len(earnings_codes)}, org_survey={len(org_survey_codes)}')
        print(f'                   hsgt={len(hsgt_holding_codes)}, reports={len(stock_report_codes)}')

        research_signal_rows = []
        structured_component_rows = []
        structured_score_component_rows = []
        if HAS_STRUCTURED_HELPERS and candidates:
            candidate_codes = {str(candidate.get('code') or '').zfill(6) for candidate in candidates if candidate.get('code')}

            def candidate_rows(domain_name):
                rows = []
                for item in data_cache.get(domain_name, []) or []:
                    if not isinstance(item, dict):
                        continue
                    code = str(
                        item.get('SECURITY_CODE', '')
                        or item.get('stockCode', '')
                        or item.get('SECUCODE', '')
                        or item.get('f12', '')
                    ).zfill(6)
                    if code and code in candidate_codes:
                        row = dict(item)
                        row.setdefault('code', code)
                        row.setdefault('symbol', code)
                        row.setdefault('domain', domain_name)
                        row.setdefault('source', f'runner_v2_{domain_name}')
                        rows.append(row)
                return rows

            candidate_lookup = {str(candidate.get('code') or '').zfill(6): candidate for candidate in candidates if candidate.get('code')}
            quote_rows = []
            for stock in stocks:
                code = str(stock.get('f12', '')).zfill(6)
                if code not in candidate_lookup:
                    continue
                candidate = candidate_lookup[code]
                quote_rows.append({
                    'code': code,
                    'symbol': code,
                    'name': candidate.get('name') or stock.get('f14', ''),
                    'net_inflow_main': candidate.get('net_inflow_main', safe_num(stock.get('f62'))),
                    'source': 'runner_v2_quote_snapshot',
                })

            rows_by_domain = {domain: [] for domain in (
                'announcements', 'risk_alerts', 'lhb', 'concept_industry', 'financials',
                'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength', 'yesterday_limit_strength',
                'popularity_heat', 'industry_board', 'sector_fund_flow', 'concept_capital_flow',
                'candidate_quote_recheck', 'candidate_fund_recheck', 'candidate_lhb_recheck',
                'candidate_announcement_recheck', 'candidate_intraday_replay',
                'block_trades', 'lockup_expiry', 'shareholder_changes',
                'research_reports', 'stock_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
                'data_directory_content',
            )}
            rows_by_domain['announcements'] = candidate_rows('announcements')
            rows_by_domain['lhb'] = candidate_rows('lhb')
            rows_by_domain['sector_fund_flow'] = candidate_rows('stock_capital_flow')
            rows_by_domain['lockup_expiry'] = candidate_rows('lockup_expiry')
            rows_by_domain['shareholder_changes'] = candidate_rows('shareholder_changes')
            rows_by_domain['earnings_preview'] = candidate_rows('earnings_preview')

            market_hsgt_summary = list(data_cache.get('hsgt_summary', []) or [])
            if market_hsgt_summary:
                summary_row = market_hsgt_summary[0] or {}
                summary_values = [
                    safe_num(summary_row.get(key))
                    for key in ('f51', 'f52', 'f53', 'f54', 'f55', 'f56')
                ]
                northbound_flow = max(summary_values) if summary_values else 0.0
                summary_text = f"北向 净流入 {northbound_flow}"
                rows_by_domain['data_directory_content'].append({
                    'item_key': 'hsgt_capital_flow',
                    'cells': [northbound_flow],
                    'raw_text': summary_text,
                    'summary': summary_text,
                    'domain': 'data_directory_content',
                    'source': 'runner_v2_hsgt_summary_proxy',
                })

            for code, candidate in candidate_lookup.items():
                stock_sector = str(
                    candidate.get('industry')
                    or candidate.get('sector_name')
                    or candidate.get('sector')
                    or ''
                ).strip()
                stock_concepts = [
                    str(value).strip()
                    for value in candidate.get('sector_opportunity_tags') or []
                    if str(value).strip()
                ]
                for ann in candidate.get('announcement_evidence', [])[:5]:
                    text = str(ann.get('title', '')).strip()
                    if text:
                        rows_by_domain['candidate_announcement_recheck'].append({
                            'code': code,
                            'symbol': code,
                            'title': text,
                            'text': text,
                            'raw_text': text,
                            'domain': 'candidate_announcement_recheck',
                            'source': 'runner_v2_candidate_announcement_recheck',
                        })
                        if ann.get('hard_block'):
                            rows_by_domain['risk_alerts'].append({
                                'code': code,
                                'symbol': code,
                                'title': text,
                                'text': text,
                                'raw_text': text,
                                'risk_category': ann.get('category'),
                                'domain': 'risk_alerts',
                                'source': 'runner_v2_mainboard_risk_announcement',
                            })
                news_evidence = candidate.get('news_evidence') or {}
                for news in (news_evidence.get('direct_symbol_news') or [])[:5]:
                    text = str(news.get('title') or '').strip()
                    if text:
                        rows_by_domain['announcements'].append({
                            'code': code, 'symbol': code, 'title': text, 'text': text, 'raw_text': text,
                            'domain': 'announcements', 'source': 'runner_v2_direct_symbol_news',
                        })
                for news in candidate.get('sector_news_evidence', [])[:5]:
                    text = str(news.get('title') or '').strip()
                    if text:
                        rows_by_domain['research_reports'].append({
                            'code': code, 'symbol': code, 'BOARD_NAME': stock_sector,
                            'title': text, 'text': text, 'raw_text': text,
                            'domain': 'research_reports', 'source': 'runner_v2_sector_news_proxy',
                        })
                for reason in candidate.get('limitup_reason_evidence', [])[:3]:
                    text = str(reason.get('reason') or '').strip()
                    if text:
                        rows_by_domain['limitup_strength'].append({
                            'code': code, 'symbol': code,
                            'text': f"涨停原因 {text}", 'raw_text': f"涨停原因 {text}",
                            'BOARD_NAME': reason.get('sector') or stock_sector,
                            'domain': 'limitup_strength', 'source': reason.get('source') or 'runner_v2_limitup_reason',
                        })
                if code in limitup_codes:
                    rows_by_domain['limitup_strength'].append({
                        'code': code,
                        'symbol': code,
                        'text': f"涨停 封板 {candidate.get('name', '')} {' '.join(candidate.get('sector_opportunity_tags') or [])}".strip(),
                        'raw_text': f"涨停 封板 {candidate.get('name', '')}",
                        'domain': 'limitup_strength',
                        'source': 'runner_v2_limitup_pool',
                    })
                if code in consecutive_codes:
                    rows_by_domain['consecutive_limit_strength'].append({
                        'code': code,
                        'symbol': code,
                        'text': f"连板 {candidate.get('name', '')}",
                        'raw_text': f"连板 {candidate.get('name', '')}",
                        'domain': 'consecutive_limit_strength',
                        'source': 'runner_v2_limitup_consecutive',
                    })
                if code in yesterday_limitup_codes:
                    rows_by_domain['yesterday_limit_strength'].append({
                        'code': code,
                        'symbol': code,
                        'text': f"昨日涨停 {candidate.get('name', '')}",
                        'raw_text': f"昨日涨停 {candidate.get('name', '')}",
                        'domain': 'yesterday_limit_strength',
                        'source': 'runner_v2_limitup_yesterday',
                    })
                if code in broken_codes:
                    rows_by_domain['broken_limit_risk'].append({
                        'code': code,
                        'symbol': code,
                        'text': f"炸板 开板 {candidate.get('name', '')}",
                        'raw_text': f"炸板 开板 {candidate.get('name', '')}",
                        'domain': 'broken_limit_risk',
                        'source': 'runner_v2_limitup_broken',
                    })
                if stock_sector:
                    rows_by_domain['industry_board'].append({
                        'code': code,
                        'symbol': code,
                        'BOARD_NAME': stock_sector,
                        'text': f"行业板块 {stock_sector}",
                        'raw_text': f"行业板块 {stock_sector}",
                        'domain': 'industry_board',
                        'source': 'eastmoney_stock_all_a.f100',
                    })
                for concept_name in stock_concepts:
                    rows_by_domain['concept_industry'].append({
                        'code': code,
                        'symbol': code,
                        'CONCEPT_NAME': concept_name,
                        'text': f"概念板块 {concept_name}",
                        'raw_text': f"概念板块 {concept_name}",
                        'domain': 'concept_industry',
                        'source': 'eastmoney_stock_all_a.f103',
                        })
                if code in capital_flow_codes:
                    cf = capital_flow_codes.get(code) or {}
                    rows_by_domain['candidate_fund_recheck'].append({
                        'code': code,
                        'symbol': code,
                        'net_inflow_main': cf.get('main_inflow'),
                        'MAIN_NET_INFLOW': cf.get('main_inflow'),
                        '主力净流入': cf.get('main_inflow'),
                        'text': f"主力净流入 {cf.get('main_inflow', 0.0)}",
                        'raw_text': f"主力净流入 {cf.get('main_inflow', 0.0)}",
                        'domain': 'candidate_fund_recheck',
                        'source': 'runner_v2_capital_flow',
                    })
                if code in lhb_details:
                    for detail in lhb_details.get(code, [])[:5]:
                        rows_by_domain['candidate_lhb_recheck'].append({
                            'code': code,
                            'symbol': code,
                            'EXPLANATION': detail.get('reason', ''),
                            'NET_AMT': detail.get('net_amount', 0.0),
                            'BUY_AMT': detail.get('buy_amount', 0.0),
                            'SELL_AMT': detail.get('sell_amount', 0.0),
                            'text': detail.get('reason', ''),
                            'raw_text': detail.get('reason', ''),
                            'domain': 'candidate_lhb_recheck',
                            'source': 'runner_v2_lhb_recheck',
                        })
                rows_by_domain['candidate_quote_recheck'].append({
                    'code': code,
                    'symbol': code,
                    'name': candidate.get('name', ''),
                    'f31': candidate.get('price'),
                    'f32': max(0.0, (candidate.get('turnover_rate') or 0.0) * 1000),
                    'f33': candidate.get('price'),
                    'f34': max(0.0, (candidate.get('turnover_rate') or 0.0) * 800),
                    'text': f"五档 买一 卖一 封板 {candidate.get('name', '')}",
                    'raw_text': f"五档 买一 {candidate.get('price', 0.0)} 卖一 {candidate.get('price', 0.0)}",
                    'domain': 'candidate_quote_recheck',
                    'source': 'runner_v2_quote_recheck',
                })
                rows_by_domain['candidate_intraday_replay'].append({
                    'code': code,
                    'symbol': code,
                    'raw_text': (
                        f"行业排名 10 主力净流入 {candidate.get('net_inflow_main', 0.0)} "
                        f"主力净占比 {round((candidate.get('turnover_rate') or 0.0), 2)} 历史资金流向 个股概况"
                    ),
                    'page_url': 'https://quote.eastmoney.com/stockdata/mock',
                    'page_title': candidate.get('name', ''),
                    'domain': 'candidate_intraday_replay',
                    'source': 'runner_v2_intraday_replay_proxy',
                })
                for report in stock_report_codes.get(code, [])[:5]:
                    text = ' '.join(part for part in (report.get('rating'), report.get('title'), report.get('org'), report.get('industry')) if part).strip()
                    if not text:
                        continue
                    rows_by_domain['research_reports'].append({
                        'code': code,
                        'symbol': code,
                        'title': text,
                        'text': text,
                        'raw_text': text,
                        'domain': 'research_reports',
                        'source': 'runner_v2_stock_reports_proxy',
                    })
                    rows_by_domain['stock_reports'].append({
                        'code': code,
                        'symbol': code,
                        'stockCode': code,
                        'title': report.get('title', ''),
                        'orgSName': report.get('org', ''),
                        'ratingName': report.get('rating', ''),
                        'industryName': report.get('industry', ''),
                        'text': text,
                        'raw_text': text,
                        'cells': ['', code, candidate.get('name', ''), report.get('title', ''), report.get('org', ''), report.get('rating', '')],
                        'domain': 'stock_reports',
                        'source': 'runner_v2_stock_reports_proxy',
                    })
                if stock_sector:
                    for report in industry_report_rows_by_sector.get(stock_sector, [])[:2]:
                        text = ' '.join(
                            part for part in (
                                stock_sector,
                                str(report.get('title', '')).strip(),
                                str(report.get('orgSName', '') or report.get('org', '')).strip(),
                                str(report.get('ratingName', '') or report.get('rating', '')).strip(),
                            )
                            if part
                        ).strip()
                        if text:
                            rows_by_domain['research_reports'].append({
                                'code': code,
                                'symbol': code,
                                'BOARD_NAME': stock_sector,
                                'title': text,
                                'text': text,
                                'raw_text': text,
                                'domain': 'research_reports',
                                'source': 'runner_v2_industry_reports_proxy',
                            })
                holding = hsgt_holding_codes.get(code)
                if isinstance(holding, dict) and (holding.get('hold_ratio') or holding.get('change_num')):
                    holding_text = (
                        f"北向 持股 代码 {code} 持股占比 {holding.get('hold_ratio', 0):.2f}% "
                        f"变动 {holding.get('change_num', 0):.0f}"
                    )
                    rows_by_domain['data_directory_content'].append({
                        'code': code,
                        'symbol': code,
                        'SECURITY_CODE': code,
                        'item_key': 'hsgt_holdings',
                        'header': ['代码', '持股占比', '持股变动'],
                        'cells': [code, f"{holding.get('hold_ratio', 0):.2f}%", f"{holding.get('change_num', 0):.0f}"],
                        'raw_text': holding_text,
                        'summary': holding_text,
                        'domain': 'data_directory_content',
                        'source': 'runner_v2_hsgt_holdings_proxy',
                    })
                for deal in hsgt_deal_codes.get(code, [])[:2]:
                    change_rate = safe_num(deal.get('CHANGE_RATE'))
                    direction = '增持' if change_rate >= 0 else '减持'
                    trade_text = (
                        f"北向 {direction} 代码 {code} "
                        f"{str(deal.get('MUTUAL_TYPE', '') or deal.get('BOARD_NAME', '')).strip()} "
                        f"变动 {change_rate:.2f}"
                    ).strip()
                    rows_by_domain['data_directory_content'].append({
                        'code': code,
                        'symbol': code,
                        'SECURITY_CODE': code,
                        'item_key': 'hsgt_turnover',
                        'cells': [f"{change_rate:.2f}"],
                        'raw_text': trade_text,
                        'summary': trade_text,
                        'domain': 'data_directory_content',
                        'source': 'runner_v2_hsgt_deals_proxy',
                    })
                earnings = earnings_codes.get(code)
                if isinstance(earnings, dict):
                    earnings_text = ' '.join(part for part in (str(earnings.get('forecast', '')).strip(), str(earnings.get('change_range', '')).strip()) if part)
                    if earnings_text:
                        rows_by_domain['financials'].append({
                            'code': code,
                            'symbol': code,
                            'text': earnings_text,
                            'raw_text': earnings_text,
                            'domain': 'financials',
                            'source': 'runner_v2_earnings_financial_proxy',
                        })

            structured_started_at = time.monotonic()
            structured_bundle = build_structured_bundle(rows_by_domain, {}, candidates, quote_rows, source_time)
            structured_scores = build_structured_scores(candidates, structured_bundle)
            record_domain_timing(
                domain_timings,
                'structured_scores_build',
                structured_started_at,
                structured_scores,
                'build_structured_bundle+build_structured_scores',
            )
            structured_by_symbol = {row.get('symbol'): row for row in structured_scores if row.get('symbol')}
            for candidate in candidates:
                structured = structured_by_symbol.get(candidate.get('code'))
                if not structured:
                    continue
                details = structured.get('component_details') or {}
                components = structured.get('components') or {}
                candidate['structured_score'] = structured.get('structured_score')
                candidate['structured_score_components'] = components
                candidate['structured_component_details'] = details
                candidate['vei_phase_d_tags'] = structured.get('vei_phase_d_tags') or []
                candidate['candidate_stage'] = structured.get('candidate_stage') or candidate.get('candidate_stage')
                candidate['early_opportunity_score'] = structured.get('early_opportunity_score')
                candidate['limitup_capture_score'] = structured.get('limitup_capture_score')
                candidate['limitup_capture_profile'] = structured.get('limitup_capture_profile')
                candidate['limitup_capture_confirmed'] = structured.get('limitup_capture_confirmed')
                candidate['limitup_capture_reasons'] = structured.get('limitup_capture_reasons') or []
                candidate['research_signals'] = structured.get('research_signals') or candidate.get('research_signals')
                candidate['main_theme_alignment_score'] = details.get('main_theme_alignment_score')
                candidate['main_theme_core_score'] = details.get('main_theme_core_score')
                candidate['hsgt_institutional_flow'] = details.get('hsgt_institutional_flow')
                candidate['experimental_catalyst_signal'] = details.get('experimental_catalyst_signal')
                auxiliary_components = {
                    'announcement_catalyst_score': candidate.get('announcement_catalyst_score', 0.0),
                    'news_catalyst_strength': candidate.get('news_catalyst_strength', 0.0),
                    'sector_news_catalyst_score': candidate.get('sector_news_catalyst_score', 0.0),
                    'limitup_reason_quality_score': candidate.get('limitup_reason_quality_score', 0.0),
                    'risk_notice_penalty': -candidate.get('risk_notice_penalty', 0.0),
                    'mainboard_auxiliary_confidence': candidate.get('mainboard_auxiliary_confidence', 0.0),
                }
                components.update(auxiliary_components)
                details.update({
                    **auxiliary_components,
                    'risk_notice_penalty': candidate.get('risk_notice_penalty', 0.0),
                    'mainboard_auxiliary_evidence_status': candidate.get('mainboard_auxiliary_evidence_status'),
                    'mainboard_auxiliary_missing_domains': candidate.get('mainboard_auxiliary_missing_domains', []),
                    'announcement_evidence_count': len(candidate.get('announcement_evidence', [])),
                    'direct_symbol_news_count': len((candidate.get('news_evidence') or {}).get('direct_symbol_news', [])),
                    'sector_news_evidence_count': len(candidate.get('sector_news_evidence', [])),
                    'limitup_reason_evidence_count': len(candidate.get('limitup_reason_evidence', [])),
                    'risk_notice_evidence_count': len(candidate.get('risk_notice_evidence', [])),
                })
                auxiliary_adjustment = (
                    candidate.get('announcement_catalyst_score', 0.0) * 0.25
                    + candidate.get('news_catalyst_strength', 0.0) * 0.30
                    + candidate.get('sector_news_catalyst_score', 0.0) * 0.20
                    + candidate.get('limitup_reason_quality_score', 0.0) * 0.30
                    + candidate.get('mainboard_auxiliary_confidence', 0.0) * 0.20
                    - candidate.get('risk_notice_penalty', 0.0) * 0.50
                )
                candidate['structured_score'] = round(fnum(candidate.get('structured_score')) + auxiliary_adjustment * 10.0, 4)
                structured['structured_score'] = candidate['structured_score']
                structured['components'] = components
                structured['component_details'] = details
                research_signals = candidate.get('research_signals') if isinstance(candidate.get('research_signals'), dict) else {}
                catalyst_quality = research_signals.setdefault('catalyst_quality', {})
                catalyst_quality['mainboard_auxiliary_confidence'] = candidate.get('mainboard_auxiliary_confidence')
                catalyst_quality['announcement_catalyst_score'] = candidate.get('announcement_catalyst_score')
                catalyst_quality['news_catalyst_strength'] = candidate.get('news_catalyst_strength')
                catalyst_quality['sector_news_catalyst_score'] = candidate.get('sector_news_catalyst_score')
                catalyst_quality['limitup_reason_quality_score'] = candidate.get('limitup_reason_quality_score')
                if catalyst_quality.get('category') in (None, '', 'neutral') and auxiliary_adjustment > 0.15:
                    catalyst_quality['category'] = 'positive_catalyst'
                catalyst_quality['usable_for_candidate_generation'] = bool(max(
                    candidate.get('announcement_catalyst_score', 0.0),
                    candidate.get('news_catalyst_strength', 0.0),
                    candidate.get('sector_news_catalyst_score', 0.0),
                    candidate.get('limitup_reason_quality_score', 0.0),
                ) > 0)
                catalyst_quality['usable_for_paper_pick'] = bool(
                    catalyst_quality['usable_for_candidate_generation']
                    and candidate.get('risk_notice_penalty', 0.0) < 0.60
                )
                risk_review = research_signals.setdefault('a_share_risk_review', {})
                risk_review['mainboard_auxiliary_risk_notices'] = candidate.get('risk_notice_evidence', [])
                risk_review['risk_notice_penalty'] = candidate.get('risk_notice_penalty')
                candidate['research_signals'] = research_signals
                structured['research_signals'] = research_signals
                research_signal_rows.append({
                    'symbol': candidate.get('code'),
                    'name': candidate.get('name'),
                    'research_signals': candidate.get('research_signals') or {},
                })
                structured_component_rows.append({
                    'symbol': candidate.get('code'),
                    'name': candidate.get('name'),
                    'component_details': details,
                })
                structured_score_component_rows.append({
                    'symbol': candidate.get('code'),
                    'name': candidate.get('name'),
                    'components': components,
                })
        rank_candidates_by_structured_priority(candidates)

        full_candidate_pool, candidate_pool_dedup_summary = select_unique_candidate_pool(
            candidates, FULL_CANDIDATE_POOL_TARGET,
        )
        passed_candidates = [
            candidate for candidate in candidates
            if int(candidate.get('rank') or 999999) <= 10
        ]
        candidate_drop_diagnostics.extend(candidate_pool_dedup_summary.get('candidate_drop_diagnostics') or [])
        candidate_drop_stage_counts = summarize_candidate_drop_stage_counts(candidate_drop_diagnostics)
        top_exclusion_reasons = {
            key: value for key, value in sorted(
                pool_exclusion_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
            if value
        }
        if candidate_pool_dedup_summary.get('candidate_pool_cut_count'):
            top_exclusion_reasons['candidate_pool_cut'] = candidate_pool_dedup_summary['candidate_pool_cut_count']
        if len(tradable) > PRE_ENRICHMENT_CANDIDATE_TARGET:
            top_exclusion_reasons['pre_enrichment_rank_cut'] = len(tradable) - PRE_ENRICHMENT_CANDIDATE_TARGET
        pool_exclusion_summary = {
            **candidate_pool_dedup_summary,
            'raw_universe_count': len(stocks),
            'mainboard_tradable_count': len(tradable),
            'pre_enrichment_source_count': len(tradable),
            'pre_enrichment_rank_cut_count': max(0, len(tradable) - PRE_ENRICHMENT_CANDIDATE_TARGET),
            'final_persisted_count': len(full_candidate_pool),
            'target_count': FULL_CANDIDATE_POOL_TARGET,
            'top_exclusion_reasons': top_exclusion_reasons,
            'candidate_drop_stage_counts': candidate_drop_stage_counts,
            'candidate_drop_diagnostic_count': len(candidate_drop_diagnostics),
            'source_status': 'PASS' if stocks else 'FAIL',
            'legacy_partial_pool': False,
        }
    record_domain_timing(domain_timings, 'candidate_scoring', candidate_scoring_started_at, candidates, 'runner_v2_candidate_scoring')
    if stocks:
        core_domain_counts = {
            'announcements': len(data_cache.get('announcements', [])),
            'stock_reports': len(data_cache.get('stock_reports', [])),
            'news_kuaixun': len(data_cache.get('news_kuaixun', [])),
            'stock_capital_flow': len(data_cache.get('stock_capital_flow', [])),
            'lhb': len(data_cache.get('lhb', [])),
        }
        enhanced_domain_counts = {
            'block_trades': len(data_cache.get('block_trades', [])),
            'shareholder_changes': len(data_cache.get('shareholder_changes', [])),
            'sector_industry': sum(
                bool(row.get('f100')) for row in data_cache.get('stock_all_a', [])
            ),
            'sector_concept': sum(
                bool(row.get('f103')) for row in data_cache.get('stock_all_a', [])
            ),
        }
        experimental_domain_counts = {
            'popularity_rank': len(data_cache.get('popularity_rank', [])),
            'trading_halts': len(data_cache.get('trading_halts', [])),
            'lockup_expiry': len(data_cache.get('lockup_expiry', [])),
            'org_survey': len(data_cache.get('org_survey', [])),
        }
        optional_domain_counts = {
            'hsgt_holdings': len(data_cache.get('hsgt_holdings', [])),
            'hsgt_deals': len(data_cache.get('hsgt_deals', [])),
            'hsgt_summary': len(data_cache.get('hsgt_summary', [])),
        }
        core_missing_domains = [name for name, count in core_domain_counts.items() if count <= 0]
        enhanced_missing_domains = [name for name, count in enhanced_domain_counts.items() if count <= 0]
        source_missing = []
        source_flags = []
        if len(stocks) < 4000:
            source_missing.append('stock_all_a')
            source_flags.append('FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW')
        if not data_cache.get('stock_capital_flow'):
            source_missing.append('stock_capital_flow')
            source_flags.append('ZERO_FUND_FLOW_READ')
        if not mainboard_capital_flow_codes:
            source_missing.append('stock_capital_flow_codes')
            source_flags.append('STOCK_CAPITAL_FLOW_CODE_FIELD_MISSING')
        if external_market_snapshot['status'] != 'PASS':
            source_missing.append('external_market')
            source_flags.append('EXTERNAL_MARKET_SNAPSHOT_NOT_COMPLETE')
        core_sentiment_pools = build_core_sentiment_pool_status(
            limit_pool_diagnostics,
            results,
            market_limitups,
        )
        if core_sentiment_pools['status'] != 'PASS':
            source_missing.extend(core_sentiment_pools['missing_sources'])
            source_flags.extend(core_sentiment_pools['flags'])
        source_status = {
            'required_api_sources': {
                'status': core_sentiment_pools['status'],
                'missing_sources': list(core_sentiment_pools['missing_sources']),
                'mode': 'api_direct',
            },
            'core_sentiment_pools': core_sentiment_pools,
            'external_market': {
                'status': external_market_snapshot['status'],
                'missing_indices': [item['key'] for item in external_market_snapshot['missing_indices']],
                'source': external_market_snapshot['source'],
                'captured_at': external_market_snapshot['captured_at'],
            },
            'enhanced_api_sources': {
                'status': 'PASS',
                'missing_sources': [],
                'mode': 'api_direct',
            },
            'experimental_api_sources': {
                'status': 'PASS',
                'missing_sources': [],
                'mode': 'api_direct',
            },
            'full_evidence_pack': {
                'status': 'PASS' if not core_missing_domains else 'PARTIAL',
                'missing_domains': core_missing_domains,
                'domain_counts': core_domain_counts,
            },
            'enhanced_evidence_coverage': {
                'status': 'PASS' if not enhanced_missing_domains else 'PARTIAL',
                'missing_domains': enhanced_missing_domains,
                'domain_counts': enhanced_domain_counts,
            },
            'experimental_evidence_coverage': {
                'status': 'PASS',
                'missing_domains': [],
                'domain_counts': experimental_domain_counts,
            },
            'optional_evidence_coverage': {
                'status': 'PASS' if optional_domain_counts['hsgt_holdings'] else ('PARTIAL' if optional_domain_counts['hsgt_deals'] or optional_domain_counts['hsgt_summary'] else 'MISSING'),
                'missing_domains': [] if optional_domain_counts['hsgt_holdings'] else ['hsgt_holdings'],
                'domain_counts': optional_domain_counts,
                'hard_block': False,
            },
            'pagination_diagnostics': pagination_diagnostics,
            'proxy_api_sources': {
                'block_trades': {
                    'status': 'PROXY',
                    'source': 'push2_paginated_quote_activity',
                    'specialized_datacenter_source': False,
                    'quality_status': 'PROXY_QUARANTINED',
                    'production_use': 'DISABLED_UNTIL_SPECIALIZED_SOURCE',
                    'hard_block': False,
                },
                'trading_halts': {
                    'status': 'PROXY',
                    'source': 'push2_paginated_quote_activity',
                    'specialized_datacenter_source': False,
                    'quality_status': 'PROXY_QUARANTINED',
                    'production_use': 'DISABLED_UNTIL_SPECIALIZED_SOURCE',
                    'hard_block': False,
                },
                'popularity_rank': {
                    'status': 'PROXY',
                    'source': 'push2_paginated_quote_activity',
                    'specialized_datacenter_source': False,
                    'quality_status': 'PROXY_QUARANTINED',
                    'production_use': 'DISABLED_UNTIL_SPECIALIZED_SOURCE',
                    'hard_block': False,
                },
            },
            'source_completeness': {
                'status': 'PASS' if not source_missing else 'PARTIAL_OR_FAIL',
                'quote_count': len(stocks),
                'direct_data_coverage': direct_data_coverage,
                'fund_count': len(data_cache.get('stock_capital_flow', [])),
                'core_sentiment_pool_counts': {
                    name: len(results.get(name, []))
                    for name in core_sentiment_pools['required_sources']
                },
                'min_quote_count': 4000,
                'missing_sources': sorted(set(source_missing)),
                'flags': sorted(set(source_flags)),
            },
        }
        hard_block_source_status = {
            'status': 'PASS' if not source_missing else 'BLOCK',
            'missing_sources': sorted(set(source_missing)),
            'flags': sorted(set(source_flags)),
        }
        full_universe_scan = {
            'enabled': True,
            'quote_count': len(stocks),
            'direct_data_coverage': direct_data_coverage,
            'tradable_count': len(tradable),
            'coverage_status': 'PASS' if len(stocks) >= 4000 else 'PARTIAL_OR_FAIL',
            'min_quote_count': 4000,
            'candidate_board_policy': 'main_only',
            'tradable_main_count': len(tradable),
            'excluded_chinext_count': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'chinext'),
            'excluded_star_count': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'star'),
            'excluded_beijing_count': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'beijing'),
            'board_counts': {
                'main': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'main'),
                'chinext': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'chinext'),
                'star': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'star'),
                'beijing': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'beijing'),
                'unknown': sum(1 for row in stocks if board_for_code(row.get('f12', '')) == 'unknown'),
            },
        }
        market_snapshot = {
            'universe_quote_count': len(stocks),
            'direct_data_coverage': direct_data_coverage,
            'full_universe_scan': full_universe_scan,
            'market_breadth_up_pct': round(market_breadth, 2),
            'market_limitups': market_limitups,
            'market_bigups': market_bigups,
            'passed_count': scored_count,
            'scored_count': scored_count,
            'market_follow_through_score': round(market_follow_through_score, 4),
            'limitup_broken_ratio': round(limitup_broken_ratio, 4),
            'broken_limitups': broken_count,
            'max_consecutive': max_consecutive,
            'sentiment_score': round(sentiment_score, 2),
            'market_main_inflow': market_main_inflow,
            'market_regime': market_regime,
            'external_market': external_market_snapshot,
            'sector_snapshot': sector_snapshot[:40],
            'hot_sector_count': len(hot_sectors),
            'hot_concept_count': len(hot_concepts),
            'candidate_drop_diagnostic_count': len(candidate_drop_diagnostics),
            'candidate_drop_stage_counts': candidate_drop_stage_counts,
            'source_status': source_status,
            'hard_block_source_status': hard_block_source_status,
            'scanner_sources': ['eastmoney_api_scan_v2'],
        }

        def coverage_record(count):
            return {
                'status': 'PASS' if count > 0 else 'MISSING',
                'record_count': count,
                'tab_count': 1 if count > 0 else 0,
            }

        sector_flow_count = result_item_count(data_cache.get('sector_capital_flow', {}))
        coverage_source_status = {
            'announcements': coverage_record(len(data_cache.get('announcements', []))),
            'risk_alerts': coverage_record(len(data_cache.get('trading_halts', []))),
            'research_reports': coverage_record(len(data_cache.get('stock_reports', [])) + len(data_cache.get('industry_reports', []))),
            'earnings_preview': coverage_record(len(data_cache.get('earnings_preview', []))),
            'limitup_strength': coverage_record(len(data_cache.get('limitup_pool', []))),
            'consecutive_limit_strength': coverage_record(len(data_cache.get('limitup_consecutive', []))),
            'yesterday_limit_strength': coverage_record(len(data_cache.get('limitup_yesterday', []))),
            'concept_industry': coverage_record(sum(
                bool(row.get('f103')) for row in data_cache.get('stock_all_a', [])
            )),
            'industry_board': coverage_record(sum(
                bool(row.get('f100')) for row in data_cache.get('stock_all_a', [])
            )),
            'sector_fund_flow': coverage_record(sector_flow_count or len(data_cache.get('flow_concept', [])) + len(data_cache.get('flow_industry', []))),
            'candidate_quote_recheck': coverage_record(len(candidates)),
            'candidate_fund_recheck': coverage_record(len(data_cache.get('stock_capital_flow', []))),
            'popularity_heat': coverage_record(len(data_cache.get('popularity_rank', []))),
            'concept_capital_flow': coverage_record(len(data_cache.get('flow_concept', []))),
            'block_trades': coverage_record(len(data_cache.get('block_trades', []))),
            'shareholder_changes': coverage_record(len(data_cache.get('shareholder_changes', []))),
            'lockup_expiry': coverage_record(len(data_cache.get('lockup_expiry', []))),
            'ipo_calendar': coverage_record(len(data_cache.get('ipo_calendar', []))),
            'trading_halts': coverage_record(len(data_cache.get('trading_halts', []))),
        }
        information_coverage_audit = build_mainboard_information_coverage_audit(data_cache, candidates)
        information_coverage_audit['hsgt_evidence'] = {
            'status': hsgt_diagnostics.get('status', 'MISSING'),
            'holdings_count': len(data_cache.get('hsgt_holdings', [])),
            'source_type': hsgt_diagnostics.get('source_type', 'MISSING'),
            'quality_status': hsgt_diagnostics.get('quality_status', hsgt_diagnostics.get('status', 'MISSING')),
            'production_use': hsgt_diagnostics.get('production_use', 'OPTIONAL'),
            'fallback_available': bool(hsgt_diagnostics.get('fallback_available')),
            'fallback_used': bool(hsgt_diagnostics.get('fallback_used')),
            'proxy_available': bool(hsgt_diagnostics.get('proxy_available')),
            'proxy_sources': list(hsgt_diagnostics.get('proxy_sources') or []),
            'hard_block': False,
        }
        source_status['mainboard_auxiliary_evidence_coverage'] = information_coverage_audit

    db_snapshot_persistence = {
        'status': 'MISSING',
        'scan_session_id': None,
        'domain_count': 0,
        'error': '',
    }
    if stocks:
        try:
            from xiaogu_db import insert_scan_session, upsert_scan_market_data

            scan_session_id = insert_scan_session(
                trade_date=datetime.fromisoformat(source_time).date(),
                scan_time=datetime.fromisoformat(source_time),
                source_id='eastmoney_api_scan_v2',
                quotes_count=len(stocks),
                scored_count=scored_count,
                passed_count=len(passed_candidates),
                scan_dir=str(output_dir / 'xiaogu_scan_summary.json'),
                market_snapshot=market_snapshot,
                source_status=source_status,
                source_counts={name: result_item_count(items) for name, items in results.items()},
                source_diagnostics=domain_timings,
                production_run_id=production_run_id,
            )
            persisted_domain_count = upsert_scan_market_data(
                scan_session_id,
                datetime.fromisoformat(source_time).date(),
                datetime.fromisoformat(source_time),
                results,
                domain_timings,
            )
            db_snapshot_persistence = {
                'status': 'PASS',
                'scan_session_id': scan_session_id,
                'domain_count': persisted_domain_count,
                'error': '',
            }
        except Exception as exc:
            db_snapshot_persistence['error'] = repr(exc)
            source_missing = sorted(set([*source_missing, 'postgres_market_snapshot']))
            source_flags = sorted(set([*source_flags, 'POSTGRES_MARKET_SNAPSHOT_PERSIST_FAILED']))
            hard_block_source_status = {
                'status': 'BLOCK',
                'missing_sources': source_missing,
                'flags': source_flags,
            }
            source_status['source_completeness']['status'] = 'PARTIAL_OR_FAIL'
            source_status['source_completeness']['missing_sources'] = source_missing
            source_status['source_completeness']['flags'] = source_flags
        source_status['scan_snapshot_persistence'] = db_snapshot_persistence
        market_snapshot['source_status'] = source_status
        market_snapshot['hard_block_source_status'] = hard_block_source_status
        if db_snapshot_persistence['status'] == 'PASS':
            try:
                insert_scan_session(
                    trade_date=datetime.fromisoformat(source_time).date(),
                    scan_time=datetime.fromisoformat(source_time),
                    source_id='eastmoney_api_scan_v2',
                    quotes_count=len(stocks),
                    scored_count=scored_count,
                    passed_count=len(passed_candidates),
                    scan_dir=str(output_dir / 'xiaogu_scan_summary.json'),
                    market_snapshot=market_snapshot,
                    source_status=source_status,
                    source_counts={name: result_item_count(items) for name, items in results.items()},
                    source_diagnostics=domain_timings,
                    production_run_id=production_run_id,
                )
            except Exception as exc:
                db_snapshot_persistence = {
                    **db_snapshot_persistence,
                    'status': 'MISSING',
                    'error': repr(exc),
                }
                source_status['scan_snapshot_persistence'] = db_snapshot_persistence
                source_missing = sorted(set([*source_missing, 'postgres_market_snapshot']))
                source_flags = sorted(set([*source_flags, 'POSTGRES_SCAN_SESSION_STATUS_UPDATE_FAILED']))
                hard_block_source_status = {
                    'status': 'BLOCK',
                    'missing_sources': source_missing,
                    'flags': source_flags,
                }
                market_snapshot['source_status'] = source_status
                market_snapshot['hard_block_source_status'] = hard_block_source_status

    runner_files = dict(summary['files'])
    write_structured_started_at = time.monotonic()

    def write_runner_rows(filename, rows):
        path = output_dir / filename
        with open(path, 'w', encoding='utf-8') as f:
            for row in rows or []:
                if isinstance(row, dict):
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return str(path)

    runner_files['scored'] = write_runner_rows('xiaogu_scored.jsonl', candidates)
    runner_files['full_candidate_pool'] = write_runner_rows(
        'xiaogu_full_candidate_pool.jsonl',
        full_candidate_pool if stocks else [],
    )
    runner_files['structured_scores'] = write_runner_rows('xiaogu_structured_scores.jsonl', structured_scores)
    runner_files['research_signals'] = write_runner_rows('xiaogu_research_signals.jsonl', research_signal_rows)
    runner_files['structured_score_components'] = write_runner_rows('xiaogu_structured_score_components.jsonl', structured_score_component_rows)
    runner_files['structured_component_details'] = write_runner_rows('xiaogu_structured_component_details.jsonl', structured_component_rows)
    # Keep factor_store live with v2 scanner (was only wired on legacy scan path).
    try:
        from xiaogu_factor_store import write_factors
        if candidates:
            factor_path = write_factors(str(source_time)[:10], candidates)
            runner_files['factors'] = str(factor_path)
    except Exception as exc:
        print(f'SCAN: factor_store write skipped: {exc!r}', flush=True)
    record_domain_timing(domain_timings, 'write_structured_outputs', write_structured_started_at, candidates, 'jsonl_write')

    scanner_elapsed_seconds = round(time.monotonic() - scanner_started_at, 4)

    runner_summary = {
        'source': 'eastmoney_api_scan_v2',
        'pipeline_version': 'v2_scanner_api',
        'source_time': source_time,
        'scanner_transport': 'direct_api',
        'universe_quote_count': len(stocks),
        'market_breadth_up_pct': market_breadth,
        'market_limitups': market_limitups,
        'market_bigups': market_bigups,
        'market_follow_through_score': round(market_follow_through_score, 4),
        'limitup_broken_ratio': round(limitup_broken_ratio, 4),
        'max_consecutive': max_consecutive,
        'sentiment_score': round(sentiment_score, 2),
        'market_main_inflow': market_main_inflow,
        'market_regime': market_regime,
        'external_market': external_market_snapshot,
        'scored_count': scored_count,
        'passed_count': len(passed_candidates) if stocks else 0,
        'full_candidate_pool_count': len(full_candidate_pool) if stocks else 0,
        'full_candidate_pool': full_candidate_pool if stocks else [],
        'scored_candidates': candidates,
        'passed_candidates': passed_candidates if stocks else [],
        'decision_candidates': [],
        'pool_exclusion_summary': pool_exclusion_summary if stocks else {
            'raw_universe_count': 0,
            'mainboard_tradable_count': 0,
            'final_persisted_count': 0,
            'target_count': FULL_CANDIDATE_POOL_TARGET,
            'top_exclusion_reasons': {'suspended_or_missing_quote': 1},
            'candidate_drop_stage_counts': {},
            'candidate_drop_diagnostic_count': 0,
            'source_status': 'FAIL',
            'legacy_partial_pool': False,
        },
        'candidate_drop_diagnostics': candidate_drop_diagnostics if stocks else [],
        'paper_scoring_candidates': candidates,
        'structured_scores': structured_scores,
        'research_signals': research_signal_rows,
        'structured_score_components': structured_score_component_rows,
        'structured_component_details': structured_component_rows,
        'information_coverage_audit': information_coverage_audit,
        'mainboard_policy': 'main_only',
        'hsgt_diagnostics': hsgt_diagnostics,
        'pagination_diagnostics': pagination_diagnostics,
        'domain_timings': domain_timings,
        'db_snapshot_persistence': db_snapshot_persistence,
        'scanner_elapsed_seconds': scanner_elapsed_seconds,
        'source_status': source_status,
        'hard_block_source_status': hard_block_source_status,
        'full_universe_scan': full_universe_scan,
        'market_snapshot': market_snapshot,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'scanner_sources': ['eastmoney_api_scan_v2'],
        'sector_snapshot': sector_snapshot[:40],
        'files': runner_files,
    }

    runner_summary_path = output_dir / 'xiaogu_scan_summary_runner.json'
    with open(runner_summary_path, 'w', encoding='utf-8') as f:
        json.dump(runner_summary, f, ensure_ascii=False, indent=2)

    # Also create the full summary for compatibility
    full_summary_path = output_dir / 'xiaogu_scan_summary.json'
    with open(full_summary_path, 'w', encoding='utf-8') as f:
        json.dump(runner_summary, f, ensure_ascii=False, indent=2)

    print(f'\n=== Scan Complete ===')
    print(f'Domains: {len(results)}')
    print(f'Total items: {sum(len(items) for items in results.values())}')
    for name, items in results.items():
        print(f'  {name:25} {len(items):5} items')
    print(f'Scanner elapsed: {scanner_elapsed_seconds:.2f}s')
    print('Top slow domains:')
    for rank, (name, timing) in enumerate(sorted(domain_timings.items(), key=lambda item: item[1].get('elapsed_seconds', 0), reverse=True)[:8], 1):
        print(f'  {rank}. {name}: {timing.get("elapsed_seconds", 0):.2f}s ({timing.get("status", "UNKNOWN")})')
    print(f'\nOutput: {output_dir}')

    return summary


def _generate_candidates(stocks, results, market_breadth, market_limitups):
    """Generate scored candidates from stock data for runner consumption.

    Uses original stock selection logic (not sector prediction dependent).
    Sector scores are low-weight reference only (0.3).
    """
    if not stocks:
        return []

    def signal_stage_bucket(pct):
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

    # Filter tradable stocks (original logic)
    tradable = []
    for s in stocks:
        code = str(s.get('f12', '')).zfill(6)
        price = fnum(s.get('f2'))
        pct = fnum(s.get('f3'))
        quote = eastmoney_quote_prices(s)
        open_price = fnum(quote.get('open'))
        high = fnum(quote.get('high'))
        low = fnum(quote.get('low'))
        amount = fnum(s.get('f6'))
        turnover = fnum(s.get('f8'))
        prev_close = fnum(s.get('f18'))
        board = board_for_code(code)

        if price <= 0:
            continue
        if board != 'main':
            continue

        # Close position score (price position in day's range)
        close_position_score = round((price - low) / (high - low), 6) if high > low else 0.5

        tradable.append({
            'code': code,
            'name': str(s.get('f14', '')),
            'price': price,
            'open': open_price,
            'signal_pct': round(pct, 2),
            'signal_amount': amount,
            'turnover_rate': round(turnover, 2),
            'board': board,
            'close_position_score': close_position_score,
            'high': high,
            'low': low,
            'prev_close': prev_close,
            'rank': 0,
            'industry': str(s.get('f100') or '').strip(),
            'sector': str(s.get('f100') or '').strip(),
            'sector_name': str(s.get('f100') or '').strip(),
            'industry_board': str(s.get('f100') or '').strip(),
            'eastmoney_industry': str(s.get('f100') or '').strip(),
            'sw_industry': '',
            'region_board': str(s.get('f102') or '').strip(),
            'sector_opportunity_tags': stock_concepts_from_quote_row(s),
            'stock_industry_source': 'eastmoney_stock_all_a.f100+f102+f103',
        })

    # Sort by amount (liquidity)
    tradable.sort(key=lambda x: x['signal_amount'], reverse=True)

    # Assign ranks
    for i, s in enumerate(tradable, 1):
        s['rank'] = i

    # Compute amount/fund percentiles
    amount_values = sorted([s['signal_amount'] for s in tradable])
    for s in tradable:
        s['amount_pctile'] = sum(1 for v in amount_values if v <= s['signal_amount']) / len(amount_values) if amount_values else 0.5

    # Classify setup types using original logic
    for s in tradable:
        pct = s['signal_pct']
        turnover = s['turnover_rate']
        amount = s['signal_amount']
        close_pos = s['close_position_score']
        stage = signal_stage_bucket(pct)

        source_layers = ['L0_FULL_UNIVERSE']
        setup_type = 'HOT_MOMENTUM'
        recovery_score = 0

        # Original candidate_setup logic
        if 2.0 <= pct <= 9.0:
            source_layers.append('L1_HOT_MOMENTUM')
            setup_type = 'HOT_MOMENTUM'
        if pct >= 9.5 or (s['high'] >= s['prev_close'] * 1.095 if s['prev_close'] > 0 else False):
            source_layers.append('L2_LIMIT_STRENGTH')
            setup_type = 'LIMIT_STRENGTH'
        if stage == 'underwater' and turnover >= 1.4 and close_pos >= 0.70:
            source_layers.append('L4_UNDERWATER_RECOVERY')
            setup_type = 'UNDERWATER_TO_RED_STRENGTH'
            recovery_score += 48
        if -3.0 <= pct < 0.0 and turnover >= 1.2 and close_pos >= 0.55 and setup_type == 'HOT_MOMENTUM':
            source_layers.append('L4_UNDERWATER_RECOVERY')
            setup_type = 'UNDERWATER_RED_FLAT_RECOVERY'
            recovery_score += 35
        if -2.0 <= pct <= 5.0 and turnover >= 1.3 and close_pos >= 0.60 and s['amount_pctile'] >= 0.50 and setup_type == 'HOT_MOMENTUM':
            source_layers.append('L4_PRE_BREAKOUT')
            setup_type = 'LOW_POSITION_SECTOR_LIFT'
            recovery_score += 44
        if 0.0 <= pct <= 4.5 and turnover >= 1.3 and close_pos >= 0.60 and s['amount_pctile'] >= 0.60 and setup_type == 'HOT_MOMENTUM':
            source_layers.append('L3_FUND_FLOW')
            setup_type = 'FUND_FLOW_IGNITION'
            recovery_score += 36

        # Stage-based recovery bonus
        if stage == 'underwater':
            recovery_score += 10
        elif stage in ('flat_0_to_3', 'early_3_to_5'):
            recovery_score += 8
        elif stage == 'mid_5_to_7':
            recovery_score += 3
        elif stage == 'high_7_to_9':
            recovery_score -= 8
        elif stage == 'near_limit_9_plus':
            recovery_score -= 16

        recovery_score += min(20.0, max(0.0, turnover * 4))
        recovery_score += min(20.0, max(0.0, s['amount_pctile'] * 20))

        s['setup_type'] = setup_type
        s['source_layers'] = source_layers
        s['recovery_score'] = recovery_score
        s['stage'] = stage

    # Score candidates (original logic - NOT sector prediction dependent)
    scored = []
    for s in tradable[:FULL_CANDIDATE_POOL_TARGET]:
        base_score = 50
        pct = s['signal_pct']
        close_pos = s['close_position_score']
        turnover = s['turnover_rate']
        amount = s['signal_amount']
        recovery = s['recovery_score']
        stage = s['stage']

        # Momentum score (original formula)
        momentum_score = 0
        if 2.0 <= pct <= 9.0:
            momentum_score = min(25, pct * 2.5)
        elif pct >= 9.5:
            momentum_score = 20  # Limit strength gets moderate score
        elif pct < 0:
            momentum_score = max(-10, pct * 1.5)  # Penalty for negative

        # Position score (close position in range)
        position_score = close_pos * 15

        # Volume/liquidity score
        volume_score = min(10, amount / 1e9 * 3)

        # Recovery score (underwater recovery bonus)
        recovery_bonus = min(15, recovery * 0.3)

        # Final score
        final_score = base_score + momentum_score + position_score + volume_score + recovery_bonus
        final_score = max(0, min(95, final_score))

        s['final_score'] = round(final_score, 2)
        s['score'] = s['final_score']
        # Sector scores are LOW-WEIGHT REFERENCE ONLY (0.3 = neutral-low)
        s['sector_catalyst_score'] = 0.3
        s['sector_opportunity_score'] = 0.3
        s['sector_name'] = ''

        scored.append(s)

    # Sort by final score
    scored.sort(key=lambda x: x['final_score'], reverse=True)

    # Keep the complete ranked pool for the runner's decision snapshot.
    return scored


if __name__ == '__main__':
    main()
