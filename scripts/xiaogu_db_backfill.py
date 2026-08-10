"""Backfill missing data in xiaogu database from all available sources."""
import json
import math
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xiaogu_db import (
    insert_pick, upsert_daily_candidate, upsert_return, upsert_signal,
    get_db, engine, classify_candidate_cohort, reconstruct_candidate_evidence,
    fetch_daily_candidates, fetch_picks, fetch_returns, update_candidate_cohort
)
from sqlalchemy import text
from datetime import date as _date


def _sanitize_nan(obj):
    """Convert NaN/Inf values to None for JSON/DB compatibility."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj


def _truncate_market_regime(val, maxlen=20):
    """Truncate market_regime to fit VARCHAR(20)."""
    if not val:
        return ''
    s = str(val)
    return s[:maxlen] if len(s) > maxlen else s


def _is_valid_trade_date(trade_date_str):
    """Reject future dates."""
    from datetime import date as _date
    try:
        td = _date.fromisoformat(trade_date_str)
    except (ValueError, TypeError):
        return False
    return td <= _date.today()


def _period_rows(start_date: str = '2026-06-20', end_date: str = '2026-07-09'):
    """Return historical candidate rows in the requested reconstruction window."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT * FROM daily_candidates
            WHERE trade_date BETWEEN :start_date AND :end_date
              AND rank IS NOT NULL AND rank <= 10
            ORDER BY trade_date, rank, symbol
        """), {'start_date': start_date, 'end_date': end_date}).mappings().all()
    return [dict(row) for row in rows]


def _call_upsert_from_row(row, snapshots, cohort_info):
    """Persist a reconstructed row using the existing DB owner."""
    def as_dict(value):
        return dict(value) if isinstance(value, dict) else {}

    def as_list(value):
        return list(value) if isinstance(value, list) else []

    upsert_daily_candidate(
        trade_date=row['trade_date'], symbol=str(row.get('symbol') or ''),
        stock_name=str(row.get('stock_name') or ''), rank=row.get('rank'),
        final_score=row.get('final_score'), decision=str(row.get('decision') or 'CANDIDATE'),
        is_official_pick=bool(row.get('is_official_pick')), open_price=row.get('open_price'),
        close_price=row.get('close_price'), high_price=row.get('high_price'), low_price=row.get('low_price'),
        volume=row.get('volume'), amount=row.get('amount'), pct_chg=row.get('pct_chg'),
        turnover_rate=row.get('turnover_rate'), signal_pct=row.get('signal_pct'),
        close_position_score=row.get('close_position_score'), fund_flow_momentum=row.get('fund_flow_momentum'),
        sector_catalyst_score=row.get('sector_catalyst_score'), early_opportunity_score=row.get('early_opportunity_score'),
        topic_propagation_score=row.get('topic_propagation_score'), market_regime=str(row.get('market_regime') or ''),
        sentiment_catalyst=str(row.get('sentiment_catalyst') or ''), theme_catalyst=str(row.get('theme_catalyst') or ''),
        news_catalyst=str(row.get('news_catalyst') or ''), positive_catalyst=str(row.get('positive_catalyst') or ''),
        selection_reason=str(row.get('selection_reason') or ''),
        selection_outcome=str(row.get('selection_outcome') or ''),
        selection_outcome_reason=str(row.get('selection_outcome_reason') or ''),
        blockers=as_list(row.get('blockers')), hard_gate_status=as_dict(row.get('hard_gate_status')),
        eligibility_snapshot=as_dict(row.get('eligibility_snapshot')),
        selection_diagnostics=as_dict(row.get('selection_diagnostics')),
        source_layers=as_list(row.get('source_layers')), candidate_features=as_dict(row.get('candidate_features')),
        raw_json=as_dict(row.get('raw_json')),
        candidate_entry_reason=snapshots.get('candidate_entry_reason') or [],
        ticket_reason=snapshots.get('ticket_reason') or {},
        not_selected_reason=snapshots.get('not_selected_reason') or [],
        factor_snapshot=snapshots.get('factor_snapshot') or {},
        auxiliary_evidence_snapshot=snapshots.get('auxiliary_evidence_snapshot') or {},
        ranking_basis=snapshots.get('ranking_basis') or {},
        postmortem_snapshot=as_dict(row.get('postmortem_snapshot')),
        future_return_fields_placeholder=snapshots.get('future_return_fields_placeholder') or {},
        cohort=cohort_info['cohort'], cohort_quality=cohort_info['cohort_quality'],
        cohort_status_flags=cohort_info['status_flags'],
        reconstruction_provenance=snapshots.get('reconstruction_provenance') or {},
    )


def reconstruct_historical_evidence(start_date: str = '2026-06-20', end_date: str = '2026-07-09', dry_run: bool = False) -> dict:
    """Reconstruct top10 snapshots from recorded DB material only.

    The return value is intentionally detailed so callers can audit missing
    evidence and confidence rather than treating a successful write as PASS.
    """
    rows = _period_rows(start_date, end_date)
    by_date = {}
    for row in rows:
        by_date.setdefault(row['trade_date'], []).append(row)
    stats = {
        'window': {'start': start_date, 'end': end_date}, 'rows': len(rows),
        'written': 0, 'dry_run': dry_run, 'before': {}, 'after': {},
        'reconstruction_confidence': {}, 'missing_fields': {}, 'errors': [],
    }
    for trade_date, day_rows in by_date.items():
        top10_count = len(day_rows)
        picks = fetch_picks(trade_date) if not isinstance(trade_date, str) else fetch_picks(trade_date)
        pick_by_symbol = {str(p.get('symbol') or ''): p for p in picks}
        return_rows = fetch_returns(trade_date) if not isinstance(trade_date, str) else fetch_returns(trade_date)
        returns_by_symbol = {str(r.get('symbol') or ''): r for r in return_rows}
        for row in day_rows:
            for field in ('candidate_entry_reason', 'factor_snapshot', 'auxiliary_evidence_snapshot', 'ranking_basis', 'not_selected_reason'):
                stats['before'][field] = stats['before'].get(field, 0) + int(_json_present(row.get(field)))
            snapshots = reconstruct_candidate_evidence(
                row, pick=pick_by_symbol.get(str(row.get('symbol') or ''), {}),
                return_row=returns_by_symbol.get(str(row.get('symbol') or ''), {}),
            )
            for field, details in (snapshots.get('reconstruction_provenance') or {}).items():
                confidence = details.get('reconstruction_confidence', 'LOW')
                stats['reconstruction_confidence'][confidence] = stats['reconstruction_confidence'].get(confidence, 0) + 1
                for missing in details.get('missing_fields') or []:
                    stats['missing_fields'][missing] = stats['missing_fields'].get(missing, 0) + 1
            has_return = str(row.get('symbol') or '') in returns_by_symbol and returns_by_symbol[str(row.get('symbol') or '')].get('t1_return') is not None
            cohort = classify_candidate_cohort({**row, **snapshots}, top10_count=top10_count, has_return=has_return, trade_date=trade_date)
            if not dry_run:
                try:
                    _call_upsert_from_row(row, snapshots, cohort)
                    stats['written'] += 1
                except Exception as exc:
                    stats['errors'].append({'trade_date': str(trade_date), 'symbol': row.get('symbol'), 'error': repr(exc)})
    # The write path is idempotent; re-read the same window for auditable
    # after-coverage rather than assuming every attempted row succeeded.
    after_rows = _period_rows(start_date, end_date)
    for field in ('candidate_entry_reason', 'factor_snapshot', 'auxiliary_evidence_snapshot', 'ranking_basis', 'not_selected_reason'):
        stats['after'][field] = sum(int(_json_present(row.get(field))) for row in after_rows)
    return stats


def backfill_cohort_labels(start_date: str = '2026-06-20', dry_run: bool = False) -> dict:
    """Classify every historical DB row without inventing missing snapshots."""
    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(text("SELECT * FROM daily_candidates WHERE trade_date >= :start_date ORDER BY trade_date, symbol"), {'start_date': start_date}).mappings().all()]
        returns = [dict(row) for row in conn.execute(text("SELECT trade_date, symbol, t1_return FROM returns WHERE trade_date >= :start_date"), {'start_date': start_date}).mappings().all()]
    return_keys = {(row['trade_date'], str(row.get('symbol') or '')) for row in returns if row.get('t1_return') is not None}
    by_date = {}
    for row in rows:
        by_date.setdefault(row['trade_date'], []).append(row)
    written = 0
    for trade_date, day_rows in by_date.items():
        top10_count = sum(1 for row in day_rows if int(row.get('rank') or 999999) <= 10)
        for row in day_rows:
            cohort = classify_candidate_cohort(row, top10_count=top10_count, has_return=(trade_date, str(row.get('symbol') or '')) in return_keys, trade_date=trade_date)
            if not dry_run:
                update_candidate_cohort(trade_date, str(row.get('symbol') or ''), cohort, {
                    'cohort_only': True,
                    'reconstruction_source': ['db_row_classification'],
                    'reconstruction_confidence': 'LOW',
                })
                written += 1
    return {'start_date': start_date, 'rows': len(rows), 'written': written, 'dry_run': dry_run}


def _json_present(value):
    return bool(value) and value not in ({}, [])


def _parse_ledger(ledger_path: Path) -> list:
    """Parse ledger JSONL into structured records for DB insertion."""
    records = []
    with open(ledger_path) as f:
        for line in f:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue

            trade_date_str = raw.get('date', '')
            if not trade_date_str:
                continue
            if not _is_valid_trade_date(trade_date_str):
                continue

            decision = raw.get('decision', '')
            symbol = (raw.get('symbol') or '').strip()
            rule_version = raw.get('rule_version', '')
            features = raw.get('features_used', {})
            if not isinstance(features, dict):
                features = {}

            cbs = features.get('candidate_bundle_status', {})
            if not isinstance(cbs, dict):
                cbs = {}
            psc_list = cbs.get('paper_scoring_candidates', [])
            if not isinstance(psc_list, list):
                psc_list = []

            cf = features.get('candidate_features', {})
            if not isinstance(cf, dict):
                cf = {}

            risk_flags = features.get('risk_flags', [])
            if not isinstance(risk_flags, list):
                risk_flags = []

            final_score = None
            if psc_list:
                for c in psc_list:
                    if isinstance(c, dict) and (c.get('code') == symbol or c.get('symbol') == symbol):
                        final_score = c.get('final_score') or c.get('score')
                        break
                if final_score is None and psc_list:
                    final_score = psc_list[0].get('final_score') or psc_list[0].get('score')

            records.append({
                'trade_date': trade_date_str,
                'symbol': symbol,
                'decision': decision or 'NO_PICK',
                'final_score': final_score,
                'blockers': risk_flags,
                'features': cf,
                'source_layers': [],
                'rule_version': rule_version,
                'scan_dir': '',
                'dry_run': True,
                't1_return': raw.get('t1_return'),
                't2_return': raw.get('t2_return'),
                't3_return': raw.get('t3_return'),
                'candidates': psc_list,
            })

    return records


def backfill_from_ledger() -> int:
    """Extract picks + candidates from forward_paper_ledger_v0_1.jsonl."""
    ledger_path = ROOT / 'forward_paper_ledger_v0_1.jsonl'
    if not ledger_path.exists():
        print(f"Ledger not found: {ledger_path}")
        return 0

    records = _parse_ledger(ledger_path)
    count = 0

    for rec in records:
        trade_date = date.fromisoformat(rec['trade_date'])

        if rec['symbol'] or rec['decision'] in ('PAPER_PICK', 'NO_PICK'):
            try:
                insert_pick(
                    trade_date=trade_date,
                    symbol=rec['symbol'],
                    decision=rec['decision'],
                    final_score=rec['final_score'],
                    blockers=rec['blockers'],
                    features=rec['features'],
                    source_layers=rec['source_layers'],
                    rule_version=rec['rule_version'],
                    scan_dir=rec['scan_dir'],
                    dry_run=rec['dry_run'],
                )
                count += 1
            except Exception as e:
                print(f"  Error inserting pick {trade_date} {rec['symbol']}: {e}")

        for cand in rec['candidates']:
            if not isinstance(cand, dict):
                continue
            code = cand.get('code') or cand.get('symbol', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=(code == rec['symbol'] and rec['decision'] == 'PAPER_PICK'),
                    decision=rec['decision'] if code == rec['symbol'] else 'CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=_truncate_market_regime(cand.get('market_regime', '')),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=_sanitize_nan(cand),
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting candidate {trade_date} {code}: {e}")

        if rec['t1_return'] is not None and rec['symbol']:
            try:
                upsert_return(
                    trade_date=trade_date,
                    symbol=rec['symbol'],
                    pick_id=None,
                    t1_return=rec['t1_return'],
                    t2_return=rec['t2_return'],
                    t3_return=rec['t3_return'],
                    legacy_backfill=True,
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting return {trade_date} {rec['symbol']}: {e}")

    return count


def _parse_bundles(bundles_dir: Path) -> list:
    """Parse forward_candidate_bundles/ into structured records."""
    results = []
    if not bundles_dir.exists():
        return results

    for date_dir in sorted(bundles_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        json_files = list(date_dir.glob('*_candidate.json'))
        if not json_files:
            continue

        with open(json_files[0]) as f:
            bundle = json.load(f)

        trade_date_str = bundle.get('date', date_dir.name)
        if not _is_valid_trade_date(trade_date_str):
            continue
        candidates = bundle.get('paper_scoring_candidates', [])
        if not isinstance(candidates, list):
            candidates = []

        results.append({
            'date': trade_date_str,
            'candidates': candidates,
            'decision_reason': bundle.get('decision_reason', ''),
        })

    return results


def backfill_from_bundles() -> int:
    """Extract candidates from data/forward_candidate_bundles/."""
    bundles_dir = ROOT / 'data' / 'forward_candidate_bundles'
    if not bundles_dir.exists():
        print(f"Bundles dir not found: {bundles_dir}")
        return 0

    parsed = _parse_bundles(bundles_dir)
    count = 0

    for day in parsed:
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            if not isinstance(cand, dict):
                continue
            code = cand.get('code') or cand.get('symbol', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=False,
                    decision='CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=_truncate_market_regime(cand.get('market_regime', '')),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=_sanitize_nan(cand),
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting bundle candidate {trade_date} {code}: {e}")

    return count


def _parse_live_scan(scan_dir: Path) -> list:
    """Parse data/live_scan/ scored JSONL files."""
    results = []
    if not scan_dir.exists():
        return results

    for date_dir in sorted(scan_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        if not _is_valid_trade_date(date_dir.name):
            continue

        latest_summary = None
        for summary_path in date_dir.glob('*/xiaogu_scan_summary_runner.json'):
            try:
                summary = json.loads(summary_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            candidates = summary.get('paper_scoring_candidates')
            if not isinstance(candidates, list):
                continue
            source_time = str(summary.get('source_time') or '')
            if latest_summary is None or source_time > latest_summary['source_time']:
                latest_summary = {
                    'source_time': source_time,
                    'candidates': candidates,
                }
        if latest_summary is not None:
            results.append({
                'date': date_dir.name,
                'candidates': latest_summary['candidates'],
            })
            continue

        base_scan = date_dir / 'eastmoney_scan_afternoon'
        if not base_scan.exists():
            continue
        scored_file = base_scan / 'xiaogu_scan_summary_runner.json'
        if not scored_file.exists():
            continue

        candidates = []
        try:
            payload = json.loads(scored_file.read_text(encoding='utf-8'))
            candidates = [
                row for row in (payload.get('paper_scoring_candidates') or [])
                if isinstance(row, dict)
            ]
        except (OSError, json.JSONDecodeError):
            candidates = []

        results.append({
            'date': date_dir.name,
            'candidates': candidates,
        })

    return results


def backfill_from_live_scan(target_date: str = '') -> int:
    """Extract the latest scored candidates for each requested live-scan date."""
    scan_dir = ROOT / 'data' / 'live_scan'
    if not scan_dir.exists():
        print(f"Live scan dir not found: {scan_dir}")
        return 0

    parsed = _parse_live_scan(scan_dir)
    count = 0

    for day in parsed:
        if target_date and day['date'] != target_date:
            continue
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            code = str(cand.get('code') or cand.get('symbol') or '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('stock_name') or cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=cand.get('final_score') or cand.get('score'),
                    is_official_pick=False,
                    decision='CANDIDATE',
                    open_price=cand.get('open_price'),
                    close_price=cand.get('close_price') or cand.get('price'),
                    high_price=cand.get('high_price'),
                    low_price=cand.get('low_price'),
                    volume=cand.get('volume'),
                    amount=cand.get('amount') or cand.get('signal_amount'),
                    pct_chg=cand.get('pct_chg'),
                    turnover_rate=cand.get('turnover_rate'),
                    signal_pct=cand.get('signal_pct'),
                    close_position_score=cand.get('close_position_score'),
                    fund_flow_momentum=cand.get('fund_flow_momentum'),
                    sector_catalyst_score=cand.get('sector_catalyst_score'),
                    early_opportunity_score=cand.get('early_opportunity_score'),
                    topic_propagation_score=cand.get('topic_propagation_score'),
                    market_regime=_truncate_market_regime(cand.get('market_regime', '')),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=cand.get('source_layers') or [],
                    candidate_features={
                        'setup_type': cand.get('setup_type'),
                        'structured_priority_score': cand.get('structured_priority_score'),
                    },
                    raw_json=_sanitize_nan(cand),
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting scan candidate {trade_date} {code}: {e}")

    return count


def _parse_factors(factors_dir: Path) -> list:
    """Parse data/factors/*.parquet files."""
    results = []
    if not factors_dir.exists():
        return results

    try:
        import pandas as pd
    except ImportError:
        print("pandas not available, skipping factors")
        return results

    for pq_file in sorted(factors_dir.glob('*.parquet')):
        try:
            df = pd.read_parquet(pq_file)
        except Exception as e:
            print(f"  Error reading {pq_file}: {e}")
            continue

        date_str = pq_file.stem
        if len(date_str) == 8:
            trade_date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        else:
            continue
        if not _is_valid_trade_date(trade_date_str):
            continue

        candidates = []
        for _, row in df.iterrows():
            candidates.append({
                'code': str(row.get('code', '')),
                'symbol': str(row.get('symbol', row.get('code', ''))),
                'name': str(row.get('name', '')),
                'rank': None,
                'final_score': row.get('final_score') or row.get('score'),
                'score': row.get('score'),
                'signal_pct': row.get('pct_chg'),
                'close_position_score': row.get('close_position_score'),
                'fund_flow_momentum': row.get('net_inflow_main'),
                'sector_catalyst_score': row.get('sector_opportunity_score'),
                'early_opportunity_score': row.get('kline_language_score'),
                'topic_propagation_score': row.get('theme_strength_score'),
                'market_regime': '',
                'pct_chg': row.get('pct_chg'),
                'close': row.get('close'),
                'amount': row.get('amount'),
                'volume_ratio': row.get('volume_ratio'),
                'decision': row.get('decision', 'CANDIDATE'),
            })

        results.append({
            'date': trade_date_str,
            'candidates': candidates,
        })

    return results


def backfill_from_factors() -> int:
    """Extract candidates from data/factors/*.parquet."""
    factors_dir = ROOT / 'data' / 'factors'
    if not factors_dir.exists():
        print(f"Factors dir not found: {factors_dir}")
        return 0

    parsed = _parse_factors(factors_dir)
    count = 0

    for day in parsed:
        trade_date = date.fromisoformat(day['date'])
        for cand in day['candidates']:
            code = cand.get('code', '')
            if not code:
                continue
            try:
                upsert_daily_candidate(
                    trade_date=trade_date,
                    symbol=code,
                    stock_name=cand.get('name', ''),
                    rank=cand.get('rank'),
                    final_score=_sanitize_nan(cand.get('final_score')),
                    is_official_pick=False,
                    decision=cand.get('decision', 'CANDIDATE'),
                    open_price=None,
                    close_price=_sanitize_nan(cand.get('close')),
                    high_price=None,
                    low_price=None,
                    volume=None,
                    amount=_sanitize_nan(cand.get('amount')),
                    pct_chg=_sanitize_nan(cand.get('pct_chg')),
                    turnover_rate=None,
                    signal_pct=_sanitize_nan(cand.get('signal_pct')),
                    close_position_score=_sanitize_nan(cand.get('close_position_score')),
                    fund_flow_momentum=_sanitize_nan(cand.get('fund_flow_momentum')),
                    sector_catalyst_score=_sanitize_nan(cand.get('sector_catalyst_score')),
                    early_opportunity_score=_sanitize_nan(cand.get('early_opportunity_score')),
                    topic_propagation_score=_sanitize_nan(cand.get('topic_propagation_score')),
                    market_regime=_truncate_market_regime(cand.get('market_regime', '')),
                    sentiment_catalyst='',
                    theme_catalyst='',
                    news_catalyst='',
                    positive_catalyst='',
                    selection_reason='',
                    blockers=[],
                    hard_gate_status={},
                    source_layers=[],
                    candidate_features={},
                    raw_json=_sanitize_nan(cand),
                )
                count += 1
            except Exception as e:
                print(f"  Error upserting factor candidate {trade_date} {code}: {e}")

    return count


def _retry_payload_paths(target_date: str = ''):
    live_root = ROOT / 'data' / 'live_scan'
    if target_date:
        yield from sorted((live_root / target_date).glob('**/db_persistence_retry_payload.json'))
        return
    yield from sorted(live_root.glob('**/db_persistence_retry_payload.json'))


def _summary_replay_paths(target_date: str = ''):
    live_root = ROOT / 'data' / 'live_scan'
    if target_date:
        yield from sorted((live_root / target_date).glob('**/xiaogu_scan_summary_runner.json'))
        return
    yield from sorted(live_root.glob('**/xiaogu_scan_summary_runner.json'))


def replay_daily_candidate_snapshots(target_date: str = '', start_date: str = '', end_date: str = '', dry_run: bool = False) -> dict:
    """Replay runner candidate snapshots through the live persistence owner."""
    from xiaogu_forward_d1_1450_runner_v0_1 import _bundle_from_scan_summary, persist_daily_candidate_snapshot

    stats = {
        'payloads_found': 0,
        'payloads_replayed': 0,
        'payloads_skipped': 0,
        'written': 0,
        'dry_run': dry_run,
        'errors': [],
        'details': [],
    }
    replay_paths = [('payload', path) for path in _retry_payload_paths(target_date)]
    if not replay_paths:
        replay_paths = [('summary', path) for path in _summary_replay_paths(target_date)]
    for source_kind, path in replay_paths:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            stats['errors'].append({'path': str(path), 'error': repr(exc)})
            stats['payloads_skipped'] += 1
            continue
        if not isinstance(payload, dict):
            stats['payloads_skipped'] += 1
            continue
        if source_kind == 'summary':
            trade_date = str(payload.get('source_time') or path.parent.parent.name)[:10]
            bundle = _bundle_from_scan_summary(path, payload)
            if not isinstance(bundle, dict) or not bundle.get('available'):
                stats['payloads_skipped'] += 1
                stats['details'].append({'path': str(path), 'date': trade_date, 'status': 'SKIP_UNAVAILABLE_SUMMARY', 'reason': bundle.get('reason') if isinstance(bundle, dict) else ''})
                continue
            picks = fetch_picks(_date.fromisoformat(trade_date))
            official_pick = next(
                (row for row in picks if row.get('decision') == 'PAPER_PICK' and str(row.get('symbol') or '').strip()),
                None,
            )
            features = {'candidate_consumption_summary': {'official_result': {}}}
            if official_pick:
                features['candidate_consumption_summary']['official_result'] = {
                    'symbol': str(official_pick.get('symbol') or '').zfill(6),
                    'decision': official_pick.get('decision'),
                    'source': 'picks',
                }
            decision = str((official_pick or {}).get('decision') or 'REPLAY_DAILY_CANDIDATES')
            reason = 'replayed_from_scan_summary_with_db_pick' if official_pick else 'replayed_from_scan_summary'
        else:
            trade_date = str(payload.get('date') or '')
            bundle = payload.get('bundle') if isinstance(payload.get('bundle'), dict) else {}
            features = payload.get('features') if isinstance(payload.get('features'), dict) else {}
            decision = str(payload.get('decision') or 'NO_PICK')
            reason = str(payload.get('reason') or '')
        if not trade_date or not _is_valid_trade_date(trade_date):
            stats['payloads_skipped'] += 1
            continue
        if start_date and trade_date < start_date:
            stats['payloads_skipped'] += 1
            continue
        if end_date and trade_date > end_date:
            stats['payloads_skipped'] += 1
            continue
        candidate_rows = bundle.get('full_candidate_pool') or bundle.get('paper_scoring_candidates')
        if not isinstance(candidate_rows, list):
            stats['payloads_skipped'] += 1
            stats['details'].append({'path': str(path), 'date': trade_date, 'status': 'SKIP_NO_CANDIDATES'})
            continue
        stats['payloads_found'] += 1
        if dry_run:
            stats['details'].append({'path': str(path), 'date': trade_date, 'status': 'DRY_RUN', 'source_kind': source_kind, 'candidate_count': len(candidate_rows)})
            continue
        try:
            result = persist_daily_candidate_snapshot(
                trade_date,
                bundle,
                features,
                decision,
                reason,
            )
            stats['payloads_replayed'] += 1
            stats['written'] += int(result.get('written') or 0)
            stats['details'].append({'path': str(path), 'date': trade_date, 'source_kind': source_kind, 'result': result})
        except Exception as exc:
            stats['errors'].append({'path': str(path), 'date': trade_date, 'error': repr(exc)})
    return stats


def main():
    """Run all backfill sources."""
    import argparse
    parser = argparse.ArgumentParser(description='xiaogu DB backfill')
    parser.add_argument('--reconstruct', action='store_true', help='Rebuild late-June top10 snapshots with provenance')
    parser.add_argument('--cohort-labels', action='store_true', help='Classify all DB candidates in the period')
    parser.add_argument('--replay-daily-candidate-snapshots', action='store_true', help='Replay full runner DB retry payloads')
    parser.add_argument('--date', default='', help='Single trade date for replay-daily-candidate-snapshots')
    parser.add_argument('--start-date', default='2026-06-20')
    parser.add_argument('--end-date', default='2026-07-09')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.replay_daily_candidate_snapshots:
        result = replay_daily_candidate_snapshots(args.date, args.start_date if not args.date else '', args.end_date if not args.date else '', args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    if args.reconstruct:
        result = reconstruct_historical_evidence(args.start_date, args.end_date, args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    if args.cohort_labels:
        print(json.dumps(backfill_cohort_labels(args.start_date, args.dry_run), ensure_ascii=False, indent=2))
        return
    print("=== xiaogu DB Backfill ===")
    n1 = backfill_from_ledger()
    print(f"Ledger: {n1} records inserted")
    n2 = backfill_from_bundles()
    print(f"Bundles: {n2} records inserted")
    n3 = backfill_from_live_scan()
    print(f"Live scan: {n3} records inserted")
    n4 = backfill_from_factors()
    print(f"Factors: {n4} records inserted")
    print(f"Total: {n1 + n2 + n3 + n4} records")


if __name__ == "__main__":
    main()
