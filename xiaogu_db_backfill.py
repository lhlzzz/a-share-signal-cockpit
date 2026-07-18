#!/usr/bin/env python3
"""
数据库历史数据回填工具
从 ledger JSONL 和 live_scan 目录回填数据到 picks 和 daily_candidates 表
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path('/workspace/hermes-workspaces/xiaogu')
sys.path.insert(0, str(BASE))

from xiaogu_db import get_db, insert_pick, upsert_daily_candidate


def parse_ledger_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """解析 ledger 记录"""
    try:
        trade_date = record.get('date')
        if not trade_date:
            return None

        symbol = record.get('symbol', '')
        decision = record.get('decision', 'NO_PICK')
        features = record.get('features_used', {})

        # 提取评分
        final_score = None
        if isinstance(features, dict):
            candidate_features = features.get('candidate_features', {})
            if isinstance(candidate_features, dict):
                final_score = candidate_features.get('final_score') or candidate_features.get('score')

        # 提取 blockers
        blockers = []
        if isinstance(features, dict):
            risk_flags = features.get('risk_flags', [])
            if isinstance(risk_flags, list):
                blockers = [str(f) for f in risk_flags]

        # 提取 source_layers
        source_layers = []
        if isinstance(features, dict):
            candidate_features = features.get('candidate_features', {})
            if isinstance(candidate_features, dict):
                source_layers = list(candidate_features.get('source_layers', []))

        return {
            'trade_date': date.fromisoformat(trade_date),
            'symbol': symbol,
            'decision': decision,
            'final_score': final_score,
            'blockers': blockers,
            'features': features,
            'source_layers': source_layers,
            'rule_version': record.get('rule_version', ''),
        }
    except Exception as e:
        print(f'Error parsing record: {e}')
        return None


def backfill_from_ledger(ledger_path: str, dry_run: bool = True) -> Dict[str, int]:
    """从 ledger 文件回填到 picks 表"""
    stats = {'total': 0, 'inserted': 0, 'skipped': 0, 'errors': 0}

    with open(ledger_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                record = json.loads(line.strip())
                stats['total'] += 1

                # 只处理 DECISION 记录
                if record.get('record_type') != 'DECISION':
                    stats['skipped'] += 1
                    continue

                parsed = parse_ledger_record(record)
                if not parsed:
                    stats['skipped'] += 1
                    continue

                if dry_run:
                    print(f'[DRY RUN] Would insert: {parsed["trade_date"]} {parsed["symbol"]} {parsed["decision"]}')
                    stats['inserted'] += 1
                else:
                    try:
                        insert_pick(
                            trade_date=parsed['trade_date'],
                            symbol=parsed['symbol'],
                            decision=parsed['decision'],
                            final_score=parsed['final_score'],
                            blockers=parsed['blockers'],
                            features=parsed['features'],
                            source_layers=parsed['source_layers'],
                            rule_version=parsed['rule_version'],
                            dry_run=False,
                        )
                        stats['inserted'] += 1
                    except Exception as e:
                        print(f'Error inserting record {line_num}: {e}')
                        stats['errors'] += 1

                if line_num % 100 == 0:
                    print(f'Processed {line_num} records...')

            except json.JSONDecodeError:
                stats['errors'] += 1
                continue

    return stats


def parse_scan_summary(summary_path: Path, trade_date: date) -> List[Dict[str, Any]]:
    """解析扫描摘要文件，提取候选数据"""
    candidates = []

    try:
        with open(summary_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        paper_scoring_candidates = data.get('paper_scoring_candidates', [])

        for i, candidate in enumerate(paper_scoring_candidates[:10], 1):  # 只取前10个
            if not isinstance(candidate, dict):
                continue

            symbol = candidate.get('code') or candidate.get('symbol', '')
            if not symbol:
                continue

            # 提取评分维度
            limit_up_potential = candidate.get('limit_up_potential', 0)
            market_bonus = candidate.get('market_bonus', 0)
            capital_bonus = candidate.get('capital_bonus', 0)
            fundamental_bonus = candidate.get('fundamental_bonus', 0)
            risk_penalty = candidate.get('risk_penalty', 0)
            sentiment_bonus = candidate.get('sentiment_bonus', 0)
            sector_rotation_bonus = candidate.get('sector_rotation_bonus', 0)
            topic_heat_bonus = candidate.get('topic_heat_bonus', 0)
            leader_bonus = candidate.get('leader_bonus', 0)
            flow_bonus = candidate.get('flow_bonus', 0)
            market_mood_bonus = candidate.get('market_mood_bonus', 0)
            news_bonus = candidate.get('news_bonus', 0)

            # 提取数据源状态
            in_limitup_pool = candidate.get('in_limitup_pool', False)
            in_consecutive = candidate.get('in_consecutive', False)
            in_lhb = candidate.get('in_lhb', False)
            in_block_trades = candidate.get('in_block_trades', False)
            in_earnings_preview = candidate.get('in_earnings_preview', False)
            in_lockup_expiry = candidate.get('in_lockup_expiry', False)
            in_org_survey = candidate.get('in_org_survey', False)
            in_margin_trading = candidate.get('in_margin_trading', False)
            in_hsgt_holdings = candidate.get('in_hsgt_holdings', False)
            in_stock_reports = candidate.get('in_stock_reports', False)
            in_announcements = candidate.get('in_announcements', False)
            in_halted = candidate.get('in_halted', False)
            in_shareholder_changes = candidate.get('in_shareholder_changes', False)
            in_popularity_rank = candidate.get('in_popularity_rank', False)
            in_capital_flow = candidate.get('in_capital_flow', False)

            # 构建 candidate_features
            candidate_features = {
                'limit_up_potential': limit_up_potential,
                'market_bonus': market_bonus,
                'capital_bonus': capital_bonus,
                'fundamental_bonus': fundamental_bonus,
                'risk_penalty': risk_penalty,
                'sentiment_bonus': sentiment_bonus,
                'sector_rotation_bonus': sector_rotation_bonus,
                'topic_heat_bonus': topic_heat_bonus,
                'leader_bonus': leader_bonus,
                'flow_bonus': flow_bonus,
                'market_mood_bonus': market_mood_bonus,
                'news_bonus': news_bonus,
                'in_limitup_pool': in_limitup_pool,
                'in_consecutive': in_consecutive,
                'in_lhb': in_lhb,
                'in_block_trades': in_block_trades,
                'in_earnings_preview': in_earnings_preview,
                'in_lockup_expiry': in_lockup_expiry,
                'in_org_survey': in_org_survey,
                'in_margin_trading': in_margin_trading,
                'in_hsgt_holdings': in_hsgt_holdings,
                'in_stock_reports': in_stock_reports,
                'in_announcements': in_announcements,
                'in_halted': in_halted,
                'in_shareholder_changes': in_shareholder_changes,
                'in_popularity_rank': in_popularity_rank,
                'in_capital_flow': in_capital_flow,
            }

            candidates.append({
                'trade_date': trade_date,
                'symbol': symbol,
                'stock_name': candidate.get('name', ''),
                'rank': i,
                'final_score': candidate.get('final_score') or candidate.get('score'),
                'decision': 'CANDIDATE',
                'is_official_pick': False,
                'open_price': candidate.get('open'),
                'close_price': candidate.get('price'),
                'high_price': candidate.get('high'),
                'low_price': candidate.get('low'),
                'volume': candidate.get('volume'),
                'amount': candidate.get('signal_amount'),
                'pct_chg': candidate.get('signal_pct'),
                'turnover_rate': candidate.get('turnover_rate'),
                'signal_pct': candidate.get('signal_pct'),
                'close_position_score': candidate.get('close_position_score'),
                'fund_flow_momentum': None,
                'sector_catalyst_score': candidate.get('sector_catalyst_score'),
                'early_opportunity_score': None,
                'topic_propagation_score': None,
                'market_regime': '',
                'sentiment_catalyst': '',
                'theme_catalyst': '',
                'news_catalyst': '',
                'positive_catalyst': '',
                'selection_reason': '',
                'blockers': [],
                'hard_gate_status': {},
                'source_layers': candidate.get('source_layers', []),
                'candidate_features': candidate_features,
                'raw_json': candidate,
            })

    except Exception as e:
        print(f'Error parsing {summary_path}: {e}')

    return candidates


def backfill_from_live_scan(live_scan_dir: str, dry_run: bool = True) -> Dict[str, int]:
    """从 live_scan 目录回填到 daily_candidates 表"""
    stats = {'total': 0, 'inserted': 0, 'skipped': 0, 'errors': 0}

    live_scan_path = Path(live_scan_dir)
    if not live_scan_path.exists():
        print(f'Live scan directory not found: {live_scan_dir}')
        return stats

    # 遍历日期目录
    for date_dir in sorted(live_scan_path.iterdir()):
        if not date_dir.is_dir():
            continue

        try:
            trade_date = date.fromisoformat(date_dir.name)
        except ValueError:
            continue

        # 查找扫描摘要文件
        summary_files = list(date_dir.rglob('eastmoney_web_tabs_summary_runner.json'))
        if not summary_files:
            summary_files = list(date_dir.rglob('eastmoney_web_tabs_summary.json'))

        for summary_file in summary_files:
            print(f'Processing {summary_file}...')
            candidates = parse_scan_summary(summary_file, trade_date)

            for candidate in candidates:
                stats['total'] += 1

                if dry_run:
                    print(f'[DRY RUN] Would insert: {candidate["trade_date"]} {candidate["symbol"]} rank={candidate["rank"]}')
                    stats['inserted'] += 1
                else:
                    try:
                        upsert_daily_candidate(**candidate)
                        stats['inserted'] += 1
                    except Exception as e:
                        print(f'Error inserting candidate: {e}')
                        stats['errors'] += 1

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description='数据库历史数据回填工具')
    parser.add_argument('--source', choices=['ledger', 'live_scan', 'all'], default='all',
                       help='数据源: ledger, live_scan, all')
    parser.add_argument('--dry-run', action='store_true', default=True,
                       help='试运行模式（默认）')
    parser.add_argument('--execute', action='store_true',
                       help='执行模式（实际写入数据库）')
    parser.add_argument('--ledger-path', default=str(BASE / 'forward_paper_ledger_v0_1.jsonl'),
                       help='ledger 文件路径')
    parser.add_argument('--live-scan-dir', default=str(BASE / 'data' / 'live_scan'),
                       help='live_scan 目录路径')

    args = parser.parse_args()

    dry_run = not args.execute

    print('=' * 60)
    print('数据库历史数据回填工具')
    print('=' * 60)
    print(f'模式: {"试运行" if dry_run else "执行"}')
    print(f'数据源: {args.source}')
    print()

    if args.source in ('ledger', 'all'):
        print('=' * 60)
        print('从 ledger 回填到 picks 表')
        print('=' * 60)
        stats = backfill_from_ledger(args.ledger_path, dry_run=dry_run)
        print(f'统计: {stats}')
        print()

    if args.source in ('live_scan', 'all'):
        print('=' * 60)
        print('从 live_scan 回填到 daily_candidates 表')
        print('=' * 60)
        stats = backfill_from_live_scan(args.live_scan_dir, dry_run=dry_run)
        print(f'统计: {stats}')
        print()

    print('=' * 60)
    print('回填完成')
    print('=' * 60)


if __name__ == '__main__':
    main()
