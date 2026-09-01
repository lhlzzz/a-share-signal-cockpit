#!/usr/bin/env python3
"""RESEARCH / DATA MIGRATION ONLY.

Recover historical snapshot_id values from existing facts.
Never invoked by production init or ensure_production_schema().
"""
from __future__ import annotations

import json
import os
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from xiaogu_db import migrate_historical_snapshot_identity


def main() -> int:
    try:
        result = migrate_historical_snapshot_identity()
    except Exception as exc:
        print(f"MIGRATION_FAILED:{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
