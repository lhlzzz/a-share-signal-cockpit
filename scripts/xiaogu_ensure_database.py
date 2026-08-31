import os
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from xiaogu_db import init_db, seed_authoritative_a_share_calendar

if __name__ == "__main__":
    init_db()
    print(seed_authoritative_a_share_calendar())
