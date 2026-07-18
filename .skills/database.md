# Database Skill

## Trigger

When schema changes, data migration, or DB operations are needed.

## Workflow

1. Check current schema (`scripts/xiaogu_db_init.sql`)
2. Write migration SQL
3. Test on dev database
4. Apply to production
5. Verify data integrity

## Key Files

- `xiaogu_db.py` — connection and helpers
- `scripts/xiaogu_db_init.sql` — schema definition
- `scripts/xiaogu_ledger_migrate.py` — JSONL → DB migration

## Rules

- Always use `CAST(:param AS type)` not `:param::type`
- All tables must have created_at, updated_at, data_version
- Never modify production DB directly (all through code)
- Test migration with `--dry-run` first

## Verification

```bash
python3 xiaogu_db.py status
python3 -c "from xiaogu_db import engine; ..."
```
