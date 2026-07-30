#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backward-compatible import surface for the scanner scoring library.

Implementation lives in xiaogu_scanner_scoring.py.
Historical name retained because scan artifact filenames and older imports
still say eastmoney_web_tabs_*. Not a CDP live entrypoint.

Thin wrappers for concept API fetch keep ``eastmoney_get`` lookups in this
module's globals so ``patch.object(scan, 'eastmoney_get', ...)`` works in tests.
"""
from __future__ import annotations

import time

from xiaogu_scanner_scoring import *  # noqa: F403
from xiaogu_scanner_scoring import (  # noqa: F401
    build_information_coverage_audit,
    build_research_signals,
    build_structured_bundle,
    build_structured_scores,
    enrich_candidates_with_v2_data,
    load_v2_scanner_data,
)

# Explicitly re-export stubs commonly patched in tests
try:
    from xiaogu_scanner_scoring import (  # noqa: F401
        collect_cdp_payloads,
        collect_candidate_detail_evidence,
        collect_candidate_detail_evidence_parallel,
        rows_from_candidate_intraday_replay,
        source_status,
        main,
        parse_cdp_dom_order_book,
    )
except ImportError:
    pass


def fetch_concept_board_list_api(page_size=50):
    """Local wrapper so tests can patch this module's eastmoney_get."""
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
    """Local wrapper so tests can patch this module's eastmoney_get."""
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
        code = item.get('f12') or ''
        if not code:
            continue
        stocks.append({
            'code': code,
            'name': item.get('f14') or '',
            'price': item.get('f2'),
            'pct_change': item.get('f3'),
            'main_force_net_inflow': item.get('f62'),
            'board_code': board_code,
        })
    return stocks


SCORING_LIBRARY = 'xiaogu_scanner_scoring.py'
LIVE_SCANNER = 'scrapy_scanner/runner_v2.py'
