#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native repo runtime wrappers for xiaogu paper-only integration."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
EXTERNAL = BASE / 'external_research'
SHARED_EXTERNAL_REPOS = ROOT / 'tools' / 'external' / 'repos'
EVIDENCE_ROOT = BASE / 'data' / 'native_repo_runtime' / 'v0_1'
TODAY = dt.date.today().isoformat()

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env', override=True)
except ImportError:
    pass

REPO_PATHS = {
    'tradingagent_a': EXTERNAL / 'tradingagent_a',
    'VEI': BASE,
    'Qlib': SHARED_EXTERNAL_REPOS / 'qlib',
    'QuantDinger': SHARED_EXTERNAL_REPOS / 'QuantDinger',
    'UZI_Skill': EXTERNAL / 'uzi_skill',
    'Kaixin_Factors': BASE,  # 凯心因子集成
    'MiMo_Reasoning': BASE,
}
ACTIVE_REPO_ORDER = (
    'tradingagent_a',
    'VEI',
    'Qlib',
    'UZI_Skill',
    'Kaixin_Factors',
)

_SCORE_CAPS = {
    'tradingagent_a': {'min': -1.0, 'max': 1.0},
    'VEI': {'min': -2.0, 'max': 2.0},
    'Qlib': {'min': -1.5, 'max': 1.5},
    'UZI_Skill': {'min': -1.0, 'max': 1.0},
    'Kaixin_Factors': {'min': -1.0, 'max': 1.0},
}

_MODULE_CACHE: Dict[str, Any] = {}
_RUN_CACHE: Dict[str, Tuple[Dict[str, Any], Optional[str]]] = {}
_COMMIT_CACHE: Dict[str, Optional[str]] = {}


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, '', '-'):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def candidate_symbol(candidate: Dict[str, Any]) -> str:
    symbol = candidate.get('symbol') or candidate.get('code') or ''
    return str(symbol).zfill(6) if symbol else ''


def candidate_date(candidate: Dict[str, Any]) -> str:
    for key in ('signal_date', 'date'):
        value = candidate.get(key)
        if value:
            return str(value)[:10]
    return ''


def is_live_candidate(candidate: Dict[str, Any]) -> bool:
    date = candidate_date(candidate)
    return bool(date and date == TODAY)


def board_from_code(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(('300', '301')):
        return 'chuangye'
    if c.startswith(('688', '689')):
        return 'kechuang'
    if c.startswith(('430', '431', '832', '833', '834', '835', '836', '837', '838', '839', '870', '871', '872', '873', '874', '875', '876', '877', '878', '879', '920')):
        return 'beijing'
    return 'main'


def small_account_buyable(
    code: str,
    price: float,
    one_lot_cost_cap: float | None = None,
    available_cash: float | None = None,
) -> Tuple[bool, Optional[str]]:
    if price <= 0:
        return False, 'price_invalid'
    cap = one_lot_cost_cap if one_lot_cost_cap is not None else available_cash
    if cap is not None and cap > 0 and price * 100 > cap:
        return False, 'one_lot_cost_gt_cap'
    return True, None


_SCAN_SUMMARY_CACHE: Dict[str, Any] = {}


def _load_scan_summary() -> Dict[str, Any]:
    """Load market-level data from scan summary as fallback."""
    if _SCAN_SUMMARY_CACHE:
        return _SCAN_SUMMARY_CACHE
    live_scan_dir = BASE / 'data' / 'live_scan'
    # Read only today's canonical direct API artifacts.
    dates_to_try = [TODAY]
    for date_str in dates_to_try:
        for subdir in ('eastmoney_scan_afternoon', 'eastmoney_scan_morning'):
            summary_path = live_scan_dir / date_str / subdir / 'xiaogu_scan_summary_runner.json'
            if summary_path.exists():
                try:
                    with open(summary_path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                    _SCAN_SUMMARY_CACHE.update({
                        'market_breadth_up_pct': data.get('market_breadth_up_pct'),
                        'market_limitups': data.get('market_limitups'),
                        'market_bigups': data.get('market_bigups'),
                        'universe_quote_count': data.get('universe_quote_count'),
                    })
                    return _SCAN_SUMMARY_CACHE
                except Exception:
                    pass
    return _SCAN_SUMMARY_CACHE


def web_evidence(candidate: Dict[str, Any]) -> Dict[str, Any]:
    sm = _load_scan_summary()
    return {
        'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
        'candidate_source': candidate.get('candidate_source') or candidate.get('source') or candidate.get('score_asof_provenance'),
        'source_time': candidate.get('source_time') or candidate.get('data_cutoff') or candidate.get('asof_time') or candidate.get('signal_date'),
        'evidence_path': candidate.get('evidence_path') or candidate.get('raw_snapshot_path') or candidate.get('raw_data_snapshot_path'),
        'source_row_hash': candidate.get('source_row_hash'),
        'fields': {
            'code': candidate_symbol(candidate),
            'price': fnum(candidate.get('price')),
            'signal_pct': fnum(candidate.get('signal_pct')),
            'rank': fnum(candidate.get('rank'), 999.0),
            'amount_pctile_rule': fnum(candidate.get('amount_pctile_rule')),
            'market_breadth_up_pct': fnum(candidate.get('market_breadth_up_pct')) or fnum(sm.get('market_breadth_up_pct')),
            'market_limitups': fnum(candidate.get('market_limitups')) or fnum(sm.get('market_limitups')),
            'market_bigups': fnum(candidate.get('market_bigups')) or fnum(sm.get('market_bigups')),
            'theme_strength': fnum(candidate.get('theme_strength')),
            'turnover_rate': fnum(candidate.get('turnover_rate'), -1.0),
            'volume_ratio': fnum(candidate.get('volume_ratio'), -1.0),
            'net_inflow_main': fnum(candidate.get('net_inflow_main'), 0.0),
            'close_position_score': fnum(candidate.get('close_position_score'), -1.0),
        },
    }


def has_web_evidence(candidate: Dict[str, Any]) -> bool:
    e = web_evidence(candidate)
    return bool(e.get('source_time') or e.get('evidence_path') or e.get('source_row_hash') or e.get('candidate_source'))


def _candidate_layers(candidate: Dict[str, Any]) -> List[str]:
    layers = candidate.get('source_layers') or []
    if isinstance(layers, str):
        return [layers]
    if isinstance(layers, list):
        return [str(layer) for layer in layers]
    return []


def _structured_component(candidate: Dict[str, Any], key: str, default: float = 0.0) -> float:
    for container_key in ('structured_component_details', 'component_details'):
        details = candidate.get(container_key)
        if isinstance(details, dict) and details.get(key) not in (None, ''):
            return fnum(details.get(key), default)
    return default


def component_score(count: float, cap: float) -> float:
    return round(min(1.0, count / cap), 4) if cap else 0.0


def compute_vei_features(candidate: Dict[str, Any]) -> Dict[str, float]:
    signal_pct = fnum(candidate.get('signal_pct'), 0.0)
    close_position_score = fnum(candidate.get('close_position_score'), -1.0)
    volume_ratio = fnum(candidate.get('volume_ratio'), 0.0)
    fund_pctile = fnum(candidate.get('full_universe_fund_pctile'), fnum(candidate.get('amount_pctile_rule'), 0.0))
    layers = set(_candidate_layers(candidate))

    persisted_pre = _structured_component(candidate, 'pre_limitup_anomaly', None)  # type: ignore[arg-type]
    persisted_weak = _structured_component(candidate, 'weak_to_strong_reversal', None)  # type: ignore[arg-type]
    persisted_first = _structured_component(candidate, 'first_board_pre_signal', None)  # type: ignore[arg-type]
    persisted_sector = _structured_component(candidate, 'sector_opportunity_score', None)  # type: ignore[arg-type]
    if persisted_pre is not None and persisted_weak is not None and persisted_first is not None:
        return {
            'pre_limitup_anomaly': persisted_pre,
            'weak_to_strong_reversal': persisted_weak,
            'first_board_pre_signal': persisted_first,
            'sector_opportunity_score': persisted_sector if persisted_sector is not None else 0.0,
        }

    pre_limitup_anomaly = 0.0
    if 5.0 <= signal_pct < 9.5 and close_position_score >= 0.70:
        pre_limitup_anomaly = min(1.0, (signal_pct - 5.0) / 4.5 * 0.55 + close_position_score * 0.25 + fund_pctile * 0.20)

    weak_to_strong_reversal = 0.0
    if 'L4_UNDERWATER_RECOVERY' in layers:
        weak_to_strong_reversal = min(1.0, component_score(volume_ratio, 3.0) * 0.35 + fund_pctile * 0.35 + max(close_position_score, 0.0) * 0.30)

    first_board_pre_signal = max(pre_limitup_anomaly, weak_to_strong_reversal * 0.85)
    if 'L4_PRE_BREAKOUT' in layers and first_board_pre_signal == 0.0:
        first_board_pre_signal = min(1.0, fund_pctile * 0.45 + max(close_position_score, 0.0) * 0.35 + component_score(volume_ratio, 3.0) * 0.20)

    return {
        'pre_limitup_anomaly': round(pre_limitup_anomaly, 4),
        'weak_to_strong_reversal': round(weak_to_strong_reversal, 4),
        'first_board_pre_signal': round(first_board_pre_signal, 4),
        'sector_opportunity_score': 0.0,
    }


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f'\n...<truncated {len(text) - limit} chars>'


def _evidence_path(repo_name: str, candidate: Dict[str, Any], label: str) -> Path:
    date = candidate_date(candidate) or TODAY
    symbol = candidate_symbol(candidate) or 'UNKNOWN'
    ts = dt.datetime.now().strftime('%H%M%S_%f')
    return EVIDENCE_ROOT / repo_name / date / f'{symbol}_{label}_{ts}.json'


def write_evidence(repo_name: str, candidate: Dict[str, Any], label: str, payload: Dict[str, Any]) -> str:
    path = _evidence_path(repo_name, candidate, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


def repo_commit(repo_path: Path) -> Optional[str]:
    key = str(repo_path)
    if key in _COMMIT_CACHE:
        return _COMMIT_CACHE[key]
    if not repo_path.exists():
        _COMMIT_CACHE[key] = None
        return None
    try:
        proc = subprocess.run(['git', '-C', str(repo_path), 'rev-parse', 'HEAD'], text=True, capture_output=True, timeout=10)
    except Exception:
        _COMMIT_CACHE[key] = None
        return None
    _COMMIT_CACHE[key] = proc.stdout.strip() if proc.returncode == 0 else None
    return _COMMIT_CACHE[key]


def adapter_record(
    repo_name: str,
    status: str,
    runtime_status: str,
    signals: Optional[Dict[str, Any]] = None,
    score_delta: float = 0.0,
    score_eligible: bool = False,
    risk_flags: Optional[List[str]] = None,
    evidence_paths: Optional[List[str]] = None,
    external_api_used: bool = False,
    llm_used: bool = False,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    cap = _SCORE_CAPS.get(repo_name, {'min': 0.0, 'max': 0.0})
    return {
        'repo_name': repo_name,
        'status': status,
        'runtime_status': runtime_status,
        'signals': signals or {},
        'risk_flags': risk_flags or [],
        'confidence': confidence,
        'used_future_fields': False,
        'external_api_used': external_api_used,
        'llm_used': llm_used,
        'paper_only': True,
        'no_trade': True,
        'production_ready': False,
        'score_eligible': score_eligible,
        'can_trade': False,
        'can_promote': False,
        'score_cap': cap,
        'score_delta': round(clamp(float(score_delta), cap['min'], cap['max']), 4),
        'evidence_paths': evidence_paths or [],
    }


def repo_contribution_status(adapter: Dict[str, Any]) -> str:
    repo_name = str(adapter.get('repo_name') or '')
    status = str(adapter.get('status') or 'BLOCKED')
    score_delta = fnum(adapter.get('score_delta'))
    score_eligible = bool(adapter.get('score_eligible'))
    runtime_status = str(adapter.get('runtime_status') or '')
    if status == 'REAL_OUTPUT' and repo_name == 'tradingagent_a' and 'TECHNICAL_AND_STRATEGY' in runtime_status:
        return 'REAL_OUTPUT_ACTIVE_TECHNICAL_STRATEGY'
    if status == 'REAL_OUTPUT' and repo_name == 'tradingagent_a' and abs(score_delta) <= 1e-9 and not score_eligible:
        return 'PLACEHOLDER_OR_NO_EFFECT'
    if status == 'REAL_OUTPUT' and repo_name == 'Qlib' and 'ENHANCED' in runtime_status:
        return 'REAL_OUTPUT_ACTIVE_ENHANCED_FEATURE_PROXY'
    if status == 'REAL_OUTPUT' and repo_name == 'Qlib':
        return 'WEAK_OR_PARTIAL'
    if status == 'REAL_OUTPUT' and repo_name == 'QuantDinger':
        return 'GUARD_ONLY'
    if status == 'REAL_OUTPUT' and repo_name == 'UZI_Skill':
        return 'REAL_OUTPUT_UZI_SKILL_SCORING'
    return status


def repo_contribution_candidate_signal(adapter: Dict[str, Any]) -> str:
    repo_name = str(adapter.get('repo_name') or '')
    signals = adapter.get('signals') if isinstance(adapter.get('signals'), dict) else {}
    runtime_status = str(adapter.get('runtime_status') or '')
    if str(adapter.get('status') or '') != 'REAL_OUTPUT':
        return str(
            signals.get('blocked_reason')
            or adapter.get('runtime_status')
            or adapter.get('status')
            or 'BLOCKED_NATIVE_RUNTIME'
        )
    if repo_name == 'tradingagent_a' and 'TECHNICAL_AND_STRATEGY' in runtime_status:
        return 'ACTIVE_TECHNICAL_STRATEGY_FEATURES'
    if repo_name == 'tradingagent_a':
        return 'COMMON_UTILS_ONLY'
    if repo_name == 'VEI':
        return 'ACTIVE_VEI_ASOF_SCORING'
    if repo_name == 'Qlib' and 'ENHANCED' in runtime_status:
        return 'ACTIVE_ENHANCED_FEATURE_PROXY'
    if repo_name == 'Qlib':
        return 'QLIB_FEATURE_PROXY_NO_MODEL'
    if repo_name == 'QuantDinger':
        return 'DATA_HEALTH_LIQUIDITY_GUARD'
    if repo_name == 'UZI_Skill':
        return 'ACTIVE_UZI_SKILL_SIMPLIFIED_SCORING'
    return str(adapter.get('runtime_status') or 'REAL_OUTPUT')


def repo_contribution_explanation(adapter: Dict[str, Any]) -> str:
    repo_name = str(adapter.get('repo_name') or '')
    status = repo_contribution_status(adapter)
    score_delta = fnum(adapter.get('score_delta'))
    signals = adapter.get('signals') if isinstance(adapter.get('signals'), dict) else {}
    runtime_status = str(adapter.get('runtime_status') or '')
    if repo_name == 'tradingagent_a' and 'TECHNICAL_AND_STRATEGY' in runtime_status:
        tf = signals.get('technical_features') if isinstance(signals.get('technical_features'), dict) else {}
        sf = signals.get('strategy_features') if isinstance(signals.get('strategy_features'), dict) else {}
        return (
            'active technical + strategy features from xiaogu web evidence; '
            f"rsi_proxy={fnum(tf.get('rsi_proxy')):.4f}, "
            f"momentum={fnum(tf.get('momentum')):.4f}, "
            f"volatility={fnum(tf.get('volatility')):.4f}, "
            f"trend_strength={fnum(tf.get('trend_strength')):.4f}, "
            f"trend_confidence={fnum(tf.get('trend_confidence')):.4f}, "
            f"price_volume_divergence={fnum(tf.get('price_volume_divergence')):.4f}; "
            f"momentum_rsi_composite={fnum(sf.get('momentum_rsi_composite')):.4f}, "
            f"ema_crossover_signal={sf.get('ema_crossover_signal', 0)}, "
            f"four_way_signal={sf.get('four_way_signal', 'hold')}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    if repo_name == 'tradingagent_a':
        return 'symbol normalization + previous trade date + small-account buyability only; score_delta=0'
    if repo_name == 'VEI':
        features = signals.get('features') if isinstance(signals.get('features'), dict) else {}
        return (
            'active as-of scoring from '
            f"pre_limitup_anomaly={fnum(features.get('pre_limitup_anomaly')):.4f}, "
            f"weak_to_strong_reversal={fnum(features.get('weak_to_strong_reversal')):.4f}, "
            f"first_board_pre_signal={fnum(features.get('first_board_pre_signal')):.4f}, "
            f"sector_opportunity_score={fnum(features.get('sector_opportunity_score')):.4f}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    if repo_name == 'Qlib' and 'ENHANCED' in runtime_status:
        ef = signals.get('enhanced_features') if isinstance(signals.get('enhanced_features'), dict) else {}
        return (
            'Qlib enhanced feature proxy from xiaogu web evidence; '
            f"trend_strength={fnum(ef.get('trend_strength')):.4f}, "
            f"trend_confidence={fnum(ef.get('trend_confidence')):.4f}, "
            f"momentum_quality={fnum(ef.get('momentum_quality')):.4f}, "
            f"breakout_score={fnum(ef.get('breakout_score')):.4f}, "
            f"reversal_score={fnum(ef.get('reversal_score')):.4f}, "
            f"sector_heat={fnum(ef.get('sector_heat')):.4f}, "
            f"price_volume_corr_proxy={fnum(ef.get('price_volume_corr_proxy')):.4f}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    if repo_name == 'Qlib':
        feature_view = signals.get('feature_view') if isinstance(signals.get('feature_view'), dict) else {}
        return (
            'Qlib feature proxy only; no qlib data fetch, no model fit, no prediction pipeline; '
            f"rank_quality={fnum(feature_view.get('rank_quality')):.4f}, "
            f"liquidity_quality={fnum(feature_view.get('liquidity_quality')):.4f}, "
            f"breadth_quality={fnum(feature_view.get('breadth_quality')):.4f}, "
            f"moderate_momentum={fnum(feature_view.get('moderate_momentum')):.4f}, "
            f"crowding_risk={fnum(feature_view.get('crowding_risk')):.4f}, "
            f"high_price_risk={fnum(feature_view.get('high_price_risk')):.4f}, "
            f"weak_breadth_risk={fnum(feature_view.get('weak_breadth_risk')):.4f}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    if repo_name == 'QuantDinger':
        return (
            'data-health / liquidity guard only; guard, not alpha; '
            f"source_row_hash_present={bool(signals.get('source_row_hash_present'))}, "
            f"evidence_path_present={bool(signals.get('evidence_path_present'))}, "
            f"source_time_present={bool(signals.get('source_time_present'))}, "
            f"data_health={fnum(signals.get('data_health')):.4f}, "
            f"liquidity_guard={fnum(signals.get('liquidity_guard')):.4f}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    if repo_name == 'UZI_Skill':
        uf = signals.get('uzi_features') if isinstance(signals.get('uzi_features'), dict) else {}
        return (
            'UZI-Skill 22-dim simplified scoring from xiaogu web evidence; '
            f"stage={uf.get('stage', 'unknown')}, "
            f"stage_score={uf.get('stage_score', 0)}, "
            f"valuation={uf.get('valuation', 'unknown')}, "
            f"valuation_score={uf.get('valuation_score', 0)}, "
            f"capital_flow={uf.get('capital_flow', 'unknown')}, "
            f"capital_score={uf.get('capital_score', 0)}, "
            f"composite_score={uf.get('composite_score', 0):.4f}; "
            f'status={status}; score_delta={score_delta:.4f}'
        )
    return f"status={status}; score_delta={score_delta:.4f}"


def repo_contribution_from_adapter(adapter: Dict[str, Any]) -> Dict[str, Any]:
    contribution = {
        'status': repo_contribution_status(adapter),
        'candidate_signal': repo_contribution_candidate_signal(adapter),
        'score_delta': round(fnum(adapter.get('score_delta')), 4),
        'explanation': repo_contribution_explanation(adapter),
    }
    if adapter.get('repo_name') == 'VEI':
        signals = adapter.get('signals') if isinstance(adapter.get('signals'), dict) else {}
        features = signals.get('features') if isinstance(signals.get('features'), dict) else {}
        contribution['components'] = {
            'pre_limitup_anomaly': round(fnum(features.get('pre_limitup_anomaly')), 4),
            'weak_to_strong_reversal': round(fnum(features.get('weak_to_strong_reversal')), 4),
            'first_board_pre_signal': round(fnum(features.get('first_board_pre_signal')), 4),
            'sector_opportunity_score': round(fnum(features.get('sector_opportunity_score')), 4),
        }
    return contribution


def repo_contribution_summary_text(repo_contributions: Dict[str, Any]) -> str:
    if not isinstance(repo_contributions, dict) or not repo_contributions:
        return ''
    parts: List[str] = []
    noise_parts: List[str] = []

    def _fmt(repo_name: str, entry: Dict[str, Any]) -> str:
        status = str(entry.get('status') or '')
        signal = str(entry.get('candidate_signal') or '')
        delta = fnum(entry.get('score_delta'))
        noise = bool(entry.get('noise')) or status in {
            'GUARD_ONLY', 'STRUCTURED_FALLBACK', 'STRUCTURED_FALLBACK_MIMO',
            'PLACEHOLDER_OR_NO_EFFECT', 'BLOCKED', 'CONCEPT_ONLY',
        } or status.startswith('STRUCTURED_FALLBACK')
        if noise or entry.get('counts_toward_total') is False:
            return f'{repo_name}:{status}[{signal}]=+0.0000(noise)'
        return f'{repo_name}:{status}[{signal}]={delta:+.4f}'

    for repo_name in ACTIVE_REPO_ORDER:
        entry = repo_contributions.get(repo_name)
        if not isinstance(entry, dict):
            continue
        text = _fmt(repo_name, entry)
        if '(noise)' in text:
            noise_parts.append(text)
        else:
            parts.append(text)
    # Active scoring first; noise last so selection_reason is not dominated by guards.
    return '; '.join(parts + noise_parts)


def blocked_record(repo_name: str, reason: str, candidate: Dict[str, Any], extra: Optional[Dict[str, Any]] = None, evidence_paths: Optional[List[str]] = None, external_api_used: bool = False, llm_used: bool = False) -> Dict[str, Any]:
    repo_path = REPO_PATHS.get(repo_name)
    signals = {
        'blocked_reason': reason,
        'repo_path': str(repo_path) if repo_path else '',
        'repo_commit': repo_commit(repo_path) if repo_path else None,
        'native_runtime_required': True,
    }
    if extra:
        signals.update(extra)
    return adapter_record(
        repo_name=repo_name,
        status='BLOCKED',
        runtime_status='BLOCKED_NATIVE_RUNTIME',
        signals=signals,
        score_delta=0.0,
        score_eligible=False,
        risk_flags=[reason],
        evidence_paths=evidence_paths,
        external_api_used=external_api_used,
        llm_used=llm_used,
        confidence=0.0,
    )


def run_native_command(repo_name: str, candidate: Dict[str, Any], label: str, args: List[str], cwd: Path, timeout: int = 60, env_extra: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, Any], str]:
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    if env_extra:
        env.update(env_extra)
    started_at = dt.datetime.now().isoformat(timespec='seconds')
    payload: Dict[str, Any]
    try:
        proc = subprocess.run(args, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)
        payload = {
            'repo_name': repo_name,
            'label': label,
            'command': args,
            'cwd': str(cwd),
            'returncode': proc.returncode,
            'stdout': _truncate(proc.stdout),
            'stderr': _truncate(proc.stderr),
            'started_at': started_at,
            'ended_at': dt.datetime.now().isoformat(timespec='seconds'),
        }
    except subprocess.TimeoutExpired as exc:
        payload = {
            'repo_name': repo_name,
            'label': label,
            'command': args,
            'cwd': str(cwd),
            'returncode': None,
            'stdout': _truncate(exc.stdout or ''),
            'stderr': _truncate(exc.stderr or ''),
            'timeout_seconds': timeout,
            'started_at': started_at,
            'ended_at': dt.datetime.now().isoformat(timespec='seconds'),
        }
    evidence_path = write_evidence(repo_name, candidate, label, payload)
    return payload, evidence_path


def run_python_probe(repo_name: str, candidate: Dict[str, Any], code: str, cwd: Path, timeout: int = 30, env_extra: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, Any], str]:
    payload, path = run_native_command(repo_name, candidate, 'native_probe', [sys.executable, '-c', code], cwd, timeout=timeout, env_extra=env_extra)
    parsed = None
    stdout = payload.get('stdout') or ''
    try:
        parsed = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
    except Exception as exc:
        parsed = {'parse_error': repr(exc)}
    payload['parsed_json'] = parsed
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload, path


def compute_tradingagent_technical_features(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """使用 xiaogu 东财数据计算 TradingAgents 风格的技术面特征"""
    signal_pct = fnum(candidate.get('signal_pct'))
    volume_ratio = fnum(candidate.get('volume_ratio'))
    amount_pctile = fnum(candidate.get('amount_pctile_rule'))
    price = fnum(candidate.get('price'))
    rank = fnum(candidate.get('rank'))
    universe_size = fnum(candidate.get('full_universe_quote_count'))
    if universe_size <= 0:
        universe_size = 5500.0

    rsi_proxy = 50.0 + signal_pct * 3.0 - (volume_ratio - 1.0) * 15.0
    rsi_proxy = max(0.0, min(100.0, rsi_proxy))

    momentum = signal_pct
    volatility = abs(signal_pct) * volume_ratio

    if signal_pct > 0 and volume_ratio < 1.0:
        price_volume_divergence = -1.0
    elif signal_pct < 0 and volume_ratio > 1.0:
        price_volume_divergence = 1.0
    else:
        price_volume_divergence = 0.0

    trend_strength = abs(signal_pct) / 10.0
    trend_confidence = 1.0 - (rank / max(1.0, universe_size))
    cross_sectional_rank = fnum(candidate.get('full_universe_rank')) / max(1.0, universe_size)

    return {
        'rsi_proxy': round(rsi_proxy, 4),
        'momentum': round(momentum, 4),
        'volatility': round(volatility, 4),
        'price_volume_divergence': round(price_volume_divergence, 4),
        'trend_strength': round(trend_strength, 4),
        'trend_confidence': round(trend_confidence, 4),
        'cross_sectional_rank': round(cross_sectional_rank, 4),
        'amount_percentile': round(amount_pctile, 4),
    }


def compute_quantdinger_strategy_features(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """使用 QuantDinger 策略模板思想计算因子"""
    signal_pct = fnum(candidate.get('signal_pct'))
    volume_ratio = fnum(candidate.get('volume_ratio'))

    momentum = signal_pct
    rsi_proxy = 50.0 + signal_pct * 3.0 - (volume_ratio - 1.0) * 15.0
    rsi_proxy = max(0.0, min(100.0, rsi_proxy))

    momentum_rsi_composite = momentum * 0.7 + (100.0 - rsi_proxy) * 0.3

    if signal_pct > 0 and volume_ratio > 1.0:
        ema_crossover_signal = 1
    elif signal_pct < 0 and volume_ratio > 1.0:
        ema_crossover_signal = -1
    else:
        ema_crossover_signal = 0

    if signal_pct > 2.0 and volume_ratio > 1.2:
        four_way_signal = 'open_long'
    elif signal_pct < -2.0 and volume_ratio > 1.2:
        four_way_signal = 'open_short'
    elif signal_pct < -2.0 and volume_ratio < 0.8:
        four_way_signal = 'close_long'
    elif signal_pct > 2.0 and volume_ratio < 0.8:
        four_way_signal = 'close_short'
    else:
        four_way_signal = 'hold'

    return {
        'momentum': round(momentum, 4),
        'rsi_proxy': round(rsi_proxy, 4),
        'momentum_rsi_composite': round(momentum_rsi_composite, 4),
        'ema_crossover_signal': ema_crossover_signal,
        'four_way_signal': four_way_signal,
    }


def tradingagent_a_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo = REPO_PATHS['tradingagent_a']
    if not repo.exists():
        return blocked_record('tradingagent_a', 'NATIVE_SOURCE_TREE_MISSING', candidate)
    symbol = candidate_symbol(candidate)
    if 'tradingagent_a_common' not in _MODULE_CACHE:
        sys.path.insert(0, str(repo))
        ak = types.ModuleType('akshare')
        try:
            import pandas as pd
            def tool_trade_date_hist_sina():
                return pd.DataFrame({'trade_date': [TODAY]})
            ak.tool_trade_date_hist_sina = tool_trade_date_hist_sina
        except Exception:
            pass
        sys.modules.setdefault('akshare', ak)
        target = repo / 'tradingagents' / 'dataflows' / 'a_share_common.py'
        if not target.exists():
            return blocked_record('tradingagent_a', 'A_SHARE_COMMON_NOT_FOUND', candidate)
        try:
            spec = importlib.util.spec_from_file_location('tradingagent_a_common_runtime', target)
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            _MODULE_CACHE['tradingagent_a_common'] = mod
        except Exception as exc:
            return blocked_record('tradingagent_a', 'A_SHARE_COMMON_IMPORT_FAILED', candidate, {'error': repr(exc)})
    mod = _MODULE_CACHE['tradingagent_a_common']
    try:
        normalized = mod.normalize_ashare_symbol(symbol)
        plain = mod.to_plain_symbol(normalized)
        exchange_prefixed = mod.to_exchange_prefixed_symbol(plain)
    except Exception as exc:
        return blocked_record('tradingagent_a', 'A_SHARE_COMMON_CALL_FAILED', candidate, {'error': repr(exc)})
    previous_trade_date_error = None
    try:
        previous_trade_date = mod.get_previous_trade_date(candidate_date(candidate) or TODAY)
    except Exception as exc:
        previous_trade_date = candidate_date(candidate) or TODAY
        previous_trade_date_error = repr(exc)
    board = board_from_code(plain)
    one_lot_cost_cap = fnum(candidate.get('paper_one_lot_cost_cap'))
    cap_source = 'paper_one_lot_cost_cap'
    if one_lot_cost_cap <= 0:
        one_lot_cost_cap = fnum(candidate.get('account_available_cash'))
        cap_source = 'account_available_cash'
    if one_lot_cost_cap <= 0:
        one_lot_cost_cap = None
        cap_source = 'account_snapshot_unavailable'
    account_available_cash = fnum(candidate.get('account_available_cash'))
    buyable, reject_reason = small_account_buyable(
        plain,
        fnum(candidate.get('price')),
        one_lot_cost_cap=one_lot_cost_cap,
        available_cash=account_available_cash if account_available_cash > 0 else None,
    )
    candidate_web_evidence = web_evidence(candidate)
    technical_features = compute_tradingagent_technical_features(candidate)
    strategy_features = compute_quantdinger_strategy_features(candidate)
    evidence_payload = {
        'repo_name': 'tradingagent_a',
        'repo_path': str(repo),
        'repo_commit': repo_commit(repo),
        'input_symbol': symbol,
        'normalized_symbol': normalized,
        'plain_symbol': plain,
        'exchange_prefixed_symbol': exchange_prefixed,
        'previous_trade_date': previous_trade_date,
        'previous_trade_date_error': previous_trade_date_error,
        'board': board,
        'small_account_buyable': buyable,
        'small_account_reject_reason': reject_reason,
        'small_account_cost_cap': one_lot_cost_cap,
        'small_account_cap_source': cap_source,
        'source_policy': candidate_web_evidence.get('source_policy') or 'XIAOGU_WEB_EVIDENCE_ONLY',
        'web_evidence': candidate_web_evidence,
        'technical_features': technical_features,
        'strategy_features': strategy_features,
        'paper_only': True,
        'no_trade': True,
    }
    # Compute score_delta from technical features
    rsi = fnum(technical_features.get('rsi_proxy'))
    momentum = fnum(technical_features.get('momentum'))
    trend_conf = fnum(technical_features.get('trend_confidence'))
    four_way = str(technical_features.get('four_way_signal', 'hold'))
    score_delta = 0.0
    if rsi > 70:
        score_delta -= 0.3
    elif rsi < 30:
        score_delta += 0.3
    if momentum > 3:
        score_delta += 0.2
    elif momentum < -3:
        score_delta -= 0.2
    if four_way == 'open_long':
        score_delta += 0.3
    elif four_way == 'open_short':
        score_delta -= 0.3
    elif four_way == 'close_long':
        score_delta -= 0.2
    score_delta = clamp(score_delta * trend_conf, -1.0, 1.0)

    evidence_paths = [write_evidence('tradingagent_a', candidate, 'native_common_utils', evidence_payload)] if is_live_candidate(candidate) else []
    return adapter_record(
        repo_name='tradingagent_a',
        status='REAL_OUTPUT',
        runtime_status='REAL_OUTPUT_NATIVE_COMMON_UTILS_WITH_TECHNICAL_AND_STRATEGY_FEATURES',
        signals={
            'normalized_symbol': normalized,
            'plain_symbol': plain,
            'exchange_prefixed_symbol': exchange_prefixed,
            'previous_trade_date': previous_trade_date,
            'previous_trade_date_error': previous_trade_date_error,
            'board': board,
            'small_account_buyable': buyable,
            'small_account_reject_reason': reject_reason,
            'small_account_cost_cap': one_lot_cost_cap,
            'small_account_cap_source': cap_source,
            'source_policy': candidate_web_evidence.get('source_policy') or 'XIAOGU_WEB_EVIDENCE_ONLY',
            'web_evidence': candidate_web_evidence,
            'web_evidence_code': candidate_web_evidence.get('fields', {}).get('code'),
            'web_evidence_price': candidate_web_evidence.get('fields', {}).get('price'),
            'technical_features': technical_features,
            'strategy_features': strategy_features,
            'native_scope': 'a_share_common_symbol_calendar_and_technical_strategy',
        },
        score_delta=score_delta,
        score_eligible=True,
        evidence_paths=evidence_paths,
        confidence=0.9,
    )


def vei_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    if not has_web_evidence(candidate):
        return blocked_record('VEI', 'XIAOGU_WEB_EVIDENCE_MISSING', candidate)
    e = web_evidence(candidate)
    f = e['fields']
    features = compute_vei_features(candidate)
    score_delta = (
        features['pre_limitup_anomaly'] * 1.1
        + features['weak_to_strong_reversal'] * 0.8
        + features['first_board_pre_signal'] * 0.6
        + features['sector_opportunity_score'] * 0.8
    )
    risk_flags = []
    if f['signal_pct'] >= 9.0 and features['pre_limitup_anomaly'] <= 0.0:
        score_delta -= 1.0
        risk_flags.append('vei_near_limit_without_pre_signal')
    if f['market_breadth_up_pct'] < 25 and features['weak_to_strong_reversal'] <= 0.0:
        score_delta -= 0.6
        risk_flags.append('vei_weak_breadth_without_reversal')
    score_delta = clamp(score_delta, -2.0, 2.0)
    evidence_path = write_evidence('VEI', candidate, 'active_vei_scoring', {
        'repo_name': 'VEI',
        'source_policy': 'XIAOGU_ASOF_FEATURES_ONLY',
        'native_logic_source': 'validated VEI pre-limitup / weak-to-strong / first-board features from xiaogu as-of evidence',
        'web_evidence': e,
        'features': features,
        'risk_flags': risk_flags,
        'score_delta': score_delta,
        'paper_only': True,
        'no_trade': True,
    }) if is_live_candidate(candidate) else None
    return adapter_record(
        repo_name='VEI',
        status='REAL_OUTPUT',
        runtime_status='REAL_OUTPUT_ACTIVE_VEI_ASOF_SCORING',
        signals={
            'source_policy': 'XIAOGU_ASOF_FEATURES_ONLY',
            'native_logic_source': 'VEI active scoring from validated as-of event features',
            'features': features,
            'web_evidence': e,
        },
        score_delta=score_delta,
        score_eligible=True,
        risk_flags=risk_flags,
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=False,
        llm_used=False,
        confidence=0.75,
    )


def compute_qlib_enhanced_features(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """使用 qlib 算子思想增强特征工程"""
    signal_pct = fnum(candidate.get('signal_pct'))
    volume_ratio = fnum(candidate.get('volume_ratio'))
    price = fnum(candidate.get('price'))
    amount = fnum(candidate.get('signal_amount'))
    rank = fnum(candidate.get('rank'))
    universe_size = fnum(candidate.get('full_universe_quote_count'))
    if universe_size <= 0:
        universe_size = 5500.0
    market_breadth = fnum(candidate.get('market_breadth_up_pct'))
    market_limitups = fnum(candidate.get('market_limitups'))

    trend_strength = abs(signal_pct) / 10.0
    trend_confidence = 1.0 - (rank / max(1.0, universe_size))
    cross_sectional_rank = fnum(candidate.get('full_universe_rank')) / max(1.0, universe_size)

    amount_norm = amount / 1e9 if amount > 0 else 0.0
    price_volume_corr_proxy = min(1.0, amount_norm / max(0.1, abs(signal_pct)))

    momentum_quality = 0.0
    if signal_pct > 0 and volume_ratio > 1.0:
        momentum_quality = min(1.0, signal_pct / 5.0 * volume_ratio / 2.0)
    elif signal_pct < 0 and volume_ratio < 1.0:
        momentum_quality = min(1.0, abs(signal_pct) / 5.0 * (2.0 - volume_ratio))

    breakout_score = 0.0
    if signal_pct > 3.0 and amount_norm > 1.0:
        breakout_score = min(1.0, (signal_pct - 3.0) / 7.0 + amount_norm / 5.0)

    reversal_score = 0.0
    if signal_pct < -2.0 and volume_ratio > 1.5:
        reversal_score = min(1.0, abs(signal_pct) / 5.0 * (volume_ratio - 1.0))

    sector_heat = 0.0
    if market_limitups > 50:
        sector_heat = min(1.0, market_limitups / 150.0)

    return {
        'trend_strength': round(trend_strength, 4),
        'trend_confidence': round(trend_confidence, 4),
        'cross_sectional_rank': round(cross_sectional_rank, 4),
        'price_volume_corr_proxy': round(price_volume_corr_proxy, 4),
        'momentum_quality': round(momentum_quality, 4),
        'breakout_score': round(breakout_score, 4),
        'reversal_score': round(reversal_score, 4),
        'sector_heat': round(sector_heat, 4),
    }


def qlib_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo = REPO_PATHS['Qlib']
    if not repo.exists():
        return blocked_record('Qlib', 'NATIVE_SOURCE_TREE_MISSING', candidate)
    if not has_web_evidence(candidate):
        return blocked_record('Qlib', 'XIAOGU_WEB_EVIDENCE_MISSING', candidate)
    e = web_evidence(candidate)
    f = e['fields']
    rank_quality = max(0.0, min(1.0, (80.0 - f['rank']) / 80.0))
    liquidity_quality = max(0.0, min(1.0, f['amount_pctile_rule']))
    breadth_quality = max(0.0, min(1.0, f['market_breadth_up_pct'] / 100.0))
    signal_pct = f['signal_pct']
    moderate_momentum = max(0.0, min(1.0, (signal_pct - 4.0) / 7.0)) if signal_pct <= 11.0 else max(0.0, 1.0 - (signal_pct - 11.0) / 6.0)
    crowding_risk = max(0.0, min(1.0, f['market_bigups'] / 220.0)) if signal_pct > 10.0 else 0.0
    high_price_risk = max(0.0, min(1.0, (f['price'] - 35.0) / 35.0))
    weak_breadth_risk = max(0.0, min(1.0, (30.0 - f['market_breadth_up_pct']) / 30.0))
    enhanced_features = compute_qlib_enhanced_features(candidate)
    feature_view = {
        'rank_quality': round(rank_quality, 4),
        'liquidity_quality': round(liquidity_quality, 4),
        'breadth_quality': round(breadth_quality, 4),
        'moderate_momentum': round(moderate_momentum, 4),
        'crowding_risk': round(crowding_risk, 4),
        'high_price_risk': round(high_price_risk, 4),
        'weak_breadth_risk': round(weak_breadth_risk, 4),
        **enhanced_features,
    }
    score_delta = (
        moderate_momentum * 0.55
        + liquidity_quality * 0.45
        + rank_quality * 0.25
        + breadth_quality * 0.25
        - crowding_risk * 0.45
        - high_price_risk * 0.25
        - weak_breadth_risk * 0.55
        + enhanced_features.get('momentum_quality', 0.0) * 0.3
        + enhanced_features.get('breakout_score', 0.0) * 0.2
    )
    risk_flags = []
    if crowding_risk >= 0.55:
        risk_flags.append('qlib_crowding_risk')
    if weak_breadth_risk >= 0.45:
        risk_flags.append('qlib_weak_breadth_risk')
    score_delta = clamp(score_delta, -1.5, 1.5)
    evidence_path = write_evidence('Qlib', candidate, 'active_qlib_feature_view', {
        'repo_name': 'Qlib',
        'repo_path': str(repo),
        'repo_commit': repo_commit(repo),
        'source_policy': 'XIAOGU_ASOF_FEATURES_ONLY_NO_QLIB_DATA_FETCH',
        'native_logic_source': 'Qlib enhanced feature proxy; no qlib data fetch, no model fit, no prediction pipeline',
        'web_evidence': e,
        'feature_view': feature_view,
        'enhanced_features': enhanced_features,
        'risk_flags': risk_flags,
        'score_delta': score_delta,
        'paper_only': True,
        'no_trade': True,
    }) if is_live_candidate(candidate) else None
    return adapter_record(
        repo_name='Qlib',
        status='REAL_OUTPUT',
        runtime_status='REAL_OUTPUT_ACTIVE_QLIB_ENHANCED_FEATURE_PROXY',
        signals={
            'repo_path': str(repo),
            'repo_commit': repo_commit(repo),
            'source_policy': 'XIAOGU_ASOF_FEATURES_ONLY_NO_QLIB_DATA_FETCH',
            'native_logic_source': 'Qlib enhanced feature proxy; no qlib data fetch, no model fit, no prediction pipeline',
            'feature_view': feature_view,
            'enhanced_features': enhanced_features,
            'web_evidence': e,
        },
        score_delta=score_delta,
        score_eligible=True,
        risk_flags=risk_flags,
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=False,
        llm_used=False,
        confidence=0.75,
    )


def quantdinger_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo = REPO_PATHS['QuantDinger']
    if not repo.exists():
        return blocked_record('QuantDinger', 'NATIVE_SOURCE_TREE_MISSING', candidate)
    if not has_web_evidence(candidate):
        return blocked_record('QuantDinger', 'XIAOGU_WEB_EVIDENCE_MISSING', candidate)
    e = web_evidence(candidate)
    f = e['fields']
    liquidity = f['amount_pctile_rule']
    price = f['price']
    source_hash = e.get('source_row_hash')
    evidence_path_in = e.get('evidence_path')
    source_time = e.get('source_time')

    data_health = 1.0 if source_hash and evidence_path_in and source_time else 0.0
    data_health_penalty = 0.0
    if not source_hash:
        data_health_penalty -= 0.15
    if not evidence_path_in:
        data_health_penalty -= 0.10
    if not source_time:
        data_health_penalty -= 0.05

    liquidity_guard = 0.0
    risk_flags = []
    if liquidity < 0.30:
        liquidity_guard -= 0.5
        risk_flags.append('quantdinger_low_liquidity_coverage')
    if price <= 0:
        liquidity_guard -= 0.5
        risk_flags.append('quantdinger_invalid_price')
    if not source_hash:
        liquidity_guard -= 0.1
        risk_flags.append('quantdinger_missing_source_row_hash')

    score_delta = clamp(data_health_penalty + liquidity_guard, -2.0, 0.0)
    evidence_path = write_evidence('QuantDinger', candidate, 'native_logic_web_evidence', {
        'repo_name': 'QuantDinger',
        'repo_path': str(repo),
        'repo_commit': repo_commit(repo),
        'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
        'native_logic_source': 'QuantDinger data-health / liquidity guard using xiaogu web evidence; repo data fetch disabled; guard, not alpha',
        'web_evidence': e,
        'data_health': data_health,
        'data_health_penalty': data_health_penalty,
        'liquidity_guard': liquidity_guard,
        'risk_flags': risk_flags,
        'score_delta': score_delta,
        'paper_only': True,
        'no_trade': True,
    }) if is_live_candidate(candidate) else None
    return adapter_record(
        repo_name='QuantDinger',
        status='REAL_OUTPUT',
        runtime_status='REAL_OUTPUT_NATIVE_LOGIC_WITH_XIAOGU_WEB_EVIDENCE',
        signals={
            'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
            'native_logic_source': 'QuantDinger data-health / liquidity guard semantics; native data fetch disabled; guard, not alpha',
            'data_health': round(data_health, 4),
            'data_health_penalty': round(data_health_penalty, 4),
            'liquidity_guard': round(liquidity_guard, 4),
            'source_row_hash_present': bool(source_hash),
            'evidence_path_present': bool(evidence_path_in),
            'source_time_present': bool(source_time),
            'web_evidence': e,
        },
        # Guard-only: visible in diagnostics, excluded from official score total.
        score_delta=score_delta,
        score_eligible=False,
        risk_flags=risk_flags,
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=False,
        confidence=0.7,
    )


def compute_uzi_skill_features(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """使用 UZI-Skill 22 维评分体系的简化版计算特征"""
    signal_pct = fnum(candidate.get('signal_pct'))
    volume_ratio = fnum(candidate.get('volume_ratio'))
    price = fnum(candidate.get('price'))
    amount = fnum(candidate.get('signal_amount'))
    rank = fnum(candidate.get('rank'))
    universe_size = fnum(candidate.get('full_universe_quote_count'))
    if universe_size <= 0:
        universe_size = 5500.0
    market_breadth = fnum(candidate.get('market_breadth_up_pct'))
    market_limitups = fnum(candidate.get('market_limitups'))
    market_bigups = fnum(candidate.get('market_bigups'))
    net_inflow_main = fnum(candidate.get('net_inflow_main'))

    # 维度 2: K线阶段 (基于 signal_pct 和 volume_ratio)
    if signal_pct > 5 and volume_ratio > 1.5:
        stage = 'Stage 2 uptrend'
        stage_score = 8
    elif signal_pct > 2 and volume_ratio > 1.0:
        stage = 'Stage 1 early'
        stage_score = 6
    elif signal_pct < -3 and volume_ratio > 1.5:
        stage = 'Stage 4 decline'
        stage_score = 3
    else:
        stage = 'Stage 3 consolidation'
        stage_score = 5

    # 维度 10: 估值 (基于 rank 分位数)
    rank_percentile = rank / max(1.0, universe_size) * 100
    if rank_percentile < 20:
        valuation = 'undervalued'
        valuation_score = 9
    elif rank_percentile < 50:
        valuation = 'fair'
        valuation_score = 6
    else:
        valuation = 'overvalued'
        valuation_score = 3

    # 维度 12: 资金面 (基于 net_inflow_main)
    if net_inflow_main > 1e8:
        capital_flow = 'strong_inflow'
        capital_score = 8
    elif net_inflow_main > 0:
        capital_flow = 'mild_inflow'
        capital_score = 6
    elif net_inflow_main < -1e8:
        capital_flow = 'strong_outflow'
        capital_score = 3
    else:
        capital_flow = 'neutral'
        capital_score = 5

    # 维度 16: 龙虎榜 (基于 market_limitups)
    if market_limitups > 100:
        lhb_heat = 'hot'
        lhb_score = 8
    elif market_limitups > 50:
        lhb_heat = 'warm'
        lhb_score = 6
    else:
        lhb_heat = 'normal'
        lhb_score = 4

    # 维度 17: 舆情 (基于 market_bigups 和 signal_pct)
    sentiment_score = 5 + min(3, int(signal_pct / 3))

    # 综合评分 (加权平均)
    weights = {'stage': 4, 'valuation': 5, 'capital': 4, 'lhb': 4, 'sentiment': 3}
    total_weight = sum(weights.values())
    composite_score = (
        stage_score * weights['stage'] +
        valuation_score * weights['valuation'] +
        capital_score * weights['capital'] +
        lhb_score * weights['lhb'] +
        sentiment_score * weights['sentiment']
    ) / total_weight

    return {
        'stage': stage,
        'stage_score': stage_score,
        'valuation': valuation,
        'valuation_score': valuation_score,
        'capital_flow': capital_flow,
        'capital_score': capital_score,
        'lhb_heat': lhb_heat if market_limitups > 50 else 'normal',
        'lhb_score': lhb_score,
        'sentiment_score': sentiment_score,
        'composite_score': round(composite_score, 4),
    }


def uzi_skill_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    repo = REPO_PATHS['UZI_Skill']
    if not repo.exists():
        return blocked_record('UZI_Skill', 'NATIVE_SOURCE_TREE_MISSING', candidate)
    if not has_web_evidence(candidate):
        return blocked_record('UZI_Skill', 'XIAOGU_WEB_EVIDENCE_MISSING', candidate)
    e = web_evidence(candidate)

    # 尝试使用完整的22维评分
    uzi_features = None
    scoring_method = 'simplified'

    try:
        uzi_skill_path = repo / 'skills' / 'deep-analysis' / 'scripts'
        if uzi_skill_path.exists():
            sys.path.insert(0, str(uzi_skill_path))
            from lib.pipeline.score_fns import score_dimensions, generate_panel

            raw = {
                'ticker': candidate.get('code', ''),
                'name': candidate.get('name', ''),
                'dimensions': {
                    '1_financials': {'data': {'roe': fnum(candidate.get('roe')), 'net_margin': fnum(candidate.get('net_margin'))}},
                    '2_kline': {'data': {'stage': 'Stage 2 uptrend' if fnum(candidate.get('signal_pct')) > 5 else 'Stage 1 early' if fnum(candidate.get('signal_pct')) > 2 else 'Stage 3 consolidation'}},
                    '10_valuation': {'data': {'pe': fnum(candidate.get('pe')), 'pe_quantile': '50%'}},
                    '12_capital_flow': {'data': {'main_fund_flow_20d': []}},
                }
            }

            dims_scored = score_dimensions(raw)
            panel = generate_panel(dims_scored, raw)

            # score_dimensions returns {'ticker': ..., 'fundamental_score': ..., 'dimensions': {...}}
            # Actual scored dimensions are under 'dimensions' key
            dims = dims_scored.get('dimensions', dims_scored)
            total_score = sum(d.get('score', 5) * d.get('weight', 3) for d in dims.values() if isinstance(d, dict))
            total_weight = sum(d.get('weight', 3) for d in dims.values() if isinstance(d, dict))
            composite_score = total_score / max(1, total_weight)

            # Only use full scoring if it produced a meaningful result (not neutral 5.0)
            if abs(composite_score - 5.0) > 0.01:
                uzi_features = {
                    'dimensions': dims,
                    'panel': panel,
                    'composite_score': composite_score,
                    'scoring_method': 'full_22dim',
                }
                scoring_method = 'full_22dim'

    except Exception:
        pass

    if uzi_features is None:
        uzi_features = compute_uzi_skill_features(candidate)
        scoring_method = 'simplified'

    # 计算score_delta
    composite = uzi_features.get('composite_score', 5.0)
    score_delta = clamp((composite - 5.0) / 5.0, -1.0, 1.0)

    evidence_path = write_evidence('UZI_Skill', candidate, 'uzi_skill_scoring', {
        'repo_name': 'UZI_Skill',
        'repo_path': str(repo),
        'repo_commit': repo_commit(repo),
        'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
        'native_logic_source': f'UZI-Skill {scoring_method} scoring',
        'web_evidence': e,
        'uzi_features': uzi_features,
        'score_delta': score_delta,
        'paper_only': True,
        'no_trade': True,
    }) if is_live_candidate(candidate) else None

    return adapter_record(
        repo_name='UZI_Skill',
        status='REAL_OUTPUT',
        runtime_status=f'REAL_OUTPUT_UZI_SKILL_{scoring_method.upper()}',
        signals={
            'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
            'native_logic_source': f'UZI-Skill {scoring_method} scoring',
            'uzi_features': uzi_features,
            'web_evidence': e,
        },
        score_delta=score_delta,
        score_eligible=True,
        risk_flags=[],
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=False,
        llm_used=False,
        confidence=0.85 if scoring_method == 'full_22dim' else 0.75,
    )


def kaixin_factors_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """凯心因子集成 - 量能、板块资金流、市场情绪、支撑压力等。"""
    repo = REPO_PATHS['Kaixin_Factors']
    e = web_evidence(candidate)

    # 计算凯心因子
    kaixin_features = compute_kaixin_factors(candidate)

    # 计算score_delta
    composite = kaixin_features.get('composite_score', 5.0)
    score_delta = clamp((composite - 5.0) / 5.0, -1.0, 1.0)

    evidence_path = write_evidence('Kaixin_Factors', candidate, 'kaixin_factor_scoring', {
        'repo_name': 'Kaixin_Factors',
        'repo_path': str(repo),
        'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
        'native_logic_source': 'Kaixin factors integrated scoring',
        'web_evidence': e,
        'kaixin_features': kaixin_features,
        'score_delta': score_delta,
        'paper_only': True,
        'no_trade': True,
    }) if is_live_candidate(candidate) else None

    return adapter_record(
        repo_name='Kaixin_Factors',
        status='REAL_OUTPUT',
        runtime_status='REAL_OUTPUT_KAIXIN_FACTORS',
        signals={
            'source_policy': 'XIAOGU_WEB_EVIDENCE_ONLY',
            'native_logic_source': 'Kaixin factors integrated scoring',
            'kaixin_features': kaixin_features,
            'web_evidence': e,
        },
        score_delta=score_delta,
        score_eligible=True,
        risk_flags=[],
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=False,
        llm_used=False,
        confidence=0.80,
    )


def compute_kaixin_factors(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """计算凯心因子。

    集成以下因子：
    1. 量能因子（成交额、放量/缩量）
    2. 板块资金流因子（板块净流入、持续性）
    3. 市场情绪因子（涨跌家数、赚钱效应）
    4. 支撑压力因子（关键价位）
    5. 涨停池因子（涨停家数、连板高度）
    6. 龙虎榜因子（机构/游资动向）
    7. 北向资金因子（外资动向）
    8. 分时异动因子（盘口信号）
    9. 历史盈亏因子（基于历史数据分析的改进）
    """
    features = {}

    # 1. 量能因子
    amount = fnum(candidate.get('signal_amount', 0))
    turnover = fnum(candidate.get('turnover_rate', 0))
    volume_ratio = fnum(candidate.get('volume_ratio', 1))

    if amount > 2e9:  # 20亿以上
        volume_score = 8
        volume_state = '放量'
    elif amount > 5e8:  # 5亿以上
        volume_score = 6
        volume_state = '平量'
    else:
        volume_score = 4
        volume_state = '缩量'

    features['volume'] = {
        'amount': amount,
        'turnover': turnover,
        'volume_ratio': volume_ratio,
        'volume_state': volume_state,
        'score': volume_score,
    }

    # 2. 板块资金流因子
    sector_catalyst = fnum(candidate.get('sector_catalyst_score', 0))
    sector_opportunity = fnum(candidate.get('sector_opportunity_score', 0))

    # 基于历史分析：板块催化=1.0过热，需要惩罚
    if sector_catalyst >= 1.0:
        sector_score = 5  # 过热惩罚
        sector_state = '过热'
    elif sector_opportunity >= 0.8:
        sector_score = 9
        sector_state = '强势'
    elif sector_opportunity >= 0.6:
        sector_score = 7
        sector_state = '健康'
    elif sector_opportunity >= 0.4:
        sector_score = 5
        sector_state = '中性'
    else:
        sector_score = 3

    features['sector_flow'] = {
        'sector_catalyst': sector_catalyst,
        'sector_opportunity': sector_opportunity,
        'score': sector_score,
    }

    # 3. 市场情绪因子
    market_breadth = fnum(candidate.get('market_breadth_up_pct', 50))
    market_limitups = fnum(candidate.get('market_limitups', 0))

    if market_breadth > 60 and market_limitups > 50:
        sentiment_score = 8
        sentiment_state = '偏强'
    elif market_breadth > 45:
        sentiment_score = 5
        sentiment_state = '震荡'
    else:
        sentiment_score = 3
        sentiment_state = '偏弱'

    features['sentiment'] = {
        'market_breadth': market_breadth,
        'market_limitups': market_limitups,
        'sentiment_state': sentiment_state,
        'score': sentiment_score,
    }

    # 4. 支撑压力因子
    price = fnum(candidate.get('price', 0))
    signal_pct = fnum(candidate.get('signal_pct', 0))

    # 简化计算：根据涨幅判断位置
    if signal_pct > 5:
        position_score = 7  # 强势
        position_state = '突破'
    elif signal_pct > 0:
        position_score = 5  # 正常
        position_state = '企稳'
    else:
        position_score = 3  # 弱势
        position_state = '回调'

    features['position'] = {
        'price': price,
        'signal_pct': signal_pct,
        'position_state': position_state,
        'score': position_score,
    }

    # 5. 涨停池因子
    is_limitup = signal_pct >= 9.5
    if is_limitup:
        limitup_score = 9
    elif signal_pct >= 7:
        limitup_score = 7
    elif signal_pct >= 5:
        limitup_score = 5
    else:
        limitup_score = 3

    features['limitup'] = {
        'is_limitup': is_limitup,
        'score': limitup_score,
    }

    # 6. 龙虎榜因子
    lhb_risk = candidate.get('lhb_risk_flags', [])
    if lhb_risk:
        lhb_score = 3  # 有风险
    else:
        lhb_score = 6  # 正常

    features['lhb'] = {
        'has_risk': bool(lhb_risk),
        'score': lhb_score,
    }

    # 7. 北向资金因子
    hsgt_inflow = fnum(candidate.get('hsgt_net_inflow', 0))
    hsgt_consecutive = fnum(candidate.get('hsgt_consecutive_days', 0))

    if hsgt_inflow > 0 and hsgt_consecutive >= 3:
        hsgt_score = 8
    elif hsgt_inflow > 0:
        hsgt_score = 6
    else:
        hsgt_score = 4

    features['hsgt'] = {
        'net_inflow': hsgt_inflow,
        'consecutive_days': hsgt_consecutive,
        'score': hsgt_score,
    }

    # 8. 分时异动因子
    # 简化：根据成交量比率判断
    if volume_ratio > 2:
        intraday_score = 7
        intraday_state = '异动'
    elif volume_ratio > 1.2:
        intraday_score = 5
        intraday_state = '正常'
    else:
        intraday_score = 3
        intraday_state = '低迷'

    features['intraday'] = {
        'volume_ratio': volume_ratio,
        'intraday_state': intraday_state,
        'score': intraday_score,
    }

    # 9. 历史盈亏因子（基于历史数据分析的改进）
    # 关键发现：
    # - 高分数(>100)的票反而亏损 - 过热追高
    # - 板块催化=1.0的票反而亏损 - 板块过热
    # - 中等分数(69-89)+中等板块催化(0.5-0.8) = 盈利
    historical_score = 5  # 默认中性

    # 高分数惩罚
    final_score = fnum(candidate.get('final_score', 0))
    if final_score > 100:
        historical_score -= 2  # 过热惩罚
        historical_state = '高分过热'
    elif final_score > 90:
        historical_score -= 1  # 轻微惩罚
        historical_state = '偏高'
    elif 69 <= final_score <= 89:
        historical_score += 2  # 最佳区间
        historical_state = '最佳区间'
    elif final_score < 50:
        historical_score -= 1  # 低分惩罚
        historical_state = '偏低'
    else:
        historical_state = '正常'

    # 板块过热惩罚
    if sector_catalyst >= 1.0:
        historical_score -= 2  # 板块过热
        historical_state += '+板块过热'
    elif sector_catalyst >= 0.8:
        historical_score -= 1  # 板块偏热
        historical_state += '+板块偏热'
    elif 0.5 <= sector_catalyst <= 0.7:
        historical_score += 1  # 板块健康
        historical_state += '+板块健康'

    # T日涨幅惩罚（追高风险）
    if signal_pct >= 7:
        historical_score -= 2  # 追高风险
        historical_state += '+追高'
    elif signal_pct >= 5:
        historical_score -= 1  # 轻微追高
        historical_state += '+偏高'

    historical_score = max(1, min(10, historical_score))

    features['historical'] = {
        'final_score': final_score,
        'sector_catalyst': sector_catalyst,
        'signal_pct': signal_pct,
        'historical_state': historical_state,
        'score': historical_score,
    }

    # 综合评分
    weights = {
        'volume': 3,
        'sector_flow': 4,
        'sentiment': 3,
        'position': 3,
        'limitup': 2,
        'lhb': 2,
        'hsgt': 2,
        'intraday': 2,
        'historical': 5,  # 历史因子权重最高
    }

    total_score = sum(features[k]['score'] * weights[k] for k in weights)
    total_weight = sum(weights.values())
    composite_score = total_score / total_weight

    features['composite_score'] = round(composite_score, 2)
    features['weights'] = weights

    return features


# ---------------------------------------------------------------------------
# MiMo LLM Client (OpenAI-compatible)
# ---------------------------------------------------------------------------

_MIMO_API_BASE = os.environ.get('MIMO_API_BASE', 'https://api.mimo.ai/v1')
_MIMO_API_KEY = os.environ.get('MIMO_API_KEY', '')
_MIMO_MODEL = os.environ.get('MIMO_MODEL', 'mimo-v2.5-pro')


def mimo_llm_enabled() -> bool:
    return bool(_MIMO_API_KEY) and os.environ.get('XIAOGU_ENABLE_MIMO_LLM', '').strip().lower() in ('1', 'true', 'yes')


def mimo_llm_chat(messages: List[Dict[str, str]], max_tokens: int = 512, temperature: float = 0.3) -> Optional[str]:
    """Call MiMo LLM via OpenAI-compatible chat completions API."""
    if not mimo_llm_enabled():
        return None
    import urllib.request
    url = f'{_MIMO_API_BASE}/chat/completions'
    body = json.dumps({
        'model': _MIMO_MODEL,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={
        'Authorization': f'Bearer {_MIMO_API_KEY}',
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            msg = data.get('choices', [{}])[0].get('message', {})
            content = msg.get('content', '')
            if not content:
                content = msg.get('reasoning_content', '')
            return content
    except Exception:
        return None



# ---------------------------------------------------------------------------
# MiMo Reasoning Adapter (LLM-powered multi-perspective analysis)
# ---------------------------------------------------------------------------

def mimo_reasoning_native_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """MiMo LLM reasoning adapter - multi-perspective analysis using Xiaomi MiMo model.

    Provides LLM-based technical, fundamental, and sentiment reasoning.
    Falls back to structured scoring when LLM is unavailable.
    """
    repo = REPO_PATHS['MiMo_Reasoning']
    symbol = candidate_symbol(candidate)
    name = str(candidate.get('name') or candidate.get('stock_name') or '')
    e = web_evidence(candidate)
    f = e.get('fields', {})

    signal_pct = f.get('signal_pct', 0)
    turnover = f.get('turnover_rate', 0)
    amount = f.get('signal_amount', 0)
    price = f.get('price', 0)
    sector = f.get('sector_name', '')
    market_breadth = f.get('market_breadth_up_pct', 0)
    net_inflow = fnum(candidate.get('net_inflow_main', 0))

    llm_analysis = None
    llm_used = False

    # Try MiMo LLM for deeper analysis
    if mimo_llm_enabled():
        prompt = f"""你是A股量化分析师。分析以下股票并给出简短评估（3句话内）：
股票：{symbol} {name}
板块：{sector}
涨幅：{signal_pct:.2f}%  换手率：{turnover:.1f}%  成交额：{amount/1e8:.0f}亿
股价：{price:.2f}元  主力净流入：{net_inflow/1e4:.0f}万
市场赚钱效应：{market_breadth:.0f}%

请评估：1)短期动量 2)资金面 3)风险点。最后给出看多/看空/中性的判断。"""

        messages = [
            {'role': 'system', 'content': '你是小米MiMo量化分析AI。简洁专业地分析A股。'},
            {'role': 'user', 'content': prompt},
        ]
        llm_analysis = mimo_llm_chat(messages, max_tokens=300, temperature=0.3)
        if llm_analysis:
            llm_used = True

    # Structured scoring (always computed, LLM enhances but not required)
    technical_score = clamp(signal_pct / 10.0, -1.0, 1.0)
    fund_score = clamp(net_inflow / 1e9, -1.0, 1.0) if net_inflow != 0 else 0.0
    sentiment_score = clamp((market_breadth - 50) / 30.0, -1.0, 1.0)
    risk_penalty = 0.0
    risk_flags = []

    if turnover > 20:
        risk_penalty -= 0.3
        risk_flags.append(f'换手率{turnover:.1f}%过高')
    if signal_pct > 9.5:
        risk_penalty -= 0.2
        risk_flags.append('接近涨停追高风险')
    if amount < 1e8:
        risk_penalty -= 0.2
        risk_flags.append('成交额不足1亿')

    base_delta = technical_score * 0.4 + fund_score * 0.3 + sentiment_score * 0.3
    score_delta = clamp(base_delta + risk_penalty, -2.0, 2.0)

    # If LLM analysis is available, adjust score based on sentiment
    llm_adjustment = 0.0
    if llm_analysis:
        analysis_lower = llm_analysis.lower()
        if '看多' in llm_analysis or 'strong' in analysis_lower:
            llm_adjustment = 0.3
        elif '看空' in llm_analysis or 'weak' in analysis_lower:
            llm_adjustment = -0.3
        score_delta = clamp(score_delta + llm_adjustment, -2.0, 2.0)

    evidence_path = write_evidence('MiMo_Reasoning', candidate, 'mimo_analysis', {
        'symbol': symbol,
        'name': name,
        'llm_used': llm_used,
        'llm_analysis': llm_analysis,
        'technical_score': round(technical_score, 4),
        'fund_score': round(fund_score, 4),
        'sentiment_score': round(sentiment_score, 4),
        'risk_penalty': round(risk_penalty, 4),
        'llm_adjustment': round(llm_adjustment, 4),
    }) if is_live_candidate(candidate) else None

    return adapter_record(
        repo_name='MiMo_Reasoning',
        status='REAL_OUTPUT' if llm_used else 'STRUCTURED_FALLBACK',
        runtime_status='REAL_OUTPUT_MIMO_REASONING' if llm_used else 'STRUCTURED_FALLBACK_MIMO',
        signals={
            'llm_used': llm_used,
            'llm_analysis': llm_analysis[:500] if llm_analysis else None,
            'technical_score': round(technical_score, 4),
            'fund_score': round(fund_score, 4),
            'sentiment_score': round(sentiment_score, 4),
            'risk_penalty': round(risk_penalty, 4),
            'llm_adjustment': round(llm_adjustment, 4),
            'risk_flags': risk_flags,
        },
        # Fallback must not count toward official six-repo total (noise).
        score_delta=score_delta if llm_used else 0.0,
        score_eligible=bool(llm_used),
        risk_flags=risk_flags,
        evidence_paths=[evidence_path] if evidence_path else [],
        external_api_used=llm_used,
        llm_used=llm_used,
        confidence=0.85 if llm_used else 0.65,
    )


def native_adapter_for_repo(repo_name: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    runners = {
        'tradingagent_a': tradingagent_a_native_adapter,
        'VEI': vei_native_adapter,
        'Qlib': qlib_native_adapter,
        'QuantDinger': quantdinger_native_adapter,
        'UZI_Skill': uzi_skill_native_adapter,
        'Kaixin_Factors': kaixin_factors_native_adapter,
        'MiMo_Reasoning': mimo_reasoning_native_adapter,
    }
    runner = runners.get(repo_name)
    if not runner:
        return blocked_record(repo_name, 'UNKNOWN_NATIVE_REPO', candidate)
    return runner(candidate)


def run_all_native_adapters(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [native_adapter_for_repo(repo_name, candidate) for repo_name in ACTIVE_REPO_ORDER]
