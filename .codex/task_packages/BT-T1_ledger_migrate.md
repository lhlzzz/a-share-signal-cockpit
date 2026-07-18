# BT-T1: 历史 Ledger 迁移到 PostgreSQL

## 目标
把 `forward_paper_ledger_v0_1.jsonl` 的 34 条历史记录迁移到 PostgreSQL picks/returns 表，
让 DB 成为可查询的历史出票仓库，同时保持 jsonl 作为 source of truth 不变。

## 工作目录
/workspace/hermes-workspaces/xiaogu

## 需要创建的文件

### scripts/xiaogu_ledger_migrate.py

```python
#!/usr/bin/env python3
"""One-shot migration: forward_paper_ledger_v0_1.jsonl → PostgreSQL picks + returns."""
import argparse
import datetime
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from xiaogu_db import get_db, engine
from sqlalchemy import text


def load_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            t = line.strip()
            if t:
                try:
                    rows.append(json.loads(t))
                except Exception:
                    pass
    return rows


def migrate(ledger_path: Path, dry_run: bool = False) -> dict:
    rows = load_jsonl(ledger_path)

    decisions = [r for r in rows if r.get('record_type') in ('DECISION', 'CORRECTION')]
    fills = {}
    for r in rows:
        if r.get('record_type') == 'RESULT_FILL':
            key = f"{r.get('date')}:{r.get('symbol')}"
            fills[key] = r

    print(f"Decisions: {len(decisions)}, Fills: {len(fills)}")

    inserted_picks = 0
    inserted_returns = 0
    skipped = 0

    for d in decisions:
        date_str = d.get('date')
        symbol = d.get('symbol') or ''
        decision = d.get('decision') or 'UNKNOWN'
        if not date_str:
            skipped += 1
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

        if dry_run:
            print(f"  DRY: {date_str} {symbol} {decision} score={final_score}")
            inserted_picks += 1
            continue

        try:
            with get_db() as db:
                # Check if already exists
                existing = db.execute(
                    text("SELECT id FROM picks WHERE trade_date=:d AND symbol=:s AND decision=:dec"),
                    {"d": date_str, "s": symbol, "dec": decision}
                ).fetchone()
                if existing:
                    pick_id = existing[0]
                else:
                    result = db.execute(
                        text("""
                            INSERT INTO picks (trade_date, symbol, decision, final_score,
                                blockers, features, source_layers, rule_version, scan_dir, dry_run)
                            VALUES (:trade_date, :symbol, :decision, :final_score,
                                :blockers::jsonb, '{}'::jsonb, :source_layers::jsonb,
                                :rule_version, :scan_dir, false)
                            RETURNING id
                        """),
                        {
                            "trade_date": date_str,
                            "symbol": symbol,
                            "decision": decision,
                            "final_score": final_score,
                            "blockers": json.dumps(blockers),
                            "source_layers": json.dumps(source_layers),
                            "rule_version": rule_version,
                            "scan_dir": scan_dir,
                        }
                    )
                    pick_id = result.fetchone()[0]
                    inserted_picks += 1

                # Insert returns if available
                key = f"{date_str}:{symbol}"
                fill = fills.get(key)
                if fill and pick_id:
                    t1 = fill.get('t1_return')
                    t2 = fill.get('t2_return')
                    t3 = fill.get('t3_return')
                    if any(v is not None for v in [t1, t2, t3]):
                        db.execute(
                            text("""
                                INSERT INTO returns (pick_id, trade_date, symbol, t1_return, t2_return, t3_return)
                                VALUES (:pick_id, :trade_date, :symbol, :t1, :t2, :t3)
                                ON CONFLICT (trade_date, symbol) DO UPDATE SET
                                    t1_return = COALESCE(EXCLUDED.t1_return, returns.t1_return),
                                    t2_return = COALESCE(EXCLUDED.t2_return, returns.t2_return),
                                    t3_return = COALESCE(EXCLUDED.t3_return, returns.t3_return),
                                    filled_at = NOW()
                            """),
                            {"pick_id": pick_id, "trade_date": date_str, "symbol": symbol,
                             "t1": t1, "t2": t2, "t3": t3}
                        )
                        inserted_returns += 1
        except Exception as exc:
            print(f"  ERROR {date_str} {symbol}: {exc}")
            skipped += 1

    return {
        "inserted_picks": inserted_picks,
        "inserted_returns": inserted_returns,
        "skipped": skipped,
        "total_decisions": len(decisions),
    }


def main():
    ap = argparse.ArgumentParser(description='Migrate ledger jsonl to PostgreSQL')
    ap.add_argument('--ledger', type=Path,
                    default=BASE / 'forward_paper_ledger_v0_1.jsonl')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    result = migrate(args.ledger, args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
```

## 验收标准
1. `python3 -m py_compile scripts/xiaogu_ledger_migrate.py` 无错
2. `python3 scripts/xiaogu_ledger_migrate.py --dry-run` 输出 34 条记录预览，无报错
3. 新增测试 `test_ledger_migrate_dry_run` 验证 dry-run 返回正确计数
4. `python3 -m pytest tests/ -x -q` 全部通过

## 禁止修改
- `forward_paper_ledger_v0_1.jsonl`（只读）
- 任何现有 runner/scanner/filler 文件
