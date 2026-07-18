#!/usr/bin/env python3
"""Parquet factor store — write and read daily factor snapshots."""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BASE = Path(__file__).resolve().parent
FACTOR_DIR = BASE / 'data' / 'factors'

# Columns to extract from scored candidates
FACTOR_COLUMNS = [
    'code', 'symbol', 'name', 'score', 'final_score',
    'pct_chg', 'close', 'amount', 'volume_ratio',
    'net_inflow_main', 'close_position_score',
    'sector_opportunity_score', 'early_opportunity_score',
    'low_position_catalyst_score', 'kline_language_score',
    'fund_flow_score', 'theme_strength_score',
    'announcement_catalyst_score', 'hsgt_institutional_flow',
    'sealed_limit_up', 'source_layers', 'hard_block',
    'blocker_count', 'decision',
]


def write_factors(trade_date: str, candidates: List[Dict[str, Any]]) -> Path:
    """Write candidates to daily parquet file. Returns path written."""
    FACTOR_DIR.mkdir(parents=True, exist_ok=True)
    date_str = trade_date.replace('-', '')
    path = FACTOR_DIR / f'{date_str}.parquet'

    rows = []
    for c in candidates:
        row = {'trade_date': trade_date}
        for col in FACTOR_COLUMNS:
            val = c.get(col)
            # Flatten source_layers list to string
            if col == 'source_layers' and isinstance(val, list):
                val = ','.join(str(v) for v in val)
            row[col] = val
        rows.append(row)

    if not rows:
        return path

    df = pd.DataFrame(rows)
    # Ensure numeric columns
    for col in ['score', 'final_score', 'pct_chg', 'close', 'amount',
                'volume_ratio', 'net_inflow_main', 'close_position_score',
                'sector_opportunity_score', 'early_opportunity_score',
                'low_position_catalyst_score', 'blocker_count',
                'hsgt_institutional_flow']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    pq.write_table(pa.Table.from_pandas(df), path)
    return path


def read_factors(trade_date: str) -> Optional[pd.DataFrame]:
    """Read factors for a given date. Returns None if not found."""
    date_str = trade_date.replace('-', '')
    path = FACTOR_DIR / f'{date_str}.parquet'
    if not path.exists():
        return None
    return pq.read_table(path).to_pandas()


def read_all_factors(start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
    """Read all factor parquet files, optionally filtered by date range."""
    files = sorted(FACTOR_DIR.glob('*.parquet'))
    dfs = []
    for f in files:
        date_str = f.stem  # YYYYMMDD
        if start_date and date_str < start_date.replace('-', ''):
            continue
        if end_date and date_str > end_date.replace('-', ''):
            continue
        try:
            dfs.append(pq.read_table(f).to_pandas())
        except Exception:
            pass
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def factors_summary() -> Dict[str, Any]:
    """Return summary of available factor data."""
    files = sorted(FACTOR_DIR.glob('*.parquet'))
    return {
        'available_dates': len(files),
        'date_range': [files[0].stem, files[-1].stem] if files else [],
        'total_size_mb': round(sum(f.stat().st_size for f in files) / 1024 / 1024, 2),
    }


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=['summary', 'read'], default='summary', nargs='?')
    ap.add_argument('--date')
    args = ap.parse_args()
    if args.command == 'summary':
        print(json.dumps(factors_summary(), indent=2))
    elif args.command == 'read' and args.date:
        df = read_factors(args.date)
        if df is not None:
            print(df.to_string())
        else:
            print(f'No factors for {args.date}')
