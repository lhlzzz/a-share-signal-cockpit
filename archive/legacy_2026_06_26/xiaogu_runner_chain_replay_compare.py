#!/usr/bin/env python3
import json
import sys
from pathlib import Path

BASE = Path('/root/hermes/company-ai-system/workspaces/xiaogu')
sys.path.insert(0, str(BASE))

import xiaogu_forward_d1_1450_runner_v0_1 as runner
import xiaogu_forward_result_filler_v0_1 as filler
import xiaogu_forward_judge_scoreboard_v0_1 as scoreboard

SOURCE_LEDGER = BASE / 'forward_paper_ledger_v0_1.jsonl'
OUTPUT_DIR = BASE / 'summary'
NEW_LEDGER = OUTPUT_DIR / 'runner_chain_replay_new_ledger.jsonl'
COMPARE_JSON = OUTPUT_DIR / 'runner_chain_replay_compare.json'
START_DATE = '2026-05-18'


def load_rows(path: Path):
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def replay_dates(rows):
    dates = set()
    for row in rows:
        if row.get('record_type', 'DECISION') not in ('DECISION', 'CORRECTION'):
            continue
        date = str(row.get('date') or '')
        if date >= START_DATE and row.get('decision') in ('PAPER_PICK', 'RESEARCH_CANDIDATE', 'NO_PICK'):
            dates.add(date)
    return sorted(dates)


def baseline_decisions(rows, dates):
    out = {}
    for row in rows:
        if row.get('record_type', 'DECISION') not in ('DECISION', 'CORRECTION'):
            continue
        date = row.get('date')
        if date in dates and row.get('decision') in ('PAPER_PICK', 'RESEARCH_CANDIDATE', 'NO_PICK'):
            out[date] = row
    return out


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    source_rows = load_rows(SOURCE_LEDGER)
    dates = replay_dates(source_rows)
    base_by_date = baseline_decisions(source_rows, dates)
    new_rows = []
    decision_summaries = []

    for date in dates:
        baseline = base_by_date.get(date) or {}
        requested_asof = str(baseline.get('asof_time') or '14:50:00')
        bundle = runner.load_candidate_bundle(date, requested_asof)
        if not bundle.get('available'):
            decision_summaries.append({'date': date, 'status': 'bundle_unavailable', 'reason': bundle.get('reason')})
            continue

        # If legacy bundle is too sparse, re-drive from the same-day scan candidate basket.
        candidates = bundle.get('paper_scoring_candidates') or []
        first_candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        any_latest_scan = runner.load_latest_eastmoney_scan(date, None)
        needs_rebuild = (
            any_latest_scan is not None
            and (
                not candidates
                or not first_candidate.get('candidate_stage')
                or not first_candidate.get('early_opportunity_score')
                or not first_candidate.get('source_time')
            )
        )
        bundle_rebuild_mode = 'legacy_bundle'
        if needs_rebuild:
            rebuilt = runner.build_research_basket_from_latest_scan(date, requested_asof or str(bundle.get('asof_time') or '14:50:00'))
            if rebuilt.get('available'):
                rebuilt['_allow_legacy_replay_governance_bypass'] = True
                bundle = rebuilt
                bundle_rebuild_mode = 'rebuilt_from_same_day_scan'

        asof_time = str(bundle.get('asof_time') or bundle.get('_runner_asof_time') or requested_asof or '14:50:00')
        bundle['_runner_asof_time'] = asof_time
        bundle['_allow_legacy_replay_governance_bypass'] = True
        runner.attach_paper_pick_eligibility(bundle)
        decision, symbol, reason, features, risk_flags = runner.evaluate_candidate_bundle(bundle, date)
        paper = {
            'record_type': 'DECISION',
            'date': date,
            'generated_at': runner.now_iso(),
            'asof_time': asof_time,
            'symbol': symbol or ('NO_PICK' if decision == 'NO_PICK' else ''),
            'decision': decision,
            'rule_version': runner.RULE_VERSION,
            'decision_reason': reason,
            'features_used': {
                'runner': 'xiaogu_runner_chain_replay_compare',
                'date': date,
                'asof_time': asof_time,
                'candidate_features': features,
                'risk_flags': risk_flags,
                'candidate_bundle_status': {
                    'available': bundle.get('available'),
                    'source_time': bundle.get('source_time'),
                    'scan_summary_path': bundle.get('scan_summary_path'),
                },
            },
            'paper_only': True,
            'no_trade': True,
            'production_ready': False,
            'allow_trade': False,
            'auto_order': False,
            'result_status': 'PENDING',
            't1_return': None,
            't2_return': None,
            't3_return': None,
            'result_filled_at': None,
            'post_result_locked': False,
        }
        new_rows.append(paper)
        decision_summaries.append({
            'date': date,
            'baseline_decision': (base_by_date.get(date) or {}).get('decision'),
            'baseline_symbol': (base_by_date.get(date) or {}).get('symbol'),
            'new_decision': decision,
            'new_symbol': paper['symbol'],
            'new_reason': reason,
            'bundle_rebuild_mode': bundle_rebuild_mode,
        })

    write_jsonl(NEW_LEDGER, new_rows)

    rows_for_fill = list(new_rows)
    filled_rows = []
    for row in list(new_rows):
        if row.get('decision') != 'PAPER_PICK':
            continue
        ret = None
        evidence = None
        attempts = []
        for fn_name, fn in [
            ('live_scan', filler.return_from_live_scan_quote_evidence),
            ('web_json', filler.return_from_web_json_evidence),
            ('auto_web', filler.auto_return_web),
            ('auto_eastmoney', filler.auto_return),
        ]:
            r, e = fn(row, 't1')
            attempts.append({'source': fn_name, 'status': e.get('status'), 'ret': r})
            if r is not None:
                ret, evidence = r, e
                evidence['selected_source'] = fn_name
                break
        if ret is None:
            filled_rows.append({'date': row['date'], 'symbol': row['symbol'], 'status': 'UNFILLED', 'attempts': attempts})
            continue
        fill = filler.build_fill(rows_for_fill, row, 't1', ret, runner.now_iso(), evidence)
        rows_for_fill.append(fill)
        new_rows.append(fill)
        filled_rows.append({'date': row['date'], 'symbol': row['symbol'], 'status': 'FILLED', 't1_return': ret, 'source': evidence.get('selected_source'), 'evidence_status': evidence.get('status')})

    write_jsonl(NEW_LEDGER, new_rows)

    baseline_board = scoreboard.build_forward_judge_scoreboard(scoreboard.merge_forward_ledger(source_rows))
    new_board = scoreboard.build_forward_judge_scoreboard(scoreboard.merge_forward_ledger(new_rows))

    compare = {
        'dates': dates,
        'decision_summaries': decision_summaries,
        'fills': filled_rows,
        'baseline_metrics': baseline_board.get('horizons', {}).get('t1', {}).get('overall'),
        'new_metrics': new_board.get('horizons', {}).get('t1', {}).get('overall'),
        'baseline_chain_scorecard': baseline_board.get('a_share_chain_scorecard', {}).get('A_SHARE_CHAIN'),
        'new_chain_scorecard': new_board.get('a_share_chain_scorecard', {}).get('A_SHARE_CHAIN'),
        'new_ledger_path': str(NEW_LEDGER),
    }
    COMPARE_JSON.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(compare, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
