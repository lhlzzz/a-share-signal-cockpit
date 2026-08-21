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


def resolve_production_run_id(trade_date: date, production_run_id: str = '') -> str:
    """Use an explicit historical run or the active run for this decision date."""
    with get_db() as db:
        if production_run_id:
            row = db.execute(text("""
                SELECT production_run_id
                FROM production_runs
                WHERE production_run_id = :production_run_id
                  AND trade_date = :trade_date
            """), {
                'production_run_id': production_run_id,
                'trade_date': trade_date,
            }).fetchone()
        else:
            row = db.execute(text("""
                SELECT production_run_id
                FROM production_run_active
                WHERE trade_date = :trade_date
            """), {'trade_date': trade_date}).fetchone()
    if not row:
        raise ValueError('PRODUCTION_RUN_NOT_FOUND_FOR_KNOWLEDGE_EXPORT')
    return str(row[0])


def load_day_knowledge(trade_date: date, production_run_id: str = '') -> Dict[str, Any]:
    production_run_id = resolve_production_run_id(trade_date, production_run_id)
    params = {'td': trade_date, 'production_run_id': production_run_id}
    with get_db() as db:
        picks = db.execute(text("""
            SELECT id, symbol, stock_name, decision, final_score, rank,
                   features, selection_reason, ticket_reason, auxiliary_evidence_status,
                   ranking_basis, paper_pick_eligibility, created_at, updated_at
            FROM picks p
            WHERE p.trade_date = :td
              AND p.production_run_id = :production_run_id
            ORDER BY created_at
        """), params).mappings().all()
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
                   r.t1_return, r.pick_id
            FROM daily_candidates dc
            LEFT JOIN returns r
              ON r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol
            WHERE dc.trade_date = :td
              AND dc.production_run_id = :production_run_id
              AND dc.rank IS NOT NULL AND dc.rank <= 10
            ORDER BY dc.rank
        """), params).mappings().all()

        paper_returns = db.execute(text("""
            SELECT p.symbol, p.stock_name, p.final_score, r.t1_return, r.pick_id
            FROM picks p
            LEFT JOIN returns r ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
            WHERE p.trade_date = :td AND p.decision = 'PAPER_PICK'
              AND p.production_run_id = :production_run_id
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        """), params).mappings().all()

    top10_list = [_jsonable(dict(x)) for x in top10]
    with_t1 = sum(
        1 for x in top10_list
        if x.get('t1_return') is not None
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
        'production_run_id': production_run_id or None,
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
        rows = db.execute(text(f"""
            SELECT dc.trade_date
            FROM daily_candidates dc
            JOIN production_run_active pra
              ON pra.trade_date = dc.trade_date
             AND pra.production_run_id = dc.production_run_id
            WHERE dc.rank IS NOT NULL AND dc.rank <= 10
              AND (:start_date IS NULL OR dc.trade_date >= CAST(:start_date AS date))
              AND (:end_date IS NULL OR dc.trade_date <= CAST(:end_date AS date))
            UNION
            SELECT p.trade_date
            FROM picks p
            JOIN production_run_active pra
              ON pra.trade_date = p.trade_date
             AND pra.production_run_id = p.production_run_id
            WHERE p.decision = 'PAPER_PICK'
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
              AND (:start_date IS NULL OR p.trade_date >= CAST(:start_date AS date))
              AND (:end_date IS NULL OR p.trade_date <= CAST(:end_date AS date))
            ORDER BY 1
        """), {
            'start_date': start_date,
            'end_date': end_date,
        }).scalars().all()
    return [value if isinstance(value, date) else date.fromisoformat(str(value)[:10]) for value in rows]


def upsert_paper_pick_cases_from_db(
    trade_date: date,
    production_run_id: str = '',
) -> Dict[str, Any]:
    """Persist active historical PAPER_PICK rows as retrieval cases."""
    pick_run_filter = (
        'AND p.production_run_id = :production_run_id'
        if production_run_id
        else 'AND p.production_run_id IS NULL'
    )
    return_join = (
        'r.production_run_id = p.production_run_id AND r.symbol = p.symbol'
        if production_run_id
        else 'r.trade_date = p.trade_date AND r.symbol = p.symbol AND r.production_run_id IS NULL'
    )
    with get_db() as db:
        rows = db.execute(text(f"""
            SELECT p.id, p.symbol, p.stock_name, p.final_score, p.rank,
                   p.features, p.selection_reason, p.ticket_reason,
                   p.ranking_basis, p.auxiliary_evidence_status,
                   r.t1_return
            FROM picks p
            LEFT JOIN returns r
              ON {return_join}
            WHERE p.trade_date = :td
              {pick_run_filter}
              AND p.decision = 'PAPER_PICK'
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
            ORDER BY p.created_at, p.id
        """), {'td': trade_date, 'production_run_id': production_run_id}).mappings().all()

    out = {
        'status': 'OK',
        'trade_date': trade_date.isoformat(),
        'production_run_id': production_run_id,
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
            't1_return': item.get('t1_return'),
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
                'production_run_id': production_run_id,
                'pick_id': item.get('id'),
                'rank': item.get('rank'),
            },
            t1_return=features.get('t1_return'),
            production_run_id=production_run_id,
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
    run_id = str(payload.get('production_run_id') or '').strip()
    suffix = f'_{run_id}' if run_id else ''
    path = SUMMARY_DIR / f'{trade_date.isoformat()}_top10_knowledge{suffix}.json'
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
    review_cases = [
        row for row in top10
        if isinstance(row.get('t1_return'), (int, float))
        and row.get('t1_return') < 0.01
    ]
    for row in payload.get('paper_pick_returns') or []:
        if (
            isinstance(row, dict)
            and isinstance(row.get('t1_return'), (int, float))
            and row.get('t1_return') < 0.01
            and not any(
                existing.get('symbol') == row.get('symbol')
                and existing.get('t1_return') == row.get('t1_return')
                for existing in review_cases
            )
        ):
            review_cases.append({
                **row,
                'decision': 'PAPER_PICK',
                'selection_reason': '正式票 T+1 低收益复盘',
            })
    vector_upsert = payload.get('top10_vector_upsert') or {}
    vector_upserted = int(vector_upsert.get('upserted') or 0)
    vector_failed = int(vector_upsert.get('failed') or 0)
    if vector_failed == 0 and vector_upserted >= len(top10):
        vector_status = f"- TOP10 已全部写入 `decision=TOP10`（{vector_upserted}/{len(top10)}）"
    elif vector_upserted:
        vector_status = (
            f"- TOP10 向量部分写入（{vector_upserted}/{len(top10)}，"
            f"失败 {vector_failed} 条；不得视为完整）"
        )
    else:
        vector_status = (
            f"- TOP10 向量写入未完成（0/{len(top10)}，失败 {vector_failed} 条；"
            "当前仅展示已有 pgvector 记录）"
        )
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
        t1s = f'{t1:.4f}' if isinstance(t1, (int, float)) else '—'
        why = str(row.get('selection_reason') or row.get('ticket_reason') or row.get('not_selected_reason') or '')[:80].replace('|', '/')
        lines.append(
            f"| {row.get('rank')} | {row.get('symbol')} | {row.get('stock_name')} | "
            f"{row.get('final_score')} | {row.get('selection_outcome')} | {t1s} | {why} |"
        )
    lines.extend([
        '',
        '## 亏损 / 低收益复盘（主力行为链升级输入）',
        '',
        '- 规则：`t1_return < 0.01`；亏损票全部纳入，盈利但低于 1% 的票作为低收益案例。',
        '- 用途：只用于复盘、证据归因和下一轮主力行为链升级，不直接生成 PAPER_PICK。',
        '',
        '| date | symbol | score | t1 | review | why |',
        '|---|---|---:|---:|---|---|',
    ])
    for row in review_cases:
        t1 = row.get('t1_return')
        t1s = f'{t1:.4f}' if isinstance(t1, (int, float)) else '—'
        review = '亏损' if isinstance(t1, (int, float)) and t1 < 0 else '低收益'
        why = str(
            row.get('selection_reason')
            or row.get('ticket_reason')
            or row.get('not_selected_reason')
            or ''
        )[:120].replace('|', '/')
        lines.append(
            f"| {row.get('trade_date') or trade_date.isoformat()} | {row.get('symbol')} | "
            f"{row.get('final_score')} | {t1s} | {review} | {why} |"
        )
    if not review_cases:
        lines.append('| — | — | — | — | 无 | 当前没有已回填的亏损/低收益案例 |')
    lines.extend([
        '',
        '## 向量层',
        '',
        f"- storage: **pgvector** table `pick_case_embeddings`",
        f"- embed_method: `{payload.get('vector_layer', {}).get('embed_method')}`",
        vector_status,
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
    structured_paths = _write_structured_knowledge_notes(
        payload,
        trade_date,
        formal,
        top10,
        review_cases,
    )
    result = {'status': 'OK', 'paths': [str(note_path), *structured_paths]}

    # Update 状态.md head section with a short stamped block (append if marker missing).
    status_path = OBSIDIAN_ASHARE / '状态.md'
    if status_path.exists():
        stamp = f'## {trade_date.isoformat()} · 正式票锁定与知识资产'
        body = status_path.read_text(encoding='utf-8')
        block = (
            f'{stamp}\n\n'
            f"- 正式票: **{formal.get('symbol')} {formal.get('stock_name')}** "
            f"score={formal.get('final_score')} pick_id={formal.get('pick_id')}\n"
            f"- Top10 知识: `summary/{trade_date.isoformat()}_top10_knowledge.json`\n"
            f"- 向量: pgvector + `{embed_method_name()}`；"
            f"TOP10 upsert={vector_upserted}/{len(top10)}，failed={vector_failed}\n"
            f"- Obsidian inbox: `{note_path.name}`\n"
        )
        if stamp in body:
            start = body.index(stamp)
            end = body.find('\n## ', start + len(stamp))
            if end < 0:
                end = len(body)
            body = body[:start] + block.rstrip() + body[end:]
        else:
            # Insert after first H1 block if present, else prepend.
            if body.startswith('# '):
                parts = body.split('\n', 1)
                body = parts[0] + '\n\n' + block + '\n' + (parts[1] if len(parts) > 1 else '')
            else:
                body = block + '\n' + body
        status_path.write_text(body, encoding='utf-8')
        result['paths'].append(str(status_path))

    # Cross-domain pointer only if 神临 exists.
    if OBSIDIAN_SHENLIN.exists():
        pool = OBSIDIAN_SHENLIN / '想法池'
        if pool.exists() or True:
            pool.mkdir(parents=True, exist_ok=True)
            pointer = pool / f'{trade_date.isoformat()}-xiaogu知识资产指针.md'
            run_id = str(payload.get('production_run_id') or '').strip()
            summary_suffix = f'_{run_id}' if run_id else ''
            pointer.write_text(
                f'# xiaogu 知识资产指针 {trade_date.isoformat()}\n\n'
                f'- 正式票: {formal.get("symbol")} {formal.get("stock_name")}\n'
                f'- A股 inbox: `Project/A股/inbox/{note_path.name}`\n'
                f'- production_run_id: `{run_id or "legacy"}`\n'
                f'- summary: `summary/{trade_date.isoformat()}_top10_knowledge{summary_suffix}.json`\n'
                f'- vector: pgvector `{embed_method_name()}`\n',
                encoding='utf-8',
            )
            result['paths'].append(str(pointer))
    return result


def _safe_note_symbol(value: Any) -> str:
    symbol = ''.join(
        char for char in str(value or '')
        if char.isalnum() or char in ('-', '_')
    )
    return symbol or 'unknown'


def _return_label(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return '待回填'
    if value < 0:
        return '亏损'
    if value < 0.01:
        return '低收益'
    return '正收益'


def _write_structured_knowledge_notes(
    payload: Dict[str, Any],
    trade_date: date,
    formal: Dict[str, Any],
    top10: List[Dict[str, Any]],
    review_cases: List[Dict[str, Any]],
) -> List[str]:
    """Write searchable decision, tracking, and lesson notes beside the inbox export."""
    date_text = trade_date.isoformat()
    decision_dir = OBSIDIAN_ASHARE / '决策日志'
    tracking_dir = OBSIDIAN_ASHARE / '跟踪记录'
    lessons_dir = OBSIDIAN_ASHARE / '失败案例'
    decision_dir.mkdir(parents=True, exist_ok=True)
    tracking_dir.mkdir(parents=True, exist_ok=True)

    official_symbol = formal.get('symbol') or 'NO_PICK'
    official_name = formal.get('stock_name') or ''
    official_return = next(
        (
            row.get('t1_return')
            for row in payload.get('paper_pick_returns') or []
            if row.get('symbol') == formal.get('symbol')
        ),
        None,
    )
    coverage = payload.get('top10_return_coverage') or {}
    decision_lines = [
        '---',
        'type: decision-log',
        f'date: {date_text}',
        'project: xiaogu',
        f'decision: {"PAPER_PICK" if formal.get("symbol") else "NO_PICK"}',
        '---',
        f'# {date_text} 投资决策与结果',
        '',
        '## 正式决策',
        f'- 决策：{"PAPER_PICK" if formal.get("symbol") else "NO_PICK"}',
        f'- 标的：{official_symbol} {official_name}',
        f'- score：{formal.get("final_score")}',
        f'- pick_id：{formal.get("pick_id")}',
        f'- T+1 结果：{official_return if official_return is not None else "待回填"}',
        f'- 结果分类：{_return_label(official_return)}',
        '',
        '## 决策理由',
        '```json',
        json.dumps({
            'ticket_reason': formal.get('ticket_reason'),
            'selection_reason': formal.get('selection_reason'),
        }, ensure_ascii=False, indent=2, default=str)[:4000],
        '```',
        '',
        '## 复盘入口',
        f'- 前十收益覆盖：{coverage}',
        f'- 低收益/亏损案例：{len(review_cases)}',
        '- 需要结合原始投资逻辑判断：价格回撤是噪声，还是逻辑被破坏。',
    ]
    decision_path = decision_dir / f'{date_text}-PAPER_PICK决策.md'
    decision_path.write_text('\n'.join(decision_lines) + '\n', encoding='utf-8')

    tracking_lines = [
        '---',
        'type: daily-tracking',
        f'date: {date_text}',
        'project: xiaogu',
        '---',
        f'# {date_text} A股每日变化与出票结果',
        '',
        '## 结果统计',
        f'- 正式票：{official_symbol} {official_name}',
        f'- 前十 T+1 覆盖：{coverage}',
        f'- 低收益/亏损案例：{len(review_cases)}',
        '',
        '## 前十结果',
        '| rank | symbol | name | score | t1 | 分类 |',
        '|---:|---|---|---:|---:|---|',
    ]
    for row in top10:
        value = row.get('t1_return')
        tracking_lines.append(
            f"| {row.get('rank')} | {row.get('symbol')} | {row.get('stock_name')} | "
            f"{row.get('final_score')} | {value if value is not None else '—'} | {_return_label(value)} |"
        )
    tracking_lines.extend([
        '',
        '## 逻辑变化待判断',
        '- 每个亏损/低收益案例必须回看对应投资逻辑，判断是执行偏差、时点问题、数据问题还是逻辑失效。',
        '- 未完成归因前，不把结果直接写成策略结论。',
    ])
    tracking_path = tracking_dir / f'{date_text}-每日变化与出票结果.md'
    tracking_path.write_text('\n'.join(tracking_lines) + '\n', encoding='utf-8')

    paths = [str(decision_path), str(tracking_path)]
    if not review_cases:
        return paths

    lessons_dir.mkdir(parents=True, exist_ok=True)
    seen_symbols = set()
    for row in review_cases:
        symbol = _safe_note_symbol(row.get('symbol'))
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        value = row.get('t1_return')
        why = (
            row.get('selection_reason')
            or row.get('ticket_reason')
            or row.get('not_selected_reason')
            or '待补充'
        )
        lesson_lines = [
            '---',
            'type: lesson',
            f'date: {date_text}',
            'project: xiaogu',
            f'symbol: {symbol}',
            f'outcome: {_return_label(value)}',
            '---',
            f'# {date_text} - {symbol} 出票复盘',
            '',
            '## 观察结果',
            f'- T+1 return：{value if value is not None else "待回填"}',
            f'- 分类：{_return_label(value)}',
            f'- score：{row.get("final_score")}',
            f'- 选择理由线索：{str(why)[:500]}',
            '',
            '## 根因结论',
            '- 待验证；当前记录是结果与线索，不把亏损自动等同于策略错误。',
            '',
            '## 需要验证',
            '- 原投资逻辑是否被新事实破坏？',
            '- 是数据质量、门禁、时点、行业判断还是个股执行问题？',
            '- 是否需要新增回放测试或监控？',
            '',
            '## 关联',
            f'- 决策日志：[[决策日志/{date_text}-PAPER_PICK决策]]',
            f'- 跟踪记录：[[跟踪记录/{date_text}-每日变化与出票结果]]',
        ]
        lesson_path = lessons_dir / f'{date_text}-{symbol}-出票复盘.md'
        lesson_path.write_text('\n'.join(lesson_lines) + '\n', encoding='utf-8')
        paths.append(str(lesson_path))
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description='Export top10/formal pick knowledge assets')
    ap.add_argument('--date', default=date.today().isoformat())
    ap.add_argument('--production-run-id', default='')
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
        production_run_id = resolve_production_run_id(td, args.production_run_id)
        top10_vec = upsert_top10_cases_from_db(td, production_run_id)
        print('top10_vector', json.dumps({
            k: top10_vec.get(k) for k in ('status', 'trade_date', 'upserted', 'failed')
        }, ensure_ascii=False))

    production_run_id = resolve_production_run_id(td, args.production_run_id)
    payload = load_day_knowledge(td, production_run_id)
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
