#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

BASE = Path('/root/hermes/company-ai-system/workspaces/xiaogu')
sys.path.insert(0, str(BASE))

from six_repo_integration_real_v2_1 import aggregate_four_repo_native_signals
from xiaogu_forward_runner import formal_candidate_sort_key, ranking_basis_adjustment_components

EASTMONEY = 'https://push2delay.eastmoney.com/api/qt/clist/get'
FIELDS = 'f12,f13,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21,f23,f62'
A_SHARE_MARKET_FS = 'm:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048'
MAIN_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003')
CHINEXT_PREFIXES = ('300', '301')
STAR_PREFIXES = ('688', '689')
BEIJING_PREFIXES = ('920',)
CORE_A_SHARE_BOARDS = ('main', 'chinext')
A_SHARE_CODE_RE = re.compile(r'(?<!\d)(?:(?:600|601|603|605|000|001|002|003|300|301|688|689|920)\d{3}|(?:4|8)\d{5})(?!\d)')
DIRECT_OPENER = build_opener(ProxyHandler({}))


def fnum(value, default=0.0):
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalized_code_text(code):
    text = str(code or '').strip()
    if text.isdigit() and 1 <= len(text) <= 6:
        return text.zfill(6)
    return text


def is_main_board(code):
    return normalized_code_text(code).startswith(MAIN_PREFIXES)


def is_a_share_code(code):
    return bool(A_SHARE_CODE_RE.fullmatch(normalized_code_text(code)))


def board_for_code(code):
    text = normalized_code_text(code)
    if text.startswith(CHINEXT_PREFIXES):
        return 'chinext'
    if text.startswith(STAR_PREFIXES):
        return 'star'
    if text.startswith(BEIJING_PREFIXES) or (text[:1] in ('4', '8') and text.isdigit() and len(text) == 6):
        return 'beijing'
    if text.startswith(MAIN_PREFIXES):
        return 'main'
    return 'unknown'


def should_bypass_proxy(url):
    host = urlparse(url).hostname or ''
    return host == 'eastmoney.com' or host.endswith('.eastmoney.com')


def fetch_page(page, sort_field, page_size):
    params = {
        'pn': page,
        'pz': page_size,
        'po': 1,
        'np': 1,
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': 2,
        'invt': 2,
        'fid': sort_field,
        'fs': A_SHARE_MARKET_FS,
        'fields': FIELDS,
    }
    req = Request(
        EASTMONEY + '?' + urlencode(params),
        headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            'Referer': 'https://quote.eastmoney.com/center/gridlist.html#hs_a_board',
        },
    )
    opener = DIRECT_OPENER if should_bypass_proxy(req.full_url) else None
    with (opener.open(req, timeout=25) if opener else urlopen(req, timeout=25)) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    return data.get('diff', []) or []


def normalize_quote(row, source):
    code = str(row.get('f12', '')).zfill(6)
    return {
        'code': code,
        'exchange_market': row.get('f13'),
        'name': row.get('f14'),
        'board': board_for_code(code),
        'price': fnum(row.get('f2')),
        'pct_chg': fnum(row.get('f3')),
        'chg': fnum(row.get('f4')),
        'volume': fnum(row.get('f5')),
        'amount': fnum(row.get('f6')),
        'amplitude': fnum(row.get('f7')),
        'turnover_rate': fnum(row.get('f8')),
        'pe_dynamic': fnum(row.get('f9')),
        'volume_ratio': fnum(row.get('f10')),
        'high': fnum(row.get('f15')),
        'low': fnum(row.get('f16')),
        'open': fnum(row.get('f17')),
        'prev_close': fnum(row.get('f18')),
        'market_cap': fnum(row.get('f20')),
        'float_market_cap': fnum(row.get('f21')),
        'pb': fnum(row.get('f23')),
        'net_inflow_main': fnum(row.get('f62')),
        'source': source,
    }


def collect_quotes(pages, page_size, allowed_boards=None):
    seen = {}
    expected_page_size = min(page_size, 100) if page_size else 100
    allowed_boards = tuple(allowed_boards) if allowed_boards else None
    for sort_field, source in [('f6', 'eastmoney_amount_ranked'), ('f3', 'eastmoney_pct_ranked')]:
        for page in range(1, pages + 1):
            rows = fetch_page(page, sort_field, page_size)
            if not rows:
                break
            for row in rows:
                code = str(row.get('f12', '')).zfill(6)
                if not is_a_share_code(code):
                    continue
                quote = normalize_quote(row, source)
                if quote['price'] <= 0:
                    continue
                if allowed_boards and quote.get('board') not in allowed_boards:
                    continue
                existing = seen.get(code)
                if existing is None or quote['amount'] > existing['amount']:
                    seen[code] = quote
            if len(rows) < expected_page_size:
                break
    return list(seen.values())


def load_quotes_from_file(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(payload, dict) and 'quotes' in payload:
        payload = payload['quotes']
    if not isinstance(payload, list):
        raise SystemExit(f'invalid quotes payload in {path}')
    return payload


def build_candidates(quotes, min_pct, max_pct, max_candidates, source_time, output_dir):
    tradable = [
        q for q in quotes
        if min_pct <= q['pct_chg'] <= max_pct
        and q['price'] > 0
    ]
    tradable.sort(key=lambda q: (q['amount'], q['pct_chg']), reverse=True)
    tradable = tradable[:max_candidates]

    amount_values = sorted(q['amount'] for q in tradable)
    market_breadth = round(sum(1 for q in quotes if q['pct_chg'] > 0) / len(quotes) * 100, 2) if quotes else 0.0
    market_limitups = sum(1 for q in quotes if q['pct_chg'] >= 9.5)
    market_bigups = sum(1 for q in quotes if q['pct_chg'] >= 5.0)

    def pctile(value):
        return round(sum(1 for amount in amount_values if amount <= value) / len(amount_values), 6) if amount_values else 0.0

    candidates = []
    for rank, q in enumerate(tradable, start=1):
        candidates.append({
            'signal_date': source_time[:10],
            'asof_time': source_time[11:],
            'code': q['code'],
            'name': q['name'],
            'board': q.get('board') or board_for_code(q['code']),
            'signal_close': q['price'],
            'price': q['price'],
            'signal_pct': q['pct_chg'],
            'signal_amount': q['amount'],
            'market_regime': 'eastmoney_tail_scan_live',
            'market_limitups': market_limitups,
            'market_bigups': market_bigups,
            'market_breadth_up_pct': market_breadth,
            'non_climax': q['pct_chg'] < 9.5,
            'theme_strength': min(12.0, max(0.0, q['pct_chg'])),
            'theme_big_strength': min(12.0, max(0.0, q['pct_chg']) * 0.6),
            'top_theme_token': 'EASTMONEY_TAIL_ALL_A_SHARE_SCAN',
            'rank': rank,
            'pct_rank': round(rank / len(tradable), 6) if tradable else 1.0,
            'amount_pctile_rule': pctile(q['amount']),
            'turnover_rate': q['turnover_rate'],
            'volume_ratio': q['volume_ratio'],
            'net_inflow_main': q['net_inflow_main'],
            'close_position_score': round((q['price'] - q['low']) / (q['high'] - q['low']), 6) if q['high'] > q['low'] else None,
            'source_time': source_time,
            'data_cutoff': source_time,
            'evidence_path': str(output_dir / 'eastmoney_tail_raw.jsonl'),
            'score_asof_provenance': 'eastmoney_tail_intraday_delayed_sample',
            'candidate_pool_count': len(tradable),
            'source_row_hash': f"eastmoney_tail:{source_time}:{q['code']}:{q['price']}:{q['pct_chg']}:{q['amount']}",
            'paper_only': True,
            'no_trade': True,
        })
    return candidates, {
        'market_breadth_up_pct': market_breadth,
        'market_limitups': market_limitups,
        'market_bigups': market_bigups,
        'tradable_candidate_count': len(tradable),
    }


def score_candidates(candidates):
    """Score through the single production main-force T+1 chain.

    This compatibility entrypoint no longer owns an independent technical or
    hot-money score. It is retained only for historical tail-scan callers.
    """
    scored = []
    block_reasons = Counter()
    for candidate in candidates:
        score_key = formal_candidate_sort_key(candidate)
        score = score_key[0] if score_key else None
        adjustment = ranking_basis_adjustment_components(candidate)
        reasons = list(adjustment.get('counter_evidence') or [])
        regime = candidate.get('market_regime')
        repo_signals = aggregate_four_repo_native_signals(candidate)
        record = {
            'signal_date': candidate['signal_date'],
            'asof_time': candidate['asof_time'],
            'code': candidate['code'],
            'name': candidate['name'],
            'board': candidate.get('board') or board_for_code(candidate['code']),
            'price': candidate['price'],
            'signal_pct': candidate['signal_pct'],
            'signal_amount': candidate['signal_amount'],
            'rank': candidate['rank'],
            'amount_pctile_rule': candidate['amount_pctile_rule'],
            'market_breadth_up_pct': candidate['market_breadth_up_pct'],
            'market_limitups': candidate.get('market_limitups'),
            'market_bigups': candidate.get('market_bigups'),
            'net_inflow_main': candidate.get('net_inflow_main'),
            'close_position_score': candidate.get('close_position_score'),
            'turnover_rate': candidate.get('turnover_rate'),
            'volume_ratio': candidate.get('volume_ratio'),
            'score': score,
            'market_regime': regime,
            'blocked_reasons': reasons,
            'production_score': score,
            'final_score': score,
            'ranking_view': 'main_force_behavior_chain',
            'score_source': 'formal_t1_profit_components',
            'ranking_basis_adjustment': adjustment,
            'repo_delta_by_repo': repo_signals.get('score_delta_by_repo', {}),
            'paper_only': True,
            'no_trade': True,
        }
        if score is None:
            for reason in reasons:
                block_reasons[reason.split(':')[0]] += 1
        scored.append(record)
    passed = sorted([record for record in scored if record['score'] is not None], key=lambda r: r['score'], reverse=True)
    return scored, passed, dict(block_reasons)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=80)
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--min-pct', type=float, default=5.0)
    parser.add_argument('--max-pct', type=float, default=9.0)
    parser.add_argument('--max-candidates', type=int, default=80)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--quotes-json', default=None, help='optional local quotes JSON produced by the direct API scanner')
    args = parser.parse_args()

    source_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    output_dir = Path(args.output_dir) if args.output_dir else BASE / 'data' / 'live_scan' / source_time[:10] / 'eastmoney_tail_scan_v0_2'
    output_dir.mkdir(parents=True, exist_ok=True)

    quotes = load_quotes_from_file(args.quotes_json) if args.quotes_json else collect_quotes(args.pages, args.page_size)
    candidates, market_stats = build_candidates(quotes, args.min_pct, args.max_pct, args.max_candidates, source_time, output_dir)
    scored, passed, block_reasons = score_candidates(candidates)

    raw_path = output_dir / 'eastmoney_tail_raw.jsonl'
    candidate_path = output_dir / 'eastmoney_tail_candidates.jsonl'
    scored_path = output_dir / 'eastmoney_tail_scored.jsonl'
    summary_path = output_dir / 'eastmoney_tail_summary.json'

    raw_path.write_text('\n'.join(json.dumps(q, ensure_ascii=False) for q in quotes) + ('\n' if quotes else ''), encoding='utf-8')
    candidate_path.write_text('\n'.join(json.dumps(c, ensure_ascii=False) for c in candidates) + ('\n' if candidates else ''), encoding='utf-8')
    scored_path.write_text('\n'.join(json.dumps(s, ensure_ascii=False) for s in scored) + ('\n' if scored else ''), encoding='utf-8')

    summary = {
        'source': 'eastmoney_push2delay_tail_scan',
        'pipeline_version': 'eastmoney_tail_scan_v0_2_native_repo',
        'source_time': source_time,
        'universe_quote_count': len(quotes),
        **market_stats,
        'scored_count': len(scored),
        'passed_count': len(passed),
        'blocked_reasons': block_reasons,
        'top_passed': passed[:10],
        'board_counts': {'main': len(quotes)},
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'files': {
            'raw': str(raw_path),
            'candidates': str(candidate_path),
            'scored': str(scored_path),
            'summary': str(summary_path),
        },
        'caveat': 'Uses Eastmoney delayed quote endpoint observed from the public page; verify freshness before any manual action.',
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
