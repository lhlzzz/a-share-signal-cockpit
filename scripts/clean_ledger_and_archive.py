#!/usr/bin/env python3
"""Clean ledger file by removing large fields and archive to database."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
CLEAN_LEDGER = BASE / 'forward_paper_ledger_v0_1_clean.jsonl'

# Fields to remove from features_used (large fields)
FIELDS_TO_REMOVE = {
    'runtime_market_snapshot',
    'candidate_bundle_status',
    'structured_observation_basket',
    'structured_sector_observation_basket',
    'structured_formal_impact',
    'no_pick_candidate_diagnostics',
    'daily_best_paper_watch',
    'sector_catalyst_diagnostics',
    'daily_ticket_search_result',
    'paper_pick_candidate_stage_distribution',
    'candidate_stage_blocker_distribution',
}


def clean_record(record):
    """Remove large fields from a record."""
    cleaned = dict(record)
    
    # Clean features_used
    if 'features_used' in cleaned and isinstance(cleaned['features_used'], dict):
        features = dict(cleaned['features_used'])
        for field in FIELDS_TO_REMOVE:
            if field in features:
                # Keep only a summary
                if field == 'runtime_market_snapshot':
                    features[field] = {'_cleaned': True, '_original_size': 'large'}
                elif field == 'candidate_bundle_status':
                    features[field] = {'_cleaned': True, '_original_size': 'large'}
                else:
                    del features[field]
        cleaned['features_used'] = features
    
    return cleaned


def archive_to_db(record):
    """Archive a record to the database."""
    try:
        from xiaogu_db import insert_pick, upsert_return
        from datetime import date as date_type
        
        trade_date = record.get('date')
        if not trade_date:
            return False
        
        try:
            trade_date = date_type.fromisoformat(trade_date)
        except ValueError:
            return False
        
        symbol = record.get('symbol', '')
        decision = record.get('decision', '')
        
        if decision not in ('PAPER_PICK', 'NO_PICK', 'RESEARCH_CANDIDATE'):
            return False
        
        # Extract score from features
        features = record.get('features_used', {})
        candidate_features = features.get('candidate_features', {})
        score = candidate_features.get('final_score') or candidate_features.get('score')
        
        # Extract blockers
        blockers = candidate_features.get('blockers', [])
        if isinstance(blockers, str):
            blockers = [blockers]
        
        # Extract source layers
        source_layers = candidate_features.get('source_layers', [])
        if isinstance(source_layers, str):
            source_layers = [source_layers]
        
        # Insert pick
        insert_pick(
            trade_date=trade_date,
            symbol=symbol,
            decision=decision,
            final_score=float(score) if score is not None else None,
            blockers=blockers,
            features=features,
            source_layers=source_layers,
            rule_version=record.get('rule_version', ''),
            scan_dir=record.get('raw_data_snapshot_path', ''),
            dry_run=False,
        )
        
        # Insert return if available
        t1_return = record.get('t1_return')
        if t1_return is not None:
            upsert_return(
                trade_date=trade_date,
                symbol=symbol,
                pick_id=None,
                t1_return=float(t1_return) if t1_return is not None else None,
                t2_return=float(record.get('t2_return')) if record.get('t2_return') is not None else None,
                t3_return=float(record.get('t3_return')) if record.get('t3_return') is not None else None,
            )
        
        return True
    except Exception as e:
        print(f'WARN: archive_to_db failed for {record.get("date")} {record.get("symbol")}: {e}', file=sys.stderr)
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Clean ledger and archive to database')
    ap.add_argument('--dry-run', action='store_true', help='Only print what would be done')
    ap.add_argument('--skip-archive', action='store_true', help='Skip database archival')
    ap.add_argument('--limit', type=int, default=0, help='Limit number of records to process (0=all)')
    args = ap.parse_args()
    
    if not LEDGER.exists():
        print(f'ERROR: Ledger file not found: {LEDGER}')
        return
    
    print(f'Reading ledger: {LEDGER}')
    print(f'File size: {LEDGER.stat().st_size / 1024 / 1024:.1f} MB')
    
    cleaned_count = 0
    archived_count = 0
    skipped_count = 0
    
    with open(LEDGER, 'r') as fin, open(CLEAN_LEDGER, 'w') as fout:
        for i, line in enumerate(fin):
            if args.limit and i >= args.limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f'WARN: Invalid JSON at line {i+1}', file=sys.stderr)
                skipped_count += 1
                continue
            
            # Clean the record
            cleaned = clean_record(record)
            
            # Write cleaned record
            fout.write(json.dumps(cleaned, ensure_ascii=False) + '\n')
            cleaned_count += 1
            
            # Archive to database
            if not args.skip_archive:
                if archive_to_db(record):
                    archived_count += 1
            
            if (i + 1) % 10 == 0:
                print(f'Processed {i+1} records...', file=sys.stderr)
    
    print(f'\nResults:')
    print(f'  Cleaned: {cleaned_count} records')
    print(f'  Archived to DB: {archived_count} records')
    print(f'  Skipped: {skipped_count} records')
    print(f'  Clean ledger: {CLEAN_LEDGER}')
    print(f'  Clean ledger size: {CLEAN_LEDGER.stat().st_size / 1024 / 1024:.1f} MB')
    
    if not args.dry_run and cleaned_count > 0:
        print(f'\nTo replace original ledger:')
        print(f'  mv {LEDGER} {LEDGER}.bak')
        print(f'  mv {CLEAN_LEDGER} {LEDGER}')


if __name__ == '__main__':
    main()
