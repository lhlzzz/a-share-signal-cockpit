#!/usr/bin/env python3
"""Shadow profit-opportunity candidates (主力/游资资金视角).

Diagnostic only:
- does NOT write picks ledger
- does NOT change official PAPER_PICK gates / formal sort key
- ranks tradable mainboard names by mainline fund flow + stock main-force inflow

Output:
  summary/profit_candidates_YYYY-MM-DD.json
  summary/profit_candidates_replay.json (when multi-day)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SUMMARY = ROOT / "summary"
LIVE_SCAN = ROOT / "data" / "live_scan"

# Tradable universe aligned with project candidate rules (paper path).
MAINBOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
PRICE_CAP = 70.0
# Limitup watch may exceed paper price cap (e.g. 通富微电 ~76) — shadow observation only.
LIMITUP_PRICE_CAP = 150.0
PCT_MIN = 0.5
PCT_MAX = 9.5  # exclude sealed +10% chase by default for non-limitup seats
TOP_SECTORS = 8
TOP_CANDIDATES = 5
RANK_WEIGHTS_PATH = SUMMARY / "profit_rank_weights_latest.json"

# Prefer mid-stage follow over sealed chase / underwater noise.
STAGE_WEIGHT = {
    "flat_0_to_3": 0.85,
    "early_3_to_5": 1.0,
    "mid_5_to_7": 0.95,
    "high_7_to_9": 0.55,
    "near_limit_9_plus": 0.25,
    "underwater": 0.35,
}

# Default profit_score feature weights (sum soft components ≈ 1.0 before *100).
# Return-supervised fit may overwrite via profit_rank_weights_latest.json.
DEFAULT_RANK_WEIGHTS: Dict[str, float] = {
    "inflow_norm": 0.35,
    "mainline_score": 0.25,
    "industry_boost": 0.20,
    "stage_w": 0.12,
    "early_theme": 0.08,
    "limitup_boost": 0.15,  # sealed + high fund from limitup pool
    "theme_cluster_boost": 0.10,  # multiple same-hybk limitups (e.g. 兵装)
}

# Generic noise boards (still allowed as context, not primary mainline).
NOISE_SECTOR_TOKENS = (
    "历史新高",
    "百日新高",
    "昨日连板",
    "昨日涨停",
    "最近多板",
    "昨日高振幅",
    "东方财富热股",
    "融资融券",
    "深股通",
    "沪股通",
    "标准普尔",
    "富时罗素",
    "HS300",
    "上证180",
    "中证500",
    "创业板综",
    "深圳特区",
    "广东自贸",
    "证金持股",
    "机构重仓",
    "茅指数",
    "权重股",
    "大盘股",
    "中盘股",
)


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _code(row: Dict[str, Any]) -> str:
    raw = row.get("code") or row.get("symbol") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(6) if digits else ""


def is_mainboard(code: str) -> bool:
    return bool(code) and code.startswith(MAINBOARD_PREFIXES)


def next_weekday(day: date) -> date:
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def resolve_scan_dir(trade_date: str) -> Optional[Path]:
    """Prefer afternoon / auxfull / full pool over bare eastmoney_scan."""
    base = LIVE_SCAN / trade_date
    if not base.is_dir():
        return None
    preferred = (
        "eastmoney_scan_afternoon",
        "eastmoney_scan_auxfull",
        "eastmoney_scan_full400",
        "eastmoney_scan_pool400",
        "eastmoney_scan",
        "eastmoney_web_tabs_scan_v0_1",
    )
    for name in preferred:
        path = base / name
        scored = path / "eastmoney_web_tabs_scored.jsonl"
        if scored.exists() and scored.stat().st_size > 0:
            return path
    # Fallback: any child with scored jsonl
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        scored = path / "eastmoney_web_tabs_scored.jsonl"
        if scored.exists() and scored.stat().st_size > 0:
            return path
    return None


def scan_content_fingerprint(scan_dir: Path) -> str:
    """Fingerprint for stale-copy detection.

    Primary signal: full industry+concept fund-flow files (identical when a day
    reuses yesterday's scan). Secondary: top-20 scored codes by main-force inflow
    (stable even if jsonl row order differs slightly).
    """
    h = hashlib.sha1()
    flow_bytes = 0
    for name in ("flow_industry.jsonl", "flow_concept.jsonl"):
        path = scan_dir / name
        if path.exists() and path.stat().st_size > 0:
            raw = path.read_bytes()
            flow_bytes += len(raw)
            h.update(name.encode("utf-8"))
            h.update(raw)
    scored = scan_dir / "eastmoney_web_tabs_scored.jsonl"
    if scored.exists() and scored.stat().st_size > 0:
        # Rank-stable sample: top codes by main-force net inflow among first 400 rows
        sample: List[Tuple[float, str]] = []
        with scored.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= 400:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                code = _code(row)
                net = _num(row.get("net_inflow_main")) or 0.0
                if code:
                    sample.append((net, code))
        sample.sort(reverse=True)
        top_codes = [c for _, c in sample[:20]]
        h.update(b"top_codes")
        h.update(",".join(top_codes).encode("utf-8"))
        # If no flow files, fall back to scored size so empty dirs don't collide.
        if flow_bytes == 0:
            h.update(f"scored_size:{scored.stat().st_size}".encode("utf-8"))
    return h.hexdigest()[:20]


def find_stale_scan_source(
    trade_date: str,
    fingerprint: str,
    *,
    lookback_calendar_days: int = 14,
    known: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return earlier trade_date if this scan fingerprint already appeared.

    known: optional {fingerprint: trade_date} from current replay pass (preferred).
    Also walks previous calendar days under data/live_scan/.
    """
    if known and fingerprint in known:
        prior = known[fingerprint]
        if prior != trade_date:
            return prior
    day = date.fromisoformat(trade_date)
    for i in range(1, lookback_calendar_days + 1):
        prev = day - timedelta(days=i)
        if prev.weekday() >= 5:
            continue
        prev_s = prev.isoformat()
        scan = resolve_scan_dir(prev_s)
        if scan is None:
            continue
        if scan_content_fingerprint(scan) == fingerprint:
            return prev_s
    return None


def _candidate_ret(c: Dict[str, Any]) -> Optional[float]:
    if "ret_t1_close" in c and c.get("ret_t1_close") is not None:
        try:
            return float(c["ret_t1_close"])
        except (TypeError, ValueError):
            pass
    t1 = c.get("t1") or {}
    if isinstance(t1, dict) and t1.get("ret_t1_close") is not None:
        try:
            return float(t1["ret_t1_close"])
        except (TypeError, ValueError):
            return None
    return None


def compute_basket_stats(candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Equal-weight basket strategies vs Top1 (diagnostic, observation only).

    Strategies (only ranks with valid T+1):
    - top1: rank-1 only
    - top2_eq / top3_eq / basket_eq: equal-weight mean of first 2 / 3 / all
    - best_in_basket: max among all with returns (oracle upper bound)
    """
    ordered = list(candidates or [])
    rets_by_rank: List[Optional[float]] = [_candidate_ret(c) for c in ordered]

    def pack(vals: List[float], label: str) -> Dict[str, Any]:
        if not vals:
            return {
                "strategy": label,
                "n": 0,
                "ret": None,
                "win": None,
                "note": "no_valid_t1",
            }
        avg = sum(vals) / len(vals)
        return {
            "strategy": label,
            "n": len(vals),
            "ret": round(avg, 6),
            "win": avg > 0,
            "constituent_rets": [round(v, 6) for v in vals],
        }

    top1_vals = [rets_by_rank[0]] if rets_by_rank and rets_by_rank[0] is not None else []
    top2_vals = [r for r in rets_by_rank[:2] if r is not None]
    top3_vals = [r for r in rets_by_rank[:3] if r is not None]
    all_vals = [r for r in rets_by_rank if r is not None]
    best = max(all_vals) if all_vals else None

    return {
        "top1": pack(top1_vals, "top1"),
        "top2_eq": pack(top2_vals, "top2_eq"),
        "top3_eq": pack(top3_vals, "top3_eq"),
        "basket_eq": pack(all_vals, "basket_eq"),
        "best_in_basket": {
            "strategy": "best_in_basket",
            "n": 1 if best is not None else 0,
            "ret": round(best, 6) if best is not None else None,
            "win": (best > 0) if best is not None else None,
            "note": "oracle upper bound; not a tradeable rule",
        },
        "valid_t1_count": len(all_vals),
        "missing_t1_count": sum(1 for r in rets_by_rank if r is None),
    }


def aggregate_strategy_stats(day_stats: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Cross-day WR / avg for each basket strategy (valid days only)."""
    keys = ("top1", "top2_eq", "top3_eq", "basket_eq", "best_in_basket")
    out: Dict[str, Any] = {}
    for key in keys:
        rets: List[float] = []
        for ds in day_stats:
            block = (ds or {}).get(key) or {}
            ret = block.get("ret")
            if ret is not None:
                rets.append(float(ret))
        if not rets:
            out[key] = {"n_days": 0, "win_rate": None, "avg_ret": None}
            continue
        wins = sum(1 for r in rets if r > 0)
        out[key] = {
            "n_days": len(rets),
            "win_rate": round(wins / len(rets), 4),
            "avg_ret": round(sum(rets) / len(rets), 6),
        }
    return out


def load_jsonl(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if limit is not None and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def load_sector_flows(scan_dir: Path, top_n: int = TOP_SECTORS) -> Dict[str, Any]:
    industry = load_jsonl(scan_dir / "flow_industry.jsonl")
    concept = load_jsonl(scan_dir / "flow_concept.jsonl")

    def rank_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ranked = sorted(rows, key=lambda r: _num(r.get("f62")) or 0.0, reverse=True)
        out: List[Dict[str, Any]] = []
        for r in ranked:
            name = str(r.get("f14") or "").strip()
            if not name:
                continue
            if any(tok in name for tok in NOISE_SECTOR_TOKENS):
                continue
            net = _num(r.get("f62")) or 0.0
            if net <= 0:
                continue
            out.append(
                {
                    "name": name,
                    "code": r.get("f12"),
                    "net_inflow": net,
                    "net_inflow_yi": round(net / 1e8, 4),
                    "pct_chg": _num(r.get("f3")),
                    "super_net": _num(r.get("f66")),
                    "big_net": _num(r.get("f72")),
                }
            )
            if len(out) >= top_n:
                break
        return out

    industry_top = rank_rows(industry)
    concept_top = rank_rows(concept)
    # Mainline tags = industry first, then concept (dedupe)
    tags: List[str] = []
    for block in (industry_top, concept_top):
        for item in block:
            name = item["name"]
            if name not in tags:
                tags.append(name)
    return {
        "industry_top": industry_top,
        "concept_top": concept_top,
        "mainline_tags": tags[: max(top_n * 2, top_n)],
    }


def tradable_filter(row: Dict[str, Any]) -> Tuple[bool, str]:
    code = _code(row)
    if not is_mainboard(code):
        return False, "not_mainboard"
    price = _num(row.get("price"))
    if price is None or price <= 0:
        return False, "missing_price"
    if price > PRICE_CAP:
        return False, "price_over_cap"
    pct = _num(row.get("signal_pct"))
    if pct is None:
        pct = _num(row.get("pct_chg"))
    if pct is None:
        return False, "missing_pct"
    if pct < PCT_MIN:
        return False, "pct_below_min"
    if pct > PCT_MAX:
        return False, "pct_above_max_chase"
    net = _num(row.get("net_inflow_main"))
    if net is None:
        capital = row.get("data_directory_capital_flow")
        if isinstance(capital, dict):
            net = _num(capital.get("main_force_net_inflow")) or _num(capital.get("main_net_inflow"))
    if net is None or net <= 0:
        return False, "main_force_not_inflow"
    return True, "ok"


def _text_blob(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "name",
        "sector",
        "industry",
        "sector_name",
        "theme",
        "predicted_sector",
    ):
        val = row.get(key)
        if val:
            parts.append(str(val))
    for key in ("sector_opportunity_tags", "theme_tags", "sector_tags"):
        val = row.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif val:
            parts.append(str(val))
    return " ".join(parts)


# Tight family synonyms (len>=2, prefer multi-char). Single-char metal tokens are NOT used
# for tag-blob matching (avoids 铜缆→有色 false hits like 立讯精密).
MAINLINE_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "半导": (
        "半导", "芯片", "存储芯片", "存储", "中芯", "封测", "集成电", "光刻", "晶圆",
        "氮化镓", "碳化硅", "先进封装", "高带宽", "射频",
    ),
    "电池": ("电池", "锂电", "固态电池", "电解液"),
    "光伏": ("光伏", "硅料", "硅片"),
    "军工": ("军工", "兵器", "航天", "航空", "红外", "雷达"),
    "有色": ("有色", "黄金", "小金属", "稀土", "能源金属"),
    "电力": ("电力", "水电", "火电", "电网", "特高压", "绿电"),
    "算力": ("算力", "云计算", "数据中心", "边缘计算"),
    "通信": ("通信", "5G概念", "6G概念", "基站"),
}

# Single-char / ambiguous tokens only allowed on industry/sector/name core fields.
CORE_ONLY_TOKENS: Dict[str, Tuple[str, ...]] = {
    "有色": ("铜", "铝", "钼", "锂", "钴"),
    "半导": ("HBM",),
}


def _core_blob(row: Dict[str, Any]) -> str:
    parts = []
    for key in ("name", "industry", "sector", "sector_name"):
        val = row.get(key)
        if val:
            parts.append(str(val))
    return " ".join(parts)


def _tag_blob(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("sector_opportunity_tags", "theme_tags", "sector_tags"):
        val = row.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif val:
            parts.append(str(val))
    return " ".join(parts)


def mainline_match_score(row: Dict[str, Any], mainline_tags: Sequence[str]) -> Tuple[float, List[str]]:
    """Strict mainline hit: direct name/industry match, or multi-char family synonym.

    Family matches require either:
    - multi-char synonym in industry/sector/name, or
    - multi-char synonym in tags AND family key also present in mainline tag.
    Single-char metals only match core industry/sector/name (not concept tags).
    """
    core = _core_blob(row)
    tags = _tag_blob(row)
    full = (core + " " + tags).strip()
    if not full or not mainline_tags:
        return 0.0, []

    hits: List[str] = []
    for tag in mainline_tags:
        if not tag:
            continue
        # 1) Direct: full mainline tag appears in core or tags
        if tag in full:
            hits.append(tag)
            continue
        # 2) Core industry/sector/name substring either way (len>=2)
        matched = False
        for token in (row.get("industry"), row.get("sector"), row.get("sector_name"), row.get("name")):
            tok = str(token or "").strip()
            if tok and len(tok) >= 2 and (tok in tag or tag in tok):
                hits.append(tag)
                matched = True
                break
        if matched:
            continue
        # 3) Family synonym:
        # - If family key is in mainline tag (e.g. 半导 in 半导体), allow full synonym bag
        #   so 氮化镓 can link to 半导体.
        # - Else only stems that literally appear in this tag (封测 in 集成电路封测),
        #   blocking 国产芯片 → 集成电路封测 cross-wiring.
        for family_key, synonyms in MAINLINE_FAMILIES.items():
            multi = [s for s in synonyms if len(s) >= 2]
            if family_key in tag:
                active = list(dict.fromkeys([family_key] + multi))
            else:
                active = [s for s in multi if s in tag]
                if not active:
                    continue
            if any(s in core for s in active):
                hits.append(tag)
                matched = True
                break
            if any(s in tags for s in active):
                hits.append(tag)
                matched = True
                break
            if family_key in tag:
                for s in CORE_ONLY_TOKENS.get(family_key, ()):
                    if s and s in core:
                        hits.append(tag)
                        matched = True
                        break
            if matched:
                break

    seen = set()
    uniq = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    if not uniq:
        return 0.0, []
    score = min(1.0, 0.55 + 0.15 * len(uniq))
    return score, uniq


def stage_weight(row: Dict[str, Any]) -> float:
    stage = str(row.get("candidate_stage") or "")
    if stage in STAGE_WEIGHT:
        return STAGE_WEIGHT[stage]
    pct = _num(row.get("signal_pct")) or 0.0
    if pct >= 9.0:
        return 0.25
    if pct >= 7.0:
        return 0.55
    if pct >= 5.0:
        return 0.95
    if pct >= 3.0:
        return 1.0
    return 0.85


def load_rank_weights(path: Optional[Path] = None) -> Dict[str, float]:
    """Load return-supervised weights if present; else defaults."""
    weights = dict(DEFAULT_RANK_WEIGHTS)
    p = path or RANK_WEIGHTS_PATH
    if p.exists():
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            raw = payload.get("weights") if isinstance(payload, dict) else None
            if isinstance(raw, dict):
                for k, v in raw.items():
                    n = _num(v)
                    if n is not None and k in weights:
                        weights[k] = float(n)
        except Exception:
            pass
    return weights


def load_limitup_pool(scan_dir: Path) -> List[Dict[str, Any]]:
    """Load sealed limitup pool as pseudo-scored rows (shadow observation).

    Covers names missing from scored jsonl (e.g. 通富微电 002156 on 2026-07-24).
    """
    path = scan_dir / "limitup_pool.jsonl"
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw in load_jsonl(path):
        code = str(raw.get("c") or raw.get("code") or raw.get("symbol") or "").zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        name = str(raw.get("n") or raw.get("name") or "")
        # Eastmoney limitup pool: p is usually 厘 (price * 1000), e.g. 76800 → 76.80.
        # Fall back to fen (/100) only when value is small; never leave raw >100 as yuan.
        price_raw = _num(raw.get("p") or raw.get("price"))
        price: Optional[float] = None
        if price_raw is not None and price_raw > 0:
            if price_raw >= 1000:
                price = price_raw / 1000.0  # 厘
            elif price_raw > 100:
                price = price_raw / 100.0  # 分 (rare)
            else:
                price = price_raw  # already yuan
        pct = _num(raw.get("zdp") or raw.get("pct_chg") or raw.get("signal_pct"))
        fund = _num(raw.get("fund"))  # sealed amount proxy (not always main-force net)
        amount = _num(raw.get("amount"))
        hybk = str(raw.get("hybk") or raw.get("industry") or "")
        reason = str(raw.get("limitup_reason") or hybk or "")
        # Prefer fund as capital intensity; fall back to amount * small fraction
        net = fund if fund and fund > 0 else ((amount or 0.0) * 0.05)
        tags = [t for t in (hybk, reason, "涨停", "limitup_pool") if t]
        rows.append(
            {
                "code": code,
                "symbol": code,
                "name": name,
                "price": price,
                "signal_pct": pct if pct is not None else 9.99,
                "pct_chg": pct if pct is not None else 9.99,
                "net_inflow_main": net,
                "industry": hybk or None,
                "sector": hybk or None,
                "sector_opportunity_tags": tags,
                "candidate_stage": "near_limit_9_plus",
                "from_limitup_pool": True,
                "limitup_fund": fund,
                "limitup_amount": amount,
                "limitup_reason": reason,
                "limitup_boards": raw.get("lbc") or raw.get("zttj"),
                "board": "main" if is_mainboard(code) else "other",
            }
        )
    return rows


def merge_scored_with_limitup(
    scored: List[Dict[str, Any]],
    limitups: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Merge limitup-only names into scored universe; mark sealed rows already present."""
    by_code: Dict[str, Dict[str, Any]] = {}
    for row in scored:
        code = _code(row)
        if code:
            by_code[code] = dict(row)
    added = 0
    annotated = 0
    for lu in limitups:
        code = _code(lu)
        if not code:
            continue
        if code in by_code:
            base = by_code[code]
            base["from_limitup_pool"] = True
            base["limitup_fund"] = lu.get("limitup_fund")
            base["limitup_amount"] = lu.get("limitup_amount")
            base["limitup_reason"] = lu.get("limitup_reason")
            if not base.get("industry") and lu.get("industry"):
                base["industry"] = lu.get("industry")
            # Ensure sealed pct visible
            if (_num(base.get("signal_pct")) or 0) < 9.5:
                base["signal_pct"] = lu.get("signal_pct") or 9.99
            annotated += 1
        else:
            by_code[code] = dict(lu)
            added += 1
    # Theme cluster size by industry among limitups (兵装 / 半导体 multi-seal)
    hy_counts: Dict[str, int] = {}
    for lu in limitups:
        hy = str(lu.get("industry") or lu.get("limitup_reason") or "").strip()
        if hy:
            hy_counts[hy] = hy_counts.get(hy, 0) + 1
    for code, row in by_code.items():
        if not row.get("from_limitup_pool"):
            continue
        hy = str(row.get("industry") or row.get("limitup_reason") or "").strip()
        row["limitup_theme_count"] = hy_counts.get(hy, 1)
    return list(by_code.values()), {
        "limitup_total": len(limitups),
        "limitup_added_missing_from_scored": added,
        "limitup_annotated_in_scored": annotated,
        "theme_counts": hy_counts,
    }


def tradable_filter_shadow(row: Dict[str, Any]) -> Tuple[bool, str]:
    """Shadow filter for the same T-day buyable universe as production."""
    if any(
        bool(row.get(key))
        for key in ("from_limitup_pool", "in_limitup_pool", "limitup_pool_member", "sealed_limit_up")
    ):
        return False, "current_day_limitup_not_tradable"
    return tradable_filter(row)


def build_profit_candidates(
    scored: List[Dict[str, Any]],
    sector_flow: Dict[str, Any],
    top_n: int = TOP_CANDIDATES,
    *,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    mainline_tags = list(sector_flow.get("mainline_tags") or [])
    industry_names = {
        str(x.get("name"))
        for x in (sector_flow.get("industry_top") or [])
        if isinstance(x, dict) and x.get("name")
    }
    w = dict(DEFAULT_RANK_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w and _num(v) is not None})

    pool: List[Dict[str, Any]] = []
    drop_reasons: Dict[str, int] = {}
    limitup_watch: List[Dict[str, Any]] = []
    for row in scored:
        ok, reason = tradable_filter_shadow(row)
        if not ok:
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
            continue
        code = _code(row)
        net = _num(row.get("net_inflow_main"))
        if net is None:
            capital = row.get("data_directory_capital_flow")
            if isinstance(capital, dict):
                net = _num(capital.get("main_force_net_inflow")) or _num(capital.get("main_net_inflow"))
        net = net or 0.0
        ml_score, ml_hits = mainline_match_score(row, mainline_tags)
        # Limitup reason / hybk still enriches diagnostics, but current-day
        # limitup rows were already removed by tradable_filter_shadow.
        industry_hits = [h for h in ml_hits if h in industry_names]
        is_limitup = bool(row.get("from_limitup_pool"))
        pool.append(
            {
                "row": row,
                "code": code,
                "net": net,
                "mainline_score": ml_score,
                "mainline_hits": ml_hits,
                "industry_hits": industry_hits,
                "stage_w": stage_weight(row),
                "is_limitup": is_limitup,
                "limitup_fund": _num(row.get("limitup_fund")) or 0.0,
                "theme_count": int(row.get("limitup_theme_count") or (1 if is_limitup else 0)),
            }
        )

    if not pool:
        return {
            "candidates": [],
            "pool_size": 0,
            "drop_reasons": drop_reasons,
            "mainline_tags": mainline_tags,
            "limitup_watch": [],
            "rank_weights": w,
        }

    max_net = max(item["net"] for item in pool) or 1.0
    for item in pool:
        row = item["row"]
        inflow_norm = min(1.0, max(0.0, item["net"] / max_net))
        early = _num(row.get("early_opportunity_score")) or 0.0
        theme = _num(row.get("main_theme_core_score")) or 0.0
        sector_opp = _num(row.get("sector_opportunity_score")) or 0.0
        fund_mom = _num(row.get("fund_flow_momentum"))
        if fund_mom is None:
            fund_mom = inflow_norm
        industry_boost = 1.0 if item["industry_hits"] else 0.0
        early_theme = min(1.0, max(early, theme, sector_opp, fund_mom))
        profit_score = (
            inflow_norm * w["inflow_norm"]
            + item["mainline_score"] * w["mainline_score"]
            + industry_boost * w["industry_boost"]
            + item["stage_w"] * w["stage_w"]
            + early_theme * w["early_theme"]
        ) * 100.0
        item["profit_score"] = round(profit_score, 4)
        item["inflow_norm"] = round(inflow_norm, 4)
        item["limitup_boost"] = 0.0
        item["theme_cluster_boost"] = 0.0
        item["features"] = {
            "inflow_norm": round(inflow_norm, 6),
            "mainline_score": round(item["mainline_score"], 6),
            "industry_boost": industry_boost,
            "stage_w": item["stage_w"],
            "early_theme": round(early_theme, 6),
            "limitup_boost": 0.0,
            "theme_cluster_boost": 0.0,
        }

    # Sort: industry mainline > any mainline > pure inflow.
    # Current-day limitups were removed above and never receive candidate seats.
    ranked = sorted(
        pool,
        key=lambda x: (
            3 if x["industry_hits"] else (
                2 if x["mainline_hits"] else 0
            ),
            x["profit_score"],
            x["net"],
        ),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []
    used = set()
    for item in ranked:
        if item["code"] in used:
            continue
        if item.get("industry_hits"):
            item["selection_role"] = "industry_mainline"
        elif item["mainline_hits"]:
            item["selection_role"] = "concept_mainline"
        else:
            item["selection_role"] = "inflow_filler"
        selected.append(item)
        used.add(item["code"])
        if len(selected) >= top_n:
            break

    # Full limitup watchlist (all mainboard limitups that passed shadow filter)
    for item in ranked:
        if not item["is_limitup"]:
            continue
        row = item["row"]
        limitup_watch.append(
            {
                "symbol": item["code"],
                "name": row.get("name"),
                "price": _num(row.get("price")),
                "signal_pct": _num(row.get("signal_pct")),
                "limitup_fund_yi": round((item["limitup_fund"] or 0) / 1e8, 4),
                "industry": row.get("industry"),
                "limitup_reason": row.get("limitup_reason"),
                "theme_count": item["theme_count"],
                "profit_score": item["profit_score"],
                "in_topn": item["code"] in used and any(
                    s["code"] == item["code"] for s in selected
                ),
                "observation_only": True,
            }
        )

    candidates: List[Dict[str, Any]] = []
    for rank, item in enumerate(selected, start=1):
        row = item["row"]
        pct = _num(row.get("signal_pct"))
        if pct is None:
            pct = _num(row.get("pct_chg"))
        candidates.append(
            {
                "rank": rank,
                "symbol": item["code"],
                "name": row.get("name"),
                "price": _num(row.get("price")),
                "signal_pct": pct,
                "board": row.get("board") or "main",
                "industry": row.get("industry") or row.get("sector"),
                "candidate_stage": row.get("candidate_stage"),
                "main_force_net_inflow": item["net"],
                "main_force_net_inflow_yi": round(item["net"] / 1e8, 4),
                "mainline_hits": item["mainline_hits"],
                "industry_hits": item.get("industry_hits") or [],
                "mainline_score": item["mainline_score"],
                "profit_score": item["profit_score"],
                "inflow_norm": item["inflow_norm"],
                "stage_weight": item["stage_w"],
                "limitup_boost": item.get("limitup_boost"),
                "theme_cluster_boost": item.get("theme_cluster_boost"),
                "from_limitup_pool": item["is_limitup"],
                "limitup_fund_yi": round((item["limitup_fund"] or 0) / 1e8, 4) if item["is_limitup"] else None,
                "limitup_reason": row.get("limitup_reason") if item["is_limitup"] else None,
                "features": item.get("features"),
                "selection_role": item.get("selection_role")
                or (
                    "industry_mainline"
                    if item.get("industry_hits")
                    else ("mainline" if item["mainline_hits"] else "inflow_filler")
                ),
                "early_opportunity_score": _num(row.get("early_opportunity_score")),
                "main_theme_core_score": _num(row.get("main_theme_core_score")),
                "sector_opportunity_score": _num(row.get("sector_opportunity_score")),
                "final_score_scan": _num(row.get("final_score") if row.get("final_score") is not None else row.get("score")),
                "perspective": "main_force_hot_money_shadow",
                "not_official_paper_pick": True,
                "observation_only": True,
                "decision_class": "PROFIT_CANDIDATE_SHADOW",
            }
        )

    return {
        "candidates": candidates,
        "pool_size": len(pool),
        "drop_reasons": drop_reasons,
        "mainline_tags": mainline_tags,
        "limitup_watch": limitup_watch,
        "rank_weights": w,
    }


def fit_rank_weights_from_returns(
    feature_rows: Sequence[Dict[str, Any]],
    *,
    min_n: int = 20,
) -> Dict[str, Any]:
    """Return-supervised weight nudge: raise weights of features correlated with T+1.

    Simple, stable: for each feature, Spearman-like rank correlation with ret_t1_close,
    then blend default weights toward positive-corr features. Shadow only — never
    writes official scoring_config.
    """
    rows = [r for r in feature_rows if r.get("ret") is not None and isinstance(r.get("features"), dict)]
    if len(rows) < min_n:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "n": len(rows),
            "min_n": min_n,
            "weights": dict(DEFAULT_RANK_WEIGHTS),
        }

    keys = list(DEFAULT_RANK_WEIGHTS.keys())
    rets = [float(r["ret"]) for r in rows]
    # Rank rets
    order = sorted(range(len(rets)), key=lambda i: rets[i])
    ret_rank = [0.0] * len(rets)
    for rk, i in enumerate(order):
        ret_rank[i] = float(rk)

    corrs: Dict[str, float] = {}
    for key in keys:
        vals = [float((r["features"] or {}).get(key) or 0.0) for r in rows]
        order_f = sorted(range(len(vals)), key=lambda i: vals[i])
        f_rank = [0.0] * len(vals)
        for rk, i in enumerate(order_f):
            f_rank[i] = float(rk)
        # Pearson on ranks = Spearman
        n = float(len(vals))
        mean_r = sum(ret_rank) / n
        mean_f = sum(f_rank) / n
        num = sum((ret_rank[i] - mean_r) * (f_rank[i] - mean_f) for i in range(int(n)))
        den_r = sum((x - mean_r) ** 2 for x in ret_rank) ** 0.5
        den_f = sum((x - mean_f) ** 2 for x in f_rank) ** 0.5
        corrs[key] = (num / (den_r * den_f)) if den_r > 1e-9 and den_f > 1e-9 else 0.0

    # Nudge defaults by Spearman: positive corr raises weight, negative lowers.
    # Scale = 1 + 0.5*corr ∈ [0.5, 1.5]; then renorm core sum to default core total.
    # Keeps defaults as anchor (not overfit) while ensuring corr direction is respected.
    blended: Dict[str, float] = {}
    for k in keys:
        scale = 1.0 + 0.5 * corrs[k]
        blended[k] = max(0.01, DEFAULT_RANK_WEIGHTS[k] * scale)
    core = ("inflow_norm", "mainline_score", "industry_boost", "stage_w", "early_theme")
    core_sum = sum(blended[k] for k in core) or 1.0
    target_core = sum(DEFAULT_RANK_WEIGHTS[k] for k in core)
    for k in core:
        blended[k] = round(blended[k] * target_core / core_sum, 4)
    # Extra components (limitup/theme) also rounded; not forced into core sum
    for k in keys:
        if k not in core:
            blended[k] = round(blended[k], 4)

    return {
        "status": "OK",
        "n": len(rows),
        "correlations": {k: round(v, 4) for k, v in corrs.items()},
        "weights": blended,
        "method": "spearman_nudge_default_x_1p5corr",
        "observation_only": True,
        "official_gates_unchanged": True,
    }


def save_rank_weights(fit: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or RANK_WEIGHTS_PATH
    SUMMARY.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "status": fit.get("status"),
        "n": fit.get("n"),
        "correlations": fit.get("correlations"),
        "weights": fit.get("weights") or dict(DEFAULT_RANK_WEIGHTS),
        "method": fit.get("method"),
        "observation_only": True,
        "official_gates_unchanged": True,
        "note": "Shadow profit_score weights only; does not write scoring_config or formal sort key",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _load_klines(symbol: str, begin: str, end: str) -> Tuple[List[Dict[str, Any]], str]:
    """Eastmoney first, Tencent fallback (network best-effort)."""
    from xiaogu_forward_result_filler_v0_1 import (
        fetch_eastmoney_klines,
        fetch_tencent_klines,
        parse_klines,
    )

    payload = fetch_eastmoney_klines(symbol, begin, end, retries=1)
    rows = parse_klines(payload)
    if rows:
        return rows, "eastmoney"
    payload = fetch_tencent_klines(symbol, begin, end)
    rows = parse_klines(payload)
    if rows:
        return rows, "tencent"
    return [], "none"


def fetch_t1_return(symbol: str, trade_date: str) -> Dict[str, Any]:
    """T+1 close return vs signal-day close (Eastmoney → Tencent)."""
    day = date.fromisoformat(trade_date)
    t1 = next_weekday(day)
    today = date.today()
    if t1 > today:
        return {
            "status": "PENDING_T1",
            "t1_date": t1.isoformat(),
            "ret_t1_close": None,
            "ret_t1_high": None,
            "note": "t1 trading day not reached yet",
        }
    begin = (day - timedelta(days=5)).isoformat()
    end = (t1 + timedelta(days=5)).isoformat()
    klines, source = _load_klines(symbol, begin, end)
    by_date = {k["date"]: k for k in klines}
    d0 = by_date.get(trade_date)
    d1 = by_date.get(t1.isoformat())
    if not d0 or not d1:
        return {
            "status": "MISSING_KLINE",
            "t1_date": t1.isoformat(),
            "source": source,
            "entry_close": d0.get("close") if d0 else None,
            "t1_close": d1.get("close") if d1 else None,
            "t1_high": d1.get("high") if d1 else None,
            "ret_t1_close": None,
            "ret_t1_high": None,
        }
    entry = float(d0["close"])
    t1_close = float(d1["close"])
    t1_high = float(d1["high"])
    if entry <= 0:
        return {"status": "BAD_ENTRY", "t1_date": t1.isoformat(), "ret_t1_close": None, "ret_t1_high": None}
    return {
        "status": "OK",
        "t1_date": t1.isoformat(),
        "source": source,
        "entry_close": entry,
        "t1_close": t1_close,
        "t1_high": t1_high,
        "ret_t1_close": round((t1_close - entry) / entry, 6),
        "ret_t1_high": round((t1_high - entry) / entry, 6),
    }


def attach_returns(candidates: List[Dict[str, Any]], trade_date: str, delay: float = 0.05) -> None:
    for c in candidates:
        try:
            ret = fetch_t1_return(c["symbol"], trade_date)
        except Exception as exc:  # noqa: BLE001 — diagnostic path
            ret = {"status": "ERROR", "error": repr(exc), "ret_t1_close": None, "ret_t1_high": None}
        c["t1"] = ret
        time.sleep(delay)


def run_for_date(
    trade_date: str,
    *,
    top_n: int = TOP_CANDIDATES,
    with_returns: bool = False,
    scan_dir: Optional[Path] = None,
    known_fingerprints: Optional[Dict[str, str]] = None,
    skip_stale: bool = True,
    weights: Optional[Dict[str, float]] = None,
    include_limitup: bool = True,
) -> Dict[str, Any]:
    resolved = scan_dir or resolve_scan_dir(trade_date)
    if resolved is None:
        payload = {
            "status": "NO_SCAN",
            "trade_date": trade_date,
            "error": f"no scored scan under data/live_scan/{trade_date}",
            "valid_for_conclusion": False,
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "candidates": [],
            "candidate_count": 0,
            "decision_class": "PROFIT_CANDIDATE_SHADOW",
            "not_official_paper_pick": True,
            "observation_only": True,
            "official_gates_unchanged": True,
        }
        SUMMARY.mkdir(parents=True, exist_ok=True)
        summary_path = SUMMARY / f"profit_candidates_{trade_date}.json"
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            payload["output_path"] = str(summary_path.relative_to(ROOT))
        except ValueError:
            payload["output_path"] = str(summary_path)
        return payload

    fingerprint = scan_content_fingerprint(resolved)
    stale_of = find_stale_scan_source(
        trade_date, fingerprint, known=known_fingerprints
    )
    if known_fingerprints is not None and fingerprint not in known_fingerprints:
        known_fingerprints[fingerprint] = trade_date

    scored_path = resolved / "eastmoney_web_tabs_scored.jsonl"
    scored = load_jsonl(scored_path)
    sector_flow = load_sector_flows(resolved)
    limitup_merge_meta: Dict[str, Any] = {
        "limitup_total": 0,
        "limitup_added_missing_from_scored": 0,
        "limitup_annotated_in_scored": 0,
        "theme_counts": {},
    }
    if include_limitup:
        limitups = load_limitup_pool(resolved)
        scored, limitup_merge_meta = merge_scored_with_limitup(scored, limitups)
    rank_weights = weights if weights is not None else load_rank_weights()
    built = build_profit_candidates(
        scored, sector_flow, top_n=top_n, weights=rank_weights
    )
    candidates = built["candidates"]
    if with_returns and candidates and not (skip_stale and stale_of):
        attach_returns(candidates, trade_date)
        # Diagnostic T+1 only for limitup names already in topN (avoid N×watch network cost)
        top_syms = {str(c.get("symbol")) for c in candidates}
        for w in built.get("limitup_watch") or []:
            if str(w.get("symbol") or "") not in top_syms:
                continue
            if w.get("symbol") and _candidate_ret(w) is None:
                try:
                    w["t1"] = fetch_t1_return(str(w["symbol"]), trade_date)
                    w["ret_t1_close"] = (w.get("t1") or {}).get("ret_t1_close")
                except Exception as exc:  # noqa: BLE001
                    w["t1"] = {"status": "ERROR", "error": repr(exc)}
                time.sleep(0.03)

    status = "STALE_SCAN" if stale_of and skip_stale else "OK"
    summary_path = SUMMARY / f"profit_candidates_{trade_date}.json"
    try:
        scan_dir_str = str(resolved.relative_to(ROOT))
    except ValueError:
        scan_dir_str = str(resolved)
    payload = {
        "status": status,
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "scan_dir": scan_dir_str,
        "scan_fingerprint": fingerprint,
        "stale_of": stale_of,
        "valid_for_conclusion": status == "OK",
        "scored_count": len(scored),
        "tradable_inflow_pool_size": built["pool_size"],
        "drop_reasons": built["drop_reasons"],
        "limitup_merge": limitup_merge_meta,
        "limitup_watch": built.get("limitup_watch") or [],
        "rank_weights": built.get("rank_weights") or rank_weights,
        "mainline": {
            "industry_top": sector_flow.get("industry_top"),
            "concept_top": sector_flow.get("concept_top"),
            "mainline_tags": built["mainline_tags"],
        },
        "decision_class": "PROFIT_CANDIDATE_SHADOW",
        "not_official_paper_pick": True,
        "observation_only": True,
        "official_gates_unchanged": True,
        "selection_basis": [
            "mainboard + price<=70 + signal_pct in [0.5, 9.5), current-day limitups excluded",
            "main_force_net_inflow > 0 required",
            "limitup_pool may be loaded for exclusion diagnostics, never as candidate seats",
            "rank by industry_mainline > concept_mainline > inflow",
            "profit_score uses return-supervised weights when available",
            "stale scan fingerprint rejected from conclusions",
            "basket_eq / top2_eq / top3_eq diagnostic strategies included",
        ],
        "candidates": candidates,
        "candidate_count": len(candidates),
    }
    if stale_of:
        payload["error"] = (
            f"scan content fingerprint matches {stale_of}; "
            "excluded from conclusion aggregates (likely copied/stale scan)"
        )
    if with_returns and candidates and status == "OK":
        rets = [_candidate_ret(c) for c in candidates]
        valid = [r for r in rets if r is not None]
        wins = sum(1 for r in valid if r > 0)
        payload["t1_stats"] = {
            "n": len(valid),
            "win_rate": round(wins / len(valid), 4) if valid else None,
            "avg_ret_t1_close": round(sum(valid) / len(valid), 6) if valid else None,
            "missing": len(rets) - len(valid),
        }
        payload["basket_stats"] = compute_basket_stats(candidates)
    elif status == "STALE_SCAN":
        payload["t1_stats"] = None
        payload["basket_stats"] = None
    SUMMARY.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        payload["output_path"] = str(summary_path.relative_to(ROOT))
    except ValueError:
        payload["output_path"] = str(summary_path)
    return payload


def load_official_paper_pick(trade_date: str) -> Dict[str, Any]:
    """Best-effort official PAPER_PICK for the day (summary → ledger → empty)."""
    formal = SUMMARY / f"{trade_date}_formal_paper_pick.json"
    if formal.exists():
        try:
            payload = json.loads(formal.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                decision = str(payload.get("decision") or "").upper()
                symbol = str(payload.get("symbol") or payload.get("code") or "").zfill(6)
                if decision == "PAPER_PICK" and symbol and symbol != "000000":
                    return {
                        "source": str(formal.relative_to(ROOT)),
                        "decision": "PAPER_PICK",
                        "symbol": symbol,
                        "name": payload.get("name") or payload.get("stock_name"),
                        "score": _num(payload.get("score") if payload.get("score") is not None else payload.get("final_score")),
                    }
                if decision == "NO_PICK" or not symbol or symbol in ("", "NO_PICK"):
                    return {
                        "source": str(formal.relative_to(ROOT)),
                        "decision": "NO_PICK",
                        "symbol": None,
                        "name": None,
                        "score": None,
                    }
        except Exception:
            pass

    live = SUMMARY / f"{trade_date}_live_pick_result.json"
    if live.exists():
        try:
            payload = json.loads(live.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                decision = str(payload.get("decision") or "").upper()
                symbol = str(payload.get("symbol") or "").zfill(6) if payload.get("symbol") else None
                if decision == "PAPER_PICK" and symbol:
                    return {
                        "source": str(live.relative_to(ROOT)),
                        "decision": "PAPER_PICK",
                        "symbol": symbol,
                        "name": payload.get("name"),
                        "score": _num(payload.get("score") or payload.get("final_score")),
                    }
        except Exception:
            pass

    ledger = ROOT / "forward_paper_ledger_v0_1.jsonl"
    if ledger.exists():
        last: Optional[Dict[str, Any]] = None
        with ledger.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("date") or row.get("trade_date") or "") == trade_date:
                    last = row
        if isinstance(last, dict):
            decision = str(last.get("decision") or "").upper()
            symbol = str(last.get("symbol") or "").zfill(6) if last.get("symbol") else None
            if decision == "PAPER_PICK" and symbol and symbol not in ("NO_PICK", "000000"):
                return {
                    "source": "forward_paper_ledger_v0_1.jsonl",
                    "decision": "PAPER_PICK",
                    "symbol": symbol,
                    "name": ((last.get("features_used") or {}).get("single_target_card") or {}).get("name"),
                    "score": _num(((last.get("features_used") or {}).get("single_target_card") or {}).get("final_score")),
                }
            return {
                "source": "forward_paper_ledger_v0_1.jsonl",
                "decision": decision or "NO_PICK",
                "symbol": None if decision != "PAPER_PICK" else symbol,
                "name": None,
                "score": None,
            }

    return {"source": None, "decision": "UNKNOWN", "symbol": None, "name": None, "score": None}


def compare_shadow_vs_official(
    trade_date: str,
    shadow_day: Dict[str, Any],
    *,
    with_returns: bool,
) -> Dict[str, Any]:
    """Compare shadow Top1 vs official PAPER_PICK on T+1 close (diagnostic)."""
    official = load_official_paper_pick(trade_date)
    candidates = shadow_day.get("candidates") or []
    top1 = candidates[0] if candidates else None
    shadow_symbol = top1.get("symbol") if isinstance(top1, dict) else None
    official_symbol = official.get("symbol")

    shadow_ret = None
    if isinstance(top1, dict):
        shadow_ret = (top1.get("t1") or {}).get("ret_t1_close")
        if shadow_ret is None and "ret_t1_close" in top1:
            shadow_ret = top1.get("ret_t1_close")

    official_ret = None
    official_t1_status = None
    if with_returns and official_symbol:
        try:
            ot = fetch_t1_return(str(official_symbol), trade_date)
            official_ret = ot.get("ret_t1_close")
            official_t1_status = ot.get("status")
        except Exception as exc:  # noqa: BLE001
            official_t1_status = f"ERROR:{exc!r}"

    same = bool(shadow_symbol and official_symbol and shadow_symbol == official_symbol)
    delta = None
    if shadow_ret is not None and official_ret is not None:
        delta = round(float(shadow_ret) - float(official_ret), 6)

    shadow_beats = None
    if shadow_ret is not None and official_ret is not None:
        shadow_beats = float(shadow_ret) > float(official_ret)
    elif official.get("decision") == "NO_PICK" and shadow_ret is not None:
        shadow_beats = float(shadow_ret) > 0

    return {
        "trade_date": trade_date,
        "official": {
            **official,
            "ret_t1_close": official_ret,
            "t1_status": official_t1_status,
        },
        "shadow_top1": {
            "symbol": shadow_symbol,
            "name": top1.get("name") if isinstance(top1, dict) else None,
            "profit_score": top1.get("profit_score") if isinstance(top1, dict) else None,
            "mainline_hits": top1.get("mainline_hits") if isinstance(top1, dict) else None,
            "ret_t1_close": shadow_ret,
            "t1_status": (top1.get("t1") or {}).get("status") if isinstance(top1, dict) else top1.get("t1_status") if isinstance(top1, dict) else None,
        },
        "same_symbol": same,
        "shadow_minus_official_t1": delta,
        "shadow_beats_official": shadow_beats,
        "note": "diagnostic only; does not change official PAPER_PICK",
    }


def _day_summary_from_payload(d: str, day: Dict[str, Any], valid: bool) -> Dict[str, Any]:
    return {
        "trade_date": d,
        "status": day.get("status"),
        "valid_for_conclusion": valid,
        "scan_dir": day.get("scan_dir"),
        "scan_fingerprint": day.get("scan_fingerprint"),
        "stale_of": day.get("stale_of"),
        "limitup_merge": day.get("limitup_merge"),
        "limitup_watch_top": (day.get("limitup_watch") or [])[:8],
        "mainline_industry_top3": [
            {"name": x.get("name"), "net_yi": x.get("net_inflow_yi")}
            for x in (day.get("mainline") or {}).get("industry_top") or []
        ][:3],
        "candidates": [
            {
                "rank": c.get("rank"),
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "signal_pct": c.get("signal_pct"),
                "main_force_yi": c.get("main_force_net_inflow_yi"),
                "mainline_hits": c.get("mainline_hits"),
                "profit_score": c.get("profit_score"),
                "selection_role": c.get("selection_role"),
                "from_limitup_pool": c.get("from_limitup_pool"),
                "limitup_fund_yi": c.get("limitup_fund_yi"),
                "features": c.get("features"),
                "ret_t1_close": (c.get("t1") or {}).get("ret_t1_close"),
                "t1_status": (c.get("t1") or {}).get("status"),
            }
            for c in day.get("candidates") or []
        ],
        "t1_stats": day.get("t1_stats"),
        "basket_stats": day.get("basket_stats"),
        "rank_weights": day.get("rank_weights"),
        "output_path": day.get("output_path"),
        "error": day.get("error"),
    }


def run_replay(
    dates: Sequence[str],
    *,
    top_n: int,
    with_returns: bool,
    compare_official: bool = False,
    skip_stale: bool = True,
    fit_weights: bool = False,
    refit_pass: bool = False,
    include_limitup: bool = True,
) -> Dict[str, Any]:
    """Multi-day shadow replay.

    If fit_weights=True and with_returns=True:
      1) pass-1 with current/default weights, collect feature→T+1 rows
      2) fit Spearman-blend weights, save profit_rank_weights_latest.json
      3) if refit_pass=True, re-run all dates with fitted weights (still shadow only)
    """
    days: List[Dict[str, Any]] = []
    all_rets: List[float] = []
    compares: List[Dict[str, Any]] = []
    basket_day_stats: List[Dict[str, Any]] = []
    known_fps: Dict[str, str] = {}
    excluded: List[Dict[str, str]] = []
    feature_rows: List[Dict[str, Any]] = []
    weight_fit: Optional[Dict[str, Any]] = None
    active_weights = load_rank_weights()

    def _one_pass(use_weights: Optional[Dict[str, float]]) -> None:
        nonlocal days, all_rets, compares, basket_day_stats, known_fps, excluded, feature_rows
        days = []
        all_rets = []
        compares = []
        basket_day_stats = []
        known_fps = {}
        excluded = []
        feature_rows = []
        for d in dates:
            day = run_for_date(
                d,
                top_n=top_n,
                with_returns=with_returns,
                known_fingerprints=known_fps,
                skip_stale=skip_stale,
                weights=use_weights,
                include_limitup=include_limitup,
            )
            valid = bool(day.get("valid_for_conclusion", day.get("status") == "OK"))
            day_summary = _day_summary_from_payload(d, day, valid)
            if not valid:
                excluded.append(
                    {
                        "trade_date": d,
                        "status": str(day.get("status")),
                        "reason": str(day.get("error") or day.get("status")),
                        "stale_of": day.get("stale_of"),
                    }
                )
            if compare_official and day.get("status") == "OK" and valid:
                cmp_payload = {"candidates": day.get("candidates") or day_summary["candidates"]}
                comparison = compare_shadow_vs_official(d, cmp_payload, with_returns=with_returns)
                day_summary["vs_official"] = comparison
                compares.append(comparison)
                daily_path = SUMMARY / f"profit_candidates_{d}.json"
                if daily_path.exists():
                    try:
                        daily = json.loads(daily_path.read_text(encoding="utf-8"))
                        daily["vs_official"] = comparison
                        daily_path.write_text(
                            json.dumps(daily, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
            days.append(day_summary)
            if valid:
                if day.get("basket_stats"):
                    basket_day_stats.append(day["basket_stats"])
                for c in day.get("candidates") or []:
                    r = _candidate_ret(c)
                    if r is not None:
                        all_rets.append(float(r))
                        if isinstance(c.get("features"), dict):
                            feature_rows.append(
                                {
                                    "trade_date": d,
                                    "symbol": c.get("symbol"),
                                    "ret": float(r),
                                    "features": c.get("features"),
                                }
                            )

    # Pass 1
    _one_pass(active_weights if not fit_weights else dict(DEFAULT_RANK_WEIGHTS))

    if fit_weights and with_returns:
        weight_fit = fit_rank_weights_from_returns(feature_rows)
        if weight_fit.get("status") == "OK":
            save_rank_weights(weight_fit)
            active_weights = weight_fit.get("weights") or active_weights
            if refit_pass:
                _one_pass(active_weights)

    wins = sum(1 for r in all_rets if r > 0)
    shadow_beats_n = sum(1 for c in compares if c.get("shadow_beats_official") is True)
    comparable = [c for c in compares if c.get("shadow_beats_official") is not None]
    valid_days = [x for x in days if x.get("valid_for_conclusion")]
    pending_t1_days = []
    return_ready_days = []
    for x in valid_days:
        cands = x.get("candidates") or []
        statuses = [c.get("t1_status") for c in cands]
        if statuses and all(s == "PENDING_T1" for s in statuses if s):
            pending_t1_days.append(x["trade_date"])
        elif any(c.get("ret_t1_close") is not None for c in cands):
            return_ready_days.append(x["trade_date"])
    strategy_agg = aggregate_strategy_stats(basket_day_stats) if basket_day_stats else {}
    aggregate = {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "dates": list(dates),
        "official_gates_unchanged": True,
        "decision_class": "PROFIT_CANDIDATE_SHADOW",
        "rank_weight_fit": weight_fit,
        "active_rank_weights": active_weights,
        "conclusion_policy": {
            "skip_stale_scans": skip_stale,
            "valid_days": len(valid_days),
            "return_ready_days": return_ready_days,
            "pending_t1_days": pending_t1_days,
            "excluded_days": excluded,
            "fit_weights": fit_weights,
            "refit_pass": refit_pass and bool(weight_fit and weight_fit.get("status") == "OK"),
            "note": (
                "aggregates use valid_for_conclusion=True days only; "
                "strategy/T+1 stats further require observed returns "
                "(PENDING_T1 days listed separately until next session); "
                "rank weights are shadow-only and never write scoring_config"
            ),
        },
        "days": days,
        "aggregate_t1": {
            "n": len(all_rets),
            "win_rate": round(wins / len(all_rets), 4) if all_rets else None,
            "avg_ret_t1_close": round(sum(all_rets) / len(all_rets), 6) if all_rets else None,
            "scope": "valid_days_all_candidates_with_t1",
            "return_ready_day_count": len(return_ready_days),
        },
        "strategy_aggregate": strategy_agg,
        "vs_official_summary": {
            "days": len(compares),
            "comparable_days": len(comparable),
            "shadow_beats_official_days": shadow_beats_n,
            "shadow_beats_rate": round(shadow_beats_n / len(comparable), 4) if comparable else None,
            "same_symbol_days": sum(1 for c in compares if c.get("same_symbol")),
            "scope": "valid_days_only",
            "pending_t1_days": pending_t1_days,
        } if compare_official else None,
    }
    out = SUMMARY / "profit_candidates_replay.json"
    SUMMARY.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    if compare_official:
        cmp_out = SUMMARY / "profit_candidates_vs_official.json"
        cmp_out.write_text(
            json.dumps(
                {
                    "status": "OK",
                    "generated_at": aggregate["generated_at"],
                    "official_gates_unchanged": True,
                    "conclusion_policy": aggregate["conclusion_policy"],
                    "rank_weight_fit": weight_fit,
                    "active_rank_weights": active_weights,
                    "strategy_aggregate": strategy_agg,
                    "summary": aggregate["vs_official_summary"],
                    "days": compares,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        aggregate["compare_output_path"] = str(cmp_out.relative_to(ROOT))
    aggregate["output_path"] = str(out.relative_to(ROOT))
    if weight_fit and weight_fit.get("status") == "OK":
        aggregate["weights_path"] = str(RANK_WEIGHTS_PATH.relative_to(ROOT))
    return aggregate


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow profit candidates from main-force / mainline fund flow")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    parser.add_argument(
        "--dates",
        help="Comma-separated trade dates for replay (e.g. 2026-07-22,2026-07-23,2026-07-24)",
    )
    parser.add_argument("--top", type=int, default=TOP_CANDIDATES, help="Top N candidates (default 5)")
    parser.add_argument(
        "--with-returns",
        action="store_true",
        help="Fetch T+1 close/high returns via Eastmoney klines (network)",
    )
    parser.add_argument(
        "--compare-official",
        action="store_true",
        help="Compare shadow Top1 vs official PAPER_PICK T+1 (diagnostic)",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Do not exclude STALE_SCAN days from selection/returns (still fingerprints)",
    )
    parser.add_argument(
        "--fit-weights",
        action="store_true",
        help="Fit return-supervised shadow rank weights from T+1 (requires --with-returns; shadow only)",
    )
    parser.add_argument(
        "--refit-pass",
        action="store_true",
        help="After --fit-weights, re-run all dates with fitted weights",
    )
    parser.add_argument(
        "--no-limitup",
        action="store_true",
        help="Disable limitup_pool merge into shadow universe",
    )
    parser.add_argument("--scan-dir", help="Override scan directory path")
    args = parser.parse_args(argv)
    skip_stale = not args.allow_stale
    include_limitup = not args.no_limitup

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
        result = run_replay(
            dates,
            top_n=args.top,
            with_returns=args.with_returns,
            compare_official=args.compare_official,
            skip_stale=skip_stale,
            fit_weights=args.fit_weights,
            refit_pass=args.refit_pass,
            include_limitup=include_limitup,
        )
        print(json.dumps(
            {
                "status": result["status"],
                "output_path": result.get("output_path"),
                "compare_output_path": result.get("compare_output_path"),
                "weights_path": result.get("weights_path"),
                "rank_weight_fit": result.get("rank_weight_fit"),
                "active_rank_weights": result.get("active_rank_weights"),
                "conclusion_policy": result.get("conclusion_policy"),
                "aggregate_t1": result.get("aggregate_t1"),
                "strategy_aggregate": result.get("strategy_aggregate"),
                "vs_official_summary": result.get("vs_official_summary"),
                "days": [
                    {
                        "trade_date": d.get("trade_date"),
                        "status": d.get("status"),
                        "valid_for_conclusion": d.get("valid_for_conclusion"),
                        "stale_of": d.get("stale_of"),
                        "candidates": d.get("candidates"),
                        "t1_stats": d.get("t1_stats"),
                        "basket_stats": d.get("basket_stats"),
                        "vs_official": d.get("vs_official"),
                        "error": d.get("error"),
                    }
                    for d in result.get("days") or []
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0 if result.get("status") == "OK" else 1

    if not args.date:
        parser.error("provide --date or --dates")

    scan = Path(args.scan_dir) if args.scan_dir else None
    result = run_for_date(
        args.date,
        top_n=args.top,
        with_returns=args.with_returns,
        scan_dir=scan,
        skip_stale=skip_stale,
        include_limitup=include_limitup,
    )
    if args.compare_official and result.get("status") == "OK" and result.get("valid_for_conclusion", True):
        comparison = compare_shadow_vs_official(args.date, result, with_returns=args.with_returns)
        result["vs_official"] = comparison
        out_path = SUMMARY / f"profit_candidates_{args.date}.json"
        if out_path.exists():
            daily = json.loads(out_path.read_text(encoding="utf-8"))
            daily["vs_official"] = comparison
            out_path.write_text(json.dumps(daily, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "status": result.get("status"),
            "trade_date": result.get("trade_date"),
            "valid_for_conclusion": result.get("valid_for_conclusion"),
            "stale_of": result.get("stale_of"),
            "scan_fingerprint": result.get("scan_fingerprint"),
            "output_path": result.get("output_path"),
            "mainline_industry_top3": [
                {"name": x.get("name"), "net_yi": x.get("net_inflow_yi")}
                for x in (result.get("mainline") or {}).get("industry_top") or []
            ][:3],
            "candidates": [
                {
                    "rank": c.get("rank"),
                    "symbol": c.get("symbol"),
                    "name": c.get("name"),
                    "signal_pct": c.get("signal_pct"),
                    "main_force_yi": c.get("main_force_net_inflow_yi"),
                    "mainline_hits": c.get("mainline_hits"),
                    "profit_score": c.get("profit_score"),
                    "ret_t1_close": (c.get("t1") or {}).get("ret_t1_close"),
                    "t1_status": (c.get("t1") or {}).get("status"),
                }
                for c in (result.get("candidates") or [])
            ],
            "t1_stats": result.get("t1_stats"),
            "basket_stats": result.get("basket_stats"),
            "vs_official": result.get("vs_official"),
            "error": result.get("error"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if result.get("status") in ("OK", "STALE_SCAN", "NO_SCAN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
