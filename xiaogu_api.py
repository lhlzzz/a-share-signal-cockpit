#!/usr/bin/env python3
"""FastAPI service for xiaogu A-share system."""
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

from scripts.xiaogu_ensure_database import ensure_database_ready

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def ensure_api_database_ready() -> None:
    if not ensure_database_ready(DATABASE_URL):
        raise RuntimeError("xiaogu API startup aborted: PostgreSQL is unavailable")


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    ensure_api_database_ready()
    yield


app = FastAPI(
    title="xiaogu API",
    description="A-share paper trading intelligence system",
    version="0.1.0",
    lifespan=app_lifespan,
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


@app.get("/demo/cockpit")
def get_demo_cockpit():
    """Offline portfolio sample; never reads market data or production state."""
    return {
        "mode": "demo",
        "research_only": True,
        "not_investment_advice": True,
        "trade_date": "2026-07-10",
        "candidate": {
            "symbol": "600000",
            "name": "Sample Bank",
            "final_score": 82.4,
            "decision": "PAPER_PICK",
            "reasons": ["positive fund flow", "sector strength", "price gate passed"],
            "risk_flags": ["unusual volume"],
            "data_sources": ["eastmoney_quote", "fund_flow", "limitup_pool"],
        },
        "forward_validation": {"window": "20 sessions", "hit_rate": 0.55, "sample": True},
    }


@app.get("/picks")
def get_picks(
    date: Optional[str] = Query(None, description="Trade date YYYY-MM-DD"),
    decision: Optional[str] = Query(None, description="PAPER_PICK or NO_PICK"),
    include_superseded: bool = Query(False, description="Include superseded correction audit rows"),
    limit: int = Query(50, le=500),
):
    """List active picks by default, with explicit superseded audit opt-in."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if decision:
        where.append("decision = :decision")
        params["decision"] = decision
    if not include_superseded:
        where.append("COALESCE(features ->> 'superseded', 'false') <> 'true'")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT * FROM picks {clause} ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT :limit",
        params,
    )
    return {"picks": rows, "count": len(rows), "include_superseded": include_superseded}


@app.get("/picks/{trade_date}/summary")
def get_daily_summary(trade_date: str):
    """Daily active pick summary: current PAPER_PICK + highest active score."""
    rows = query_rows(
        """
        SELECT *
        FROM picks
        WHERE trade_date = :date
          AND COALESCE(features ->> 'superseded', 'false') <> 'true'
        ORDER BY updated_at DESC NULLS LAST, created_at DESC
        """,
        {"date": trade_date},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No active picks for {trade_date}")
    paper_picks = [r for r in rows if r["decision"] == "PAPER_PICK"]
    highest_rows = sorted(rows, key=lambda r: (r.get("final_score") is not None, r.get("final_score") or 0), reverse=True)
    paper_pick = paper_picks[0] if paper_picks else None
    features = paper_pick.get("features") if isinstance(paper_pick, dict) and isinstance(paper_pick.get("features"), dict) else {}
    source_summary = features.get("source_consumption_summary") if isinstance(features.get("source_consumption_summary"), dict) else {}
    return {
        "trade_date": trade_date,
        "paper_pick": paper_pick,
        "highest_score": highest_rows[0],
        "total_candidates": len(rows),
        "source_summary_path": paper_pick.get("source_summary_path") if paper_pick else "",
        "scan_source_time": source_summary.get("scan_source_time") or source_summary.get("source_time") or "",
        "source_completeness_status": source_summary.get("source_completeness_status") or "",
        "optional_or_proxy_gaps": source_summary.get("optional_or_proxy_gaps") or [],
    }


@app.get("/returns")
def get_returns(
    date: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit_up_only: bool = Query(False),
    method: str = Query("close", description="close, high, vwap"),
    limit: int = Query(50, le=500),
):
    """List return records with selectable exit methodology."""
    method_col = {"close": "COALESCE(t1_return_close, t1_return)", "high": "t1_return_high", "vwap": "t1_vwap"}.get(method, "COALESCE(t1_return_close, t1_return)")
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol
    if limit_up_only:
        where.append(f"{method_col} >= 0.095")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT *, {method_col} AS t1_return_method FROM returns {clause} ORDER BY trade_date DESC LIMIT :limit",
        params,
    )
    return {"returns": rows, "count": len(rows), "method": method}


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
            COUNT(*) FILTER (WHERE decision = 'RESEARCH_CANDIDATE') AS total_research,
            ROUND(AVG(COALESCE(r.t1_return_close, r.t1_return))::numeric, 4) AS avg_t1_close,
            ROUND(AVG(r.t1_return_high)::numeric, 4) AS avg_t1_high,
            ROUND(AVG(r.t1_vwap)::numeric, 4) AS avg_t1_vwap,
            COUNT(*) FILTER (WHERE COALESCE(r.t1_return_close, r.t1_return) >= 0.095) AS limit_ups_close,
            COUNT(*) FILTER (WHERE COALESCE(r.t1_return_close, r.t1_return) >= 0) AS wins_close,
            COUNT(*) FILTER (WHERE COALESCE(r.t1_return_close, r.t1_return) IS NOT NULL) AS total_filled,
            MAX(p.trade_date) AS latest_trade_date,
            MIN(p.trade_date) AS earliest_trade_date
        FROM picks p
        LEFT JOIN returns r ON p.trade_date = r.trade_date AND p.symbol = r.symbol
    """)
    return rows[0] if rows else {}


@app.get("/stats/performance")
def get_performance():
    """Detailed performance breakdown by period."""
    monthly = query_rows("""
        SELECT
            DATE_TRUNC('month', p.trade_date)::date AS month,
            COUNT(*) AS picks,
            COUNT(COALESCE(r.t1_return_close, r.t1_return)) AS filled,
            ROUND(AVG(COALESCE(r.t1_return_close, r.t1_return))::numeric, 4) AS avg_close,
            ROUND(AVG(r.t1_return_high)::numeric, 4) AS avg_high,
            ROUND(AVG(r.t1_vwap)::numeric, 4) AS avg_vwap,
            COUNT(*) FILTER (WHERE COALESCE(r.t1_return_close, r.t1_return) >= 0.095) AS limit_ups,
            COUNT(*) FILTER (WHERE COALESCE(r.t1_return_close, r.t1_return) >= 0) AS wins
        FROM picks p
        LEFT JOIN returns r ON p.trade_date = r.trade_date AND p.symbol = r.symbol
        WHERE p.decision = 'PAPER_PICK'
        GROUP BY 1 ORDER BY 1
    """)
    return {"monthly": monthly}


@app.get("/signals")
def get_signals(
    date: Optional[str] = Query(None, description="Trade date YYYY-MM-DD"),
    symbol: Optional[str] = Query(None, description="Stock code e.g. 300603"),
    signal_key: Optional[str] = Query(None, description="Signal name e.g. market_regime"),
    limit: int = Query(100, le=1000),
):
    """Raw signal values for stocks."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol
    if signal_key:
        where.append("signal_key = :key")
        params["key"] = signal_key
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT * FROM signals {clause} ORDER BY trade_date DESC, symbol LIMIT :limit",
        params,
    )
    return {"signals": rows, "count": len(rows)}


@app.get("/research-runs")
def get_research_runs(
    date: Optional[str] = Query(None),
    run_type: Optional[str] = Query(None, description="scanner or runner"),
    limit: int = Query(50, le=200),
):
    """Research/scan run history."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    if run_type:
        where.append("run_type = :type")
        params["type"] = run_type
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT id, trade_date, run_type, run_time, rule_version, scanner_version, runner_version, quotes_count, candidates_count, passed_count FROM research_runs {clause} ORDER BY trade_date DESC, run_time LIMIT :limit",
        params,
    )
    return {"runs": rows, "count": len(rows)}


@app.get("/scan-sessions")
def get_scan_sessions(
    date: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """Scanner session history."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if date:
        where.append("trade_date = :date")
        params["date"] = date
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT id, trade_date, scan_time, cdp_url, quotes_count, scored_count, passed_count, status FROM scan_sessions {clause} ORDER BY trade_date DESC, scan_time LIMIT :limit",
        params,
    )
    return {"sessions": rows, "count": len(rows)}


@app.get("/daily-candidates/{trade_date}")
def get_daily_candidates(trade_date: str):
    """Daily candidate analysis: official picks + top scored candidates with selection rationale."""
    rows = query_rows(
        "SELECT * FROM daily_candidates WHERE trade_date = :date ORDER BY is_official_pick DESC, final_score DESC NULLS LAST",
        {"date": trade_date},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No candidates for {trade_date}")
    return {"trade_date": trade_date, "candidates": rows, "count": len(rows)}


@app.get("/explain/{trade_date}/{symbol}")
def explain_candidate(trade_date: str, symbol: str):
    """Explain an existing candidate using persisted fields without changing scoring."""
    rows = query_rows(
        "SELECT * FROM daily_candidates WHERE trade_date = :date AND symbol = :symbol",
        {"date": trade_date, "symbol": symbol},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No candidate for {symbol} on {trade_date}")
    candidate = rows[0]
    score = candidate.get("final_score")
    reasons = [
        f"final score: {score}" if score is not None else "score not recorded",
        "official pick" if candidate.get("is_official_pick") else "research candidate",
    ]
    for key in ("selection_reason", "reason", "notes"):
        value = candidate.get(key)
        if value:
            reasons.append(str(value))
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "final_score": score,
        "decision": candidate.get("decision"),
        "reasons": reasons,
        "risk_flags": [],
        "data_sources": ["daily_candidates"],
    }


@app.get("/picks/{trade_date}/detail")
def get_pick_detail(trade_date: str):
    """Full pick detail: decision + returns + signals + candidate analysis for a date."""
    # Get candidates from daily_candidates table
    dc_rows = query_rows(
        "SELECT * FROM daily_candidates WHERE trade_date = :date ORDER BY is_official_pick DESC, final_score DESC NULLS LAST",
        {"date": trade_date},
    )
    if not dc_rows:
        raise HTTPException(status_code=404, detail=f"No candidates for {trade_date}")

    # Enrich with returns
    symbols = [r["symbol"] for r in dc_rows if r["symbol"]]
    returns = {}
    if symbols:
        ret_rows = query_rows(
            "SELECT * FROM returns WHERE trade_date = :date AND symbol = ANY(:symbols)",
            {"date": trade_date, "symbols": symbols},
        )
        returns = {r["symbol"]: r for r in ret_rows}

    for r in dc_rows:
        r["return"] = returns.get(r["symbol"])

    official = [r for r in dc_rows if r["is_official_pick"]]
    return {
        "trade_date": trade_date,
        "official_pick": official[0] if official else None,
        "candidates": dc_rows,
    }
