#!/usr/bin/env python3
"""FastAPI service for xiaogu A-share system."""
import math
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, ProxyHandler, build_opener

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, text
from fastapi.staticfiles import StaticFiles

from scripts.xiaogu_ensure_database import ensure_database_ready

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://xiaogu:xiaogu@localhost:5432/xiaogu"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
EASTMONEY_OPENER = build_opener(ProxyHandler({}))
EASTMONEY_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}


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


def _public_trading_text(value: Any) -> str:
    return str(value or "").replace("PAPER_PICK", "TRADING").replace("paper_pick", "trading")


def _as_json_array(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


@lru_cache(maxsize=512)
def _eastmoney_stock_sector_profile(symbol: str) -> Dict[str, Any]:
    """Read missing stock-industry metadata from Eastmoney's stock-level sources."""
    code = str(symbol or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return {}
    market_prefix = "sh" if code.startswith("6") else "sz"
    try:
        quote_request = Request(
            f"https://quote.eastmoney.com/concept/{market_prefix}{code}.html",
            headers=EASTMONEY_HEADERS,
        )
        with EASTMONEY_OPENER.open(quote_request, timeout=8) as response:
            quote_html = response.read().decode("utf-8", "replace")
        marker = "var quotedata ="
        marker_pos = quote_html.find(marker)
        quote_payload: Dict[str, Any] = {}
        if marker_pos >= 0:
            quote_payload, _ = json.JSONDecoder().raw_decode(
                quote_html[marker_pos + len(marker):].lstrip()
            )
            if not isinstance(quote_payload, dict):
                quote_payload = {}

        survey_request = Request(
            "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax"
            f"?code={market_prefix.upper()}{code}",
            headers=EASTMONEY_HEADERS,
        )
        with EASTMONEY_OPENER.open(survey_request, timeout=8) as response:
            survey_payload = json.loads(response.read().decode("utf-8", "replace"))
        company = survey_payload.get("jbzl") if isinstance(survey_payload, dict) else {}
        company = company if isinstance(company, dict) else {}
        board_name = str(quote_payload.get("bk_name") or "").strip()
        industry_name = str(company.get("sshy") or "").strip()
        sw_industry_name = str(company.get("sszjhhy") or "").strip()
        primary = board_name or industry_name or sw_industry_name
        if not primary:
            return {}
        return {
            "names": [primary],
            "primary": primary,
            "industry_board": board_name,
            "eastmoney_industry": industry_name,
            "sw_industry": sw_industry_name,
            "source": "eastmoney_quote_quotedata+company_survey_api",
            "symbol": code,
        }
    except Exception:
        return {}


def _eastmoney_index_level(value: Any) -> Optional[float]:
    number = _to_float(value)
    if number is None:
        return None
    return round(number / 100.0, 2)


def _latest_scan_market_payloads(production_run_id: Optional[str] = None) -> Dict[str, Any]:
    run_id = production_run_id
    if not run_id:
        active = _resolve_production_run()
        run_id = active.get("production_run_id") if active else None
    market_session_rows = query_rows("""
        SELECT smd.scan_session_id, smd.trade_date, smd.scan_time
        FROM scan_market_data smd
        JOIN scan_sessions ss ON ss.id = smd.scan_session_id
        WHERE ss.production_run_id = :production_run_id
        ORDER BY smd.scan_session_id DESC, smd.trade_date DESC, smd.scan_time DESC
        LIMIT 1
    """, {"production_run_id": run_id})
    market_session_id = market_session_rows[0].get("scan_session_id") if market_session_rows else None
    session_rows = query_rows("""
        SELECT id, trade_date, scan_time, market_snapshot, quotes_count, scored_count, passed_count
        FROM scan_sessions
        WHERE id = :scan_session_id AND production_run_id = :production_run_id
        LIMIT 1
    """, {"scan_session_id": market_session_id, "production_run_id": run_id}) if market_session_id else []
    session = session_rows[0] if session_rows else {}
    scan_session_id = market_session_id or session.get("id")
    payload_rows = query_rows("""
        SELECT domain, payload
        FROM scan_market_data
        WHERE scan_session_id = :scan_session_id
        ORDER BY domain
    """, {"scan_session_id": scan_session_id}) if scan_session_id else []
    payloads = {}
    for row in payload_rows:
        domain = str(row.get("domain") or "").strip()
        if domain:
            payloads[domain] = row.get("payload")
    return {"session": session, "payloads": payloads}


def _as_share_index_quote(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(item.get("f57") or "").strip(),
        "name": str(item.get("name") or item.get("f58") or "").strip(),
        "level": _eastmoney_index_level(item.get("f43")) or 0,
        "high": _eastmoney_index_level(item.get("f44")),
        "low": _eastmoney_index_level(item.get("f45")),
        "open": _eastmoney_index_level(item.get("f46")),
        "volume": _to_float(item.get("f47")),
        "amount": _to_float(item.get("f48")),
        "changePercent": _round_float(_to_float(item.get("f50")) / 100.0 if _to_float(item.get("f50")) is not None else None, 2),
    }


def _as_share_sector_flow_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": str(item.get("f12") or "").strip(),
        "name": str(item.get("f14") or "").strip(),
        "changePercent": _round_float(_to_float(item.get("f3")), 2),
        "netInflow": _to_float(item.get("f62")),
        "superLargeInflow": _to_float(item.get("f66")),
        "mediumInflow": _to_float(item.get("f72")),
        "leaderStockName": str(item.get("f204") or "").strip(),
        "leaderStockCode": str(item.get("f205") or "").strip(),
    }


def _as_market_capital_flow_item(item: Dict[str, Any]) -> Dict[str, Any]:
    klines = item.get("klines")
    if isinstance(klines, list) and klines:
        parts = str(klines[0] or "").split(",")
        if len(parts) >= 5:
            return {
                "name": str(item.get("name") or "").strip(),
                "secid": str(item.get("secid") or "").strip(),
                "mainInflow": _to_float(parts[1]),
                "superLargeInflow": _to_float(parts[2]),
                "mediumInflow": _to_float(parts[3]),
                "largeInflow": _to_float(parts[4]),
            }
    return {
        "name": str(item.get("name") or "").strip(),
        "secid": str(item.get("secid") or "").strip(),
        "mainInflow": _to_float(item.get("f62")),
        "superLargeInflow": _to_float(item.get("f66")),
        "mediumInflow": _to_float(item.get("f72")),
        "largeInflow": _to_float(item.get("f75")),
    }


def _resolve_dashboard_dates(requested_date: Optional[str]) -> Dict[str, Optional[str]]:
    if isinstance(requested_date, str) and requested_date:
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
    scan_date = _iso(row.get("scan_date")) or None
    candidate_date = _iso(row.get("candidate_date")) or None
    pick_date = _iso(row.get("pick_date")) or None
    # A fresh scan owns the operator view. Do not show yesterday's candidate
    # pool or pick beneath today's live market snapshot.
    if scan_date:
        candidate_date = scan_date
        pick_date = scan_date
    return {
        "scan_date": scan_date,
        "candidate_date": candidate_date,
        "pick_date": pick_date,
    }


def _resolve_production_run(
    requested_date: Optional[str] = None,
    production_run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve one authoritative run; legacy date-only rows are never auto-joined."""
    if production_run_id:
        rows = query_rows("""
            SELECT pr.production_run_id, pr.trade_date, pra.candidate_snapshot_id,
                   pra.active_pick_id, pr.status
            FROM production_runs pr
            LEFT JOIN production_run_active pra
              ON pra.production_run_id = pr.production_run_id
             AND pra.trade_date = pr.trade_date
            WHERE pr.production_run_id = :production_run_id
              AND (:trade_date IS NULL OR pr.trade_date = CAST(:trade_date AS date))
            LIMIT 1
        """, {
            "production_run_id": production_run_id,
            "trade_date": requested_date or None,
        })
    else:
        rows = query_rows("""
            SELECT pra.production_run_id, pra.trade_date, pra.candidate_snapshot_id,
                   pra.active_pick_id, pr.status
            FROM production_run_active pra
            JOIN production_runs pr ON pr.production_run_id = pra.production_run_id
            WHERE (:trade_date IS NULL OR pra.trade_date = CAST(:trade_date AS date))
            ORDER BY pra.trade_date DESC, pra.updated_at DESC
            LIMIT 1
        """, {"trade_date": requested_date or None})
    return rows[0] if rows else None


def _dashboard_return(value: Any) -> Optional[float]:
    try:
        return round(float(value), 4) if value is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_source_time(row: Dict[str, Any]) -> Optional[str]:
    raw = _as_json_object(row.get("raw_json"))
    features = _as_json_object(row.get("candidate_features"))
    value = (
        row.get("source_time")
        or raw.get("source_time")
        or raw.get("runner_asof_time")
        or features.get("source_time")
    )
    return str(value) if value not in (None, "") else None


def _t1_result_annotations(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add post-close diagnostics without changing the persisted formal rank."""
    top10 = []
    for row in rows:
        try:
            rank = int(row.get("rank"))
        except (TypeError, ValueError):
            continue
        if rank <= 10:
            top10.append(row)
    filled = [row for row in top10 if _dashboard_return(row.get("t1_return")) is not None]
    complete = bool(top10) and len(filled) == len(top10)
    ordered = sorted(
        filled,
        key=lambda row: (
            -float(row["t1_return"]),
            int(row.get("rank") or 999),
            str(row.get("symbol") or ""),
        ),
    )
    labels = {id(row): (index, ("状元", "榜眼", "探花")[index - 1])
              for index, row in enumerate(ordered[:3], start=1)}
    annotated = []
    for row in rows:
        output = dict(row)
        output["t1_result_complete"] = _dashboard_return(row.get("t1_return")) is not None
        output["t1_result_coverage"] = f"{len(filled)}/{len(top10)}" if top10 else "0/0"
        output["t1_result_rank"] = None
        output["t1_result_label"] = None
        if complete and id(row) in labels:
            result_rank, label = labels[id(row)]
            output["t1_result_rank"] = result_rank
            output["t1_result_label"] = label
        annotated.append(output)
    return annotated


def _first_json_value(containers: List[Dict[str, Any]], key: str, default: Any = None) -> Any:
    for container in containers:
        value = container.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


_GATE_REASON_CN = {
    "ALL_FORWARD_PAPER_HARD_GATES_PASS": "全部正式出票门禁通过",
    "replayed_from_scan_summary_with_db_pick": "基于扫描摘要与数据库出票回放",
    "NO_PICK_FALLBACK_TO_HIGHEST_SCORE": "正式门禁未通过，回放使用最高分回退候选",
    "ACTIVE_CHAIN_GOVERNANCE_GATE_NOT_PASS": "主力行为生产链治理门禁未通过",
    "ACTIVE_CANDIDATE_BUNDLE_PATH_DEGRADED": "正式候选数据包链路降级",
    "CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION": "追高但缺少涨停确认",
    "FINAL_PICK_MUST_BE_BUYABLE_SEALED_LIMIT_UP": "封死涨停，收盘前不可交易",
    "FINAL_PICK_MUST_BE_BUYABLE_SMALL_ACCOUNT_FALSE": "小账户买入条件未通过",
    "RECENT_T1_NONPROFIT_HARD_BLOCK": "连续历史 T+1 非盈利，触发冷却",
    "REGULATORY_HARD_BLOCK": "监管或风险提示硬门禁",
    "DATA_GATE_NOT_PASS_RUNTIME_INDEX_SNAPSHOT": "运行时行情数据门禁未通过",
    "T1_PROFIT_EVIDENCE_INSUFFICIENT": "T+1 获利证据不足",
    "CORE_MARKET_SOURCE_GATE_NOT_PASS": "核心市场数据源门禁未通过",
    "QUALIFIED_CANDIDATE_FALSE": "正式资格未通过",
    "HARD_GATE_NOT_ALL_PASS": "正式门禁未全部通过",
    "mainboard_auxiliary_evidence_status_not_PASS": "主板辅助证据状态未通过",
    "low_score_without_direct_catalyst_confirmation": "分数偏低且缺少直接催化确认",
    "buy_confirmation_below_threshold": "买入确认度低于阈值",
    "outside_top10_full_candidate_pool": "位于正式前十之外",
    "ranked_below_top10": "正式排序低于前十",
    "mainboard auxiliary evidence status=PASS": "主板辅助证据状态通过",
    "sector opportunity score>=0.2 or VEI strong signal": "主线机会分或 VEI 强信号达到要求",
    "score>=70_or_direct_catalyst_confirmation": "正式分达到 70 或有直接催化确认",
    "score>=70 or direct catalyst confirmation": "正式分达到 70 或有直接催化确认",
    "CANDIDATE_BLOCKED": "候选被机会门禁拦截",
    "mainboard_auxiliary_evidence_status": "主板辅助证据状态",
    "data_gate_status=PASS": "数据门禁通过",
    "candidate_evidence_status=PASS": "候选证据通过",
    "source_time<=asof_time": "数据时间不晚于决策时点",
    "one_lot_cost_valid": "一手成本有效",
    "one_lot_cost<=cap": "一手成本在资金边界内",
    "risk_penalty=0": "风险惩罚为零",
    "no_regulatory_hard_block": "未触发监管风险硬门禁",
    "no_near_limit_up_risk": "未触发近涨停追高风险",
    "sector_opportunity_score>=0.2": "板块机会分达到 0.2",
    "vei_phase_d_tags includes SECTOR_OPPORTUNITY": "VEI 阶段标签包含板块机会",
    "fund_flow_momentum>0": "资金流动量为正",
    "time_series_momentum>0": "时间序列动量为正",
    "candidate_stage=flat_0_to_3": "候选处于低位启动阶段",
    "early_opportunity_score>=0.65": "早期机会分达到 0.65",
    "buy_confirmation>=0.6": "买入确认度达到 0.6",
    "near_limit_chase": "接近涨停追高",
    "weak_fund_confirmation": "主力资金确认偏弱",
    "concept_hype_without_company_link": "题材热度与公司直接关联不足",
}

_RANKING_REASON_CN = {
    "formal_profit_first": "主力行为链排序",
    "formal_profit_first_unique_sort": "唯一正式排序器：主力行为链（T+1获利目标）",
    "structured_evidence_primary": "唯一正式排序器：主力行为链（T+1获利目标）",
    "正式候选排序": "唯一正式排序器：主力行为链（T+1获利目标）",
}

_PRODUCTION_CHAIN_NAME = "main_force_behavior_chain"
_PRODUCTION_RANK_SOURCE = "formal_profit_first"


def _load_latest_chain_replay() -> Dict[str, Any]:
    """Build active-chain replay statistics from the authoritative database."""
    rows = query_rows("""
        SELECT
            dc.trade_date,
            dc.symbol,
            dc.rank,
            r.t1_return
        FROM daily_candidates dc
        JOIN production_run_active pra
          ON pra.trade_date = dc.trade_date
         AND pra.production_run_id = dc.production_run_id
        LEFT JOIN returns r
          ON r.production_run_id = dc.production_run_id
         AND r.symbol = dc.symbol
        WHERE COALESCE(
                dc.ranking_basis ->> 'ranking_view',
                dc.raw_json ->> 'ranking_view'
              ) = :production_chain
          AND COALESCE(
                dc.ranking_basis ->> 'rank_source',
                dc.raw_json ->> 'rank_source'
              ) = :rank_source
        ORDER BY dc.trade_date, dc.rank NULLS LAST, dc.symbol
    """, {
        "production_chain": _PRODUCTION_CHAIN_NAME,
        "rank_source": _PRODUCTION_RANK_SOURCE,
    })
    candidate_dates = sorted({_iso(row.get("trade_date")) for row in rows if row.get("trade_date")})
    settled_samples = [
        {
            "trade_date": _iso(row.get("trade_date")),
            "symbol": row.get("symbol"),
            "rank": row.get("rank"),
            "t1_return": float(row["t1_return"]),
            "gate": "",
        }
        for row in rows
        if row.get("rank") == 1 and _to_float(row.get("t1_return")) is not None
    ]
    equity = 1.0
    peak = 1.0
    peak_sample: Optional[Dict[str, Any]] = None
    max_drawdown_detail: Dict[str, Any] = {}
    max_drawdown_value = 0.0
    for sample in settled_samples:
        equity *= 1.0 + sample["t1_return"]
        if equity > peak:
            peak = equity
            peak_sample = dict(sample)
        drawdown = equity / peak - 1.0 if peak else 0.0
        if drawdown < max_drawdown_value:
            max_drawdown_value = drawdown
            max_drawdown_detail = {
                **sample,
                "drawdown": round(drawdown * 100, 4),
                "peak_trade_date": peak_sample.get("trade_date") if peak_sample else None,
                "peak_symbol": peak_sample.get("symbol") if peak_sample else None,
            }
    wins = sum(sample["t1_return"] > 0 for sample in settled_samples)
    losses = sum(sample["t1_return"] < 0 for sample in settled_samples)
    return {
        "status": "DATABASE_FULL_REPLAY",
        "source": "PostgreSQL daily_candidates/returns/production_run_active",
        "tier": "active_production_runs",
        "window": {
            "start": candidate_dates[0] if candidate_dates else "",
            "end": candidate_dates[-1] if candidate_dates else "",
            "trading_days": len(candidate_dates),
        },
        "database_trade_dates": len(candidate_dates),
        "candidate_days": len(candidate_dates),
        "candidate_rows": len(rows),
        "candidate_rows_with_t1": sum(
            _to_float(row.get("t1_return")) is not None for row in rows
        ),
        "sample_count": len(settled_samples),
        "winning_samples": wins,
        "losing_samples": losses,
        "win_rate": round(wins * 100 / len(settled_samples), 2) if settled_samples else 0.0,
        "average_t1_return": round(
            sum(sample["t1_return"] for sample in settled_samples)
            * 100
            / len(settled_samples),
            4,
        ) if settled_samples else 0.0,
        "max_drawdown": round(max_drawdown_value * 100, 4),
        "settledSamples": list(reversed(settled_samples)),
        "max_drawdown_detail": max_drawdown_detail,
    }


def _cn_gate_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("limitup_continuation:"):
        return "涨停延续证据：" + text.split(":", 1)[1]
    for code, label in _GATE_REASON_CN.items():
        if code in text:
            return label
    for code, label in _GATE_REASON_CN.items():
        if code.lower() in text.lower():
            return label
    return text.replace("_", " ")


def _cn_ranking_reason(value: Any) -> str:
    text = str(value or "").strip()
    return _RANKING_REASON_CN.get(
        text,
        text.replace("_", " ") if text else "唯一正式排序器：主力行为链（T+1获利目标）",
    )


def _factor_number(containers: List[Dict[str, Any]], *keys: str) -> Any:
    return _first_json_value(containers, keys[0]) if len(keys) == 1 else next(
        (value for key in keys for value in [ _first_json_value(containers, key) ] if value not in (None, "")),
        None,
    )


def _format_factor_value(value: Any) -> str:
    if value in (None, ""):
        return "未记录"
    if isinstance(value, bool):
        return "有" if value else "无"
    if isinstance(value, (int, float)):
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    return str(value)


def _candidate_selection_explanation(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return the user-facing, Chinese explanation for a candidate decision."""
    factor = _as_json_object(row.get("factor_snapshot"))
    features = _as_json_object(row.get("candidate_features"))
    eligibility = _as_json_object(row.get("eligibility_snapshot"))
    diagnostics = _as_json_object(row.get("selection_diagnostics"))
    ranking = _as_json_object(row.get("ranking_basis"))
    raw = _as_json_object(row.get("raw_json"))
    research = _as_json_object(raw.get("research_signals"))
    t1_profile = _as_json_object(raw.get("t1_profit_profile"))
    ranking_components = (
        _as_json_object(raw.get("ranking_basis_adjustment_components"))
        or _as_json_object(ranking.get("ranking_basis_adjustment_components"))
        or _as_json_object(factor.get("ranking_basis_adjustment_components"))
    )
    containers = [
        row,
        factor,
        features,
        eligibility,
        diagnostics,
        ranking,
        ranking_components,
        research,
        raw,
        t1_profile,
    ]

    factors: List[Dict[str, Any]] = []

    def add_factor(name: str, keys: List[str], detail: str) -> None:
        value = _factor_number(containers, *keys)
        if value not in (None, ""):
            factors.append({
                "名称": name,
                "数值": _format_factor_value(value),
                "说明": detail,
            })

    add_factor("事件催化", ["announcement_catalyst_score", "news_catalyst_strength"], "公告、政策或新闻催化强度")
    add_factor("板块承载", ["sector_attack_score", "sector_opportunity_score", "sector_catalyst_score"], "板块承载与扩散强度")
    add_factor("直接资金确认", ["flow_confirmation_score", "capital_behavior_score", "fund_flow_momentum", "net_inflow_main"], "T日可观测的直接资金行为")
    add_factor("T+1空间", ["t1_room_score", "low_position_catalyst_score", "close_position_score"], "T日证据对应的次日可兑现空间")
    add_factor("派发风险", ["distribution_risk_score", "capital_risk_profile"], "资金派发与高位兑现风险")
    selection_reasons_zh = []
    for container in (row, features, eligibility):
        values = container.get("selection_reasons_zh")
        if isinstance(values, list):
            selection_reasons_zh.extend(str(value) for value in values if value)
    selection_reasons_zh = list(dict.fromkeys(selection_reasons_zh))

    blockers: List[str] = []
    for source in (
        row.get("blockers"),
        eligibility.get("blockers"),
        diagnostics.get("blockers"),
        row.get("not_selected_reason"),
    ):
        values = source if isinstance(source, list) else [source]
        blockers.extend(_cn_gate_reason(value) for value in values if value)
    blockers = list(dict.fromkeys(value for value in blockers if value))
    gate_status = eligibility.get("eligible")
    if gate_status is True and not blockers:
        gate_text = "已通过正式门禁"
    elif blockers:
        gate_text = "；".join(blockers[:4])
    else:
        gate_text = "已通过正式门禁"

    summary = [
        f"{item['名称']}：{item['说明']}={item['数值']}"
        for item in factors
    ]
    summary = list(dict.fromkeys([*selection_reasons_zh, *summary]))
    summary.append("门禁：通过后按主力行为链正式排序" if not blockers else f"门禁：{gate_text}")
    ranking_key = (
        ranking_components.get("ranking_view")
        or raw.get("ranking_view")
        or ranking.get("rank_source")
        or ranking.get("basis")
        or ranking.get("ranking_basis")
        or "正式候选排序"
    )
    ranking_text = (
        "主力行为链排序"
        if ranking_key == "main_force_behavior_chain"
        else _cn_ranking_reason(ranking_key)
    )
    return {
        "因子": factors,
        "门禁": blockers or ["已通过正式门禁"],
        "门禁中文": gate_text,
        "排序依据": "门禁通过后按事件催化、板块承载、直接资金确认、T+1空间和派发风险排序",
        "排序口径": str(ranking_text),
        "中文摘要": summary,
    }


def _candidate_entry_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact, source-labeled explanation for pool admission."""
    auxiliary = _as_json_object(row.get("auxiliary_evidence_snapshot"))
    features = _as_json_object(row.get("candidate_features"))
    raw = _as_json_object(row.get("raw_json"))
    research = _as_json_object(raw.get("research_signals"))
    auxiliary_news = _as_json_object(auxiliary.get("news"))
    raw_news = _as_json_object(raw.get("news_evidence"))
    containers = [auxiliary, auxiliary_news, features, raw, raw_news]
    announcements = (
        _first_json_value(containers, "announcement_evidence", [])
        or _first_json_value(containers, "announcements", [])
        or []
    )
    news = _first_json_value(containers, "news_evidence", {}) or raw_news
    sector_news = (
        _first_json_value(containers, "sector_news_evidence", [])
        or (news.get("sector_related_news") if isinstance(news, dict) else [])
        or []
    )
    capital_flow = _first_json_value(
        containers,
        "capital_flow_evidence",
        _first_json_value(containers, "data_directory_capital_flow", {}),
    ) or {}
    if not isinstance(news, dict):
        news = {}
    direct_news = news.get("direct_symbol_news") or []
    policy_evidence = _first_json_value(containers, "policy_evidence", []) or []
    limitup_reasons = _first_json_value(containers, "limitup_reasons", []) or []
    sentiment = _first_json_value(containers, "sentiment_catalyst")
    social_sentiment = _first_json_value(containers, "social_sentiment_score")
    fund_flow = _first_json_value(containers, "net_inflow_main")
    if fund_flow is None:
        fund_flow = _first_json_value(containers, "fund_flow_momentum")
    risk_review = research.get("a_share_risk_review") if isinstance(research.get("a_share_risk_review"), dict) else {}
    quality = research.get("catalyst_quality") if isinstance(research.get("catalyst_quality"), dict) else {}
    risk_flags = list(row.get("blockers") or []) if isinstance(row.get("blockers"), list) else []
    risk_flags.extend(auxiliary.get("risk_notice_evidence") or [])
    if quality.get("category") in ("risk_notice", "regulatory_notice") or quality.get("regulatory_hard_block"):
        risk_flags.append("regulatory_hard_block")
    if risk_review.get("disqualified_for_paper_pick"):
        risk_flags.append("a_share_risk_review_disqualified")
    missing = []
    for container in containers:
        for key in ("mainboard_auxiliary_missing_domains", "candidate_evidence_missing_domains", "enhanced_evidence_missing_domains"):
            values = container.get(key)
            if isinstance(values, list):
                missing.extend(str(value) for value in values if value)
    labels = []
    if announcements:
        labels.append("公告")
    if direct_news:
        labels.append("个股新闻")
    if sector_news:
        labels.append("板块新闻")
    if capital_flow not in ({}, None) or fund_flow not in (None, ""):
        labels.append("资金")
    if sentiment not in (None, "") or social_sentiment not in (None, ""):
        labels.append("情绪")
    if not labels:
        labels.append("结构/技术")
    # Consecutive limit-up data
    consecutive_limit = _first_json_value(containers, "consecutive_limit_count")
    if consecutive_limit is None:
        consecutive_limit = _first_json_value(containers, "consecutive_limitups")
    if consecutive_limit is None:
        consecutive_limit = _first_json_value(containers, "consecutive_limit_days")
    social_evidence = _first_json_value(
        containers,
        "social_evidence",
        _first_json_value(containers, "blogger_evidence", _first_json_value(containers, "sszcw_evidence", [])),
    ) or []
    macro_evidence = _first_json_value(
        containers,
        "macro_evidence",
        _first_json_value(containers, "overseas_evidence", _first_json_value(containers, "macro_context", [])),
    ) or []
    if isinstance(social_evidence, dict):
        social_evidence = [social_evidence]
    if isinstance(macro_evidence, dict):
        macro_evidence = [macro_evidence]
    if social_evidence:
        labels.append("社交/博主")
    if macro_evidence:
        labels.append("宏观/海外")
    source_time = _candidate_source_time(row)
    audit = (
        _as_json_object(auxiliary.get("information_coverage_audit"))
        or _as_json_object(raw.get("information_coverage_audit"))
    )
    audit_sources = {}
    for group in ("news_sources", "auxiliary_sources"):
        values = audit.get(group)
        if isinstance(values, dict):
            audit_sources.update(values)
    source_specs = [
        ("公告", "announcements", announcements, "eastmoney_announcements"),
        ("个股新闻", "direct_symbol_news", direct_news, "eastmoney_news"),
        ("板块新闻", "sector_news", sector_news, "eastmoney_sector_news"),
        ("涨停原因", "limitup_reasons", limitup_reasons, "eastmoney_limitup_reasons"),
        ("社交/博主", "social_blog", social_evidence, "MISSING"),
        ("宏观/海外", "macro_overseas", macro_evidence, "MISSING"),
    ]
    source_metadata = []
    for label, domain, evidence, default_source in source_specs:
        audit_row = audit_sources.get(domain) or {}
        if not isinstance(audit_row, dict):
            audit_row = {}
        first_evidence = evidence[0] if isinstance(evidence, list) and evidence else {}
        evidence_source = first_evidence.get("source") if isinstance(first_evidence, dict) else None
        source_ref = None
        if isinstance(first_evidence, dict):
            source_ref = (
                first_evidence.get("source_ref")
                or first_evidence.get("url")
                or first_evidence.get("id")
            )
        source_metadata.append({
            "domain": domain,
            "label": label,
            "source": evidence_source or audit_row.get("source") or default_source,
            "source_ref": source_ref or audit_row.get("source_ref"),
            "source_time": source_time,
            "source_status": audit_row.get("status") or ("PASS" if evidence else "MISSING"),
            "candidate_hit": bool(evidence),
            "evidence_count": len(evidence) if isinstance(evidence, list) else (1 if evidence else 0),
        })
    flow_hit = capital_flow not in ({}, None) or fund_flow not in (None, "")
    flow_audit = audit_sources.get("stock_capital_flow") or {}
    if not isinstance(flow_audit, dict):
        flow_audit = {}
    source_metadata.append({
        "domain": "stock_capital_flow",
        "label": "个股资金",
        "source": flow_audit.get("source") or "eastmoney_stock_capital_flow",
        "source_ref": flow_audit.get("source_ref"),
        "source_time": source_time,
        "source_status": flow_audit.get("status") or ("PASS" if flow_hit else "MISSING"),
        "candidate_hit": flow_hit,
        "evidence_count": 1 if flow_hit else 0,
    })
    source_metadata.append({
        "domain": "quote",
        "label": "行情结构",
        "source": raw.get("quote_truth_source") or "eastmoney_stock_all_a",
        "source_ref": raw.get("quote_truth_fields"),
        "source_time": source_time,
        "source_status": "PASS",
        "candidate_hit": True,
        "evidence_count": 1,
    })
    missing.extend(
        item["domain"]
        for item in source_metadata
        if item["domain"] != "quote" and not item["candidate_hit"]
    )
    for item in source_metadata:
        item["candidate_status"] = "PRESENT" if item["candidate_hit"] else "MISSING"
    # Research summary
    research_summary = {}
    if quality:
        research_summary["catalyst_category"] = quality.get("category")
        research_summary["usable_for_paper_pick"] = quality.get("usable_for_paper_pick")
    return {
        "basis_labels": list(dict.fromkeys(labels)),
        "basis_detail": "、".join(dict.fromkeys(labels)),
        "announcement_evidence": announcements[:5] if isinstance(announcements, list) else [],
        "direct_symbol_news": direct_news[:5] if isinstance(direct_news, list) else [],
        "policy_evidence": policy_evidence[:10] if isinstance(policy_evidence, list) else [],
        "sector_news_evidence": sector_news[:5] if isinstance(sector_news, list) else [],
        "limitup_reasons": limitup_reasons[:10] if isinstance(limitup_reasons, list) else [],
        "capital_flow_evidence": capital_flow if isinstance(capital_flow, dict) else {"value": capital_flow},
        "sentiment_evidence": {
            "sentiment_catalyst": sentiment,
            "social_sentiment_score": social_sentiment,
        },
        "risk_flags": [str(flag) for flag in risk_flags if flag][:10],
        "missing_domains": list(dict.fromkeys(missing)),
        "status": str(auxiliary.get("status") or row.get("auxiliary_evidence_status") or "UNKNOWN"),
        "source_time": source_time,
        "source_metadata": source_metadata,
        "consecutive_limit_count": consecutive_limit,
        "research_summary": research_summary,
        "one_liner": _first_json_value(containers, "one_liner"),
    }


def _candidate_evidence_report(row: Dict[str, Any]) -> Dict[str, Any]:
    """Expose the recorded T-day evidence needed to audit one production pick."""
    evidence = _candidate_entry_evidence(row)
    explanation = _candidate_selection_explanation(row)
    features = _as_json_object(row.get("candidate_features"))
    factor = _as_json_object(row.get("factor_snapshot"))
    eligibility = _as_json_object(row.get("eligibility_snapshot"))
    ranking = _as_json_object(row.get("ranking_basis"))
    raw = _as_json_object(row.get("raw_json"))
    research = _as_json_object(raw.get("research_signals"))
    quality = _as_json_object(research.get("catalyst_quality"))
    sector_mapping = _as_json_object(research.get("sector_mapping"))
    structured = _as_json_object(factor.get("structured_components"))
    ranking_components = (
        _as_json_object(raw.get("ranking_basis_adjustment_components"))
        or _as_json_object(ranking.get("ranking_basis_adjustment_components"))
        or factor
    )
    containers = [
        row,
        features,
        factor,
        structured,
        eligibility,
        ranking,
        ranking_components,
        quality,
        raw,
    ]

    def number(*keys: str) -> Optional[float]:
        value = _factor_number(containers, *keys)
        return _round_float(value, 4)

    def text(*keys: str, default: str = "") -> str:
        value = next(
            (
                container.get(key)
                for key in keys
                for container in containers
                if container.get(key) not in (None, "", [], {})
            ),
            default,
        )
        return str(value) if value not in (None, "") else default

    direct_inflow = number("replay_main_force_net_inflow", "net_inflow_main", "main_force_net_inflow")
    direct_inflow_source = (
        "daily_candidates.candidate_features.replay_main_force_net_inflow"
        if features.get("replay_main_force_net_inflow") is not None
        else "daily_candidates.candidate_features.net_inflow_main"
        if features.get("net_inflow_main") is not None
        else "缺失"
    )
    # Keep stock industry evidence separate from market/theme tags.  The
    # latter are global ranking context and must not be presented as the
    # candidate's own sector.
    sector_names = []
    for value in (
        raw.get("sector_name"),
        raw.get("sector"),
        raw.get("industry"),
        row.get("sector_name"),
        row.get("industry"),
    ):
        if value:
            sector_names.append(str(value))
    sector_names.extend(
        str(value)
        for value in sector_mapping.get("sectors") or []
        if value
    )
    sector_source = "recorded_stock_or_mapping" if sector_names else ""
    # Older snapshots did not promote the yesterday-limit-up industry's
    # `hybk` into a top-level field, but the raw evidence is still recorded.
    for container in (features, factor, raw):
        limitup_evidence = container.get("yesterday_limitup_gene_evidence")
        if not isinstance(limitup_evidence, dict):
            continue
        for record in limitup_evidence.get("records") or []:
            if not isinstance(record, dict) or not record.get("hybk"):
                continue
            sector_names.append(str(record["hybk"]))
            sector_source = "yesterday_limitup.hybk"
    sector_names = list(dict.fromkeys(sector_names))
    live_sector: Dict[str, Any] = {}
    if not sector_names:
        live_sector = _eastmoney_stock_sector_profile(str(row.get("symbol") or ""))
        sector_names.extend(str(value) for value in live_sector.get("names") or [] if value)
        if sector_names:
            sector_source = str(
                live_sector.get("source")
                or "eastmoney_stock_industry_profile"
            )
    sector_tags = (
        features.get("sector_opportunity_tags")
        or features.get("main_theme_alignment_tags")
        or quality.get("industry_chain_tags")
        or []
    )
    if not isinstance(sector_tags, list):
        sector_tags = [sector_tags]
    sector_tags = list(dict.fromkeys(str(value) for value in sector_tags if value))

    factor_rows = [
        {
            "key": "announcement_catalyst_score",
            "label": "事件催化",
            "value": number("announcement_catalyst_score", "news_catalyst_strength"),
            "meaning": "公告、政策或新闻对次日交易的催化强度",
        },
        {
            "key": "sector_attack_score",
            "label": "板块承载",
            "value": number("sector_attack_score", "sector_opportunity_score", "sector_catalyst_score"),
            "meaning": "所属板块的承载、扩散和主线强度",
        },
        {
            "key": "flow_confirmation_score",
            "label": "直接资金确认",
            "value": number("flow_confirmation_score", "capital_behavior_score", "fund_flow_momentum"),
            "meaning": "T日可观测的个股主力资金行为确认",
        },
        {
            "key": "t1_room_score",
            "label": "T+1空间",
            "value": number("t1_room_score", "low_position_catalyst_score", "close_position_score"),
            "meaning": "T日证据对应的次日可兑现空间",
        },
        {
            "key": "distribution_risk_score",
            "label": "派发风险",
            "value": number("distribution_risk_score", "capital_risk_profile"),
            "meaning": "资金派发和高位兑现风险，越低越好",
        },
    ]
    missing_domains = list(dict.fromkeys(
        list(evidence.get("missing_domains") or [])
        + list(features.get("mainboard_auxiliary_missing_domains") or [])
    ))
    proxy_limitup = [
        item for item in evidence.get("limitup_reasons") or []
        if isinstance(item, dict) and item.get("proxy")
    ]
    policy_evidence = evidence.get("policy_evidence") or []
    return {
        "trade_date": _iso(row.get("trade_date")),
        "source_time": evidence.get("source_time") or _candidate_source_time(row),
        "decision": "TRADING" if row.get("is_official_pick") else str(row.get("decision") or "NO_PICK"),
        "entry_price": _pick_entry_price(row),
        "catalyst": {
            "score": number("announcement_catalyst_score", "news_catalyst_strength"),
            "type": text("catalyst_type", default=str(quality.get("category") or "未记录")),
            "announcements": evidence.get("announcement_evidence") or [],
            "direct_symbol_news": evidence.get("direct_symbol_news") or [],
            "policy_evidence": policy_evidence[:10] if isinstance(policy_evidence, list) else [],
            "missing_types": [
                label for label, items in (
                    ("公告", evidence.get("announcement_evidence")),
                    ("政策", policy_evidence),
                    ("个股新闻", evidence.get("direct_symbol_news")),
                )
                if not items
            ],
            "missing": "direct_symbol_news" in missing_domains and not evidence.get("direct_symbol_news"),
        },
        "sectors": {
            "names": sector_names,
            "primary": sector_names[0] if sector_names else "缺失",
            "mapping_confidence": number("sector_mapping_confidence"),
            "source": sector_source or "missing_stock_sector_evidence",
            "industry_board": live_sector.get("industry_board") or "",
            "eastmoney_industry": live_sector.get("eastmoney_industry") or "",
            "sw_industry": live_sector.get("sw_industry") or "",
            "tags": sector_tags,
            "market_theme_tags": sector_tags,
            "theme_primary": sector_tags[0] if sector_tags else "缺失",
            "sector_news": evidence.get("sector_news_evidence") or [],
            "sector_news_score": number("sector_news_catalyst_score"),
            "sector_attack_score": number("sector_attack_score", "sector_opportunity_score"),
            "propagation_score": number("sector_propagation", "topic_propagation_score"),
            "support": "板块新闻和板块扩散证据" if evidence.get("sector_news_evidence") else "缺失",
        },
        "capital_flow": {
            "main_force_net_inflow_yuan": direct_inflow,
            "main_force_net_inflow_yi": _round_float(direct_inflow / 100_000_000, 4) if direct_inflow is not None else None,
            "unit": "元",
            "display_unit": "亿元",
            "source": direct_inflow_source,
            "data_time": evidence.get("source_time") or _candidate_source_time(row),
            "momentum": number("fund_flow_momentum"),
            "behavior_score": number("capital_behavior_score"),
            "confirmation_score": number("flow_confirmation_score"),
            "missing": direct_inflow is None,
        },
        "score_breakdown": factor_rows,
        "t1_space": {
            "score": number("t1_room_score"),
            "profit_edge_score": number("profit_edge_score"),
            "low_position_score": number("low_position_catalyst_score"),
            "close_position_score": number("close_position_score"),
            "entry_price": _pick_entry_price(row),
            "actual_t1_return": _dashboard_return(row.get("t1_return")),
            "basis": "T日收盘/建议入手价与低位、资金和板块延续证据共同推导",
        },
        "continuation": {
            "limitup_reason_quality_score": number("limitup_reason_quality_score"),
            "limitup_reason_evidence_count": len(evidence.get("limitup_reasons") or []),
            "reasons": evidence.get("limitup_reasons") or [],
            "proxy_only": bool(proxy_limitup),
            "proxy_status": text("limitup_reason_status", default="未记录"),
            "warning": "涨停原因是板块代理证据，不是个股直接原因" if proxy_limitup else "",
        },
        "risk": {
            "distribution_risk_score": number("distribution_risk_score"),
            "popularity_rank": number("popularity_best_rank", "popularity_rank"),
            "risk_flags": evidence.get("risk_flags") or [],
            "missing_domains": missing_domains,
            "counter_evidence": ranking_components.get("counter_evidence") or [],
        },
        "data_coverage": {
            "status": evidence.get("status") or "UNKNOWN",
            "sources": evidence.get("source_metadata") or [],
            "missing_domains": missing_domains,
        },
        "selection_explanation": explanation,
    }


def _dashboard_candidate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    public_fields = {
        "trade_date", "symbol", "stock_name", "rank", "final_score", "decision",
        "selection_outcome", "is_official_pick", "pct_chg", "close_position_score",
        "market_regime", "open_price", "close_price", "high_price", "low_price",
        "sentiment_catalyst", "theme_catalyst", "news_catalyst", "positive_catalyst",
        "fund_flow_momentum", "sector_catalyst_score",
    }
    output = {key: row.get(key) for key in public_fields if key in row}
    output["t1_return"] = _dashboard_return(row.get("t1_return"))
    output["t1_result_complete"] = bool(row.get("t1_result_complete"))
    output["t1_result_coverage"] = row.get("t1_result_coverage") or "0/0"
    output["t1_result_rank"] = row.get("t1_result_rank")
    output["t1_result_label"] = row.get("t1_result_label")
    output["pct_chg"] = _dashboard_return(
        row.get("pct_chg") if row.get("pct_chg") is not None else row.get("signal_pct")
    )
    output["auxiliary_status"] = (
        _as_json_object(row.get("auxiliary_evidence_snapshot")).get("status")
        or row.get("auxiliary_evidence_status")
        or "UNKNOWN"
    )
    output["entry_evidence"] = _candidate_entry_evidence(row)
    output["selection_explanation"] = _candidate_selection_explanation(row)
    output["selection_basis_cn"] = output["selection_explanation"]["中文摘要"]
    output["risk_gates_cn"] = output["selection_explanation"]["门禁"]
    # Entry price is the T-day executable reference shown to the operator.
    output["entry_price"] = row.get("entry_price") or row.get("close_price")
    # Catalyst fields: prefer top-level columns, fallback to candidate_features/raw_json
    raw = _as_json_object(row.get("raw_json"))
    feat = _as_json_object(row.get("candidate_features"))
    factor = _as_json_object(row.get("factor_snapshot"))
    eligibility = _as_json_object(row.get("eligibility_snapshot"))
    t1_profile = _as_json_object(raw.get("t1_profit_profile"))
    factor_containers = [row, feat, factor, eligibility, raw, t1_profile]
    research = _as_json_object(raw.get("research_signals"))
    quality = _as_json_object(research.get("catalyst_quality"))
    sent_cat = row.get("sentiment_catalyst") or quality.get("category") or ""
    theme_cat = row.get("theme_catalyst") or ",".join(
        (feat.get("sector_opportunity_tags") or feat.get("main_theme_alignment_tags") or [])[:3]
    )
    news_cat = row.get("news_catalyst") or str(feat.get("news_catalyst_strength") or "")
    output["sentiment_catalyst"] = str(sent_cat)
    output["theme_catalyst"] = str(theme_cat)
    output["news_catalyst"] = str(news_cat)
    output["positive_catalyst"] = row.get("positive_catalyst") or ""
    output["fund_flow_momentum"] = _factor_number(
        factor_containers,
        "fund_flow_momentum",
        "net_inflow_main",
    )
    output["sector_catalyst_score"] = _factor_number(
        factor_containers,
        "sector_catalyst_score",
    )
    output["expected_t1_profit_score"] = _factor_number(
        factor_containers,
        "expected_t1_profit_score",
    )
    production_score = _production_score_from_row(row)
    output["production_score"] = production_score
    output["score_source"] = "formal_t1_profit_components" if production_score is not None else "UNAVAILABLE"
    output["ranking_view"] = _PRODUCTION_CHAIN_NAME
    output["rank_source"] = _PRODUCTION_RANK_SOURCE
    if production_score is not None:
        output["final_score"] = production_score
    # Persisted selection payloads can contain old machine summaries. Keep only
    # the short decision sentence, translated when it is still a machine code.
    selection_reason = row.get("selection_reason")
    if isinstance(selection_reason, str) and selection_reason.startswith("{"):
        try:
            import json as _json
            selection_reason = _json.loads(selection_reason)
        except (TypeError, ValueError):
            selection_reason = {}
    persisted_reason = (
        selection_reason.get("decision_reason")
        if isinstance(selection_reason, dict)
        else selection_reason
    )
    output["decision_reason"] = (
        _cn_gate_reason(persisted_reason)
        if persisted_reason
        else output["selection_explanation"]["门禁中文"]
    )
    output["one_liner"] = "；".join(output["selection_explanation"]["中文摘要"][:4])
    # Consecutive limit-up: extract from candidate_features or raw_json
    cl = feat.get("consecutive_limit_count") or feat.get("consecutive_limitups") or feat.get("consecutive_limit_days")
    if cl is None:
        cl = raw.get("consecutive_limit_count") or raw.get("consecutive_limitups") or raw.get("consecutive_limit_days")
    output["consecutive_limit_count"] = cl
    output["decision_reason_cn"] = (
        output["selection_explanation"]["门禁中文"]
        if output["selection_explanation"]["门禁中文"] != "已通过正式门禁"
        else "通过正式门禁后按正式候选分排序"
    )
    return output


def _dashboard_pick_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a public pick row without persisted machine payloads."""
    features = _as_json_object(row.get("features"))
    candidate_features = _as_json_object(features.get("candidate_features"))
    candidate_row = {
        **row,
        **candidate_features,
        "candidate_features": candidate_features,
        "factor_snapshot": features.get("factor_snapshot") or {},
        "eligibility_snapshot": candidate_features.get("paper_pick_eligibility") or {},
        "raw_json": candidate_features,
    }
    output = {
        key: row.get(key)
        for key in (
            "id", "trade_date", "symbol", "stock_name", "decision",
            "final_score", "rank", "auxiliary_evidence_status",
        )
        if key in row
    }
    # PAPER_PICK remains the internal DB compatibility value. The operator
    # sees the production signal as TRADING.
    output["display_decision"] = "TRADING" if row.get("decision") == "PAPER_PICK" else row.get("decision")
    output["t1_return"] = _dashboard_return(row.get("t1_return"))
    output["entry_price"] = (
        candidate_features.get("entry_price")
        or candidate_features.get("price")
        or candidate_features.get("signal_close")
    )
    explanation = _candidate_selection_explanation(candidate_row)
    output["selection_explanation"] = explanation
    output["selection_basis_cn"] = explanation["中文摘要"]
    output["risk_gates_cn"] = explanation["门禁"]
    output["decision_reason"] = explanation["门禁中文"]
    output["expected_t1_profit_score"] = _factor_number(
        [
            candidate_row,
            _as_json_object(candidate_row.get("factor_snapshot")),
            _as_json_object(candidate_row.get("eligibility_snapshot")),
            _as_json_object(candidate_features.get("t1_profit_profile")),
        ],
        "expected_t1_profit_score",
    )
    return output


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round_float(value: Any, digits: int = 2) -> Optional[float]:
    number = _to_float(value)
    return round(number, digits) if number is not None else None


def _score_to_stars(value: Any) -> int:
    number = _to_float(value)
    if number is None:
        return 0
    return max(1, min(5, int(round(number / 20.0))))


def _percent_from_decimal(value: Any) -> Optional[float]:
    number = _to_float(value)
    return round(number * 100.0, 2) if number is not None else None


def _production_score_from_row(row: Dict[str, Any]) -> Optional[float]:
    """Read only the score stamped by the main-force production ranker."""
    ranking_basis = _as_json_object(row.get("ranking_basis"))
    raw = _as_json_object(row.get("raw_json"))
    features = _as_json_object(row.get("candidate_features"))
    for container in (row, features, ranking_basis, raw):
        for key in ("production_score", "formal_primary_score"):
            value = _to_float(container.get(key))
            if value is not None:
                return round(value, 4)
    rank_source = (
        str(row.get("rank_source") or "").strip()
        or str(ranking_basis.get("rank_source") or "").strip()
        or str(raw.get("rank_source") or "").strip()
    )
    if rank_source == "formal_profit_first":
        value = _to_float(row.get("final_score"))
        return round(value, 4) if value is not None else None
    return None


def _stock_name(row: Dict[str, Any]) -> str:
    name = str(row.get("stock_name") or "").strip()
    if name and name.upper() != "NO_PICK":
        return name
    return str(row.get("symbol") or "").strip()


def _pick_entry_price(row: Dict[str, Any]) -> Optional[float]:
    features = _as_json_object(row.get("features"))
    candidate_features = _as_json_object(features.get("candidate_features"))
    candidate_features.update(_as_json_object(row.get("candidate_features")))
    raw = _as_json_object(row.get("raw_json"))
    for value in (
        row.get("close_price"),
        row.get("entry_price"),
        candidate_features.get("entry_price"),
        candidate_features.get("price"),
        candidate_features.get("signal_close"),
        candidate_features.get("close"),
        candidate_features.get("last_price"),
        raw.get("entry_price"),
        raw.get("price"),
        raw.get("signal_close"),
    ):
        number = _to_float(value)
        if number and number > 0:
            return round(number, 4)
    return None


def _board_lot_shares(cash_budget: float, price: Optional[float]) -> int:
    if not price or price <= 0 or cash_budget <= 0:
        return 0
    return int(math.floor(cash_budget / (price * 100.0)) * 100)


def _paper_trade_rows() -> List[Dict[str, Any]]:
    return query_rows("""
        SELECT
            p.id, p.trade_date, p.symbol, p.stock_name, p.final_score,
            p.rank, p.features, p.selection_reason,
            p.ticket_reason, p.ranking_basis AS pick_ranking_basis,
            p.created_at, dc.close_price, dc.open_price,
            dc.candidate_features, dc.raw_json, dc.ranking_basis AS candidate_ranking_basis,
            COALESCE(
                dc.candidate_features ->> 'score_source',
                dc.raw_json ->> 'score_source',
                dc.ranking_basis ->> 'score_source'
            ) AS score_source,
            COALESCE(
                dc.candidate_features ->> 'ranking_view',
                dc.raw_json ->> 'ranking_view',
                dc.ranking_basis ->> 'ranking_view'
            ) AS ranking_view,
            COALESCE(
                dc.candidate_features ->> 'rank_source',
                dc.raw_json ->> 'rank_source',
                dc.ranking_basis ->> 'rank_source'
            ) AS rank_source,
            r.t1_return,
            r.t1_return_close,
            r.t1_return_high,
            r.next_day_open_return,
            r.next_day_high_return,
            r.next_day_low_return,
            r.next_day_drawdown,
            r.high_to_close_retrace
        FROM picks p
        LEFT JOIN daily_candidates dc
          ON dc.production_run_id = p.production_run_id AND dc.symbol = p.symbol
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        WHERE p.decision = 'PAPER_PICK'
          AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
          AND p.symbol IS NOT NULL
          AND p.symbol <> ''
        ORDER BY p.trade_date, p.created_at, p.id
    """)


def _paper_trade_chain_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a recorded pick without rewriting its historical score."""
    pick_features = _as_json_object(row.get("features"))
    pick_candidate_features = _as_json_object(pick_features.get("candidate_features"))
    candidate_features = _as_json_object(row.get("candidate_features"))
    candidate_raw = _as_json_object(row.get("raw_json"))
    pick_ranking = _as_json_object(row.get("pick_ranking_basis"))
    candidate_ranking = _as_json_object(row.get("candidate_ranking_basis"))
    if not candidate_ranking:
        candidate_ranking = _as_json_object(row.get("ranking_basis"))
    snapshot_context = _as_json_object(
        candidate_ranking.get("candidate_pool_context")
    )
    containers = [
        row,
        candidate_ranking,
        candidate_raw,
        candidate_features,
        snapshot_context,
        pick_ranking,
        pick_candidate_features,
        pick_features,
    ]
    ranking_view = str(_first_json_value(containers, "ranking_view", "") or "").strip()
    rank_source = str(_first_json_value(containers, "rank_source", "") or "").strip()
    score_source = str(_first_json_value(containers, "score_source", "") or "").strip()
    production_score = _to_float(
        _first_json_value(
            containers,
            "production_score",
            _first_json_value(containers, "formal_primary_score"),
        )
    )
    formal_rank = _to_float(_first_json_value(containers, "formal_rank"))
    recorded_score = _to_float(row.get("final_score"))
    recorded_rank = _to_float(row.get("rank"))
    score_matches = (
        production_score is not None
        and recorded_score is not None
        and abs(production_score - recorded_score) <= 0.01
    )
    rank_matches = (
        formal_rank is not None
        and recorded_rank is not None
        and formal_rank == recorded_rank
    )
    formal_production = (
        ranking_view == _PRODUCTION_CHAIN_NAME
        and rank_source == _PRODUCTION_RANK_SOURCE
        and score_source == "formal_t1_profit_components"
        and score_matches
        and rank_matches
    )
    status = "formal_production" if formal_production else "historical_unverified"
    return {
        "chain_status": status,
        "chain_name": ranking_view or None,
        "score_source": score_source or "UNAVAILABLE",
        "ranking_view": ranking_view or "UNAVAILABLE",
        "rank_source": rank_source or "UNAVAILABLE",
        "production_score": round(production_score, 4) if production_score is not None else None,
        "formal_rank": int(formal_rank) if formal_rank is not None and formal_rank.is_integer() else formal_rank,
        "formal_rank_snapshot_id": _first_json_value(
            containers, "formal_rank_snapshot_id"
        ),
        "chain_audit": {
            "score_matches": score_matches,
            "rank_matches": rank_matches,
            "reason": (
                "正式链字段、正式分和正式排名一致"
                if formal_production
                else "历史记录缺少正式链快照或分数/排名与正式链不一致"
            ),
        },
    }


def _trade_selection_reason_cn(row: Dict[str, Any]) -> str:
    """Build a compact Chinese explanation from the persisted pick evidence."""
    selection = row.get("selection_reason")
    if isinstance(selection, str):
        try:
            parsed = json.loads(selection)
            selection = parsed if isinstance(parsed, dict) else selection
        except (TypeError, ValueError):
            pass
    ticket_reason = row.get("ticket_reason")
    if isinstance(ticket_reason, str):
        try:
            parsed = json.loads(ticket_reason)
            ticket_reason = parsed if isinstance(parsed, dict) else ticket_reason
        except (TypeError, ValueError):
            pass

    reasons: List[str] = []
    if isinstance(selection, dict):
        reasons.extend(
            str(value)
            for value in selection.get("why_selected") or []
            if value
        )
        evidence_card = _as_json_object(selection.get("evidence_card"))
        reasons.extend(str(value) for value in evidence_card.get("fund_flow") or [] if value)
        reasons.extend(str(value) for value in evidence_card.get("profit_evidence") or [] if value)
        reasons.extend(str(value) for value in evidence_card.get("main_theme") or [] if value)
        decision_reason = selection.get("decision_reason")
        if decision_reason:
            reasons.append(_cn_gate_reason(decision_reason))
    elif selection:
        reasons.append(_cn_gate_reason(selection))

    if isinstance(ticket_reason, dict) and ticket_reason.get("reason"):
        reasons.append(_cn_gate_reason(ticket_reason["reason"]))
    elif ticket_reason:
        reasons.append(_cn_gate_reason(ticket_reason))

    deduped: List[str] = []
    for value in reasons:
        text = _cn_gate_reason(value)
        if text and text not in deduped:
            deduped.append(text)
    return "；".join(deduped[:8]) or "主力行为链正式出票；已记录 T+1 收盘收益"


def _trade_history_base(row: Dict[str, Any]) -> Dict[str, Any]:
    chain = _paper_trade_chain_metadata(row)
    production_score = chain.get("production_score")
    formal_rank = chain.get("formal_rank")
    score = production_score if production_score is not None else _to_float(row.get("final_score"))
    rank = formal_rank if formal_rank is not None else row.get("rank")
    return {
        **chain,
        "id": f"T{row.get('id')}",
        "date": _iso(row.get("trade_date")),
        "trade_date": _iso(row.get("trade_date")),
        "symbol": row.get("symbol"),
        "name": _stock_name(row),
        "direction": "BUY",
        "price": _pick_entry_price(row),
        "final_score": _round_float(score, 4),
        "production_score": _round_float(production_score, 4),
        "rank": rank,
        "formal_rank": formal_rank,
        "selection_reason_cn": _trade_selection_reason_cn(row),
        "selection_explanation": _candidate_selection_explanation({
            **row,
            "candidate_features": _as_json_object(row.get("candidate_features")),
            "raw_json": _as_json_object(row.get("raw_json")),
            "ranking_basis": _as_json_object(row.get("candidate_ranking_basis")),
        }),
        "evidenceReport": _candidate_evidence_report(row),
        "t1_return": _to_float(row.get("t1_return")),
        "t1_return_close": _to_float(row.get("t1_return_close")),
        "t1_return_high": _to_float(row.get("t1_return_high")),
        "next_day_open_return": _to_float(row.get("next_day_open_return")),
        "next_day_high_return": _to_float(row.get("next_day_high_return")),
        "next_day_low_return": _to_float(row.get("next_day_low_return")),
        "next_day_drawdown": _to_float(row.get("next_day_drawdown")),
        "high_to_close_retrace": _to_float(row.get("high_to_close_retrace")),
    }


def _simulate_paper_portfolio(initial_capital: Optional[float] = None) -> Dict[str, Any]:
    """Summarize recorded T+1 results without inventing an account balance."""
    rows = _paper_trade_rows()
    has_capital = initial_capital is not None and float(initial_capital) > 0
    capital = float(initial_capital) if has_capital else 0.0
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    skipped_trades = 0
    trade_history: List[Dict[str, Any]] = []
    equity_curve = [{"date": "start", "value": round(capital, 2)}]
    day_returns: List[float] = []
    latest_filled_date = None

    grouped: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("trade_date"), []).append(row)

    for trade_date in sorted(grouped):
        day_rows = [row for row in grouped[trade_date] if _to_float(row.get("t1_return")) is not None]
        if not day_rows:
            continue
        start_capital = capital
        cash = capital
        allocation = start_capital / len(day_rows)
        day_trades = 0
        for row in day_rows:
            price = _pick_entry_price(row)
            t1_return = float(row["t1_return"])
            if not has_capital:
                total_trades += 1
                if t1_return > 0:
                    winning_trades += 1
                else:
                    losing_trades += 1
                trade_history.append({
                    **_trade_history_base(row),
                    "shares": None,
                    "amount": None,
                    "reason": _trade_selection_reason_cn(row),
                    "executionReason": "未提供账户资金；仅记录主力行为链 T+1 收益，不作可买性判断",
                    "aiJudgment": f"score={_round_float(row.get('final_score'), 2)}",
                    "result": _percent_from_decimal(t1_return),
                    "review": f"T+1 收盘收益 {_percent_from_decimal(t1_return)}%",
                })
                continue
            shares = _board_lot_shares(allocation, price)
            cost = round(shares * price, 2) if price else 0.0
            pnl = round(cost * t1_return, 2)
            if shares <= 0 or cost <= 0:
                skipped_trades += 1
                trade_history.append({
                    **_trade_history_base(row),
                    "shares": 0,
                    "amount": 0,
                    "reason": _trade_selection_reason_cn(row),
                    "executionReason": "账户资金不足一手；真实记录保留，未形成模拟成交",
                    "aiJudgment": f"score={_round_float(row.get('final_score'), 2)}",
                    "result": _percent_from_decimal(t1_return),
                    "review": "真实记录保留；模拟成交为 0 股",
                })
                continue
            cash -= cost
            cash += cost + pnl
            day_trades += 1
            total_trades += 1
            if t1_return > 0:
                winning_trades += 1
            else:
                losing_trades += 1
            trade_history.append({
                **_trade_history_base(row),
                "shares": shares,
                "amount": cost,
                "reason": _trade_selection_reason_cn(row),
                "executionReason": "数据库 TRADING；T 日买入，T+1 收盘退出",
                "aiJudgment": f"score={_round_float(row.get('final_score'), 2)} rank={row.get('rank')}",
                "result": _percent_from_decimal(t1_return),
                "review": f"T+1 收盘收益 {_percent_from_decimal(t1_return)}%，盈亏 {pnl:+.2f} 元",
            })
        capital = round(cash, 2)
        if day_trades:
            latest_filled_date = trade_date
            day_returns.append((capital / start_capital - 1.0) if start_capital else 0.0)
            equity_curve.append({"date": _iso(trade_date), "value": capital})

    pending_rows = [
        row for row in rows
        if _to_float(row.get("t1_return")) is None
        and row.get("trade_date") is not None
        and (latest_filled_date is None or row.get("trade_date") > latest_filled_date)
    ]
    latest_pending_date = max((row.get("trade_date") for row in pending_rows), default=None)
    current_rows = [
        row for row in pending_rows
        if latest_pending_date is not None and row.get("trade_date") == latest_pending_date
    ]

    positions = []
    cash = capital
    allocation = capital / len(current_rows) if current_rows else 0.0
    for row in current_rows:
        trade_base = _trade_history_base(row)
        price = _pick_entry_price(row)
        shares = _board_lot_shares(allocation, price)
        cost = round(shares * price, 2) if price else 0.0
        cash -= cost
        status = "WATCH" if shares <= 0 else "HOLD"
        reason = (
            "等待真实 T+1 收益回填；按最新 TRADING 记录展示"
            if shares > 0
            else "账户资金不足一手；未形成模拟持仓"
        )
        positions.append({
            "symbol": row.get("symbol"),
            "name": _stock_name(row),
            "shares": shares,
            "avgCost": price or 0,
            "currentPrice": price or 0,
            "marketValue": cost,
            "pnl": 0,
            "pnlPercent": 0,
            "aiStatus": status,
            "aiReason": reason,
            "tradeDate": _iso(row.get("trade_date")),
            "score": trade_base.get("final_score"),
            "rank": trade_base.get("rank"),
            "formalRank": trade_base.get("formal_rank"),
            "chainStatus": trade_base.get("chain_status"),
            "chainName": trade_base.get("chain_name"),
            "scoreSource": trade_base.get("score_source"),
            "selectionReason": trade_base.get("selection_reason_cn"),
            "selectionExplanation": trade_base.get("selection_explanation"),
            "evidenceReport": trade_base.get("evidenceReport"),
        })

    stock_value = round(sum(float(pos["marketValue"]) for pos in positions), 2)
    total_assets = round(cash + stock_value, 2)
    total_pnl = round(total_assets - initial_capital, 2) if has_capital else None
    total_return = round((total_assets / initial_capital - 1.0) * 100.0, 2) if has_capital else None
    peak = float(initial_capital) if has_capital else 0.0
    max_drawdown = 0.0
    for point in equity_curve[1:] + ([{"value": total_assets}] if positions else []):
        value = float(point["value"])
        peak = max(peak, value)
        if peak:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100.0)
    current_drawdown = ((peak - total_assets) / peak * 100.0) if peak else 0.0
    avg_day = sum(day_returns) / len(day_returns) if day_returns else 0.0
    variance = (
        sum((value - avg_day) ** 2 for value in day_returns) / (len(day_returns) - 1)
        if len(day_returns) > 1 else 0.0
    )
    sharpe = round((avg_day / math.sqrt(variance) * math.sqrt(252)) if variance > 0 else 0.0, 2)

    monthly: Dict[str, Dict[str, float]] = {}
    for trade in trade_history:
        result = _to_float(trade.get("result"))
        if result is None:
            continue
        month = str(trade["date"])[:7]
        bucket = monthly.setdefault(month, {"return": 0.0, "count": 0})
        bucket["return"] += result
        bucket["count"] += 1
    monthly_data = [
        {"month": month[-2:] + "月", "return": round(stats["return"] / max(stats["count"], 1), 2)}
        for month, stats in sorted(monthly.items())
    ]

    return {
        "initialCapital": round(initial_capital, 2) if has_capital else None,
        "capitalSource": "request_parameter" if has_capital else "UNAVAILABLE",
        "totalAssets": total_assets if has_capital else None,
        "cash": round(cash, 2),
        "stockValue": stock_value if has_capital else None,
        "totalPnl": total_pnl,
        "totalPnlPercent": total_return,
        "closedCapital": capital,
        "positions": positions,
        "allocation": [
            *[
                {"name": pos["name"], "value": pos["marketValue"]}
                for pos in positions
                if pos["marketValue"] > 0
            ],
            {"name": "现金", "value": round(max(cash, 0), 2)},
        ],
        "equityCurve": equity_curve[1:] if has_capital else [],
        "tradeHistory": list(reversed(trade_history[-50:])),
        "monthlyData": monthly_data,
        "stats": {
            "runningDays": len(grouped),
            "totalReturn": total_return,
            "winRate": round(winning_trades / total_trades * 100.0, 1) if total_trades else 0.0,
            "maxDrawdown": round(-max_drawdown, 2),
            "sharpeRatio": sharpe,
            "totalTrades": total_trades,
            "winningTrades": winning_trades,
            "losingTrades": losing_trades,
            "skippedTrades": skipped_trades,
        },
        "riskMetrics": {
            "overall": "LOW" if current_drawdown < 5 and stock_value <= total_assets * 0.6 else "MEDIUM",
            "marketRisk": min(100, int(abs(max_drawdown) * 5)),
            "concentrationRisk": min(100, int((stock_value / total_assets * 100) if total_assets else 0)),
            "volatilityRisk": min(100, int((math.sqrt(variance) * 1000) if variance else 0)),
            "liquidityRisk": 20 if positions else 0,
            "currentDrawdown": round(-current_drawdown, 2),
            "maxDrawdown": round(-max_drawdown, 2),
            "var95": round(-1.65 * math.sqrt(variance) * 100, 2) if variance else 0,
        },
        "latestPendingDate": _iso(latest_pending_date),
        "pendingRecords": [
            {
                "date": _iso(row.get("trade_date")),
                "symbol": row.get("symbol"),
                "name": _stock_name(row),
                "score": _round_float(row.get("final_score"), 2),
                "entryPrice": _pick_entry_price(row),
                "rank": row.get("rank"),
                "chainStatus": _paper_trade_chain_metadata(row).get("chain_status"),
                "selectionReason": _trade_selection_reason_cn(row),
            }
            for row in pending_rows
        ],
        }


def _replay_sample_reason(row: Dict[str, Any]) -> str:
    """Explain a replay sample using only persisted candidate evidence."""
    selection_reason = str(row.get("selection_reason") or "").strip()
    legacy_markers = (
        "NO_PICK_FALLBACK",
        "FALLBACK",
        "legacy",
        "回退",
        "最高分",
    )
    features = _as_json_object(row.get("candidate_features"))
    raw = _as_json_object(row.get("raw_json"))
    research = _as_json_object(raw.get("research_signals"))
    adversarial = _as_json_object(research.get("adversarial_review"))
    flags = [
        str(value)
        for value in adversarial.get("bear_case_flags") or []
        if value
    ]
    reasons: List[str] = []
    if selection_reason and not any(marker in selection_reason for marker in legacy_markers):
        reasons.append(_cn_gate_reason(selection_reason))
    if (_to_float(features.get("replay_main_force_net_ratio")) or 0) <= 0:
        reasons.append("直接主力资金确认不足")
    if (_to_float(features.get("news_catalyst_strength")) or 0) <= 0:
        reasons.append("缺少新闻/公告催化")
    if flags:
        reasons.append("对抗复核：" + "、".join(_cn_gate_reason(flag) for flag in flags[:3]))
    return "；".join(dict.fromkeys(reasons)) or "回放未记录可解释选股理由"


def _enrich_chain_replay(replay: Dict[str, Any]) -> Dict[str, Any]:
    """Attach names, entry data, scores, and persisted reasons to replay rows."""
    samples = replay.get("settledSamples")
    if not isinstance(samples, list) or not samples:
        return replay
    replay_keys = [
        (str(sample.get("trade_date") or ""), str(sample.get("symbol") or ""))
        for sample in samples
        if sample.get("trade_date") and sample.get("symbol")
    ]
    if not replay_keys:
        return replay
    try:
        pair_params: Dict[str, Any] = {}
        pair_clauses = []
        for index, (trade_date, symbol) in enumerate(replay_keys):
            date_key = f"trade_date_{index}"
            symbol_key = f"symbol_{index}"
            pair_clauses.append(
                f"(dc.trade_date::text = :{date_key} AND dc.symbol = :{symbol_key})"
            )
            pair_params[date_key] = trade_date
            pair_params[symbol_key] = symbol
        detail_rows = query_rows("""
            SELECT
                dc.trade_date::text AS trade_date,
                dc.symbol,
                dc.stock_name,
                dc.rank,
                dc.final_score,
                dc.close_price,
                dc.pct_chg,
                dc.signal_pct,
                dc.selection_reason,
                dc.candidate_features,
                dc.raw_json,
                r.t1_return,
                r.t1_return_high,
                r.next_day_low_return,
                r.next_day_drawdown,
                r.high_to_close_retrace
            FROM daily_candidates dc
            JOIN production_run_active pra
              ON pra.trade_date = dc.trade_date
             AND pra.production_run_id = dc.production_run_id
            LEFT JOIN returns r
              ON r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol
            WHERE """ + " OR ".join(pair_clauses), pair_params)
    except Exception:
        return replay

    details_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in detail_rows:
        key = (str(row.get("trade_date") or ""), str(row.get("symbol") or ""))
        if key not in details_by_key:
            details_by_key[key] = row

    enriched_samples = []
    for sample in samples:
        item = dict(sample)
        detail = details_by_key.get(
            (str(sample.get("trade_date") or ""), str(sample.get("symbol") or ""))
        )
        if detail:
            detail_features = _as_json_object(detail.get("candidate_features"))
            detail_raw = _as_json_object(detail.get("raw_json"))
            ranking_basis = _as_json_object(detail.get("ranking_basis"))
            canonical_score = _to_float(
                detail_features.get("production_score")
                or detail_features.get("formal_primary_score")
                or ranking_basis.get("production_score")
                or ranking_basis.get("formal_primary_score")
            )
            persisted_reason = str(detail.get("selection_reason") or "").strip()
            has_legacy_reason = any(
                marker in persisted_reason
                for marker in ("NO_PICK_FALLBACK", "FALLBACK", "legacy", "回退", "最高分")
            )
            entry_price = _pick_entry_price({
                "close_price": detail.get("close_price"),
                "entry_price": detail.get("entry_price"),
                "features": {"candidate_features": detail_features},
                "candidate_features": detail_features,
                "raw_json": detail_raw,
            })
            item.update({
                "stock_name": detail.get("stock_name") or sample.get("symbol"),
                "final_score": canonical_score,
                "entry_price": entry_price,
                "pct_chg": detail.get("pct_chg") or detail.get("signal_pct") or detail_raw.get("signal_pct"),
                "selection_reason": (
                    ""
                    if has_legacy_reason
                    else persisted_reason or detail_raw.get("decision_reason") or ""
                ),
                "reason_summary": _replay_sample_reason(detail),
                "t1_return_high": detail.get("t1_return_high"),
                "next_day_low_return": detail.get("next_day_low_return"),
                "next_day_drawdown": detail.get("next_day_drawdown"),
                "high_to_close_retrace": detail.get("high_to_close_retrace"),
            })
        else:
            item.setdefault("stock_name", sample.get("symbol"))
            item.setdefault("reason_summary", "回放未找到对应候选证据")
        enriched_samples.append(item)

    output = dict(replay)
    output["settledSamples"] = enriched_samples
    drawdown_detail = dict(output.get("max_drawdown_detail") or {})
    for sample in enriched_samples:
        if (
            sample.get("trade_date") == drawdown_detail.get("trade_date")
            and sample.get("symbol") == drawdown_detail.get("symbol")
        ):
            drawdown_detail.update(sample)
            break
    if drawdown_detail:
        drawdown_detail["loss_reason"] = (
            drawdown_detail.get("reason_summary")
            or drawdown_detail.get("selection_reason")
            or "回放未记录可解释亏损原因"
        )
    output["max_drawdown_detail"] = drawdown_detail
    return output


def _latest_chain_system_stats() -> Dict[str, Any]:
    """Expose only the validated replay score for the active production chain."""
    replay = _load_latest_chain_replay()
    return {
        "runningDays": replay["window"]["trading_days"],
        "totalReturn": replay["average_t1_return"],
        "winRate": replay["win_rate"],
        "maxDrawdown": replay["max_drawdown"],
        "sharpeRatio": 0.0,
        "totalTrades": replay["sample_count"],
        "winningTrades": replay["winning_samples"],
        "losingTrades": replay["losing_samples"],
    }


@app.get("/api/dashboard/overview")
def get_dashboard_overview(
    date: Optional[str] = Query(None, description="A-share trade date YYYY-MM-DD"),
    production_run_id: Optional[str] = None,
):
    """Single read model for the A-share operator dashboard."""
    active_run = _resolve_production_run(date, production_run_id)
    if not active_run:
        raise HTTPException(status_code=404, detail="No active production run found")
    active_run_id = str(active_run["production_run_id"])
    active_trade_date = _iso(active_run.get("trade_date"))
    dates = _resolve_dashboard_dates(active_trade_date)
    scan_date = dates["scan_date"]
    candidate_date = dates["candidate_date"]
    pick_date = dates["pick_date"]

    session_rows = query_rows("""
        SELECT id, trade_date, scan_time, quotes_count, scored_count, passed_count,
               status, market_snapshot, source_status, source_counts,
               source_diagnostics
        FROM scan_sessions
        WHERE production_run_id = :production_run_id
          AND (:scan_date IS NULL OR trade_date = CAST(:scan_date AS date))
        ORDER BY
          CASE WHEN market_snapshot IS NOT NULL
                AND market_snapshot::text <> '{}'
                AND market_snapshot::text <> 'null'
               THEN 0 ELSE 1 END,
          trade_date DESC, scan_time DESC
        LIMIT 1
    """, {"scan_date": scan_date, "production_run_id": active_run_id})
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
            dc.pct_chg, dc.signal_pct, dc.close_position_score, dc.market_regime,
            dc.auxiliary_evidence_snapshot, dc.selection_reason,
            dc.ticket_reason, dc.not_selected_reason,
            dc.candidate_features, dc.factor_snapshot, dc.ranking_basis,
            dc.eligibility_snapshot, dc.selection_diagnostics, dc.source_layers,
            dc.candidate_entry_reason, dc.raw_json, dc.blockers,
            dc.open_price, dc.close_price, dc.high_price, dc.low_price,
            dc.sentiment_catalyst, dc.theme_catalyst, dc.news_catalyst, dc.positive_catalyst,
            dc.fund_flow_momentum, dc.sector_catalyst_score,
            r.t1_return AS t1_return
        FROM daily_candidates dc
        LEFT JOIN returns r
          ON r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol
        WHERE dc.production_run_id = :production_run_id
          AND (:candidate_date IS NULL OR dc.trade_date = CAST(:candidate_date AS date))
          AND dc.pct_chg IS NOT NULL
            AND dc.pct_chg < 9.5
          AND dc.rank <= 10
          AND COALESCE(dc.raw_json -> 'research_signals' -> 'catalyst_quality' ->> 'regulatory_hard_block', 'false') <> 'true'
          AND COALESCE(dc.raw_json -> 'research_signals' -> 'a_share_risk_review' ->> 'disqualified_for_paper_pick', 'false') <> 'true'
        ORDER BY dc.rank NULLS LAST, dc.final_score DESC NULLS LAST
        LIMIT 10
    """, {"candidate_date": candidate_date, "production_run_id": active_run_id})

    pool_rows = query_rows("""
        SELECT COALESCE(selection_outcome, decision, 'UNKNOWN') AS outcome, COUNT(*) AS count
        FROM daily_candidates
        WHERE production_run_id = :production_run_id
          AND (:candidate_date IS NULL OR trade_date = CAST(:candidate_date AS date))
          AND pct_chg IS NOT NULL
          AND pct_chg < 9.5
          AND COALESCE(raw_json -> 'research_signals' -> 'catalyst_quality' ->> 'regulatory_hard_block', 'false') <> 'true'
          AND COALESCE(raw_json -> 'research_signals' -> 'a_share_risk_review' ->> 'disqualified_for_paper_pick', 'false') <> 'true'
        GROUP BY 1
        ORDER BY count DESC
    """, {"candidate_date": candidate_date, "production_run_id": active_run_id})

    raw_pick_rows = query_rows("""
        SELECT
            p.id, p.trade_date, p.symbol, p.stock_name, p.decision,
            p.final_score, p.rank, p.auxiliary_evidence_status,
            p.ticket_reason, p.selection_reason, p.created_at, p.features,
            r.t1_return AS t1_return
        FROM picks p
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
        WHERE p.production_run_id = :production_run_id
          AND (:pick_date IS NULL OR p.trade_date = CAST(:pick_date AS date))
          AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 20
    """, {"pick_date": pick_date, "production_run_id": active_run_id})
    paper_pick_raw = next(
        (row for row in raw_pick_rows if row.get("decision") == "PAPER_PICK"),
        None,
    )
    pick_rows = [_dashboard_pick_row(row) for row in raw_pick_rows]

    recent_picks = query_rows("""
        SELECT
            p.trade_date, p.symbol, p.stock_name, p.decision, p.final_score,
            p.rank, r.t1_return AS t1_return
        FROM picks p
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
        WHERE p.production_run_id = :production_run_id
          AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        ORDER BY p.trade_date DESC, p.created_at DESC
        LIMIT 12
    """, {"production_run_id": active_run_id})
    run_steps = query_rows("""
        SELECT step_name, status, required, started_at, completed_at,
               error_message, retry_command, metadata
        FROM production_run_steps
        WHERE production_run_id = :production_run_id
        ORDER BY step_name
    """, {"production_run_id": active_run_id})

    paper_pick = next((row for row in pick_rows if row.get("decision") == "PAPER_PICK"), None)
    latest_decision = pick_rows[0] if pick_rows else None
    # Extract entry price from paper_pick features
    paper_pick_entry_price = None
    if paper_pick_raw and isinstance(paper_pick_raw.get("features"), dict):
        feat = paper_pick_raw["features"]
        candidate_feat = feat.get("candidate_features") or {}
        paper_pick_entry_price = candidate_feat.get("entry_price") or candidate_feat.get("price") or candidate_feat.get("signal_close")
    if paper_pick_entry_price is None and paper_pick:
        paper_pick_entry_price = paper_pick.get("entry_price")
    filled = [row for row in recent_picks if row.get("t1_return") is not None]
    wins = [row for row in filled if float(row["t1_return"]) >= 0]
    limit_ups = [row for row in filled if float(row["t1_return"]) >= 0.095]
    average_t1 = (
        round(sum(float(row["t1_return"]) for row in filled) / len(filled), 4)
        if filled else None
    )
    pending_picks = [
        row for row in recent_picks
        if row.get("decision") == "PAPER_PICK"
        and row.get("trade_date") is not None
        and row.get("t1_return") is None
    ]
    latest_pending_date = max(
        (row.get("trade_date") for row in pending_picks),
        default=None,
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
        "display_mode": "TRADING",
        "execution_mode": "SIGNAL_ONLY",
        "trading_enabled": False,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "dates": dates,
        "production_run_id": active_run_id,
        "candidate_snapshot_id": active_run.get("candidate_snapshot_id"),
        "active_pick_id": active_run.get("active_pick_id"),
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
            "trading": paper_pick,
            "paper_pick_entry_price": paper_pick_entry_price,
            "trading_entry_price": paper_pick_entry_price,
            "candidate_date": candidate_date,
            "candidate_count": len(candidate_rows),
            "pool_outcomes": pool_rows,
        },
        "candidates": _t1_result_annotations(
            [_dashboard_candidate_row(row) for row in candidate_rows]
        ),
        "performance": {
            "filled": len(filled),
            "wins": len(wins),
            "limit_ups": len(limit_ups),
            "win_rate": round(len(wins) / len(filled) * 100, 1) if filled else None,
            "average_t1": average_t1,
        },
        "settlement": {
            "status": "PENDING_T1" if pending_picks else "COMPLETE",
            "latestPendingDate": _iso(latest_pending_date),
            "pendingCount": len(pending_picks),
            "pendingRecords": [
                {
                    "date": _iso(row.get("trade_date")),
                    "symbol": row.get("symbol"),
                    "name": row.get("stock_name"),
                    "score": _round_float(row.get("final_score"), 2),
                    "rank": row.get("rank"),
                }
                for row in pending_picks
            ],
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
        "run_steps": run_steps,
        "rules": {
            "production_policy": "T日买入，T+1日交易并以获利为唯一目标",
            "official_return_field": "T+1收盘收益",
            "candidate_policy": "主力行为链按事件催化、板块承载、直接资金确认、T+1空间和派发风险排序",
            "limitup_policy": "当日涨停/封死不可交易标的不得进入可交易候选池",
            "underwater_policy": "T 日下跌票不直接排除，必须通过最新主力行为链门禁",
            "sszcw": "软上下文，仅作解释与排序辅助，不强制出票",
        },
    }


def _os_candidate_rows(limit: int = 10) -> List[Dict[str, Any]]:
    rows = query_rows("""
        SELECT
            dc.trade_date, dc.symbol, dc.stock_name, dc.rank, dc.final_score,
            dc.decision, dc.selection_outcome, dc.is_official_pick,
            dc.pct_chg, dc.signal_pct, dc.close_position_score, dc.market_regime,
            dc.auxiliary_evidence_snapshot, dc.selection_reason,
            dc.ticket_reason, dc.not_selected_reason,
            dc.candidate_features, dc.factor_snapshot, dc.ranking_basis,
            dc.eligibility_snapshot, dc.selection_diagnostics, dc.source_layers,
            dc.candidate_entry_reason, dc.raw_json, dc.blockers,
            dc.open_price, dc.close_price, dc.high_price, dc.low_price,
            dc.sentiment_catalyst, dc.theme_catalyst, dc.news_catalyst, dc.positive_catalyst,
            dc.fund_flow_momentum, dc.sector_catalyst_score,
            r.t1_return AS t1_return
        FROM daily_candidates dc
        JOIN production_run_active pra
          ON pra.trade_date = dc.trade_date
         AND pra.production_run_id = dc.production_run_id
        LEFT JOIN returns r
          ON r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol
        WHERE dc.trade_date = (
              SELECT trade_date
              FROM production_run_active
              ORDER BY trade_date DESC, updated_at DESC
              LIMIT 1
          )
          AND dc.rank IS NOT NULL
          AND dc.rank <= :limit
        ORDER BY dc.rank NULLS LAST, dc.final_score DESC NULLS LAST
    """, {"limit": limit})
    candidates = []
    for row in rows:
        public = _dashboard_candidate_row(row)
        production_score = _production_score_from_row(row)
        score = production_score if production_score is not None else 0
        outcome = str(public.get("selection_outcome") or public.get("decision") or "").upper()
        decision = "TRADING" if public.get("is_official_pick") or outcome == "OFFICIAL_PICK" else "WATCH"
        if "BLOCK" in outcome or "NOT_SELECTED" in outcome:
            decision = "NO_PICK" if score < 60 else "WATCH"
        candidates.append({
            "symbol": public.get("symbol"),
            "name": public.get("stock_name") or public.get("symbol"),
            "rank": public.get("rank"),
            "score": score,
            "productionScore": score,
            "scoreSource": "formal_t1_profit_components" if production_score is not None else "UNAVAILABLE",
            "rankingView": "main_force_behavior_chain",
            "rankSource": "formal_profit_first",
            "pct_chg": _round_float(public.get("pct_chg"), 2),
            "aiRating": _score_to_stars(score),
            "trend": _score_to_stars((_to_float(public.get("close_position_score")) or 0) * 100),
            "capitalFlow": _score_to_stars((_to_float(public.get("fund_flow_momentum")) or 0) * 100),
            "sentiment": _score_to_stars((_to_float(public.get("sector_catalyst_score")) or 0) * 100),
            "risk": "low" if score >= 75 else ("medium" if score >= 60 else "high"),
            "signals": [
                {
                    "type": "leader",
                    "name": "正式排序",
                    "strength": _score_to_stars(score),
                    "description": "来自 daily_candidates 的正式 Top10 排序记录",
                    "dataSources": [
                        {
                            "name": "候选池",
                            "source": "PostgreSQL daily_candidates",
                            "timestamp": _iso(public.get("trade_date")),
                            "status": "PASS",
                            "data": {"rank": public.get("rank"), "score": score},
                        }
                    ],
                    "derivation": "；".join(public.get("selection_basis_cn") or [])[:240],
                }
            ],
            "decision": decision,
            "t1Return": _percent_from_decimal(public.get("t1_return")),
            "entryPrice": _round_float(public.get("entry_price"), 2),
            "selectionReason": public.get("one_liner") or "",
            "entryPriceLabel": "建议入手价",
            "evidenceReport": _candidate_evidence_report(row),
            "dataSources": [
                {
                    "name": item.get("domain") or "source",
                    "source": item.get("source") or "PostgreSQL",
                    "timestamp": public.get("entry_evidence", {}).get("source_time") or _iso(public.get("trade_date")),
                    "status": (
                        item.get("candidate_status")
                        or item.get("source_status")
                        or "UNKNOWN"
                    ),
                    "data": item,
                }
                for item in (public.get("entry_evidence", {}).get("source_metadata") or [])[:4]
            ],
            "tradeDate": _iso(public.get("trade_date")),
            "expectedT1ProfitScore": public.get("expected_t1_profit_score"),
            "pctChg": _round_float(public.get("pct_chg"), 2),
            "themeCatalyst": public.get("theme_catalyst") or "",
            "newsCatalyst": public.get("news_catalyst") or "",
            "sentimentCatalyst": public.get("sentiment_catalyst") or "",
            "entryEvidence": public.get("entry_evidence") or {},
            "selectionExplanation": public.get("selection_explanation") or {},
            "selectionBasisCn": public.get("selection_basis_cn") or [],
            "riskGatesCn": public.get("risk_gates_cn") or [],
        })
    return candidates


def _os_memory_payload(limit: int = 20) -> Dict[str, Any]:
    obsidian_root = Path(os.environ.get("XIAOGU_OBSIDIAN_ASHARE", "/mnt/d/obisidian/Obsidian/Project/A股"))
    shenlin_root = Path(os.environ.get("XIAOGU_OBSIDIAN_SHENLIN", "/mnt/d/obisidian/Obsidian/神临"))
    entries: List[Dict[str, Any]] = []
    vector_count = 0
    try:
        count_rows = query_rows("""
            SELECT COUNT(*) AS count
            FROM pick_case_embeddings pce
            JOIN production_run_active pra
              ON pra.trade_date = pce.trade_date
             AND pra.production_run_id = pce.production_run_id
        """)
        vector_count = int(count_rows[0].get("count") or 0) if count_rows else 0
        case_rows = query_rows("""
            SELECT pce.trade_date, pce.symbol, pce.stock_name, pce.decision, pce.final_score,
                   t1_return, case_text
            FROM pick_case_embeddings pce
            JOIN production_run_active pra
              ON pra.trade_date = pce.trade_date
             AND pra.production_run_id = pce.production_run_id
            ORDER BY pce.trade_date DESC, pce.symbol
            LIMIT :limit
        """, {"limit": limit})
        for row in case_rows:
            t1 = _percent_from_decimal(row.get("t1_return"))
            decision = _public_trading_text(row.get("decision") or "CASE")
            entries.append({
                "id": f"db-{_iso(row.get('trade_date'))}-{row.get('symbol')}",
                "date": _iso(row.get("trade_date")),
                "type": "lesson" if (t1 is not None and t1 < 0) else "pattern",
                "title": f"{_stock_name(row)} {decision}",
                "content": _public_trading_text(row.get("case_text"))[:260],
                "tags": [
                    "PostgreSQL",
                    "pgvector",
                    decision,
                ],
                "similarity": None,
            })
    except Exception:
        vector_count = 0

    for path in sorted((Path(__file__).resolve().parent / "summary").glob("*_top10_knowledge.json"), reverse=True)[:5]:
        try:
            import json
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        formal = payload.get("formal_paper_pick") or {}
        entries.append({
            "id": f"summary-{payload.get('trade_date') or path.stem}",
            "date": str(payload.get("trade_date") or path.name[:10]),
            "type": "decision",
            "title": f"正式票复盘 {formal.get('symbol') or 'NO_PICK'} {formal.get('stock_name') or ''}".strip(),
            "content": f"Top10 覆盖 {payload.get('top10_return_coverage')}; summary={path.name}",
            "tags": ["summary", "Top10", "TRADING"],
            "similarity": None,
        })

    inbox = obsidian_root / "inbox"
    if inbox.exists():
        for path in sorted(inbox.glob("*.md"), reverse=True)[:5]:
            try:
                text_body = path.read_text(encoding="utf-8")
            except Exception:
                continue
            title = next((line.lstrip("# ").strip() for line in text_body.splitlines() if line.startswith("#")), path.stem)
            entries.append({
                "id": f"obsidian-{path.name}",
                "date": path.name[:10],
                "type": "market",
                "title": title,
                "content": " ".join(line.strip() for line in text_body.splitlines() if line.strip() and not line.startswith("#"))[:260],
                "tags": ["Obsidian", "Project/A股"],
                "similarity": None,
            })

    patterns = query_rows("""
        SELECT
            COALESCE(decision, 'CASE') AS pattern,
            COUNT(*) AS matches,
            ROUND(AVG(CASE WHEN t1_return >= 0 THEN 1 ELSE 0 END)::numeric * 100, 1) AS accuracy,
            MAX(trade_date) AS last_seen
        FROM pick_case_embeddings pce
        JOIN production_run_active pra
          ON pra.trade_date = pce.trade_date
         AND pra.production_run_id = pce.production_run_id
        WHERE pce.t1_return IS NOT NULL
        GROUP BY 1
        ORDER BY matches DESC
        LIMIT 6
    """) if vector_count else []

    return {
        "entries": entries[:limit],
        "patterns": [
            {
                "pattern": _public_trading_text(row.get("pattern")),
                "matches": int(row.get("matches") or 0),
                "accuracy": _round_float(row.get("accuracy"), 1) or 0,
                "lastSeen": _iso(row.get("last_seen")),
            }
            for row in patterns
        ],
        "connection": {
            "database": "online" if vector_count else "degraded",
            "vectorRecords": vector_count,
            "obsidian": "online" if obsidian_root.exists() else "offline",
            "obsidianPath": str(obsidian_root),
            "shenlin": "online" if shenlin_root.exists() else "offline",
            "shenlinPath": str(shenlin_root),
        },
    }


def _os_review_payload(limit: int = 50, low_return_threshold: float = 0.01) -> Dict[str, Any]:
    """Expose only settled loss/low-return cases as production upgrade input."""
    obsidian_root = Path(os.environ.get("XIAOGU_OBSIDIAN_ASHARE", "/mnt/d/obisidian/Obsidian/Project/A股"))
    try:
        rows = query_rows("""
            SELECT
                p.trade_date,
                p.symbol,
                p.stock_name,
                p.final_score,
                p.rank,
                p.selection_reason,
                p.ticket_reason,
                dc.candidate_features,
                dc.raw_json,
                r.t1_return
            FROM picks p
            JOIN returns r
              ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
            LEFT JOIN daily_candidates dc
              ON dc.production_run_id = p.production_run_id AND dc.symbol = p.symbol
            JOIN production_run_active pra
              ON pra.trade_date = p.trade_date
             AND pra.production_run_id = p.production_run_id
            WHERE p.decision = 'PAPER_PICK'
              AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
              AND r.t1_return IS NOT NULL
              AND r.t1_return < :threshold
            ORDER BY r.t1_return ASC, p.trade_date DESC
            LIMIT :limit
        """, {"threshold": low_return_threshold, "limit": limit})
    except Exception:
        rows = []

    cases = []
    for row in rows:
        t1_return = _to_float(row.get("t1_return"))
        if t1_return is None:
            continue
        case = {
            "trade_date": _iso(row.get("trade_date")),
            "symbol": row.get("symbol"),
            "stock_name": row.get("stock_name"),
            "final_score": _round_float(row.get("final_score"), 2),
            "rank": row.get("rank"),
            "t1_return": t1_return,
            "review_level": "LOSS" if t1_return < 0 else "LOW_RETURN",
            "selection_reason": row.get("selection_reason") or row.get("ticket_reason") or "",
            "production_chain": _PRODUCTION_CHAIN_NAME,
            "upgrade_target": "main_force_behavior_chain",
        }
        case["diagnosis"] = _replay_sample_reason(row)
        cases.append(case)

    return {
        "threshold": low_return_threshold,
        "cases": cases,
        "count": len(cases),
        "source": "PostgreSQL picks/returns/daily_candidates",
        "obsidian": {
            "status": "online" if obsidian_root.exists() else "offline",
            "path": str(obsidian_root),
            "role": "复盘结论进入主力行为生产链升级依据",
        },
    }


def _os_system_payload(
    simulation: Dict[str, Any],
    production_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    run_id = production_run_id or (
        (_resolve_production_run() or {}).get("production_run_id")
    )
    session_rows = query_rows("""
        SELECT trade_date, scan_time, quotes_count, scored_count, passed_count,
               status, source_status, source_counts, source_diagnostics
        FROM scan_sessions
        WHERE production_run_id = :production_run_id
        ORDER BY trade_date DESC, scan_time DESC
        LIMIT 1
    """, {"production_run_id": run_id})
    session = session_rows[0] if session_rows else {}
    source_status = _as_json_object(session.get("source_status"))
    source_counts = _as_json_object(session.get("source_counts"))
    diagnostics = _as_json_object(session.get("source_diagnostics"))
    memory = _os_memory_payload(limit=12)
    review = _os_review_payload()
    scanner_status = "online" if session.get("status") in {"completed", "OK", "PASS"} else "degraded"
    memory_status = "online" if memory["connection"]["obsidian"] == "online" else "degraded"
    data_sources = []
    for name, value in source_status.items():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "PASS").upper()
        domain_counts = value.get("domain_counts")
        if not isinstance(domain_counts, dict):
            domain_counts = {}
        record_count = (
            source_counts.get(name)
            or value.get("record_count")
            or domain_counts.get(name)
            or sum(
                int(count or 0)
                for count in domain_counts.values()
                if isinstance(count, (int, float))
            )
        )
        data_sources.append({
            "name": name,
            "status": "online" if status == "PASS" else "degraded",
            "latency": "n/a",
            "lastSync": _iso(session.get("scan_time")),
            "records": str(record_count or "0"),
        })
    recent_errors = []
    for key, value in diagnostics.items():
        if isinstance(value, dict) and value.get("error"):
            recent_errors.append({
                "id": len(recent_errors) + 1,
                "time": _iso(session.get("scan_time")),
                "service": key,
                "message": str(value.get("error"))[:160],
                "level": "warning",
            })
    return {
        "health": {
            "database": "online",
            "api": "online",
            "scanner": scanner_status,
            "model": "online",
            "memory": memory_status,
            "lastUpdate": _iso(session.get("scan_time")) or datetime.now(timezone.utc).isoformat(),
        },
        "dataSources": data_sources[:8],
        "recentErrors": recent_errors[:8],
        "memory": memory,
        "review": review,
    }


@app.get("/api/os/front-data")
def get_os_front_data(
    initial_capital: Optional[float] = None,
):
    """Read model for Xiaogu OS pages; uses only recorded DB/Obsidian state."""
    if initial_capital is not None and (initial_capital <= 0 or initial_capital > 1000000):
        raise HTTPException(status_code=400, detail="initial_capital must be between 0 and 1000000")
    active_run = _resolve_production_run()
    if not active_run:
        raise HTTPException(status_code=404, detail="No active production run found")
    production_run_id = str(active_run["production_run_id"])
    simulation = _simulate_paper_portfolio(initial_capital=initial_capital)
    candidates = _os_candidate_rows()
    system = _os_system_payload(simulation, production_run_id)
    replay = _enrich_chain_replay(_load_latest_chain_replay())
    scan_bundle = _latest_scan_market_payloads(production_run_id)
    scan = scan_bundle["session"]
    scan_payloads = scan_bundle["payloads"]
    market_snapshot = _as_json_object(scan.get("market_snapshot"))
    direct_data_coverage = _as_json_object(
        market_snapshot.get("direct_data_coverage")
    )
    limit_counts = _as_json_object(
        _as_json_object(market_snapshot.get("source_status")).get("source_completeness")
    ).get("core_sentiment_pool_counts") or {}
    sentiment_score = _to_float(market_snapshot.get("sentiment_score")) or 0.0
    regime_map = {
        "bullish": "BULLISH",
        "strong": "BULLISH",
        "weak": "BEARISH",
        "bearish": "BEARISH",
        "sideways": "SIDEWAYS",
        "neutral": "NEUTRAL",
    }
    quote_count = int(market_snapshot.get("universe_quote_count") or scan.get("quotes_count") or 0)
    market_main_inflow_snapshot = _to_float(market_snapshot.get("market_main_inflow")) or 0.0
    breadth_up_pct = _to_float(market_snapshot.get("market_breadth_up_pct"))
    recorded_advancing = int(market_snapshot.get("market_bigups") or 0)
    advancing = (
        int(round(quote_count * breadth_up_pct / 100.0))
        if breadth_up_pct is not None and quote_count
        else recorded_advancing
    )
    market_state = {
        "regime": regime_map.get(str(market_snapshot.get("market_regime") or "").strip().lower(), "NEUTRAL"),
        "sentiment": min(100, max(0, int(round(sentiment_score * 10)))),
        "volume": 0.0,
        "limitUpCount": int(market_snapshot.get("market_limitups") or limit_counts.get("limitup_pool") or 0),
        "limitDownCount": int(market_snapshot.get("broken_limitups") or limit_counts.get("limitup_broken") or 0),
        "advancing": advancing,
        "declining": max(0, quote_count - advancing),
        "quoteCount": quote_count,
        "upPercent": _round_float(breadth_up_pct, 2) or 0,
        "breadthSource": "scan_sessions.market_snapshot.market_breadth_up_pct",
        "brokenLimitups": int(market_snapshot.get("broken_limitups") or 0),
        "marketMainInflow": 0.0,
        "marketMainInflowUnit": "亿元",
        "marketMainInflowSource": "scan_market_data.market_capital_flow",
        "directDataCoverage": direct_data_coverage,
    }
    indexes_payload = _as_json_array(scan_payloads.get("indexes"))
    sector_payload = _as_json_object(scan_payloads.get("sector_capital_flow"))
    market_capital_flow_payload = _as_json_array(scan_payloads.get("market_capital_flow"))
    market_main_inflow_yuan = sum(
        _to_float(item.get("mainInflow")) or 0.0
        for item in (
            _as_market_capital_flow_item(value)
            for value in market_capital_flow_payload
            if isinstance(value, dict)
        )
    )
    if market_main_inflow_yuan == 0 and abs(market_main_inflow_snapshot) > 0:
        market_main_inflow_yuan = (
            market_main_inflow_snapshot
            if abs(market_main_inflow_snapshot) >= 1_000_000
            else market_main_inflow_snapshot * 100_000_000
        )
    market_main_inflow_yi = round(market_main_inflow_yuan / 100_000_000, 2)
    market_state["volume"] = market_main_inflow_yi
    market_state["marketMainInflow"] = market_main_inflow_yi
    market_state["marketMainInflowYuan"] = round(market_main_inflow_yuan, 2)
    paper_pick_trade_date = (
        _iso(scan.get("trade_date"))
        or _iso(session.get("trade_date"))
        or datetime.now(timezone.utc).date().isoformat()
    )
    paper_pick_rows = query_rows("""
        SELECT
            p.id, p.trade_date, p.symbol, p.stock_name, p.decision,
            p.final_score, p.rank, p.auxiliary_evidence_status,
            p.ticket_reason, p.selection_reason, p.created_at, p.features,
            r.t1_return AS t1_return
        FROM picks p
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id AND r.symbol = p.symbol
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        WHERE p.trade_date = CAST(:trade_date AS date)
          AND p.production_run_id = :production_run_id
          AND p.decision = 'PAPER_PICK'
          AND COALESCE(p.features ->> 'superseded', 'false') <> 'true'
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 1
    """, {"trade_date": paper_pick_trade_date, "production_run_id": production_run_id})
    paper_pick = _dashboard_pick_row(paper_pick_rows[0]) if paper_pick_rows else None
    if paper_pick:
        matched_candidate = next(
            (item for item in candidates if item.get("symbol") == paper_pick.get("symbol")),
            None,
        )
        if matched_candidate:
            paper_pick["name"] = matched_candidate.get("name") or matched_candidate.get("stock_name") or paper_pick.get("name") or paper_pick.get("stock_name") or paper_pick.get("symbol")
            paper_pick["evidenceReport"] = matched_candidate.get("evidenceReport")
            paper_pick["selectionReason"] = matched_candidate.get("selectionReason") or paper_pick.get("selectionReason")
            paper_pick["entryPrice"] = matched_candidate.get("entryPrice") or paper_pick.get("entryPrice")
            paper_pick["score"] = matched_candidate.get("score") or paper_pick.get("score")
            paper_pick["productionScore"] = matched_candidate.get("productionScore") or paper_pick.get("score")
        else:
            paper_pick["name"] = paper_pick.get("name") or paper_pick.get("stock_name") or paper_pick.get("symbol")
            official_candidate_rows = query_rows(
                """
                SELECT dc.*, r.t1_return AS t1_return
                FROM daily_candidates dc
                LEFT JOIN returns r
                  ON r.production_run_id = dc.production_run_id AND r.symbol = dc.symbol
                WHERE dc.trade_date = CAST(:trade_date AS date)
                  AND dc.symbol = :symbol
                  AND dc.production_run_id = :production_run_id
                ORDER BY dc.updated_at DESC NULLS LAST, dc.id DESC
                LIMIT 1
                """,
                {
                    "trade_date": paper_pick_trade_date,
                    "symbol": paper_pick.get("symbol"),
                    "production_run_id": production_run_id,
                },
            )
            if official_candidate_rows:
                official_candidate = _dashboard_candidate_row(official_candidate_rows[0])
                paper_pick["evidenceReport"] = _candidate_evidence_report(official_candidate_rows[0])
                paper_pick["selectionReason"] = (
                    official_candidate.get("one_liner")
                    or paper_pick.get("selectionReason")
                )
    sector_concept = sorted(
        [
            _as_share_sector_flow_item(item)
            for item in _as_json_array(sector_payload.get("concept"))
            if isinstance(item, dict)
        ],
        key=lambda item: abs(_to_float(item.get("netInflow")) or 0.0),
        reverse=True,
    )[:8]
    sector_industry = sorted(
        [
            _as_share_sector_flow_item(item)
            for item in _as_json_array(sector_payload.get("industry"))
            if isinstance(item, dict)
        ],
        key=lambda item: abs(_to_float(item.get("netInflow")) or 0.0),
        reverse=True,
    )[:8]
    a_share_market = {
        "snapshotTradeDate": _iso(scan.get("trade_date")),
        "snapshotScanTime": _iso(scan.get("scan_time")),
        "directDataCoverage": direct_data_coverage,
        "indexes": [
            _as_share_index_quote(item)
            for item in indexes_payload
            if isinstance(item, dict)
        ],
        "sectorFlows": {
            "concept": sector_concept,
            "industry": sector_industry,
        },
        "marketCapitalFlow": [
            _as_market_capital_flow_item(item)
            for item in market_capital_flow_payload
            if isinstance(item, dict)
        ],
    }
    latest_candidate = candidates[0] if candidates else None
    final_pick = paper_pick or latest_candidate
    final_pick_score = None
    if final_pick:
        final_pick_score = (
            final_pick.get("score")
            or final_pick.get("productionScore")
            or final_pick.get("final_score")
        )
    memory = system["memory"]
    ai_decisions = [
        {
            "step": "数据采集",
            "description": "读取 scan_sessions / daily_candidates / picks / returns",
            "dataUsed": ["PostgreSQL", "Eastmoney API v2", "Obsidian"],
            "result": f"最新扫描 {_iso(scan.get('trade_date'))}，候选 {len(candidates)} 只",
            "confidence": 100,
            "timestamp": _iso(scan.get("scan_time")),
        },
            {
                "step": "收益回填",
                "description": "按 T 日出票、T+1 收盘收益记录；不使用固定账户金额作为出票依据",
                "dataUsed": ["picks", "returns", "daily_candidates.close_price"],
                "result": (
                    f"已记录 {simulation['stats'].get('totalTrades', 0)} 笔 T+1 结果，"
                    f"胜率 {simulation['stats'].get('winRate', 0)}%"
                ),
            "confidence": 100,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        {
            "step": "最终建议",
            "description": "展示最新正式出票和当前候选池",
            "dataUsed": ["picks", "daily_candidates Top10", "pending TRADING"],
            "result": (
                f"{final_pick['name']} ({final_pick['symbol']}) score={final_pick_score}"
                if final_pick else "暂无正式出票"
            ),
            "confidence": 90,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ]
    intelligence = [
        {
            "id": f"pick-{item['symbol']}",
            "type": "capital",
            "title": f"{item['name']} ({item['symbol']})",
            "summary": item.get("selectionReason") or "正式候选记录",
            "source": "daily_candidates",
            "timestamp": _iso(scan.get("trade_date")),
            "relevance": int(item.get("score") or 0),
            "impact": "positive" if item.get("decision") == "TRADING" else "neutral",
        }
        for item in candidates[:6]
    ]
    return {
        "mode": "live",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "productionRunId": production_run_id,
        "candidateSnapshotId": active_run.get("candidate_snapshot_id"),
        "productionChain": {
            "name": _PRODUCTION_CHAIN_NAME,
            "label": "主力行为链（唯一生产链路）",
            "candidateSource": "daily_candidates",
            "rankSource": _PRODUCTION_RANK_SOURCE,
            "objective": "T日出票，T+1收盘获利",
            "returnField": "returns.t1_return",
            "singleOfficialOutput": "TRADING/NO_PICK",
            "performanceSource": "main_force_behavior_chain_full_database_replay",
            "performanceWindow": f"{replay['window']['start']}至{replay['window']['end']}",
            "performanceArtifact": replay.get("source"),
            "performanceSampleCount": replay.get("sample_count", 0),
        },
        "systemStats": _latest_chain_system_stats(),
        "latestChainReplay": replay,
        "productionScoreContract": {
            "scoreField": "production_score",
            "rankField": "rank",
            "rankSource": _PRODUCTION_RANK_SOURCE,
            "rankingView": _PRODUCTION_CHAIN_NAME,
            "objective": "T日买入 → T+1收盘获利",
            "returnField": "returns.t1_return",
        },
        "observability": {
            "scanSessionId": scan.get("id"),
            "scanTradeDate": _iso(scan.get("trade_date")),
            "scanTime": _iso(scan.get("scan_time")),
            "quoteCount": quote_count,
            "scoredCount": int(scan.get("scored_count") or 0),
            "passedCount": int(scan.get("passed_count") or 0),
            "candidateCount": len(candidates),
            "vectorRecords": memory["connection"]["vectorRecords"],
            "obsidianEntries": len(memory["entries"]),
            "replaySource": replay.get("source"),
            "replaySamples": replay.get("sample_count", 0),
        },
        "decision": {
            "latest": latest_candidate,
            "paper_pick": paper_pick,
            "trading": paper_pick,
            "paper_pick_entry_price": paper_pick.get("entryPrice") if paper_pick else None,
            "trading_entry_price": paper_pick.get("entryPrice") if paper_pick else None,
            "candidate_date": _iso(scan.get("trade_date")),
            "candidate_count": len(candidates),
        },
        "marketState": market_state,
        "aShareMarket": a_share_market,
        "candidates": candidates,
        "aiDecisions": ai_decisions,
        "intelligence": intelligence,
        "portfolio": {
            "initialCapital": simulation["initialCapital"],
            "totalAssets": simulation["totalAssets"],
            "cash": simulation["cash"],
            "stockValue": simulation["stockValue"],
            "totalPnl": simulation["totalPnl"],
            "totalPnlPercent": simulation["totalPnlPercent"],
            "positions": simulation["positions"],
            "allocation": simulation["allocation"],
            "equityCurve": simulation["equityCurve"],
            "riskMetrics": simulation["riskMetrics"],
            "latestPendingDate": simulation["latestPendingDate"],
            "pendingRecords": simulation["pendingRecords"],
        },
        "settlement": {
            "status": "PENDING_T1" if simulation["pendingRecords"] else "COMPLETE",
            "latestPendingDate": simulation["latestPendingDate"],
            "pendingCount": len(simulation["pendingRecords"]),
            "pendingRecords": simulation["pendingRecords"],
        },
        "tradeHistory": simulation["tradeHistory"],
        "monthlyData": simulation["monthlyData"],
        "memory": memory,
        "review": system.get("review") or {
            "threshold": 0.01,
            "cases": [],
            "count": 0,
            "source": "PostgreSQL picks/returns/daily_candidates",
            "obsidian": {"status": "offline", "path": "", "role": "复盘结论进入主力行为生产链升级依据"},
        },
        "systemHealth": system["health"],
        "dataSources": system["dataSources"],
        "recentErrors": system["recentErrors"],
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
    decision: Optional[str] = Query(None, description="TRADING or NO_PICK"),
    production_run_id: Optional[str] = None,
    include_superseded: bool = Query(False, description="Include superseded correction audit rows"),
    limit: int = Query(50, le=500),
):
    """List active picks by default, with explicit superseded audit opt-in."""
    if decision == "TRADING":
        decision = "PAPER_PICK"
    if decision is not None and decision not in {"PAPER_PICK", "NO_PICK"}:
        raise HTTPException(status_code=400, detail="xiaogu 只支持 TRADING（内部兼容 PAPER_PICK）或 NO_PICK")
    where = []
    params: Dict[str, Any] = {"limit": limit}
    run = _resolve_production_run(date, production_run_id) if (date or production_run_id) else None
    if date or production_run_id:
        if not run:
            raise HTTPException(status_code=404, detail="No production run found")
        where.append("production_run_id = :production_run_id")
        params["production_run_id"] = run["production_run_id"]
    else:
        where.append(
            "production_run_id IN (SELECT production_run_id FROM production_run_active)"
        )
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
    for row in rows:
        row["display_decision"] = "TRADING" if row.get("decision") == "PAPER_PICK" else row.get("decision")
    return {
        "picks": rows,
        "count": len(rows),
        "include_superseded": include_superseded,
        "production_run_id": run.get("production_run_id") if run else None,
    }


@app.get("/picks/{trade_date}/summary")
def get_daily_summary(
    trade_date: str,
    production_run_id: Optional[str] = None,
):
    """Daily active pick summary: current PAPER_PICK + highest active score."""
    run = _resolve_production_run(trade_date, production_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"No production run for {trade_date}")
    rows = query_rows(
        """
        SELECT *
        FROM picks
        WHERE trade_date = :date
          AND production_run_id = :production_run_id
          AND COALESCE(features ->> 'superseded', 'false') <> 'true'
        ORDER BY updated_at DESC NULLS LAST, created_at DESC
        """,
        {"date": trade_date, "production_run_id": run["production_run_id"]},
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
        "production_run_id": run["production_run_id"],
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
    production_run_id: Optional[str] = None,
    limit_up_only: bool = Query(False),
    method: str = Query("close", description="仅支持 T+1 收盘收益"),
    limit: int = Query(50, le=500),
):
    """List the single production return measure: T+1 close."""
    if method != "close":
        raise HTTPException(status_code=400, detail="xiaogu 仅支持 T+1 收盘收益口径")
    method_col = "t1_return"
    where = []
    params: Dict[str, Any] = {"limit": limit}
    run = _resolve_production_run(date, production_run_id) if (date or production_run_id) else None
    if date or production_run_id:
        if not run:
            raise HTTPException(status_code=404, detail="No production run found")
        where.append("production_run_id = :production_run_id")
        params["production_run_id"] = run["production_run_id"]
    else:
        where.append("production_run_id IN (SELECT production_run_id FROM production_run_active)")
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
    return {
        "returns": rows,
        "count": len(rows),
        "method": method,
        "production_run_id": run.get("production_run_id") if run else None,
    }


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
            ROUND(AVG(r.t1_return)::numeric, 4) AS avg_t1_close,
            COUNT(*) FILTER (WHERE r.t1_return >= 0.095) AS limit_ups_close,
            COUNT(*) FILTER (WHERE r.t1_return >= 0) AS wins_close,
            COUNT(*) FILTER (WHERE r.t1_return IS NOT NULL) AS total_filled,
            MAX(p.trade_date) AS latest_trade_date,
            MIN(p.trade_date) AS earliest_trade_date
        FROM picks p
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id
         AND r.symbol = p.symbol
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        WHERE p.production_run_id IS NOT NULL
    """)
    return rows[0] if rows else {}


@app.get("/stats/performance")
def get_performance():
    """Detailed performance breakdown by period."""
    monthly = query_rows("""
        SELECT
            DATE_TRUNC('month', p.trade_date)::date AS month,
            COUNT(*) AS picks,
            COUNT(r.t1_return) AS filled,
            ROUND(AVG(r.t1_return)::numeric, 4) AS avg_close,
            COUNT(*) FILTER (WHERE r.t1_return >= 0.095) AS limit_ups,
            COUNT(*) FILTER (WHERE r.t1_return >= 0) AS wins
        FROM picks p
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id
         AND r.symbol = p.symbol
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        WHERE p.decision = 'PAPER_PICK'
        GROUP BY 1 ORDER BY 1
    """)
    return {"monthly": monthly}


@app.get("/signals")
def get_signals(
    date: Optional[str] = Query(None, description="Trade date YYYY-MM-DD"),
    symbol: Optional[str] = Query(None, description="Stock code e.g. 300603"),
    signal_key: Optional[str] = Query(None, description="Signal name e.g. market_regime"),
    production_run_id: Optional[str] = None,
    limit: int = Query(100, le=1000),
):
    """Raw signal values for stocks."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    run = _resolve_production_run(date, production_run_id) if (date or production_run_id) else None
    if date or production_run_id:
        if not run:
            raise HTTPException(status_code=404, detail="No production run found")
        where.append("production_run_id = :production_run_id")
        params["production_run_id"] = run["production_run_id"]
    else:
        where.append("production_run_id IN (SELECT production_run_id FROM production_run_active)")
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
    return {
        "signals": rows,
        "count": len(rows),
        "production_run_id": run.get("production_run_id") if run else None,
    }


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
    production_run_id: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    """Scanner session history."""
    where = []
    params: Dict[str, Any] = {"limit": limit}
    run = _resolve_production_run(date, production_run_id) if (date or production_run_id) else None
    if date or production_run_id:
        if not run:
            raise HTTPException(status_code=404, detail="No production run found")
        where.append("production_run_id = :production_run_id")
        params["production_run_id"] = run["production_run_id"]
    else:
        where.append("production_run_id IN (SELECT production_run_id FROM production_run_active)")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = query_rows(
        f"SELECT id, trade_date, scan_time, source_id, quotes_count, scored_count, passed_count, status FROM scan_sessions {clause} ORDER BY trade_date DESC, scan_time LIMIT :limit",
        params,
    )
    return {
        "sessions": rows,
        "count": len(rows),
        "production_run_id": run.get("production_run_id") if run else None,
    }


@app.get("/daily-candidates/{trade_date}")
def get_daily_candidates(
    trade_date: str,
    production_run_id: Optional[str] = None,
):
    """Daily candidate analysis: official picks + top scored candidates with selection rationale."""
    run = _resolve_production_run(trade_date, production_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"No production run for {trade_date}")
    rows = query_rows(
        """SELECT * FROM daily_candidates
           WHERE trade_date = :date AND production_run_id = :production_run_id
           ORDER BY is_official_pick DESC, final_score DESC NULLS LAST""",
        {"date": trade_date, "production_run_id": run["production_run_id"]},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No candidates for {trade_date}")
    return {
        "trade_date": trade_date,
        "production_run_id": run["production_run_id"],
        "candidates": rows,
        "count": len(rows),
    }


@app.get("/explain/{trade_date}/{symbol}")
def explain_candidate(
    trade_date: str,
    symbol: str,
    production_run_id: Optional[str] = None,
):
    """Explain an existing candidate using persisted fields without changing scoring."""
    run = _resolve_production_run(trade_date, production_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"No production run for {trade_date}")
    rows = query_rows(
        """SELECT * FROM daily_candidates
           WHERE trade_date = :date AND symbol = :symbol
             AND production_run_id = :production_run_id""",
        {"date": trade_date, "symbol": symbol, "production_run_id": run["production_run_id"]},
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
        "production_run_id": run["production_run_id"],
        "symbol": symbol,
        "final_score": score,
        "decision": candidate.get("decision"),
        "reasons": reasons,
        "risk_flags": [],
        "data_sources": ["daily_candidates"],
    }


@app.get("/picks/{trade_date}/detail")
def get_pick_detail(
    trade_date: str,
    production_run_id: Optional[str] = None,
):
    """Full pick detail: decision + returns + signals + candidate analysis for a date."""
    run = _resolve_production_run(trade_date, production_run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"No production run for {trade_date}")
    # Get candidates from daily_candidates table
    dc_rows = query_rows(
        """SELECT * FROM daily_candidates
           WHERE trade_date = :date AND production_run_id = :production_run_id
           ORDER BY is_official_pick DESC, final_score DESC NULLS LAST""",
        {"date": trade_date, "production_run_id": run["production_run_id"]},
    )
    if not dc_rows:
        raise HTTPException(status_code=404, detail=f"No candidates for {trade_date}")

    # Enrich with returns
    symbols = [r["symbol"] for r in dc_rows if r["symbol"]]
    returns = {}
    if symbols:
        ret_rows = query_rows(
            """SELECT * FROM returns
               WHERE production_run_id = :production_run_id
                 AND symbol = ANY(:symbols)""",
            {"production_run_id": run["production_run_id"], "symbols": symbols},
        )
        returns = {r["symbol"]: r for r in ret_rows}

    for r in dc_rows:
        r["return"] = returns.get(r["symbol"])

    official = [r for r in dc_rows if r["is_official_pick"]]
    return {
        "trade_date": trade_date,
        "production_run_id": run["production_run_id"],
        "official_pick": official[0] if official else None,
        "candidates": dc_rows,
    }


@app.get("/dashboard", response_class=HTMLResponse)
def get_simulation_dashboard():
    """Serve the dashboard from the repository's public assets."""
    if PUBLIC_DIR.is_dir():
        html_path = PUBLIC_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    """T+1 模拟交易验证看板 - 从数据库出票开始模拟，验证策略有效性"""
    from jinja2 import Template

    # 查询所有PAPER_PICK及收益
    rows = query_rows("""
        SELECT p.trade_date, p.symbol, p.final_score, r.t1_return
        FROM picks p
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id
         AND r.symbol = p.symbol
        WHERE p.decision = 'PAPER_PICK'
          AND p.symbol IS NOT NULL AND p.symbol != ''
          AND r.t1_return IS NOT NULL
        ORDER BY p.trade_date, p.symbol
    """)

    initial_capital = 1.0
    capital = initial_capital
    total_trades = 0
    winning_trades = 0

    daily_picks = {}
    for row in rows:
        trade_date = row['trade_date']
        if trade_date not in daily_picks:
            daily_picks[trade_date] = []
        daily_picks[trade_date].append({'score': row['final_score'], 't1_return': row['t1_return']})

    equity_dates = []
    equity_values = []

    for trade_date in sorted(daily_picks.keys()):
        picks = daily_picks[trade_date]
        if picks:
            capital_per_pick = capital / len(picks)
            day_pnl = 0.0
            for pick in picks:
                t1_return = pick['t1_return']
                if t1_return is not None:
                    day_pnl += capital_per_pick * t1_return
                    total_trades += 1
                    if t1_return > 0:
                        winning_trades += 1
            capital += day_pnl
        equity_dates.append(trade_date.strftime('%m-%d'))
        equity_values.append(round(capital, 2))

    peak = initial_capital
    max_drawdown = 0
    for val in equity_values:
        if val > peak:
            peak = val
        drawdown = (peak - val) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0
    total_return_pct = (capital / initial_capital - 1) * 100

    # 分数段统计
    score_rows = query_rows("""
        SELECT
            CASE
                WHEN p.final_score < 50 THEN '< 50'
                WHEN p.final_score < 60 THEN '50-60'
                WHEN p.final_score < 70 THEN '60-70'
                WHEN p.final_score < 80 THEN '70-80'
                WHEN p.final_score < 90 THEN '80-90'
                ELSE '90+'
            END as bucket,
            count(*) as total,
            count(CASE WHEN r.t1_return > 0 THEN 1 END) as wins,
            round(count(CASE WHEN r.t1_return > 0 THEN 1 END)::numeric / count(*) * 100, 1) as win_rate,
            round(avg(r.t1_return)::numeric * 100, 2) as avg_return
        FROM picks p
        JOIN production_run_active pra
          ON pra.trade_date = p.trade_date
         AND pra.production_run_id = p.production_run_id
        LEFT JOIN returns r
          ON r.production_run_id = p.production_run_id
         AND r.symbol = p.symbol
        WHERE p.decision = 'PAPER_PICK'
          AND p.symbol IS NOT NULL AND p.symbol != ''
          AND r.t1_return IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket
    """)

    score_stats = score_rows
    score_labels = [r['bucket'] for r in score_rows]
    score_win_rates = [float(r['win_rate']) for r in score_rows]

    # 月度统计
    monthly_stats = {}
    for trade_date in sorted(daily_picks.keys()):
        month = trade_date.strftime('%Y-%m')
        if month not in monthly_stats:
            monthly_stats[month] = {'return': 0, 'count': 0}
        for pick in daily_picks[trade_date]:
            if pick['t1_return'] is not None:
                monthly_stats[month]['return'] += pick['t1_return']
                monthly_stats[month]['count'] += 1

    monthly_labels = sorted(monthly_stats.keys())
    monthly_returns = [round(monthly_stats[m]['return'] * 100 / max(monthly_stats[m]['count'], 1), 2) for m in monthly_labels]

    DASHBOARD_HTML = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>A股 T+1 模拟交易验证看板</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { background: #0f172a; color: #e2e8f0; }
            .card { background: #1e293b; border-radius: 12px; padding: 24px; }
            .stat-value { font-size: 2rem; font-weight: bold; }
            .positive { color: #4ade80; }
            .negative { color: #f87171; }
        </style>
    </head>
    <body class="p-8">
        <div class="max-w-7xl mx-auto">
            <div class="flex justify-between items-center mb-8">
                <h1 class="text-3xl font-bold">A股 T+1 模拟交易验证看板</h1>
                <a href="/" class="text-blue-400 hover:text-blue-300">← 返回首页</a>
            </div>

            <div class="bg-gray-800 border border-gray-700 rounded-lg p-4 mb-6">
                <p class="text-gray-300">验证逻辑：从数据库出票日起，每日平均分配资金买入所有PAPER_PICK，T+1日按收盘价计算收益，验证策略是否能稳定获利。</p>
                <p class="text-gray-400 text-sm mt-2">归一化起点: 1.0 | 模拟区间: {{ equity_dates[0] }} 至 {{ equity_dates[-1] }}</p>
            </div>

            <div class="grid grid-cols-5 gap-4 mb-8">
                <div class="card">
                    <div class="text-gray-400 text-sm">总交易次数</div>
                    <div class="stat-value">{{ total_trades }}</div>
                </div>
                <div class="card">
                    <div class="text-gray-400 text-sm">胜率</div>
                    <div class="stat-value {{ 'positive' if win_rate > 50 else 'negative' }}">{{ "%.1f"|format(win_rate) }}%</div>
                </div>
                <div class="card">
                    <div class="text-gray-400 text-sm">累计收益</div>
                    <div class="stat-value {{ 'positive' if total_return_pct > 0 else 'negative' }}">{{ "%.2f"|format(total_return_pct) }}%</div>
                </div>
                <div class="card">
                    <div class="text-gray-400 text-sm">最终资金</div>
                    <div class="stat-value {{ 'positive' if capital > initial_capital else 'negative' }}">¥{{ "%.0f"|format(capital) }}</div>
                </div>
                <div class="card">
                    <div class="text-gray-400 text-sm">最大回撤</div>
                    <div class="stat-value negative">{{ "%.1f"|format(max_drawdown) }}%</div>
                </div>
            </div>

            <div class="card mb-8">
                <h2 class="text-xl font-semibold mb-4">资金曲线</h2>
                <canvas id="equityChart" height="80"></canvas>
            </div>

            <div class="grid grid-cols-2 gap-6 mb-8">
                <div class="card">
                    <h2 class="text-xl font-semibold mb-4">分数段胜率分析</h2>
                    <canvas id="scoreChart" height="150"></canvas>
                </div>
                <div class="card">
                    <h2 class="text-xl font-semibold mb-4">月度平均收益</h2>
                    <canvas id="monthlyChart" height="150"></canvas>
                </div>
            </div>

            <div class="card">
                <h2 class="text-xl font-semibold mb-4">分数段详细数据</h2>
                <table class="w-full">
                    <thead>
                        <tr class="text-gray-400 border-b border-gray-700">
                            <th class="text-left py-2">分数段</th>
                            <th class="text-right py-2">交易数</th>
                            <th class="text-right py-2">盈利数</th>
                            <th class="text-right py-2">胜率</th>
                            <th class="text-right py-2">平均收益</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in score_stats %}
                        <tr class="border-b border-gray-800">
                            <td class="py-2">{{ row.bucket }}</td>
                            <td class="text-right">{{ row.total }}</td>
                            <td class="text-right">{{ row.wins }}</td>
                            <td class="text-right {{ 'positive' if row.win_rate|float > 50 else 'negative' }}">{{ "%.1f"|format(row.win_rate|float) }}%</td>
                            <td class="text-right {{ 'positive' if row.avg_return|float > 0 else 'negative' }}">{{ "%.2f"|format(row.avg_return|float) }}%</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            new Chart(document.getElementById('equityChart'), {
                type: 'line',
                data: {
                    labels: {{ equity_dates | tojson }},
                    datasets: [{
                        label: '资金',
                        data: {{ equity_values | tojson }},
                        borderColor: '#4ade80',
                        backgroundColor: 'rgba(74, 222, 128, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8', maxTicksLimit: 15 } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
                    }
                }
            });

            new Chart(document.getElementById('scoreChart'), {
                type: 'bar',
                data: {
                    labels: {{ score_labels | tojson }},
                    datasets: [{
                        label: '胜率',
                        data: {{ score_win_rates | tojson }},
                        backgroundColor: {{ score_win_rates | tojson }}.map(v => v > 50 ? '#4ade80' : '#f87171')
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' }, max: 100 }
                    }
                }
            });

            new Chart(document.getElementById('monthlyChart'), {
                type: 'bar',
                data: {
                    labels: {{ monthly_labels | tojson }},
                    datasets: [{
                        label: '平均收益%',
                        data: {{ monthly_returns | tojson }},
                        backgroundColor: {{ monthly_returns | tojson }}.map(v => v > 0 ? '#4ade80' : '#f87171')
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
                        y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
                    }
                }
            });
        </script>
    </body>
    </html>
    """

    template = Template(DASHBOARD_HTML)
    return HTMLResponse(content=template.render(
        initial_capital=initial_capital,
        total_trades=total_trades,
        win_rate=win_rate,
        total_return_pct=total_return_pct,
        capital=capital,
        max_drawdown=max_drawdown,
        equity_dates=equity_dates,
        equity_values=equity_values,
        score_stats=score_stats,
        score_labels=score_labels,
        score_win_rates=score_win_rates,
        monthly_labels=monthly_labels,
        monthly_returns=monthly_returns
    ))
