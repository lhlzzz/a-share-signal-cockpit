import xiaogu_api
from datetime import date, datetime, timezone


def test_get_picks_hides_superseded_by_default(monkeypatch):
    calls = []

    def fake_query_rows(sql, params=None):
        calls.append((sql, params))
        return [{'symbol': '600001', 'decision': 'PAPER_PICK', 'features': {}}]

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    response = xiaogu_api.get_picks(date=None, decision=None, include_superseded=False, limit=50)

    assert response['include_superseded'] is False
    assert response['count'] == 1
    assert "COALESCE(features ->> 'superseded', 'false') <> 'true'" in calls[0][0]


def test_get_picks_includes_superseded_only_with_explicit_audit_flag(monkeypatch):
    calls = []

    def fake_query_rows(sql, params):
        calls.append((sql, params))
        return [
            {'symbol': '600001', 'decision': 'PAPER_PICK', 'features': {'superseded': 'true'}},
            {'symbol': '600002', 'decision': 'PAPER_PICK', 'features': {}},
        ]

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    response = xiaogu_api.get_picks(date=None, decision=None, include_superseded=True, limit=50)

    assert response['include_superseded'] is True
    assert response['count'] == 2
    assert "COALESCE(features ->> 'superseded', 'false') <> 'true'" not in calls[0][0]


def test_daily_summary_uses_active_rows_and_updated_at_current_pick(monkeypatch):
    def fake_query_rows(sql, params):
        assert "COALESCE(features ->> 'superseded', 'false') <> 'true'" in sql
        assert 'updated_at DESC' in sql
        return [
            {
                'symbol': '600003',
                'decision': 'PAPER_PICK',
                'final_score': 70.0,
                'source_summary_path': 'summary.json',
                'features': {
                    'source_consumption_summary': {
                        'scan_source_time': '2026-07-16 14:30:00',
                        'source_completeness_status': 'PASS_WITH_OPTIONAL_GAPS',
                        'optional_or_proxy_gaps': ['sector_news=PROXY'],
                    }
                },
            },
            {'symbol': '600004', 'decision': 'NO_PICK', 'final_score': 90.0, 'features': {}},
        ]

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    response = xiaogu_api.get_daily_summary('2026-07-16')

    assert response['paper_pick']['symbol'] == '600003'
    assert response['highest_score']['symbol'] == '600004'
    assert response['source_summary_path'] == 'summary.json'
    assert response['scan_source_time'] == '2026-07-16 14:30:00'
    assert response['source_completeness_status'] == 'PASS_WITH_OPTIONAL_GAPS'
    assert response['optional_or_proxy_gaps'] == ['sector_news=PROXY']


def test_dashboard_overview_exposes_current_candidate_and_paper_only_rules(monkeypatch):
    calls = []

    def fake_query_rows(sql, params=None):
        calls.append((sql, params))
        if 'FROM scan_sessions' in sql:
            return [{
                'trade_date': date(2026, 7, 30),
                'scan_time': datetime(2026, 7, 30, 9, 30, tzinfo=timezone.utc),
                'quotes_count': 5885,
                'scored_count': 500,
                'passed_count': 10,
                'status': 'completed',
                'market_snapshot': {'market_regime': 'weak', 'market_bigups': 130},
                'source_status': {
                    'source_completeness': {'status': 'PASS', 'flags': []},
                    'external_market': {'source': 'eastmoney_api_global_index'},
                    'announcements': {'status': 'PASS', 'source': 'eastmoney'},
                },
                'source_counts': {'announcements': 3410},
                'source_diagnostics': {},
            }]
        if 'FROM daily_candidates dc' in sql:
            return [{
                'trade_date': date(2026, 7, 29),
                'symbol': '002906',
                'stock_name': '华阳集团',
                'rank': 1,
                'final_score': 59.95,
                'decision': 'CANDIDATE',
                'selection_outcome': 'TOP10_NOT_SELECTED',
                'is_official_pick': False,
                'pct_chg': -1.2,
                'close_position_score': 0.5,
                'market_regime': 'weak',
                'auxiliary_evidence_snapshot': {'status': 'PASS'},
                'selection_reason': {},
                'ticket_reason': {},
                'not_selected_reason': {},
                't1_return': 0.005,
                't1_return_high': 0.01,
                'next_day_open_return': 0.0,
                'next_day_high_return': 0.01,
            }]
        if 'GROUP BY 1' in sql:
            return [{'outcome': 'TOP10_NOT_SELECTED', 'count': 1}]
        if 'FROM picks p' in sql and 'LIMIT 20' in sql:
            return [{
                'id': 1,
                'trade_date': date(2026, 7, 30),
                'symbol': '',
                'stock_name': 'NO_PICK',
                'decision': 'NO_PICK',
                'final_score': 48.9,
                'rank': 5,
                'auxiliary_evidence_status': 'PARTIAL',
                'ticket_reason': {},
                'selection_reason': {},
                'created_at': datetime(2026, 7, 30, 9, 35, tzinfo=timezone.utc),
                't1_return': None,
                't1_return_high': None,
            }]
        if 'FROM picks p' in sql and 'LIMIT 12' in sql:
            return [{
                'trade_date': date(2026, 7, 29),
                'symbol': '002906',
                'stock_name': '华阳集团',
                'decision': 'CANDIDATE',
                'final_score': 59.95,
                'rank': 1,
                't1_return': 0.005,
            }]
        raise AssertionError(f'unexpected query: {sql}')

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    response = xiaogu_api.get_dashboard_overview('2026-07-30')

    assert response['mode'] == 'PAPER_ONLY'
    assert response['trading_enabled'] is False
    assert response['dates'] == {
        'scan_date': '2026-07-30',
        'candidate_date': '2026-07-30',
        'pick_date': '2026-07-30',
    }
    assert response['decision']['latest']['decision'] == 'NO_PICK'
    assert response['candidates'][0]['symbol'] == '002906'
    assert response['candidates'][0]['pct_chg'] == -1.2
    assert response['candidates'][0]['t1_return'] == 0.005
    assert response['rules']['underwater_policy'].startswith('T 日下跌票不直接排除')
    assert response['rules']['limitup_policy'].startswith('当日涨停/封死不可交易')
    assert len(calls) == 5
