# Database Gate

## Before merge

- [ ] Migration script exists (if schema changed)
- [ ] Rollback script exists
- [ ] Indexes verified (no missing indexes on query paths)
- [ ] Constraints verified (UNIQUE, FK, CHECK)
- [ ] No destructive SQL (DROP TABLE, TRUNCATE in migration)
- [ ] Backup strategy confirmed

## Check

```bash
python3 xiaogu_db.py status
python3 -c "from xiaogu_db import engine; engine.connect().execute(text('SELECT 1'))"
```

## Schema

8 tables: picks, returns, scan_sessions, signal_effectiveness, signals, research_runs, daily_candidates, scoring_config

All tables must have: created_at, updated_at, data_version
