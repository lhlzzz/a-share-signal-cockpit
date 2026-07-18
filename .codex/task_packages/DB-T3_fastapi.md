# DB-T3: FastAPI 接口骨架

## 目标
创建 `xiaogu_api.py`，提供查询出票记录、收益、信号分析结果的 HTTP 接口。

## 工作目录
/workspace/hermes-workspaces/xiaogu

## 文件：xiaogu_api.py

```python
#!/usr/bin/env python3
"""FastAPI service for xiaogu A-share system."""
import os
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = FastAPI(
    title="xiaogu API",
    description="A-share paper trading intelligence system",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def query_rows(sql: str, params: dict = {}) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/picks")
def get_picks(
    date: Optional[str] = Query(None, description="Trade date YYYY-MM-DD"),
    decision: Optional[str] = Query(None, description="PAPER_PICK or NO_PICK"),
    limit: int = Query(50, le=500),
):
    """List picks with optional filters."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if decision:
        where.append("decision = :decision")
        params["decision"] = decision
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT * FROM picks {clause} ORDER BY created_at DESC LIMIT :limit",
        params,
    )
    return {"picks": rows, "count": len(rows)}


@app.get("/picks/{trade_date}/summary")
def get_daily_summary(trade_date: str):
    """Daily pick summary: PAPER_PICK + highest_score + closest_to_pick."""
    rows = query_rows(
        "SELECT * FROM picks WHERE trade_date = :date ORDER BY final_score DESC NULLS LAST",
        {"date": trade_date},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No picks for {trade_date}")
    paper_picks = [r for r in rows if r["decision"] == "PAPER_PICK"]
    return {
        "trade_date": trade_date,
        "paper_pick": paper_picks[0] if paper_picks else None,
        "highest_score": rows[0],
        "total_candidates": len(rows),
    }


@app.get("/returns")
def get_returns(
    date: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit_up_only: bool = Query(False),
    limit: int = Query(50, le=500),
):
    """List return records."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol
    if limit_up_only:
        where.append("is_limit_up = TRUE")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT * FROM returns {clause} ORDER BY trade_date DESC LIMIT :limit",
        params,
    )
    return {"returns": rows, "count": len(rows)}


@app.get("/signals/effectiveness")
def get_signal_effectiveness(
    date: Optional[str] = Query(None, description="Analysis date YYYY-MM-DD"),
    limit: int = Query(20, le=100),
):
    """Signal effectiveness analysis results."""
    where = "WHERE analysis_date = :date" if date else ""
    params: Dict[str, Any] = {"limit": limit}
    if date:
        params["date"] = date
    rows = query_rows(
        f"SELECT * FROM signal_effectiveness {where} ORDER BY limit_up_rate DESC LIMIT :limit",
        params,
    )
    return {"signals": rows, "count": len(rows)}


@app.get("/stats/overview")
def get_overview():
    """High-level system stats."""
    rows = query_rows("""
        SELECT
            COUNT(*) FILTER (WHERE decision = 'PAPER_PICK') AS total_paper_picks,
            COUNT(*) FILTER (WHERE decision = 'NO_PICK') AS total_no_picks,
            ROUND(AVG(r.t1_return)::numeric, 4) AS avg_t1_return,
            COUNT(*) FILTER (WHERE r.is_limit_up = TRUE) AS total_limit_ups,
            MAX(p.trade_date) AS latest_trade_date
        FROM picks p
        LEFT JOIN returns r ON p.id = r.pick_id
    """)
    return rows[0] if rows else {}
```

## 验收标准
1. `python3 -m py_compile xiaogu_api.py` 无错
2. `python3 -c "import xiaogu_api; print('OK')"` 无错（需 fastapi 已安装）
3. 路由存在：/health, /picks, /picks/{date}/summary, /returns, /signals/effectiveness, /stats/overview
4. `python3 -m pytest tests/ -x -q` 仍然全部通过

## 禁止修改
- 任何现有文件
- `forward_paper_ledger_v0_1.jsonl`
