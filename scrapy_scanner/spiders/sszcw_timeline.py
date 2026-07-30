#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scrapy spider: fetch X timeline posts + replies for soft market context.

Sources (best-effort, fail soft):
1) r.jina.ai markdown of https://x.com/<screen_name>  (recent original posts)
2) api.fxtwitter.com status enrich (created_at, replying_to / parent)
3) syndication.twitter.com embed HTML (fallback bulk full_text)

Rules:
- Soft observation only — never official PAPER_PICK.
- Includes replies when fxtwitter marks replying_to / text starts with @.
- Parent question text attached when available for Q&A analysis.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import scrapy


DEFAULT_SCREEN_NAME = "sszcw"


def _normalize_screen_name(screen_name: str) -> str:
    return str(screen_name or DEFAULT_SCREEN_NAME).strip().lstrip("@").lower() or DEFAULT_SCREEN_NAME


def _build_jina_profile_candidates(screen_name: str) -> tuple[str, str]:
    screen = _normalize_screen_name(screen_name)
    return (
        f"https://r.jina.ai/https://x.com/{screen}",
        f"https://r.jina.ai/https://twitter.com/{screen}",
    )


def _build_jina_replies_candidates(screen_name: str) -> tuple[str, str]:
    screen = _normalize_screen_name(screen_name)
    return (
        f"https://r.jina.ai/https://x.com/{screen}/with_replies",
        f"https://r.jina.ai/https://twitter.com/{screen}/with_replies",
    )


def _build_jina_post_re(screen_name: str) -> re.Pattern[str]:
    screen = re.escape(_normalize_screen_name(screen_name))
    host = r"(?:https://(?:x|twitter)\.com)"
    return re.compile(
        r"\[@"
        + screen
        + r"\]\("
        + host
        + r"/"
        + screen
        + r"\)\s*\[([^\]]+)\]\("
        + host
        + r"/"
        + screen
        + r"/status/(\d+)\)\s*(.*?)(?=\s*(?:\*|\[!\[|\[@"
        + screen
        + r"\]|Pinned|$))",
        re.S,
    )


def _build_status_id_re(screen_name: str) -> re.Pattern[str]:
    screen = re.escape(_normalize_screen_name(screen_name))
    host = r"(?:https://(?:x|twitter)\.com)"
    return re.compile(host + r"/" + screen + r"/status/(\d+)")


def _build_syndication_url(screen_name: str) -> str:
    screen = _normalize_screen_name(screen_name)
    return "https://syndication.twitter.com/srv/timeline-profile/screen-name/" + screen


FX_STATUS = "https://api.fxtwitter.com/{screen}/status/{tid}"
JINA_PROFILE_CANDIDATES = _build_jina_profile_candidates(DEFAULT_SCREEN_NAME)
JINA_WITH_REPLIES_CANDIDATES = _build_jina_replies_candidates(DEFAULT_SCREEN_NAME)
# Back-compat aliases (first candidate).
JINA_PROFILE = JINA_PROFILE_CANDIDATES[0]
JINA_WITH_REPLIES = JINA_WITH_REPLIES_CANDIDATES[0]
SYNDICATION = _build_syndication_url(DEFAULT_SCREEN_NAME)


def _parse_twitter_created_at(value: str) -> str:
    """Normalize Twitter created_at to ISO-8601 (local-aware if possible)."""
    text = str(value or "").strip()
    if not text:
        return ""
    # Prefer RFC2822 / Twitter "Sat Jul 25 10:47:51 +0000 2026" before ISO,
    # because "+" also appears in that format and breaks fromisoformat.
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().isoformat()
    except Exception:
        return text


def _clean_jina_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    text = re.sub(r"\[!\[.*?$", "", text).strip()
    # strip engagement tails like "5  16 [](...)[3.7K](...)"
    text = re.sub(r"\s+\d+\s+\d+\s*\[.*?$", "", text).strip()
    text = re.sub(r"\s+\d+\s*$", "", text).strip()
    return text


def parse_jina_markdown(md: str, screen_name: str = DEFAULT_SCREEN_NAME) -> List[Dict[str, Any]]:
    """Parse jina.ai profile markdown into post stubs."""
    posts: List[Dict[str, Any]] = []
    seen = set()
    jina_post_re = _build_jina_post_re(screen_name)
    status_id_re = _build_status_id_re(screen_name)
    for when, tid, body in jina_post_re.findall(md or ""):
        tid = str(tid)
        if tid in seen:
            continue
        seen.add(tid)
        text = _clean_jina_text(body)
        if not text:
            continue
        posts.append(
            {
                "id": tid,
                "text": text,
                "when_label": str(when).strip(),
                "source": "scrapy_jina",
                "kind": "post",
            }
        )
    # fallback: any status ids if regex misses structured blocks
    if not posts:
        for tid in status_id_re.findall(md or ""):
            if tid in seen:
                continue
            seen.add(tid)
            posts.append(
                {
                    "id": tid,
                    "text": "",
                    "source": "scrapy_jina",
                    "kind": "post",
                }
            )
    return posts


def parse_syndication_html(html: str) -> List[Dict[str, Any]]:
    """Extract tweet objects embedded in syndication profile HTML."""
    tweets: List[Dict[str, Any]] = []
    for m in re.finditer(r'\{"id":0,"location":""', html or ""):
        start = m.start()
        depth = 0
        blob = ""
        for i, ch in enumerate(html[start : start + 30000]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = html[start : start + i + 1]
                    break
        if not blob:
            continue
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        tid = str(obj.get("id_str") or "")
        text = str(obj.get("full_text") or "").strip()
        if not tid or not text:
            continue
        is_reply = bool(obj.get("in_reply_to_status_id_str") or obj.get("in_reply_to_user_id_str"))
        row: Dict[str, Any] = {
            "id": tid,
            "created_at": _parse_twitter_created_at(str(obj.get("created_at") or "")),
            "text": text,
            "source": "scrapy_syndication",
            "kind": "reply" if is_reply else "post",
        }
        if obj.get("in_reply_to_status_id_str"):
            row["in_reply_to_tweet_id"] = str(obj.get("in_reply_to_status_id_str"))
            row["referenced_tweet_id"] = row["in_reply_to_tweet_id"]
        if obj.get("in_reply_to_screen_name"):
            row["in_reply_to_screen_name"] = str(obj.get("in_reply_to_screen_name"))
        tweets.append(row)
    # unique by id, prefer first (usually pin + timeline order)
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in tweets:
        by_id.setdefault(row["id"], row)
    return list(by_id.values())


def _fx_extract_text(t: Dict[str, Any]) -> str:
    """Prefer full tweet body (note-tweet / raw_text dict / text)."""
    # Long-form note tweets
    note = t.get("note_tweet")
    if isinstance(note, dict):
        for key in ("text", "raw_text", "full_text"):
            val = note.get(key)
            if isinstance(val, dict):
                val = val.get("text")
            if isinstance(val, str) and val.strip():
                return val.strip()
    raw = t.get("raw_text")
    if isinstance(raw, dict):
        val = raw.get("text")
        if isinstance(val, str) and val.strip():
            return val.strip()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    for key in ("text", "full_text"):
        val = t.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _merge_post_row(prev: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two post rows; longer full text wins (fxtwitter over jina stubs)."""
    merged = dict(prev)
    prev_text = str(prev.get("text") or "")
    new_text = str(row.get("text") or "")
    for k, v in row.items():
        if not v:
            continue
        if k == "text":
            # Longer body wins; on equal length prefer fxtwitter (authoritative full post).
            prefer_fx = (
                len(new_text) > len(prev_text)
                or (
                    len(new_text) == len(prev_text)
                    and str(row.get("source") or "").startswith("scrapy_fxtwitter")
                )
            )
            if prefer_fx and new_text:
                merged["text"] = new_text
                if row.get("source"):
                    merged["source"] = row.get("source")
                if row.get("full_text"):
                    merged["full_text"] = row.get("full_text")
            continue
        if k in ("parent_text", "question_text"):
            if len(str(v)) >= len(str(merged.get(k) or "")):
                merged[k] = v
            continue
        if k == "kind" and v == "reply":
            merged[k] = "reply"
            continue
        if k == "created_at":
            # Prefer ISO-looking / longer normalized timestamps.
            if not merged.get(k) or ("T" in str(v) and "T" not in str(merged.get(k) or "")):
                merged[k] = v
            elif len(str(v)) > len(str(merged.get(k) or "")):
                merged[k] = v
            continue
        if not merged.get(k):
            merged[k] = v
    return merged


def parse_fxtwitter_status(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize fxtwitter status JSON → post/reply row (full text)."""
    if not isinstance(payload, dict):
        return None
    t = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else payload
    if not isinstance(t, dict):
        return None
    tid = str(t.get("id") or t.get("id_str") or "")
    text = _fx_extract_text(t)
    if not tid or not text:
        return None
    replying_to = t.get("replying_to")
    replying_status = t.get("replying_to_status") or t.get("in_reply_to_status_id_str")
    is_reply = bool(replying_to or replying_status or str(text).startswith("@"))
    row: Dict[str, Any] = {
        "id": tid,
        "created_at": _parse_twitter_created_at(str(t.get("created_at") or "")),
        "text": text,
        "source": "scrapy_fxtwitter",
        "kind": "reply" if is_reply else "post",
        "full_text": text,
    }
    if replying_status:
        row["in_reply_to_tweet_id"] = str(replying_status)
        row["referenced_tweet_id"] = str(replying_status)
    if replying_to:
        # may be handle or list
        if isinstance(replying_to, list):
            row["in_reply_to_screen_name"] = ",".join(str(x) for x in replying_to)
        else:
            row["in_reply_to_screen_name"] = str(replying_to)
    return row


class TimelineSpider(scrapy.Spider):
    name = "sszcw_timeline"
    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS": 2,
        "LOG_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": False,
        "USER_AGENT": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    }

    def __init__(self, screen_name: str = DEFAULT_SCREEN_NAME, limit: int = 40, include_replies: bool = True, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen_name = _normalize_screen_name(screen_name)
        self.limit = int(limit or 40)
        self.include_replies = str(include_replies).lower() not in {"0", "false", "no"}
        self.collected: List[Dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._pending_parent: Dict[str, str] = {}  # child_id -> parent_id

    def start_requests(self):
        for i, url in enumerate(_build_jina_profile_candidates(self.screen_name)):
            yield scrapy.Request(
                url,
                callback=self.parse_jina,
                errback=self._err,
                dont_filter=True,
                priority=10 - i,
            )
        if self.include_replies:
            for i, url in enumerate(_build_jina_replies_candidates(self.screen_name)):
                yield scrapy.Request(
                    url,
                    callback=self.parse_jina_replies_page,
                    errback=self._err,
                    dont_filter=True,
                    priority=-5 - i,
                )
        yield scrapy.Request(
            _build_syndication_url(self.screen_name),
            callback=self.parse_syndication,
            errback=self._err,
            dont_filter=True,
            priority=-10,
        )

    def _err(self, failure):
        self.logger.debug("timeline fetch soft-fail: %s", failure)

    def _add(self, row: Dict[str, Any]) -> None:
        tid = str(row.get("id") or "")
        text = str(row.get("text") or "").strip()
        if not tid or not text:
            return
        row = dict(row)
        row.setdefault("author_handle", self.screen_name)
        row.setdefault("screen_name", self.screen_name)
        if tid in self._seen_ids:
            for i, prev in enumerate(self.collected):
                if prev.get("id") == tid:
                    self.collected[i] = _merge_post_row(prev, row)
                    return
            return
        self._seen_ids.add(tid)
        self.collected.append(row)

    def parse_jina(self, response: scrapy.http.Response):
        posts = parse_jina_markdown(response.text, self.screen_name)
        for row in posts[: self.limit]:
            self._add(row)
            tid = row["id"]
            url = FX_STATUS.format(screen=self.screen_name, tid=tid)
            yield scrapy.Request(
                url,
                callback=self.parse_fxtwitter,
                errback=self._err,
                meta={"seed_id": tid, "seed_text": row.get("text")},
                dont_filter=True,
                priority=10,
            )

    def parse_jina_replies_page(self, response: scrapy.http.Response):
        posts = parse_jina_markdown(response.text, self.screen_name)
        for row in posts:
            row = {**row, "kind": "reply" if row.get("kind") == "post" else row.get("kind"), "source": "scrapy_jina_replies"}
            if "hasn’t posted" in response.text or "hasn&#39;t posted" in response.text:
                return
            self._add(row)
            tid = row.get("id")
            if tid:
                yield scrapy.Request(
                    FX_STATUS.format(screen=self.screen_name, tid=tid),
                    callback=self.parse_fxtwitter,
                    errback=self._err,
                    meta={"seed_id": tid, "seed_text": row.get("text")},
                    dont_filter=True,
                )

    def parse_syndication(self, response: scrapy.http.Response):
        for row in parse_syndication_html(response.text)[: max(self.limit, 20)]:
            self._add(row)
            if row.get("kind") == "reply" and row.get("in_reply_to_tweet_id"):
                parent_id = str(row["in_reply_to_tweet_id"])
                self._pending_parent[row["id"]] = parent_id
                yield scrapy.Request(
                    f"https://api.fxtwitter.com/status/{parent_id}",
                    callback=self.parse_parent,
                    errback=self._err,
                    meta={"child_id": row["id"], "parent_id": parent_id},
                    dont_filter=True,
                )

    def parse_fxtwitter(self, response: scrapy.http.Response):
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return
        row = parse_fxtwitter_status(payload)
        if not row:
            seed_id = response.meta.get("seed_id")
            seed_text = response.meta.get("seed_text")
            if seed_id and seed_text:
                self._add(
                    {
                        "id": str(seed_id),
                        "text": str(seed_text),
                        "source": "scrapy_jina",
                        "kind": "post",
                    }
                )
            return
        seed_text = response.meta.get("seed_text")
        if seed_text and not row.get("text"):
            row["text"] = seed_text
        row.setdefault("author_handle", self.screen_name)
        row.setdefault("screen_name", self.screen_name)
        self._add(row)
        parent_id = row.get("in_reply_to_tweet_id") or row.get("referenced_tweet_id")
        if parent_id and self.include_replies:
            yield scrapy.Request(
                f"https://api.fxtwitter.com/status/{parent_id}",
                callback=self.parse_parent,
                errback=self._err,
                meta={"child_id": row["id"], "parent_id": str(parent_id)},
                dont_filter=True,
                priority=20,
            )

    def parse_parent(self, response: scrapy.http.Response):
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return
        parent = parse_fxtwitter_status(payload)
        if not parent:
            return
        child_id = str(response.meta.get("child_id") or "")
        parent_text = str(parent.get("text") or "")
        if not child_id or not parent_text:
            return
        for i, row in enumerate(self.collected):
            if str(row.get("id")) == child_id:
                merged = dict(row)
                merged["kind"] = "reply"
                merged["parent_text"] = parent_text
                merged["question_text"] = parent_text
                merged["in_reply_to_tweet_id"] = str(response.meta.get("parent_id") or parent.get("id"))
                merged["referenced_tweet_id"] = merged["in_reply_to_tweet_id"]
                merged.setdefault("author_handle", self.screen_name)
                merged.setdefault("screen_name", self.screen_name)
                self.collected[i] = merged
                break

    def closed(self, reason):
        def sort_key(r: Dict[str, Any]):
            return (str(r.get("created_at") or ""), str(r.get("id") or ""))

        self.collected.sort(key=sort_key, reverse=True)
        if len(self.collected) > self.limit:
            self.collected = self.collected[: self.limit]


def run_timeline_scrapy_fetch(
    *,
    screen_name: str = DEFAULT_SCREEN_NAME,
    limit: int = 40,
    include_replies: bool = True,
    timeout_sec: int = 60,
) -> List[Dict[str, Any]]:
    """Run spider in-process and return collected posts/replies."""
    try:
        from scrapy.crawler import CrawlerProcess
    except Exception:
        return _run_timeline_urllib_fallback(screen_name=screen_name, limit=limit, include_replies=include_replies)

    posts_holder: List[Dict[str, Any]] = []

    class _Collector(TimelineSpider):
        def closed(self, reason):
            super().closed(reason)
            posts_holder.extend(self.collected)

    try:
        settings = {
            "ROBOTSTXT_OBEY": False,
            "LOG_ENABLED": False,
            "TELNETCONSOLE_ENABLED": False,
            "COOKIES_ENABLED": False,
            "DOWNLOAD_TIMEOUT": min(30, max(8, timeout_sec // 2)),
            "CLOSESPIDER_TIMEOUT": timeout_sec,
            "USER_AGENT": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        process = CrawlerProcess(settings, install_root_handler=False)
        process.crawl(_Collector, screen_name=screen_name, limit=limit, include_replies=include_replies)
        process.start()
    except Exception:
        return _run_timeline_urllib_fallback(screen_name=screen_name, limit=limit, include_replies=include_replies)
    return posts_holder


def run_sszcw_scrapy_fetch(
    *,
    limit: int = 40,
    include_replies: bool = True,
    timeout_sec: int = 60,
) -> List[Dict[str, Any]]:
    return run_timeline_scrapy_fetch(
        screen_name=DEFAULT_SCREEN_NAME,
        limit=limit,
        include_replies=include_replies,
        timeout_sec=timeout_sec,
    )


def _run_timeline_urllib_fallback(
    *,
    screen_name: str = DEFAULT_SCREEN_NAME,
    limit: int = 40,
    include_replies: bool = True,
) -> List[Dict[str, Any]]:
    """Same parse path without CrawlerProcess (tests / scrapy reactor issues)."""
    import urllib.request

    screen_name = _normalize_screen_name(screen_name)

    def _get(url: str, timeout: int = 20) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    collected: Dict[str, Dict[str, Any]] = {}

    def add(row: Dict[str, Any]) -> None:
        tid = str(row.get("id") or "")
        if not tid or not str(row.get("text") or "").strip():
            return
        row = dict(row)
        row.setdefault("author_handle", screen_name)
        row.setdefault("screen_name", screen_name)
        prev = collected.get(tid)
        if not prev:
            collected[tid] = row
            return
        collected[tid] = _merge_post_row(prev, row)

    md = ""
    for url in _build_jina_profile_candidates(screen_name):
        try:
            md = _get(url, 25)
            if parse_jina_markdown(md, screen_name):
                break
        except Exception:
            continue
    for row in parse_jina_markdown(md, screen_name)[:limit]:
        add(row)
        try:
            raw = _get(FX_STATUS.format(screen=screen_name, tid=row["id"]), 15)
            fx = parse_fxtwitter_status(json.loads(raw))
            if fx:
                fx.setdefault("author_handle", screen_name)
                fx.setdefault("screen_name", screen_name)
                add(fx)
                parent_id = fx.get("in_reply_to_tweet_id") if include_replies else None
                if parent_id:
                    try:
                        praw = _get(f"https://api.fxtwitter.com/status/{parent_id}", 12)
                        parent = parse_fxtwitter_status(json.loads(praw))
                        if parent and parent.get("text"):
                            child = dict(collected.get(fx["id"]) or fx)
                            child["kind"] = "reply"
                            child["parent_text"] = parent["text"]
                            child["question_text"] = parent["text"]
                            add(child)
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        html = _get(_build_syndication_url(screen_name), 25)
        for row in parse_syndication_html(html)[: max(limit, 20)]:
            add(row)
    except Exception:
        pass

    rows = list(collected.values())
    for r in rows:
        ft = str(r.get("full_text") or "")
        if ft and len(ft) >= len(str(r.get("text") or "")):
            r["text"] = ft
    rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id") or "")), reverse=True)
    return rows[:limit]


def _run_sszcw_urllib_fallback(
    *,
    limit: int = 40,
    include_replies: bool = True,
) -> List[Dict[str, Any]]:
    return _run_timeline_urllib_fallback(
        screen_name=DEFAULT_SCREEN_NAME,
        limit=limit,
        include_replies=include_replies,
    )
