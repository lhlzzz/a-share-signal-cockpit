import xiaogu_api


def test_get_picks_hides_superseded_by_default(monkeypatch):
    calls = []

    def fake_query_rows(sql, params):
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
