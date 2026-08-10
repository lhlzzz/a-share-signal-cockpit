#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transport tests: production default is direct HTTP only."""
from __future__ import annotations

import json

import pytest


def _reload_runner_v2():
    import importlib
    import scrapy_scanner.runner_v2 as m
    return importlib.reload(m)


def test_default_scanner_transport_is_direct(monkeypatch):
    monkeypatch.delenv('XIAOGU_SCANNER_TRANSPORT', raising=False)
    m = _reload_runner_v2()
    assert m.DEFAULT_SCANNER_TRANSPORT == 'direct'
    assert m.resolve_scanner_transport() == 'direct'


def test_transport_is_not_configurable(monkeypatch):
    monkeypatch.setenv('XIAOGU_SCANNER_TRANSPORT', 'browser_please')
    m = _reload_runner_v2()
    assert m.resolve_scanner_transport() == 'direct'


def test_api_get_direct_uses_http_only(monkeypatch):
    monkeypatch.setenv('XIAOGU_SCANNER_TRANSPORT', 'direct')
    m = _reload_runner_v2()
    m._SCANNER_TRANSPORT_LOGGED = False

    payload = {'rc': 0, 'data': {'total': 1, 'diff': [{'f12': '600000'}]}}
    body = json.dumps(payload).encode('utf-8')

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body

    monkeypatch.setattr(m.LOCAL_OPENER, 'open', lambda *_a, **_k: FakeResp())
    out = m.api_get('https://example.test/clist')
    assert out['rc'] == 0
    assert out['data']['total'] == 1


def test_stock_all_quote_fields_are_directly_usable():
    from scrapy_scanner import runner_v2

    row = {
        'f12': '600498',
        'f14': '烽火通信',
        'f100': '通信设备',
        'f102': '湖北板块',
        'f103': '5G概念,数据要素,光通信模块',
    }

    candidates = runner_v2._generate_candidates([{
        **row,
        'f2': 39.2,
        'f3': 3.73,
        'f5': 100000,
        'f6': 1000000000,
        'f8': 2.0,
        'f15': 39.5,
        'f16': 37.5,
        'f17': 38.0,
        'f18': 37.79,
    }], {}, 50.0, 0)

    assert candidates[0]['industry'] == '通信设备'
    assert candidates[0]['region_board'] == '湖北板块'
    assert candidates[0]['sector_opportunity_tags'] == ['5G概念', '数据要素', '光通信模块']


def test_stock_capital_flow_requests_direct_stock_identity_fields():
    from scrapy_scanner import runner_v2

    fields = set(runner_v2.STOCK_CAPITAL_FLOW_FIELDS.split(','))

    assert {'f12', 'f14', 'f62', 'f66', 'f69', 'f72', 'f75'} <= fields


def test_fetch_paginated_follows_reported_total_and_records_pages(monkeypatch):
    from scrapy_scanner import runner_v2

    calls = []

    def fake_api_get(url):
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        page = int(query['pn'][0])
        calls.append(page)
        if page == 1:
            return {'data': {'total': 3, 'diff': [{'f12': '600000'}, {'f12': '600001'}]}}
        return {'data': {'total': 3, 'diff': [{'f12': '600002'}]}}

    diagnostics = {}
    monkeypatch.setattr(runner_v2, 'api_get', fake_api_get)

    rows = runner_v2.fetch_paginated(
        'm:1+t:2',
        page_size=2,
        diagnostics=diagnostics,
        max_pages=10,
    )

    assert calls == [1, 2]
    assert [row['f12'] for row in rows] == ['600000', '600001', '600002']
    assert diagnostics['status'] == 'PASS'
    assert diagnostics['pages'] == 2
    assert diagnostics['reported_total'] == 3
    assert diagnostics['limit_hit'] is False


def test_fetch_datacenter_follows_reported_total(monkeypatch):
    from scrapy_scanner import runner_v2

    calls = []

    def fake_api_get(url):
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        page = int(query['pageNumber'][0])
        calls.append(page)
        if page == 1:
            return {'result': {'count': 3, 'data': [{'SECURITY_CODE': '600000'}, {'SECURITY_CODE': '600001'}]}}
        return {'result': {'count': 3, 'data': [{'SECURITY_CODE': '600002'}]}}

    diagnostics = []
    monkeypatch.setattr(runner_v2, 'api_get', fake_api_get)

    rows = runner_v2.fetch_datacenter(
        'RPT_TEST',
        'TRADE_DATE',
        page_size=2,
        diagnostics=diagnostics,
        max_pages=10,
    )

    assert calls == [1, 2]
    assert len(rows) == 3
    assert diagnostics[0]['status'] == 'PASS'
    assert diagnostics[0]['pages'] == 2
    assert diagnostics[0]['reported_total'] == 3
    assert diagnostics[0]['limit_hit'] is False


def test_fetch_report_list_is_paginated(monkeypatch):
    from scrapy_scanner import runner_v2

    calls = []

    def fake_api_get(url):
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        page = int(query['pageNo'][0])
        calls.append(page)
        if page == 1:
            return {'data': {'total': 3, 'data': [{'stockCode': '600000'}, {'stockCode': '600001'}]}}
        return {'data': {'total': 3, 'data': [{'stockCode': '600002'}]}}

    diagnostics = {}
    monkeypatch.setattr(runner_v2, 'api_get', fake_api_get)

    rows = runner_v2.fetch_report_list(
        '0',
        '2026-08-01',
        '2026-08-07',
        page_size=2,
        diagnostics=diagnostics,
        max_pages=10,
    )

    assert calls == [1, 2]
    assert len(rows) == 3
    assert diagnostics['status'] == 'PASS'
    assert diagnostics['pages'] == 2
    assert diagnostics['reported_total'] == 3


def test_fetch_paginated_marks_safe_page_limit_as_partial(monkeypatch):
    from scrapy_scanner import runner_v2

    def fake_api_get(_url):
        return {'data': {'total': 1000, 'diff': [{'f12': '600000'}]}}

    diagnostics = {}
    monkeypatch.setattr(runner_v2, 'api_get', fake_api_get)

    rows = runner_v2.fetch_paginated(
        'm:1+t:2',
        page_size=1,
        diagnostics=diagnostics,
        max_pages=2,
    )

    assert len(rows) == 2
    assert diagnostics['status'] == 'PARTIAL'
    assert diagnostics['pages'] == 2
    assert diagnostics['limit_hit'] is True


def test_v2_api_scan_requires_canonical_direct_api_source():
    from xiaogu_forward_gates import production_evidence_missing_flags

    bundle = {
        'candidate_source': 'eastmoney_api_scan_v2',
        'pipeline_version': 'v2_scanner_api',
        'source_status': {
            'announcements': {'status': 'PASS'},
            'risk_alerts': {'status': 'PASS'},
            'lhb': {'status': 'PASS'},
            'concept_industry': {'status': 'PASS'},
            'financials': {'status': 'PASS'},
        },
        'full_universe_scan': {'quote_count': 5000, 'coverage_status': 'PASS'},
    }
    flags = production_evidence_missing_flags(bundle)
    assert flags
    assert 'PRODUCTION_SOURCE_NOT_CANONICAL' not in flags


def test_gates_reexported_from_runner():
    from xiaogu_forward_d1_1450_runner_v0_1 import (
        candidate_evidence_missing_flags,
        missing_coverage_items,
        production_evidence_missing_flags,
        soft_no_pick_flag,
    )
    assert production_evidence_missing_flags.__module__ == 'xiaogu_forward_gates'
    assert soft_no_pick_flag.__module__ == 'xiaogu_forward_gates'
    assert candidate_evidence_missing_flags.__module__ == 'xiaogu_forward_gates'
    assert missing_coverage_items.__module__ == 'xiaogu_forward_gates'


def test_generate_candidates_uses_price_fields_not_change_amount_or_volume():
    from scrapy_scanner import runner_v2

    rows = [{
        'f2': 10.88,
        'f3': 2.74,
        'f4': 0.29,
        'f5': 165792,
        'f6': 178735728.0,
        'f7': 5.0,
        'f8': 3.2,
        'f10': 0.88,
        'f12': '002452',
        'f14': '长高电气',
        'f18': 10.59,
        'f44': 10.93,
        'f45': 10.40,
        'f46': 10.58,
    }]

    candidates = runner_v2._generate_candidates(rows, {}, 50.0, 0)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['open'] == 10.58
    assert candidate['high'] == 10.93
    assert candidate['low'] == 10.40
    assert candidate['close_position_score'] == pytest.approx((10.88 - 10.40) / (10.93 - 10.40))


def test_generate_candidates_supports_clist_f15_f16_f17_quote_aliases():
    from scrapy_scanner import runner_v2

    rows = [{
        'f2': 13.14,
        'f3': 4.2,
        'f4': 0.53,
        'f5': 1623005,
        'f6': 2014674705.79,
        'f7': 17.92,
        'f8': 14.85,
        'f10': 6.46,
        'f12': '002969',
        'f14': '嘉美包装',
        'f15': 13.81,
        'f16': 11.55,
        'f17': 13.80,
        'f18': 12.61,
    }]

    candidates = runner_v2._generate_candidates(rows, {}, 50.0, 0)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['open'] == 13.80
    assert candidate['high'] == 13.81
    assert candidate['low'] == 11.55
    assert candidate['close_position_score'] == pytest.approx((13.14 - 11.55) / (13.81 - 11.55))


def test_generate_candidates_prefers_clist_prices_over_unrelated_f43_f46_fields():
    from scrapy_scanner import runner_v2

    rows = [{
        'f2': 6.10,
        'f3': -4.84,
        'f5': 421623,
        'f6': 256362250.0,
        'f7': 5.30,
        'f8': 6.46,
        'f10': 2.82,
        'f12': '603421',
        'f14': '鼎信通讯',
        'f15': 6.34,
        'f16': 6.00,
        'f17': 6.32,
        'f18': 6.41,
        'f43': 418960.49,
        'f44': -93508923.12,
        'f45': -93686673.08,
        'f46': 32.395634446329,
    }]

    candidates = runner_v2._generate_candidates(rows, {}, 50.0, 0)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate['price'] == 6.10
    assert candidate['open'] == 6.32
    assert candidate['high'] == 6.34
    assert candidate['low'] == 6.00
    assert candidate['close_position_score'] == pytest.approx(0.294118)
