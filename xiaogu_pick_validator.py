#!/usr/bin/env python3
"""
Pick validator using Serenity + Buffett frameworks.
Runs after PAPER_PICK to validate the pick with investment analysis.
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

BASE = Path(os.environ.get('XIAOGU_HOME') or Path(__file__).resolve().parent)


def validate_pick_with_serenity(symbol: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serenity framework: Supply chain chokepoint analysis + institutional capital.
    
    Returns validation result with:
    - supply_chain_score: 0-100 (higher = better chokepoint position)
    - institutional_signal: positive/negative/neutral
    - valuation_reset_potential: 0-100
    - risk_factors: list of risks
    - verdict: BUY/HOLD/SELL/SKIP
    """
    result = {
        'framework': 'serenity',
        'symbol': symbol,
        'supply_chain_score': 0,
        'institutional_signal': 'neutral',
        'valuation_reset_potential': 0,
        'risk_factors': [],
        'verdict': 'SKIP',
        'reasoning': [],
    }
    
    # Extract data from candidate (check nested fields too)
    sector_score = (
        candidate.get('sector_opportunity_score') 
        or candidate.get('sector_catalyst_score')
        or (candidate.get('structured_component_details') or {}).get('sector_opportunity_score')
        or 0
    )
    fund_flow = candidate.get('fund_flow_momentum') or 0
    net_inflow = candidate.get('net_inflow_main') or 0
    turnover = candidate.get('turnover_rate') or 0
    signal_pct = candidate.get('signal_pct') or 0
    
    # Supply chain score (based on sector strength)
    if sector_score >= 0.8:
        result['supply_chain_score'] = 80
        result['reasoning'].append('Strong sector position (sector_score >= 0.8)')
    elif sector_score >= 0.5:
        result['supply_chain_score'] = 60
        result['reasoning'].append('Moderate sector position')
    elif sector_score >= 0.3:
        result['supply_chain_score'] = 40
        result['reasoning'].append('Weak sector position')
    else:
        result['supply_chain_score'] = 20
        result['reasoning'].append('No clear sector position')
    
    # Institutional signal (based on fund flow momentum OR net_inflow)
    # fund_flow_momentum is the primary indicator (0-1 scale)
    # net_inflow_main is fallback (raw amount in yuan)
    if fund_flow >= 0.1 or net_inflow > 1000000:
        result['institutional_signal'] = 'positive'
        result['reasoning'].append(f'Strong institutional buying (fund_flow={fund_flow:.3f}, net_inflow={net_inflow/10000:.0f}万)')
    elif fund_flow < -0.1 or net_inflow < -1000000:
        result['institutional_signal'] = 'negative'
        result['reasoning'].append('Institutional selling detected')
    else:
        result['institutional_signal'] = 'neutral'
        result['reasoning'].append('Neutral institutional flow')
    
    # Valuation reset potential
    if signal_pct >= 5 and sector_score >= 0.5:
        result['valuation_reset_potential'] = 70
        result['reasoning'].append('High momentum + strong sector = re-rating potential')
    elif signal_pct >= 3:
        result['valuation_reset_potential'] = 50
        result['reasoning'].append('Moderate momentum')
    else:
        result['valuation_reset_potential'] = 30
    
    # Risk factors
    if turnover > 20:
        result['risk_factors'].append('HIGH_TURNOVER: Possible distribution')
    if signal_pct > 9:
        result['risk_factors'].append('NEAR_LIMIT: Chasing risk')
    if fund_flow < -0.3:
        result['risk_factors'].append('INSTITUTIONAL_SELLING: Smart money exiting')
    
    # Verdict
    score = result['supply_chain_score'] * 0.4 + result['valuation_reset_potential'] * 0.3
    if result['institutional_signal'] == 'positive':
        score += 20
    elif result['institutional_signal'] == 'negative':
        score -= 20
    
    if score >= 70 and not result['risk_factors']:
        result['verdict'] = 'BUY'
    elif score >= 50:
        result['verdict'] = 'HOLD'
    elif result['risk_factors']:
        result['verdict'] = 'SELL'
    else:
        result['verdict'] = 'SKIP'
    
    return result


def validate_pick_with_buffett(symbol: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Buffett framework: Moat + Management + Valuation.
    
    Returns validation result with:
    - moat_score: 0-100
    - management_score: 0-100
    - valuation_score: 0-100
    - margin_of_safety: percentage
    - quick_filter_pass: bool (8-question filter)
    - verdict: BUY/HOLD/SELL/SKIP
    """
    result = {
        'framework': 'buffett',
        'symbol': symbol,
        'moat_score': 0,
        'management_score': 0,
        'valuation_score': 0,
        'margin_of_safety': 0,
        'quick_filter_pass': False,
        'quick_filter_answers': {},
        'verdict': 'SKIP',
        'reasoning': [],
    }
    
    # Extract data (check nested fields too)
    pe = candidate.get('pe_dynamic') or 0
    pb = candidate.get('pb') or 0
    sector_score = (
        candidate.get('sector_opportunity_score') 
        or candidate.get('sector_catalyst_score')
        or (candidate.get('structured_component_details') or {}).get('sector_opportunity_score')
        or 0
    )
    fund_flow = candidate.get('fund_flow_momentum') or 0
    net_inflow = candidate.get('net_inflow_main') or 0
    
    # Quick filter (8 questions)
    q1 = sector_score > 0  # Can explain business
    q2 = sector_score >= 0.3  # Will exist in 10 years
    q3 = sector_score >= 0.5  # Has moat
    q4 = fund_flow >= 0.1 or net_inflow > 1000000  # Pricing power (institutional buying)
    q5 = net_inflow > 0  # Earnings quality (positive flow)
    q6 = True  # Debt safety (assume OK for now)
    q7 = True  # Management integrity (assume OK)
    q8 = pe > 0 and pe < 30  # Reasonable price
    
    result['quick_filter_answers'] = {
        'circle_of_competence': q1,
        'durability': q2,
        'moat': q3,
        'pricing_power': q4,
        'earnings_quality': q5,
        'debt_safety': q6,
        'management_integrity': q7,
        'reasonable_price': q8,
    }
    
    no_count = sum(1 for v in result['quick_filter_answers'].values() if not v)
    result['quick_filter_pass'] = no_count < 4
    
    # Moat score
    if sector_score >= 0.8:
        result['moat_score'] = 80
        result['reasoning'].append('Strong moat: dominant sector position')
    elif sector_score >= 0.5:
        result['moat_score'] = 60
        result['reasoning'].append('Moderate moat')
    elif sector_score >= 0.3:
        result['moat_score'] = 40
        result['reasoning'].append('Weak moat')
    else:
        result['moat_score'] = 20
        result['reasoning'].append('No clear moat')
    
    # Management score (based on fund flow momentum OR net_inflow as proxy)
    if fund_flow >= 0.1 or net_inflow > 1000000:
        result['management_score'] = 70
        result['reasoning'].append(f'Institutional confidence (fund_flow={fund_flow:.3f}, net_inflow={net_inflow/10000:.0f}万)')
    elif fund_flow > 0 or net_inflow > 0:
        result['management_score'] = 50
    else:
        result['management_score'] = 30
        result['reasoning'].append('Institutional skepticism')
    
    # Valuation score
    if pe > 0 and pe < 15:
        result['valuation_score'] = 80
        result['reasoning'].append('Undervalued (PE < 15)')
    elif pe > 0 and pe < 25:
        result['valuation_score'] = 60
        result['reasoning'].append('Fair value')
    elif pe > 0 and pe < 40:
        result['valuation_score'] = 40
        result['reasoning'].append('Slightly overvalued')
    elif pe >= 40:
        result['valuation_score'] = 20
        result['reasoning'].append('Overvalued')
    else:
        result['valuation_score'] = 30  # No PE data
    
    # Margin of safety
    if result['valuation_score'] >= 60:
        result['margin_of_safety'] = max(0, (80 - pe) / 80 * 100)
    else:
        result['margin_of_safety'] = 0
    
    # Verdict
    if not result['quick_filter_pass']:
        result['verdict'] = 'SKIP'
        result['reasoning'].append('Failed quick filter (4+ No answers)')
    elif result['moat_score'] >= 60 and result['valuation_score'] >= 60:
        result['verdict'] = 'BUY'
    elif result['moat_score'] >= 40:
        result['verdict'] = 'HOLD'
    else:
        result['verdict'] = 'SKIP'
    
    return result


def validate_pick(symbol: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run both Serenity and Buffett validation on a pick.
    Returns combined validation result.
    """
    serenity = validate_pick_with_serenity(symbol, candidate)
    buffett = validate_pick_with_buffett(symbol, candidate)
    
    # Combined verdict
    verdicts = [serenity['verdict'], buffett['verdict']]
    
    if 'SELL' in verdicts:
        combined_verdict = 'SELL'
    elif 'SKIP' in verdicts:
        combined_verdict = 'SKIP'
    elif verdicts == ['BUY', 'BUY']:
        combined_verdict = 'STRONG_BUY'
    elif 'BUY' in verdicts:
        combined_verdict = 'BUY'
    elif 'HOLD' in verdicts:
        combined_verdict = 'HOLD'
    else:
        combined_verdict = 'SKIP'
    
    # Validation override: can this candidate bypass normal gates?
    validation_override = False
    override_reason = ''
    
    # Strong validation can override soft gates
    if combined_verdict == 'STRONG_BUY':
        validation_override = True
        override_reason = 'STRONG_BUY: Both frameworks agree on BUY'
    elif combined_verdict == 'BUY' and serenity['supply_chain_score'] >= 70:
        validation_override = True
        override_reason = 'BUY with strong supply chain position'
    elif combined_verdict == 'BUY' and buffett['moat_score'] >= 70:
        validation_override = True
        override_reason = 'BUY with strong moat'
    elif combined_verdict == 'HOLD' and serenity['supply_chain_score'] >= 80 and buffett['moat_score'] >= 80:
        validation_override = True
        override_reason = 'HOLD with strong supply chain + moat (both >= 80)'
    elif combined_verdict == 'HOLD' and serenity['institutional_signal'] == 'positive' and buffett['management_score'] >= 70:
        validation_override = True
        override_reason = 'HOLD with positive institutional signal + strong management'
    
    # Risk factors can block override
    if serenity['risk_factors'] or buffett['quick_filter_answers'].get('management_integrity') == False:
        validation_override = False
        override_reason = 'Blocked by risk factors or management integrity issue'
    
    return {
        'symbol': symbol,
        'combined_verdict': combined_verdict,
        'serenity': serenity,
        'buffett': buffett,
        'validation_passed': combined_verdict in ('STRONG_BUY', 'BUY', 'HOLD'),
        'validation_override': validation_override,
        'override_reason': override_reason,
    }


if __name__ == '__main__':
    # Test with sample data
    sample = {
        'sector_opportunity_score': 0.7,
        'fund_flow_momentum': 0.6,
        'net_inflow_main': 50000000,
        'turnover_rate': 15,
        'signal_pct': 5.5,
        'pe_dynamic': 25,
        'pb': 3.5,
    }
    result = validate_pick('600519', sample)
    print(json.dumps(result, indent=2, ensure_ascii=False))
