#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single production source and evidence gates for the forward runner."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

PRODUCTION_SOURCE = 'eastmoney_api_scan_v2'
PRODUCTION_PIPELINE = 'v2_scanner_api'

REQUIRED_EASTMONEY_EVIDENCE_DOMAINS = (
    'announcements', 'risk_alerts', 'lhb', 'concept_industry', 'financials',
)
REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS = (
    'limitup_strength', 'broken_limit_risk', 'consecutive_limit_strength',
    'yesterday_limit_strength', 'popularity_heat', 'industry_board',
    'sector_fund_flow', 'candidate_quote_recheck', 'candidate_fund_recheck',
    'candidate_lhb_recheck', 'candidate_announcement_recheck',
)
REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS = (
    'block_trades', 'lockup_expiry', 'shareholder_changes',
    'research_reports', 'earnings_preview', 'ipo_calendar', 'trading_halts',
)
REQUIRED_EASTMONEY_CANDIDATE_RECHECK_DOMAINS = (
    'candidate_quote_recheck', 'candidate_fund_recheck',
    'candidate_lhb_recheck', 'candidate_announcement_recheck',
)


def is_api_scan_source(bundle_or_source: Dict[str, Any] | str) -> bool:
    """Return True only for the canonical direct API scanner identity."""
    if isinstance(bundle_or_source, dict):
        source = str(
            bundle_or_source.get('candidate_source')
            or bundle_or_source.get('source')
            or bundle_or_source.get('pipeline_version')
            or ''
        )
        pipeline = str(
            bundle_or_source.get('pipeline_version')
            or (bundle_or_source.get('market_snapshot') or {}).get('pipeline_version')
            or ''
        )
        return source == PRODUCTION_SOURCE or pipeline == PRODUCTION_PIPELINE
    return str(bundle_or_source or '') in {PRODUCTION_SOURCE, PRODUCTION_PIPELINE}


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
    return [] if section.get('status') == 'PASS' else list(expected_items)


def production_evidence_missing_flags(bundle: Dict[str, Any]) -> List[str]:
    """Validate only direct-API evidence; legacy source identities are rejected."""
    if not is_api_scan_source(bundle):
        return ['PRODUCTION_SOURCE_NOT_CANONICAL']

    flags: List[str] = []
    snapshot = bundle.get('market_snapshot') if isinstance(bundle.get('market_snapshot'), dict) else {}
    source_status = bundle.get('source_status') or snapshot.get('source_status') or {}
    full_universe = bundle.get('full_universe_scan') or snapshot.get('full_universe_scan') or {}
    quote_count = full_universe.get('quote_count') if isinstance(full_universe, dict) else None
    if quote_count is None:
        quote_count = snapshot.get('universe_quote_count')
    try:
        quote_count = int(quote_count or 0)
    except (TypeError, ValueError):
        quote_count = 0
    coverage_status = full_universe.get('coverage_status') if isinstance(full_universe, dict) else None
    if (coverage_status and coverage_status != 'PASS') or quote_count < 4000:
        flags.append('FULL_UNIVERSE_SCAN_INCOMPLETE')

    missing = []
    for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS:
        status = source_status.get(domain, {}) if isinstance(source_status, dict) else {}
        if status.get('status') not in ('PASS', 'PARTIAL'):
            missing.append(domain)
    if missing:
        flags.append('EASTMONEY_FULL_EVIDENCE_PACK_MISSING_' + ','.join(missing))

    enhanced_missing = missing_coverage_items(
        source_status,
        'enhanced_evidence_coverage',
        'missing_domains',
        REQUIRED_EASTMONEY_CORE_ENHANCED_EVIDENCE_DOMAINS,
    )
    if enhanced_missing:
        flags.append('EASTMONEY_ENHANCED_EVIDENCE_PACK_MISSING_' + ','.join(enhanced_missing))

    experimental_missing = missing_coverage_items(
        source_status,
        'experimental_evidence_coverage',
        'missing_domains',
        REQUIRED_EASTMONEY_EXPERIMENTAL_EVIDENCE_DOMAINS,
    )
    if experimental_missing:
        flags.append('EASTMONEY_EXPERIMENTAL_EVIDENCE_PACK_MISSING_' + ','.join(experimental_missing))
    return flags


def soft_no_pick_flag(flag: str) -> bool:
    text = str(flag or '')
    return (
        text.startswith('EASTMONEY_')
        or text.startswith('FULL_UNIVERSE_')
        or text.startswith('ZERO_QUOTE_READ')
        or text.startswith('ZERO_FUND_FLOW_READ')
        or text in {
            'SOURCE_COMPLETENESS_MISSING',
            'DATA_SOURCE_INCOMPLETE',
            'candidate_evidence_status!=PASS',
            'CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
            'OPPORTUNITY_HARD_BLOCK_CHASE_HIGH_WITHOUT_LIMITUP_CONFIRMATION',
        }
        or text.startswith('buy_confirmation_below_threshold')
    )


def candidate_evidence_missing_flags(candidate: Dict[str, Any], bundle: Dict[str, Any]) -> List[str]:
    if not is_api_scan_source(bundle):
        return ['PRODUCTION_SOURCE_NOT_CANONICAL']
    flags: List[str] = []
    status = candidate.get('candidate_evidence_status')
    explicit_missing = candidate.get('candidate_evidence_missing_domains') or []
    if explicit_missing:
        flags.append('EASTMONEY_CANDIDATE_EVIDENCE_MISSING_' + ','.join(str(item) for item in explicit_missing))
    else:
        counts = candidate.get('candidate_evidence_domain_counts') or {}
        if isinstance(counts, dict):
            missing = [
                domain for domain in REQUIRED_EASTMONEY_EVIDENCE_DOMAINS
                if domain != 'risk_alerts' and not counts.get(domain)
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
