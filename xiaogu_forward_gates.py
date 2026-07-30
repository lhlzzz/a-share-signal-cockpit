#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forward runner evidence / soft-gate helpers (extracted from the 10k runner).

Production pick chain still enters via xiaogu_forward_d1_1450_runner_v0_1.py,
which re-exports these symbols for import compatibility.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

REQUIRED_EASTMONEY_EVIDENCE_DOMAINS = (
    'announcements', 'risk_alerts', 'lhb', 'concept_industry', 'financials',
)
REQUIRED_EASTMONEY_CDP_TAB_SOURCES = (
    'quote_rank', 'fund_flow', 'watchlist', 'announcements', 'lhb', 'concept_industry', 'financials',
)
REQUIRED_EASTMONEY_DEFAULT_ENHANCED_CDP_TAB_SOURCES = (
    'limitup_pool', 'broken_limit_pool', 'consecutive_limit_pool', 'yesterday_limit_pool',
    'popularity_rank', 'industry_board', 'sector_fund_flow',
)
REQUIRED_EASTMONEY_EXPERIMENTAL_ENHANCED_CDP_TAB_SOURCES = (
    'margin_trading', 'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS = (
    'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength', 'yesterday_limit_strength',
    'popularity_heat', 'industry_board', 'sector_fund_flow', 'candidate_quote_recheck',
    'candidate_fund_recheck', 'candidate_lhb_recheck', 'candidate_announcement_recheck',
)
REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS = (
    'margin_trading', 'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS = (
    'candidate_quote_recheck', 'candidate_fund_recheck', 'candidate_lhb_recheck', 'candidate_announcement_recheck',
)

_CDP_TABS_HARD_FLAG_PREFIXES = (
    'EASTMONEY_REQUIRED_CDP_TABS_MISSING_',
    'EASTMONEY_ENHANCED_CDP_TABS_MISSING_',
    'EASTMONEY_DEFAULT_ENHANCED_CDP_TABS_MISSING_',
)


def is_v2_api_scan_source(bundle: Dict[str, Any]) -> bool:
    """True when basket is production v2 API scanner (not true CDP web_tabs live)."""
    candidate_source = str(bundle.get('candidate_source', '') or '')
    pipeline = str(
        bundle.get('pipeline_version')
        or (bundle.get('market_snapshot') or {}).get('pipeline_version')
        or ''
    )
    source_status = bundle.get('source_status') or (bundle.get('market_snapshot') or {}).get('source_status') or {}
    cdp_tabs = source_status.get('required_cdp_tabs', {}) if isinstance(source_status, dict) else {}
    mode = str((cdp_tabs or {}).get('mode') or '')
    if 'v2_scanner_api' in candidate_source or 'eastmoney_api_scan_v2' in candidate_source:
        return True
    if 'v2_scanner_api' in pipeline or 'eastmoney_api_scan_v2' in pipeline:
        return True
    if mode == 'api_direct':
        return True
    return False


def missing_coverage_items(
    source_status: Dict[str, Any],
    section_name: str,
    missing_key: str,
    expected_items: Tuple[str, ...],
) -> List[str]:
    section = source_status.get(section_name, {}) if isinstance(source_status, dict) else {}
    if not isinstance(section, dict) or not section:
        return list(expected_items)
    explicit_missing = section.get(missing_key)
    if isinstance(explicit_missing, list):
        missing = [str(item) for item in explicit_missing if item]
        if section.get('status') != 'PASS' and not missing:
            return list(expected_items)
        return missing
    if section.get('status') != 'PASS':
        return list(expected_items)
    return []


def web_tabs_evidence_missing_flags(bundle: Dict[str, Any]) -> List[str]:
    candidate_source = str(bundle.get('candidate_source', ''))
    if 'eastmoney_web_tabs' not in candidate_source:
        return []
    is_four_repo_source = 'four_repo' in candidate_source
    is_formal_web_tabs_source = 'eastmoney_web_tabs_scan_v0_1' in candidate_source
    is_v2 = is_v2_api_scan_source(bundle)
    flags: List[str] = []
    source_status = bundle.get('source_status') or bundle.get('market_snapshot', {}).get('source_status') or {}
    full_universe = bundle.get('full_universe_scan') or bundle.get('market_snapshot', {}).get('full_universe_scan') or {}
    has_full_universe_status = bool(full_universe) or bundle.get('market_snapshot', {}).get('universe_quote_count') is not None
    if has_full_universe_status:
        quote_count = full_universe.get('quote_count') if isinstance(full_universe, dict) else None
        if quote_count is None:
            quote_count = bundle.get('market_snapshot', {}).get('universe_quote_count')
        try:
            quote_count_num = int(quote_count)
        except (TypeError, ValueError):
            quote_count_num = 0
        coverage_status = full_universe.get('coverage_status') if isinstance(full_universe, dict) else None
        if (coverage_status and coverage_status != 'PASS') or quote_count_num < 4000:
            flags.append('FULL_UNIVERSE_SCAN_INCOMPLETE')
    # True CDP tab completeness only for non-v2 web_tabs live sources.
    if not is_v2:
        cdp_tabs_status = source_status.get('required_cdp_tabs', {}) if isinstance(source_status, dict) else {}
        if cdp_tabs_status:
            missing_tabs = cdp_tabs_status.get('missing_sources') or []
            if cdp_tabs_status.get('status') != 'PASS' or missing_tabs:
                flags.append(
                    'EASTMONEY_REQUIRED_CDP_TABS_MISSING_'
                    + ','.join(str(source) for source in missing_tabs)
                )
        else:
            tabs = bundle.get('eastmoney_web_tabs') or bundle.get('market_snapshot', {}).get('eastmoney_web_tabs') or []
            if not tabs:
                flags.append('EASTMONEY_REQUIRED_CDP_TABS_MISSING_legacy_no_visible_tabs')
    missing = []
    for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS:
        status = source_status.get(domain, {}) if isinstance(source_status, dict) else {}
        if status.get('status') not in ('PASS', 'PARTIAL'):
            missing.append(domain)
    if missing:
        flags.append('EASTMONEY_FULL_EVIDENCE_PACK_MISSING_' + ','.join(missing))
    if not is_v2:
        default_enhanced_tabs_missing = missing_coverage_items(
            source_status,
            'enhanced_cdp_tabs',
            'missing_sources',
            REQUIRED_EASTMONEY_DEFAULT_ENHANCED_CDP_TAB_SOURCES,
        )
        if default_enhanced_tabs_missing:
            if is_formal_web_tabs_source:
                flags.append(
                    'EASTMONEY_ENHANCED_CDP_TABS_MISSING_'
                    + ','.join(default_enhanced_tabs_missing)
                )
            else:
                flags.append(
                    'EASTMONEY_DEFAULT_ENHANCED_CDP_TABS_MISSING_'
                    + ','.join(default_enhanced_tabs_missing)
                )
    enhanced_missing = missing_coverage_items(
        source_status,
        'enhanced_evidence_coverage',
        'missing_domains',
        REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS,
    )
    if enhanced_missing:
        flags.append('EASTMONEY_ENHANCED_EVIDENCE_PACK_MISSING_' + ','.join(enhanced_missing))
    if not is_four_repo_source:
        experimental_missing = missing_coverage_items(
            source_status,
            'experimental_evidence_coverage',
            'missing_domains',
            REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS,
        )
        if experimental_missing:
            flags.append('EASTMONEY_EXPERIMENTAL_EVIDENCE_PACK_MISSING_' + ','.join(experimental_missing))
    # Safety filter: never emit CDP-tabs hard flags for v2 API scan identity.
    if is_v2:
        flags = [
            flag for flag in flags
            if not any(flag.startswith(prefix) for prefix in _CDP_TABS_HARD_FLAG_PREFIXES)
        ]
    return flags


def soft_no_pick_flag(flag: str) -> bool:
    text = str(flag or '')
    return (
        text.startswith('EASTMONEY_')
        or text.startswith('FULL_UNIVERSE_QUOTE_COUNT_TOO_LOW')
        or text.startswith('ZERO_QUOTE_READ')
        or text.startswith('ZERO_FUND_FLOW_READ')
        or text == 'SOURCE_COMPLETENESS_MISSING'
        or text == 'DATA_SOURCE_INCOMPLETE'
        or text == 'candidate_evidence_status!=PASS'
        or text.startswith('buy_confirmation_below_threshold')
        or text == 'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
        or text == 'OPPORTUNITY_HARD_BLOCK_CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION'
    )


def candidate_evidence_missing_flags(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> List[str]:
    candidate_source = str(bundle.get('candidate_source', ''))
    if 'eastmoney_web_tabs' not in candidate_source:
        return []
    flags: List[str] = []
    status = candidate.get('candidate_evidence_status')
    explicit_missing = candidate.get('candidate_evidence_missing_domains') or []
    if explicit_missing:
        flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING_' + ','.join(str(domain) for domain in explicit_missing))
    else:
        counts = candidate.get('candidate_evidence_domain_counts') or {}
        if isinstance(counts, dict):
            # risk_alerts empty = no risk = PASS (not missing)
            optional_evidence_domains = {'risk_alerts'}
            missing = [
                domain for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS
                if domain not in optional_evidence_domains and not counts.get(domain)
            ]
            if missing:
                flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING_' + ','.join(missing))
        elif status != 'PASS':
            flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING')
    enhanced_counts = candidate.get('enhanced_evidence_domain_counts') or {}
    if isinstance(enhanced_counts, dict):
        missing_recheck = [
            domain for domain in REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS
            if not enhanced_counts.get(domain)
        ]
    else:
        explicit_enhanced_missing = candidate.get('enhanced_evidence_missing_domains') or []
        missing_recheck = [
            domain for domain in REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS
            if domain in explicit_enhanced_missing
        ]
        if not explicit_enhanced_missing:
            missing_recheck = list(REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS)
    if missing_recheck:
        flags.append('EASTMONEY_CANDIDATE_RECHECK_EVIDENCE_MISSING_' + ','.join(missing_recheck))
    return flags
