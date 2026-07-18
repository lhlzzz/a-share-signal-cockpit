# Release Gate

## Before merge

- [ ] Compile gate passes
- [ ] Lint passes
- [ ] Tests pass (82+ passed, 8 skipped acceptable)
- [ ] Replay passes
- [ ] Benchmark passes
- [ ] Documentation updated
- [ ] Version bumped (if applicable)
- [ ] Rollback plan documented

## Checklist

```
Compile:  python3 -m py_compile *.py
Tests:    python3 -m pytest tests/ -x -q
API:      curl http://localhost:8000/health
DB:       python3 xiaogu_db.py status
```

## Rollback

1. Revert to previous commit
2. Run `python3 xiaogu_db.py init` if schema changed
3. Verify tests pass on previous version
