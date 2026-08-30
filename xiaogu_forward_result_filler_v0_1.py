#!/usr/bin/env python3
"""Append-only five-day profit-window outcome filler."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import signal
from typing import Any, Dict
from urllib.parse import urlencode

from xiaogu_core_alpha import CANONICAL_COST_MODEL, DEFAULT_COST_RATE
from xiaogu_horizon_evaluation import HORIZONS
from scrapy_scanner.runner_v2 import api_get
from xiaogu_utils import append_jsonl, now_iso

BASE = Path(__file__).resolve().parent
FORWARD_LEDGER = BASE / "forward_paper_ledger_v0_1.jsonl"  # audit artifact only
EASTMONEY_KLINE_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_KLINE_FIELDS = (
    "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
)
PRICE_BASIS = "UNADJUSTED"
ENTRY_EXECUTION_MODE = "SIGNAL_TIME_LAST_PRICE"
DEFAULT_EXECUTION_COST_RATE = DEFAULT_COST_RATE
PROFIT_WINDOW_TARGET = 0.02
EVALUATION_DAYS = (1, 2, 3, 4, 5)
REALIZABILITY_LEVEL = "DAILY_BAR_APPROXIMATION"


def _row_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    result = dict(payload) if isinstance(payload, dict) else {}
    result.update({key: value for key, value in row.items() if key != "payload"})
    return result


@contextmanager
def _deadline(seconds: int):
    """Bound providers that do not expose a request-timeout argument."""
    if seconds <= 0:
        yield
        return

    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"HISTORICAL_PROVIDER_TIMEOUT:{seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _eastmoney_secid(symbol: str) -> str:
    code = str(symbol).strip().zfill(6)
    return f"{'1' if code.startswith(('5', '6', '9')) else '0'}.{code}"


def fetch_eastmoney_daily_bars(
    symbol: str,
    *,
    start_date: str,
    end_date: str | None = None,
    timeout: int = 30,
) -> list[Dict[str, Any]]:
    params = {
        "secid": _eastmoney_secid(symbol),
        "klt": "101",
        "fqt": "0",
        "beg": start_date.replace("-", ""),
        "end": (end_date or date.today().isoformat()).replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": EASTMONEY_KLINE_FIELDS,
    }
    payload = api_get(f"{EASTMONEY_KLINE_ENDPOINT}?{urlencode(params)}", timeout=timeout)
    data = payload.get("data") or {}
    if payload.get("rc") not in (0, None) or not data.get("klines"):
        raise RuntimeError(f"EASTMONEY_KLINE_UNAVAILABLE:{symbol}:{payload.get('rc')}")
    bars = []
    for line in data["klines"]:
        parts = str(line).split(",")
        if len(parts) < 5:
            continue
        bars.append({
            "trade_date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]) if len(parts) > 5 and parts[5] not in ("", "-") else None,
            "amount": float(parts[6]) if len(parts) > 6 and parts[6] not in ("", "-") else None,
            "price_basis": PRICE_BASIS,
            "source": "eastmoney_api_daily_kline",
        })
    return bars


def fetch_baostock_daily_bars(
    symbol: str,
    *,
    start_date: str,
    end_date: str | None = None,
    timeout: int = 10,
) -> list[Dict[str, Any]]:
    """Fetch unadjusted historical OHLCV from the installed Baostock client.

    This is a historical-label fallback only. It is never used for T-day
    feature collection or current production scanning.
    """
    import baostock as bs

    code = str(symbol).strip().zfill(6)
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    with _deadline(timeout):
        login = bs.login()
        if str(login.error_code) != "0":
            raise RuntimeError(f"BAOSTOCK_LOGIN_FAILED:{login.error_code}:{login.error_msg}")
        try:
            result = bs.query_history_k_data_plus(
                f"{market}.{code}",
                "date,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date or date.today().isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if str(result.error_code) != "0":
                raise RuntimeError(f"BAOSTOCK_QUERY_FAILED:{code}:{result.error_code}:{result.error_msg}")
            rows = list(result.data)
        finally:
            bs.logout()

    return [
        {
            "trade_date": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]) if row[5] not in (None, "") else None,
            "amount": float(row[6]) if row[6] not in (None, "") else None,
            "price_basis": PRICE_BASIS,
            "source": "baostock_daily_kline",
        }
        for row in rows
        if len(row) >= 7 and all(row[index] not in (None, "") for index in range(5))
    ]


def canonical_future_prices(
    bars: list[Dict[str, Any]],
    *,
    symbol: str = "",
    source_timestamp: str = "",
    price_basis: str = PRICE_BASIS,
    require_volume: bool = False,
) -> list[Dict[str, Any]]:
    """Normalize future OHLC bars without deriving targets from stored decisions."""
    if price_basis != PRICE_BASIS:
        raise ValueError(f"UNSUPPORTED_PRICE_BASIS:{price_basis}")
    normalized = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        if bar.get("price_basis") not in (None, price_basis):
            raise ValueError(f"PRICE_BASIS_MISMATCH:{bar.get('price_basis')}:{price_basis}")
        required = ("open", "high", "low", "close")
        if any(bar.get(key) in (None, "") for key in required):
            continue
        if require_volume and bar.get("volume") in (None, ""):
            continue
        bar_date = str(bar.get("trade_date") or bar.get("date") or "")
        if not bar_date:
            continue
        normalized.append({
            "symbol": str(symbol).zfill(6) if symbol else str(bar.get("symbol") or "").zfill(6),
            "date": bar_date,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": None if bar.get("volume") in (None, "") else float(bar["volume"]),
            "amount": None if bar.get("amount") in (None, "") else float(bar["amount"]),
            "source": str(bar.get("source") or "eastmoney_api_daily_kline"),
            "source_timestamp": source_timestamp or bar.get("source_timestamp") or "",
            "price_basis": price_basis,
        })
    return normalized


def _parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("ENTRY_SIGNAL_TIME_REQUIRED")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def historical_entry_contract(
    snapshot: Dict[str, Any],
    *,
    execution_time: str | None = None,
    execution_mode: str = ENTRY_EXECUTION_MODE,
    price_basis: str = PRICE_BASIS,
) -> Dict[str, Any]:
    """Return the explicit entry contract shared by replay and result filling."""
    if price_basis != PRICE_BASIS:
        raise ValueError(f"UNSUPPORTED_PRICE_BASIS:{price_basis}")
    signal_time = snapshot.get("signal_time") or snapshot.get("source_time") or snapshot.get("as_of")
    signal_dt = _parse_timestamp(signal_time)
    execution_dt = _parse_timestamp(execution_time or signal_time)
    if execution_dt < signal_dt:
        raise ValueError("EXECUTION_BEFORE_SIGNAL")
    price = snapshot.get("execution_price", snapshot.get("price"))
    if price in (None, "") or float(price) <= 0:
        raise ValueError("ENTRY_EXECUTION_PRICE_REQUIRED")
    return {
        "signal_time": signal_dt.isoformat(),
        "execution_time": execution_dt.isoformat(),
        "execution_mode": execution_mode,
        "execution_price": float(price),
        "entry_price": float(price),
        "price_basis": price_basis,
        "entry_price_source": "canonical_snapshot.price",
    }


def entry_contract_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Read the persisted explicit contract; reject records that lack it."""
    contract = record.get("entry_contract")
    if isinstance(contract, dict):
        return historical_entry_contract(
            {
                "signal_time": contract.get("signal_time"),
                "execution_price": contract.get("execution_price", contract.get("entry_price")),
            },
            execution_time=contract.get("execution_time"),
            execution_mode=str(contract.get("execution_mode") or ENTRY_EXECUTION_MODE),
            price_basis=str(contract.get("price_basis") or PRICE_BASIS),
        )
    features = record.get("features_used") or {}
    canonical = features.get("canonical_snapshot") if isinstance(features, dict) else None
    if not isinstance(canonical, dict):
        raise ValueError("ENTRY_CONTRACT_MISSING")
    signal_time = canonical.get("signal_time") or canonical.get("source_time") or record.get("asof_time")
    if not signal_time:
        raise ValueError("ENTRY_CONTRACT_MISSING")
    return historical_entry_contract(canonical, execution_time=signal_time)


def eastmoney_future_close_prices(
    symbol: str,
    *,
    entry_date: str,
    end_date: str | None = None,
) -> Dict[int, float | None]:
    bars = [
        bar for bar in fetch_eastmoney_daily_bars(
            symbol, start_date=entry_date, end_date=end_date,
        )
        if bar["trade_date"] > entry_date
    ]
    return {5: bars[4]["close"] if len(bars) >= 5 else None}


def calculate_horizon_returns(entry_price: float, future_prices: Dict[int, Any]) -> Dict[str, float | None]:
    close = future_prices.get(5)
    return {"future_5d_return": None if close is None else (float(close) - entry_price) / entry_price}


def calculate_horizon_outcomes(
    entry_price: float,
    future_bars: list[Dict[str, Any]],
    *,
    horizons: tuple[int, ...] = HORIZONS,
) -> Dict[str, Any]:
    """Build T+1..T+5 outcomes with an explicit daily-bar approximation level."""
    if tuple(horizons) != HORIZONS:
        raise ValueError(f"UNSUPPORTED_HORIZON:{tuple(horizons)}")
    bars = list(future_bars or [])[:5]
    outcomes: Dict[str, Any] = {
        "profit_window_target": PROFIT_WINDOW_TARGET,
        "execution_cost_rate": DEFAULT_EXECUTION_COST_RATE,
        "cost_model_version": CANONICAL_COST_MODEL["version"],
        "all_in_transaction_cost": DEFAULT_EXECUTION_COST_RATE,
        "daily_outcomes": [],
        "days": {str(day): {} for day in EVALUATION_DAYS},
        "max_daily_bar_profit_opportunity_5d": None,
        "first_profit_day": None,
        "time_to_profit": None,
        "max_mae_5d": None,
        "net_profit_window": None,
        "profit_window": False,
        "future_5d_ohlc_coverage": False,
        "future_5d_volume_coverage": False,
        "data_status": "INVALID",
        "realizability_level": REALIZABILITY_LEVEL,
        "outcome_complete": False,
        "available_days": 0,
        "partial_status": "NO_DATA",
        "execution_assumptions": {
            "liquidity_checked": False,
            "slippage_included": False,
            "spread_included": False,
            "market_impact_included": False,
            "transaction_cost_rate": DEFAULT_EXECUTION_COST_RATE,
            "all_in_transaction_cost": DEFAULT_EXECUTION_COST_RATE,
            "cost_model_version": CANONICAL_COST_MODEL["version"],
        },
    }
    for day in EVALUATION_DAYS:
        outcomes.update({
            f"future_{day}d_date": None,
            f"future_{day}d_open": None,
            f"future_{day}d_high": None,
            f"future_{day}d_low": None,
            f"future_{day}d_close": None,
            f"future_{day}d_return": None,
            f"future_{day}d_mfe": None,
            f"future_{day}d_mae": None,
            f"future_{day}d_net_return": None,
        })
    if entry_price <= 0 or not bars:
        return outcomes

    daily = []
    for day, bar in enumerate(bars, 1):
        high = float(bar["high"])
        low = float(bar["low"])
        bar_opportunity = (high - entry_price) / entry_price - DEFAULT_EXECUTION_COST_RATE
        daily.append({
            "day": day,
            "date": bar.get("trade_date") or bar.get("date"),
            "open": float(bar["open"]), "high": high, "low": low, "close": float(bar["close"]),
            "return": (float(bar["close"]) - entry_price) / entry_price,
            "net_return": (float(bar["close"]) - entry_price) / entry_price - DEFAULT_EXECUTION_COST_RATE,
            "daily_bar_profit_opportunity": bar_opportunity,
            "mae": (low - entry_price) / entry_price,
            "capital_state": bar.get("capital_state", "UNKNOWN"),
            "repricing_state": bar.get("repricing_state", "UNKNOWN"),
        })
        outcomes["days"][str(day)] = {
            "date": bar.get("trade_date") or bar.get("date"),
            "open": float(bar["open"]),
            "high": high,
            "low": low,
            "close": float(bar["close"]),
            "return": (float(bar["close"]) - entry_price) / entry_price,
            "mfe": (high - entry_price) / entry_price,
            "mae": (low - entry_price) / entry_price,
            "net_return": (float(bar["close"]) - entry_price) / entry_price - DEFAULT_EXECUTION_COST_RATE,
            "daily_bar_profit_opportunity": bar_opportunity,
            "volume": bar.get("volume"),
            "amount": bar.get("amount"),
            "source": bar.get("source", "unknown"),
            "source_timestamp": bar.get("source_timestamp", ""),
            "price_basis": bar.get("price_basis", PRICE_BASIS),
        }
        outcomes.update({
            f"future_{day}d_date": bar.get("trade_date") or bar.get("date"),
            f"future_{day}d_open": float(bar["open"]),
            f"future_{day}d_high": high,
            f"future_{day}d_low": low,
            f"future_{day}d_close": float(bar["close"]),
            f"future_{day}d_return": (float(bar["close"]) - entry_price) / entry_price,
            f"future_{day}d_mfe": (high - entry_price) / entry_price,
            f"future_{day}d_mae": (low - entry_price) / entry_price,
            f"future_{day}d_net_return": (float(bar["close"]) - entry_price) / entry_price - DEFAULT_EXECUTION_COST_RATE,
        })
    outcomes["available_days"] = len(daily)
    outcomes["data_status"] = "COMPLETE" if len(daily) >= 5 else "PARTIAL"
    outcomes["partial_status"] = "PARTIAL" if len(daily) < 5 else "COMPLETE"
    outcomes["daily_outcomes"] = daily
    if len(daily) < 5:
        return outcomes

    profitable = [item for item in daily if item["daily_bar_profit_opportunity"] >= PROFIT_WINDOW_TARGET]
    max_profit = max(item["daily_bar_profit_opportunity"] for item in daily)
    min_mae = min(item["mae"] for item in daily)
    outcomes.update({
        "daily_outcomes": daily,
        "max_daily_bar_profit_opportunity_5d": max_profit,
        "first_profit_day": profitable[0]["day"] if profitable else None,
        "time_to_profit": profitable[0]["day"] if profitable else None,
        "max_mae_5d": min_mae,
        "net_profit_window": max(0.0, max(item["net_return"] for item in daily)),
        "profit_window": bool(profitable),
        "future_5d_date": bars[-1].get("trade_date") or bars[-1].get("date"),
        "future_5d_open": float(bars[0]["open"]),
        "future_5d_high": max(float(bar["high"]) for bar in bars),
        "future_5d_low": min(float(bar["low"]) for bar in bars),
        "future_5d_close": float(bars[-1]["close"]),
        "future_5d_return": (float(bars[-1]["close"]) - entry_price) / entry_price,
        "future_5d_mfe": (max(float(bar["high"]) for bar in bars) - entry_price) / entry_price,
        "future_5d_mae": min_mae,
        "future_5d_net_return": (float(bars[-1]["close"]) - entry_price) / entry_price - DEFAULT_EXECUTION_COST_RATE,
        "future_5d_ohlc_coverage": True,
        "future_5d_volume_coverage": all(bar.get("volume") not in (None, "") for bar in bars),
        "data_status": "COMPLETE",
        "outcome_complete": True,
    })
    return outcomes


def eastmoney_future_bars(
    symbol: str,
    *,
    entry_date: str,
    end_date: str | None = None,
) -> list[Dict[str, Any]]:
    return [
        bar
        for bar in fetch_eastmoney_daily_bars(symbol, start_date=entry_date, end_date=end_date)
        if bar["trade_date"] > entry_date
    ]


def _entry_price(record: Dict[str, Any]) -> float:
    return float(entry_contract_from_record(record)["entry_price"])


def append_result(
    record: Dict[str, Any],
    future_prices: Dict[int, Any] | None = None,
    future_bars: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    canonical = (record.get("features_used") or {}).get("canonical_snapshot", {})
    contract = entry_contract_from_record(record)
    entry = float(contract["entry_price"])
    if future_bars is not None:
        future_bars = canonical_future_prices(
            future_bars,
            symbol=str(record.get("symbol") or canonical.get("symbol") or ""),
            price_basis=str(contract["price_basis"]),
        )
    outcomes = (
        calculate_horizon_outcomes(entry, future_bars)
        if future_bars is not None
        else calculate_horizon_returns(entry, future_prices or {})
    )
    decision_id = str(record.get("id") or record.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("DECISION_ID_REQUIRED")
    result = {
        "record_type": "RESULT",
        "decision_id": decision_id,
        "date": record.get("date"),
        "symbol": record.get("symbol"),
        "rule_version": record.get("rule_version"),
        "production_run_id": record.get("production_run_id"),
        "entry_price": entry,
        "entry_price_source": contract["entry_price_source"],
        "entry_contract": contract,
        "price_basis": contract["price_basis"],
        "outcome_boundary": "OUTCOMES_ENTER_AFTER_PRODUCTION_DECISION",
        **outcomes,
        "actual_5d_return": outcomes.get("future_5d_return"),
        "actual_5d_mfe": outcomes.get("future_5d_mfe"),
        "actual_5d_mae": outcomes.get("future_5d_mae"),
        "result_status": "SETTLED" if outcomes.get("outcome_complete") else "PENDING",
        "result_filled_at": now_iso(),
    }
    if outcomes.get("outcome_complete"):
        result["post_trade_review"] = build_post_trade_review(record, outcomes)
    return result


def build_post_trade_review(record: Dict[str, Any], outcomes: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a settled window as research memory, never as new alpha."""
    alpha = (record.get("features_used") or {}).get("core_alpha") or {}
    success = bool(outcomes.get("profit_window"))
    if success:
        levels = (alpha.get("capital_convergence") or {}).get("levels") or {}
        priority = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        strongest = max(levels, key=lambda key: priority.get(levels[key], 0), default="")
        attribution = {
            "institution": "INSTITUTION_LED_REPRICING",
            "main_force": "MAIN_FORCE_LED_REPRICING",
            "hot_money": "HOT_MONEY_LED_REPRICING",
        }.get(strongest, "CAPITAL_CONVERGENCE" if (alpha.get("capital_convergence") or {}).get("status") == "CONVERGENCE" else "SUPPLY_ABSORPTION")
    else:
        raw = (record.get("features_used") or {}).get("canonical_snapshot") or {}
        risk = (record.get("features_used") or {}).get("feature_vector", {}).get("RISK", {})
        execution = (record.get("features_used") or {}).get("feature_vector", {}).get("EXECUTION", {})
        execution_feasibility = execution.get("execution_feasibility")
        supply_absorption = alpha.get("supply_absorption")
        future_buyer_capacity = alpha.get("future_buyer_capacity")
        pricing_gap = alpha.get("pricing_gap")
        reflexivity_break_risk = alpha.get("reflexivity_break_risk")
        attribution = next((reason for reason, condition in (
            ("EXECUTION_FAILURE", execution_feasibility is not None and float(execution_feasibility) < 0.35),
            ("CAPITAL_EXIT", (alpha.get("capital_convergence") or {}).get("status") == "CONFLICT"),
            ("DISTRIBUTION_MISREAD", str(alpha.get("repricing_state") or "") == "DISTRIBUTION"),
            ("SUPPLY_NOT_ABSORBED", supply_absorption is not None and float(supply_absorption) < 0.35),
            ("NO_FUTURE_BUYER", future_buyer_capacity is not None and float(future_buyer_capacity) <= 0),
            ("PRICING_GAP_FALSE", pricing_gap is not None and float(pricing_gap) < 0.35),
            ("REFLEXIVITY_FAILED", reflexivity_break_risk is not None and float(reflexivity_break_risk) >= 0.70),
            ("INDUSTRY_THESIS_WRONG", bool(raw.get("industry_thesis_wrong") or risk.get("industry_thesis_wrong"))),
            ("BUSINESS_THESIS_WRONG", bool(raw.get("business_thesis_wrong") or risk.get("business_thesis_wrong"))),
            ("MARKET_REGIME_FAILURE", bool(raw.get("market_regime_failure") or risk.get("market_regime_failure"))),
            ("FALSE_ACCUMULATION", str(alpha.get("repricing_state") or "") == "ACCUMULATION" and not outcomes.get("profit_window")),
        ) if condition), "MODEL_ERROR")
    return {
        "status": "SUCCESS" if success else "FAILURE",
        "attribution": attribution,
        "thesis": record.get("thesis") or alpha.get("thesis"),
        "profit_window_day": outcomes.get("first_profit_day"),
        "maximum_favorable_excursion": outcomes.get("future_5d_mfe"),
        "maximum_adverse_excursion": outcomes.get("max_mae_5d"),
        "exit_reason": record.get("exit_reason") or "MAX_HOLDING_BOUNDARY_REVIEW",
    }


def _persist_and_append_result(result: Dict[str, Any]) -> Dict[str, Any]:
    from xiaogu_db import record_returns
    try:
        record_returns(
            str(result["date"]),
            str(result["symbol"]),
            result,
            decision_id=str(result.get("decision_id") or result.get("id") or ""),
        )
        result["database_persistence"] = {"status": "PASS"}
    except Exception as exc:
        result["database_persistence"] = {"status": "FAILED", "error": repr(exc)}
        raise
    append_jsonl(FORWARD_LEDGER, result)
    result["audit_persistence"] = {"status": "PASS"}
    try:
        from xiaogu_forward_paper_recorder_v0_1 import update_trade_memory
        result["memory_path"] = update_trade_memory(result)
    except OSError as exc:
        result["memory_error"] = repr(exc)
    return result


def _has_new_outcome(result: Dict[str, Any], prior: Dict[str, Any] | None) -> bool:
    prior = prior or {}
    result_days = result.get("available_days")
    prior_days = prior.get("available_days")
    return int(0 if result_days is None else result_days) > int(0 if prior_days is None else prior_days)


def fill_pending_results(*, end_date: str | None = None) -> Dict[str, Any]:
    """Fill DB decisions; JSONL receives only the resulting audit artifact."""
    from xiaogu_db import fetch_picks, fetch_returns

    records = []
    for row in fetch_picks():
        record = _row_payload(row)
        action = str(record.get("action") or record.get("state") or record.get("decision") or "").upper()
        if action not in {"BUY", "HOLD", "REDUCE", "SELL"}:
            continue
        record["record_type"] = "DECISION"
        record["decision"] = action
        record["id"] = str(record.get("decision_id") or record.get("id") or "")
        record["date"] = str(record.get("date") or record.get("trade_date") or "")
        records.append(record)
    prior_results: Dict[str, Dict[str, Any]] = {}
    for row in fetch_returns():
        record = _row_payload(row)
        decision_id = str(record.get("decision_id") or "").strip()
        if decision_id and decision_id not in prior_results:
            prior_results[decision_id] = record
    filled = []
    errors = []
    for record in records:
        decision_id = str(record.get("id") or record.get("decision_id") or "")
        if not decision_id:
            errors.append({"decision_id": "", "error": "DECISION_ID_REQUIRED"})
            continue
        try:
            bars = eastmoney_future_bars(
                str(record["symbol"]), entry_date=str(record["date"]), end_date=end_date,
            )
            result = append_result(record, future_bars=bars)
            if _has_new_outcome(result, prior_results.get(decision_id)):
                filled.append(_persist_and_append_result(result))
        except Exception as exc:
            errors.append({"decision_id": decision_id, "error": repr(exc)})
    return {"filled": len(filled), "results": filled, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-json", default="")
    parser.add_argument("--pending", action="store_true", help="append newly available outcomes for ledger decisions")
    parser.add_argument("--end-date", default="")
    args = parser.parse_args()
    if args.pending:
        print(json.dumps(fill_pending_results(end_date=args.end_date or None), ensure_ascii=False, default=str))
        return
    if not args.record_json:
        parser.error("--record-json is required unless --pending is used")
    record = json.loads(Path(args.record_json).read_text(encoding="utf-8"))
    bars = eastmoney_future_bars(
        str(record["symbol"]),
        entry_date=str(record["date"]),
        end_date=args.end_date or None,
    )
    result = append_result(record, future_bars=bars)
    _persist_and_append_result(result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
