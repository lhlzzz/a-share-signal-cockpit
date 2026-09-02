#!/usr/bin/env python3
"""Eastmoney market-reality capture.

The scanner owns transport, raw domain collection, lineage, and canonical
snapshot assembly. Selection, feature measurement, research interpretation,
alpha, and portfolio actions belong to downstream owners.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import http.client
from zoneinfo import ZoneInfo

import sys

BASE = Path(os.environ.get("XIAOGU_HOME") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BASE))

from xiaogu_forward_snapshot import attach_research_observations, build_scan_lineage_id, validate_and_build_canonical_snapshot
from xiaogu_forward_eligibility import cheap_eligibility_blockers

HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}
MAX_PAGES = 100
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
LIGHT_STOCK_FIELDS = ",".join([
    "f2", "f3", "f5", "f6", "f8", "f10", "f12", "f13", "f14", "f15", "f16", "f17",
    "f1", "f18", "f26", "f43", "f44", "f45", "f46", "f100", "f62", "f125", "f148",
])
STOCK_ALL_A_FS = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048"
CLIST_SORT_FIELD = "f12"
API_GET_RETRIES = 2
MARKET_CODES = {0: "SZ", 1: "SH", 2: "BJ"}
UNIVERSE_ACTIVE = "ACTIVE"
UNIVERSE_HALTED = "HALTED"
UNIVERSE_DELISTED = "DELISTED"
UNIVERSE_NOT_YET_OPEN = "NOT_YET_OPEN"
UNIVERSE_OTHER_NON_TRADABLE = "OTHER_NON_TRADABLE"
UNIVERSE_UNKNOWN = "UNKNOWN"
SOURCE_UNIVERSE_STATUS_FIELDS = ("f125", "f1", "f148", "f26", "source_trade_status", "listing_date", "security_class")
STOCK_FIELDS = LIGHT_STOCK_FIELDS
CAPITAL_FIELDS = ",".join(["f1", "f2", "f3", "f5", "f6", "f8", "f10", "f12", "f14"] + [f"f{i}" for i in range(51, 76)])
CAPITAL_HISTORY_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
DEEP_DOMAINS = (
    "stock_capital_flow", "earnings_preview", "org_survey", "stock_reports", "lhb",
    "capital_history", "announcements", "shareholder_changes", "lockup_expiry", "industry_reports", "news_kuaixun",
)
CRITICAL_SOURCES = frozenset({"stock_all_a"})
OPTIONAL_SOURCES = frozenset(DEEP_DOMAINS + (
    "flow_industry", "flow_concept", "hsgt_holdings", "hsgt_deals",
    "industry_reports", "external_market", "indexes", "market_capital_flow",
))
CANDIDATE_FILTER_BATCH = 40
RECENT_NEWS_LOOKBACK_DAYS = 7
CAPITAL_HISTORY_LOOKBACK_DAYS = 12


class CriticalSourceError(RuntimeError):
    """Raised when a production-critical capture domain fails."""


def fnum(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-"):
        return default
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    return datetime.now(MARKET_TIMEZONE).isoformat(timespec="seconds")


def _secid(code: str | None) -> str | None:
    normalized = normalize_stock_code(code)
    if not normalized:
        return None
    if normalized.startswith(("4", "8", "92")):
        prefix = "0"
    elif normalized.startswith(("5", "6", "9")):
        prefix = "1"
    else:
        prefix = "0"
    return f"{prefix}.{normalized}"


def _code_filter(codes: Iterable[str], extra: str = "") -> str:
    wanted = [normalize_stock_code(code) for code in codes]
    wanted = [code for code in wanted if code]
    clause = ""
    if wanted:
        inner = ",".join(f'"{code}"' for code in wanted)
        clause = f"(SECURITY_CODE in ({inner}))"
    parts = [part.strip() for part in (extra, clause) if part and part.strip()]
    return " AND ".join(f"({part})" for part in parts)


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
    last_exc: Exception | None = None
    for attempt in range(API_GET_RETRIES + 1):
        request = Request(url, headers=HEADERS)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = _json_payload(response.read().decode("utf-8", "replace"))
            if not isinstance(payload, dict):
                raise ValueError("EASTMONEY_RESPONSE_NOT_OBJECT")
            return payload
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            last_exc = exc
            if attempt >= API_GET_RETRIES:
                break
            time.sleep(0.2 * (attempt + 1))
    raise last_exc if last_exc is not None else RuntimeError("EASTMONEY_TRANSPORT_FAILED")


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
        unrelated_symbols = values.get("unrelated_symbols")
        unrelated_rows = values.get("unrelated_rows", 0)
        if not isinstance(unrelated_symbols, (list, tuple, set)):
            unrelated_symbols = []
        started = diagnostics.pop("_fetch_started_at", None)
        values["unrelated_symbols"] = sorted(set(unrelated_symbols))
        values["unrelated_rows"] = int(unrelated_rows)
        values.setdefault("fetch_started_at", started or diagnostics.get("fetch_started_at") or _iso_now())
        values.setdefault("fetch_finished_at", _iso_now())
        diagnostics.update(values)


def _start_diagnostic(diagnostics: Dict[str, Any] | None) -> None:
    if diagnostics is not None:
        diagnostics["_fetch_started_at"] = _iso_now()


def fetch_ulist(
    secids: Iterable[str],
    fields: str,
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Fetch only the requested candidate quotes; never the full market table."""
    _start_diagnostic(diagnostics)
    wanted = [item for item in (_secid(code) for code in secids) if item]
    rows: list[Dict[str, Any]] = []
    requests = 0
    for index in range(0, len(wanted), CANDIDATE_FILTER_BATCH):
        batch = wanted[index:index + CANDIDATE_FILTER_BATCH]
        payload = api_get("https://push2.eastmoney.com/api/qt/ulist.np/get?" + urlencode({
            "fltt": 2, "invt": 2, "fields": fields, "secids": ",".join(batch),
        }))
        requests += 1
        data = payload.get("data") or {}
        batch_rows = data.get("diff") or []
        if isinstance(batch_rows, list):
            rows.extend(item for item in batch_rows if isinstance(item, dict))
        time.sleep(0.03)
    returned = []
    unrelated = 0
    unrelated_symbols = []
    wanted_codes = {normalize_stock_code(item.split(".", 1)[-1]) for item in wanted}
    kept = []
    for row in rows:
        codes = stock_codes_from_row(row)
        if any(code in wanted_codes for code in codes):
            kept.append(row)
            returned.extend(codes)
        else:
            unrelated += 1
            unrelated_symbols.extend(codes)
    _store_diagnostic(
        diagnostics,
        pages=requests,
        reported_total=len(wanted),
        row_count=len(kept),
        requested_symbols=sorted(code for code in wanted_codes if code),
        returned_symbols=sorted(set(returned)),
        unrelated_symbols=sorted(set(unrelated_symbols)),
        unrelated_rows=unrelated,
        request_count=requests,
        response_count=len(rows),
        status="PASS" if kept or not wanted else "EMPTY",
    )
    return kept


def fetch_capital_history(
    candidate_codes: Iterable[str],
    *,
    begin_date: str = "",
    end_date: str = "",
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Capture provider-returned daily capital history for L2 candidates only.

    Eastmoney's history endpoint is optional.  Provider availability is kept
    in diagnostics; no row is synthesized when the endpoint is empty or fails.
    """
    _start_diagnostic(diagnostics)
    wanted = sorted({code for code in (normalize_stock_code(item) for item in candidate_codes) if code})
    if not wanted:
        _store_diagnostic(
            diagnostics, requested_symbols=[], returned_symbols=[], unrelated_symbols=[],
            unrelated_rows=0, request_count=0, response_count=0, row_count=0,
            status="SKIPPED", evidence_status="SOURCE_UNAVAILABLE",
        )
        return []
    fetched_at = _iso_now()
    rows: list[Dict[str, Any]] = []
    requests = 0
    for code in wanted:
        secid = _secid(code)
        if not secid:
            continue
        payload = api_get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline?" + urlencode({
            "lmt": 0, "klt": 101, "fqt": 0, "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": CAPITAL_HISTORY_FIELDS,
        }))
        requests += 1
        data = payload.get("data") or {}
        klines = data.get("klines") if isinstance(data, dict) else []
        for item in klines or []:
            if isinstance(item, str):
                values = item.split(",")
            elif isinstance(item, (list, tuple)):
                values = list(item)
            else:
                continue
            if len(values) < 3:
                continue
            trade_date = str(values[0] or "")[:10]
            if not trade_date or (begin_date and trade_date < begin_date) or (end_date and trade_date > end_date):
                continue
            def number(index: int) -> float | None:
                return fnum(values[index], None) if index < len(values) else None
            rows.append({
                "symbol": code,
                "trade_date": trade_date,
                "capital_flow": number(1),
                "capital_flow_ratio": number(6),
                "main_force_flow": number(5),
                "source": "eastmoney_capital_history",
                "source_id": "eastmoney_capital_history",
                "source_time": f"{trade_date}T15:00:00+08:00",
                "available_at": fetched_at,
                "observation_class": "FORWARD_OBSERVATION_ONLY",
                "historical_training_eligible": False,
                "availability_provenance": "FETCH_TIME_ONLY",
                "provider_fields": CAPITAL_HISTORY_FIELDS.split(","),
            })
    returned = sorted({str(row["symbol"]) for row in rows})
    _store_diagnostic(
        diagnostics, requested_symbols=wanted, returned_symbols=returned,
        unrelated_symbols=[], unrelated_rows=0, request_count=requests,
        response_count=len(rows), row_count=len(rows),
        status="PASS" if rows else "EMPTY",
        evidence_status="OBSERVED" if rows else "SOURCE_UNAVAILABLE",
    )
    return rows


def _row_identity(row: Dict[str, Any]) -> str | None:
    codes = stock_codes_from_row(row)
    if codes:
        return codes[0]
    marker = str(row.get("f12") or "").strip()
    return marker or None


def _clist_diff(data: Dict[str, Any] | None) -> list[Dict[str, Any]]:
    batch = (data or {}).get("diff")
    if isinstance(batch, dict):
        ordered = sorted(batch, key=lambda key: int(key) if str(key).isdigit() else str(key))
        return [batch[key] for key in ordered if isinstance(batch.get(key), dict)]
    if isinstance(batch, list):
        return [item for item in batch if isinstance(item, dict)]
    return []


def fetch_paginated(
    fs: str,
    page_size: int = 100,
    fields: str = "f12,f14,f2,f3,f5,f6,f8",
    diagnostics: Dict[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    _start_diagnostic(diagnostics)
    rows: list[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    duplicates: list[str] = []
    unidentified = 0
    short_nonlast_pages: list[int] = []
    total = None
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        payload = api_get("https://push2delay.eastmoney.com/api/qt/clist/get?" + urlencode({
            "pn": page, "pz": page_size, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2,
            "invt": 2, "fid": CLIST_SORT_FIELD, "fs": fs, "fields": fields,
        }))
        data = payload.get("data") or {}
        if page == 1:
            try:
                total = int(data.get("total"))
            except (TypeError, ValueError):
                total = None
        batch = _clist_diff(data if isinstance(data, dict) else {})
        if not batch:
            if total is not None and len(rows) < total:
                _store_diagnostic(
                    diagnostics, pages=page, reported_total=total, row_count=len(rows),
                    expected_universe_count=total, observed_universe_count=len(seen),
                    duplicate_symbol_count=len(set(duplicates)), missing_symbol_count=max(0, (total or 0) - len(seen)),
                    sort_field=CLIST_SORT_FIELD, status="PAGING_OR_PROVIDER_PAGE_FAILURE",
                )
                raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:missing_page:{page}:rows={len(rows)}:total={total}")
            break
        for item in batch:
            ident = _row_identity(item)
            if ident is None:
                unidentified += 1
            elif ident in seen:
                duplicates.append(ident)
            else:
                seen[ident] = page
            rows.append(item)
        pages = page
        is_last = (total is not None and len(rows) >= total) or len(batch) < page_size
        if not is_last and len(batch) != page_size:
            short_nonlast_pages.append(page)
        if is_last:
            break
        time.sleep(0.03)
    if pages and short_nonlast_pages:
        _store_diagnostic(
            diagnostics, pages=pages, reported_total=total, row_count=len(rows),
            expected_universe_count=total, observed_universe_count=len(seen),
            duplicate_symbol_count=len(set(duplicates)), missing_symbol_count=None,
            sort_field=CLIST_SORT_FIELD, status="PAGING_OR_PROVIDER_PAGE_FAILURE",
        )
        raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:short_page:{short_nonlast_pages}")
    if unidentified:
        _store_diagnostic(
            diagnostics, pages=pages, reported_total=total, row_count=len(rows),
            expected_universe_count=total, observed_universe_count=len(seen),
            duplicate_symbol_count=len(set(duplicates)), missing_symbol_count=None,
            sort_field=CLIST_SORT_FIELD, status="PAGING_OR_PROVIDER_PAGE_FAILURE",
        )
        raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:unidentified_rows:{unidentified}")
    if duplicates:
        _store_diagnostic(
            diagnostics, pages=pages, reported_total=total, row_count=len(rows),
            expected_universe_count=total, observed_universe_count=len(seen),
            duplicate_symbol_count=len(set(duplicates)),
            missing_symbol_count=None if total is None else max(0, total - len(seen)),
            sort_field=CLIST_SORT_FIELD, status="PAGING_OR_PROVIDER_PAGE_FAILURE",
        )
        raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:duplicate_symbols:{len(set(duplicates))}")
    if total is not None and len(seen) != total:
        _store_diagnostic(
            diagnostics, pages=pages, reported_total=total, row_count=len(rows),
            expected_universe_count=total, observed_universe_count=len(seen),
            duplicate_symbol_count=0, missing_symbol_count=max(0, total - len(seen)),
            sort_field=CLIST_SORT_FIELD, status="PAGING_OR_PROVIDER_PAGE_FAILURE",
        )
        raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:missing_symbols:{max(0, total - len(seen))}")
    if total is not None and pages >= MAX_PAGES and len(rows) < total:
        raise CriticalSourceError(f"PAGING_OR_PROVIDER_PAGE_FAILURE:truncated:{len(rows)}:{total}")
    _store_diagnostic(
        diagnostics,
        pages=pages,
        reported_total=total,
        row_count=len(rows),
        expected_universe_count=total,
        observed_universe_count=len(seen),
        duplicate_symbol_count=0,
        missing_symbol_count=0 if total is not None else None,
        sort_field=CLIST_SORT_FIELD,
        status="PASS" if rows else "EMPTY",
    )
    return rows


def fetch_datacenter(
    report_name: str,
    sort_column: str,
    page_size: int = 500,
    extra_params: Dict[str, Any] | None = None,
    diagnostics: Dict[str, Any] | None = None,
    candidate_codes: Iterable[str] | None = None,
) -> list[Dict[str, Any]]:
    _start_diagnostic(diagnostics)
    wanted = [normalize_stock_code(code) for code in (candidate_codes or [])]
    wanted = [code for code in wanted if code]
    if candidate_codes is not None and not wanted:
        _store_diagnostic(
            diagnostics, pages=0, reported_total=0, row_count=0, requested_symbols=[],
            returned_symbols=[], unrelated_rows=0, request_count=0, response_count=0, status="SKIPPED",
        )
        return []
    rows = []
    total = None
    pages = 0
    requests = 0
    batches = [wanted[index:index + CANDIDATE_FILTER_BATCH] for index in range(0, len(wanted), CANDIDATE_FILTER_BATCH)] or [None]
    for batch in batches:
        extra = dict(extra_params or {})
        if batch:
            extra["filter"] = _code_filter(batch, extra.get("filter") or "")
        base = {
            "reportName": report_name, "columns": "ALL", "pageSize": page_size,
            "sortTypes": -1, "sortColumns": sort_column, "source": "WEB",
            "client": "WEB", **extra,
        }
        batch_rows = []
        for page in range(1, MAX_PAGES + 1):
            payload = api_get("https://datacenter-web.eastmoney.com/api/data/v1/get?" + urlencode({
                **base, "pageNumber": page,
            }))
            requests += 1
            result = payload.get("result") or {}
            if page == 1 and batch is batches[0]:
                try:
                    total = int(result.get("count"))
                except (TypeError, ValueError):
                    total = None
            page_batch = result.get("data") or []
            if not isinstance(page_batch, list) or not page_batch:
                break
            batch_rows.extend(item for item in page_batch if isinstance(item, dict))
            pages += 1
            if len(page_batch) < page_size:
                break
            time.sleep(0.03)
        rows.extend(batch_rows)
    returned = []
    unrelated = 0
    unrelated_symbols = []
    wanted_set = set(wanted)
    kept = []
    for row in rows:
        codes = stock_codes_from_row(row)
        if not wanted_set or any(code in wanted_set for code in codes):
            kept.append(row)
            returned.extend(codes)
        else:
            unrelated += 1
            unrelated_symbols.extend(codes)
    _store_diagnostic(
        diagnostics, pages=pages, reported_total=total, row_count=len(kept),
        requested_symbols=wanted, returned_symbols=sorted(set(returned)),
        unrelated_symbols=sorted(set(unrelated_symbols)),
        unrelated_rows=unrelated, request_count=requests, response_count=len(rows),
        status="PASS" if kept else "EMPTY",
    )
    return kept


def fetch_report_list(
    query_type: str,
    begin_time: str,
    end_time: str,
    page_size: int = 500,
    diagnostics: Dict[str, Any] | None = None,
    candidate_codes: Iterable[str] | None = None,
) -> list[Dict[str, Any]]:
    """Capture research reports as raw evidence, without rating or filtering."""
    _start_diagnostic(diagnostics)
    wanted = [normalize_stock_code(code) for code in (candidate_codes or [])]
    wanted = [code for code in wanted if code]
    if candidate_codes is not None and not wanted:
        _store_diagnostic(
            diagnostics, row_count=0, requested_symbols=[], returned_symbols=[],
            unrelated_rows=0, request_count=0, response_count=0, status="SKIPPED",
        )
        return []
    rows = []
    seen = set()
    requests = 0
    codes = wanted or [None]
    for code in codes:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "industryCode": "*", "pageSize": page_size, "industry": "*",
                "rating": "*", "ratingChange": "*", "beginTime": begin_time,
                "endTime": end_time, "pageNo": page, "qType": query_type,
            }
            if code:
                params["code"] = code
            payload = api_get("https://reportapi.eastmoney.com/report/list?" + urlencode(params))
            requests += 1
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
    returned = []
    unrelated = 0
    unrelated_symbols = []
    wanted_set = set(wanted)
    kept = []
    for row in rows:
        codes_in_row = stock_codes_from_row(row)
        if not wanted_set or any(code in wanted_set for code in codes_in_row):
            kept.append(row)
            returned.extend(codes_in_row)
        else:
            unrelated += 1
            unrelated_symbols.extend(codes_in_row)
    _store_diagnostic(
        diagnostics, row_count=len(kept), requested_symbols=wanted,
        returned_symbols=sorted(set(returned)), unrelated_symbols=sorted(set(unrelated_symbols)),
        unrelated_rows=unrelated,
        request_count=requests, response_count=len(rows),
        status="PASS" if kept else "EMPTY",
    )
    return kept


def fetch_announcements(
    page_size: int = 100,
    diagnostics: Dict[str, Any] | None = None,
    candidate_codes: Iterable[str] | None = None,
) -> list[Dict[str, Any]]:
    _start_diagnostic(diagnostics)
    wanted = [normalize_stock_code(code) for code in (candidate_codes or [])]
    wanted = [code for code in wanted if code]
    if candidate_codes is not None and not wanted:
        _store_diagnostic(
            diagnostics, row_count=0, requested_symbols=[], returned_symbols=[],
            unrelated_rows=0, request_count=0, response_count=0, status="SKIPPED",
        )
        return []
    rows = []
    seen = set()
    requests = 0
    codes = wanted or [None]
    for code in codes:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "ann_type": "A", "client_source": "WEB", "f_node": 0,
                "page_index": page, "page_size": page_size, "s_node": 0,
            }
            if code:
                params["stock"] = code
            payload = api_get("https://np-anotice-stock.eastmoney.com/api/security/ann?" + urlencode(params))
            requests += 1
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
    returned = []
    unrelated = 0
    unrelated_symbols = []
    wanted_set = set(wanted)
    kept = []
    for row in rows:
        codes_in_row = stock_codes_from_row(row)
        if not wanted_set or any(item in wanted_set for item in codes_in_row):
            kept.append(row)
            returned.extend(codes_in_row)
        else:
            unrelated += 1
            unrelated_symbols.extend(codes_in_row)
    _store_diagnostic(
        diagnostics, row_count=len(kept), requested_symbols=wanted,
        returned_symbols=sorted(set(returned)), unrelated_symbols=sorted(set(unrelated_symbols)),
        unrelated_rows=unrelated,
        request_count=requests, response_count=len(rows),
        status="PASS" if kept else "EMPTY",
    )
    return kept


def fetch_news(
    page_size: int = 50,
    diagnostics: Dict[str, Any] | None = None,
    candidate_codes: Iterable[str] | None = None,
) -> list[Dict[str, Any]]:
    _start_diagnostic(diagnostics)
    wanted = [normalize_stock_code(code) for code in (candidate_codes or [])]
    wanted = [code for code in wanted if code]
    if candidate_codes is not None and not wanted:
        _store_diagnostic(
            diagnostics, row_count=0, requested_symbols=[], returned_symbols=[],
            unrelated_rows=0, request_count=0, response_count=0, status="SKIPPED",
        )
        return []
    rows = []
    requests = 0
    for code in wanted:
        payload = api_get("https://searchapi.eastmoney.com/api/info/Search?" + urlencode({
            "type": 301, "pageIndex": 1, "pageSize": page_size, "keyword": code,
        }))
        requests += 1
        data = payload.get("data") or payload.get("result") or {}
        batch = data.get("list") if isinstance(data, dict) else data
        if isinstance(batch, list):
            for item in batch:
                if isinstance(item, dict):
                    rows.append(item)
        time.sleep(0.03)
    returned = []
    unrelated = 0
    unrelated_symbols = []
    wanted_set = set(wanted)
    kept = []
    for row in rows:
        codes_in_row = stock_codes_from_row(row)
        if any(item in wanted_set for item in codes_in_row):
            kept.append(row)
            returned.extend(codes_in_row)
        else:
            unrelated += 1
            unrelated_symbols.extend(codes_in_row)
    _store_diagnostic(
        diagnostics, row_count=len(kept), requested_symbols=wanted,
        returned_symbols=sorted(set(returned)), unrelated_symbols=sorted(set(unrelated_symbols)),
        unrelated_rows=unrelated,
        request_count=requests, response_count=len(rows),
        status="PASS" if kept else "EMPTY",
    )
    return kept


def _by_symbol(rows: Iterable[Dict[str, Any]]) -> Dict[str, list[Dict[str, Any]]]:
    indexed: Dict[str, list[Dict[str, Any]]] = {}
    for row in rows or []:
        if isinstance(row, dict):
            for code in stock_codes_from_row(row):
                indexed.setdefault(code, []).append(row)
    return indexed


def _quote_number(row: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if row.get(key) not in (None, "", "-"):
            try:
                return float(str(row[key]).replace(",", "").replace("%", ""))
            except (TypeError, ValueError):
                return None
    return None


def _market_name(row: Dict[str, Any]) -> str | None:
    explicit = row.get("market")
    if explicit not in (None, "", "-"):
        return str(explicit)
    code = row.get("f13")
    try:
        return MARKET_CODES.get(int(code))
    except (TypeError, ValueError):
        return None


def _trade_status(row: Dict[str, Any]) -> str:
    return classify_universe_row(row)["trade_status"]


def _source_int(row: Dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value in (None, "", "-"):
            return None
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None
    return None


def _listing_date_yyyymmdd(row: Dict[str, Any]) -> str | None:
    if "listing_date" not in row and "f26" not in row:
        return None
    value = row.get("listing_date")
    if value in (None, "", "-"):
        value = row.get("f26")
    if value in (None, "", "-"):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def _has_source_status(row: Dict[str, Any]) -> bool:
    return any(key in row for key in SOURCE_UNIVERSE_STATUS_FIELDS)


def _session_quote_complete(row: Dict[str, Any]) -> bool:
    return (
        _quote_number(row, "f2", "price") is not None
        and _quote_number(row, "f5", "volume") is not None
        and _quote_number(row, "f6", "amount") is not None
        and _quote_number(row, "f62", "main_net_inflow") is not None
    )


def classify_universe_row(row: Dict[str, Any], trade_date: str = "") -> Dict[str, Any]:
    """Map Eastmoney source metadata onto one exclusive universe_state.

    f125 is the clist trade/listing status: 0 trading, 1 non-trading/delisted,
    2 halted-but-listed, 4 not yet listed. f1=2 is an A-share stock. Session
    quotes are never inferred from previous close (f18).
    """
    symbol = (stock_codes_from_row(row) or [None])[0]
    security_class = _source_int(row, "security_class", "f1")
    status_code = _source_int(row, "source_trade_status", "f125")
    type_code = _source_int(row, "security_type", "f148")
    listing_date = _listing_date_yyyymmdd(row)
    today = "".join(ch for ch in str(trade_date or datetime.now(MARKET_TIMEZONE).strftime("%Y%m%d")) if ch.isdigit())[:8]
    session_complete = _session_quote_complete(row)
    if not symbol:
        state = UNIVERSE_UNKNOWN
    elif not _has_source_status(row):
        state = UNIVERSE_ACTIVE if session_complete else UNIVERSE_UNKNOWN
    elif security_class is not None and security_class != 2:
        state = UNIVERSE_OTHER_NON_TRADABLE
    elif status_code == 4 or (type_code is not None and type_code & 16):
        state = UNIVERSE_NOT_YET_OPEN
    elif listing_date is None and not session_complete and status_code not in {1, 2}:
        state = UNIVERSE_NOT_YET_OPEN
    elif listing_date is not None and today and listing_date > today:
        state = UNIVERSE_NOT_YET_OPEN
    elif status_code == 1:
        state = UNIVERSE_DELISTED
    elif status_code == 2:
        state = UNIVERSE_HALTED
    elif status_code == 0 or session_complete:
        state = UNIVERSE_ACTIVE
    else:
        state = UNIVERSE_UNKNOWN
    production_required = state == UNIVERSE_ACTIVE
    if state == UNIVERSE_ACTIVE and session_complete:
        trade_status = "TRADING"
    elif state == UNIVERSE_NOT_YET_OPEN:
        trade_status = "NOT_YET_OPEN"
    elif state in {UNIVERSE_HALTED, UNIVERSE_DELISTED, UNIVERSE_OTHER_NON_TRADABLE}:
        trade_status = "HALTED"
    elif row.get("halted") or row.get("is_suspended") or row.get("in_halted"):
        trade_status = "HALTED"
    elif _quote_number(row, "f2", "price") is None or (_quote_number(row, "f5", "volume") or 0) <= 0:
        trade_status = "HALTED"
    else:
        trade_status = "TRADING"
    halted = state in {UNIVERSE_HALTED, UNIVERSE_DELISTED} or trade_status == "HALTED"
    buyable = False if state in {UNIVERSE_NOT_YET_OPEN, UNIVERSE_OTHER_NON_TRADABLE, UNIVERSE_DELISTED, UNIVERSE_HALTED, UNIVERSE_UNKNOWN} else None
    return {
        "symbol": symbol,
        "universe_state": state,
        "listing_status": state,
        "production_required": production_required,
        "trade_status": trade_status,
        "halted": halted,
        "buyable": buyable,
        "security_class": security_class,
        "source_trade_status": status_code,
        "listing_date": listing_date,
        "session_quote_complete": session_complete,
    }


def _annotate_source_row(row: Dict[str, Any], trade_date: str = "") -> Dict[str, Any]:
    item = dict(row)
    classified = classify_universe_row(item, trade_date=trade_date)
    item["universe_state"] = classified["universe_state"]
    item["listing_status"] = classified["listing_status"]
    item["production_required"] = classified["production_required"]
    item["trade_status"] = classified["trade_status"]
    item["halted"] = classified["halted"]
    item["previous_close"] = _quote_number(item, "previous_close", "f18")
    if classified["buyable"] is False:
        item["buyable"] = False
    return item


def universe_audit(rows: list[Dict[str, Any]], trade_date: str = "") -> Dict[str, Any]:
    exclusive = {
        UNIVERSE_ACTIVE: 0,
        UNIVERSE_HALTED: 0,
        UNIVERSE_DELISTED: 0,
        UNIVERSE_NOT_YET_OPEN: 0,
        UNIVERSE_OTHER_NON_TRADABLE: 0,
        UNIVERSE_UNKNOWN: 0,
    }
    active_complete = 0
    active_incomplete = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        classified = classify_universe_row(row, trade_date=trade_date)
        state = classified["universe_state"]
        exclusive[state] = exclusive.get(state, 0) + 1
        if classified["production_required"]:
            if classified["session_quote_complete"]:
                active_complete += 1
            else:
                active_incomplete += 1
    source_observed = sum(exclusive.values())
    production_universe = exclusive[UNIVERSE_ACTIVE]
    return {
        "source_observed": source_observed,
        "production_universe": production_universe,
        "non_production_observed": source_observed - production_universe,
        "active_required": production_universe,
        "active_required_complete": active_complete,
        "active_required_incomplete": active_incomplete,
        "exclusive_classification": exclusive,
        "HALTED": exclusive[UNIVERSE_HALTED],
        "DELISTED": exclusive[UNIVERSE_DELISTED],
        "NOT_YET_OPEN": exclusive[UNIVERSE_NOT_YET_OPEN],
        "OTHER_NON_TRADABLE": exclusive[UNIVERSE_OTHER_NON_TRADABLE],
        "UNKNOWN": exclusive[UNIVERSE_UNKNOWN],
        "ACTIVE": exclusive[UNIVERSE_ACTIVE],
    }


def detect_capital_candidates(
    stocks: Iterable[Dict[str, Any]],
    *,
    industry_rows: Iterable[Dict[str, Any]] | None = None,
    market: Dict[str, Any] | None = None,
) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    """Route expensive deep-fetch budget. This is not ranking or selection."""
    stocks = [row for row in (stocks or []) if isinstance(row, dict)]
    industry_move = {
        str(row.get("f14") or "").strip(): _quote_number(row, "f3")
        for row in (industry_rows or [])
        if isinstance(row, dict) and str(row.get("f14") or "").strip()
    }
    market_breadth = None if not isinstance(market, dict) else market.get("breadth_up_pct")
    candidates = []
    rejected = []
    routing = []
    for row in stocks or []:
        symbol = row.get("f12") or row.get("symbol")
        blockers = cheap_eligibility_blockers(row)
        route = {"symbol": symbol, "deep_fetch_required": False, "routing_reasons": []}
        if blockers:
            rejected.append({"symbol": symbol, "blockers": blockers, **route})
            routing.append(route)
            continue
        pct_change = _quote_number(row, "f3", "pct_change")
        main_flow = _quote_number(row, "f62", "main_net_inflow")
        turnover = _quote_number(row, "f8", "turnover")
        relative_volume = _quote_number(row, "f10", "relative_volume")
        price = _quote_number(row, "f2", "price")
        low = _quote_number(row, "f16", "low")
        high = _quote_number(row, "f15", "high")
        amount = _quote_number(row, "f6", "amount")
        close_position = (price - low) / (high - low) if price is not None and high is not None and low is not None and high > low else None
        industry_pct = industry_move.get(str(row.get("f100") or row.get("industry") or "").strip())
        evidence = []
        if main_flow is not None and main_flow != 0:
            evidence.append("basic_capital_flow")
        if pct_change is not None and pct_change != 0:
            evidence.append("price_response")
        if turnover is not None and turnover >= 1.0:
            evidence.append("turnover")
        if relative_volume is not None and relative_volume >= 1.0:
            evidence.append("relative_volume")
        if close_position is not None and close_position >= 0.60:
            evidence.append("close_position")
        if industry_pct is not None and industry_pct != 0:
            evidence.append("industry_movement")
        if amount is not None and amount > 0 and relative_volume is not None and relative_volume >= 1.5:
            evidence.append("amount_activity")
        if market_breadth is not None and float(market_breadth) > 50 and pct_change is not None and pct_change != 0:
            evidence.append("market_movement")
        triggered = len(evidence) >= 2
        route.update({"deep_fetch_required": triggered, "routing_reasons": evidence})
        routing.append(route)
        if triggered:
            item = dict(row)
            item["deep_fetch_required"] = True
            item["routing_reasons"] = evidence
            candidates.append(item)
        else:
            rejected.append({"symbol": symbol, "blockers": ["NO_DEEP_FETCH_TRIGGER"], **route})
    l1_eligible_count = sum(not cheap_eligibility_blockers(row) for row in stocks)
    return candidates, {
        "input_count": len(stocks),
        "full_universe_count": len(stocks),
        "full_l0_count": len(stocks),
        "l1_eligible_count": l1_eligible_count,
        "l1_rejected_count": len(stocks) - l1_eligible_count,
        "l2_routed_count": len(candidates),
        "l2_not_routed_count": l1_eligible_count - len(candidates),
        "l3_requested_count": None,
        "l3_returned_count": None,
        "l3_unrelated_count": None,
        "l3_fetched_count": None,
        "l3_fetch_failed_count": None,
        "alpha_evaluated_count": None,
        "decision_count": None,
        "L2_SELECTION_FEATURES": [
            "basic_capital_flow", "price_response", "turnover", "relative_volume",
            "close_position", "industry_movement", "amount_activity", "market_movement",
        ],
        "rejected": rejected,
        "routing": routing,
        "selection": False,
        "ranking": False,
        "alpha": False,
        "purpose": "RESOURCE_ROUTER",
    }


def build_canonical_snapshots(
    results: Dict[str, Any],
    source_time: str,
    lineage_id: str = "",
    symbols: Iterable[str] | None = None,
    market: Dict[str, Any] | None = None,
    available_at: str = "",
) -> list[Dict[str, Any]]:
    """Create canonical rows; deep observations are attached only for symbols."""
    stocks = [row for row in results.get("stock_all_a", []) if isinstance(row, dict)]
    selected_symbols = {normalize_stock_code(value) for value in (symbols or [])}
    lineage_id = lineage_id or build_scan_lineage_id(
        source="eastmoney_api_scan_v2",
        source_time=source_time,
        producer="scrapy_scanner.runner_v2.build_canonical_snapshots",
        trade_date=source_time[:10],
        scan_nonce=uuid.uuid4().hex,
    )
    capital = _by_symbol(results.get("stock_capital_flow", []))
    capital_history = _by_symbol(results.get("capital_history", []))
    earnings = _by_symbol(results.get("earnings_preview", []))
    reports = _by_symbol(results.get("stock_reports", []))
    lhb = _by_symbol(results.get("lhb", []))
    announcements = _by_symbol(results.get("announcements", []))
    org_surveys = _by_symbol(results.get("org_survey", []))
    news = _by_symbol(results.get("news_kuaixun", []))
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
        if row.get("production_required") is False:
            continue
        sector = str(row.get("f100") or row.get("industry") or "").strip()
        deep = symbols is None or code in selected_symbols
        visible = dict(row)
        visible["pct_change"] = _quote_number(row, "f3", "pct_change")
        visible["trade_status"] = _trade_status(row)
        visible["market"] = _market_name(row)
        visible["industry"] = sector
        visible["basic_capital_flow"] = _quote_number(row, "f62", "main_net_inflow")
        if available_at:
            visible["available_at"] = available_at
        if isinstance(market, dict):
            visible["market_breadth"] = market.get("breadth_up_pct")
            visible["market_breadth_up_pct"] = market.get("breadth_up_pct")
            visible["market_regime_inputs"] = {
                "up_count": market.get("up_count"),
                "down_count": market.get("down_count"),
                "flat_count": market.get("flat_count"),
                "breadth_up_pct": market.get("breadth_up_pct"),
                "limit_up_observation_count": market.get("limit_up_observation_count"),
                "large_gain_observation_count": market.get("large_gain_observation_count"),
            }
        visible["source_layers"] = ["L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY"]
        if deep:
            visible = attach_research_observations(
                visible,
                stock_capital_flow=(capital.get(code) or [{}])[0],
                capital_history=(capital_history.get(code) or [])[-6:],
                earnings_preview=(earnings.get(code) or [{}])[0],
                org_surveys=(org_surveys.get(code) or [])[:5],
                stock_reports=(reports.get(code) or [])[:5],
                lhb=(lhb.get(code) or [])[:5],
                announcements=(announcements.get(code) or [])[:5],
                shareholder_changes=(shareholder_changes.get(code) or [])[:5],
                lockup_expiry=(lockup_expiry.get(code) or [])[:5],
                industry_flow=industry_flow.get(sector, {}),
                industry_reports=(industry_reports.get(sector) or [])[:5],
                news=(news.get(code) or [])[:5],
            )
            visible["source_layers"] = [
                "L0_LIGHT_MARKET_CAPTURE", "L1_CHEAP_ELIGIBILITY",
                "L2_RESOURCE_ROUTER", "L3_DEEP_CANDIDATE_FETCH",
            ]
        snapshots.append(validate_and_build_canonical_snapshot(
            visible,
            trade_date=source_time[:10],
            source="eastmoney_api_scan_v2",
            source_time=source_time,
            producer="scrapy_scanner.runner_v2.build_canonical_snapshots",
            lineage_id=lineage_id,
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


def _stamp_available_at(value: Any, available_at: str) -> Any:
    if isinstance(value, list):
        stamped = []
        for row in value:
            if isinstance(row, dict) and not row.get("available_at"):
                item = dict(row)
                item["available_at"] = available_at
                stamped.append(item)
            else:
                stamped.append(row)
        return stamped
    if isinstance(value, dict) and value and not value.get("available_at"):
        item = dict(value)
        item["available_at"] = available_at
        return item
    return value


def _raw_field_status(row: Dict[str, Any], key: str) -> str:
    if key not in row:
        return "RAW_FIELD_MISSING"
    value = row.get(key)
    if value is None or value in ("", "-"):
        return "NULL_VALUE"
    try:
        float(str(value).replace(",", "").replace("%", ""))
        return "OK"
    except (TypeError, ValueError):
        return "INVALID_VALUE"


def _critical_row_gaps(row: Dict[str, Any]) -> list[str]:
    gaps = []
    if not stock_codes_from_row(row):
        gaps.append("MISSING_SYMBOL")
    classified = classify_universe_row(row)
    require_session = classified["production_required"] or classified["universe_state"] == UNIVERSE_UNKNOWN
    if require_session:
        if _quote_number(row, "f2", "price") is None:
            gaps.append("MISSING_PRICE")
        if _quote_number(row, "f5", "volume") is None:
            gaps.append("MISSING_VOLUME")
        if _quote_number(row, "f6", "amount") is None:
            gaps.append("MISSING_AMOUNT")
        if _quote_number(row, "f62", "main_net_inflow") is None:
            gaps.append("MISSING_MAIN_NET_INFLOW")
        if classified["universe_state"] == UNIVERSE_UNKNOWN and len(gaps) > (1 if "MISSING_SYMBOL" in gaps else 0):
            gaps.append("UNKNOWN_SECURITY_STATE")
    return gaps


def _incomplete_audit(rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    distribution: Dict[str, int] = {}
    compact = []
    for index, row in enumerate(rows):
        gaps = _critical_row_gaps(row)
        if not gaps:
            continue
        key = "+".join(gaps)
        distribution[key] = distribution.get(key, 0) + 1
        if len(gaps) > 1:
            distribution["MULTIPLE_REQUIRED_FIELDS_MISSING"] = distribution.get("MULTIPLE_REQUIRED_FIELDS_MISSING", 0) + 1
        for gap in gaps:
            distribution[gap] = distribution.get(gap, 0) + 1
        codes = stock_codes_from_row(row)
        classified = classify_universe_row(row)
        compact.append({
            "symbol": codes[0] if codes else None,
            "name": row.get("f14") or row.get("name"),
            "missing": gaps,
            "universe_state": classified["universe_state"],
            "production_required": classified["production_required"],
            "raw_field_status": {key: _raw_field_status(row, key) for key in ("f2", "f5", "f6", "f62", "f12")},
            "raw_f2": row.get("f2"),
            "raw_f5": row.get("f5"),
            "raw_f6": row.get("f6"),
            "raw_f62": row.get("f62"),
            "row_index": index,
            "approx_page": index // 100 + 1,
        })
    return {
        "incomplete_row_count": len(compact),
        "incomplete_reason_distribution": distribution,
        "rows": compact,
    }


def _collect(
    name: str,
    timings: Dict[str, Any],
    fetcher: Callable[[], Any],
    default: Any,
    *,
    critical: bool = False,
) -> Any:
    started = time.monotonic()
    domain_started_at = _iso_now()
    try:
        value = fetcher()
        domain_finished_at = _iso_now()
        item_count = result_item_count(value)
        if critical and item_count == 0:
            raise CriticalSourceError(f"CRITICAL_SOURCE_EMPTY:{name}")
        if critical:
            trade_date = domain_finished_at[:10]
            if isinstance(value, list):
                value = [
                    _annotate_source_row(row, trade_date=trade_date) if isinstance(row, dict) else row
                    for row in value
                ]
            universe = universe_audit(value if isinstance(value, list) else [], trade_date=trade_date)
            invalid = [
                row for row in (value if isinstance(value, list) else [])
                if isinstance(row, dict) and _critical_row_gaps(row)
            ]
            if invalid:
                audit = _incomplete_audit(value if isinstance(value, list) else [])
                audit["universe"] = universe
                timings[name] = {
                    "status": "ERROR",
                    "item_count": item_count,
                    "incomplete_row_count": audit["incomplete_row_count"],
                    "incomplete_reason_distribution": audit["incomplete_reason_distribution"],
                    "universe_audit": universe,
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                    "domain_started_at": domain_started_at,
                    "domain_finished_at": domain_finished_at,
                    "fetch_started_at": domain_started_at,
                    "fetch_completed_at": domain_finished_at,
                    "fetch_finished_at": domain_finished_at,
                    "source_time": domain_finished_at,
                    "available_at": None,
                    "critical": True,
                    "evidence_status": "BLOCKED",
                    "error": f"CRITICAL_SOURCE_INCOMPLETE:{name}:{len(invalid)}",
                }
                error = CriticalSourceError(f"CRITICAL_SOURCE_INCOMPLETE:{name}:{len(invalid)}")
                error.audit = audit
                raise error
        value = _stamp_available_at(value, domain_finished_at)
        timings[name] = {
            "status": "PASS" if item_count else "EMPTY",
            "item_count": item_count,
            "elapsed_seconds": round(time.monotonic() - started, 4),
            "domain_started_at": domain_started_at,
            "domain_finished_at": domain_finished_at,
            "fetch_started_at": domain_started_at,
            "fetch_completed_at": domain_finished_at,
            "fetch_finished_at": domain_finished_at,
            "source_time": domain_finished_at,
            "available_at": domain_finished_at,
            "critical": critical,
        }
        if critical and isinstance(value, list):
            timings[name]["universe_audit"] = universe_audit(value, trade_date=domain_finished_at[:10])
        return value
    except Exception as exc:
        domain_finished_at = _iso_now()
        if name not in timings:
            timings[name] = {
                "status": "ERROR",
                "item_count": 0,
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "error": repr(exc),
                "domain_started_at": domain_started_at,
                "domain_finished_at": domain_finished_at,
                "fetch_started_at": domain_started_at,
                "fetch_completed_at": domain_finished_at,
                "fetch_finished_at": domain_finished_at,
                "source_time": None,
                "available_at": None,
                "critical": critical,
                "evidence_status": "UNKNOWN" if not critical else "BLOCKED",
            }
        else:
            timings[name].setdefault("elapsed_seconds", round(time.monotonic() - started, 4))
            timings[name].setdefault("error", repr(exc))
        if critical:
            if isinstance(exc, CriticalSourceError):
                raise
            raise CriticalSourceError(f"CRITICAL_SOURCE_FAILURE:{name}:{exc}") from exc
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
    scan_started_at = market_now.isoformat(timespec="seconds")
    source_time = scan_started_at
    output_dir = Path(args.output_dir) if args.output_dir else BASE / "data" / "live_scan" / source_time[:10] / "eastmoney_scan"
    output_dir.mkdir(parents=True, exist_ok=True)
    timings: Dict[str, Any] = {}
    diagnostics: Dict[str, Any] = {}
    results: Dict[str, Any] = {}
    production_scan = "PASS"
    block_reason = ""
    snapshots: list[Dict[str, Any]] = []
    scan_lineage_id = ""
    candidate_codes: list[str] = []
    level_2_audit: Dict[str, Any] = {}
    market: Dict[str, Any] = {}

    try:
        from xiaogu_db import CALENDAR_UNKNOWN, TRADING_DAY, is_trading_date
        calendar_status = is_trading_date(source_time[:10])
        if calendar_status == CALENDAR_UNKNOWN:
            production_scan = "BLOCKED"
            block_reason = "CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE"
        elif calendar_status != TRADING_DAY:
            production_scan = "BLOCKED"
            block_reason = "NON_TRADING_DAY"
    except Exception:
        production_scan = "BLOCKED"
        block_reason = "CALENDAR_BLOCKED:CALENDAR_DATA_UNAVAILABLE"

    try:
        if production_scan == "BLOCKED":
            raise CriticalSourceError(block_reason)
        results["stock_all_a"] = _collect(
            "stock_all_a",
            timings,
            lambda: fetch_paginated(STOCK_ALL_A_FS, 100, LIGHT_STOCK_FIELDS, diagnostics.setdefault("stock_all_a", {})),
            [],
            critical=True,
        )
    except CriticalSourceError as exc:
        production_scan = "BLOCKED"
        block_reason = block_reason or str(exc) or "CRITICAL_SOURCE_FAILURE"
        timings.setdefault("stock_all_a", {"status": "ERROR", "error": repr(exc), "critical": True})
        audit = getattr(exc, "audit", None)
        if isinstance(audit, dict):
            results["_source_completeness_audit"] = audit
            timings["stock_all_a"]["incomplete_row_count"] = audit.get("incomplete_row_count")
            timings["stock_all_a"]["incomplete_reason_distribution"] = audit.get("incomplete_reason_distribution")
        results["stock_all_a"] = []

    if production_scan != "BLOCKED":
        results["flow_industry"] = _collect("flow_industry", timings, lambda: fetch_paginated("m:90+t:2", 100, "f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87", diagnostics.setdefault("flow_industry", {})), [])
        results["flow_concept"] = _collect("flow_concept", timings, lambda: fetch_paginated("m:90+t:3", 100, "f12,f14,f3,f62,f66,f72,f75,f78,f81,f84,f87", diagnostics.setdefault("flow_concept", {})), [])
        recent = (market_now - timedelta(days=RECENT_NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        today = market_now.strftime("%Y-%m-%d")
        market = build_market_snapshot(results["stock_all_a"], source_time)
        level_2_candidates, level_2_audit = detect_capital_candidates(
            results["stock_all_a"],
            industry_rows=results.get("flow_industry") or [],
            market=market,
        )
        candidate_codes = [normalize_stock_code(row.get("f12")) for row in level_2_candidates]
        candidate_codes = [code for code in candidate_codes if code]
        results["level_2_candidates"] = level_2_candidates
        results["level_2_audit"] = level_2_audit
        if candidate_codes:
            results["stock_capital_flow"] = _collect(
                "stock_capital_flow", timings,
                lambda: fetch_ulist(candidate_codes, CAPITAL_FIELDS, diagnostics.setdefault("stock_capital_flow", {})),
                [],
            )
            results["capital_history"] = _collect(
                "capital_history", timings,
                lambda: fetch_capital_history(
                    candidate_codes,
                    begin_date=(market_now - timedelta(days=CAPITAL_HISTORY_LOOKBACK_DAYS)).strftime("%Y-%m-%d"),
                    end_date=source_time[:10],
                    diagnostics=diagnostics.setdefault("capital_history", {}),
                ),
                [],
            )
            results["lhb"] = _collect(
                "lhb", timings,
                lambda: fetch_datacenter("RPT_DAILYBILLBOARD_DETAILSNEW", "TRADE_DATE,DEAL_AMOUNT_RATIO", diagnostics=diagnostics.setdefault("lhb", {}), candidate_codes=candidate_codes),
                [],
            )
            results["earnings_preview"] = _collect(
                "earnings_preview", timings,
                lambda: fetch_datacenter("RPT_LICO_FN_CPD", "NOTICE_DATE", diagnostics=diagnostics.setdefault("earnings_preview", {}), candidate_codes=candidate_codes),
                [],
            )
            results["shareholder_changes"] = _collect(
                "shareholder_changes", timings,
                lambda: fetch_datacenter("RPT_SHARE_HOLDER_INCREASE", "END_DATE", diagnostics=diagnostics.setdefault("shareholder_changes", {}), candidate_codes=candidate_codes),
                [],
            )
            results["lockup_expiry"] = _collect(
                "lockup_expiry", timings,
                lambda: fetch_datacenter("RPT_LIFT_STAGE", "FREE_DATE", diagnostics=diagnostics.setdefault("lockup_expiry", {}), candidate_codes=candidate_codes),
                [],
            )
            results["org_survey"] = _collect(
                "org_survey", timings,
                lambda: fetch_datacenter("RPT_ORG_SURVEY", "NOTICE_DATE", extra_params={"filter": f"(NOTICE_DATE>='{recent}')"}, diagnostics=diagnostics.setdefault("org_survey", {}), candidate_codes=candidate_codes),
                [],
            )
            results["hsgt_holdings"] = _collect(
                "hsgt_holdings", timings,
                lambda: fetch_datacenter("RPT_MUTUAL_HOLDSTOCKNORTH_STA", "TRADE_DATE", diagnostics=diagnostics.setdefault("hsgt_holdings", {}), candidate_codes=candidate_codes),
                [],
            )
            results["hsgt_deals"] = _collect(
                "hsgt_deals", timings,
                lambda: fetch_datacenter("RPT_MUTUAL_DEAL_HISTORY", "TRADE_DATE", diagnostics=diagnostics.setdefault("hsgt_deals", {}), candidate_codes=candidate_codes),
                [],
            )
            results["stock_reports"] = _collect(
                "stock_reports", timings,
                lambda: fetch_report_list("0", recent, today, diagnostics=diagnostics.setdefault("stock_reports", {}), candidate_codes=candidate_codes),
                [],
            )
            results["industry_reports"] = _collect(
                "industry_reports", timings,
                lambda: fetch_report_list("1", recent, today, diagnostics=diagnostics.setdefault("industry_reports", {}), candidate_codes=candidate_codes),
                [],
            )
            results["announcements"] = _collect(
                "announcements", timings,
                lambda: fetch_announcements(diagnostics=diagnostics.setdefault("announcements", {}), candidate_codes=candidate_codes),
                [],
            )
            results["news_kuaixun"] = _collect(
                "news_kuaixun", timings,
                lambda: fetch_news(diagnostics=diagnostics.setdefault("news_kuaixun", {}), candidate_codes=candidate_codes),
                [],
            )
        else:
            for name in DEEP_DOMAINS:
                results[name] = []
            results["external_market"] = []
            results["indexes"] = []
            results["market_capital_flow"] = []
    else:
        for name in ("flow_industry", "flow_concept", *DEEP_DOMAINS, "external_market", "indexes", "market_capital_flow", "level_2_candidates"):
            results.setdefault(name, [])
        results["level_2_audit"] = {"purpose": "RESOURCE_ROUTER", "selection": False, "ranking": False, "alpha": False}

    files = {}
    source_audit = results.pop("_source_completeness_audit", None)
    for name, rows in results.items():
        files[name] = _write_jsonl(output_dir / f"{name}.jsonl", rows if isinstance(rows, list) else [rows])
    if isinstance(source_audit, dict):
        audit_path = output_dir / "source_completeness_audit.json"
        audit_path.write_text(json.dumps(source_audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        files["source_completeness_audit"] = str(audit_path)
        files["source_completeness_incomplete"] = _write_jsonl(
            output_dir / "source_completeness_incomplete.jsonl",
            source_audit.get("rows") or [],
        )
    stocks = results.get("stock_all_a") or []
    scan_lineage_id = build_scan_lineage_id(
        source="eastmoney_api_scan_v2",
        source_time=source_time,
        producer="scrapy_scanner.runner_v2.build_canonical_snapshots",
        trade_date=source_time[:10],
        scan_nonce=uuid.uuid4().hex,
    )
    if production_scan != "BLOCKED":
        snapshots = build_canonical_snapshots(
            results,
            source_time,
            lineage_id=scan_lineage_id,
            symbols=candidate_codes,
            market=market,
            available_at=(timings.get("stock_all_a") or {}).get("available_at") or source_time,
        )
    files["canonical_snapshots"] = _write_jsonl(output_dir / "canonical_snapshots.jsonl", snapshots)
    market_path = output_dir / "canonical_market_snapshot.json"
    market_path.write_text(json.dumps(market, ensure_ascii=False, indent=2), encoding="utf-8")
    files["canonical_market_snapshot"] = str(market_path)
    source_counts = {name: result_item_count(value) for name, value in results.items()}
    l0_count = len(stocks)
    canonical_count = len(snapshots)
    results.clear()
    del stocks
    gc.collect()

    persistence = {"status": "SKIPPED", "reason": "XIAOGU_PERSIST_DB_not_set" if production_scan != "BLOCKED" else "PRODUCTION_SCAN_BLOCKED"}
    if production_scan != "BLOCKED" and os.environ.get("XIAOGU_PERSIST_DB") == "1":
        try:
            from xiaogu_db import persist_scan_capture
            snapshot_file = output_dir / "canonical_snapshots.jsonl"

            def _canonical_snapshot_rows() -> Iterable[Dict[str, Any]]:
                with snapshot_file.open(encoding="utf-8") as handle:
                    for raw in handle:
                        line = raw.strip()
                        if line:
                            yield json.loads(line)

            persistence = persist_scan_capture(
                trade_date=source_time[:10],
                scan_time=source_time,
                source_id="eastmoney_api_scan_v2",
                quotes_count=l0_count,
                captured_count=l0_count,
                scan_dir=str(output_dir),
                market_snapshot=market,
                source_status=diagnostics,
                source_counts=source_counts,
                source_diagnostics=timings,
                lineage_id=scan_lineage_id,
                snapshots=_canonical_snapshot_rows(),
            )
        except Exception as exc:
            production_scan = "BLOCKED"
            block_reason = f"PRODUCTION_SCAN_BLOCKED:{type(exc).__name__}:{exc}"
            persistence = {"status": "FAILED", "error": repr(exc), "reason": block_reason}

    scan_finished_at = datetime.now(MARKET_TIMEZONE).isoformat(timespec="seconds")
    l1_eligible_value = level_2_audit.get("l1_eligible_count")
    l1_eligible = int(0 if l1_eligible_value is None else l1_eligible_value)
    l2_routed = int(level_2_audit.get("l2_routed_count") or len(candidate_codes))
    l3_returned_symbols = {
        str(symbol)
        for name, item in diagnostics.items()
        if name in DEEP_DOMAINS and isinstance(item, dict)
        for symbol in (item.get("returned_symbols") or [])
    }
    l3_unrelated_count = sum(
        int(0 if item.get("unrelated_rows") is None else item["unrelated_rows"])
        for name, item in diagnostics.items()
        if name in DEEP_DOMAINS and isinstance(item, dict)
    )
    # Scanner is capture-only. It cannot truthfully emit NO_SIGNAL until the
    # downstream Decision and Paper Observation stages have completed.
    scan_status = "SCAN_BLOCKED" if production_scan == "BLOCKED" or not snapshots else None
    scan_reason = (
        block_reason or "CANONICAL_SNAPSHOT_UNAVAILABLE"
        if scan_status == "SCAN_BLOCKED"
        else "SCANNER_SUCCESS_AWAITING_DECISION"
    )
    l0_diag = diagnostics.get("stock_all_a") or {}
    l0_time = timings.get("stock_all_a") or {}
    summary = {
        "source": "eastmoney_api_scan_v2", "pipeline_version": "market_reality_capture_v1",
        "scan_started_at": scan_started_at, "scan_finished_at": scan_finished_at,
        "source_time": source_time, "raw_domain_counts": source_counts,
        "canonical_snapshot_count": canonical_count, "canonical_market_snapshot": market,
        "production_scan": production_scan, "block_reason": block_reason,
        "scan_status": scan_status, "scan_reason": scan_reason,
        "l0_count": l0_count,
        "l1_count": l1_eligible,
        "l2_count": l2_routed,
        "l3_count": len(candidate_codes) if production_scan != "BLOCKED" else 0,
        "canonical_count": canonical_count, "feature_count": None, "alpha_count": None,
        "decision_count": None, "paper_observation_count": None,
        "critical_sources": sorted(CRITICAL_SOURCES), "optional_sources": sorted(OPTIONAL_SOURCES),
        "scanner_contract": {
            "owner": "scrapy_scanner.runner_v2", "responsibility": "DATA_CAPTURE_ONLY",
            "selection": False, "ranking": False, "strategy_score": False, "portfolio_action": False,
        },
        "universes": {
            "full_l0_universe": l0_count,
            "l1_eligible_universe": l1_eligible,
            "l2_routed_universe": l2_routed,
            "l3_researched_universe": len(candidate_codes) if production_scan != "BLOCKED" else 0,
        },
        "sample_accounting": {
            **level_2_audit,
            "full_l0_count": l0_count,
            "l1_eligible_count": l1_eligible,
            "l1_rejected_count": l0_count - l1_eligible,
            "l2_routed_count": l2_routed,
            "l2_not_routed_count": max(0, l1_eligible - l2_routed),
            "l3_requested_count": len(candidate_codes) if production_scan != "BLOCKED" else 0,
            "l3_returned_count": len(l3_returned_symbols),
            "l3_unrelated_count": l3_unrelated_count,
            "l3_fetched_count": len(candidate_codes) if production_scan != "BLOCKED" else 0,
            "l3_fetch_failed_count": 0 if production_scan != "BLOCKED" else len(candidate_codes),
            "alpha_evaluated_count": None,
            "decision_count": None,
        },
        "levels": {
            "level_0": {"name": "LIGHT_MARKET_CAPTURE", "universe_count": l0_count, "fields": LIGHT_STOCK_FIELDS.split(",")},
            "level_1": {"name": "CHEAP_ELIGIBILITY", "operational_only": True, "eligible_count": l1_eligible},
            "level_2": {"name": "RESOURCE_ROUTER", **level_2_audit},
            "level_3": {
                "name": "DEEP_CANDIDATE_FETCH", "candidate_count": len(candidate_codes),
                "requested_count": len(candidate_codes) if production_scan != "BLOCKED" else 0,
                "returned_count": len(l3_returned_symbols), "unrelated_count": l3_unrelated_count,
                "domains": list(DEEP_DOMAINS),
            },
        },
        "lineage": {
            "lineage_id": scan_lineage_id,
            "source": "eastmoney",
            "source_time": source_time,
            "source_version": "market_reality_capture_v1",
        },
        "domain_timings": timings, "fetch_diagnostics": diagnostics,
        "source_audit": {
            "expected_universe_count": l0_diag.get("expected_universe_count"),
            "observed_universe_count": l0_diag.get("observed_universe_count"),
            "duplicate_symbol_count": l0_diag.get("duplicate_symbol_count"),
            "missing_symbol_count": l0_diag.get("missing_symbol_count"),
            "incomplete_row_count": l0_time.get("incomplete_row_count") if not isinstance(source_audit, dict) else source_audit.get("incomplete_row_count"),
            "incomplete_reason_distribution": (
                source_audit.get("incomplete_reason_distribution")
                if isinstance(source_audit, dict)
                else l0_time.get("incomplete_reason_distribution")
            ),
            "sort_field": l0_diag.get("sort_field") or CLIST_SORT_FIELD,
            "fields": LIGHT_STOCK_FIELDS,
            "fs": STOCK_ALL_A_FS,
        },
        "universe_audit": (
            (source_audit.get("universe") if isinstance(source_audit, dict) else None)
            or (l0_time.get("universe_audit") or {})
        ),
        "database_persistence": persistence, "files": files,
        "elapsed_seconds": round(time.monotonic() - started, 4),
    }
    for filename in ("scan_summary.json", "xiaogu_scan_summary.json", "xiaogu_scan_summary_runner.json"):
        (output_dir / filename).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return summary


if __name__ == "__main__":
    main()
