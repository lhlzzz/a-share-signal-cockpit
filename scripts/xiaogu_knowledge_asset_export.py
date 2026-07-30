#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export daily knowledge assets: formal pick + top10 why/returns → summary + Obsidian.

Second-brain writer (not passive backfill-only):
- reads picks / daily_candidates / returns
- upserts top10 into pick_case_embeddings (TOP10 decision)
- writes summary JSON
- autonomously fills Obsidian Project/A股 when mounted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from xiaogu_case_vector_store import (
    backend_status,
    embed_method_name,
    rebuild_all_case_embeddings,
    upsert_pick_case,
    upsert_top10_cases_from_db,
)
from xiaogu_db import get_db

SUMMARY_DIR = ROOT / 'summary'
OBSIDIAN_ASHARE = Path(
    os.environ.get('XIAOGU_OBSIDIAN_ASHARE', '/mnt/d/obisidian/Obsidian/Project/A股')
)
OBSIDIAN_SHENLIN = Path(
    os.environ.get('XIAOGU_OBSIDIAN_SHENLIN', '/mnt/d/obisidian/Obsidian/神临')
)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


def load_day_knowledge(trade_date: date) -> Dict[str, Any]:
    with get_db() as db:
        picks = db.execute(text("""
            SELECT id, symbol, stock_name, decision, final_score, rank,
                   features, selection_reason, ticket_reason, auxiliary_evidence_status,
                   ranking_basis, paper_pick_eligibility, created_at, updated_at
            FROM picks
            WHERE trade_date = :td
            ORDER BY created_at
        """), {'td': trade_date}).mappings().all()
        active = []
        superseded = []
        for p in picks:
            d = dict(p)
            feat = d.get('features') or {}
            if isinstance(feat, str):
                try:
                    feat = json.loads(feat)
                except Exception:
                    feat = {}
            d['features'] = feat
            super_flag = str(feat.get('superseded') or '').lower() in {'1', 'true', 'yes'}
            if super_flag:
                superseded.append(d)
            else:
                active.append(d)

        top10 = db.execute(text("""
            SELECT dc.rank, dc.symbol, dc.stock_name, dc.final_score, dc.decision,
                   dc.selection_outcome, dc.is_official_pick, dc.selection_reason,
                   dc.ticket_reason, dc.not_selected_reason,
                   dc.auxiliary_evidence_snapshot, dc.ranking_basis,
                   r.t1_return, r.t1_return_close, r.t1_return_high,
                   r.next_day_open_return, r.next_day_high_return, r.pick_id
            FROM daily_candidates dc
            LEFT JOIN returns r
              ON r.trade_date = dc.trade_date AND r.symbol = dc.symbol
            WHERE dc.trade_date = :td AND dc.rank IS NOT NULL AND dc.rank <= 10
            ORDER BY dc.rank
        """), {'td': trade_date}).mappings().all()

        paper_returns = db.execute(text("""
            SELECT p.symbol, p.stock_name, p.final_score, r.t1_return, r.t1_return_close,
                   r.t1_return_high, r.pick_id
            FROM picks p
            LEFT JOIN returns r ON r.trade_date = p.trade_date AND r.symbol = p.symbol
            WHERE p.trade_date = :td AND p.decision = 'PAPER_PICK'
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        """), {'td': trade_date}).mappings().all()

    top10_list = [_jsonable(dict(x)) for x in top10]
    with_t1 = sum(
        1 for x in top10_list
        if x.get('t1_return') is not None or x.get('t1_return_close') is not None
    )
    formal = None
    for p in active:
        if p.get('decision') == 'PAPER_PICK':
            formal = {
                'pick_id': p.get('id'),
                'symbol': p.get('symbol'),
                'stock_name': p.get('stock_name'),
                'final_score': p.get('final_score'),
                'rank': p.get('rank'),
                'selection_reason': _jsonable(p.get('selection_reason')),
                'ticket_reason': _jsonable(p.get('ticket_reason')),
                'auxiliary_evidence_status': p.get('auxiliary_evidence_status'),
                'features_flags': {
                    'user_locked_official': (p.get('features') or {}).get('user_locked_official'),
                    'active_correction': (p.get('features') or {}).get('active_correction'),
                    'superseded': (p.get('features') or {}).get('superseded'),
                },
            }
            break

    return {
        'trade_date': trade_date.isoformat(),
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'formal_paper_pick': formal,
        'active_picks': [
            {
                'pick_id': p.get('id'),
                'symbol': p.get('symbol'),
                'stock_name': p.get('stock_name'),
                'decision': p.get('decision'),
                'final_score': p.get('final_score'),
            }
            for p in active
        ],
        'superseded_picks': [
            {
                'pick_id': p.get('id'),
                'symbol': p.get('symbol'),
                'stock_name': p.get('stock_name'),
                'decision': p.get('decision'),
                'superseded_reason': (p.get('features') or {}).get('superseded_reason'),
            }
            for p in superseded
        ],
        'top10': top10_list,
        'top10_return_coverage': {
            'n': len(top10_list),
            'with_t1': with_t1,
            'ratio': round(with_t1 / len(top10_list), 4) if top10_list else None,
        },
        'paper_pick_returns': _jsonable([dict(x) for x in paper_returns]),
        'why_layer': {
            'db_fields': [
                'selection_reason', 'ticket_reason', 'not_selected_reason',
                'ranking_basis', 'auxiliary_evidence_snapshot',
            ],
            'note': 'Top10 why + returns live in daily_candidates + returns; vectors store TOP10 cohort for retrieval.',
        },
        'vector_layer': {
            **backend_status(),
            'embed_method': embed_method_name(),
            'table': 'pick_case_embeddings',
            'storage': 'pgvector',
        },
    }


def historical_knowledge_dates(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[date]:
    """Return dates with persisted top10 candidates or formal paper picks."""
    with get_db() as db:
        rows = db.execute(text("""
            SELECT trade_date
            FROM daily_candidates
            WHERE rank IS NOT NULL AND rank <= 10
              AND (:start_date IS NULL OR trade_date >= CAST(:start_date AS date))
              AND (:end_date IS NULL OR trade_date <= CAST(:end_date AS date))
            UNION
            SELECT trade_date
            FROM picks
            WHERE decision = 'PAPER_PICK'
              AND COALESCE(features ->> 'superseded', 'false') <> 'true'
              AND (:start_date IS NULL OR trade_date >= CAST(:start_date AS date))
              AND (:end_date IS NULL OR trade_date <= CAST(:end_date AS date))
            ORDER BY trade_date
        """), {
            'start_date': start_date,
            'end_date': end_date,
        }).scalars().all()
    return [value if isinstance(value, date) else date.fromisoformat(str(value)[:10]) for value in rows]


def upsert_paper_pick_cases_from_db(trade_date: date) -> Dict[str, Any]:
    """Persist active historical PAPER_PICK rows as retrieval cases."""
    with get_db() as db:
        rows = db.execute(text("""
            SELECT p.id, p.symbol, p.stock_name, p.final_score, p.rank,
                   p.features, p.selection_reason, p.ticket_reason,
                   p.ranking_basis, p.auxiliary_evidence_status,
                   r.t1_return, r.t1_return_close, r.t1_return_high
            FROM picks p
            LEFT JOIN returns r
              ON r.trade_date = p.trade_date AND r.symbol = p.symbol
            WHERE p.trade_date = :td
              AND p.decision = 'PAPER_PICK'
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
            ORDER BY p.created_at, p.id
        """), {'td': trade_date}).mappings().all()

    out = {
        'status': 'OK',
        'trade_date': trade_date.isoformat(),
        'upserted': 0,
        'failed': 0,
    }
    for row in rows:
        item = dict(row)
        features = item.get('features') or {}
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except Exception:
                features = {}
        features = dict(features) if isinstance(features, dict) else {}
        features.update({
            'rank': item.get('rank'),
            'ranking_basis': item.get('ranking_basis'),
            'auxiliary_evidence_status': item.get('auxiliary_evidence_status'),
            't1_return': item.get('t1_return') if item.get('t1_return') is not None
            else item.get('t1_return_close'),
        })
        reason = json.dumps({
            'selection_reason': _jsonable(item.get('selection_reason')),
            'ticket_reason': _jsonable(item.get('ticket_reason')),
        }, ensure_ascii=False, default=str)
        result = upsert_pick_case(
            trade_date=trade_date,
            symbol=str(item.get('symbol') or ''),
            decision='PAPER_PICK',
            stock_name=str(item.get('stock_name') or ''),
            final_score=item.get('final_score'),
            features=features,
            reason=reason,
            metadata={
                'cohort': 'paper_pick',
                'pick_id': item.get('id'),
                'rank': item.get('rank'),
                't1_return_high': item.get('t1_return_high'),
            },
            t1_return=features.get('t1_return'),
        )
        if result.get('status') == 'OK':
            out['upserted'] += 1
        else:
            out['failed'] += 1
    return out


def export_historical_knowledge(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    *,
    rebuild_vectors: bool = False,
) -> Dict[str, Any]:
    """Idempotently load persisted historical cases into the vector knowledge layer."""
    dates = historical_knowledge_dates(start_date, end_date)
    report: Dict[str, Any] = {
        'status': 'OK',
        'window': {
            'start': start_date.isoformat() if start_date else None,
            'end': end_date.isoformat() if end_date else None,
        },
        'dates': len(dates),
        'top10_upserted': 0,
        'paper_pick_upserted': 0,
        'failed': 0,
        'rebuild_vectors': None,
        'date_results': [],
    }
    for trade_date in dates:
        top10 = upsert_top10_cases_from_db(trade_date)
        paper = upsert_paper_pick_cases_from_db(trade_date)
        report['top10_upserted'] += int(top10.get('upserted') or 0)
        report['paper_pick_upserted'] += int(paper.get('upserted') or 0)
        report['failed'] += int(top10.get('failed') or 0) + int(paper.get('failed') or 0)
        report['date_results'].append({
            'trade_date': trade_date.isoformat(),
            'top10': {k: top10.get(k) for k in ('upserted', 'failed')},
            'paper_pick': {k: paper.get(k) for k in ('upserted', 'failed')},
        })
    if rebuild_vectors:
        report['rebuild_vectors'] = rebuild_all_case_embeddings()
    return report


def write_historical_summary(report: Dict[str, Any]) -> Path:
    """Write one compact audit record for the historical import."""
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    start = report.get('window', {}).get('start') or 'min'
    end = report.get('window', {}).get('end') or 'max'
    path = SUMMARY_DIR / f'historical_knowledge_export_{start}_{end}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return path


def write_historical_obsidian(report: Dict[str, Any]) -> Dict[str, Any]:
    """Record the historical import in the A-share Obsidian inbox when mounted."""
    result = {'status': 'SKIPPED', 'reason': 'obsidian_not_mounted', 'paths': []}
    if not OBSIDIAN_ASHARE.exists():
        return result
    inbox = OBSIDIAN_ASHARE / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    window = report.get('window') or {}
    start = window.get('start') or 'min'
    end = window.get('end') or 'max'
    note_path = inbox / f'{start}_{end}-历史知识资产入库.md'
    note_path.write_text(
        '\n'.join([
            f'# xiaogu 历史知识资产入库 · {start} 至 {end}',
            '',
            '- 来源：PostgreSQL `daily_candidates` / `picks` / `returns`',
            '- 入库：pgvector `pick_case_embeddings`',
            '- 范围：历史 Top10 + active PAPER_PICK；不改写历史决策',
            f"- 交易日数: {report.get('dates', 0)}",
            f"- Top10 upsert: {report.get('top10_upserted', 0)}",
            f"- PAPER_PICK upsert: {report.get('paper_pick_upserted', 0)}",
            f"- failures: {report.get('failed', 0)}",
            f"- audit: `summary/historical_knowledge_export_{start}_{end}.json`",
            '',
        ]),
        encoding='utf-8',
    )
    result = {'status': 'OK', 'paths': [str(note_path)]}
    status_path = OBSIDIAN_ASHARE / '状态.md'
    stamp = f'## {end} · 历史知识资产入库'
    if status_path.exists():
        body = status_path.read_text(encoding='utf-8')
        if stamp not in body:
            block = '\n'.join([
                stamp,
                '',
                f'- 历史范围: **{start} 至 {end}**，{report.get("dates", 0)} 个交易日',
                f'- 入库: `pick_case_embeddings` Top10={report.get("top10_upserted", 0)}，PAPER_PICK={report.get("paper_pick_upserted", 0)}，failures={report.get("failed", 0)}',
                f'- 证据审计: `summary/historical_knowledge_export_{start}_{end}.json`',
                '- 口径: 仅沉淀 DB 已记录的历史证据，不改写历史决策；后续出票只将相似案例作为 soft context。',
                '',
            ])
            if body.startswith('# '):
                head, rest = body.split('\n', 1)
                body = head + '\n\n' + block + rest
            else:
                body = block + body
            status_path.write_text(body, encoding='utf-8')
            result['paths'].append(str(status_path))
    return result


def write_summary(payload: Dict[str, Any], trade_date: date) -> Path:
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = SUMMARY_DIR / f'{trade_date.isoformat()}_top10_knowledge.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    latest = SUMMARY_DIR / 'top10_knowledge_latest.json'
    latest.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    return path


def write_obsidian(payload: Dict[str, Any], trade_date: date) -> Dict[str, Any]:
    result = {'status': 'SKIPPED', 'reason': 'obsidian_not_mounted', 'paths': []}
    if not OBSIDIAN_ASHARE.exists():
        return result
    inbox = OBSIDIAN_ASHARE / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    formal = payload.get('formal_paper_pick') or {}
    top10 = payload.get('top10') or []
    lines = [
        f'# {trade_date.isoformat()} 知识资产 · 正式票 + 前十 why/returns',
        '',
        '## 正式 PAPER_PICK',
        '',
        f"- symbol: **{formal.get('symbol') or '—'} {formal.get('stock_name') or ''}**",
        f"- score: {formal.get('final_score')}",
        f"- pick_id: {formal.get('pick_id')}",
        f"- aux: {formal.get('auxiliary_evidence_status')}",
        f"- user_locked: {(formal.get('features_flags') or {}).get('user_locked_official')}",
        '',
        '### why (ticket / selection)',
        '',
        '```json',
        json.dumps({
            'ticket_reason': formal.get('ticket_reason'),
            'selection_reason': formal.get('selection_reason'),
        }, ensure_ascii=False, indent=2, default=str)[:4000],
        '```',
        '',
        '## Top10 候选 · 原因 · 收益',
        '',
        f"覆盖: t1 {payload.get('top10_return_coverage')}",
        '',
        '| rank | symbol | name | score | outcome | t1 | why |',
        '|---:|---|---|---:|---|---:|---|',
    ]
    for row in top10:
        t1 = row.get('t1_return')
        if t1 is None:
            t1 = row.get('t1_return_close')
        t1s = f'{t1:.4f}' if isinstance(t1, (int, float)) else '—'
        why = str(row.get('selection_reason') or row.get('ticket_reason') or row.get('not_selected_reason') or '')[:80].replace('|', '/')
        lines.append(
            f"| {row.get('rank')} | {row.get('symbol')} | {row.get('stock_name')} | "
            f"{row.get('final_score')} | {row.get('selection_outcome')} | {t1s} | {why} |"
        )
    lines.extend([
        '',
        '## 向量层',
        '',
        f"- storage: **pgvector** table `pick_case_embeddings`",
        f"- embed_method: `{payload.get('vector_layer', {}).get('embed_method')}`",
        f"- top10 已 upsert 为 decision=TOP10（可相似检索）",
        '',
        '## 用途（第二大脑）',
        '',
        '- 优化链路：对比 top10 vs official 的 T+1，解释 miss / overfit',
        '- 复盘：why 字段可回放 formal 门禁与 soft bias',
        '- 检索：pgvector 相似历史案例 soft boost',
        '',
        f"generated_at: {payload.get('generated_at')}",
        '',
    ])
    note_path = inbox / f'{trade_date.isoformat()}-正式票与前十知识资产.md'
    note_path.write_text('\n'.join(lines), encoding='utf-8')
    result = {'status': 'OK', 'paths': [str(note_path)]}

    # Update 状态.md head section with a short stamped block (append if marker missing).
    status_path = OBSIDIAN_ASHARE / '状态.md'
    if status_path.exists():
        stamp = f'## {trade_date.isoformat()} · 正式票锁定与知识资产'
        body = status_path.read_text(encoding='utf-8')
        block = (
            f'\n{stamp}\n\n'
            f"- 正式票: **{formal.get('symbol')} {formal.get('stock_name')}** "
            f"score={formal.get('final_score')} pick_id={formal.get('pick_id')}\n"
            f"- Top10 知识: `summary/{trade_date.isoformat()}_top10_knowledge.json`\n"
            f"- 向量: pgvector + `{embed_method_name()}`；TOP10 cohort 已写入\n"
            f"- Obsidian inbox: `{note_path.name}`\n"
        )
        if stamp not in body:
            # Insert after first H1 block if present, else prepend.
            if body.startswith('# '):
                parts = body.split('\n', 1)
                body = parts[0] + '\n' + block + (parts[1] if len(parts) > 1 else '')
            else:
                body = block + body
            status_path.write_text(body, encoding='utf-8')
            result['paths'].append(str(status_path))

    # Cross-domain pointer only if 神临 exists.
    if OBSIDIAN_SHENLIN.exists():
        pool = OBSIDIAN_SHENLIN / '想法池'
        if pool.exists() or True:
            pool.mkdir(parents=True, exist_ok=True)
            pointer = pool / f'{trade_date.isoformat()}-xiaogu知识资产指针.md'
            pointer.write_text(
                f'# xiaogu 知识资产指针 {trade_date.isoformat()}\n\n'
                f'- 正式票: {formal.get("symbol")} {formal.get("stock_name")}\n'
                f'- A股 inbox: `Project/A股/inbox/{note_path.name}`\n'
                f'- summary: `summary/{trade_date.isoformat()}_top10_knowledge.json`\n'
                f'- vector: pgvector `{embed_method_name()}`\n',
                encoding='utf-8',
            )
            result['paths'].append(str(pointer))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description='Export top10/formal pick knowledge assets')
    ap.add_argument('--date', default=date.today().isoformat())
    ap.add_argument('--historical', action='store_true', help='load all historical top10/PAPER_PICK cases')
    ap.add_argument('--start-date', default='', help='historical window start, YYYY-MM-DD')
    ap.add_argument('--end-date', default='', help='historical window end, YYYY-MM-DD')
    ap.add_argument('--rebuild-vectors', action='store_true', help='rebuild all case embeddings')
    ap.add_argument('--skip-top10-vector', action='store_true')
    ap.add_argument('--skip-obsidian', action='store_true')
    args = ap.parse_args()

    if args.historical:
        start = date.fromisoformat(args.start_date[:10]) if args.start_date else None
        end = date.fromisoformat(args.end_date[:10]) if args.end_date else None
        report = export_historical_knowledge(start, end, rebuild_vectors=args.rebuild_vectors)
        path = write_historical_summary(report)
        print('historical_summary', path)
        if not args.skip_obsidian:
            print('obsidian', json.dumps(write_historical_obsidian(report), ensure_ascii=False))
        print(json.dumps({
            'status': report.get('status'),
            'window': report.get('window'),
            'dates': report.get('dates'),
            'top10_upserted': report.get('top10_upserted'),
            'paper_pick_upserted': report.get('paper_pick_upserted'),
            'failed': report.get('failed'),
            'summary': str(path),
        }, ensure_ascii=False))
        return 0

    td = date.fromisoformat(args.date[:10])

    rebuild_stats = None
    if args.rebuild_vectors:
        rebuild_stats = rebuild_all_case_embeddings()
        print('rebuild_vectors', json.dumps(rebuild_stats, ensure_ascii=False))

    top10_vec = None
    if not args.skip_top10_vector:
        top10_vec = upsert_top10_cases_from_db(td)
        print('top10_vector', json.dumps({
            k: top10_vec.get(k) for k in ('status', 'trade_date', 'upserted', 'failed')
        }, ensure_ascii=False))

    payload = load_day_knowledge(td)
    payload['top10_vector_upsert'] = {
        k: top10_vec.get(k) for k in ('status', 'upserted', 'failed')
    } if top10_vec else None
    payload['rebuild_vectors'] = rebuild_stats
    path = write_summary(payload, td)
    print('summary', path)

    obs = {'status': 'SKIPPED'}
    if not args.skip_obsidian:
        obs = write_obsidian(payload, td)
        print('obsidian', json.dumps(obs, ensure_ascii=False))

    formal = payload.get('formal_paper_pick') or {}
    print(json.dumps({
        'trade_date': td.isoformat(),
        'formal': f"{formal.get('symbol')} {formal.get('stock_name')}",
        'top10_n': len(payload.get('top10') or []),
        'top10_t1_coverage': payload.get('top10_return_coverage'),
        'summary': str(path),
        'obsidian': obs.get('status'),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
