#!/usr/bin/env python3
"""Historical backfill: scan summaries, candidate bundles, and ledgers → PostgreSQL."""
import argparse
import datetime
import glob
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from sqlalchemy import text

from xiaogu_db import (
    SessionLocal,
    fetch_daily_candidates,
    fetch_scan_data_directory_catalog,
    fetch_scan_data_directory_content,
    get_db,
    upsert_daily_candidate,
    upsert_return,
    upsert_scan_data_directory_records,
    upsert_signal,
)


def _runner_module():
    import importlib
    return importlib.import_module('xiaogu_forward_runner')


def _scanner_module():
    import importlib
    return importlib.import_module('scrapy_scanner.runner_v2')


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding='utf-8').splitlines():
        t = line.strip()
        if not t:
            continue
        try:
            rows.append(json.loads(t))
        except Exception:
            continue
    return rows


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except Exception:
                continue


def _ledger_decisions_and_fills(path: Path) -> Tuple[int, Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Count decisions without loading the full ledger into memory."""
    decision_count = 0
    fills: Dict[str, Dict[str, Any]] = {}
    samples: List[Dict[str, Any]] = []
    for row in iter_jsonl(path):
        record_type = row.get('record_type')
        if record_type in ('DECISION', 'CORRECTION'):
            decision_count += 1
            if len(samples) < 10:
                samples.append(row)
        elif record_type == 'RESULT_FILL':
            key = f"{row.get('date')}:{row.get('symbol')}"
            fills[key] = row
    return decision_count, fills, samples


def _safe_date(value: Any) -> Optional[datetime.date]:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _iter_candidate_bundle_files(root: Path) -> Iterable[Path]:
    yield from sorted(root.glob('*.json'))
    yield from sorted(root.glob('*/*.json'))
    yield from sorted(root.glob('**/*.json'))


def _iter_result_evidence_files(root: Path) -> Iterable[Path]:
    yield from sorted(root.glob('**/*.json'))


def _find_scan_summary_for_date(date_str: str) -> Optional[Path]:
    roots = [BASE / 'data' / 'live_scan', BASE / 'data' / 'forward_raw_runtime']
    for root in roots:
        for path in sorted(root.glob(f'{date_str}/**/xiaogu_scan_summary.json')):
            return path
    return None


def _find_candidate_bundle_for_date(date_str: str) -> Optional[Path]:
    root = BASE / 'data' / 'forward_candidate_bundles'
    for path in sorted(root.glob(f'{date_str}/*.json')):
        return path
    return None


def _bundle_from_paths(date_str: str, asof_time: Optional[str], summary_path: Optional[Path], bundle_path: Optional[Path]) -> Dict[str, Any]:
    if bundle_path and bundle_path.exists():
        try:
            data = load_json(bundle_path)
            data['available'] = True
            data['_bundle_path'] = str(bundle_path)
            return data
        except Exception:
            pass
    if summary_path and summary_path.exists():
        summary = load_json(summary_path)
        try:
            runner = _runner_module()
            data = runner._bundle_from_scan_summary(summary_path, summary)
            data['available'] = True
            data['_bundle_path'] = str(summary_path)
            return data
        except Exception:
            pass
    return {'available': False, 'reason': 'NO_BUNDLE', 'date': date_str, 'asof_time': asof_time or ''}


def _select_scan_dir_from_bundle(bundle: Dict[str, Any]) -> str:
    for key in ('scan_summary_path', '_bundle_path', 'scan_dir'):
        value = bundle.get(key)
        if value:
            return str(value)
    return ''


def _normalize_daily_decision(value: Any, is_official_pick: bool) -> str:
    decision = str(value or '').strip().upper()
    if decision in {'PAPER_PICK', 'NO_PICK', 'CANDIDATE'}:
        return decision
    return 'PAPER_PICK' if is_official_pick else 'CANDIDATE'


def _normalize_market_regime(value: Any) -> str:
    regime = str(value or '').strip()
    if not regime:
        return 'direct_api'
    return regime[:20]


def _extract_candidate_features(record: Dict[str, Any]) -> Dict[str, Any]:
    features = record.get('candidate_features') if isinstance(record.get('candidate_features'), dict) else {}
    if features:
        return dict(features)
    features_used = record.get('features_used') if isinstance(record.get('features_used'), dict) else {}
    if isinstance(features_used.get('candidate_features'), dict):
        return dict(features_used['candidate_features'])
    if isinstance(features_used, dict):
        return dict(features_used)
    return {}


def _ledger_entry_price_lookup(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
    lookup: Dict[Tuple[str, str], float] = {}
    for row in rows:
        if row.get('record_type') not in ('DECISION', 'CORRECTION'):
            continue
        date_str = str(row.get('date') or row.get('trade_date') or '')[:10]
        symbol = str(row.get('symbol') or '').strip()
        if not date_str or not symbol:
            continue
        features = _extract_candidate_features(row)
        for key in ('entry_price', 'price', 'signal_close'):
            try:
                parsed = float(features.get(key))
            except Exception:
                parsed = None
            if parsed is not None:
                lookup[(date_str, symbol)] = parsed
                break
    return lookup


def _ledger_entry_price_lookup_from_iter(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
    lookup: Dict[Tuple[str, str], float] = {}
    for row in rows:
        if row.get('record_type') not in ('DECISION', 'CORRECTION'):
            continue
        date_str = str(row.get('date') or row.get('trade_date') or '')[:10]
        symbol = str(row.get('symbol') or '').strip()
        if not date_str or not symbol:
            continue
        features = _extract_candidate_features(row)
        for key in ('entry_price', 'price', 'signal_close'):
            try:
                parsed = float(features.get(key))
            except Exception:
                parsed = None
            if parsed is not None:
                lookup[(date_str, symbol)] = parsed
                break
    return lookup


def _return_from_evidence_payload(decision_date: str, symbol: str, entry_price: float, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for raw in (payload.get('data') or {}).get('klines') or []:
        parts = str(raw).split(',')
        if len(parts) < 11:
            continue
        try:
            datetime.date.fromisoformat(parts[0])
        except Exception:
            continue
        try:
            values = [float(parts[i]) for i in range(1, 11)]
        except Exception:
            continue
        rows.append({
            'date': parts[0],
            'open': values[0],
            'close': values[1],
            'high': values[2],
            'low': values[3],
            'volume': values[4],
            'amount': values[5],
            'amplitude_pct': values[6],
            'pct_chg': values[7],
            'chg': values[8],
            'turnover_rate': values[9],
        })
    if not rows:
        return {'status': 'NO_KLINE_ROWS'}
    future_rows = [row for row in rows if row['date'] > decision_date]
    out: Dict[str, Any] = {'status': 'PASS', 'entry_price': entry_price, 'entry_date': decision_date}
    for horizon, idx in (('t1', 0), ('t2', 1), ('t3', 2), ('t5', 4)):
        if len(future_rows) <= idx:
            out[f'{horizon}_return'] = None
            continue
        exit_row = future_rows[idx]
        exit_high = exit_row.get('high')
        if exit_high is None:
            exit_high = exit_row.get('close')
        if exit_high is None:
            out[f'{horizon}_return'] = None
            continue
        out[f'{horizon}_return'] = (float(exit_high) - float(entry_price)) / float(entry_price)
    out['symbol'] = symbol
    out['payload_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return out


def _pick_id_for_trade_date_symbol(trade_date: datetime.date, symbol: str) -> Optional[int]:
    with get_db() as db:
        row = db.execute(
            text('SELECT id FROM picks WHERE trade_date = :trade_date AND symbol = :symbol ORDER BY id DESC LIMIT 1'),
            {'trade_date': trade_date, 'symbol': symbol},
        ).fetchone()
    return int(row[0]) if row else None


def _extract_trade_date(bundle: Dict[str, Any], fallback: str) -> datetime.date:
    for key in ('source_market_date', 'date', 'signal_date'):
        parsed = _safe_date(bundle.get(key))
        if parsed:
            return parsed
    return datetime.date.fromisoformat(fallback)


def _insert_scan_session(session: Dict[str, Any]) -> int:
    with get_db() as db:
        row = db.execute(
            text("""
                SELECT id FROM scan_sessions
                WHERE trade_date = :trade_date AND scan_dir = :scan_dir
                ORDER BY scan_time DESC, id DESC
                LIMIT 1
            """),
            {'trade_date': session['trade_date'], 'scan_dir': session['scan_dir']},
        ).fetchone()
        if row:
            return int(row[0])
        result = db.execute(
            text("""
                INSERT INTO scan_sessions
                    (trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, scan_dir, status)
                VALUES
                    (:trade_date, :scan_time, :source_id, :quotes_count, :scored_count, :passed_count, :scan_dir, :status)
                RETURNING id
            """),
            session,
        )
        return int(result.scalar_one())


def _write_bundle(bundle: Dict[str, Any], fallback_date: Optional[str] = None) -> int:
    date_str = str(bundle.get('date') or bundle.get('source_market_date') or bundle.get('signal_date') or fallback_date or '')[:10]
    if not date_str:
        return 0
    trade_date = _extract_trade_date(bundle, date_str)
    scan_time = str(bundle.get('source_time') or bundle.get('scan_summary_source_time') or bundle.get('asof_time') or '')
    scan_dir = _select_scan_dir_from_bundle(bundle)

    scan_session_id = _insert_scan_session({
        'trade_date': trade_date,
        'scan_time': scan_time or datetime.datetime.combine(trade_date, datetime.time(15, 0)).isoformat(),
        'source_id': str(bundle.get('eastmoney_source_id') or bundle.get('source_id') or ''),
        'quotes_count': int((bundle.get('full_universe_scan') or {}).get('quote_count') or bundle.get('universe_quote_count') or 0),
        'scored_count': int(bundle.get('scored_count') or len(bundle.get('paper_scoring_candidates') or [])),
        'passed_count': int(bundle.get('passed_count') or len([r for r in bundle.get('paper_scoring_candidates') or [] if r.get('decision') == 'PAPER_PICK'])),
        'scan_dir': scan_dir,
        'status': 'completed',
    })

    catalog_records = list(bundle.get('data_directory_catalog_records') or [])
    content_records = list(bundle.get('data_directory_content_records') or [])
    if catalog_records or content_records:
        upsert_scan_data_directory_records(
            scan_session_id=scan_session_id,
            trade_date=trade_date,
            scan_time=scan_time or datetime.datetime.combine(trade_date, datetime.time(15, 0)).isoformat(),
            catalog_records=catalog_records,
            content_records=content_records,
        )

    candidates = list(bundle.get('paper_scoring_candidates') or [])
    inserted = 0
    with get_db() as db:
        for row in candidates:
            symbol = str(row.get('symbol') or row.get('code') or '').strip()
            if not symbol:
                continue
            raw_json = row if isinstance(row, dict) else {}
            candidate_features = dict(row.get('candidate_features') or row.get('structured_component_details') or {})
            # If candidate_features is empty, populate from raw row data (v2 scanner compatibility)
            if not candidate_features:
                candidate_features = {
                    'setup_type': row.get('setup_type', ''),
                    'signal_pct': row.get('signal_pct'),
                    'close_position_score': row.get('close_position_score'),
                    'fund_flow_momentum': row.get('fund_flow_momentum'),
                    'sector_catalyst_score': row.get('sector_catalyst_score') or row.get('sector_opportunity_score'),
                    'turnover_rate': row.get('turnover_rate'),
                    'volume_ratio': row.get('volume_ratio'),
                    'net_inflow_main': row.get('net_inflow_main'),
                    'hsgt_net_inflow': row.get('hsgt_net_inflow'),
                    'hsgt_consecutive_days': row.get('hsgt_consecutive_days'),
                    'earnings_preview_flags': row.get('earnings_preview_flags'),
                    'lockup_days_to_expiry': row.get('lockup_days_to_expiry'),
                    'lockup_amount_ratio': row.get('lockup_amount_ratio'),
                    'announcement_sentiment': row.get('announcement_sentiment'),
                    'macro_liquidity_score': row.get('macro_liquidity_score'),
                }
            candidate_features.setdefault('final_score', row.get('final_score') or row.get('score'))
            candidate_features.setdefault('source_layers', list(row.get('source_layers') or []))
            upsert_daily_candidate(
                trade_date=trade_date,
                symbol=symbol,
                stock_name=str(row.get('name') or row.get('stock_name') or ''),
                rank=row.get('rank'),
                final_score=row.get('final_score') or row.get('score'),
                decision=_normalize_daily_decision(row.get('decision'), bool(row.get('is_official_pick'))),
                is_official_pick=bool(row.get('is_official_pick')),
                open_price=row.get('open') or row.get('open_price'),
                close_price=row.get('close') or row.get('close_price') or row.get('price'),
                high_price=row.get('high') or row.get('high_price'),
                low_price=row.get('low') or row.get('low_price'),
                volume=row.get('volume'),
                amount=row.get('amount'),
                pct_chg=row.get('pct_chg') or row.get('signal_pct'),
                turnover_rate=row.get('turnover_rate'),
                signal_pct=row.get('signal_pct'),
                close_position_score=row.get('close_position_score'),
                fund_flow_momentum=row.get('fund_flow_momentum'),
                sector_catalyst_score=row.get('sector_catalyst_score') or row.get('sector_opportunity_score'),
                early_opportunity_score=row.get('early_opportunity_score'),
                topic_propagation_score=row.get('topic_propagation_score'),
                market_regime=_normalize_market_regime(row.get('market_regime') or 'direct_api'),
                sentiment_catalyst=str(row.get('sentiment_catalyst') or ''),
                theme_catalyst=str(row.get('theme_catalyst') or ''),
                news_catalyst=str(row.get('news_catalyst') or ''),
                positive_catalyst=str(row.get('positive_catalyst') or ''),
                selection_reason=str(row.get('selection_reason') or bundle.get('decision_reason') or ''),
                blockers=list(row.get('blockers') or []),
                hard_gate_status=dict(row.get('hard_gate_status') or {}),
                source_layers=list(row.get('source_layers') or []),
                candidate_features=candidate_features,
                raw_json=raw_json,
            )
            try:
                scanner = _scanner_module()
                signal_rows = scanner.signal_records_from_candidate(row, scan_time or '')
            except Exception:
                signal_rows = []
            for signal_row in signal_rows:
                upsert_signal(
                    trade_date=trade_date,
                    symbol=str(signal_row.get('symbol') or symbol),
                    signal_key=str(signal_row.get('signal_key') or ''),
                    signal_value=signal_row.get('signal_value'),
                    raw_json=signal_row.get('raw_json') if isinstance(signal_row.get('raw_json'), dict) else signal_row,
                    db=db,
                )
            if row.get('decision') == 'PAPER_PICK' and row.get('final_score') is not None:
                pick_id = -1
                existing = db.execute(
                    text('SELECT id FROM picks WHERE trade_date=:d AND symbol=:s AND decision=:dec'),
                    {'d': trade_date, 's': symbol, 'dec': str(row.get('decision') or 'PAPER_PICK')},
                ).fetchone()
                if existing:
                    pick_id = int(existing[0])
                else:
                    pick_id = db.execute(
                        text("""
                            INSERT INTO picks
                                (trade_date, symbol, decision, final_score, blockers, features, source_layers,
                                 rule_version, scan_dir, dry_run, paper_only, no_trade)
                            VALUES
                                (:trade_date, :symbol, :decision, :final_score, CAST(:blockers AS jsonb),
                                 CAST(:features AS jsonb), CAST(:source_layers AS jsonb), :rule_version,
                                 :scan_dir, :dry_run, :paper_only, :no_trade)
                            RETURNING id
                        """),
                        {
                            'trade_date': trade_date,
                            'symbol': symbol,
                            'decision': str(row.get('decision') or 'PAPER_PICK'),
                            'final_score': row.get('final_score') or row.get('score'),
                            'blockers': json.dumps(list(row.get('blockers') or []), ensure_ascii=False, default=str),
                            'features': json.dumps(candidate_features, ensure_ascii=False, default=str),
                            'source_layers': json.dumps(list(row.get('source_layers') or []), ensure_ascii=False, default=str),
                            'rule_version': str(bundle.get('rule_version') or ''),
                            'scan_dir': scan_dir,
                            'dry_run': False,
                            'paper_only': True,
                            'no_trade': True,
                        },
                    ).scalar_one()
                fill = row.get('result_fill') if isinstance(row.get('result_fill'), dict) else None
                if fill:
                    upsert_return(
                        trade_date=trade_date,
                        symbol=symbol,
                        pick_id=pick_id if pick_id > 0 else None,
                        t1_return=fill.get('t1_return'),
                        t2_return=fill.get('t2_return'),
                        t3_return=fill.get('t3_return'),
                        t5_return=fill.get('t5_return'),
                        legacy_backfill=True,
                    )
            inserted += 1
    return inserted


def migrate_history(
    ledger_path: Optional[Path] = None,
    scan_root: Optional[Path] = None,
    bundle_root: Optional[Path] = None,
    result_evidence_root: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    summary = {
        'total_decisions': 0,
        'ledger_decisions': 0,
        'bundle_files': 0,
        'result_evidence_files': 0,
        'scan_summaries': 0,
        'inserted_picks': 0,
        'inserted_rows': 0,
        'skipped': 0,
    }

    if ledger_path and ledger_path.exists():
        decision_count, fills, samples = _ledger_decisions_and_fills(ledger_path)
        summary['ledger_decisions'] = decision_count
        summary['total_decisions'] = decision_count
        if dry_run:
            summary['inserted_picks'] = decision_count
            for d in samples:
                key = f"{d.get('date')}:{d.get('symbol') or ''}"
                fill = fills.get(key)
                print(f"DRY ledger {d.get('date')} {d.get('symbol')} {d.get('decision')} fill={bool(fill)}")
        else:
            with get_db() as db:
                for d in iter_jsonl(ledger_path):
                    if d.get('record_type') not in ('DECISION', 'CORRECTION'):
                        continue
                    date_str = d.get('date')
                    symbol = d.get('symbol') or ''
                    decision = d.get('decision') or 'UNKNOWN'
                    if not date_str:
                        summary['skipped'] += 1
                        continue
                    features = d.get('candidate_features') or {}
                    final_score = None
                    try:
                        final_score = float(features.get('final_score') or d.get('final_score') or 0) or None
                    except Exception:
                        pass
                    blockers = list(features.get('blockers') or [])
                    source_layers = list(features.get('source_layers') or d.get('source_layers') or [])
                    rule_version = d.get('rule_version') or ''
                    scan_dir = str(d.get('raw_data_snapshot_path') or '')
                    existing = db.execute(
                        text('SELECT id FROM picks WHERE trade_date=:d AND symbol=:s AND decision=:dec'),
                        {'d': date_str, 's': symbol, 'dec': decision},
                    ).fetchone()
                    if existing:
                        pick_id = int(existing[0])
                    else:
                        pick_id = db.execute(
                            text("""
                                INSERT INTO picks
                                    (trade_date, symbol, decision, final_score, blockers, features, source_layers,
                                     rule_version, scan_dir, dry_run, paper_only, no_trade)
                                VALUES
                                    (:trade_date, :symbol, :decision, :final_score, CAST(:blockers AS jsonb),
                                     CAST(:features AS jsonb), CAST(:source_layers AS jsonb), :rule_version,
                                     :scan_dir, false, true, true)
                                RETURNING id
                            """),
                            {
                                'trade_date': date_str,
                                'symbol': symbol,
                                'decision': decision,
                                'final_score': final_score,
                                'blockers': json.dumps(blockers, ensure_ascii=False),
                                'features': json.dumps(features, ensure_ascii=False),
                                'source_layers': json.dumps(source_layers, ensure_ascii=False),
                                'rule_version': rule_version,
                                'scan_dir': scan_dir,
                            },
                        ).scalar_one()
                        summary['inserted_picks'] += 1
                        summary['inserted_rows'] += 1
                    key = f"{date_str}:{symbol}"
                    fill = fills.get(key)
                    if fill and pick_id:
                        t1 = fill.get('t1_return')
                        t2 = fill.get('t2_return')
                        t3 = fill.get('t3_return')
                        t5 = fill.get('t5_return')
                        if any(v is not None for v in [t1, t2, t3, t5]):
                            upsert_return(
                                trade_date=datetime.date.fromisoformat(date_str),
                                symbol=symbol,
                                pick_id=pick_id,
                                t1_return=t1,
                                t2_return=t2,
                                t3_return=t3,
                                t5_return=t5,
                                legacy_backfill=True,
                                db=db,
                            )
        return summary

    bundle_root = bundle_root or (BASE / 'data' / 'forward_candidate_bundles')
    result_evidence_root = result_evidence_root or (BASE / 'data' / 'forward_result_evidence' / 'eastmoney')

    bundle_paths = list(_iter_candidate_bundle_files(bundle_root))
    summary['bundle_files'] = len(bundle_paths)
    evidence_paths = list(_iter_result_evidence_files(result_evidence_root))
    summary['result_evidence_files'] = len(evidence_paths)

    if dry_run:
        for path in bundle_paths[:5]:
            print(f'DRY bundle {path}')
        for path in evidence_paths[:5]:
            print(f'DRY evidence {path}')
        return summary

    processed_dates = set()
    for bundle_path in bundle_paths:
        try:
            bundle = load_json(bundle_path)
        except Exception:
            summary['skipped'] += 1
            continue
        date_str = str(bundle.get('date') or bundle.get('source_market_date') or bundle_path.parent.name)[:10]
        if date_str in processed_dates:
            continue
        try:
            inserted = _write_bundle(bundle, fallback_date=date_str)
            summary['inserted_rows'] += inserted
            processed_dates.add(date_str)
        except Exception:
            summary['skipped'] += 1

    entry_price_cache: Dict[Tuple[str, str], float] = {}
    try:
        rows = iter_jsonl(ledger_path) if ledger_path and ledger_path.exists() else ()
        entry_price_cache = _ledger_entry_price_lookup_from_iter(rows)
    except Exception:
        entry_price_cache = {}

    for evidence_path in evidence_paths:
        try:
            evidence = load_json(evidence_path)
        except Exception:
            summary['skipped'] += 1
            continue
        symbol = str(evidence.get('symbol') or evidence_path.name.split('_')[0] or '').strip().zfill(6)
        date_str = str(evidence.get('decision_date') or evidence_path.parent.name or '')[:10]
        if not symbol or not date_str:
            summary['skipped'] += 1
            continue
        trade_date = datetime.date.fromisoformat(date_str)
        payload = evidence.get('payload') if isinstance(evidence.get('payload'), dict) else {}
        entry_price = evidence.get('entry_price')
        if entry_price is None:
            entry_price = entry_price_cache.get((date_str, symbol))
        if entry_price is None and isinstance(payload.get('data'), dict):
            klines = payload['data'].get('klines') or []
            if klines:
                try:
                    entry_price = float(str(klines[0]).split(',')[1])
                except Exception:
                    entry_price = None
        if entry_price is None:
            summary['skipped'] += 1
            continue
        returns = _return_from_evidence_payload(date_str, symbol, float(entry_price), payload)
        pick_id = _pick_id_for_trade_date_symbol(trade_date, symbol)
        upsert_return(
            trade_date=trade_date,
            symbol=symbol,
            pick_id=pick_id,
            t1_return=returns.get('t1_return'),
            t2_return=returns.get('t2_return'),
            t3_return=returns.get('t3_return'),
            t5_return=returns.get('t5_return'),
            legacy_backfill=True,
        )
        summary['inserted_rows'] += 1

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description='Historical backfill into xiaogu PostgreSQL')
    ap.add_argument('--ledger', type=Path, default=BASE / 'forward_paper_ledger_v0_1.jsonl')
    ap.add_argument('--scan-root', type=Path, default=BASE / 'data' / 'live_scan')
    ap.add_argument('--bundle-root', type=Path, default=BASE / 'data' / 'forward_candidate_bundles')
    ap.add_argument('--ledger-only', action='store_true')
    ap.add_argument('--scan-only', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if args.ledger_only and args.scan_only:
        raise SystemExit('--ledger-only and --scan-only are mutually exclusive')

    if args.ledger_only:
        result = migrate_history(ledger_path=args.ledger, dry_run=args.dry_run)
    elif args.scan_only:
        result = migrate_history(scan_root=args.scan_root, bundle_root=args.bundle_root, dry_run=args.dry_run)
    else:
        ledger_result = migrate_history(ledger_path=args.ledger, dry_run=args.dry_run)
        scan_result = migrate_history(scan_root=args.scan_root, bundle_root=args.bundle_root, dry_run=args.dry_run)
        result = {
            'ledger': ledger_result,
            'scan': scan_result,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
