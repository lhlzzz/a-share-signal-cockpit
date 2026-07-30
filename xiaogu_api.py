#!/usr/bin/env python3
"""FastAPI service for xiaogu A-share system."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from fastapi.staticfiles import StaticFiles

from scripts.xiaogu_ensure_database import ensure_database_ready

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
PUBLIC_DIR = Path(__file__).resolve().parent / "public"


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

if PUBLIC_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=PUBLIC_DIR, html=True), name="dashboard")


def query_rows(sql: str, params: dict = {}) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def _as_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _resolve_dashboard_dates(requested_date: Optional[str]) -> Dict[str, Optional[str]]:
    if requested_date:
        return {
            "scan_date": requested_date,
            "candidate_date": requested_date,
            "pick_date": requested_date,
        }
    latest = query_rows("""
        SELECT
            (SELECT MAX(trade_date) FROM scan_sessions) AS scan_date,
            (SELECT MAX(trade_date) FROM daily_candidates) AS candidate_date,
            (SELECT MAX(trade_date) FROM picks) AS pick_date
    """)
    row = latest[0] if latest else {}
    return {
        "scan_date": _iso(row.get("scan_date")) or None,
        "candidate_date": _iso(row.get("candidate_date")) or None,
        "pick_date": _iso(row.get("pick_date")) or None,
    }


def _dashboard_return(value: Any) -> Optional[float]:
    try:
        return round(float(value), 4) if value is not None else None
    except (TypeError, ValueError):
        return None


@app.get("/api/dashboard/overview")
def get_dashboard_overview(
    date: Optional[str] = Query(None, description="A-share trade date YYYY-MM-DD"),
):
    """Single read model for the A-share operator dashboard."""
    dates = _resolve_dashboard_dates(date)
    scan_date = dates["scan_date"]
    candidate_date = dates["candidate_date"]
    pick_date = dates["pick_date"]

    session_rows = query_rows("""
        SELECT id, trade_date, scan_time, quotes_count, scored_count, passed_count,
               status, market_snapshot, source_status, source_counts,
               source_diagnostics
        FROM scan_sessions
        WHERE (:scan_date IS NULL OR trade_date = CAST(:scan_date AS date))
        ORDER BY trade_date DESC, scan_time DESC
        LIMIT 1
    """, {"scan_date": scan_date})
    session = session_rows[0] if session_rows else {}
    market_snapshot = _as_json_object(session.get("market_snapshot"))
    source_status = _as_json_object(session.get("source_status"))
    source_counts = _as_json_object(session.get("source_counts"))
    source_diagnostics = _as_json_object(session.get("source_diagnostics"))
    completeness = _as_json_object(source_status.get("source_completeness"))

    candidate_rows = query_rows("""
        SELECT
            dc.trade_date, dc.symbol, dc.stock_name, dc.rank, dc.final_score,
            dc.decision, dc.selection_outcome, dc.is_official_pick,
            dc.pct_chg, dc.close_position_score, dc.market_regime,
            dc.auxiliary_evidence_snapshot, dc.selection_reason,
            dc.ticket_reason, dc.not_selected_reason,
            COALESCE(r.t1_return_close, r.t1_return) AS t1_return,
            r.t1_return_high, r.next_day_open_return,
            r.next_day_high_return
        FROM daily_candidates dc
        LEFT JOIN returns r
          ON r.trade_date = dc.trade_date AND r.symbol = dc.symbol
        WHERE (:candidate_date IS NULL OR dc.trade_date = CAST(:candidate_date AS date))
        ORDER BY dc.rank NULLS LAST, dc.final_score DESC NULLS LAST
        LIMIT 20
    """, {"candidate_date": candidate_date})

    pool_rows = query_rows("""
        SELECT COALESCE(selection_outcome, decision, 'UNKNOWN') AS outcome, COUNT(*) AS count
        FROM daily_candidates
        WHERE (:candidate_date IS NULL OR trade_date = CAST(:candidate_date AS date))
        GROUP BY 1
        ORDER BY count DESC
    """, {"candidate_date": candidate_date})

    pick_rows = query_rows("""
        SELECT
            p.id, p.trade_date, p.symbol, p.stock_name, p.decision,
            p.final_score, p.rank, p.auxiliary_evidence_status,
            p.ticket_reason, p.selection_reason, p.created_at,
            COALESCE(r.t1_return_close, r.t1_return) AS t1_return,
            r.t1_return_high
        FROM picks p
        LEFT JOIN returns r
          ON r.trade_date = p.trade_date AND r.symbol = p.symbol
        WHERE (:pick_date IS NULL OR p.trade_date = CAST(:pick_date AS date))
          AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 20
    """, {"pick_date": pick_date})

    recent_picks = query_rows("""
        SELECT
            p.trade_date, p.symbol, p.stock_name, p.decision, p.final_score,
            p.rank, COALESCE(r.t1_return_close, r.t1_return) AS t1_return
        FROM picks p
        LEFT JOIN returns r
          ON r.trade_date = p.trade_date AND r.symbol = p.symbol
        WHERE COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        ORDER BY p.trade_date DESC, p.created_at DESC
        LIMIT 12
    """)

    paper_pick = next((row for row in pick_rows if row.get("decision") == "PAPER_PICK"), None)
    latest_decision = pick_rows[0] if pick_rows else None
    filled = [row for row in recent_picks if row.get("t1_return") is not None]
    wins = [row for row in filled if float(row["t1_return"]) >= 0]
    limit_ups = [row for row in filled if float(row["t1_return"]) >= 0.095]
    average_t1 = (
        round(sum(float(row["t1_return"]) for row in filled) / len(filled), 4)
        if filled else None
    )

    scan_time = session.get("scan_time")
    scan_age = None
    if scan_time:
        try:
            scan_dt = scan_time if scan_time.tzinfo else scan_time.replace(tzinfo=timezone.utc)
            scan_age = max(0, int((datetime.now(timezone.utc) - scan_dt.astimezone(timezone.utc)).total_seconds()))
        except (AttributeError, TypeError, ValueError):
            scan_age = None

    source_health = []
    for name, value in source_status.items():
        if isinstance(value, dict):
            source_health.append({
                "name": name,
                "status": value.get("status") or value.get("mode") or "UNKNOWN",
                "source": value.get("source") or value.get("mode") or "",
                "missing": value.get("missing_sources") or value.get("missing_domains") or value.get("flags") or [],
                "count": source_counts.get(name),
            })

    return {
        "mode": "PAPER_ONLY",
        "trading_enabled": False,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "dates": dates,
        "scan": {
            "trade_date": _iso(session.get("trade_date")),
            "scan_time": _iso(scan_time),
            "age_seconds": scan_age,
            "status": session.get("status") or "missing",
            "quotes_count": session.get("quotes_count") or 0,
            "scored_count": session.get("scored_count") or 0,
            "passed_count": session.get("passed_count") or 0,
        },
        "market": {
            "regime": market_snapshot.get("market_regime") or "UNKNOWN",
            "big_up_count": market_snapshot.get("market_bigups"),
            "passed_count": market_snapshot.get("passed_count"),
            "source": _as_json_object(source_status.get("external_market")).get("source", "Eastmoney API"),
            "completeness": completeness.get("status") or "UNKNOWN",
            "missing": completeness.get("flags") or completeness.get("missing_sources") or [],
        },
        "decision": {
            "latest": latest_decision,
            "paper_pick": paper_pick,
            "candidate_date": candidate_date,
            "candidate_count": len(candidate_rows),
            "pool_outcomes": pool_rows,
        },
        "candidates": [
            {
                **row,
                "t1_return": _dashboard_return(row.get("t1_return")),
                "t1_return_high": _dashboard_return(row.get("t1_return_high")),
                "pct_chg": _dashboard_return(row.get("pct_chg")),
                "auxiliary_status": (
                    _as_json_object(row.get("auxiliary_evidence_snapshot")).get("status")
                    or "UNKNOWN"
                ),
            }
            for row in candidate_rows
        ],
        "performance": {
            "filled": len(filled),
            "wins": len(wins),
            "limit_ups": len(limit_ups),
            "win_rate": round(len(wins) / len(filled) * 100, 1) if filled else None,
            "average_t1": average_t1,
        },
        "recent_picks": [
            {
                **row,
                "t1_return": _dashboard_return(row.get("t1_return")),
                "date": _iso(row.get("trade_date")),
            }
            for row in recent_picks
        ],
        "sources": {
            "health": source_health,
            "counts": source_counts,
            "diagnostics": source_diagnostics,
        },
        "rules": {
            "candidate_policy": "先过 T+1 获利证据门，再做 Top10 与 PAPER_PICK",
            "limitup_policy": "当日涨停/封死不可交易标的不得进入可交易候选池",
            "underwater_policy": "T 日下跌票不直接排除，必须通过 T+1 获利证据门",
            "sszcw": "软上下文，仅作解释与排序辅助，不强制出票",
        },
    }


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
