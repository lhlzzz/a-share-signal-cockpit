import sys
sys.path.insert(0, '/root/hermes/company-ai-system/workspaces/xiaogu')
from xiaogu_v2_1_six_repo_real_integrated import hot_money_features, hot_money_score


def test_hot_money_features_basic():
    c = {
        'code': '000001', 'price': 15.0, 'signal_pct': 6.0,
        'close_position_score': 0.8, 'net_inflow_main': 5000000,
        'turnover_rate': 10.0, 'volume_ratio': 1.5, 'amplitude': 4.0,
        'market_breadth_up_pct': 40.0, 'market_limitups': 80,
        'sector_opportunity_score': 0.6, 'sector_opportunity_tags': ['AI', '芯片', '半导体', '科技'],
        'signal_date': '2026-06-26', 'signal_amount': 1e9, 'rank': 5,
        'amount_pctile_rule': 0.8, 'market_regime': 'strong',
        'market_bigups': 200, 'signal_close': 15.0, 'theme_strength': 8.0,
        'theme_big_strength': 6.0, 'non_climax': True,
    }
    features = hot_money_features(c)
    assert 0 <= features['accumulation_signal'] <= 1
    assert 0 <= features['sector_heat'] <= 1
    assert 0 <= features['control_difficulty'] <= 1
    assert 0 <= features['upside_potential'] <= 1
    assert 0 <= features['exit_conditions'] <= 1
    print('test_hot_money_features_basic PASSED')


def test_hot_money_score_range():
    c = {
        'code': '000001', 'price': 10.0, 'signal_pct': 5.0,
        'close_position_score': 0.7, 'net_inflow_main': 3000000,
        'turnover_rate': 8.0, 'volume_ratio': 1.2, 'amplitude': 3.0,
        'market_breadth_up_pct': 35.0, 'market_limitups': 60,
        'sector_opportunity_score': 0.4, 'sector_opportunity_tags': ['新能源', '光伏'],
        'signal_date': '2026-06-26', 'signal_amount': 5e8, 'rank': 10,
        'amount_pctile_rule': 0.7, 'market_regime': 'neutral',
        'market_bigups': 150, 'signal_close': 10.0, 'theme_strength': 5.0,
        'theme_big_strength': 4.0, 'non_climax': True,
    }
    score, features = hot_money_score(c)
    assert 0 <= score <= 100
    print(f'test_hot_money_score_range PASSED (score={score})')


def test_hot_money_weak_stock():
    c = {
        'code': '000002', 'price': 50.0, 'signal_pct': -5.0,
        'close_position_score': 0.2, 'net_inflow_main': -10000000,
        'turnover_rate': 30.0, 'volume_ratio': 5.0, 'amplitude': 10.0,
        'market_breadth_up_pct': 15.0, 'market_limitups': 20,
        'sector_opportunity_score': 0.0, 'sector_opportunity_tags': [],
        'signal_date': '2026-06-26', 'signal_amount': 1e9, 'rank': 100,
        'amount_pctile_rule': 0.3, 'market_regime': 'weak',
        'market_bigups': 50, 'signal_close': 50.0, 'theme_strength': 1.0,
        'theme_big_strength': 0.5, 'non_climax': True,
    }
    score, features = hot_money_score(c)
    assert 0 <= score <= 100
    assert score < 50
    print(f'test_hot_money_weak_stock PASSED (score={score})')


def test_hot_money_strong_stock():
    c = {
        'code': '000003', 'price': 8.0, 'signal_pct': 7.0,
        'close_position_score': 0.9, 'net_inflow_main': 20000000,
        'turnover_rate': 12.0, 'volume_ratio': 1.8, 'amplitude': 3.5,
        'market_breadth_up_pct': 45.0, 'market_limitups': 100,
        'sector_opportunity_score': 0.8, 'sector_opportunity_tags': ['AI', '芯片', '机器人', '半导体', '5G'],
        'signal_date': '2026-06-26', 'signal_amount': 8e8, 'rank': 3,
        'amount_pctile_rule': 0.9, 'market_regime': 'strong',
        'market_bigups': 250, 'signal_close': 8.0, 'theme_strength': 10.0,
        'theme_big_strength': 8.0, 'non_climax': True,
    }
    score, features = hot_money_score(c)
    assert score > 50
    print(f'test_hot_money_strong_stock PASSED (score={score})')


if __name__ == '__main__':
    test_hot_money_features_basic()
    test_hot_money_score_range()
    test_hot_money_weak_stock()
    test_hot_money_strong_stock()
    print('\nAll hot_money_score tests PASSED')
