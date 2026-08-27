#!/usr/bin/env python3
"""Eastmoney market-reality capture.

The scanner owns transport, raw domain collection, lineage, and canonical
snapshot assembly. Selection, feature measurement, research interpretation,
alpha, and portfolio actions belong to downstream owners.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import sys

BASE = Path(os.environ.get("XIAOGU_HOME") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BASE))

from xiaogu_forward_snapshot import attach_research_observations, canonical_snapshot

HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}
MAX_PAGES = 100
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
STOCK_FIELDS = ",".join(f"f{i}" for i in range(1, 201))
CAPITAL_FIELDS = ",".join(["f1", "f2", "f3", "f12", "f14"] + [f"f{i}" for i in range(51, 76)])


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _json_payload(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def api_get(url: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch one Eastmoney response through the direct HTTP transport."""
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=timeout) as response:
        payload = _json_payload(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise ValueError("EASTMONEY_RESPONSE_NOT_OBJECT")
    return payload


def normalize_stock_code(value: Any) -> str | None:
    raw = str(value or "").split(".", 1)[0].strip()
    if raw[:2].lower() in {"sh", "sz", "bj"}:
        raw = raw[2:]
    if not raw.isdigit() or len(raw) > 6:
        return None
    return raw.zfill(6)


def stock_codes_from_row(row: Dict[str, Any]) -> list[str]:
    codes = []
    for value in (
        row.get("SECURITY_CODE"), row.get("SECURITY_CODE_A"), row.get("SECUCODE"),
        row.get("stockCode"), row.get("f12"), row.get("symbol"), row.get("code"),
    ):
        code = normalize_stock_code(value)
        if code and code not in codes:
            codes.append(code)
    for item in row.get("codes") or []:
        value = item.get("stock_code") if isinstance(item, dict) else item
        code = normalize_stock_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def result_item_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(len(item) for item in value.values() if isinstance(item, list)) or len(value)
    return 0


def _store_diagnostic(diagnostics: Dict[str, Any] | None, **values: Any) -> None:
    if diagnostics is not None:
        diagnostics.update(values)


def fetch_paginated(
    fs: str,
    page_size: int = 100,
    fields: str = "f12,f14,f2,f3,f5,f6,f8",
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    rows = []
    total = None
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://push2delay.eastmoney.com/api/qt/clist/get?" + urlencode({
            "pn": page, "pz": page_size, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2,
            "invt": 2, "fid": "f3", "fs": fs, "fields": fields,
        }))
        data = payload.get("data") or {}
        if page == 1:
            try:
                total = int(data.get("total"))
            except (TypeError, ValueError):
                total = None
        batch = data.get("diff") or []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        pages = page
        if (total is not None and len(rows) >= total) or len(batch) < page_size:
            break
        time.sleep(0.03)
    _store_diagnostic(diagnostics, pages=pages, reported_total=total, row_count=len(rows), status="PASS" if rows else "EMPTY")
    return rows


def fetch_datacenter(
    report_name: str,
    sort_column: str,
    page_size: int = 500,
    extra_params: Dict[str, Any] | None = None,
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    rows = []
    total = None
    pages = 0
    base = {
        "reportName": report_name, "columns": "ALL", "pageSize": page_size,
        "sortTypes": -1, "sortColumns": sort_column, "source": "WEB",
        "client": "WEB", **(extra_params or {}),
    }
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode({
            **base, "pageNumber": page,
        }))
        result = payload.get("result") or {}
        if page == 1:
            try:
                total = int(result.get("count"))
            except (TypeError, ValueError):
                total = None
        batch = result.get("data") or []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        pages = page
        if (total is not None and len(rows) >= total) or len(batch) < page_size:
            break
        time.sleep(0.03)
    _store_diagnostic(diagnostics, pages=pages, reported_total=total, row_count=len(rows), status="PASS" if rows else "EMPTY")
    return rows


def fetch_report_list(
    query_type: str,
    begin_time: str,
    end_time: str,
    page_size: int = 500,
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Capture research reports as raw evidence, without rating or filtering."""
    rows = []
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://reportapi.eastmoney.com/report/list?" + urlencode({
            "industryCode": "*", "pageSize": page_size, "industry": "*",
            "rating": "*", "ratingChange": "*", "beginTime": begin_time,
            "endTime": end_time, "pageNo": page, "qType": query_type,
        }))
        result = payload.get("data") or payload.get("result") or {}
        batch = result.get("data") if isinstance(result, dict) else result
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                continue
            key = row.get("art_code") or row.get("infoCode") or json.dumps(row, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                rows.append(row)
        if len(batch) < page_size:
            break
        time.sleep(0.03)
    _store_diagnostic(diagnostics, row_count=len(rows), status="PASS" if rows else "EMPTY")
    return rows


def fetch_announcements(page_size: int = 100, diagnostics: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
    rows = []
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://np-anotice-stock.eastmoney.com/api/security/ann?" + urlencode({
            "ann_type": "A", "client_source": "WEB", "f_node": 0,
            "page_index": page, "page_size": page_size, "s_node": 0,
        }))
        data = payload.get("data") or {}
        batch = data.get("list") if isinstance(data, dict) else []
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                continue
            key = row.get("art_code") or row.get("title") or json.dumps(row, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                rows.append(row)
        if len(batch) < page_size:
            break
    _store_diagnostic(diagnostics, row_count=len(rows), status="PASS" if rows else "EMPTY")
    return rows


def fetch_news(page_size: int = 200, diagnostics: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?" + urlencode({
            "client": "web", "biz": "web_724", "column": 350,
            "order": 1, "needInteractData": 0, "page_index": page,
            "page_size": page_size, "req_trace": int(time.time() * 1000),
        }))
        data = payload.get("data") or {}
        batch = data.get("list") if isinstance(data, dict) else []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < page_size:
            break
    _store_diagnostic(diagnostics, row_count=len(rows), status="PASS" if rows else "EMPTY")
    return rows


def _by_symbol(rows: Iterable[Dict[str, Any]]) -> Dict[str, list[Dict[str, Any]]]:
    indexed: Dict[str, list[Dict[str, Any]]] = {}
    for row in rows or []:
        if isinstance(row, dict):
            for code in stock_codes_from_row(row):
                indexed.setdefault(code, []).append(row)
    return indexed


def build_canonical_snapshots(results: Dict[str, Any], source_time: str) -> list[Dict[str, Any]]:
    """Attach same-time raw observations and create one canonical row per quote."""
    stocks = [row for row in results.get("stock_all_a", []) if isinstance(row, dict)]
    capital = _by_symbol(results.get("stock_capital_flow", []))
    earnings = _by_symbol(results.get("earnings_preview", []))
    reports = _by_symbol(results.get("stock_reports", []))
    lhb = _by_symbol(results.get("lhb", []))
    announcements = _by_symbol(results.get("announcements", []))
    shareholder_changes = _by_symbol(results.get("shareholder_changes", []))
    lockup_expiry = _by_symbol(results.get("lockup_expiry", []))
    industry_flow = {
        str(row.get("f14") or "").strip(): row
        for row in results.get("flow_industry", []) or []
        if isinstance(row, dict) and str(row.get("f14") or "").strip()
    }
    industry_reports: Dict[str, list[Dict[str, Any]]] = {}
    for row in results.get("industry_reports", []) or []:
        if isinstance(row, dict):
            name = str(row.get("industryName") or row.get("industry") or "").strip()
            if name:
                industry_reports.setdefault(name, []).append(row)

    snapshots = []
    for row in stocks:
        code = normalize_stock_code(row.get("f12"))
        sector = str(row.get("f100") or "").strip()
        visible = attach_research_observations(
            row,
            stock_capital_flow=(capital.get(code) or [{}])[0],
            earnings_preview=(earnings.get(code) or [{}])[0],
            stock_reports=(reports.get(code) or [])[:5],
            lhb=(lhb.get(code) or [])[:5],
            announcements=(announcements.get(code) or [])[:5],
            shareholder_changes=(shareholder_changes.get(code) or [])[:5],
            lockup_expiry=(lockup_expiry.get(code) or [])[:5],
            industry_flow=industry_flow.get(sector, {}),
            industry_reports=(industry_reports.get(sector) or [])[:5],
        )
        snapshots.append(canonical_snapshot(
            visible, trade_date=source_time[:10], source="eastmoney_api_scan_v2", source_time=source_time,
        ))
    return snapshots


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> str:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, dict):
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return str(path)


def build_market_snapshot(stocks: list[Dict[str, Any]], source_time: str) -> Dict[str, Any]:
    changes = [fnum(row.get("f3"), None) for row in stocks]
    changes = [value for value in changes if value is not None]
    up_count = sum(value > 0 for value in changes)
    return {
        "trade_date": source_time[:10], "timestamp": source_time,
        "quote_count": len(stocks), "up_count": up_count,
        "down_count": sum(value < 0 for value in changes),
        "flat_count": sum(value == 0 for value in changes),
        "breadth_up_pct": round(up_count / len(changes) * 100, 4) if changes else None,
        "limit_up_observation_count": sum(value >= 9.5 for value in changes),
        "large_gain_observation_count": sum(value >= 5 for value in changes),
        "source": "eastmoney_api_scan_v2", "source_version": "market_snapshot_v1",
    }


def _collect(name: str, timings: Dict[str, Any], fetcher: Callable[[], Any], default: Any) -> Any:
    started = time.monotonic()
    try:
        value = fetcher()
        timings[name] = {"status": "PASS" if result_item_count(value) else "EMPTY", "item_count": result_item_count(value), "elapsed_seconds": round(time.monotonic() - started, 4)}
        return value
    except Exception as exc:
        timings[name] = {"status": "ERROR", "item_count": result_item_count(default), "elapsed_seconds": round(time.monotonic() - started, 4), "error": repr(exc)}
        return default


def main() -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description="Eastmoney raw market-reality scanner")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--date", default="", help="Rejected: live capture always uses its actual source timestamp.")
    args = parser.parse_args()
    if args.date:
        parser.error("--date is unsupported for live capture; use stored canonical snapshots for historical replay")
    started = time.monotonic()
    market_now = datetime.now(MARKET_TIMEZONE)
    source_time = market_now.isoformat(timespec="seconds")
    output_dir = Path(args.output_dir) if args.output_dir else BASE / "data" / "live_scan" / source_time[:10] / "eastmoney_scan"
    output_dir.mkdir(parents=True, exist_ok=True)
    timings: Dict[str, Any] = {}
    diagnostics: Dict[str, Any] = {}
    results: Dict[str, Any] = {}

    results["stock_all_a"] = _collect("stock_all_a", timings, lambda: fetch_paginated("m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048", 100, STOCK_FIELDS, diagnostics.setdefault("stock_all_a", {})), [])
    results["flow_industry"] = _collect("flow_industry", timings, lambda: fetch_paginated("m:90+t:2", 100, "f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87", diagnostics.setdefault("flow_industry", {})), [])
    results["flow_concept"] = _collect("flow_concept", timings, lambda: fetch_paginated("m:90+t:3", 100, "f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87", diagnostics.setdefault("flow_concept", {})), [])
    results["stock_capital_flow"] = _collect("stock_capital_flow", timings, lambda: fetch_paginated("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", 100, CAPITAL_FIELDS, diagnostics.setdefault("stock_capital_flow", {})), [])
    results["lhb"] = _collect("lhb", timings, lambda: fetch_datacenter("RPT_DAILYBILLBOARD_DETAILSNEW", "TRADE_DATE,DEAL_AMOUNT_RATIO", diagnostics=diagnostics.setdefault("lhb", {})), [])
    recent = (market_now - timedelta(days=7)).strftime("%Y-%m-%d")
    today = market_now.strftime("%Y-%m-%d")
    results["earnings_preview"] = _collect("earnings_preview", timings, lambda: fetch_datacenter("RPT_LICO_FN_CPD", "NOTICE_DATE", diagnostics=diagnostics.setdefault("earnings_preview", {})), [])
    results["shareholder_changes"] = _collect("shareholder_changes", timings, lambda: fetch_datacenter("RPT_SHARE_HOLDER_INCREASE", "END_DATE", diagnostics=diagnostics.setdefault("shareholder_changes", {})), [])
    results["lockup_expiry"] = _collect("lockup_expiry", timings, lambda: fetch_datacenter("RPT_LIFT_STAGE", "FREE_DATE", diagnostics=diagnostics.setdefault("lockup_expiry", {})), [])
    results["org_survey"] = _collect("org_survey", timings, lambda: fetch_datacenter("RPT_ORG_SURVEY", "NOTICE_DATE", extra_params={"filter": f"(NOTICE_DATE>='{recent}')"}, diagnostics=diagnostics.setdefault("org_survey", {})), [])
    results["hsgt_holdings"] = _collect("hsgt_holdings", timings, lambda: fetch_datacenter("RPT_MUTUAL_HOLDSTOCKNORTH_STA", "TRADE_DATE", diagnostics=diagnostics.setdefault("hsgt_holdings", {})), [])
    results["hsgt_deals"] = _collect("hsgt_deals", timings, lambda: fetch_datacenter("RPT_MUTUAL_DEAL_HISTORY", "TRADE_DATE", diagnostics=diagnostics.setdefault("hsgt_deals", {})), [])
    results["stock_reports"] = _collect("stock_reports", timings, lambda: fetch_report_list("0", recent, today, diagnostics=diagnostics.setdefault("stock_reports", {})), [])
    results["industry_reports"] = _collect("industry_reports", timings, lambda: fetch_report_list("1", recent, today, diagnostics=diagnostics.setdefault("industry_reports", {})), [])
    results["announcements"] = _collect("announcements", timings, lambda: fetch_announcements(diagnostics=diagnostics.setdefault("announcements", {})), [])
    results["news_kuaixun"] = _collect("news_kuaixun", timings, lambda: fetch_news(diagnostics=diagnostics.setdefault("news_kuaixun", {})), [])
    results["external_market"] = []
    results["indexes"] = []
    results["market_capital_flow"] = []

    files = {}
    for name, rows in results.items():
        files[name] = _write_jsonl(output_dir / f"{name}.jsonl", rows if isinstance(rows, list) else [rows])
    stocks = results["stock_all_a"]
    market = build_market_snapshot(stocks, source_time)
    snapshots = build_canonical_snapshots(results, source_time)
    files["canonical_snapshots"] = _write_jsonl(output_dir / "canonical_snapshots.jsonl", snapshots)
    market_path = output_dir / "canonical_market_snapshot.json"
    market_path.write_text(json.dumps(market, ensure_ascii=False, indent=2), encoding="utf-8")
    files["canonical_market_snapshot"] = str(market_path)

    persistence = {"status": "SKIPPED", "reason": "XIAOGU_PERSIST_DB_not_set"}
    if os.environ.get("XIAOGU_PERSIST_DB") == "1":
        try:
            from xiaogu_db import insert_scan_session, record_snapshot, upsert_scan_market_data
            session_id = insert_scan_session(
                trade_date=source_time[:10], scan_time=source_time, source_id="eastmoney_api_scan_v2",
                quotes_count=len(stocks), captured_count=len(stocks),
                scan_dir=str(output_dir), market_snapshot=market,
                source_status=diagnostics, source_counts={name: result_item_count(value) for name, value in results.items()},
                source_diagnostics=timings,
            )
            upsert_scan_market_data(session_id, source_time[:10], source_time, results, timings)
            for snapshot in snapshots:
                record_snapshot(snapshot)
            persistence = {"status": "PASS", "scan_session_id": session_id, "snapshot_count": len(snapshots)}
        except Exception as exc:
            persistence = {"status": "FAILED", "error": repr(exc)}

    summary = {
        "source": "eastmoney_api_scan_v2", "pipeline_version": "market_reality_capture_v1",
        "source_time": source_time, "raw_domain_counts": {name: result_item_count(value) for name, value in results.items()},
        "canonical_snapshot_count": len(snapshots), "canonical_market_snapshot": market,
        "scanner_contract": {
            "owner": "scrapy_scanner.runner_v2", "responsibility": "DATA_CAPTURE_ONLY",
            "selection": False, "ranking": False, "strategy_score": False, "portfolio_action": False,
        },
        "lineage": {"source": "eastmoney", "source_time": source_time, "source_version": "market_reality_capture_v1"},
        "domain_timings": timings, "fetch_diagnostics": diagnostics,
        "database_persistence": persistence, "files": files,
        "elapsed_seconds": round(time.monotonic() - started, 4),
    }
    for filename in ("scan_summary.json", "xiaogu_scan_summary.json", "xiaogu_scan_summary_runner.json"):
        (output_dir / filename).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return summary


if __name__ == "__main__":
    main()
