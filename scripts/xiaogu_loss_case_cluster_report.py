#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cluster loss cases from pick_case_embeddings (+ knowledge fallback).

Produces:
  summary/YYYY-MM-DD_loss_case_clusters.json
  summary/YYYY-MM-DD_loss_case_clusters.md

Soft-only diagnostic. Does not change hard gates.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

THEME_BUCKETS = [
    ('贵金属有色', re.compile(r'黄金|贵金属|有色|白银|铜|钼|锡|银锡|紫金|山金|兴业银锡')),
    ('电力公用', re.compile(r'电力|水电|火电|能源|燃气|煤炭|平煤|华银|长江电力|豫能|九丰')),
    ('半导体电子', re.compile(r'半导体|芯片|电子|通信|中兴|紫光|通富|华天|海星')),
    ('医药', re.compile(r'医药|药业|医疗|创新药|恒瑞|昭衍')),
    ('游戏传媒', re.compile(r'游戏|传媒|影视|网络|巨人|儒意')),
    ('机械军工', re.compile(r'军工|机械|机器人|埃斯顿|长高')),
    ('消费食品', re.compile(r'食品|控股|莲花|教育|中公')),
    ('化工材料', re.compile(r'化学|化工|新材|阿科力|百傲')),
]

PATTERN_RULES = [
    ('post_limitup_weak', re.compile(r'涨停|limit.?up|was_yesterday|封板|连板', re.I)),
    ('hollow_theme', re.compile(r'main_theme_core_score=0|core=0\.0|theme_core=0|空主题')),
    ('chase_high_fund', re.compile(r'fund_flow_momentum=0\.[7-9]|追高|pct.?chg.?[=:].*[7-9]\.|signal_pct[=:].*[7-9]')),
    ('partial_aux', re.compile(r'PARTIAL|partial_aux|strong_sector_theme_partial')),
    ('seed_soft', re.compile(r"'source': 'seed'|source=seed|seed soft")),
    ('resource_cluster', re.compile(r'黄金|有色|煤炭|能源|贵金属')),
]


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


def load_from_db(min_date: str, max_date: str) -> List[Dict[str, Any]]:
    """Read loss rows from pgvector table. Avoid embedding model load."""
    try:
        from sqlalchemy import text
        from xiaogu_db import get_db
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with get_db() as db:
            exists = db.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='pick_case_embeddings'
            """)).fetchone()
            if not exists:
                return []
            q = text("""
                SELECT trade_date, symbol, stock_name, decision, final_score,
                       t1_return, case_text, metadata
                FROM pick_case_embeddings
                WHERE t1_return IS NOT NULL
                  AND t1_return < 0
                  AND trade_date >= CAST(:min_d AS date)
                  AND trade_date <= CAST(:max_d AS date)
                ORDER BY t1_return ASC
            """)
            for r in db.execute(q, {'min_d': min_date, 'max_d': max_date}).mappings().all():
                d = dict(r)
                d['trade_date'] = d['trade_date'].isoformat() if hasattr(d['trade_date'], 'isoformat') else str(d['trade_date'])
                d['source'] = 'pick_case_embeddings'
                rows.append(d)
    except Exception:
        return []
    return rows


def load_from_knowledge(min_date: str, max_date: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((ROOT / 'summary').glob('*_top10_knowledge.json')):
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        td = str(d.get('trade_date') or path.name[:10])
        if td < min_date or td > max_date:
            continue
        for it in d.get('top10') or []:
            if not isinstance(it, dict):
                continue
            t1 = it.get('t1_return')
            if t1 is None:
                t1 = it.get('t1_return_close')
            if t1 is None:
                continue
            try:
                t1f = float(t1)
            except Exception:
                continue
            if t1f >= 0:
                continue
            rows.append({
                'trade_date': td,
                'symbol': it.get('symbol'),
                'stock_name': it.get('stock_name') or it.get('name'),
                'decision': it.get('decision') or it.get('selection_outcome') or 'TOP10',
                'final_score': it.get('final_score') or it.get('score'),
                't1_return': t1f,
                'case_text': json.dumps(it, ensure_ascii=False)[:500],
                'metadata': {},
                'source': f'knowledge:{path.name}',
            })
        for it in d.get('paper_pick_returns') or []:
            if not isinstance(it, dict):
                continue
            t1 = it.get('t1_return_close')
            if t1 is None:
                t1 = it.get('t1_return')
            if t1 is None:
                continue
            try:
                t1f = float(t1)
            except Exception:
                continue
            if t1f >= 0:
                continue
            rows.append({
                'trade_date': td,
                'symbol': it.get('symbol'),
                'stock_name': it.get('stock_name') or it.get('name'),
                'decision': 'PAPER_PICK',
                'final_score': it.get('final_score'),
                't1_return': t1f,
                'case_text': json.dumps(it, ensure_ascii=False)[:500],
                'metadata': {},
                'source': f'knowledge_paper:{path.name}',
            })
    return rows


def theme_bucket(text: str) -> str:
    for name, rx in THEME_BUCKETS:
        if rx.search(text or ''):
            return name
    return '其他'


def pattern_hits(text: str) -> List[str]:
    hits = []
    for name, rx in PATTERN_RULES:
        if rx.search(text or ''):
            hits.append(name)
    return hits or ['unclassified']


def cluster_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    # dedupe by date+symbol keep worst t1
    best: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (str(r.get('trade_date')), str(r.get('symbol') or '').zfill(6))
        if key not in best or float(r['t1_return']) < float(best[key]['t1_return']):
            best[key] = r
    uniq = list(best.values())
    by_theme: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_pattern: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_decision = Counter()
    severe = []
    paper = []
    for r in uniq:
        t1 = float(r['t1_return'])
        blob = ' '.join([
            str(r.get('stock_name') or ''),
            str(r.get('case_text') or ''),
            str(r.get('decision') or ''),
        ])
        th = theme_bucket(blob)
        r['theme_bucket'] = th
        pats = pattern_hits(blob)
        r['pattern_hits'] = pats
        by_theme[th].append(r)
        for p in pats:
            by_pattern[p].append(r)
        by_decision[str(r.get('decision') or '?')] += 1
        if t1 <= -0.05:
            severe.append(r)
        if str(r.get('decision') or '').upper() in ('PAPER_PICK', 'OFFICIAL_PICK'):
            paper.append(r)

    def _summ(items: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
        items = sorted(items, key=lambda x: float(x['t1_return']))
        out = []
        for r in items[:limit]:
            out.append({
                'trade_date': r.get('trade_date'),
                'symbol': r.get('symbol'),
                'stock_name': r.get('stock_name'),
                'decision': r.get('decision'),
                't1_return': round(float(r['t1_return']), 6),
                'final_score': r.get('final_score'),
                'theme_bucket': r.get('theme_bucket'),
                'pattern_hits': r.get('pattern_hits'),
                'source': r.get('source'),
            })
        return out

    themes = {
        k: {
            'n': len(v),
            'avg_t1': round(sum(float(x['t1_return']) for x in v) / len(v), 6) if v else None,
            'samples': _summ(v),
        }
        for k, v in sorted(by_theme.items(), key=lambda kv: -len(kv[1]))
    }
    patterns = {
        k: {
            'n': len(v),
            'avg_t1': round(sum(float(x['t1_return']) for x in v) / len(v), 6) if v else None,
            'samples': _summ(v, 10),
        }
        for k, v in sorted(by_pattern.items(), key=lambda kv: -len(kv[1]))
    }
    return {
        'n_unique': len(uniq),
        'n_severe_le_5pct': len(severe),
        'n_paper_pick_loss': len(paper),
        'decision_mix': dict(by_decision),
        'theme_clusters': themes,
        'pattern_clusters': patterns,
        'paper_pick_losses': _summ(paper, 40),
        'severe_losses': _summ(severe, 40),
    }


def render_md(report: Dict[str, Any]) -> str:
    lines = [
        f"# 亏票向量聚类报告",
        '',
        f"生成: {report.get('generated_at')}",
        f"范围: {report.get('min_date')} ~ {report.get('max_date')}",
        f"数据源: {report.get('data_source')}",
        '',
        '## 汇总',
        '',
        f"- 唯一亏票: **{report['clusters']['n_unique']}**",
        f"- 重亏 (t1≤-5%): **{report['clusters']['n_severe_le_5pct']}**",
        f"- PAPER_PICK 亏: **{report['clusters']['n_paper_pick_loss']}**",
        f"- decision mix: `{report['clusters']['decision_mix']}`",
        '',
        '## 主题簇',
        '',
        '| 主题 | n | avg t1 |',
        '|------|--:|-------:|',
    ]
    for name, info in (report['clusters'].get('theme_clusters') or {}).items():
        avg = info.get('avg_t1')
        lines.append(f"| {name} | {info['n']} | {avg*100:.2f}% |" if avg is not None else f"| {name} | {info['n']} | - |")
    lines += ['', '## 模式簇', '']
    for name, info in (report['clusters'].get('pattern_clusters') or {}).items():
        avg = info.get('avg_t1')
        avg_s = f"{avg*100:.2f}%" if avg is not None else '-'
        lines.append(f"### {name} (n={info['n']}, avg={avg_s})")
        for s in info.get('samples') or [][:8]:
            lines.append(
                f"- {s['trade_date']} {s['symbol']} {s.get('stock_name') or ''} "
                f"t1={s['t1_return']*100:.2f}% dec={s['decision']}"
            )
        lines.append('')
    lines += ['## PAPER_PICK 亏票清单', '']
    for s in report['clusters'].get('paper_pick_losses') or []:
        lines.append(
            f"- {s['trade_date']} {s['symbol']} {s.get('stock_name') or ''} "
            f"t1={s['t1_return']*100:.2f}% score={s.get('final_score')} theme={s.get('theme_bucket')}"
        )
    lines += [
        '',
        '## 与山金闸门关系',
        '',
        '- `post_limitup_weak` / hollow seed soft：山金路径已 hard 拦截',
        '- 其余簇（追高资金、partial aux、资源透支、电力非最强主线）→ **similar_loss soft 降权** + 后续有界 hard（需回测批准）',
        '',
    ]
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-date', default='2026-07-01')
    ap.add_argument('--max-date', default='2026-07-24')
    ap.add_argument('--asof', default=None, help='output date stamp (default today)')
    args = ap.parse_args()
    asof = args.asof or date.today().isoformat()

    db_rows = load_from_db(args.min_date, args.max_date)
    kn_rows = load_from_knowledge(args.min_date, args.max_date)
    if db_rows:
        rows = db_rows
        source = f'pick_case_embeddings n={len(db_rows)}'
        # merge any knowledge-only paper names missing from DB
        seen = {(str(r['trade_date']), str(r.get('symbol') or '').zfill(6)) for r in db_rows}
        for r in kn_rows:
            key = (str(r['trade_date']), str(r.get('symbol') or '').zfill(6))
            if key not in seen:
                rows.append(r)
                seen.add(key)
        source += f' + knowledge_extra n={len(rows)-len(db_rows)}'
    else:
        rows = kn_rows
        source = f'knowledge_fallback n={len(kn_rows)}'

    clusters = cluster_rows(rows)
    report = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'min_date': args.min_date,
        'max_date': args.max_date,
        'data_source': source,
        'clusters': clusters,
        'gate_note': {
            'shanjin_hard': 'post_limitup_weak_continuation + quality_escape_hard_waive_ok',
            'similar_loss_soft': 'similar_cases_ranking_boost asymmetric demotion + formal attach',
            'hard_gate': False,
        },
    }
    out_json = ROOT / 'summary' / f'{asof}_loss_case_clusters.json'
    out_md = ROOT / 'summary' / f'{asof}_loss_case_clusters.md'
    out_json.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=2), encoding='utf-8')
    out_md.write_text(render_md(report), encoding='utf-8')
    print(json.dumps({
        'status': 'OK',
        'json': str(out_json),
        'md': str(out_md),
        'n_unique': clusters['n_unique'],
        'n_paper': clusters['n_paper_pick_loss'],
        'source': source,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
