import xiaogu_api
from datetime import date, datetime, timezone

import pytest


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


def test_candidate_entry_evidence_explains_pool_basis_and_risk():
    evidence = xiaogu_api._candidate_entry_evidence({
        'blockers': [],
        'auxiliary_evidence_snapshot': {
            'status': 'PARTIAL',
            'announcement_evidence': [{'title': '中标公告'}],
            'sector_news_evidence': [{'title': '电网设备板块资金回流'}],
            'mainboard_auxiliary_missing_domains': ['direct_symbol_news'],
        },
        'candidate_features': {
            'net_inflow_main': 2827974.0,
            'social_sentiment_score': 0.10,
        },
        'raw_json': {
            'research_signals': {
                'catalyst_quality': {'category': 'sector_catalyst'},
            },
        },
    })

    assert evidence['basis_labels'] == ['公告', '板块新闻', '资金', '情绪']
    assert evidence['missing_domains'] == [
        'direct_symbol_news', 'limitup_reasons', 'social_blog', 'macro_overseas',
    ]
    assert evidence['risk_flags'] == []
    assert evidence['source_time'] is None
    assert {item['domain'] for item in evidence['source_metadata']} >= {
        'announcements', 'direct_symbol_news', 'sector_news', 'stock_capital_flow', 'quote',
    }


def test_candidate_evidence_separates_stock_sector_from_market_theme_tags():
    report = xiaogu_api._candidate_evidence_report({
        'trade_date': date(2026, 8, 7),
        'symbol': '002826',
        'is_official_pick': True,
        'candidate_features': {
            'sector_opportunity_tags': ['电力'],
            'main_theme_alignment_tags': ['电力', '医疗服务'],
            'main_theme_core_score': 1.0,
            'yesterday_limitup_gene_evidence': {
                'records': [{'hybk': '化学制药'}],
            },
        },
        'factor_snapshot': {},
        'eligibility_snapshot': {},
        'ranking_basis': {},
        'raw_json': {},
    })

    assert report['sectors']['primary'] == '化学制药'
    assert report['sectors']['source'] == 'yesterday_limitup.hybk'
    assert report['sectors']['market_theme_tags'] == ['电力']


def test_candidate_evidence_reads_missing_stock_sector_from_eastmoney(monkeypatch):
    monkeypatch.setattr(
        xiaogu_api,
        '_eastmoney_stock_sector_profile',
        lambda symbol: {
            'names': ['通信设备'],
            'industry_board': '通信设备',
            'eastmoney_industry': '通讯行业',
            'sw_industry': '制造业-计算机、通信和其他电子设备制造业',
            'source': 'eastmoney_quote_quotedata+company_survey_api',
        },
    )

    report = xiaogu_api._candidate_evidence_report({
        'trade_date': date(2026, 8, 7),
        'symbol': '600498',
        'candidate_features': {},
        'factor_snapshot': {},
        'eligibility_snapshot': {},
        'ranking_basis': {},
        'raw_json': {'research_signals': {'sector_mapping': {'sectors': []}}},
    })

    assert report['sectors']['primary'] == '通信设备'
    assert report['sectors']['industry_board'] == '通信设备'
    assert report['sectors']['eastmoney_industry'] == '通讯行业'
    assert report['sectors']['sw_industry'] == '制造业-计算机、通信和其他电子设备制造业'
    assert report['sectors']['source'] == 'eastmoney_quote_quotedata+company_survey_api'


def test_t1_result_annotations_only_label_complete_top10_and_preserve_formal_rank():
    rows = [
        {'rank': 1, 'symbol': '000001', 't1_return': 0.01},
        {'rank': 2, 'symbol': '000002', 't1_return': 0.03},
        {'rank': 3, 'symbol': '000003', 't1_return': 0.02},
    ]

    result = xiaogu_api._t1_result_annotations(rows)

    assert [row['rank'] for row in result] == [1, 2, 3]
    assert [(row['symbol'], row['t1_result_rank'], row['t1_result_label']) for row in result] == [
        ('000001', 3, '探花'),
        ('000002', 1, '状元'),
        ('000003', 2, '榜眼'),
    ]
    assert all(row['t1_result_complete'] for row in result)

    partial = xiaogu_api._t1_result_annotations([
        {'rank': 1, 'symbol': '000001', 't1_return': None},
        {'rank': 2, 'symbol': '000002', 't1_return': 0.03},
    ])
    assert all(row['t1_result_label'] is None for row in partial)
    assert [row['t1_result_coverage'] for row in partial] == ['1/2', '1/2']


def test_dashboard_candidate_explanation_is_chinese_and_structured():
    row = {
        'symbol': '603178',
        'final_score': 55.75,
        'fund_flow_momentum': 0.62,
        'candidate_features': {
            'announcement_catalyst_score': 1.0,
            'sector_attack_score': 0.7,
            't1_room_score': 0.58,
        },
        'factor_snapshot': {
            'flow_confirmation_score': 0.8,
            'distribution_risk_score': 0.4,
        },
        'eligibility_snapshot': {'eligible': True},
        'ranking_basis': {'rank_source': 'formal_profit_first'},
        'blockers': [],
        'auxiliary_evidence_snapshot': {},
        'raw_json': {},
    }

    result = xiaogu_api._dashboard_candidate_row(row)

    assert result['selection_explanation']['门禁中文'] == '已通过正式门禁'
    factor_names = [item['名称'] for item in result['selection_explanation']['因子']]
    assert factor_names == ['事件催化', '板块承载', '直接资金确认', 'T+1空间', '派发风险']
    assert result['selection_explanation']['排序口径'] == '主力行为链排序'
    assert 'selection_diagnostics' not in result
    assert 'ranking_basis' not in result
    assert 'eligibility_snapshot' not in result
    assert 'raw_json' not in result


def test_dashboard_candidate_uses_signal_pct_fallback_and_entry_price():
    result = xiaogu_api._dashboard_candidate_row({
        'trade_date': '2026-08-03',
        'symbol': '003018',
        'stock_name': '金富科技',
        'signal_pct': -1.35,
        'close_price': 31.31,
        'candidate_features': {},
        'factor_snapshot': {},
        'eligibility_snapshot': {},
        'raw_json': {},
        'blockers': [],
    })

    assert result['pct_chg'] == -1.35
    assert result['entry_price'] == 31.31


def test_dashboard_translates_limitup_continuation_reason_and_uses_one_policy():
    assert xiaogu_api._cn_gate_reason(
        'limitup_continuation:缺少昨日涨停或当日炸板证据'
    ) == '涨停延续证据：缺少昨日涨停或当日炸板证据'

    assert "T日买入，T+1日交易并以获利为唯一目标" in xiaogu_api.get_dashboard_overview.__code__.co_consts
    assert "T+2/T+3/T+5" not in xiaogu_api.get_dashboard_overview.__code__.co_consts


def test_dashboard_public_candidate_does_not_expose_machine_reason_fields():
    result = xiaogu_api._dashboard_candidate_row({
        'trade_date': '2026-07-31',
        'symbol': '600475',
        'stock_name': '华光环能',
        'rank': 1,
        'final_score': 78.5,
        'decision': 'PAPER_PICK',
        'selection_outcome': 'OFFICIAL_PICK',
        'candidate_features': {'expected_t1_profit_score': 1.0},
        'factor_snapshot': {'expected_t1_profit_score': 1.0},
        'eligibility_snapshot': {'eligible': True},
        'selection_diagnostics': {'official_decision_reason': 'INTERNAL'},
        'ranking_basis': {'basis': 'formal_profit_first'},
        'raw_json': {'legacy_repo_summary': 'INTERNAL'},
        'blockers': [],
        'selection_reason': {
            'decision_reason': '全部正式出票门禁通过',
            'legacy_repo_summary': 'INTERNAL',
        },
    })

    assert result['selection_basis_cn']
    assert result['decision_reason'] == '全部正式出票门禁通过'
    assert 'official_decision_reason' not in result
    assert 'legacy_repo_summary' not in result
    assert 'candidate_features' not in result
    assert 'selection_reason' not in result


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
        if 'FROM production_run_active' in sql:
            return [{
                'production_run_id': 'run-20260716',
                'trade_date': '2026-07-16',
                'candidate_snapshot_id': 'snapshot-20260716',
                'active_pick_id': 1,
                'status': 'PASS',
            }]
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


def test_get_picks_isolates_explicit_run_and_defaults_to_active_run(monkeypatch):
    calls = []

    def fake_query_rows(sql, params=None):
        calls.append((sql, params or {}))
        if 'FROM production_runs pr' in sql:
            assert params['production_run_id'] == 'run-a'
            return [{
                'production_run_id': 'run-a',
                'trade_date': date(2026, 8, 7),
                'candidate_snapshot_id': 'snapshot-a',
                'active_pick_id': 1,
                'status': 'PASS',
            }]
        if 'FROM production_run_active pra' in sql:
            return [{
                'production_run_id': 'run-b',
                'trade_date': date(2026, 8, 7),
                'candidate_snapshot_id': 'snapshot-b',
                'active_pick_id': 2,
                'status': 'PASS',
            }]
        if 'SELECT * FROM picks' in sql:
            run_id = params['production_run_id']
            return [{
                'symbol': '600001' if run_id == 'run-a' else '600002',
                'decision': 'PAPER_PICK',
                'features': {},
            }]
        raise AssertionError(f'unexpected SQL: {sql}')

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    explicit = xiaogu_api.get_picks(
        date='2026-08-07',
        decision=None,
        production_run_id='run-a',
        include_superseded=False,
        limit=50,
    )
    active = xiaogu_api.get_picks(
        date='2026-08-07',
        decision=None,
        include_superseded=False,
        limit=50,
    )

    assert explicit['production_run_id'] == 'run-a'
    assert explicit['picks'][0]['symbol'] == '600001'
    assert active['production_run_id'] == 'run-b'
    assert active['picks'][0]['symbol'] == '600002'
    pick_queries = [params for sql, params in calls if 'SELECT * FROM picks' in sql]
    assert [params['production_run_id'] for params in pick_queries] == ['run-a', 'run-b']


def test_dashboard_overview_exposes_current_candidate_and_paper_only_rules(monkeypatch):
    calls = []

    def fake_query_rows(sql, params=None):
        calls.append((sql, params))
        if 'FROM production_run_active' in sql:
            return [{
                'production_run_id': 'run-20260730',
                'trade_date': date(2026, 7, 30),
                'candidate_snapshot_id': 'snapshot-20260730',
                'active_pick_id': 1,
                'status': 'PASS',
            }]
        if 'FROM production_run_steps' in sql:
            return []
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
            assert 'dc.pct_chg IS NOT NULL' in sql
            assert 'dc.pct_chg < 9.5' in sql
            assert "regulatory_hard_block" in sql
            assert "disqualified_for_paper_pick" in sql
            assert 'dc.rank <= 10' in sql
            assert 'LIMIT 10' in sql
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
            assert 'pct_chg IS NOT NULL' in sql
            assert 'pct_chg < 9.5' in sql
            assert "regulatory_hard_block" in sql
            assert "disqualified_for_paper_pick" in sql
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
    assert response['settlement']['status'] == 'COMPLETE'
    assert response['settlement']['pendingCount'] == 0
    assert response['rules']['underwater_policy'].startswith('T 日下跌票不直接排除')
    assert response['rules']['limitup_policy'].startswith('当日涨停/封死不可交易')
    assert len(calls) == 7
    assert response['production_run_id'] == 'run-20260730'


def test_front_data_exposes_official_pick_separately_from_top_candidate(monkeypatch):
    top_candidate = {
        'symbol': '601126',
        'name': '四方股份',
        'rank': 1,
        'score': 100,
        'productionScore': 100,
        'scoreSource': 'formal_t1_profit_components',
        'rankingView': 'main_force_behavior_chain',
        'rankSource': 'formal_profit_first',
        'decision': 'WATCH',
        'entryPrice': 44.43,
        'selectionReason': '候选榜首',
        'evidenceReport': {
            'sectors': {'primary': '电网自动化设备', 'sector_attack_score': 0.45},
            'capital_flow': {'main_force_net_inflow_yi': 1.95},
            'risk': {'missing_domains': ['sector_news']},
            'catalyst': {'announcements': [{'title': '四方股份:公告'}]},
            'data_coverage': {'missing_domains': ['sector_news']},
        },
    }
    formal_pick_candidate = {
        'symbol': '601899',
        'name': '紫金矿业',
        'rank': 14,
        'score': 78.6984,
        'productionScore': 78.6984,
        'scoreSource': 'formal_t1_profit_components',
        'rankingView': 'main_force_behavior_chain',
        'rankSource': 'formal_profit_first',
        'decision': 'TRADING',
        'entryPrice': 34.08,
        'selectionReason': '正式出票',
        'evidenceReport': {
            'sectors': {'primary': '贵金属', 'sector_attack_score': 1.0},
            'capital_flow': {'main_force_net_inflow_yi': 2.69},
            'risk': {'missing_domains': ['sector_news']},
            'catalyst': {'announcements': [{'title': '紫金矿业:公告'}]},
            'data_coverage': {'missing_domains': ['sector_news']},
        },
    }

    def fake_query_rows(sql, params=None):
        if 'FROM production_run_active' in sql:
            return [{
                'production_run_id': 'run-20260805',
                'trade_date': date(2026, 8, 5),
                'candidate_snapshot_id': 'snapshot-20260805',
                'active_pick_id': 1,
                'status': 'PASS',
            }]
        if 'FROM picks p' in sql and "decision = 'PAPER_PICK'" in sql:
            return [{
                'id': 1,
                'trade_date': date(2026, 8, 5),
                'symbol': '601899',
                'stock_name': '紫金矿业',
                'decision': 'PAPER_PICK',
                'final_score': 78.6984,
                'rank': 14,
                'auxiliary_evidence_status': 'PASS',
                'ticket_reason': {'reason': 'ALL_FORWARD_PAPER_HARD_GATES_PASS'},
                'selection_reason': {'one_liner': '正式出票'},
                'created_at': datetime(2026, 8, 5, 14, 49, tzinfo=timezone.utc),
                'features': {'candidate_features': {'entry_price': 34.08}},
                't1_return': None,
            }]
        return []

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)
    monkeypatch.setattr(xiaogu_api, '_simulate_paper_portfolio', lambda initial_capital: {
        'initialCapital': initial_capital,
        'totalAssets': initial_capital,
        'cash': initial_capital,
        'stockValue': 0,
        'totalPnl': 0,
        'totalPnlPercent': 0,
        'positions': [],
        'allocation': [],
        'equityCurve': [],
        'riskMetrics': {},
        'latestPendingDate': None,
        'pendingRecords': [],
        'tradeHistory': [],
        'monthlyData': [],
        'stats': {'winRate': 100.0},
    })
    monkeypatch.setattr(xiaogu_api, '_os_system_payload', lambda simulation, production_run_id=None: {
        'memory': {'connection': {'vectorRecords': 0}, 'entries': []},
        'health': {'database': 'online', 'api': 'online'},
        'dataSources': [],
        'recentErrors': [],
    })
    monkeypatch.setattr(xiaogu_api, '_load_latest_chain_replay', lambda: {
        'window': {'start': '2026-01-01', 'end': '2026-08-05', 'trading_days': 1},
        'sample_count': 1,
        'winning_samples': 1,
        'losing_samples': 0,
        'win_rate': 100.0,
        'average_t1_return': 0.1,
        'max_drawdown': 0.0,
        'source': 'summary',
    })
    monkeypatch.setattr(xiaogu_api, '_enrich_chain_replay', lambda replay: replay)
    monkeypatch.setattr(xiaogu_api, '_latest_scan_market_payloads', lambda production_run_id=None: {
        'session': {
            'trade_date': date(2026, 8, 5),
            'scan_time': datetime(2026, 8, 5, 14, 40, tzinfo=timezone.utc),
            'quotes_count': 500,
            'scored_count': 2,
            'passed_count': 1,
            'market_snapshot': {
                'market_regime': 'strong',
                'market_bigups': 219,
                'market_limitups': 95,
                'broken_limitups': 5,
                'market_breadth_up_pct': 28.22,
                'universe_quote_count': 500,
                'sentiment_score': 1.0,
                'direct_data_coverage': {
                    'mode': 'eastmoney_api_direct_raw',
                    'mainboard_quote_rows': 3483,
                    'mainboard_stock_capital_flow': {
                        'coverage_ratio': 1.0,
                    },
                },
            },
        },
        'payloads': {
            'indexes': [],
            'sector_capital_flow': {},
            'market_capital_flow': [],
        },
    })
    monkeypatch.setattr(xiaogu_api, '_os_candidate_rows', lambda limit=10: [top_candidate, formal_pick_candidate])
    monkeypatch.setattr(xiaogu_api, '_latest_chain_system_stats', lambda: {
        'runningDays': 1,
        'totalReturn': 0.1,
        'winRate': 100.0,
        'maxDrawdown': 0.0,
        'sharpeRatio': 0.0,
        'totalTrades': 1,
        'winningTrades': 1,
        'losingTrades': 0,
    })

    response = xiaogu_api.get_os_front_data()

    assert response['candidates'][0]['name'] == '四方股份'
    assert response['decision']['paper_pick']['symbol'] == '601899'
    assert response['decision']['paper_pick']['name'] == '紫金矿业'
    assert response['aiDecisions'][2]['result'].startswith('紫金矿业 (601899)')
    assert '正式出票' in response['aiDecisions'][2]['description']
    assert response['marketState']['directDataCoverage']['mode'] == 'eastmoney_api_direct_raw'
    assert response['aShareMarket']['directDataCoverage']['mainboard_quote_rows'] == 3483


def test_picks_endpoint_rejects_non_production_decision():
    import xiaogu_api
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        xiaogu_api.get_picks(decision='CANDIDATE')

    assert exc_info.value.status_code == 400
    assert 'PAPER_PICK' in str(exc_info.value.detail)


def test_paper_portfolio_reads_only_active_formal_profit_chain(monkeypatch):
    calls = []

    def fake_query_rows(sql, params=None):
        calls.append(sql)
        return []

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    assert xiaogu_api._paper_trade_rows() == []
    assert "p.decision = 'PAPER_PICK'" in calls[0]
    assert "COALESCE(p.features ->> 'superseded', 'false') <> 'true'" in calls[0]
    assert "FROM picks p" in calls[0]
    assert "dc.is_official_pick = TRUE" not in calls[0]


def test_paper_trade_chain_metadata_separates_formal_and_mixed_history():
    formal = xiaogu_api._paper_trade_chain_metadata({
        'final_score': 100.0,
        'rank': 1,
        'candidate_ranking_basis': {
            'formal_rank': 1,
            'rank_source': 'formal_profit_first',
            'ranking_view': 'main_force_behavior_chain',
            'score_source': 'formal_t1_profit_components',
            'production_score': 100.0,
            'formal_rank_snapshot_id': 'snapshot-2026-08-05',
        },
    })
    assert formal['chain_status'] == 'formal_production'
    assert formal['score_source'] == 'formal_t1_profit_components'
    assert formal['ranking_view'] == 'main_force_behavior_chain'
    assert formal['chain_audit'] == {
        'score_matches': True,
        'rank_matches': True,
        'reason': '正式链字段、正式分和正式排名一致',
    }

    mixed = xiaogu_api._paper_trade_chain_metadata({
        'final_score': 86.05,
        'rank': 1,
        'candidate_ranking_basis': {
            'formal_rank': 1,
            'rank_source': 'formal_profit_first',
            'formal_primary_score': 4.2776,
        },
    })
    assert mixed['chain_status'] == 'historical_unverified'
    assert mixed['score_source'] == 'UNAVAILABLE'
    assert mixed['ranking_view'] == 'UNAVAILABLE'
    assert mixed['chain_audit']['score_matches'] is False


def test_paper_portfolio_history_exposes_chain_metadata(monkeypatch):
    monkeypatch.setattr(xiaogu_api, '_paper_trade_rows', lambda: [{
        'id': 1,
        'trade_date': date(2026, 8, 5),
        'symbol': '601126',
        'stock_name': '四方股份',
        'final_score': 100.0,
        'rank': 1,
        'close_price': 10.0,
        'candidate_ranking_basis': {
            'formal_rank': 1,
            'rank_source': 'formal_profit_first',
            'ranking_view': 'main_force_behavior_chain',
            'score_source': 'formal_t1_profit_components',
            'production_score': 100.0,
        },
        't1_return': 0.1,
    }])

    result = xiaogu_api._simulate_paper_portfolio()

    history = result['tradeHistory'][0]
    assert history['chain_status'] == 'formal_production'
    assert history['score_source'] == 'formal_t1_profit_components'
    assert history['ranking_view'] == 'main_force_behavior_chain'
    assert history['production_score'] == 100.0


def test_latest_chain_system_stats_are_the_only_production_score(monkeypatch):
    monkeypatch.setattr(xiaogu_api, '_load_latest_chain_replay', lambda: {
        'window': {'trading_days': 57},
        'sample_count': 33,
        'winning_samples': 20,
        'losing_samples': 12,
        'average_t1_return': 1.2622,
        'win_rate': 60.61,
        'max_drawdown': -10.9597,
    })
    stats = xiaogu_api._latest_chain_system_stats()

    assert stats == {
        'runningDays': 57,
        'totalReturn': 1.2622,
        'winRate': 60.61,
        'maxDrawdown': -10.9597,
        'sharpeRatio': 0.0,
        'totalTrades': 33,
        'winningTrades': 20,
        'losingTrades': 12,
    }
    assert xiaogu_api._PRODUCTION_CHAIN_NAME == 'main_force_behavior_chain'
    assert xiaogu_api._PRODUCTION_RANK_SOURCE == 'formal_profit_first'


def test_latest_chain_replay_uses_active_production_database_rows(monkeypatch):
    monkeypatch.setattr(xiaogu_api, 'query_rows', lambda sql, params=None: [
        {'trade_date': date(2026, 8, 6), 'symbol': '000001', 'rank': 1, 't1_return': 0.1},
        {'trade_date': date(2026, 8, 6), 'symbol': '000002', 'rank': 2, 't1_return': 0.05},
        {'trade_date': date(2026, 8, 7), 'symbol': '000003', 'rank': 1, 't1_return': -0.02},
    ])
    replay = xiaogu_api._load_latest_chain_replay()

    assert replay['status'] == 'DATABASE_FULL_REPLAY'
    assert replay['database_trade_dates'] == 2
    assert replay['candidate_days'] == 2
    assert replay['candidate_rows'] == 3
    assert replay['candidate_rows_with_t1'] == 3
    assert replay['sample_count'] == 2
    assert replay['win_rate'] == 50.0
    assert replay['source'] == 'PostgreSQL daily_candidates/returns/production_run_active'


def test_replay_enrichment_suppresses_legacy_fallback_fields(monkeypatch):
    monkeypatch.setattr(xiaogu_api, "query_rows", lambda sql, params=None: [{
        "trade_date": "2026-06-25",
        "symbol": "600030",
        "stock_name": "中信证券",
        "rank": 2,
        "final_score": 73.22898,
        "close_price": 28.98,
        "pct_chg": 3.91,
        "selection_reason": "NO_PICK_FALLBACK_TO_HIGHEST_SCORE:ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS",
        "candidate_features": {"replay_main_force_net_ratio": 0.0},
        "raw_json": {},
        "ranking_basis": {"basis_status": "RECORDED_RANK_SCORE_ONLY"},
        "t1_return": -0.042788,
        "t1_return_high": None,
        "next_day_low_return": None,
        "next_day_drawdown": None,
        "high_to_close_retrace": None,
    }])

    result = xiaogu_api._enrich_chain_replay({
        "settledSamples": [{
            "trade_date": "2026-06-25",
            "symbol": "600030",
            "t1_return": -0.042788,
        }]
    })

    sample = result["settledSamples"][0]
    assert sample["selection_reason"] == ""
    assert sample["final_score"] is None
    assert "FALLBACK" not in sample["reason_summary"]
    assert "回退" not in sample["reason_summary"]


def test_market_capital_flow_parser_reads_persisted_kline_payload():
    result = xiaogu_api._as_market_capital_flow_item({
        'name': '上证指数',
        'secid': '1.000001',
        'klines': ['2026-08-04,10910867456.0,92274688.0,-11004223488.0,17461956608.0,0.00000,-0.01405'],
    })

    assert result == {
        'name': '上证指数',
        'secid': '1.000001',
        'mainInflow': 10910867456.0,
        'superLargeInflow': 92274688.0,
        'mediumInflow': -11004223488.0,
        'largeInflow': 17461956608.0,
    }


def test_memory_public_labels_use_trading_for_internal_compatibility_value(monkeypatch):
    def fake_query_rows(sql, params=None):
        if 'COUNT(*) AS count' in sql and 'pick_case_embeddings' in sql:
            return [{'count': 1}]
        if 'FROM pick_case_embeddings pce' in sql and 'ORDER BY pce.trade_date DESC' in sql:
            return [{
                'trade_date': date(2026, 8, 4),
                'symbol': '600487',
                'stock_name': '亨通光电',
                'decision': 'PAPER_PICK',
                'final_score': 100.0,
                't1_return': None,
                'case_text': 'main_force_behavior_chain',
            }]
        if 'GROUP BY 1' in sql:
            return []
        return []

    monkeypatch.setattr(xiaogu_api, 'query_rows', fake_query_rows)

    memory = xiaogu_api._os_memory_payload(limit=1)

    assert memory['entries'][0]['title'].endswith('TRADING')
    assert 'PAPER_PICK' not in memory['entries'][0]['tags']


def test_review_payload_only_selects_losses_and_low_returns_for_obsidian_upgrade(monkeypatch):
    def fake_query_rows(sql, params=None):
        assert "r.t1_return < :threshold" in sql
        assert params["threshold"] == 0.01
        return [
            {
                "trade_date": date(2026, 8, 4),
                "symbol": "601126",
                "stock_name": "四方股份",
                "final_score": 100.0,
                "rank": 1,
                "selection_reason": "主力行为链正式出票",
                "ticket_reason": {},
                "candidate_features": {},
                "raw_json": {},
                "t1_return": -0.02,
            },
            {
                "trade_date": date(2026, 8, 5),
                "symbol": "601899",
                "stock_name": "紫金矿业",
                "final_score": 78.0,
                "rank": 2,
                "selection_reason": "主力行为链正式出票",
                "ticket_reason": {},
                "candidate_features": {},
                "raw_json": {},
                "t1_return": 0.005,
            },
        ]

    monkeypatch.setattr(xiaogu_api, "query_rows", fake_query_rows)
    review = xiaogu_api._os_review_payload()

    assert review["threshold"] == 0.01
    assert [row["review_level"] for row in review["cases"]] == ["LOSS", "LOW_RETURN"]
    assert all(row["production_chain"] == "main_force_behavior_chain" for row in review["cases"])
    assert review["obsidian"]["role"]


def test_portfolio_summary_without_capital_does_not_invent_account_balance(monkeypatch):
    monkeypatch.setattr(xiaogu_api, "_paper_trade_rows", lambda: [{
        "id": 1,
        "trade_date": date(2026, 8, 4),
        "symbol": "601126",
        "stock_name": "四方股份",
        "final_score": 100.0,
        "rank": 1,
        "close_price": 10.0,
        "t1_return": 0.005,
        "candidate_ranking_basis": {
            "formal_rank": 1,
            "rank_source": "formal_profit_first",
            "ranking_view": "main_force_behavior_chain",
            "score_source": "formal_t1_profit_components",
            "production_score": 100.0,
        },
    }])

    result = xiaogu_api._simulate_paper_portfolio()

    assert result["initialCapital"] is None
    assert result["capitalSource"] == "UNAVAILABLE"
    assert result["stats"]["totalTrades"] == 1
    assert result["tradeHistory"][0]["amount"] is None
