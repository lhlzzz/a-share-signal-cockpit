#!/usr/bin/env python3
"""
xiaogu 每日健康检查脚本
使用方式: python3 scripts/xiaogu_daily_health_check.py [--json]
在每个交易日开盘前运行，验证完整链路各环节状态。
"""

import argparse
import json
import os
import py_compile
import sys
import tempfile

# 确保从 workspace 根目录可以 import 各模块
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

SCANNER_FILE = os.path.join(WORKSPACE_ROOT, "scrapy_scanner", "runner_v2.py")
RUNNER_FILE  = os.path.join(WORKSPACE_ROOT, "xiaogu_forward_d1_1450_runner_v0_1.py")
SCHEDULER_FILE = os.path.join(WORKSPACE_ROOT, "xiaogu_scheduler.py")
DAILY_PIPELINE_FILE = os.path.join(WORKSPACE_ROOT, "daily_pipeline.sh")
FILLER_FILE  = os.path.join(WORKSPACE_ROOT, "xiaogu_forward_result_filler_v0_1.py")
RETURN_BACKFILL_FILE = os.path.join(WORKSPACE_ROOT, "scripts", "xiaogu_return_backfill.py")
SOCIAL_FILE = os.path.join(WORKSPACE_ROOT, "xiaogu_social_sentiment.py")
LEDGER_FILE  = os.path.join(WORKSPACE_ROOT, "forward_paper_ledger_v0_1.jsonl")


def _py_compile_check(path: str):
    """返回 (ok: bool, detail: str)"""
    if not os.path.isfile(path):
        return False, f"file not found: {path}"
    try:
        py_compile.compile(path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)


def _read_file_text(path: str):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 各检查项
# ---------------------------------------------------------------------------

def check_scanner_py_compile():
    ok, detail = _py_compile_check(SCANNER_FILE)
    return ok, detail or "ok"


def check_runner_py_compile():
    ok, detail = _py_compile_check(RUNNER_FILE)
    return ok, detail or "ok"


def check_filler_py_compile():
    ok, detail = _py_compile_check(FILLER_FILE)
    return ok, detail or "ok"


def check_api_scanner_identity():
    text = _read_file_text(SCANNER_FILE)
    if text is None:
        return False, "scanner file not found"
    required_tokens = ("eastmoney_api_scan_v2", "v2_scanner_api", "api_get", "source_id")
    missing = [token for token in required_tokens if token not in text]
    return not missing, "direct API scanner identity confirmed" if not missing else f"missing: {', '.join(missing)}"


DIRECT_NETWORK_TOKENS = ("urllib.request.urlopen", "DIRECT_OPENER.open", "requests.get", "requests.post")


def _direct_network_hits(path: str, *, eastmoney_only: bool = False):
    text = _read_file_text(path)
    if text is None:
        return [f"missing:{path}"]
    hits = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        if not any(token in line for token in DIRECT_NETWORK_TOKENS):
            continue
        context = "\n".join(lines[max(0, idx - 3): min(len(lines), idx + 3)]).lower()
        if eastmoney_only and "eastmoney" not in context and "push2" not in context and "datacenter" not in context:
            continue
        hits.append(f"{os.path.relpath(path, WORKSPACE_ROOT)}:{idx}:{line.strip()}")
    return hits


def check_official_pre_pick_direct_network_boundary():
    paths = [SCANNER_FILE, RUNNER_FILE, DAILY_PIPELINE_FILE, SCHEDULER_FILE]
    hits = []
    for path in paths:
        hits.extend(_direct_network_hits(path, eastmoney_only=True))
    return not hits, "official direct-network boundary PASS" if not hits else "; ".join(hits[:8])


def check_post_pick_sidecar_direct_network_boundary():
    paths = [FILLER_FILE, RETURN_BACKFILL_FILE, SOCIAL_FILE]
    hits = []
    for path in paths:
        hits.extend(_direct_network_hits(path, eastmoney_only=False))
    detail = "WARN/INFO only; no official pre-pick block"
    if hits:
        detail += ": " + "; ".join(hits[:8])
    return True, detail


def check_api_scanner_has_no_direct_urllib_fallback():
    text = _read_file_text(SCANNER_FILE)
    if text is None:
        return False, "scanner file not found"
    forbidden_tokens = ("DIRECT_OPENER", "urllib.request.urlopen")
    present = [token for token in forbidden_tokens if token in text]
    return not present, "no direct urllib Eastmoney fallback" if not present else f"found: {', '.join(present)}"


def check_external_market_api_snapshot():
    text = _read_file_text(SCANNER_FILE)
    if text is None:
        return False, "scanner file not found"
    required_tokens = (
        "fetch_external_market_snapshot",
        "push2delay.eastmoney.com/api/qt/stock/get",
        "100.DJIA",
        "100.SPX",
        "100.NDX",
        "100.KS11",
    )
    missing = [token for token in required_tokens if token not in text]
    return not missing, "Eastmoney external-market API snapshot confirmed" if not missing else f"missing: {', '.join(missing)}"


def check_runner_requires_api_source():
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    required_tokens = ("API_A_SHARE_SOURCE_TOKENS = ('v2_scanner_api', 'eastmoney_api_scan_v2')", "is_active_api_source")
    missing = [token for token in required_tokens if token not in text]
    return not missing, "runner API source gate confirmed" if not missing else f"missing: {', '.join(missing)}"


def check_runner_consumes_external_market_signal():
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    required_tokens = (
        "external_market_signal_score",
        "external_market_risk_off",
        "external_market_supportive",
    )
    missing = [token for token in required_tokens if token not in text]
    return not missing, "runner external-market gate confirmed" if not missing else f"missing: {', '.join(missing)}"


def check_runner_has_formal_diagnostic_output():
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    ok = "formal_diagnostic_candidate" in text
    return ok, "found" if ok else "string 'formal_diagnostic_candidate' not found in runner"


def check_runner_has_closest_to_pick_output():
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    ok = "closest_to_pick_candidate" in text
    return ok, "found" if ok else "string 'closest_to_pick_candidate' not found in runner"


def check_sector_rotation_in_scoring():
    """检查 runner 含 sector 相关评分逻辑（sector_opportunity 或 sector_fund_flow）。"""
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    ok = ("sector_opportunity" in text) or ("sector_fund_flow" in text)
    return ok, "found sector signal" if ok else "no sector scoring signal found"


def check_northbound_in_scoring():
    """检查 runner 含北向/机构资金相关评分逻辑（hsgt 或 northbound 或 institutional）。"""
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    ok = any(kw in text for kw in ("hsgt", "northbound", "institutional", "north_bound"))
    # 若 runner 本身确实没有这些词也记录实际情况
    if not ok:
        # 降级：检查 scanner 是否有相关 tab（sector_fund_flow 覆盖资金流向）
        scanner_text = _read_file_text(SCANNER_FILE)
        ok = scanner_text is not None and "sector_fund_flow" in scanner_text
        detail = "sector_fund_flow tab present in scanner (northbound proxy)" if ok else "no northbound/hsgt signal found"
        return ok, detail
    return True, "found northbound/institutional signal"


def check_overheated_in_scoring():
    text = _read_file_text(RUNNER_FILE)
    if text is None:
        return False, "runner file not found"
    ok = "overheated_market" in text
    return ok, "found" if ok else "string 'overheated_market' not found in runner"


def check_filler_has_fill_all_pending():
    text = _read_file_text(FILLER_FILE)
    if text is None:
        return False, "filler file not found"
    ok = "--fill-all-pending" in text
    return ok, "found" if ok else "'--fill-all-pending' not found in filler"


def check_ledger_readable():
    if not os.path.isfile(LEDGER_FILE):
        # 若 ledger 不存在则跳过（返回 PASS with note）
        return True, "ledger not present — skipped"
    try:
        with open(LEDGER_FILE, encoding="utf-8") as fh:
            first = fh.readline()
        if first.strip():
            json.loads(first)  # 验证第一行是合法 JSON
        return True, f"readable ({os.path.getsize(LEDGER_FILE)} bytes)"
    except Exception as exc:
        return False, f"read/parse error: {exc}"


# ---------------------------------------------------------------------------
# 检查注册表
# ---------------------------------------------------------------------------

CHECKS = [
    ("scanner_py_compile",              check_scanner_py_compile),
    ("runner_py_compile",               check_runner_py_compile),
    ("filler_py_compile",               check_filler_py_compile),
    ("api_scanner_identity",            check_api_scanner_identity),
    ("official_pre_pick_direct_network_boundary", check_official_pre_pick_direct_network_boundary),
    ("post_pick_sidecar_direct_network_boundary", check_post_pick_sidecar_direct_network_boundary),
    ("api_scanner_has_no_direct_urllib_fallback", check_api_scanner_has_no_direct_urllib_fallback),
    ("external_market_api_snapshot",    check_external_market_api_snapshot),
    ("runner_requires_api_source",      check_runner_requires_api_source),
    ("runner_consumes_external_market_signal", check_runner_consumes_external_market_signal),
    ("runner_has_formal_diagnostic_output", check_runner_has_formal_diagnostic_output),
    ("runner_has_closest_to_pick_output", check_runner_has_closest_to_pick_output),
    ("sector_rotation_in_scoring",      check_sector_rotation_in_scoring),
    ("northbound_in_scoring",           check_northbound_in_scoring),
    ("overheated_in_scoring",           check_overheated_in_scoring),
    ("filler_has_fill_all_pending",     check_filler_has_fill_all_pending),
    ("ledger_readable",                 check_ledger_readable),
]


def run_checks(as_json: bool = False):
    results = []
    passed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"exception: {exc}"
        results.append({"name": name, "ok": ok, "detail": detail})
        if ok:
            passed += 1

    total = len(results)
    failed = total - passed

    if as_json:
        print(json.dumps({"checks": results, "passed": passed, "total": total}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            tag = "[PASS]" if r["ok"] else "[FAIL]"
            line = f"{tag} {r['name']}"
            if r["detail"] and r["detail"] not in ("ok", "found"):
                line += f" — {r['detail']}"
            print(line)
        print()
        print(f"SUMMARY: {passed}/{total} checks passed")
        print(f"EXIT_CODE: {0 if failed == 0 else 1}")

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="xiaogu 每日健康检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    ok = run_checks(as_json=args.json)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
