#!/usr/bin/env python3
"""Build @sszcw market-direction context for pre-pick soft guidance.

Rules:
- Diagnostic + soft sector bias only; never force PAPER_PICK or rewrite production weights.
- Prefer cached posts under data/sszcw/; --seed-from-summary uses known case notes when X is offline.
- Include **replies** (not only original posts): followers ask market/stock Qs; his answers matter.
- Output: summary/sszcw_market_context_{date}.json and data/sszcw/latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "sszcw"
SUMMARY_DIR = ROOT / "summary"
LIVE_INBOX_PATH = DATA_DIR / "live_inbox.jsonl"
DEFAULT_SOCIAL_HANDLES = ("sszcw", "naiyin04", "andredavid90")
TRUSTED_STOCK_PREDICTION_HANDLES = {"sszcw"}
DEFAULT_HANDLE_LIMIT = int(os.environ.get("XIAOGU_SOCIAL_HANDLE_LIMIT", "25") or 25)
_ENV_LOADED = False

# A-share code mentions in free text / Q&A.
STOCK_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")
# Common stock-name tokens for Q&A (extend via posts; keep short to avoid false hits).
STOCK_NAME_HINTS = (
    "通富微电", "华天科技", "长城军工", "中光学", "建设工业", "中兴通讯",
    "宁德时代", "中芯国际", "寒武纪", "海光信息", "北方华创", "中微公司",
    "贵州茅台", "五粮液", "中国平安", "招商银行", "比亚迪", "隆基绿能",
    "阳光电源", "紫金矿业", "中国神华", "中国石油", "中国海油", "中国西电",
    "华银电力", "生益科技", "昭衍新药", "至纯科技", "电科芯片", "博通集成",
)
QA_MARKET_RE = re.compile(
    r"大盘|指数|行情|走势|方向|支撑|压力|主线|观望|牛市|熊市|反弹|调整|见底|见顶|仓位|空仓|满仓"
)
QA_STOCK_RE = re.compile(
    r"这只|该股|个股|能不能买|能买吗|怎么看|如何看|持有|下车|上车|追高|补仓|止损|目标位|几点买"
)
BULLISH_RE = re.compile(r"还会涨|继续看多|可以拿|持有|逢低|低吸|有机会|不急卖|趋势向上|强势|主升")
BEARISH_RE = re.compile(r"趋势向下|透支|减仓|不追|别碰|风险|观望|走弱|鱼尾|高潮|出不完|不建议|规避")


def _load_project_env() -> None:
    """Load xiaogu .env into process env without overriding existing values."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
    except OSError:
        pass


def live_token_configured() -> bool:
    """True when any live fetch path can run (scrapy default, or token/URL)."""
    _load_project_env()
    if (os.environ.get("XIAOGU_SSZCW_SKIP_SCRAPY") or "").strip() != "1":
        return True  # scrapy/jina path is always attempted
    return bool(
        (
            os.environ.get("X_BEARER_TOKEN")
            or os.environ.get("TWITTER_BEARER_TOKEN")
            or os.environ.get("XIAOGU_X_BEARER_TOKEN")
            or os.environ.get("XIAOGU_SSZCW_LIVE_URL")
            or ""
        ).strip()
    )

# Theme lexicon for method objectization (not follow-trade tips).
THEME_LEXICON: Dict[str, List[str]] = {
    "贵金属": ["黄金", "白银", "贵金属", "金", "银"],
    "有色": ["有色", "铜", "铝", "锌", "镍"],
    "油气": ["石油", "油气", "油服", "原油", "炼化"],
    "煤炭": ["煤炭", "煤", "动力煤", "焦煤"],
    "电力": ["电力", "火电", "火力发电", "水电", "电网"],
    "医药": ["医药", "创新药", "CXO", "减肥药", "药", "医美"],
    "半导体": ["半导体", "电子", "CPO", "芯片", "算力", "科技股"],
    "白酒": ["白酒"],
}

FAVORED_DEFENSIVE = {"贵金属", "有色", "油气", "煤炭", "电力", "医药"}
RISK_OFF_TECH = {"半导体"}

STAGE_PATTERNS = [
    (re.compile(r"没有主线|边走边看"), "NO_MAIN"),
    (re.compile(r"观望为主|观望"), "WATCH"),
    (re.compile(r"超跌反弹|反弹"), "BOUNCE"),
    (re.compile(r"高潮|鱼尾|透支"), "CLIMAX"),
    (re.compile(r"主升|主线"), "MAIN_UP"),
    (re.compile(r"轮动"), "ROTATION"),
]

INDEX_PATTERNS = [
    (re.compile(r"往下|下跌才是真正|趋势是向下"), "DOWNTREND"),
    (re.compile(r"支撑|硬底|3763|不稳"), "FRAGILE"),
    (re.compile(r"稳住|硬底确认"), "STABLE"),
]


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def _normalize_handle(handle: str) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _normalize_handles(handles: Any) -> List[str]:
    if handles is None:
        raw: Iterable[Any] = DEFAULT_SOCIAL_HANDLES
    elif isinstance(handles, str):
        raw = handles.split(",")
    else:
        raw = handles
    normalized: List[str] = []
    seen = set()
    for item in raw:
        handle = _normalize_handle(str(item))
        if not handle or handle in seen:
            continue
        seen.add(handle)
        normalized.append(handle)
    return normalized or list(DEFAULT_SOCIAL_HANDLES)


def _row_handle(row: Dict[str, Any]) -> str:
    for key in ("author_handle", "screen_name", "handle", "username"):
        value = _normalize_handle(str(row.get(key) or ""))
        if value:
            return value
    return ""


def _post_day(ts: str) -> Optional[date]:
    text = str(ts or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _post_source_kind(row: Dict[str, Any]) -> str:
    src = str(row.get("source") or "").lower()
    if src.startswith("seed"):
        return "seed"
    if src in (
        "live",
        "x_api",
        "x_live",
        "nitter",
        "web_live",
        "cdp_live",
        "inbox",
        "scrapy",
        "scrapy_jina",
        "scrapy_jina_replies",
        "scrapy_fxtwitter",
        "scrapy_syndication",
    ):
        return "live"
    if src in ("cache", "cached", "file_cache"):
        return "cache"
    # Untagged historical cache treated as cache, not live.
    return "cache" if src else "unknown"


def _dedupe_posts(posts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe by tweet id (preferred) or text; keep fuller body + reply metadata."""
    by_key: Dict[str, Dict[str, Any]] = {}
    rank = {
        "live": 3,
        "x_api": 3,
        "x_live": 3,
        "web_live": 3,
        "inbox": 3,
        "scrapy": 3,
        "scrapy_jina": 3,
        "scrapy_jina_replies": 3,
        "scrapy_fxtwitter": 4,  # authoritative full post body
        "scrapy_syndication": 3,
        "nitter": 3,
        "cdp_live": 3,
        "cache": 2,
        "cached": 2,
        "file_cache": 2,
    }

    def _richness(row: Dict[str, Any]) -> int:
        score = len(str(row.get("text") or ""))  # full text length is primary
        if _is_reply_row(row):
            score += 50
        if row.get("parent_text") or row.get("question_text") or row.get("in_reply_to_text"):
            score += 80
        if row.get("referenced_tweet_id") or row.get("in_reply_to_tweet_id"):
            score += 20
        if row.get("created_at"):
            score += 5
        src = str(row.get("source") or "").lower()
        if src.startswith("scrapy_fxtwitter"):
            score += 30
        return score

    def _row_key(row: Dict[str, Any]) -> str:
        tid = str(row.get("id") or "").strip()
        if tid:
            return f"id:{tid}"
        return f"text:{str(row.get('text') or '').strip()}"

    for row in posts:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        # Normalize full_text onto text when longer.
        ft = str(row.get("full_text") or "").strip()
        if ft and len(ft) > len(text):
            row = dict(row)
            row["text"] = ft
            text = ft
        key = _row_key(row)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = row
            continue
        prev_kind = _post_source_kind(prev)
        new_kind = _post_source_kind(row)
        prev_rank = rank.get(str(prev.get("source") or prev_kind).lower(), rank.get(prev_kind, 1))
        new_rank = rank.get(str(row.get("source") or new_kind).lower(), rank.get(new_kind, 1))
        if _richness(row) > _richness(prev):
            by_key[key] = row
        elif _richness(row) == _richness(prev) and new_rank > prev_rank:
            by_key[key] = row
    return list(by_key.values())


def load_cached_posts() -> List[Dict[str, Any]]:
    posts: List[Dict[str, Any]] = []
    if not DATA_DIR.exists():
        return posts
    for path in sorted(DATA_DIR.glob("posts_*.jsonl")):
        # seed_snapshot is fallback-only and not treated as durable cache.
        if path.name.startswith("posts_seed"):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not row.get("text"):
                continue
            if not row.get("source"):
                row = {**row, "source": "cache"}
            # Never treat seed rows as durable cache (legacy pollution).
            if _post_source_kind(row) == "seed":
                continue
            posts.append(row)
    latest = DATA_DIR / "posts_latest.json"
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            for row in payload if isinstance(payload, list) else payload.get("posts") or []:
                if not isinstance(row, dict) or not row.get("text"):
                    continue
                if not row.get("source"):
                    row = {**row, "source": "cache"}
                if _post_source_kind(row) == "seed":
                    continue
                posts.append(row)
        except Exception:
            pass
    return _dedupe_posts(posts)


def purge_seed_from_durable_cache() -> int:
    """Rewrite dated/latest cache files dropping seed rows. Returns removed count."""
    if not DATA_DIR.exists():
        return 0
    removed = 0
    paths = list(DATA_DIR.glob("posts_*.jsonl"))
    latest = DATA_DIR / "posts_latest.json"
    for path in paths:
        if path.name.startswith("posts_seed"):
            continue
        try:
            keep: List[Dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or not row.get("text"):
                    continue
                if _post_source_kind(row) == "seed":
                    removed += 1
                    continue
                keep.append(row)
            with path.open("w", encoding="utf-8") as fh:
                for row in keep:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            continue
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("posts") or []
            keep = []
            for row in rows:
                if not isinstance(row, dict) or not row.get("text"):
                    continue
                if _post_source_kind(row) == "seed":
                    removed += 1
                    continue
                keep.append(row)
            latest.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return removed


def load_live_inbox() -> List[Dict[str, Any]]:
    """Manual/agent-injected live posts (data/sszcw/live_inbox.jsonl)."""
    if not LIVE_INBOX_PATH.exists():
        return []
    posts: List[Dict[str, Any]] = []
    try:
        for line in LIVE_INBOX_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not row.get("text"):
                continue
            item: Dict[str, Any] = {
                "id": str(row.get("id") or f"inbox-{abs(hash(str(row.get('text'))))%10**10}"),
                "created_at": str(row.get("created_at") or row.get("date") or ""),
                "text": str(row.get("text") or ""),
                "source": "inbox",
                "kind": str(row.get("kind") or ("reply" if row.get("parent_text") or row.get("question_text") else "post")),
            }
            handle = _normalize_handle(str(row.get("author_handle") or row.get("screen_name") or row.get("handle") or ""))
            item["author_handle"] = handle or "sszcw"
            # Preserve Q&A fields when agent/manual injects replies with parent question.
            for key in (
                "parent_text",
                "question_text",
                "in_reply_to_text",
                "in_reply_to_tweet_id",
                "referenced_tweet_id",
                "in_reply_to_user_id",
            ):
                if row.get(key):
                    item[key] = row.get(key)
            posts.append(item)
    except OSError:
        return []
    return posts


def seed_builtin_posts() -> List[Dict[str, Any]]:
    """Seed from known public @sszcw themes already captured in project case notes + live window."""
    return [
        {
            "id": "seed-2026-07-20-close",
            "created_at": "2026-07-20T15:00:00+08:00",
            "text": "【A股收盘总结】07月20日 板块涨幅TOP：油气及炼化工程 油服工程 油气开采 火力发电；跌幅TOP：电子化学品 半导体材料。大盘反复回踩确认3763支撑，观望为主。",
            "source": "seed_case_and_live",
            "author_handle": "sszcw",
        },
        {
            "id": "seed-2026-07-20-support",
            "created_at": "2026-07-20T20:46:00+08:00",
            "text": "今天大盘反复回踩确认3763的支撑，目前看是有效的，但是否是硬底仍需观察，从大趋势来看，还得往下，观望为主。",
            "source": "seed_live",
            "author_handle": "sszcw",
        },
        {
            "id": "seed-2026-07-22-copper",
            "created_at": "2026-07-22T12:34:00+08:00",
            "text": "铜还会涨，像石油，黄金，有色这些与期货强关联。",
            "source": "seed_live",
            "author_handle": "sszcw",
        },
        {
            "id": "seed-2026-07-22-semi",
            "created_at": "2026-07-22T14:34:00+08:00",
            "text": "半导体已经透支了未来一年的市场容量。科技股反弹是两三天的事情，趋势是向下的，CPO资金体量出不完。",
            "source": "seed_live",
            "author_handle": "sszcw",
        },
        {
            "id": "seed-2026-07-17-nomain",
            "created_at": "2026-07-17T15:00:00+08:00",
            "text": "现在没有主线，边走边看。药是主线/老登天下是中线叙事，短线仍要看涨跌榜与阶段。",
            "source": "seed_case",
            "author_handle": "sszcw",
        },
        {
            "id": "seed-2026-07-21-method",
            "created_at": "2026-07-21T11:36:00+08:00",
            "text": "会懂的人没有牛熊之分，但重仓一只左侧交易从鱼头吃到鱼尾；高潮鱼尾不吃。",
            "source": "seed_live",
            "author_handle": "sszcw",
        },
    ]


def save_posts(posts: Iterable[Dict[str, Any]], asof: date, *, seed_only: bool = False) -> Path:
    """Persist posts. Seed-only snapshots use a separate file to avoid cache pollution."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = _dedupe_posts(list(posts))
    if seed_only:
        path = DATA_DIR / "posts_seed_snapshot.jsonl"
    else:
        path = DATA_DIR / f"posts_{asof.isoformat().replace('-', '')}.jsonl"
        # Merge with existing dated non-seed cache so re-runs don't wipe live rows.
        existing: List[Dict[str, Any]] = []
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and row.get("text") and _post_source_kind(row) != "seed":
                        existing.append(row)
            except OSError:
                pass
        rows = _dedupe_posts(existing + rows)
        # Drop pure seed rows from durable dated cache.
        rows = [r for r in rows if _post_source_kind(r) != "seed"]
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if not seed_only:
        latest = DATA_DIR / "posts_latest.json"
        latest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def posts_in_window(posts: List[Dict[str, Any]], end: date, days: int = 5) -> List[Dict[str, Any]]:
    start = end - timedelta(days=max(1, days) - 1)
    selected = []
    for row in posts:
        day = _post_day(str(row.get("created_at") or row.get("date") or ""))
        if day is None:
            continue
        if start <= day <= end:
            selected.append({**row, "post_date": day.isoformat()})
    # de-dupe by text + kind (reply vs original may share short text)
    seen = set()
    unique = []
    for row in selected:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        kind = str(row.get("kind") or row.get("post_kind") or "post")
        key = f"{kind}:{text}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _is_reply_row(row: Dict[str, Any]) -> bool:
    kind = str(row.get("kind") or row.get("post_kind") or "").lower()
    if kind in ("reply", "qa_reply", "answer"):
        return True
    if row.get("in_reply_to_user_id") or row.get("in_reply_to_tweet_id") or row.get("referenced_tweet_id"):
        return True
    if row.get("parent_text") or row.get("question_text"):
        return True
    text = str(row.get("text") or "")
    # Heuristic: starts with @mention and short answer style
    if text.startswith("@") and len(text) < 280:
        return True
    return False


def extract_stock_mentions(text: str) -> List[str]:
    """Extract A-share codes and known name hints from free text."""
    found: List[str] = []
    for m in STOCK_CODE_RE.findall(str(text or "")):
        code = str(m).zfill(6)
        if code not in found:
            found.append(code)
    for name in STOCK_NAME_HINTS:
        if name and name in (text or "") and name not in found:
            found.append(name)
    return found


def _tone_from_answer(answer: str) -> str:
    a = str(answer or "")
    bull = bool(BULLISH_RE.search(a))
    bear = bool(BEARISH_RE.search(a))
    if bull and not bear:
        return "BULLISH"
    if bear and not bull:
        return "BEARISH"
    if bull and bear:
        return "MIXED"
    return "NEUTRAL"


def extract_qa_pairs(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Q&A cards from reply rows (+ optional parent question).

    Focus: market-direction and individual-stock questions he answers in replies.
    Soft diagnostic only — never becomes official pick list.
    """
    cards: List[Dict[str, Any]] = []
    for row in posts:
        if not _is_reply_row(row) and not row.get("parent_text"):
            # Still scan original posts that look like "Q: ... A: ..." rare; skip.
            continue
        answer = str(row.get("text") or "").strip()
        if not answer:
            continue
        question = str(
            row.get("question_text")
            or row.get("parent_text")
            or row.get("in_reply_to_text")
            or ""
        ).strip()
        combined = f"{question}\n{answer}"
        stocks = extract_stock_mentions(combined)
        is_market = bool(QA_MARKET_RE.search(combined))
        is_stock_phrase = bool(QA_STOCK_RE.search(combined))
        # Stock type only when concrete code/name present; "怎么看" alone is not enough
        # (often used on 大盘/板块 questions too).
        is_stock_q = bool(stocks) or (is_stock_phrase and not is_market)
        if not is_market and not is_stock_q and not stocks:
            # Keep short replies only when they look like direct answers with tone
            if not (BULLISH_RE.search(answer) or BEARISH_RE.search(answer)):
                continue
        themes: List[str] = []
        for theme, words in THEME_LEXICON.items():
            if any(w in combined for w in words):
                themes.append(theme)
        if stocks:
            qa_type = "stock"
        elif is_market:
            qa_type = "market"
        elif is_stock_phrase:
            qa_type = "stock"
        else:
            qa_type = "general"
        cards.append(
            {
                "date": str(row.get("post_date") or row.get("created_at") or "")[:10],
                "reply_id": str(row.get("id") or ""),
                "question": question[:220] if question else None,
                "answer": answer[:280],
                "stocks": stocks[:8],
                "themes": themes[:6],
                "qa_type": qa_type,
                "tone": _tone_from_answer(answer),
                "source_kind": _post_source_kind(row),
                "kind": "reply",
                "observation_only": True,
            }
        )
    # Prefer stock/market cards, cap length
    cards.sort(key=lambda c: (0 if c.get("qa_type") == "stock" else 1, c.get("date") or ""), reverse=False)
    stock_cards = [c for c in cards if c.get("qa_type") == "stock"]
    market_cards = [c for c in cards if c.get("qa_type") == "market"]
    other = [c for c in cards if c.get("qa_type") not in ("stock", "market")]
    return (stock_cards + market_cards + other)[:20]


def favored_stock_mentions_from_qa(qa_pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate soft stock-level hints from Q&A tones (never official picks)."""
    bullish: Counter[str] = Counter()
    bearish: Counter[str] = Counter()
    for card in qa_pairs:
        tone = str(card.get("tone") or "")
        for s in card.get("stocks") or []:
            if tone == "BULLISH":
                bullish[str(s)] += 1
            elif tone == "BEARISH":
                bearish[str(s)] += 1
    return {
        "soft_bullish_stocks": [k for k, _ in bullish.most_common(10)],
        "soft_bearish_stocks": [k for k, _ in bearish.most_common(10)],
        "bullish_counts": dict(bullish),
        "bearish_counts": dict(bearish),
        "note": "soft stock hints from sszcw replies only; not PAPER_PICK candidates",
    }


def trusted_stock_predictions_from_posts(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract explicit @sszcw stock calls for direct-confirmation use.

    Market/sector opinions remain soft context. A concrete code/name in a
    trusted account's post is stronger: it may confirm a candidate only after
    the runner also sees T-day price/flow structure.
    """
    bullish: Counter[str] = Counter()
    bearish: Counter[str] = Counter()
    for row in posts:
        handle = _row_handle(row).lower()
        if handle not in TRUSTED_STOCK_PREDICTION_HANDLES:
            continue
        text = str(row.get("text") or "")
        parent = str(row.get("parent_text") or row.get("question_text") or "")
        combined = f"{parent}\n{text}"
        stocks = extract_stock_mentions(combined)
        if not stocks:
            continue
        tone = _tone_from_answer(text)
        for stock in stocks:
            if tone == "BULLISH":
                bullish[str(stock)] += 1
            elif tone == "BEARISH":
                bearish[str(stock)] += 1
    return {
        "trusted_handles": sorted(TRUSTED_STOCK_PREDICTION_HANDLES),
        "bullish_stocks": [k for k, _ in bullish.most_common(20)],
        "bearish_stocks": [k for k, _ in bearish.most_common(20)],
        "bullish_counts": dict(bullish),
        "bearish_counts": dict(bearish),
        "confidence": 1.0,
        "policy": (
            "Explicit @sszcw stock calls are trusted direct confirmation when "
            "matched by symbol/name; T+1 price/flow structure and hard gates remain required."
        ),
    }


def analyze_posts(
    posts: List[Dict[str, Any]],
    asof: date,
    *,
    window_days: int = 5,
    source_label: str = "@sszcw",
    include_accounts: bool = True,
) -> Dict[str, Any]:
    theme_hits: Counter[str] = Counter()
    stage_hits: Counter[str] = Counter()
    index_hits: Counter[str] = Counter()
    excerpts: List[Dict[str, Any]] = []
    live_count = 0
    seed_count = 0
    cache_count = 0
    reply_count = 0
    original_count = 0
    for row in posts:
        text = str(row.get("text") or "")
        if not text:
            continue
        kind = _post_source_kind(row)
        if kind == "live":
            live_count += 1
        elif kind == "seed":
            seed_count += 1
        else:
            cache_count += 1
        is_reply = _is_reply_row(row)
        if is_reply:
            reply_count += 1
        else:
            original_count += 1
        # For theme/stage analysis, include parent question + answer so sector words in Q count.
        parent = str(row.get("parent_text") or row.get("question_text") or "")
        analysis_text = f"{parent}\n{text}" if parent else text
        excerpts.append(
            {
                "date": str(row.get("post_date") or row.get("created_at") or "")[:10],
                # Keep full post body for soft analysis / audit (no silent truncation).
                "text": text,
                "text_len": len(text),
                "source_kind": kind,
                "kind": "reply" if is_reply else "post",
                "parent_text": (parent if parent else None),
                "id": str(row.get("id") or "") or None,
                "author_handle": _row_handle(row) or None,
            }
        )
        for theme, words in THEME_LEXICON.items():
            if any(word in analysis_text for word in words):
                theme_hits[theme] += 1
        for pattern, label in STAGE_PATTERNS:
            if pattern.search(analysis_text):
                stage_hits[label] += 1
        for pattern, label in INDEX_PATTERNS:
            if pattern.search(analysis_text):
                index_hits[label] += 1

    qa_pairs = extract_qa_pairs(posts)
    stock_soft = favored_stock_mentions_from_qa(qa_pairs)
    trusted_stock_predictions = trusted_stock_predictions_from_posts(posts)

    ranked_themes = [name for name, _ in theme_hits.most_common()]
    favored = [t for t in ranked_themes if t in FAVORED_DEFENSIVE]
    risk = [t for t in ranked_themes if t in RISK_OFF_TECH]
    # If copper/oil/gold mentioned, force resource chain into favored even if tied
    for must in ("有色", "油气", "贵金属", "煤炭", "电力", "医药"):
        if theme_hits.get(must) and must not in favored:
            favored.append(must)

    stage = stage_hits.most_common(1)[0][0] if stage_hits else "UNKNOWN"
    index_regime = index_hits.most_common(1)[0][0] if index_hits else "UNKNOWN"
    if "DOWNTREND" in index_hits and index_hits["DOWNTREND"] >= index_hits.get(index_regime, 0):
        index_regime = "DOWNTREND"
    if "FRAGILE" in index_hits and index_regime == "UNKNOWN":
        index_regime = "FRAGILE"

    stance = "DEFENSIVE_ROTATION"
    if stage in ("WATCH", "NO_MAIN") and risk:
        stance = "RISK_OFF_TECH_DEFENSIVE"
    if stage == "CLIMAX" and risk:
        stance = "AVOID_CLIMAX_TECH"
    if not posts:
        stance = "INSUFFICIENT_POSTS"

    confidence = 0.0
    if posts:
        # Replies (Q&A) add signal but cap their weight so spam @replies don't dominate.
        conf_n = original_count + min(reply_count, 8) * 0.6
        confidence = min(
            1.0,
            0.25 + 0.1 * conf_n + 0.08 * len(favored) + (0.15 if index_regime != "UNKNOWN" else 0.0)
            + (0.05 if qa_pairs else 0.0),
        )
        # Pure seed context cannot claim full confidence (prevents systematic soft widen).
        if live_count == 0 and seed_count > 0 and cache_count == 0:
            confidence = min(confidence, 0.55)
        elif live_count == 0 and seed_count >= max(1, len(posts) // 2):
            confidence = min(confidence, 0.70)

    soft_context_source = "live" if live_count > 0 else ("cache" if cache_count > 0 else ("seed" if seed_count > 0 else "empty"))
    # Valid for high-confidence escape only when not pure-seed OR has live posts.
    soft_context_valid = bool(
        posts
        and stance not in ("INSUFFICIENT_POSTS", "MISSING")
        and (live_count > 0 or (cache_count > 0 and seed_count < len(posts)) or (seed_count > 0 and confidence >= 0.50 and len(favored) >= 1))
    )
    # High-confidence flag for runner: require non-seed-only with confidence.
    high_confidence_allowed = bool(
        soft_context_valid
        and confidence >= 0.60
        and (live_count > 0 or cache_count > 0)
        and not (live_count == 0 and seed_count > 0 and cache_count == 0)
    )

    accounts: List[Dict[str, Any]] = []
    if include_accounts:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in posts:
            handle = _row_handle(row)
            if not handle:
                continue
            grouped.setdefault(handle, []).append(row)
        for handle in sorted(grouped):
            subgroup = grouped[handle]
            account_payload = analyze_posts(
                subgroup,
                asof,
                window_days=window_days,
                source_label=f"@{handle}",
                include_accounts=False,
            )
            accounts.append(
                {
                    "handle": handle,
                    "source_label": f"@{handle}",
                    "post_count": account_payload.get("post_count", 0),
                    "original_post_count": account_payload.get("original_post_count", 0),
                    "reply_post_count": account_payload.get("reply_post_count", 0),
                    "qa_count": account_payload.get("qa_count", 0),
                    "favored_sectors": account_payload.get("favored_sectors", []),
                    "risk_sectors": account_payload.get("risk_sectors", []),
                    "market_stance": account_payload.get("market_stance"),
                    "confidence": account_payload.get("confidence"),
                    "soft_context_valid": account_payload.get("soft_context_valid"),
                    "high_confidence_allowed": account_payload.get("high_confidence_allowed"),
                }
            )

    return {
        "asof": asof.isoformat(),
        "window_days": max(1, int(window_days or 5)),
        "source": source_label,
        "handles": sorted({handle for handle in (_row_handle(row) for row in posts) if handle}),
        "handle_count": len({handle for handle in (_row_handle(row) for row in posts) if handle}),
        "post_count": len(posts),
        "original_post_count": original_count,
        "reply_post_count": reply_count,
        "live_post_count": live_count,
        "seed_post_count": seed_count,
        "cache_post_count": cache_count,
        "soft_context_source": soft_context_source,
        "soft_context_valid": soft_context_valid,
        "high_confidence_allowed": high_confidence_allowed,
        "theme_counts": dict(theme_hits),
        "favored_sectors": favored[:8],
        "risk_sectors": risk[:6],
        "mainline_stage_hint": stage,
        "index_regime_hint": index_regime,
        "market_stance": stance,
        "confidence": round(confidence, 3),
        "excerpts": excerpts[:16],
        "qa_pairs": qa_pairs,
        "qa_count": len(qa_pairs),
        "stock_soft_from_replies": stock_soft,
        "trusted_stock_predictions": trusted_stock_predictions,
        "accounts": accounts,
        "selected_for_production": False,
        "production_mutation_allowed": False,
        "usage": {
            "pre_pick": True,
            "soft_sector_bias": True,
            "reply_qa": True,
            "hard_gate": False,
            "force_pick": False,
            "note": (
                "quality-first soft context only; includes sszcw replies to market/stock questions; "
                "xiaogu gates still decide official PAPER_PICK; seed-only cannot high-confidence escape; "
                "trusted_stock_predictions may confirm only an explicitly matched @sszcw stock call "
                "after T+1 price/flow evidence passes"
            ),
        },
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }


def write_outputs(payload: Dict[str, Any], asof: date) -> Dict[str, str]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    dated = SUMMARY_DIR / f"sszcw_market_context_{asof.isoformat()}.json"
    latest_summary = SUMMARY_DIR / "sszcw_market_context_latest.json"
    latest_data = DATA_DIR / "latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    dated.write_text(text, encoding="utf-8")
    latest_summary.write_text(text, encoding="utf-8")
    latest_data.write_text(text, encoding="utf-8")
    return {
        "dated": str(dated),
        "summary_latest": str(latest_summary),
        "data_latest": str(latest_data),
    }


def _merge_live_post_row(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    """Keep the richer rendered row when the same post appears on both tabs."""
    if not previous:
        return dict(current)
    old_text = str(previous.get("text") or "")
    new_text = str(current.get("text") or "")
    if len(new_text) > len(old_text):
        merged = dict(previous)
        merged.update(current)
        return merged
    merged = dict(current)
    merged.update(previous)
    if previous.get("kind") == "reply":
        merged["kind"] = "reply"
    return merged


def _cdp_extract_articles(tab_id: str) -> List[Dict[str, Any]]:
    """Read rendered X article metadata from an existing CDP tab."""
    expression = r"""
JSON.stringify(Array.from(document.querySelectorAll("article")).map((article) => {
  const meta = (name) => article.querySelector(`meta[itemprop="${name}"]`)?.content || "";
  const status = article.querySelector('a[href*="/status/"]')?.href || "";
  const id = article.getAttribute("data-tweet-id") ||
    (status.match(/\/status\/(\d+)/) || [])[1] || "";
  const body = meta("articleBody") ||
    article.querySelector('[data-testid="tweetText"]')?.innerText || "";
  const text = article.innerText || "";
  return {
    id: String(id),
    created_at: meta("datePublished") || meta("dateCreated"),
    text: String(body || text).trim(),
    visible_text: String(text).trim(),
    url: status
  };
}).filter((row) => row.id && row.text))
"""
    raw = _sszcw_cdp_evaluate(tab_id, expression)
    if not raw:
        return []
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return []
    return [row for row in payload if isinstance(row, dict) and row.get("id") and row.get("text")]


def _sszcw_cdp_tabs() -> List[Dict[str, Any]]:
    """Return CDP pages, starting the existing project browser fallback if needed."""
    endpoint = (
        os.environ.get("XIAOGU_SSZCW_CDP_URL")
        or os.environ.get("XIAOGU_SOCIAL_CDP_URL")
        or os.environ.get("XIAOGU_SCANNER_CDP_URL")
        or "http://127.0.0.1:9333"
    ).rstrip("/")
    try:
        import urllib.request

        request = urllib.request.Request(f"{endpoint}/json/list")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        try:
            from xiaogu_social_sentiment import _ensure_social_cdp_browser

            if _ensure_social_cdp_browser():
                return []
            import urllib.request

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(10):
                try:
                    with opener.open(f"{endpoint}/json/list", timeout=2) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    if isinstance(payload, list) and payload:
                        return payload
                except Exception:
                    pass
                time.sleep(0.3)
            return []
        except Exception:
            return []


def _sszcw_cdp_command(
    tab_id: str,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Send one bounded CDP command; never wait indefinitely on a page event."""
    import websocket

    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    tabs = _sszcw_cdp_tabs()
    tab = next((item for item in tabs if str(item.get("id")) == str(tab_id)), None)
    ws_url = str((tab or {}).get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return {}
    ws = websocket.create_connection(ws_url, timeout=timeout)
    ws.settimeout(timeout)
    request_id = int(time.time() * 1000000) % 2000000000
    try:
        ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = json.loads(ws.recv())
            except Exception:
                return {}
            if message.get("id") == request_id:
                return message
        return {}
    finally:
        ws.close()


def _sszcw_cdp_navigate(tab_id: str, url: str) -> bool:
    response = _sszcw_cdp_command(tab_id, "Page.navigate", {"url": url}, timeout=5.0)
    return bool(response.get("result", {}).get("frameId"))


def _sszcw_cdp_evaluate(tab_id: str, expression: str) -> Any:
    response = _sszcw_cdp_command(
        tab_id,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        timeout=5.0,
    )
    return (response.get("result", {}).get("result", {}) or {}).get("value")


def _fetch_cdp_posts_for_handle(
    asof: date,
    *,
    handle: str,
    limit: int = 40,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fetch recent rendered posts from X using the existing CloakChrome session."""
    if (os.environ.get("XIAOGU_SSZCW_SKIP_CDP") or "").strip() == "1":
        return [], ["cdp_disabled"]
    try:
        tabs = _sszcw_cdp_tabs()
        tab = next((item for item in tabs if item.get("type") == "page" and item.get("id")), None)
        if not tab:
            return [], ["cdp_no_page"]
        tab_id = str(tab["id"])
    except Exception as exc:
        return [], [f"cdp_setup:{type(exc).__name__}"]

    handle = _normalize_handle(handle)
    start = asof - timedelta(days=2)
    collected: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    pages = [f"https://x.com/{handle}"]
    if (os.environ.get("XIAOGU_SSZCW_FETCH_REPLIES") or "").strip() == "1":
        pages.append(f"https://x.com/{handle}/with_replies")
    for page_url in pages:
        try:
            if not _sszcw_cdp_navigate(tab_id, page_url):
                errors.append(f"cdp_navigate:{page_url.rsplit('/', 1)[-1]}")
                continue
            time.sleep(3.0)
            for _ in range(6):
                for row in _cdp_extract_articles(tab_id):
                    created_at = str(row.get("created_at") or "").strip()
                    day = _post_day(created_at)
                    if day is None or not (start <= day <= asof):
                        continue
                    text = str(row.get("text") or "").strip()
                    visible = str(row.get("visible_text") or "")
                    if not text:
                        continue
                    item = {
                        "id": str(row["id"]),
                        "created_at": created_at,
                        "text": text,
                        "source": "cdp_live",
                        "kind": "reply" if "Replying to" in visible else "post",
                        "author_handle": handle,
                    }
                    collected[item["id"]] = _merge_live_post_row(collected.get(item["id"]), item)
                if len(collected) >= limit:
                    break
                try:
                    _sszcw_cdp_evaluate(
                        tab_id,
                        "window.scrollBy(0, Math.max(window.innerHeight * 1.5, 900));"
                        "document.scrollingElement?.scrollBy(0, Math.max(window.innerHeight * 1.5, 900)); true",
                    )
                except Exception:
                    break
                time.sleep(0.6)
        except Exception as exc:
            errors.append(f"cdp_page:{type(exc).__name__}")

    rows = list(collected.values())
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")), reverse=True)
    rows = rows[: max(1, int(limit or 40))]

    # Browser metadata can be truncated by the rendered mirror. FxTwitter supplies
    # the full body for each discovered ID without requiring a second timeline source.
    for row in rows:
        try:
            import urllib.request
            from scrapy_scanner.spiders.sszcw_timeline import parse_fxtwitter_status

            url = f"https://api.fxtwitter.com/{handle}/status/{row['id']}"
            request = urllib.request.Request(url, headers={"User-Agent": "xiaogu-sszcw/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                fx = parse_fxtwitter_status(json.loads(response.read().decode("utf-8")))
            if fx and fx.get("text"):
                row["text"] = str(fx["text"])
                row["full_text"] = str(fx["text"])
                row["source"] = "cdp_live_fxtwitter"
                if fx.get("kind") == "reply":
                    row["kind"] = "reply"
                for key in ("in_reply_to_tweet_id", "referenced_tweet_id", "in_reply_to_screen_name"):
                    if fx.get(key):
                        row[key] = fx[key]
        except Exception as exc:
            errors.append(f"cdp_enrich:{row.get('id')}:{type(exc).__name__}")
    return rows, errors


def _x_api_get(url: str, bearer: str, timeout: int = 15) -> Dict[str, Any]:
    import urllib.request

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_timeline_row(
    row: Dict[str, Any],
    *,
    asof: date,
    source: str,
    parent_by_id: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict) or not row.get("text"):
        return None
    text = str(row.get("text") or "")
    ref_ids: List[str] = []
    is_reply = False
    for ref in row.get("referenced_tweets") or []:
        if not isinstance(ref, dict):
            continue
        rtype = str(ref.get("type") or "")
        rid = str(ref.get("id") or "")
        if rid:
            ref_ids.append(rid)
        if rtype == "replied_to":
            is_reply = True
    # X API may also set in_reply_to_user_id without referenced_tweets in some payloads
    if row.get("in_reply_to_user_id"):
        is_reply = True
    parent_id = ref_ids[0] if ref_ids else str(row.get("in_reply_to_tweet_id") or "")
    parent_text = None
    if parent_by_id and parent_id:
        parent_text = parent_by_id.get(parent_id)
    # Inbox / custom live URL may already carry parent
    if not parent_text:
        parent_text = row.get("parent_text") or row.get("question_text") or row.get("in_reply_to_text")
    out: Dict[str, Any] = {
        "id": str(row.get("id") or ""),
        "created_at": str(row.get("created_at") or row.get("date") or asof.isoformat()),
        "text": text,
        "source": source,
        "kind": "reply" if is_reply else str(row.get("kind") or "post"),
    }
    if parent_id:
        out["referenced_tweet_id"] = parent_id
        out["in_reply_to_tweet_id"] = parent_id
    if row.get("in_reply_to_user_id"):
        out["in_reply_to_user_id"] = str(row.get("in_reply_to_user_id"))
    if parent_text:
        out["parent_text"] = str(parent_text)
        out["question_text"] = str(parent_text)
    return out


def fetch_parent_tweets(tweet_ids: List[str], bearer: str) -> Dict[str, str]:
    """Batch-fetch parent tweet texts for reply Q&A context."""
    parent_by_id: Dict[str, str] = {}
    ids = [str(i).strip() for i in tweet_ids if str(i).strip()]
    # unique preserve order
    seen = set()
    uniq: List[str] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        uniq.append(i)
    # X API allows up to 100 ids per request
    for i in range(0, len(uniq), 50):
        batch = uniq[i : i + 50]
        if not batch:
            continue
        url = (
            "https://api.twitter.com/2/tweets"
            f"?ids={','.join(batch)}&tweet.fields=created_at,text,author_id"
        )
        try:
            payload = _x_api_get(url, bearer, timeout=15)
            for row in payload.get("data") or []:
                if isinstance(row, dict) and row.get("id") and row.get("text"):
                    parent_by_id[str(row["id"])] = str(row["text"])
        except Exception:
            continue
    return parent_by_id


def fetch_scrapy_posts(
    asof: date,
    limit: int = 40,
    screen_name: str = DEFAULT_SOCIAL_HANDLES[0],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Primary live path: Scrapy spider (jina + fxtwitter + syndication).

    Same pipeline historically used for soft sources when API tokens are absent.
    Returns posts with source=scrapy_* and kind post|reply.
    """
    screen = _normalize_handle(screen_name) or DEFAULT_SOCIAL_HANDLES[0]
    errors: List[str] = []
    posts: List[Dict[str, Any]] = []
    try:
        # Prefer package path; fall back to path insert for script-only runs.
        try:
            from scrapy_scanner.spiders.sszcw_timeline import (
                run_timeline_scrapy_fetch,
                _run_timeline_urllib_fallback,
            )
        except Exception:
            import sys

            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from scrapy_scanner.spiders.sszcw_timeline import (  # type: ignore
                run_timeline_scrapy_fetch,
                _run_timeline_urllib_fallback,
            )
        # CrawlerProcess can conflict with an already-running reactor; try urllib
        # fallback first (same parsers) for reliability in pipeline/agent sessions.
        prefer_crawler = (os.environ.get("XIAOGU_SOCIAL_SCRAPY_CRAWLER") or os.environ.get("XIAOGU_SSZCW_SCRAPY_CRAWLER") or "").strip() == "1"
        if prefer_crawler:
            raw = run_timeline_scrapy_fetch(screen_name=screen, limit=limit, include_replies=True, timeout_sec=55)
            if not raw:
                raw = _run_timeline_urllib_fallback(screen_name=screen, limit=limit, include_replies=True)
                if raw:
                    errors.append("scrapy_crawler_empty_used_urllib_fallback")
        else:
            raw = _run_timeline_urllib_fallback(screen_name=screen, limit=limit, include_replies=True)
            if not raw:
                raw = run_timeline_scrapy_fetch(screen_name=screen, limit=limit, include_replies=True, timeout_sec=55)
                if not raw:
                    errors.append("scrapy_empty")
        for row in raw or []:
            if not isinstance(row, dict) or not row.get("text"):
                continue
            created = str(row.get("created_at") or "").strip()
            if not created:
                # jina relative labels ("1h") — treat as asof so 5d window keeps them
                created = f"{asof.isoformat()}T12:00:00+08:00"
            kind = str(row.get("kind") or "post")
            if row.get("parent_text") or row.get("question_text") or row.get("in_reply_to_tweet_id"):
                kind = "reply"
            full_text = str(row.get("full_text") or row.get("text") or "").strip()
            # Prefer longer body if both present (fxtwitter full over jina stub).
            body = str(row.get("text") or "").strip()
            if full_text and len(full_text) >= len(body):
                body = full_text
            item: Dict[str, Any] = {
                "id": str(row.get("id") or ""),
                "created_at": created,
                "text": body,
                "full_text": body,
                "source": str(row.get("source") or "scrapy"),
                "kind": kind,
                "author_handle": screen,
            }
            for key in (
                "parent_text",
                "question_text",
                "in_reply_to_tweet_id",
                "referenced_tweet_id",
                "in_reply_to_screen_name",
            ):
                if row.get(key):
                    item[key] = row.get(key)
            posts.append(item)
    except Exception as exc:
        errors.append(f"scrapy:{type(exc).__name__}")
    return posts, errors


def _fetch_live_posts_for_handle(asof: date, handle: str, limit: int = 40) -> Tuple[List[Dict[str, Any]], List[str]]:
    handle = _normalize_handle(handle)
    if not handle:
        return [], ["handle_missing"]
    posts: List[Dict[str, Any]] = []
    errors: List[str] = []
    cdp_posts, cdp_errors = _fetch_cdp_posts_for_handle(asof, handle=handle, limit=limit)
    posts.extend(cdp_posts)
    errors.extend([f"{handle}:{err}" for err in cdp_errors])
    skip_scrapy = (os.environ.get("XIAOGU_SSZCW_SKIP_SCRAPY") or "").strip() == "1"
    if not skip_scrapy:
        scrapy_posts, scrapy_errors = fetch_scrapy_posts(asof, limit=limit, screen_name=handle)
        errors.extend([f"{handle}:{err}" for err in scrapy_errors])
        posts.extend(scrapy_posts)
    bearer = (
        os.environ.get("X_BEARER_TOKEN")
        or os.environ.get("TWITTER_BEARER_TOKEN")
        or os.environ.get("XIAOGU_X_BEARER_TOKEN")
        or ""
    ).strip()
    if bearer:
        try:
            user_url = f"https://api.twitter.com/2/users/by/username/{handle}?user.fields=id"
            user_payload = _x_api_get(user_url, bearer, timeout=12)
            user_id = str((user_payload.get("data") or {}).get("id") or "")
            if user_id:
                max_n = min(100, max(10, limit))
                tl_url = (
                    f"https://api.twitter.com/2/users/{user_id}/tweets"
                    f"?max_results={max_n}"
                    f"&exclude=retweets"
                    f"&tweet.fields=created_at,text,referenced_tweets,in_reply_to_user_id,conversation_id"
                )
                tl = _x_api_get(tl_url, bearer, timeout=18)
                raw_rows = [r for r in (tl.get("data") or []) if isinstance(r, dict)]
                parent_ids: List[str] = []
                for row in raw_rows:
                    for ref in row.get("referenced_tweets") or []:
                        if isinstance(ref, dict) and str(ref.get("type") or "") == "replied_to":
                            rid = str(ref.get("id") or "")
                            if rid:
                                parent_ids.append(rid)
                parent_by_id: Dict[str, str] = {}
                if parent_ids:
                    try:
                        parent_by_id = fetch_parent_tweets(parent_ids, bearer)
                    except Exception as exc:
                        errors.append(f"{handle}:x_api_parents:{type(exc).__name__}")
                for row in raw_rows:
                    norm = _normalize_timeline_row(row, asof=asof, source=f"x_api:{handle}", parent_by_id=parent_by_id)
                    if norm:
                        norm["author_handle"] = handle
                        posts.append(norm)
            else:
                errors.append(f"{handle}:x_api:user_id_missing")
        except Exception as exc:
            errors.append(f"{handle}:x_api:{type(exc).__name__}")
    return posts, errors


def fetch_live_posts(
    asof: date,
    limit: int = 40,
    handles: Any = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Best-effort live fetch for verified X handles. Never raises into the pick chain.

    Order:
    0) Scrapy (jina profile + fxtwitter status enrich + syndication fallback)
    1) X API timeline for each verified handle when token is configured
    2) Optional XIAOGU_SSZCW_LIVE_URL JSON endpoint (may include handle fields)

    Returns (posts tagged source=live|x_api|scrapy_*, soft error codes).
    """
    _load_project_env()
    posts: List[Dict[str, Any]] = []
    errors: List[str] = []

    handles_list = _normalize_handles(handles)
    for handle in handles_list:
        handle_posts, handle_errors = _fetch_live_posts_for_handle(asof, handle, limit=min(limit, DEFAULT_HANDLE_LIMIT))
        posts.extend(handle_posts)
        errors.extend(handle_errors)

    live_url = (os.environ.get("XIAOGU_SSZCW_LIVE_URL") or "").strip()
    if live_url and len(posts) < 3:
        try:
            import urllib.request

            req = urllib.request.Request(live_url, headers={"User-Agent": "xiaogu-sszcw/1.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("posts") or payload.get("data") or []
            for row in rows:
                if not isinstance(row, dict) or not row.get("text"):
                    continue
                norm = _normalize_timeline_row(row, asof=asof, source="live")
                if norm:
                    handle = _row_handle(norm)
                    if handle:
                        norm["author_handle"] = handle
                    posts.append(norm)
        except Exception as exc:
            errors.append(f"live_url:{type(exc).__name__}")
    # Tag untagged as live when from fetch path.
    for row in posts:
        if not row.get("source"):
            row["source"] = "live"
        if not row.get("kind"):
            row["kind"] = "reply" if _is_reply_row(row) else "post"
        if not row.get("author_handle"):
            row["author_handle"] = handles_list[0] if handles_list else DEFAULT_SOCIAL_HANDLES[0]
    return posts, errors


def build_context(
    asof: date,
    days: int = 5,
    seed: bool = True,
    prefer_live: bool = True,
    handles: Any = None,
) -> Dict[str, Any]:
    """Build soft context.

    Priority: live fetch + live_inbox + non-seed cache. Seed only when window empty.
    Seed rows are never written into durable dated cache (prevents pollution).
    """
    _load_project_env()
    # One-shot hygiene for pre-fix polluted dated caches.
    try:
        purge_seed_from_durable_cache()
    except Exception:
        pass
    live_posts: List[Dict[str, Any]] = []
    fetch_errors: List[str] = []
    if prefer_live:
        if handles is None:
            live_posts, fetch_errors = fetch_live_posts(asof)
        else:
            live_posts, fetch_errors = fetch_live_posts(asof, handles=handles)
    inbox_posts = load_live_inbox()
    cached = load_cached_posts()
    requested_handles = set(_normalize_handles(handles)) if handles is not None else None
    if requested_handles is not None:
        live_posts = [
            row for row in live_posts
            if _row_handle(row) in requested_handles
        ]
        inbox_posts = [
            row for row in inbox_posts
            if _row_handle(row) in requested_handles
        ]
        cached = [
            row for row in cached
            if _row_handle(row) in requested_handles
        ]
    non_seed = _dedupe_posts(
        [
            row
            for row in (list(live_posts) + list(inbox_posts) + list(cached))
            if isinstance(row, dict) and row.get("text") and _post_source_kind(row) != "seed"
        ]
    )
    windowed = posts_in_window(non_seed, asof, days=days)
    used_seed = False
    if not windowed and seed:
        windowed = posts_in_window(seed_builtin_posts(), asof, days=days)
        used_seed = True
    # Persist only non-seed durable posts; seed gets a separate snapshot for audit.
    durable = [r for r in (list(live_posts) + list(inbox_posts) + list(cached)) if _post_source_kind(r) != "seed"]
    if durable:
        save_posts(durable, asof, seed_only=False)
    elif used_seed:
        save_posts(windowed, asof, seed_only=True)
    payload = analyze_posts(
        windowed,
        asof,
        window_days=days,
        source_label=",".join(f"@{handle}" for handle in _normalize_handles(handles))
        if handles is not None
        else "@sszcw,@naiyin04,@andredavid90",
    )
    payload["live_fetch_attempted"] = bool(prefer_live)
    payload["live_fetch_count"] = len(live_posts)
    payload["live_inbox_count"] = len(inbox_posts)
    payload["live_token_configured"] = live_token_configured()
    payload["live_fetch_errors"] = list(fetch_errors)
    payload["used_seed_fallback"] = used_seed
    payload["handles_requested"] = _normalize_handles(handles) if handles is not None else list(DEFAULT_SOCIAL_HANDLES)
    if not live_posts and prefer_live and not live_token_configured():
        payload.setdefault("live_fetch_errors", []).append("no_live_token_or_url")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="Build @sszcw 5-day market context for pre-pick soft guidance")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--seed", action="store_true", default=True, help="Seed builtin posts only when live/cache empty")
    ap.add_argument("--no-seed", action="store_true")
    ap.add_argument("--prefer-live", action="store_true", default=True, help="Attempt live X/@sszcw fetch before seed/cache")
    ap.add_argument("--no-live", action="store_true", help="Skip live fetch (cache/seed only)")
    ap.add_argument(
        "--handles",
        default=",".join(DEFAULT_SOCIAL_HANDLES),
        help="Comma-separated verified X handles to analyze",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    asof = _parse_date(args.date)
    handles = _normalize_handles(args.handles)
    payload = build_context(
        asof,
        days=args.days,
        seed=not args.no_seed,
        prefer_live=bool(args.prefer_live and not args.no_live),
        handles=handles,
    )
    paths = write_outputs(payload, asof)
    payload["output_paths"] = paths
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"asof={payload['asof']} posts={payload['post_count']} "
        f"orig={payload.get('original_post_count')} replies={payload.get('reply_post_count')} "
        f"qa={payload.get('qa_count')} stance={payload['market_stance']}"
    )
    print(f"favored={payload['favored_sectors']} risk={payload['risk_sectors']}")
    stock_soft = payload.get("stock_soft_from_replies") or {}
    if stock_soft.get("soft_bullish_stocks") or stock_soft.get("soft_bearish_stocks"):
        print(
            f"reply_stocks bullish={stock_soft.get('soft_bullish_stocks')} "
            f"bearish={stock_soft.get('soft_bearish_stocks')}"
        )
    print(
        f"stage={payload['mainline_stage_hint']} index={payload['index_regime_hint']} "
        f"conf={payload['confidence']} src={payload.get('soft_context_source')} "
        f"valid={payload.get('soft_context_valid')} live={payload.get('live_post_count')} "
        f"token={payload.get('live_token_configured')} seed_fb={payload.get('used_seed_fallback')}"
    )
    print(f"wrote {paths['dated']}")


if __name__ == "__main__":
    main()
