# xiaogu 生命闭环完善 — Compose 执行规格

**模型**: mimoauto  
**权限**: 全开  
**Claude角色**: 设计+验收，不执行代码  
**Mimocode角色**: 全部代码实现+本地验证

---

## COMPOSE TASK 1: LC-04 A股交易日历（先行）

### 文件
`xiaogu_scheduler.py`

### 实现要求
```python
# 在 is_trading_day() 函数，替换当前 weekday<5 实现

CHINA_HOLIDAYS_2026 = {
    "2026-01-01",
    "2026-01-26","2026-01-27","2026-01-28","2026-01-29","2026-01-30",
    "2026-02-02","2026-02-03",
    "2026-04-06",
    "2026-05-01","2026-05-04","2026-05-05",
    "2026-06-22",
    "2026-10-01","2026-10-02","2026-10-05","2026-10-06",
    "2026-10-07","2026-10-08","2026-10-09",
}

def is_trading_day(check_date=None) -> bool:
    """Return True if check_date (default: today) is an A-share trading day."""
    import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    d = check_date or datetime.datetime.now(tz).date()
    if isinstance(d, datetime.datetime):
        d = d.date()
    date_str = d.isoformat()
    if d.weekday() >= 5:
        return False
    # Try exchange_calendars first
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XSHG")
        return bool(cal.is_session(date_str))
    except Exception:
        pass
    # Fallback to hardcoded list
    return date_str not in CHINA_HOLIDAYS_2026
```

### 验收
```bash
python -c "
from xiaogu_scheduler import is_trading_day
import datetime
assert is_trading_day(datetime.date(2026,10,1)) == False, '国庆应为休市'
assert is_trading_day(datetime.date(2026,6,26)) == True, '普通周五应为交易日'
assert is_trading_day(datetime.date(2026,6,27)) == False, '周六应为休市'
print('LC-04 PASS')
"
```

---

## COMPOSE TASK 2: LC-01 权重反馈自动写回

### 文件
`xiaogu_signal_effectiveness_v0_1.py` (主改)  
`xiaogu_scheduler.py` (追加调用)

### xiaogu_signal_effectiveness_v0_1.py 新增

在文件末尾（`main()` 之前）新增以下两个函数：

```python
WEIGHT_STEP = 0.1
WEIGHT_MIN = 0.1
WEIGHT_MAX = 3.0


def apply_weight_suggestions(
    analysis_result: Dict[str, Any],
    db_url: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Write INCREASE/DECREASE suggestions back to scoring_config table.

    Returns dict with applied, skipped, errors lists.
    """
    import os
    db_url = db_url or os.environ.get("XIAOGU_DB_URL")
    signals = analysis_result.get("signal_effectiveness", [])
    applied = []
    skipped = []
    errors = []

    if not db_url:
        return {"applied": [], "skipped": signals, "errors": ["no db_url"], "dry_run": dry_run}

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
    except Exception as e:
        return {"applied": [], "skipped": signals, "errors": [str(e)], "dry_run": dry_run}

    import datetime as _dt

    for sig in signals:
        key = sig["signal_key"]
        suggestion = sig.get("weight_suggestion")
        if suggestion not in ("INCREASE", "DECREASE"):
            skipped.append({"key": key, "reason": suggestion})
            continue

        try:
            cur.execute(
                "SELECT config_value FROM scoring_config WHERE signal_key = %s", (key,)
            )
            row = cur.fetchone()
            current = float(row[0]) if row else 1.0
            delta = WEIGHT_STEP if suggestion == "INCREASE" else -WEIGHT_STEP
            new_val = round(max(WEIGHT_MIN, min(WEIGHT_MAX, current + delta)), 4)

            if dry_run:
                applied.append({"key": key, "old": current, "new": new_val, "suggestion": suggestion, "dry_run": True})
                continue

            if row:
                cur.execute(
                    "UPDATE scoring_config SET config_value=%s, updated_at=%s WHERE signal_key=%s",
                    (new_val, _dt.datetime.utcnow(), key),
                )
            else:
                cur.execute(
                    "INSERT INTO scoring_config(signal_key, config_value, updated_at) VALUES(%s,%s,%s)",
                    (key, new_val, _dt.datetime.utcnow()),
                )
            applied.append({"key": key, "old": current, "new": new_val, "suggestion": suggestion})
        except Exception as e:
            errors.append({"key": key, "error": str(e)})

    if not dry_run:
        conn.commit()
    cur.close()
    conn.close()
    return {"applied": applied, "skipped": skipped, "errors": errors, "dry_run": dry_run}
```

同时在 `main()` 函数末尾追加：

```python
    # --apply-weights flag
    import os
    ap.add_argument("--apply-weights", action="store_true", dest="apply_weights")
    # (注意 ap 已在 main 开头定义，这行追加到 ap 参数解析之前，在 args = ap.parse_args() 之前)
```

实际上直接在 `main()` 中的 `args = ap.parse_args()` 之前插入：
```python
    ap.add_argument("--apply-weights", action="store_true", dest="apply_weights",
                    help="Apply weight suggestions to scoring_config table")
```

并在 `main()` 末尾追加：
```python
    if args.apply_weights:
        import os
        db_url = os.environ.get("XIAOGU_DB_URL")
        dry = not os.environ.get("XIAOGU_WEIGHT_AUTO_TUNE") == "1"
        tune_result = apply_weight_suggestions(result, db_url=db_url, dry_run=dry)
        print(f"\nWeight tuning ({'DRY RUN' if dry else 'LIVE'}):")
        for item in tune_result["applied"]:
            print(f"  {item['key']}: {item.get('old','-')} → {item['new']} ({item['suggestion']})")
        if tune_result["errors"]:
            print(f"  ERRORS: {tune_result['errors']}")
```

### xiaogu_scheduler.py 新增

在 `job_signal_effectiveness` 函数体末尾追加：
```python
    # Auto-tune weights if enabled
    if os.environ.get("XIAOGU_WEIGHT_AUTO_TUNE") == "1":
        run_cmd([
            PYTHON, "xiaogu_signal_effectiveness_v0_1.py",
            "--ledger", "forward_paper_ledger_v0_1.jsonl",
            "--min-samples", "3",
            "--apply-weights",
        ], "weight_auto_tune")
```

### 验收
```bash
python xiaogu_signal_effectiveness_v0_1.py \
  --ledger forward_paper_ledger_v0_1.jsonl \
  --min-samples 3 \
  --apply-weights
# 应输出 "Weight tuning (DRY RUN):" 和各信号的权重变动预览
# 无DB连接时输出 "no db_url" 提示，不崩溃
```

---

## COMPOSE TASK 3: LC-03 滚动涨停率门控

### 文件
`xiaogu_signal_effectiveness_v0_1.py` (追加函数)  
`xiaogu_scheduler.py` (调用)

### 新增函数

在 `analyze_signal_effectiveness` 之后新增：

```python
def rolling_limit_up_check(
    ledger_path: Path,
    window: int = 7,
    threshold: float = 0.20,
) -> Dict[str, Any]:
    """Calculate rolling limit-up rate over the last `window` filled trading days.

    Writes result to state/performance_gate.json.
    Returns dict with rolling_lu_rate, alert bool, reason.
    """
    import datetime as _dt
    rows = load_jsonl(ledger_path)
    decisions = [r for r in rows if r.get("record_type") in ("DECISION", "CORRECTION")]
    fills: Dict[str, float] = {}
    for r in rows:
        if r.get("record_type") == "RESULT_FILL":
            key = f"{r.get('date')}:{r.get('symbol')}"
            t1 = _fnum(r.get("t1_return"))
            if t1 is not None:
                fills[key] = t1

    filled = []
    for d in decisions:
        key = f"{d.get('date')}:{d.get('symbol')}"
        t1 = _fnum(d.get("t1_return")) or fills.get(key)
        if t1 is not None:
            filled.append({"date": d.get("date", ""), "t1_return": t1})

    filled.sort(key=lambda x: x["date"], reverse=True)
    recent = filled[:window]
    filled_count = len(recent)

    if filled_count < 3:
        result = {
            "checked_at": _dt.datetime.utcnow().isoformat(),
            "rolling_window": window,
            "rolling_lu_rate": None,
            "alert": False,
            "reason": "INSUFFICIENT_DATA",
            "filled_count": filled_count,
        }
    else:
        lu_count = sum(1 for x in recent if x["t1_return"] >= LIMIT_UP_THRESHOLD)
        rate = round(lu_count / filled_count, 3)
        alert = rate < threshold
        result = {
            "checked_at": _dt.datetime.utcnow().isoformat(),
            "rolling_window": window,
            "rolling_lu_rate": rate,
            "alert": alert,
            "reason": f"{window}日滚动涨停率{rate:.1%} {'< 阈值' + f'{threshold:.1%}' if alert else '>= 阈值' + f'{threshold:.1%}'}",
            "filled_count": filled_count,
        }

    # Write to state/
    state_dir = BASE / "state"
    state_dir.mkdir(exist_ok=True)
    gate_path = state_dir / "performance_gate.json"
    gate_path.write_text(
        __import__("json").dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
```

在 `main()` 中追加 arg：
```python
    ap.add_argument("--rolling-check", action="store_true", dest="rolling_check")
```
并在末尾：
```python
    if args.rolling_check:
        gate = rolling_limit_up_check(args.ledger)
        status = "⚠️ ALERT" if gate["alert"] else "✅ OK"
        print(f"\nRolling gate {status}: {gate['reason']}")
```

在 `job_signal_effectiveness` 调用后追加（scheduler）：
```python
    run_cmd([
        PYTHON, "xiaogu_signal_effectiveness_v0_1.py",
        "--ledger", "forward_paper_ledger_v0_1.jsonl",
        "--rolling-check",
    ], "rolling_gate_check")
```

### 验收
```bash
python xiaogu_signal_effectiveness_v0_1.py \
  --ledger forward_paper_ledger_v0_1.jsonl \
  --rolling-check
# 应输出 Rolling gate 状态，并写入 state/performance_gate.json
ls -la state/performance_gate.json && python -c "import json; print(json.load(open('state/performance_gate.json')))"
```

---

## COMPOSE TASK 4: LC-02 社会情绪采集入调度

### 文件
`xiaogu_social_sentiment.py` (主改)  
`xiaogu_scheduler.py` (追加job)

### xiaogu_social_sentiment.py 追加

在文件末尾 `if __name__ == '__main__':` 之前新增：

```python
def collect_and_store(
    symbols: list,
    db_url: Optional[str] = None,
    trade_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect guba sentiment for symbols and store in DB signals table."""
    import os
    import datetime as _dt
    db_url = db_url or os.environ.get("XIAOGU_DB_URL")
    trade_date = trade_date or _dt.date.today().isoformat()
    results = []
    for sym in symbols[:10]:  # hard cap 10 stocks
        data = scrape_eastmoney_guba(sym)
        if "error" not in data:
            results.append({"symbol": sym, "score": data["sentiment_score"], "meta": data})
        time.sleep(1)

    if not db_url or not results:
        return {"stored": 0, "results": results, "trade_date": trade_date}

    stored = 0
    try:
        import psycopg2, json as _json
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        for r in results:
            cur.execute("""
                INSERT INTO signals(trade_date, symbol, signal_key, signal_value, metadata, created_at)
                VALUES(%s, %s, 'social_sentiment', %s, %s, NOW())
                ON CONFLICT(trade_date, symbol, signal_key) DO UPDATE
                  SET signal_value=EXCLUDED.signal_value,
                      metadata=EXCLUDED.metadata,
                      updated_at=NOW()
            """, (trade_date, r["symbol"], r["score"], _json.dumps(r["meta"])))
            stored += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return {"stored": stored, "results": results, "error": str(e)}

    return {"stored": stored, "results": results, "trade_date": trade_date}


def symbols_from_candidates(base_path: Optional[str] = None) -> list:
    """Read latest candidate bundle and extract symbol list."""
    import glob, json as _json
    base = Path(base_path or ".") / "data" / "forward_candidate_bundles"
    files = sorted(glob.glob(str(base / "*.json")))
    if not files:
        return []
    try:
        bundle = _json.loads(Path(files[-1]).read_text(encoding="utf-8"))
        candidates = bundle.get("candidates") or bundle.get("scored_candidates") or []
        return [str(c.get("symbol") or c.get("code") or "").zfill(6)
                for c in candidates[:10] if c.get("symbol") or c.get("code")]
    except Exception:
        return []
```

同时扩展 `__main__` 块：
```python
if __name__ == '__main__':
    import argparse as _ap
    parser = _ap.ArgumentParser()
    parser.add_argument('--symbols', help='comma-separated symbols')
    parser.add_argument('--from-candidates', action='store_true')
    parser.add_argument('--store', action='store_true')
    args = parser.parse_args()

    if args.from_candidates:
        syms = symbols_from_candidates()
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(',')]
    else:
        syms = ['300059']

    if args.store:
        r = collect_and_store(syms)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        for s in syms:
            print(json.dumps(scrape_eastmoney_guba(s), ensure_ascii=False, indent=2))
```

### scheduler 追加

在 `job_afternoon_scan_and_pick` 之前新增：
```python
def job_sentiment_collect():
    """14:20 — collect social sentiment for today's candidates."""
    if not is_trading_day():
        return
    if os.environ.get("XIAOGU_SENTIMENT_ENABLED") != "1":
        logger.info("[sentiment] disabled, set XIAOGU_SENTIMENT_ENABLED=1 to enable")
        return
    run_cmd([
        PYTHON, "xiaogu_social_sentiment.py",
        "--from-candidates", "--store",
    ], "sentiment_collect")
```

并在 `main()` 注册：
```python
    scheduler.add_job(job_sentiment_collect, CronTrigger(hour=14, minute=20, timezone=TZ),
                      id="sentiment_collect", name="14:20 Sentiment Collect", misfire_grace_time=300)
```

### 验收
```bash
# 验证 collect_and_store 接口（mock CDP 情况下至少不崩溃）
python xiaogu_social_sentiment.py --symbols 300059 
# 验证 symbols_from_candidates 从bundle读取
python -c "from xiaogu_social_sentiment import symbols_from_candidates; print(symbols_from_candidates())"
```

---

## 全局验收（所有TASK完成后）

```bash
cd /workspace/hermes-workspaces/xiaogu

# 1. 单元测试不能新增失败
python -m pytest tests/test_xiaogu_a_share_forward_runner.py -x -q 2>&1 | tail -20

# 2. 日历测试
python -c "
from xiaogu_scheduler import is_trading_day
import datetime
cases = [
    (datetime.date(2026,10,1), False),
    (datetime.date(2026,6,26), True),
    (datetime.date(2026,6,27), False),
    (datetime.date(2026,1,1), False),
]
for d, expected in cases:
    result = is_trading_day(d)
    status = 'PASS' if result == expected else 'FAIL'
    print(f'{status}: {d} → {result} (expected {expected})')
"

# 3. 权重写回 dry run
python xiaogu_signal_effectiveness_v0_1.py \
  --ledger forward_paper_ledger_v0_1.jsonl \
  --min-samples 3 --apply-weights

# 4. 滚动门控
python xiaogu_signal_effectiveness_v0_1.py \
  --ledger forward_paper_ledger_v0_1.jsonl \
  --rolling-check && cat state/performance_gate.json

# 5. scheduler job list
python -c "
import sys; sys.argv=['s']
from xiaogu_scheduler import main
# just check registrations
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo
TZ = ZoneInfo('Asia/Shanghai')
s = BlockingScheduler(timezone=TZ)
import xiaogu_scheduler as xs
xs.main  # importable without error
print('scheduler import OK')
"
```

---

## 禁止清单（mimocode 不得做的事）

- ❌ 修改 `LOCKED_SAFETY` 字典
- ❌ 修改 ledger 历史记录格式
- ❌ 新建除 `xiaogu_theme_collector.py` 以外的新文件（LC-06暂不在本批次）
- ❌ 删除任何已有功能
- ❌ 把默认开关设为开启
- ❌ 修改 runner 核心决策路径
- ❌ 修改 PAPER_ONLY / NO_TRADE 标志
