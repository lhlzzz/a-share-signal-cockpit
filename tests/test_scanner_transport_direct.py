#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Transport tests: production default is direct HTTP; CDP is not the only path."""
from __future__ import annotations

import io
import json
import os
from unittest import mock

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


def test_invalid_transport_falls_back_to_direct(monkeypatch):
    monkeypatch.setenv('XIAOGU_SCANNER_TRANSPORT', 'browser_please')
    m = _reload_runner_v2()
    assert m.resolve_scanner_transport() == 'direct'


def test_api_get_direct_does_not_call_cdp(monkeypatch):
    monkeypatch.setenv('XIAOGU_SCANNER_TRANSPORT', 'direct')
    m = _reload_runner_v2()
    m._SCANNER_TRANSPORT_LOGGED = False

    def boom(*_a, **_k):
        raise AssertionError('CDP must not run in direct mode')

    monkeypatch.setattr(m, '_cdp_navigate_text', boom)
    monkeypatch.setattr(m, '_ensure_cdp_browser', boom)

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


def test_api_get_auto_falls_back_to_cdp(monkeypatch):
    monkeypatch.setenv('XIAOGU_SCANNER_TRANSPORT', 'auto')
    m = _reload_runner_v2()
    m._SCANNER_TRANSPORT_LOGGED = False

    def fail_direct(*_a, **_k):
        raise RuntimeError('direct_down')

    monkeypatch.setattr(m, '_api_get_direct', fail_direct)
    monkeypatch.setattr(
        m,
        '_cdp_navigate_text',
        lambda url, timeout=25: json.dumps({'rc': 0, 'via': 'cdp'}),
    )
    out = m.api_get('https://example.test/clist')
    assert out['via'] == 'cdp'


def test_v2_api_scan_skips_cdp_tabs_hard_flags():
    from xiaogu_forward_gates import web_tabs_evidence_missing_flags

    bundle = {
        'candidate_source': 'eastmoney_web_tabs_v2_scanner_api',
        'pipeline_version': 'v2_scanner_api',
        'source_status': {
            'required_cdp_tabs': {
                'status': 'PASS',
                'mode': 'api_direct',
                'missing_sources': [],
            },
            'announcements': {'status': 'PASS'},
            'risk_alerts': {'status': 'PASS'},
            'lhb': {'status': 'PASS'},
            'concept_industry': {'status': 'PASS'},
            'financials': {'status': 'PASS'},
            'enhanced_cdp_tabs': {
                'status': 'FAIL',
                'missing_sources': ['limitup_pool'],
            },
            'enhanced_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
            'experimental_evidence_coverage': {'status': 'PASS', 'missing_domains': []},
        },
        'full_universe_scan': {'quote_count': 5000, 'coverage_status': 'PASS'},
    }
    flags = web_tabs_evidence_missing_flags(bundle)
    assert not any(f.startswith('EASTMONEY_REQUIRED_CDP_TABS_MISSING_') for f in flags)
    assert not any(f.startswith('EASTMONEY_ENHANCED_CDP_TABS_MISSING_') for f in flags)
    assert not any(f.startswith('EASTMONEY_DEFAULT_ENHANCED_CDP_TABS_MISSING_') for f in flags)


def test_gates_reexported_from_runner():
    from xiaogu_forward_d1_1450_runner_v0_1 import (
        candidate_evidence_missing_flags,
        missing_coverage_items,
        soft_no_pick_flag,
        web_tabs_evidence_missing_flags,
    )
    assert web_tabs_evidence_missing_flags.__module__ == 'xiaogu_forward_gates'
    assert soft_no_pick_flag.__module__ == 'xiaogu_forward_gates'
    assert candidate_evidence_missing_flags.__module__ == 'xiaogu_forward_gates'
    assert missing_coverage_items.__module__ == 'xiaogu_forward_gates'
