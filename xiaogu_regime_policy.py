#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-owner market-regime policy for dynamic strategy thresholds.

Owns:
- production regime taxonomy (strong / weak / sideways / climax / no_main)
- stage gate thresholds by regime
- sector gate thresholds
- preferred shadow ranking variant
- self_evolve scoring_config bounds (strategy knobs only)

Does NOT own:
- regulatory / data-completeness hard floors
- formal_candidate_sort_key
- PAPER_PICK ledger mutation

Compatibility: legacy market_regime labels strong/weak/neutral still work;
production_regime adds climax/no_main for soft/mainline-aware policy.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# --- Taxonomy -----------------------------------------------------------------
PRODUCTION_REGIMES = ("strong", "weak", "sideways", "climax", "no_main")
LEGACY_MARKET_REGIMES = ("strong", "weak", "neutral")

# Shadow variants used by xiaogu_backtest_v0_1._shadow_ranking_replay
PREFERRED_SHADOW_VARIANT = {
    "strong": "limitup_gene_shadow_plus",
    "weak": "weak_market_defensive_shadow",
    "sideways": "low_position_catalyst_shadow_plus",
    "climax": "risk_penalty_shadow_plus",
    "no_main": "weak_market_defensive_shadow",
}

# Sector opportunity gate (lower = easier sector pass in defensive regimes)
SECTOR_GATE_THRESHOLD = {
    "strong": 0.5,
    "weak": 0.2,
    "sideways": 0.4,
    "climax": 0.45,
    "no_main": 0.15,
    # legacy alias
    "neutral": 0.4,
}

# Quality-first daily ticket escape floor (score) by regime — soft path only
QUALITY_ESCAPE_SCORE_FLOOR = {
    "strong": 70.0,
    "weak": 65.0,
    "sideways": 65.0,
    "climax": 72.0,
    "no_main": 62.0,
    "neutral": 65.0,
}

# Stage adaptive thresholds: preserve historical weak/supportive/overheated numbers.
# Keys match candidate_stage buckets used by market_adaptive_thresholds.
_STAGE_TABLE: Dict[str, Dict[str, Dict[str, float]]] = {
    "weak": {
        "near_limit_9_plus": {
            "component_min": 0.74,
            "buy_confirmation_min": 0.68,
            "order_book_min": 0.56,
            "dynamic_required_confirmations": 5,
            "dynamic_close_position_min": 0.84,
            "dynamic_fund_flow_min": 0.60,
            "dynamic_time_series_min": 0.20,
        },
        "high_7_to_9": {
            "component_min": 0.70,
            "buy_confirmation_min": 0.68,
            "order_book_min": 0.55,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.78,
            "dynamic_fund_flow_min": 0.55,
            "dynamic_time_series_min": 0.20,
        },
        "default": {
            "component_min": 0.60,
            "buy_confirmation_min": 0.62,
            "order_book_min": 0.52,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.65,
            "dynamic_fund_flow_min": 0.50,
            "dynamic_time_series_min": 0.15,
        },
    },
    "climax": {
        # overheated path: slightly tighter confirmation than strong chase
        "near_limit_9_plus": {
            "component_min": 0.70,
            "buy_confirmation_min": 0.68,
            "order_book_min": 0.56,
            "dynamic_required_confirmations": 5,
            "dynamic_close_position_min": 0.84,
            "dynamic_fund_flow_min": 0.60,
            "dynamic_time_series_min": 0.20,
        },
        "high_7_to_9": {
            "component_min": 0.66,
            "buy_confirmation_min": 0.62,
            "order_book_min": 0.52,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.78,
            "dynamic_fund_flow_min": 0.55,
            "dynamic_time_series_min": 0.10,
        },
        "default": {
            "component_min": 0.58,
            "buy_confirmation_min": 0.60,
            "order_book_min": 0.50,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.65,
            "dynamic_fund_flow_min": 0.48,
            "dynamic_time_series_min": 0.10,
        },
    },
    "strong": {
        "near_limit_9_plus": {
            "component_min": 0.64,
            "buy_confirmation_min": 0.58,
            "order_book_min": 0.48,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.84,
            "dynamic_fund_flow_min": 0.50,
            "dynamic_time_series_min": 0.10,
        },
        "high_7_to_9": {
            "component_min": 0.55,
            "buy_confirmation_min": 0.55,
            "order_book_min": 0.48,
            "dynamic_required_confirmations": 3,
            "dynamic_close_position_min": 0.76,
            "dynamic_fund_flow_min": 0.45,
            "dynamic_time_series_min": 0.10,
        },
        "default": {
            "component_min": 0.52,
            "buy_confirmation_min": 0.55,
            "order_book_min": 0.46,
            "dynamic_required_confirmations": 3,
            "dynamic_close_position_min": 0.65,
            "dynamic_fund_flow_min": 0.45,
            "dynamic_time_series_min": 0.10,
        },
    },
    "sideways": {
        "near_limit_9_plus": {
            "component_min": 0.66,
            "buy_confirmation_min": 0.60,
            "order_book_min": 0.50,
            "dynamic_required_confirmations": 5,
            "dynamic_close_position_min": 0.84,
            "dynamic_fund_flow_min": 0.55,
            "dynamic_time_series_min": 0.10,
        },
        "high_7_to_9": {
            "component_min": 0.60,
            "buy_confirmation_min": 0.60,
            "order_book_min": 0.50,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.78,
            "dynamic_fund_flow_min": 0.45,
            "dynamic_time_series_min": 0.10,
        },
        "default": {
            "component_min": 0.55,
            "buy_confirmation_min": 0.60,
            "order_book_min": 0.48,
            "dynamic_required_confirmations": 4,
            "dynamic_close_position_min": 0.65,
            "dynamic_fund_flow_min": 0.48,
            "dynamic_time_series_min": 0.10,
        },
    },
    # no_main shares weak stage gates (defensive) — sector gate is looser separately
    "no_main": {},
}

# Fill no_main from weak after definition
_STAGE_TABLE["no_main"] = {
    stage: dict(vals) for stage, vals in _STAGE_TABLE["weak"].items()
}

# Self-evolve bounds: (lo, hi, step). Strategy knobs only — never formal sort key.
SELF_EVOLVE_BOUNDS: Dict[str, Tuple[float, float, float]] = {
    "evidence_limitup_momentum_weight": (0.4, 1.5, 0.10),
    "evidence_catalyst_boost_weight": (0.3, 1.2, 0.10),
    "evidence_broken_limit_penalty_weight": (1.0, 2.5, 0.10),
    "l2_limit_strength_bonus": (1.0, 150.0, 5.0),
    "sector_catalyst_penalty": (1.0, 150.0, 5.0),
    "instant_momentum_min_confirmations": (1.0, 3.0, 1.0),
}

# Which knobs a regime prefers when shadow evidence is ambiguous
REGIME_EVOLVE_PREFERRED_KEYS: Dict[str, List[str]] = {
    "strong": ["evidence_limitup_momentum_weight", "evidence_catalyst_boost_weight"],
    "weak": ["evidence_broken_limit_penalty_weight", "evidence_catalyst_boost_weight"],
    "sideways": ["evidence_catalyst_boost_weight", "evidence_limitup_momentum_weight"],
    "climax": ["evidence_broken_limit_penalty_weight", "sector_catalyst_penalty"],
    "no_main": ["evidence_broken_limit_penalty_weight", "evidence_catalyst_boost_weight"],
}

# Map shadow variants → evolve direction
SHADOW_VARIANT_EVOLVE: Dict[str, Tuple[str, str]] = {
    "limitup_gene_shadow_plus": ("evidence_limitup_momentum_weight", "INCREASE"),
    "risk_penalty_shadow_plus": ("evidence_broken_limit_penalty_weight", "INCREASE"),
    "weak_market_defensive_shadow": ("evidence_broken_limit_penalty_weight", "INCREASE"),
    "low_position_catalyst_shadow_plus": ("evidence_catalyst_boost_weight", "INCREASE"),
    "social_catalyst_shadow": ("evidence_catalyst_boost_weight", "INCREASE"),
}

# Default scoring_config baselines (must match xiaogu_db SCORING_CONFIG_DEFAULTS).
# formal_sort / ranking_basis hardcodes were tuned at these values → scale=1.0 preserves behavior.
EVIDENCE_WEIGHT_DEFAULTS: Dict[str, float] = {
    "evidence_limitup_momentum_weight": 0.7,
    "evidence_catalyst_boost_weight": 0.5,
    "evidence_broken_limit_penalty_weight": 1.5,
}

# Soft regime multipliers applied AFTER config/default scale.
# sideways/neutral = 1.0 so missing regime does not change existing tests.
RANKING_EVIDENCE_REGIME_SCALE: Dict[str, Dict[str, float]] = {
    "strong": {"limitup": 1.15, "catalyst": 1.10, "broken": 1.00},
    "sideways": {"limitup": 1.00, "catalyst": 1.00, "broken": 1.00},
    "neutral": {"limitup": 1.00, "catalyst": 1.00, "broken": 1.00},
    "weak": {"limitup": 0.85, "catalyst": 1.05, "broken": 1.15},
    "climax": {"limitup": 0.75, "catalyst": 0.90, "broken": 1.25},
    "no_main": {"limitup": 0.80, "catalyst": 1.00, "broken": 1.20},
}

# Combined scale clamps (config * regime) so self_evolve extremes stay bounded.
_RANKING_SCALE_CLAMP: Dict[str, Tuple[float, float]] = {
    "limitup": (0.40, 2.50),
    "catalyst": (0.40, 2.20),
    "broken": (0.70, 2.80),
}


def classify_production_regime(
    market_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify production regime from scanner market state only."""
    ctx = market_context if isinstance(market_context, dict) else {}
    overheated = bool(ctx.get("overheated_market"))
    weak_acc = bool(ctx.get("weak_acceptance_market"))
    supportive = bool(ctx.get("supportive_market")) and not weak_acc
    market_regime = str(ctx.get("market_regime") or "").lower()

    if overheated:
        return "climax"
    if market_regime == "strong" or supportive:
        return "strong"
    if market_regime == "weak" or weak_acc:
        return "weak"
    if market_regime in ("sideways", "neutral"):
        return "sideways"
    # Infer from flags when market_regime empty
    if supportive:
        return "strong"
    if weak_acc:
        return "weak"
    return "sideways"


def threshold_mode_for_context(market_context: Optional[Dict[str, Any]] = None) -> str:
    """Map adaptive flags to the stage-threshold bucket (preserves legacy behavior).

    Precedence mirrors historical market_adaptive_thresholds:
    weak_acceptance → weak; overheated → climax; supportive → strong; else sideways.
    """
    ctx = market_context if isinstance(market_context, dict) else {}
    weak_acceptance_market = bool(ctx.get("weak_acceptance_market"))
    supportive_market = bool(ctx.get("supportive_market")) and not weak_acceptance_market
    overheated_market = bool(ctx.get("overheated_market"))
    if weak_acceptance_market:
        return "weak"
    if overheated_market:
        return "climax"
    if supportive_market:
        return "strong"
    # Prefer production_regime when already classified
    prod = str(ctx.get("production_regime") or "").lower()
    if prod in _STAGE_TABLE:
        return prod
    return "sideways"


def stage_bucket(candidate_stage: str) -> str:
    stage = str(candidate_stage or "").strip()
    if stage == "near_limit_9_plus":
        return "near_limit_9_plus"
    if stage == "high_7_to_9":
        return "high_7_to_9"
    return "default"


def market_adaptive_thresholds(
    candidate_stage: str,
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """Stage gates looked up from regime table (legacy-compatible numbers)."""
    mode = threshold_mode_for_context(market_context)
    table = _STAGE_TABLE.get(mode) or _STAGE_TABLE["sideways"]
    bucket = stage_bucket(candidate_stage)
    row = table.get(bucket) or table.get("default") or _STAGE_TABLE["sideways"]["default"]
    return dict(row)


def sector_gate_threshold_for_market(market_context: Optional[Dict[str, Any]] = None) -> float:
    """Sector opportunity threshold by production regime (legacy market_regime fallback)."""
    ctx = market_context if isinstance(market_context, dict) else {}
    prod = str(ctx.get("production_regime") or "").lower()
    if prod not in SECTOR_GATE_THRESHOLD:
        # Prefer flag-aware classification when production_regime missing
        if bool(ctx.get("weak_acceptance_market")) or str(ctx.get("market_regime") or "") == "weak":
            prod = "weak"
        elif bool(ctx.get("supportive_market")) or str(ctx.get("market_regime") or "") == "strong":
            prod = "strong"
        elif str(ctx.get("market_regime") or "") in ("neutral", "sideways"):
            prod = "sideways"
        else:
            prod = classify_production_regime(ctx)
    return float(SECTOR_GATE_THRESHOLD.get(prod, SECTOR_GATE_THRESHOLD["sideways"]))


def quality_escape_score_floor(market_context: Optional[Dict[str, Any]] = None) -> float:
    ctx = market_context if isinstance(market_context, dict) else {}
    prod = str(ctx.get("production_regime") or classify_production_regime(ctx)).lower()
    return float(QUALITY_ESCAPE_SCORE_FLOOR.get(prod, 65.0))


def preferred_shadow_variant(regime: str) -> str:
    key = str(regime or "sideways").lower()
    return PREFERRED_SHADOW_VARIANT.get(key, PREFERRED_SHADOW_VARIANT["sideways"])


def normalize_regime_for_ranking_scale(regime: str) -> str:
    key = str(regime or "sideways").lower().strip()
    if key in RANKING_EVIDENCE_REGIME_SCALE:
        return key
    if "climax" in key:
        return "climax"
    if key in ("bull", "strong"):
        return "strong"
    if key in ("bear", "weak"):
        return "weak"
    if key in ("no_main", "nomain"):
        return "no_main"
    return "sideways"


def resolve_ranking_evidence_scales(
    config: Optional[Dict[str, Any]] = None,
    production_regime: str = "sideways",
) -> Dict[str, Any]:
    """Map scoring_config weights + regime → multiplicative scales for production ranking.

    scale = (config_weight / default_weight) * regime_mult, clamped.
    At defaults + sideways: all scales == 1.0 (preserves pre-wiring hardcodes).
    Does not change formal_candidate_sort_key structure — runner multiplies existing coeffs.
    """
    cfg = config if isinstance(config, dict) else {}

    def _weight(key: str) -> float:
        raw = cfg.get(key)
        if raw is None or raw == "":
            return float(EVIDENCE_WEIGHT_DEFAULTS[key])
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(EVIDENCE_WEIGHT_DEFAULTS[key])

    limitup_w = _weight("evidence_limitup_momentum_weight")
    catalyst_w = _weight("evidence_catalyst_boost_weight")
    broken_w = _weight("evidence_broken_limit_penalty_weight")

    config_limitup = limitup_w / EVIDENCE_WEIGHT_DEFAULTS["evidence_limitup_momentum_weight"]
    config_catalyst = catalyst_w / EVIDENCE_WEIGHT_DEFAULTS["evidence_catalyst_boost_weight"]
    config_broken = broken_w / EVIDENCE_WEIGHT_DEFAULTS["evidence_broken_limit_penalty_weight"]

    regime = normalize_regime_for_ranking_scale(production_regime)
    regime_row = RANKING_EVIDENCE_REGIME_SCALE.get(regime) or RANKING_EVIDENCE_REGIME_SCALE["sideways"]

    def _clamp(kind: str, value: float) -> float:
        lo, hi = _RANKING_SCALE_CLAMP[kind]
        return round(max(lo, min(hi, float(value))), 4)

    limitup_scale = _clamp("limitup", config_limitup * float(regime_row["limitup"]))
    catalyst_scale = _clamp("catalyst", config_catalyst * float(regime_row["catalyst"]))
    broken_scale = _clamp("broken", config_broken * float(regime_row["broken"]))

    return {
        "limitup_scale": limitup_scale,
        "catalyst_scale": catalyst_scale,
        "broken_scale": broken_scale,
        "limitup_weight": round(limitup_w, 4),
        "catalyst_weight": round(catalyst_w, 4),
        "broken_weight": round(broken_w, 4),
        "config_limitup_scale": round(config_limitup, 4),
        "config_catalyst_scale": round(config_catalyst, 4),
        "config_broken_scale": round(config_broken, 4),
        "regime_limitup_mult": float(regime_row["limitup"]),
        "regime_catalyst_mult": float(regime_row["catalyst"]),
        "regime_broken_mult": float(regime_row["broken"]),
        "production_regime": regime,
        "active": True,
    }


def self_evolve_bounds() -> Dict[str, Tuple[float, float, float]]:
    """Copy of allowed scoring_config knobs for safe_self_evolve."""
    return {k: tuple(v) for k, v in SELF_EVOLVE_BOUNDS.items()}


def clamp_self_evolve_value(key: str, value: float) -> Optional[float]:
    bounds = SELF_EVOLVE_BOUNDS.get(key)
    if not bounds:
        return None
    lo, hi, _step = bounds
    return round(max(lo, min(hi, float(value))), 4)


def resolve_evolve_key_for_shadow(selected_variant: str, regime: str = "") -> Optional[Tuple[str, str]]:
    """Return (config_key, direction) for a shadow winner, regime-aware fallback."""
    variant = str(selected_variant or "")
    if variant in SHADOW_VARIANT_EVOLVE:
        return SHADOW_VARIANT_EVOLVE[variant]
    prefs = REGIME_EVOLVE_PREFERRED_KEYS.get(str(regime or "sideways").lower()) or []
    if prefs:
        return prefs[0], "INCREASE"
    return None


def attach_regime_to_context(
    market_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Mutate+return market_context with production_regime and policy snapshot."""
    if not isinstance(market_context, dict):
        return {}
    prod = classify_production_regime(market_context)
    market_context["production_regime"] = prod
    market_context["regime_policy"] = {
        "production_regime": prod,
        "sector_gate_threshold": sector_gate_threshold_for_market(market_context),
        "quality_escape_score_floor": quality_escape_score_floor(market_context),
        "preferred_shadow_variant": preferred_shadow_variant(prod),
        "threshold_mode": threshold_mode_for_context(market_context),
        "dynamic_strategy_gates": True,
        "fixed_safety_floors": True,
    }
    return market_context


def policy_snapshot() -> Dict[str, Any]:
    return {
        "production_regimes": list(PRODUCTION_REGIMES),
        "sector_gate_threshold": dict(SECTOR_GATE_THRESHOLD),
        "preferred_shadow_variant": dict(PREFERRED_SHADOW_VARIANT),
        "self_evolve_keys": list(SELF_EVOLVE_BOUNDS.keys()),
        "ranking_evidence_regime_scale": {
            k: dict(v) for k, v in RANKING_EVIDENCE_REGIME_SCALE.items()
        },
        "evidence_weight_defaults": dict(EVIDENCE_WEIGHT_DEFAULTS),
        "version": "regime_policy_v1",
    }
