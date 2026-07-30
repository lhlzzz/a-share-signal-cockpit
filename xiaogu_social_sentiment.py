#!/usr/bin/env python3
"""Eastmoney social evidence collector: public JSON API first, HTML second, CDP last."""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CDP_URL = (
    os.environ.get("XIAOGU_SOCIAL_CDP_URL")
    or os.environ.get("XIAOGU_SCANNER_CDP_URL")
    or "http://127.0.0.1:9333"
).rstrip("/")
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CDP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PUBLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36 XiaoguSocial/0.1",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://guba.eastmoney.com/",
    "Origin": "https://guba.eastmoney.com",
}
# Direct guba article list (no captcha HTML shell). Primary path after SPA/captcha broke SSR.
GUBA_ARTICLELIST_URL = (
    "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist"
    "?product=Guba&plat=Web&version=200&deviceid=0.0.0"
    "&code={code}&p=1&ps={ps}&type=0&sorttype=0"
)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_CDP_UNAVAILABLE_UNTIL = 0.0
_CDP_LAST_ERROR = ""


def _fetch_public_text(url: str, *, timeout: int = 15, direct: bool = False, accept: str = "") -> str:
    headers = dict(PUBLIC_HEADERS)
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    opener = DIRECT_OPENER if direct else None
    response = opener.open(request, timeout=timeout) if opener else urllib.request.urlopen(request, timeout=timeout)
    with response:
        return response.read().decode("utf-8", "replace")


def _clean_public_text(value: str) -> str:
    return " ".join(html.unescape(_TAG_PATTERN.sub(" ", value or "")).split())


def _sentiment_payload(
    *,
    source_key: str,
    subject_key: str,
    subject_value: str,
    texts: Iterable[str],
) -> Dict[str, Any]:
    titles = list(dict.fromkeys(text for text in (_clean_public_text(item) for item in texts) if text))[:30]
    positive_words = ["利好", "涨停", "大涨", "突破", "强势", "买入", "看多", "牛市", "bull", "beat", "growth", "upside"]
    negative_words = ["利空", "跌停", "大跌", "破位", "弱势", "卖出", "看空", "熊市", "bear", "risk", "crash", "downside"]
    positive = sum(1 for title in titles if any(word in title.lower() for word in positive_words))
    negative = sum(1 for title in titles if any(word in title.lower() for word in negative_words))
    total = len(titles)
    sentiment = 0.5 if not total else max(0.0, min(1.0, (positive - negative + total) / (2 * total)))
    return {
        subject_key: subject_value,
        f"{source_key}_count": total,
        "positive_count": positive,
        "negative_count": negative,
        "sentiment_score": round(sentiment, 3),
        "sample_titles": titles[:5],
    }


def _cloakbrowser_binary() -> str:
    cli = shutil.which("cloakbrowser") or shutil.which("CloakBrowser") or shutil.which("cloak")
    if not cli:
        return ""
    try:
        completed = subprocess.run(
            [cli, "info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    for line in completed.stdout.splitlines():
        if line.strip().startswith("Binary:"):
            binary = line.split(":", 1)[1].strip()
            return binary if Path(binary).exists() else ""
    return ""


def _ensure_social_cdp_browser() -> Optional[str]:
    """Reuse the scanner's CloakChrome launcher for Eastmoney fallback pages."""
    os.environ["XIAOGU_SCANNER_CDP_URL"] = CDP_URL
    os.environ.setdefault("XIAOGU_SCANNER_CDP_HOME", "https://guba.eastmoney.com/")
    os.environ.setdefault("XIAOGU_SCANNER_CDP_USER_DATA_DIR", "/tmp/xiaogu-cloakchrome-eastmoney-social")
    if os.environ.get("XIAOGU_SOCIAL_PROXY") and not os.environ.get("XIAOGU_SCANNER_PROXY_SERVER"):
        os.environ["XIAOGU_SCANNER_PROXY_SERVER"] = os.environ["XIAOGU_SOCIAL_PROXY"]
    if not os.environ.get("XIAOGU_SCANNER_CHROME_BIN"):
        cloak_bin = _cloakbrowser_binary()
        if cloak_bin:
            os.environ["XIAOGU_SCANNER_CHROME_BIN"] = cloak_bin
    try:
        from scrapy_scanner import runner_v2 as scanner_runner

        scanner_runner.CDP_URL = os.environ.get("XIAOGU_SCANNER_CDP_URL", CDP_URL).rstrip("/")
        scanner_runner.CDP_FETCH_HOME = os.environ.get("XIAOGU_SCANNER_CDP_HOME", "https://guba.eastmoney.com/")
        scanner_runner._ensure_cdp_browser()
        return None
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        if message.startswith(("CDP_BROWSER_UNAVAILABLE", "CDP_BROWSER_START_FAILED")):
            return message
        return f"CDP_BROWSER_START_FAILED:{message}"


def cdp_get_tabs() -> list:
    """Get all open CloakChrome/CDP tabs."""
    global _CDP_UNAVAILABLE_UNTIL, _CDP_LAST_ERROR
    if time.monotonic() < _CDP_UNAVAILABLE_UNTIL:
        return []
    ensure_error = _ensure_social_cdp_browser()
    if ensure_error:
        _CDP_LAST_ERROR = ensure_error
        _CDP_UNAVAILABLE_UNTIL = time.monotonic() + 30.0
        return []
    try:
        with CDP_OPENER.open(f"{CDP_URL}/json", timeout=1) as response:
            _CDP_LAST_ERROR = ""
            return json.loads(response.read())
    except Exception as exc:
        _CDP_LAST_ERROR = f"cdp_tabs_request_failed:{type(exc).__name__}"
        _CDP_UNAVAILABLE_UNTIL = time.monotonic() + 30.0
        return []


def _cdp_tab_ws_url(tab_id: str) -> Optional[str]:
    for tab in cdp_get_tabs():
        if tab.get('id') == tab_id:
            return tab.get('webSocketDebuggerUrl')
    return None


def _cdp_recv_response(ws: Any, request_id: int, timeout: int) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = json.loads(ws.recv())
        except Exception:
            return {}
        if message.get('id') == request_id:
            return message
    return {}


def cdp_navigate(tab_id: str, url: str, timeout: int = 30) -> bool:
    """Navigate a CloakChrome tab and wait for the matching CDP response frame."""
    try:
        import websocket
        ws_url = _cdp_tab_ws_url(tab_id)
        if not ws_url:
            return False
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        ws = websocket.create_connection(ws_url, timeout=timeout)
        try:
            ws.send(json.dumps({'id': 1, 'method': 'Page.enable'}))
            _cdp_recv_response(ws, 1, min(timeout, 5))
            ws.send(json.dumps({'id': 2, 'method': 'Page.navigate', 'params': {'url': url}}))
            response = _cdp_recv_response(ws, 2, timeout)
            result = response.get('result') or {}
            ok = bool(result.get('frameId')) and not result.get('errorText') and not response.get('error')
            time.sleep(3)
            if ok:
                return True
            current_url = next(
                (str(tab.get('url') or '') for tab in cdp_get_tabs() if tab.get('id') == tab_id),
                '',
            )
            current_base = current_url.split('?', 1)[0].split('#', 1)[0]
            target_base = url.split('?', 1)[0].split('#', 1)[0]
            return bool(current_base and current_base == target_base)
        finally:
            ws.close()
    except Exception:
        return False


def cdp_evaluate(tab_id: str, expression: str) -> Any:
    """Evaluate JavaScript in a CloakChrome tab and wait for the matching CDP result."""
    try:
        import websocket
        ws_url = _cdp_tab_ws_url(tab_id)
        if not ws_url:
            return None
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        ws = websocket.create_connection(ws_url, timeout=15)
        try:
            ws.send(json.dumps({
                'id': 1,
                'method': 'Runtime.evaluate',
                'params': {'expression': expression, 'returnByValue': True},
            }))
            result = _cdp_recv_response(ws, 1, 15)
            return result.get('result', {}).get('result', {}).get('value')
        finally:
            ws.close()
    except Exception:
        return None


def _scrape_eastmoney_guba_api(symbol: str, *, page_size: int = 30) -> Dict[str, Any]:
    """Primary path: gbapi article list JSON (no captcha HTML, no CDP)."""
    code = str(symbol or "").zfill(6)
    url = GUBA_ARTICLELIST_URL.format(code=code, ps=max(5, min(50, int(page_size or 30))))
    raw = _fetch_public_text(
        url,
        direct=True,
        timeout=12,
        accept="application/json,text/plain,*/*",
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("eastmoney_guba_api_not_object")
    rows = payload.get("re") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("eastmoney_guba_api_empty")
    titles: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("post_title") or row.get("title") or "").strip()
        if title:
            titles.append(title)
    if not titles:
        raise ValueError("eastmoney_guba_api_titles_missing")
    result = _sentiment_payload(
        source_key="post",
        subject_key="symbol",
        subject_value=code,
        texts=titles,
    )
    result["transport"] = "gbapi_articlelist"
    result["api_count"] = int(payload.get("count") or len(titles) or 0)
    return result


def scrape_eastmoney_guba(symbol: str) -> Dict[str, Any]:
    """Collect guba titles: JSON API first, HTML second, optional CDP last."""
    code = str(symbol or "").zfill(6)
    errors: List[str] = []

    # 1) Direct JSON API — preferred; survives captcha HTML shell.
    try:
        return _scrape_eastmoney_guba_api(code)
    except Exception as exc:
        errors.append(f"api:{type(exc).__name__}:{exc}")

    # 2) Public HTML (often captcha shell now; keep for older environments / tests).
    url = f"https://guba.eastmoney.com/list,{code}.html"
    try:
        page = _fetch_public_text(url, direct=True)
        titles = re.findall(r'<div[^>]+class="title[^"]*"[^>]*>(.*?)</div>', page, re.S)
        if titles:
            result = _sentiment_payload(
                source_key="post",
                subject_key="symbol",
                subject_value=code,
                texts=titles,
            )
            result["transport"] = "public_html"
            return result
        errors.append("html:titles_missing")
    except Exception as exc:
        errors.append(f"html:{type(exc).__name__}")

    # 3) CDP fallback only when API+HTML both fail and browser is already usable.
    # Scanner main path no longer requires CDP; social must not force-start it for happy path.
    if os.environ.get("XIAOGU_SOCIAL_ALLOW_CDP", "0") != "1":
        return {
            "error": "eastmoney_guba_unavailable:" + ";".join(errors) + ";cdp_disabled",
            "symbol": code,
        }

    tab_id = None
    try:
        tabs = cdp_get_tabs()
        for t in tabs:
            if t.get("type") == "page":
                tab_id = t["id"]
                break
        if not tab_id:
            return {"error": "eastmoney_guba_unavailable:" + ";".join(errors) + ";no_cdp_tab", "symbol": code}

        if not cdp_navigate(tab_id, url, timeout=15):
            return {
                "error": "eastmoney_guba_unavailable:" + ";".join(errors) + ";cloak_cdp_navigation_failed",
                "symbol": code,
            }
        time.sleep(2)

        result = cdp_evaluate(
            tab_id,
            """
            (() => {
                const titles = [];
                document.querySelectorAll('.title').forEach(el => {
                    const text = el.textContent.trim();
                    if (text.length > 5 && text.length < 200) titles.push(text);
                });
                if (titles.length < 5) {
                    document.querySelectorAll('tr.listitem').forEach(el => {
                        const text = el.textContent.trim();
                        if (text.length > 10 && text.length < 300) titles.push(text.substring(0, 150));
                    });
                }
                return JSON.stringify(titles.slice(0, 30));
            })()
        """,
        )

        if result and isinstance(result, str):
            titles = json.loads(result)
        elif isinstance(result, list):
            titles = result
        else:
            titles = []

        if not titles:
            return {
                "error": "eastmoney_guba_unavailable:" + ";".join(errors) + ";cdp_titles_missing",
                "symbol": code,
            }
        payload = _sentiment_payload(
            source_key="post",
            subject_key="symbol",
            subject_value=code,
            texts=titles,
        )
        payload["transport"] = "cdp_fallback"
        return payload
    except Exception as e:
        return {
            "error": "eastmoney_guba_unavailable:" + ";".join(errors) + f";cdp_fallback_failed:{e}",
            "symbol": code,
        }


def collect_sector_sentiment(sector_stocks: list) -> Dict[str, Any]:
    """Collect sentiment for a sector's top stocks."""
    results = []
    for symbol in sector_stocks[:5]:  # Limit to 5 stocks per sector
        data = scrape_eastmoney_guba(symbol)
        if 'error' not in data:
            results.append(data)
        time.sleep(1)  # Rate limit
    
    if not results:
        return {'sector_sentiment': 0.5, 'stocks_analyzed': 0}
    
    avg_sentiment = sum(r['sentiment_score'] for r in results) / len(results)
    return {
        'sector_sentiment': round(avg_sentiment, 3),
        'stocks_analyzed': len(results),
        'details': results,
    }


def _normalized_sentiment(positive: int, negative: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(-1.0, min(1.0, (positive - negative) / total)), 4)


def normalize_social_signal(
    source: str, payload: Dict[str, Any], *, theme_strength: float | None = None,
) -> Dict[str, Any]:
    """Normalize Eastmoney collector output into shadow-only candidate features."""
    source = str(source or '').lower()
    if payload.get('error'):
        return {
            'source': source,
            'error': str(payload.get('error')),
            'mentions': 0,
            'positive': 0,
            'negative': 0,
            'sentiment_score': 0.0,
            'theme_strength_last30d': 0.0,
            'social_noise_risk': 0.0,
            'sample_posts': [],
            'raw': payload,
        }
    mentions = int(payload.get('post_count') or payload.get('mentions') or 0)
    positive = int(payload.get('positive_count') or payload.get('positive') or 0)
    negative = int(payload.get('negative_count') or payload.get('negative') or 0)
    sentiment = _normalized_sentiment(positive, negative, mentions)
    if payload.get('sentiment_score') is not None:
        sentiment = round((float(payload['sentiment_score']) - 0.5) * 2.0, 4)
    noise = 0.0
    if mentions >= 8 and negative / max(mentions, 1) >= 0.45:
        noise = 0.8
    elif mentions >= 12 and positive + negative <= 1:
        noise = 0.6
    return {
        'source': source,
        'mentions': mentions,
        'positive': positive,
        'negative': negative,
        'sentiment_score': sentiment,
        'theme_strength_last30d': 0.0,
        'social_noise_risk': noise,
        'sample_posts': list(payload.get('sample_posts') or payload.get('sample_titles') or [])[:5],
        'raw': payload,
    }


def _store_social_payload(trade_date: date, symbol: str, payloads: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    from xiaogu_db import upsert_signal

    normalized = [payload for payload in payloads if isinstance(payload, dict)]
    eastmoney_payloads = [payload for payload in normalized if payload.get('source') == 'eastmoney_guba']
    usable = [payload for payload in eastmoney_payloads if not payload.get('error')]
    collection_errors = [
        {
            'source': str(payload.get('source') or 'unknown'),
            'error': str(payload.get('error') or 'collection_failed'),
        }
        for payload in normalized
        if payload.get('error')
    ]
    if not usable:
        metadata = {
            'collection_status': 'WARN',
            'collection_errors': collection_errors,
            'source_layers': [],
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'payloads': normalized,
            'used_for_official_ranking': False,
            'preserved_existing_social_values': True,
        }
        upsert_signal(
            trade_date,
            symbol,
            'social_collection_status',
            None,
            metadata,
        )
        return {
            'symbol': symbol,
            'social_catalyst_score': 0.0,
            'theme_strength_last30d': 0.0,
            'social_sentiment_score': 0.0,
            'social_noise_risk': 0.0,
            'social_signal_quality': 'MISSING',
            'social_source_layers': [],
            'status': 'WARN',
            'metadata': metadata,
        }

    eastmoney_payloads = usable
    eastmoney_mentions = sum(int(payload.get('mentions') or 0) for payload in eastmoney_payloads)
    sentiment_values = [payload.get('sentiment_score', 0.0) for payload in eastmoney_payloads]
    catalyst = round(min(1.0, (eastmoney_mentions * 1.2) / 30.0), 4)
    noise = round(max((payload.get('social_noise_risk', 0.0) for payload in eastmoney_payloads), default=0.0), 4)
    sentiment = round(sum(sentiment_values) / len(sentiment_values), 4) if sentiment_values else 0.0
    observed_source_layers = ['eastmoney_guba'] if eastmoney_payloads else []
    source_layers = ['eastmoney_guba'] if eastmoney_mentions > 0 else []
    quality = 'MEDIUM' if source_layers else 'LOW'
    metadata = {
        'collection_status': 'PASS',
        'source_layers': observed_source_layers,
        'active_source_layers': source_layers,
        'social_signal_quality': quality,
        'collection_errors': collection_errors,
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'payloads': normalized,
        'used_for_official_ranking': False,
    }
    signal_values = {
        'social_sentiment_eastmoney_guba': next(
            (item.get('sentiment_score') for item in eastmoney_payloads),
            None,
        ),
        'social_catalyst_score': catalyst,
        'social_noise_risk': noise,
        'social_sentiment_score': sentiment,
    }
    for key, value in signal_values.items():
        upsert_signal(trade_date, symbol, key, value, {**metadata, 'signal_key': key})
    upsert_signal(trade_date, symbol, 'social_collection_status', 1.0, metadata)
    return {
        'symbol': symbol,
        'social_catalyst_score': catalyst,
        'theme_strength_last30d': 0.0,
        'social_sentiment_score': sentiment,
        'social_noise_risk': noise,
        'social_signal_quality': quality,
        'social_source_layers': observed_source_layers,
        'status': 'PASS',
        'metadata': metadata,
    }


def collect_and_store(
    symbols: Iterable[str],
    *,
    trade_date: str,
    sources: Iterable[str] = ('eastmoney_guba',),
    themes_by_symbol: Optional[Dict[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """Collect Eastmoney Guba shadow signals and write normalized diagnostics."""
    target_date = date.fromisoformat(trade_date)
    selected_sources = {str(source).strip() for source in sources if str(source).strip()}
    collect_eastmoney = not selected_sources or 'eastmoney_guba' in selected_sources
    results = []
    for raw_symbol in symbols:
        symbol = str(raw_symbol or '').zfill(6)
        if not symbol.isdigit() or len(symbol) != 6:
            continue
        payloads = []
        if collect_eastmoney:
            payloads.append(normalize_social_signal('eastmoney_guba', scrape_eastmoney_guba(symbol)))
        results.append(_store_social_payload(target_date, symbol, payloads))
    return {
        'status': 'PASS' if any(row['status'] == 'PASS' for row in results) else 'WARN',
        'trade_date': trade_date,
        'result_count': len(results),
        'results': results,
        'used_for_official_ranking': False,
    }


def attach_social_features(candidates: Iterable[Dict[str, Any]], trade_date: str) -> List[Dict[str, Any]]:
    """Attach same-day DB signals to candidate diagnostics; never score official ranking."""
    try:
        from xiaogu_db import fetch_signals
        rows = fetch_signals(date.fromisoformat(trade_date))
    except Exception:
        return [candidate for candidate in candidates if isinstance(candidate, dict)]
    by_symbol: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        raw_json = row.get('raw_json')
        if isinstance(raw_json, str):
            try:
                raw_json = json.loads(raw_json)
            except json.JSONDecodeError:
                raw_json = {}
        by_symbol.setdefault(str(row.get('symbol') or '').zfill(6), {})[str(row.get('signal_key') or '')] = {
            'value': row.get('signal_value'),
            'raw_json': raw_json if isinstance(raw_json, dict) else {},
        }
    attached = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get('symbol') or candidate.get('code') or '').zfill(6)
        signal_rows = by_symbol.get(symbol, {})
        status_row = signal_rows.get('social_collection_status') or {}
        metadata = status_row.get('raw_json') or {}
        if not metadata:
            metadata = next(
                (
                    row.get('raw_json') or {}
                    for row in signal_rows.values()
                    if isinstance(row.get('raw_json'), dict) and row.get('raw_json')
                ),
                {},
            )
        layers = list(metadata.get('source_layers') or [])
        if not layers and (signal_rows.get('social_sentiment_eastmoney_guba') or {}).get('value') is not None:
            layers = ['eastmoney_guba']
        candidate['social_catalyst_score'] = float((signal_rows.get('social_catalyst_score') or {}).get('value') or 0.0)
        candidate['theme_strength_last30d'] = 0.0
        candidate['social_sentiment_score'] = float((signal_rows.get('social_sentiment_score') or {}).get('value') or 0.0)
        candidate['social_noise_risk'] = float((signal_rows.get('social_noise_risk') or {}).get('value') or 0.0)
        candidate['social_source_layers'] = layers
        collection_status = str(metadata.get('collection_status') or ('PASS' if layers else 'MISSING')).upper()
        candidate['social_signal_collection_status'] = collection_status
        candidate['social_signal_error'] = list(metadata.get('collection_errors') or [])
        candidate['social_signal_quality'] = str(
            metadata.get('social_signal_quality')
            or ('MEDIUM' if layers else 'MISSING')
        ).upper()
        attached.append(candidate)
    return attached


def _normalize_symbol(raw: Any) -> str:
    sym = str(raw or "").strip().zfill(6)
    if not sym.isdigit() or len(sym) != 6 or sym == "000000":
        return ""
    return sym


def _symbols_from_formal_and_top10(trade_date: str, *, limit: int = 15) -> List[str]:
    """Always cover formal PAPER_PICK + top10 candidates for soft social evidence."""
    out: List[str] = []
    try:
        from xiaogu_db import get_db
        from sqlalchemy import text

        with get_db() as db:
            pick_rows = db.execute(
                text(
                    """
                    SELECT symbol FROM picks
                    WHERE trade_date = CAST(:d AS date)
                      AND decision = 'PAPER_PICK'
                      AND COALESCE(features->>'superseded', 'false') <> 'true'
                    ORDER BY id DESC
                    LIMIT 5
                    """
                ),
                {"d": trade_date},
            ).fetchall()
            for row in pick_rows:
                sym = _normalize_symbol(row[0])
                if sym:
                    out.append(sym)
            top_rows = db.execute(
                text(
                    """
                    SELECT symbol FROM daily_candidates
                    WHERE trade_date = CAST(:d AS date) AND rank <= 10
                    ORDER BY rank
                    LIMIT :lim
                    """
                ),
                {"d": trade_date, "lim": int(limit)},
            ).fetchall()
            for row in top_rows:
                sym = _normalize_symbol(row[0])
                if sym:
                    out.append(sym)
    except Exception:
        # Soft path: DB unavailable must not break collection.
        pass
    # Also try summary formal file.
    try:
        formal = Path(__file__).resolve().parent / "summary" / f"{trade_date}_formal_paper_pick.json"
        if formal.exists():
            payload = json.loads(formal.read_text(encoding="utf-8"))
            for key in ("symbol", "code"):
                sym = _normalize_symbol(payload.get(key))
                if sym:
                    out.append(sym)
            cand = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
            sym = _normalize_symbol(cand.get("symbol") or cand.get("code"))
            if sym:
                out.append(sym)
    except Exception:
        pass
    return list(dict.fromkeys(out))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', default='')
    parser.add_argument('--trade-date', default=date.today().isoformat())
    parser.add_argument('--sources', default='eastmoney_guba')
    parser.add_argument('--from-scan', default='')
    parser.add_argument('--topn', type=int, default=50)
    parser.add_argument(
        '--ensure-formal-top10',
        action='store_true',
        help='Also collect formal PAPER_PICK + daily_candidates rank<=10',
    )
    parser.add_argument(
        '--status-file',
        default='',
        help='Write compact top-level status JSON for pipeline gate detection',
    )
    args = parser.parse_args()
    symbols = [item.strip() for item in args.symbols.split(',') if item.strip()]
    themes_by_symbol: Dict[str, List[str]] = {}
    if args.from_scan:
        try:
            summary = json.loads(Path(args.from_scan).read_text(encoding='utf-8'))
            rows = summary.get('full_candidate_pool') or summary.get('paper_scoring_candidates') or []
            for row in rows[:args.topn]:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get('symbol') or row.get('code') or '').zfill(6)
                if not symbol.isdigit() or len(symbol) != 6:
                    continue
                symbols.append(symbol)
                tags = row.get('sector_opportunity_tags')
                themes = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
                if not themes:
                    themes = [str(row.get('name') or row.get('stock_name') or '').strip()]
                themes_by_symbol[symbol] = list(dict.fromkeys(theme for theme in themes if theme))[:3]
        except (OSError, json.JSONDecodeError):
            pass
    if args.ensure_formal_top10:
        symbols.extend(_symbols_from_formal_and_top10(args.trade_date, limit=15))
    # Dedupe while preserving order; formal/top10 always included even beyond topn.
    ordered = list(dict.fromkeys(str(s).zfill(6) for s in symbols if str(s).strip()))
    scan_slice = ordered[: max(1, int(args.topn))]
    extra = [s for s in ordered if s not in scan_slice]
    target_symbols = scan_slice + extra
    result = collect_and_store(
        target_symbols,
        trade_date=args.trade_date,
        sources=[item.strip() for item in args.sources.split(',') if item.strip()],
        themes_by_symbol=themes_by_symbol,
    )
    if args.status_file:
        try:
            pass_n = sum(1 for row in (result.get('results') or []) if row.get('status') == 'PASS')
            warn_n = sum(1 for row in (result.get('results') or []) if row.get('status') != 'PASS')
            Path(args.status_file).write_text(
                json.dumps(
                    {
                        'status': result.get('status'),
                        'trade_date': result.get('trade_date'),
                        'result_count': result.get('result_count'),
                        'pass_count': pass_n,
                        'warn_count': warn_n,
                        'used_for_official_ranking': False,
                    },
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
        except OSError:
            pass
    # One-line machine status first so pipeline can tail/rg without loading full payload.
    print(
        json.dumps(
            {
                'status': result.get('status'),
                'trade_date': result.get('trade_date'),
                'result_count': result.get('result_count'),
                'used_for_official_ranking': False,
            },
            ensure_ascii=False,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
