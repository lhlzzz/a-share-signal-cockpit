"""Unit tests: sszcw reply Q&A extraction (market/stock answers)."""
from __future__ import annotations

from datetime import date

import scripts.xiaogu_sszcw_market_context as sszcw


def test_extract_stock_mentions_codes_and_names():
    text = "通富微电002156还能不能追？半导体透支了"
    hits = sszcw.extract_stock_mentions(text)
    assert "002156" in hits
    assert "通富微电" in hits


def test_sszcw_explicit_stock_post_is_trusted_direct_confirmation():
    payload = sszcw.analyze_posts(
        [
            {
                "id": "trusted-1",
                "created_at": "2026-07-30T10:00:00+08:00",
                "post_date": "2026-07-30",
                "text": "002156继续看多，趋势向上",
                "kind": "post",
                "source": "scrapy_fxtwitter",
                "author_handle": "sszcw",
            },
            {
                "id": "other-1",
                "created_at": "2026-07-30T10:05:00+08:00",
                "post_date": "2026-07-30",
                "text": "002156不追高",
                "kind": "post",
                "source": "scrapy_fxtwitter",
                "author_handle": "other",
            },
        ],
        date(2026, 7, 30),
    )
    trusted = payload["trusted_stock_predictions"]
    assert trusted["bullish_stocks"] == ["002156"]
    assert trusted["bearish_stocks"] == []
    assert trusted["trusted_handles"] == ["sszcw"]


def test_extract_qa_pairs_from_replies():
    posts = [
        {
            "id": "1",
            "created_at": "2026-07-22T10:00:00+08:00",
            "post_date": "2026-07-22",
            "text": "趋势是向下的，不追，科技股反弹两三天的事。",
            "kind": "reply",
            "parent_text": "半导体还能买吗？通富微电怎么看",
            "source": "inbox",
        },
        {
            "id": "2",
            "created_at": "2026-07-22T11:00:00+08:00",
            "post_date": "2026-07-22",
            "text": "铜还会涨，像石油黄金有色这些与期货强关联。",
            "kind": "reply",
            "parent_text": "大盘还能不能上？有色怎么看",
            "source": "inbox",
        },
        {
            "id": "3",
            "created_at": "2026-07-22T12:00:00+08:00",
            "post_date": "2026-07-22",
            "text": "早上好",  # noise reply
            "kind": "reply",
            "parent_text": "早",
            "source": "inbox",
        },
    ]
    cards = sszcw.extract_qa_pairs(posts)
    assert any(c.get("qa_type") == "stock" for c in cards)
    assert any(c.get("qa_type") == "market" for c in cards)
    stock = next(c for c in cards if c.get("qa_type") == "stock")
    assert "通富微电" in (stock.get("stocks") or [])
    assert stock.get("tone") == "BEARISH"
    soft = sszcw.favored_stock_mentions_from_qa(cards)
    assert "通富微电" in soft["soft_bearish_stocks"]


def test_analyze_posts_counts_replies_and_qa():
    posts = [
        {
            "id": "p1",
            "created_at": "2026-07-22T09:00:00+08:00",
            "post_date": "2026-07-22",
            "text": "观望为主，大盘还得往下。",
            "kind": "post",
            "source": "inbox",
        },
        {
            "id": "r1",
            "created_at": "2026-07-22T10:00:00+08:00",
            "post_date": "2026-07-22",
            "text": "铜还会涨。",
            "kind": "reply",
            "parent_text": "有色还能拿吗",
            "source": "inbox",
        },
    ]
    out = sszcw.analyze_posts(posts, date(2026, 7, 22))
    assert out["post_count"] == 2
    assert out["reply_post_count"] == 1
    assert out["original_post_count"] == 1
    assert out["qa_count"] >= 1
    assert "有色" in out["favored_sectors"] or out["theme_counts"].get("有色")
    assert out["usage"]["reply_qa"] is True
    assert out["usage"]["hard_gate"] is False


def test_normalize_timeline_row_marks_reply_and_parent():
    row = {
        "id": "99",
        "created_at": "2026-07-22T10:00:00.000Z",
        "text": "@user 观望为主",
        "referenced_tweets": [{"type": "replied_to", "id": "88"}],
        "in_reply_to_user_id": "123",
    }
    norm = sszcw._normalize_timeline_row(
        row,
        asof=date(2026, 7, 22),
        source="x_api",
        parent_by_id={"88": "大盘怎么看？还能不能上车"},
    )
    assert norm is not None
    assert norm["kind"] == "reply"
    assert norm["parent_text"] == "大盘怎么看？还能不能上车"
    assert norm["referenced_tweet_id"] == "88"


def test_parse_jina_markdown_extracts_posts():
    md = """
[@sszcw](https://x.com/sszcw)  [1h](https://x.com/sszcw/status/2081012362459697472)   在牛市行情中，任何的消息面都能起作用，相反，在熊市当中，消息面一点卵用都没有。   5  16 [](https://x.com/sszcw/status/2081012362459697472/quotes)[3.7K]
[@sszcw](https://x.com/sszcw)  [2h](https://twitter.com/sszcw/status/2080968264184963092)   下周一还得跌，到我说的那个点位。
"""
    posts = sszcw.parse_jina_markdown(md)
    assert len(posts) >= 2
    assert posts[0]["id"] == "2081012362459697472"
    assert "牛市" in posts[0]["text"]
    assert posts[0]["source"] == "scrapy_jina"
    assert posts[1]["id"] == "2080968264184963092"


def test_parse_twitter_created_at_rfc2822():
    iso = sszcw._parse_twitter_created_at("Sat Jul 25 10:47:51 +0000 2026")
    assert iso.startswith("2026-07-25")
    assert "T" in iso


def test_parse_fxtwitter_status_reply_flag():
    payload = {
        "tweet": {
            "id": "1",
            "text": "@foo 观望为主，大盘还得往下",
            "created_at": "Sat Jul 25 10:47:51 +0000 2026",
            "replying_to": "foo",
            "replying_to_status": "99",
        }
    }
    row = sszcw.parse_fxtwitter_status(payload)
    assert row is not None
    assert row["kind"] == "reply"
    assert row["in_reply_to_tweet_id"] == "99"
    assert "观望" in row["text"]


def test_merge_prefers_longer_full_text():
    short = {"id": "1", "text": "下周一还得跌", "source": "scrapy_jina", "kind": "post"}
    long = {
        "id": "1",
        "text": "下周一还得跌，到我说的那个点位。",
        "source": "scrapy_fxtwitter",
        "kind": "post",
        "created_at": "2026-07-25T18:47:51+08:00",
    }
    merged = sszcw._merge_post_row(short, long)
    assert merged["text"] == long["text"]
    assert merged["source"] == "scrapy_fxtwitter"

    # raw_text dict form from fxtwitter
    payload = {
        "tweet": {
            "id": "2",
            "text": "short",
            "raw_text": {"text": "full body that is longer than short", "display_text_range": [0, 30]},
            "created_at": "Sat Jul 25 10:47:51 +0000 2026",
        }
    }
    fx = sszcw.parse_fxtwitter_status(payload)
    assert fx is not None
    assert "full body" in fx["text"]
    assert fx.get("full_text") == fx["text"]


def test_analyze_posts_keeps_full_excerpt_text():
    long_text = "观望为主，大盘还得往下。" * 20  # >180 chars
    posts = [
        {
            "id": "p1",
            "created_at": "2026-07-22T09:00:00+08:00",
            "post_date": "2026-07-22",
            "text": long_text,
            "kind": "post",
            "source": "scrapy_fxtwitter",
        }
    ]
    out = sszcw.analyze_posts(posts, date(2026, 7, 22))
    assert out["excerpts"]
    assert out["excerpts"][0]["text"] == long_text
    assert out["excerpts"][0]["text_len"] == len(long_text)
